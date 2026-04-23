from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request
from typing import Any

from app.services.validation.project_snapshot import (
    build_project_snapshot,
    build_project_snapshot_history_stem,
    collect_project_snapshot_gate_failures,
    compare_project_snapshots,
    render_project_snapshot_markdown,
    unwrap_project_snapshot_payload,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture and compare a project replay snapshot from the runtime API.")
    parser.add_argument("--project-id", required=True, help="Target project UUID.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
        help="Runtime API base URL.",
    )
    parser.add_argument(
        "--draft-version",
        type=int,
        default=0,
        help="Draft version to inspect. Defaults to current project draft when omitted or 0.",
    )
    parser.add_argument(
        "--output",
        default="../output/RAG_test-project-replay-eval.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="Optional JSON output path. Defaults to markdown path with .json suffix.",
    )
    parser.add_argument(
        "--baseline-json",
        default="",
        help="Optional baseline snapshot JSON path for comparison.",
    )
    parser.add_argument(
        "--history-dir",
        default="../output/project-replay-history",
        help="Directory used to persist replay snapshot history and auto-discover the latest baseline.",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Do not persist the current snapshot into the history directory.",
    )
    parser.add_argument(
        "--fail-on-attention",
        action="store_true",
        help="Exit non-zero when the snapshot health is not healthy.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero when the baseline comparison is missing or reports regressions.",
    )
    parser.add_argument(
        "--fail-on-retrieval-regression",
        action="store_true",
        help="Exit non-zero when the baseline comparison reports retrieval regressions.",
    )
    parser.add_argument(
        "--fail-on-open-blocking",
        action="store_true",
        help="Exit non-zero when open blocking review tasks remain.",
    )
    parser.add_argument(
        "--min-trace-sections",
        type=int,
        default=0,
        help="Require at least this many sections to carry structured retrieval trace in the snapshot summary.",
    )
    parser.add_argument(
        "--min-avg-retrieval-final",
        type=float,
        default=0.0,
        help="Require the snapshot average retrieval final score to stay at or above this threshold.",
    )
    parser.add_argument(
        "--min-avg-retrieval-semantic",
        type=float,
        default=0.0,
        help="Require the snapshot average retrieval semantic score to stay at or above this threshold.",
    )
    parser.add_argument(
        "--min-avg-retrieval-rerank",
        type=float,
        default=0.0,
        help="Require the snapshot average retrieval rerank score to stay at or above this threshold.",
    )
    parser.add_argument(
        "--use-recommended-thresholds",
        action="store_true",
        help="Load non-null retrieval thresholds from the recommendation JSON when explicit flags are unset.",
    )
    parser.add_argument(
        "--recommended-thresholds-json",
        default="../output/RAG_test-project-replay-thresholds.json",
        help="Recommendation JSON used by --use-recommended-thresholds.",
    )
    return parser.parse_args()


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _load_snapshot_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return unwrap_project_snapshot_payload(payload)


def load_recommended_gate_thresholds(path: Path) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "source_path": str(path.resolve()) if path.exists() else "",
        "available": False,
        "available_threshold_keys": [],
    }
    if not path.exists():
        return resolved
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return resolved
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), dict) else {}
    trace_sections = recommendations.get("min_trace_sections")
    final_score = recommendations.get("min_avg_retrieval_final")
    semantic_score = recommendations.get("min_avg_retrieval_semantic")
    rerank_score = recommendations.get("min_avg_retrieval_rerank")
    if trace_sections is not None:
        resolved["min_retrieval_trace_sections"] = int(trace_sections)
        resolved["available_threshold_keys"].append("min_retrieval_trace_sections")
    if final_score is not None:
        resolved["min_average_retrieval_final_score"] = float(final_score)
        resolved["available_threshold_keys"].append("min_average_retrieval_final_score")
    if semantic_score is not None:
        resolved["min_average_retrieval_semantic_score"] = float(semantic_score)
        resolved["available_threshold_keys"].append("min_average_retrieval_semantic_score")
    if rerank_score is not None:
        resolved["min_average_retrieval_rerank_score"] = float(rerank_score)
        resolved["available_threshold_keys"].append("min_average_retrieval_rerank_score")
    resolved["available"] = bool(resolved["available_threshold_keys"])
    return resolved


