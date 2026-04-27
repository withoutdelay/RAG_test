from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from queue import Empty, PriorityQueue
import threading
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.db import get_session_factory
from app.models.job import Job


logger = logging.getLogger(__name__)

AsyncJobCallable = Callable[[], Awaitable[None]]


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
    def __init__(self, *, worker_count: int) -> None:
        self.worker_count = max(1, int(worker_count))
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
                    name=f"background-job-worker-{index + 1}",
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


_queue: BackgroundTaskQueue | None = None


def get_background_task_queue() -> BackgroundTaskQueue:
    global _queue
    if _queue is None:
        _queue = BackgroundTaskQueue(worker_count=get_settings().background_job_worker_count)
    return _queue
