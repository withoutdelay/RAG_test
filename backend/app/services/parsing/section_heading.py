from __future__ import annotations

import re
from typing import Any


CHAPTER_HEADING_PATTERN = re.compile(r"^(?P<ordinal>第[一二三四五六七八九十百零〇0-9]+章)\s*(?P<title>.+?)\s*$")
SECTION_HEADING_PATTERN = re.compile(r"^(?P<ordinal>第[一二三四五六七八九十百零〇0-9]+节)\s*(?P<title>.+?)\s*$")
ARTICLE_HEADING_PATTERN = re.compile(r"^(?P<ordinal>第[一二三四五六七八九十百零〇0-9]+条)\s*(?P<title>.+?)\s*$")
CHINESE_NUMERIC_HEADING_PATTERN = re.compile(r"^(?P<ordinal>[一二三四五六七八九十]+)[、.．]\s*(?P<title>.+?)\s*$")
PAREN_HEADING_PATTERN = re.compile(r"^[（(](?P<ordinal>[一二三四五六七八九十0-9]+)[)）]\s*(?P<title>.+?)\s*$")
ARABIC_DOTTED_HEADING_PATTERN = re.compile(r"^(?P<ordinal>[0-9]+\.[0-9]+(?:\.[0-9]+){0,2})(?:[、.．])?\s*(?P<title>.+?)\s*$")
ARABIC_SIMPLE_HEADING_PATTERN = re.compile(r"^(?P<ordinal>[0-9]{1,2})(?:\s*[、.．]\s*|\s+)(?P<title>.+?)\s*$")
ARABIC_COMPACT_CJK_HEADING_PATTERN = re.compile(r"^(?P<ordinal>[0-9]{1,2})(?P<title>[\u4e00-\u9fff].+?)\s*$")
TOC_FILLER_PATTERN = re.compile(r"[.。·…]{2,}")
HEADING_WHITESPACE_PATTERN = re.compile(r"\s+")
PUNCT_NORMALIZE_PATTERN = re.compile(r"[：:+（）()【】\[\]、,.，。;；/\\\-]+")
CJK_SPLIT_SPACE_PATTERN = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


