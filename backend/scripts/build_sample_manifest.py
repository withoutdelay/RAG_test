from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Iterable

from app.services.parsing.parser import ParserService
from app.services.parsing.sample_manifest import (
    TRACK_NEEDS_REVIEW,
    apply_profile_to_manifest_entry,
    build_sample_manifest,
    build_sample_manifest_entry,
    render_sample_manifest_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a sample manifest for reuse-first pilot documents.")
    parser.add_argument("paths", nargs="+", help="Document file paths or directories containing supported files.")
    parser.add_argument(
        "--output-json",
        default="data/sample_manifests/sample_manifest.json",
        help="Output path for the manifest JSON. Defaults to backend/data/sample_manifests/sample_manifest.json.",
    )
    parser.add_argument(
        "--output-md",
        default="data/sample_manifests/sample_manifest.md",
        help="Output path for the manifest markdown summary. Defaults to backend/data/sample_manifests/sample_manifest.md.",
    )
    parser.add_argument(
        "--assigned-track",
        default=TRACK_NEEDS_REVIEW,
        choices=("needs_review", "pilot_main", "holdout_eval", "ocr_asset_only"),
        help="Default assigned_track for new manifest entries. Defaults to needs_review.",
    )
    parser.add_argument(
        "--with-profile",
        action="store_true",
        help="Parse each document and enrich the manifest with detected document profile and ingestion recommendation.",
    )
    return parser.parse_args()


async def build_entries(paths: list[Path], *, assigned_track: str, with_profile: bool) -> list[dict]:
    entries = []
    parser = ParserService() if with_profile else None
    for path in paths:
        entry = build_sample_manifest_entry(file_path=path, assigned_track=assigned_track)
        if parser is not None:
            parsed = await parser.parse_document(str(path))
            profile_metadata = parsed.metadata.get("document_profile") or {}
            entry = apply_profile_to_manifest_entry(
                entry,
                profile_name=str(profile_metadata.get("name") or "unknown"),
                ingestion_recommendation=str(parsed.metadata.get("ingestion_recommendation") or "unknown"),
                high_risk_content_flags=list(parsed.metadata.get("high_risk_content_flags") or []),
                metrics=dict(profile_metadata.get("metrics") or {}),
            )
        entries.append(entry)
    return entries


def iter_document_paths(raw_paths: Iterable[str]) -> list[Path]:
    allowed_suffixes = {".pdf", ".doc", ".docx"}
    resolved: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            for suffix in ("*.pdf", "*.PDF", "*.doc", "*.DOC", "*.docx", "*.DOCX"):
                resolved.extend(sorted(item for item in path.rglob(suffix) if item.is_file()))
            continue
        if path.suffix.lower() not in allowed_suffixes:
            raise SystemExit(f"Only PDF/DOC/DOCX files are supported for sample manifest generation: {raw_path}")
        resolved.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in resolved:
        normalized = path.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    if not unique:
        raise SystemExit("No supported document files were found.")
    return unique


async def main() -> None:
    args = parse_args()
    document_paths = iter_document_paths(args.paths)
    entries = await build_entries(document_paths, assigned_track=args.assigned_track, with_profile=args.with_profile)
    manifest = build_sample_manifest(entries=entries, root_paths=[str(path) for path in document_paths])

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_sample_manifest_markdown(manifest), encoding="utf-8")

    print(f"Built sample manifest for {len(entries)} file(s).")
    print(f"  json: {output_json.resolve()}")
    print(f"  markdown: {output_md.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
