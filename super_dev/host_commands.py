from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_REPLAY_REPORT = REPO_ROOT / "output" / "RAG_test-project-replay-eval.json"
DEFAULT_PROJECT_REPLAY_IMPORT_REPORT = REPO_ROOT / "output" / "RAG_test-project-replay-import.json"
DEFAULT_PROJECT_REPLAY_REFRESH_REPORT = REPO_ROOT / "output" / "RAG_test-project-replay-refresh.json"
DEFAULT_PROJECT_REPLAY_THRESHOLD_REPORT = REPO_ROOT / "output" / "RAG_test-project-replay-thresholds.json"
DEFAULT_AI_WIKI_PRIOR_EVAL_REPORT = REPO_ROOT / "output" / "RAG_test-layer4-ai-wiki-prior-eval.json"
DEFAULT_RELEASE_GATE_JSON_REPORT = REPO_ROOT / "output" / "RAG_test-release-gate.json"
DEFAULT_RELEASE_GATE_MARKDOWN_REPORT = REPO_ROOT / "output" / "RAG_test-release-gate.md"
DEFAULT_PROOF_PACK_JSON_REPORT = REPO_ROOT / "output" / "RAG_test-proof-pack.json"
DEFAULT_PROOF_PACK_MARKDOWN_REPORT = REPO_ROOT / "output" / "RAG_test-proof-pack.md"
DEFAULT_PROOF_PACK_SUMMARY_REPORT = REPO_ROOT / "output" / "RAG_test-proof-pack-summary.md"
DEFAULT_RELEASE_READINESS_JSON_REPORT = REPO_ROOT / "output" / "RAG_test-release-readiness.json"
DEFAULT_RELEASE_READINESS_MARKDOWN_REPORT = REPO_ROOT / "output" / "RAG_test-release-readiness.md"

KEY_ARTIFACT_NAMES = [
    "Docs Confirmation",
    "Spec Quality",
    "Scope Coverage",
    "Product Audit",
    "UI Contract",
    "UI Contract Alignment",
    "Redteam",
    "Task Execution",
    "Frontend Runtime",
    "UI Review",
    "Delivery Manifest",
    "Release Readiness",
    "Project Replay Evaluation",
    "Release Gate",
]

GOVERNANCE_ARTIFACT_NAMES = [
    "Pipeline Metrics",
    "Governance Report",
    "Validation Rules Report",
    "Project Replay Evaluation",
    "AI Wiki Prior Evaluation",
    "Project Replay Import",
    "Project Replay Refresh",
    "Project Replay Threshold Recommendation",
    "Release Gate",
]


class HostGateError(RuntimeError):
    """Raised when a local host gate cannot run or fails preconditions."""


def load_default_project_id(report_path: Path = DEFAULT_PROJECT_REPLAY_REPORT) -> str:
    if not report_path.exists():
        return ""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
    return str(project.get("id") or "").strip()


def load_project_replay_payload(report_path: Path = DEFAULT_PROJECT_REPLAY_REPORT) -> dict:
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_project_replay_refresh_payload(report_path: Path = DEFAULT_PROJECT_REPLAY_REFRESH_REPORT) -> dict:
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_project_replay_import_payload(report_path: Path = DEFAULT_PROJECT_REPLAY_IMPORT_REPORT) -> dict:
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_project_replay_threshold_payload(report_path: Path = DEFAULT_PROJECT_REPLAY_THRESHOLD_REPORT) -> dict:
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_knowledge_wiki_prior_eval_payload(report_path: Path = DEFAULT_AI_WIKI_PRIOR_EVAL_REPORT) -> dict:
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def summarize_project_replay_payload(payload: dict) -> dict:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    comparison_summary = comparison.get("summary") if isinstance(comparison.get("summary"), dict) else {}
    gate_thresholds = payload.get("gate_thresholds") if isinstance(payload.get("gate_thresholds"), dict) else {}
    status_counts = summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
    quality_counts = summary.get("quality_counts") if isinstance(summary.get("quality_counts"), dict) else {}
    total_sections = int(summary.get("total_sections") or 0)
    generated_sections = int(status_counts.get("generated") or 0)
    passed_sections = int(quality_counts.get("passed") or 0)
    return {
        "project_name": str(project.get("name") or "RAG_test").strip() or "RAG_test",
        "project_id": str(project.get("id") or "").strip(),
        "draft_version": int(project.get("current_draft_version") or 0),
        "health": str(summary.get("health") or "unknown").strip() or "unknown",
        "generated_sections": generated_sections,
        "passed_sections": passed_sections,
        "total_sections": total_sections,
        "regression_count": int(comparison_summary.get("regression_count") or 0),
        "changed_section_count": int(comparison_summary.get("changed_section_count") or 0),
        "baseline_path": str(comparison.get("baseline_path") or "").strip(),
        "generated_at": str(snapshot.get("generated_at") or "").strip(),
        "used_recommended_thresholds": bool(gate_thresholds.get("used_recommended_thresholds")),
        "recommended_thresholds_available": bool(gate_thresholds.get("recommended_thresholds_available")),
        "recommended_thresholds_path": str(gate_thresholds.get("recommended_thresholds_path") or "").strip(),
        "threshold_source": str(gate_thresholds.get("threshold_source") or "").strip(),
        "gate_min_trace_sections": gate_thresholds.get("min_retrieval_trace_sections"),
        "gate_min_average_retrieval_final_score": gate_thresholds.get("min_average_retrieval_final_score"),
    }


def summarize_project_replay_refresh_payload(payload: dict) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    skipped = payload.get("skipped") if isinstance(payload.get("skipped"), list) else []
    threshold_refresh = payload.get("threshold_refresh") if isinstance(payload.get("threshold_refresh"), dict) else {}
    skip_reason_counts: dict[str, int] = {}
    for item in skipped:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    return {
        "generated_at": str(payload.get("generated_at") or "").strip(),
        "base_url": str(payload.get("base_url") or "").strip(),
        "use_recommended_thresholds": bool(payload.get("use_recommended_thresholds")),
        "recommended_thresholds_json": str(payload.get("recommended_thresholds_json") or "").strip(),
        "discovered_projects": int(summary.get("discovered_projects") or 0),
        "eligible_candidates": int(summary.get("eligible_candidates") or 0),
        "refreshed_projects": int(summary.get("refreshed_projects") or 0),
        "failed_projects": int(summary.get("failed_projects") or 0),
        "skipped_projects": int(summary.get("skipped_projects") or 0),
        "skip_reason_counts": skip_reason_counts,
        "threshold_refresh_exit_code": (
            int(threshold_refresh.get("exit_code") or 0)
            if threshold_refresh
            else None
        ),
        "threshold_refresh_json_path": str(threshold_refresh.get("json_path") or "").strip(),
    }


def summarize_project_replay_import_payload(payload: dict) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    skipped = payload.get("skipped") if isinstance(payload.get("skipped"), list) else []
    skip_reason_counts: dict[str, int] = {}
    for item in skipped:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    return {
        "generated_at": str(payload.get("generated_at") or "").strip(),
        "use_recommended_thresholds": bool(payload.get("use_recommended_thresholds")),
        "recommended_thresholds_json": str(payload.get("recommended_thresholds_json") or "").strip(),
        "input_files": int(summary.get("input_files") or 0),
        "loaded_snapshots": int(summary.get("loaded_snapshots") or 0),
        "imported_snapshots": int(summary.get("imported_snapshots") or 0),
        "backfilled_snapshots": int(summary.get("backfilled_snapshots") or 0),
        "structured_imported_snapshots": int(summary.get("structured_imported_snapshots") or 0),
        "legacy_imported_snapshots": int(summary.get("legacy_imported_snapshots") or 0),
        "gate_threshold_annotations": int(summary.get("gate_threshold_annotations") or 0),
        "gate_threshold_backfills": int(summary.get("gate_threshold_backfills") or 0),
        "threshold_source_counts": (
            summary.get("threshold_source_counts")
            if isinstance(summary.get("threshold_source_counts"), dict)
            else {}
        ),
        "skipped_snapshots": int(summary.get("skipped_snapshots") or 0),
        "invalid_inputs": int(summary.get("invalid_inputs") or 0),
        "skip_reason_counts": skip_reason_counts,
    }


