from __future__ import annotations

from typing import Any

from app.services.parsing.docling_parser import ParsedAsset


BAD_VISUAL_ROLES = {"page_furniture", "asset_fragment", "text_fragment"}
LIMITED_REUSE_VISUAL_ROLES = {"layout_drawing", "product_photo"}


def apply_asset_quality_gate(
    assets: list[ParsedAsset],
    *,
    confidence_threshold: float,
) -> list[ParsedAsset]:
    for asset in assets:
        asset.meta = _build_quality_metadata(asset=asset, confidence_threshold=confidence_threshold)
    return assets


def _build_quality_metadata(*, asset: ParsedAsset, confidence_threshold: float) -> dict[str, Any]:
    metadata = dict(asset.meta or {})
    quality_flags = _normalize_list(metadata.get("quality_flags"))
    reasons: list[str] = []
    status = "review_passed"
    score = 0.86

    visual_role = str(metadata.get("visual_role") or "").strip().lower()
    storage_fallback = bool(metadata.get("storage_fallback"))

    if metadata.get("preserve_in_vector_db") is False:
        status = "rejected"
        score = min(score, 0.05)
        reasons.append("not_preserved_for_vector_db")

    if asset.asset_type == "figure":
        if not asset.image_bytes:
            status = _max_status(status, "review_pending")
            score = min(score, 0.38)
            reasons.append("figure_without_image_bytes")
        if visual_role in BAD_VISUAL_ROLES:
            status = "rejected"
            score = min(score, 0.08)
            reasons.append(f"visual_role:{visual_role}")
            metadata["preserve_in_vector_db"] = False
        elif visual_role == "product_photo":
            status = _max_status(status, "review_pending")
            score = min(score, 0.46)
            reasons.append("product_photo_needs_section_specific_use")
        elif visual_role == "layout_drawing":
            status = _max_status(status, "review_pending")
            score = min(score, 0.58)
            reasons.append("layout_drawing_needs_section_specific_use")

    if asset.asset_type == "table":
        if storage_fallback and not bool(metadata.get("raw_table_markdown") or metadata.get("table_profile")):
            status = "rejected"
            score = min(score, 0.12)
            reasons.append("table_storage_fallback_without_structure")
            metadata["preserve_in_vector_db"] = False
        else:
            status = _max_status(status, "review_pending")
            score = min(score, 0.66)
            reasons.append("table_requires_reconstruction_or_review")

    review = metadata.get("llm_asset_review")
    if isinstance(review, dict):
        review_status = str(review.get("status") or "").strip().lower()
        confidence = _coerce_confidence(review.get("confidence"))
        suggested_role = str(review.get("suggested_visual_role") or "").strip().lower()
        if review_status == "failed":
            status = _max_status(status, "review_pending")
            score = min(score, 0.52)
            reasons.append("llm_asset_review_failed")
        elif review_status == "reviewed":
            if confidence and confidence < confidence_threshold:
                status = _max_status(status, "review_pending")
                score = min(score, 0.62)
                reasons.append("llm_asset_review_low_confidence")
            if suggested_role in BAD_VISUAL_ROLES and confidence >= confidence_threshold:
                status = "rejected"
                score = min(score, 0.06)
                reasons.append(f"llm_rejected_as:{suggested_role}")
                metadata["visual_role"] = suggested_role
                metadata["preserve_in_vector_db"] = False
            elif suggested_role in LIMITED_REUSE_VISUAL_ROLES and confidence >= confidence_threshold:
                status = _max_status(status, "review_pending")
                score = min(score, 0.48 if suggested_role == "product_photo" else 0.58)
                reasons.append(f"llm_limited_reuse_as:{suggested_role}")
                metadata["visual_role"] = suggested_role
            elif suggested_role == "engineering_figure" and confidence >= confidence_threshold and status != "rejected":
                score = max(score, 0.9)

    semantic_summary = metadata.get("semantic_summary")
    if isinstance(semantic_summary, dict) and str(semantic_summary.get("status") or "") in {"summarized", "reviewed"}:
        summary_confidence = _coerce_confidence(semantic_summary.get("confidence"))
        if bool(semantic_summary.get("review_required")):
            status = _max_status(status, "review_pending")
            score = min(score, 0.62)
            reasons.append("semantic_summary_review_required")
        if summary_confidence and summary_confidence < 0.45:
            status = _max_status(status, "review_pending")
            score = min(score, 0.54)
            reasons.append("semantic_summary_low_confidence")

    if status != "review_passed":
        metadata["review_required"] = True

    for reason in reasons:
        if reason not in quality_flags:
            quality_flags.append(reason)

    metadata["asset_audit_status"] = status
    metadata["asset_quality_score"] = round(score, 4)
    metadata["asset_audit_reasons"] = reasons
    metadata["quality_flags"] = quality_flags
    return metadata


def _max_status(current: str, candidate: str) -> str:
    order = {"review_passed": 0, "review_pending": 1, "rejected": 2}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized
