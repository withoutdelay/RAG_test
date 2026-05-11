"""Tests for ``RequirementService`` reading the RFP light parse context.

Phase 3 / Tasks 3.1 / 3.2 of
``.super-dev/changes/rfp-light-parse-20260511/tasks.md``.

These tests cover:

- ``_load_source_context`` dispatches ``doc_type='rfp'`` to the light path and
  every other doc_type to the legacy chunk path;
- ``_load_rfp_light_context`` prefers ``meta.rfp_text_storage_path`` (with
  requirement-aware selection over the *full* RFP body) over the legacy
  ``meta.rfp_text_excerpt`` head slice, which itself takes precedence over
  legacy ``Chunk`` rows (Review R4 #3);
- when both meta and chunks are empty the helper returns the empty fallback;
- ``extract_requirement_card`` refuses to generate a card while the RFP is
  still being parsed AND the project has no description;
- ``extract_requirement_card`` falls back to ``project.description`` when the
  RFP is still parsing but the description is present (confidence drops to
  0.55 and a ``rfp_parse_pending`` clarification item is added).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from app.services.requirement.service import (
    RFP_PARSE_PENDING_ITEM_ID,
    RequirementService,
)
from app.services.v2_errors import ArtifactValidationError


def _run(coro):
    return asyncio.run(coro)


class _FakeChunk:
    def __init__(self, *, chunk_id: UUID, content: str, heading_path: str = "", chunk_index: int = 0) -> None:
        self.id = chunk_id
        self.content = content
        self.heading_path = heading_path
        self.chunk_index = chunk_index


class _FakeScalarResult:
    """Mimics the awaitable result of ``AsyncSession.scalars(...)``."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeSession:
    """Lightweight async session stub for RequirementService unit tests."""

    def __init__(
        self,
        *,
        chunks: list[_FakeChunk] | None = None,
        max_version: int | None = None,
    ) -> None:
        self._chunks = chunks or []
        self._max_version = max_version
        self.added: list[Any] = []
        self.commits = 0
        self.flushes = 0

    async def scalars(self, _stmt: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._chunks)

    async def scalar(self, _stmt: Any) -> Any:
        return self._max_version

    async def get(self, _model: type, _key: Any) -> Any:
        return None

    def add(self, instance: Any) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = uuid4()
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _instance: Any) -> None:
        return None


def _rfp_document(
    *,
    meta: dict[str, Any] | None = None,
    parse_status: str = "done",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        filename="rfp.pdf",
        doc_type="rfp",
        parse_status=parse_status,
        meta=dict(meta or {}),
    )


def _non_rfp_document(*, doc_type: str = "historical_proposal") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        filename="historical.pdf",
        doc_type=doc_type,
        parse_status="done",
        meta={},
    )


