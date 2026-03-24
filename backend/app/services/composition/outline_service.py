from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.services.agents.planner import PlannerAgent
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


MANDATORY_SECTION_HINTS = {
    "项目概述",
    "需求分析",
    "技术架构",
    "硬件配置清单",
    "实施排期",
    "售后服务",
}


def _suggest_evidence_types(title: str) -> list[str]:
    lowered = title.lower()
    if "配置" in title or "清单" in title:
        return ["table", "parameter"]
    if "架构" in title or "系统" in title:
        return ["section", "figure"]
    if "计划" in title or "排期" in title:
        return ["section"]
    if "服务" in title:
        return ["case_summary", "section"]
    if "需求" in title:
        return ["requirement", "case_summary"]
    if "概述" in title:
        return ["requirement", "case_summary"]
    if "risk" in lowered:
        return ["section"]
    return ["section"]


def normalize_outline_payload(payload: dict[str, Any], *, project_name: str) -> dict[str, Any]:
    title = str(payload.get("title") or f"{project_name}技术方案")
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(payload.get("sections") or [], start=1):
        section_title = str(section.get("title") or f"章节 {index}")
        purpose = str(section.get("purpose") or section.get("description") or f"围绕{section_title}展开说明。")
        evidence_types = section.get("expected_evidence_types")
        if not isinstance(evidence_types, list) or not evidence_types:
            evidence_types = _suggest_evidence_types(section_title)
        needs_human_review = bool(section.get("needs_human_review"))
        if not needs_human_review and any(item in evidence_types for item in ["figure", "table", "parameter"]):
            needs_human_review = True
        section_id = str(section.get("section_id") or index)
        sections.append(
            {
                "section_id": section_id,
                "title": section_title,
                "purpose": purpose,
                "mandatory": bool(section.get("mandatory", section_title in MANDATORY_SECTION_HINTS)),
                "expected_evidence_types": [str(item) for item in evidence_types],
                "needs_human_review": needs_human_review,
                "children": section.get("children") if isinstance(section.get("children"), list) else [],
            }
        )
    return {"title": title, "sections": sections}


def build_outline_inputs(
    *,
    requirement_card: RequirementCard,
    evidence_bundle: EvidenceBundle,
) -> tuple[str, dict[str, Any], str]:
    content = requirement_card.content or {}
    global_params = content.get("key_parameters") if isinstance(content.get("key_parameters"), dict) else {}
    evidence_results = (evidence_bundle.content or {}).get("results") or []
    evidence_summary = "\n".join(
        f"- {item.get('source_title')}: {item.get('summary')}"
        for item in evidence_results[:5]
        if isinstance(item, dict)
    )
    instructions = (
        f"请基于需求卡生成一份面向客户技术方案的大纲。"
        f"项目名称：{content.get('project_name') or '未命名项目'}。"
        f"业务目标：{content.get('business_objective') or '请结合检索证据归纳'}。"
    )
    rfp_context = "\n\n".join(
        part
        for part in [
            str(content.get("source_excerpt") or "").strip(),
            f"证据摘要：\n{evidence_summary}" if evidence_summary else "",
        ]
        if part
    )
    return instructions, global_params, rfp_context


class OutlineService:
    def __init__(self, *, planner: PlannerAgent | None = None) -> None:
        self.planner = planner or PlannerAgent()

    async def generate_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None = None,
        evidence_bundle_id: UUID | None = None,
        instructions: str | None = None,
    ) -> tuple[Job, ProposalOutline]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        requirement_card = await self._resolve_requirement_card(
            session=session,
            project_id=project_id,
            requirement_card_id=requirement_card_id,
        )
        evidence_bundle = await self._resolve_evidence_bundle(
            session=session,
            project_id=project_id,
            evidence_bundle_id=evidence_bundle_id,
        )
        if any(item.get("status") != "resolved" for item in (requirement_card.blocking_items or [])):
            raise ArtifactValidationError("Requirement card still has unresolved blocking items")

        job = Job(
            project_id=project_id,
            job_type="outline",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "requirement_card_id": str(requirement_card.id),
                "evidence_bundle_id": str(evidence_bundle.id),
            },
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        default_instructions, global_params, rfp_context = build_outline_inputs(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
        )
        response = await self.planner.generate_outline(
            task_id=str(job.id),
            project_name=project.name,
            instructions=instructions or default_instructions,
            global_params=global_params,
            rfp_context=rfp_context,
        )
        outline_payload = self._parse_outline_response(response.content, project_name=project.name)
        outline_json = normalize_outline_payload(outline_payload, project_name=project.name)
        version = await self._next_version(session=session, project_id=project_id)
        outline = ProposalOutline(
            project_id=project_id,
            version=version,
            outline_json=outline_json,
            requirement_card_id=requirement_card.id,
            evidence_bundle_id=evidence_bundle.id,
            validator_status="pending",
        )
        session.add(outline)
        await session.flush()

        project.current_outline_id = outline.id
        project.status = "OUTLINE_READY"
        job.status = "succeeded"
        job.output_ref = {"outline_id": str(outline.id)}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(outline)
        return job, outline

    async def get_latest_outline(self, *, session: AsyncSession, project_id: UUID) -> ProposalOutline:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
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

    async def update_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID,
        outline_json: dict[str, Any],
    ) -> ProposalOutline:
        outline = await session.get(ProposalOutline, outline_id)
        if not outline or outline.project_id != project_id:
            raise ArtifactNotFoundError("Outline not found")
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline.outline_json = normalize_outline_payload(outline_json, project_name=project.name)
        outline.validator_status = "pending"
        project.current_outline_id = outline.id
        project.status = "OUTLINE_READY"
        await session.commit()
        await session.refresh(outline)
        return outline

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None,
    ) -> RequirementCard:
        if requirement_card_id is not None:
            card = await session.get(RequirementCard, requirement_card_id)
            if not card or card.project_id != project_id:
                raise ArtifactNotFoundError("Requirement card not found")
            return card
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

    async def _resolve_evidence_bundle(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        evidence_bundle_id: UUID | None,
    ) -> EvidenceBundle:
        if evidence_bundle_id is not None:
            bundle = await session.get(EvidenceBundle, evidence_bundle_id)
            if not bundle or bundle.project_id != project_id:
                raise ArtifactNotFoundError("Evidence bundle not found")
            return bundle
        result = await session.scalars(
            select(EvidenceBundle)
            .where(EvidenceBundle.project_id == project_id)
            .order_by(EvidenceBundle.retrieval_version.desc(), EvidenceBundle.created_at.desc())
            .limit(1)
        )
        bundle = result.first()
        if not bundle:
            raise ArtifactNotFoundError("Evidence bundle not found")
        return bundle

    async def _next_version(self, *, session: AsyncSession, project_id: UUID) -> int:
        latest = await session.scalar(
            select(func.max(ProposalOutline.version)).where(ProposalOutline.project_id == project_id)
        )
        return int(latest or 0) + 1

    def _parse_outline_response(self, text: str, *, project_name: str) -> dict[str, Any]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            payload = json.loads(match.group(0)) if match else {}
        if not payload:
            return {"title": f"{project_name}技术方案", "sections": []}
        return payload
