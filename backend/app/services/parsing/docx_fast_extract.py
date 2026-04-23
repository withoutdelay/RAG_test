from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import zipfile
from xml.etree import ElementTree as ET


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = {"w": WORD_NAMESPACE}
PARAGRAPH_TAG = f"{{{WORD_NAMESPACE}}}p"
TABLE_TAG = f"{{{WORD_NAMESPACE}}}tbl"
ROW_TAG = f"{{{WORD_NAMESPACE}}}tr"
CELL_TAG = f"{{{WORD_NAMESPACE}}}tc"
TEXT_TAG = f"{{{WORD_NAMESPACE}}}t"
BREAK_TAG = f"{{{WORD_NAMESPACE}}}br"
TAB_TAG = f"{{{WORD_NAMESPACE}}}tab"
STYLE_VALUE_RE = re.compile(r"heading\s*([1-6])", re.IGNORECASE)
TEXT_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十0-9]+章\s*"), 2),
    (re.compile(r"^[一二三四五六七八九十]+[、.．]\s*"), 3),
    (re.compile(r"^\d+(?:\.\d+){0,4}\s+"), 3),
)


def extract_docx_markdown(path: str | Path) -> tuple[str, dict[str, Any]]:
    docx_path = Path(path)
    with zipfile.ZipFile(docx_path) as archive:
        document_xml = archive.read("word/document.xml")
        style_map = _load_style_map(archive)

    root = ET.fromstring(document_xml)
    body = root.find("w:body", WORD)
    if body is None:
        return "", _build_metadata(docx_path)

    lines: list[str] = []
    saw_title = False
    for child in body:
        if child.tag == PARAGRAPH_TAG:
            text = _extract_paragraph_text(child).strip()
            if not text:
                continue
            style_id = _paragraph_style_id(child)
            style_name = style_map.get(style_id, "")
            heading_level = _resolve_heading_level(style_id=style_id, style_name=style_name, text=text)
            if heading_level is None and not saw_title and _looks_like_document_title(text):
                heading_level = 1
            if heading_level is not None:
                lines.append(f"{'#' * heading_level} {text}")
                if heading_level == 1:
                    saw_title = True
            else:
                lines.append(text)
            lines.append("")
            continue

        if child.tag == TABLE_TAG:
            table_lines = _extract_table_markdown(child)
            if table_lines:
                lines.extend(table_lines)
                lines.append("")

    markdown = "\n".join(lines).strip()
    return markdown, _build_metadata(docx_path)


def _build_metadata(path: Path) -> dict[str, Any]:
    return {
        "source_name": path.name,
        "parser": "docx_fast_extract",
        "parser_backend_requested": "holdout_eval_fast_heuristic_only",
        "parser_backend_used": "docx_fast_extract",
        "asset_extraction_enabled": False,
        "format": "docx",
    }


def _load_style_map(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        styles_xml = archive.read("word/styles.xml")
    except KeyError:
        return {}
    root = ET.fromstring(styles_xml)
    style_map: dict[str, str] = {}
    for style in root.findall("w:style", WORD):
        style_id = str(style.attrib.get(f"{{{WORD_NAMESPACE}}}styleId") or "").strip()
        if not style_id:
            continue
        name_node = style.find("w:name", WORD)
        style_name = str(name_node.attrib.get(f"{{{WORD_NAMESPACE}}}val") if name_node is not None else "").strip()
        style_map[style_id] = style_name
    return style_map


def _extract_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == TEXT_TAG:
            value = str(node.text or "")
            if value:
                parts.append(value)
        elif node.tag == TAB_TAG:
            parts.append(" ")
        elif node.tag == BREAK_TAG:
            parts.append("\n")
    text = "".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _paragraph_style_id(paragraph: ET.Element) -> str:
    style_node = paragraph.find("w:pPr/w:pStyle", WORD)
    if style_node is None:
        return ""
    return str(style_node.attrib.get(f"{{{WORD_NAMESPACE}}}val") or "").strip()


def _resolve_heading_level(*, style_id: str, style_name: str, text: str) -> int | None:
    candidates = [str(style_id or "").strip(), str(style_name or "").strip()]
    for candidate in candidates:
        normalized = candidate.casefold()
        if normalized in {"title", "document title"} or "标题" in candidate:
            return 1
        match = STYLE_VALUE_RE.search(candidate)
        if match:
            return max(1, min(int(match.group(1)), 6))
        chinese_match = re.search(r"标题\s*([1-6])", candidate)
        if chinese_match:
            return max(1, min(int(chinese_match.group(1)), 6))
    compact_text = re.sub(r"\s+", "", str(text or ""))
    for pattern, level in TEXT_HEADING_PATTERNS:
        if pattern.search(compact_text):
            return level
    return None


def _looks_like_document_title(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped or len(stripped) > 80:
        return False
    if any(token in stripped for token in ("技术方案", "成套方案", "项目方案", "解决方案")):
        return True
    return False


def _extract_table_markdown(table: ET.Element) -> list[str]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", WORD):
        cells: list[str] = []
        for cell in row.findall("w:tc", WORD):
            text_parts = [_extract_paragraph_text(paragraph) for paragraph in cell.findall("w:p", WORD)]
            text = " ".join(part for part in text_parts if part).strip()
            cells.append(text)
        if any(cell for cell in cells):
            rows.append(cells)
    if not rows:
        return []
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    header = normalized_rows[0]
    body = normalized_rows[1:]
    lines = [
        "| " + " | ".join(cell or " " for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(cell or " " for cell in row) + " |")
    return lines
