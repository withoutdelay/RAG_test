from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.services.composition.outline_service import _suggest_customer_specificity, _suggest_reuse_level, _suggest_section_class
from app.services.parsing.section_catalog import (
    build_section_catalog,
    flatten_section_catalog,
    normalize_section_heading,
    promote_body_headings,
)
from app.services.vectorstore.chunker import ChunkPayload, Chunker
from app.services.vectorstore.block_taxonomy import classify_block_taxonomy, heading_looks_like_document_title


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class OutlineNode:
    title: str
    level: int
    heading_path: str
    children: tuple["OutlineNode", ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "heading_path": self.heading_path,
            "children": [child.to_dict() for child in self.children],
        }


def extract_outline_tree(markdown: str) -> list[OutlineNode]:
    catalog = build_section_catalog(markdown)
    section_nodes = tuple(_outline_node_from_section(section) for section in (catalog.get("sections") or []))
    document_title = str(catalog.get("document_title") or "").strip()
    if document_title and section_nodes:
        return (
            OutlineNode(
                title=document_title,
                level=1,
                heading_path=document_title,
                children=tuple(_prefix_outline_node(node, prefix=document_title) for node in section_nodes),
            ),
        )
    if section_nodes:
        return section_nodes

    raw_nodes: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        match = HEADING_PATTERN.match(line.strip())
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        node: dict[str, Any] = {
            "title": title,
            "level": level,
            "heading_path": "",
            "children": [],
        }
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        if stack:
            parent = stack[-1]
            node["heading_path"] = f"{parent['heading_path']} > {title}" if parent["heading_path"] else f"{parent['title']} > {title}"
            parent["children"].append(node)
        else:
            node["heading_path"] = title
            raw_nodes.append(node)
        stack.append(node)
    return tuple(_freeze_outline_node(node) for node in raw_nodes)