def normalize_section_heading(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = _strip_heading_ordinal_prefix(raw)
    raw = TOC_FILLER_PATTERN.sub(" ", raw)
    raw = PUNCT_NORMALIZE_PATTERN.sub(" ", raw)
    raw = HEADING_WHITESPACE_PATTERN.sub(" ", raw).strip()
    raw = CJK_SPLIT_SPACE_PATTERN.sub("", raw)
    return raw


def build_heading_aliases(text: str) -> tuple[str, ...]:
    normalized = normalize_section_heading(text)
    if not normalized:
        return ()
    aliases = [normalized]
    compact = normalized.replace(" ", "")
    if compact != normalized and compact not in aliases:
        aliases.append(compact)
    collapsed = compact.replace("及", "")
    if collapsed and collapsed not in aliases:
        aliases.append(collapsed)
    if normalized.startswith("系统") and len(normalized) > 4:
        alias = normalized.removeprefix("系统").strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    if normalized.startswith("总体") and len(normalized) > 4:
        alias = f"系统{normalized}"
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def parse_single_ordinal_token(token: str | None) -> int | None:
    value = str(token or "").strip()
    if not value:
        return None
    value = re.sub(r"^第|[章节条]$", "", value)
    if value.isdigit():
        return int(value)
    return _parse_chinese_numeral(value)


def parse_ordinal_tokens(text: str) -> tuple[int, ...]:
    stripped = str(text or "").strip()
    if not stripped:
        return ()
    if re.fullmatch(r"第[一二三四五六七八九十百零〇0-9]+[章节条]", stripped):
        value = parse_single_ordinal_token(stripped)
        return (value,) if value is not None else ()
    if re.fullmatch(r"[一二三四五六七八九十百零〇0-9]+", stripped):
        value = parse_single_ordinal_token(stripped)
        return (value,) if value is not None else ()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", stripped):
        try:
            return tuple(int(part) for part in stripped.split(".") if part)
        except ValueError:
            return ()
    chapter_match = CHAPTER_HEADING_PATTERN.match(stripped)
    if chapter_match:
        value = parse_single_ordinal_token(chapter_match.group("ordinal"))
        return (value,) if value is not None else ()
    section_match = SECTION_HEADING_PATTERN.match(stripped)
    if section_match:
        value = parse_single_ordinal_token(section_match.group("ordinal"))
        return (value,) if value is not None else ()
    article_match = ARTICLE_HEADING_PATTERN.match(stripped)
    if article_match:
        value = parse_single_ordinal_token(article_match.group("ordinal"))
        return (value,) if value is not None else ()
    chinese_match = CHINESE_NUMERIC_HEADING_PATTERN.match(stripped)
    if chinese_match:
        value = parse_single_ordinal_token(chinese_match.group("ordinal"))
        return (value,) if value is not None else ()
    paren_match = PAREN_HEADING_PATTERN.match(stripped)
    if paren_match:
        value = parse_single_ordinal_token(paren_match.group("ordinal"))
        return (value,) if value is not None else ()
    for pattern in (ARABIC_DOTTED_HEADING_PATTERN, ARABIC_SIMPLE_HEADING_PATTERN, ARABIC_COMPACT_CJK_HEADING_PATTERN):
        match = pattern.match(stripped)
        if not match:
            continue
        ordinal = str(match.group("ordinal") or "")
        parts = [part for part in ordinal.split(".") if part]
        if not parts:
            return ()
        try:
            return tuple(int(part) for part in parts)
        except ValueError:
            return ()
    return ()


def ordinal_prefix_match_length(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    length = 0
    for left_part, right_part in zip(left, right):
        if left_part != right_part:
            break
        length += 1
    return length


def is_valid_ordinal_parent(parent_tokens: tuple[int, ...], child_tokens: tuple[int, ...]) -> bool:
    if not parent_tokens or len(parent_tokens) >= len(child_tokens):
        return False
    return tuple(child_tokens[: len(parent_tokens)]) == parent_tokens


def section_parent_quality_score(node: dict[str, Any]) -> int:
    signals = {str(item) for item in (node.get("source_signals") or []) if item}
    score = 0
    if "toc" in signals:
        score += 6
    if "body_heading" in signals or "markdown_heading" in signals:
        score += 3
    if "parser_heading" in signals:
        score += 2
    score += max(0, 4 - int(node.get("level") or 0))
    return score


def document_title_compare_key(text: str) -> str:
    compact = normalize_section_heading(text).replace(" ", "")
    compact = re.sub(r"\.(?:doc|docx|pdf|ppt|pptx|xls|xlsx)$", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\d{4,}", "", compact)
    for token in ("技术", "方案", "协议", "文档", "文件"):
        compact = compact.replace(token, "")
    return compact


def _strip_heading_ordinal_prefix(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    for pattern in (
        CHAPTER_HEADING_PATTERN,
        SECTION_HEADING_PATTERN,
        ARTICLE_HEADING_PATTERN,
        CHINESE_NUMERIC_HEADING_PATTERN,
        PAREN_HEADING_PATTERN,
        ARABIC_DOTTED_HEADING_PATTERN,
        ARABIC_SIMPLE_HEADING_PATTERN,
        ARABIC_COMPACT_CJK_HEADING_PATTERN,
    ):
        match = pattern.match(stripped)
        if match:
            return str(match.group("title") or "").strip()
    return stripped


def _parse_chinese_numeral(text: str) -> int | None:
    mapping = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    stripped = str(text or "").strip()
    if not stripped:
        return None
    if stripped == "十":
        return 10
    if "十" not in stripped:
        return mapping.get(stripped)
    if stripped.startswith("十"):
        suffix = stripped.removeprefix("十")
        return 10 + mapping.get(suffix, 0)
    if stripped.endswith("十"):
        prefix = stripped.removesuffix("十")
        return mapping.get(prefix, 0) * 10
    prefix, suffix = stripped.split("十", maxsplit=1)
    if prefix not in mapping or suffix not in mapping:
        return None
    return mapping[prefix] * 10 + mapping[suffix]
