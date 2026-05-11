"""Tests for the four review follow-up fixes addressed in the same MVP.

These cover:

- **#1** ``generate-outline`` / ``generate-sections`` (full draft) now route through
  the ``BackgroundTaskQueueManager`` instead of FastAPI ``background_tasks.add_task``.
  Most of the surface is already covered by ``tests/test_composition_api.py``
  asserting three ``submit()`` calls; here we add a focused source-level guard.
- **#2** ``RfpLightParser`` uses a dedicated ``ThreadPoolExecutor`` (not the
  asyncio default pool) and logs a warning when a thread is left behind on
  timeout.
- **#3** ``recover_document_parse_jobs_on_startup`` refuses to re-route legacy
  ``document_parse`` jobs whose document is an RFP, since that would re-enter
  the heavy ingestion pipeline.
- **#4** HTTP-triggered case library refresh routes through the maintenance
  queue via ``submit_case_library_refresh_job``.
"""

from __future__ import annotations

import inspect
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from app.api import artifacts as artifacts_api
from app.api import documents as documents_api
from app.api import library as library_api
from app.services.knowledge import library_refresh
from app.services.parsing import rfp_light_parser
from app.services.task_queue import (
    get_background_task_queue_manager,
    reset_background_task_queue_manager,
)


# ---------------------------------------------------------------------------
# #1: outline / draft routed through the interactive queue
# ---------------------------------------------------------------------------


class OutlineDraftQueueRoutingSourceTests(TestCase):
    """Static guards so future edits do not regress the queue-routing contract."""

    def test_generate_outline_uses_interactive_queue_submit(self) -> None:
        source = inspect.getsource(artifacts_api.generate_outline)
        # Must NOT silently re-introduce background_tasks.add_task for outline
        self.assertNotIn("background_tasks.add_task", source)
        self.assertIn('get_background_task_queue("interactive")', source)
        self.assertIn('job_type="outline"', source)
        self.assertIn('generate-outline:', source)

    def test_generate_sections_uses_interactive_queue_submit(self) -> None:
        source = inspect.getsource(artifacts_api.generate_sections)
        self.assertNotIn("background_tasks.add_task", source)
        self.assertIn('get_background_task_queue("interactive")', source)
        self.assertIn('job_type="generate_draft"', source)
        self.assertIn('generate-sections:', source)


# ---------------------------------------------------------------------------
# #2: dedicated ThreadPoolExecutor for RFP light parsing
# ---------------------------------------------------------------------------


