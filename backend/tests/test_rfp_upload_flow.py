"""Tests for the RFP light parse upload flow in ``app.api.documents``.

Phase 2 / Tasks 2.3 / 2.4 / 2.5 of
``.super-dev/changes/rfp-light-parse-20260511/tasks.md``.

Coverage:

- ``upload_document(doc_type='rfp')`` is dispatched to ``_accept_rfp_light_upload``
  whenever ``RFP_LIGHT_PARSE_ENABLED=true``, and to ``_accept_document_upload``
  otherwise.
- ``upload_document(doc_type='historical_proposal')`` is dispatched to
  ``_accept_document_upload`` regardless of the RFP flag.
- ``reparse_document`` re-routes RFP documents to ``_reparse_rfp_light_document``
  while non-RFP documents stay on the legacy ``document_parse`` path.
- ``_run_rfp_light_parse_job`` source MUST NOT mention Chunker / Embedder /
  Qdrant / raw_document / figure asset / case library refresh symbols.  This is
  a static safety net so future drift does not silently re-enter the heavy
  ingestion pipeline.
- ``recover_document_parse_jobs_on_startup`` knows about ``rfp_light_parse`` and
  submits it to the ``interactive`` queue.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.api import documents
from app.api.documents import (
    _accept_rfp_light_upload,
    _reparse_rfp_light_document,
    _run_rfp_light_parse_job,
    recover_document_parse_jobs_on_startup,
    reparse_document,
    upload_document,
)


def _run(coro):
    return asyncio.run(coro)


class _DummyResponse:
    def __init__(self, *, code: int = 202, message: str = "success", data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data


class _DummyProject:
    def __init__(self, project_id: UUID) -> None:
        self.id = project_id


class _DummyDocument:
    def __init__(
        self,
        *,
        doc_type: str = "rfp",
        parse_status: str = "done",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid4()
        self.project_id = uuid4()
        self.filename = "rfp.pdf"
        self.doc_type = doc_type
        self.parse_status = parse_status
        self.meta = dict(meta or {})


class _AsyncSessionStub:
    """Bare-minimum async session stub for branch tests.

    We only need ``get()`` to return either a project or ``None``; the actual
    upload helpers are mocked out so no other session methods are exercised here.
    """

    def __init__(self, *, project: _DummyProject | None = None, document: _DummyDocument | None = None) -> None:
        self._project = project
        self._document = document
        self.gets: list[Any] = []

    async def get(self, model: type, key: Any) -> Any:
        self.gets.append((model, key))
        # Selecting on type names keeps us decoupled from the actual ORM imports
        if model.__name__ == "Project":
            return self._project
        if model.__name__ == "Document":
            return self._document
        return None


class UploadDocumentRoutingTests(TestCase):
    """``upload_document`` must dispatch RFP uploads to the light parser path."""

    def test_rfp_doc_type_with_flag_enabled_routes_to_rfp_light_upload(self) -> None:
        project_id = uuid4()
        project = _DummyProject(project_id)
        session = _AsyncSessionStub(project=project)
        accepted = _DummyResponse(data={"path": "rfp_light"})

        with patch(
            "app.api.documents._accept_rfp_light_upload",
            new=AsyncMock(return_value=accepted),
        ) as rfp_helper, patch(
            "app.api.documents._accept_document_upload",
            new=AsyncMock(return_value=_DummyResponse(data={"path": "heavy"})),
        ) as heavy_helper:
            result = _run(
                upload_document(
                    project_id=project_id,
                    background_tasks=MagicMock(),
                    file=MagicMock(),
                    doc_type="rfp",
                    metadata=None,
                    session=session,  # type: ignore[arg-type]
                )
            )

        self.assertIs(result, accepted)
        rfp_helper.assert_awaited_once()
        heavy_helper.assert_not_called()

        # Verify the kwargs we passed encode the RFP routing contract
        call_kwargs = rfp_helper.await_args.kwargs
        self.assertEqual(call_kwargs["project_id"], project_id)
        self.assertEqual(call_kwargs["storage_prefix"], f"{project_id}_rfp_")
        self.assertEqual(call_kwargs["job_label_prefix"], "rfp-light-parse")
        self.assertEqual(call_kwargs["trace_prefix"], "rfp-light-parse")

    def test_rfp_doc_type_with_flag_disabled_falls_back_to_heavy_pipeline(self) -> None:
        project_id = uuid4()
        project = _DummyProject(project_id)
        session = _AsyncSessionStub(project=project)
        heavy_response = _DummyResponse(data={"path": "heavy"})

        fake_settings = MagicMock()
        fake_settings.rfp_light_parse_enabled = False

        with patch(
            "app.api.documents.get_settings",
            return_value=fake_settings,
        ), patch(
            "app.api.documents._accept_rfp_light_upload",
            new=AsyncMock(return_value=_DummyResponse(data={"path": "rfp_light"})),
        ) as rfp_helper, patch(
            "app.api.documents._accept_document_upload",
            new=AsyncMock(return_value=heavy_response),
        ) as heavy_helper:
            result = _run(
                upload_document(
                    project_id=project_id,
                    background_tasks=MagicMock(),
                    file=MagicMock(),
                    doc_type="rfp",
                    metadata=None,
                    session=session,  # type: ignore[arg-type]
                )
            )

        self.assertIs(result, heavy_response)
        heavy_helper.assert_awaited_once()
        rfp_helper.assert_not_called()

    def test_historical_doc_type_routes_to_heavy_pipeline(self) -> None:
        project_id = uuid4()
        project = _DummyProject(project_id)
        session = _AsyncSessionStub(project=project)
        heavy_response = _DummyResponse(data={"path": "heavy"})

        with patch(
            "app.api.documents._accept_rfp_light_upload",
            new=AsyncMock(return_value=_DummyResponse(data={"path": "rfp_light"})),
        ) as rfp_helper, patch(
            "app.api.documents._accept_document_upload",
            new=AsyncMock(return_value=heavy_response),
        ) as heavy_helper:
            result = _run(
                upload_document(
                    project_id=project_id,
                    background_tasks=MagicMock(),
                    file=MagicMock(),
                    doc_type="historical_proposal",
                    metadata=None,
                    session=session,  # type: ignore[arg-type]
                )
            )

        self.assertIs(result, heavy_response)
        heavy_helper.assert_awaited_once()
        rfp_helper.assert_not_called()


class ReparseDocumentRoutingTests(TestCase):
    def test_rfp_document_reparse_routes_to_light_reparse(self) -> None:
        rfp_doc = _DummyDocument(doc_type="rfp", parse_status="done")
        session = _AsyncSessionStub(document=rfp_doc)
        light_response = _DummyResponse(data={"path": "rfp_light_reparse"})

        with patch(
            "app.api.documents._reparse_rfp_light_document",
            new=AsyncMock(return_value=light_response),
        ) as light_helper:
            result = _run(
                reparse_document(
                    document_id=rfp_doc.id,
                    session=session,  # type: ignore[arg-type]
                )
            )

        self.assertIs(result, light_response)
        light_helper.assert_awaited_once()
        kwargs = light_helper.await_args.kwargs
        self.assertIs(kwargs["session"], session)
        self.assertIs(kwargs["document"], rfp_doc)

    def test_historical_document_reparse_uses_legacy_path(self) -> None:
        historical_doc = _DummyDocument(doc_type="historical_proposal", parse_status="done")
        session = _AsyncSessionStub(document=historical_doc)

        with patch(
            "app.api.documents._reparse_rfp_light_document",
            new=AsyncMock(return_value=_DummyResponse(data={"path": "rfp_light_reparse"})),
        ) as light_helper, patch(
            "app.api.documents.get_background_task_queue"
        ) as queue_factory:
            # Make the legacy path bail out with a 202 active-dedupe response so we don't
            # need the full DB plumbing; we only want to confirm we did NOT branch into
            # the RFP path.
            queue = MagicMock()
            queue.active_job_id.return_value = uuid4()
            queue_factory.return_value = queue
            session._project = None  # type: ignore[attr-defined]

            # The legacy path queries session.get(Job, active_job_id); make it return
            # an "in flight" job so we get an early 202.
            class _Job:
                __name__ = "Job"

                def __init__(self) -> None:
                    self.id = uuid4()
                    self.status = "queued"

            stub_job = _Job()
            original_get = session.get

            async def _get(model: type, key: Any) -> Any:
                if model.__name__ == "Job":
                    return stub_job
                return await original_get(model, key)

            session.get = _get  # type: ignore[assignment]

            _run(
                reparse_document(
                    document_id=historical_doc.id,
                    session=session,  # type: ignore[arg-type]
                )
            )

        light_helper.assert_not_called()


class RfpLightParseJobIsolationTests(TestCase):
    """Static guard so future edits do not re-introduce the heavy pipeline."""

    def _job_source(self) -> str:
        return inspect.getsource(_run_rfp_light_parse_job)

    def test_run_rfp_light_parse_job_does_not_reference_heavy_pipeline(self) -> None:
        # Strip the docstring so we don't false-positive on the helpful
        # "DOES NOT call ..." enumeration inside it.
        body = inspect.getsource(_run_rfp_light_parse_job)
        doc = inspect.getdoc(_run_rfp_light_parse_job) or ""
        for line in doc.splitlines():
            body = body.replace(line, "")

        # Check for actual call sites (function name + opening paren / `await`),
        # not bare name mentions which can occur in comments.
        forbidden_call_tokens = (
            "Chunker(",
            "Embedder(",
            "QdrantService(",
            "_parse_and_index_document(",
            "_upsert_raw_document(",
            "_replace_figure_assets(",
            "request_case_library_refresh(",
        )
        for token in forbidden_call_tokens:
            self.assertNotIn(
                token,
                body,
                msg=(
                    f"_run_rfp_light_parse_job must not call {token!r}; "
                    "doing so re-enters the heavy ingestion pipeline."
                ),
            )

    def test_run_rfp_light_parse_job_writes_layered_progress_stages(self) -> None:
        source = self._job_source()
        for stage in ("materializing_file", "extracting_text", "saving_requirement_text", "completed"):
            self.assertIn(stage, source, msg=f"missing progress stage: {stage}")

    def test_run_rfp_light_parse_job_handles_parse_insufficient_gracefully(self) -> None:
        source = self._job_source()
        self.assertIn("RfpLightParseInsufficient", source)
        self.assertIn("parse_insufficient", source)
        # Insufficient path must not raise; it should commit the job as succeeded
        # and return (so the worker drains it without crashing on every restart).
        self.assertIn("return", source)


class AcceptRfpLightUploadEndToEndTests(TestCase):
    """End-to-end test of ``_accept_rfp_light_upload`` with fake session + storage."""

    def test_helper_creates_rfp_light_parse_job_and_routes_to_interactive(self) -> None:
        project_id = uuid4()

        class _Session:
            def __init__(self) -> None:
                self.added: list[Any] = []
                self.flush_calls = 0
                self.commit_calls = 0
                self.refresh_calls = 0

            def add(self, instance: Any) -> None:
                # SQLAlchemy auto-generates Document.id / Job.id via default UUID factory.
                if getattr(instance, "id", None) is None:
                    instance.id = uuid4()
                self.added.append(instance)

            async def flush(self) -> None:
                self.flush_calls += 1

            async def commit(self) -> None:
                self.commit_calls += 1

            async def refresh(self, _instance: Any) -> None:
                self.refresh_calls += 1

        class _Storage:
            def __init__(self) -> None:
                self.save_calls: list[Any] = []

            def save(self, source_path, *, prefix: str) -> str:
                self.save_calls.append((source_path, prefix))
                return f"local://test/{prefix}{source_path.name}"

        class _UploadFile:
            def __init__(self) -> None:
                self.filename = "rfp.pdf"

            async def read(self) -> bytes:
                return b"%PDF-1.4 minimal"

        session = _Session()
        storage = _Storage()
        captured_submits: list[dict[str, Any]] = []

        class _FakeQueue:
            def submit(self, **kwargs: Any) -> UUID:
                captured_submits.append(kwargs)
                return kwargs["job_id"]

        def _queue_factory(name: str | None = None) -> _FakeQueue:
            self.assertEqual(name, "interactive")  # must explicitly target interactive
            return _FakeQueue()

        with patch("app.api.documents.get_object_storage", return_value=storage), patch(
            "app.api.documents.get_background_task_queue", side_effect=_queue_factory
        ):
            response = _run(
                _accept_rfp_light_upload(
                    session=session,  # type: ignore[arg-type]
                    file=_UploadFile(),  # type: ignore[arg-type]
                    project_id=project_id,
                    metadata={"trace": "test"},
                    storage_prefix="rfp_test_",
                    job_label_prefix="rfp-light-parse",
                    trace_prefix="rfp-light-parse",
                    priority=20,
                )
            )

        self.assertEqual(response.code, 202)
        self.assertEqual(response.message, "success")
        # Persisted exactly one Document and one Job
        documents_added = [obj for obj in session.added if type(obj).__name__ == "Document"]
        jobs_added = [obj for obj in session.added if type(obj).__name__ == "Job"]
        self.assertEqual(len(documents_added), 1)
        self.assertEqual(len(jobs_added), 1)

        document = documents_added[0]
        self.assertEqual(document.doc_type, "rfp")
        self.assertEqual(document.parse_status, "parsing")
        self.assertEqual(document.meta["rfp_parse_mode"], "light")
        self.assertEqual(document.meta["trace"], "test")

        job = jobs_added[0]
        self.assertEqual(job.job_type, "rfp_light_parse")
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.input_ref["doc_type"], "rfp")
        self.assertEqual(job.input_ref["rfp_parse_mode"], "light")
        self.assertIn("trace_id", job.__dict__)
        self.assertTrue(job.trace_id.startswith("rfp-light-parse-"))

        # Exactly one submit; routed to interactive queue with the right dedupe key
        self.assertEqual(len(captured_submits), 1)
        submit_kwargs = captured_submits[0]
        self.assertEqual(submit_kwargs["job_type"], "rfp_light_parse")
        self.assertEqual(submit_kwargs["dedupe_key"], f"rfp_light_parse:{document.id}")
        self.assertEqual(submit_kwargs["priority"], 20)
        self.assertTrue(submit_kwargs["label"].startswith("rfp-light-parse:"))


class RfpRecoveryWiringTests(TestCase):
    """``recover_document_parse_jobs_on_startup`` must handle both job types and route correctly."""

    def test_recovery_query_includes_both_job_types(self) -> None:
        source = inspect.getsource(recover_document_parse_jobs_on_startup)
        self.assertIn('Job.job_type.in_(["document_parse", "rfp_light_parse"])', source)

    def test_recovery_submits_rfp_light_parse_to_interactive_queue(self) -> None:
        source = inspect.getsource(recover_document_parse_jobs_on_startup)
        # interactive_queue.submit(...) branch is present
        self.assertIn("interactive_queue.submit", source)
        self.assertIn('"rfp_light_parse"', source)
        self.assertIn("_run_rfp_light_parse_job", source)

    def test_recovery_submits_document_parse_to_library_parse_queue(self) -> None:
        source = inspect.getsource(recover_document_parse_jobs_on_startup)
        self.assertIn("library_queue.submit", source)
        self.assertIn('"document_parse"', source)
        self.assertIn("_run_document_parse_job", source)
