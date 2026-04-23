from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.composition.section_service import (
    SectionDraftService,
    build_reusable_blocks,
    build_section_reuse_query,
)
from app.services.knowledge import KnowledgeWikiContextProvider
from app.services.knowledge.wiki_prior_eval import (
    build_holdout_query_section,
    evaluate_block_ranking,
    render_prior_eval_markdown,
    select_holdout_sections,
    summarize_eval_records,
)
from app.services.parsing.case_library import build_outline_library_entry
from app.services.parsing.docx_fast_extract import extract_docx_markdown
from app.services.parsing.docling_parser import DoclingParser, ParsedDocument
from app.services.parsing.markdown_cleaner import clean_markdown
from app.services.parsing.parser import ParserService
from app.services.retrieval import CaseLibraryService
from app.services.retrieval.reranker import NoopReranker


class NoopKnowledgeWiki:
    def collect_retrieval_prior_bundle(self, *, section: dict[str, Any], global_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "query_expansion_terms": [],
            "glossary_entries": [],
            "product_cards": [],
            "module_cards": [],
        }

    def collect_query_expansion_terms(self, *, section: dict[str, Any], global_params: dict[str, Any]) -> list[str]:
        return []

    def build_section_context(self, *, section: dict[str, Any], global_params: dict[str, Any]) -> str:
        return ""


class NoopSemanticScorer:
    @property
    def available(self) -> bool:
        return False

    def score(self, *, query: str, text: str) -> float:
        return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Layer 4 AI Wiki priors on holdout documents.")
    parser.add_argument(
        "--manifest",
        default="data/sample_manifests/sample_manifest.json",
        help="Path to sample manifest JSON. Defaults to backend/data/sample_manifests/sample_manifest.json.",
    )
    parser.add_argument(
        "--knowledge-wiki-root",
        default="data/knowledge_wiki",
        help="Path to compiled knowledge wiki root.",
    )
    parser.add_argument(
        "--outline-library",
        default="data/case_library/outline_library.json",
        help="Path to current pilot_main outline library.",
    )
    parser.add_argument(
        "--block-library",
        default="data/case_library/block_library.json",
        help="Path to current pilot_main block library.",
    )
    parser.add_argument(
        "--output",
        default="../output/RAG_test-layer4-ai-wiki-prior-eval.md",
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="Optional JSON report output path. Defaults to the markdown path with .json suffix.",
    )
    parser.add_argument(
        "--holdout-ids",
        default="",
        help="Comma-separated holdout sample_ids to evaluate. Defaults to all holdout_eval entries in the manifest.",
    )
    parser.add_argument(
        "--max-sections-per-doc",
        type=int,
        default=8,
        help="Maximum number of holdout sections to evaluate per document.",
    )
    parser.add_argument(
        "--full-parser",
        action="store_true",
        help="Use full ParserService instead of lightweight DoclingParser.",
    )
    parser.add_argument(
        "--per-document-timeout-seconds",
        type=int,
        default=45,
        help="Timeout for parsing a single holdout document. Timed-out documents are skipped.",
    )
    parser.add_argument(
        "--fast-heuristic-only",
        action="store_true",
        help="Disable dense semantic scoring and use DOCX XML fast extraction for holdout eval.",
    )
    return parser.parse_args()


async def _parse_holdout_document(
    *,
    file_path: str,
    full_parser: bool,
    fast_heuristic_only: bool,
    full_parser_service: ParserService | None = None,
    lightweight_parser: DoclingParser | None = None,
) -> ParsedDocument:
    path = Path(file_path)
    if fast_heuristic_only and path.suffix.lower() == ".docx":
        markdown, metadata = extract_docx_markdown(path)
        cleaned_markdown, cleaner_metadata = clean_markdown(markdown, file_path=file_path)
        return ParsedDocument(
            markdown=cleaned_markdown,
            metadata={**metadata, **cleaner_metadata, "case_library_parse_mode": "docx_fast_extract_holdout_eval"},
            structure={},
        )

    if full_parser:
        parser = full_parser_service or ParserService()
        return await parser.parse_document(file_path)

    parser = lightweight_parser or DoclingParser()
    parsed = await parser.parse(file_path, include_assets=False)
    cleaned_markdown, cleaner_metadata = clean_markdown(parsed.markdown, file_path=file_path)
    return ParsedDocument(
        markdown=cleaned_markdown,
        metadata={**parsed.metadata, **cleaner_metadata, "case_library_parse_mode": "lightweight_holdout_eval"},
        structure=parsed.structure,
    )


