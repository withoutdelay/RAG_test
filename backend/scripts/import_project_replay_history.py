from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from app.services.validation import (
    build_project_snapshot_history_stem,
    unwrap_project_snapshot_payload,
)

try:
    from scripts.evaluate_project_snapshot import resolve_gate_thresholds
except ModuleNotFoundError:  # pragma: no cover - direct script execution path
    from evaluate_project_snapshot import resolve_gate_thresholds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import offline project replay snapshots/evaluations into the canonical history directory."
    )
    parser.add_argument(
        "--input-json",
        action="append",
        default=[],
        help="Explicit replay snapshot/evaluation JSON file. Can be passed multiple times.",
    )
    parser.add_argument(
        "--input-dir",
        action="append",
        default=[],
        help="Directory containing replay snapshot/evaluation JSON files. Can be passed multiple times.",
    )
    parser.add_argument(
        "--glob",
        default="*.json",
        help="Glob pattern used for each --input-dir. Defaults to *.json.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project id filter.",
    )
    parser.add_argument(
        "--history-dir",
        default="../output/project-replay-history",
        help="Canonical replay history directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing history snapshot when the canonical stem already exists.",
    )
    parser.add_argument(
        "--backfill-existing-gate-thresholds",
        action="store_true",
        help="When a canonical history snapshot already exists, update its gate_thresholds metadata instead of only skipping.",
    )
    parser.add_argument(
        "--min-trace-sections",
        type=int,
        default=0,
        help="Optional explicit gate threshold written into imported history metadata.",
    )
    parser.add_argument(
        "--min-avg-retrieval-final",
        type=float,
        default=0.0,
        help="Optional explicit gate threshold written into imported history metadata.",
    )
    parser.add_argument(
        "--min-avg-retrieval-semantic",
        type=float,
        default=0.0,
        help="Optional explicit gate threshold written into imported history metadata.",
    )
    parser.add_argument(
        "--min-avg-retrieval-rerank",
        type=float,
        default=0.0,
        help="Optional explicit gate threshold written into imported history metadata.",
    )
    parser.add_argument(
        "--use-recommended-thresholds",
        action="store_true",
        help="Resolve gate_thresholds from the recommendation JSON when explicit thresholds are unset.",
    )
    parser.add_argument(
        "--recommended-thresholds-json",
        default="../output/RAG_test-project-replay-thresholds.json",
        help="Recommendation JSON used by --use-recommended-thresholds.",
    )
    parser.add_argument(
        "--refresh-thresholds",
        action="store_true",
        help="Also rerun recommend_project_replay_thresholds.py after import.",
    )
    parser.add_argument(
        "--report-output",
        default="../output/RAG_test-project-replay-import.md",
        help="Markdown report path.",
    )
    parser.add_argument(
        "--report-json-output",
        default="",
        help="Optional JSON report path. Defaults to markdown path with .json suffix.",
    )
    parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="Exit non-zero when no snapshot is imported.",
    )
    return parser.parse_args()


