from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from app.config import get_settings
from app.services.domain.synonyms import expand_domain_terms, extract_domain_terms
from app.services.domain.term_lexicon import build_corpus_term_lexicon, expand_terms_with_lexicon, extract_terms_from_lexicon
from app.services.parsing.section_catalog import build_heading_aliases, normalize_section_heading
from app.services.retrieval.hybrid import bm25_sparse_score
from app.services.retrieval.reranker import Reranker, build_default_reranker
from app.services.retrieval.semantic_scorer import EmbeddingSemanticScorer, SemanticScorer
from app.services.vectorstore.block_taxonomy import (
    content_form_is_table,
    extract_taxonomy_hints,
    heading_focus_adjustment,
    heading_family_similarity,
    heading_looks_like_document_title,
    infer_target_taxonomy,
    related_section_types,
    support_content_forms,
)


CASE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
CASE_STOPWORDS = {
    "项目",
    "方案",
    "技术",
    "系统",
    "说明",
    "当前",
    "相关",
    "以及",
    "进行",
}
LIGHT_CONTEXT_TERMS = {
    "钢铁",
    "冶金",
    "矿山",
    "水泥",
    "风机",
    "鼓风机",
    "环冷风机",
    "冷风机",
    "电机",
    "压缩机",
    "高压电机",
}
DETAIL_HINT_TERMS = (
    "系统",
    "架构",
    "结构",
    "模块",
    "接口",
    "边界",
    "方案",
    "总体",
    "拓扑",
    "流程",
    "控制",
    "通讯",
    "通信",
    "联锁",
    "保护",
    "启动",
    "起动",
    "同步",
    "曲线",
    "特性",
    "单线图",
    "总布置图",
)
STRICT_QUERY_INTENT_TERMS = {
    "单线图",
    "singlelinediagram",
    "singleline",
    "曲线",
    "总布置图",
    "generalarrangementdrawing",
    "generalarrangement",
}
FIGURE_HEADING_INTENT_TOKENS = {
    "单线图",
    "singlelinediagram",
    "singleline",
    "曲线",
    "curve",
    "startcurve",
    "总布置图",
    "generalarrangementdrawing",
    "generalarrangement",
    "外形图",
    "outlinedrawing",
    "示意图",
    "diagram",
}
GENERIC_SUMMARY_SECTION_HEADINGS = {
    "方案综述",
    "项目综述",
    "方案概述",
    "项目概述",
    "总体说明",
}
SECTION_NOISE_HINT_TERMS = (
    "必要性",
    "意义",
    "节电",
    "运行时",
    "工频运行",
    "变频运行",
    "储运",
    "培训",
    "服务",
    "备品",
)
EQUIPMENT_TYPE_INTENT_SEEDS: dict[str, tuple[str, ...]] = {
    "lci": ("lci",),
    "vfd": ("变频器",),
    "soft_starter": ("软起动",),
    "motor": ("电机",),
    "transformer": ("变压器",),
    "switchgear": ("开关柜",),
    "cabinet": ("控制柜",),
    "dcs_plc_interface": ("plc", "dcs"),
    "cooling_system": ("冷却系统",),
    "fan_blower": ("鼓风机",),
    "compressor": ("压缩机",),
}
SUPPORT_SECTION_REQUEST_SEEDS: dict[str, tuple[str, ...]] = {
    "supply_scope": ("供货范围", "供货", "供货界面"),
    "bom_or_supply_list": ("供货清单", "设备清单", "配置清单", "物料清单", "备件"),
    "commercial_manual_only": (
        "资料提供",
        "提交资料",
        "交付资料",
        "随机资料",
        "技术资料",
        "文档清单",
        "交付文档",
        "商务",
        "报价",
        "合同",
        "法务",
        "授权",
        "保密",
    ),
    "service_support": ("售后", "培训", "维保", "质保", "巡检", "备件"),
    "site_conditions": ("工况条件", "环境条件", "现场条件", "现场环境"),
    "installation_conditions": ("安装条件", "安装要求", "配套要求", "基础要求", "储运"),
    "commissioning_acceptance": ("调试", "试验", "测试", "验收", "联调", "开车"),
}
BROAD_PARENT_HEADING_HINTS = (
    "系统方案",
    "systemsolution",
    "总体方案",
    "整体方案",
    "方案概述",
    "项目概述",
    "总体设计",
    "技术方案",
)
DIRECT_DESCENDANT_MATCH_REASON_PREFIXES = (
    "query_intent_heading_overlap",
    "leaf_query_intent_match",
    "heading_detail_overlap",
    "leaf_heading_detail_match",
    "section_anchor_title_match",
)
WEAK_ANCESTOR_REASON_PREFIXES = (
    "contextual_anchor_only_penalty",
    "broad_heading_without_heading_intent_penalty",
    "broad_heading_query_intent_context_penalty",
    "section_title_without_query_intent_penalty",
)
PRIMARY_SPECIFIC_HIT_REASON_PREFIXES = (
    "query_intent_heading_overlap",
    "leaf_query_intent_match",
    "section_anchor_title_match",
)
TAIL_NOISE_REASON_PREFIXES = (
    "non_requested_figure_section_penalty",
    "non_requested_support_section_penalty",
    "strict_query_intent_mismatch",
    "section_title_without_query_intent_penalty",
)
BROAD_PARENT_DESCENDANT_SIGNAL_PREFIXES = (
    "query_intent_heading_overlap",
    "query_intent_overlap",
    "heading_detail_overlap",
    "leaf_heading_detail_match",
    "section_anchor_title_match",
)
TECHNICAL_DETAIL_HEADING_HINTS = (
    "技术数据",
    "componenttechnicaldata",
    "technicaldata",
    "配置",
    "configuration",
    "已配置的选项",
    "selectedoptions",
    "标准",
    "standard",
    "证书",
    "certification",
    "参数",
    "parameter",
    "性能",
    "performance",
)
HYBRID_RRF_K = 10
HYBRID_RRF_MAX_RANK_WINDOW = 32
HYBRID_RRF_MIN_SPARSE_SCORE = 0.12
HYBRID_RRF_MIN_SEMANTIC_SCORE = 0.2
HYBRID_RRF_MAX_CASE_BOOST = 0.08
HYBRID_RRF_MAX_SECTION_BOOST = 0.06
HYBRID_RRF_MAX_BLOCK_BOOST = 0.05
HYBRID_RERANK_K = 4
HYBRID_RERANK_MAX_RANK_WINDOW = 16
HYBRID_RERANK_MIN_SCORE = 0.18
HYBRID_RERANK_MAX_CASE_BOOST = 0.05
HYBRID_RERANK_MAX_SECTION_BOOST = 0.04
HYBRID_RERANK_MAX_BLOCK_BOOST = 0.035
HYBRID_BM25_K1 = 1.2
HYBRID_BM25_B = 0.75
HYBRID_BM25_PHRASE_BONUS = 0.8
HYBRID_RRF_EXCLUSION_REASON_PREFIXES = (
    "query_intent_mismatch_penalty",
    "non_requested_support_section_penalty",
    "non_requested_figure_section_penalty",
    "section_title_without_query_intent_penalty",
    "contextual_anchor_only_penalty",
    "broad_heading_without_heading_intent_penalty",
    "broad_heading_query_intent_context_penalty",
)
EQUIPMENT_TYPE_COMPATIBILITY_BY_SECTION: dict[str, dict[str, tuple[str, ...]]] = {
    "vfd_spec": {
        "lci": ("vfd",),
        "vfd": ("lci",),
    },
}


