from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from app.services.parsing.section_heading import (
    ARABIC_COMPACT_CJK_HEADING_PATTERN,
    ARABIC_DOTTED_HEADING_PATTERN,
    ARABIC_SIMPLE_HEADING_PATTERN,
    ARTICLE_HEADING_PATTERN,
    CHAPTER_HEADING_PATTERN,
    CHINESE_NUMERIC_HEADING_PATTERN,
    PAREN_HEADING_PATTERN,
    SECTION_HEADING_PATTERN,
    build_heading_aliases,
    document_title_compare_key as _document_title_compare_key,
    is_valid_ordinal_parent as _is_valid_ordinal_parent,
    normalize_section_heading,
    ordinal_prefix_match_length as _ordinal_prefix_match_length,
    parse_ordinal_tokens as _parse_ordinal_tokens,
    parse_single_ordinal_token as _parse_single_ordinal_token,
    section_parent_quality_score as _section_parent_quality_score,
)
from app.services.vectorstore.block_taxonomy import heading_looks_like_document_title


MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_HEADING_PATTERN = re.compile(r"^(?:[-*+]\s+)(?P<title>.+?)\s*$")
TOC_TITLE_PATTERN = re.compile(r"^(?:目\s*录(?:\s+index)?|index)$", re.IGNORECASE)
TOC_DOTTED_ENTRY_PATTERN = re.compile(r"^(?P<title>.+?)(?:[.。·…]{2,}|\s{2,})\s*(?P<page>\d{1,4})\s*$")
TOC_LIST_ENTRY_PATTERN = re.compile(r"^(?:[-*+]\s+)?(?P<title>.+?)\s+(?P<page>\d{1,4})\s*$")
FILE_NAME_PATTERN = re.compile(r"\.(?:doc|docx|pdf|ppt|pptx|xls|xlsx)\b", re.IGNORECASE)
PAGE_NUMBER_ONLY_PATTERN = re.compile(r"^\d{1,4}$")
TABLE_ROW_PATTERN = re.compile(r"^\|.*\|$")
PARSER_TRAILING_PAGE_PATTERN = re.compile(r"^(?P<title>.+?)\s+(?P<page>\d{1,4})$")
HEADING_MULTI_WHITESPACE_PATTERN = re.compile(r"[ \t\u3000]+")
LEADING_CHINESE_ENUM_SPACE_PATTERN = re.compile(r"^([一二三四五六七八九十]+[、.．])\s+")
LEADING_PAREN_ENUM_SPACE_PATTERN = re.compile(r"^([（(][一二三四五六七八九十0-9]+[)）])\s+")
FRONT_MATTER_HEADING_TERMS = {
    "方案名称",
    "项目名称",
    "设备名称",
    "采购单位",
    "编制",
    "编制日期",
    "联系方式",
    "技术",
    "商务",
    "联系人",
    "电话",
    "首页",
    "签字页",
    "目录",
}
FRONT_MATTER_HEADING_PREFIX_TERMS = {
    "项目名称",
    "方案名称",
    "设备名称",
    "采购单位",
    "编制日期",
    "联系方式",
    "联系人",
    "电话",
    "投标方 公司全称",
    "招标方 公司全称",
    "购买方 公司全称",
}
UNNUMBERED_HEADING_KEYWORDS = (
    "方案",
    "综述",
    "概述",
    "简介",
    "说明",
    "原理",
    "结构",
    "接口",
    "清单",
    "范围",
    "要求",
    "条件",
    "安排",
    "计算",
    "规划",
    "背景",
)
DATE_LIKE_TEXT_PATTERN = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?$")
DATE_MONTH_TEXT_PATTERN = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月$")
PAGE_FOOTER_PATTERN = re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页$")
INLINE_OPERATION_STATE_PATTERN = re.compile(r"[#＃].*(?:工频运行|变频运行|在.+时)")
COLON_LABEL_PATTERN = re.compile(r"[：:]$")
GENERIC_BODY_ONLY_HEADINGS = {
    "方案综述",
    "项目综述",
    "方案概述",
    "项目概述",
    "总体说明",
}
LOW_VALUE_HEADING_TOKENS = (
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
SENTENCE_LIKE_HEADING_PUNCTUATION = ("，", ",", "；", ";", "。", "！", "？")
SENTENCE_LIKE_HEADING_CLAUSE_DELIMITERS = ("，", ",", "；", ";")
SENTENCE_LIKE_HEADING_MIN_LENGTH = 32
PARAMETER_CLAUSE_VALUE_PATTERN = re.compile(
    r"(?:[<>≤≥~～±])|(?:\d+\s*(?:%|％|℃|°|K|kV|KV|V|kW|KW|W|MW|A|mA|Hz|s|ms|mm|m|次|级)\b)|(?:AC\s*\d)|(?:DC\s*\d)|(?:IP\s*\d+)|(?:\b[Ii][eqt]\b)"
)
PARAMETER_CLAUSE_TERMINAL_PATTERN = re.compile(r"[；;。:]$")
PARAMETER_CLAUSE_KEYWORDS = (
    "环境温度",
    "海拔高度",
    "相对湿度",
    "污染等级",
    "配用电机功率",
    "额定电流",
    "起动电流",
    "起动转矩",
    "额定起动时间",
    "单次起动温升",
    "连续起动次数",
    "起动控制时间",
    "控制电源",
    "不大于",
    "不超过",
    "不少于",
    "不低于",
)
LEAD_IN_LABEL_PATTERNS = (
    re.compile(r"见下面.*(?:图|表)"),
    re.compile(r"图中.*供货范围"),
    re.compile(r"下列.*(?:条件|要求).*(?:正常工作|如下)"),
)
STATUS_OR_REQUIREMENT_CLAUSE_PATTERNS = (
    re.compile(r"^每一种.+需要提供"),
    re.compile(r"^(?:卖方|买方).+(?:免费保修服务|技术服务热线|提供免费咨询服务|负责|保证|到达业主现场)"),
    re.compile(r".+具备.+条件$"),
    re.compile(r"^全部电缆已经"),
    re.compile(r"^电机及附件已安装"),
    re.compile(r"^电源已准备好"),
    re.compile(r"^可得到.+参数$"),
    re.compile(r"^安装应该符合"),
)
EMBEDDED_FIGURE_LABEL_PATTERNS = (
    re.compile(r"总布置图.*generalarrangementdrawing"),
    re.compile(r"外形图.*outlinedrawing"),
    re.compile(r"单线图.*singlelinediagram"),
)
FIGURE_CAPTION_HEADING_PATTERN = re.compile(r"^图\d+.*(?:示意图|结构图|布置图|原理图|图)$")

def build_section_catalog(markdown: str, *, structure_hints: dict[str, Any] | None = None) -> dict[str, Any]:
    lines = markdown.splitlines()
    document_title = _extract_document_title(lines)
    toc_candidates, toc_line_indexes = _extract_toc_candidates(lines)
    parser_candidates = _extract_parser_heading_candidates(
        structure_hints=structure_hints,
        document_title=document_title,
    )
    body_candidates = _extract_body_heading_candidates(
        lines,
        toc_line_indexes=toc_line_indexes,
        document_title=document_title,
    )
    body_candidates = _merge_body_and_parser_candidates(body_candidates, parser_candidates)
    merged_candidates = _merge_section_candidates(
        toc_candidates=toc_candidates,
        body_candidates=body_candidates,
    )
    tree = _build_section_tree(merged_candidates)
    tree = _merge_duplicate_root_sections(tree)
    tree = _prune_root_sections(tree)
    tree = _normalize_inner_sections(tree, document_title=document_title)
    tree = _reindex_section_tree(tree)
    return {
        "document_title": document_title,
        "sections": tree,
    }


def flatten_section_catalog(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        item = dict(section)
        item["children"] = [dict(child) for child in section.get("children") or []]
        flattened.append(item)
        flattened.extend(flatten_section_catalog(section.get("children") or []))
    return flattened


def promote_body_headings(markdown: str, *, structure_hints: dict[str, Any] | None = None) -> str:
    lines = markdown.splitlines()
    document_title = _extract_document_title(lines)
    _toc_candidates, toc_line_indexes = _extract_toc_candidates(lines)
    parser_candidates = _extract_parser_heading_candidates(
        structure_hints=structure_hints,
        document_title=document_title,
    )
    body_candidates = _extract_body_heading_candidates(
        lines,
        toc_line_indexes=toc_line_indexes,
        document_title=document_title,
    )
    body_candidates = _merge_body_and_parser_candidates(body_candidates, parser_candidates)
    promoted_lines_by_index: dict[int, tuple[int, str]] = {}
    has_document_title = bool(document_title)
    for candidate in body_candidates:
        if candidate.get("signal") == "markdown_heading":
            continue
        line_index = candidate.get("line_index")
        if line_index is None:
            continue
        logical_level = int(candidate.get("level") or 1)
        markdown_level = min(6, logical_level + (1 if has_document_title else 0))
        promoted_lines_by_index[int(line_index)] = (
            markdown_level,
            str(candidate.get("title") or "").strip(),
        )

    promoted_lines: list[str] = []
    for index, line in enumerate(lines):
        promotion = promoted_lines_by_index.get(index)
        stripped = line.strip()
        if promotion and stripped and not MARKDOWN_HEADING_PATTERN.match(stripped):
            level, title = promotion
            promoted_lines.append(f"{'#' * level} {title or stripped}")
        else:
            promoted_lines.append(line)
    return "\n".join(promoted_lines)


def _extract_document_title(lines: list[str]) -> str | None:
    for line in lines[:12]:
        stripped = line.strip()
        if not stripped:
            continue
        match = MARKDOWN_HEADING_PATTERN.match(stripped)
        if not match:
            return None
        title = _sanitize_heading_title(match.group(2))
        if not title or TOC_TITLE_PATTERN.match(title):
            return None
        normalized = normalize_section_heading(title)
        if not normalized:
            return None
        if _infer_heading_level(title) is not None:
            return None
        return title
    return None


def _extract_toc_candidates(lines: list[str]) -> tuple[list[dict[str, Any]], set[int]]:
    toc_start = None
    for index, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip()
        if TOC_TITLE_PATTERN.match(stripped):
            toc_start = index
            break
    if toc_start is None:
        return [], set()

    toc_candidates: list[dict[str, Any]] = []
    toc_line_indexes: set[int] = {toc_start}
    blank_streak = 0

    for index in range(toc_start + 1, min(len(lines), toc_start + 200)):
        stripped = lines[index].strip()
        if not stripped:
            blank_streak += 1
            if toc_candidates and blank_streak >= 2:
                break
            continue
        blank_streak = 0
        parsed = _parse_toc_line(stripped)
        if not parsed:
            if toc_candidates and not TABLE_ROW_PATTERN.match(stripped):
                break
            continue
        toc_line_indexes.add(index)
        toc_candidates.append(
            {
                "title": parsed["title"],
                "normalized_heading": parsed["normalized_heading"],
                "heading_aliases": list(build_heading_aliases(parsed["title"])),
                "level": parsed["level"],
                "ordinal": parsed["ordinal"],
                "page_no": parsed["page_no"],
                "line_index": None,
                "order_index": len(toc_candidates),
                "signals": ["toc"],
                "audit_flags": [],
            }
        )

    return toc_candidates, toc_line_indexes


def _extract_parser_heading_candidates(
    *,
    structure_hints: dict[str, Any] | None,
    document_title: str | None,
) -> list[dict[str, Any]]:
    hints = (structure_hints or {}).get("heading_hints") or []
    candidates: list[dict[str, Any]] = []
    for order_index, hint in enumerate(hints):
        if not isinstance(hint, dict):
            continue
        raw_text = _sanitize_heading_title(hint.get("text"))
        if not raw_text:
            continue
        title, page_no = _split_parser_heading_text(raw_text, page_no=hint.get("page_no"))
        if document_title and title == document_title:
            continue
        if _looks_like_front_matter_heading(title):
            continue
        inferred = _infer_heading_level(title)
        if _looks_like_lead_in_label_heading(title):
            continue
        if _looks_like_figure_caption_heading(title):
            continue
        if _looks_like_short_person_name_heading(title):
            continue
        if _looks_like_code_like_heading(title):
            continue
        if _looks_like_parameter_clause_heading(title, inferred=inferred):
            continue
        if inferred is not None and (
            _looks_like_sentence_like_heading(title)
            or _looks_like_embedded_figure_label_heading(title, inferred=inferred)
            or _looks_like_status_or_requirement_clause(title)
        ):
            continue
        if inferred is None and _looks_like_status_or_requirement_clause(title):
            continue
        parser_level = hint.get("parser_level")
        item_type = str(hint.get("item_type") or "")
        if inferred is None and item_type != "SectionHeaderItem":
            if not _is_unnumbered_parser_heading_candidate(title, parser_level=parser_level):
                continue
        if _looks_like_noise_heading(title):
            continue
        level = inferred["level"] if inferred else _map_parser_level_to_heading_level(parser_level)
        if level is None:
            continue
        candidates.append(
            {
                "title": title,
                "normalized_heading": normalize_section_heading(title),
                "heading_aliases": list(build_heading_aliases(title)),
                "level": level,
                "ordinal": inferred["ordinal"] if inferred else None,
                "page_no": page_no,
                "line_index": None,
                "order_index": 10_000 + order_index,
                "signals": ["parser_heading"],
                "signal": "parser_heading",
                "audit_flags": [],
            }
        )
    return _dedupe_body_candidates(candidates)


def _parse_toc_line(stripped: str) -> dict[str, Any] | None:
    cells = [cell.strip() for cell in stripped.split("|") if cell.strip()]
    if not cells:
        cells = [stripped]
    for cell in cells:
        if len(cell) < 3 or PAGE_NUMBER_ONLY_PATTERN.fullmatch(cell):
            continue
        match = TOC_DOTTED_ENTRY_PATTERN.match(cell)
        if match:
            title = _sanitize_heading_title(match.group("title"))
            page_no = int(match.group("page"))
            if _looks_like_noise_heading(title):
                continue
            inferred = _infer_heading_level(title)
            if inferred is None:
                continue
            return {
                "title": title,
                "normalized_heading": normalize_section_heading(title),
                "level": inferred["level"],
                "ordinal": inferred["ordinal"],
                "page_no": page_no,
            }
        list_match = TOC_LIST_ENTRY_PATTERN.match(cell)
        if list_match:
            title = _sanitize_heading_title(list_match.group("title"))
            page_no = int(list_match.group("page"))
            if _looks_like_noise_heading(title):
                continue
            inferred = _infer_heading_level(title)
            if inferred is None and not _is_freeform_heading_candidate(title):
                continue
            return {
                "title": title,
                "normalized_heading": normalize_section_heading(title),
                "level": inferred["level"] if inferred else 1,
                "ordinal": inferred["ordinal"] if inferred else None,
                "page_no": page_no,
            }
    return None


def _extract_body_heading_candidates(
    lines: list[str],
    *,
    toc_line_indexes: set[int],
    document_title: str | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, raw_line in enumerate(lines):
        if index in toc_line_indexes:
            continue
        stripped = raw_line.strip()
        if not stripped or TABLE_ROW_PATTERN.match(stripped):
            continue
        markdown_match = MARKDOWN_HEADING_PATTERN.match(stripped)
        if markdown_match:
            title = _sanitize_heading_title(markdown_match.group(2))
            if document_title and title == document_title and index <= 3:
                continue
            inferred = _infer_heading_level(title)
            if inferred is None and not _is_freeform_heading_candidate(title):
                continue
            if _looks_like_lead_in_label_heading(title):
                continue
            if _looks_like_figure_caption_heading(title):
                continue
            if _looks_like_short_person_name_heading(title):
                continue
            if _looks_like_code_like_heading(title):
                continue
            if _looks_like_parameter_clause_heading(title, inferred=inferred):
                continue
            if inferred is not None and (
                _looks_like_sentence_like_heading(title)
                or _looks_like_embedded_figure_label_heading(title, inferred=inferred)
                or _looks_like_status_or_requirement_clause(title)
            ):
                continue
            if inferred is None and _looks_like_status_or_requirement_clause(title):
                continue
            if _looks_like_noise_heading(title):
                continue
            level = inferred["level"] if inferred else max(1, len(markdown_match.group(1)))
            candidates.append(
                {
                    "title": title,
                    "normalized_heading": normalize_section_heading(title),
                    "heading_aliases": list(build_heading_aliases(title)),
                    "level": level,
                    "ordinal": inferred["ordinal"] if inferred else None,
                    "line_index": index,
                    "order_index": index,
                    "signals": ["markdown_heading"],
                    "signal": "markdown_heading",
                    "audit_flags": [],
                }
            )
            continue
        list_heading_match = LIST_HEADING_PATTERN.match(stripped)
        body_title = _sanitize_heading_title(list_heading_match.group("title")) if list_heading_match else _sanitize_heading_title(stripped)
        inferred = _infer_heading_level(body_title)
        if not inferred or _looks_like_noise_heading(body_title):
            continue
        if _looks_like_figure_caption_heading(body_title):
            continue
        if _looks_like_short_person_name_heading(body_title):
            continue
        if _looks_like_code_like_heading(body_title):
            continue
        if _looks_like_parameter_clause_heading(body_title, inferred=inferred):
            continue
        if (
            _looks_like_sentence_like_heading(body_title)
            or _looks_like_embedded_figure_label_heading(body_title, inferred=inferred)
            or _looks_like_status_or_requirement_clause(body_title)
        ):
            continue
        if not _is_body_heading_like(body_title):
            continue
        candidates.append(
            {
                "title": body_title,
                "normalized_heading": normalize_section_heading(body_title),
                "heading_aliases": list(build_heading_aliases(body_title)),
                "level": inferred["level"],
                "ordinal": inferred["ordinal"],
                "line_index": index,
                "order_index": index,
                "signals": ["body_heading"],
                "signal": "body_heading",
                "audit_flags": [],
            }
        )
    return _dedupe_body_candidates(candidates)


def _merge_body_and_parser_candidates(
    body_candidates: list[dict[str, Any]],
    parser_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not parser_candidates:
        return body_candidates
    merged = list(body_candidates)
    used_parser_indexes: set[int] = set()
    for body in merged:
        for parser_index, parser_candidate in enumerate(parser_candidates):
            if parser_index in used_parser_indexes:
                continue
            if not _candidate_titles_match(body, parser_candidate):
                continue
            used_parser_indexes.add(parser_index)
            for signal in parser_candidate.get("signals") or []:
                if signal not in body["signals"]:
                    body["signals"].append(signal)
            if not body.get("ordinal") and parser_candidate.get("ordinal"):
                body["ordinal"] = parser_candidate.get("ordinal")
            if body.get("page_no") is None and parser_candidate.get("page_no") is not None:
                body["page_no"] = parser_candidate.get("page_no")
            break
    for parser_index, parser_candidate in enumerate(parser_candidates):
        if parser_index not in used_parser_indexes:
            merged.append(parser_candidate)
    merged.sort(
        key=lambda item: (
            int(item.get("line_index") if item.get("line_index") is not None else 10_000),
            int(item.get("order_index") if item.get("order_index") is not None else 10_000),
        )
    )
    return _dedupe_merged_candidates(merged)


def _dedupe_body_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int | None]] = set()
    for candidate in candidates:
        key = (
            str(candidate.get("normalized_heading") or ""),
            int(candidate.get("level") or 0),
            candidate.get("line_index"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _merge_section_candidates(
    *,
    toc_candidates: list[dict[str, Any]],
    body_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not toc_candidates:
        return [
            {
                **candidate,
                "source_signals": list(candidate.get("signals") or []),
                "audit_flags": list(candidate.get("audit_flags") or ["body_only"]),
            }
            for candidate in body_candidates
        ]

    merged: list[dict[str, Any]] = []
    used_body_indexes: set[int] = set()

    for toc_candidate in toc_candidates:
        matched_body = None
        for body_index, body_candidate in enumerate(body_candidates):
            if body_index in used_body_indexes:
                continue
            if _candidate_titles_match(toc_candidate, body_candidate):
                matched_body = body_candidate
                used_body_indexes.add(body_index)
                break
        source_signals = list(toc_candidate.get("signals") or [])
        audit_flags = list(toc_candidate.get("audit_flags") or [])
        line_index = None
        if matched_body:
            source_signals.extend(item for item in (matched_body.get("signals") or []) if item not in source_signals)
            line_index = matched_body.get("line_index")
        else:
            audit_flags.append("toc_only")
        merged.append(
            {
                **toc_candidate,
                "line_index": line_index,
                "source_signals": source_signals,
                "audit_flags": audit_flags,
            }
        )

    for body_index, body_candidate in enumerate(body_candidates):
        if body_index in used_body_indexes:
            continue
        merged.append(
            {
                **body_candidate,
                "page_no": None,
                "source_signals": list(body_candidate.get("signals") or []),
                "audit_flags": list(body_candidate.get("audit_flags") or []) + ["body_only"],
            }
        )

    merged.sort(
        key=lambda item: (
            int(item.get("order_index") if item.get("order_index") is not None else 10_000),
            int(item.get("line_index") if item.get("line_index") is not None else 10_000),
        )
    )
    return merged


def _candidate_titles_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_normalized = str(left.get("normalized_heading") or "")
    right_normalized = str(right.get("normalized_heading") or "")
    if left_normalized and left_normalized == right_normalized:
        return True
    if left_normalized and right_normalized and left_normalized.casefold() == right_normalized.casefold():
        return True
    left_aliases = set(str(item) for item in (left.get("heading_aliases") or []) if item)
    right_aliases = set(str(item) for item in (right.get("heading_aliases") or []) if item)
    if left_aliases & right_aliases:
        return True
    left_aliases_casefold = {item.casefold() for item in left_aliases}
    right_aliases_casefold = {item.casefold() for item in right_aliases}
    return bool(left_aliases_casefold & right_aliases_casefold)


def _build_section_tree(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    counters: dict[str, int] = defaultdict(int)

    for candidate in candidates:
        level = max(1, int(candidate.get("level") or 1))
        while stack and int(stack[-1].get("level") or 1) >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        parent_id = str(parent.get("section_id")) if parent else None
        counter_key = parent_id or "root"
        counters[counter_key] += 1
        section_id = (
            f"{parent_id}.{counters[counter_key]}"
            if parent_id
            else str(counters[counter_key])
        )
        source_heading = _sanitize_heading_title(candidate.get("title"))
        section_path = f"{parent['section_path']} > {source_heading}" if parent else source_heading
        normalized_path_items = list(parent.get("normalized_section_path_items") or []) if parent else []
        normalized_heading = str(candidate.get("normalized_heading") or "")
        if normalized_heading:
            normalized_path_items.append(normalized_heading)
        node = {
            "section_id": section_id,
            "title": source_heading,
            "source_heading": source_heading,
            "normalized_heading": normalized_heading,
            "heading_aliases": list(candidate.get("heading_aliases") or []),
            "level": level,
            "ordinal": candidate.get("ordinal"),
            "parent_id": parent_id,
            "section_path": section_path,
            "heading_path": section_path,
            "normalized_section_path": " > ".join(item for item in normalized_path_items if item),
            "normalized_section_path_items": normalized_path_items,
            "page_no": candidate.get("page_no"),
            "line_index": candidate.get("line_index"),
            "source_signals": list(candidate.get("source_signals") or []),
            "audit_flags": list(dict.fromkeys(candidate.get("audit_flags") or [])),
            "children": [],
        }
        if parent:
            parent.setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append(node)

    return roots


def _infer_heading_level(text: str) -> dict[str, Any] | None:
    stripped = _sanitize_heading_title(text)
    if not stripped:
        return None
    for pattern, level in (
        (CHAPTER_HEADING_PATTERN, 1),
        (SECTION_HEADING_PATTERN, 2),
        (ARTICLE_HEADING_PATTERN, 3),
        (CHINESE_NUMERIC_HEADING_PATTERN, 2),
        (PAREN_HEADING_PATTERN, 3),
        (ARABIC_DOTTED_HEADING_PATTERN, None),
        (ARABIC_SIMPLE_HEADING_PATTERN, 2),
        (ARABIC_COMPACT_CJK_HEADING_PATTERN, 2),
    ):
        match = pattern.match(stripped)
        if not match:
            continue
        ordinal = str(match.group("ordinal") or "").strip()
        if pattern is ARABIC_DOTTED_HEADING_PATTERN:
            parts = [item for item in ordinal.split(".") if item]
            level_value = min(4, len(parts) + 1)
        else:
            level_value = level
        return {
            "ordinal": ordinal,
            "level": level_value,
        }
    return None


def _is_body_heading_like(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped or len(stripped) > 90:
        return False
    if sum(stripped.count(char) for char in ("。", "！", "？")) > 0:
        return False
    if FILE_NAME_PATTERN.search(stripped):
        return False
    return True


def _split_parser_heading_text(text: str, *, page_no: Any) -> tuple[str, int | None]:
    stripped = _sanitize_heading_title(text)
    inferred_page = None
    match = PARSER_TRAILING_PAGE_PATTERN.match(stripped)
    if match:
        candidate_title = _sanitize_heading_title(match.group("title"))
        candidate_page = int(match.group("page"))
        if _infer_heading_level(candidate_title) is not None or len(candidate_title) <= 40:
            stripped = candidate_title
            inferred_page = candidate_page
    explicit_page = None
    try:
        explicit_page = int(page_no) if page_no is not None else None
    except (TypeError, ValueError):
        explicit_page = None
    return stripped, explicit_page or inferred_page


def _map_parser_level_to_heading_level(parser_level: Any) -> int | None:
    try:
        value = int(parser_level)
    except (TypeError, ValueError):
        return None
    if value <= 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    return None


def _looks_like_front_matter_heading(text: str) -> bool:
    normalized = normalize_section_heading(text)
    if not normalized:
        return True
    if normalized in FRONT_MATTER_HEADING_TERMS:
        return True
    if DATE_LIKE_TEXT_PATTERN.match(normalized):
        return True
    if DATE_MONTH_TEXT_PATTERN.match(normalized):
        return True
    if "年" in normalized and "月" in normalized and re.search(r"\d", normalized):
        return True
    if PAGE_FOOTER_PATTERN.match(normalized):
        return True
    if any(normalized.startswith(term) for term in FRONT_MATTER_HEADING_PREFIX_TERMS):
        return True
    return False


def _is_unnumbered_parser_heading_candidate(text: str, *, parser_level: Any) -> bool:
    normalized = normalize_section_heading(_sanitize_heading_title(text))
    if not normalized:
        return False
    try:
        level_value = int(parser_level) if parser_level is not None else None
    except (TypeError, ValueError):
        level_value = None
    if level_value is None or level_value > 3:
        return False
    if len(normalized) > 24:
        return False
    if any(token in text for token in ("，", ",", "；", ";", "。", "！", "？")):
        return False
    if normalized.count(" ") > 2:
        return False
    if re.search(r"\d{2,}", normalized):
        return False
    compact = normalized.replace(" ", "")
    if _looks_like_status_or_requirement_clause(text):
        return False
    if not any(keyword in compact for keyword in UNNUMBERED_HEADING_KEYWORDS):
        return False
    return True


def _is_freeform_heading_candidate(text: str) -> bool:
    normalized = normalize_section_heading(_sanitize_heading_title(text))
    if not normalized:
        return False
    if _looks_like_front_matter_heading(normalized):
        return False
    if len(normalized) > 28:
        return False
    if any(token in text for token in ("，", ",", "；", ";", "。", "！", "？")):
        return False
    compact = normalized.replace(" ", "")
    if re.search(r"\d{2,}", compact):
        return False
    if not any(keyword in compact for keyword in UNNUMBERED_HEADING_KEYWORDS):
        return False
    return True


def _dedupe_merged_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        if not deduped:
            deduped.append(candidate)
            continue
        existing = deduped[-1]
        same_key = (
            str(existing.get("normalized_heading") or "") == str(candidate.get("normalized_heading") or "")
            and int(existing.get("level") or 0) == int(candidate.get("level") or 0)
        )
        existing_order = existing.get("order_index")
        candidate_order = candidate.get("order_index")
        nearby = False
        if existing_order is not None and candidate_order is not None:
            try:
                nearby = abs(int(existing_order) - int(candidate_order)) <= 3
            except (TypeError, ValueError):
                nearby = False
        if not (same_key and nearby):
            deduped.append(candidate)
            continue
        existing_signals = list(existing.get("signals") or [])
        for signal in candidate.get("signals") or []:
            if signal not in existing_signals:
                existing_signals.append(signal)
        existing["signals"] = existing_signals
        existing["source_signals"] = existing_signals
        if existing.get("page_no") is None and candidate.get("page_no") is not None:
            existing["page_no"] = candidate.get("page_no")
        if existing.get("ordinal") is None and candidate.get("ordinal") is not None:
            existing["ordinal"] = candidate.get("ordinal")
    return deduped


def _merge_duplicate_root_sections(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for root in roots:
        normalized_heading = str(root.get("normalized_heading") or "")
        if not normalized_heading:
            merged.append(root)
            continue
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if str(existing.get("normalized_heading") or "") == normalized_heading
            ),
            None,
        )
        if duplicate_index is None:
            merged.append(root)
            continue
        existing = merged[duplicate_index]
        keep_existing = _root_quality_score(existing) >= _root_quality_score(root)
        winner = existing if keep_existing else root
        loser = root if keep_existing else existing
        _merge_section_nodes(target=winner, source=loser)
        merged[duplicate_index] = winner
    return merged


def _prune_root_sections(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [root for root in roots if not _should_prune_root_section(root)]


def _should_prune_root_section(root: dict[str, Any]) -> bool:
    title = _sanitize_heading_title(root.get("title"))
    normalized_heading = str(root.get("normalized_heading") or "")
    signals = {str(item) for item in (root.get("source_signals") or []) if item}
    if not normalized_heading:
        return True
    if _looks_like_front_matter_heading(title):
        return True
    if _looks_like_lead_in_label_heading(title):
        return True
    if _looks_like_figure_caption_heading(title):
        return True
    if _looks_like_short_person_name_heading(title):
        return True
    if _looks_like_code_like_heading(title):
        return True
    if _looks_like_parameter_clause_heading(title, inferred=_infer_heading_level(title)):
        return True
    if not signals.intersection({"toc", "body_heading", "markdown_heading", "parser_heading"}):
        return True
    if _infer_heading_level(title) is None and not _is_freeform_heading_candidate(title):
        return True
    if not {"toc", "body_heading", "markdown_heading"} & signals:
        if re.search(r"[A-Za-z]{8,}", normalized_heading) and not any(
            keyword in normalized_heading.replace(" ", "")
            for keyword in UNNUMBERED_HEADING_KEYWORDS
        ):
            return True
        if len(normalized_heading) <= 6 and not any(
            keyword in normalized_heading.replace(" ", "")
            for keyword in UNNUMBERED_HEADING_KEYWORDS
        ):
            return True
    return False


def _normalize_inner_sections(
    roots: list[dict[str, Any]],
    *,
    document_title: str | None,
) -> list[dict[str, Any]]:
    normalized_roots: list[dict[str, Any]] = []
    for root in roots:
        root_node = dict(root)
        root_node["children"] = _normalize_child_sections(
            root.get("children") or [],
            parent=root_node,
            document_title=document_title,
            external_targets=[],
        )
        normalized_roots.append(root_node)
    return normalized_roots


def _normalize_child_sections(
    children: list[dict[str, Any]],
    *,
    parent: dict[str, Any],
    document_title: str | None,
    external_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized_children: list[dict[str, Any]] = []
    for child in children:
        child_node = dict(child)
        visible_targets = [*external_targets, *normalized_children]
        child_node["children"] = _normalize_child_sections(
            child.get("children") or [],
            parent=child_node,
            document_title=document_title,
            external_targets=visible_targets,
        )
        if _should_collapse_inner_section(
            child_node,
            parent=parent,
            document_title=document_title,
        ):
            _reattach_collapsed_children(
                collapsed=child_node,
                siblings=normalized_children,
                external_targets=visible_targets,
            )
            continue
        better_target = _find_better_ordinal_target(
            siblings=normalized_children,
            external_targets=visible_targets,
            child=child_node,
            current_parent=parent,
        )
        if better_target is not None:
            target_children = list(better_target.get("children") or [])
            target_children.append(child_node)
            better_target["children"] = _dedupe_sibling_sections(target_children)
            continue
        normalized_children.append(child_node)
    return _dedupe_sibling_sections(normalized_children)


def _should_collapse_inner_section(
    node: dict[str, Any],
    *,
    parent: dict[str, Any],
    document_title: str | None,
) -> bool:
    title = _sanitize_heading_title(node.get("title"))
    normalized_heading = str(node.get("normalized_heading") or "")
    compact_heading = normalized_heading.replace(" ", "")
    signals = {str(item) for item in (node.get("source_signals") or []) if item}
    children = list(node.get("children") or [])
    inferred = _infer_heading_level(title)
    if not normalized_heading:
        return True
    if _looks_like_front_matter_heading(title):
        return True
    if _looks_like_noise_heading(title):
        return True
    if _looks_like_lead_in_label_heading(title):
        return True
    if _looks_like_figure_caption_heading(title):
        return True
    if _looks_like_short_person_name_heading(title):
        return True
    if _looks_like_code_like_heading(title):
        return True
    if inferred is not None and _looks_like_sentence_like_heading(title):
        return True
    if _looks_like_parameter_clause_heading(title, inferred=inferred):
        return True
    if document_title and compact_heading == normalize_section_heading(document_title).replace(" ", ""):
        return True
    if _looks_like_document_title_repeat(title, document_title=document_title):
        return True
    if heading_looks_like_document_title(title):
        return True
    if "toc" in signals:
        return False
    if compact_heading in GENERIC_BODY_ONLY_HEADINGS and not children:
        return True
    if COLON_LABEL_PATTERN.search(title) and inferred is None:
        return True
    if INLINE_OPERATION_STATE_PATTERN.search(title):
        return True
    if "如下" in compact_heading or all(token in compact_heading for token in ("尺寸", "型号")):
        return True
    alnum_compact = re.sub(r"[\s:：._-]+", "", title)
    if re.fullmatch(r"(?:[A-Za-z]{2,}\d+[A-Za-z0-9]*|\d+[A-Za-z]{2,}[A-Za-z0-9]*)", alnum_compact):
        return True
    if inferred is None and not children and any(token in compact_heading for token in LOW_VALUE_HEADING_TOKENS):
        return True
    if inferred is None and len(normalized_heading) > 30:
        return True
    if compact_heading == normalize_section_heading(str(parent.get("title") or "")).replace(" ", ""):
        return True
    return False


def _reattach_collapsed_children(
    *,
    collapsed: dict[str, Any],
    siblings: list[dict[str, Any]],
    external_targets: list[dict[str, Any]],
) -> None:
    for child in collapsed.get("children") or []:
        target = _find_reparent_target(
            siblings=siblings,
            external_targets=external_targets,
            child=child,
        )
        if target is None:
            siblings.append(child)
            continue
        target_children = list(target.get("children") or [])
        target_children.append(child)
        target["children"] = _dedupe_sibling_sections(target_children)


def _find_reparent_target(
    *,
    siblings: list[dict[str, Any]],
    external_targets: list[dict[str, Any]],
    child: dict[str, Any],
) -> dict[str, Any] | None:
    child_tokens = _ordinal_tokens_for_node(child)
    if not child_tokens:
        return None
    desired_parent_length = max(1, len(child_tokens) - 1)
    best_target: dict[str, Any] | None = None
    best_key: tuple[int, int, int, int] | None = None
    for target in _iter_section_descendants_reversed([*external_targets, *siblings]):
        target_tokens = _ordinal_tokens_for_node(target)
        if not target_tokens:
            continue
        if len(target_tokens) >= len(child_tokens):
            continue
        prefix_length = _ordinal_prefix_match_length(target_tokens, child_tokens)
        if prefix_length <= 0 or prefix_length >= len(child_tokens):
            continue
        candidate_key = (
            prefix_length,
            int(len(target_tokens) == desired_parent_length),
            -abs(len(target_tokens) - desired_parent_length),
            _section_parent_quality_score(target),
        )
        if best_key is None or candidate_key > best_key:
            best_key = candidate_key
            best_target = target
    return best_target


def _find_better_ordinal_target(
    *,
    siblings: list[dict[str, Any]],
    external_targets: list[dict[str, Any]],
    child: dict[str, Any],
    current_parent: dict[str, Any],
) -> dict[str, Any] | None:
    child_tokens = _ordinal_tokens_for_node(child)
    if not child_tokens:
        return None
    current_parent_tokens = _ordinal_tokens_for_node(current_parent)
    current_prefix = _ordinal_prefix_match_length(current_parent_tokens, child_tokens) if current_parent_tokens else 0
    target = _find_reparent_target(
        siblings=siblings,
        external_targets=external_targets,
        child=child,
    )
    if target is None:
        return None
    target_tokens = _ordinal_tokens_for_node(target)
    target_prefix = _ordinal_prefix_match_length(target_tokens, child_tokens)
    current_parent_is_valid = _is_valid_ordinal_parent(current_parent_tokens, child_tokens)
    if target_prefix < current_prefix:
        return None
    if target_prefix == current_prefix:
        if not current_parent_is_valid:
            return target
        target_quality = _section_parent_quality_score(target)
        current_quality = _section_parent_quality_score(current_parent)
        if target_quality > current_quality + 1:
            return target
        if len(target_tokens) < len(current_parent_tokens):
            return target
        return None
    if target_prefix <= current_prefix:
        return None
    return target


def _iter_section_descendants_reversed(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for node in reversed(nodes):
        children = list(node.get("children") or [])
        if children:
            collected.extend(_iter_section_descendants_reversed(children))
        collected.append(node)
    return collected


def _ordinal_tokens_for_node(node: dict[str, Any]) -> tuple[int, ...]:
    ordinal = str(node.get("ordinal") or "").strip()
    if ordinal:
        tokens = _parse_ordinal_tokens(ordinal)
        if tokens:
            return tokens
    return _parse_ordinal_tokens(str(node.get("title") or ""))


def _looks_like_document_title_repeat(text: str, *, document_title: str | None) -> bool:
    if not document_title:
        return False
    heading_compact = _document_title_compare_key(text)
    document_compact = _document_title_compare_key(document_title)
    if len(heading_compact) < 8 or len(document_compact) < 8:
        return False
    return heading_compact in document_compact or document_compact in heading_compact


def _looks_like_sentence_like_heading(text: str) -> bool:
    normalized = normalize_section_heading(_sanitize_heading_title(text))
    if len(normalized) < SENTENCE_LIKE_HEADING_MIN_LENGTH:
        return False
    punctuation_hits = sum(text.count(token) for token in SENTENCE_LIKE_HEADING_PUNCTUATION)
    has_clause_delimiter = any(token in text for token in SENTENCE_LIKE_HEADING_CLAUSE_DELIMITERS)
    return has_clause_delimiter or punctuation_hits >= 2


def _looks_like_parameter_clause_heading(text: str, *, inferred: dict[str, Any] | None) -> bool:
    if not inferred:
        return False
    tokens = _parse_ordinal_tokens(str(inferred.get("ordinal") or text))
    if len(tokens) != 1:
        return False
    stripped = _sanitize_heading_title(text)
    normalized = normalize_section_heading(stripped)
    if not normalized or len(normalized) > 40:
        return False
    has_value_marker = bool(PARAMETER_CLAUSE_VALUE_PATTERN.search(stripped))
    has_parameter_keyword = any(keyword in normalized for keyword in PARAMETER_CLAUSE_KEYWORDS)
    if not (has_value_marker or has_parameter_keyword):
        return False
    return bool(PARAMETER_CLAUSE_TERMINAL_PATTERN.search(stripped) or "：" in stripped)


def _looks_like_lead_in_label_heading(text: str) -> bool:
    compact = normalize_section_heading(_sanitize_heading_title(text)).replace(" ", "")
    if not compact:
        return False
    return any(pattern.search(compact) for pattern in LEAD_IN_LABEL_PATTERNS)


def _looks_like_status_or_requirement_clause(text: str) -> bool:
    compact = normalize_section_heading(_sanitize_heading_title(text)).replace(" ", "")
    if len(compact) < 8:
        return False
    return any(pattern.search(compact) for pattern in STATUS_OR_REQUIREMENT_CLAUSE_PATTERNS)


def _looks_like_embedded_figure_label_heading(text: str, *, inferred: dict[str, Any] | None) -> bool:
    if not inferred:
        return False
    tokens = _parse_ordinal_tokens(str(inferred.get("ordinal") or text))
    if len(tokens) != 1:
        return False
    compact = normalize_section_heading(_sanitize_heading_title(text)).replace(" ", "").casefold()
    return any(pattern.search(compact) for pattern in EMBEDDED_FIGURE_LABEL_PATTERNS)


def _looks_like_figure_caption_heading(text: str) -> bool:
    compact = normalize_section_heading(_sanitize_heading_title(text)).replace(" ", "")
    if not compact:
        return False
    return bool(FIGURE_CAPTION_HEADING_PATTERN.match(compact))


def _looks_like_short_person_name_heading(text: str) -> bool:
    compact = normalize_section_heading(_sanitize_heading_title(text)).replace(" ", "")
    if not compact:
        return False
    if any(keyword in compact for keyword in UNNUMBERED_HEADING_KEYWORDS):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{3}", compact))


def _looks_like_code_like_heading(text: str) -> bool:
    compact = normalize_section_heading(_sanitize_heading_title(text))
    if not compact or re.search(r"[\u4e00-\u9fff]", compact):
        return False
    if not re.search(r"[A-Za-z]", compact) or not re.search(r"\d", compact):
        return False
    return len(compact.replace(" ", "")) <= 18


def _sanitize_heading_title(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = HEADING_MULTI_WHITESPACE_PATTERN.sub(" ", value)
    value = LEADING_CHINESE_ENUM_SPACE_PATTERN.sub(r"\1", value)
    value = LEADING_PAREN_ENUM_SPACE_PATTERN.sub(r"\1", value)
    value = HEADING_MULTI_WHITESPACE_PATTERN.sub(" ", value)
    return value.strip()


def _dedupe_sibling_sections(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for node in nodes:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(deduped)
                if (
                    str(existing.get("normalized_heading") or "") == str(node.get("normalized_heading") or "")
                    and int(existing.get("level") or 0) == int(node.get("level") or 0)
                )
            ),
            None,
        )
        if duplicate_index is None:
            deduped.append(node)
            continue
        existing = deduped[duplicate_index]
        keep_existing = _root_quality_score(existing) >= _root_quality_score(node)
        winner = existing if keep_existing else node
        loser = node if keep_existing else existing
        _merge_section_nodes(target=winner, source=loser)
        deduped[duplicate_index] = winner
    return deduped


def _reindex_section_tree(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _reindex_section_nodes(roots)


def _reindex_section_nodes(
    nodes: list[dict[str, Any]],
    *,
    parent_id: str | None = None,
    parent_path: str | None = None,
    parent_normalized_items: list[str] | None = None,
) -> list[dict[str, Any]]:
    reindexed: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        source_heading = str(node.get("source_heading") or node.get("title") or "").strip()
        normalized_heading = str(node.get("normalized_heading") or normalize_section_heading(source_heading))
        normalized_items = list(parent_normalized_items or [])
        if normalized_heading:
            normalized_items.append(normalized_heading)
        section_id = f"{parent_id}.{index}" if parent_id else str(index)
        section_path = f"{parent_path} > {source_heading}" if parent_path else source_heading
        reindexed_node = {
            **node,
            "section_id": section_id,
            "parent_id": parent_id,
            "title": source_heading,
            "source_heading": source_heading,
            "normalized_heading": normalized_heading,
            "heading_aliases": list(node.get("heading_aliases") or build_heading_aliases(source_heading)),
            "section_path": section_path,
            "heading_path": section_path,
            "normalized_section_path_items": normalized_items,
            "normalized_section_path": " > ".join(item for item in normalized_items if item),
        }
        reindexed_node["children"] = _reindex_section_nodes(
            list(node.get("children") or []),
            parent_id=section_id,
            parent_path=section_path,
            parent_normalized_items=normalized_items,
        )
        reindexed.append(reindexed_node)
    return reindexed


def _root_quality_score(root: dict[str, Any]) -> int:
    signals = {str(item) for item in (root.get("source_signals") or []) if item}
    score = len(root.get("children") or []) * 10
    score += len(signals) * 2
    if "toc" in signals:
        score += 6
    if "markdown_heading" in signals or "body_heading" in signals:
        score += 4
    if "parser_heading" in signals:
        score += 2
    if _infer_heading_level(str(root.get("title") or "")) is not None:
        score += 2
    return score


def _merge_section_nodes(*, target: dict[str, Any], source: dict[str, Any]) -> None:
    target_signals = list(target.get("source_signals") or [])
    for signal in source.get("source_signals") or []:
        if signal not in target_signals:
            target_signals.append(signal)
    target["source_signals"] = target_signals

    target_flags = list(target.get("audit_flags") or [])
    for flag in source.get("audit_flags") or []:
        if flag not in target_flags:
            target_flags.append(flag)
    target["audit_flags"] = target_flags

    if target.get("page_no") is None and source.get("page_no") is not None:
        target["page_no"] = source.get("page_no")

    target_children = list(target.get("children") or [])
    existing_child_keys = {
        (
            str(child.get("normalized_heading") or ""),
            int(child.get("level") or 0),
        )
        for child in target_children
    }
    for child in source.get("children") or []:
        key = (
            str(child.get("normalized_heading") or ""),
            int(child.get("level") or 0),
        )
        if key in existing_child_keys:
            continue
        target_children.append(child)
        existing_child_keys.add(key)
    target["children"] = target_children


def _looks_like_noise_heading(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    if heading_looks_like_document_title(stripped):
        return True
    if FILE_NAME_PATTERN.search(stripped):
        return True
    normalized = normalize_section_heading(stripped)
    if not normalized:
        return True
    if len(normalized) < 2:
        return True
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    alpha_words = re.findall(r"[A-Za-z]{3,}", normalized)
    digit_count = len(re.findall(r"\d", normalized))
    if chinese_count == 0:
        if not alpha_words:
            return True
        if digit_count > sum(len(word) for word in alpha_words):
            return True
    if re.fullmatch(r"[A-Za-z0-9 _./+-]+", normalized) and len(normalized) < 4:
        return True
    return False