def discover_replay_import_inputs(
    *,
    input_jsons: list[str],
    input_dirs: list[str],
    pattern: str = "*.json",
) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        resolved = str(path.resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        ordered.append(path)

    for raw in input_jsons:
        text = str(raw or "").strip()
        if not text:
            continue
        _add(Path(text))
    for raw in input_dirs:
        text = str(raw or "").strip()
        if not text:
            continue
        directory = Path(text)
        for path in sorted(directory.glob(pattern)) if directory.exists() else []:
            if path.is_file():
                _add(path)
    return ordered


def _load_snapshot_input(path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}, "file_not_found"
    except Exception:
        return {}, {}, "invalid_json"
    if not isinstance(payload, dict):
        return {}, {}, "invalid_payload"
    snapshot = unwrap_project_snapshot_payload(payload)
    if not isinstance(snapshot, dict) or not snapshot:
        return {}, {}, "missing_snapshot"
    project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        return {}, {}, "missing_project_id"
    normalized_payload = payload if isinstance(payload.get("snapshot"), dict) else {"snapshot": snapshot}
    return normalized_payload, snapshot, ""


def _summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    sections = snapshot.get("sections") if isinstance(snapshot.get("sections"), list) else []
    trace_sections = int(summary.get("sections_with_retrieval_trace") or 0)
    if trace_sections <= 0:
        trace_sections = sum(
            1 for item in sections if isinstance(item, dict) and int(item.get("retrieval_trace_count") or 0) > 0
        )
    final_score = summary.get("average_retrieval_final_score")
    semantic_score = summary.get("average_retrieval_semantic_score")
    rerank_score = summary.get("average_retrieval_rerank_score")
    has_structured_metrics = bool(
        trace_sections > 0
        or final_score is not None
        or semantic_score is not None
        or rerank_score is not None
    )
    return {
        "project_id": str(project.get("id") or "").strip(),
        "draft_version": int(project.get("current_draft_version") or 0),
        "generated_at": str(snapshot.get("generated_at") or "").strip(),
        "health": str(summary.get("health") or "unknown").strip() or "unknown",
        "trace_sections": trace_sections,
        "average_retrieval_final_score": final_score,
        "has_structured_metrics": has_structured_metrics,
    }


def _build_gate_thresholds(args: argparse.Namespace) -> dict[str, Any]:
    return resolve_gate_thresholds(args)


def import_project_replay_history(
    *,
    input_paths: list[Path],
    history_dir: Path,
    project_id_filter: str = "",
    overwrite: bool = False,
    gate_thresholds: dict[str, Any] | None = None,
    backfill_existing_gate_thresholds: bool = False,
) -> dict[str, Any]:
    history_dir.mkdir(parents=True, exist_ok=True)
    normalized_filter = str(project_id_filter or "").strip()
    imported: list[dict[str, Any]] = []
    backfilled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    structured_import_count = 0
    legacy_import_count = 0
    loaded_snapshot_count = 0
    gate_threshold_annotation_count = 0
    gate_threshold_backfill_count = 0
    threshold_source_counts: dict[str, int] = {}
    effective_gate_thresholds = dict(gate_thresholds or {})
    threshold_source = str(effective_gate_thresholds.get("threshold_source") or "").strip()
    if threshold_source:
        threshold_source_counts[threshold_source] = 0

    for path in input_paths:
        payload, snapshot, error = _load_snapshot_input(path)
        if error:
            invalid.append(
                {
                    "source_path": str(path.resolve()),
                    "reason": error,
                }
            )
            continue
        loaded_snapshot_count += 1
        snapshot_summary = _summarize_snapshot(snapshot)
        project_id = str(snapshot_summary.get("project_id") or "")
        if normalized_filter and project_id != normalized_filter:
            skipped.append(
                {
                    "source_path": str(path.resolve()),
                    "project_id": project_id,
                    "reason": "project_id_mismatch",
                }
            )
            continue

        history_stem = build_project_snapshot_history_stem(snapshot)
        destination = history_dir / f"{history_stem}.json"
        if effective_gate_thresholds:
            payload["gate_thresholds"] = effective_gate_thresholds
        if destination.exists() and not overwrite:
            if backfill_existing_gate_thresholds and effective_gate_thresholds:
                try:
                    existing_payload = json.loads(destination.read_text(encoding="utf-8"))
                except Exception:
                    existing_payload = {}
                if not isinstance(existing_payload, dict):
                    existing_payload = {}
                existing_payload["gate_thresholds"] = effective_gate_thresholds
                destination.write_text(json.dumps(existing_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                backfilled_item = {
                    "source_path": str(path.resolve()),
                    "destination_path": str(destination.resolve()),
                    "threshold_source": threshold_source or "none",
                    "used_recommended_thresholds": bool(effective_gate_thresholds.get("used_recommended_thresholds")),
                    "recommended_thresholds_available": bool(effective_gate_thresholds.get("recommended_thresholds_available")),
                    **snapshot_summary,
                }
                backfilled.append(backfilled_item)
                gate_threshold_backfill_count += 1
                if threshold_source:
                    threshold_source_counts[threshold_source] = threshold_source_counts.get(threshold_source, 0) + 1
                continue
            skipped.append(
                {
                    "source_path": str(path.resolve()),
                    "project_id": project_id,
                    "destination_path": str(destination.resolve()),
                    "reason": "history_snapshot_exists",
                }
            )
            continue

        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        imported_item = {
            "source_path": str(path.resolve()),
            "destination_path": str(destination.resolve()),
            "threshold_source": threshold_source or "none",
            "used_recommended_thresholds": bool(effective_gate_thresholds.get("used_recommended_thresholds")),
            "recommended_thresholds_available": bool(effective_gate_thresholds.get("recommended_thresholds_available")),
            **snapshot_summary,
        }
        imported.append(imported_item)
        if effective_gate_thresholds:
            gate_threshold_annotation_count += 1
        if threshold_source:
            threshold_source_counts[threshold_source] = threshold_source_counts.get(threshold_source, 0) + 1
        if bool(snapshot_summary.get("has_structured_metrics")):
            structured_import_count += 1
        else:
            legacy_import_count += 1

    return {
        "summary": {
            "input_files": len(input_paths),
            "loaded_snapshots": loaded_snapshot_count,
            "imported_snapshots": len(imported),
            "backfilled_snapshots": len(backfilled),
            "structured_imported_snapshots": structured_import_count,
            "legacy_imported_snapshots": legacy_import_count,
            "gate_threshold_annotations": gate_threshold_annotation_count,
            "gate_threshold_backfills": gate_threshold_backfill_count,
            "threshold_source_counts": threshold_source_counts,
            "skipped_snapshots": len(skipped),
            "invalid_inputs": len(invalid),
        },
        "imported": imported,
        "backfilled": backfilled,
        "skipped": skipped,
        "invalid": invalid,
    }


def render_project_replay_import_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Project Replay Import",
        "",
        f"- Generated At (UTC): `{report.get('generated_at')}`",
        f"- Project Filter: `{report.get('project_id_filter') or 'all'}`",
        f"- Use Recommended Thresholds: `{bool(report.get('use_recommended_thresholds'))}`",
        "",
        "## Summary",
        "",
        f"- Input files: `{summary.get('input_files')}`",
        f"- Loaded snapshots: `{summary.get('loaded_snapshots')}`",
        f"- Imported snapshots: `{summary.get('imported_snapshots')}`",
        f"- Backfilled snapshots: `{summary.get('backfilled_snapshots')}`",
        f"- Structured metric imports: `{summary.get('structured_imported_snapshots')}`",
        f"- Legacy metric imports: `{summary.get('legacy_imported_snapshots')}`",
        f"- Gate threshold annotations: `{summary.get('gate_threshold_annotations')}`",
        f"- Gate threshold backfills: `{summary.get('gate_threshold_backfills')}`",
        f"- Threshold source counts: `{summary.get('threshold_source_counts') or {}}`",
        f"- Skipped snapshots: `{summary.get('skipped_snapshots')}`",
        f"- Invalid inputs: `{summary.get('invalid_inputs')}`",
    ]

    imported = report.get("imported") if isinstance(report.get("imported"), list) else []
    if imported:
        lines.extend(
            [
                "",
                "## Imported",
                "",
                "| Project | Draft | Health | Trace Sections | Final Score | Structured | Threshold Source | Destination |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in imported:
            lines.append(
                "| "
                f"{item.get('project_id')} | {item.get('draft_version')} | {item.get('health')} | "
                f"{item.get('trace_sections')} | {item.get('average_retrieval_final_score')} | "
                f"{item.get('has_structured_metrics')} | {item.get('threshold_source')} | {item.get('destination_path')} |"
            )

    backfilled = report.get("backfilled") if isinstance(report.get("backfilled"), list) else []
    if backfilled:
        lines.extend(
            [
                "",
                "## Backfilled",
                "",
                "| Project | Draft | Threshold Source | Destination |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in backfilled:
            lines.append(
                "| "
                f"{item.get('project_id')} | {item.get('draft_version')} | {item.get('threshold_source')} | {item.get('destination_path')} |"
            )

    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    if skipped:
        lines.extend(["", "## Skipped", "", "| Source | Project | Reason | Destination |", "| --- | --- | --- | --- |"])
        for item in skipped:
            lines.append(
                "| "
                f"{item.get('source_path')} | {item.get('project_id') or '-'} | {item.get('reason')} | {item.get('destination_path') or '-'} |"
            )

    invalid = report.get("invalid") if isinstance(report.get("invalid"), list) else []
    if invalid:
        lines.extend(["", "## Invalid Inputs", "", "| Source | Reason |", "| --- | --- |"])
        for item in invalid:
            lines.append(f"| {item.get('source_path')} | {item.get('reason')} |")

    threshold_refresh = report.get("threshold_refresh") if isinstance(report.get("threshold_refresh"), dict) else {}
    if threshold_refresh:
        lines.extend(
            [
                "",
                "## Threshold Refresh",
                "",
                f"- Exit code: `{threshold_refresh.get('exit_code')}`",
                f"- Markdown path: `{threshold_refresh.get('markdown_path')}`",
                f"- JSON path: `{threshold_refresh.get('json_path')}`",
            ]
        )
        notes = threshold_refresh.get("notes")
        if notes:
            lines.append(f"- Notes: `{notes}`")

    return "\n".join(lines).rstrip() + "\n"


def _run_threshold_refresh(*, project_id_filter: str, history_dir: Path) -> dict[str, Any]:
    markdown_path = Path("../output/RAG_test-project-replay-thresholds.md").resolve()
    json_path = Path("../output/RAG_test-project-replay-thresholds.json").resolve()
    command = [
        sys.executable,
        str(Path(__file__).with_name("recommend_project_replay_thresholds.py")),
        "--history-dir",
        str(history_dir),
    ]
    if project_id_filter:
        command.extend(["--project-id", project_id_filter])
    result = subprocess.run(command, capture_output=True, text=True)
    return {
        "exit_code": int(result.returncode),
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "notes": (result.stdout or result.stderr).strip(),
    }


def main() -> int:
    args = parse_args()
    input_paths = discover_replay_import_inputs(
        input_jsons=list(args.input_json or []),
        input_dirs=list(args.input_dir or []),
        pattern=str(args.glob or "*.json"),
    )
    history_dir = Path(args.history_dir)
    gate_thresholds = _build_gate_thresholds(args)
    report = import_project_replay_history(
        input_paths=input_paths,
        history_dir=history_dir,
        project_id_filter=str(args.project_id or ""),
        overwrite=bool(args.overwrite),
        gate_thresholds=gate_thresholds,
        backfill_existing_gate_thresholds=bool(args.backfill_existing_gate_thresholds),
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["project_id_filter"] = str(args.project_id or "")
    report["use_recommended_thresholds"] = bool(args.use_recommended_thresholds)
    report["recommended_thresholds_json"] = str(args.recommended_thresholds_json or "")
    if args.refresh_thresholds:
        report["threshold_refresh"] = _run_threshold_refresh(
            project_id_filter=str(args.project_id or ""),
            history_dir=history_dir,
        )

    markdown = render_project_replay_import_markdown(report)
    output_path = Path(args.report_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    json_output_path = (
        Path(args.report_json_output)
        if str(args.report_json_output or "").strip()
        else output_path.with_suffix(".json")
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown report to {output_path.resolve()}")
    print(f"Wrote JSON report to {json_output_path.resolve()}")
    print(
        "Import summary: "
        f"inputs={report['summary'].get('input_files')} "
        f"loaded={report['summary'].get('loaded_snapshots')} "
        f"imported={report['summary'].get('imported_snapshots')} "
        f"backfilled={report['summary'].get('backfilled_snapshots')} "
        f"structured={report['summary'].get('structured_imported_snapshots')}"
    )
    if args.fail_on_empty and int(report["summary"].get("imported_snapshots") or 0) <= 0 and int(report["summary"].get("backfilled_snapshots") or 0) <= 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
