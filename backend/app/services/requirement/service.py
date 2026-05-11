from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.job import Job
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.services.parsing.rfp_excerpt_selector import select_requirement_excerpt
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError

logger = logging.getLogger(__name__)

RFP_PARSE_PENDING_ITEM_ID = "rfp_parse_pending"

# Short, user-facing preview kept in ``RequirementCard.content.source_excerpt``.
# UI / list cards render this; downstream LLM context now reads
# ``source_context`` instead (see :func:`resolve_requirement_source_context`).
SOURCE_EXCERPT_PREVIEW_CHARS = 600

INTERNAL_OBJECTIVE_TERMS = (
    "review",
    "质量审查",
    "质量review",
    "导出",
    "导出稿",
    "draft",
    "smoke",
    "联调",
    "测试",
    "验证",
    "llm",
    "prompt",
    "模型",
    "闭环",
    "relay",
)


def build_requirement_content(
    *,
    project: Project,
    source_excerpt: str,
) -> dict[str, Any]:
    """Build the JSON body stored on :class:`RequirementCard.content`.

    **Review R5 update.**  R4 #3 made ``_load_rfp_light_context`` return the
    full filtered RFP context (~20K chars after :func:`select_requirement_excerpt`),
    but :func:`build_requirement_content` then sliced it back down to 600
    characters for ``source_excerpt`` and every downstream consumer
    (outline prompt / retrieval query hints / section parameter evidence)
    only saw that head slice.  The smart selector's back-half picks were
    therefore silently dropped before they ever reached the LLM.

    The fix keeps backward compatibility with the UI by writing the same
    short preview at ``source_excerpt`` while persisting the full filtered
    context at ``source_context``.  Downstream code reads
    ``source_context`` via :func:`resolve_requirement_source_context`,
    falling back to ``source_excerpt`` for legacy cards.
    """

    business_objective = derive_business_objective(project=project, source_excerpt=source_excerpt)
    full_context = (source_excerpt or "").strip()

    return {
        "project_name": project.name,
        "product_line": project.product_line,
        "industry": project.industry,
        "business_objective": business_objective,
        # UI / list-card preview only.  Do not feed to LLM context.
        "source_excerpt": full_context[:SOURCE_EXCERPT_PREVIEW_CHARS],
        # Full filtered RFP context (set by selector upstream).  This is what
        # outline / retrieval / section consumers read.  Empty string is kept
        # explicit so callers can distinguish "no source" from a legacy card
        # without the field.
        "source_context": full_context,
        "constraints": [],
        "key_parameters": {},
    }


def resolve_requirement_source_context(content: dict[str, Any] | None) -> str:
    """Return the full RFP context for downstream LLM/query consumers.

    Preference order:

    1. ``content["source_context"]`` — new R5 field carrying the full
       :func:`select_requirement_excerpt` output (up to
       ``RFP_LIGHT_PARSE_EXCERPT_CHARS``).
    2. ``content["source_excerpt"]`` — legacy short preview, used only as a
       compatibility fallback for requirement cards written before R5.
    3. ``""`` when neither field carries useful text.

    The returned string is stripped; callers can rely on it being either
    empty or non-whitespace.
    """

    if not isinstance(content, dict):
        return ""
    full = content.get("source_context")
    if isinstance(full, str) and full.strip():
        return full.strip()
    legacy = content.get("source_excerpt")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    return ""


def derive_business_objective(*, project: Project, source_excerpt: str) -> str:
    candidate = (project.description or "").strip()
    if candidate and not looks_like_internal_objective(candidate):
        return candidate
    return summarize_business_objective_from_excerpt(source_excerpt)


def looks_like_internal_objective(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in INTERNAL_OBJECTIVE_TERMS)


def summarize_business_objective_from_excerpt(source_excerpt: str) -> str:
    if not source_excerpt:
        return ""

    cleaned_lines: list[str] = []
    for raw_line in source_excerpt.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        if re.fullmatch(r"[-:\s|]+", line):
            continue
        cleaned_lines.append(line)

    collapsed = re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()
    if not collapsed:
        return ""

    preferred_patterns = [
        r"(本项目[^。！？]{0,180}[。！？]?)",
        r"(项目面向[^。！？]{0,180}[。！？]?)",
        r"(方案采用[^。！？]{0,180}[。！？]?)",
    ]
    for pattern in preferred_patterns:
        match = re.search(pattern, collapsed)
        if match:
            return match.group(1).strip().rstrip("。！？")

    return collapsed[:180].rstrip("。！？")


