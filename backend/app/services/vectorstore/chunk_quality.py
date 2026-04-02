from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.services.parsing.table_profile import build_table_profile


CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
NUMERIC_TABLE_CELL_PATTERN = re.compile(r"^[\d.\-+/%()]+$")
NUMERIC_HEADING_PATTERN = re.compile(r"^[\d.\-+/%()\s]+$")
LOW_SIGNAL_ENGLISH_WORDS = {"of", "and", "or", "to", "for", "the", "with", "by", "from"}
TEXT_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}|\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ChunkQualityAssessment:
    reasons: tuple[str, ...]
    preserve_for_assets: bool = False


def flatten_heading_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " > ".join(str(segment).strip() for segment in value if str(segment).strip()).strip()
    return ""


def strip_markdown_heading(line: str) -> str:
    return re.sub(r"^\s*#+\s*", "", line).strip()


def looks_like_low_signal_plain_chunk(*, raw_content: str, heading_text: str) -> bool:
    lines = [line.strip() for line in str(raw_content or "").splitlines() if line.strip()]
    if not lines:
        return True

    body_lines = [strip_markdown_heading(line) for line in lines if not line.lstrip().startswith("#")]
    if not body_lines:
        body_lines = [strip_markdown_heading(line) for line in lines]

    body_text = " ".join(part for part in body_lines if part).strip()
    normalized_heading = strip_markdown_heading(heading_text)
    combined_text = " ".join(part for part in (normalized_heading, body_text) if part).strip()
    if not combined_text:
        return True

    visible_chars = [char for char in combined_text if not char.isspace()]
    if not visible_chars:
        return True

    cyrillic_ratio = sum(1 for char in visible_chars if CYRILLIC_PATTERN.match(char)) / len(visible_chars)
    if cyrillic_ratio >= 0.12 and len(visible_chars) <= 40:
        return True

    if body_text.casefold() in LOW_SIGNAL_ENGLISH_WORDS and len(normalized_heading) <= 20:
        return True

    token_count = len(TEXT_TOKEN_PATTERN.findall(combined_text))
    if len(visible_chars) <= 24 and token_count <= 2 and not re.search(r"[\u4e00-\u9fff]{2,}", combined_text):
        return True

    return False


def looks_like_numeric_table_fragment(*, raw_content: str, heading_text: str) -> bool:
    profile = build_table_profile(str(raw_content or ""))
    header_fields = [str(field).strip() for field in profile.header_fields if str(field).strip()]
    header_all_numeric = bool(header_fields) and all(NUMERIC_TABLE_CELL_PATTERN.fullmatch(field) for field in header_fields)
    numeric_heading = bool(NUMERIC_HEADING_PATTERN.fullmatch(str(heading_text or "").strip()))
    return (
        profile.profile_name == "sparse_lookup"
        and profile.row_count <= 4
        and profile.numeric_ratio >= 0.8
        and (header_all_numeric or numeric_heading)
    )


def assess_chunk_quality(
    *,
    chunk_type: str,
    raw_content: str,
    heading_path: Any,
) -> ChunkQualityAssessment:
    normalized_chunk_type = str(chunk_type or "PLAIN").upper()
    heading_text = flatten_heading_text(heading_path)
    content = str(raw_content or "").strip()
    if not content:
        return ChunkQualityAssessment(reasons=("empty_chunk",), preserve_for_assets=False)

    if normalized_chunk_type == "PLAIN":
        if looks_like_low_signal_plain_chunk(raw_content=content, heading_text=heading_text):
            return ChunkQualityAssessment(reasons=("low_signal_plain_fragment",), preserve_for_assets=False)
        return ChunkQualityAssessment(reasons=(), preserve_for_assets=False)

    if normalized_chunk_type == "TABLE":
        profile = build_table_profile(content)
        if profile.profile_name == "garbled_table":
            return ChunkQualityAssessment(reasons=("garbled_table_fragment",), preserve_for_assets=True)
        if looks_like_numeric_table_fragment(raw_content=content, heading_text=heading_text):
            return ChunkQualityAssessment(reasons=("numeric_table_fragment",), preserve_for_assets=False)
        return ChunkQualityAssessment(reasons=(), preserve_for_assets=False)

    return ChunkQualityAssessment(reasons=(), preserve_for_assets=False)


def is_noise_chunk(*, chunk_type: str, raw_content: str, heading_path: Any) -> bool:
    return bool(
        assess_chunk_quality(
            chunk_type=chunk_type,
            raw_content=raw_content,
            heading_path=heading_path,
        ).reasons
    )