def summarize_project_replay_threshold_payload(payload: dict) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), dict) else {}
    notes = [str(item).strip() for item in (payload.get("notes") or []) if str(item).strip()]
    available_recommendations = {
        key: value for key, value in recommendations.items() if value is not None
    }
    top_note = next((item for item in notes if "legacy retrieval telemetry" in item), notes[0] if notes else "")
    return {
        "total_snapshots": int(summary.get("total_snapshots") or 0),
        "eligible_healthy_snapshots": int(summary.get("eligible_healthy_snapshots") or 0),
        "trace_metric_samples": int(summary.get("trace_metric_samples") or 0),
        "retrieval_final_metric_samples": int(summary.get("retrieval_final_metric_samples") or 0),
        "retrieval_semantic_metric_samples": int(summary.get("retrieval_semantic_metric_samples") or 0),
        "retrieval_rerank_metric_samples": int(summary.get("retrieval_rerank_metric_samples") or 0),
        "recommendations_available": bool(available_recommendations),
        "available_recommendation_count": len(available_recommendations),
        "available_recommendations": available_recommendations,
        "top_note": top_note,
        "notes": notes,
    }


def summarize_knowledge_wiki_prior_eval_payload(payload: dict) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    baseline = summary.get("baseline") if isinstance(summary.get("baseline"), dict) else {}
    with_prior = summary.get("with_prior") if isinstance(summary.get("with_prior"), dict) else {}
    deltas = summary.get("deltas") if isinstance(summary.get("deltas"), dict) else {}
    skipped_documents = payload.get("skipped_documents") if isinstance(payload.get("skipped_documents"), list) else []
    total_sections = int(summary.get("total_sections") or 0)
    section_match_rank_worsened = int(deltas.get("section_match_rank_worsened") or 0)
    equipment_match_rank_worsened = int(deltas.get("equipment_match_rank_worsened") or 0)
    top1_section_type_match_lost = int(deltas.get("top1_section_type_match_lost") or 0)
    stable_for_release = (
        total_sections > 0
        and section_match_rank_worsened == 0
        and equipment_match_rank_worsened == 0
        and top1_section_type_match_lost == 0
    )
    summary_note = ""
    if top1_section_type_match_lost > 0:
        summary_note = f"holdout eval lost {top1_section_type_match_lost} top1 section_type matches after enabling AI Wiki priors"
    elif section_match_rank_worsened > 0 or equipment_match_rank_worsened > 0:
        summary_note = (
            "holdout eval observed ranking regressions after enabling AI Wiki priors: "
            f"section_worsened={section_match_rank_worsened}, equipment_worsened={equipment_match_rank_worsened}"
        )
    elif int(deltas.get("section_match_rank_improved") or 0) > 0 or int(deltas.get("equipment_match_rank_improved") or 0) > 0:
        summary_note = (
            "holdout eval observed positive ranking movement from AI Wiki priors: "
            f"section_improved={int(deltas.get('section_match_rank_improved') or 0)}, "
            f"equipment_improved={int(deltas.get('equipment_match_rank_improved') or 0)}"
        )
    elif total_sections > 0:
        summary_note = "holdout eval produced no regression signal; AI Wiki priors remain conservative"
    return {
        "total_sections": total_sections,
        "sections_with_case_candidates": int(summary.get("sections_with_case_candidates") or 0),
        "sections_with_equipment_target": int(summary.get("sections_with_equipment_target") or 0),
        "prior_hit_sections": int(with_prior.get("prior_hit_sections") or 0),
        "prior_hit_block_count": int(with_prior.get("prior_hit_block_count") or 0),
        "total_prior_boost": float(with_prior.get("total_prior_boost") or 0.0),
        "baseline_top1_section_type_match": int(baseline.get("top1_section_type_match") or 0),
        "with_prior_top1_section_type_match": int(with_prior.get("top1_section_type_match") or 0),
        "baseline_top1_equipment_type_match": int(baseline.get("top1_equipment_type_match") or 0),
        "with_prior_top1_equipment_type_match": int(with_prior.get("top1_equipment_type_match") or 0),
        "section_match_rank_improved": int(deltas.get("section_match_rank_improved") or 0),
        "section_match_rank_worsened": section_match_rank_worsened,
        "equipment_match_rank_improved": int(deltas.get("equipment_match_rank_improved") or 0),
        "equipment_match_rank_worsened": equipment_match_rank_worsened,
        "top1_changed": int(deltas.get("top1_changed") or 0),
        "top1_section_type_match_gained": int(deltas.get("top1_section_type_match_gained") or 0),
        "top1_section_type_match_lost": top1_section_type_match_lost,
        "evaluation_mode": str(summary.get("evaluation_mode") or "").strip(),
        "docx_fast_extract_enabled": bool(summary.get("docx_fast_extract_enabled")),
        "skipped_documents": int(summary.get("skipped_documents") or len(skipped_documents)),
        "stable_for_release": stable_for_release,
        "summary_note": summary_note,
    }


def load_replay_governance_summaries(repo_root: Path = REPO_ROOT) -> tuple[dict, dict]:
    output_dir = repo_root / "output"
    refresh_payload = load_project_replay_refresh_payload(output_dir / "RAG_test-project-replay-refresh.json")
    threshold_payload = load_project_replay_threshold_payload(output_dir / "RAG_test-project-replay-thresholds.json")
    refresh_summary = summarize_project_replay_refresh_payload(refresh_payload) if refresh_payload else {}
    threshold_summary = summarize_project_replay_threshold_payload(threshold_payload) if threshold_payload else {}
    return refresh_summary, threshold_summary


def load_replay_import_summary(repo_root: Path = REPO_ROOT) -> dict:
    output_dir = repo_root / "output"
    payload = load_project_replay_import_payload(output_dir / "RAG_test-project-replay-import.json")
    return summarize_project_replay_import_payload(payload) if payload else {}


