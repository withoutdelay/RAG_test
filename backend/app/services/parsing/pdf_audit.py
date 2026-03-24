from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.services.parsing.document_profile import build_document_profile
from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.formula_candidates import extract_formula_candidates
from app.services.parsing.formula_ocr import FormulaOCRResult
from app.services.vectorstore.chunker import Chunker


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIGURE_KEYWORD_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)
SUSPICIOUS_GARBLED_PATTERN = re.compile(r"(�|Ã|Ð|Ñ|þ|ÿ)")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def build_pdf_audit_report(
    *,
    file_path: str,
    markdown: str,
    metadata: dict[str, Any] | None = None,
    assets: list[ParsedAsset] | None = None,
    formula_ocr_results: list[FormulaOCRResult] | None = None,
    max_previews: int = 8,
) -> dict[str, Any]:
    metadata = metadata or {}
    assets = assets or []
    formula_ocr_results = formula_ocr_results or []
    lines = markdown.splitlines()
    nonempty_lines = [line for line in lines if line.strip()]
    headings = _extract_headings(lines)
    chunker = Chunker(max_chars=800)
    chunks = chunker.split(markdown)

    suspicious_lines = _collect_suspicious_lines(lines)
    formula_candidates = extract_formula_candidates(markdown)
    figure_candidates = _collect_matching_lines(lines, FIGURE_KEYWORD_PATTERN)
    control_char_count = len(CONTROL_CHAR_PATTERN.findall(markdown))
    table_blocks = sum(1 for chunk in chunks if chunk.chunk_type == "TABLE")
    large_visual_assets = _summarize_large_visual_assets(assets)
    garbled_formula_count = sum(1 for item in formula_candidates if item.garbled)
    formula_ocr_summary = _summarize_formula_ocr_results(formula_ocr_results)
    profiled_metadata = {
        **metadata,
        "format": metadata.get("format", Path(file_path).suffix.lstrip(".")),
        "table_count": metadata.get("table_count", table_blocks),
        "image_count": metadata.get("image_count", 0),
    }
    document_profile = build_document_profile(
        markdown=markdown,
        metadata=profiled_metadata,
        assets=assets,
    )

    findings = _build_findings(
        metadata=profiled_metadata,
        markdown=markdown,
        suspicious_lines=suspicious_lines,
        math_candidates=formula_candidates,
        figure_candidates=figure_candidates,
        control_char_count=control_char_count,
        large_visual_assets=large_visual_assets,
        garbled_formula_count=garbled_formula_count,
        formula_ocr_summary=formula_ocr_summary,
        document_profile=document_profile,
    )

    return {
        "file_name": Path(file_path).name,
        "file_path": str(Path(file_path).resolve()),
        "parser_backend_requested": metadata.get("parser_backend_requested"),
        "parser_backend_used": metadata.get("parser_backend_used"),
        "formula_ocr_backend_requested": formula_ocr_summary["backend_requested"],
        "formula_ocr_backend_used": formula_ocr_summary["backend_used"],
        "format": metadata.get("format"),
        "markdown_char_count": len(markdown),
        "line_count": len(lines),
        "nonempty_line_count": len(nonempty_lines),
        "heading_count": len(headings),
        "heading_levels": _count_heading_levels(headings),
        "table_count": metadata.get("table_count", table_blocks),
        "image_count": metadata.get("image_count", 0),
        "chunk_count": len(chunks),
        "math_candidate_count": len(formula_candidates),
        "garbled_formula_count": garbled_formula_count,
        "figure_keyword_count": len(figure_candidates),
        "control_char_count": control_char_count,
        "suspicious_line_count": len(suspicious_lines),
        "large_visual_asset_count": len(large_visual_assets),
        "formula_ocr_attempt_count": formula_ocr_summary["attempt_count"],
        "formula_ocr_success_count": formula_ocr_summary["success_count"],
        "formula_ocr_review_count": formula_ocr_summary["review_count"],
        "document_profile": {
            "name": document_profile.name,
            "confidence": document_profile.confidence,
            "reasons": list(document_profile.reasons),
            "metrics": document_profile.metrics,
        },
        "ingestion_recommendation": document_profile.ingestion_recommendation,
        "high_risk_content_flags": list(document_profile.high_risk_content_flags),
        "findings": findings,
        "samples": {
            "headings": headings[: min(12, len(headings))],
            "suspicious_lines": suspicious_lines[:8],
            "math_candidates": [
                {
                    "line_number": item.line_number,
                    "text": _trim(item.text, 220),
                    "latex_hint": _trim(item.latex_hint, 220) if item.latex_hint else None,
                    "garbled": item.garbled,
                }
                for item in formula_candidates[:8]
            ],
            "figure_candidates": figure_candidates[:8],
            "large_visual_assets": large_visual_assets[:8],
            "formula_ocr_results": formula_ocr_summary["samples"][:8],
            "chunk_previews": [
                {
                    "chunk_index": chunk.chunk_index,
                    "chunk_type": chunk.chunk_type,
                    "heading_path": chunk.heading_path,
                    "preview": _trim(chunk.content, 240),
                }
                for chunk in chunks[:max_previews]
            ],
        },
    }


