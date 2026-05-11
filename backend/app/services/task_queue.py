from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from queue import Empty, PriorityQueue
import threading
from typing import Any, Literal, get_args
from uuid import UUID

from app.config import get_settings
from app.db import get_session_factory
from app.models.job import Job


logger = logging.getLogger(__name__)

AsyncJobCallable = Callable[[], Awaitable[None]]

QueueName = Literal["interactive", "library_parse", "maintenance"]
VALID_QUEUE_NAMES: tuple[QueueName, ...] = get_args(QueueName)
DEFAULT_QUEUE_NAME: QueueName = "interactive"

# Mapping job_type -> default queue.  Submission sites may override by passing queue_name
# explicitly to BackgroundTaskQueueManager.submit(), but this table provides a single
# source of truth so recovery and tests can route consistently.
JOB_TYPE_QUEUE_DEFAULTS: dict[str, QueueName] = {
    # interactive (customer-facing realtime flows)
    "rfp_light_parse": "interactive",
    "retrieve": "interactive",
    "generate_section": "interactive",
    "requirement_extract": "interactive",
    "generate_outline": "interactive",
    "generate_draft": "interactive",
    # library_parse (historical proposal ingestion)
    "document_parse": "library_parse",
    "library_material_rebuild": "library_parse",
    # maintenance (background knowledge refresh)
    "case_library_refresh": "maintenance",
    "visual_cache_refresh": "maintenance",
    "knowledge_wiki_compile": "maintenance",
    "rfp_knowledge_extract": "maintenance",
}


@dataclass(order=True)
class _QueuedTask:
    sort_key: tuple[int, float, int]
    job_id: UUID = field(compare=False)
    job_type: str = field(compare=False)
    label: str = field(compare=False)
    dedupe_key: str | None = field(compare=False)
    run: AsyncJobCallable = field(compare=False)
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), compare=False)


class BackgroundTaskQueue:
    def __init__(self, *, worker_count: int, name: str = "default") -> None:
        self.worker_count = max(1, int(worker_count))
        self.name = str(name)
        self._queue: PriorityQueue[_QueuedTask] = PriorityQueue()
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._sequence = 0
        self._queued: dict[UUID, _QueuedTask] = {}
        self._running: dict[UUID, _QueuedTask] = {}
        self._active_by_key: dict[str, UUID] = {}

    def start(self) -> None:
        with self._lock:
            for index in range(len(self._workers), self.worker_count):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"bg-{self.name}-worker-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def submit(
        self,
        *,
        job_id: UUID,
        job_type: str,
        label: str,
        run: AsyncJobCallable,
        dedupe_key: str | None = None,
        priority: int = 100,
    ) -> UUID:
        self.start()
        with self._lock:
            if dedupe_key:
                existing = self._active_by_key.get(dedupe_key)
                if existing is not None:
                    return existing
                self._active_by_key[dedupe_key] = job_id
            self._sequence += 1
            task = _QueuedTask(
                sort_key=(priority, datetime.now(timezone.utc).timestamp(), self._sequence),
                job_id=job_id,
                job_type=job_type,
                label=label,
                dedupe_key=dedupe_key,
                run=run,
            )
            self._queued[job_id] = task
            self._queue.put(task)
            return job_id

    def active_job_id(self, dedupe_key: str) -> UUID | None:
        with self._lock:
            return self._active_by_key.get(dedupe_key)

    def status(self) -> dict[str, Any]:
        with self._lock:
            queued_items = [self._describe_task(task) for task in self._queued.values()]
            running_items = [self._describe_task(task) for task in self._running.values()]
            return {
                "name": self.name,
                "worker_count": self.worker_count,
                "queued_count": len(queued_items),
                "running_count": len(running_items),
                "queued": queued_items,
                "running": running_items,
            }

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=1.0)
            except Empty:
                continue
            with self._lock:
                self._queued.pop(task.job_id, None)
                self._running[task.job_id] = task
            try:
                asyncio.run(task.run())
            except Exception as exc:  # noqa: BLE001
                logger.exception("Background task crashed: %s", task.label)
                asyncio.run(_mark_job_failed(task.job_id, exc))
            finally:
                with self._lock:
                    self._running.pop(task.job_id, None)
                    if task.dedupe_key and self._active_by_key.get(task.dedupe_key) == task.job_id:
                        self._active_by_key.pop(task.dedupe_key, None)
                self._queue.task_done()

    @staticmethod
    def _describe_task(task: _QueuedTask) -> dict[str, Any]:
        return {
            "job_id": str(task.job_id),
            "job_type": task.job_type,
            "label": task.label,
            "dedupe_key": task.dedupe_key,
            "enqueued_at": task.enqueued_at.isoformat(),
        }


async def _mark_job_failed(job_id: UUID, exc: Exception) -> None:
    async with get_session_factory()() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {"succeeded", "failed"}:
            return
        job.status = "failed"
        job.error_code = exc.__class__.__name__[:50]
        job.output_ref = {
            **(job.output_ref or {}),
            "error": str(exc),
            "progress": {
                **((job.output_ref or {}).get("progress") or {}),
                "stage": "failed",
            },
        }
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()