def flatten_outline_nodes(nodes: list[OutlineNode] | tuple[OutlineNode, ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        flattened.append(
            {
                "title": node.title,
                "level": node.level,
                "heading_path": node.heading_path,
                "section_class": _suggest_section_class(node.title),
            }
        )
        flattened.extend(flatten_outline_nodes(node.children))
    return flattened


def build_outline_library_entry(
    *,
    sample_entry: dict[str, Any],
    markdown: str,
    structure_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = build_section_catalog(markdown, structure_hints=structure_hints)
    sections = catalog.get("sections") or []
    flattened = flatten_section_catalog(sections)
    outline_tree = [_section_to_outline_dict(section) for section in sections]
    document_title = str(catalog.get("document_title") or "").strip()
    top_level_titles = [str(section.get("title") or "").strip() for section in sections if str(section.get("title") or "").strip()]
    if not top_level_titles and document_title:
        top_level_titles = [document_title]
    return {
        "sample_id": sample_entry["sample_id"],
        "file_name": sample_entry["file_name"],
        "file_format": sample_entry["file_format"],
        "library_track": sample_entry.get("library_track") or sample_entry.get("track") or "pilot_main",
        "profile": sample_entry.get("profile") or sample_entry.get("detected_profile"),
        "ingestion_recommendation": sample_entry.get("ingestion_recommendation"),
        "document_title": document_title or None,
        "heading_count": len(flattened),
        "max_heading_level": max((item["level"] for item in flattened), default=0),
        "top_level_titles": top_level_titles,
        "outline_tree": outline_tree,
        "flat_outline": flattened,
        "section_catalog": sections,
    }


def build_reusable_block_entries(
    *,
    sample_entry: dict[str, Any],
    markdown: str,
    structure_hints: dict[str, Any] | None = None,
    chunker: Chunker | None = None,
) -> list[dict[str, Any]]:
    chunker = chunker or Chunker(max_chars=1200)
    catalog = build_section_catalog(markdown, structure_hints=structure_hints)
    flat_sections = flatten_section_catalog(catalog.get("sections") or [])
    promoted_markdown = promote_body_headings(markdown, structure_hints=structure_hints)
    chunks = chunker.split(promoted_markdown)
    blocks: list[dict[str, Any]] = []
    current_section_index = -1
    current_section: dict[str, Any] | None = None
    for chunk in chunks:
        matched_section_index, matched_section = _match_chunk_to_section(
            chunk=chunk,
            flat_sections=flat_sections,
            start_index=max(current_section_index, 0),
        )
        if matched_section is not None:
            current_section_index = matched_section_index
            current_section = matched_section
        section_heading_path = (
            str(current_section.get("section_path") or "").strip()
            if current_section
            else str(chunk.heading_path or "").strip()
        )
        section_title = (
            str(current_section.get("title") or "").strip()
            if current_section
            else str(chunk.heading_path or "").strip()
        )
        section_aliases = list(current_section.get("heading_aliases") or []) if current_section else []
        normalized_heading = (
            str(current_section.get("normalized_heading") or "").strip()
            if current_section
            else ""
        )
        if not _is_reusable_chunk(chunk):
            continue
        section_class = _suggest_section_class(section_title or chunk.heading_path or "")
        customer_specificity = _suggest_customer_specificity(section_class=section_class, title=chunk.heading_path or "")
        parameter_sensitive = bool(chunk.metadata.get("needs_asset_lookup")) or chunk.chunk_type == "TABLE"
        reuse_level = _suggest_reuse_level(
            section_class=section_class,
            customer_specificity=customer_specificity,
            parameter_sensitive=parameter_sensitive,
        )
        taxonomy = classify_block_taxonomy(
            content=chunk.content,
            heading_path=section_heading_path or chunk.heading_path,
            chunk_type=chunk.chunk_type,
            front_matter=bool(chunk.metadata.get("front_matter")),
            needs_asset_lookup=bool(chunk.metadata.get("needs_asset_lookup")),
        )
        blocks.append(
            {
                "sample_id": sample_entry["sample_id"],
                "file_name": sample_entry["file_name"],
                "file_format": sample_entry["file_format"],
                "library_track": sample_entry.get("library_track") or sample_entry.get("track") or "pilot_main",
                "chunk_index": chunk.chunk_index,
                "chunk_type": chunk.chunk_type,
                "heading_path": section_heading_path or chunk.heading_path,
                "source_section_id": current_section.get("section_id") if current_section else None,
                "source_heading": current_section.get("source_heading") if current_section else chunk.heading_path,
                "normalized_heading": normalized_heading or None,
                "heading_aliases": section_aliases,
                "section_path": current_section.get("section_path") if current_section else (section_heading_path or chunk.heading_path),
                "normalized_section_path": current_section.get("normalized_section_path") if current_section else None,
                "section_level": current_section.get("level") if current_section else None,
                "section_class": section_class,
                "customer_specificity": customer_specificity,
                "reuse_level": reuse_level,
                "content_risk_level": chunk.metadata.get("content_risk_level"),
                "needs_asset_lookup": bool(chunk.metadata.get("needs_asset_lookup")),
                "front_matter": bool(chunk.metadata.get("front_matter")),
                "section_type": chunk.metadata.get("section_type") or taxonomy["section_type"],
                "equipment_type": chunk.metadata.get("equipment_type") or taxonomy["equipment_type"],
                "content_form": chunk.metadata.get("content_form") or taxonomy["content_form"],
                "token_count": chunk.token_count,
                "content": chunk.content,
            }
        )
    return blocks


def summarize_case_library(
    *,
    outline_entries: list[dict[str, Any]],
    block_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    by_track: dict[str, dict[str, int]] = {}
    for entry in outline_entries:
        track = str(entry.get("library_track") or "unknown")
        payload = by_track.setdefault(track, {"outline_docs": 0, "blocks": 0})
        payload["outline_docs"] += 1
    for entry in block_entries:
        track = str(entry.get("library_track") or "unknown")
        payload = by_track.setdefault(track, {"outline_docs": 0, "blocks": 0})
        payload["blocks"] += 1
    return {
        "outline_document_count": len(outline_entries),
        "reusable_block_count": len(block_entries),
        "track_summary": by_track,
    }


def render_case_library_markdown(
    *,
    summary: dict[str, Any],
    outline_entries: list[dict[str, Any]],
) -> str:
    lines = [
        "# Case Library Summary",
        "",
        f"- Outline documents: `{summary.get('outline_document_count', 0)}`",
        f"- Reusable blocks: `{summary.get('reusable_block_count', 0)}`",
        "",
        "## Track Summary",
        "",
    ]
    track_summary = summary.get("track_summary") or {}
    if track_summary:
        for track, payload in track_summary.items():
            lines.append(
                f"- `{track}`: outline_docs=`{payload.get('outline_docs', 0)}`, blocks=`{payload.get('blocks', 0)}`"
            )
    else:
        lines.append("- `none`")

    lines.extend(
        [
            "",
            "## Outline Documents",
            "",
            "| file_name | track | profile | headings | top_level_titles |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for entry in outline_entries:
        lines.append(
            "| {file_name} | {track} | {profile} | {headings} | {titles} |".format(
                file_name=entry.get("file_name"),
                track=entry.get("library_track"),
                profile=entry.get("profile"),
                headings=entry.get("heading_count"),
                titles=", ".join(entry.get("top_level_titles") or []),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _is_reusable_chunk(chunk: ChunkPayload) -> bool:
    if chunk.metadata.get("front_matter"):
        return False
    if heading_looks_like_document_title(str(chunk.heading_path or "")):
        return False
    if chunk.metadata.get("content_risk_level") == "high":
        return False
    if chunk.chunk_type == "TABLE" and len(chunk.content) > 2200:
        return False
    normalized = " ".join(chunk.content.split())
    if len(normalized) < 80:
        return False
    return True


def _freeze_outline_node(node: dict[str, Any]) -> OutlineNode:
    return OutlineNode(
        title=str(node["title"]),
        level=int(node["level"]),
        heading_path=str(node["heading_path"]),
        children=tuple(_freeze_outline_node(child) for child in node.get("children") or []),
    )


def _outline_node_from_section(section: dict[str, Any]) -> OutlineNode:
    return OutlineNode(
        title=str(section.get("title") or ""),
        level=int(section.get("level") or 1),
        heading_path=str(section.get("section_path") or section.get("title") or ""),
        children=tuple(_outline_node_from_section(child) for child in (section.get("children") or [])),
    )


def _prefix_outline_node(node: OutlineNode, *, prefix: str) -> OutlineNode:
    heading_path = f"{prefix} > {node.heading_path}" if node.heading_path else prefix
    return OutlineNode(
        title=node.title,
        level=node.level,
        heading_path=heading_path,
        children=tuple(_prefix_outline_node(child, prefix=prefix) for child in node.children),
    )


def _section_to_outline_dict(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": section.get("title"),
        "level": section.get("level"),
        "heading_path": section.get("section_path"),
        "normalized_heading": section.get("normalized_heading"),
        "source_signals": section.get("source_signals") or [],
        "children": [_section_to_outline_dict(child) for child in (section.get("children") or [])],
    }


def _match_chunk_to_section(
    *,
    chunk: ChunkPayload,
    flat_sections: list[dict[str, Any]],
    start_index: int,
) -> tuple[int, dict[str, Any] | None]:
    if not flat_sections:
        return -1, None
    heading_text = str(chunk.heading_path or "").strip()
    if not heading_text:
        return -1, None
    normalized_heading = promote_heading_match_key(heading_text)
    if not normalized_heading:
        return -1, None
    for index in range(start_index, len(flat_sections)):
        section = flat_sections[index]
        if _section_matches_heading(section=section, heading_text=heading_text, normalized_heading=normalized_heading):
            return index, section
    for index in range(0, min(start_index, len(flat_sections))):
        section = flat_sections[index]
        if _section_matches_heading(section=section, heading_text=heading_text, normalized_heading=normalized_heading):
            return index, section
    return -1, None


def _section_matches_heading(*, section: dict[str, Any], heading_text: str, normalized_heading: str) -> bool:
    if normalized_heading and normalized_heading == str(section.get("normalized_heading") or ""):
        return True
    source_heading = str(section.get("source_heading") or "")
    if source_heading and source_heading == heading_text:
        return True
    aliases = {str(item) for item in (section.get("heading_aliases") or []) if item}
    return normalized_heading in aliases


def promote_heading_match_key(text: str) -> str:
    return normalize_section_heading(text)
