from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.models.section_draft import SectionDraft
from app.services.agents.executor import ExecutorAgent
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


def build_section_context(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    expected_types = {str(item) for item in (section.get("expected_evidence_types") or [])}
    results = (evidence_bundle.content or {}).get("results") or []
    selected: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        result_type = str(result.get("type") or "")
        if expected_types and result_type not in expected_types:
            continue
        selected.append(result)
        if len(selected) >= limit:
            break
    if not selected:
        selected = [item for item in results[:limit] if isinstance(item, dict)]

    context_lines = []
    citations: list[dict[str, Any]] = []
    for item in selected:
        heading_path = item.get("heading_path") or []
        heading_text = " > ".join(str(segment) for segment in heading_path if segment)
        summary = str(item.get("summary") or "")
        context_lines.append(f"- {item.get('source_title')} {heading_text}: {summary}".strip())
        citations.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": item.get("relevance_score"),
                "type": item.get("type"),
            }
        )
    return "\n".join(context_lines), citations


def section_outline_to_executor_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": section.get("title"),
        "description": section.get("purpose", ""),
        "keywords": [section.get("title", ""), *[str(item) for item in section.get("expected_evidence_types") or []]],
    }


class SectionDraftService:
    def __init__(self, *, executor: ExecutorAgent | None = None) -> None:
        self.executor = executor or ExecutorAgent()

    async def generate_sections(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None = None,
    ) -> tuple[Job, list[SectionDraft]]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)

        sections = (outline.outline_json or {}).get("sections") or []
        if not sections:
            raise ArtifactValidationError("Outline has no sections")

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "outline_id": str(outline.id)},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        draft_version = int(project.current_draft_version or 0) + 1
        generated_drafts: list[SectionDraft] = []
        global_params = requirement_card.content.get("key_parameters") if isinstance(requirement_card.content, dict) else {}

        for section in sections:
            context, citations = build_section_context(section=section, evidence_bundle=evidence_bundle)
            response = await self.executor.write_section(
                task_id=str(job.id),
                section=section_outline_to_executor_payload(section),
                global_params=global_params if isinstance(global_params, dict) else {},
                retrieved_context=context,
                outline_title=(outline.outline_json or {}).get("title", "技术方案"),
            )
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=str(section.get("title") or "未命名章节"),
                content_md=response.content,
                citation_refs=citations,
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status="generated",
                validator_result={},
            )
            session.add(draft)
            generated_drafts.append(draft)

        project.current_draft_version = draft_version
        project.status = "DRAFT_READY"
        job.status = "succeeded"
        job.output_ref = {"draft_version": draft_version, "section_count": len(generated_drafts)}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        for draft in generated_drafts:
            await session.refresh(draft)
        await session.refresh(job)
        return job, generated_drafts

    async def regenerate_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        outline_id: UUID | None = None,
    ) -> tuple[Job, SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)
        section = self._find_section(outline=outline, section_id=section_id)
        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "section_id": section_id},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        context, citations = build_section_context(section=section, evidence_bundle=evidence_bundle)
        global_params = requirement_card.content.get("key_parameters") if isinstance(requirement_card.content, dict) else {}
        response = await self.executor.write_section(
            task_id=str(job.id),
            section=section_outline_to_executor_payload(section),
            global_params=global_params if isinstance(global_params, dict) else {},
            retrieved_context=context,
            outline_title=(outline.outline_json or {}).get("title", "技术方案"),
        )
        draft.title = str(section.get("title") or draft.title)
        draft.content_md = response.content
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params if isinstance(global_params, dict) else {}
        draft.status = "generated"
        draft.validator_result = {}

        job.status = "succeeded"
        job.output_ref = {"draft_version": project.current_draft_version, "section_id": section_id}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(draft)
        return job, draft

    async def update_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        content_md: str,
        citation_refs: list | None = None,
        assumptions: list | None = None,
    ) -> SectionDraft:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )
        draft.content_md = content_md
        if citation_refs is not None:
            draft.citation_refs = citation_refs
        if assumptions is not None:
            draft.assumptions = assumptions
        draft.status = "edited"
        await session.commit()
        await session.refresh(draft)
        return draft

    async def _resolve_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None,
    ) -> ProposalOutline:
        if outline_id is not None:
            outline = await session.get(ProposalOutline, outline_id)
            if not outline or outline.project_id != project_id:
                raise ArtifactNotFoundError("Outline not found")
            return outline
        result = await session.scalars(
            select(ProposalOutline)
            .where(ProposalOutline.project_id == project_id)
            .order_by(ProposalOutline.version.desc(), ProposalOutline.created_at.desc())
            .limit(1)
        )
        outline = result.first()
        if not outline:
            raise ArtifactNotFoundError("Outline not found")
        return outline

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> RequirementCard:
        if outline.requirement_card_id is None:
            raise ArtifactValidationError("Outline is not bound to a requirement card")
        card = await session.get(RequirementCard, outline.requirement_card_id)
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _resolve_evidence_bundle(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> EvidenceBundle:
        if outline.evidence_bundle_id is None:
            raise ArtifactValidationError("Outline is not bound to an evidence bundle")
        bundle = await session.get(EvidenceBundle, outline.evidence_bundle_id)
        if not bundle:
            raise ArtifactNotFoundError("Evidence bundle not found")
        return bundle

    async def _get_current_section_draft(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
        section_id: str,
    ) -> SectionDraft:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
                SectionDraft.section_id == section_id,
            )
            .limit(1)
        )
        draft = result.first()
        if not draft:
            raise ArtifactNotFoundError("Section draft not found")
        return draft

    def _find_section(self, *, outline: ProposalOutline, section_id: str) -> dict[str, Any]:
        sections = (outline.outline_json or {}).get("sections") or []
        for section in sections:
            if str(section.get("section_id")) == section_id:
                return section
        raise ArtifactNotFoundError("Section not found in outline")