def _select_holdout_entries(manifest: dict[str, Any], *, requested_ids: set[str]) -> list[dict[str, Any]]:
    entries = [
        entry
        for entry in (manifest.get("entries") or [])
        if entry.get("phase_b_track") == "holdout_eval"
    ]
    if not requested_ids:
        return entries
    return [entry for entry in entries if str(entry.get("sample_id") or "").strip() in requested_ids]


def _build_case_candidates(*, case_library: CaseLibraryService, query_section: dict[str, Any]) -> list[dict[str, Any]]:
    query = build_section_reuse_query(section=query_section, global_params={}, extra_terms=None)
    return case_library.retrieve_cases(query=query, top_k=3, library_tracks={"pilot_main"})


def _serialize_case_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in candidates:
        serialized.append(
            {
                "sample_id": item.get("sample_id"),
                "file_name": item.get("file_name"),
                "score": item.get("score"),
                "reason": item.get("reason"),
            }
        )
    return serialized


def _compare_section(
    *,
    query_section: dict[str, Any],
    case_candidates: list[dict[str, Any]],
    with_prior_service: SectionDraftService,
    without_prior_service: SectionDraftService,
) -> dict[str, Any]:
    evidence_bundle = SimpleNamespace(content={"case_candidates": case_candidates, "results": []})

    with_bundle = with_prior_service._build_knowledge_wiki_retrieval_bundle(section=query_section, global_params={})
    with_result = with_prior_service._retrieve_case_library_matches(
        section=query_section,
        evidence_bundle=evidence_bundle,
        global_params={},
    )
    with_blocks = build_reusable_blocks(
        section=query_section,
        evidence_bundle=SimpleNamespace(content={"results": []}),
        global_params={},
        case_library_matches=with_result.get("matches") or [],
        extra_query_terms=list(with_bundle.get("query_expansion_terms") or []),
        knowledge_retrieval_bundle=with_bundle,
        limit=3,
    )

    without_result = without_prior_service._retrieve_case_library_matches(
        section=query_section,
        evidence_bundle=evidence_bundle,
        global_params={},
    )
    without_blocks = build_reusable_blocks(
        section=query_section,
        evidence_bundle=SimpleNamespace(content={"results": []}),
        global_params={},
        case_library_matches=without_result.get("matches") or [],
        extra_query_terms=[],
        knowledge_retrieval_bundle={},
        limit=3,
    )

    return {
        "case_candidate_count": len(case_candidates),
        "case_candidates": _serialize_case_candidates(case_candidates),
        "knowledge_wiki_terms": with_result.get("trace", {}).get("knowledge_wiki_terms") or [],
        "knowledge_wiki_product_cards": with_result.get("trace", {}).get("knowledge_wiki_product_cards") or [],
        "knowledge_wiki_module_cards": with_result.get("trace", {}).get("knowledge_wiki_module_cards") or [],
        "baseline": evaluate_block_ranking(query_section=query_section, blocks=without_blocks),
        "with_prior": evaluate_block_ranking(query_section=query_section, blocks=with_blocks),
    }