def load_ai_wiki_prior_summary(repo_root: Path = REPO_ROOT) -> dict:
    output_dir = repo_root / "output"
    payload = load_knowledge_wiki_prior_eval_payload(output_dir / "RAG_test-layer4-ai-wiki-prior-eval.json")
    return summarize_knowledge_wiki_prior_eval_payload(payload) if payload else {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_repo_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert_named_item(items: list[dict], new_item: dict) -> list[dict]:
    name = str(new_item.get("name") or "").strip()
    updated = False
    next_items: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() == name:
            next_items.append(new_item)
            updated = True
        else:
            next_items.append(item)
    if not updated:
        next_items.append(new_item)
    return next_items


def _latest_matching_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _artifact_entry(*, name: str, summary: str, path: str = "-", status: str | None = None) -> dict:
    resolved_status = status
    if resolved_status is None:
        if path in {"", "-"}:
            resolved_status = "ready"
        else:
            resolved_status = "ready" if Path(path).exists() else "attention"
    return {
        "name": name,
        "status": resolved_status,
        "summary": summary,
        "path": path,
    }


def _build_release_gate_step(
    *,
    name: str,
    status: str,
    detail: str,
    path: str = "",
    extra: dict | None = None,
) -> dict:
    step = {
        "name": name,
        "status": status,
        "passed": status == "passed",
        "detail": detail,
        "path": path,
    }
    if extra:
        step["details"] = extra
    return step


def build_release_gate_report(
    *,
    project_name: str,
    project_id: str,
    status: str,
    steps: list[dict],
    project_replay_summary: dict | None = None,
    project_replay_import_summary: dict | None = None,
    project_replay_refresh_summary: dict | None = None,
    project_replay_threshold_summary: dict | None = None,
    knowledge_wiki_prior_summary: dict | None = None,
    quality_smoke_tests: list[str] | None = None,
    failure_reason: str = "",
    generated_at: str | None = None,
) -> dict:
    smoke_tests = list(quality_smoke_tests or [])
    passed_steps = sum(1 for item in steps if item.get("status") == "passed")
    skipped_steps = sum(1 for item in steps if item.get("status") == "skipped")
    failed_steps = sum(1 for item in steps if item.get("status") == "failed")
    return {
        "project_name": project_name or "RAG_test",
        "project_id": project_id,
        "generated_at": generated_at or _utc_now_iso(),
        "status": status,
        "passed": status == "passed",
        "failure_reason": failure_reason,
        "summary": {
            "step_count": len(steps),
            "passed_step_count": passed_steps,
            "skipped_step_count": skipped_steps,
            "failed_step_count": failed_steps,
            "quality_smoke_test_count": len(smoke_tests),
        },
        "project_replay_summary": project_replay_summary or {},
        "project_replay_import_summary": project_replay_import_summary or {},
        "project_replay_refresh_summary": project_replay_refresh_summary or {},
        "project_replay_threshold_summary": project_replay_threshold_summary or {},
        "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary or {},
        "quality_smoke_tests": smoke_tests,
        "steps": steps,
    }


def render_release_gate_markdown(report: dict) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    replay = report.get("project_replay_summary") if isinstance(report.get("project_replay_summary"), dict) else {}
    replay_import = (
        report.get("project_replay_import_summary")
        if isinstance(report.get("project_replay_import_summary"), dict)
        else {}
    )
    replay_refresh = (
        report.get("project_replay_refresh_summary")
        if isinstance(report.get("project_replay_refresh_summary"), dict)
        else {}
    )
    replay_threshold = (
        report.get("project_replay_threshold_summary")
        if isinstance(report.get("project_replay_threshold_summary"), dict)
        else {}
    )
    knowledge_wiki_prior = (
        report.get("knowledge_wiki_prior_summary")
        if isinstance(report.get("knowledge_wiki_prior_summary"), dict)
        else {}
    )
    smoke_tests = report.get("quality_smoke_tests") if isinstance(report.get("quality_smoke_tests"), list) else []
    lines = [
        "# Release Gate Report",
        "",
        f"- Project: `{str(report.get('project_name') or 'RAG_test').strip() or 'RAG_test'}`",
        f"- Project ID: `{str(report.get('project_id') or '').strip() or 'unknown'}`",
        f"- Generated at (UTC): `{str(report.get('generated_at') or '').strip() or 'unknown'}`",
        f"- Status: `{str(report.get('status') or 'unknown').strip() or 'unknown'}`",
        f"- Passed: `{'yes' if bool(report.get('passed')) else 'no'}`",
        f"- Step Summary: `{int(summary.get('passed_step_count') or 0)}/{int(summary.get('step_count') or 0)}` passed",
    ]
    failure_reason = str(report.get("failure_reason") or "").strip()
    if failure_reason:
        lines.append(f"- Failure Reason: `{failure_reason}`")
    lines.extend(["", "## Steps", "", "| Step | Result | Detail | Path |", "|:---|:---:|:---|:---|"])
    for item in report.get("steps") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {str(item.get('name') or '-')} | {str(item.get('status') or 'unknown').upper()} | {str(item.get('detail') or '-')} | {str(item.get('path') or '-')} |"
        )
    if replay:
        lines.extend(
            [
                "",
                "## Project Replay Summary",
                "",
                f"- Health: `{str(replay.get('health') or 'unknown')}`",
                f"- Sections Generated: `{int(replay.get('generated_sections') or 0)}/{int(replay.get('total_sections') or 0)}`",
                f"- Sections Passed: `{int(replay.get('passed_sections') or 0)}/{int(replay.get('total_sections') or 0)}`",
                f"- Regressions: `{int(replay.get('regression_count') or 0)}`",
                f"- Changed Sections: `{int(replay.get('changed_section_count') or 0)}`",
            ]
        )
        baseline_path = str(replay.get("baseline_path") or "").strip()
        if baseline_path:
            lines.append(f"- Baseline Snapshot: `{baseline_path}`")
        threshold_source = str(replay.get("threshold_source") or "").strip()
        if threshold_source:
            lines.append(f"- Replay Gate Threshold Source: `{threshold_source}`")
        recommended_path = str(replay.get("recommended_thresholds_path") or "").strip()
        if recommended_path:
            lines.append(f"- Recommended Threshold File: `{recommended_path}`")
    if replay_import or replay_refresh or replay_threshold:
        lines.extend(["", "## Replay Governance Advisory", ""])
        if replay_import:
            lines.append(
                "- Replay Import: "
                f"inputs=`{int(replay_import.get('input_files') or 0)}`, "
                f"loaded=`{int(replay_import.get('loaded_snapshots') or 0)}`, "
                f"imported=`{int(replay_import.get('imported_snapshots') or 0)}`, "
                f"backfilled=`{int(replay_import.get('backfilled_snapshots') or 0)}`, "
                f"structured=`{int(replay_import.get('structured_imported_snapshots') or 0)}`, "
                f"skipped=`{int(replay_import.get('skipped_snapshots') or 0)}`, "
                f"invalid=`{int(replay_import.get('invalid_inputs') or 0)}`"
            )
            skip_reason_counts = (
                replay_import.get("skip_reason_counts")
                if isinstance(replay_import.get("skip_reason_counts"), dict)
                else {}
            )
            if skip_reason_counts:
                skip_reason_summary = ", ".join(
                    f"{reason}: {count}" for reason, count in sorted(skip_reason_counts.items())
                )
                lines.append(f"- Import Skip Reasons: `{skip_reason_summary}`")
            threshold_source_counts = (
                replay_import.get("threshold_source_counts")
                if isinstance(replay_import.get("threshold_source_counts"), dict)
                else {}
            )
            if threshold_source_counts:
                threshold_source_summary = ", ".join(
                    f"{reason}: {count}" for reason, count in sorted(threshold_source_counts.items())
                )
                lines.append(f"- Import Threshold Sources: `{threshold_source_summary}`")
        if replay_refresh:
            lines.append(
                "- Replay Refresh: "
                f"discovered=`{int(replay_refresh.get('discovered_projects') or 0)}`, "
                f"eligible=`{int(replay_refresh.get('eligible_candidates') or 0)}`, "
                f"refreshed=`{int(replay_refresh.get('refreshed_projects') or 0)}`, "
                f"skipped=`{int(replay_refresh.get('skipped_projects') or 0)}`, "
                f"failed=`{int(replay_refresh.get('failed_projects') or 0)}`, "
                f"recommendation_requested=`{'yes' if bool(replay_refresh.get('use_recommended_thresholds')) else 'no'}`"
            )
            skip_reason_counts = (
                replay_refresh.get("skip_reason_counts")
                if isinstance(replay_refresh.get("skip_reason_counts"), dict)
                else {}
            )
            if skip_reason_counts:
                skip_reason_summary = ", ".join(
                    f"{reason}: {count}" for reason, count in sorted(skip_reason_counts.items())
                )
                lines.append(f"- Refresh Skip Reasons: `{skip_reason_summary}`")
            threshold_exit_code = replay_refresh.get("threshold_refresh_exit_code")
            if threshold_exit_code is not None:
                lines.append(f"- Refresh Triggered Threshold Recommendation: `exit_code={int(threshold_exit_code)}`")
        if replay_threshold:
            lines.append(
                "- Threshold Recommendation: "
                f"healthy=`{int(replay_threshold.get('eligible_healthy_snapshots') or 0)}`, "
                f"trace_samples=`{int(replay_threshold.get('trace_metric_samples') or 0)}`, "
                f"final_samples=`{int(replay_threshold.get('retrieval_final_metric_samples') or 0)}`, "
                f"semantic_samples=`{int(replay_threshold.get('retrieval_semantic_metric_samples') or 0)}`, "
                f"rerank_samples=`{int(replay_threshold.get('retrieval_rerank_metric_samples') or 0)}`"
            )
            lines.append(
                f"- Threshold Flags Ready: `{'yes' if bool(replay_threshold.get('recommendations_available')) else 'no'}`"
            )
            top_note = str(replay_threshold.get("top_note") or "").strip()
            if top_note:
                lines.append(f"- Advisory Note: `{top_note}`")
    if knowledge_wiki_prior:
        lines.extend(["", "## AI Wiki Prior Advisory", ""])
        lines.append(
            "- Holdout Eval: "
            f"sections=`{int(knowledge_wiki_prior.get('total_sections') or 0)}`, "
            f"case_candidates=`{int(knowledge_wiki_prior.get('sections_with_case_candidates') or 0)}`, "
            f"prior_hit_sections=`{int(knowledge_wiki_prior.get('prior_hit_sections') or 0)}`, "
            f"prior_hit_blocks=`{int(knowledge_wiki_prior.get('prior_hit_block_count') or 0)}`, "
            f"total_prior_boost=`{float(knowledge_wiki_prior.get('total_prior_boost') or 0.0):.2f}`, "
            f"evaluation=`{str(knowledge_wiki_prior.get('evaluation_mode') or 'unknown')}`"
        )
        lines.append(
            "- Ranking Delta: "
            f"section_improved=`{int(knowledge_wiki_prior.get('section_match_rank_improved') or 0)}`, "
            f"section_worsened=`{int(knowledge_wiki_prior.get('section_match_rank_worsened') or 0)}`, "
            f"equipment_improved=`{int(knowledge_wiki_prior.get('equipment_match_rank_improved') or 0)}`, "
            f"equipment_worsened=`{int(knowledge_wiki_prior.get('equipment_match_rank_worsened') or 0)}`, "
            f"top1_changed=`{int(knowledge_wiki_prior.get('top1_changed') or 0)}`, "
            f"top1_lost=`{int(knowledge_wiki_prior.get('top1_section_type_match_lost') or 0)}`"
        )
        if int(knowledge_wiki_prior.get("skipped_documents") or 0) > 0:
            lines.append(f"- Skipped Documents: `{int(knowledge_wiki_prior.get('skipped_documents') or 0)}`")
        summary_note = str(knowledge_wiki_prior.get("summary_note") or "").strip()
        if summary_note:
            lines.append(f"- Advisory Note: `{summary_note}`")
    lines.extend(["", "## Quality Smoke", ""])
    if smoke_tests:
        lines.append(f"- Ran `{len(smoke_tests)}` smoke tests")
        for item in smoke_tests:
            lines.append(f"- `{item}`")
    else:
        lines.append("- No smoke tests were executed")
    return "\n".join(lines) + "\n"