class LoadRfpLightContextTests(TestCase):
    """Coverage for the four priority branches inside ``_load_rfp_light_context``."""

    def setUp(self) -> None:
        self.service = RequirementService()

    def test_legacy_excerpt_branch_is_used_when_storage_path_missing(self) -> None:
        """Review R4 #3: ``rfp_text_excerpt`` is now a *fallback* path.

        Documents parsed by versions before R4 #1 have ``rfp_text_excerpt``
        set but no ``rfp_text_storage_path`` on disk.  We still surface
        their excerpt rather than returning empty.
        """

        document = _rfp_document(meta={"rfp_text_excerpt": "需求一：支持高压变频改造。"})
        session = _FakeSession()
        excerpt, refs = _run(
            self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "需求一：支持高压变频改造。")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["document_id"], str(document.id))
        self.assertEqual(refs[0]["heading_path"], "rfp_text_excerpt")

    def test_storage_path_branch_reads_full_text_file(self) -> None:
        with NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write("需求二：电机功率 ≥ 2.5MW。\n需求三：保护等级 IP55。".encode("utf-8"))
            tmp_path = Path(tmp.name)

        document = _rfp_document(meta={"rfp_text_storage_path": str(tmp_path)})
        session = _FakeSession()
        try:
            excerpt, refs = _run(
                self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                    session=session,  # type: ignore[arg-type]
                    document=document,
                )
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        self.assertIn("需求二", excerpt)
        self.assertIn("需求三", excerpt)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["document_id"], str(document.id))

    def test_storage_path_wins_over_legacy_excerpt(self) -> None:
        """Review R4 #3: when both are present ``rfp_text_storage_path`` wins.

        The previous implementation short-circuited on
        ``meta.rfp_text_excerpt`` (a mechanical ``text[:excerpt_chars]`` head
        slice) so any requirement past the head slice never reached the
        requirement-card LLM prompt.  The new priority order reads the full
        RFP body off disk first and lets the requirement-aware selector
        decide which paragraphs to surface.
        """

        with NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write("项目概况：电机改造。\n\n技术要求：必须 ≥ 2.5MW。".encode("utf-8"))
            tmp_path = Path(tmp.name)

        document = _rfp_document(
            meta={
                "rfp_text_excerpt": "head-slice ignored once storage path is available.",
                "rfp_text_storage_path": str(tmp_path),
            }
        )
        session = _FakeSession()
        try:
            excerpt, _refs = _run(
                self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                    session=session,  # type: ignore[arg-type]
                    document=document,
                )
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        # The storage-path body wins over the legacy head-slice.
        self.assertIn("电机改造", excerpt)
        self.assertIn("技术要求", excerpt)
        self.assertNotIn("head-slice ignored", excerpt)

    def test_back_half_requirements_survive_smart_selection(self) -> None:
        """Review R4 #3: requirements past the head slice must reach the LLM.

        The regression we are guarding against: a 60,000-character RFP whose
        关键技术要求 section sits at offset ~50,000 (well past the 20,000-char
        ``rfp_light_parse_excerpt_chars`` head slice).  With the old
        head-slice behaviour the technical requirement disappeared; the new
        selector keeps the section heading and requirement markers no matter
        where they appear in the body.
        """

        # Build a long, otherwise unremarkable body so the requirement-bearing
        # section sits well past the head-slice cap.  We use plain prose
        # without any heading keywords / requirement markers so the head
        # filler scores 0 and is NOT preferred by the selector — we want
        # the back-half requirement paragraph to win on signal alone.
        head_filler = ("此处为大量背景描述。" * 1500)
        # Centre block: ~30k more of plain prose so we are well past the
        # 20k head-slice cap and the back half is genuinely "back half".
        middle_filler = "本节描述材料采购历史与公司沿革。" * 2200
        # Real requirement parked at the back half — heading + numeric spec
        # + requirement marker.
        back_half_requirement = (
            "技术要求：投标人提供的核心电机必须 ≥ 2.5MW，且保护等级不低于 IP55。"
        )
        full_text = "\n\n".join([head_filler, middle_filler, back_half_requirement])
        # Sanity: body is well past the default 20k cap so we are exercising
        # the case the regression is about.
        self.assertGreater(len(full_text), 50_000)

        with NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(full_text.encode("utf-8"))
            tmp_path = Path(tmp.name)

        document = _rfp_document(
            meta={
                "rfp_text_excerpt": head_filler[:20_000],  # what R4 #1 wrote
                "rfp_text_storage_path": str(tmp_path),
            }
        )
        session = _FakeSession()
        try:
            excerpt, _refs = _run(
                self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                    session=session,  # type: ignore[arg-type]
                    document=document,
                )
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        # The whole point: the back-half requirement reaches the prompt.
        self.assertIn("≥ 2.5MW", excerpt)
        self.assertIn("IP55", excerpt)
        self.assertIn("技术要求", excerpt)
        # And the prose-only middle filler is NOT what we surfaced.
        self.assertNotIn("公司沿革", excerpt)

    def test_legacy_chunks_fallback_when_meta_is_empty(self) -> None:
        document = _rfp_document(meta={})
        chunk = _FakeChunk(chunk_id=uuid4(), content="老 RFP 的旧 chunk 文本", heading_path="第1章")
        session = _FakeSession(chunks=[chunk])

        excerpt, refs = _run(
            self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "老 RFP 的旧 chunk 文本")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["chunk_id"], str(chunk.id))
        self.assertEqual(refs[0]["heading_path"], "第1章")

    def test_all_empty_returns_empty_tuple(self) -> None:
        document = _rfp_document(meta={})
        session = _FakeSession(chunks=[])
        excerpt, refs = _run(
            self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "")
        self.assertEqual(refs, [])

    def test_storage_path_missing_file_falls_back_to_chunks(self) -> None:
        document = _rfp_document(
            meta={"rfp_text_storage_path": "/does/not/exist/abc.txt"}
        )
        chunk = _FakeChunk(chunk_id=uuid4(), content="legacy fallback", chunk_index=0)
        session = _FakeSession(chunks=[chunk])
        excerpt, _refs = _run(
            self.service._load_rfp_light_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "legacy fallback")


