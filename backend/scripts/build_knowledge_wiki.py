from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.knowledge import compile_knowledge_wiki
from app.services.knowledge.wiki_llm_compiler import compile_llm_wiki_candidates, merge_llm_wiki_items
from app.services.knowledge.wiki_storage import write_knowledge_wiki_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile a local AI wiki from case library artifacts.")
    parser.add_argument(
        "--outline-library",
        default="data/case_library/outline_library.json",
        help="Path to outline_library.json",
    )
    parser.add_argument(
        "--block-library",
        default="data/case_library/block_library.json",
        help="Path to block_library.json",
    )
    parser.add_argument(
        "--output-dir",
        default="data/knowledge_wiki",
        help="Directory for generated wiki files.",
    )
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Run the offline LLM compiler before writing draft/published/rejected layers.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    outline_path = Path(args.outline_library)
    block_path = Path(args.block_library)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outline_payload = json.loads(outline_path.read_text(encoding="utf-8"))
    block_payload = json.loads(block_path.read_text(encoding="utf-8"))
    bundle = compile_knowledge_wiki(
        outline_entries=outline_payload.get("entries") or [],
        block_entries=block_payload.get("entries") or [],
        term_lexicon=outline_payload.get("term_lexicon") or block_payload.get("term_lexicon") or {},
    )
    llm_result: dict[str, object] = {"status": "disabled", "items": [], "summary": {}}
    if args.enable_llm:
        llm_result = await compile_llm_wiki_candidates(
            wiki_items=bundle.get("wiki_items") or [],
            structured_assets=bundle.get("structured_assets") or {},
            outline_entries=outline_payload.get("entries") or [],
            block_entries=block_payload.get("entries") or [],
            enabled=True,
        )
        bundle = merge_llm_wiki_items(bundle=bundle, llm_result=llm_result)
    write_summary = write_knowledge_wiki_bundle(output_dir=output_dir, bundle=bundle)
    structured_assets = bundle["structured_assets"]

    print(f"Compiled AI wiki to {output_dir.resolve()}")
    print(f"  draft items: {write_summary['draft_items']}")
    print(f"  published items: {write_summary['published_items']}")
    print(f"  rejected items: {write_summary['rejected_items']}")
    print(f"  pages: {len(bundle['pages']) + 1}")
    print(f"  product cards: {len(structured_assets['product_cards'])}")
    print(f"  module cards: {len(structured_assets['module_cards'])}")
    print(f"  equipment cards: {len(structured_assets['equipment_cards'])}")
    print(f"  interface cards: {len(structured_assets['interface_cards'])}")
    print(f"  templates: {len(structured_assets['section_templates'])}")
    print(f"  llm compiler: {llm_result['status']}")
if __name__ == "__main__":
    asyncio.run(main())
