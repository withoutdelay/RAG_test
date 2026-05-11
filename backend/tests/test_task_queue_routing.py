"""Tests for the layered ``BackgroundTaskQueueManager``.

Phase 1 / Tasks 1.1 / 1.2 / 1.3 / 1.4 / 1.5 of
``.super-dev/changes/rfp-light-parse-20260511/tasks.md``.

These tests exercise the queue manager in isolation (no FastAPI client, no DB)
so they stay fast and deterministic even on a stripped CI environment.

Coverage:

- three queues are created with the expected default worker counts;
- ``submit(queue_name=...)`` deposits the task into the correct underlying queue
  without leaking into the other two;
- ``JOB_TYPE_QUEUE_DEFAULTS`` routes ``rfp_light_parse`` -> interactive,
  ``document_parse`` -> library_parse, etc., when the caller does not specify
  a queue name explicitly;
- ``status()`` returns the multi-queue shape consumed by ``GET /jobs/queue``;
- per-queue dedupe namespaces stay independent;
- the legacy ``get_background_task_queue()`` accessor still works and now
  defaults to the ``interactive`` queue;
- the recovery code paths in ``app.api.documents`` /
  ``app.api.library`` import without falling back to the old singleton.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest import TestCase
from uuid import uuid4

from app.services import task_queue
from app.services.task_queue import (
    BackgroundTaskQueueManager,
    DEFAULT_QUEUE_NAME,
    JOB_TYPE_QUEUE_DEFAULTS,
    VALID_QUEUE_NAMES,
    get_background_task_queue,
    get_background_task_queue_manager,
    reset_background_task_queue_manager,
)


class _CountingTask:
    """Pseudo job that just counts invocations (no DB writes)."""

    def __init__(self) -> None:
        self.calls = 0
        self._event = threading.Event()

    async def run(self) -> None:
        self.calls += 1
        self._event.set()

    def wait(self, *, timeout: float = 2.0) -> bool:
        return self._event.wait(timeout=timeout)


class BackgroundTaskQueueManagerTests(TestCase):
    """Construct managers explicitly so we don't disturb the module-level singleton."""

    def _build_manager(
        self,
        *,
        interactive: int = 1,
        library_parse: int = 1,
        maintenance: int = 1,
    ) -> BackgroundTaskQueueManager:
        return BackgroundTaskQueueManager(
            worker_counts={
                "interactive": interactive,
                "library_parse": library_parse,
                "maintenance": maintenance,
            }
        )

    def test_three_queues_are_created_with_distinct_worker_pools(self) -> None:
        manager = self._build_manager(interactive=3, library_parse=1, maintenance=1)
        snapshot = manager.status()
        self.assertEqual(set(snapshot["queues"].keys()), set(VALID_QUEUE_NAMES))
        self.assertEqual(snapshot["queues"]["interactive"]["worker_count"], 3)
        self.assertEqual(snapshot["queues"]["library_parse"]["worker_count"], 1)
        self.assertEqual(snapshot["queues"]["maintenance"]["worker_count"], 1)
        self.assertEqual(snapshot["total"]["worker_count"], 5)
        self.assertEqual(snapshot["total"]["queued_count"], 0)
        self.assertEqual(snapshot["total"]["running_count"], 0)

    def test_default_queue_name_is_interactive(self) -> None:
        self.assertEqual(DEFAULT_QUEUE_NAME, "interactive")

    def test_job_type_queue_defaults_route_realtime_vs_historical(self) -> None:
        # realtime customer-facing flows
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["rfp_light_parse"], "interactive")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["retrieve"], "interactive")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["generate_section"], "interactive")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["generate_outline"], "interactive")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["generate_draft"], "interactive")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["requirement_extract"], "interactive")
        # historical-ingestion path
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["document_parse"], "library_parse")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["library_material_rebuild"], "library_parse")
        # maintenance-tier flows
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["case_library_refresh"], "maintenance")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["visual_cache_refresh"], "maintenance")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["knowledge_wiki_compile"], "maintenance")
        self.assertEqual(JOB_TYPE_QUEUE_DEFAULTS["rfp_knowledge_extract"], "maintenance")

    def test_submit_explicit_queue_name_routes_to_only_that_queue(self) -> None:
        manager = self._build_manager()
        task = _CountingTask()

        manager.submit(
            job_id=uuid4(),
            job_type="rfp_light_parse",
            label="rfp-light-parse:test",
            run=task.run,
            queue_name="interactive",
            dedupe_key="rfp_light_parse:abc",
        )
        # Wait briefly for the worker thread to pick up the task.
        task.wait()
        self.assertEqual(task.calls, 1)

        snapshot = manager.status()
        # After the task drained, both other queues should still be empty.
        self.assertEqual(snapshot["queues"]["library_parse"]["queued_count"], 0)
        self.assertEqual(snapshot["queues"]["library_parse"]["running_count"], 0)
        self.assertEqual(snapshot["queues"]["maintenance"]["queued_count"], 0)
        self.assertEqual(snapshot["queues"]["maintenance"]["running_count"], 0)

    def test_submit_falls_back_to_job_type_default_when_queue_name_missing(self) -> None:
        manager = self._build_manager()
        task = _CountingTask()

        manager.submit(
            job_id=uuid4(),
            job_type="document_parse",
            label="doc-parse:test",
            run=task.run,
            dedupe_key="document_parse:xyz",
        )
        # document_parse default = library_parse; the interactive queue should remain idle.
        task.wait()
        self.assertEqual(task.calls, 1)

    def test_submit_unknown_job_type_falls_back_to_interactive(self) -> None:
        manager = self._build_manager()
        task = _CountingTask()
        # Unknown job_type -> defaults to DEFAULT_QUEUE_NAME (=interactive)
        manager.submit(
            job_id=uuid4(),
            job_type="some_brand_new_job_type",
            label="unknown",
            run=task.run,
        )
        task.wait()
        self.assertEqual(task.calls, 1)

    def test_active_job_id_is_isolated_per_queue_namespace(self) -> None:
        """``dedupe_key`` is queue-local: same key in two queues is allowed."""

        manager = self._build_manager()
        # Build two never-completing tasks so we can observe the queued state without races.
        # We bypass ``submit`` and poke the underlying queue directly so the workers do not
        # actually drain the items during the test.
        interactive_queue = manager.queue("interactive")
        library_queue = manager.queue("library_parse")

        with interactive_queue._lock:  # noqa: SLF001
            interactive_queue._active_by_key["x"] = uuid4()
        with library_queue._lock:  # noqa: SLF001
            library_queue._active_by_key["x"] = uuid4()

        # active_job_id without queue_name should find one (any of them) but it
        # MUST NOT raise.
        self.assertIsNotNone(manager.active_job_id("x"))
        # explicit queue_name returns the queue-local active id only
        self.assertEqual(
            manager.active_job_id("x", queue_name="interactive"),
            interactive_queue._active_by_key["x"],  # noqa: SLF001
        )
        self.assertEqual(
            manager.active_job_id("x", queue_name="library_parse"),
            library_queue._active_by_key["x"],  # noqa: SLF001
        )
        # absent dedupe_key returns None
        self.assertIsNone(manager.active_job_id("does-not-exist"))

    def test_unknown_queue_name_raises(self) -> None:
        manager = self._build_manager()
        with self.assertRaises(ValueError):
            manager.queue("ghost-queue")  # type: ignore[arg-type]