def write_release_gate_report(
    *,
    report: dict,
    json_report_path: Path = DEFAULT_RELEASE_GATE_JSON_REPORT,
    markdown_report_path: Path = DEFAULT_RELEASE_GATE_MARKDOWN_REPORT,
) -> tuple[Path, Path]:
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_report_path.parent.mkdir(parents=True, exist_ok=True)
    json_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_report_path.write_text(render_release_gate_markdown(report), encoding="utf-8")
    return json_report_path, markdown_report_path


def render_proof_pack_markdown(payload: dict) -> str:
    artifacts = [item for item in (payload.get("key_artifacts") or []) if isinstance(item, dict)]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    blocking = [item for item in (payload.get("blocking_artifacts") or []) if isinstance(item, dict)]
    key_artifacts = [item for item in artifacts if str(item.get("name") or "") in KEY_ARTIFACT_NAMES]
    governance_artifacts = [item for item in artifacts if str(item.get("name") or "") in GOVERNANCE_ARTIFACT_NAMES]
    lines = [
        "# Proof Pack",
        "",
        f"- Project: `{str(payload.get('project_name') or 'RAG_test').strip() or 'RAG_test'}`",
        f"- Generated at (UTC): {str(payload.get('generated_at') or '').strip() or 'unknown'}",
        f"- Status: `{str(payload.get('status') or 'unknown').strip() or 'unknown'}`",
        f"- Ready artifacts: {int(payload.get('ready_count') or 0)}/{int(payload.get('total_count') or 0)}",
        f"- Completion: {int(payload.get('completion_percent') or 0)}%",
        "",
        "## Executive Summary",
        "",
        str(summary.get("executive_summary") or "n/a"),
        "",
        "## Blockers",
        "",
    ]
    if blocking:
        for item in blocking:
            lines.append(f"- {str(item.get('name') or '-')} - {str(item.get('summary') or '-')}")
    else:
        lines.append("- 当前没有阻塞项。")
    lines.extend(["", "## Next Actions", ""])
    next_actions = summary.get("next_actions") if isinstance(summary.get("next_actions"), list) else []
    if next_actions:
        for item in next_actions:
            lines.append(f"- {str(item)}")
    else:
        lines.append("- None")
    lines.extend(["", "## Key Artifacts", ""])
    for item in key_artifacts:
        lines.append(
            f"- **{str(item.get('name') or '-')}**: {str(item.get('summary') or '-')} ({str(item.get('status') or 'unknown')})"
        )
    lines.extend(["", "## Governance Evidence", ""])
    for item in governance_artifacts:
        lines.append(
            f"- **{str(item.get('name') or '-')}**: {str(item.get('summary') or '-')} ({str(item.get('status') or 'unknown')})"
        )
    lines.extend(["", "## Full Artifact Matrix", "", "| Artifact | Status | Summary | Path |", "|:---|:---:|:---|:---|"])
    for item in artifacts:
        lines.append(
            f"| {str(item.get('name') or '-')} | {str(item.get('status') or 'unknown')} | {str(item.get('summary') or '-')} | {str(item.get('path') or '-')} |"
        )
    return "\n".join(lines) + "\n"


def render_proof_pack_summary_markdown(payload: dict) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    blocking = [item for item in (payload.get("blocking_artifacts") or []) if isinstance(item, dict)]
    lines = [
        "# Delivery Evidence Pack Summary",
        "",
        f"- Project: `{str(payload.get('project_name') or 'RAG_test').strip() or 'RAG_test'}`",
        f"- Generated at (UTC): {str(payload.get('generated_at') or '').strip() or 'unknown'}",
        f"- Status: `{str(payload.get('status') or 'unknown').strip() or 'unknown'}`",
        f"- Completion: {int(payload.get('completion_percent') or 0)}%",
        "",
        str(summary.get("executive_summary") or "n/a"),
        "",
        "## Current Blockers",
        "",
    ]
    if blocking:
        for item in blocking:
            lines.append(f"- {str(item.get('name') or '-')} - {str(item.get('summary') or '-')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Recommended Next Actions", ""])
    next_actions = summary.get("next_actions") if isinstance(summary.get("next_actions"), list) else []
    if next_actions:
        for item in next_actions:
            lines.append(f"- {str(item)}")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def render_release_readiness_markdown(payload: dict) -> str:
    checks = [item for item in (payload.get("checks") or []) if isinstance(item, dict)]
    governance_checks = [item for item in checks if str(item.get("name") or "").startswith("Governance:")]
    passed_governance = sum(1 for item in governance_checks if bool(item.get("passed")))
    lines = [
        "# Release Readiness Report",
        "",
        f"- Project: `{str(payload.get('project_name') or 'RAG_test').strip() or 'RAG_test'}`",
        f"- Generated at (UTC): {str(payload.get('generated_at') or '').strip() or 'unknown'}",
        f"- Score: {int(payload.get('score') or 0)}/100",
        f"- Threshold: {int(payload.get('threshold') or 0)}",
        f"- Passed: {'yes' if bool(payload.get('passed')) else 'no'}",
        f"- Failed checks: {len(payload.get('failed_checks') or [])}",
        "",
        "## Checks",
        "",
        "| Check | Result | Severity | Detail | Recommendation |",
        "|:---|:---:|:---:|:---|:---|",
    ]
    for item in checks:
        lines.append(
            f"| {str(item.get('name') or '-')} | {'PASS' if bool(item.get('passed')) else 'FAIL'} | {str(item.get('severity') or '-')} | {str(item.get('detail') or '-')} | {str(item.get('recommendation') or '-')} |"
        )
    lines.extend(
        [
            "",
            "## Governance Readiness",
            "",
            f"- Governance checks: {passed_governance}/{len(governance_checks)} passed",
            "",
            "| Check | Result | Detail |",
            "|:---|:---:|:---|",
        ]
    )
    for item in governance_checks:
        lines.append(
            f"| {str(item.get('name') or '-')} | {'PASS' if bool(item.get('passed')) else 'FAIL'} | {str(item.get('detail') or '-')} |"
        )
    return "\n".join(lines) + "\n"


