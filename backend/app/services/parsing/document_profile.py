from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.formula_candidates import extract_formula_candidates


FIGURE_KEYWORD_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)
PAGE_FURNITURE_PATTERN = re.compile(r"(版本|页码|总页数|DAYU ELECTRIC|项目名称|买方|卖方)")


@dataclass(frozen=True)
class DocumentProfileResult:
    name: str
    confidence: float
    reasons: tuple[str, ...]
    ingestion_recommendation: str
    high_risk_content_flags: tuple[str, ...]
    metrics: dict[str, int | float]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "document_profile": {
                "name": self.name,
                "confidence": self.confidence,
                "reasons": list(self.reasons),
                "metrics": self.metrics,
            },
            "ingestion_recommendation": self.ingestion_recommendation,
            "high_risk_content_flags": list(self.high_risk_content_flags),
        }


def build_document_profile(
    *,
    markdown: str,
    metadata: dict[str, Any] | None = None,
    assets: list[ParsedAsset] | None = None,
) -> DocumentProfileResult:
    metadata = metadata or {}
    assets = assets or []

    lines = markdown.splitlines()
    nonempty_lines = [line.strip() for line in lines if line.strip()]
    markdown_char_count = len(markdown)
    nonempty_line_count = len(nonempty_lines)
    parser_backend_used = str(metadata.get("parser_backend_used") or "unknown")
    file_format = str(metadata.get("format") or "").lower()
    table_count = int(metadata.get("table_count") or 0)
    image_count = int(metadata.get("image_count") or 0)
    table_asset_count = sum(1 for asset in assets if asset.asset_type == "table")
    figure_keyword_count = sum(1 for line in nonempty_lines if FIGURE_KEYWORD_PATTERN.search(line))
    formula_candidates = extract_formula_candidates(markdown)
    math_candidate_count = len(formula_candidates)
    garbled_formula_count = sum(1 for item in formula_candidates if item.garbled)
    front_matter_noise_count = sum(
        1 for line in nonempty_lines[:8] if PAGE_FURNITURE_PATTERN.search(line)
    )
    large_visual_asset_count = sum(1 for asset in assets if _is_large_visual_asset(asset))
    text_density = round(markdown_char_count / max(nonempty_line_count, 1), 2)

    high_risk_flags: list[str] = []
    reasons: list[str] = []

    if file_format == "pdf" and parser_backend_used != "docling":
        high_risk_flags.append("fallback_parser_used")
        reasons.append("pdf_not_kept_on_docling_path")
    if front_matter_noise_count:
        high_risk_flags.append("front_matter_noise")
        reasons.append("front_matter_leaked_into_text")
    if large_visual_asset_count:
        high_risk_flags.append("engineering_figures_present")
        reasons.append("engineering_figures_or_waveforms_detected")
    if table_asset_count:
        high_risk_flags.append("preserved_table_assets_present")
        reasons.append("table_assets_preserved_for_reconstruction")
    if math_candidate_count:
        high_risk_flags.append("formula_like_content")
        reasons.append("formula_like_content_detected")
    if garbled_formula_count:
        high_risk_flags.append("garbled_formula_text")
        reasons.append("garbled_formula_text_detected")
    if figure_keyword_count >= 2:
        high_risk_flags.append("diagram_reference_dense")
        reasons.append("diagram_or_schematic_references_detected")

    scanned_like = _is_scanned_like_pdf(
        file_format=file_format,
        parser_backend_used=parser_backend_used,
        markdown_char_count=markdown_char_count,
        nonempty_line_count=nonempty_line_count,
        image_count=image_count,
        large_visual_asset_count=large_visual_asset_count,
        text_density=text_density,
        table_count=table_count,
    )

    mixed_engineering = (
        file_format == "pdf"
        and not scanned_like
        and (
            large_visual_asset_count >= 1
            or figure_keyword_count >= 2
            or table_count >= 3
            or table_asset_count >= 1
            or math_candidate_count >= 4
        )
    )

    if scanned_like:
        profile_name = "scanned_pdf"
        ingestion_recommendation = "asset_only_review"
        confidence = _calculate_confidence(base=0.72, signals=4 + bool(large_visual_asset_count))
        reasons.extend(
            [
                "text_is_too_sparse_for_pdf",
                "images_or_visual_assets_dominate_the_document",
            ]
        )
        if "scanned_content_likely" not in high_risk_flags:
            high_risk_flags.append("scanned_content_likely")
    elif mixed_engineering:
        profile_name = "mixed_engineering_pdf"
        ingestion_recommendation = "text_primary_with_asset_review"
        confidence = _calculate_confidence(
            base=0.76,
            signals=int(large_visual_asset_count > 0)
            + int(figure_keyword_count >= 2)
            + int(table_count >= 3 or table_asset_count >= 1)
            + int(math_candidate_count >= 4),
        )
        reasons.append("text_and_high_risk_assets_are_both_present")
    else:
        profile_name = "text_digital"
        ingestion_recommendation = "main_vector_ready"
        confidence = 0.9 if file_format in {"md", "txt"} else _calculate_confidence(base=0.82, signals=1)
        reasons.append("text_content_is_dense_enough_for_primary_retrieval")

    metrics = {
        "markdown_char_count": markdown_char_count,
        "nonempty_line_count": nonempty_line_count,
        "table_count": table_count,
        "image_count": image_count,
        "table_asset_count": table_asset_count,
        "large_visual_asset_count": large_visual_asset_count,
        "math_candidate_count": math_candidate_count,
        "garbled_formula_count": garbled_formula_count,
        "figure_keyword_count": figure_keyword_count,
        "front_matter_noise_count": front_matter_noise_count,
        "text_density": text_density,
    }

    return DocumentProfileResult(
        name=profile_name,
        confidence=round(confidence, 2),
        reasons=tuple(dict.fromkeys(reasons)),
        ingestion_recommendation=ingestion_recommendation,
        high_risk_content_flags=tuple(dict.fromkeys(high_risk_flags)),
        metrics=metrics,
    )


def _is_large_visual_asset(asset: ParsedAsset) -> bool:
    if asset.asset_type == "table":
        return False
    asset_meta = asset.meta if isinstance(asset.meta, dict) else {}
    visual_role = str(asset_meta.get("visual_role") or "")
    width = int(asset_meta.get("width") or 0)
    height = int(asset_meta.get("height") or 0)
    if visual_role == "page_furniture":
        return False
    if visual_role == "engineering_figure":
        return True
    return width >= 280 or height >= 180


def _is_scanned_like_pdf(
    *,
    file_format: str,
    parser_backend_used: str,
    markdown_char_count: int,
    nonempty_line_count: int,
    image_count: int,
    large_visual_asset_count: int,
    text_density: float,
    table_count: int,
) -> bool:
    if file_format != "pdf":
        return False
    if table_count >= 2 and markdown_char_count >= 120:
        return False
    if parser_backend_used != "docling" and markdown_char_count < 1200:
        return True
    if markdown_char_count < 900 and image_count >= 1:
        return True
    if nonempty_line_count < 18 and (image_count >= 3 or large_visual_asset_count >= 2):
        return True
    if markdown_char_count < 1800 and image_count >= 8 and text_density < 45:
        return True
    return False


def _calculate_confidence(*, base: float, signals: int) -> float:
    return min(0.97, base + signals * 0.04)