class RfpLightParserDedicatedExecutorTests(TestCase):
    """``RfpLightParser`` must isolate slow PDFs from the global asyncio pool."""

    def setUp(self) -> None:
        # Reset module-level executor cache between tests so worker count
        # reconfigurations take effect.
        rfp_light_parser._rfp_light_executor = None
        rfp_light_parser._rfp_light_executor_workers = None

    def tearDown(self) -> None:
        rfp_light_parser._rfp_light_executor = None
        rfp_light_parser._rfp_light_executor_workers = None

    def test_get_executor_returns_thread_pool_with_prefix(self) -> None:
        executor = rfp_light_parser._get_rfp_light_executor(max_workers=2)
        self.assertIsInstance(executor, ThreadPoolExecutor)
        # Smoke-test that a submitted task runs on a thread carrying the prefix.
        captured: dict[str, str] = {}

        def _capture() -> None:
            captured["thread_name"] = threading.current_thread().name

        future = executor.submit(_capture)
        future.result(timeout=2.0)
        self.assertTrue(
            captured["thread_name"].startswith("rfp-light-parse"),
            msg=f"unexpected thread name: {captured['thread_name']!r}",
        )

    def test_get_executor_caches_singleton_for_same_worker_count(self) -> None:
        first = rfp_light_parser._get_rfp_light_executor(max_workers=2)
        second = rfp_light_parser._get_rfp_light_executor(max_workers=2)
        self.assertIs(first, second)

    def test_get_executor_replaces_pool_when_worker_count_changes(self) -> None:
        first = rfp_light_parser._get_rfp_light_executor(max_workers=2)
        second = rfp_light_parser._get_rfp_light_executor(max_workers=3)
        self.assertIsNot(first, second)

    def test_parse_uses_dedicated_executor_not_default(self) -> None:
        source = inspect.getsource(rfp_light_parser.RfpLightParser.parse)
        # Strip comment lines so a doc/comment mention of asyncio.to_thread
        # does not false-positive (we explain in a comment WHY we use a
        # dedicated executor instead of the default pool).
        code_lines = [
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        # Must NOT call asyncio.to_thread (which uses the default executor)
        self.assertNotIn("asyncio.to_thread(", code_only)
        # Must explicitly call the dedicated executor helper
        self.assertIn("_get_rfp_light_executor", code_only)
        self.assertIn("run_in_executor", code_only)

    def test_parse_uses_subprocess_hard_timeout(self) -> None:
        """Review R2 #1 / R4 #1: hard timeout via spawn subprocess + SIGTERM/SIGKILL.

        The R2 implementation already replaced the soft asyncio-cancel design
        with a real subprocess + SIGTERM/SIGKILL escalation.  R4 then swapped
        the IPC carrier from a pipe-write of the whole text to a tmpfile so a
        large RFP does not deadlock the pipe.  Both invariants must hold:

        - The subprocess context is ``spawn``.
        - A poll/recv watchdog loop drives the parent.
        - SIGTERM -> SIGKILL escalation is reachable from ``_terminate_subprocess``.
        """

        run_source = inspect.getsource(rfp_light_parser._run_extract_with_subprocess)
        # Watchdog is now the poll/recv loop (R3 #1) backed by tmpfile IPC (R4 #1).
        self.assertIn("parent_conn.poll", run_source)
        # SIGTERM -> SIGKILL escalation must be present
        self.assertIn("_terminate_subprocess", run_source)
        # Operator-facing warning describing the hard timeout
        self.assertIn("logger.warning", run_source)
        self.assertIn('reason="timeout"', run_source)

        terminate_source = inspect.getsource(rfp_light_parser._terminate_subprocess)
        self.assertIn("process.terminate", terminate_source)
        self.assertIn("process.kill", terminate_source)
        # ``_terminate_subprocess`` still uses ``process.join`` to wait for
        # the SIGTERM/SIGKILL to take effect — keep that invariant.
        self.assertIn("process.join", terminate_source)

        # The subprocess context must be ``spawn`` to avoid inheriting parent fds / locks
        module_source = inspect.getsource(rfp_light_parser)
        self.assertIn('multiprocessing.get_context("spawn")', module_source)


# ---------------------------------------------------------------------------
# #3: recovery migrates legacy document_parse on RFP documents to light parser
# ---------------------------------------------------------------------------


class RecoveryHandlesLegacyRfpHeavyPathTests(TestCase):
    """Phase 2 / Review R2 #4: legacy RFPs migrate to the lightweight parser."""

    def _recovery_source(self) -> str:
        return inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)

    def test_recovery_detects_legacy_rfp_doc_type(self) -> None:
        source = self._recovery_source()
        self.assertIn('job.job_type == "document_parse"', source)
        self.assertIn('document.doc_type', source)
        self.assertIn('"rfp"', source)

    def test_recovery_migrates_legacy_rfp_to_light_parser(self) -> None:
        """The branch must close the heavy job AND enqueue a fresh light-parse job."""
        source = self._recovery_source()
        # New stable error_code reflects auto-migration, not a hard fail.
        self.assertIn("MigratedToRfpLightParse", source)
        # A new ``rfp_light_parse`` Job must be created and added to task_specs
        self.assertIn('job_type="rfp_light_parse"', source)
        self.assertIn("migrated_from_job_id", source)
        self.assertIn("migrated_to_rfp_light_parse", source)
        # The branch still increments failed_invalid for the old heavy job (it IS failed).
        self.assertIn("failed_invalid += 1", source)
        # And the migrated rfp_light_parse job must be enqueued via task_specs.
        self.assertIn('task_specs.append', source)

    def test_recovery_legacy_rfp_branch_appears_before_status_routing(self) -> None:
        """The RFP guard must execute BEFORE the parse_status routing below it."""
        source = self._recovery_source()
        migration_idx = source.index("MigratedToRfpLightParse")
        recoverable_idx = source.index("RECOVERABLE_DOCUMENT_PARSE_STATUSES")
        self.assertLess(
            migration_idx,
            recoverable_idx,
            msg="MigratedToRfpLightParse branch must short-circuit before recovery routing.",
        )


