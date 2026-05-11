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
from app.services.requirement.service import resolve_requirement_source_context
from app.services.retrieval.case_service import build_outline_examples
from app.services.vectorstore.block_taxonomy import is_commercial_manual_section_text
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


MANDATORY_SECTION_HINTS = {
    "项目概述",
    "需求分析",
    "技术架构",
    "硬件配置清单",
    "实施排期",
    "售后服务",
}
SECTION_CLASS_CHOICES = {
    "overview",
    "requirement",
    "architecture",
    "configuration",
    "implementation",
    "service",
    "appendix",
    "custom",
}
REUSE_LEVEL_CHOICES = {"low", "medium", "high"}
CUSTOMER_SPECIFICITY_CHOICES = {"low", "medium", "high"}
GENERATION_MODE_CHOICES = {"baseline", "reuse_first", "manual_only"}
MANUAL_ONLY_SECTION_HINTS = ("商务", "报价", "成本", "合同", "法务", "授权", "保密")
ASSET_REQUIRED_HINTS = ("图", "表", "波形", "原理", "接线", "布局")
PARAMETER_SENSITIVE_HINTS = ("参数", "配置", "清单", "规格", "容量", "功率", "数量")


def _suggest_evidence_types(title: str) -> list[str]:
    lowered = title.lower()
    if is_commercial_manual_section_text(title):
        return ["section"]
    if "供货" in title or "清单" in title or "物料" in title:
        return ["table", "parameter", "section"]
    if "接口" in title or "通讯" in title or "通信" in title:
        return ["section", "parameter"]
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