def _tokenize(text: str, *, term_lexicon: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    tokens: list[str] = []
    expanded_tokens: list[str] = []
    raw_text = str(text or "")
    for token in CASE_TOKEN_PATTERN.findall(raw_text):
        expanded_tokens.extend(expand_domain_terms([token]))
    expanded_tokens.extend(extract_domain_terms(raw_text))
    expanded_tokens = expand_terms_with_lexicon(expanded_tokens, term_lexicon)
    expanded_tokens.extend(extract_terms_from_lexicon(raw_text, term_lexicon))
    for expanded in expanded_tokens:
        if expanded and expanded not in CASE_STOPWORDS and expanded not in tokens:
            tokens.append(expanded)
    return tokens


def _normalize_file_name_for_lookup(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\s+", "", text)
    return text


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _extract_detail_hints(text: str) -> list[str]:
    haystack = str(text or "").casefold()
    hints: list[str] = []
    for term in DETAIL_HINT_TERMS:
        normalized = term.casefold()
        if normalized in haystack and normalized not in hints:
            hints.append(normalized)
    return hints


def _append_unique_terms(target: list[str], additions: list[str] | tuple[str, ...] | set[str]) -> None:
    for item in additions:
        normalized = str(item or "").strip().casefold()
        if normalized and normalized not in target:
            target.append(normalized)


def _effective_target_equipment_types(*, target_equipment_type: str, compatibility_hint_text: str) -> set[str]:
    normalized_type = str(target_equipment_type or "").strip().lower()
    if not normalized_type:
        return set()
    effective_types = {normalized_type}
    if _looks_like_technical_detail_heading(compatibility_hint_text):
        effective_types.update(
            EQUIPMENT_TYPE_COMPATIBILITY_BY_SECTION.get("vfd_spec", {}).get(normalized_type, ())
        )
    return effective_types


def _extract_equipment_intent_terms(
    *,
    query: str,
    section_title: str,
    target_equipment_type: str,
    compatibility_hint_text: str,
    term_lexicon: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    normalized_type = str(target_equipment_type or "").strip().lower()
    if not normalized_type or normalized_type == "generic":
        return []
    effective_types = _effective_target_equipment_types(
        target_equipment_type=normalized_type,
        compatibility_hint_text=compatibility_hint_text,
    )
    seeds = [
        seed
        for equipment_type in effective_types
        for seed in (EQUIPMENT_TYPE_INTENT_SEEDS.get(equipment_type) or ())
    ]
    if not seeds:
        return []
    source_terms = set(_tokenize("\n".join(part for part in (query, section_title) if part), term_lexicon=term_lexicon))
    selected: list[str] = []
    for seed in seeds:
        seed_terms = _tokenize(seed, term_lexicon=term_lexicon)
        if source_terms & set(seed_terms):
            _append_unique_terms(selected, seed_terms)
    return [term for term in selected if term and term not in LIGHT_CONTEXT_TERMS]


def _query_requests_support_section(
    *,
    query: str,
    section_title: str,
    section_type: str,
    term_lexicon: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    normalized_type = str(section_type or "").strip().lower()
    seeds = SUPPORT_SECTION_REQUEST_SEEDS.get(normalized_type) or ()
    if not seeds:
        return False
    source_text = "\n".join(part for part in (query, section_title) if part)
    normalized_source = normalize_section_heading(source_text).replace(" ", "")
    if normalized_type == "commercial_manual_only":
        for exact_phrase in ("启动同步资料", "同步资料", "启动资料"):
            if normalize_section_heading(exact_phrase).replace(" ", "") in normalized_source:
                return True
    source_terms = set(_tokenize(source_text, term_lexicon=term_lexicon))
    for seed in seeds:
        if source_terms & set(_tokenize(seed, term_lexicon=term_lexicon)):
            return True
    return False


def _section_heading_noise_adjustment(
    *,
    section_title: str,
    normalized_heading: str,
    signals: set[str],
) -> tuple[float, list[str]]:
    compact_heading = normalized_heading.replace(" ", "")
    normalized_title = normalize_section_heading(section_title).replace(" ", "")
    if not compact_heading:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    if "toc" not in signals and compact_heading in GENERIC_SUMMARY_SECTION_HEADINGS:
        score -= 0.12
        reasons.append("generic_summary_heading_penalty")
    if "toc" not in signals and any(token in compact_heading for token in SECTION_NOISE_HINT_TERMS):
        if any(token in normalized_title for token in ("系统", "方案", "架构", "结构")):
            score -= 0.16
            reasons.append("heading_noise_penalty")
    if "#" in compact_heading and any(token in compact_heading for token in ("运行时", "工频运行", "变频运行")):
        score -= 0.2
        reasons.append("inline_state_heading_penalty")
    if "toc" not in signals and len(compact_heading) > 26 and any(token in compact_heading for token in ("如下", "尺寸", "型号")):
        score -= 0.18
        reasons.append("long_inline_heading_penalty")
    return score, reasons


def _looks_like_broad_parent_heading(text: str) -> bool:
    compact_heading = normalize_section_heading(text).replace(" ", "").casefold()
    if not compact_heading:
        return False
    return any(token in compact_heading for token in BROAD_PARENT_HEADING_HINTS)


def _looks_like_technical_detail_heading(text: str) -> bool:
    compact_heading = normalize_section_heading(text).replace(" ", "").casefold()
    if not compact_heading:
        return False
    return any(token in compact_heading for token in TECHNICAL_DETAIL_HEADING_HINTS)


def _looks_like_sentence_fragment_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading or len(heading) < 20:
        return False
    token_count = len(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", heading))
    if token_count < 5:
        return False
    lowercase_word_count = sum(
        1
        for token in re.findall(r"[A-Za-z]+", heading)
        if token == token.lower()
    )
    has_sentence_punctuation = any(marker in heading for marker in (":", "：", ",", "，", ".", ";"))
    return has_sentence_punctuation and lowercase_word_count >= 3


def _split_section_path(text: str) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in str(text or "").split(" > ") if segment.strip())


def _candidate_section_path(candidate: dict[str, Any]) -> str:
    return str(candidate.get("section_path") or candidate.get("heading_path") or "").strip()


def _candidate_support_dedupe_key(candidate: dict[str, Any]) -> tuple[str, str]:
    sample_id = str(candidate.get("sample_id") or "").strip()
    section_id = str(candidate.get("source_section_id") or "").strip()
    if section_id:
        return sample_id, section_id
    section_path = _candidate_section_path(candidate)
    if section_path:
        return sample_id, section_path.casefold()
    heading = str(candidate.get("source_heading") or candidate.get("heading_path") or "").strip()
    return sample_id, heading.casefold()


def _is_nonrequested_support_tail_candidate(candidate: dict[str, Any]) -> bool:
    section_type = str(candidate.get("section_type") or "").strip().lower()
    if section_type not in SUPPORT_SECTION_REQUEST_SEEDS:
        return False
    return _has_reason_prefix(list(candidate.get("_reasons") or []), "non_requested_support_section_penalty")


def _is_low_score_specific_tail_noise_candidate(candidate: dict[str, Any]) -> bool:
    reasons = list(candidate.get("_reasons") or [])
    if not _has_reason_prefix(reasons, "specific_hit_tail_noise_penalty"):
        return False
    score = float(candidate.get("_score") or 0)
    if score < 0.02:
        return True
    if score >= 0.12:
        return False
    return _has_reason_prefix(
        reasons,
        "non_requested_support_section_penalty",
        "technical_detail_sidecar_penalty",
    )


def _is_low_score_redundant_broad_or_figure_candidate(candidate: dict[str, Any]) -> bool:
    reasons = list(candidate.get("_reasons") or [])
    score = float(candidate.get("_score") or 0)
    if score < 0.02 or score >= 0.12:
        return False
    if (
        _has_reason_prefix(reasons, "broad_parent_competitive_specificity_penalty", "broad_parent_competitive_score_cap")
        and _has_reason_prefix(reasons, "contextual_anchor_only_penalty")
    ):
        return True
    if (
        _has_reason_prefix(reasons, "specific_hit_tail_noise_penalty")
        and _has_reason_prefix(reasons, "non_requested_figure_section_penalty")
        and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
        and not _has_reason_prefix(
            reasons,
            "title_aligned_wrong_leaf_penalty",
            "section_anchor_title_match",
            "section_title_match",
            "title_overlap",
        )
    ):
        return True
    return False


def _is_descendant_section_path(*, parent_path: str, child_path: str) -> bool:
    parent_segments = _split_section_path(parent_path)
    child_segments = _split_section_path(child_path)
    if not parent_segments or len(child_segments) <= len(parent_segments):
        return False
    return child_segments[: len(parent_segments)] == parent_segments


def _has_reason_prefix(reasons: list[str], *prefixes: str) -> bool:
    for reason in reasons:
        for prefix in prefixes:
            if reason == prefix or reason.startswith(f"{prefix}="):
                return True
    return False


def _is_detached_fragment_candidate(candidate: dict[str, Any]) -> bool:
    heading = str(candidate.get("source_heading") or candidate.get("heading_path") or "").strip()
    if not _looks_like_sentence_fragment_heading(heading):
        return False
    if str(candidate.get("source_section_id") or "").strip():
        return False
    if str(candidate.get("normalized_section_path") or "").strip():
        return False
    if candidate.get("section_summary") or candidate.get("section_retrieval_text"):
        return False
    if str(candidate.get("content_form") or "") != "narrative":
        return False
    return int(candidate.get("token_count") or 0) <= 80


def _apply_ancestor_specificity_penalties(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 2:
        return

    for candidate in candidates:
        parent_path = _candidate_section_path(candidate)
        if not parent_path:
            continue
        parent_reasons = list(candidate.get("_reasons") or [])
        parent_heading = str(candidate.get("source_heading") or candidate.get("heading_path") or parent_path)
        if not _looks_like_broad_parent_heading(parent_heading) and not _has_reason_prefix(
            parent_reasons,
            *WEAK_ANCESTOR_REASON_PREFIXES,
        ):
            continue

        parent_score = float(candidate.get("_score") or 0)
        descendant_matches = [
            other
            for other in candidates
            if other is not candidate
            and str(other.get("sample_id") or "") == str(candidate.get("sample_id") or "")
            and _is_descendant_section_path(parent_path=parent_path, child_path=_candidate_section_path(other))
            and _has_reason_prefix(list(other.get("_reasons") or []), *DIRECT_DESCENDANT_MATCH_REASON_PREFIXES)
            and float(other.get("_score") or 0) + 0.06 >= parent_score
        ]
        if not descendant_matches:
            continue

        best_descendant = max(
            descendant_matches,
            key=lambda other: (
                float(other.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(other))),
                int(other.get("token_count") or 0),
            ),
        )
        descendant_reasons = list(best_descendant.get("_reasons") or [])
        extra_reasons: list[str] = []
        penalty = 0.22
        if _has_reason_prefix(descendant_reasons, "query_intent_heading_overlap", "leaf_query_intent_match"):
            penalty += 0.04
        if float(best_descendant.get("_score") or 0) >= parent_score:
            penalty += 0.02
        if _has_reason_prefix(parent_reasons, "contextual_anchor_only_penalty", "broad_heading_without_heading_intent_penalty"):
            penalty += 0.02
        if (
            _looks_like_broad_parent_heading(parent_heading)
            and _has_reason_prefix(descendant_reasons, "query_intent_heading_overlap", "leaf_query_intent_match")
            and not _has_reason_prefix(
                parent_reasons,
                "query_intent_heading_overlap",
                "heading_detail_overlap",
                "leaf_query_intent_match",
                "section_anchor_title_match",
            )
        ):
            penalty += 0.12
            extra_reasons.append("broad_parent_direct_leaf_redundancy_penalty")
        candidate["_score"] = parent_score - min(0.42, penalty)
        candidate["_reasons"] = [*parent_reasons, *extra_reasons, "ancestor_specific_hit_penalty"]


def _apply_specific_hit_tail_noise_penalties(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 2:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        if not primary_hits:
            continue

        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        )
        best_primary_reasons = list(best_primary.get("_reasons") or [])
        best_primary_score = float(best_primary.get("_score") or 0)
        primary_is_direct_intent_hit = _has_reason_prefix(
            best_primary_reasons,
            "query_intent_heading_overlap",
            "leaf_query_intent_match",
        )

        for candidate in sample_candidates:
            if candidate is best_primary:
                continue
            reasons = list(candidate.get("_reasons") or [])
            extra_reasons: list[str] = []
            penalty = 0.0
            if _has_reason_prefix(reasons, "non_requested_figure_section_penalty"):
                penalty += 0.18
                if primary_is_direct_intent_hit:
                    penalty += 0.04
            if _has_reason_prefix(reasons, "non_requested_support_section_penalty"):
                penalty += 0.18
            if _has_reason_prefix(reasons, "strict_query_intent_mismatch"):
                penalty += 0.14
                if primary_is_direct_intent_hit:
                    penalty += 0.08
            if _has_reason_prefix(reasons, "section_title_without_query_intent_penalty"):
                penalty += 0.06
                if _has_reason_prefix(reasons, "section_anchor_title_match", "section_title_match"):
                    penalty += 0.06
            candidate_path = _candidate_section_path(candidate)
            best_primary_path = _candidate_section_path(best_primary)
            hierarchically_related = _is_descendant_section_path(parent_path=candidate_path, child_path=best_primary_path) or _is_descendant_section_path(
                parent_path=best_primary_path,
                child_path=candidate_path,
            )
            if (
                primary_is_direct_intent_hit
                and not hierarchically_related
                and _has_reason_prefix(reasons, "section_anchor_title_match", "section_title_match")
                and _has_reason_prefix(reasons, "section_title_without_query_intent_penalty")
            ):
                penalty += 0.2
                extra_reasons.append("title_aligned_wrong_leaf_penalty")
            if (
                primary_is_direct_intent_hit
                and not hierarchically_related
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and (
                    _looks_like_technical_detail_heading(candidate_path)
                    or str(candidate.get("content_form") or "") in {"formula", "parameter_table", "certificate"}
                )
            ):
                penalty += 0.18
                extra_reasons.append("technical_detail_sidecar_penalty")
            if (
                primary_is_direct_intent_hit
                and not hierarchically_related
                and _is_detached_fragment_candidate(candidate)
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                )
            ):
                penalty += 0.38
                extra_reasons.append("detached_fragment_sidecar_penalty")
            if penalty <= 0:
                continue
            if primary_is_direct_intent_hit:
                penalty += 0.04
            if float(candidate.get("_score") or 0) + 0.02 >= best_primary_score:
                penalty += 0.02
            candidate["_score"] = float(candidate.get("_score") or 0) - min(0.6, penalty)
            candidate["_reasons"] = [*reasons, *extra_reasons, "specific_hit_tail_noise_penalty"]


def _apply_broad_parent_context_penalties(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 3:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        if not primary_hits:
            continue

        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        )

        for candidate in sample_candidates:
            reasons = list(candidate.get("_reasons") or [])
            candidate_heading = str(candidate.get("source_heading") or candidate.get("heading_path") or "")
            candidate_path = _candidate_section_path(candidate)
            if not _looks_like_broad_parent_heading(candidate_heading) or not candidate_path:
                continue
            if not _is_descendant_section_path(parent_path=candidate_path, child_path=_candidate_section_path(best_primary)):
                continue

            clean_descendants = [
                other
                for other in sample_candidates
                if other is not candidate
                and other is not best_primary
                and _is_descendant_section_path(parent_path=candidate_path, child_path=_candidate_section_path(other))
                and not _looks_like_broad_parent_heading(str(other.get("source_heading") or other.get("heading_path") or ""))
                and not _has_reason_prefix(list(other.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
                and _has_reason_prefix(list(other.get("_reasons") or []), *BROAD_PARENT_DESCENDANT_SIGNAL_PREFIXES)
                and float(other.get("_score") or 0) + 0.04 >= float(candidate.get("_score") or 0)
            ]
            if not clean_descendants:
                continue
            candidate["_score"] = float(candidate.get("_score") or 0) - 0.14
            candidate["_reasons"] = [*reasons, "broad_parent_secondary_context_penalty"]


def _apply_broad_parent_competitive_specificity_penalties(
    *,
    candidates: list[dict[str, Any]],
    query_intent_terms: list[str],
) -> None:
    if not query_intent_terms or len(candidates) < 3:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        ) if primary_hits else None
        for candidate in sample_candidates:
            reasons = list(candidate.get("_reasons") or [])
            candidate_heading = str(candidate.get("source_heading") or candidate.get("heading_path") or "")
            if not _looks_like_broad_parent_heading(candidate_heading):
                continue
            if not _has_reason_prefix(reasons, "ancestor_specific_hit_penalty", "broad_parent_direct_leaf_redundancy_penalty"):
                continue

            current_score = float(candidate.get("_score") or 0)
            candidate_path = _candidate_section_path(candidate)
            score_floor = 0.18
            nearby_specifics = [
                other
                for other in sample_candidates
                if other is not candidate
                and other is not best_primary
                and float(other.get("_score") or 0) >= 0.26
                and not _looks_like_broad_parent_heading(str(other.get("source_heading") or other.get("heading_path") or ""))
                and not _has_reason_prefix(list(other.get("_reasons") or []), "non_requested_support_section_penalty")
                and _has_reason_prefix(
                    list(other.get("_reasons") or []),
                    "heading_detail_overlap",
                    "leaf_heading_detail_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "query_intent_overlap",
                    "specific_hit_tail_noise_penalty",
                    "context_only_sidecar_penalty",
                )
            ]
            fallback_score_cap: float | None = None
            if not nearby_specifics:
                if not best_primary:
                    continue
                primary_path = _candidate_section_path(best_primary)
                primary_reasons = list(best_primary.get("_reasons") or [])
                if not candidate_path or not primary_path:
                    continue
                if not _is_descendant_section_path(parent_path=candidate_path, child_path=primary_path):
                    continue
                if not _has_reason_prefix(primary_reasons, "query_intent_heading_overlap", "leaf_query_intent_match"):
                    continue
                support_tail_competitors = [
                    other
                    for other in sample_candidates
                    if other is not candidate
                    and other is not best_primary
                    and _has_reason_prefix(list(other.get("_reasons") or []), "non_requested_support_section_penalty")
                    and _has_reason_prefix(list(other.get("_reasons") or []), "specific_hit_tail_noise_penalty")
                ]
                fallback_score_cap = 0.22
                if _has_reason_prefix(reasons, "broad_heading_without_heading_intent_penalty") and _has_reason_prefix(
                    reasons,
                    "contextual_anchor_only_penalty",
                ):
                    score_floor = 0.16
                    fallback_score_cap = 0.16
                    if support_tail_competitors:
                        score_floor = 0.10
                        fallback_score_cap = 0.10
                elif _has_reason_prefix(reasons, "broad_heading_without_heading_intent_penalty", "contextual_anchor_only_penalty"):
                    fallback_score_cap = 0.20

            penalty = 0.06
            specific_tail_noise_count = 0
            if nearby_specifics:
                specific_tail_noise_count = sum(
                    1
                    for other in nearby_specifics
                    if _has_reason_prefix(list(other.get("_reasons") or []), "specific_hit_tail_noise_penalty")
                )
                if specific_tail_noise_count:
                    penalty += 0.02
                    penalty += min(0.12, max(0, specific_tail_noise_count - 1) * 0.05)
            elif fallback_score_cap is not None:
                penalty += 0.02
            new_score = current_score - min(0.18, penalty)
            if nearby_specifics:
                strongest_competing_score = max(float(other.get("_score") or 0) for other in nearby_specifics)
                cap_margin = 0.02
                if len(nearby_specifics) >= 3:
                    cap_margin += 0.06
                elif len(nearby_specifics) >= 2:
                    cap_margin += 0.03
                if any(
                    _has_reason_prefix(list(other.get("_reasons") or []), "context_only_sidecar_penalty", "specific_hit_tail_noise_penalty")
                    for other in nearby_specifics
                ):
                    cap_margin += 0.02
                score_cap = max(score_floor, strongest_competing_score - cap_margin)
            else:
                score_cap = max(score_floor, float(fallback_score_cap or 0.22))
            score_capped = score_cap < new_score
            candidate["_score"] = min(new_score, score_cap)
            updated_reasons = [*reasons, "broad_parent_competitive_specificity_penalty"]
            if score_capped:
                updated_reasons.append("broad_parent_competitive_score_cap")
            candidate["_reasons"] = updated_reasons


def _apply_context_only_sidecar_penalties(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 2:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        if not primary_hits:
            continue

        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        )
        primary_reasons = list(best_primary.get("_reasons") or [])
        if not _has_reason_prefix(primary_reasons, "query_intent_heading_overlap", "leaf_query_intent_match"):
            continue

        primary_path = _candidate_section_path(best_primary)
        for candidate in sample_candidates:
            if candidate is best_primary:
                continue
            reasons = list(candidate.get("_reasons") or [])
            if not _has_reason_prefix(reasons, "specific_hit_tail_noise_penalty"):
                continue
            if _has_reason_prefix(reasons, "non_requested_support_section_penalty"):
                continue
            if _has_reason_prefix(reasons, "query_intent_heading_overlap", "query_intent_overlap", "leaf_query_intent_match"):
                continue
            if not _has_reason_prefix(reasons, "query_intent_mismatch_penalty", "section_title_without_query_intent_penalty"):
                continue
            if not _has_reason_prefix(
                reasons,
                "technical_detail_sidecar_penalty",
                "title_aligned_wrong_leaf_penalty",
                "non_requested_figure_section_penalty",
            ):
                continue

            candidate_path = _candidate_section_path(candidate)
            if _is_descendant_section_path(parent_path=candidate_path, child_path=primary_path) or _is_descendant_section_path(
                parent_path=primary_path,
                child_path=candidate_path,
            ):
                continue

            penalty = 0.08
            if _has_reason_prefix(reasons, "technical_detail_sidecar_penalty", "title_aligned_wrong_leaf_penalty"):
                penalty += 0.04
            if _has_reason_prefix(reasons, "non_requested_figure_section_penalty"):
                penalty += 0.02
            if _has_reason_prefix(reasons, "section_context_match", "section_anchor_context_overlap"):
                penalty += 0.02
            candidate["_score"] = float(candidate.get("_score") or 0) - min(0.16, penalty)
            candidate["_reasons"] = [*reasons, "context_only_sidecar_penalty"]


def _apply_direct_hit_context_sidecar_score_caps(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 2:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        if not primary_hits:
            continue

        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        )
        primary_reasons = list(best_primary.get("_reasons") or [])
        if not _has_reason_prefix(primary_reasons, "query_intent_heading_overlap", "leaf_query_intent_match"):
            continue

        primary_path = _candidate_section_path(best_primary)
        for candidate in sample_candidates:
            if candidate is best_primary:
                continue
            reasons = list(candidate.get("_reasons") or [])
            if not _has_reason_prefix(reasons, "specific_hit_tail_noise_penalty"):
                continue
            if not _has_reason_prefix(reasons, "context_only_sidecar_penalty"):
                continue
            if _has_reason_prefix(reasons, "non_requested_support_section_penalty"):
                continue

            candidate_path = _candidate_section_path(candidate)
            if _is_descendant_section_path(parent_path=candidate_path, child_path=primary_path) or _is_descendant_section_path(
                parent_path=primary_path,
                child_path=candidate_path,
            ):
                continue

            score_cap = 0.32
            score_floor = 0.18
            if _has_reason_prefix(reasons, "technical_detail_sidecar_penalty"):
                score_cap = min(score_cap, 0.28)
            if _has_reason_prefix(reasons, "non_requested_figure_section_penalty"):
                score_cap = min(score_cap, 0.28)
            if _has_reason_prefix(reasons, "title_aligned_wrong_leaf_penalty"):
                score_cap = min(score_cap, 0.24)
            if _has_reason_prefix(reasons, "non_requested_figure_section_penalty") and not _has_reason_prefix(
                reasons,
                "heading_detail_overlap",
                "section_title_without_query_intent_penalty",
                "title_aligned_wrong_leaf_penalty",
            ):
                score_cap -= 0.02
            if _has_reason_prefix(reasons, "non_requested_figure_section_penalty") and not _has_reason_prefix(
                reasons,
                "query_intent_overlap",
                "query_intent_heading_overlap",
                "heading_detail_overlap",
                "leaf_query_intent_match",
                "section_anchor_title_match",
                "title_overlap",
            ):
                score_cap -= 0.04
            if (
                _has_reason_prefix(reasons, "non_requested_figure_section_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not any(term in FIGURE_HEADING_INTENT_TOKENS for term in query_intent_terms)
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                    "title_aligned_wrong_leaf_penalty",
                )
            ):
                score_floor = 0.14
                score_cap = min(score_cap - 0.02, 0.14)
                if not _has_reason_prefix(reasons, "heading_detail_overlap", "leaf_heading_detail_match"):
                    score_floor = 0.10
                    score_cap = min(score_cap - 0.02, 0.10)
            if (
                _has_reason_prefix(reasons, "non_requested_figure_section_penalty")
                and _has_reason_prefix(reasons, "equipment_type_mismatch_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                    "title_aligned_wrong_leaf_penalty",
                )
            ):
                score_floor = 0.10
                score_cap = min(score_cap - 0.02, 0.10)
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_floor = 0.16
                score_cap = min(score_cap - 0.02, 0.16)
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "non_requested_figure_section_penalty")
                and _has_reason_prefix(reasons, "equipment_type_mismatch_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                    "title_aligned_wrong_leaf_penalty",
                )
            ):
                score_floor = 0.10
                score_cap = min(score_cap - 0.02, 0.10)
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "equipment_type_mismatch_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not _has_reason_prefix(reasons, "high_reuse_level", "non_requested_figure_section_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                    "title_aligned_wrong_leaf_penalty",
                )
            ):
                score_floor = 0.10
                score_cap = min(score_cap - 0.02, 0.10)
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "high_reuse_level")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_floor = 0.10
                score_cap = min(score_cap - 0.02, 0.10)
            if _has_reason_prefix(reasons, "technical_detail_sidecar_penalty") and not _has_reason_prefix(
                reasons,
                "query_intent_overlap",
                "query_intent_heading_overlap",
                "leaf_query_intent_match",
                "section_anchor_title_match",
                "section_title_match",
                "title_overlap",
            ):
                score_cap -= 0.02
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_cap -= 0.02
            if (
                _has_reason_prefix(reasons, "technical_detail_sidecar_penalty")
                and _has_reason_prefix(reasons, "equipment_type_mismatch_penalty")
                and not _has_reason_prefix(
                    reasons,
                    "high_reuse_level",
                    "query_intent_overlap",
                    "query_intent_heading_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_cap -= 0.02
            if (
                _has_reason_prefix(reasons, "title_aligned_wrong_leaf_penalty")
                and _has_reason_prefix(reasons, "query_intent_mismatch_penalty")
                and _has_reason_prefix(reasons, "section_title_without_query_intent_penalty")
            ):
                score_cap -= 0.02
            if _has_reason_prefix(reasons, "section_title_without_query_intent_penalty"):
                score_cap -= 0.02
            if _has_reason_prefix(reasons, "high_reuse_level"):
                score_cap -= 0.02
            if _has_reason_prefix(
                reasons,
                "equipment_type_mismatch_penalty",
                "missing_equipment_intent_penalty",
                "explicit_equipment_mismatch_penalty",
            ):
                score_cap -= 0.02
            score_cap = max(score_floor, score_cap)

            current_score = float(candidate.get("_score") or 0)
            if current_score <= score_cap:
                continue

            candidate["_score"] = score_cap
            candidate["_reasons"] = [*reasons, "direct_hit_context_sidecar_score_cap"]


def _apply_direct_hit_support_tail_score_caps(*, candidates: list[dict[str, Any]], query_intent_terms: list[str]) -> None:
    if not query_intent_terms or len(candidates) < 2:
        return

    by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        sample_id = str(candidate.get("sample_id") or "").strip()
        if not sample_id:
            continue
        by_sample.setdefault(sample_id, []).append(candidate)

    for sample_candidates in by_sample.values():
        primary_hits = [
            candidate
            for candidate in sample_candidates
            if _has_reason_prefix(list(candidate.get("_reasons") or []), *PRIMARY_SPECIFIC_HIT_REASON_PREFIXES)
            and not _has_reason_prefix(list(candidate.get("_reasons") or []), *TAIL_NOISE_REASON_PREFIXES)
        ]
        if not primary_hits:
            continue

        best_primary = max(
            primary_hits,
            key=lambda candidate: (
                float(candidate.get("_score") or 0),
                len(_split_section_path(_candidate_section_path(candidate))),
                int(candidate.get("token_count") or 0),
            ),
        )
        primary_reasons = list(best_primary.get("_reasons") or [])
        if not _has_reason_prefix(primary_reasons, "query_intent_heading_overlap", "leaf_query_intent_match"):
            continue

        primary_path = _candidate_section_path(best_primary)
        for candidate in sample_candidates:
            if candidate is best_primary:
                continue
            reasons = list(candidate.get("_reasons") or [])
            if not _has_reason_prefix(reasons, "non_requested_support_section_penalty"):
                continue
            if not _has_reason_prefix(reasons, "specific_hit_tail_noise_penalty"):
                continue

            candidate_path = _candidate_section_path(candidate)
            if _is_descendant_section_path(parent_path=candidate_path, child_path=primary_path) or _is_descendant_section_path(
                parent_path=primary_path,
                child_path=candidate_path,
            ):
                continue

            stronger_support_competitors = [
                other
                for other in sample_candidates
                if other is not candidate
                and other is not best_primary
                and _has_reason_prefix(list(other.get("_reasons") or []), "non_requested_support_section_penalty")
                and _has_reason_prefix(list(other.get("_reasons") or []), "equipment_type_match")
                and not _has_reason_prefix(
                    list(other.get("_reasons") or []),
                    "equipment_type_mismatch_penalty",
                    "missing_equipment_intent_penalty",
                )
            ]
            title_aligned_secondary_candidates = [
                other
                for other in sample_candidates
                if other is not candidate
                and other is not best_primary
                and _has_reason_prefix(list(other.get("_reasons") or []), "title_aligned_wrong_leaf_penalty")
                and _has_reason_prefix(list(other.get("_reasons") or []), "specific_hit_tail_noise_penalty")
            ]
            score_cap = 0.34
            score_floor = 0.18
            if not _has_reason_prefix(
                reasons,
                "query_intent_heading_overlap",
                "heading_detail_overlap",
                "leaf_query_intent_match",
                "section_anchor_title_match",
            ):
                score_cap = 0.26
            if _has_reason_prefix(reasons, "query_intent_overlap") and not _has_reason_prefix(
                reasons,
                "query_intent_heading_overlap",
                "heading_detail_overlap",
                "leaf_query_intent_match",
                "section_anchor_title_match",
            ):
                score_cap -= 0.04
                if _has_reason_prefix(reasons, "section_anchor_context_overlap") and not _has_reason_prefix(
                    reasons,
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                ):
                    score_cap -= 0.04
            if (
                title_aligned_secondary_candidates
                and _has_reason_prefix(reasons, "high_reuse_level", "equipment_type_match", "query_intent_overlap")
                and _has_reason_prefix(reasons, "section_anchor_context_overlap")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_heading_overlap",
                    "heading_detail_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_floor = 0.10
                score_cap = min(score_cap - 0.02, 0.10)
            if (
                _has_reason_prefix(reasons, "equipment_type_mismatch_penalty", "missing_equipment_intent_penalty")
                and _has_reason_prefix(reasons, "section_anchor_context_overlap")
                and not _has_reason_prefix(
                    reasons,
                    "query_intent_heading_overlap",
                    "heading_detail_overlap",
                    "leaf_query_intent_match",
                    "section_anchor_title_match",
                    "section_title_match",
                    "title_overlap",
                )
            ):
                score_floor = 0.16
                score_cap = min(score_cap - 0.02, 0.16)
                if stronger_support_competitors:
                    score_floor = 0.10
                    score_cap = min(score_cap - 0.02, 0.10)
            if _has_reason_prefix(reasons, "equipment_type_mismatch_penalty", "missing_equipment_intent_penalty"):
                score_cap -= 0.04
            if _has_reason_prefix(reasons, "high_reuse_level"):
                score_cap -= 0.02
            score_cap = max(score_floor, score_cap)

            current_score = float(candidate.get("_score") or 0)
            if current_score <= score_cap:
                continue

            candidate["_score"] = score_cap
            candidate["_reasons"] = [*reasons, "direct_hit_support_tail_score_cap"]


class CaseLibraryService:
    def __init__(
        self,
        *,
        outline_library_path: str | Path | None = None,
        block_library_path: str | Path | None = None,
        semantic_scorer: SemanticScorer | None = None,
        reranker: Reranker | None = None,
        section_scope_rerank_enabled: bool | None = None,
        section_scope_semantic_enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.outline_library_path = Path(outline_library_path or settings.case_library_outline_path)
        self.block_library_path = Path(block_library_path or settings.case_library_block_path)
        self.semantic_scorer = semantic_scorer or EmbeddingSemanticScorer()
        self.reranker = reranker or build_default_reranker()
        self.section_scope_rerank_enabled = (
            bool(settings.case_library_section_rerank_enabled)
            if section_scope_rerank_enabled is None
            else bool(section_scope_rerank_enabled)
        )
        self.section_scope_semantic_enabled = (
            bool(settings.case_library_section_semantic_enabled)
            if section_scope_semantic_enabled is None
            else bool(section_scope_semantic_enabled)
        )
        self._outline_payload_cache: dict[str, Any] | None = None
        self._block_payload_cache: dict[str, Any] | None = None
        self._term_lexicon_cache: dict[str, tuple[str, ...]] | None = None

    def retrieve_cases(
        self,
        *,
        query: str,
        top_k: int = 3,
        library_tracks: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        outline_entries = self._get_outline_entries()
        term_lexicon = self._get_term_lexicon()
        candidates: list[dict[str, Any]] = []
        query_terms = _tokenize(query, term_lexicon=term_lexicon)
        for entry in outline_entries:
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            retrieval_text = self._build_outline_retrieval_text(entry)
            score, reasons = self._score_case(query=query, query_terms=query_terms, entry=entry, retrieval_text=retrieval_text)
            if score <= 0:
                continue
            candidates.append(
                {
                    "sample_id": entry.get("sample_id"),
                    "file_name": entry.get("file_name"),
                    "library_track": track,
                    "profile": entry.get("profile"),
                    "top_level_titles": entry.get("top_level_titles") or [],
                    "outline_tree": entry.get("outline_tree") or [],
                    "_score": float(score),
                    "_reasons": list(reasons),
                    "_score_breakdown": _initialize_retrieval_score_breakdown(base_score=float(score)),
                    "_retrieval_text": retrieval_text,
                }
            )
        self._apply_hybrid_rrf_boost(
            candidates=candidates,
            query_text=query,
            query_terms=query_terms,
            term_lexicon=term_lexicon,
            text_builder=lambda item: str(item.get("_retrieval_text") or ""),
            max_boost=HYBRID_RRF_MAX_CASE_BOOST,
        )
        self._apply_hybrid_rerank_boost(
            candidates=candidates,
            query_text=query,
            text_builder=lambda item: str(item.get("_retrieval_text") or ""),
            max_boost=HYBRID_RERANK_MAX_CASE_BOOST,
        )
        candidates.sort(
            key=lambda item: (
                float(item.get("_score") or 0),
                len(item.get("top_level_titles") or []),
                str(item.get("file_name") or ""),
            ),
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        for item in candidates[:top_k]:
            result = {
                key: value
                for key, value in item.items()
                if key not in {"_score", "_reasons", "_retrieval_text", "_score_breakdown"}
            }
            result["score"] = round(float(item.get("_score") or 0), 4)
            result["reason"] = "; ".join(item.get("_reasons") or [])
            result["reason_trace"] = list(item.get("_reasons") or [])
            result["score_breakdown"] = _finalize_retrieval_score_breakdown(
                item.get("_score_breakdown"),
                final_score=float(item.get("_score") or 0),
            )
            result["retrieval_text"] = str(item.get("_retrieval_text") or "")
            results.append(result)
        return results

    def resolve_sample_ids_by_file_names(
        self,
        file_names: set[str],
        *,
        library_tracks: set[str] | None = None,
    ) -> set[str]:
        normalized_names = {_normalize_file_name_for_lookup(item) for item in file_names if str(item or "").strip()}
        if not normalized_names:
            return set()
        sample_ids: set[str] = set()
        for entry in self._get_outline_entries():
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            entry_file_name = _normalize_file_name_for_lookup(str(entry.get("file_name") or ""))
            if entry_file_name in normalized_names:
                sample_id = str(entry.get("sample_id") or "").strip()
                if sample_id:
                    sample_ids.add(sample_id)
        return sample_ids

    def retrieve_blocks(
        self,
        *,
        query: str,
        top_k: int = 8,
        sample_ids: set[str] | None = None,
        library_tracks: set[str] | None = None,
        section_title: str | None = None,
        section_ids: set[str] | None = None,
        section_path_prefixes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        block_entries = self._get_block_entries()
        term_lexicon = self._get_term_lexicon()
        query_terms = _tokenize(query, term_lexicon=term_lexicon)
        title_terms = _tokenize(section_title or "", term_lexicon=term_lexicon)
        _append_unique_terms(query_terms, _extract_detail_hints(query))
        _append_unique_terms(title_terms, _extract_detail_hints(section_title or ""))
        query_intent_hints = [hint for hint in _extract_detail_hints(query) if hint not in _extract_detail_hints(section_title or "")]
        query_intent_terms = _tokenize(" ".join(query_intent_hints), term_lexicon=term_lexicon) if query_intent_hints else []
        _append_unique_terms(query_intent_terms, query_intent_hints)
        context_terms = {term for term in query_terms if term in LIGHT_CONTEXT_TERMS}
        for hint in extract_taxonomy_hints(query, section_title or ""):
            normalized = hint.casefold()
            if normalized not in query_terms:
                query_terms.append(normalized)
            context_terms.update(
                term for term in _tokenize(normalized, term_lexicon=term_lexicon) if term in LIGHT_CONTEXT_TERMS
            )
        for hint in _extract_detail_hints(query):
            if hint not in query_terms:
                query_terms.append(hint)
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title or query,
                "purpose": query,
                "expected_evidence_types": [],
                "asset_required": False,
            }
        )
        equipment_intent_terms = _extract_equipment_intent_terms(
            query=query,
            section_title=section_title or "",
            target_equipment_type=str(target_taxonomy.get("equipment_type") or "generic"),
            compatibility_hint_text="\n".join(part for part in (section_title or "", query) if part),
            term_lexicon=term_lexicon,
        )
        effective_section_ids = section_ids
        effective_section_path_prefixes = section_path_prefixes
        auto_scope_used = False
        if not effective_section_ids and not effective_section_path_prefixes:
            scoped_sections = _select_block_scope_candidates(
                self.retrieve_sections(
                    query=query,
                    top_k=6,
                    sample_ids=sample_ids,
                    library_tracks=library_tracks,
                    section_title=section_title,
                ),
                limit=3,
            )
            effective_section_ids = {
                str(item.get("section_id") or "").strip()
                for item in scoped_sections
                if str(item.get("section_id") or "").strip()
            } or None
            effective_section_path_prefixes = {
                str(item.get("section_path") or item.get("heading_path") or "").strip()
                for item in scoped_sections
                if str(item.get("section_path") or item.get("heading_path") or "").strip()
            } or None
            auto_scope_used = bool(effective_section_ids or effective_section_path_prefixes)

        candidates = self._collect_block_candidates(
            block_entries=block_entries,
            query=query,
            query_terms=query_terms,
            title_terms=title_terms,
            query_intent_terms=query_intent_terms,
            equipment_intent_terms=equipment_intent_terms,
            context_terms=context_terms,
            section_title=section_title or "",
            term_lexicon=term_lexicon,
            target_taxonomy=target_taxonomy,
            sample_ids=sample_ids,
            library_tracks=library_tracks,
            section_ids=effective_section_ids,
            section_path_prefixes=effective_section_path_prefixes,
        )
        if not candidates and auto_scope_used:
            candidates = self._collect_block_candidates(
                block_entries=block_entries,
                query=query,
                query_terms=query_terms,
                title_terms=title_terms,
                query_intent_terms=query_intent_terms,
                equipment_intent_terms=equipment_intent_terms,
                context_terms=context_terms,
                section_title=section_title or "",
                term_lexicon=term_lexicon,
                target_taxonomy=target_taxonomy,
                sample_ids=sample_ids,
                library_tracks=library_tracks,
                section_ids=section_ids,
                section_path_prefixes=section_path_prefixes,
            )
        self._apply_hybrid_rrf_boost(
            candidates=candidates,
            query_text="\n".join(part for part in (section_title or "", query) if part).strip(),
            query_terms=_dedupe_keep_order([*title_terms, *query_terms]),
            term_lexicon=term_lexicon,
            text_builder=self._build_block_retrieval_text,
            max_boost=HYBRID_RRF_MAX_BLOCK_BOOST,
        )
        self._apply_hybrid_rerank_boost(
            candidates=candidates,
            query_text="\n".join(part for part in (section_title or "", query) if part).strip(),
            text_builder=self._build_block_retrieval_text,
            max_boost=HYBRID_RERANK_MAX_BLOCK_BOOST,
        )
        _apply_ancestor_specificity_penalties(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_specific_hit_tail_noise_penalties(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_broad_parent_context_penalties(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_context_only_sidecar_penalties(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_direct_hit_context_sidecar_score_caps(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_broad_parent_competitive_specificity_penalties(candidates=candidates, query_intent_terms=query_intent_terms)
        _apply_direct_hit_support_tail_score_caps(candidates=candidates, query_intent_terms=query_intent_terms)
        candidates = [item for item in candidates if float(item.get("_score") or 0) > 0]
        candidates = [item for item in candidates if not _is_low_score_specific_tail_noise_candidate(item)]
        candidates = [item for item in candidates if not _is_low_score_redundant_broad_or_figure_candidate(item)]
        candidates.sort(
            key=lambda item: (
                float(item.get("_score") or 0),
                float(item.get("reuse_level") == "high"),
                int(item.get("token_count") or 0),
            ),
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        seen_support_tail_sections: set[tuple[str, str]] = set()
        for item in candidates:
            if _is_nonrequested_support_tail_candidate(item):
                dedupe_key = _candidate_support_dedupe_key(item)
                if dedupe_key in seen_support_tail_sections:
                    continue
                seen_support_tail_sections.add(dedupe_key)
            result = {
                key: value
                for key, value in item.items()
                if key not in {"_score", "_reasons", "_score_breakdown"}
            }
            result["score"] = round(float(item.get("_score") or 0), 4)
            result["reason"] = "; ".join(item.get("_reasons") or [])
            result["reason_trace"] = list(item.get("_reasons") or [])
            result["score_breakdown"] = _finalize_retrieval_score_breakdown(
                item.get("_score_breakdown"),
                final_score=float(item.get("_score") or 0),
            )
            results.append(result)
            if len(results) >= top_k:
                break
        return results

    def retrieve_section_blocks(
        self,
        *,
        sample_id: str,
        section_id: str | None = None,
        section_path: str | None = None,
        file_name: str | None = None,
        library_tracks: set[str] | None = None,
        top_k: int = 32,
        base_score: float = 0.75,
    ) -> list[dict[str, Any]]:
        normalized_sample_id = str(sample_id or "").strip()
        normalized_section_id = str(section_id or "").strip()
        normalized_section_path = str(section_path or "").strip()
        normalized_file_name = str(file_name or "").strip()
        if not normalized_sample_id and not normalized_file_name:
            return []

        matches: list[dict[str, Any]] = []
        for entry in self._get_block_entries():
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            entry_sample_id = str(entry.get("sample_id") or "").strip()
            entry_file_name = str(entry.get("file_name") or "").strip()
            if normalized_sample_id and entry_sample_id != normalized_sample_id:
                continue
            if normalized_file_name and entry_file_name and entry_file_name != normalized_file_name:
                continue
            entry_section_id = str(entry.get("source_section_id") or "").strip()
            entry_section_path = str(entry.get("section_path") or entry.get("heading_path") or "").strip()
            section_id_matched = bool(normalized_section_id and entry_section_id == normalized_section_id)
            section_path_matched = bool(
                normalized_section_path
                and (
                    entry_section_path == normalized_section_path
                    or entry_section_path.startswith(f"{normalized_section_path} >")
                )
            )
            if normalized_section_id or normalized_section_path:
                if not section_id_matched and not section_path_matched:
                    continue
            result = dict(entry)
            result["score"] = round(float(base_score or 0.75), 4)
            result["reason"] = "full_section_source_block"
            result["reason_trace"] = ["full_section_source_block"]
            matches.append(result)

        def _order_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
            def _int_value(value: Any, default: int = 0) -> int:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return default

            return (
                _int_value(item.get("page_no"), 0),
                _int_value(item.get("chunk_index"), 0),
                _int_value(item.get("subchunk_index"), 0),
                str(item.get("heading_path") or item.get("section_path") or ""),
            )

        matches.sort(key=_order_key)
        return matches[: max(1, int(top_k or 1))]

    def _collect_block_candidates(
        self,
        *,
        block_entries: list[dict[str, Any]],
        query: str,
        query_terms: list[str],
        title_terms: list[str],
        query_intent_terms: list[str],
        equipment_intent_terms: list[str],
        context_terms: set[str],
        section_title: str,
        term_lexicon: dict[str, tuple[str, ...]] | None,
        target_taxonomy: dict[str, Any],
        sample_ids: set[str] | None,
        library_tracks: set[str] | None,
        section_ids: set[str] | None,
        section_path_prefixes: set[str] | None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for entry in block_entries:
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            if sample_ids and str(entry.get("sample_id") or "") not in sample_ids:
                continue
            section_id = str(entry.get("source_section_id") or "").strip()
            section_path = str(entry.get("section_path") or entry.get("heading_path") or "").strip()
            if section_ids and section_id not in section_ids:
                continue
            if section_path_prefixes and not any(section_path.startswith(prefix) for prefix in section_path_prefixes if prefix):
                continue
            score, reasons = self._score_block(
                query=query,
                query_terms=query_terms,
                title_terms=title_terms,
                query_intent_terms=query_intent_terms,
                equipment_intent_terms=equipment_intent_terms,
                context_terms=context_terms,
                entry=entry,
                section_title=section_title,
                term_lexicon=term_lexicon,
                target_taxonomy=target_taxonomy,
            )
            if score <= 0:
                continue
            candidates.append(
                {
                    **entry,
                    "_score": float(score),
                    "_reasons": list(reasons),
                    "_score_breakdown": _initialize_retrieval_score_breakdown(base_score=float(score)),
                }
            )
        return candidates

    def retrieve_sections(
        self,
        *,
        query: str,
        top_k: int = 6,
        sample_ids: set[str] | None = None,
        library_tracks: set[str] | None = None,
        section_title: str | None = None,
    ) -> list[dict[str, Any]]:
        outline_entries = self._get_outline_entries()
        term_lexicon = self._get_term_lexicon()
        query_terms = _tokenize(query, term_lexicon=term_lexicon)
        title_terms = _tokenize(section_title or "", term_lexicon=term_lexicon)
        _append_unique_terms(query_terms, _extract_detail_hints(query))
        _append_unique_terms(title_terms, _extract_detail_hints(section_title or ""))
        context_terms = {term for term in query_terms if term in LIGHT_CONTEXT_TERMS}
        for hint in extract_taxonomy_hints(query, section_title or ""):
            normalized = hint.casefold()
            if normalized not in query_terms:
                query_terms.append(normalized)
            context_terms.update(
                term for term in _tokenize(normalized, term_lexicon=term_lexicon) if term in LIGHT_CONTEXT_TERMS
            )
        for hint in _extract_detail_hints(query):
            if hint not in query_terms:
                query_terms.append(hint)
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title or query,
                "purpose": query,
                "expected_evidence_types": [],
            }
        )

        section_rows: list[tuple[dict[str, Any], str, str, dict[str, Any], str]] = []
        for outline_entry in outline_entries:
            track = str(outline_entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            sample_id = str(outline_entry.get("sample_id") or "").strip()
            if sample_ids and sample_id not in sample_ids:
                continue
            raw_sections = outline_entry.get("section_catalog") or outline_entry.get("flat_outline") or []
            sections = _flatten_section_entries(raw_sections)
            for section in sections:
                if not isinstance(section, dict):
                    continue
                retrieval_text = self._build_section_retrieval_text(section)
                section_rows.append((outline_entry, track, sample_id, section, retrieval_text))

        semantic_scores_by_section_id: dict[int, float] = {}
        semantic_query = "\n".join(part for part in (section_title or "", query) if part).strip()
        if self.section_scope_semantic_enabled and self.semantic_scorer.available and section_rows:
            section_texts = [retrieval_text for _outline_entry, _track, _sample_id, _section, retrieval_text in section_rows]
            if hasattr(self.semantic_scorer, "score_many"):
                semantic_scores = self.semantic_scorer.score_many(query=semantic_query, texts=section_texts)
            else:
                semantic_scores = [self.semantic_scorer.score(query=semantic_query, text=text) for text in section_texts]
            for (_outline_entry, _track, _sample_id, section, _retrieval_text), semantic_score in zip(
                section_rows,
                semantic_scores,
            ):
                semantic_scores_by_section_id[id(section)] = float(semantic_score or 0.0)

        candidates: list[dict[str, Any]] = []
        for outline_entry, track, sample_id, section, retrieval_text in section_rows:
            score, reasons = self._score_section_candidate(
                query=query,
                query_terms=query_terms,
                title_terms=title_terms,
                context_terms=context_terms,
                section_title=section_title or "",
                section=section,
                term_lexicon=term_lexicon,
                target_taxonomy=target_taxonomy,
                use_semantic=self.section_scope_semantic_enabled,
                semantic_score_override=semantic_scores_by_section_id.get(id(section)),
            )
            if score <= 0:
                continue
            candidates.append(
                {
                    "sample_id": sample_id,
                    "file_name": outline_entry.get("file_name"),
                    "library_track": track,
                    "section_id": section.get("section_id"),
                    "heading_path": section.get("heading_path") or section.get("section_path"),
                    "section_path": section.get("section_path") or section.get("heading_path"),
                    "source_heading": section.get("source_heading") or section.get("title"),
                    "normalized_heading": section.get("normalized_heading"),
                    "heading_aliases": section.get("heading_aliases") or [],
                    "heading_family": section.get("heading_family") or [],
                    "page_span": section.get("page_span"),
                    "content_span": section.get("content_span"),
                    "section_summary": section.get("section_summary"),
                    "level": section.get("level"),
                    "source_signals": section.get("source_signals") or [],
                    "_score": float(score),
                    "_reasons": list(reasons),
                    "_score_breakdown": _initialize_retrieval_score_breakdown(base_score=float(score)),
                    "_retrieval_text": retrieval_text,
                }
            )
        if self.section_scope_semantic_enabled:
            self._apply_hybrid_rrf_boost(
                candidates=candidates,
                query_text="\n".join(part for part in (section_title or "", query) if part).strip(),
                query_terms=_dedupe_keep_order([*title_terms, *query_terms]),
                term_lexicon=term_lexicon,
                text_builder=lambda item: str(item.get("_retrieval_text") or ""),
                max_boost=HYBRID_RRF_MAX_SECTION_BOOST,
            )
        if self.section_scope_rerank_enabled:
            self._apply_hybrid_rerank_boost(
                candidates=candidates,
                query_text="\n".join(part for part in (section_title or "", query) if part).strip(),
                text_builder=lambda item: str(item.get("_retrieval_text") or ""),
                max_boost=HYBRID_RERANK_MAX_SECTION_BOOST,
            )
        candidates.sort(
            key=lambda item: (
                float(item.get("_score") or 0),
                int(item.get("level") or 0),
                len(str(item.get("heading_path") or "")),
            ),
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        for item in candidates[:top_k]:
            result = {
                key: value
                for key, value in item.items()
                if key not in {"_score", "_reasons", "_retrieval_text", "_score_breakdown"}
            }
            result["score"] = round(float(item.get("_score") or 0), 4)
            result["reason"] = "; ".join(item.get("_reasons") or [])
            result["reason_trace"] = list(item.get("_reasons") or [])
            result["score_breakdown"] = _finalize_retrieval_score_breakdown(
                item.get("_score_breakdown"),
                final_score=float(item.get("_score") or 0),
            )
            results.append(result)
        return results

    def expand_related_blocks(
        self,
        *,
        seed_blocks: list[dict[str, Any]],
        section_title: str,
        top_k: int = 4,
        radius: int = 8,
    ) -> list[dict[str, Any]]:
        if not seed_blocks:
            return []
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title,
                "purpose": section_title,
                "expected_evidence_types": [],
                "asset_required": False,
            }
        )
        seed_by_sample: dict[str, list[int]] = {}
        seed_signatures: set[tuple[str, int]] = set()
        for block in seed_blocks:
            sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
            chunk_index = _neighbor_index(block)
            if not sample_id or chunk_index is None:
                continue
            try:
                chunk_index_int = int(chunk_index)
            except (TypeError, ValueError):
                continue
            seed_signatures.add((sample_id, chunk_index_int))
            seed_by_sample.setdefault(sample_id, []).append(chunk_index_int)

        candidates: list[dict[str, Any]] = []
        for entry in self._load_block_entries():
            sample_id = str(entry.get("sample_id") or "").strip()
            if sample_id not in seed_by_sample:
                continue
            try:
                chunk_index = int(_neighbor_index(entry))
            except (TypeError, ValueError):
                continue
            if (sample_id, chunk_index) in seed_signatures:
                continue
            nearest_distance = min(abs(chunk_index - anchor) for anchor in seed_by_sample[sample_id])
            if nearest_distance > radius:
                continue
            score, reasons = self._score_related_block(
                entry=entry,
                nearest_distance=nearest_distance,
                target_taxonomy=target_taxonomy,
                section_title=section_title,
            )
            if score <= 0:
                continue
            candidates.append({**entry, "score": round(score, 4), "reason": "; ".join(reasons)})

        candidates.sort(
            key=lambda item: (
                float(item.get("score") or 0),
                -abs(int(item.get("chunk_index") or 0)),
            ),
            reverse=True,
        )
        return candidates[:top_k]

    def _build_outline_retrieval_text(self, entry: dict[str, Any]) -> str:
        semantic_retrieval_text = str(entry.get("semantic_retrieval_text") or "").strip()
        if semantic_retrieval_text:
            return semantic_retrieval_text
        flat_outline = entry.get("flat_outline") or []
        heading_paths = [str(item.get("heading_path") or "") for item in flat_outline[:24]]
        parts = [
            str(entry.get("file_name") or ""),
            str(entry.get("profile") or ""),
            " ".join(str(item) for item in (entry.get("top_level_titles") or [])),
            " ".join(path for path in heading_paths if path),
        ]
        return "\n".join(part for part in parts if part).strip()

    def _score_case(
        self,
        *,
        query: str,
        query_terms: list[str],
        entry: dict[str, Any],
        retrieval_text: str,
    ) -> tuple[float, list[str]]:
        text_lower = retrieval_text.casefold()
        score = 0.0
        reasons: list[str] = []
        overlap_terms = [term for term in query_terms if term.casefold() in text_lower]
        if overlap_terms:
            score += min(0.7, len(overlap_terms) * 0.14)
            reasons.append(f"query_overlap={','.join(overlap_terms[:6])}")
        top_titles = [str(item) for item in (entry.get("top_level_titles") or [])]
        if any(any(term.casefold() in title.casefold() for term in query_terms) for title in top_titles):
            score += 0.18
            reasons.append("top_level_title_match")
        headings = entry.get("heading_count") or 0
        if int(headings) >= 20:
            score += 0.05
            reasons.append("rich_outline_structure")
        score += self._semantic_score_adjustment(query=query, candidate_text=retrieval_text, reasons=reasons, weight=0.28)
        return score, reasons

    def _score_block(
        self,
        *,
        query: str,
        query_terms: list[str],
        title_terms: list[str],
        query_intent_terms: list[str],
        equipment_intent_terms: list[str],
        context_terms: set[str],
        entry: dict[str, Any],
        section_title: str,
        term_lexicon: dict[str, tuple[str, ...]] | None = None,
        target_taxonomy: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        content = str(entry.get("content") or "")
        heading_path = str(entry.get("heading_path") or entry.get("section_path") or "")
        section_summary = str(entry.get("section_summary") or "")
        contextualized_block_text = str(entry.get("contextualized_block_text") or "")
        haystack = "\n".join(
            part
            for part in (
                heading_path,
                section_summary,
                contextualized_block_text,
                content,
            )
            if part
        ).casefold()
        score = 0.0
        reasons: list[str] = []
        if heading_looks_like_document_title(heading_path):
            score -= 0.28
            reasons.append("document_title_penalty")
        detail_terms = [term for term in query_terms if term not in title_terms and term not in context_terms]
        context_overlap = [term for term in context_terms if term.casefold() in haystack]
        if context_overlap:
            score += min(0.12, len(context_overlap) * 0.03)
            reasons.append(f"context_overlap={','.join(context_overlap[:6])}")
        overlap_terms = [term for term in detail_terms if term.casefold() in haystack]
        if overlap_terms:
            score += min(0.48, len(overlap_terms) * 0.09)
            reasons.append(f"detail_overlap={','.join(overlap_terms[:6])}")
            section_context_haystack = f"{section_summary}\n{contextualized_block_text}".casefold()
            if section_context_haystack.strip() and any(term.casefold() in section_context_haystack for term in overlap_terms):
                score += min(0.08, len(overlap_terms) * 0.02)
                reasons.append("section_context_match")
        elif detail_terms:
            score -= 0.04
            reasons.append("detail_mismatch_penalty")
        direct_heading_text = "\n".join(
            part
            for part in (
                heading_path,
                str(entry.get("source_heading") or ""),
                str(entry.get("normalized_heading") or ""),
                str(entry.get("normalized_section_path") or ""),
            )
            if part
        )
        direct_heading_haystack = direct_heading_text.casefold()
        broad_heading = str(entry.get("source_heading") or heading_path)
        is_broad_heading = _looks_like_broad_parent_heading(broad_heading)
        equipment_type = str(entry.get("equipment_type") or "generic")
        target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic") if target_taxonomy else "generic"
        target_section_type = str(target_taxonomy.get("section_type") or "unknown") if target_taxonomy else "unknown"
        effective_target_equipment_types = _effective_target_equipment_types(
            target_equipment_type=target_equipment_type,
            compatibility_hint_text="\n".join(part for part in (section_title, query) if part),
        )
        heading_detail_overlap = [term for term in detail_terms if term.casefold() in direct_heading_haystack]
        if heading_detail_overlap:
            score += min(0.24, len(heading_detail_overlap) * 0.08)
            reasons.append(f"heading_detail_overlap={','.join(heading_detail_overlap[:6])}")
            leaf_heading = str(entry.get("source_heading") or "")
            if leaf_heading and any(term.casefold() in leaf_heading.casefold() for term in heading_detail_overlap):
                score += min(0.06, len(heading_detail_overlap) * 0.02)
                reasons.append("leaf_heading_detail_match")
        equipment_intent_overlap = [term for term in equipment_intent_terms if term.casefold() in haystack]
        equipment_intent_heading_overlap = [term for term in equipment_intent_terms if term.casefold() in direct_heading_haystack]
        if equipment_intent_heading_overlap:
            score += min(0.18, len(equipment_intent_heading_overlap) * 0.06)
            reasons.append(f"equipment_intent_heading_overlap={','.join(equipment_intent_heading_overlap[:6])}")
        elif equipment_intent_overlap:
            score += min(0.1, len(equipment_intent_overlap) * 0.04)
            reasons.append(f"equipment_intent_overlap={','.join(equipment_intent_overlap[:6])}")
        elif equipment_intent_terms:
            score -= 0.24
            reasons.append("missing_equipment_intent_penalty")
            if target_equipment_type != "generic" and equipment_type not in {"generic", *effective_target_equipment_types}:
                score -= 0.1
                reasons.append("explicit_equipment_mismatch_penalty")
        query_intent_overlap = [term for term in query_intent_terms if term.casefold() in haystack]
        query_intent_heading_overlap = [term for term in query_intent_terms if term.casefold() in direct_heading_haystack]
        if query_intent_heading_overlap:
            score += min(0.26, len(query_intent_heading_overlap) * 0.1)
            reasons.append(f"query_intent_heading_overlap={','.join(query_intent_heading_overlap[:6])}")
            leaf_heading = str(entry.get("source_heading") or "")
            if leaf_heading and any(term.casefold() in leaf_heading.casefold() for term in query_intent_heading_overlap):
                score += min(0.08, len(query_intent_heading_overlap) * 0.03)
                reasons.append("leaf_query_intent_match")
        elif query_intent_overlap:
            score += min(0.12, len(query_intent_overlap) * 0.05)
            reasons.append(f"query_intent_overlap={','.join(query_intent_overlap[:6])}")
            if is_broad_heading:
                score -= 0.16
                reasons.append("broad_heading_query_intent_context_penalty")
                if not query_intent_heading_overlap:
                    score -= 0.14
                    reasons.append("broad_heading_without_heading_intent_penalty")
        elif query_intent_terms:
            score -= 0.08
            reasons.append("query_intent_mismatch_penalty")
            if is_broad_heading:
                score -= 0.06
                reasons.append("broad_heading_query_intent_penalty")
        strict_query_intent_terms = [term for term in query_intent_terms if term in STRICT_QUERY_INTENT_TERMS]
        if strict_query_intent_terms and not query_intent_overlap:
            score -= min(0.18, len(strict_query_intent_terms) * 0.12)
            reasons.append(f"strict_query_intent_mismatch={','.join(strict_query_intent_terms[:6])}")
        requested_figure_terms = [term for term in query_terms if term in FIGURE_HEADING_INTENT_TOKENS]
        heading_figure_terms = [term for term in FIGURE_HEADING_INTENT_TOKENS if term in direct_heading_haystack]
        if heading_figure_terms and not requested_figure_terms:
            score -= min(0.18, len(heading_figure_terms) * 0.06)
            reasons.append(f"non_requested_figure_section_penalty={','.join(heading_figure_terms[:6])}")
        title_score, title_reasons = self._score_section_title_alignment(
            section_title=section_title,
            entry=entry,
            heading_path=heading_path,
            term_lexicon=term_lexicon,
        )
        score += title_score
        reasons.extend(title_reasons)
        anchor_score, anchor_reasons = self._score_block_section_anchor_alignment(
            section_title=section_title,
            title_terms=title_terms,
            entry=entry,
        )
        score += anchor_score
        reasons.extend(anchor_reasons)
        if query_intent_terms and not query_intent_overlap:
            strong_title_alignment = any(
                marker in title_reasons
                for marker in (
                    "normalized_section_title_match",
                    "section_path_title_match",
                    "heading_alias_match",
                    "section_title_match",
                )
            )
            strong_anchor_alignment = "section_anchor_title_match" in anchor_reasons
            if strong_title_alignment or strong_anchor_alignment:
                score -= 0.3 + (0.16 if strong_title_alignment else 0.0) + (0.16 if strong_anchor_alignment else 0.0)
                reasons.append("section_title_without_query_intent_penalty")
        noise_adjustment, noise_reasons = _section_heading_noise_adjustment(
            section_title=section_title,
            normalized_heading=str(entry.get("normalized_heading") or normalize_section_heading(heading_path)),
            signals={
                str(item)
                for item in (
                    entry.get("source_signals")
                    or entry.get("signals")
                    or []
                )
                if item
            },
        )
        score += noise_adjustment
        reasons.extend(noise_reasons)
        reuse_level = str(entry.get("reuse_level") or "")
        if reuse_level == "high":
            if is_broad_heading and query_intent_terms and not query_intent_heading_overlap:
                score -= 0.04
                reasons.append("broad_heading_high_reuse_penalty")
            else:
                score += 0.08
                reasons.append("high_reuse_level")
        if not bool(entry.get("front_matter")):
            score += 0.04
        if str(entry.get("content_risk_level") or "") == "low":
            score += 0.05
            reasons.append("low_risk_block")
        section_type = str(entry.get("section_type") or "unknown")
        content_form = str(entry.get("content_form") or "narrative")
        if content_form == "page_furniture":
            score -= 0.25
            reasons.append("page_furniture_penalty")
        if (
            (query_intent_terms or strict_query_intent_terms or equipment_intent_terms)
            and section_type in SUPPORT_SECTION_REQUEST_SEEDS
            and not _query_requests_support_section(
                query=query,
                section_title=section_title,
                section_type=section_type,
                term_lexicon=term_lexicon,
            )
        ):
            score -= 0.22
            reasons.append("non_requested_support_section_penalty")
        if target_taxonomy:
            preferred_forms = set(target_taxonomy.get("preferred_content_forms") or [])
            support_forms = set(target_taxonomy.get("support_content_forms") or support_content_forms(target_section_type))
            if target_section_type != "company_profile" and section_type == "company_profile":
                score -= 0.18
                reasons.append("company_profile_penalty")
            if target_section_type != "unknown" and section_type == target_section_type:
                score += 0.2
                reasons.append("section_type_match")
            elif section_type in related_section_types(target_section_type):
                score += 0.1
                reasons.append("related_section_type_match")
            if target_equipment_type != "generic" and equipment_type in effective_target_equipment_types:
                score += 0.1
                reasons.append("equipment_type_match")
            elif target_equipment_type != "generic" and equipment_type not in {"generic", *effective_target_equipment_types}:
                score -= 0.1
                reasons.append("equipment_type_mismatch_penalty")
            if preferred_forms and content_form in preferred_forms:
                score += 0.08
                reasons.append("content_form_match")
            elif support_forms and content_form in support_forms:
                score += 0.04
                reasons.append("support_content_form_match")
            if target_section_type in {"overall_solution", "project_overview"} and content_form_is_table(content_form):
                score -= 0.14
                reasons.append("narrative_section_table_penalty")
            if target_section_type in {"overall_solution", "project_overview"} and content_form == "formula":
                score -= 0.12
                reasons.append("narrative_section_formula_penalty")
            heading_adjustment, heading_reasons = heading_focus_adjustment(
                target_section_type=target_section_type,
                heading_text=heading_path,
            )
            score += heading_adjustment
            reasons.extend(heading_reasons)
            heading_lower = heading_path.casefold()
            if target_section_type == "communication_interface":
                if any(token in heading_lower for token in ("上位机", "控制信号接口", "接口说明", "点表", "modbus", "rs485")):
                    score += 0.12
                    reasons.append("interface_heading_bonus")
                if any(token in heading_lower for token in ("性能要求", "整体要求", "技术要求")):
                    score -= 0.16
                    reasons.append("interface_heading_noise_penalty")
        candidate_text = self._build_block_retrieval_text(entry)
        semantic_query = "\n".join(part for part in (section_title, query) if part).strip()
        score += self._semantic_score_adjustment(query=semantic_query, candidate_text=candidate_text, reasons=reasons, weight=0.32)
        return score, reasons

    def _score_block_section_anchor_alignment(
        self,
        *,
        section_title: str,
        title_terms: list[str],
        entry: dict[str, Any],
    ) -> tuple[float, list[str]]:
        if not section_title:
            return 0.0, []
        direct_anchor_text = "\n".join(
            part
            for part in (
                str(entry.get("heading_path") or entry.get("section_path") or ""),
                str(entry.get("source_heading") or ""),
            )
            if part
        ).strip()
        contextual_anchor_text = "\n".join(
            part
            for part in (
                str(entry.get("section_summary") or ""),
                str(entry.get("section_retrieval_text") or ""),
            )
            if part
        ).strip()
        if not direct_anchor_text and not contextual_anchor_text:
            return 0.0, []

        score = 0.0
        reasons: list[str] = []
        normalized_title = normalize_section_heading(section_title)
        normalized_direct_anchor = normalize_section_heading(direct_anchor_text)
        normalized_context_anchor = normalize_section_heading(contextual_anchor_text)
        direct_title_match = bool(normalized_title and normalized_title in normalized_direct_anchor)
        contextual_title_match = bool(
            normalized_title and not direct_title_match and normalized_title in normalized_context_anchor
        )
        if direct_title_match:
            score += 0.18
            reasons.append("section_anchor_title_match")
        elif contextual_title_match:
            score += 0.06
            reasons.append("section_anchor_context_match")

        direct_anchor_haystack = direct_anchor_text.casefold()
        direct_anchor_overlap = [term for term in title_terms if term.casefold() in direct_anchor_haystack]
        if direct_anchor_overlap:
            score += min(0.12, len(direct_anchor_overlap) * 0.04)
            reasons.append(f"section_anchor_overlap={','.join(direct_anchor_overlap[:6])}")

        contextual_anchor_haystack = contextual_anchor_text.casefold()
        contextual_anchor_overlap = [
            term
            for term in title_terms
            if term.casefold() in contextual_anchor_haystack and term not in direct_anchor_overlap
        ]
        if contextual_anchor_overlap:
            score += min(0.08, len(contextual_anchor_overlap) * 0.02)
            reasons.append(f"section_anchor_context_overlap={','.join(contextual_anchor_overlap[:6])}")

        if contextual_title_match and not direct_anchor_overlap:
            heading = str(entry.get("source_heading") or entry.get("heading_path") or "")
            if _looks_like_broad_parent_heading(heading):
                score -= 0.08
                reasons.append("contextual_anchor_only_penalty")
        return score, reasons

    def _score_section_title_alignment(
        self,
        *,
        section_title: str,
        entry: dict[str, Any],
        heading_path: str,
        term_lexicon: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[float, list[str]]:
        normalized_section_title = normalize_section_heading(section_title)
        if not normalized_section_title:
            return 0.0, []
        reasons: list[str] = []
        score = 0.0
        normalized_heading = str(entry.get("normalized_heading") or normalize_section_heading(heading_path))
        compact_heading = normalized_heading.replace(" ", "")
        normalized_path = str(entry.get("normalized_section_path") or "")
        path_segments = [segment.strip() for segment in normalized_path.split(">") if segment.strip()]
        aliases = {
            str(item).strip()
            for item in (entry.get("heading_aliases") or build_heading_aliases(entry.get("source_heading") or heading_path))
            if str(item).strip()
        }
        title_aliases = set(build_heading_aliases(section_title))
        if normalized_heading and normalized_heading == normalized_section_title:
            score += 0.34
            reasons.append("normalized_section_title_match")
        elif normalized_section_title in path_segments:
            score += 0.22
            reasons.append("section_path_title_match")
        elif title_aliases & aliases:
            score += 0.22
            reasons.append("heading_alias_match")
        elif section_title and section_title.casefold() in heading_path.casefold():
            score += 0.2
            reasons.append("section_title_match")

        family_score = 0.0
        best_family = 0.0
        for segment in [heading_path, str(entry.get("source_heading") or ""), *path_segments]:
            best_family = max(best_family, heading_family_similarity(section_title, segment))
        if best_family >= 0.2:
            family_score = 0.12
        elif best_family > 0:
            family_score = 0.06
        if family_score:
            score += family_score
            reasons.append("heading_family_match")

        title_terms = [term for term in _tokenize(section_title, term_lexicon=term_lexicon) if term]
        title_overlap = [term for term in title_terms if term.casefold() in heading_path.casefold()]
        if title_overlap:
            score += min(0.12, len(title_overlap) * 0.04)
            reasons.append(f"title_overlap={','.join(title_overlap[:6])}")
        if any(token in normalized_section_title for token in ("系统", "方案")):
            if any(token in compact_heading for token in ("系统方案", "系统结构", "总体方案", "总体设计", "技术架构", "架构", "接口")):
                score += 0.12
                reasons.append("system_overview_heading_bonus")
            if any(
                token in compact_heading
                for token in ("散热", "冷却", "存储", "培训", "节电", "工频运行", "变频运行", "清单", "备品", "维护", "服务", "供方", "安装")
            ):
                score -= 0.14
                reasons.append("system_overview_heading_noise_penalty")
        return score, reasons

    def _score_section_candidate(
        self,
        *,
        query: str,
        query_terms: list[str],
        title_terms: list[str],
        context_terms: set[str],
        section_title: str,
        section: dict[str, Any],
        term_lexicon: dict[str, tuple[str, ...]] | None = None,
        target_taxonomy: dict[str, Any] | None = None,
        use_semantic: bool = True,
        semantic_score_override: float | None = None,
    ) -> tuple[float, list[str]]:
        heading_path = str(section.get("heading_path") or section.get("section_path") or section.get("title") or "")
        normalized_heading = str(section.get("normalized_heading") or normalize_section_heading(heading_path))
        summary_text = str(section.get("section_summary") or "")
        retrieval_text = str(section.get("section_retrieval_text") or "")
        haystack = "\n".join(
            part
            for part in (
                heading_path,
                normalized_heading,
                summary_text,
                retrieval_text,
            )
            if part
        ).casefold()
        detail_terms = [term for term in query_terms if term not in title_terms and term not in context_terms]
        score = 0.0
        reasons: list[str] = []

        title_score, title_reasons = self._score_section_title_alignment(
            section_title=section_title,
            entry={
                "normalized_heading": normalized_heading,
                "normalized_section_path": section.get("normalized_section_path"),
                "heading_aliases": section.get("heading_aliases") or [],
                "source_heading": section.get("source_heading") or section.get("title"),
            },
            heading_path=heading_path,
            term_lexicon=term_lexicon,
        )
        score += title_score
        reasons.extend(title_reasons)

        context_overlap = [term for term in context_terms if term.casefold() in haystack]
        if context_overlap:
            score += min(0.1, len(context_overlap) * 0.03)
            reasons.append(f"context_overlap={','.join(context_overlap[:6])}")
        detail_overlap = [term for term in detail_terms if term.casefold() in haystack]
        if detail_overlap:
            score += min(0.28, len(detail_overlap) * 0.08)
            reasons.append(f"detail_overlap={','.join(detail_overlap[:6])}")
            if summary_text and any(term.casefold() in summary_text.casefold() for term in detail_overlap):
                score += min(0.1, len(detail_overlap) * 0.03)
                reasons.append("section_summary_match")
        elif detail_terms:
            score -= 0.04
            reasons.append("detail_mismatch_penalty")

        signals = {str(item) for item in (section.get("source_signals") or []) if item}
        if {"toc", "parser_heading"} <= signals:
            score += 0.08
            reasons.append("toc_parser_confirmed")
        elif "toc" in signals:
            score += 0.05
            reasons.append("toc_confirmed")

        level = int(section.get("level") or 1)
        if detail_overlap and level >= 2:
            score += 0.04 + min(0.04, max(level - 2, 0) * 0.04)
            reasons.append("detail_section_specificity_bonus")
        if level == 1 and not detail_overlap:
            score += 0.03
            reasons.append("root_section_bonus")

        if target_taxonomy:
            section_taxonomy = infer_target_taxonomy(
                {
                    "title": str(section.get("source_heading") or section.get("title") or ""),
                    "purpose": heading_path,
                    "expected_evidence_types": [],
                }
            )
            target_section_type = str(target_taxonomy.get("section_type") or "unknown")
            section_type = str(section_taxonomy.get("section_type") or "unknown")
            if target_section_type != "unknown" and section_type == target_section_type:
                score += 0.14
                reasons.append("section_type_match")
            elif section_type in related_section_types(target_section_type):
                score += 0.08
                reasons.append("related_section_type_match")
            heading_adjustment, heading_reasons = heading_focus_adjustment(
                target_section_type=target_section_type,
                heading_text=heading_path,
            )
            score += heading_adjustment
            reasons.extend(heading_reasons)

        noise_adjustment, noise_reasons = _section_heading_noise_adjustment(
            section_title=section_title,
            normalized_heading=normalized_heading,
            signals=signals,
        )
        score += noise_adjustment
        reasons.extend(noise_reasons)

        if heading_looks_like_document_title(heading_path):
            score -= 0.22
            reasons.append("document_title_penalty")
        if use_semantic:
            candidate_text = self._build_section_retrieval_text(section)
            semantic_query = "\n".join(part for part in (section_title, query) if part).strip()
            semantic_weight = 0.28 if not detail_overlap else 0.12
            score += self._semantic_score_adjustment(
                query=semantic_query,
                candidate_text=candidate_text,
                reasons=reasons,
                weight=semantic_weight,
                semantic_score_override=semantic_score_override,
            )
        return score, reasons

    def _semantic_score_adjustment(
        self,
        *,
        query: str,
        candidate_text: str,
        reasons: list[str],
        weight: float,
        semantic_score_override: float | None = None,
    ) -> float:
        if semantic_score_override is None and not self.semantic_scorer.available:
            return 0.0
        semantic_score = (
            float(semantic_score_override)
            if semantic_score_override is not None
            else self.semantic_scorer.score(query=query, text=candidate_text)
        )
        if semantic_score <= 0:
            return 0.0
        reasons.append(f"semantic_match={semantic_score:.3f}")
        if semantic_score >= 0.7:
            reasons.append("semantic_high_confidence")
        return min(weight, semantic_score * weight)

    def _hybrid_sparse_score(
        self,
        *,
        query_text: str,
        query_terms: list[str],
        candidate_text: str,
        candidate_term_counts: Counter[str],
        document_frequencies: dict[str, int],
        corpus_size: int,
        avg_doc_length: float,
    ) -> float:
        return bm25_sparse_score(
            query_text=query_text,
            query_terms=query_terms,
            candidate_text=candidate_text,
            candidate_term_counts=candidate_term_counts,
            document_frequencies=document_frequencies,
            corpus_size=corpus_size,
            avg_doc_length=avg_doc_length,
            k1=HYBRID_BM25_K1,
            b=HYBRID_BM25_B,
            phrase_bonus=HYBRID_BM25_PHRASE_BONUS,
        )

    def _apply_hybrid_rrf_boost(
        self,
        *,
        candidates: list[dict[str, Any]],
        query_text: str,
        query_terms: list[str],
        term_lexicon: dict[str, tuple[str, ...]] | None,
        text_builder: Any,
        max_boost: float,
    ) -> None:
        if not candidates or not self.semantic_scorer.available:
            return
        ranked_candidates = sorted(
            candidates,
            key=lambda item: (
                float(item.get("_score") or 0),
                int(item.get("token_count") or 0),
                str(item.get("heading_path") or item.get("file_name") or ""),
            ),
            reverse=True,
        )[:HYBRID_RRF_MAX_RANK_WINDOW]
        if not ranked_candidates:
            return

        sparse_query_terms = _dedupe_keep_order(
            [
                *query_terms,
                *[hint for hint in _extract_detail_hints(query_text) if hint],
            ]
        )
        sparse_candidates: list[dict[str, Any]] = []
        semantic_candidates: list[dict[str, Any]] = []
        candidate_corpus: list[tuple[dict[str, Any], str, Counter[str]]] = []
        for candidate in ranked_candidates:
            reasons = list(candidate.get("_reasons") or [])
            if _has_reason_prefix(reasons, *HYBRID_RRF_EXCLUSION_REASON_PREFIXES):
                continue
            if _is_detached_fragment_candidate(candidate):
                continue
            candidate_text = str(text_builder(candidate) or "").strip()
            if not candidate_text:
                continue
            candidate_terms = _dedupe_keep_order(
                [
                    *_tokenize(candidate_text, term_lexicon=term_lexicon),
                    *[hint for hint in _extract_detail_hints(candidate_text) if hint],
                ]
            )
            candidate_counts: Counter[str] = Counter(candidate_terms)
            candidate_corpus.append((candidate, candidate_text, candidate_counts))
        if not candidate_corpus:
            return

        document_frequencies: dict[str, int] = {
            term: sum(1 for _candidate, _text, counts in candidate_corpus if counts.get(term, 0) > 0)
            for term in sparse_query_terms
        }
        avg_doc_length = sum(sum(counts.values()) for _candidate, _text, counts in candidate_corpus) / max(len(candidate_corpus), 1)

        candidate_texts = [candidate_text for _candidate, candidate_text, _candidate_counts in candidate_corpus]
        if hasattr(self.semantic_scorer, "score_many"):
            semantic_scores = self.semantic_scorer.score_many(query=query_text, texts=candidate_texts)
        else:
            semantic_scores = [
                self.semantic_scorer.score(query=query_text, text=candidate_text)
                for candidate_text in candidate_texts
            ]

        for (candidate, candidate_text, candidate_counts), semantic_score in zip(candidate_corpus, semantic_scores):
            sparse_score = self._hybrid_sparse_score(
                query_text=query_text,
                query_terms=sparse_query_terms,
                candidate_text=candidate_text,
                candidate_term_counts=candidate_counts,
                document_frequencies=document_frequencies,
                corpus_size=len(candidate_corpus),
                avg_doc_length=avg_doc_length,
            )
            candidate["_hybrid_sparse_score"] = sparse_score
            candidate["_hybrid_semantic_score"] = semantic_score
            breakdown = _ensure_retrieval_score_breakdown(candidate)
            breakdown["sparse"] = round(float(sparse_score), 4)
            breakdown["semantic"] = round(float(semantic_score), 4)
            if sparse_score >= HYBRID_RRF_MIN_SPARSE_SCORE:
                sparse_candidates.append(candidate)
            if semantic_score >= HYBRID_RRF_MIN_SEMANTIC_SCORE:
                semantic_candidates.append(candidate)

        if not sparse_candidates or not semantic_candidates:
            return

        sparse_ranks = {
            id(candidate): rank
            for rank, candidate in enumerate(
                sorted(
                    sparse_candidates,
                    key=lambda item: (
                        float(item.get("_hybrid_sparse_score") or 0),
                        float(item.get("_score") or 0),
                        int(item.get("token_count") or 0),
                    ),
                    reverse=True,
                ),
                start=1,
            )
        }
        semantic_ranks = {
            id(candidate): rank
            for rank, candidate in enumerate(
                sorted(
                    semantic_candidates,
                    key=lambda item: (
                        float(item.get("_hybrid_semantic_score") or 0),
                        float(item.get("_score") or 0),
                        int(item.get("token_count") or 0),
                    ),
                    reverse=True,
                ),
                start=1,
            )
        }
        top_rrf = (1.0 / (HYBRID_RRF_K + 1)) * 2.0
        if top_rrf <= 0:
            return

        for candidate in ranked_candidates:
            candidate_id = id(candidate)
            sparse_rank = sparse_ranks.get(candidate_id)
            semantic_rank = semantic_ranks.get(candidate_id)
            if sparse_rank is None or semantic_rank is None:
                continue
            rrf_score = (1.0 / (HYBRID_RRF_K + sparse_rank)) + (1.0 / (HYBRID_RRF_K + semantic_rank))
            boost = min(max_boost, max_boost * (rrf_score / top_rrf))
            if boost <= 0:
                continue
            candidate["_score"] = float(candidate.get("_score") or 0) + boost
            breakdown = _ensure_retrieval_score_breakdown(candidate)
            breakdown["hybrid_rrf"] = round(float(breakdown.get("hybrid_rrf") or 0.0) + boost, 4)
            candidate["_reasons"] = [
                *list(candidate.get("_reasons") or []),
                f"hybrid_rrf_boost={boost:.3f}",
                "hybrid_rrf_sources=sparse,semantic",
            ]

    def _apply_hybrid_rerank_boost(
        self,
        *,
        candidates: list[dict[str, Any]],
        query_text: str,
        text_builder: Any,
        max_boost: float,
    ) -> None:
        if not candidates or not self.reranker.available:
            return
        ranked_candidates = sorted(
            candidates,
            key=lambda item: (
                float(item.get("_score") or 0),
                int(item.get("token_count") or 0),
                str(item.get("heading_path") or item.get("file_name") or ""),
            ),
            reverse=True,
        )[:HYBRID_RERANK_MAX_RANK_WINDOW]
        rerank_candidates: list[dict[str, Any]] = []
        rerank_texts: list[str] = []
        for candidate in ranked_candidates:
            reasons = list(candidate.get("_reasons") or [])
            if _has_reason_prefix(reasons, *HYBRID_RRF_EXCLUSION_REASON_PREFIXES):
                continue
            if _is_detached_fragment_candidate(candidate):
                continue
            candidate_text = str(text_builder(candidate) or "").strip()
            if not candidate_text:
                continue
            rerank_candidates.append(candidate)
            rerank_texts.append(candidate_text)
        if not rerank_candidates:
            return

        rerank_scores = [max(0.0, min(1.0, float(score))) for score in self.reranker.score_many(query=query_text, texts=rerank_texts)]
        for candidate, rerank_score in zip(rerank_candidates, rerank_scores):
            breakdown = _ensure_retrieval_score_breakdown(candidate)
            breakdown["rerank"] = round(float(rerank_score), 4)
        ranked_indices = [
            index
            for index, score in sorted(
                enumerate(rerank_scores),
                key=lambda item: (
                    item[1],
                    float(rerank_candidates[item[0]].get("_score") or 0),
                    int(rerank_candidates[item[0]].get("token_count") or 0),
                ),
                reverse=True,
            )
            if score >= HYBRID_RERANK_MIN_SCORE
        ]
        if not ranked_indices:
            return

        top_rrf = 1.0 / (HYBRID_RERANK_K + 1)
        if top_rrf <= 0:
            return
        for rank, index in enumerate(ranked_indices, start=1):
            candidate = rerank_candidates[index]
            rerank_score = rerank_scores[index]
            breakdown = _ensure_retrieval_score_breakdown(candidate)
            rrf_score = 1.0 / (HYBRID_RERANK_K + rank)
            boost = min(max_boost, max_boost * (rrf_score / top_rrf) * rerank_score)
            if boost <= 0:
                continue
            candidate["_score"] = float(candidate.get("_score") or 0) + boost
            breakdown["hybrid_rerank"] = round(float(breakdown.get("hybrid_rerank") or 0.0) + boost, 4)
            candidate["_reasons"] = [
                *list(candidate.get("_reasons") or []),
                f"hybrid_rerank_score={rerank_score:.3f}",
                f"hybrid_rerank_boost={boost:.3f}",
            ]

    def _build_section_retrieval_text(self, section: dict[str, Any]) -> str:
        semantic_retrieval_text = str(section.get("semantic_retrieval_text") or "").strip()
        if semantic_retrieval_text:
            return semantic_retrieval_text
        parts = [
            str(section.get("heading_path") or section.get("section_path") or section.get("title") or ""),
            str(section.get("normalized_heading") or ""),
            str(section.get("section_summary") or ""),
            str(section.get("contextual_text") or ""),
            str(section.get("section_retrieval_text") or ""),
        ]
        return "\n".join(part for part in parts if part).strip()

    def _build_block_retrieval_text(self, entry: dict[str, Any]) -> str:
        semantic_retrieval_text = str(entry.get("semantic_retrieval_text") or "").strip()
        if semantic_retrieval_text:
            return semantic_retrieval_text
        parts = [
            str(entry.get("heading_path") or entry.get("section_path") or ""),
            str(entry.get("section_summary") or ""),
            str(entry.get("contextual_text") or ""),
            str(entry.get("contextualized_block_text") or ""),
            str(entry.get("content") or "")[:1200],
        ]
        return "\n".join(part for part in parts if part).strip()

    def _score_related_block(
        self,
        *,
        entry: dict[str, Any],
        nearest_distance: int,
        target_taxonomy: dict[str, Any],
        section_title: str,
    ) -> tuple[float, list[str]]:
        score = max(0.0, 0.38 - (nearest_distance * 0.04))
        reasons = [f"neighbor_distance={nearest_distance}"]
        if bool(entry.get("front_matter")):
            score -= 0.18
            reasons.append("front_matter_penalty")
        if heading_looks_like_document_title(str(entry.get("heading_path") or "")):
            score -= 0.22
            reasons.append("document_title_penalty")
        if str(entry.get("content_risk_level") or "") == "low":
            score += 0.05
            reasons.append("low_risk_block")
        section_type = str(entry.get("section_type") or "unknown")
        equipment_type = str(entry.get("equipment_type") or "generic")
        content_form = str(entry.get("content_form") or "narrative")
        target_section_type = str(target_taxonomy.get("section_type") or "unknown")
        target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic")
        effective_target_equipment_types = _effective_target_equipment_types(
            target_equipment_type=target_equipment_type,
            compatibility_hint_text=section_title,
        )
        preferred_forms = set(target_taxonomy.get("preferred_content_forms") or [])
        support_forms = set(target_taxonomy.get("support_content_forms") or support_content_forms(target_section_type))

        if target_section_type != "unknown" and section_type == target_section_type:
            score += 0.22
            reasons.append("section_type_match")
        elif section_type in related_section_types(target_section_type):
            score += 0.1
            reasons.append("related_section_type_match")
        elif section_type == "unknown":
            score -= 0.04
            reasons.append("unknown_section_type_penalty")
        else:
            score -= 0.08
            reasons.append("section_type_mismatch_penalty")

        if target_equipment_type != "generic" and equipment_type in effective_target_equipment_types:
            score += 0.1
            reasons.append("equipment_type_match")
        elif target_equipment_type != "generic" and equipment_type not in {"generic", *effective_target_equipment_types}:
            score -= 0.06
            reasons.append("equipment_type_mismatch_penalty")

        if preferred_forms and content_form in preferred_forms:
            score += 0.08
            reasons.append("content_form_match")
        elif support_forms and content_form in support_forms:
            score += 0.04
            reasons.append("support_content_form_match")
        elif content_form == "page_furniture":
            score -= 0.25
            reasons.append("page_furniture_penalty")
        heading_adjustment, heading_reasons = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=str(entry.get("heading_path") or ""),
        )
        score += heading_adjustment
        reasons.extend(heading_reasons)
        heading_lower = str(entry.get("heading_path") or "").casefold()
        if target_section_type == "communication_interface":
            if any(token in heading_lower for token in ("上位机", "控制信号接口", "接口说明", "点表", "modbus", "rs485")):
                score += 0.12
                reasons.append("interface_heading_bonus")
            if any(token in heading_lower for token in ("性能要求", "整体要求", "技术要求")):
                score -= 0.16
                reasons.append("interface_heading_noise_penalty")
        return score, reasons

    def _load_outline_entries(self) -> list[dict[str, Any]]:
        return [item for item in (self._load_outline_payload().get("entries") or []) if isinstance(item, dict)]

    def _load_block_entries(self) -> list[dict[str, Any]]:
        return [item for item in (self._load_block_payload().get("entries") or []) if isinstance(item, dict)]

    def _load_outline_payload(self) -> dict[str, Any]:
        if not self.outline_library_path.exists():
            return {}
        payload = json.loads(self.outline_library_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _load_block_payload(self) -> dict[str, Any]:
        if not self.block_library_path.exists():
            return {}
        payload = json.loads(self.block_library_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _get_outline_entries(self) -> list[dict[str, Any]]:
        return [item for item in (self._get_outline_payload().get("entries") or []) if isinstance(item, dict)]

    def _get_block_entries(self) -> list[dict[str, Any]]:
        return [item for item in (self._get_block_payload().get("entries") or []) if isinstance(item, dict)]

    def _get_term_lexicon(self) -> dict[str, tuple[str, ...]]:
        if self._term_lexicon_cache is None:
            materialized_lexicon = self._extract_materialized_term_lexicon()
            if materialized_lexicon:
                self._term_lexicon_cache = materialized_lexicon
            else:
                self._term_lexicon_cache = build_corpus_term_lexicon(
                    outline_entries=self._get_outline_entries(),
                    block_entries=self._get_block_entries(),
                )
        return self._term_lexicon_cache

    def _get_outline_payload(self) -> dict[str, Any]:
        if self._outline_payload_cache is None:
            self._outline_payload_cache = self._load_outline_payload()
        return self._outline_payload_cache

    def _get_block_payload(self) -> dict[str, Any]:
        if self._block_payload_cache is None:
            self._block_payload_cache = self._load_block_payload()
        return self._block_payload_cache

    def _extract_materialized_term_lexicon(self) -> dict[str, tuple[str, ...]]:
        outline_lexicon = _normalize_term_lexicon_payload(self._get_outline_payload().get("term_lexicon"))
        if outline_lexicon:
            return outline_lexicon
        block_lexicon = _normalize_term_lexicon_payload(self._get_block_payload().get("term_lexicon"))
        if block_lexicon:
            return block_lexicon
        return {}


def _normalize_term_lexicon_payload(payload: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, tuple[str, ...]] = {}
    for alias, group in payload.items():
        alias_text = str(alias or "").strip().casefold()
        if not alias_text or not isinstance(group, (list, tuple)):
            continue
        values = tuple(
            item
            for item in _dedupe_keep_order([str(candidate or "").strip().casefold() for candidate in group])
            if item
        )
        if len(values) < 2:
            continue
        normalized[alias_text] = values
    return normalized


def _neighbor_index(entry: dict[str, Any]) -> Any:
    if isinstance(entry, dict) and entry.get("subchunk_index") is not None:
        return entry.get("subchunk_index")
    if isinstance(entry, dict):
        return entry.get("chunk_index")
    return None


def _initialize_retrieval_score_breakdown(*, base_score: float) -> dict[str, float]:
    rounded_base = round(float(base_score or 0.0), 4)
    return {
        "base": rounded_base,
        "sparse": 0.0,
        "semantic": 0.0,
        "hybrid_rrf": 0.0,
        "rerank": 0.0,
        "hybrid_rerank": 0.0,
        "final": rounded_base,
    }


def _ensure_retrieval_score_breakdown(candidate: dict[str, Any]) -> dict[str, float]:
    breakdown = candidate.get("_score_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = _initialize_retrieval_score_breakdown(base_score=float(candidate.get("_score") or 0.0))
        candidate["_score_breakdown"] = breakdown
    return breakdown


def _finalize_retrieval_score_breakdown(breakdown: Any, *, final_score: float) -> dict[str, float]:
    normalized = _initialize_retrieval_score_breakdown(base_score=0.0)
    if isinstance(breakdown, dict):
        for key in tuple(normalized.keys()):
            try:
                normalized[key] = round(float(breakdown.get(key) or 0.0), 4)
            except (TypeError, ValueError):
                continue
    normalized["final"] = round(float(final_score or 0.0), 4)
    if normalized["base"] <= 0:
        base_fallback = normalized["final"] - normalized["hybrid_rrf"] - normalized["hybrid_rerank"]
        normalized["base"] = round(max(0.0, base_fallback), 4)
    return normalized


def build_outline_examples(case_candidates: list[dict[str, Any]], *, max_cases: int = 3, max_titles: int = 10) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for candidate in case_candidates[:max_cases]:
        top_level_titles = _dedupe_keep_order(
            [str(item).strip() for item in (candidate.get("top_level_titles") or []) if str(item).strip()]
        )
        examples.append(
            {
                "file_name": candidate.get("file_name"),
                "score": candidate.get("score"),
                "top_level_titles": top_level_titles[:max_titles],
            }
        )
    return examples


def _flatten_section_entries(sections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        flattened.append(section)
        children = section.get("children") or []
        if children:
            flattened.extend(_flatten_section_entries(children))
    return flattened


def _select_block_scope_candidates(section_candidates: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    if not section_candidates:
        return []
    candidates = [
        item
        for item in section_candidates
        if str(item.get("section_path") or item.get("heading_path") or "").strip()
    ]
    if not candidates:
        return []

    def _supports_narrow_scope(item: dict[str, Any]) -> bool:
        reason_text = str(item.get("reason") or "")
        return int(item.get("level") or 1) >= 2 and any(
            token in reason_text
            for token in (
                "detail_overlap=",
                "section_summary_match",
                "semantic_match=",
                "heading_focus_match",
                "section_type_match",
                "related_section_type_match",
                "heading_alias_match",
                "section_title_match",
                "normalized_section_title_match",
            )
        )

    strict_specifics = [item for item in candidates if _supports_narrow_scope(item)]
    if strict_specifics:
        best_score = max(float(item.get("score") or 0) for item in strict_specifics)
        scoped = [item for item in strict_specifics if float(item.get("score") or 0) + 0.08 >= best_score]
    else:
        specific_candidates = [item for item in candidates if int(item.get("level") or 1) >= 2]
        if specific_candidates:
            best_score = max(float(item.get("score") or 0) for item in specific_candidates)
            scoped = [item for item in specific_candidates if float(item.get("score") or 0) + 0.10 >= best_score]
        else:
            scoped = candidates[:1]

    scoped = sorted(
        scoped,
        key=lambda item: (
            float(item.get("score") or 0),
            int(item.get("level") or 0),
            len(str(item.get("section_path") or item.get("heading_path") or "")),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    selected_paths: list[str] = []
    for item in scoped:
        path = str(item.get("section_path") or item.get("heading_path") or "").strip()
        if not path:
            continue
        if path in selected_paths:
            continue
        if any(
            _is_descendant_section_path(parent_path=path, child_path=existing)
            or _is_descendant_section_path(parent_path=existing, child_path=path)
            for existing in selected_paths
        ):
            continue
        selected.append(item)
        selected_paths.append(path)
        if len(selected) >= limit:
            break
    return selected or scoped[:limit]