# ---------------------------------------------------------------------------
# #4: HTTP case_library_refresh routes through the maintenance queue
# ---------------------------------------------------------------------------


class SubmitCaseLibraryRefreshJobTests(TestCase):
    """``submit_case_library_refresh_job`` must enqueue on the maintenance queue."""

    def setUp(self) -> None:
        reset_background_task_queue_manager()

    def tearDown(self) -> None:
        reset_background_task_queue_manager()

    def test_submit_goes_to_maintenance_queue_with_global_dedupe(self) -> None:
        manager = get_background_task_queue_manager()
        # Probe both queues to make sure the submit only lands on maintenance.
        maintenance_queue = manager.queue("maintenance")
        library_queue = manager.queue("library_parse")

        captured: list[tuple[str, str, str]] = []

        def _capture_submit(queue_name: str):
            original = manager.queue(queue_name).submit

            def _wrapper(**kwargs):
                captured.append((queue_name, kwargs["job_type"], kwargs["dedupe_key"]))
                return kwargs["job_id"]

            return _wrapper

        with patch.object(maintenance_queue, "submit", side_effect=_capture_submit("maintenance")), patch.object(
            library_queue, "submit", side_effect=_capture_submit("library_parse")
        ):
            job_id = library_refresh.submit_case_library_refresh_job()

        self.assertIsNotNone(job_id)
        self.assertEqual(len(captured), 1)
        queue_name, job_type, dedupe_key = captured[0]
        self.assertEqual(queue_name, "maintenance")
        self.assertEqual(job_type, "case_library_refresh")
        self.assertEqual(dedupe_key, "case_library_refresh:global")

    def test_concurrent_submits_collapse_via_dedupe_key(self) -> None:
        manager = get_background_task_queue_manager()
        maintenance_queue = manager.queue("maintenance")
        with patch.object(
            maintenance_queue,
            "submit",
            wraps=maintenance_queue.submit,
        ) as wrapped:
            first = library_refresh.submit_case_library_refresh_job()
            second = library_refresh.submit_case_library_refresh_job()

        self.assertEqual(wrapped.call_count, 2)
        # Second submit should return the same active job_id (dedupe)
        self.assertEqual(first, second)


class HttpHandlersUseMaintenanceQueueForRefreshTests(TestCase):
    """Static guard: the HTTP handlers must NOT use ``background_tasks.add_task``."""

    def test_library_material_import_uses_submit_helper(self) -> None:
        # The specific endpoint that may schedule the refresh on main_indexed route.
        source = inspect.getsource(library_api)
        # Locate the route handler that mentions main_indexed + submit helper
        self.assertIn("submit_case_library_refresh_job", source)
        # The legacy fire-and-forget pattern should not target request_case_library_refresh
        # at HTTP-handler level any more.
        forbidden = "background_tasks.add_task(request_case_library_refresh)"
        self.assertNotIn(forbidden, source)

    def test_delete_document_uses_submit_helper(self) -> None:
        source = inspect.getsource(documents_api.delete_document)
        self.assertIn("submit_case_library_refresh_job", source)
        self.assertNotIn("background_tasks.add_task(request_case_library_refresh)", source)


# ---------------------------------------------------------------------------
# Sanity: __all__ exports for the new helper
# ---------------------------------------------------------------------------


class KnowledgePackageSurfaceTests(TestCase):
    def test_submit_case_library_refresh_job_is_exported(self) -> None:
        from app.services import knowledge

        self.assertIn("submit_case_library_refresh_job", knowledge.__all__)
        self.assertTrue(callable(knowledge.submit_case_library_refresh_job))


# ---------------------------------------------------------------------------
# Round-2 review fixes
# ---------------------------------------------------------------------------


class WorkerInternalRefreshRoutesToMaintenanceQueueTests(TestCase):
    """Review R2 #2: ``library_parse`` workers must NOT await the refresh inline.

    Previously ``_run_document_parse_job`` and the parent rebuild finaliser
    awaited ``request_case_library_refresh()`` directly inside the worker,
    which pinned the worker on the slow refresh and bypassed
    ``MAINTENANCE_JOB_WORKER_COUNT``.  Both call sites now submit a
    ``case_library_refresh`` job onto the maintenance queue instead.
    """

    def test_document_parse_worker_submits_via_maintenance_helper(self) -> None:
        source = inspect.getsource(documents_api._run_document_parse_job)
        self.assertNotIn("await request_case_library_refresh", source)
        self.assertIn("submit_case_library_refresh_job", source)

    def test_library_material_rebuild_finaliser_submits_via_maintenance_helper(self) -> None:
        # The finaliser lives at module scope; assert the file no longer awaits
        # the refresh and the new helper is referenced.
        source = inspect.getsource(library_api)
        self.assertNotIn("await request_case_library_refresh", source)
        # Both refresh sites in this file now use the helper.
        self.assertGreaterEqual(source.count("submit_case_library_refresh_job"), 2)


class InteractiveJobRecoverySourceTests(TestCase):
    """Review R2 #3: stale interactive jobs must be reaped on backend restart."""

    def test_recover_interactive_jobs_function_exists_and_is_async(self) -> None:
        recover = getattr(artifacts_api, "recover_interactive_jobs_on_startup", None)
        self.assertIsNotNone(recover, msg="recover_interactive_jobs_on_startup must be defined")
        self.assertTrue(inspect.iscoroutinefunction(recover))

    def test_recover_covers_all_interactive_job_types(self) -> None:
        from app.api.artifacts import INTERACTIVE_RECOVERABLE_JOB_TYPES

        # Job types that the artifacts router enqueues onto interactive must
        # all be covered.  ``rfp_light_parse`` is intentionally NOT in this
        # list because ``recover_document_parse_jobs_on_startup`` already
        # handles it together with ``document_parse``.
        for job_type in ("retrieve", "outline", "generate_draft", "generate_section"):
            self.assertIn(job_type, INTERACTIVE_RECOVERABLE_JOB_TYPES)
        self.assertNotIn("rfp_light_parse", INTERACTIVE_RECOVERABLE_JOB_TYPES)

    def test_recover_marks_jobs_failed_with_stable_error_code(self) -> None:
        source = inspect.getsource(artifacts_api.recover_interactive_jobs_on_startup)
        self.assertIn('job.status = "failed"', source)
        self.assertIn("StaleAfterRestart", source)
        self.assertIn("stale_after_restart", source)

    def test_lifespan_invokes_interactive_recovery(self) -> None:
        from app import main as main_module

        source = inspect.getsource(main_module.lifespan)
        self.assertIn("recover_interactive_jobs_on_startup", source)


class LegacyRfpAutoMigrateRecoveryTests(TestCase):
    """Review R2 #4: legacy ``document_parse`` jobs on RFPs auto-migrate."""

    def test_recovery_creates_new_rfp_light_parse_job_row(self) -> None:
        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # The migration branch must create a fresh Job(...) with the right job_type
        self.assertIn("Job(", source)
        self.assertIn('job_type="rfp_light_parse"', source)
        # And it must reset the document parse_status so the new job picks up parsing.
        self.assertIn('document.parse_status = "parsing"', source)

    def test_recovery_branch_uses_session_flush_for_new_job_id(self) -> None:
        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # ``await session.flush()`` is required so the migrated job's id is
        # available before pushing onto task_specs.
        self.assertIn("await session.flush()", source)