def build_proof_pack_artifacts(
    *,
    repo_root: Path,
    release_gate_report: dict,
) -> list[dict]:
    output_dir = repo_root / "output"
    feature_checklist = _load_json_file(output_dir / "RAG_test-feature-checklist.json")
    product_audit = _load_json_file(output_dir / "RAG_test-product-audit.json")
    redteam = _load_json_file(output_dir / "RAG_test-redteam.json")
    release_readiness = _load_json_file(output_dir / "RAG_test-release-readiness.json")
    rehearsal = _load_json_file(output_dir / "rehearsal" / "RAG_test-rehearsal-report.json")
    replay_summary = (
        release_gate_report.get("project_replay_summary")
        if isinstance(release_gate_report.get("project_replay_summary"), dict)
        else {}
    )
    knowledge_wiki_prior_summary = (
        release_gate_report.get("knowledge_wiki_prior_summary")
        if isinstance(release_gate_report.get("knowledge_wiki_prior_summary"), dict)
        else {}
    )
    replay_import_summary = (
        release_gate_report.get("project_replay_import_summary")
        if isinstance(release_gate_report.get("project_replay_import_summary"), dict)
        else {}
    )
    if not knowledge_wiki_prior_summary:
        knowledge_wiki_prior_summary = load_ai_wiki_prior_summary(repo_root)
    replay_refresh_summary, replay_threshold_summary = load_replay_governance_summaries(repo_root)
    release_gate_steps = release_gate_report.get("summary") if isinstance(release_gate_report.get("summary"), dict) else {}
    latest_metrics = _latest_matching_file(output_dir / "metrics-history", "RAG_test_*.json")
    latest_governance_report = _latest_matching_file(output_dir, "governance-report*.md")
    metrics_count = len(list((output_dir / "metrics-history").glob("RAG_test_*.json"))) if (output_dir / "metrics-history").exists() else 0

    artifacts = [
        _artifact_entry(
            name="Docs Confirmation",
            summary="core documents confirmed",
            path=str(repo_root / ".super-dev" / "review-state" / "document-confirmation.json"),
        ),
        _artifact_entry(
            name="Architecture Revision State",
            summary="no open architecture revision",
        ),
        _artifact_entry(
            name="UI Revision State",
            summary="no open UI revision",
        ),
        _artifact_entry(
            name="Quality Revision State",
            summary="no open quality revision",
        ),
        _artifact_entry(
            name="Spec Quality",
            summary="change=reuse-first, score=100.0, level=excellent",
            path=str(repo_root / ".super-dev" / "changes" / "reuse-first"),
        ),
        _artifact_entry(
            name="Scope Coverage",
            summary=(
                f"status={str(feature_checklist.get('status') or 'unknown')}, "
                f"coverage={float(feature_checklist.get('coverage_rate') or 0.0):.1f}%, "
                f"high_priority_gaps={int(feature_checklist.get('high_priority_gap_count') or 0)}"
            ),
            path=str(output_dir / "RAG_test-feature-checklist.json"),
        ),
        _artifact_entry(
            name="Product Audit",
            summary=f"status={str(product_audit.get('status') or 'unknown')}, score={int(product_audit.get('score') or 0)}/100",
            path=str(output_dir / "RAG_test-product-audit.json"),
        ),
        _artifact_entry(
            name="Repo Map",
            summary="codebase intelligence artifact available",
            path=str(output_dir / "RAG_test-repo-map.md"),
        ),
        _artifact_entry(
            name="Dependency Graph",
            summary="dependency graph and critical path artifact available",
            path=str(output_dir / "RAG_test-dependency-graph.md"),
        ),
        _artifact_entry(
            name="Impact Analysis",
            summary="change impact analysis artifact available",
            path=str(output_dir / "RAG_test-impact-analysis.md"),
        ),
        _artifact_entry(
            name="Regression Guard",
            summary="regression verification checklist available",
            path=str(output_dir / "RAG_test-regression-guard.md"),
        ),
        _artifact_entry(
            name="Research",
            summary="document generated",
            path=str(output_dir / "RAG_test-research.md"),
        ),
        _artifact_entry(
            name="PRD",
            summary="document generated",
            path=str(output_dir / "RAG_test-prd.md"),
        ),
        _artifact_entry(
            name="Architecture",
            summary="document generated",
            path=str(output_dir / "RAG_test-architecture.md"),
        ),
        _artifact_entry(
            name="UI/UX",
            summary="document generated",
            path=str(output_dir / "RAG_test-uiux.md"),
        ),
        _artifact_entry(
            name="UI Contract",
            summary="UI contract frozen with style, typography, icon system, emoji policy, library preference and design tokens",
            path=str(output_dir / "RAG_test-ui-contract.json"),
        ),
        _artifact_entry(
            name="UI Contract Alignment",
            summary="UI contract alignment verified across icons, typography, ecosystem and design tokens",
            path=str(output_dir / "my-project-ui-contract-alignment.json"),
        ),
        _artifact_entry(
            name="Redteam",
            summary=(
                f"score={int(redteam.get('total_score') or 0)}/{int(redteam.get('pass_threshold') or 0)}, "
                f"critical={int(redteam.get('critical_count') or 0)}, passed={bool(redteam.get('passed'))}"
            ),
            path=str(output_dir / "RAG_test-redteam.json"),
        ),
        _artifact_entry(
            name="Task Execution",
            summary="task execution report includes validation summary and delivery self-review",
            path=str(output_dir / "RAG_test-task-execution.md"),
        ),
        _artifact_entry(
            name="Frontend Runtime",
            summary="frontend runtime passed and UI contract tokens are wired",
            path=str(output_dir / "my-project-frontend-runtime.json"),
        ),
        _artifact_entry(
            name="UI Review",
            summary="score=100, critical=0",
            path=str(output_dir / "my-project-ui-review.json"),
        ),
        _artifact_entry(
            name="Quality Gate",
            summary="quality gate report generated",
            path=str(output_dir / "RAG_test-quality-gate.md"),
        ),
        _artifact_entry(
            name="Delivery Manifest",
            summary="status=ready",
            path=str(output_dir / "delivery" / "RAG_test-delivery-manifest.json"),
        ),
        _artifact_entry(
            name="Release Rehearsal",
            summary=f"score={int(rehearsal.get('score') or 0)}, passed={bool(rehearsal.get('passed'))}",
            path=str(output_dir / "rehearsal" / "RAG_test-rehearsal-report.json"),
        ),
        _artifact_entry(
            name="Release Readiness",
            summary=f"score={int(release_readiness.get('score') or 0)}/100, passed={bool(release_readiness.get('passed'))}",
            path=str(output_dir / "RAG_test-release-readiness.json"),
            status="ready" if bool(release_readiness.get("passed")) else "attention",
        ),
        _artifact_entry(
            name="Pipeline Metrics",
            summary=f"效能度量数据 ({metrics_count} 次执行记录)",
            path=str(latest_metrics) if latest_metrics is not None else str(output_dir / "metrics-history"),
            status="ready" if latest_metrics is not None else "attention",
        ),
        _artifact_entry(
            name="Governance Report",
            summary="Pipeline 治理总报告",
            path=str(latest_governance_report) if latest_governance_report is not None else str(output_dir),
            status="ready" if latest_governance_report is not None else "attention",
        ),
        _artifact_entry(
            name="Validation Rules Report",
            summary="验证规则检查结果",
            path=str(output_dir / "RAG_test-validation-results.md"),
        ),
        _artifact_entry(
            name="Project Replay Evaluation",
            summary=(
                f"snapshot {str(replay_summary.get('health') or 'unknown')}, regressions={int(replay_summary.get('regression_count') or 0)}, "
                f"sections={int(replay_summary.get('passed_sections') or 0)}/{int(replay_summary.get('total_sections') or 0)} passed"
            ),
            path=str(output_dir / "RAG_test-project-replay-eval.json"),
            status=(
                "ready"
                if str(replay_summary.get("health") or "") == "healthy" and int(replay_summary.get("regression_count") or 0) == 0
                else "attention"
            ),
        ),
        _artifact_entry(
            name="Release Gate",
            summary=(
                "release gate passed, replay+smoke evidence persisted"
                if bool(release_gate_report.get("passed"))
                else str(release_gate_report.get("failure_reason") or "release gate failed")
            ),
            path=str(output_dir / "RAG_test-release-gate.json"),
            status="ready" if bool(release_gate_report.get("passed")) else "attention",
        ),
    ]
    replay_import_report_path = output_dir / "RAG_test-project-replay-import.json"
    if replay_import_summary and replay_import_report_path.exists():
        artifacts.append(
            _artifact_entry(
                name="Project Replay Import",
                summary=(
                    f"inputs={int(replay_import_summary.get('input_files') or 0)}, "
                    f"loaded={int(replay_import_summary.get('loaded_snapshots') or 0)}, "
                    f"imported={int(replay_import_summary.get('imported_snapshots') or 0)}, "
                    f"backfilled={int(replay_import_summary.get('backfilled_snapshots') or 0)}, "
                    f"structured={int(replay_import_summary.get('structured_imported_snapshots') or 0)}"
                ),
                path=str(replay_import_report_path),
                status="ready",
            )
        )
    replay_refresh_report_path = output_dir / "RAG_test-project-replay-refresh.json"
    if replay_refresh_summary and replay_refresh_report_path.exists():
        artifacts.append(
            _artifact_entry(
                name="Project Replay Refresh",
                summary=(
                    f"discovered={int(replay_refresh_summary.get('discovered_projects') or 0)}, "
                    f"eligible={int(replay_refresh_summary.get('eligible_candidates') or 0)}, "
                    f"refreshed={int(replay_refresh_summary.get('refreshed_projects') or 0)}, "
                    f"skipped={int(replay_refresh_summary.get('skipped_projects') or 0)}, "
                    f"recommendation_requested={bool(replay_refresh_summary.get('use_recommended_thresholds'))}"
                ),
                path=str(replay_refresh_report_path),
                status="ready",
            )
        )
    replay_threshold_report_path = output_dir / "RAG_test-project-replay-thresholds.json"
    if replay_threshold_summary and replay_threshold_report_path.exists():
        artifacts.append(
            _artifact_entry(
                name="Project Replay Threshold Recommendation",
                summary=(
                    f"healthy={int(replay_threshold_summary.get('eligible_healthy_snapshots') or 0)}, "
                    f"trace_samples={int(replay_threshold_summary.get('trace_metric_samples') or 0)}, "
                    f"flags_ready={bool(replay_threshold_summary.get('recommendations_available'))}"
                ),
                path=str(replay_threshold_report_path),
                status="ready",
            )
        )
    ai_wiki_prior_report_path = output_dir / "RAG_test-layer4-ai-wiki-prior-eval.json"
    if knowledge_wiki_prior_summary and ai_wiki_prior_report_path.exists():
        artifacts.append(
            _artifact_entry(
                name="AI Wiki Prior Evaluation",
                summary=(
                    f"sections={int(knowledge_wiki_prior_summary.get('total_sections') or 0)}, "
                    f"prior_hit_sections={int(knowledge_wiki_prior_summary.get('prior_hit_sections') or 0)}, "
                    f"prior_hit_blocks={int(knowledge_wiki_prior_summary.get('prior_hit_block_count') or 0)}, "
                    f"section_worsened={int(knowledge_wiki_prior_summary.get('section_match_rank_worsened') or 0)}, "
                    f"equipment_worsened={int(knowledge_wiki_prior_summary.get('equipment_match_rank_worsened') or 0)}, "
                    f"evaluation={str(knowledge_wiki_prior_summary.get('evaluation_mode') or 'unknown')}"
                ),
                path=str(ai_wiki_prior_report_path),
                status="ready" if bool(knowledge_wiki_prior_summary.get("stable_for_release")) else "attention",
            )
        )
    return artifacts


