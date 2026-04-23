from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_WHITESPACE_PATTERN = re.compile(r"\s+")
_SURROUNDING_PUNCTUATION_PATTERN = re.compile(r"^[\s:：,，;；/]+|[\s:：,，;；/]+$")
_ACRONYM_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{1,15}$")
_LONG_FORM_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff\-]{2,32}$")
_PARENTHETICAL_ALIAS_PATTERNS = (
    re.compile(
        r"(?P<long>[A-Za-z0-9\u4e00-\u9fff\-]{2,32})\s*[（(](?P<alias>[A-Za-z][A-Za-z0-9-]{1,15})[)）]"
    ),
    re.compile(
        r"(?P<alias>[A-Za-z][A-Za-z0-9-]{1,15})\s*[（(](?P<long>[A-Za-z0-9\u4e00-\u9fff\-]{2,32})[)）]"
    ),
)
_DEFINITIONAL_ALIAS_PATTERNS = (
    re.compile(
        r"(?P<long>[A-Za-z0-9\u4e00-\u9fff\-]{2,32})\s*(?:，|,|:|：)?\s*(?:以下简称|下简称|简称|缩写为|英文缩写为|英文简称为|又称|亦称|即)\s*(?P<alias>[A-Za-z][A-Za-z0-9-]{1,15})"
    ),
    re.compile(
        r"(?P<alias>[A-Za-z][A-Za-z0-9-]{1,15})\s*(?:，|,|:|：)?\s*(?:即|是|指)\s*(?P<long>[A-Za-z0-9\u4e00-\u9fff\-]{2,32})"
    ),
)
_TRIMMABLE_SUFFIXES = (
    "系统",
    "装置",
    "方案",
    "平台",
    "单元",
    "接口",
    "控制器",
)
_LONG_FORM_REJECT_SUBSTRINGS = (
    "采用",
    "用于",
    "负责",
    "实现",
    "提供",
    "进行",
    "发送",
    "保证",
    "邮编",
    "电话",
    "地址",
    "标准",
    "电压",
    "电流",
    "频率",
    "功率",
    "转矩",
    "时间",
    "颜色",
    "尺寸",
)
_LONG_FORM_REJECT_PREFIXES = ("或", "和", "及", "与")


def _normalize_term(text: str) -> str:
    cleaned = _SURROUNDING_PUNCTUATION_PATTERN.sub("", str(text or "").strip())
    cleaned = _WHITESPACE_PATTERN.sub("", cleaned)
    return cleaned.casefold()


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_term(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _looks_like_acronym(text: str) -> bool:
    return bool(_ACRONYM_PATTERN.fullmatch(str(text or "").strip()))


def _looks_like_long_form(text: str) -> bool:
    value = str(text or "").strip()
    if not bool(_LONG_FORM_PATTERN.fullmatch(value)) or _looks_like_acronym(value):
        return False
    if not any("\u4e00" <= char <= "\u9fff" for char in value):
        return False
    if any(marker in value for marker in ("的", "由")):
        return False
    if value.startswith(_LONG_FORM_REJECT_PREFIXES):
        return False
    if any(fragment in value for fragment in _LONG_FORM_REJECT_SUBSTRINGS):
        return False
    return True


def _iter_group_variants(term: str) -> list[str]:
    normalized = _normalize_term(term)
    if not normalized:
        return []
    variants = [normalized]
    for suffix in _TRIMMABLE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
            variants.append(normalized[: -len(suffix)])
    return _dedupe_keep_order(variants)


def _build_group(long_form: str, alias: str) -> list[str]:
    values = [*_iter_group_variants(long_form), *_iter_group_variants(alias)]
    return _dedupe_keep_order(values)


def _iter_alias_groups_from_text(text: str) -> list[list[str]]:
    haystack = str(text or "")
    groups: list[list[str]] = []
    for pattern in (*_PARENTHETICAL_ALIAS_PATTERNS, *_DEFINITIONAL_ALIAS_PATTERNS):
        for match in pattern.finditer(haystack):
            long_form = str(match.group("long") or "").strip()
            alias = str(match.group("alias") or "").strip()
            if not (_looks_like_long_form(long_form) and _looks_like_acronym(alias)):
                continue
            if _normalize_term(long_form) == _normalize_term(alias):
                continue
            groups.append(_build_group(long_form=long_form, alias=alias))
    return groups


def _merge_alias_groups(groups: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    for group in groups:
        if not group:
            continue
        overlapping_indexes = [
            index
            for index, existing in enumerate(merged)
            if set(existing) & set(group)
        ]
        if not overlapping_indexes:
            merged.append(list(group))
            continue
        combined: list[str] = []
        for index in overlapping_indexes:
            combined.extend(merged[index])
        combined.extend(group)
        deduped = _dedupe_keep_order(combined)
        for index in reversed(overlapping_indexes):
            merged.pop(index)
        merged.append(deduped)
    return merged


def _iter_outline_texts(entries: Iterable[dict[str, Any]]) -> Iterable[str]:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        yield str(entry.get("file_name") or "")
        for title in entry.get("top_level_titles") or []:
            yield str(title or "")
        for item in entry.get("flat_outline") or []:
            if isinstance(item, dict):
                yield str(item.get("heading_path") or "")
        for section in _flatten_sections(entry.get("section_catalog") or []):
            yield str(section.get("title") or "")
            yield str(section.get("source_heading") or "")
            yield str(section.get("heading_path") or section.get("section_path") or "")
            yield str(section.get("section_summary") or "")
            yield str(section.get("section_retrieval_text") or "")


def _iter_block_texts(entries: Iterable[dict[str, Any]]) -> Iterable[str]:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        yield str(entry.get("heading_path") or entry.get("section_path") or "")
        yield str(entry.get("source_heading") or "")
        yield str(entry.get("section_summary") or "")


def _flatten_sections(sections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        flattened.append(section)
        children = section.get("children") or []
        if children:
            flattened.extend(_flatten_sections(children))
    return flattened


def build_corpus_term_lexicon(
    *,
    outline_entries: Iterable[dict[str, Any]] = (),
    block_entries: Iterable[dict[str, Any]] = (),
) -> dict[str, tuple[str, ...]]:
    raw_groups: list[list[str]] = []
    for text in [*_iter_outline_texts(outline_entries), *_iter_block_texts(block_entries)]:
        if not text:
            continue
        raw_groups.extend(_iter_alias_groups_from_text(text))
    merged_groups = _merge_alias_groups(raw_groups)
    alias_map: dict[str, tuple[str, ...]] = {}
    for group in merged_groups:
        normalized_group = tuple(_dedupe_keep_order(group))
        if len(normalized_group) < 2:
            continue
        for alias in normalized_group:
            alias_map[alias] = normalized_group
    return alias_map


def expand_terms_with_lexicon(
    terms: Iterable[str],
    lexicon: Mapping[str, tuple[str, ...]] | None,
) -> list[str]:
    if not lexicon:
        return _dedupe_keep_order(terms)
    expanded: list[str] = []
    for term in terms:
        normalized = _normalize_term(term)
        if not normalized:
            continue
        expanded.append(normalized)
        expanded.extend(lexicon.get(normalized, ()))
    return _dedupe_keep_order(expanded)


def extract_terms_from_lexicon(text: str, lexicon: Mapping[str, tuple[str, ...]] | None) -> list[str]:
    if not lexicon:
        return []
    haystack = _normalize_term(text)
    matches: list[str] = []
    for alias, group in lexicon.items():
        if alias and alias in haystack:
            matches.extend(group)
    return _dedupe_keep_order(matches)