def render_pdf_audit_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# PDF Parse Audit: {report['file_name']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- File: `{report['file_path']}`")
    lines.append(
        f"- Parser backend: requested `{report.get('parser_backend_requested')}`, used `{report.get('parser_backend_used')}`"
    )
    document_profile = report.get("document_profile") or {}
    lines.append(
        f"- Document profile: `{document_profile.get('name')}` (confidence `{document_profile.get('confidence')}`)"
    )
    lines.append(f"- Ingestion recommendation: `{report.get('ingestion_recommendation')}`")
    high_risk_flags = report.get("high_risk_content_flags") or []
    lines.append(
        "- High-risk flags: "
        + (", ".join(f"`{item}`" for item in high_risk_flags) if high_risk_flags else "`none`")
    )
    lines.append(f"- Markdown chars: `{report['markdown_char_count']}`")
    lines.append(f"- Headings: `{report['heading_count']}`")
    lines.append(f"- Tables: `{report['table_count']}`")
    lines.append(f"- Images: `{report['image_count']}`")
    lines.append(f"- Chunks: `{report['chunk_count']}`")
    lines.append(f"- Math-like lines: `{report['math_candidate_count']}`")
    lines.append(
        f"- Formula OCR: requested `{report.get('formula_ocr_backend_requested')}`, used `{report.get('formula_ocr_backend_used')}`, attempts `{report.get('formula_ocr_attempt_count')}`, successes `{report.get('formula_ocr_success_count')}`"
    )
    lines.append(f"- Figure/diagram keyword hits: `{report['figure_keyword_count']}`")
    lines.append(f"- Suspicious lines: `{report['suspicious_line_count']}`")
    lines.append("")
    lines.append("## Findings")
    lines.append("")

    findings = report.get("findings") or []
    if findings:
        for finding in findings:
            lines.append(f"- [{finding['level'].upper()}] {finding['message']}")
    else:
        lines.append("- No obvious parsing risk signals were detected in the extracted markdown.")

    lines.append("")
    lines.append("## Quality Gate")
    lines.append("")
    lines.append(f"- Recommendation: `{report.get('ingestion_recommendation')}`")
    lines.append(
        f"- Profile reasons: "
        + (
            ", ".join(f"`{item}`" for item in (document_profile.get("reasons") or []))
            if document_profile.get("reasons")
            else "`none`"
        )
    )
    lines.append(
        f"- High-risk flags: "
        + (", ".join(f"`{item}`" for item in high_risk_flags) if high_risk_flags else "`none`")
    )

    lines.append("")
    lines.append("## Heading Samples")
    lines.append("")
    heading_samples = report.get("samples", {}).get("headings") or []
    if heading_samples:
        for item in heading_samples:
            lines.append(f"- L{item['line_number']}: `{'#' * item['level']} {item['text']}`")
    else:
        lines.append("- No headings were extracted.")

    lines.append("")
    lines.append("## Suspicious Lines")
    lines.append("")
    suspicious = report.get("samples", {}).get("suspicious_lines") or []
    if suspicious:
        for item in suspicious:
            lines.append(f"- L{item['line_number']}: `{item['text']}`")
    else:
        lines.append("- No suspicious lines sampled.")

    lines.append("")
    lines.append("## Math-like Samples")
    lines.append("")
    math_candidates = report.get("samples", {}).get("math_candidates") or []
    if math_candidates:
        for item in math_candidates:
            latex_hint = item.get("latex_hint")
            suffix = " [garbled]" if item.get("garbled") else ""
            if latex_hint:
                lines.append(f"- L{item['line_number']}: `{item['text']}` -> `{latex_hint}`{suffix}")
            else:
                lines.append(f"- L{item['line_number']}: `{item['text']}`{suffix}")
    else:
        lines.append("- No math-like lines sampled.")

    lines.append("")
    lines.append("## Formula OCR Samples")
    lines.append("")
    formula_ocr_results = report.get("samples", {}).get("formula_ocr_results") or []
    if formula_ocr_results:
        for item in formula_ocr_results:
            prefix = f"P{item['page_no']}" if item.get("page_no") is not None else f"L{item['line_number']}"
            body = f"{prefix} `{item['status']}`"
            crop_bbox = item.get("crop_bbox")
            if crop_bbox:
                body = f"{body} crop={crop_bbox['left']},{crop_bbox['top']},{crop_bbox['right']},{crop_bbox['bottom']}"
            if item.get("latex"):
                body = f"{body} -> `{item['latex']}`"
            if item.get("quality"):
                body = f"{body} ({item['quality']})"
            if item.get("reason"):
                body = f"{body} - {item['reason']}"
            lines.append(f"- {body}")
    else:
        lines.append("- No formula OCR attempts were recorded.")

    lines.append("")
    lines.append("## Figure/Diagram Keyword Samples")
    lines.append("")
    figure_candidates = report.get("samples", {}).get("figure_candidates") or []
    if figure_candidates:
        for item in figure_candidates:
            lines.append(f"- L{item['line_number']}: `{item['text']}`")
    else:
        lines.append("- No figure or diagram keywords sampled.")

    lines.append("")
    lines.append("## Large Visual Assets")
    lines.append("")
    large_visual_assets = report.get("samples", {}).get("large_visual_assets") or []
    if large_visual_assets:
        for item in large_visual_assets:
            lines.append(
                f"- P{item['page_no']} `{item['asset_type']}` {item['size']} heading=`{item['heading_path'] or 'N/A'}` context=`{item['context_before'] or ''}`"
            )
    else:
        lines.append("- No large visual assets sampled.")

    lines.append("")
    lines.append("## Chunk Previews")
    lines.append("")
    for item in report.get("samples", {}).get("chunk_previews") or []:
        heading = item.get("heading_path") or "NO_HEADING"
        lines.append(f"- Chunk {item['chunk_index']} [{item['chunk_type']}] `{heading}`")
        lines.append(f"  `{item['preview']}`")

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- This audit only inspects extracted markdown and parser metadata.")
    lines.append("- It does not prove formulas, superscripts/subscripts, or engineering figures were preserved faithfully.")
    lines.append("- If the file contains many waveforms, schematics, bitmap plots, or circuit drawings, manual visual comparison is still required.")
    return "\n".join(lines)