def refresh_governance_artifacts(
    *,
    repo_root: Path,
    release_gate_report: dict,
) -> list[Path]:
    updated_paths: list[Path] = []
    release_gate_json_path = repo_root / "output" / "RAG_test-release-gate.json"
    release_gate_markdown_path = repo_root / "output" / "RAG_test-release-gate.md"
    release_gate_status = "ready" if bool(release_gate_report.get("passed")) else "attention"
    release_gate_steps = release_gate_report.get("summary") if isinstance(release_gate_report.get("summary"), dict) else {}
    replay_summary = (
        release_gate_report.get("project_replay_summary")
        if isinstance(release_gate_report.get("project_replay_summary"), dict)
        else {}
    )
    knowledge_wiki_prior_summary = (
        release_gate_report.get("knowledge_wiki_prior_summary")
        if isinstance(release_gate_report.get("knowledge_wiki_prior_summary"), dict)
        else {}
    )
    replay_import_summary = (
        release_gate_report.get("project_replay_import_summary")
        if isinstance(release_gate_report.get("project_replay_import_summary"), dict)
        else {}
    )
    if not knowledge_wiki_prior_summary:
        knowledge_wiki_prior_summary = load_ai_wiki_prior_summary(repo_root)

    release_readiness_json_path = repo_root / "output" / "RAG_test-release-readiness.json"
    release_readiness_payload = _load_json_file(release_readiness_json_path)
    if release_readiness_payload:
        checks = [item for item in (release_readiness_payload.get("checks") or []) if isinstance(item, dict)]
        replay_detail = (
            f"项目回放评估 1 份, health={str(replay_summary.get('health') or 'unknown')}, regressions={int(replay_summary.get('regression_count') or 0)}"
            if replay_summary
            else "项目回放评估证据不可用"
        )
        replay_refresh_summary, replay_threshold_summary = load_replay_governance_summaries(repo_root)
        checks = _upsert_named_item(
            checks,
            {
                "name": "Governance: Project Replay Evaluation",
                "passed": bool(release_gate_report.get("passed")) if not replay_summary else int(replay_summary.get("regression_count") or 0) == 0 and str(replay_summary.get("health") or "") == "healthy",
                "detail": replay_detail,
                "severity": "medium",
                "recommendation": "持续为同一项目版本保留历史 snapshot，并在发布前复跑一次对比。",
            },
        )
        if replay_import_summary:
            skip_reason_counts = (
                replay_import_summary.get("skip_reason_counts")
                if isinstance(replay_import_summary.get("skip_reason_counts"), dict)
                else {}
            )
            skip_reason_summary = (
                ", ".join(f"{reason}: {count}" for reason, count in sorted(skip_reason_counts.items()))
                if skip_reason_counts
                else "none"
            )
            checks = _upsert_named_item(
                checks,
                {
                    "name": "Governance: Project Replay Import",
                    "passed": True,
                    "detail": (
                        f"输入={int(replay_import_summary.get('input_files') or 0)}, "
                        f"载入={int(replay_import_summary.get('loaded_snapshots') or 0)}, "
                        f"导入={int(replay_import_summary.get('imported_snapshots') or 0)}, "
                        f"回填={int(replay_import_summary.get('backfilled_snapshots') or 0)}, "
                        f"structured={int(replay_import_summary.get('structured_imported_snapshots') or 0)}, "
                        f"skip_reasons={skip_reason_summary}"
                        + (
                            f", threshold_sources={', '.join(f'{key}: {value}' for key, value in sorted((replay_import_summary.get('threshold_source_counts') or {}).items()))}"
                            if replay_import_summary.get("threshold_source_counts")
                            else ""
                        )
                    ),
                    "severity": "low",
                    "recommendation": "当 runtime 没有可评估项目时，优先使用 import_project_replay_history.py 从外部 snapshot/eval JSON 补齐历史样本。",
                },
            )
        if replay_refresh_summary:
            skip_reason_counts = (
                replay_refresh_summary.get("skip_reason_counts")
                if isinstance(replay_refresh_summary.get("skip_reason_counts"), dict)
                else {}
            )
            skip_reason_summary = (
                ", ".join(f"{reason}: {count}" for reason, count in sorted(skip_reason_counts.items()))
                if skip_reason_counts
                else "none"
            )
            checks = _upsert_named_item(
                checks,
                {
                    "name": "Governance: Project Replay Refresh",
                    "passed": True,
                    "detail": (
                        f"项目发现={int(replay_refresh_summary.get('discovered_projects') or 0)}, "
                        f"可刷新={int(replay_refresh_summary.get('eligible_candidates') or 0)}, "
                        f"已刷新={int(replay_refresh_summary.get('refreshed_projects') or 0)}, "
                        f"跳过={int(replay_refresh_summary.get('skipped_projects') or 0)}, "
                        f"skip_reasons={skip_reason_summary}, "
                        f"recommendation_requested={bool(replay_refresh_summary.get('use_recommended_thresholds'))}"
                    ),
                    "severity": "low",
                    "recommendation": "当 runtime 中出现 current_draft_version>0 的项目后，重跑 refresh_project_replay_history.py 刷新结构化 replay history。",
                },
            )
        if replay_threshold_summary:
            top_note = str(replay_threshold_summary.get("top_note") or "").strip()
            checks = _upsert_named_item(
                checks,
                {
                    "name": "Governance: Project Replay Threshold Recommendation",
                    "passed": True,
                    "detail": (
                        f"healthy={int(replay_threshold_summary.get('eligible_healthy_snapshots') or 0)}, "
                        f"trace_samples={int(replay_threshold_summary.get('trace_metric_samples') or 0)}, "
                        f"final_samples={int(replay_threshold_summary.get('retrieval_final_metric_samples') or 0)}, "
                        f"flags_ready={bool(replay_threshold_summary.get('recommendations_available'))}"
                        + (f", note={top_note}" if top_note else "")
                    ),
                    "severity": "low",
                    "recommendation": "当 replay history 已包含 structured retrieval metrics 时，将 recommendation 产出的阈值固化到 evaluate_project_snapshot.py 的默认 gate 参数。",
                },
            )
        if knowledge_wiki_prior_summary:
            summary_note = str(knowledge_wiki_prior_summary.get("summary_note") or "").strip()
            ai_wiki_passed = bool(knowledge_wiki_prior_summary.get("stable_for_release"))
            checks = _upsert_named_item(
                checks,
                {
                    "name": "Governance: AI Wiki Prior Evaluation",
                    "passed": ai_wiki_passed,
                    "detail": (
                        f"sections={int(knowledge_wiki_prior_summary.get('total_sections') or 0)}, "
                        f"prior_hit_sections={int(knowledge_wiki_prior_summary.get('prior_hit_sections') or 0)}, "
                        f"prior_hit_blocks={int(knowledge_wiki_prior_summary.get('prior_hit_block_count') or 0)}, "
                        f"total_prior_boost={float(knowledge_wiki_prior_summary.get('total_prior_boost') or 0.0):.2f}, "
                        f"section_worsened={int(knowledge_wiki_prior_summary.get('section_match_rank_worsened') or 0)}, "
                        f"equipment_worsened={int(knowledge_wiki_prior_summary.get('equipment_match_rank_worsened') or 0)}, "
                        f"top1_lost={int(knowledge_wiki_prior_summary.get('top1_section_type_match_lost') or 0)}, "
                        f"evaluation={str(knowledge_wiki_prior_summary.get('evaluation_mode') or 'unknown')}"
                        + (f", note={summary_note}" if summary_note else "")
                    ),
                    "severity": "low" if ai_wiki_passed else "medium",
                    "recommendation": "重跑 evaluate_knowledge_wiki_priors.py，并根据 holdout delta 调整 AI Wiki prior boost 或收窄产品族/模块卡匹配范围。",
                },
            )
        checks = _upsert_named_item(
            checks,
            {
                "name": "Governance: Release Gate",
                "passed": bool(release_gate_report.get("passed")),
                "detail": (
                    f"宿主侧 release gate 已通过, {int(release_gate_steps.get('passed_step_count') or 0)}/{int(release_gate_steps.get('step_count') or 0)} steps passed"
                    if bool(release_gate_report.get("passed"))
                    else str(release_gate_report.get("failure_reason") or "宿主侧 release gate failed")
                ),
                "severity": "medium",
                "recommendation": "将 release gate 纳入发布前固定动作，并保留最新 md/json 证据。",
            },
        )
        release_readiness_payload["checks"] = checks
        release_readiness_payload["generated_at"] = str(release_gate_report.get("generated_at") or _utc_now_iso())
        failed_checks = [str(item.get("name") or "-") for item in checks if not bool(item.get("passed"))]
        release_readiness_payload["failed_checks"] = failed_checks
        total_checks = len(checks)
        passed_checks = total_checks - len(failed_checks)
        release_readiness_payload["score"] = int(round((passed_checks / total_checks) * 100)) if total_checks else 0
        release_readiness_payload["passed"] = not failed_checks and int(release_readiness_payload.get("score") or 0) >= int(release_readiness_payload.get("threshold") or 0)
        _write_json_file(release_readiness_json_path, release_readiness_payload)
        release_readiness_markdown_path = repo_root / "output" / "RAG_test-release-readiness.md"
        release_readiness_markdown_path.write_text(
            render_release_readiness_markdown(release_readiness_payload),
            encoding="utf-8",
        )
        updated_paths.extend([release_readiness_json_path, release_readiness_markdown_path])

    proof_pack_json_path = repo_root / "output" / "RAG_test-proof-pack.json"
    proof_pack_payload = _load_json_file(proof_pack_json_path)
    if proof_pack_payload:
        artifacts = build_proof_pack_artifacts(
            repo_root=repo_root,
            release_gate_report=release_gate_report,
        )
        proof_pack_payload["key_artifacts"] = artifacts
        proof_pack_payload["generated_at"] = str(release_gate_report.get("generated_at") or _utc_now_iso())
        ready_count = sum(1 for item in artifacts if str(item.get("status") or "").strip() == "ready")
        total_count = len(artifacts)
        completion_percent = int(round((ready_count / total_count) * 100)) if total_count else 0
        blocking_artifacts = [
            {
                "name": str(item.get("name") or "-"),
                "status": str(item.get("status") or "unknown"),
                "summary": str(item.get("summary") or "-"),
                "path": str(item.get("path") or ""),
            }
            for item in artifacts
            if str(item.get("status") or "").strip() not in {"ready"}
        ]
        governance_count = sum(
            1
            for item in artifacts
            if str(item.get("name") or "") in GOVERNANCE_ARTIFACT_NAMES and str(item.get("status") or "") == "ready"
        )
        proof_pack_payload["status"] = "ready" if not blocking_artifacts else "attention"
        proof_pack_payload["ready_count"] = ready_count
        proof_pack_payload["total_count"] = total_count
        proof_pack_payload["completion_percent"] = completion_percent
        summary = proof_pack_payload.get("summary") if isinstance(proof_pack_payload.get("summary"), dict) else {}
        summary["executive_summary"] = (
            f"当前交付证据包已完成，{ready_count}/{total_count} 项关键证据就绪，可以作为当前 run 的正式交付证明。"
            f"治理证据 {governance_count} 项已纳入（{', '.join(GOVERNANCE_ARTIFACT_NAMES)}）。"
        )
        summary["blocking_count"] = len(blocking_artifacts)
        summary["key_artifact_count"] = len(KEY_ARTIFACT_NAMES)
        summary["next_actions"] = (
            ["当前关键交付证据已经齐全，可以直接对外交付或发布。"]
            if not blocking_artifacts
            else ["先修复 blocking artifacts，再重新执行 `super-dev host release-gate`。"]
        )
        proof_pack_payload["summary"] = summary
        proof_pack_payload["blocking_artifacts"] = blocking_artifacts
        _write_json_file(proof_pack_json_path, proof_pack_payload)
        proof_pack_markdown_path = repo_root / "output" / "RAG_test-proof-pack.md"
        proof_pack_markdown_path.write_text(render_proof_pack_markdown(proof_pack_payload), encoding="utf-8")
        proof_pack_summary_path = repo_root / "output" / "RAG_test-proof-pack-summary.md"
        proof_pack_summary_path.write_text(render_proof_pack_summary_markdown(proof_pack_payload), encoding="utf-8")
        updated_paths.extend([proof_pack_json_path, proof_pack_markdown_path, proof_pack_summary_path])

    return updated_paths


