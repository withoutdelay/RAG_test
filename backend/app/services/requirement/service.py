from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.job import Job
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


def build_requirement_content(
    *,
    project: Project,
    source_excerpt: str,
) -> dict[str, Any]:
    business_objective = (project.description or "").strip()
    if not business_objective and source_excerpt:
        business_objective = source_excerpt[:300]

    return {
        "project_name": project.name,
        "product_line": project.product_line,
        "industry": project.industry,
        "business_objective": business_objective,
        "source_excerpt": source_excerpt[:600],
        "constraints": [],
        "key_parameters": {},
    }


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
        source_excerpt, source_refs = await self._load_source_context(
            session=session,
            document=source_document,
        )

        job = Job(
            project_id=project_id,
            job_type="extract",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "rfp_document_id": str(rfp_document_id) if rfp_document_id else None},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        version = await self._next_version(session=session, project_id=project_id)
        content = build_requirement_content(project=project, source_excerpt=source_excerpt)
        missing_items, blocking_items = build_clarification_items(content)
        card = RequirementCard(
            project_id=project_id,
            version=version,
            schema_version="v1",
            content=content,
            missing_items=missing_items,
            blocking_items=blocking_items,
            confidence=Decimal("0.8200") if source_excerpt else Decimal("0.6500"),
            source_refs=source_refs,
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
        if missing_items is not None:
            card.missing_items = missing_items
        if blocking_items is not None:
            card.blocking_items = blocking_items
        if confirmed_by_user is not None:
            card.confirmed_by_user = confirmed_by_user

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