class BackgroundTaskQueueManager:
    """Holds one BackgroundTaskQueue per logical queue name so realtime customer flows
    cannot be blocked by long-running historical ingestion or background maintenance jobs.

    Each queue maintains its own worker pool + PriorityQueue + dedupe namespace.  The
    manager exposes both an explicit ``submit(queue_name=...)`` API and a job_type-based
    fallback for callers that want defaults driven by :data:`JOB_TYPE_QUEUE_DEFAULTS`.
    """

    def __init__(self, *, worker_counts: dict[QueueName, int]) -> None:
        self._queues: dict[QueueName, BackgroundTaskQueue] = {
            name: BackgroundTaskQueue(worker_count=worker_counts.get(name, 1), name=name)
            for name in VALID_QUEUE_NAMES
        }

    def queue(self, name: QueueName) -> BackgroundTaskQueue:
        if name not in self._queues:
            raise ValueError(f"Unknown queue name: {name!r}; valid: {VALID_QUEUE_NAMES}")
        return self._queues[name]

    def queue_for_job_type(self, job_type: str) -> BackgroundTaskQueue:
        name = JOB_TYPE_QUEUE_DEFAULTS.get(job_type, DEFAULT_QUEUE_NAME)
        return self.queue(name)

    def submit(
        self,
        *,
        job_id: UUID,
        job_type: str,
        label: str,
        run: AsyncJobCallable,
        queue_name: QueueName | None = None,
        dedupe_key: str | None = None,
        priority: int = 100,
    ) -> UUID:
        resolved = queue_name or JOB_TYPE_QUEUE_DEFAULTS.get(job_type, DEFAULT_QUEUE_NAME)
        return self.queue(resolved).submit(
            job_id=job_id,
            job_type=job_type,
            label=label,
            run=run,
            dedupe_key=dedupe_key,
            priority=priority,
        )

    def active_job_id(self, dedupe_key: str, *, queue_name: QueueName | None = None) -> UUID | None:
        """Look up an active job by dedupe_key.

        If ``queue_name`` is provided, only that queue is consulted.  Otherwise all queues
        are searched (callers using globally unique dedupe_key prefixes such as
        ``document_parse:<id>`` or ``retrieve:<...>`` get correct cross-queue behaviour).
        """

        if queue_name is not None:
            return self.queue(queue_name).active_job_id(dedupe_key)
        for queue in self._queues.values():
            jid = queue.active_job_id(dedupe_key)
            if jid is not None:
                return jid
        return None

    def status(self) -> dict[str, Any]:
        queues_status: dict[str, dict[str, Any]] = {}
        total_queued = 0
        total_running = 0
        total_workers = 0
        for name in VALID_QUEUE_NAMES:
            snapshot = self._queues[name].status()
            queues_status[name] = snapshot
            total_queued += int(snapshot.get("queued_count") or 0)
            total_running += int(snapshot.get("running_count") or 0)
            total_workers += int(snapshot.get("worker_count") or 0)
        return {
            "queues": queues_status,
            "total": {
                "worker_count": total_workers,
                "queued_count": total_queued,
                "running_count": total_running,
            },
        }


_manager: BackgroundTaskQueueManager | None = None
_manager_lock = threading.Lock()


def _build_worker_counts_from_settings() -> dict[QueueName, int]:
    settings = get_settings()
    interactive = int(getattr(settings, "interactive_job_worker_count", 0) or 0)
    library_parse = int(getattr(settings, "library_parse_job_worker_count", 0) or 0)
    maintenance = int(getattr(settings, "maintenance_job_worker_count", 0) or 0)
    fallback = int(getattr(settings, "background_job_worker_count", 1) or 1)
    # If layered settings were not explicitly tuned (fall back from clamp defaults),
    # boost interactive with the legacy fallback so existing deployments do not regress
    # to a single worker total.
    if interactive <= 0:
        interactive = max(1, fallback)
    if library_parse <= 0:
        library_parse = 1
    if maintenance <= 0:
        maintenance = 1
    return {
        "interactive": max(1, interactive),
        "library_parse": max(1, library_parse),
        "maintenance": max(1, maintenance),
    }


def get_background_task_queue_manager() -> BackgroundTaskQueueManager:
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is None:
            _manager = BackgroundTaskQueueManager(worker_counts=_build_worker_counts_from_settings())
    return _manager


def get_background_task_queue(queue_name: QueueName = DEFAULT_QUEUE_NAME) -> BackgroundTaskQueue:
    """Backward-compatible accessor: defaults to the ``interactive`` queue.

    Existing callers can keep using ``get_background_task_queue()`` and will continue to
    get a single queue object (now the realtime queue).  New code should prefer
    ``get_background_task_queue_manager().submit(queue_name=...)`` for clarity.
    """

    return get_background_task_queue_manager().queue(queue_name)


def reset_background_task_queue_manager() -> None:
    """Drop the cached manager so tests can re-construct it with patched settings.

    Worker threads of the old manager are daemon threads; once GC drops the queue
    they will exit on their next 1s poll timeout.  No graceful shutdown is required
    because all work is idempotent (jobs are persisted in Postgres).
    """

    global _manager
    with _manager_lock:
        _manager = None