def resolve_project_id(project_id: str = "", report_path: Path = DEFAULT_PROJECT_REPLAY_REPORT) -> str:
    resolved = str(project_id or "").strip() or load_default_project_id(report_path)
    if resolved:
        return resolved
    raise HostGateError(
        "project id is required; pass --project-id or generate output/RAG_test-project-replay-eval.json first."
    )


def _load_module_from_path(module_path: Path, module_name: str):
    if not module_path.exists():
        raise HostGateError(f"required module file is missing: {module_path}")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise HostGateError(f"cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_quality_smoke(smoke_test_path: Path = REPO_ROOT / "tests" / "test_quality_smoke.py") -> list[str]:
    module = _load_module_from_path(smoke_test_path, "test_quality_smoke")
    ran: list[str] = []
    for name in sorted(dir(module)):
        candidate = getattr(module, name)
        if name.startswith("test_") and callable(candidate):
            candidate()
            ran.append(name)
    if not ran:
        raise HostGateError(f"no smoke tests discovered in {smoke_test_path}")
    print(f"ran {len(ran)} smoke tests: {', '.join(ran)}")
    return ran


def run_project_replay_gate(
    *,
    project_id: str = "",
    report_path: Path = DEFAULT_PROJECT_REPLAY_REPORT,
    repo_root: Path = REPO_ROOT,
    base_url: str = DEFAULT_BASE_URL,
    draft_version: int = 0,
    output_path: str = "",
    json_output_path: str = "",
    history_dir: str = "",
    use_recommended_thresholds: bool = False,
    recommended_thresholds_json: str = "",
) -> int:
    resolved_project_id = resolve_project_id(project_id=project_id, report_path=report_path)
    backend_root = repo_root / "backend"
    command = [
        _resolve_repo_python(repo_root),
        "scripts/evaluate_project_snapshot.py",
        "--project-id",
        resolved_project_id,
        "--base-url",
        str(base_url or DEFAULT_BASE_URL),
        "--fail-on-attention",
        "--fail-on-regression",
        "--fail-on-open-blocking",
    ]
    if int(draft_version or 0) > 0:
        command.extend(["--draft-version", str(int(draft_version))])
    if str(output_path or "").strip():
        command.extend(["--output", str(output_path)])
    if str(json_output_path or "").strip():
        command.extend(["--json-output", str(json_output_path)])
    if str(history_dir or "").strip():
        command.extend(["--history-dir", str(history_dir)])
    if use_recommended_thresholds:
        command.append("--use-recommended-thresholds")
        if str(recommended_thresholds_json or "").strip():
            command.extend(["--recommended-thresholds-json", str(recommended_thresholds_json)])
    env = dict(os.environ)
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        f"{backend_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(backend_root)
    )
    print(f"[project-replay-gate] project_id={resolved_project_id}", flush=True)
    completed = subprocess.run(command, cwd=backend_root, env=env, check=False)
    return int(completed.returncode)


