from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
from typing import Any

RETRIEVAL_SCORE_DELTA_THRESHOLD = 0.05


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", [], {}):
            return None
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        if value in (None, "", [], {}):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_text(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _serialize_issue_codes(review_payload: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for issue in (review_payload.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        code = _normalize_text(issue.get("code"))
        if code and code not in codes:
            codes.append(code)
    return codes


def _average_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _normalize_breakdown(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, item in value.items():
        numeric = _safe_float(item)
        if numeric is not None:
            normalized[str(key)] = numeric
    return normalized


def _breakdown_metric(breakdown: dict[str, float], *keys: str) -> float | None:
    for key in keys:
        numeric = _safe_float(breakdown.get(key))
        if numeric is not None:
            return numeric
    return None


def _summarize_generation_retrieval(generation_details: dict[str, Any]) -> dict[str, Any]:
    selected_blocks = generation_details.get("selected_blocks") if isinstance(generation_details.get("selected_blocks"), list) else []
    selected_sections = generation_details.get("selected_sections") if isinstance(generation_details.get("selected_sections"), list) else []

    block_breakdowns = [
        _normalize_breakdown(item.get("retrieval_score_breakdown"))
        for item in selected_blocks
        if isinstance(item, dict)
    ]
    block_breakdowns = [item for item in block_breakdowns if item]
    section_breakdowns = [
        _normalize_breakdown(item.get("score_breakdown"))
        for item in selected_sections
        if isinstance(item, dict)
    ]
    section_breakdowns = [item for item in section_breakdowns if item]

    trace_source = "blocks" if block_breakdowns else "sections" if section_breakdowns else "none"
    effective_breakdowns = block_breakdowns or section_breakdowns

    return {
        "selected_block_count": len(selected_blocks),
        "selected_section_count": len(selected_sections),
        "retrieval_trace_source": trace_source,
        "retrieval_trace_count": len(effective_breakdowns),
        "retrieval_avg_final_score": _average_or_none(
            [float(item) for item in (_breakdown_metric(breakdown, "final", "hybrid") for breakdown in effective_breakdowns) if item is not None]
        ),
        "retrieval_avg_semantic_score": _average_or_none(
            [float(item) for item in (_breakdown_metric(breakdown, "semantic", "dense") for breakdown in effective_breakdowns) if item is not None]
        ),
        "retrieval_avg_rrf_score": _average_or_none(
            [float(item.get("hybrid_rrf") or 0.0) for item in effective_breakdowns if "hybrid_rrf" in item]
        ),
        "retrieval_avg_rerank_score": _average_or_none(
            [float(item.get("rerank") or 0.0) for item in effective_breakdowns if "rerank" in item]
        ),
    }


def _format_retrieval_matrix_cell(section_snapshot: dict[str, Any]) -> str:
    block_count = _safe_int(section_snapshot.get("selected_block_count"))
    final_score = section_snapshot.get("retrieval_avg_final_score")
    semantic_score = section_snapshot.get("retrieval_avg_semantic_score")
    rerank_score = section_snapshot.get("retrieval_avg_rerank_score")
    if block_count <= 0 and all(value is None for value in (final_score, semantic_score, rerank_score)):
        return "-"
    return (
        f"{block_count}b/"
        f"F{final_score if final_score is not None else '-'} "
        f"S{semantic_score if semantic_score is not None else '-'} "
        f"R{rerank_score if rerank_score is not None else '-'}"
    )


def _format_retrieval_delta_cell(change: dict[str, Any]) -> str:
    final_delta = _safe_float(change.get("retrieval_final_score_delta"))
    semantic_delta = _safe_float(change.get("retrieval_semantic_score_delta"))
    rerank_delta = _safe_float(change.get("retrieval_rerank_score_delta"))
    if final_delta is None and semantic_delta is None and rerank_delta is None:
        return "-"
    return (
        f"F{final_delta if final_delta is not None else '-'} "
        f"S{semantic_delta if semantic_delta is not None else '-'} "
        f"R{rerank_delta if rerank_delta is not None else '-'}"
    )


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    target = max(0.0, min(1.0, q))
    position = (len(ordered) - 1) * target
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _extract_snapshot_retrieval_metrics(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    sections = snapshot.get("sections") if isinstance(snapshot.get("sections"), list) else []
    retrieval_mode_counts: Counter[str] = Counter()
    trace_sections = _safe_int(summary.get("sections_with_retrieval_trace"))
    selected_blocks = _safe_int(summary.get("total_selected_blocks"))
    final_score = _safe_float(summary.get("average_retrieval_final_score"))
    semantic_score = _safe_float(summary.get("average_retrieval_semantic_score"))
    rerank_score = _safe_float(summary.get("average_retrieval_rerank_score"))

    for item in sections:
        if not isinstance(item, dict):
            continue
        retrieval_mode = _normalize_text(item.get("retrieval_mode"))
        if retrieval_mode:
            retrieval_mode_counts[retrieval_mode] += 1

    if trace_sections <= 0:
        trace_sections = sum(1 for item in sections if isinstance(item, dict) and _safe_int(item.get("retrieval_trace_count")) > 0)
    if selected_blocks <= 0:
        selected_blocks = sum(_safe_int(item.get("selected_block_count")) for item in sections if isinstance(item, dict))
    if final_score is None:
        final_values = [_safe_float(item.get("retrieval_avg_final_score")) for item in sections if isinstance(item, dict)]
        final_score = _average_or_none([float(item) for item in final_values if item is not None])
    if semantic_score is None:
        semantic_values = [_safe_float(item.get("retrieval_avg_semantic_score")) for item in sections if isinstance(item, dict)]
        semantic_score = _average_or_none([float(item) for item in semantic_values if item is not None])
    if rerank_score is None:
        rerank_values = [_safe_float(item.get("retrieval_avg_rerank_score")) for item in sections if isinstance(item, dict)]
        rerank_score = _average_or_none([float(item) for item in rerank_values if item is not None])

    return {
        "project_id": _normalize_text((snapshot.get("project") or {}).get("id")),
        "generated_at": _normalize_text(snapshot.get("generated_at")),
        "health": _normalize_text(summary.get("health"), default="unknown"),
        "total_sections": _safe_int(summary.get("total_sections")),
        "trace_sections": trace_sections,
        "selected_blocks": selected_blocks,
        "average_retrieval_final_score": final_score,
        "average_retrieval_semantic_score": semantic_score,
        "average_retrieval_rerank_score": rerank_score,
        "retrieval_mode_counts": dict(sorted(retrieval_mode_counts.items())),
    }


def build_project_replay_threshold_recommendation(
    *,
    snapshots: list[dict[str, Any]],
    min_samples: int = 3,
) -> dict[str, Any]:
    metrics = [_extract_snapshot_retrieval_metrics(snapshot) for snapshot in snapshots if isinstance(snapshot, dict)]
    eligible = [
        item for item in metrics
        if item.get("health") == "healthy"
    ]
    trace_sections = [float(item["trace_sections"]) for item in eligible if _safe_int(item.get("trace_sections")) > 0]
    final_scores = [float(item["average_retrieval_final_score"]) for item in eligible if isinstance(item.get("average_retrieval_final_score"), float)]
    semantic_scores = [float(item["average_retrieval_semantic_score"]) for item in eligible if isinstance(item.get("average_retrieval_semantic_score"), float)]
    rerank_scores = [float(item["average_retrieval_rerank_score"]) for item in eligible if isinstance(item.get("average_retrieval_rerank_score"), float)]
    mode_counts: Counter[str] = Counter()
    for item in eligible:
        mode_counts.update(item.get("retrieval_mode_counts") or {})
    legacy_healthy_snapshots = [
        item
        for item in eligible
        if item.get("trace_sections", 0) <= 0
        and item.get("average_retrieval_final_score") is None
        and item.get("retrieval_mode_counts")
    ]

    notes: list[str] = []
    recommendations: dict[str, float | int | None] = {
        "min_trace_sections": None,
        "min_avg_retrieval_final": None,
        "min_avg_retrieval_semantic": None,
        "min_avg_retrieval_rerank": None,
    }
    if len(trace_sections) >= min_samples:
        trace_threshold = _quantile(trace_sections, 0.2)
        recommendations["min_trace_sections"] = max(1, int(trace_threshold or 0))
    else:
        notes.append(
            f"insufficient healthy snapshot samples for trace-section threshold: {len(trace_sections)} < {int(min_samples)}"
        )
    if len(final_scores) >= min_samples:
        threshold = _quantile(final_scores, 0.2)
        recommendations["min_avg_retrieval_final"] = round(max(0.0, float(threshold or 0.0) - 0.02), 4)
    else:
        notes.append(
            f"insufficient healthy snapshot samples for retrieval final score threshold: {len(final_scores)} < {int(min_samples)}"
        )
    if len(semantic_scores) >= min_samples:
        threshold = _quantile(semantic_scores, 0.2)
        recommendations["min_avg_retrieval_semantic"] = round(max(0.0, float(threshold or 0.0) - 0.02), 4)
    else:
        notes.append(
            f"insufficient healthy snapshot samples for retrieval semantic score threshold: {len(semantic_scores)} < {int(min_samples)}"
        )
    if len(rerank_scores) >= min_samples:
        threshold = _quantile(rerank_scores, 0.2)
        recommendations["min_avg_retrieval_rerank"] = round(max(0.0, float(threshold or 0.0) - 0.02), 4)
    else:
        notes.append(
            f"insufficient healthy snapshot samples for retrieval rerank score threshold: {len(rerank_scores)} < {int(min_samples)}"
        )
    if not eligible:
        notes.append("no healthy replay snapshots available; threshold recommendation is provisional")
    elif legacy_healthy_snapshots:
        notes.append(
            f"{len(legacy_healthy_snapshots)} healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics"
        )

    return {
        "summary": {
            "total_snapshots": len(metrics),
            "eligible_healthy_snapshots": len(eligible),
            "trace_metric_samples": len(trace_sections),
            "retrieval_final_metric_samples": len(final_scores),
            "retrieval_semantic_metric_samples": len(semantic_scores),
            "retrieval_rerank_metric_samples": len(rerank_scores),
            "retrieval_mode_counts": dict(sorted(mode_counts.items())),
        },
        "recommendations": recommendations,
        "basis": {
            "quantile": 0.2,
            "score_safety_margin": 0.02,
            "min_samples": int(min_samples),
        },
        "notes": notes,
        "snapshots": metrics,
    }


def render_project_replay_threshold_markdown(recommendation: dict[str, Any]) -> str:
    summary = recommendation.get("summary") if isinstance(recommendation.get("summary"), dict) else {}
    recommendations = recommendation.get("recommendations") if isinstance(recommendation.get("recommendations"), dict) else {}
    basis = recommendation.get("basis") if isinstance(recommendation.get("basis"), dict) else {}
    lines = [
        "# Project Replay Threshold Recommendation",
        "",
        "## Summary",
        "",
        f"- Total snapshots: `{_safe_int(summary.get('total_snapshots'))}`",
        f"- Eligible healthy snapshots: `{_safe_int(summary.get('eligible_healthy_snapshots'))}`",
        f"- Trace metric samples: `{_safe_int(summary.get('trace_metric_samples'))}`",
        f"- Retrieval final metric samples: `{_safe_int(summary.get('retrieval_final_metric_samples'))}`",
        f"- Retrieval semantic metric samples: `{_safe_int(summary.get('retrieval_semantic_metric_samples'))}`",
        f"- Retrieval rerank metric samples: `{_safe_int(summary.get('retrieval_rerank_metric_samples'))}`",
        f"- Retrieval mode counts: `{summary.get('retrieval_mode_counts') or {}}`",
        "",
        "## Recommended Flags",
        "",
        f"- `--min-trace-sections`: `{recommendations.get('min_trace_sections')}`",
        f"- `--min-avg-retrieval-final`: `{recommendations.get('min_avg_retrieval_final')}`",
        f"- `--min-avg-retrieval-semantic`: `{recommendations.get('min_avg_retrieval_semantic')}`",
        f"- `--min-avg-retrieval-rerank`: `{recommendations.get('min_avg_retrieval_rerank')}`",
        "",
        "## Basis",
        "",
        f"- Quantile: `{basis.get('quantile')}`",
        f"- Score safety margin: `{basis.get('score_safety_margin')}`",
        f"- Minimum samples: `{basis.get('min_samples')}`",
    ]
    notes = recommendation.get("notes") if isinstance(recommendation.get("notes"), list) else []
    if notes:
        lines.extend(["", "## Notes", ""])
        for item in notes:
            lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def _is_retrieval_regression(change: dict[str, Any]) -> bool:
    trace_before, trace_after = change.get("retrieval_trace_count_change") or (0, 0)
    final_delta = _safe_float(change.get("retrieval_final_score_delta"))
    semantic_delta = _safe_float(change.get("retrieval_semantic_score_delta"))
    rerank_delta = _safe_float(change.get("retrieval_rerank_score_delta"))
    return (
        (trace_before > 0 and trace_after <= 0)
        or (trace_before > trace_after and (final_delta is None or final_delta <= 0))
        or (final_delta is not None and final_delta <= -RETRIEVAL_SCORE_DELTA_THRESHOLD)
        or (
            semantic_delta is not None
            and rerank_delta is not None
            and semantic_delta <= -RETRIEVAL_SCORE_DELTA_THRESHOLD
            and rerank_delta <= -RETRIEVAL_SCORE_DELTA_THRESHOLD
        )
    )


def _is_retrieval_improvement(change: dict[str, Any]) -> bool:
    trace_before, trace_after = change.get("retrieval_trace_count_change") or (0, 0)
    final_delta = _safe_float(change.get("retrieval_final_score_delta"))
    semantic_delta = _safe_float(change.get("retrieval_semantic_score_delta"))
    rerank_delta = _safe_float(change.get("retrieval_rerank_score_delta"))
    return (
        (trace_before <= 0 and trace_after > 0)
        or (trace_after > trace_before and (final_delta is None or final_delta >= 0))
        or (final_delta is not None and final_delta >= RETRIEVAL_SCORE_DELTA_THRESHOLD)
        or (
            semantic_delta is not None
            and rerank_delta is not None
            and semantic_delta >= RETRIEVAL_SCORE_DELTA_THRESHOLD
            and rerank_delta >= RETRIEVAL_SCORE_DELTA_THRESHOLD
        )
    )


def unwrap_project_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None
    return snapshot if snapshot is not None else payload


def build_project_snapshot_history_stem(snapshot: dict[str, Any]) -> str:
    project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
    project_id = re.sub(r"[^A-Za-z0-9._-]+", "-", _normalize_text(project.get("id"), default="unknown-project")).strip("-")
    project_id = project_id or "unknown-project"
    draft_version = _safe_int(project.get("current_draft_version"))
    generated_at = _normalize_text(snapshot.get("generated_at"))
    timestamp = re.sub(r"[^0-9]", "", generated_at)[:14]
    if not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{project_id}-v{draft_version}-{timestamp}"


def build_section_snapshot(section_payload: dict[str, Any]) -> dict[str, Any]:
    validator_result = (
        section_payload.get("validator_result")
        if isinstance(section_payload.get("validator_result"), dict)
        else {}
    )
    quality_gate = (
        validator_result.get("quality_gate")
        if isinstance(validator_result.get("quality_gate"), dict)
        else {}
    )
    final_review = (
        quality_gate.get("final_review")
        if isinstance(quality_gate.get("final_review"), dict)
        else {}
    )
    initial_review = (
        quality_gate.get("initial_review")
        if isinstance(quality_gate.get("initial_review"), dict)
        else {}
    )
    review_payload = final_review or initial_review or quality_gate
    generation_details = (
        validator_result.get("generation_details")
        if isinstance(validator_result.get("generation_details"), dict)
        else {}
    )
    prior_summary = (
        generation_details.get("knowledge_wiki_prior_summary")
        if isinstance(generation_details.get("knowledge_wiki_prior_summary"), dict)
        else {}
    )
    retrieval_summary = _summarize_generation_retrieval(generation_details)
    return {
        "section_id": _normalize_text(section_payload.get("section_id"), default="unknown"),
        "title": _normalize_text(section_payload.get("title"), default="未命名章节"),
        "status": _normalize_text(section_payload.get("status"), default="unknown"),
        "quality_status": _normalize_text(quality_gate.get("status"), default="unknown"),
        "quality_score": _safe_float(quality_gate.get("score")),
        "quality_issue_count": len(review_payload.get("issues") or []),
        "quality_issue_codes": _serialize_issue_codes(review_payload),
        "effective_path": _normalize_text(generation_details.get("effective_path"), default="unknown"),
        "retrieval_mode": _normalize_text(generation_details.get("retrieval_mode"), default="unknown"),
        "refinement_status": _normalize_text(generation_details.get("refinement_status"), default="unknown"),
        "refinement_error": _normalize_text(generation_details.get("refinement_error")),
        "assembled_block_count": _safe_int(generation_details.get("assembled_block_count")),
        "knowledge_wiki_prior_hit_block_count": _safe_int(prior_summary.get("prior_hit_block_count")),
        "knowledge_wiki_prior_total_boost": _safe_float(prior_summary.get("total_prior_boost")) or 0.0,
        "selected_block_count": _safe_int(retrieval_summary.get("selected_block_count")),
        "selected_section_count": _safe_int(retrieval_summary.get("selected_section_count")),
        "retrieval_trace_source": _normalize_text(retrieval_summary.get("retrieval_trace_source"), default="none"),
        "retrieval_trace_count": _safe_int(retrieval_summary.get("retrieval_trace_count")),
        "retrieval_avg_final_score": _safe_float(retrieval_summary.get("retrieval_avg_final_score")),
        "retrieval_avg_semantic_score": _safe_float(retrieval_summary.get("retrieval_avg_semantic_score")),
        "retrieval_avg_rrf_score": _safe_float(retrieval_summary.get("retrieval_avg_rrf_score")),
        "retrieval_avg_rerank_score": _safe_float(retrieval_summary.get("retrieval_avg_rerank_score")),
        "updated_at": _normalize_text(section_payload.get("updated_at")),
    }


def build_project_snapshot(
    *,
    project_payload: dict[str, Any],
    sections_payload: list[dict[str, Any]],
    validation_payload: dict[str, Any] | None = None,
    export_payload: dict[str, Any] | None = None,
    review_tasks_payload: list[dict[str, Any]] | None = None,
    source_label: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    sections = [build_section_snapshot(item) for item in sections_payload]
    scores = [item["quality_score"] for item in sections if isinstance(item.get("quality_score"), float)]
    status_counts = Counter(item["status"] for item in sections)
    quality_counts = Counter(item["quality_status"] for item in sections)
    effective_paths = Counter(item["effective_path"] for item in sections if item.get("effective_path"))
    refinement_statuses = Counter(item["refinement_status"] for item in sections if item.get("refinement_status"))
    fallback_sections = [
        item["section_id"]
        for item in sections
        if str(item.get("effective_path") or "").startswith("extractive_reuse")
        and "deterministic" not in str(item.get("effective_path") or "")
        and str(item.get("refinement_status") or "").startswith("fallback_")
    ]
    prior_sections = [
        item["section_id"]
        for item in sections
        if _safe_int(item.get("knowledge_wiki_prior_hit_block_count")) > 0
    ]
    retrieval_sections = [
        item["section_id"]
        for item in sections
        if _safe_int(item.get("retrieval_trace_count")) > 0
    ]
    review_tasks = list(review_tasks_payload or [])
    open_review_tasks = [
        item for item in review_tasks if _normalize_text(item.get("status")).lower() not in {"resolved", "rejected"}
    ]
    open_blocking_review_tasks = [
        item
        for item in open_review_tasks
        if _normalize_text(item.get("blocking_level")).upper() == "P0"
    ]
    summary = {
        "total_sections": len(sections),
        "status_counts": dict(sorted(status_counts.items())),
        "quality_counts": dict(sorted(quality_counts.items())),
        "effective_paths": dict(sorted(effective_paths.items())),
        "refinement_statuses": dict(sorted(refinement_statuses.items())),
        "average_quality_score": round(sum(scores) / len(scores), 4) if scores else None,
        "min_quality_score": min(scores) if scores else None,
        "max_quality_score": max(scores) if scores else None,
        "non_generated_sections": [item["section_id"] for item in sections if item["status"] != "generated"],
        "non_passed_sections": [item["section_id"] for item in sections if item["quality_status"] != "passed"],
        "fallback_sections": fallback_sections,
        "prior_hit_sections": prior_sections,
        "sections_with_retrieval_trace": len(retrieval_sections),
        "retrieval_trace_sections": retrieval_sections,
        "total_selected_blocks": sum(_safe_int(item.get("selected_block_count")) for item in sections),
        "average_selected_block_count": _average_or_none(
            [float(_safe_int(item.get("selected_block_count"))) for item in sections if _safe_int(item.get("selected_block_count")) > 0]
        ),
        "average_retrieval_final_score": _average_or_none(
            [float(item.get("retrieval_avg_final_score")) for item in sections if isinstance(item.get("retrieval_avg_final_score"), float)]
        ),
        "average_retrieval_semantic_score": _average_or_none(
            [float(item.get("retrieval_avg_semantic_score")) for item in sections if isinstance(item.get("retrieval_avg_semantic_score"), float)]
        ),
        "average_retrieval_rrf_score": _average_or_none(
            [float(item.get("retrieval_avg_rrf_score")) for item in sections if isinstance(item.get("retrieval_avg_rrf_score"), float)]
        ),
        "average_retrieval_rerank_score": _average_or_none(
            [float(item.get("retrieval_avg_rerank_score")) for item in sections if isinstance(item.get("retrieval_avg_rerank_score"), float)]
        ),
        "total_prior_hit_blocks": sum(_safe_int(item.get("knowledge_wiki_prior_hit_block_count")) for item in sections),
        "total_prior_boost": round(
            sum(float(item.get("knowledge_wiki_prior_total_boost") or 0.0) for item in sections),
            4,
        ),
        "open_review_task_count": len(open_review_tasks),
        "open_blocking_review_task_count": len(open_blocking_review_tasks),
        "open_advisory_review_task_count": max(len(open_review_tasks) - len(open_blocking_review_tasks), 0),
        "resolved_review_task_count": max(len(review_tasks) - len(open_review_tasks), 0),
        "health": "healthy"
        if not fallback_sections
        and not open_blocking_review_tasks
        and all(item["status"] == "generated" for item in sections)
        and all(item["quality_status"] == "passed" for item in sections)
        else "attention",
    }
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source": _normalize_text(source_label),
        "project": {
            "id": _normalize_text(project_payload.get("id")),
            "name": _normalize_text(project_payload.get("name"), default="unknown"),
            "status": _normalize_text(project_payload.get("status"), default="unknown"),
            "current_draft_version": _safe_int(project_payload.get("current_draft_version")),
            "industry": _normalize_text(project_payload.get("industry")),
            "product_line": _normalize_text(project_payload.get("product_line")),
        },
        "summary": summary,
        "sections": sections,
        "validation": validation_payload or {},
        "export": export_payload or {},
        "review_tasks": {
            "total_count": len(review_tasks),
            "open_count": len(open_review_tasks),
            "open_blocking_count": len(open_blocking_review_tasks),
        },
    }


def compare_project_snapshots(*, baseline_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> dict[str, Any]:
    baseline_sections = {
        _normalize_text(item.get("section_id"), default="unknown"): item
        for item in (baseline_snapshot.get("sections") or [])
        if isinstance(item, dict)
    }
    current_sections = {
        _normalize_text(item.get("section_id"), default="unknown"): item
        for item in (current_snapshot.get("sections") or [])
        if isinstance(item, dict)
    }
    changed_sections: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    retrieval_regressions: list[dict[str, Any]] = []
    retrieval_improvements: list[dict[str, Any]] = []

    for section_id in sorted(set(baseline_sections) | set(current_sections), key=lambda value: [int(part) if part.isdigit() else part for part in value.split(".")]):
        previous = baseline_sections.get(section_id)
        current = current_sections.get(section_id)
        if previous is None or current is None:
            change = {
                "section_id": section_id,
                "title": _normalize_text((current or previous or {}).get("title"), default="未命名章节"),
                "change_type": "added" if previous is None else "removed",
                "status_change": (_normalize_text((previous or {}).get("status"), default="-"), _normalize_text((current or {}).get("status"), default="-")),
                "quality_status_change": (
                    _normalize_text((previous or {}).get("quality_status"), default="-"),
                    _normalize_text((current or {}).get("quality_status"), default="-"),
                ),
                "quality_score_delta": None,
                "effective_path_change": (
                    _normalize_text((previous or {}).get("effective_path"), default="-"),
                    _normalize_text((current or {}).get("effective_path"), default="-"),
                ),
                "retrieval_trace_count_change": (
                    _safe_int((previous or {}).get("retrieval_trace_count")),
                    _safe_int((current or {}).get("retrieval_trace_count")),
                ),
                "retrieval_final_score_delta": None,
                "retrieval_semantic_score_delta": None,
                "retrieval_rerank_score_delta": None,
            }
            changed_sections.append(change)
            continue

        score_before = _safe_float(previous.get("quality_score"))
        score_after = _safe_float(current.get("quality_score"))
        score_delta = round((score_after or 0.0) - (score_before or 0.0), 4) if score_before is not None and score_after is not None else None
        retrieval_final_before = _safe_float(previous.get("retrieval_avg_final_score"))
        retrieval_final_after = _safe_float(current.get("retrieval_avg_final_score"))
        retrieval_semantic_before = _safe_float(previous.get("retrieval_avg_semantic_score"))
        retrieval_semantic_after = _safe_float(current.get("retrieval_avg_semantic_score"))
        retrieval_rerank_before = _safe_float(previous.get("retrieval_avg_rerank_score"))
        retrieval_rerank_after = _safe_float(current.get("retrieval_avg_rerank_score"))
        change = {
            "section_id": section_id,
            "title": _normalize_text(current.get("title"), default="未命名章节"),
            "change_type": "changed",
            "status_change": (previous.get("status"), current.get("status")),
            "quality_status_change": (previous.get("quality_status"), current.get("quality_status")),
            "quality_score_delta": score_delta,
            "effective_path_change": (previous.get("effective_path"), current.get("effective_path")),
            "refinement_status_change": (previous.get("refinement_status"), current.get("refinement_status")),
            "retrieval_trace_count_change": (
                _safe_int(previous.get("retrieval_trace_count")),
                _safe_int(current.get("retrieval_trace_count")),
            ),
            "retrieval_final_score_delta": round((retrieval_final_after or 0.0) - (retrieval_final_before or 0.0), 4)
            if retrieval_final_before is not None and retrieval_final_after is not None
            else None,
            "retrieval_semantic_score_delta": round((retrieval_semantic_after or 0.0) - (retrieval_semantic_before or 0.0), 4)
            if retrieval_semantic_before is not None and retrieval_semantic_after is not None
            else None,
            "retrieval_rerank_score_delta": round((retrieval_rerank_after or 0.0) - (retrieval_rerank_before or 0.0), 4)
            if retrieval_rerank_before is not None and retrieval_rerank_after is not None
            else None,
        }
        change["retrieval_regression"] = _is_retrieval_regression(change)
        change["retrieval_improvement"] = _is_retrieval_improvement(change)
        status_changed = change["status_change"][0] != change["status_change"][1]
        quality_changed = change["quality_status_change"][0] != change["quality_status_change"][1]
        path_changed = change["effective_path_change"][0] != change["effective_path_change"][1]
        refinement_changed = change["refinement_status_change"][0] != change["refinement_status_change"][1]
        score_changed = score_delta is not None and abs(score_delta) >= 0.0001
        if status_changed or quality_changed or path_changed or refinement_changed or score_changed:
            changed_sections.append(change)

        is_regression = (
            previous.get("status") == "generated" and current.get("status") != "generated"
        ) or (
            previous.get("quality_status") == "passed" and current.get("quality_status") != "passed"
        ) or (
            score_delta is not None and score_delta <= -0.02
        )
        is_improvement = (
            previous.get("status") != "generated" and current.get("status") == "generated"
        ) or (
            previous.get("quality_status") != "passed" and current.get("quality_status") == "passed"
        ) or (
            score_delta is not None and score_delta >= 0.02
        )
        if is_regression:
            regressions.append(change)
        elif is_improvement:
            improvements.append(change)
        if change["retrieval_regression"]:
            retrieval_regressions.append(change)
        elif change["retrieval_improvement"]:
            retrieval_improvements.append(change)

    baseline_summary = baseline_snapshot.get("summary") or {}
    current_summary = current_snapshot.get("summary") or {}
    avg_before = _safe_float(baseline_summary.get("average_quality_score"))
    avg_after = _safe_float(current_summary.get("average_quality_score"))
    retrieval_final_before = _safe_float(baseline_summary.get("average_retrieval_final_score"))
    retrieval_final_after = _safe_float(current_summary.get("average_retrieval_final_score"))
    retrieval_semantic_before = _safe_float(baseline_summary.get("average_retrieval_semantic_score"))
    retrieval_semantic_after = _safe_float(current_summary.get("average_retrieval_semantic_score"))
    retrieval_rerank_before = _safe_float(baseline_summary.get("average_retrieval_rerank_score"))
    retrieval_rerank_after = _safe_float(current_summary.get("average_retrieval_rerank_score"))
    return {
        "baseline_generated_at": _normalize_text(baseline_snapshot.get("generated_at")),
        "current_generated_at": _normalize_text(current_snapshot.get("generated_at")),
        "summary": {
            "changed_section_count": len(changed_sections),
            "regression_count": len(regressions),
            "improvement_count": len(improvements),
            "retrieval_regression_count": len(retrieval_regressions),
            "retrieval_improvement_count": len(retrieval_improvements),
            "average_quality_score_delta": round((avg_after or 0.0) - (avg_before or 0.0), 4)
            if avg_before is not None and avg_after is not None
            else None,
            "average_retrieval_final_score_delta": round((retrieval_final_after or 0.0) - (retrieval_final_before or 0.0), 4)
            if retrieval_final_before is not None and retrieval_final_after is not None
            else None,
            "average_retrieval_semantic_score_delta": round((retrieval_semantic_after or 0.0) - (retrieval_semantic_before or 0.0), 4)
            if retrieval_semantic_before is not None and retrieval_semantic_after is not None
            else None,
            "average_retrieval_rerank_score_delta": round((retrieval_rerank_after or 0.0) - (retrieval_rerank_before or 0.0), 4)
            if retrieval_rerank_before is not None and retrieval_rerank_after is not None
            else None,
            "status_count_delta": {
                "generated": _safe_int((current_summary.get("status_counts") or {}).get("generated"))
                - _safe_int((baseline_summary.get("status_counts") or {}).get("generated")),
                "passed": _safe_int((current_summary.get("quality_counts") or {}).get("passed"))
                - _safe_int((baseline_summary.get("quality_counts") or {}).get("passed")),
            },
        },
        "changed_sections": changed_sections,
        "regressions": regressions,
        "improvements": improvements,
        "retrieval_regressions": retrieval_regressions,
        "retrieval_improvements": retrieval_improvements,
    }


def collect_project_snapshot_gate_failures(
    *,
    snapshot: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    require_healthy: bool = False,
    require_zero_regressions: bool = False,
    require_zero_retrieval_regressions: bool = False,
    require_no_open_blocking: bool = False,
    min_retrieval_trace_sections: int | None = None,
    min_average_retrieval_final_score: float | None = None,
    min_average_retrieval_semantic_score: float | None = None,
    min_average_retrieval_rerank_score: float | None = None,
) -> list[str]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    failures: list[str] = []

    if require_healthy and _normalize_text(summary.get("health"), default="unknown").lower() != "healthy":
        failures.append(f"project replay snapshot health is {_normalize_text(summary.get('health'), default='unknown')}")

    if require_no_open_blocking and _safe_int(summary.get("open_blocking_review_task_count")) > 0:
        failures.append(
            f"project replay snapshot still has {_safe_int(summary.get('open_blocking_review_task_count'))} open blocking review tasks"
        )

    if min_retrieval_trace_sections is not None:
        trace_sections = _safe_int(summary.get("sections_with_retrieval_trace"))
        if trace_sections < int(min_retrieval_trace_sections):
            failures.append(
                f"project replay snapshot has {trace_sections} sections with structured retrieval trace, below required {int(min_retrieval_trace_sections)}"
            )

    average_retrieval_final_score = _safe_float(summary.get("average_retrieval_final_score"))
    if min_average_retrieval_final_score is not None:
        if average_retrieval_final_score is None or average_retrieval_final_score < float(min_average_retrieval_final_score):
            failures.append(
                f"project replay snapshot average retrieval final score is {average_retrieval_final_score}, below required {round(float(min_average_retrieval_final_score), 4)}"
            )

    average_retrieval_semantic_score = _safe_float(summary.get("average_retrieval_semantic_score"))
    if min_average_retrieval_semantic_score is not None:
        if average_retrieval_semantic_score is None or average_retrieval_semantic_score < float(min_average_retrieval_semantic_score):
            failures.append(
                f"project replay snapshot average retrieval semantic score is {average_retrieval_semantic_score}, below required {round(float(min_average_retrieval_semantic_score), 4)}"
            )

    average_retrieval_rerank_score = _safe_float(summary.get("average_retrieval_rerank_score"))
    if min_average_retrieval_rerank_score is not None:
        if average_retrieval_rerank_score is None or average_retrieval_rerank_score < float(min_average_retrieval_rerank_score):
            failures.append(
                f"project replay snapshot average retrieval rerank score is {average_retrieval_rerank_score}, below required {round(float(min_average_retrieval_rerank_score), 4)}"
            )

    comparison_required = require_zero_regressions or require_zero_retrieval_regressions
    comparison_payload = comparison if isinstance(comparison, dict) else None
    if comparison_required and comparison_payload is None:
        failures.append("project replay snapshot baseline comparison is missing")
    elif require_zero_regressions or require_zero_retrieval_regressions:
        comparison_summary = (
            comparison_payload.get("summary")
            if isinstance(comparison_payload.get("summary"), dict)
            else {}
        )
        if require_zero_regressions:
            regression_count = _safe_int(comparison_summary.get("regression_count"))
            if regression_count > 0:
                failures.append(f"project replay comparison found {regression_count} regressions")
        if require_zero_retrieval_regressions:
            retrieval_regression_count = _safe_int(comparison_summary.get("retrieval_regression_count"))
            if retrieval_regression_count > 0:
                failures.append(f"project replay comparison found {retrieval_regression_count} retrieval regressions")

    return failures


def render_project_snapshot_markdown(*, snapshot: dict[str, Any], comparison: dict[str, Any] | None = None) -> str:
    project = snapshot.get("project") or {}
    summary = snapshot.get("summary") or {}
    lines = [
        "# Project Replay Evaluation",
        "",
        f"- Project: `{_normalize_text(project.get('name'), default='unknown')}`",
        f"- Project ID: `{_normalize_text(project.get('id'), default='unknown')}`",
        f"- Draft Version: `{_safe_int(project.get('current_draft_version'))}`",
        f"- Project Status: `{_normalize_text(project.get('status'), default='unknown')}`",
        f"- Snapshot Generated At (UTC): `{_normalize_text(snapshot.get('generated_at'), default='unknown')}`",
    ]
    if _normalize_text(snapshot.get("source")):
        lines.append(f"- Source: `{_normalize_text(snapshot.get('source'))}`")
    lines.extend(
        [
            "",
            "## Snapshot Summary",
            "",
            f"- Health: `{_normalize_text(summary.get('health'), default='unknown')}`",
            f"- Total sections: `{_safe_int(summary.get('total_sections'))}`",
            f"- Generated sections: `{_safe_int((summary.get('status_counts') or {}).get('generated'))}`",
            f"- Passed sections: `{_safe_int((summary.get('quality_counts') or {}).get('passed'))}`",
            f"- Average quality score: `{summary.get('average_quality_score')}`",
            f"- Sections with structured retrieval trace: `{_safe_int(summary.get('sections_with_retrieval_trace'))}`",
            f"- Total selected blocks: `{_safe_int(summary.get('total_selected_blocks'))}`",
            f"- Average selected blocks per section: `{summary.get('average_selected_block_count')}`",
            f"- Average retrieval final score: `{summary.get('average_retrieval_final_score')}`",
            f"- Average retrieval semantic score: `{summary.get('average_retrieval_semantic_score')}`",
            f"- Average retrieval rerank score: `{summary.get('average_retrieval_rerank_score')}`",
            f"- Open blocking review tasks: `{_safe_int(summary.get('open_blocking_review_task_count'))}`",
            f"- Open advisory review tasks: `{_safe_int(summary.get('open_advisory_review_task_count'))}`",
            f"- Fallback sections: `{', '.join(summary.get('fallback_sections') or []) or 'none'}`",
            f"- AI Wiki prior hit sections: `{', '.join(summary.get('prior_hit_sections') or []) or 'none'}`",
            f"- Retrieval trace sections: `{', '.join(summary.get('retrieval_trace_sections') or []) or 'none'}`",
            "",
            "## Section Matrix",
            "",
            "| Section | Title | Status | Quality | Score | Path | Refinement | Retrieval | Issues |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in snapshot.get("sections") or []:
        if not isinstance(item, dict):
            continue
        issue_codes = ",".join((item.get("quality_issue_codes") or [])[:3]) or "-"
        retrieval_cell = _format_retrieval_matrix_cell(item)
        lines.append(
            f"| {item.get('section_id')} | {item.get('title')} | {item.get('status')} | "
            f"{item.get('quality_status')} | {item.get('quality_score')} | {item.get('effective_path')} | "
            f"{item.get('refinement_status')} | {retrieval_cell} | {issue_codes} |"
        )

    validation_payload = snapshot.get("validation") or {}
    export_payload = snapshot.get("export") or {}
    lines.extend(
        [
            "",
            "## Runtime Artifacts",
            "",
            f"- Validation status: `{_normalize_text(validation_payload.get('status'), default='unknown')}`",
            f"- Latest export status: `{_normalize_text(export_payload.get('status'), default='unknown')}`",
            f"- Latest export file: `{_normalize_text(export_payload.get('file_name')) or 'n/a'}`",
        ]
    )

    if comparison:
        comparison_summary = comparison.get("summary") or {}
        lines.extend(
            [
                "",
                "## Comparison",
                "",
                f"- Baseline snapshot: `{_normalize_text(comparison.get('baseline_path')) or 'n/a'}`",
                f"- Baseline generated at: `{_normalize_text(comparison.get('baseline_generated_at')) or 'unknown'}`",
                f"- Changed sections: `{_safe_int(comparison_summary.get('changed_section_count'))}`",
                f"- Regressions: `{_safe_int(comparison_summary.get('regression_count'))}`",
                f"- Improvements: `{_safe_int(comparison_summary.get('improvement_count'))}`",
                f"- Retrieval regressions: `{_safe_int(comparison_summary.get('retrieval_regression_count'))}`",
                f"- Retrieval improvements: `{_safe_int(comparison_summary.get('retrieval_improvement_count'))}`",
                f"- Average quality score delta: `{comparison_summary.get('average_quality_score_delta')}`",
                f"- Average retrieval final score delta: `{comparison_summary.get('average_retrieval_final_score_delta')}`",
                f"- Average retrieval semantic score delta: `{comparison_summary.get('average_retrieval_semantic_score_delta')}`",
                f"- Average retrieval rerank score delta: `{comparison_summary.get('average_retrieval_rerank_score_delta')}`",
                "",
            ]
        )
        regression_sections = comparison.get("regressions") or []
        if regression_sections:
            lines.extend(["### Regressions", "", "| Section | Title | Status | Quality | Score Delta | Retrieval Delta | Path |", "| --- | --- | --- | --- | --- | --- | --- |"])
            for item in regression_sections:
                lines.append(
                    f"| {item.get('section_id')} | {item.get('title')} | "
                    f"{item.get('status_change')[0]} -> {item.get('status_change')[1]} | "
                    f"{item.get('quality_status_change')[0]} -> {item.get('quality_status_change')[1]} | "
                    f"{item.get('quality_score_delta')} | "
                    f"{_format_retrieval_delta_cell(item)} | "
                    f"{item.get('effective_path_change')[0]} -> {item.get('effective_path_change')[1]} |"
                )
            lines.append("")
        improvement_sections = comparison.get("improvements") or []
        if improvement_sections:
            lines.extend(["### Improvements", "", "| Section | Title | Status | Quality | Score Delta | Retrieval Delta | Path |", "| --- | --- | --- | --- | --- | --- | --- |"])
            for item in improvement_sections:
                lines.append(
                    f"| {item.get('section_id')} | {item.get('title')} | "
                    f"{item.get('status_change')[0]} -> {item.get('status_change')[1]} | "
                    f"{item.get('quality_status_change')[0]} -> {item.get('quality_status_change')[1]} | "
                    f"{item.get('quality_score_delta')} | "
                    f"{_format_retrieval_delta_cell(item)} | "
                    f"{item.get('effective_path_change')[0]} -> {item.get('effective_path_change')[1]} |"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
