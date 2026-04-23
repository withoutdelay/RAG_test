from __future__ import annotations

from functools import lru_cache
import re
from typing import Iterable


_SEPARATOR_PATTERN = re.compile(r"[/+|,_-]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")

DOMAIN_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("变频器", "高压变频器", "高压变频", "变频装置", "变频柜", "变频驱动", "变频驱动系统", "频率转换器", "vfd", "hv-vfd"),
    ("变频软起", "变频软起动", "变频软启动"),
    ("lci", "sfc", "晶闸管换相", "晶闸管换流器", "负载换相"),
    ("软起动", "软启动", "软起动器", "软启动器", "软起"),
    ("启动", "起动", "start-up", "startup", "start up"),
    ("同步", "同期", "synchronization", "synchronize", "synchro-tact"),
    ("单线图", "single line diagram", "single line"),
    ("总布置图", "general arrangement drawing", "general arrangement"),
    ("启动曲线", "启动特性", "start curve", "start-up characteristic", "startup characteristic"),
    ("plc", "可编程逻辑控制器", "可编程控制器"),
    ("dcs", "分布式控制系统", "集散控制系统"),
    ("hmi", "人机界面", "触摸屏", "操作面板"),
    ("lcu", "本地控制单元", "本地控制器"),
    ("通信", "通讯"),
    ("通信接口", "通讯接口"),
    ("总体方案", "整体方案", "总体设计", "整体设计", "系统方案", "系统总体"),
    ("供货清单", "设备清单", "配置清单", "物料清单", "bom"),
    ("主回路", "一次接线", "一次系统", "主接线"),
    ("pt100", "热电阻"),
    ("ups", "不间断电源"),
)


def _normalize_match_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub("", str(text or "").strip().casefold())


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _iter_base_variants(term: str) -> list[str]:
    normalized = str(term or "").strip().casefold()
    if not normalized:
        return []
    variants: list[str] = [normalized]
    compact = _normalize_match_text(normalized)
    if compact and compact != normalized:
        variants.append(compact)
    for part in _SEPARATOR_PATTERN.split(normalized):
        candidate = part.strip()
        if len(candidate) >= 2:
            variants.append(candidate)
    collapsed = _SEPARATOR_PATTERN.sub("", normalized).strip()
    if len(collapsed) >= 2 and collapsed not in variants:
        variants.append(collapsed)
    return _dedupe_keep_order(variants)


_NORMALIZED_GROUPS: tuple[tuple[str, ...], ...] = tuple(
    tuple(_dedupe_keep_order(group))
    for group in DOMAIN_SYNONYM_GROUPS
)
_ALIAS_TO_GROUP: dict[str, tuple[str, ...]] = {
    alias: group
    for group in _NORMALIZED_GROUPS
    for alias in group
}
_SEARCHABLE_ALIASES: tuple[str, ...] = tuple(
    sorted(_ALIAS_TO_GROUP.keys(), key=len, reverse=True)
)


@lru_cache(maxsize=1024)
def expand_domain_term(term: str) -> tuple[str, ...]:
    expanded: list[str] = []
    for variant in _iter_base_variants(term):
        expanded.append(variant)
        group = _ALIAS_TO_GROUP.get(variant)
        if group:
            expanded.extend(group)
        for alias in _SEARCHABLE_ALIASES:
            if alias == variant or alias not in variant:
                continue
            expanded.append(alias)
            expanded.extend(_ALIAS_TO_GROUP[alias])
    return tuple(_dedupe_keep_order(expanded))


def expand_domain_terms(terms: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        expanded.extend(expand_domain_term(str(term or "")))
    return _dedupe_keep_order(expanded)


def extract_domain_terms(text: str) -> list[str]:
    haystack = str(text or "").casefold()
    compact_haystack = _normalize_match_text(text)
    matches: list[str] = []
    for alias in _SEARCHABLE_ALIASES:
        alias_compact = _normalize_match_text(alias)
        if alias in haystack or (alias_compact and alias_compact in compact_haystack):
            matches.extend(_ALIAS_TO_GROUP[alias])
    return _dedupe_keep_order(matches)


def text_contains_domain_term(text: str, term: str) -> bool:
    haystack = str(text or "").casefold()
    compact_haystack = _normalize_match_text(text)
    for candidate in expand_domain_term(term):
        compact_candidate = _normalize_match_text(candidate)
        if candidate in haystack or (compact_candidate and compact_candidate in compact_haystack):
            return True
    return False
