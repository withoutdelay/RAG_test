from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.knowledge import compile_knowledge_wiki


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
    return parser.parse_args()


def main() -> None:
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
    prune_stale_wiki_pages(output_dir=output_dir, active_page_paths=set(bundle["pages"]))

    for relative_path, content in bundle["pages"].items():
        target_path = output_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(bundle["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    structured_assets = bundle["structured_assets"]
    (output_dir / "glossary.json").write_text(
        json.dumps(structured_assets["glossary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "product_cards.json").write_text(
        json.dumps(structured_assets["product_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "module_cards.json").write_text(
        json.dumps(structured_assets["module_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "equipment_cards.json").write_text(
        json.dumps(structured_assets["equipment_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "interface_cards.json").write_text(
        json.dumps(structured_assets["interface_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "section_templates.json").write_text(
        json.dumps(structured_assets["section_templates"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "forbidden_phrases.json").write_text(
        json.dumps(structured_assets["forbidden_phrases"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_path = output_dir / "log.md"
    previous = log_path.read_text(encoding="utf-8") if log_path.exists() else "# AI Wiki Log\n\n"
    if not previous.endswith("\n"):
        previous += "\n"
    log_path.write_text(previous + "\n" + bundle["log_entry"], encoding="utf-8")

    print(f"Compiled AI wiki to {output_dir.resolve()}")
    print(f"  pages: {len(bundle['pages']) + 1}")
    print(f"  product cards: {len(structured_assets['product_cards'])}")
    print(f"  module cards: {len(structured_assets['module_cards'])}")
    print(f"  equipment cards: {len(structured_assets['equipment_cards'])}")
    print(f"  interface cards: {len(structured_assets['interface_cards'])}")
    print(f"  templates: {len(structured_assets['section_templates'])}")


def prune_stale_wiki_pages(*, output_dir: Path, active_page_paths: set[str]) -> None:
    keep_paths = set(active_page_paths)
    keep_paths.add("log.md")
    for path in output_dir.rglob("*.md"):
        relative_path = path.relative_to(output_dir).as_posix()
        if relative_path in keep_paths:
            continue
        path.unlink(missing_ok=True)
    for directory in sorted((path for path in output_dir.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


if __name__ == "__main__":
    main()