class RfpHardTimeoutSubprocessIntegrationTests(TestCase):
    """Review R2 #1: spawn-subprocess hard timeout must really run in a child."""

    def test_run_extract_with_subprocess_uses_spawn_context(self) -> None:
        # The module captures the spawn context once at import time.
        self.assertEqual(rfp_light_parser._mp_context.get_start_method(), "spawn")

    def test_terminate_subprocess_returns_terminated_killed_flags(self) -> None:
        from unittest.mock import MagicMock

        process = MagicMock()
        # First check: alive (so we send SIGTERM).  After terminate, dead.
        process.is_alive.side_effect = [True, False]
        terminated, killed = rfp_light_parser._terminate_subprocess(
            process, grace_seconds=0.1
        )
        self.assertTrue(terminated)
        self.assertFalse(killed)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    def test_terminate_subprocess_escalates_to_kill_when_terminate_ignored(self) -> None:
        from unittest.mock import MagicMock

        process = MagicMock()
        # Alive on every check until kill is called.
        process.is_alive.side_effect = [True, True, False]
        terminated, killed = rfp_light_parser._terminate_subprocess(
            process, grace_seconds=0.1
        )
        self.assertTrue(terminated)
        self.assertTrue(killed)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Round-3 review fixes
# ---------------------------------------------------------------------------