def _normalize_choice(value: Any, *, allowed: set[str], fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in allowed:
        return candidate
    return fallback


def _normalize_keywords(raw_keywords: Any, *, title: str, evidence_types: list[str]) -> list[str]:
    values: list[str] = []
    if isinstance(raw_keywords, list):
        values.extend(str(item).strip() for item in raw_keywords if str(item).strip())
    for item in [title, *evidence_types]:
        normalized = str(item).strip()
        if normalized and normalized not in values:
            values.append(normalized)
    return values[:8]


def _suggest_section_class(title: str) -> str:
    if is_commercial_manual_section_text(title):
        return "service"
    if "供货" in title or "清单" in title or "物料" in title:
        return "configuration"
    if "接口" in title or "通讯" in title or "通信" in title:
        return "architecture"
    if "概述" in title or "背景" in title:
        return "overview"
    if "需求" in title or "范围" in title or "目标" in title:
        return "requirement"
    if "架构" in title or "系统" in title or "接口" in title:
        return "architecture"
    if "配置" in title or "清单" in title or "参数" in title:
        return "configuration"
    if "实施" in title or "计划" in title or "排期" in title or "交付" in title:
        return "implementation"
    if "服务" in title or "培训" in title or "维保" in title:
        return "service"
    if "附录" in title:
        return "appendix"
    return "custom"


def _suggest_customer_specificity(*, section_class: str, title: str) -> str:
    if is_commercial_manual_section_text(title):
        return "low"
    if "供货" in title or "清单" in title or "物料" in title:
        return "medium"
    if "接口" in title or "通讯" in title or "通信" in title:
        return "low"
    if section_class in {"overview", "requirement"}:
        return "high"
    if section_class in {"configuration", "implementation"} or "工艺" in title:
        return "medium"
    return "low"


def _suggest_reuse_level(*, section_class: str, customer_specificity: str, parameter_sensitive: bool) -> str:
    if section_class in {"architecture", "configuration", "service"} and not parameter_sensitive:
        return "high"
    if customer_specificity == "high":
        return "low"
    return "medium"


def _suggest_generation_mode(
    *,
    title: str,
    section_class: str,
    reuse_level: str,
    customer_specificity: str,
) -> str:
    if any(hint in title for hint in MANUAL_ONLY_SECTION_HINTS):
        return "manual_only"
    if section_class in {"architecture", "configuration", "service"} and reuse_level == "high":
        return "reuse_first"
    if customer_specificity == "high":
        return "baseline"
    return "reuse_first" if reuse_level != "low" else "baseline"


def _normalize_approved_at(value: Any, *, approved: bool) -> str | None:
    if not approved or value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def outline_is_approved(outline_json: dict[str, Any] | None) -> bool:
    if not isinstance(outline_json, dict):
        return False
    return str(outline_json.get("outline_status") or "").lower() == "approved"


def _normalize_section_payload(
    section: dict[str, Any],
    *,
    fallback_id: str,
) -> dict[str, Any]:
    section_id = str(section.get("section_id") or fallback_id)
    section_title = str(section.get("title") or f"章节 {section_id}")
    purpose = str(section.get("purpose") or section.get("description") or f"围绕{section_title}展开说明。")
    is_manual_delivery_section = is_commercial_manual_section_text(
        section_title,
        purpose,
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
    )
    evidence_types = section.get("expected_evidence_types")
    if is_manual_delivery_section:
        evidence_types = ["section"]
    elif not isinstance(evidence_types, list) or not evidence_types:
        evidence_types = _suggest_evidence_types(section_title)
    evidence_types = [str(item) for item in evidence_types if str(item).strip()]
    if not evidence_types:
        evidence_types = _suggest_evidence_types(section_title)
    needs_human_review = bool(section.get("needs_human_review"))
    if not needs_human_review and any(item in evidence_types for item in ["figure", "table", "parameter"]):
        needs_human_review = True

    section_class = _normalize_choice(
        section.get("section_class"),
        allowed=SECTION_CLASS_CHOICES,
        fallback=_suggest_section_class(section_title),
    )
    if is_manual_delivery_section:
        section_class = "service"
    customer_specificity = _normalize_choice(
        section.get("customer_specificity"),
        allowed=CUSTOMER_SPECIFICITY_CHOICES,
        fallback=_suggest_customer_specificity(section_class=section_class, title=section_title),
    )
    asset_required = (
        bool(section.get("asset_required"))
        if "asset_required" in section
        else any(item in evidence_types for item in ["figure", "table", "parameter"])
        or any(hint in section_title for hint in ASSET_REQUIRED_HINTS)
    )
    if is_manual_delivery_section:
        asset_required = False
    parameter_sensitive = (
        bool(section.get("parameter_sensitive"))
        if "parameter_sensitive" in section
        else any(item in evidence_types for item in ["table", "parameter"])
        or any(hint in section_title for hint in PARAMETER_SENSITIVE_HINTS)
    )
    if is_manual_delivery_section:
        parameter_sensitive = False
    reuse_level = _normalize_choice(
        section.get("reuse_level"),
        allowed=REUSE_LEVEL_CHOICES,
        fallback=_suggest_reuse_level(
            section_class=section_class,
            customer_specificity=customer_specificity,
            parameter_sensitive=parameter_sensitive,
        ),
    )
    generation_mode = _normalize_choice(
        section.get("generation_mode"),
        allowed=GENERATION_MODE_CHOICES,
        fallback=_suggest_generation_mode(
            title=section_title,
            section_class=section_class,
            reuse_level=reuse_level,
            customer_specificity=customer_specificity,
        ),
    )
    keywords = _normalize_keywords(section.get("keywords"), title=section_title, evidence_types=evidence_types)
    if is_manual_delivery_section:
        keywords = [
            item
            for item in keywords
            if str(item).strip().lower() not in {"table", "parameter", "configuration"}
        ]

    children_raw = section.get("children") if isinstance(section.get("children"), list) else []
    children = [
        _normalize_section_payload(child, fallback_id=f"{section_id}.{index}")
        for index, child in enumerate(children_raw, start=1)
    ]

    normalized_section = {
        "section_id": section_id,
        "title": section_title,
        "purpose": purpose,
        "mandatory": bool(section.get("mandatory", section_title in MANDATORY_SECTION_HINTS)),
        "expected_evidence_types": evidence_types,
        "needs_human_review": needs_human_review,
        "section_class": section_class,
        "reuse_level": reuse_level,
        "asset_required": asset_required,
        "parameter_sensitive": parameter_sensitive,
        "customer_specificity": customer_specificity,
        "generation_mode": generation_mode,
        "keywords": keywords,
        "children": children,
    }
    explicit_target_section_type = str(section.get("target_section_type") or "").strip()
    if explicit_target_section_type:
        normalized_section["target_section_type"] = explicit_target_section_type
    elif is_manual_delivery_section:
        normalized_section["target_section_type"] = "commercial_manual_only"
    return normalized_section


def normalize_outline_payload(payload: dict[str, Any], *, project_name: str) -> dict[str, Any]:
    title = str(payload.get("title") or f"{project_name}技术方案")
    outline_status = _normalize_choice(
        payload.get("outline_status"),
        allowed={"candidate", "approved"},
        fallback="candidate",
    )
    approved_by_user = bool(payload.get("approved_by_user")) if outline_status == "approved" else False
    approved_at = _normalize_approved_at(payload.get("approved_at"), approved=outline_status == "approved")
    reviewer_notes = str(payload.get("reviewer_notes") or "").strip() or None
    sections = [
        _normalize_section_payload(section, fallback_id=str(index))
        for index, section in enumerate(payload.get("sections") or [], start=1)
    ]
    return {
        "title": title,
        "generation_strategy": "reuse_first",
        "approval_required": True,
        "outline_status": outline_status,
        "approved_by_user": approved_by_user,
        "approved_at": approved_at,
        "reviewer_notes": reviewer_notes,
        "sections": sections,
    }


def mark_outline_as_approved(
    outline_json: dict[str, Any],
    *,
    project_name: str,
    reviewer_notes: str | None = None,
    approved_by_user: bool = True,
    approved_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_outline_payload(outline_json, project_name=project_name)
    normalized["outline_status"] = "approved"
    normalized["approved_by_user"] = bool(approved_by_user)
    normalized["approved_at"] = _normalize_approved_at(
        approved_at or datetime.now(timezone.utc),
        approved=True,
    )
    normalized["reviewer_notes"] = str(reviewer_notes).strip() if reviewer_notes else None
    return normalized


def _advance_project_to_outline_state(
    *,
    project: Project,
    outline: ProposalOutline,
    status: str,
) -> None:
    project.current_outline_id = outline.id
    project.current_draft_version = 0
    project.status = status


def build_outline_inputs(
    *,
    requirement_card: RequirementCard,
    evidence_bundle: EvidenceBundle,
) -> tuple[str, dict[str, Any], str, list[dict[str, Any]]]:
    content = requirement_card.content or {}
    global_params = content.get("key_parameters") if isinstance(content.get("key_parameters"), dict) else {}
    global_params = {
        **global_params,
        **{
            key: value
            for key, value in {
                "project_name": content.get("project_name"),
                "industry": content.get("industry"),
                "product_line": content.get("product_line"),
                "business_objective": content.get("business_objective"),
            }.items()
            if value not in (None, "", [], {})
        },
    }
    evidence_results = (evidence_bundle.content or {}).get("results") or []
    evidence_summary = "\n\n".join(
        f"- {item.get('source_title')}: {str(item.get('raw_content') or item.get('summary') or '').strip()[:420]}"
        for item in evidence_results[:5]
        if isinstance(item, dict)
    )
    case_candidates = (evidence_bundle.content or {}).get("case_candidates") or []
    outline_examples = build_outline_examples(case_candidates, max_cases=3, max_titles=12)
    instructions = (
        f"请基于需求卡生成一份面向客户技术方案的大纲。"
        f"项目名称：{content.get('project_name') or '未命名项目'}。"
        f"业务目标：{content.get('business_objective') or '请结合检索证据归纳'}。"
    )
    rfp_context = "\n\n".join(
        part
        for part in [
            # R5: pull the full filtered RFP context (source_context) instead
            # of the 600-char source_excerpt preview so back-half requirements
            # (评分条款 / 工期 / 验收 / 后半段技术参数) actually reach the
            # planner.  Falls back to source_excerpt for legacy cards.
            resolve_requirement_source_context(content),
            f"证据摘要：\n{evidence_summary}" if evidence_summary else "",
        ]
        if part
    )
    return instructions, global_params, rfp_context, outline_examples


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

        default_instructions, global_params, rfp_context, outline_examples = build_outline_inputs(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
        )
        response = await self.planner.generate_outline(
            task_id=str(job.id),
            project_name=project.name,
            instructions=instructions or default_instructions,
            global_params=global_params,
            rfp_context=rfp_context,
            outline_examples=outline_examples,
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

        _advance_project_to_outline_state(project=project, outline=outline, status="OUTLINE_READY")
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
        _advance_project_to_outline_state(project=project, outline=outline, status="OUTLINE_READY")
        await session.commit()
        await session.refresh(outline)
        return outline

    async def approve_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID,
        outline_json: dict[str, Any] | None = None,
        reviewer_notes: str | None = None,
        approved_by_user: bool = True,
    ) -> ProposalOutline:
        outline = await session.get(ProposalOutline, outline_id)
        if not outline or outline.project_id != project_id:
            raise ArtifactNotFoundError("Outline not found")
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not approved_by_user:
            raise ArtifactValidationError("Outline approval must be explicitly confirmed by user")

        source_outline = outline_json if isinstance(outline_json, dict) else dict(outline.outline_json or {})
        outline.outline_json = mark_outline_as_approved(
            source_outline,
            project_name=project.name,
            reviewer_notes=reviewer_notes,
            approved_by_user=approved_by_user,
        )
        outline.validator_status = "approved"
        _advance_project_to_outline_state(project=project, outline=outline, status="OUTLINE_APPROVED")
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
        if not payload.get("title") and payload.get("document_title"):
            payload["title"] = payload["document_title"]
        if not payload.get("sections") and isinstance(payload.get("outline"), list):
            converted_sections: list[dict[str, Any]] = []
            for index, item in enumerate(payload.get("outline") or [], start=1):
                if not isinstance(item, dict):
                    continue
                converted_sections.append(
                    {
                        "index": index,
                        "title": str(item.get("title") or f"章节 {index}"),
                        "description": str(item.get("description") or item.get("purpose") or ""),
                        "keywords": item.get("keywords") if isinstance(item.get("keywords"), list) else [],
                    }
                )
            payload = {
                "title": str(payload.get("title") or payload.get("project_name") or f"{project_name}技术方案"),
                "sections": converted_sections,
            }
        if not payload.get("sections") and isinstance(payload.get("chapters"), list):
            converted_sections = []
            for index, item in enumerate(payload.get("chapters") or [], start=1):
                if not isinstance(item, dict):
                    continue
                chapter_title = str(item.get("title") or item.get("heading") or f"章节 {index}")
                chapter_description = str(
                    item.get("description")
                    or item.get("purpose")
                    or item.get("summary")
                    or f"围绕{chapter_title}展开技术说明。"
                )
                converted_sections.append(
                    {
                        "index": index,
                        "title": chapter_title,
                        "description": chapter_description,
                        "keywords": item.get("keywords") if isinstance(item.get("keywords"), list) else [chapter_title],
                    }
                )
            payload = {
                "title": str(
                    payload.get("title")
                    or payload.get("project_name")
                    or payload.get("document_title")
                    or f"{project_name}技术方案"
                ),
                "sections": converted_sections,
            }
        return payload