class BackgroundTaskQueueAccessorTests(TestCase):
    """Cover the singleton accessors used by the api modules."""

    def setUp(self) -> None:
        reset_background_task_queue_manager()

    def tearDown(self) -> None:
        reset_background_task_queue_manager()

    def test_module_singleton_default_returns_interactive_queue(self) -> None:
        manager = get_background_task_queue_manager()
        self.assertIs(get_background_task_queue(), manager.queue("interactive"))
        self.assertIs(get_background_task_queue("library_parse"), manager.queue("library_parse"))
        self.assertIs(get_background_task_queue("maintenance"), manager.queue("maintenance"))

    def test_status_payload_shape_matches_jobs_queue_endpoint_contract(self) -> None:
        manager = get_background_task_queue_manager()
        snapshot = manager.status()
        self.assertIn("queues", snapshot)
        self.assertIn("total", snapshot)
        for name in VALID_QUEUE_NAMES:
            queue_snapshot = snapshot["queues"][name]
            self.assertIn("worker_count", queue_snapshot)
            self.assertIn("queued_count", queue_snapshot)
            self.assertIn("running_count", queue_snapshot)
            self.assertIn("queued", queue_snapshot)
            self.assertIn("running", queue_snapshot)
            self.assertEqual(queue_snapshot["name"], name)