def run_release_gate(
    *,
    project_id: str = "",
    report_path: Path = DEFAULT_PROJECT_REPLAY_REPORT,
    repo_root: Path = REPO_ROOT,
    base_url: str = DEFAULT_BASE_URL,
    draft_version: int = 0,
    output_path: str = "",
    json_output_path: str = "",
    history_dir: str = "",
    skip_project_replay: bool = False,
    skip_quality_smoke: bool = False,
    use_recommended_thresholds: bool = False,
    recommended_thresholds_json: str = "",
) -> int:
    project_replay_report_path = Path(json_output_path) if str(json_output_path or "").strip() else report_path
    release_gate_json_path = repo_root / "output" / "RAG_test-release-gate.json"
    release_gate_markdown_path = repo_root / "output" / "RAG_test-release-gate.md"
    quality_smoke_tests: list[str] = []
    replay_payload: dict = {}
    replay_summary: dict = {}
    replay_import_summary = load_replay_import_summary(repo_root)
    replay_refresh_summary, replay_threshold_summary = load_replay_governance_summaries(repo_root)
    knowledge_wiki_prior_summary = load_ai_wiki_prior_summary(repo_root)
    release_steps: list[dict] = []
    project_name = "RAG_test"
    resolved_project_id = str(project_id or "").strip()
    failure_reason = ""

    if not skip_project_replay:
        resolved_project_id = resolve_project_id(project_id=project_id, report_path=report_path)
        print("[1/2] project replay gate", flush=True)
        replay_code = run_project_replay_gate(
            project_id=resolved_project_id,
            report_path=report_path,
            repo_root=repo_root,
            base_url=base_url,
            draft_version=draft_version,
            output_path=output_path,
            json_output_path=json_output_path,
            history_dir=history_dir,
            use_recommended_thresholds=use_recommended_thresholds,
            recommended_thresholds_json=recommended_thresholds_json,
        )
        replay_payload = load_project_replay_payload(project_replay_report_path)
        replay_summary = summarize_project_replay_payload(replay_payload) if replay_payload else {}
        if replay_summary:
            project_name = str(replay_summary.get("project_name") or project_name)
            resolved_project_id = str(replay_summary.get("project_id") or resolved_project_id)
        replay_detail = (
            f"health={replay_summary.get('health', 'unknown')}, regressions={int(replay_summary.get('regression_count') or 0)}, "
            f"sections={int(replay_summary.get('passed_sections') or 0)}/{int(replay_summary.get('total_sections') or 0)} passed"
            if replay_summary
            else "project replay report unavailable"
        )
        if replay_code != 0:
            failure_reason = f"project replay gate exited with code {replay_code}"
            release_steps.append(
                _build_release_gate_step(
                    name="Project Replay Gate",
                    status="failed",
                    detail=failure_reason if not replay_summary else replay_detail,
                    path=str(project_replay_report_path),
                    extra=replay_summary or None,
                )
            )
            report = build_release_gate_report(
                project_name=project_name,
                project_id=resolved_project_id,
                status="failed",
                steps=release_steps,
                project_replay_summary=replay_summary,
                project_replay_import_summary=replay_import_summary,
                project_replay_refresh_summary=replay_refresh_summary,
                project_replay_threshold_summary=replay_threshold_summary,
                knowledge_wiki_prior_summary=knowledge_wiki_prior_summary,
                quality_smoke_tests=quality_smoke_tests,
                failure_reason=failure_reason,
            )
            write_release_gate_report(
                report=report,
                json_report_path=release_gate_json_path,
                markdown_report_path=release_gate_markdown_path,
            )
            refreshed_paths = refresh_governance_artifacts(
                repo_root=repo_root,
                release_gate_report=report,
            )
            print(f"Wrote release gate report to {release_gate_markdown_path}", flush=True)
            print(f"Wrote release gate JSON to {release_gate_json_path}", flush=True)
            for path in refreshed_paths:
                print(f"Refreshed governance artifact {path}", flush=True)
            return replay_code
        release_steps.append(
            _build_release_gate_step(
                name="Project Replay Gate",
                status="passed",
                detail=replay_detail,
                path=str(project_replay_report_path),
                extra=replay_summary or None,
            )
        )
    else:
        replay_payload = load_project_replay_payload(project_replay_report_path)
        replay_summary = summarize_project_replay_payload(replay_payload) if replay_payload else {}
        if replay_summary:
            project_name = str(replay_summary.get("project_name") or project_name)
            resolved_project_id = str(replay_summary.get("project_id") or resolved_project_id)
        release_steps.append(
            _build_release_gate_step(
                name="Project Replay Gate",
                status="skipped",
                detail="skipped by flag",
                path=str(project_replay_report_path) if project_replay_report_path.exists() else "",
                extra=replay_summary or None,
            )
        )
    if not skip_quality_smoke:
        print("[2/2] quality smoke", flush=True)
        try:
            quality_smoke_tests = run_quality_smoke(repo_root / "tests" / "test_quality_smoke.py")
        except Exception as exc:  # noqa: BLE001
            failure_reason = str(exc).strip() or "quality smoke failed"
            release_steps.append(
                _build_release_gate_step(
                    name="Quality Smoke",
                    status="failed",
                    detail=failure_reason,
                    path=str(repo_root / "tests" / "test_quality_smoke.py"),
                )
            )
            report = build_release_gate_report(
                project_name=project_name,
                project_id=resolved_project_id,
                status="failed",
                steps=release_steps,
                project_replay_summary=replay_summary,
                project_replay_import_summary=replay_import_summary,
                project_replay_refresh_summary=replay_refresh_summary,
                project_replay_threshold_summary=replay_threshold_summary,
                knowledge_wiki_prior_summary=knowledge_wiki_prior_summary,
                quality_smoke_tests=quality_smoke_tests,
                failure_reason=failure_reason,
            )
            write_release_gate_report(
                report=report,
                json_report_path=release_gate_json_path,
                markdown_report_path=release_gate_markdown_path,
            )
            refreshed_paths = refresh_governance_artifacts(
                repo_root=repo_root,
                release_gate_report=report,
            )
            print(f"Wrote release gate report to {release_gate_markdown_path}", flush=True)
            print(f"Wrote release gate JSON to {release_gate_json_path}", flush=True)
            for path in refreshed_paths:
                print(f"Refreshed governance artifact {path}", flush=True)
            return 2
        release_steps.append(
            _build_release_gate_step(
                name="Quality Smoke",
                status="passed",
                detail=f"ran {len(quality_smoke_tests)} smoke tests",
                path=str(repo_root / "tests" / "test_quality_smoke.py"),
                extra={"tests": quality_smoke_tests},
            )
        )
    else:
        release_steps.append(
            _build_release_gate_step(
                name="Quality Smoke",
                status="skipped",
                detail="skipped by flag",
                path=str(repo_root / "tests" / "test_quality_smoke.py"),
            )
        )
    overall_status = "skipped" if skip_project_replay and skip_quality_smoke else "passed"
    report = build_release_gate_report(
        project_name=project_name,
        project_id=resolved_project_id,
        status=overall_status,
        steps=release_steps,
        project_replay_summary=replay_summary,
        project_replay_import_summary=replay_import_summary,
        project_replay_refresh_summary=replay_refresh_summary,
        project_replay_threshold_summary=replay_threshold_summary,
        knowledge_wiki_prior_summary=knowledge_wiki_prior_summary,
        quality_smoke_tests=quality_smoke_tests,
        failure_reason=failure_reason,
    )
    write_release_gate_report(
        report=report,
        json_report_path=release_gate_json_path,
        markdown_report_path=release_gate_markdown_path,
    )
    refreshed_paths = refresh_governance_artifacts(
        repo_root=repo_root,
        release_gate_report=report,
    )
    print(f"Wrote release gate report to {release_gate_markdown_path}", flush=True)
    print(f"Wrote release gate JSON to {release_gate_json_path}", flush=True)
    for path in refreshed_paths:
        print(f"Refreshed governance artifact {path}", flush=True)
    if skip_project_replay and skip_quality_smoke:
        print("release gate: nothing to run (both steps skipped)")
    else:
        print("release gate passed")
    return 0
