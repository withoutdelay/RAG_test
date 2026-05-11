from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import get_session_factory
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.job import Job
from app.models.proposal_outline import ProposalOutline
from app.models.project import Project
from app.models.project_export import ProjectExport
from app.models.section_draft import SectionDraft
from app.services.composition.outline_service import normalize_outline_payload


HEADING_PREFIX_RE = re.compile(r"^\s*(?:#+\s*)?(?P<number>\d+(?:\.\d+)*)(?:[、.\s]+)?(?P<title>.+?)\s*$")
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,5}\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
ASSET_PLACEHOLDER_RE = re.compile(r"\[\[ASSET:([A-Z_]+):([^\]]+)\]\]")
INTERNAL_RESIDUE_RE = re.compile(
    r"(prompt|draft|smoke|review|validator|validation|模型|提示词|质检约束|禁用表述|对标资料|项目需求|内部残留|LLM|AI\s*Wiki)",
    re.IGNORECASE,
)
FILLER_RE = re.compile(
    r"(本章围绕|本节围绕|本章节围绕|确保设备满足|综合要求|总体要求|进行说明|提供支撑|奠定基础|具有重要意义|有效保障|关键环节|主要包括以下方面)"
)
TECHNICAL_SECTION_RE = re.compile(r"(系统方案|主回路|主接线|拓扑|单线图|启动|同步|变频器|软起|技术数据|技术参数|核心设备|控制|联锁|接口)")
COMMERCIAL_OR_SERVICE_RE = re.compile(r"(交付|资料|培训|售后|服务|备品|备件|合同|商务|报价|建设|运营|项目管理)")
TECHNICAL_NOISE_RE = re.compile(r"(培训|交付资料|提交资料|售后服务|备品备件|合同|商务|报价|建设与运营|项目建设|经营方案)")
COMMERCIAL_NOISE_RE = re.compile(r"(负载数据|Load data|启动曲线|同步过程|主回路|主接线|单线图|变频器技术数据|技术参数|阻力矩|转动惯量)")
DEFAULT_THRESHOLDS = {
    "max_duration_seconds": 600,
    "target_duration_seconds": 300,
    "min_outline_coverage": 0.8,
    "min_technical_evidence_accuracy": 0.8,
    "min_asset_top3_source_bound_rate": 0.8,
    "max_wrong_figure_body_rate": 0.1,
    "max_internal_residue_count": 0,
}