def _extract_headings(lines: list[str]) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        headings.append(
            {
                "line_number": index,
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
            }
        )
    return headings


def _count_heading_levels(headings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in headings:
        level = str(item["level"])
        counts[level] = counts.get(level, 0) + 1
    return counts


def _collect_matching_lines(lines: list[str], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if pattern.search(stripped):
            matches.append({"line_number": index, "text": _trim(stripped, 220)})
    return matches


def _collect_suspicious_lines(lines: list[str]) -> list[dict[str, Any]]:
    suspicious: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        reasons: list[str] = []
        if SUSPICIOUS_GARBLED_PATTERN.search(stripped):
            reasons.append("garbled_characters")
        if CONTROL_CHAR_PATTERN.search(stripped):
            reasons.append("control_characters")
        if len(stripped) > 220 and stripped.count(" ") < 3:
            reasons.append("very_long_dense_line")

        if reasons:
            suspicious.append(
                {
                    "line_number": index,
                    "text": _trim(stripped, 220),
                    "reasons": reasons,
                }
            )
    return suspicious


def _build_findings(
    *,
    metadata: dict[str, Any],
    markdown: str,
    suspicious_lines: list[dict[str, Any]],
    math_candidates: list[dict[str, Any]],
    figure_candidates: list[dict[str, Any]],
    control_char_count: int,
    large_visual_assets: list[dict[str, Any]],
    garbled_formula_count: int,
    formula_ocr_summary: dict[str, Any],
    document_profile: Any,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    parser_backend_used = str(metadata.get("parser_backend_used") or "unknown")
    image_count = int(metadata.get("image_count") or 0)

    if parser_backend_used != "docling":
        findings.append(
            {
                "level": "high",
                "message": "PDF did not stay on the Docling path. Parsed markdown is likely unreliable for engineering documents.",
            }
        )

    if document_profile.ingestion_recommendation == "asset_only_review":
        findings.append(
            {
                "level": "high",
                "message": "Document profile suggests this PDF should stay out of the main vector path and be handled as asset-first review material.",
            }
        )
    elif document_profile.ingestion_recommendation == "text_primary_with_asset_review":
        findings.append(
            {
                "level": "info",
                "message": "Document profile suggests a text-primary path with asset review. Main text can be indexed, but figures, formulas, and complex tables need separate handling.",
            }
        )

    if len(markdown.strip()) < 400:
        findings.append(
            {
                "level": "medium",
                "message": "Extracted markdown is very short. Large portions of the PDF may not have been parsed into usable text.",
            }
        )

    if suspicious_lines:
        findings.append(
            {
                "level": "high",
                "message": f"Detected {len(suspicious_lines)} suspicious lines with garbled characters or dense unreadable text.",
            }
        )

    if control_char_count:
        findings.append(
            {
                "level": "high",
                "message": "Control characters were found in extracted markdown, which usually indicates decoding or layout conversion damage.",
            }
        )

    if figure_candidates and image_count == 0:
        findings.append(
            {
                "level": "high",
                "message": "The document mentions figures or diagrams, but no image assets were extracted separately. Waveforms and circuit drawings likely need manual review.",
            }
        )
    elif large_visual_assets:
        findings.append(
            {
                "level": "info",
                "message": f"Extracted {len(large_visual_assets)} large visual assets that are likely engineering figures, waveforms, or schematics.",
            }
        )

    if math_candidates:
        findings.append(
            {
                "level": "medium",
                "message": "Math-like lines were detected. Formula formatting, superscripts, and subscripts should be visually checked against the source PDF.",
            }
        )

    if garbled_formula_count:
        findings.append(
            {
                "level": "high",
                "message": f"Detected {garbled_formula_count} formula-like lines with garbled characters. These likely need OCR or manual correction before generation.",
            }
        )

    if formula_ocr_summary["backend_requested"] == "pix2tex" and formula_ocr_summary["attempt_count"] == 0:
        findings.append(
            {
                "level": "medium",
                "message": "Formula OCR was enabled, but no suitable figure assets were selected for OCR. The current PDF may still need manual formula review.",
            }
        )
    elif formula_ocr_summary["backend_requested"] == "pix2tex" and formula_ocr_summary["success_count"] == 0:
        findings.append(
            {
                "level": "medium",
                "message": "Formula OCR attempted local pix2tex inference, but no usable LaTeX result was recovered yet.",
            }
        )
    elif formula_ocr_summary["success_count"]:
        findings.append(
            {
                "level": "info",
                "message": f"Recovered {formula_ocr_summary['success_count']} formula OCR result(s) from extracted figure assets. These still require visual verification.",
            }
        )

    if not findings:
        findings.append(
            {
                "level": "info",
                "message": "No obvious parser regressions were detected from markdown-level signals alone.",
            }
        )

    return findings


def _trim(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


def _summarize_large_visual_assets(assets: list[ParsedAsset]) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for asset in assets:
        asset_meta = asset.meta if isinstance(asset.meta, dict) else {}
        width = int(asset_meta.get("width") or 0)
        height = int(asset_meta.get("height") or 0)
        visual_role = str(asset_meta.get("visual_role") or "")
        if asset.asset_type == "table":
            continue
        if visual_role == "page_furniture":
            continue
        if visual_role == "engineering_figure":
            include = True
        else:
            include = width >= 280 or height >= 180
        if not include:
            continue
        if width < 280 and height < 180:
            if visual_role != "engineering_figure":
                continue
        summarized.append(
            {
                "page_no": asset.page_no,
                "asset_type": asset.asset_type,
                "size": f"{width}x{height}",
                "heading_path": asset.heading_path,
                "visual_role": visual_role or None,
                "context_before": _trim(asset.context_before or "", 120) if asset.context_before else None,
                "context_after": _trim(asset.context_after or "", 120) if asset.context_after else None,
                "bbox": asset.bbox,
                "source_ref": asset.source_ref,
            }
        )
    return summarized


def _summarize_formula_ocr_results(results: list[FormulaOCRResult]) -> dict[str, Any]:
    backend_requested = "none"
    backend_used = "none"
    if results:
        backend_requested = results[0].backend_requested
        backend_used = results[0].backend_used
        for item in results:
            if item.backend_used != "none":
                backend_used = item.backend_used
                break

    summarized = [item.to_dict() for item in results]
    return {
        "backend_requested": backend_requested,
        "backend_used": backend_used,
        "attempt_count": sum(1 for item in results if item.source_type == "asset"),
        "success_count": sum(1 for item in results if item.status == "succeeded"),
        "review_count": sum(1 for item in results if item.status == "manual_review"),
        "samples": summarized,
    }