def build_clarification_items(content: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [
        (
            "product_line",
            "P0",
            True,
            "尚未明确产品线，无法稳定限定检索范围。",
            "请确认本项目所属的产品线。",
        ),
        (
            "industry",
            "P1",
            False,
            "行业标签缺失会影响案例和模板召回。",
            "请确认项目所属行业。",
        ),
        (
            "business_objective",
            "P1",
            False,
            "业务目标缺失会影响需求理解和大纲生成。",
            "请补充客户的核心业务目标或改造诉求。",
        ),
    ]
    missing_items: list[dict[str, Any]] = []
    blocking_items: list[dict[str, Any]] = []
    for field_name, priority, blocking, reason, question in candidates:
        value = content.get(field_name)
        if value not in (None, "", [], {}):
            continue
        item = {
            "item_id": f"{field_name}_{priority.lower()}",
            "field_name": field_name,
            "priority": priority,
            "reason": reason,
            "question": question,
            "blocking": blocking,
            "status": "open",
        }
        missing_items.append(item)
        if blocking:
            blocking_items.append(item)
    return missing_items, blocking_items


def resolve_clarification_state(
    *,
    content: dict[str, Any],
    missing_items: list | None = None,
    blocking_items: list | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recomputed_missing_items, recomputed_blocking_items = build_clarification_items(content)
    return (
        missing_items if missing_items is not None else recomputed_missing_items,
        blocking_items if blocking_items is not None else recomputed_blocking_items,
    )


class RequirementService:
    async def extract_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        rfp_document_id: UUID | None = None,
    ) -> tuple[Job, RequirementCard]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        source_document = await self._resolve_source_document(
            session=session,
            project_id=project_id,
            rfp_document_id=rfp_document_id,
        )

        # Phase 3 / Task 3.2: when the RFP is still being parsed by the lightweight
        # path, fall back to project.description so the user can keep moving.  If
        # there is no description either, refuse with a 409-style validation error
        # so the UI prompts the user to wait.
        rfp_parse_pending = bool(
            source_document is not None
            and str(source_document.doc_type or "").lower() == "rfp"
            and source_document.parse_status == "parsing"
        )
        if rfp_parse_pending and not (project.description or "").strip():
            raise ArtifactValidationError(
                "需求文档仍在解析，请稍后再试。",
            )

        source_excerpt, source_refs = await self._load_source_context(
            session=session,
            document=source_document,
        )

        job = Job(
            project_id=project_id,
            job_type="extract",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "rfp_document_id": str(rfp_document_id) if rfp_document_id else None,
                "rfp_parse_pending": rfp_parse_pending,
            },
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        version = await self._next_version(session=session, project_id=project_id)
        content = build_requirement_content(project=project, source_excerpt=source_excerpt)
        missing_items, blocking_items = build_clarification_items(content)
        if rfp_parse_pending:
            pending_item = {
                "item_id": RFP_PARSE_PENDING_ITEM_ID,
                "field_name": "rfp_document",
                "priority": "P0",
                "reason": "RFP 解析尚未完成，需求字段基于项目描述生成，需复核。",
                "question": "请等待 RFP 解析完成或检查 RFP 文件是否上传成功。",
                "blocking": False,
                "status": "open",
            }
            if not any(item.get("item_id") == RFP_PARSE_PENDING_ITEM_ID for item in missing_items):
                missing_items.append(pending_item)

        if rfp_parse_pending:
            confidence = Decimal("0.5500")
            confidence_source_refs: list[dict[str, str]] = []
        elif source_excerpt:
            confidence = Decimal("0.8200")
            confidence_source_refs = source_refs
        else:
            confidence = Decimal("0.6500")
            confidence_source_refs = source_refs

        card = RequirementCard(
            project_id=project_id,
            version=version,
            schema_version="v1",
            content=content,
            missing_items=missing_items,
            blocking_items=blocking_items,
            confidence=confidence,
            source_refs=confidence_source_refs,
            confirmed_by_user=False,
        )
        session.add(card)
        await session.flush()

        project.current_requirement_card_id = card.id
        project.status = "BLOCKED_FOR_CLARIFICATION" if self._has_open_blockers(card) else "REQUIREMENT_DRAFTED"
        if not project.product_line and content.get("product_line"):
            project.product_line = str(content["product_line"])

        job.status = "succeeded"
        job.output_ref = {"requirement_card_id": str(card.id)}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(card)
        return job, card

    async def get_latest_requirement_card(self, *, session: AsyncSession, project_id: UUID) -> RequirementCard:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        result = await session.scalars(
            select(RequirementCard)
            .where(RequirementCard.project_id == project_id)
            .order_by(RequirementCard.version.desc(), RequirementCard.created_at.desc())
            .limit(1)
        )
        card = result.first()
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def update_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        card_id: UUID,
        content: dict | None = None,
        missing_items: list | None = None,
        blocking_items: list | None = None,
        confirmed_by_user: bool | None = None,
    ) -> RequirementCard:
        card = await self._get_requirement_card(session=session, project_id=project_id, card_id=card_id)
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        if content is not None:
            merged_content = dict(card.content or {})
            merged_content.update(content)
            card.content = merged_content
        content_payload = dict(card.content or {})
        card.missing_items, card.blocking_items = resolve_clarification_state(
            content=content_payload,
            missing_items=missing_items,
            blocking_items=blocking_items,
        )
        if confirmed_by_user is not None:
            card.confirmed_by_user = confirmed_by_user
        if "product_line" in content_payload:
            raw_product_line = content_payload.get("product_line")
            project.product_line = str(raw_product_line) if raw_product_line not in (None, "") else None

        project.current_requirement_card_id = card.id
        project.status = "BLOCKED_FOR_CLARIFICATION" if self._has_open_blockers(card) else "REQUIREMENT_DRAFTED"
        await session.commit()
        await session.refresh(card)
        return card

    async def resolve_clarification(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        item_id: str,
        resolution: Any,
        confirmed_by_user: bool | None = None,
    ) -> RequirementCard:
        card = await self.get_latest_requirement_card(session=session, project_id=project_id)
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        updated = False
        card.missing_items, item_found = self._mark_item_resolved(card.missing_items, item_id=item_id, resolution=resolution)
        updated = updated or item_found
        card.blocking_items, item_found = self._mark_item_resolved(card.blocking_items, item_id=item_id, resolution=resolution)
        updated = updated or item_found
        if not updated:
            raise ArtifactNotFoundError("Clarification item not found")

        field_name = self._find_field_name(card.missing_items, item_id=item_id) or self._find_field_name(card.blocking_items, item_id=item_id)
        if field_name:
            merged_content = dict(card.content or {})
            merged_content[field_name] = resolution
            card.content = merged_content
            if field_name == "product_line" and isinstance(resolution, str):
                project.product_line = resolution

        if confirmed_by_user is not None:
            card.confirmed_by_user = confirmed_by_user

        project.current_requirement_card_id = card.id
        project.status = "BLOCKED_FOR_CLARIFICATION" if self._has_open_blockers(card) else "REQUIREMENT_DRAFTED"
        await session.commit()
        await session.refresh(card)
        return card

    async def _get_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        card_id: UUID,
    ) -> RequirementCard:
        card = await session.get(RequirementCard, card_id)
        if not card or card.project_id != project_id:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _resolve_source_document(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        rfp_document_id: UUID | None,
    ) -> Document | None:
        if rfp_document_id is not None:
            document = await session.get(Document, rfp_document_id)
            if not document or document.project_id != project_id:
                raise ArtifactValidationError("RFP document not found in project")
            return document

        result = await session.scalars(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        return result.first()

    async def _load_source_context(
        self,
        *,
        session: AsyncSession,
        document: Document | None,
        max_chunks: int = 5,
    ) -> tuple[str, list[dict[str, str]]]:
        if document is None:
            return "", []
        if str(document.doc_type or "").lower() == "rfp":
            return await self._load_rfp_light_context(
                session=session,
                document=document,
                max_chunks=max_chunks,
            )
        return await self._load_chunk_context(
            session=session,
            document=document,
            max_chunks=max_chunks,
        )

    async def _load_chunk_context(
        self,
        *,
        session: AsyncSession,
        document: Document,
        max_chunks: int = 5,
    ) -> tuple[str, list[dict[str, str]]]:
        chunks = (
            await session.scalars(
                select(Chunk)
                .where(Chunk.document_id == document.id)
                .order_by(Chunk.chunk_index.asc())
                .limit(max_chunks)
            )
        ).all()
        excerpt = "\n\n".join(chunk.content for chunk in chunks)
        source_refs = [
            {
                "document_id": str(document.id),
                "document_name": document.filename,
                "chunk_id": str(chunk.id),
                "heading_path": chunk.heading_path or "",
            }
            for chunk in chunks
        ]
        return excerpt, source_refs

    async def _load_rfp_light_context(
        self,
        *,
        session: AsyncSession,
        document: Document,
        max_chunks: int = 5,
    ) -> tuple[str, list[dict[str, str]]]:
        """Resolve RFP requirement context for the lightweight parser.

        **Review R4 #3 update.**  R4 #1 already fixed storage to keep the
        full extracted RFP text on disk at ``rfp_text_storage_path``.  But
        the consumer side here used to short-circuit on
        ``meta.rfp_text_excerpt`` (a mechanical ``text[:excerpt_chars]``
        head slice) and only ever reached the storage path with the same
        head-slice fallback, so any requirement past the first
        ``RFP_LIGHT_PARSE_EXCERPT_CHARS`` characters never made it into the
        LLM prompt that produces the requirement card.

        New priority order:

        1. ``Document.meta.rfp_text_storage_path`` — full text on disk; run
           :func:`select_requirement_excerpt` to keep the highest-scoring
           paragraphs (section headings + requirement markers + numeric
           specs) up to ``RFP_LIGHT_PARSE_EXCERPT_CHARS``.
        2. ``Document.meta.rfp_text_excerpt`` — legacy fallback for documents
           parsed before R4 #1 (storage path may not exist for them).
        3. legacy ``Chunk`` rows (read-only fallback for RFPs that were
           originally parsed by the heavy historical-ingestion pipeline).
        4. empty
        """

        meta = dict(document.meta or {})
        document_ref = self._build_document_ref(document)
        excerpt_cap = int(get_settings().rfp_light_parse_excerpt_chars)

        # Preferred path: full text on disk → requirement-aware selection.
        storage_path = meta.get("rfp_text_storage_path")
        if isinstance(storage_path, str) and storage_path.strip():
            text_path = Path(storage_path)
            if text_path.exists():
                try:
                    content = await asyncio.to_thread(
                        text_path.read_text,
                        encoding="utf-8",
                    )
                except OSError as exc:  # pragma: no cover - filesystem hiccup
                    logger.warning(
                        "Failed to read RFP text storage path %s: %s",
                        storage_path,
                        exc,
                    )
                else:
                    snippet = select_requirement_excerpt(
                        (content or "").strip(),
                        max_chars=excerpt_cap,
                    )
                    if snippet:
                        return snippet, [document_ref]

        # Legacy fallback: documents written before R4 #1 only have the head
        # slice on ``rfp_text_excerpt``.  We still surface them rather than
        # nothing, but new RFPs always take the storage-path branch above.
        excerpt = meta.get("rfp_text_excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            return excerpt, [document_ref]

        # Legacy fallback (read-only): older RFPs may still have Chunk rows.
        chunk_excerpt, chunk_refs = await self._load_chunk_context(
            session=session,
            document=document,
            max_chunks=max_chunks,
        )
        if chunk_excerpt:
            return chunk_excerpt, chunk_refs
        return "", []

    @staticmethod
    def _build_document_ref(document: Document) -> dict[str, str]:
        return {
            "document_id": str(document.id),
            "document_name": document.filename or "",
            "chunk_id": "",
            "heading_path": "rfp_text_excerpt",
        }

    async def _next_version(self, *, session: AsyncSession, project_id: UUID) -> int:
        latest = await session.scalar(
            select(func.max(RequirementCard.version)).where(RequirementCard.project_id == project_id)
        )
        return int(latest or 0) + 1

    def _has_open_blockers(self, card: RequirementCard) -> bool:
        return any(item.get("status") != "resolved" for item in (card.blocking_items or []))

    def _mark_item_resolved(self, items: list, *, item_id: str, resolution: Any) -> tuple[list, bool]:
        updated_items: list = []
        found = False
        for item in items or []:
            current = dict(item)
            if current.get("item_id") == item_id:
                current["status"] = "resolved"
                current["resolution"] = resolution
                found = True
            updated_items.append(current)
        return updated_items, found

    def _find_field_name(self, items: list, *, item_id: str) -> str | None:
        for item in items or []:
            if item.get("item_id") == item_id:
                value = item.get("field_name")
                return str(value) if value else None
        return None