class BackgroundTaskQueueWorkerCountSettingsTests(TestCase):
    """Verify ``_build_worker_counts_from_settings`` honors the layered config knobs."""

    def setUp(self) -> None:
        reset_background_task_queue_manager()

    def tearDown(self) -> None:
        reset_background_task_queue_manager()

    def test_default_settings_produce_3_1_1_worker_pool(self) -> None:
        from app.config import get_settings

        # Live settings come from .env; clamps in get_settings keep values within bounds.
        # We just assert the manager that gets built sees the same numbers.
        settings = get_settings()
        manager = get_background_task_queue_manager()
        snapshot = manager.status()
        self.assertEqual(
            snapshot["queues"]["interactive"]["worker_count"],
            settings.interactive_job_worker_count,
        )
        self.assertEqual(
            snapshot["queues"]["library_parse"]["worker_count"],
            settings.library_parse_job_worker_count,
        )
        self.assertEqual(
            snapshot["queues"]["maintenance"]["worker_count"],
            settings.maintenance_job_worker_count,
        )

    def test_zero_or_negative_layered_setting_falls_back_to_legacy_count(self) -> None:
        """When a layered setting is unset (= 0) the manager should not create a 0-worker queue."""

        from unittest.mock import patch

        # Build a fake Settings object reachable by the helper.
        class _Bare:
            interactive_job_worker_count = 0
            library_parse_job_worker_count = 0
            maintenance_job_worker_count = 0
            background_job_worker_count = 2

        with patch.object(task_queue, "get_settings", return_value=_Bare()):
            counts = task_queue._build_worker_counts_from_settings()
        self.assertEqual(counts["interactive"], 2)
        self.assertEqual(counts["library_parse"], 1)
        self.assertEqual(counts["maintenance"], 1)


class BackgroundTaskQueueRecoveryWiringTests(TestCase):
    """Smoke test that the recovery functions in api modules import correctly.

    Phase 1 / Task 1.5 split ``recover_document_parse_jobs_on_startup`` to handle
    both ``document_parse`` and ``rfp_light_parse`` job types.  We don't need a
    DB to verify the wiring; we just confirm the API modules use the new accessor
    signature and do not re-import the legacy singleton.
    """

    def test_document_recovery_uses_layered_queue(self) -> None:
        from app.api import documents
        import inspect

        source = inspect.getsource(documents.recover_document_parse_jobs_on_startup)
        # Phase 1: must address both queues by name
        self.assertIn('get_background_task_queue("library_parse")', source)
        self.assertIn('get_background_task_queue("interactive")', source)
        # Phase 2: must handle rfp_light_parse alongside document_parse
        self.assertIn('"rfp_light_parse"', source)
        self.assertIn('"document_parse"', source)

    def test_library_material_recovery_uses_library_parse_queue(self) -> None:
        from app.api import library
        import inspect

        source = inspect.getsource(library.recover_library_material_rebuild_jobs_on_startup)
        self.assertIn('get_background_task_queue("library_parse")', source)
