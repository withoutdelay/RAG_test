from __future__ import annotations

import re
from typing import Any

from app.services.domain.synonyms import extract_domain_terms
from app.services.parsing.section_heading import normalize_section_heading
from app.services.vectorstore.block_taxonomy import classify_block_taxonomy, extract_taxonomy_hints
from app.services.vectorstore.chunker import ChunkPayload


ARABIC_DOTTED_ORDINAL_PATTERN = re.compile(r"^(?P<ordinal>[0-9]+(?:\.[0-9]+){0,3})")
ARABIC_SIMPLE_ORDINAL_PATTERN = re.compile(r"^(?P<ordinal>[0-9]{1,2})(?:[、.．]|\s+|(?=[\u4e00-\u9fff]))")
RETRIEVAL_IMAGE_MARKER_PATTERN = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)
RETRIEVAL_INLINE_FIGURE_CAPTION_PATTERN = re.compile(
    r"(?:^|[\s。；，,:：])图\s*\d+\s*[A-Za-z0-9\u4e00-\u9fff]{0,24}?(?:示意图|结构图|安装图|总布置图|外形图)\s*(?=<!--\s*image\s*-->)",
    re.IGNORECASE,
)
CJK_SPLIT_SPACE_PATTERN = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
PATH_CHAPTER_PREFIX_PATTERN = re.compile(r"^(?P<ordinal>第[一二三四五六七八九十百零〇0-9]+[章节条])\s+(?P<title>.+?)$")


def classify_section_taxonomy(
    *,
    section: dict[str, Any],
    section_summary: str,
    subtree_texts: list[str],
) -> dict[str, str]:
    content = "\n".join(text for text in subtree_texts[:2] if text).strip() or section_summary
    chunk_type = "TABLE" if any("|" in str(text) and "\n|" in str(text) for text in subtree_texts[:2]) else "PLAIN"
    return classify_block_taxonomy(
        content=content,
        heading_path=str(section.get("section_path") or section.get("source_heading") or ""),
        chunk_type=chunk_type,
    )


def build_outline_semantic_retrieval_text(
    *,
    document_title: str,
    file_name: str,
    profile: str,
    top_level_titles: list[str],
    flattened: list[dict[str, Any]],
) -> str:
    heading_paths = [str(item.get("heading_path") or "").strip() for item in flattened[:24] if str(item.get("heading_path") or "").strip()]
    parts = _dedupe_text_parts(
        (
            document_title,
            file_name,
            profile,
            " ".join(top_level_titles),
            " ".join(heading_paths),
        )
    )
    return _truncate_joined_parts(parts, limit=2000)


def build_section_retrieval_text(
    *,
    section: dict[str, Any],
    document_title: str,
    file_name: str,
    section_summary: str,
) -> str:
    child_titles = [
        _normalize_retrieval_heading_label(
            str(child.get("normalized_heading") or child.get("title") or "")
        )
        for child in (section.get("children") or [])[:4]
        if _normalize_retrieval_heading_label(str(child.get("normalized_heading") or child.get("title") or ""))
    ]
    parts = _dedupe_text_parts(
        (
            document_title,
            file_name,
            _normalize_retrieval_heading_path(str(section.get("section_path") or "")),
            str(section.get("normalized_section_path") or ""),
            " ".join(str(item) for item in (section.get("heading_aliases") or []) if item),
            " ".join(str(item) for item in (section.get("heading_family") or []) if item),
            " ".join(child_titles),
            " ".join(str(item) for item in (section.get("taxonomy_hints") or [])[:8] if item),
            " ".join(str(item) for item in (section.get("domain_terms") or [])[:10] if item),
            _sanitize_retrieval_summary_text(section_summary),
        )
    )
    return _truncate_joined_parts(parts, limit=1200)


def build_block_contextual_text(
    *,
    document_title: str,
    file_name: str,
    section: dict[str, Any] | None,
    chunk: ChunkPayload,
    fallback_heading_path: str | None,
    taxonomy: dict[str, str],
) -> str:
    heading_path = str(chunk.heading_path or fallback_heading_path or "").strip()
    chunk_taxonomy_hints = extract_taxonomy_hints(heading_path, chunk.content[:1000])
    chunk_domain_terms = extract_domain_terms("\n".join(part for part in (heading_path, chunk.content[:1000]) if part))
    parts = _dedupe_text_parts(
        (
            section.get("section_retrieval_text") if section else None,
            heading_path if not section or heading_path != str(section.get("section_path") or "").strip() else None,
            " ".join(str(item) for item in ((section.get("taxonomy_hints") or []) if section else chunk_taxonomy_hints)[:8] if item),
            " ".join(str(item) for item in ((section.get("domain_terms") or []) if section else chunk_domain_terms)[:10] if item),
            taxonomy.get("section_type"),
            taxonomy.get("equipment_type"),
            taxonomy.get("content_form"),
            document_title if not section else None,
            file_name if not section else None,
        )
    )
    return _truncate_joined_parts(parts, limit=1600)


def build_block_semantic_retrieval_text(*, contextual_text: str, chunk_content: str) -> str:
    parts = _dedupe_text_parts((contextual_text, chunk_content))
    return _truncate_joined_parts(parts, limit=3200)


def parse_heading_ordinal_tokens(text: str) -> tuple[int, ...]:
    stripped = str(text or "").strip()
    if not stripped:
        return ()
    for pattern in (ARABIC_DOTTED_ORDINAL_PATTERN, ARABIC_SIMPLE_ORDINAL_PATTERN):
        match = pattern.match(stripped)
        if not match:
            continue
        ordinal = str(match.group("ordinal") or "").strip()
        try:
            return tuple(int(part) for part in ordinal.split(".") if part)
        except ValueError:
            return ()
    return ()


def ordinal_prefix_length(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    prefix_length = 0
    for left_part, right_part in zip(left, right):
        if left_part != right_part:
            break
        prefix_length += 1
    return prefix_length


def _dedupe_text_parts(parts: tuple[Any, ...] | list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = re.sub(r"\s+", " ", str(part or "")).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _truncate_joined_parts(parts: list[str], *, limit: int) -> str:
    joined = "\n".join(parts).strip()
    if len(joined) <= limit:
        return joined
    return joined[: limit - 1].rstrip() + "..."


def _sanitize_retrieval_summary_text(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = RETRIEVAL_INLINE_FIGURE_CAPTION_PATTERN.sub(" ", normalized)
    normalized = RETRIEVAL_IMAGE_MARKER_PATTERN.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalize_retrieval_heading_label(text: str) -> str:
    normalized = normalize_section_heading(text)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_retrieval_heading_path(text: str) -> str:
    segments = [segment.strip() for segment in str(text or "").split(">")]
    normalized_segments = [_normalize_retrieval_path_segment(segment) for segment in segments if segment]
    return " > ".join(segment for segment in normalized_segments if segment)


def _normalize_retrieval_path_segment(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    match = PATH_CHAPTER_PREFIX_PATTERN.match(normalized)
    if match:
        title = CJK_SPLIT_SPACE_PATTERN.sub("", str(match.group("title") or "").strip())
        return f"{match.group('ordinal')} {title}".strip()
    return CJK_SPLIT_SPACE_PATTERN.sub("", normalized)
