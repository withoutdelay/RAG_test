from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


BLOCKING_QUALITY_FLAGS = {
    "missing_source_evidence",
    "cross_section_contamination",
    "ocr_noise_high",
    "conflicts_with_published",
    "over_generic_term",
    "asset_type_uncertain",
    "source_section_missing",
}
LEGACY_BLOCKING_FLAG_MAP = {
    "blocking:missing_evidence": "missing_source_evidence",
    "blocking:incomplete_evidence": "source_section_missing",
    "blocking:published_conflict": "conflicts_with_published",
    "blocking:unknown_visual_audit_type": "asset_type_uncertain",
}
GENERIC_TERMS = {
    "系统",
    "方案",
    "设备",
    "柜",
    "装置",
    "产品",
    "模块",
    "项目",
    "工程",
    "图纸",
    "示意图",
    "模板",
}
BUSINESS_OR_DELIVERY_TERMS = (
    "培训",
    "售后",
    "维保",
    "服务承诺",
    "实施进度",
    "施工计划",
    "供货周期",
    "交付计划",
    "商务",
    "报价",
    "付款",
    "质保",
    "公司简介",
)
TECHNICAL_TERMS = (
    "主回路",
    "拓扑",
    "控制",
    "联锁",
    "接口",
    "通信",
    "变频",
    "电机",
    "变压器",
    "参数",
    "信号",
    "柜体",
    "保护",
)
GARBLED_CHARS = set("�□■◆◇●○�")
ASCII_NOISE_PATTERN = re.compile(r"(?:[A-Za-z]{1,2}[-_/]){4,}|[?？]{3,}|[\\|]{3,}")


@dataclass(frozen=True, slots=True)
class WikiQualityResult:
    quality_flags: list[str]
    quality_score: float
    status: str


def evaluate_wiki_item_quality(
    *,
    item_type: str,
    canonical_name: str,
    aliases: Iterable[str],
    evidence: Iterable[dict[str, Any]],
    source_documents: Iterable[str],
    existing_item: Mapping[str, Any] | None = None,
    reuse_hit_count: int | None = None,
    base_flags: Iterable[str] | None = None,
) -> WikiQualityResult:
    evidence_list = [item for item in evidence if isinstance(item, dict)]
    source_document_list = _dedupe_keep_order(str(item) for item in source_documents)
    flags = _quality_flags_for_item(
        item_type=item_type,
        canonical_name=canonical_name,
        aliases=list(aliases),
        evidence=evidence_list,
        source_documents=source_document_list,
        existing_item=existing_item,
        reuse_hit_count=reuse_hit_count,
        base_flags=base_flags,
    )
    score = quality_score_for_item(
        item_type=item_type,
        evidence=evidence_list,
        source_documents=source_document_list,
        quality_flags=flags,
        reuse_hit_count=reuse_hit_count,
    )
    return WikiQualityResult(
        quality_flags=flags,
        quality_score=score,
        status=status_for_quality(quality_score=score, quality_flags=flags),
    )


