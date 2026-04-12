from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.models.review_task import ReviewTask
from app.models.section_draft import SectionDraft
from app.models.validation_report import ValidationReport
from app.services.composition.section_quality import analyze_section_heading_quality
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


HARD_BLOCKING_CODES = {"VAL001", "VAL002", "VAL004", "VAL005", "VAL007", "VAL008", "VAL009", "VAL010"}
CONTENT_REVIEW_CODES = {"VAL101", "VAL102", "VAL103", "VAL104", "VAL105", "VAL106", "VAL107", "VAL108"}
ASSUMPTION_HINTS = ("待确认", "待补充", "TBD", "暂定", "后续确认")
TECHNICAL_SECTION_HINTS = ("技术", "架构", "配置", "参数", "实施", "系统", "方案")
PARAMETER_REPLACE_FIELDS = {"voltage_level", "power_rating", "quantity", "delivery_scope"}
PLACEHOLDER_PATTERNS = [
    re.compile(r"\[[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\]"),
    re.compile(r"\{\{[^{}\n]+\}\}"),
]
ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:(FIGURE|TABLE|FORMULA):[^\]]+\]\]")


def flatten_outline_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def _visit(nodes: list[dict[str, Any]]) -> None:
        for node in nodes or []:
            current = dict(node)
            children = current.get("children")
            current["children"] = children if isinstance(children, list) else []
            flattened.append(current)
            if current["children"]:
                _visit(current["children"])

    _visit(sections or [])
    return flattened


