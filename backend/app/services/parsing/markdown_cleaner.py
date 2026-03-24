from __future__ import annotations

from pathlib import Path
import re
import unicodedata


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"<!--\s*image\s*-->")
BLANK_LINE_PATTERN = re.compile(r"\n{3,}")
PAGE_FURNITURE_PATTERN = re.compile(r"(版本\s*页码|总页数|页码\s*\d+|版权所有)", re.IGNORECASE)
DOT_LEADER_PATTERN = re.compile(r"[.．·•]{8,}")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\|?[\s:\-]{8,}\|[\s|\-:]*$")
DATE_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$")
LABEL_ONLY_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(项目名称|买方|卖方|联系人|电话|地址)\s*[:：]\s*$")
TOC_HEADING_PATTERN = re.compile(r"^#{1,6}\s+目录\s*$")
NUMBERED_SECTION_PATTERN = re.compile(r"^#{1,6}\s+\d+(?:\.\d+)*\s+")
FILE_NAME_HEADING_PATTERN = re.compile(r"^#{1,6}\s+.+\.(pdf|docx?|pptx?)\s*$", re.IGNORECASE)
BRAND_LINE_PATTERN = re.compile(r"^[A-Z][A-Z0-9 .,&()/_-]{4,}$")
COVER_LABEL_PATTERN = re.compile(r"(项目名称|买方|卖方|联系人|电话|地址|日期)")
COVER_LABEL_ONLY_LINE_PATTERN = re.compile(r"^(项目名称|买方|卖方|联系人|电话|地址|日期)\s*[:：]?\s*$")


def clean_markdown(markdown: str, *, file_path: str) -> tuple[str, dict[str, int | str]]:
    suffix = Path(file_path).suffix.lower()
    if suffix != ".pdf":
        return markdown, {"cleaner": "noop", "lines_removed": 0}

    normalized = unicodedata.normalize("NFKC", markdown)
    normalized = IMAGE_PLACEHOLDER_PATTERN.sub("", normalized)
    lines = normalized.splitlines()

    cleaned_lines: list[str] = []
    removed_lines = 0
    removed_toc_lines = 0
    removed_front_matter_lines = 0
    removed_brand_lines = 0
    removed_page_furniture_lines = 0
    inside_toc = False
    inside_cover = True

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()

        if NUMBERED_SECTION_PATTERN.match(stripped) and not DATE_HEADING_PATTERN.match(stripped):
            inside_cover = False

        if TOC_HEADING_PATTERN.match(stripped):
            inside_toc = True
            removed_lines += 1
            removed_toc_lines += 1
            continue

        if inside_toc:
            if NUMBERED_SECTION_PATTERN.match(stripped):
                inside_toc = False
            else:
                removed_lines += 1
                removed_toc_lines += 1
                continue

        if not stripped:
            cleaned_lines.append("")
            continue

        if line_number == 1 and FILE_NAME_HEADING_PATTERN.match(stripped):
            removed_lines += 1
            removed_front_matter_lines += 1
            continue

        if inside_cover and _is_brand_line(stripped):
            removed_lines += 1
            removed_front_matter_lines += 1
            removed_brand_lines += 1
            continue

        if inside_cover and _is_cover_metadata_line(stripped):
            removed_lines += 1
            removed_front_matter_lines += 1
            continue

        if inside_cover and COVER_LABEL_ONLY_LINE_PATTERN.match(stripped):
            removed_lines += 1
            removed_front_matter_lines += 1
            continue

        if PAGE_FURNITURE_PATTERN.search(stripped):
            removed_lines += 1
            removed_page_furniture_lines += 1
            continue

        if DOT_LEADER_PATTERN.search(stripped):
            removed_lines += 1
            continue

        if TABLE_SEPARATOR_PATTERN.match(stripped):
            removed_lines += 1
            continue

        if DATE_HEADING_PATTERN.match(stripped):
            if inside_cover:
                removed_lines += 1
                removed_front_matter_lines += 1
            else:
                cleaned_lines.append(stripped.lstrip("#").strip())
            continue

        if LABEL_ONLY_HEADING_PATTERN.match(stripped):
            if inside_cover:
                removed_lines += 1
                removed_front_matter_lines += 1
            else:
                cleaned_lines.append(stripped.lstrip("#").strip())
            continue

        cleaned_lines.append(_collapse_inline_spaces(line).rstrip())

    cleaned = "\n".join(cleaned_lines)
    cleaned = BLANK_LINE_PATTERN.sub("\n\n", cleaned).strip()
    return cleaned, {
        "cleaner": "pdf-basic-v1",
        "lines_removed": removed_lines,
        "toc_lines_removed": removed_toc_lines,
        "front_matter_lines_removed": removed_front_matter_lines,
        "brand_lines_removed": removed_brand_lines,
        "page_furniture_lines_removed": removed_page_furniture_lines,
    }


def _collapse_inline_spaces(line: str) -> str:
    if line.lstrip().startswith("|"):
        return line
    return re.sub(r"[ \t]{2,}", " ", line)


def _is_brand_line(stripped: str) -> bool:
    if not stripped or len(stripped) > 48:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in stripped):
        return False
    return bool(BRAND_LINE_PATTERN.match(stripped))


def _is_cover_metadata_line(stripped: str) -> bool:
    label_matches = len(COVER_LABEL_PATTERN.findall(stripped))
    colon_count = stripped.count(":") + stripped.count("：")
    return label_matches >= 2 or (label_matches >= 1 and colon_count >= 2)
