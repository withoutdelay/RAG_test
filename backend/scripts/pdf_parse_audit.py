from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Iterable

from app.services.parsing.formula_ocr import FormulaOCRService
from app.services.parsing.parser import ParserService
from app.services.parsing.pdf_audit import build_pdf_audit_report, render_pdf_audit_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit document parsing quality based on extracted markdown.")
    parser.add_argument("paths", nargs="+", help="Document file paths or directories containing supported files.")
    parser.add_argument(
        "--output-dir",
        default="data/pdf_audits",
        help="Directory for generated markdown/json reports. Defaults to backend/data/pdf_audits.",
    )
    parser.add_argument(
        "--max-previews",
        type=int,
        default=8,
        help="Maximum chunk previews to include in the report.",
    )
    parser.add_argument(
        "--formula-ocr-backend",
        choices=("none", "pix2tex"),
        default=None,
        help="Optional local OCR backend for formula-like figure assets. Defaults to config/env.",
    )
    parser.add_argument(
        "--formula-ocr-max-assets",
        type=int,
        default=None,
        help="Maximum number of figure assets to send to formula OCR. Defaults to config/env.",
    )
    parser.add_argument(
        "--formula-ocr-max-regions-per-asset",
        type=int,
        default=None,
        help="Maximum number of cropped formula regions to try per asset. Defaults to config/env.",
    )
    return parser.parse_args()


async def audit_pdf(
    path: Path,
    *,
    output_dir: Path,
    max_previews: int,
    formula_ocr_backend: str | None,
    formula_ocr_max_assets: int | None,
    formula_ocr_max_regions_per_asset: int | None,
) -> dict[str, str]:
    parser = ParserService()
    parsed = await parser.parse_document(str(path))
    formula_ocr_service = FormulaOCRService(
        backend=formula_ocr_backend,
        max_assets=formula_ocr_max_assets,
        max_regions_per_asset=formula_ocr_max_regions_per_asset,
    )
    formula_ocr_results = formula_ocr_service.analyze(markdown=parsed.markdown, assets=parsed.assets)
    report = build_pdf_audit_report(
        file_path=str(path),
        markdown=parsed.markdown,
        metadata=parsed.metadata,
        assets=parsed.assets,
        formula_ocr_results=formula_ocr_results,
        max_previews=max_previews,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = path.stem.replace(" ", "_")
    markdown_path = output_dir / f"{safe_stem}.audit.md"
    json_path = output_dir / f"{safe_stem}.audit.json"
    markdown_path.write_text(render_pdf_audit_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "file": str(path.resolve()),
        "backend": str(report.get("parser_backend_used") or "unknown"),
        "markdown_chars": str(report.get("markdown_char_count") or 0),
        "tables": str(report.get("table_count") or 0),
        "images": str(report.get("image_count") or 0),
        "findings": str(len(report.get("findings") or [])),
        "formula_ocr_attempts": str(report.get("formula_ocr_attempt_count") or 0),
        "formula_ocr_successes": str(report.get("formula_ocr_success_count") or 0),
        "markdown_report": str(markdown_path.resolve()),
        "json_report": str(json_path.resolve()),
    }


def iter_pdf_paths(raw_paths: Iterable[str]) -> list[Path]:
    allowed_suffixes = {".pdf", ".doc", ".docx"}
    resolved: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            for suffix in ("*.pdf", "*.PDF", "*.doc", "*.DOC", "*.docx", "*.DOCX"):
                resolved.extend(sorted(item for item in path.rglob(suffix) if item.is_file()))
            continue
        if path.suffix.lower() not in allowed_suffixes:
            raise SystemExit(f"Only PDF/DOC/DOCX files are supported for this audit: {raw_path}")
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
    pdf_paths = iter_pdf_paths(args.paths)
    output_dir = Path(args.output_dir)
    failures: list[dict[str, str]] = []

    print(f"Auditing {len(pdf_paths)} document file(s) with ParserService...")
    for path in pdf_paths:
        try:
            result = await audit_pdf(
                path,
                output_dir=output_dir,
                max_previews=args.max_previews,
                formula_ocr_backend=args.formula_ocr_backend,
                formula_ocr_max_assets=args.formula_ocr_max_assets,
                formula_ocr_max_regions_per_asset=args.formula_ocr_max_regions_per_asset,
            )
            print("")
            print(f"Document: {result['file']}")
            print(f"  parser backend: {result['backend']}")
            print(f"  markdown chars: {result['markdown_chars']}")
            print(f"  tables/images: {result['tables']}/{result['images']}")
            print(f"  findings: {result['findings']}")
            print(f"  formula ocr attempts/successes: {result['formula_ocr_attempts']}/{result['formula_ocr_successes']}")
            print(f"  markdown report: {result['markdown_report']}")
            print(f"  json report: {result['json_report']}")
        except Exception as exc:
            failures.append({"file": str(path.resolve()), "error": f"{type(exc).__name__}: {exc}"})
            print("")
            print(f"Document: {path.resolve()}")
            print(f"  ERROR: {type(exc).__name__}: {exc}")

    if failures:
        print("")
        print(f"Completed with {len(failures)} failure(s):")
        for item in failures:
            print(f"  - {item['file']}")
            print(f"    {item['error']}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