def make_issue(
    *,
    code: str,
    level: str,
    message: str,
    section_id: str | None = None,
    section_title: str | None = None,
    suggested_action: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue = {
        "code": code,
        "level": level,
        "message": message,
        "section_id": section_id,
        "section_title": section_title,
        "suggested_action": suggested_action,
    }
    if details:
        issue["details"] = details
    return issue


def derive_validation_status(*, errors: list[dict[str, Any]], open_review_task_count: int) -> str:
    if any(issue.get("code") in HARD_BLOCKING_CODES for issue in errors):
        return "blocked"
    if open_review_task_count > 0:
        return "review_required"
    return "passed"


def collect_validation_findings(
    *,
    requirement_card: RequirementCard,
    evidence_bundle: EvidenceBundle,
    outline: ProposalOutline,
    section_drafts: list[SectionDraft],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, list[dict[str, Any]]]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    section_results: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"errors": [], "warnings": []})

    outline_sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
    section_by_id = {str(section.get("section_id")): section for section in outline_sections}
    drafts_by_id = {draft.section_id: draft for draft in section_drafts}
    evidence_items = ((evidence_bundle.content or {}).get("results") or [])
    valid_evidence_ids = {
        str(item.get("evidence_id"))
        for item in evidence_items
        if isinstance(item, dict) and item.get("evidence_id")
    }

    open_blockers = [item for item in (requirement_card.blocking_items or []) if item.get("status") != "resolved"]
    if open_blockers:
        fields = [str(item.get("field_name") or item.get("item_id") or "unknown") for item in open_blockers]
        errors.append(
            make_issue(
                code="VAL001",
                level="P0",
                message=f"Requirement Card 仍缺少待确认的 P0 字段：{', '.join(fields)}。",
                suggested_action="先补齐并确认 Requirement Card 的阻塞字段，再重新校验。",
                details={"fields": fields},
            )
        )

    for section in outline_sections:
        section_id = str(section.get("section_id") or "")
        draft = drafts_by_id.get(section_id)
        if not section.get("mandatory"):
            continue
        if draft and draft.content_md.strip():
            continue
        issue = make_issue(
            code="VAL002",
            level="P0",
            section_id=section_id,
            section_title=str(section.get("title") or ""),
            message=f"必填章节《{section.get('title') or section_id}》缺失或正文为空。",
            suggested_action="补齐必填章节并重新触发章节校验。",
        )
        errors.append(issue)
        section_results[section_id]["errors"].append(issue)

    param_values: dict[str, set[str]] = defaultdict(set)
    param_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    accepted_values: dict[str, str] = {}

    card_params = requirement_card.content.get("key_parameters") if isinstance(requirement_card.content, dict) else {}
    if isinstance(card_params, dict):
        for key, value in card_params.items():
            normalized = _normalize_param_value(value)
            if not normalized:
                continue
            param_values[str(key)].add(normalized)
            param_sources[str(key)].append({"source": "requirement_card", "value": normalized})

    for draft in section_drafts:
        validator_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        accepted_param_values = validator_result.get("accepted_param_values")
        if isinstance(accepted_param_values, dict):
            for key, value in accepted_param_values.items():
                normalized = _normalize_param_value(value)
                if normalized:
                    accepted_values[str(key)] = normalized
        snapshot = draft.global_param_snapshot if isinstance(draft.global_param_snapshot, dict) else {}
        for key, value in snapshot.items():
            normalized = _normalize_param_value(value)
            if not normalized:
                continue
            param_values[str(key)].add(normalized)
            param_sources[str(key)].append(
                {
                    "source": draft.section_id,
                    "value": normalized,
                    "section_title": draft.title,
                }
            )

    for param_name, values in sorted(param_values.items()):
        if len(values) <= 1:
            continue
        accepted_value = accepted_values.get(param_name)
        if accepted_value and values == {accepted_value}:
            continue
        errors.append(
            make_issue(
                code="VAL003",
                level="P0",
                message=f"参数 {param_name} 存在多值冲突：{' / '.join(sorted(values))}。",
                suggested_action="确认唯一参数值并回写需求卡与章节参数快照。",
                details={"param_name": param_name, "values": sorted(values), "sources": param_sources[param_name]},
            )
        )

    for draft in section_drafts:
        section = section_by_id.get(draft.section_id, {})
        section_id = draft.section_id
        section_title = draft.title
        citations = draft.citation_refs if isinstance(draft.citation_refs, list) else []
        validator_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        reuse_pack = validator_result.get("reuse_pack") if isinstance(validator_result.get("reuse_pack"), dict) else {}
        valid_reuse_ids = {
            str(item.get("block_id") or "").strip()
            for item in (reuse_pack.get("reusable_blocks") or [])
            if isinstance(item, dict) and str(item.get("block_id") or "").strip()
        }

        if _is_technical_section(section=section, draft=draft) and not citations:
            issue = make_issue(
                code="VAL004",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"技术性章节《{section_title}》缺少引用证据。",
                suggested_action="补充章节引用，至少关联一条可追溯证据。",
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)

        invalid_citations: list[dict[str, Any]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                invalid_citations.append({"reason": "citation_not_object"})
                continue
            evidence_id = citation.get("evidence_id")
            source_doc_id = citation.get("source_doc_id")
            source_title = citation.get("source_title")
            if not evidence_id or not source_doc_id or not source_title:
                invalid_citations.append({"reason": "missing_required_fields", "citation": citation})
                continue
            if valid_evidence_ids and str(evidence_id) not in valid_evidence_ids and str(evidence_id) not in valid_reuse_ids:
                invalid_citations.append({"reason": "unknown_evidence_id", "citation": citation})
        if invalid_citations:
            issue = make_issue(
                code="VAL005",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》存在失效或不完整的引用。",
                suggested_action="修复引用目标，确保 evidence_id 与证据包一致。",
                details={"invalid_citations": invalid_citations},
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)

        requires_figure_confirmation = bool(section.get("needs_human_review")) and any(
            isinstance(citation, dict) and str(citation.get("type") or "") in {"figure", "table", "parameter"}
            for citation in citations
        )
        if requires_figure_confirmation and not bool(validator_result.get("figure_confirmed")):
            issue = make_issue(
                code="VAL006",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》引用了待人工确认的复杂图表或参数资产。",
                suggested_action="请完成图表/参数对外使用确认后再导出。",
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)

        placeholder_matches = _find_placeholders(draft.content_md)
        if placeholder_matches:
            issue = make_issue(
                code="VAL007",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》存在未消解占位符。",
                suggested_action="替换正文中的占位符或模板变量，再重新校验。",
                details={"matches": placeholder_matches},
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)

        heading_quality_issues = analyze_section_heading_quality(
            section_title=section_title,
            content_md=draft.content_md,
        )
        blocking_heading_issues = [item for item in heading_quality_issues if item.severity == "high"]
        warning_heading_issues = [item for item in heading_quality_issues if item.severity != "high"]
        if blocking_heading_issues:
            issue = make_issue(
                code="VAL010",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》存在不适合直接外发的小标题或内部结构标题。",
                suggested_action="统一章节小标题风格，删除内部提示性标题后重新校验。",
                details={"issues": [item.to_dict() for item in blocking_heading_issues]},
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)
        elif warning_heading_issues:
            warning = make_issue(
                code="VAL107",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》的小标题风格仍需整理。",
                suggested_action="将空泛或风格不一致的小标题改成直接表达技术主题的中文标题。",
                details={"issues": [item.to_dict() for item in warning_heading_issues]},
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        quality_gate = validator_result.get("quality_gate") if isinstance(validator_result.get("quality_gate"), dict) else {}
        quality_gate_status = str(quality_gate.get("status") or "").lower()
        try:
            quality_gate_score = float(quality_gate.get("score"))
        except (TypeError, ValueError):
            quality_gate_score = None
        if quality_gate_status == "review_required" or (
            quality_gate_score is not None and quality_gate_score < 0.78
        ):
            warning = make_issue(
                code="VAL108",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》未通过自动质量审查，建议人工复核当前表达和结构。",
                suggested_action="优先根据自动质检意见重写该章节，再重新执行校验。",
                details={
                    "quality_gate_status": quality_gate_status or None,
                    "quality_gate_score": quality_gate_score,
                    "quality_gate_summary": quality_gate.get("summary"),
                    "quality_gate_issues": quality_gate.get("issues") or [],
                },
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        leaked_terms = _find_reuse_leakage_terms(
            content=draft.content_md,
            banned_terms=reuse_pack.get("banned_terms") or [],
        )
        if leaked_terms:
            issue = make_issue(
                code="VAL008",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》疑似残留旧项目或旧客户痕迹。",
                suggested_action="清理旧项目名称、买卖方或客户标识后重新校验。",
                details={"leaked_terms": leaked_terms},
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)

        expected_replacements = _collect_expected_replacement_values(section=section, reuse_pack=reuse_pack)
        missing_replacements = [
            field_name
            for field_name, value in expected_replacements.items()
            if not _contains_normalized_value(draft.content_md, value)
        ]
        if expected_replacements and len(missing_replacements) == len(expected_replacements):
            issue = make_issue(
                code="VAL009",
                level="P0",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》未体现当前项目的关键参数替换结果。",
                suggested_action="根据当前项目参数重写该章节，并显式落入关键数值或供货范围。",
                details={
                    "missing_fields": missing_replacements,
                    "expected_values": expected_replacements,
                },
            )
            errors.append(issue)
            section_results[section_id]["errors"].append(issue)
        elif missing_replacements:
            warning = make_issue(
                code="VAL106",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》仍缺少部分当前项目参数替换结果。",
                suggested_action="补齐缺失的关键参数，避免沿用历史方案的默认值。",
                details={
                    "missing_fields": missing_replacements,
                    "expected_values": expected_replacements,
                },
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        reuse_similarity = _compute_reuse_similarity(
            content=draft.content_md,
            reusable_blocks=reuse_pack.get("reusable_blocks") or [],
        )
        if _should_flag_similarity(section=section, reuse_similarity=reuse_similarity):
            warning = make_issue(
                code="VAL105",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》与历史复用块相似度过高，建议人工确认是否过拟合。",
                suggested_action="检查是否残留历史项目语境，并补足当前项目的差异化描述。",
                details={
                    "similarity_score": reuse_similarity.get("score"),
                    "source_title": reuse_similarity.get("source_title"),
                    "block_id": reuse_similarity.get("block_id"),
                },
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        if _looks_like_goal_drift(draft=draft, section=section):
            warning = make_issue(
                code="VAL101",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》内容过短，可能尚未充分覆盖预期目标。",
                suggested_action="补充与章节目标直接相关的业务或技术说明。",
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        if _has_implicit_assumption(draft.content_md) and not (draft.assumptions or []):
            warning = make_issue(
                code="VAL102",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》疑似存在未声明假设。",
                suggested_action="将待确认前提写入 assumptions，或直接补充确定信息。",
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

        if bool(section.get("asset_required")) and validator_result.get("recommended_assets") and not _has_asset_placeholder(
            draft.content_md
        ):
            warning = make_issue(
                code="VAL104",
                level="P1",
                section_id=section_id,
                section_title=section_title,
                message=f"章节《{section_title}》尚未显式插入推荐图表/公式占位符。",
                suggested_action="补充 [[ASSET:...]] 占位符，或确认本章节无需插入资产。",
            )
            warnings.append(warning)
            section_results[section_id]["warnings"].append(warning)

    quality_score = evidence_bundle.quality_score
    if quality_score is None or Decimal(quality_score) < Decimal("0.6500"):
        warnings.append(
            make_issue(
                code="VAL103",
                level="P1",
                message="当前证据包相关性偏低，可能影响草案稳定性。",
                suggested_action="建议重新检索证据，或补充更精确的需求参数后再生成。",
                details={"quality_score": str(quality_score) if quality_score is not None else None},
            )
        )

    return errors, warnings, section_results


def build_review_task_blueprints(
    *,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    outline: ProposalOutline,
    draft_version: int,
    existing_tasks: list[ReviewTask],
) -> list[dict[str, Any]]:
    blueprints: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for issue in errors:
        if issue.get("code") == "VAL003":
            param_name = ((issue.get("details") or {}).get("param_name") or "unknown")
            signature = task_signature(
                task_type="param_conflict",
                draft_version=draft_version,
                section_id=None,
                code=str(issue.get("code")),
                extra=str(param_name),
            )
            if signature not in seen_signatures:
                blueprints.append(
                    {
                        "task_type": "param_conflict",
                        "blocking_level": "P0",
                        "signature": signature,
                        "payload": {
                            "draft_version": draft_version,
                            "code": issue.get("code"),
                            "message": issue.get("message"),
                            "param_name": param_name,
                            "values": (issue.get("details") or {}).get("values") or [],
                        },
                    }
                )
                seen_signatures.add(signature)
        if issue.get("code") == "VAL006":
            section_id = str(issue.get("section_id") or "")
            signature = task_signature(
                task_type="figure_confirm",
                draft_version=draft_version,
                section_id=section_id,
                code=str(issue.get("code")),
            )
            if signature not in seen_signatures:
                blueprints.append(
                    {
                        "task_type": "figure_confirm",
                        "blocking_level": "P0",
                        "signature": signature,
                        "payload": {
                            "draft_version": draft_version,
                            "code": issue.get("code"),
                            "message": issue.get("message"),
                            "section_id": section_id,
                            "section_title": issue.get("section_title"),
                        },
                    }
                )
                seen_signatures.add(signature)

    for issue in warnings:
        if issue.get("code") not in CONTENT_REVIEW_CODES:
            continue
        section_id = str(issue.get("section_id") or "")
        signature = task_signature(
            task_type="content_review",
            draft_version=draft_version,
            section_id=section_id or None,
            code=str(issue.get("code")),
        )
        if signature in seen_signatures:
            continue
        blueprints.append(
            {
                "task_type": "content_review",
                "blocking_level": "P1",
                "signature": signature,
                "payload": {
                    "draft_version": draft_version,
                    "code": issue.get("code"),
                    "message": issue.get("message"),
                    "section_id": section_id or None,
                    "section_title": issue.get("section_title"),
                },
            }
        )
        seen_signatures.add(signature)

    if not any(issue.get("code") in HARD_BLOCKING_CODES for issue in errors):
        final_review_signature = task_signature(
            task_type="final_review",
            draft_version=draft_version,
            section_id=None,
            code="FINAL",
        )
        if final_review_signature not in seen_signatures:
            blueprints.append(
                {
                    "task_type": "final_review",
                    "blocking_level": "P0",
                    "signature": final_review_signature,
                    "payload": {
                        "draft_version": draft_version,
                        "outline_id": str(outline.id),
                        "title": (outline.outline_json or {}).get("title"),
                        "message": "请确认当前草案已达到可发送/可导出门槛。",
                    },
                }
            )
            seen_signatures.add(final_review_signature)

    return blueprints


def task_signature(
    *,
    task_type: str,
    draft_version: int,
    section_id: str | None,
    code: str,
    extra: str | None = None,
) -> str:
    parts = [task_type, str(draft_version), section_id or "-", code]
    if extra:
        parts.append(extra)
    return ":".join(parts)


class ValidationService:
    async def validate_project(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int | None = None,
        outline_id: UUID | None = None,
    ) -> tuple[Job, ValidationReport]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)
        target_draft_version = draft_version or int(project.current_draft_version or 0)
        if target_draft_version <= 0:
            raise ArtifactValidationError("No draft version exists for this project")
        section_drafts = await self._load_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=target_draft_version,
        )
        if not section_drafts:
            raise ArtifactValidationError("Section drafts not found for current draft version")

        job = Job(
            project_id=project_id,
            job_type="validate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "draft_version": target_draft_version,
                "outline_id": str(outline.id),
            },
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        existing_tasks = await self._list_review_tasks_raw(session=session, project_id=project_id)
        blueprints = build_review_task_blueprints(
            errors=errors,
            warnings=warnings,
            outline=outline,
            draft_version=target_draft_version,
            existing_tasks=existing_tasks,
        )
        current_signatures = {blueprint["signature"] for blueprint in blueprints}
        await self._close_obsolete_tasks(
            session=session,
            review_tasks=existing_tasks,
            draft_version=target_draft_version,
            valid_signatures=current_signatures,
        )

        created_tasks: list[ReviewTask] = []
        for blueprint in blueprints:
            if self._has_open_task(existing_tasks, signature=blueprint["signature"]):
                continue
            payload = dict(blueprint["payload"])
            payload["signature"] = blueprint["signature"]
            task = ReviewTask(
                project_id=project_id,
                task_type=blueprint["task_type"],
                blocking_level=blueprint["blocking_level"],
                payload=payload,
                status="open",
            )
            session.add(task)
            created_tasks.append(task)
            existing_tasks.append(task)
        await session.flush()

        for draft in section_drafts:
            current_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
            persistent_fields = {
                key: current_result[key]
                for key in [
                    "figure_confirmed",
                    "accepted_param_values",
                    "review_resolutions",
                    "final_reviewed_at",
                    "recommended_assets",
                    "generation_mode",
                    "reuse_pack",
                ]
                if key in current_result
            }
            per_section = section_results.get(draft.section_id, {"errors": [], "warnings": []})
            draft.validator_result = {
                **persistent_fields,
                "errors": per_section["errors"],
                "warnings": per_section["warnings"],
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
            if per_section["errors"] or per_section["warnings"]:
                draft.status = "review_required"
            elif draft.status == "review_required":
                draft.status = "generated"

        open_current_tasks = [
            task
            for task in existing_tasks
            if task.status == "open" and int((task.payload or {}).get("draft_version") or target_draft_version) == target_draft_version
        ]
        report_status = derive_validation_status(errors=errors, open_review_task_count=len(open_current_tasks))
        report = ValidationReport(
            project_id=project_id,
            draft_version=target_draft_version,
            outline_id=outline.id,
            requirement_card_id=requirement_card.id,
            evidence_bundle_id=evidence_bundle.id,
            status=report_status,
            errors=errors,
            warnings=warnings,
            review_tasks_created=[str(task.id) for task in created_tasks],
        )
        session.add(report)
        await session.flush()

        outline.validator_status = report_status
        project.status = "EXPORTABLE" if report_status == "passed" else "REVIEW_REQUIRED"

        session.add(
            AuditLog(
                project_id=project_id,
                action="validate",
                entity_type="validation_report",
                entity_id=report.id,
                payload={
                    "draft_version": target_draft_version,
                    "status": report_status,
                    "error_count": len(errors),
                    "warning_count": len(warnings),
                },
                trace_id=job.trace_id,
            )
        )

        job.status = "succeeded"
        job.output_ref = {"validation_report_id": str(report.id), "status": report_status}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(report)
        return job, report

    async def get_latest_validation_report(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
    ) -> ValidationReport:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        result = await session.scalars(
            select(ValidationReport)
            .where(ValidationReport.project_id == project_id)
            .order_by(ValidationReport.created_at.desc())
            .limit(1)
        )
        report = result.first()
        if not report:
            raise ArtifactNotFoundError("Validation report not found")
        return report

    async def list_review_tasks(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
    ) -> list[ReviewTask]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        result = await session.scalars(
            select(ReviewTask)
            .where(ReviewTask.project_id == project_id)
            .order_by(ReviewTask.created_at.desc())
        )
        return list(result.all())

    async def resolve_review_task(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        task_id: UUID,
        resolution: Any = None,
        status: str = "resolved",
        assignee_user_id: UUID | None = None,
    ) -> ReviewTask:
        if status not in {"resolved", "rejected"}:
            raise ArtifactValidationError("Unsupported review task status")

        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        task = await session.get(ReviewTask, task_id)
        if not task or task.project_id != project_id:
            raise ArtifactNotFoundError("Review task not found")
        if task.status not in {"open", "in_progress"}:
            raise ArtifactValidationError("Review task is not open")

        if assignee_user_id is not None:
            task.assignee_user_id = assignee_user_id
        payload = dict(task.payload or {})
        payload["resolution"] = resolution
        task.payload = payload
        task.status = status
        task.resolved_at = datetime.now(timezone.utc)

        await self._apply_review_resolution(
            session=session,
            project=project,
            task=task,
            resolution=resolution,
            status=status,
        )
        await self._refresh_project_state_after_review(session=session, project=project)

        session.add(
            AuditLog(
                project_id=project_id,
                action="resolve_review_task",
                entity_type="review_task",
                entity_id=task.id,
                payload={"status": status, "task_type": task.task_type, "resolution": resolution},
            )
        )

        await session.commit()
        await session.refresh(task)
        return task

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

    async def _load_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
    ) -> list[SectionDraft]:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
            )
            .order_by(SectionDraft.section_id.asc())
        )
        return list(result.all())

    async def _list_review_tasks_raw(self, *, session: AsyncSession, project_id: UUID) -> list[ReviewTask]:
        result = await session.scalars(
            select(ReviewTask)
            .where(ReviewTask.project_id == project_id)
            .order_by(ReviewTask.created_at.asc())
        )
        return list(result.all())

    async def _close_obsolete_tasks(
        self,
        *,
        session: AsyncSession,
        review_tasks: list[ReviewTask],
        draft_version: int,
        valid_signatures: set[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        for task in review_tasks:
            if task.status != "open":
                continue
            payload = task.payload if isinstance(task.payload, dict) else {}
            task_draft_version = int(payload.get("draft_version") or draft_version)
            signature = str(payload.get("signature") or "")
            if task_draft_version != draft_version or signature not in valid_signatures:
                task.status = "resolved"
                task.resolved_at = now
                task.payload = {
                    **payload,
                    "auto_resolution": "superseded_by_validation",
                }
                session.add(task)

    def _has_open_task(self, review_tasks: list[ReviewTask], *, signature: str) -> bool:
        return any(
            task.status == "open"
            and isinstance(task.payload, dict)
            and str(task.payload.get("signature") or "") == signature
            for task in review_tasks
        )

    async def _apply_review_resolution(
        self,
        *,
        session: AsyncSession,
        project: Project,
        task: ReviewTask,
        resolution: Any,
        status: str,
    ) -> None:
        payload = task.payload if isinstance(task.payload, dict) else {}
        draft_version = int(payload.get("draft_version") or project.current_draft_version or 0)
        section_id = payload.get("section_id")
        draft = None
        if section_id:
            result = await session.scalars(
                select(SectionDraft)
                .where(
                    SectionDraft.project_id == project.id,
                    SectionDraft.draft_version == draft_version,
                    SectionDraft.section_id == str(section_id),
                )
                .limit(1)
            )
            draft = result.first()

        if task.task_type == "figure_confirm" and draft:
            current = draft.validator_result if isinstance(draft.validator_result, dict) else {}
            current["figure_confirmed"] = status == "resolved"
            current.setdefault("review_resolutions", {})["figure_confirm"] = resolution
            draft.validator_result = current
            draft.status = "approved" if status == "resolved" else "rejected"
            session.add(draft)

        if task.task_type == "content_review" and draft:
            draft.status = "approved" if status == "resolved" else "rejected"
            current = draft.validator_result if isinstance(draft.validator_result, dict) else {}
            current.setdefault("review_resolutions", {})["content_review"] = resolution
            draft.validator_result = current
            session.add(draft)

        if task.task_type == "param_conflict":
            param_name = payload.get("param_name")
            resolved_value = resolution.get("value") if isinstance(resolution, dict) else resolution
            if param_name and resolved_value not in (None, ""):
                requirement_card = None
                if project.current_requirement_card_id:
                    requirement_card = await session.get(RequirementCard, project.current_requirement_card_id)
                if requirement_card:
                    content = dict(requirement_card.content or {})
                    key_parameters = dict(content.get("key_parameters") or {})
                    key_parameters[str(param_name)] = resolved_value
                    content["key_parameters"] = key_parameters
                    requirement_card.content = content
                    session.add(requirement_card)
                drafts = await self._load_section_drafts(
                    session=session,
                    project_id=project.id,
                    draft_version=draft_version,
                )
                for item in drafts:
                    snapshot = dict(item.global_param_snapshot or {})
                    snapshot[str(param_name)] = resolved_value
                    item.global_param_snapshot = snapshot
                    current = item.validator_result if isinstance(item.validator_result, dict) else {}
                    accepted = dict(current.get("accepted_param_values") or {})
                    accepted[str(param_name)] = resolved_value
                    current["accepted_param_values"] = accepted
                    item.validator_result = current
                    session.add(item)

        if task.task_type == "final_review" and status == "resolved":
            drafts = await self._load_section_drafts(
                session=session,
                project_id=project.id,
                draft_version=draft_version,
            )
            reviewed_at = datetime.now(timezone.utc).isoformat()
            for item in drafts:
                current = item.validator_result if isinstance(item.validator_result, dict) else {}
                current["final_reviewed_at"] = reviewed_at
                item.validator_result = current
                if item.status in {"generated", "edited", "review_required"}:
                    item.status = "approved"
                session.add(item)

    async def _refresh_project_state_after_review(self, *, session: AsyncSession, project: Project) -> None:
        latest_report = await self.get_latest_validation_report(session=session, project_id=project.id)
        open_tasks = [
            task
            for task in await self._list_review_tasks_raw(session=session, project_id=project.id)
            if task.status in {"open", "rejected"}
            and int((task.payload or {}).get("draft_version") or project.current_draft_version or 0)
            == int(project.current_draft_version or 0)
        ]
        report_status = derive_validation_status(errors=latest_report.errors or [], open_review_task_count=len(open_tasks))
        latest_report.status = report_status
        outline = await self._resolve_outline(session=session, project_id=project.id, outline_id=project.current_outline_id)
        outline.validator_status = report_status
        project.status = "EXPORTABLE" if report_status == "passed" else "REVIEW_REQUIRED"
        session.add(latest_report)
        session.add(outline)
        session.add(project)


def _normalize_param_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return str(value).strip()


def _is_technical_section(*, section: dict[str, Any], draft: SectionDraft) -> bool:
    title = str(section.get("title") or draft.title or "")
    evidence_types = {str(item) for item in (section.get("expected_evidence_types") or [])}
    return any(hint in title for hint in TECHNICAL_SECTION_HINTS) or bool(
        evidence_types.intersection({"section", "figure", "table", "parameter"})
    )


def _find_placeholders(content: str) -> list[str]:
    matches: list[str] = []
    for pattern in PLACEHOLDER_PATTERNS:
        matches.extend(match.group(0) for match in pattern.finditer(content or ""))
    return sorted(set(matches))


def _collect_expected_replacement_values(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> dict[str, str]:
    if not bool(section.get("parameter_sensitive")):
        return {}
    replacement_hints = reuse_pack.get("replacement_hints") if isinstance(reuse_pack.get("replacement_hints"), dict) else {}
    must_replace_fields = [
        str(field_name)
        for field_name in (reuse_pack.get("must_replace_fields") or [])
        if str(field_name) in PARAMETER_REPLACE_FIELDS
    ]
    expected: dict[str, str] = {}
    for field_name in must_replace_fields:
        value = replacement_hints.get(field_name)
        if value in (None, "", [], {}):
            continue
        expected[field_name] = str(value).strip()
    return expected


def _contains_normalized_value(content: str, expected_value: str) -> bool:
    normalized_content = re.sub(r"\s+", "", str(content or "")).lower()
    normalized_expected = re.sub(r"\s+", "", str(expected_value or "")).lower()
    if not normalized_expected:
        return True
    return normalized_expected in normalized_content


def _compute_reuse_similarity(*, content: str, reusable_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_content = _normalize_similarity_text(content)
    if len(normalized_content) < 60:
        return {}

    best: dict[str, Any] = {}
    best_score = 0.0
    for block in reusable_blocks:
        source_content = _normalize_similarity_text(block.get("content_md") or "")
        if len(source_content) < 60:
            continue
        score = SequenceMatcher(None, normalized_content, source_content).ratio()
        if score <= best_score:
            continue
        best_score = score
        best = {
            "score": round(score, 4),
            "block_id": block.get("block_id"),
            "source_title": block.get("source_title"),
        }
    return best


def _normalize_similarity_text(content: str) -> str:
    normalized = str(content or "")
    normalized = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", normalized)
    normalized = re.sub(r"[#>*`_\-\|\[\]\(\)]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _should_flag_similarity(*, section: dict[str, Any], reuse_similarity: dict[str, Any]) -> bool:
    score = float(reuse_similarity.get("score") or 0)
    if score <= 0:
        return False
    if score >= 0.98:
        return True
    customer_specificity = str(section.get("customer_specificity") or "medium").lower()
    if bool(section.get("parameter_sensitive")) and score >= 0.92:
        return True
    if customer_specificity in {"medium", "high"} and score >= 0.92:
        return True
    return False


def _has_asset_placeholder(content: str) -> bool:
    return bool(ASSET_PLACEHOLDER_PATTERN.search(content or ""))


def _find_reuse_leakage_terms(*, content: str, banned_terms: list[Any]) -> list[str]:
    text = content or ""
    leaked_terms: list[str] = []
    for term in banned_terms:
        candidate = str(term or "").strip()
        if len(candidate) < 3:
            continue
        if candidate in text and candidate not in leaked_terms:
            leaked_terms.append(candidate)
    return leaked_terms


def _looks_like_goal_drift(*, draft: SectionDraft, section: dict[str, Any]) -> bool:
    content = (draft.content_md or "").strip()
    if len(content) < 60:
        return True
    title = str(section.get("title") or draft.title or "").strip()
    if not title:
        return False
    keywords = [segment for segment in re.split(r"[\s/、，,（）()]+", title) if len(segment) >= 2]
    return bool(keywords) and not any(keyword in content for keyword in keywords)


def _has_implicit_assumption(content: str) -> bool:
    text = content or ""
    return any(token in text for token in ASSUMPTION_HINTS)
