from __future__ import annotations

import uuid
from datetime import datetime, timezone
import re
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
from app.services.composition.outline_service import outline_is_approved
from app.services.retrieval import AssetRetrievalService
from app.services.validation.service import flatten_outline_sections
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError

DEFAULT_REUSE_LIMIT = 3
REUSE_CANDIDATE_MULTIPLIER = 3
REUSE_MIN_CANDIDATES = 6
REUSE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
REUSE_STOPWORDS = {
    "项目",
    "方案",
    "系统",
    "技术",
    "章节",
    "当前",
    "相关",
    "说明",
    "用于",
    "以及",
    "进行",
}
BANNED_TERM_PATTERNS = (
    re.compile(r"(?:项目名称|买方|卖方|客户|用户)\s*[:：]\s*([^\n]{2,80})"),
)
STANDARD_REPLACE_FIELDS = (
    "project_name",
    "customer_name",
    "buyer_name",
    "seller_name",
    "location",
    "factory_name",
    "production_line_name",
    "voltage_level",
    "power_rating",
    "quantity",
    "delivery_scope",
)


def build_section_context(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    selected = _select_evidence_items(section=section, evidence_bundle=evidence_bundle, limit=limit)

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
        "section_class": section.get("section_class"),
        "reuse_level": section.get("reuse_level"),
        "generation_mode": section.get("generation_mode"),
        "asset_required": bool(section.get("asset_required")),
    }