class LargeRfpPayloadDoesNotTimeoutTests(TestCase):
    """Review R3 #1: a large RFP must not be misreported as a hard timeout.

    Previous bug: ``_run_extract_with_subprocess`` joined on the child first
    and only ``recv``-ed afterwards.  Because the OS pipe buffer is small
    (16-32KB on macOS), a child writing ~200KB of pickled text would block
    inside ``conn.send``, the child would never exit, and the parent watchdog
    would SIGKILL it as a "timeout".  The fix is twofold:

    1. The child pre-truncates to ``max_chars`` before sending.
    2. The parent drains the pipe via a poll/recv loop concurrently with the
       child's send.
    """

    def setUp(self) -> None:
        rfp_light_parser._rfp_light_executor = None
        rfp_light_parser._rfp_light_executor_workers = None

    def tearDown(self) -> None:
        rfp_light_parser._rfp_light_executor = None
        rfp_light_parser._rfp_light_executor_workers = None

    def _make_large_markdown(self, suffix: str = ".md", *, chars: int = 200_000) -> str:
        from tempfile import NamedTemporaryFile

        # Use ASCII so the encoded size is roughly equal to ``chars``; the
        # point is to exceed the OS pipe buffer so the regression would
        # reproduce without the fix.  Generate plain text rather than markdown
        # headers so the character count is deterministic.
        content = "Requirement A. " * (chars // len("Requirement A. ") + 1)
        content = content[:chars]
        with NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as tmp:
            tmp.write(content)
            return tmp.name

    def test_run_extract_with_subprocess_handles_large_text_under_timeout(self) -> None:
        """Review R4 #1: 200KB markdown round-trips through tmpfile IPC unscathed.

        Previously the child sent the whole text over a multiprocessing Pipe
        and either blocked on the OS pipe buffer (16-32KB on macOS) or
        truncated to ``max_chars``.  Now the child writes the full body to a
        parent-owned tmpfile and the pipe only carries metadata, so the
        parent reads back the entire 200KB without truncation.
        """

        import os

        path = self._make_large_markdown(chars=200_000)
        try:
            text, page_count, truncated_pages, source_format, extras = (
                rfp_light_parser._run_extract_with_subprocess(
                    path,
                    max_pages=30,
                    max_seconds=15.0,
                )
            )
            self.assertEqual(source_format, "text")
            self.assertEqual(page_count, 0)
            self.assertFalse(truncated_pages)
            # The parent reads the full body back from the tmpfile — no
            # silent truncation anywhere in the chain.
            self.assertEqual(len(text), 200_000)
            # The metadata extras report the same char_count back as a hint.
            self.assertEqual(extras.get("reported_char_count"), 200_000)
        finally:
            os.unlink(path)

    def test_parse_large_markdown_returns_full_body_not_truncated(self) -> None:
        """End-to-end: ``RfpLightParser.parse`` on a 200KB file returns full body.

        Review R4 #1: the parser must not silently truncate to ``max_chars``.
        The downstream "feed at most N chars to the LLM" decision lives on
        ``RfpLightParseResult.excerpt`` (still a head-slice today, replaced
        by section-aware sampling under F3).
        """

        import os

        parser = rfp_light_parser.RfpLightParser(
            settings=_LargeFileSettings(max_seconds=15.0, max_chars=80_000),
        )
        path = self._make_large_markdown(chars=200_000)
        try:
            result = _run_async(parser.parse(path))
            self.assertEqual(result.source_format, "text")
            # Full body preserved, never truncated by the parser itself.
            self.assertFalse(result.truncated_chars)
            self.assertEqual(result.char_count, 200_000)
            self.assertEqual(len(result.text), 200_000)
            # Excerpt is the bounded slice for the LLM prompt; max_chars is
            # no longer involved in truncating ``text``.
            self.assertLessEqual(len(result.excerpt), 20_000)
        finally:
            os.unlink(path)

    def test_parent_loop_drives_poll_then_recv_not_join_first(self) -> None:
        """Static guard: the parent must drive a poll/recv loop, not join first.

        Without this property a large IPC payload deadlocks the child on
        ``conn.send`` and the parent on ``process.join``.  We assert the
        relative ordering of the first ``parent_conn.poll`` call vs the
        ``parent_conn.recv()`` call in the executable body of the function
        (docstrings are stripped before the search).
        """

        import inspect

        raw_source = inspect.getsource(rfp_light_parser._run_extract_with_subprocess)
        # Strip the docstring so example code inside it does not false-match.
        # The function has a single triple-quoted docstring at the top.
        lines = raw_source.splitlines()
        body_lines: list[str] = []
        in_docstring = False
        triple = '"""'
        for line in lines:
            stripped = line.strip()
            if not in_docstring and stripped.startswith(triple):
                in_docstring = True
                if stripped.count(triple) >= 2:
                    in_docstring = False
                continue
            if in_docstring:
                if triple in stripped:
                    in_docstring = False
                continue
            body_lines.append(line)
        body = "\n".join(body_lines)

        poll_idx = body.index("parent_conn.poll(timeout=poll_wait)")
        recv_idx = body.index("payload = parent_conn.recv()")
        # The poll must come before recv inside the watchdog loop.
        self.assertLess(poll_idx, recv_idx)
        # The legacy "join the whole timeout, then recv" shape must not regress.
        self.assertNotIn("process.join(timeout=max_seconds)", body)


class LegacyRfpMigrationPersistsValidJobRowTests(TestCase):
    """Review R3 #2: migrated Job rows must satisfy NOT NULL constraints."""

    def test_migration_branch_sets_trace_id_and_id(self) -> None:
        import inspect

        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # The migration branch creates ``new_job = Job(id=new_job_id, ..., trace_id=...)``
        # so flush() does not violate NOT NULL on ``trace_id`` and the
        # ``migrated_to_job_id`` written into output_ref matches the row id.
        self.assertIn("id=new_job_id", source)
        self.assertIn("trace_id=uuid.uuid4().hex", source)


class TmpfileIpcAvoidsPipeBlockAndTruncationTests(TestCase):
    """Review R4 #1: full text travels through a tmpfile, not the pipe.

    Two static guards plus a runtime guard:

    - The subprocess entrypoint signature must take ``output_text_path``
      (not the legacy ``max_chars``) so we cannot accidentally regress to
      truncating in the child.
    - ``_run_extract_with_subprocess`` must create a tmpfile and clean it up.
    - The parser must not call ``text[:max_chars]`` anywhere in its body.
    """

    def test_subprocess_entrypoint_signature_carries_output_text_path(self) -> None:
        import inspect

        sig = inspect.signature(rfp_light_parser._subprocess_extract_entrypoint)
        params = list(sig.parameters.keys())
        # We require the new parameter and forbid the legacy max_chars hack.
        self.assertIn("output_text_path", params)
        self.assertNotIn("max_chars", params)

    def test_run_extract_with_subprocess_uses_named_temporary_file(self) -> None:
        import inspect

        source = inspect.getsource(rfp_light_parser._run_extract_with_subprocess)
        # tmpfile creation, child receives path, parent reads back, finally cleanup.
        self.assertIn("tempfile.NamedTemporaryFile", source)
        self.assertIn("tmpfile_path", source)
        self.assertIn("read_text(encoding=\"utf-8\")", source)
        self.assertIn("os.unlink(tmpfile_path)", source)

    def test_parser_parse_does_not_truncate_text_to_max_chars(self) -> None:
        """``result.text`` is the full body — no ``text[:max_chars]`` slice."""

        import inspect

        source = inspect.getsource(rfp_light_parser.RfpLightParser.parse)
        # The legacy "if len(text) > max_chars: text = text[:max_chars]" shape
        # must not return.  ``excerpt_chars`` is fine since that is the
        # LLM-input bound, not a destructive truncation of result.text.
        self.assertNotIn("text = text[:max_chars]", source)
        self.assertNotIn("text[:max_chars]", source)
        # And the RFP semantic doc string must reflect the no-truncation contract.
        self.assertIn("Review R4 #1", source)


class FullDraftStaleRecoveryCoversDbJobTypeTests(TestCase):
    """Review R3 #3: the full-draft DB ``job_type`` must be reaped on restart."""

    def test_db_job_type_generate_is_included(self) -> None:
        from app.api.artifacts import INTERACTIVE_RECOVERABLE_JOB_TYPES

        # ``SectionDraftService.start_section_generation_job`` writes
        # job_type="generate" (the queue submit uses "generate_draft" but the
        # DB row uses "generate").  Both names must be covered.
        self.assertIn("generate", INTERACTIVE_RECOVERABLE_JOB_TYPES)
        self.assertIn("generate_draft", INTERACTIVE_RECOVERABLE_JOB_TYPES)

    def test_db_job_type_matches_section_service_value(self) -> None:
        """Source-level invariant: the DB job_type literal stays in sync."""

        import inspect

        from app.services.composition import section_service

        from app.api.artifacts import INTERACTIVE_RECOVERABLE_JOB_TYPES

        # Find all ``job_type="..."`` literals in section_service and verify
        # the full-draft one ("generate") is covered by the recovery list.
        section_source = inspect.getsource(section_service)
        self.assertIn('job_type="generate"', section_source)
        self.assertIn("generate", INTERACTIVE_RECOVERABLE_JOB_TYPES)


class RecoveryFinalisesDedupeCollidingJobsTests(TestCase):
    """Review R4 #2: a recovered job that collides with an active dedupe key
    must NOT stay queued forever — it should be marked
    ``status="failed" + error_code="DuplicateRecoveredJob"`` and reference
    the surviving job id in ``output_ref.deduplicated_to_job_id``.
    """

    def test_recovery_checks_submit_return_value(self) -> None:
        import inspect

        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # The recovery loop must capture the return of ``submit(...)`` so it
        # can detect dedupe collisions.  Two variables are common: the
        # caller-side ``job_id`` we tried to register, and ``submitted_job_id``
        # which is what the queue actually points the dedupe key at.
        self.assertIn("submitted_job_id = ", source)
        self.assertIn("submitted_job_id != job_id", source)

    def test_recovery_finalises_duplicate_with_stable_error_code(self) -> None:
        import inspect

        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # The finalisation branch must produce a stable, machine-readable
        # error code AND record where the request was deduplicated to.
        self.assertIn("DuplicateRecoveredJob", source)
        self.assertIn("deduplicated_to_job_id", source)
        # The DB row is moved to ``failed`` (not silently stuck on queued).
        self.assertIn('dup_job.status = "failed"', source)

    def test_recovery_returns_duplicates_deduplicated_count(self) -> None:
        import inspect

        source = inspect.getsource(documents_api.recover_document_parse_jobs_on_startup)
        # The return dict surfaces the new counter for observability.
        self.assertIn("duplicates_deduplicated", source)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _LargeFileSettings:
    """Duck-typed settings stand-in used by R3/R4 large-file tests.

    Review R4 #1: ``max_chars`` is still accepted on Settings for backward
    compatibility but the parser no longer truncates ``result.text``.  The
    setting is retained here for the duck-type interface only.
    """

    def __init__(self, *, max_seconds: float, max_chars: int) -> None:
        self.rfp_light_parse_max_seconds = max_seconds
        self.rfp_light_parse_max_pages = 30
        self.rfp_light_parse_max_chars = max_chars
        self.rfp_light_parse_excerpt_chars = min(20_000, max_chars)
        self.rfp_light_parse_max_workers = 2


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)