async def main() -> None:
    args = parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    requested_ids = {
        item.strip()
        for item in str(args.holdout_ids or "").split(",")
        if item.strip()
    }
    holdout_entries = _select_holdout_entries(manifest, requested_ids=requested_ids)
    if not holdout_entries:
        raise SystemExit("No holdout_eval entries found for evaluation.")

    case_library = CaseLibraryService(
        outline_library_path=Path(args.outline_library),
        block_library_path=Path(args.block_library),
        semantic_scorer=NoopSemanticScorer() if args.fast_heuristic_only else None,
        reranker=NoopReranker() if args.fast_heuristic_only else None,
    )
    knowledge_wiki = KnowledgeWikiContextProvider(args.knowledge_wiki_root)
    with_prior_service = SectionDraftService(
        executor=object(),
        quality_gate=object(),
        case_library=case_library,
        knowledge_wiki=knowledge_wiki,
    )
    without_prior_service = SectionDraftService(
        executor=object(),
        quality_gate=object(),
        case_library=case_library,
        knowledge_wiki=NoopKnowledgeWiki(),
    )

    full_parser_service = ParserService() if args.full_parser else None
    lightweight_parser = None if args.full_parser else DoclingParser()

    records: list[dict[str, Any]] = []
    skipped_documents: list[dict[str, Any]] = []
    for entry in holdout_entries:
        try:
            parsed = await asyncio.wait_for(
                _parse_holdout_document(
                    file_path=entry["file_path"],
                    full_parser=bool(args.full_parser),
                    fast_heuristic_only=bool(args.fast_heuristic_only),
                    full_parser_service=full_parser_service,
                    lightweight_parser=lightweight_parser,
                ),
                timeout=max(int(args.per_document_timeout_seconds or 0), 1),
            )
        except Exception as exc:  # noqa: BLE001
            skipped_documents.append(
                {
                    "sample_id": entry.get("sample_id"),
                    "file_name": entry.get("file_name"),
                    "error": str(exc),
                }
            )
            continue
        outline_entry = build_outline_library_entry(
            sample_entry={**entry, "library_track": "holdout_eval"},
            markdown=parsed.markdown,
            structure_hints=parsed.structure,
        )
        selected_sections = select_holdout_sections(
            list(outline_entry.get("section_catalog") or []),
            limit=max(int(args.max_sections_per_doc or 0), 1),
        )
        for section in selected_sections:
            query_section = build_holdout_query_section(
                section=section,
                document_name=str(entry.get("file_name") or ""),
            )
            case_candidates = _build_case_candidates(case_library=case_library, query_section=query_section)
            comparison = _compare_section(
                query_section=query_section,
                case_candidates=case_candidates,
                with_prior_service=with_prior_service,
                without_prior_service=without_prior_service,
            )
            records.append(
                {
                    "sample_id": entry.get("sample_id"),
                    "document_name": entry.get("file_name"),
                    "section_id": query_section.get("source_section_id"),
                    "section_title": query_section.get("title"),
                    "section_path": query_section.get("source_section_path"),
                    "target_section_type": query_section.get("target_section_type"),
                    "target_equipment_type": query_section.get("target_equipment_type"),
                    **comparison,
                }
            )

    summary = summarize_eval_records(records)
    summary["evaluation_mode"] = "fast_heuristic_only" if args.fast_heuristic_only else "default"
    summary["docx_fast_extract_enabled"] = bool(args.fast_heuristic_only)
    summary["skipped_documents"] = len(skipped_documents)
    markdown = render_prior_eval_markdown(summary=summary, records=records)
    if skipped_documents:
        markdown += "\n## Skipped Documents\n\n"
        for item in skipped_documents:
            markdown += f"- {item.get('file_name') or item.get('sample_id')}: {item.get('error')}\n"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    json_output_path = Path(args.json_output) if str(args.json_output or "").strip() else output_path.with_suffix(".json")
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(
        json.dumps({"summary": summary, "records": records, "skipped_documents": skipped_documents}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote markdown report to {output_path.resolve()}")
    print(f"Wrote JSON report to {json_output_path.resolve()}")
    print(f"Evaluated sections: {summary.get('total_sections')}")
    print(f"With prior total boost: {(summary.get('with_prior') or {}).get('total_prior_boost', 0.0)}")
    if skipped_documents:
        print(f"Skipped documents: {len(skipped_documents)}")


if __name__ == "__main__":
    asyncio.run(main())
