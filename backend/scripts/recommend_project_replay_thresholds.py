from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.validation import (
    build_project_replay_threshold_recommendation,
    render_project_replay_threshold_markdown,
    unwrap_project_snapshot_payload,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend project replay retrieval thresholds from snapshot history.")
    parser.add_argument(
        "--history-dir",
        default="../output/project-replay-history",
        help="Directory containing replay snapshot JSON history.",
    )
    parser.add_argument(
        "--current-json",
        default="../output/RAG_test-project-replay-eval.json",
        help="Optional latest replay evaluation JSON to include in the recommendation set.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project id filter. When omitted, recommendations use all discovered snapshots.",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=3,
        help="Minimum healthy sample count required before emitting a threshold recommendation.",
    )
    parser.add_argument(
        "--output",
        default="../output/RAG_test-project-replay-thresholds.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="Optional JSON output path. Defaults to markdown path with .json suffix.",
    )
    return parser.parse_args()


def _load_snapshot(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return unwrap_project_snapshot_payload(payload)


def main() -> int:
    args = parse_args()
    history_dir = Path(args.history_dir)
    snapshots: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    project_id_filter = str(args.project_id or "").strip()

    for path in sorted(history_dir.glob("*.json")) if history_dir.exists() else []:
        try:
            snapshot = _load_snapshot(path)
        except Exception:
            continue
        project_id = str((snapshot.get("project") or {}).get("id") or "").strip()
        if project_id_filter and project_id != project_id_filter:
            continue
        dedupe_key = (project_id, str(snapshot.get("generated_at") or ""))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        snapshots.append(snapshot)

    current_path = Path(args.current_json)
    if current_path.exists():
        try:
            snapshot = _load_snapshot(current_path)
        except Exception:
            snapshot = {}
        if snapshot:
            project_id = str((snapshot.get("project") or {}).get("id") or "").strip()
            if not project_id_filter or project_id == project_id_filter:
                dedupe_key = (project_id, str(snapshot.get("generated_at") or ""))
                if dedupe_key not in seen_keys:
                    seen_keys.add(dedupe_key)
                    snapshots.append(snapshot)

    recommendation = build_project_replay_threshold_recommendation(
        snapshots=snapshots,
        min_samples=max(1, int(args.min_samples)),
    )
    markdown = render_project_replay_threshold_markdown(recommendation)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    json_output_path = Path(args.json_output) if str(args.json_output or "").strip() else output_path.with_suffix(".json")
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(recommendation, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown report to {output_path.resolve()}")
    print(f"Wrote JSON report to {json_output_path.resolve()}")
    print(
        "Recommendation summary: "
        f"healthy={recommendation['summary'].get('eligible_healthy_snapshots')} "
        f"trace_samples={recommendation['summary'].get('trace_metric_samples')} "
        f"final_samples={recommendation['summary'].get('retrieval_final_metric_samples')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