@dataclass(frozen=True)
class HeadingItem:
    section_id: str
    title: str
    order: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate holdout outline and draft separately, and emit a baseline-outline override JSON."
    )
    parser.add_argument("--project-id", required=True, help="Generated project UUID.")
    parser.add_argument("--baseline-document-name", required=True, help="Holdout/baseline document filename or substring.")
    parser.add_argument("--draft-version", type=int, default=0, help="Draft version. Defaults to latest/current.")
    parser.add_argument(
        "--output",
        default="output/holdout-outline-draft-eval.md",
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="JSON output path. Defaults to --output with .json suffix.",
    )
    parser.add_argument(
        "--outline-override-output",
        default="",
        help="Baseline outline override JSON path. Defaults to --output stem + -outline-override.json.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    project_id = UUID(args.project_id)
    output_path = _resolve_repo_relative_path(args.output)
    json_output_path = _resolve_repo_relative_path(args.json_output) if args.json_output else output_path.with_suffix(".json")
    override_path = (
        _resolve_repo_relative_path(args.outline_override_output)
        if args.outline_override_output
        else output_path.with_name(f"{output_path.stem}-outline-override.json")
    )

    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise SystemExit(f"Project not found: {project_id}")

        outline = await _load_latest_outline(session=session, project_id=project_id)
        generated_outline = dict(outline.outline_json or {})
        baseline_doc = await _find_baseline_document(session=session, name=args.baseline_document_name)
        baseline_headings = await _collect_baseline_headings(session=session, document_id=baseline_doc.id)
        draft_version = args.draft_version or int(project.current_draft_version or 0)
        drafts = await _load_section_drafts(session=session, project_id=project_id, draft_version=draft_version)
        generation_job = await _load_generation_job(session=session, project_id=project_id, draft_version=draft_version)
        latest_export = await _load_latest_export(session=session, project_id=project_id, draft_version=draft_version)

    generated_sections = _flatten_outline_sections(generated_outline.get("sections") or [])
    outline_eval = _evaluate_outline(generated_sections=generated_sections, baseline_headings=baseline_headings)
    draft_eval = _evaluate_drafts(drafts=drafts, baseline_headings=baseline_headings)
    asset_stability_eval = _evaluate_asset_stability(drafts=drafts)
    evidence_eval = _evaluate_evidence_quality(drafts=drafts)
    writing_eval = _evaluate_writing_quality(drafts=drafts)
    runtime_eval = _evaluate_runtime(job=generation_job)
    export_eval = _evaluate_export(export_record=latest_export)
    gate_eval = _evaluate_phase8_gates(
        outline_eval=outline_eval,
        evidence_eval=evidence_eval,
        asset_stability_eval=asset_stability_eval,
        writing_eval=writing_eval,
        runtime_eval=runtime_eval,
        export_eval=export_eval,
    )
    override_outline = _build_baseline_outline_override(
        project_name=project.name,
        source_outline=generated_outline,
        baseline_headings=baseline_headings,
    )

    payload = {
        "project_id": str(project_id),
        "project_name": project.name,
        "baseline_document": baseline_doc.filename,
        "outline_id": str(outline.id),
        "draft_version": draft_version,
        "baseline_heading_count": len(baseline_headings),
        "generated_outline_count": len(generated_sections),
        "outline_eval": outline_eval,
        "draft_eval": draft_eval,
        "asset_stability_eval": asset_stability_eval,
        "evidence_eval": evidence_eval,
        "writing_eval": writing_eval,
        "runtime_eval": runtime_eval,
        "export_eval": export_eval,
        "phase8_gate": gate_eval,
        "outline_override_path": str(override_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    override_path.write_text(json.dumps(override_outline, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.write_text(_render_markdown(payload=payload, baseline_headings=baseline_headings), encoding="utf-8")
    print(str(output_path))


async def _load_latest_outline(*, session: Any, project_id: UUID) -> ProposalOutline:
    result = await session.scalars(
        select(ProposalOutline)
        .where(ProposalOutline.project_id == project_id)
        .order_by(ProposalOutline.version.desc(), ProposalOutline.created_at.desc())
        .limit(1)
    )
    outline = result.first()
    if outline is None:
        raise SystemExit(f"Outline not found for project: {project_id}")
    return outline


async def _find_baseline_document(*, session: Any, name: str) -> Document:
    result = await session.scalars(
        select(Document)
        .where(Document.filename.ilike(f"%{name}%"))
        .order_by(Document.created_at.desc())
    )
    documents = list(result.all())
    if not documents:
        raise SystemExit(f"Baseline document not found by name: {name}")
    holdout = [item for item in documents if str(item.doc_type or "") == "holdout_eval"]
    return holdout[0] if holdout else documents[0]


async def _collect_baseline_headings(*, session: Any, document_id: UUID) -> list[HeadingItem]:
    result = await session.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index.asc())
    )
    seen: OrderedDict[str, HeadingItem] = OrderedDict()
    for chunk in result.all():
        candidates = [
            str((chunk.meta or {}).get("source_heading") or "").strip(),
            str((chunk.meta or {}).get("section_path") or "").strip(),
            str(chunk.heading_path or "").strip(),
        ]
        candidates.extend(_markdown_headings(str(chunk.content or "")))
        for candidate in candidates:
            item = _normalize_heading_item(candidate, order=int(chunk.chunk_index or 0))
            if item is None:
                continue
            key = item.section_id or item.title
            if key not in seen:
                seen[key] = item
    items = list(seen.values())
    numbered = [item for item in items if item.section_id]
    return numbered or items


async def _load_section_drafts(*, session: Any, project_id: UUID, draft_version: int) -> list[SectionDraft]:
    result = await session.scalars(
        select(SectionDraft)
        .where(SectionDraft.project_id == project_id, SectionDraft.draft_version == draft_version)
        .order_by(SectionDraft.section_id.asc())
    )
    return list(result.all())


async def _load_generation_job(*, session: Any, project_id: UUID, draft_version: int) -> Job | None:
    result = await session.scalars(
        select(Job)
        .where(Job.project_id == project_id, Job.job_type == "generate")
        .order_by(Job.created_at.desc())
    )
    for job in result.all():
        output_ref = job.output_ref if isinstance(job.output_ref, dict) else {}
        if int(output_ref.get("draft_version") or 0) == int(draft_version or 0):
            return job
    return None


async def _load_latest_export(*, session: Any, project_id: UUID, draft_version: int) -> ProjectExport | None:
    result = await session.scalars(
        select(ProjectExport)
        .where(ProjectExport.project_id == project_id, ProjectExport.draft_version == draft_version)
        .order_by(ProjectExport.created_at.desc())
        .limit(1)
    )
    return result.first()


def _markdown_headings(content: str) -> list[str]:
    return [match.group(1).strip() for line in content.splitlines() if (match := MARKDOWN_HEADING_RE.match(line))]


def _normalize_heading_item(value: str, *, order: int) -> HeadingItem | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return None
    if text in {"版本", "页码", "目录"}:
        return None
    match = HEADING_PREFIX_RE.match(text)
    if match:
        number = match.group("number").strip(". ")
        suffix = match.group("title").strip()
        if _looks_like_heading_noise(suffix):
            return None
        title = f"{number} {suffix}"
        return HeadingItem(section_id=number, title=title, order=order)
    if len(text) > 36 or not any("\u4e00" <= char <= "\u9fff" for char in text):
        return None
    return HeadingItem(section_id="", title=text, order=order)


def _looks_like_heading_noise(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    alpha_words = re.findall(r"[A-Za-z]{2,}", text)
    if chinese_count == 0 and not alpha_words:
        return True
    digit_count = sum(char.isdigit() for char in text)
    if digit_count >= 10 and chinese_count == 0 and len(alpha_words) <= 2:
        return True
    return False


def _flatten_outline_sections(sections: list[dict[str, Any]], *, parent: str = "") -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        fallback_id = f"{parent}.{index}" if parent else str(index)
        section_id = str(section.get("section_id") or fallback_id)
        flattened.append({"section_id": section_id, "title": str(section.get("title") or "").strip(), "raw": section})
        children = section.get("children") or []
        if isinstance(children, list):
            flattened.extend(_flatten_outline_sections(children, parent=section_id))
    return flattened


def _evaluate_outline(*, generated_sections: list[dict[str, Any]], baseline_headings: list[HeadingItem]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for baseline in baseline_headings:
        best = max(
            (
                {
                    "section_id": str(section.get("section_id") or ""),
                    "title": str(section.get("title") or ""),
                    "score": _title_similarity(baseline.title, str(section.get("title") or "")),
                }
                for section in generated_sections
            ),
            key=lambda item: item["score"],
            default={"section_id": "", "title": "", "score": 0.0},
        )
        matches.append({"baseline": baseline.__dict__, "best_generated": best})
    missing = [item for item in matches if float(item["best_generated"]["score"]) < 0.34]
    strong = [item for item in matches if float(item["best_generated"]["score"]) >= 0.55]
    return {
        "coverage": round(len(strong) / max(1, len(baseline_headings)), 4),
        "weak_or_missing_count": len(missing),
        "weak_or_missing": missing[:20],
        "matches": matches,
    }


def _evaluate_drafts(*, drafts: list[SectionDraft], baseline_headings: list[HeadingItem]) -> dict[str, Any]:
    baseline_titles = [item.title for item in baseline_headings]
    section_reports: list[dict[str, Any]] = []
    for draft in drafts:
        assets = draft.recommended_assets
        placeholders = ASSET_PLACEHOLDER_RE.findall(draft.content_md or "")
        diagnostics = (
            ((draft.validator_result or {}).get("generation_details") or {})
            .get("retrieval_trace", {})
            .get("layers", {})
            .get("assets", {})
            .get("diagnostics", {})
        )
        section_reports.append(
            {
                "section_id": draft.section_id,
                "title": draft.title,
                "best_baseline_score": max((_title_similarity(draft.title, title) for title in baseline_titles), default=0.0),
                "asset_placeholder_count": len(placeholders),
                "recommended_asset_count": len(assets),
                "recommended_figures": [
                    {
                        "asset_id": str(asset.get("asset_id") or ""),
                        "title": asset.get("title") or asset.get("display_title"),
                        "document_name": asset.get("document_name"),
                        "score": asset.get("score"),
                        "source_binding": ((asset.get("metadata") or {}).get("source_binding") or {})
                        if isinstance(asset.get("metadata"), dict)
                        else {},
                        "asset_stability_gate": ((asset.get("metadata") or {}).get("asset_stability_gate") or {})
                        if isinstance(asset.get("metadata"), dict)
                        else {},
                    }
                    for asset in assets
                    if str(asset.get("asset_type") or "") == "figure"
                ],
                "asset_diagnostics": diagnostics,
            }
        )
    no_asset_sections = [
        item
        for item in section_reports
        if item["recommended_asset_count"] == 0 and _text_has_asset_intent(str(item["title"]))
    ]
    return {
        "section_count": len(section_reports),
        "sections_with_placeholders": sum(1 for item in section_reports if item["asset_placeholder_count"] > 0),
        "asset_intent_without_recommendation": no_asset_sections[:20],
        "sections": section_reports,
    }


def _evaluate_evidence_quality(*, drafts: list[SectionDraft]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    technical_sections = 0
    technical_clean = 0
    body_pollution_count = 0
    evidence_pollution_count = 0
    for draft in drafts:
        title = str(draft.title or "")
        content = str(draft.content_md or "")
        validator_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        evidence_text = "\n".join(_collect_evidence_strings(validator_result))
        is_technical = bool(TECHNICAL_SECTION_RE.search(title))
        is_commercial = bool(COMMERCIAL_OR_SERVICE_RE.search(title))
        body_hits = _detect_cross_section_noise(section_title=title, text=content)
        evidence_hits = _detect_cross_section_noise(section_title=title, text=evidence_text)
        if is_technical:
            technical_sections += 1
            if not body_hits and not evidence_hits:
                technical_clean += 1
        body_pollution_count += len(body_hits)
        evidence_pollution_count += len(evidence_hits)
        reports.append(
            {
                "section_id": draft.section_id,
                "title": draft.title,
                "section_kind": "technical" if is_technical else ("commercial_or_service" if is_commercial else "other"),
                "body_pollution_hits": body_hits,
                "evidence_pollution_hits": evidence_hits,
                "evidence_string_count": len(_collect_evidence_strings(validator_result)),
            }
        )
    return {
        "technical_section_count": technical_sections,
        "technical_evidence_accuracy": _safe_ratio(technical_clean, technical_sections),
        "body_pollution_count": body_pollution_count,
        "evidence_pollution_count": evidence_pollution_count,
        "polluted_sections": [
            item
            for item in reports
            if item["body_pollution_hits"] or item["evidence_pollution_hits"]
        ],
        "sections": reports,
    }


def _evaluate_writing_quality(*, drafts: list[SectionDraft]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    total_internal = 0
    total_filler = 0
    total_paragraphs = 0
    for draft in drafts:
        content = str(draft.content_md or "")
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", content) if part.strip()]
        internal_hits = _regex_hit_samples(INTERNAL_RESIDUE_RE, content)
        filler_hits = _regex_hit_samples(FILLER_RE, content)
        total_internal += len(internal_hits)
        total_filler += len(filler_hits)
        total_paragraphs += len(paragraphs)
        reports.append(
            {
                "section_id": draft.section_id,
                "title": draft.title,
                "paragraph_count": len(paragraphs),
                "internal_residue_hits": internal_hits,
                "filler_hits": filler_hits,
                "filler_ratio": _safe_ratio(len(filler_hits), max(1, len(paragraphs))),
            }
        )
    return {
        "internal_residue_count": total_internal,
        "filler_hit_count": total_filler,
        "paragraph_count": total_paragraphs,
        "filler_ratio": _safe_ratio(total_filler, max(1, total_paragraphs)),
        "sections_with_internal_residue": [item for item in reports if item["internal_residue_hits"]],
        "sections_with_filler": [item for item in reports if item["filler_hits"]],
        "sections": reports,
    }


def _evaluate_asset_stability(*, drafts: list[SectionDraft]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for draft in drafts:
        validator_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        recommended_assets = validator_result.get("recommended_assets") if isinstance(validator_result.get("recommended_assets"), list) else []
        asset_candidates = validator_result.get("asset_candidates") if isinstance(validator_result.get("asset_candidates"), list) else []
        asset_trace = validator_result.get("asset_trace") if isinstance(validator_result.get("asset_trace"), dict) else {}
        if not asset_trace:
            asset_trace = (
                (validator_result.get("generation_details") or {})
                .get("retrieval_trace", {})
                .get("layers", {})
                .get("assets", {})
                if isinstance(validator_result.get("generation_details"), dict)
                else {}
            )
        diagnostics = asset_trace.get("diagnostics") if isinstance(asset_trace.get("diagnostics"), dict) else {}
        stability = diagnostics.get("asset_stability") if isinstance(diagnostics.get("asset_stability"), dict) else {}
        placeholders = ASSET_PLACEHOLDER_RE.findall(draft.content_md or "")
        asset_lookup = {
            str(asset.get("asset_id") or ""): asset
            for asset in [*recommended_assets, *asset_candidates]
            if isinstance(asset, dict) and str(asset.get("asset_id") or "")
        }
        wrong_body_assets = []
        for placeholder_type, asset_id in placeholders:
            asset = asset_lookup.get(str(asset_id))
            metadata = asset.get("metadata") if isinstance(asset, dict) and isinstance(asset.get("metadata"), dict) else {}
            gate = metadata.get("asset_stability_gate") if isinstance(metadata.get("asset_stability_gate"), dict) else {}
            blocking_flags = list(gate.get("blocking_flags") or []) if isinstance(gate, dict) else []
            if placeholder_type == "FIGURE" and blocking_flags:
                wrong_body_assets.append({"asset_id": asset_id, "blocking_flags": blocking_flags})

        figure_required = _text_has_asset_intent(draft.title)
        recommended_figures = [asset for asset in recommended_assets if str(asset.get("asset_type") or "") == "figure"]
        top3_candidates = [*recommended_figures, *[asset for asset in asset_candidates if str(asset.get("asset_type") or "") == "figure"]][:3]
        reports.append(
            {
                "section_id": draft.section_id,
                "title": draft.title,
                "figure_required": figure_required,
                "primary_source_bound": _asset_has_source_section(recommended_figures[0]) if recommended_figures else False,
                "top3_source_bound": any(_asset_has_source_section(asset) for asset in top3_candidates),
                "wrong_body_assets": wrong_body_assets,
                "missing_asset_explainable": bool(stability.get("missing_asset_explainable")),
                "missing_asset_diagnostics": stability.get("missing_asset_diagnostics") or [],
                "filtered_count": int(stability.get("filtered_count") or 0),
            }
        )

    required = [item for item in reports if item["figure_required"]]
    wrong_body_count = sum(len(item["wrong_body_assets"]) for item in reports)
    missing_required = [item for item in required if not item["top3_source_bound"]]
    return {
        "figure_required_sections": len(required),
        "top3_source_bound_rate": _safe_ratio(sum(1 for item in required if item["top3_source_bound"]), len(required)),
        "primary_source_bound_rate": _safe_ratio(sum(1 for item in required if item["primary_source_bound"]), len(required)),
        "wrong_figure_body_rate": _safe_ratio(wrong_body_count, max(1, len(required))),
        "missing_asset_explainability_rate": _safe_ratio(
            sum(1 for item in missing_required if item["missing_asset_explainable"] or item["missing_asset_diagnostics"]),
            len(missing_required),
        ),
        "filtered_asset_count": sum(int(item["filtered_count"] or 0) for item in reports),
        "sections": reports,
    }


def _evaluate_runtime(*, job: Job | None) -> dict[str, Any]:
    if job is None:
        return {
            "available": False,
            "duration_seconds": None,
            "within_target_5m": None,
            "within_hard_limit_10m": None,
            "generation_summary": {},
        }
    duration = None
    if job.started_at and job.completed_at:
        duration = max(0.0, (job.completed_at - job.started_at).total_seconds())
    output_ref = job.output_ref if isinstance(job.output_ref, dict) else {}
    return {
        "available": True,
        "job_id": str(job.id),
        "status": job.status,
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "within_target_5m": duration <= DEFAULT_THRESHOLDS["target_duration_seconds"] if duration is not None else None,
        "within_hard_limit_10m": duration <= DEFAULT_THRESHOLDS["max_duration_seconds"] if duration is not None else None,
        "generation_summary": output_ref.get("generation_summary") if isinstance(output_ref.get("generation_summary"), dict) else {},
    }


def _evaluate_export(*, export_record: ProjectExport | None) -> dict[str, Any]:
    if export_record is None:
        return {
            "available": False,
            "word_export_available": False,
            "status": "missing",
            "file_type": None,
            "file_name": None,
        }
    return {
        "available": True,
        "word_export_available": str(export_record.file_type or "").lower() == "docx"
        and str(export_record.status or "") in {"succeeded", "forced", "ready", "exported"},
        "status": export_record.status,
        "file_type": export_record.file_type,
        "file_name": export_record.file_name,
        "storage_path": export_record.storage_path,
    }


def _evaluate_phase8_gates(
    *,
    outline_eval: dict[str, Any],
    evidence_eval: dict[str, Any],
    asset_stability_eval: dict[str, Any],
    writing_eval: dict[str, Any],
    runtime_eval: dict[str, Any],
    export_eval: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "outline_coverage": _gate_check(
            value=outline_eval.get("coverage"),
            threshold=DEFAULT_THRESHOLDS["min_outline_coverage"],
            op=">=",
        ),
        "technical_evidence_accuracy": _gate_check(
            value=evidence_eval.get("technical_evidence_accuracy"),
            threshold=DEFAULT_THRESHOLDS["min_technical_evidence_accuracy"],
            op=">=",
        ),
        "asset_top3_source_bound_rate": _gate_check(
            value=asset_stability_eval.get("top3_source_bound_rate"),
            threshold=DEFAULT_THRESHOLDS["min_asset_top3_source_bound_rate"],
            op=">=",
            skip_when_none=True,
        ),
        "wrong_figure_body_rate": _gate_check(
            value=asset_stability_eval.get("wrong_figure_body_rate"),
            threshold=DEFAULT_THRESHOLDS["max_wrong_figure_body_rate"],
            op="<=",
            skip_when_none=True,
        ),
        "internal_residue_count": _gate_check(
            value=writing_eval.get("internal_residue_count"),
            threshold=DEFAULT_THRESHOLDS["max_internal_residue_count"],
            op="<=",
        ),
        "generation_duration_10m": _gate_check(
            value=runtime_eval.get("duration_seconds"),
            threshold=DEFAULT_THRESHOLDS["max_duration_seconds"],
            op="<=",
            skip_when_none=True,
        ),
        "word_export": {
            "value": bool(export_eval.get("word_export_available")),
            "threshold": True,
            "status": "passed" if export_eval.get("word_export_available") else ("skipped" if not export_eval.get("available") else "failed"),
        },
    }
    failed = [name for name, check in checks.items() if check.get("status") == "failed"]
    return {
        "status": "passed" if not failed else "failed",
        "failed_checks": failed,
        "thresholds": DEFAULT_THRESHOLDS,
        "checks": checks,
    }


def _asset_has_source_section(asset: dict[str, Any]) -> bool:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    source_binding = metadata.get("source_binding") if isinstance(metadata.get("source_binding"), dict) else {}
    return bool(source_binding.get("source_section_id") or metadata.get("source_section_id") or asset.get("source_section_id"))


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _gate_check(*, value: Any, threshold: float, op: str, skip_when_none: bool = False) -> dict[str, Any]:
    if value is None:
        return {"value": None, "threshold": threshold, "op": op, "status": "skipped" if skip_when_none else "failed"}
    numeric = float(value)
    passed = numeric >= threshold if op == ">=" else numeric <= threshold
    return {"value": value, "threshold": threshold, "op": op, "status": "passed" if passed else "failed"}


def _collect_evidence_strings(value: Any) -> list[str]:
    strings: list[str] = []
    interesting_keys = {
        "title",
        "section_title",
        "source_title",
        "source_heading",
        "section_path",
        "heading_path",
        "retrieval_subsection_title",
        "document_name",
        "file_name",
        "reason",
        "retrieval_reason",
    }

    def walk(node: Any, *, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child_value in node.items():
                walk(child_value, key=str(child_key))
            return
        if isinstance(node, list):
            for child in node:
                walk(child, key=key)
            return
        if key in interesting_keys and isinstance(node, (str, int, float)):
            text = str(node).strip()
            if text:
                strings.append(text)

    walk(value)
    return _dedupe_keep_order(strings)[:300]


def _detect_cross_section_noise(*, section_title: str, text: str) -> list[str]:
    title = str(section_title or "")
    body = str(text or "")
    if not body:
        return []
    pattern = None
    if TECHNICAL_SECTION_RE.search(title):
        pattern = TECHNICAL_NOISE_RE
    elif COMMERCIAL_OR_SERVICE_RE.search(title):
        pattern = COMMERCIAL_NOISE_RE
    if pattern is None:
        return []
    return _regex_hit_samples(pattern, body)


def _regex_hit_samples(pattern: re.Pattern[str], text: str, *, limit: int = 12) -> list[str]:
    hits: list[str] = []
    for match in pattern.finditer(str(text or "")):
        hit = match.group(0).strip()
        if hit and hit not in hits:
            hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _build_baseline_outline_override(
    *,
    project_name: str,
    source_outline: dict[str, Any],
    baseline_headings: list[HeadingItem],
) -> dict[str, Any]:
    top_sections: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in baseline_headings:
        section_id = item.section_id or str(len(top_sections) + 1)
        parts = section_id.split(".")
        top_id = parts[0]
        if top_id not in top_sections:
            top_sections[top_id] = {
                "section_id": top_id,
                "title": item.title if len(parts) == 1 else top_id,
                "purpose": f"按基准方案《{source_outline.get('title') or project_name}》对应章节结构生成。",
                "children": [],
                "expected_evidence_types": ["section"],
                "asset_required": _text_has_asset_intent(item.title),
            }
        if len(parts) > 1:
            top_sections[top_id]["children"].append(
                {
                    "section_id": section_id,
                    "title": item.title,
                    "purpose": f"覆盖基准章节：{item.title}",
                    "children": [],
                    "expected_evidence_types": ["section", "figure"] if _text_has_asset_intent(item.title) else ["section"],
                    "asset_required": _text_has_asset_intent(item.title),
                }
            )
    payload = {
        "title": source_outline.get("title") or f"{project_name}技术方案",
        "sections": list(top_sections.values()),
        "outline_status": "candidate",
        "generation_strategy": "reuse_first",
        "holdout_eval_override": True,
    }
    return normalize_outline_payload(payload, project_name=project_name)


def _title_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    left_chars = set(_char_ngrams(left))
    right_chars = set(_char_ngrams(right))
    char_score = 0.0
    if left_chars and right_chars:
        char_score = len(left_chars & right_chars) / len(left_chars | right_chars)
    return max(token_score, char_score)


def _tokens(value: str) -> list[str]:
    normalized = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", str(value or "").casefold())
    return TOKEN_RE.findall(normalized)


def _char_ngrams(value: str) -> list[str]:
    normalized = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", str(value or "").casefold())
    normalized = re.sub(r"[\s:：,，.。()（）\-_/]+", "", normalized)
    chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff" or char.isalnum()]
    if len(chars) < 2:
        return chars
    return ["".join(chars[index : index + 2]) for index in range(len(chars) - 1)]


def _text_has_asset_intent(value: str) -> bool:
    return bool(re.search(r"(图|示意|单线|接线|原理|拓扑|曲线|波形|表|参数|清单|配置|规格|数据)", str(value or "")))


def _render_markdown(*, payload: dict[str, Any], baseline_headings: list[HeadingItem]) -> str:
    outline_eval = payload["outline_eval"]
    draft_eval = payload["draft_eval"]
    evidence_eval = payload["evidence_eval"]
    writing_eval = payload["writing_eval"]
    runtime_eval = payload["runtime_eval"]
    export_eval = payload["export_eval"]
    phase8_gate = payload["phase8_gate"]
    lines = [
        "# Holdout Outline / Draft Evaluation",
        "",
        f"- Project: {payload['project_name']} (`{payload['project_id']}`)",
        f"- Baseline: {payload['baseline_document']}",
        f"- Phase 8 gate: `{phase8_gate['status']}`",
        f"- Outline coverage: {outline_eval['coverage']}",
        f"- Weak or missing baseline headings: {outline_eval['weak_or_missing_count']}",
        f"- Draft sections: {draft_eval['section_count']}",
        f"- Draft sections with asset placeholders: {draft_eval['sections_with_placeholders']}",
        f"- Technical evidence accuracy: {evidence_eval['technical_evidence_accuracy']}",
        f"- Evidence pollution hits: {evidence_eval['evidence_pollution_count']}",
        f"- Body pollution hits: {evidence_eval['body_pollution_count']}",
        f"- Internal residue hits: {writing_eval['internal_residue_count']}",
        f"- Filler ratio: {writing_eval['filler_ratio']}",
        f"- Figure-required sections: {payload['asset_stability_eval']['figure_required_sections']}",
        f"- Figure Top-3 source-bound rate: {payload['asset_stability_eval']['top3_source_bound_rate']}",
        f"- Wrong figure body rate: {payload['asset_stability_eval']['wrong_figure_body_rate']}",
        f"- Generation duration seconds: {runtime_eval['duration_seconds']}",
        f"- Word export available: {export_eval['word_export_available']}",
        f"- Baseline outline override: `{payload['outline_override_path']}`",
        "",
        "## Gate Checks",
        "",
        "| Check | Status | Value | Threshold |",
        "| --- | --- | ---: | ---: |",
    ]
    for name, check in phase8_gate["checks"].items():
        lines.append(
            f"| `{name}` | `{check.get('status')}` | {check.get('value')} | {check.get('threshold')} |"
        )
    lines.extend(
        [
            "",
        "## Baseline Headings",
        "",
        ]
    )
    lines.extend(f"- {item.title}" for item in baseline_headings[:80])
    lines.extend(["", "## Weak / Missing Outline Matches", ""])
    for item in outline_eval["weak_or_missing"][:20]:
        baseline = item["baseline"]
        best = item["best_generated"]
        lines.append(f"- {baseline['title']} -> {best['title'] or 'NO_MATCH'} ({best['score']:.2f})")
    lines.extend(["", "## Evidence Pollution", ""])
    for item in evidence_eval["polluted_sections"][:20]:
        lines.append(
            f"- {item['section_id']} {item['title']} | body={item['body_pollution_hits']} | evidence={item['evidence_pollution_hits']}"
        )
    lines.extend(["", "## Internal Residue", ""])
    for item in writing_eval["sections_with_internal_residue"][:20]:
        lines.append(f"- {item['section_id']} {item['title']} | hits={item['internal_residue_hits']}")
    lines.extend(["", "## Asset Gaps", ""])
    for item in draft_eval["asset_intent_without_recommendation"][:20]:
        lines.append(f"- {item['section_id']} {item['title']}")
    return "\n".join(lines) + "\n"


def _resolve_repo_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[2]
    if len(path.parts) >= 2 and path.parts[0] == ".." and path.parts[1] == "output":
        return (repo_root / "output" / Path(*path.parts[2:])).resolve()
    return (repo_root / path).resolve()


if __name__ == "__main__":
    asyncio.run(main())
