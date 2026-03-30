from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.parsing.case_library import (
    build_outline_library_entry,
    build_reusable_block_entries,
    render_case_library_markdown,
    summarize_case_library,
)
from app.services.parsing.parser import ParserService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build outline/block libraries from sample manifest phase B tracks.")
    parser.add_argument(
        "--manifest",
        default="data/sample_manifests/sample_manifest.json",
        help="Path to the sample manifest JSON. Defaults to backend/data/sample_manifests/sample_manifest.json.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/case_library",
        help="Directory for generated outline/block library artifacts. Defaults to backend/data/case_library.",
    )
    parser.add_argument(
        "--include-holdout",
        action="store_true",
        help="Also build outline/block entries for holdout_eval documents and mark them as evaluation-only.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    phase_b_plan = manifest.get("phase_b_plan") or {}

    manifest_entries = {entry["sample_id"]: entry for entry in manifest.get("entries") or []}
    candidate_entries = [
        entry
        for entry in manifest_entries.values()
        if entry.get("phase_b_track") == "pilot_main"
        or (args.include_holdout and entry.get("phase_b_track") == "holdout_eval")
    ]
    parser = ParserService()
    outline_entries: list[dict] = []
    block_entries: list[dict] = []

    for candidate in candidate_entries:
        entry = manifest_entries.get(candidate["sample_id"])
        if not entry:
            continue
        library_track = str(entry.get("phase_b_track") or "pilot_main")
        enriched_entry = {
            **entry,
            "profile": entry.get("detected_profile"),
            "track": library_track,
            "library_track": library_track,
        }
        parsed = await parser.parse_document(entry["file_path"])
        outline_entries.append(build_outline_library_entry(sample_entry=enriched_entry, markdown=parsed.markdown))
        block_entries.extend(build_reusable_block_entries(sample_entry=enriched_entry, markdown=parsed.markdown))

    summary = summarize_case_library(outline_entries=outline_entries, block_entries=block_entries)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outline_path = output_dir / "outline_library.json"
    block_path = output_dir / "block_library.json"
    summary_path = output_dir / "case_library_summary.md"

    outline_path.write_text(json.dumps({"entries": outline_entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    block_path.write_text(json.dumps({"entries": block_entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(
        render_case_library_markdown(summary=summary, outline_entries=outline_entries),
        encoding="utf-8",
    )

    print(f"Built case library from {len(candidate_entries)} document(s).")
    print(f"  outline library: {outline_path.resolve()}")
    print(f"  block library: {block_path.resolve()}")
    print(f"  summary: {summary_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