def resolve_gate_thresholds(args: argparse.Namespace) -> dict[str, Any]:
    explicit_thresholds = {
        "min_retrieval_trace_sections": int(args.min_trace_sections) if int(args.min_trace_sections or 0) > 0 else None,
        "min_average_retrieval_final_score": float(args.min_avg_retrieval_final) if float(args.min_avg_retrieval_final or 0) > 0 else None,
        "min_average_retrieval_semantic_score": float(args.min_avg_retrieval_semantic) if float(args.min_avg_retrieval_semantic or 0) > 0 else None,
        "min_average_retrieval_rerank_score": float(args.min_avg_retrieval_rerank) if float(args.min_avg_retrieval_rerank or 0) > 0 else None,
    }
    resolved: dict[str, Any] = {
        **explicit_thresholds,
        "recommendation_requested": bool(args.use_recommended_thresholds),
        "recommended_thresholds_available": False,
        "used_recommended_thresholds": False,
        "recommended_thresholds_path": "",
        "recommended_threshold_keys": [],
        "threshold_source": "explicit" if any(value is not None for value in explicit_thresholds.values()) else "none",
    }
    if not bool(args.use_recommended_thresholds):
        return resolved

    recommended_path = Path(args.recommended_thresholds_json)
    recommended = load_recommended_gate_thresholds(recommended_path)
    resolved["recommended_thresholds_path"] = str(recommended.get("source_path") or "")
    resolved["recommended_thresholds_available"] = bool(recommended.get("available"))
    resolved["recommended_threshold_keys"] = list(recommended.get("available_threshold_keys") or [])
    if not bool(recommended.get("available")):
        if any(value is not None for value in explicit_thresholds.values()):
            resolved["threshold_source"] = "explicit"
        else:
            resolved["threshold_source"] = "recommended_unavailable"
        return resolved

    used_recommended_keys: list[str] = []
    for key in (
        "min_retrieval_trace_sections",
        "min_average_retrieval_final_score",
        "min_average_retrieval_semantic_score",
        "min_average_retrieval_rerank_score",
    ):
        if resolved.get(key) is None and recommended.get(key) is not None:
            resolved[key] = recommended[key]
            used_recommended_keys.append(key)
    resolved["used_recommended_thresholds"] = bool(used_recommended_keys)
    resolved["used_recommended_threshold_keys"] = used_recommended_keys
    if resolved["used_recommended_thresholds"] and any(value is not None for value in explicit_thresholds.values()):
        resolved["threshold_source"] = "mixed"
    elif resolved["used_recommended_thresholds"]:
        resolved["threshold_source"] = "recommended"
    elif any(value is not None for value in explicit_thresholds.values()):
        resolved["threshold_source"] = "explicit"
    else:
        resolved["threshold_source"] = "recommended_available_unused"
    return resolved