class LoadSourceContextDispatchTests(TestCase):
    def setUp(self) -> None:
        self.service = RequirementService()

    def test_none_document_returns_empty(self) -> None:
        excerpt, refs = _run(
            self.service._load_source_context(  # type: ignore[attr-defined]
                session=_FakeSession(),  # type: ignore[arg-type]
                document=None,
            )
        )
        self.assertEqual(excerpt, "")
        self.assertEqual(refs, [])

    def test_rfp_doc_dispatches_to_light_helper(self) -> None:
        document = _rfp_document(meta={"rfp_text_excerpt": "x"})
        session = _FakeSession()

        async def _stub_light(*, session, document, max_chunks):  # type: ignore[no-redef]
            return ("light", [{"document_id": str(document.id), "marker": "light"}])

        async def _stub_chunks(*, session, document, max_chunks):  # type: ignore[no-redef]
            return ("CHUNKS", [{"document_id": str(document.id), "marker": "chunks"}])

        self.service._load_rfp_light_context = _stub_light  # type: ignore[assignment]
        self.service._load_chunk_context = _stub_chunks  # type: ignore[assignment]
        excerpt, refs = _run(
            self.service._load_source_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "light")
        self.assertEqual(refs[0]["marker"], "light")

    def test_historical_doc_dispatches_to_chunk_helper(self) -> None:
        document = _non_rfp_document(doc_type="historical_proposal")
        session = _FakeSession(
            chunks=[_FakeChunk(chunk_id=uuid4(), content="historic chunk", chunk_index=0)]
        )
        excerpt, refs = _run(
            self.service._load_source_context(  # type: ignore[attr-defined]
                session=session,  # type: ignore[arg-type]
                document=document,
            )
        )
        self.assertEqual(excerpt, "historic chunk")
        self.assertEqual(len(refs), 1)
        # historical refs include a chunk_id (light path does not)
        self.assertNotEqual(refs[0]["chunk_id"], "")


class ExtractRequirementCardWithPendingRfpTests(TestCase):
    """``extract_requirement_card`` graceful-degradation when the RFP is still parsing."""

    def setUp(self) -> None:
        self.service = RequirementService()

    def _project(self, *, description: str | None = "目标：变电站智能改造") -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            name="测试项目",
            description=description,
            industry="电力",
            product_line=None,
            business_objective=None,
            current_requirement_card_id=None,
            status="REQUIREMENT_DRAFTING",
        )

    def _build_session(self, *, project: SimpleNamespace, document: SimpleNamespace) -> _FakeSession:
        session = _FakeSession(max_version=0)
        original_get = session.get

        async def _get(model: type, key: Any) -> Any:
            name = model.__name__
            if name == "Project":
                return project
            if name == "Document":
                return document
            return await original_get(model, key)

        session.get = _get  # type: ignore[assignment]
        return session

    def test_rfp_parsing_with_description_yields_low_confidence_card(self) -> None:
        rfp_doc = _rfp_document(meta={}, parse_status="parsing")
        project = self._project()
        session = self._build_session(project=project, document=rfp_doc)

        async def _resolve(*, session, project_id, rfp_document_id):  # type: ignore[no-redef]
            return rfp_doc

        self.service._resolve_source_document = _resolve  # type: ignore[assignment]
        job, card = _run(
            self.service.extract_requirement_card(
                session=session,  # type: ignore[arg-type]
                project_id=project.id,
            )
        )

        self.assertEqual(card.confidence, Decimal("0.5500"))
        self.assertEqual(card.source_refs, [])
        # rfp_parse_pending clarification item was appended
        pending_items = [
            item
            for item in card.missing_items or []
            if item.get("item_id") == RFP_PARSE_PENDING_ITEM_ID
        ]
        self.assertEqual(len(pending_items), 1)
        # Should NOT be marked as blocking (UI just flags for review)
        self.assertFalse(pending_items[0]["blocking"])
        # Job records that the RFP was still parsing when this card was generated
        self.assertTrue(job.input_ref["rfp_parse_pending"])

    def test_rfp_parsing_without_description_raises_validation_error(self) -> None:
        rfp_doc = _rfp_document(meta={}, parse_status="parsing")
        project = self._project(description="")
        session = self._build_session(project=project, document=rfp_doc)

        async def _resolve(*, session, project_id, rfp_document_id):  # type: ignore[no-redef]
            return rfp_doc

        self.service._resolve_source_document = _resolve  # type: ignore[assignment]
        with self.assertRaises(ArtifactValidationError):
            _run(
                self.service.extract_requirement_card(
                    session=session,  # type: ignore[arg-type]
                    project_id=project.id,
                )
            )

    def test_rfp_parsing_complete_uses_excerpt_with_high_confidence(self) -> None:
        rfp_doc = _rfp_document(
            meta={"rfp_text_excerpt": "需求摘要：要求支持远程监控。"},
            parse_status="done",
        )
        project = self._project()
        session = self._build_session(project=project, document=rfp_doc)

        async def _resolve(*, session, project_id, rfp_document_id):  # type: ignore[no-redef]
            return rfp_doc

        self.service._resolve_source_document = _resolve  # type: ignore[assignment]
        _job, card = _run(
            self.service.extract_requirement_card(
                session=session,  # type: ignore[arg-type]
                project_id=project.id,
            )
        )
        self.assertEqual(card.confidence, Decimal("0.8200"))
        self.assertEqual(len(card.source_refs or []), 1)
        # rfp_parse_pending must not appear when parsing is complete
        self.assertFalse(
            any(
                item.get("item_id") == RFP_PARSE_PENDING_ITEM_ID
                for item in (card.missing_items or [])
            )
        )