def build_section_global_params(requirement_content: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(requirement_content, dict):
        return {}

    merged: dict[str, Any] = {}
    key_parameters = requirement_content.get("key_parameters")
    if isinstance(key_parameters, dict):
        merged.update(key_parameters)

    for field_name in ("project_name", "industry", "product_line", "business_objective"):
        value = requirement_content.get(field_name)
        if value not in (None, "", [], {}):
            merged.setdefault(field_name, value)
    return merged


def build_section_asset_query(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_asset_types(section: dict[str, Any]) -> list[str] | None:
    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    asset_types: list[str] = []
    if {"table", "parameter"} & expected_types:
        asset_types.append("table")
    if {"figure", "diagram"} & expected_types:
        asset_types.append("figure")
    if {"formula", "equation"} & expected_types:
        asset_types.append("formula_candidate")
    return asset_types or None


def _select_evidence_items(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    limit: int,
) -> list[dict[str, Any]]:
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
    return selected


def _collect_replace_fields(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    lowered = raw_content.lower()
    replace_fields = [field for field in STANDARD_REPLACE_FIELDS if field in global_params and global_params.get(field)]
    if any(token in raw_content for token in ["买方", "卖方", "客户", "项目名称"]):
        replace_fields.extend(["customer_name", "buyer_name", "seller_name", "project_name"])
    if any(token in raw_content for token in ["110kV", "35kV", "6kV", "10kV", "电压"]):
        replace_fields.append("voltage_level")
    if any(token in lowered for token in ["kw", "mw", "mva", "数量", "台"]):
        replace_fields.extend(["power_rating", "quantity"])
    deduped: list[str] = []
    for field in replace_fields:
        if field not in deduped:
            deduped.append(field)
    return deduped


def _extract_banned_terms(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    current_project_name = str(global_params.get("project_name") or "").strip()
    terms: list[str] = []
    for pattern in BANNED_TERM_PATTERNS:
        for match in pattern.finditer(raw_content):
            candidate = re.sub(r"\s+", " ", str(match.group(1)).strip())
            candidate = candidate.strip(" :：;；,.，。()（）[]【】")
            if len(candidate) < 3:
                continue
            if current_project_name and candidate == current_project_name:
                continue
            if candidate not in terms:
                terms.append(candidate)
    return terms


def _build_required_asset_placeholders(recommended_assets: list[dict[str, Any]], *, asset_required: bool) -> list[dict[str, Any]]:
    if not asset_required:
        return []
    placeholders: list[dict[str, Any]] = []
    for asset in recommended_assets[:3]:
        asset_type = str(asset.get("asset_type") or "").lower()
        if asset_type not in {"figure", "table", "formula_candidate"}:
            continue
        asset_id = asset.get("asset_id")
        if not asset_id:
            continue
        normalized_type = "FORMULA" if asset_type == "formula_candidate" else asset_type.upper()
        placeholders.append(
            {
                "placeholder": f"[[ASSET:{normalized_type}:{asset_id}]]",
                "title": asset.get("title") or asset.get("caption") or "参考资产",
                "asset_type": asset_type,
            }
        )
    return placeholders


def build_reusable_blocks(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    limit: int = DEFAULT_REUSE_LIMIT,
) -> list[dict[str, Any]]:
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        limit=max(limit * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
    )
    blocks: list[dict[str, Any]] = []
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    for item in selected:
        raw_content = str(item.get("raw_content") or item.get("summary") or "").strip()
        if not raw_content:
            continue
        heading_path = item.get("heading_path") or []
        block_type = str(item.get("source_chunk_type") or item.get("type") or "section").lower()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        selection_score, selection_reasons = _score_reuse_candidate(
            section=section,
            item=item,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
        )
        blocks.append(
            {
                "block_id": str(item.get("evidence_id") or item.get("source_chunk_id") or ""),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "heading_path": heading_path,
                "content_md": raw_content,
                "block_type": block_type,
                "reusability_score": float(item.get("reusability_score") or item.get("relevance_score") or 0),
                "selection_score": selection_score,
                "selection_reasons": selection_reasons,
                "customer_specificity_score": 0.8 if metadata.get("front_matter") else 0.25,
                "asset_dependency_level": "high" if metadata.get("needs_asset_lookup") else "low",
                "must_replace_fields": _collect_replace_fields(raw_content=raw_content, global_params=global_params),
                "banned_terms": _extract_banned_terms(raw_content=raw_content, global_params=global_params),
                "must_not_copy_spans": [],
                "metadata": metadata,
            }
        )
    blocks.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    return blocks[:limit]


def build_reuse_pack(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    risk_flags: list[str] = []
    if bool(section.get("parameter_sensitive")):
        risk_flags.append("parameter_sensitive")
    if bool(section.get("asset_required")):
        risk_flags.append("asset_required")
    if bool(section.get("needs_human_review")):
        risk_flags.append("human_review_required")
    must_replace_fields: list[str] = []
    banned_terms: list[str] = []
    for block in reusable_blocks:
        for field_name in block.get("must_replace_fields") or []:
            if field_name not in must_replace_fields:
                must_replace_fields.append(field_name)
        for term in block.get("banned_terms") or []:
            if term not in banned_terms:
                banned_terms.append(term)
    required_asset_placeholders = _build_required_asset_placeholders(
        recommended_assets,
        asset_required=bool(section.get("asset_required")),
    )

    return {
        "section_title": str(section.get("title") or ""),
        "section_purpose": str(section.get("purpose") or ""),
        "generation_mode": str(section.get("generation_mode") or "baseline"),
        "reuse_level": str(section.get("reuse_level") or "medium"),
        "reusable_blocks": reusable_blocks,
        "recommended_assets": recommended_assets,
        "must_replace_fields": must_replace_fields,
        "banned_terms": banned_terms,
        "replacement_hints": {
            field_name: global_params.get(field_name)
            for field_name in must_replace_fields
            if global_params.get(field_name) not in (None, "", [], {})
        },
        "required_asset_placeholders": required_asset_placeholders,
        "parameter_candidates": {
            key: value
            for key, value in global_params.items()
            if key in {"project_name", "product_line", "industry", "business_objective", "voltage_level", "power_rating", "quantity"}
        },
        "do_not_reuse_signals": [
            "customer_specific_fields",
            "outdated_schedule",
            "unconfirmed_parameters",
        ],
        "risk_flags": risk_flags,
    }


def render_reuse_pack_context(reuse_pack: dict[str, Any]) -> str:
    blocks = reuse_pack.get("reusable_blocks") or []
    if not blocks:
        return ""

    sections: list[str] = []
    for index, block in enumerate(blocks, start=1):
        heading_path = block.get("heading_path") or []
        path_text = " > ".join(str(item) for item in heading_path if item)
        replace_fields = ", ".join(block.get("must_replace_fields") or []) or "无"
        sections.append(
            "\n".join(
                [
                    f"[复用块 {index}]",
                    f"来源: {block.get('source_title') or '未知来源'}",
                    f"位置: {path_text or '未标注章节'}",
                    f"类型: {block.get('block_type')}",
                    f"选择评分: {block.get('selection_score')}",
                    f"复用评分: {block.get('reusability_score')}",
                    f"必须替换字段: {replace_fields}",
                    "正文:",
                    str(block.get("content_md") or ""),
                ]
            )
        )
    return "\n\n".join(sections)


def build_manual_only_section_content(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> str:
    title = str(section.get("title") or "未命名章节")
    lines = [
        f"## {title}",
        "",
        "> 本章节已标记为人工编写，系统暂不自动生成最终客户正文。",
        "",
    ]
    blocks = reuse_pack.get("reusable_blocks") or []
    if blocks:
        lines.extend(["### 可参考复用材料", ""])
        for block in blocks[:3]:
            heading_path = " > ".join(str(item) for item in (block.get("heading_path") or []) if item)
            lines.append(
                f"- {block.get('source_title') or '未知来源'}"
                f"{f' / {heading_path}' if heading_path else ''}"
                f" / 复用评分 {block.get('reusability_score')}"
            )
        lines.append("")
    assets = reuse_pack.get("recommended_assets") or []
    if assets:
        lines.extend(["### 建议插入资产", ""])
        placeholders = reuse_pack.get("required_asset_placeholders") or []
        if placeholders:
            for item in placeholders[:3]:
                lines.append(f"- {item.get('placeholder')} {item.get('title')}")
        else:
            for asset in assets[:3]:
                asset_type = str(asset.get("asset_type") or "asset").upper()
                asset_id = asset.get("asset_id")
                title_text = asset.get("title") or asset.get("caption") or "参考资产"
                lines.append(f"- [[ASSET:{asset_type}:{asset_id}]] {title_text}")
        lines.append("")
    lines.extend(
        [
            "### 编写提示",
            "",
            "- 优先基于上述复用材料和资产进行人工整理。",
            "- 涉及客户信息、参数和供货边界时，必须按当前项目重新确认。",
        ]
    )
    return "\n".join(lines)


def ensure_required_asset_placeholders(*, content_md: str, reuse_pack: dict[str, Any]) -> str:
    placeholders = reuse_pack.get("required_asset_placeholders") or []
    if not placeholders:
        return content_md
    missing = [
        item
        for item in placeholders
        if str(item.get("placeholder") or "") and str(item.get("placeholder")) not in content_md
    ]
    if not missing:
        return content_md

    appendix_lines = ["", "### 建议插入图表", ""]
    for item in missing:
        appendix_lines.append(f"- {item.get('placeholder')} {item.get('title') or '参考资产'}")
    return content_md.rstrip() + "\n" + "\n".join(appendix_lines).rstrip() + "\n"


def _build_reuse_query_terms(*, section: dict[str, Any], global_params: dict[str, Any]) -> list[str]:
    parts = [
        str(section.get("title") or ""),
        str(section.get("purpose") or ""),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or ""),
        str(global_params.get("product_line") or ""),
        str(global_params.get("industry") or ""),
    ]
    tokens: list[str] = []
    for part in parts:
        for token in _tokenize_reuse_text(part):
            if token not in tokens:
                tokens.append(token)
    return tokens


def _tokenize_reuse_text(text: str) -> list[str]:
    tokens: list[str] = []
    for match in REUSE_TOKEN_PATTERN.findall(str(text or "")):
        token = match.strip().lower()
        if len(token) < 2 or token in REUSE_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _score_reuse_candidate(
    *,
    section: dict[str, Any],
    item: dict[str, Any],
    raw_content: str,
    heading_path: list[Any],
    query_terms: list[str],
) -> tuple[float, list[str]]:
    base_score = float(item.get("reusability_score") or item.get("relevance_score") or 0)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    result_type = str(item.get("type") or item.get("source_chunk_type") or "").lower()
    heading_text = " ".join(str(segment) for segment in heading_path if segment)
    heading_terms = set(_tokenize_reuse_text(heading_text))
    content_terms = set(_tokenize_reuse_text(raw_content[:1200]))
    query_set = set(query_terms)
    overlap_count = len(query_set & (heading_terms | content_terms))
    overlap_ratio = (overlap_count / len(query_set)) if query_set else 0.0
    score = base_score
    reasons: list[str] = []

    if overlap_ratio:
        bonus = min(0.22, overlap_ratio * 0.22)
        score += bonus
        reasons.append(f"keyword_overlap={overlap_count}")
    elif query_set:
        score -= 0.12
        reasons.append("keyword_mismatch_penalty")
    if query_set and heading_terms and (query_set & heading_terms):
        score += 0.12
        reasons.append("heading_match")

    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    if result_type and result_type in expected_types:
        score += 0.06
        reasons.append("expected_type")

    section_class = str(section.get("section_class") or "").lower()
    if section_class and any(section_class in token for token in heading_terms | content_terms):
        score += 0.08
        reasons.append("section_class_match")

    if metadata.get("front_matter"):
        score -= 0.18
        reasons.append("front_matter_penalty")
    if metadata.get("needs_asset_lookup") and not bool(section.get("asset_required")):
        score -= 0.08
        reasons.append("asset_dependency_penalty")

    customer_specificity = str(section.get("customer_specificity") or "medium").lower()
    if customer_specificity in {"medium", "high"} and _looks_customer_specific(raw_content):
        penalty = 0.08 if customer_specificity == "medium" else 0.12
        score -= penalty
        reasons.append("customer_specific_penalty")

    if bool(section.get("parameter_sensitive")) and result_type not in {"parameter", "table"}:
        score -= 0.04
        reasons.append("parameter_type_penalty")

    normalized_score = round(min(max(score, 0.0), 1.2), 4)
    return normalized_score, reasons


def _looks_customer_specific(content: str) -> bool:
    text = str(content or "")
    return any(token in text for token in ("买方", "卖方", "客户", "项目名称", "用户"))


class SectionDraftService:
    def __init__(
        self,
        *,
        executor: ExecutorAgent | None = None,
        asset_retriever: AssetRetrievalService | None = None,
    ) -> None:
        self.executor = executor or ExecutorAgent()
        self.asset_retriever = asset_retriever or AssetRetrievalService()

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
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before generating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)

        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
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
        global_params = build_section_global_params(requirement_card.content)

        for section in sections:
            generation_mode = str(section.get("generation_mode") or "baseline")
            context, citations = build_section_context(section=section, evidence_bundle=evidence_bundle)
            recommended_assets = await self._search_recommended_assets(
                session=session,
                project_id=project_id,
                section=section,
                global_params=global_params,
            )
            reusable_blocks = build_reusable_blocks(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            reuse_pack = build_reuse_pack(
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
                recommended_assets=recommended_assets,
            )
            if generation_mode == "reuse_first" and reusable_blocks:
                context = render_reuse_pack_context(reuse_pack)
            if generation_mode == "manual_only":
                content_md = build_manual_only_section_content(section=section, reuse_pack=reuse_pack)
                draft_status = "manual_required"
            else:
                response = await self.executor.write_section(
                    task_id=str(job.id),
                    section=section_outline_to_executor_payload(section),
                    global_params=global_params,
                    retrieved_context=context,
                    outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                    recommended_assets=recommended_assets,
                    reuse_pack=reuse_pack,
                )
                content_md = ensure_required_asset_placeholders(content_md=response.content, reuse_pack=reuse_pack)
                draft_status = "generated"
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=str(section.get("title") or "未命名章节"),
                content_md=content_md,
                citation_refs=citations,
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status=draft_status,
                validator_result={
                    "recommended_assets": recommended_assets,
                    "generation_mode": generation_mode,
                    "reuse_pack": reuse_pack,
                },
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

    async def list_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int | None = None,
    ) -> list[SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        target_draft_version = draft_version or int(project.current_draft_version or 0)
        if target_draft_version <= 0:
            return []

        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == target_draft_version,
            )
            .order_by(SectionDraft.section_id.asc())
        )
        drafts = list(result.all())
        if not drafts:
            return []

        return self._sort_drafts_by_outline(
            drafts=drafts,
            outline=await self._resolve_outline(session=session, project_id=project_id, outline_id=project.current_outline_id),
        )

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
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before regenerating sections")
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

        generation_mode = str(section.get("generation_mode") or "baseline")
        context, citations = build_section_context(section=section, evidence_bundle=evidence_bundle)
        global_params = build_section_global_params(requirement_card.content)
        recommended_assets = await self._search_recommended_assets(
            session=session,
            project_id=project_id,
            section=section,
            global_params=global_params,
        )
        reusable_blocks = build_reusable_blocks(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        reuse_pack = build_reuse_pack(
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
        )
        if generation_mode == "reuse_first" and reusable_blocks:
            context = render_reuse_pack_context(reuse_pack)
        if generation_mode == "manual_only":
            content_md = build_manual_only_section_content(section=section, reuse_pack=reuse_pack)
            draft_status = "manual_required"
        else:
            response = await self.executor.write_section(
                task_id=str(job.id),
                section=section_outline_to_executor_payload(section),
                global_params=global_params,
                retrieved_context=context,
                outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                recommended_assets=recommended_assets,
                reuse_pack=reuse_pack,
            )
            content_md = ensure_required_asset_placeholders(content_md=response.content, reuse_pack=reuse_pack)
            draft_status = "generated"
        draft.title = str(section.get("title") or draft.title)
        draft.content_md = content_md
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params
        draft.status = draft_status
        draft.validator_result = {
            "recommended_assets": recommended_assets,
            "generation_mode": generation_mode,
            "reuse_pack": reuse_pack,
        }
        project.status = "DRAFT_READY"

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
        current_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        draft.validator_result = {
            "recommended_assets": current_result.get("recommended_assets", []),
            "generation_mode": current_result.get("generation_mode", "baseline"),
            "reuse_pack": current_result.get("reuse_pack", {}),
        }
        project.status = "DRAFT_READY"
        await session.commit()
        await session.refresh(draft)
        return draft

    async def _search_recommended_assets(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        query = build_section_asset_query(section=section, global_params=global_params)
        if not query:
            return []
        response = await self.asset_retriever.search_project_assets(
            session=session,
            project_id=project_id,
            query=query,
            top_k=3,
            asset_types=build_section_asset_types(section),
            section_context={
                "section_title": str(section.get("title") or ""),
                "expected_evidence_types": list(section.get("expected_evidence_types") or []),
                "keywords": list(section.get("keywords") or []),
            },
        )
        return [item.model_dump(mode="json") for item in response.results]

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
        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        for section in sections:
            if str(section.get("section_id")) == section_id:
                return section
        raise ArtifactNotFoundError("Section not found in outline")

    def _sort_drafts_by_outline(self, *, drafts: list[SectionDraft], outline: ProposalOutline) -> list[SectionDraft]:
        ordered_sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        order_map = {
            str(section.get("section_id") or ""): index
            for index, section in enumerate(ordered_sections)
        }
        return sorted(
            drafts,
            key=lambda draft: (order_map.get(draft.section_id, len(order_map)), draft.section_id),
        )