def can_publish_wiki_item(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    quality_score = _safe_float(item.get("quality_score"))
    return (
        status in {"auto_approved", "published"}
        and quality_score >= 0.82
        and validate_wiki_item_schema(item)
        and not item_has_blocking_quality_flags(item)
        and evidence_sources_still_exist(item)
    )


def item_can_enter_generation(item: Mapping[str, Any]) -> bool:
    return (
        str(item.get("status") or "").strip().lower() == "published"
        and validate_wiki_item_schema(item)
        and not item_has_blocking_quality_flags(item)
        and evidence_sources_still_exist(item)
    )


def item_has_blocking_quality_flags(item: Mapping[str, Any]) -> bool:
    return any(is_blocking_quality_flag(flag) for flag in item.get("quality_flags") or [])


def is_blocking_quality_flag(flag: Any) -> bool:
    normalized = str(flag or "").strip()
    return normalized in BLOCKING_QUALITY_FLAGS or normalized.startswith("blocking:")


def normalize_quality_flags(flags: Iterable[Any]) -> list[str]:
    normalized_flags: list[str] = []
    for flag in flags:
        value = str(flag or "").strip()
        if not value:
            continue
        value = LEGACY_BLOCKING_FLAG_MAP.get(value, value)
        if value not in normalized_flags:
            normalized_flags.append(value)
    return normalized_flags


def validate_wiki_item_schema(item: Mapping[str, Any]) -> bool:
    if not str(item.get("item_id") or "").strip():
        return False
    if str(item.get("item_type") or "").strip() not in {
        "term_alias",
        "product_family",
        "section_template",
        "asset_type_rule",
    }:
        return False
    if not str(item.get("canonical_name") or "").strip():
        return False
    try:
        quality_score = float(item.get("quality_score"))
    except (TypeError, ValueError):
        return False
    if not 0.0 <= quality_score <= 1.0:
        return False
    if not isinstance(item.get("quality_flags"), list):
        return False
    if not isinstance(item.get("evidence"), list):
        return False
    return True


def evidence_sources_still_exist(item: Mapping[str, Any]) -> bool:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    if not evidence:
        return False
    return all(is_publishable_evidence_item(evidence_item) for evidence_item in evidence)


def is_publishable_evidence_item(evidence_item: Any) -> bool:
    if not isinstance(evidence_item, Mapping):
        return False
    if not str(evidence_item.get("sample_id") or "").strip():
        return False
    if not str(evidence_item.get("raw_document_id") or "").strip():
        return False
    if not str(evidence_item.get("source_section_id") or "").strip():
        return False
    if not str(evidence_item.get("heading_path") or "").strip():
        return False
    return bool(
        str(evidence_item.get("evidence_quote") or "").strip()
        or str(evidence_item.get("asset_id") or "").strip()
    )


def quality_score_for_item(
    *,
    item_type: str,
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    quality_flags: list[str],
    reuse_hit_count: int | None = None,
) -> float:
    if "missing_source_evidence" in quality_flags:
        return 0.0
    if "over_generic_term" in quality_flags:
        return 0.38
    if "source_section_missing" in quality_flags:
        return 0.52
    base = 0.64
    base += min(0.22, len(evidence) * 0.055)
    base += min(0.12, len(source_documents) * 0.04)
    base += min(0.05, max(0, reuse_hit_count or 0) * 0.01)
    if item_type == "term_alias" and len(evidence) < 2:
        base -= 0.08
    if "low_source_document_coverage" in quality_flags:
        base -= 0.01
    if "low_reuse_value" in quality_flags:
        base -= 0.02
    if any(flag in quality_flags for flag in ("conflicts_with_published", "cross_section_contamination", "ocr_noise_high")):
        base = min(base, 0.74)
    if "asset_type_uncertain" in quality_flags:
        base = min(base, 0.68)
    return round(max(0.0, min(0.95, base)), 4)


def status_for_quality(*, quality_score: float, quality_flags: Iterable[str]) -> str:
    flags = set(quality_flags)
    if "missing_source_evidence" in flags or "over_generic_term" in flags:
        return "rejected"
    if any(is_blocking_quality_flag(flag) for flag in flags):
        return "review_required"
    if quality_score >= 0.82:
        return "auto_approved"
    if quality_score >= 0.60:
        return "review_required"
    return "rejected"


def _quality_flags_for_item(
    *,
    item_type: str,
    canonical_name: str,
    aliases: list[str],
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    existing_item: Mapping[str, Any] | None,
    reuse_hit_count: int | None,
    base_flags: Iterable[str] | None,
) -> list[str]:
    flags = normalize_quality_flags(base_flags or [])
    if not evidence or not source_documents:
        flags.append("missing_source_evidence")
    if evidence and any(not is_publishable_evidence_item(item) for item in evidence):
        flags.append("source_section_missing")
    if not source_documents:
        flags.append("low_source_document_coverage")
    if len(source_documents) < 2:
        flags.append("single_source")
    if reuse_hit_count is not None and reuse_hit_count <= 0:
        flags.append("low_reuse_value")
    if _published_item_conflicts(existing_item=existing_item, canonical_name=canonical_name, aliases=aliases):
        flags.append("conflicts_with_published")
    if _is_over_generic_term(canonical_name):
        flags.append("over_generic_term")
    if _has_high_ocr_noise(evidence):
        flags.append("ocr_noise_high")
    if _has_cross_section_contamination(item_type=item_type, evidence=evidence):
        flags.append("cross_section_contamination")
    if _asset_type_uncertain(item_type=item_type, canonical_name=canonical_name, aliases=aliases, evidence=evidence):
        flags.append("asset_type_uncertain")
    return _dedupe_keep_order(flags)


def _published_item_conflicts(
    *,
    existing_item: Mapping[str, Any] | None,
    canonical_name: str,
    aliases: list[str],
) -> bool:
    if not existing_item:
        return False
    existing_name = str(existing_item.get("canonical_name") or "").strip()
    if existing_name and existing_name != canonical_name:
        return True
    existing_aliases = {str(item).strip() for item in (existing_item.get("aliases") or []) if str(item).strip()}
    new_aliases = {str(item).strip() for item in aliases if str(item).strip()}
    return bool(existing_aliases and new_aliases and existing_aliases != new_aliases)


def _is_over_generic_term(value: str) -> bool:
    compact = re.sub(r"[\s/_\-·:：]+", "", str(value or "").strip())
    if not compact:
        return True
    if compact in GENERIC_TERMS:
        return True
    cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in compact)
    return 0 < cjk_count <= 1 and len(compact) <= 2


def _has_high_ocr_noise(evidence: Iterable[Mapping[str, Any]]) -> bool:
    for item in evidence:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("heading_path", "source_document", "evidence_quote")
        )
        if _ocr_noise_ratio(text) >= 0.12:
            return True
        if ASCII_NOISE_PATTERN.search(text):
            return True
    return False


def _ocr_noise_ratio(text: str) -> float:
    normalized = str(text or "").strip()
    if len(normalized) < 24:
        return 0.0
    noisy = sum(1 for char in normalized if char in GARBLED_CHARS)
    noisy += sum(1 for char in normalized if ord(char) < 32 and char not in {"\n", "\r", "\t"})
    return noisy / max(1, len(normalized))


def _has_cross_section_contamination(*, item_type: str, evidence: Iterable[Mapping[str, Any]]) -> bool:
    if item_type not in {"section_template", "product_family"}:
        return False
    for item in evidence:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("heading_path", "source_document", "evidence_quote")
        )
        if not any(term in text for term in BUSINESS_OR_DELIVERY_TERMS):
            continue
        if not any(term in text for term in TECHNICAL_TERMS):
            return True
    return False


def _asset_type_uncertain(
    *,
    item_type: str,
    canonical_name: str,
    aliases: Iterable[str],
    evidence: Iterable[Mapping[str, Any]],
) -> bool:
    if item_type != "asset_type_rule":
        return False
    haystack = " ".join(
        [
            canonical_name,
            " ".join(aliases),
            *[
                " ".join(str(item.get(key) or "") for key in ("heading_path", "evidence_quote", "asset_id"))
                for item in evidence
            ],
        ]
    ).casefold()
    return not haystack or "unknown" in haystack or "待确认" in haystack or "不确定" in haystack


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