def _find_latest_history_snapshot(
    *,
    history_dir: Path,
    project_id: str,
    draft_version: int,
) -> tuple[Path, dict[str, Any]] | None:
    if not history_dir.exists():
        return None
    candidates = sorted(history_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            snapshot = _load_snapshot_file(path)
        except Exception:
            continue
        project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
        if str(project.get("id") or "").strip() != project_id:
            continue
        if int(project.get("current_draft_version") or 0) != int(draft_version):
            continue
        return path, snapshot
    return None


def main() -> int:
    args = parse_args()
    base_url = str(args.base_url or "").rstrip("/")
    project_id = str(args.project_id or "").strip()
    project_response = _fetch_json(f"{base_url}/projects/{project_id}")
    project_payload = project_response["data"]
    draft_version = int(args.draft_version or project_payload.get("current_draft_version") or 0)

    sections_payload = _fetch_json(f"{base_url}/projects/{project_id}/sections?draft_version={draft_version}")["data"]
    validation_payload = _fetch_json(f"{base_url}/projects/{project_id}/validation/latest").get("data") or {}
    export_payload = _fetch_json(f"{base_url}/projects/{project_id}/exports/latest").get("data") or {}
    review_tasks_payload = _fetch_json(f"{base_url}/projects/{project_id}/review-tasks").get("data") or []

    snapshot = build_project_snapshot(
        project_payload=project_payload,
        sections_payload=sections_payload,
        validation_payload=validation_payload,
        export_payload=export_payload,
        review_tasks_payload=review_tasks_payload,
        source_label=base_url,
    )

    comparison = None
    baseline_path: Path | None = None
    baseline_snapshot: dict[str, Any] | None = None
    if str(args.baseline_json or "").strip():
        baseline_path = Path(args.baseline_json)
        baseline_snapshot = _load_snapshot_file(baseline_path)
    else:
        history_dir = Path(args.history_dir)
        latest_history = _find_latest_history_snapshot(
            history_dir=history_dir,
            project_id=project_id,
            draft_version=draft_version,
        )
        if latest_history is not None:
            baseline_path, baseline_snapshot = latest_history
    if baseline_snapshot is not None:
        comparison = compare_project_snapshots(
            baseline_snapshot=baseline_snapshot,
            current_snapshot=snapshot,
        )
        comparison["baseline_path"] = str(baseline_path.resolve()) if baseline_path is not None else ""

    markdown = render_project_snapshot_markdown(snapshot=snapshot, comparison=comparison)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    json_output_path = Path(args.json_output) if str(args.json_output or "").strip() else output_path.with_suffix(".json")
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"snapshot": snapshot}
    if comparison is not None:
        payload["comparison"] = comparison
    gate_thresholds = resolve_gate_thresholds(args)
    payload["gate_thresholds"] = gate_thresholds
    json_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    history_output_path: Path | None = None
    if not bool(args.no_history):
        history_dir = Path(args.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)
        history_stem = build_project_snapshot_history_stem(snapshot)
        history_output_path = history_dir / f"{history_stem}.json"
        history_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown report to {output_path.resolve()}")
    print(f"Wrote JSON report to {json_output_path.resolve()}")
    if history_output_path is not None:
        print(f"Wrote history snapshot to {history_output_path.resolve()}")
    if baseline_path is not None:
        print(f"Compared against baseline {baseline_path.resolve()}")
    print(f"Project status: {snapshot['project']['status']}")
    print(f"Health: {snapshot['summary']['health']}")
    print(f"Generated sections: {snapshot['summary']['status_counts'].get('generated', 0)}/{snapshot['summary']['total_sections']}")
    print(
        "Retrieval summary: "
        f"trace_sections={snapshot['summary'].get('sections_with_retrieval_trace')} "
        f"avg_final={snapshot['summary'].get('average_retrieval_final_score')} "
        f"avg_semantic={snapshot['summary'].get('average_retrieval_semantic_score')} "
        f"avg_rerank={snapshot['summary'].get('average_retrieval_rerank_score')}"
    )
    print(
        "Gate thresholds: "
        f"trace_sections={gate_thresholds.get('min_retrieval_trace_sections')} "
        f"avg_final={gate_thresholds.get('min_average_retrieval_final_score')} "
        f"avg_semantic={gate_thresholds.get('min_average_retrieval_semantic_score')} "
        f"avg_rerank={gate_thresholds.get('min_average_retrieval_rerank_score')} "
        f"recommended={bool(gate_thresholds.get('used_recommended_thresholds'))} "
        f"source={gate_thresholds.get('threshold_source')}"
    )
    failures = collect_project_snapshot_gate_failures(
        snapshot=snapshot,
        comparison=comparison,
        require_healthy=bool(args.fail_on_attention),
        require_zero_regressions=bool(args.fail_on_regression),
        require_zero_retrieval_regressions=bool(args.fail_on_retrieval_regression),
        require_no_open_blocking=bool(args.fail_on_open_blocking),
        min_retrieval_trace_sections=gate_thresholds.get("min_retrieval_trace_sections"),
        min_average_retrieval_final_score=gate_thresholds.get("min_average_retrieval_final_score"),
        min_average_retrieval_semantic_score=gate_thresholds.get("min_average_retrieval_semantic_score"),
        min_average_retrieval_rerank_score=gate_thresholds.get("min_average_retrieval_rerank_score"),
    )
    if failures:
        for item in failures:
            print(f"GATE FAILED: {item}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
