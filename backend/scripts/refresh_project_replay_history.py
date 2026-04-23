from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh project replay history for runtime projects with available drafts.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
        help="Runtime API base URL.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project id filter.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Project list page size when discovering candidates.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of candidate projects to refresh. Defaults to all.",
    )
    parser.add_argument(
        "--history-dir",
        default="../output/project-replay-history",
        help="Directory used by evaluate_project_snapshot.py for replay history persistence.",
    )
    parser.add_argument(
        "--output-dir",
        default="../output/project-replay-refresh",
        help="Directory for per-project replay markdown/json outputs.",
    )
    parser.add_argument(
        "--report-output",
        default="../output/RAG_test-project-replay-refresh.md",
        help="Markdown refresh report path.",
    )
    parser.add_argument(
        "--report-json-output",
        default="",
        help="Optional JSON refresh report path. Defaults to markdown path with .json suffix.",
    )
    parser.add_argument(
        "--refresh-thresholds",
        action="store_true",
        help="Also rerun recommend_project_replay_thresholds.py after successful refresh.",
    )
    parser.add_argument(
        "--use-recommended-thresholds",
        action="store_true",
        help="Pass recommended retrieval thresholds through to evaluate_project_snapshot.py during refresh.",
    )
    parser.add_argument(
        "--recommended-thresholds-json",
        default="../output/RAG_test-project-replay-thresholds.json",
        help="Recommendation JSON used when --use-recommended-thresholds is enabled.",
    )
    parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="Exit non-zero when no eligible replay candidate project is found.",
    )
    return parser.parse_args()


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _fetch_all_projects(*, base_url: str, page_size: int) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"page": page, "size": page_size})
        payload = _fetch_json(f"{base_url}/projects?{query}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        projects.extend(item for item in items if isinstance(item, dict))
        total = int(data.get("total") or len(projects) or 0)
        if len(projects) >= total or not items:
            break
        page += 1
    return projects


def select_project_replay_candidates(
    projects: list[dict[str, Any]],
    *,
    project_id_filter: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    normalized_filter = str(project_id_filter or "").strip()
    matched_filter = False

    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if normalized_filter and project_id != normalized_filter:
            continue
        if normalized_filter and project_id == normalized_filter:
            matched_filter = True
        draft_version = int(project.get("current_draft_version") or 0)
        status = str(project.get("status") or "").strip()
        if draft_version <= 0:
            skipped.append(
                {
                    "project_id": project_id,
                    "name": str(project.get("name") or ""),
                    "status": status,
                    "draft_version": draft_version,
                    "reason": "current_draft_version=0",
                }
            )
            continue
        candidates.append(
            {
                "project_id": project_id,
                "name": str(project.get("name") or ""),
                "status": status,
                "draft_version": draft_version,
            }
        )

    if normalized_filter and not matched_filter:
        skipped.append(
            {
                "project_id": normalized_filter,
                "name": "",
                "status": "",
                "draft_version": 0,
                "reason": "project_not_found",
            }
        )

    return candidates, skipped


def render_project_replay_refresh_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Project Replay Refresh",
        "",
        f"- Base URL: `{report.get('base_url')}`",
        f"- Project Filter: `{report.get('project_id_filter') or 'all'}`",
        f"- Generated At (UTC): `{report.get('generated_at')}`",
        f"- Use Recommended Thresholds: `{bool(report.get('use_recommended_thresholds'))}`",
        "",
        "## Summary",
        "",
        f"- Discovered projects: `{summary.get('discovered_projects')}`",
        f"- Eligible candidates: `{summary.get('eligible_candidates')}`",
        f"- Refreshed projects: `{summary.get('refreshed_projects')}`",
        f"- Failed refreshes: `{summary.get('failed_projects')}`",
        f"- Skipped projects: `{summary.get('skipped_projects')}`",
    ]

    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    if candidates:
        lines.extend(["", "## Candidates", "", "| Project | Status | Draft |", "| --- | --- | --- |"])
        for item in candidates:
            lines.append(f"| {item.get('project_id')} | {item.get('status')} | {item.get('draft_version')} |")

    refreshed = report.get("refreshed") if isinstance(report.get("refreshed"), list) else []
    if refreshed:
        lines.extend(["", "## Refreshed", "", "| Project | Exit | Markdown | JSON |", "| --- | --- | --- | --- |"])
        for item in refreshed:
            lines.append(
                f"| {item.get('project_id')} | {item.get('exit_code')} | {item.get('markdown_path')} | {item.get('json_path')} |"
            )
            threshold_source = str(item.get("threshold_source") or "").strip()
            if threshold_source:
                lines.append(
                    f"| ↳ thresholds | {threshold_source} | {item.get('recommended_thresholds_json') or '-'} | - |"
                )

    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    if skipped:
        lines.extend(["", "## Skipped", "", "| Project | Reason | Status | Draft |", "| --- | --- | --- | --- |"])
        for item in skipped:
            lines.append(
                f"| {item.get('project_id')} | {item.get('reason')} | {item.get('status') or '-'} | {item.get('draft_version')} |"
            )

    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    if failures:
        lines.extend(["", "## Failures", "", "| Project | Exit | Error |", "| --- | --- | --- |"])
        for item in failures:
            lines.append(
                f"| {item.get('project_id')} | {item.get('exit_code')} | {item.get('error') or '-'} |"
            )

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


def _run_evaluate_snapshot(
    *,
    project_id: str,
    draft_version: int,
    base_url: str,
    history_dir: Path,
    output_dir: Path,
    use_recommended_thresholds: bool = False,
    recommended_thresholds_json: str = "",
) -> dict[str, Any]:
    markdown_path = output_dir / f"{project_id}.md"
    json_path = output_dir / f"{project_id}.json"
    command = [
        sys.executable,
        str(Path(__file__).with_name("evaluate_project_snapshot.py")),
        "--project-id",
        project_id,
        "--draft-version",
        str(draft_version),
        "--base-url",
        base_url,
        "--history-dir",
        str(history_dir),
        "--output",
        str(markdown_path),
        "--json-output",
        str(json_path),
    ]
    if use_recommended_thresholds:
        command.append("--use-recommended-thresholds")
        if str(recommended_thresholds_json or "").strip():
            command.extend(["--recommended-thresholds-json", str(recommended_thresholds_json)])
    result = subprocess.run(command, capture_output=True, text=True)
    gate_thresholds: dict[str, Any] = {}
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload.get("gate_thresholds"), dict):
            gate_thresholds = payload["gate_thresholds"]
    return {
        "project_id": project_id,
        "draft_version": draft_version,
        "exit_code": int(result.returncode),
        "markdown_path": str(markdown_path.resolve()),
        "json_path": str(json_path.resolve()),
        "gate_thresholds": gate_thresholds,
        "used_recommended_thresholds": bool(gate_thresholds.get("used_recommended_thresholds")),
        "recommended_thresholds_available": bool(gate_thresholds.get("recommended_thresholds_available")),
        "recommended_thresholds_json": str(gate_thresholds.get("recommended_thresholds_path") or recommended_thresholds_json or "").strip(),
        "threshold_source": str(gate_thresholds.get("threshold_source") or ""),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


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
    base_url = str(args.base_url or "").rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    history_dir = Path(args.history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)

    projects = _fetch_all_projects(base_url=base_url, page_size=max(1, int(args.page_size)))
    candidates, skipped = select_project_replay_candidates(
        projects,
        project_id_filter=str(args.project_id or ""),
    )
    if int(args.limit or 0) > 0:
        candidates = candidates[: int(args.limit)]

    refreshed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in candidates:
        result = _run_evaluate_snapshot(
            project_id=str(item["project_id"]),
            draft_version=int(item["draft_version"]),
            base_url=base_url,
            history_dir=history_dir,
            output_dir=output_dir,
            use_recommended_thresholds=bool(args.use_recommended_thresholds),
            recommended_thresholds_json=str(args.recommended_thresholds_json or ""),
        )
        if result["exit_code"] == 0:
            refreshed.append(result)
        else:
            failures.append(
                {
                    **result,
                    "error": result.get("stderr") or result.get("stdout"),
                }
            )

    threshold_refresh: dict[str, Any] | None = None
    if args.refresh_thresholds:
        threshold_refresh = _run_threshold_refresh(
            project_id_filter=str(args.project_id or ""),
            history_dir=history_dir,
        )

    report = {
        "base_url": base_url,
        "project_id_filter": str(args.project_id or ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "use_recommended_thresholds": bool(args.use_recommended_thresholds),
        "recommended_thresholds_json": str(args.recommended_thresholds_json or "").strip(),
        "summary": {
            "discovered_projects": len(projects),
            "eligible_candidates": len(candidates),
            "refreshed_projects": len(refreshed),
            "failed_projects": len(failures),
            "skipped_projects": len(skipped),
        },
        "candidates": candidates,
        "refreshed": refreshed,
        "failures": failures,
        "skipped": skipped,
        "threshold_refresh": threshold_refresh or {},
    }

    report_output = Path(args.report_output)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(render_project_replay_refresh_markdown(report), encoding="utf-8")
    report_json_output = Path(args.report_json_output) if str(args.report_json_output or "").strip() else report_output.with_suffix(".json")
    report_json_output.parent.mkdir(parents=True, exist_ok=True)
    report_json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown report to {report_output.resolve()}")
    print(f"Wrote JSON report to {report_json_output.resolve()}")
    print(
        "Refresh summary: "
        f"discovered={len(projects)} candidates={len(candidates)} "
        f"refreshed={len(refreshed)} failed={len(failures)} skipped={len(skipped)}"
    )

    if args.fail_on_empty and not candidates:
        return 2
    if failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
