from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.services.domain.term_lexicon import build_corpus_term_lexicon
from app.services.parsing.case_library import (
    build_outline_library_entry,
    build_reusable_block_entries,
    render_case_library_markdown,
    summarize_case_library,
)
from app.services.parsing.docling_parser import DoclingParser, ParsedDocument
from app.services.parsing.markdown_cleaner import clean_markdown
from app.services.parsing.document_sources import (
    build_direct_source_entry,
    is_library_ready_entry,
    iter_document_paths,
)
from app.services.parsing.parser import ParserService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build outline/block libraries from a manifest or direct document paths.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Document file paths or directories containing supported files. When provided, manifest mode is bypassed.",
    )
    parser.add_argument(
        "--manifest",
        default="data/sample_manifests/sample_manifest.json",
        help="Path to the sample manifest JSON. Used only when no direct paths are provided.",
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
    parser.add_argument(
        "--library-track",
        default="pilot_main",
        help="Track label to stamp onto direct-path entries. Defaults to pilot_main.",
    )
    parser.add_argument(
        "--include-nonready",
        action="store_true",
        help="In direct-path mode, also include files whose ingestion recommendation is not library-ready.",
    )
    parser.add_argument(
        "--full-parser",
        action="store_true",
        help="Use the full ParserService pipeline for all documents. By default manifest mode uses lightweight parsing.",
    )
    return parser.parse_args()


def _build_manifest_candidates(manifest: dict[str, Any], *, include_holdout: bool) -> list[dict[str, Any]]:
    manifest_entries = {entry["sample_id"]: entry for entry in manifest.get("entries") or []}
    return [
        entry
        for entry in manifest_entries.values()
        if entry.get("phase_b_track") == "pilot_main"
        or (include_holdout and entry.get("phase_b_track") == "holdout_eval")
    ]


async def _parse_case_library_document(
    file_path: str,
    *,
    lightweight: bool,
    full_parser: ParserService | None = None,
    lightweight_parser: DoclingParser | None = None,
) -> ParsedDocument:
    if not lightweight:
        parser = full_parser or ParserService()
        return await parser.parse_document(file_path)

    parser = lightweight_parser or DoclingParser()
    parsed = await parser.parse(file_path, include_assets=False)
    cleaned_markdown, cleaner_metadata = clean_markdown(parsed.markdown, file_path=file_path)
    metadata = {
        **parsed.metadata,
        **cleaner_metadata,
        "case_library_parse_mode": "lightweight",
    }
    return ParsedDocument(
        markdown=cleaned_markdown,
        metadata=metadata,
        structure=parsed.structure,
    )


async def main() -> None:
    args = parse_args()
    full_parser = ParserService() if args.paths or args.full_parser else None
    lightweight_parser = DoclingParser() if not args.paths and not args.full_parser else None
    outline_entries: list[dict] = []
    block_entries: list[dict] = []
    skipped_nonready: list[str] = []
    skipped_parse_insufficient: list[str] = []
    failed_documents: list[str] = []
    attempted_documents = 0

    if args.paths:
        candidate_entries = []
        for file_path in iter_document_paths(args.paths):
            attempted_documents += 1
            try:
                parsed = await _parse_case_library_document(
                    str(file_path),
                    lightweight=False,
                    full_parser=full_parser,
                )
            except Exception as exc:
                failed_documents.append(f"{file_path.name} ({exc})")
                continue
            entry = build_direct_source_entry(
                file_path=file_path,
                parsed_metadata=parsed.metadata,
                library_track=args.library_track,
                source="direct_paths",
            )
            if str(entry.get("parse_gate_status") or "").strip().lower() == "insufficient":
                skipped_parse_insufficient.append(
                    f"{entry['file_name']} ({entry.get('parse_gate_reason') or 'parse_gate_insufficient'})"
                )
                continue
            if not args.include_nonready and not is_library_ready_entry(entry):
                skipped_nonready.append(
                    f"{entry['file_name']} ({entry.get('ingestion_recommendation') or 'unknown'})"
                )
                continue
            candidate_entries.append({"entry": entry, "parsed": parsed, "lightweight": False})
    else:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_entries = []
        for entry in _build_manifest_candidates(manifest, include_holdout=args.include_holdout):
            candidate_entries.append({"entry": entry, "parsed": None, "lightweight": not args.full_parser})
        attempted_documents = len(candidate_entries)

    for index, candidate in enumerate(candidate_entries, start=1):
        entry = candidate["entry"]
        file_label = entry.get("file_name") or entry.get("file_path") or f"document-{index}"
        parse_mode = "preparsed" if candidate["parsed"] is not None else ("lightweight" if candidate["lightweight"] else "full")
        print(f"[{index}/{len(candidate_entries)}] parsing {file_label} ({parse_mode})", flush=True)
        try:
            parsed = candidate["parsed"] or await _parse_case_library_document(
                entry["file_path"],
                lightweight=bool(candidate["lightweight"]),
                full_parser=full_parser,
                lightweight_parser=lightweight_parser,
            )
        except Exception as exc:
            failed_documents.append(f"{entry.get('file_name') or entry.get('file_path')} ({exc})")
            print(f"  failed: {exc}", flush=True)
            continue
        library_track = str(entry.get("phase_b_track") or entry.get("library_track") or "pilot_main")
        enriched_entry = {
            **entry,
            "profile": entry.get("profile") or entry.get("detected_profile"),
            "track": library_track,
            "library_track": library_track,
        }
        outline_entry = build_outline_library_entry(
            sample_entry=enriched_entry,
            markdown=parsed.markdown,
            structure_hints=parsed.structure,
        )
        document_block_entries = build_reusable_block_entries(
            sample_entry=enriched_entry,
            markdown=parsed.markdown,
            structure_hints=parsed.structure,
        )
        outline_entries.append(outline_entry)
        block_entries.extend(document_block_entries)
        print(
            f"  built headings={outline_entry.get('heading_count', 0)} blocks={len(document_block_entries)}",
            flush=True,
        )

    summary = summarize_case_library(outline_entries=outline_entries, block_entries=block_entries)
    term_lexicon = build_corpus_term_lexicon(
        outline_entries=outline_entries,
        block_entries=block_entries,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outline_path = output_dir / "outline_library.json"
    block_path = output_dir / "block_library.json"
    summary_path = output_dir / "case_library_summary.md"

    outline_payload = {"entries": outline_entries, "term_lexicon": term_lexicon}
    block_payload = {"entries": block_entries, "term_lexicon": term_lexicon}
    outline_path.write_text(json.dumps(outline_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    block_path.write_text(json.dumps(block_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(
        render_case_library_markdown(summary=summary, outline_entries=outline_entries),
        encoding="utf-8",
    )

    print(f"Attempted documents: {attempted_documents}")
    print(f"Built case library from {len(outline_entries)} document(s).")
    if skipped_parse_insufficient:
        print(f"Skipped parse-insufficient: {len(skipped_parse_insufficient)}")
        for item in skipped_parse_insufficient:
            print(f"  skipped: {item}")
    if skipped_nonready:
        print(f"Skipped non-ready: {len(skipped_nonready)}")
        for item in skipped_nonready:
            print(f"  skipped: {item}")
    if failed_documents:
        print(f"Failed documents: {len(failed_documents)}")
        for item in failed_documents:
            print(f"  failed: {item}")
    print(f"  outline library: {outline_path.resolve()}")
    print(f"  block library: {block_path.resolve()}")
    print(f"  summary: {summary_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
