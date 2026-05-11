from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.services.composition.outline_service import _suggest_customer_specificity, _suggest_reuse_level, _suggest_section_class
from app.services.domain.synonyms import extract_domain_terms
from app.services.parsing.section_catalog import (
    build_section_catalog,
    flatten_section_catalog,
    normalize_section_heading,
    promote_body_headings,
)
from app.services.parsing.case_library_text import (
    build_block_contextual_text,
    build_block_semantic_retrieval_text,
    build_outline_semantic_retrieval_text,
    build_section_retrieval_text,
    classify_section_taxonomy,
    ordinal_prefix_length,
    parse_heading_ordinal_tokens,
)
from app.services.vectorstore.chunker import ChunkPayload, Chunker
from app.services.vectorstore.block_taxonomy import classify_block_taxonomy, extract_taxonomy_hints, heading_looks_like_document_title
from app.services.vectorstore.chunk_quality import assess_chunk_quality


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_MARKER_PATTERN = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)
SUMMARY_INLINE_FIGURE_CAPTION_PATTERN = re.compile(
    r"(?:^|[\s。；，,:：])图\s*\d+\s*[A-Za-z0-9\u4e00-\u9fff]{0,24}?(?:示意图|结构图|安装图|总布置图|外形图)\s*(?=<!--\s*image\s*-->)",
    re.IGNORECASE,
)
SUMMARY_INLINE_FIGURE_STUB_PATTERN = re.compile(r"\s*图…\s*")
SUMMARY_LEADING_FIGURE_CAPTION_PATTERN = re.compile(
    r"^\s*图\s*\d+\s*[A-Za-z0-9\u4e00-\u9fff]{0,24}?(?:示意图|结构图|安装图|总布置图|外形图)\s*",
    re.IGNORECASE,
)
FIGURE_BACKED_HEADING_HINTS = (
    "曲线",
    "curve",
    "单线图",
    "diagram",
    "总布置图",
    "drawing",
    "接线图",
    "原理图",
    "流程图",
)
TOC_PAGE_LINE_PATTERN = re.compile(r"^(?:#{1,6}\s+)?(?:[-*]\s+)?.+?(?:\t|\s{2,})\d{1,4}\s*$")
SENTENCE_FRAGMENT_HEADING_TOKEN_PATTERN = re.compile(r"[A-Za-z]+|[\u4e00-\u9fff]+")
EQUIPMENT_INSTANCE_LABEL_PATTERN = re.compile(r"^\d{1,2}\s*#")
NUMERIC_LABEL_HEADING_PATTERN = re.compile(r"^\d{1,2}\s*[：:、.．]\s*[\u4e00-\u9fff]")
ALPHA_LABEL_HEADING_PATTERN = re.compile(r"^[A-Za-z]\s+[\u4e00-\u9fff]")
SUMMARY_LIST_MARKER_PATTERN = re.compile(r"^(?:[-*+]\s*)+")
SUMMARY_PAGE_HEADER_PREFIX_PATTERN = re.compile(r"^[·•]\s*\d+\s*")
SUMMARY_INLINE_ORDINAL_PATTERN = re.compile(r"\b\d+(?:\.\d+){0,3}\b(?=\s*[\u4e00-\u9fff])")
SUMMARY_UPPERCASE_ASCII_NOISE_PATTERN = re.compile(
    r"\b[A-Z][A-Z0-9&;.,()'/\s-]{12,}(?:CO\.,?\s*LTD\.?|LIMITED|CORP\.?|COMPANY)\b"
)
SUMMARY_MISC_PAGE_NOISE_PATTERN = re.compile(r"\bM%\s*\d+\b")
SUMMARY_SENTENCE_LIKE_PUNCTUATION = ("，", ",", "；", ";", "。", "！", "？")
SUMMARY_SENTENCE_LIKE_CLAUSE_DELIMITERS = ("，", ",", "；", ";")
SUMMARY_SENTENCE_LIKE_MIN_LENGTH = 32
SUMMARY_CJK_SPLIT_SPACE_PATTERN = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
SUMMARY_MARKDOWN_EMPHASIS_PATTERN = re.compile(r"[*_`]{1,}")
SUMMARY_INLINE_LABEL_BOUNDARY_PATTERN = re.compile(
    r"(?P<head>见(?:下|上)?面?[\u4e00-\u9fffA-Za-z0-9]{0,18}(?:图|曲线|方案图|流程图|接线图|外形图))(?P<label>[\u4e00-\u9fff]{2,20}描述：)"
)
SUMMARY_INLINE_ORDINAL_LIST_LEAD_PATTERN = re.compile(
    r"(?P<head>[\u4e00-\u9fff]{0,24}(?:下列|如下)[\u4e00-\u9fff]{0,24}(?:条件|要求|参数|项目|事项|工作))\s+(?P<item>\d+\.\s*[\u4e00-\u9fff])"
)
SUMMARY_ORDINAL_CLAUSE_PATTERN = re.compile(r"(?:^|(?<=\s))(?P<ordinal>\d+(?:\.\d+){0,3}\.)\s*(?=[\u4e00-\u9fffA-Za-z])")
SUMMARY_LEADING_PIPE_HEADER_PREFIX_PATTERN = re.compile(r"^\s*(?:\|\s*[^|\n]{0,32}\s*){3,}\|\s*(?=[\u4e00-\u9fff])")
SUMMARY_LEADING_FIELD_HEADER_PREFIX_PATTERN = re.compile(
    r"^(?P<prefix>(?:(?:序号|设备名称|型号规格|规格型号|数量|外形尺寸|备注|接口名称|类型|来处|去处|电压等级|额定容量|适配电机功率|输出电流|重量|变频器尺寸|额定电压|额定电流)\s*){4,}(?:(?!(?:\d+(?:\.\d+){1,4}\.))[A-Za-z0-9*×x/().（）-]{2,}\s*)*)(?P<rest>.+)$"
)
SUMMARY_LEADING_INTERFACE_SIGNAL_PREFIX_PATTERN = re.compile(
    r"^(?P<prefix>(?:[\u4e00-\u9fffA-Za-z0-9（）()/-]{2,20}\s+(?:DI|DO|AI|AO|PI|PO|RTD|TTL|ETH|TCP|UDP|RS485|RS-485)\s*){2,})(?P<rest>.+)$"
)
SUMMARY_SPLIT_INITIAL_WORD_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s+([a-z]{4,})(?=\s+[A-Z]{2,}\b)")
SUMMARY_SPLIT_LATIN_SEQUENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]{1,2}\s+){2,}[A-Za-z0-9]{1,8}(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])[A-Z]{1,2}\s+[A-Z][A-Z0-9]{0,2}(?![A-Za-z0-9])"
)
SUMMARY_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
SUMMARY_CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")
SUMMARY_NUMERIC_PREFIX_PATTERN = re.compile(r"^(?:\d{1,4}\s+){1,3}\d{1,4}$")
SUMMARY_TRAILING_MODEL_TOKEN_PATTERN = re.compile(r"([A-Za-z]{2,}(?:-[A-Za-z0-9]+)+)\s*$")
SUMMARY_LEADING_PLACEHOLDER_NOTE_PATTERN = re.compile(r"^(?:尺寸暂定|参数待定|待定|暂定)\s*(?=[\u4e00-\u9fff])")
SUMMARY_TRAILING_VISUAL_REFERENCE_PATTERN = re.compile(r"(?:外形)?(?:如下图所示|见下图|见图|如下所示)\s*[：:]?\s*\d{0,4}\s*$")
SUMMARY_TRAILING_FIELD_LABEL_PATTERN = re.compile(r"(?:电机|负载参数)\s*[：:]\s*$")
SUMMARY_TRAILING_CAPTION_LABEL_PATTERN = re.compile(r"[A-Za-z]\s*型[\u4e00-\u9fffA-Za-z]{0,12}\s*[：:]?(?:\s*\d{0,4})?\s*$")
SUMMARY_TRAILING_ENGLISH_VALUE_NOTE_PATTERN = re.compile(r"\s+@\s*[A-Za-z][A-Za-z0-9\s()./%-]{4,}$")
SUMMARY_TRAILING_VALUE_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9/%.+\-~≤≥×x*()（）\s，,;；:]+$")
SUMMARY_MID_NUMERIC_NOISE_PATTERN = re.compile(r"(?<=[\u4e00-\u9fff])\s+\d{4,}:\s+(?=[\u4e00-\u9fff])")
SUMMARY_SHORT_CAPTION_LABEL_PATTERN = re.compile(r"^[A-Za-z]\s*型[\u4e00-\u9fffA-Za-z]{0,12}\s*[：:]?(?:\s*\d{0,4})?\s*$")
SUMMARY_ORDINAL_CODE_PATTERN = re.compile(r"^\d+(?:\.\d+){1,4}$")
SUMMARY_NUMERIC_OCR_LINE_PATTERN = re.compile(r"^\d{4,}(?::\d+)?$")
SUMMARY_SHORT_UPPER_CODE_PATTERN = re.compile(r"^(?=.*[A-Z])[A-Z0-9.:]{2,12}$")
SUMMARY_SHORT_DOTTED_CODE_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\.)[A-Za-z0-9.]{2,8}$")
SUMMARY_SHORT_OCR_ID_PATTERN = re.compile(r"^\d{1,3}\s+[A-Za-z0-9]{5,}(?:\s+[A-Za-z0-9#]{1,4}){0,2}$")
SUMMARY_NOISE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9#@*+=_/().,:;%$&'\"\\\[\]<>-]+")
SUMMARY_NOISE_HARD_SYMBOL_PATTERN = re.compile(r"[#@$*=«»ΔĐ≤≥]")
SUMMARY_LOW_SIGNAL_LABEL_PATTERN = re.compile(r"^(?:图示说明|示意如下|见下图|见图|如下图所示)$")
SUMMARY_GARBLED_TABLE_ASCII_PATTERN = re.compile(r"[A-Za-z][A-Za-z\s./-]{10,}$")
SUMMARY_REPORT_NOISE_KEYWORDS = (
    "report no",
    "client address",
    "tested by",
    "postcode",
    "model type",
    "serial nd",
    "test report",
    "line converter machine",
    "dayu electric",
)
SUMMARY_FIELD_CHAIN_KEYWORDS = (
    "型号",
    "参数",
    "数据表",
    "额定功率",
    "轴功率",
    "工作转速",
    "转速",
    "额定力矩",
    "空载阻力矩",
    "启动阻力矩",
    "静阻力矩",
    "转动惯量",
    "阻力矩曲线",
    "电压",
    "电流",
    "频率",
)
SUMMARY_FIELD_CHAIN_VERB_HINTS = (
    "采用",
    "实现",
    "用于",
    "通过",
    "具有",
    "提供",
    "控制",
    "满足",
    "配置",
    "运行",
    "说明",
    "如下",
    "建立",
    "提升",
    "保证",
    "检测",
    "保护",
)
SUMMARY_TABLE_HEADER_LABELS = frozenset(
    {
        "标准",
        "标题",
        "项目",
        "参数",
        "名称",
        "单位",
        "备注",
        "内容",
        "说明",
        "item",
        "title",
        "standard",
        "name",
        "value",
        "unit",
        "description",
        "parameter",
        "specification",
        "specifications",
        "content",
        "remark",
    }
)
SUMMARY_FIELD_HEADER_PREFIX_LABELS = frozenset(
    {
        "序号",
        "设备名称",
        "型号规格",
        "规格型号",
        "数量",
        "外形尺寸",
        "备注",
        "接口名称",
        "类型",
        "来处",
        "去处",
        "电压等级",
        "额定容量",
        "适配电机功率",
        "输出电流",
        "重量",
        "变频器尺寸",
        "额定电压",
        "额定电流",
        "标准",
        "标题",
        "项目",
        "参数",
        "名称",
        "单位",
        "内容",
        "说明",
    }
)


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
    catalog, sections, flattened, _chunks = _build_enriched_section_catalog(
        sample_entry=sample_entry,
        markdown=markdown,
        structure_hints=structure_hints,
    )
    outline_tree = [_section_to_outline_dict(section) for section in sections]
    document_title = str(catalog.get("document_title") or "").strip()
    top_level_titles = [str(section.get("title") or "").strip() for section in sections if str(section.get("title") or "").strip()]
    if not top_level_titles and document_title:
        top_level_titles = [document_title]
    semantic_retrieval_text = build_outline_semantic_retrieval_text(
        document_title=document_title,
        file_name=str(sample_entry.get("file_name") or ""),
        profile=str(sample_entry.get("profile") or sample_entry.get("detected_profile") or ""),
        top_level_titles=top_level_titles,
        flattened=flattened,
    )
    _strip_internal_section_fields(sections)
    return {
        "sample_id": sample_entry["sample_id"],
        "file_name": sample_entry["file_name"],
        "file_format": sample_entry["file_format"],
        "source": sample_entry.get("source"),
        "library_track": sample_entry.get("library_track") or sample_entry.get("track") or "pilot_main",
        "profile": sample_entry.get("profile") or sample_entry.get("detected_profile"),
        "ingestion_recommendation": sample_entry.get("ingestion_recommendation"),
        "document_title": document_title or None,
        "heading_count": len(flattened),
        "max_heading_level": max((item["level"] for item in flattened), default=0),
        "top_level_titles": top_level_titles,
        "semantic_retrieval_text": semantic_retrieval_text,
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
    chunker = chunker or Chunker()
    catalog, sections, flat_sections, chunks = _build_enriched_section_catalog(
        sample_entry=sample_entry,
        markdown=markdown,
        structure_hints=structure_hints,
        chunker=chunker,
    )
    document_title = str(catalog.get("document_title") or "").strip()
    blocks: list[dict[str, Any]] = []
    current_section_index = -1
    current_section: dict[str, Any] | None = None
    current_section_active_until: int | None = None
    subchunk_index = 0
    for chunk in chunks:
        reusable_payloads, current_section_index, current_section, current_section_active_until = _expand_reusable_chunk_payloads(
            chunk=chunk,
            flat_sections=flat_sections,
            current_section_index=current_section_index,
            current_section=current_section,
            current_section_active_until=current_section_active_until,
            chunker=chunker,
        )
        for active_chunk, active_section, anchor_source in reusable_payloads:
            section_heading_path = (
                str(active_section.get("section_path") or "").strip()
                if active_section
                else str(active_chunk.heading_path or "").strip()
            )
            section_title = (
                str(active_section.get("title") or "").strip()
                if active_section
                else str(active_chunk.heading_path or "").strip()
            )
            section_aliases = list(active_section.get("heading_aliases") or []) if active_section else []
            normalized_heading = (
                str(active_section.get("normalized_heading") or "").strip()
                if active_section
                else ""
            )
            if active_section is None and anchor_source == "unmatched":
                if _should_skip_unmatched_chunk(active_chunk):
                    continue
            if not _is_reusable_chunk(active_chunk):
                continue
            section_class = _suggest_section_class(section_title or active_chunk.heading_path or "")
            customer_specificity = _suggest_customer_specificity(section_class=section_class, title=active_chunk.heading_path or "")
            parameter_sensitive = bool(active_chunk.metadata.get("needs_asset_lookup")) or active_chunk.chunk_type == "TABLE"
            reuse_level = _suggest_reuse_level(
                section_class=section_class,
                customer_specificity=customer_specificity,
                parameter_sensitive=parameter_sensitive,
            )
            taxonomy = classify_block_taxonomy(
                content=active_chunk.content,
                heading_path=section_heading_path or active_chunk.heading_path,
                chunk_type=active_chunk.chunk_type,
                front_matter=bool(active_chunk.metadata.get("front_matter")),
                needs_asset_lookup=bool(active_chunk.metadata.get("needs_asset_lookup")),
            )
            contextual_text = build_block_contextual_text(
                document_title=document_title,
                file_name=str(sample_entry.get("file_name") or ""),
                section=active_section,
                chunk=active_chunk,
                fallback_heading_path=section_heading_path or active_chunk.heading_path,
                taxonomy=taxonomy,
            )
            semantic_retrieval_text = build_block_semantic_retrieval_text(
                contextual_text=contextual_text,
                chunk_content=active_chunk.content,
            )
            blocks.append(
                {
                    "sample_id": sample_entry["sample_id"],
                    "file_name": sample_entry["file_name"],
                    "file_format": sample_entry["file_format"],
                    "source": sample_entry.get("source"),
                    "library_track": sample_entry.get("library_track") or sample_entry.get("track") or "pilot_main",
                    "chunk_index": active_chunk.chunk_index,
                    "subchunk_index": subchunk_index,
                    "chunk_type": active_chunk.chunk_type,
                    "heading_path": section_heading_path or active_chunk.heading_path,
                    "source_section_id": active_section.get("section_id") if active_section else None,
                    "section_anchor_source": anchor_source,
                    "source_heading": active_section.get("source_heading") if active_section else active_chunk.heading_path,
                    "normalized_heading": normalized_heading or None,
                    "heading_aliases": section_aliases,
                    "section_path": active_section.get("section_path") if active_section else (section_heading_path or active_chunk.heading_path),
                    "normalized_section_path": active_section.get("normalized_section_path") if active_section else None,
                    "heading_family": list(active_section.get("heading_family") or []) if active_section else [],
                    "page_span": active_section.get("page_span") if active_section else None,
                    "content_span": active_section.get("content_span") if active_section else None,
                    "section_summary": active_section.get("section_summary") if active_section else None,
                    "section_retrieval_text": active_section.get("section_retrieval_text") if active_section else None,
                    "contextual_text": contextual_text,
                    "semantic_retrieval_text": semantic_retrieval_text,
                    "domain_terms": list(active_section.get("domain_terms") or []) if active_section else [],
                    "taxonomy_hints": list(active_section.get("taxonomy_hints") or []) if active_section else [],
                    "contextualized_block_text": semantic_retrieval_text,
                    "section_level": active_section.get("level") if active_section else None,
                    "section_class": section_class,
                    "customer_specificity": customer_specificity,
                    "reuse_level": reuse_level,
                    "content_risk_level": active_chunk.metadata.get("content_risk_level"),
                    "needs_asset_lookup": bool(active_chunk.metadata.get("needs_asset_lookup")),
                    "front_matter": bool(active_chunk.metadata.get("front_matter")),
                    "section_type": active_section.get("section_type") if active_section else (active_chunk.metadata.get("section_type") or taxonomy["section_type"]),
                    "equipment_type": active_section.get("equipment_type") if active_section else (active_chunk.metadata.get("equipment_type") or taxonomy["equipment_type"]),
                    "content_form": active_section.get("content_form") if active_section else (active_chunk.metadata.get("content_form") or taxonomy["content_form"]),
                    "token_count": active_chunk.token_count,
                    "content": active_chunk.content,
                }
            )
            subchunk_index += 1
    materialized_section_ids = {
        str(block.get("source_section_id") or "").strip()
        for block in blocks
        if str(block.get("source_section_id") or "").strip()
    }
    for section in _flatten_section_refs(sections):
        synthetic_chunk = _build_synthetic_section_summary_chunk(section=section)
        if synthetic_chunk is None:
            continue
        section_id = str(section.get("section_id") or "").strip()
        if not section_id or section_id in materialized_section_ids:
            continue
        section_heading_path = str(section.get("section_path") or synthetic_chunk.heading_path or "").strip()
        section_title = str(section.get("title") or section.get("source_heading") or synthetic_chunk.heading_path or "").strip()
        section_class = _suggest_section_class(section_title or synthetic_chunk.heading_path or "")
        customer_specificity = _suggest_customer_specificity(section_class=section_class, title=section_heading_path or synthetic_chunk.heading_path or "")
        parameter_sensitive = bool(synthetic_chunk.metadata.get("needs_asset_lookup")) or synthetic_chunk.chunk_type == "TABLE"
        reuse_level = _suggest_reuse_level(
            section_class=section_class,
            customer_specificity=customer_specificity,
            parameter_sensitive=parameter_sensitive,
        )
        contextual_text = build_block_contextual_text(
            document_title=document_title,
            file_name=str(sample_entry.get("file_name") or ""),
            section=section,
            chunk=synthetic_chunk,
            fallback_heading_path=section_heading_path or synthetic_chunk.heading_path,
            taxonomy={
                "section_type": str(section.get("section_type") or synthetic_chunk.metadata.get("section_type") or "unknown"),
                "equipment_type": str(section.get("equipment_type") or synthetic_chunk.metadata.get("equipment_type") or "generic"),
                "content_form": str(section.get("content_form") or synthetic_chunk.metadata.get("content_form") or "narrative"),
            },
        )
        semantic_retrieval_text = build_block_semantic_retrieval_text(
            contextual_text=contextual_text,
            chunk_content=synthetic_chunk.content,
        )
        blocks.append(
            {
                "sample_id": sample_entry["sample_id"],
                "file_name": sample_entry["file_name"],
                "file_format": sample_entry["file_format"],
                "source": sample_entry.get("source"),
                "library_track": sample_entry.get("library_track") or sample_entry.get("track") or "pilot_main",
                "chunk_index": synthetic_chunk.chunk_index,
                "subchunk_index": subchunk_index,
                "chunk_type": synthetic_chunk.chunk_type,
                "heading_path": section_heading_path or synthetic_chunk.heading_path,
                "source_section_id": section.get("section_id"),
                "section_anchor_source": "section_summary_fallback",
                "source_heading": section.get("source_heading") or synthetic_chunk.heading_path,
                "normalized_heading": section.get("normalized_heading") or None,
                "heading_aliases": list(section.get("heading_aliases") or []),
                "section_path": section.get("section_path") or (section_heading_path or synthetic_chunk.heading_path),
                "normalized_section_path": section.get("normalized_section_path"),
                "heading_family": list(section.get("heading_family") or []),
                "page_span": section.get("page_span"),
                "content_span": section.get("content_span"),
                "section_summary": section.get("section_summary"),
                "section_retrieval_text": section.get("section_retrieval_text"),
                "contextual_text": contextual_text,
                "semantic_retrieval_text": semantic_retrieval_text,
                "domain_terms": list(section.get("domain_terms") or []),
                "taxonomy_hints": list(section.get("taxonomy_hints") or []),
                "contextualized_block_text": semantic_retrieval_text,
                "section_level": section.get("level"),
                "section_class": section_class,
                "customer_specificity": customer_specificity,
                "reuse_level": reuse_level,
                "content_risk_level": synthetic_chunk.metadata.get("content_risk_level"),
                "needs_asset_lookup": bool(synthetic_chunk.metadata.get("needs_asset_lookup")),
                "front_matter": bool(synthetic_chunk.metadata.get("front_matter")),
                "section_type": section.get("section_type") or synthetic_chunk.metadata.get("section_type"),
                "equipment_type": section.get("equipment_type") or synthetic_chunk.metadata.get("equipment_type"),
                "content_form": section.get("content_form") or synthetic_chunk.metadata.get("content_form"),
                "token_count": synthetic_chunk.token_count,
                "content": synthetic_chunk.content,
                "synthetic_section_summary": True,
            }
        )
        materialized_section_ids.add(section_id)
        subchunk_index += 1
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
    if _looks_like_toc_chunk(chunk):
        return False
    if chunk.metadata.get("content_risk_level") == "high":
        return False
    if chunk.chunk_type == "TABLE" and len(chunk.content) > 2200:
        return False
    normalized = " ".join(chunk.content.split())
    if len(normalized) < 80:
        return False
    return True


def _looks_like_toc_chunk(chunk: ChunkPayload) -> bool:
    lines = [line.strip() for line in str(chunk.content or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return False

    toc_like_lines = 0
    for line in lines:
        if "|" in line:
            return False
        if TOC_PAGE_LINE_PATTERN.match(line):
            toc_like_lines += 1
            continue
        if line.startswith(("#", "-", "*")):
            return False

    return toc_like_lines >= max(3, len(lines) - 1)


def _looks_like_sentence_fragment_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading or len(heading) < 20:
        return False
    tokens = SENTENCE_FRAGMENT_HEADING_TOKEN_PATTERN.findall(heading)
    if len(tokens) < 5:
        return False
    lowercase_word_count = sum(
        1
        for token in re.findall(r"[A-Za-z]+", heading)
        if token == token.lower()
    )
    has_sentence_punctuation = any(marker in heading for marker in (":", "：", ",", "，", ".", ";"))
    return has_sentence_punctuation and lowercase_word_count >= 3


def _looks_like_unmatched_fragment_chunk(chunk: ChunkPayload) -> bool:
    heading = str(chunk.heading_path or "").strip()
    if not heading or " > " in heading or len(heading) < 20:
        return False
    if parse_heading_ordinal_tokens(heading):
        return False
    heading_tokens = SENTENCE_FRAGMENT_HEADING_TOKEN_PATTERN.findall(heading)
    if len(heading_tokens) < 5:
        return False
    lowercase_word_count = sum(
        1
        for token in re.findall(r"[A-Za-z]+", heading)
        if token == token.lower()
    )
    has_sentence_punctuation = any(marker in heading for marker in (":", "：", ",", "，", ".", ";"))
    if not has_sentence_punctuation or lowercase_word_count < 3:
        return False
    return chunk.chunk_type == "PLAIN"


def _looks_like_garbled_unmatched_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading:
        return False
    compact_heading = re.sub(r"\s+", "", heading)
    if not compact_heading:
        return False
    if re.search(r"[\u0400-\u04FF]", heading):
        return True
    if re.search(r"JC-Q[A-Z0-9-]*", heading, re.IGNORECASE):
        return True

    chinese_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", heading)
    alpha_tokens = re.findall(r"[A-Za-z]+", heading)
    digit_tokens = re.findall(r"\d+", heading)
    if chinese_tokens:
        return False
    if len(compact_heading) <= 8 and len(alpha_tokens) <= 2:
        return True
    if digit_tokens and len(alpha_tokens) <= 2 and len(compact_heading) <= 18:
        return True
    if len(alpha_tokens) <= 3 and compact_heading.isascii() and compact_heading.upper() != compact_heading.lower():
        return True
    return False


def _should_skip_unmatched_chunk(chunk: ChunkPayload) -> bool:
    if _looks_like_unmatched_fragment_chunk(chunk):
        return True
    quality = assess_chunk_quality(
        chunk_type=chunk.chunk_type,
        raw_content=chunk.content,
        heading_path=chunk.heading_path,
    )
    if quality.reasons:
        return True
    return _looks_like_garbled_unmatched_heading(str(chunk.heading_path or ""))


def _looks_like_equipment_instance_label_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading:
        return False
    if not EQUIPMENT_INSTANCE_LABEL_PATTERN.match(heading):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", heading))


def _looks_like_enumerated_label_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading:
        return False
    return bool(
        NUMERIC_LABEL_HEADING_PATTERN.match(heading)
        or ALPHA_LABEL_HEADING_PATTERN.match(heading)
    )


def _looks_like_low_signal_label_heading(text: str) -> bool:
    heading = str(text or "").strip()
    if not heading or " > " in heading:
        return False
    if _looks_like_equipment_instance_label_heading(heading):
        return True
    if _looks_like_enumerated_label_heading(heading):
        return True
    if parse_heading_ordinal_tokens(heading):
        return False
    if _looks_like_sentence_fragment_heading(heading):
        return False
    tokens = SENTENCE_FRAGMENT_HEADING_TOKEN_PATTERN.findall(heading)
    if not tokens or len(tokens) > 12:
        return False
    compact_heading = re.sub(r"\s+", "", heading)
    if len(compact_heading) > 32:
        return False
    if re.search(r"[\u4e00-\u9fff]", heading):
        return True
    alpha_words = [token for token in re.findall(r"[A-Za-z]+", heading) if len(token) >= 4]
    return len(alpha_words) >= 2


def _is_chunk_near_current_section(
    *,
    chunk: ChunkPayload,
    current_section: dict[str, Any],
    current_section_active_until: int | None = None,
    max_distance: int = 6,
) -> bool:
    content_span = current_section.get("content_span") or {}
    start_index = content_span.get("chunk_start")
    end_index = content_span.get("chunk_end")
    if start_index is None and end_index is None:
        return False
    try:
        chunk_index = int(chunk.chunk_index)
        start_value = int(start_index if start_index is not None else end_index)
        end_value = int(end_index if end_index is not None else start_index)
    except (TypeError, ValueError):
        return False
    if current_section_active_until is not None:
        try:
            end_value = max(end_value, int(current_section_active_until))
        except (TypeError, ValueError):
            pass
    return start_value - 1 <= chunk_index <= end_value + max_distance


def _should_contextually_carry_forward_chunk(
    *,
    chunk: ChunkPayload,
    current_section: dict[str, Any] | None,
    current_section_active_until: int | None = None,
) -> bool:
    if current_section is None:
        return False
    if not _is_chunk_near_current_section(
        chunk=chunk,
        current_section=current_section,
        current_section_active_until=current_section_active_until,
    ):
        return False
    if _looks_like_unmatched_fragment_chunk(chunk):
        return False
    return _looks_like_low_signal_label_heading(str(chunk.heading_path or ""))


def _get_section_active_end_index(section: dict[str, Any] | None, *, fallback: int | None = None) -> int | None:
    if section is None:
        return fallback
    content_span = section.get("content_span") or {}
    for key in ("chunk_end", "chunk_start"):
        value = content_span.get(key)
        try:
            return max(int(value), int(fallback)) if fallback is not None else int(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _build_synthetic_section_summary_chunk(*, section: dict[str, Any]) -> ChunkPayload | None:
    if section.get("children"):
        return None
    section_heading_path = str(section.get("section_path") or section.get("source_heading") or section.get("title") or "").strip()
    if not section_heading_path or heading_looks_like_document_title(section_heading_path):
        return None
    section_summary = str(section.get("section_summary") or "").strip()
    summary_has_image_marker = bool(section.get("_section_summary_has_image_marker"))
    if not section_summary and not summary_has_image_marker:
        return None
    if not summary_has_image_marker and not IMAGE_MARKER_PATTERN.search(section_summary):
        return None

    cleaned_summary = _clean_synthetic_section_summary(section_summary)
    if not cleaned_summary and not _looks_like_figure_backed_heading(section_heading_path):
        return None

    content_parts = [f"### {str(section.get('source_heading') or section.get('title') or section_heading_path).strip()}"]
    if cleaned_summary:
        content_parts.append(cleaned_summary)
    content = "\n\n".join(part for part in content_parts if part).strip()
    if not content:
        return None

    content_span = section.get("content_span") or {}
    chunk_index = content_span.get("chunk_end")
    if chunk_index is None:
        chunk_index = content_span.get("chunk_start")
    try:
        normalized_chunk_index = int(chunk_index)
    except (TypeError, ValueError):
        normalized_chunk_index = -1

    taxonomy = classify_block_taxonomy(
        content=content,
        heading_path=section_heading_path,
        chunk_type="PLAIN",
        front_matter=False,
        needs_asset_lookup=True,
    )
    return ChunkPayload(
        chunk_index=normalized_chunk_index,
        chunk_type="PLAIN",
        content=content,
        token_count=max(1, len(content) // 4),
        heading_path=section_heading_path,
        metadata={
            "content_risk_level": "low",
            "front_matter": False,
            "needs_asset_lookup": True,
            "synthetic_section_summary": True,
            **taxonomy,
        },
    )


def _clean_synthetic_section_summary(text: str) -> str:
    normalized = _normalize_section_summary_text(text)
    normalized = IMAGE_MARKER_PATTERN.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _looks_like_figure_backed_heading(text: str) -> bool:
    heading = str(text or "").casefold()
    return any(token in heading for token in FIGURE_BACKED_HEADING_HINTS)


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
        "heading_family": list(section.get("heading_family") or []),
        "page_span": section.get("page_span"),
        "content_span": section.get("content_span"),
        "section_summary": section.get("section_summary"),
        "section_retrieval_text": section.get("section_retrieval_text"),
        "section_type": section.get("section_type"),
        "equipment_type": section.get("equipment_type"),
        "content_form": section.get("content_form"),
        "taxonomy_hints": list(section.get("taxonomy_hints") or []),
        "domain_terms": list(section.get("domain_terms") or []),
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


def _build_enriched_section_catalog(
    *,
    sample_entry: dict[str, Any],
    markdown: str,
    structure_hints: dict[str, Any] | None = None,
    chunker: Chunker | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[ChunkPayload]]:
    chunker = chunker or Chunker()
    catalog = build_section_catalog(markdown, structure_hints=structure_hints)
    sections = catalog.get("sections") or []
    flat_sections = _flatten_section_refs(sections)
    promoted_markdown = promote_body_headings(markdown, structure_hints=structure_hints)
    chunks = chunker.split(promoted_markdown)

    for section in flat_sections:
        section["_direct_chunk_indexes"] = []
        section["_direct_chunk_texts"] = []

    current_section_index = -1
    current_section: dict[str, Any] | None = None
    for chunk in chunks:
        segment_payloads = _segment_chunk_section_payloads(
            chunk=chunk,
            flat_sections=flat_sections,
            current_section_index=current_section_index,
            current_section=current_section,
        )
        if segment_payloads:
            current_section_index = segment_payloads[-1][0]
            current_section = segment_payloads[-1][1]
            for _segment_index, active_section, segment_text in segment_payloads:
                active_section["_direct_chunk_indexes"].append(int(chunk.chunk_index))
                active_section["_direct_chunk_texts"].append(segment_text)
            continue

        resolved_section_index, active_section, _anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=flat_sections,
            current_section_index=current_section_index,
            current_section=current_section,
        )
        if active_section is not None:
            current_section_index = resolved_section_index
            current_section = active_section
            active_section["_direct_chunk_indexes"].append(int(chunk.chunk_index))
            active_section["_direct_chunk_texts"].append(str(chunk.content or ""))

    document_title = str(catalog.get("document_title") or "").strip()
    file_name = str(sample_entry.get("file_name") or "").strip()
    for section in sections:
        _finalize_section_enrichment(
            section=section,
            document_title=document_title,
            file_name=file_name,
        )
    return catalog, sections, flatten_section_catalog(sections), chunks


def _flatten_section_refs(sections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        flattened.append(section)
        children = section.get("children") or []
        if children:
            flattened.extend(_flatten_section_refs(children))
    return flattened


def _finalize_section_enrichment(
    *,
    section: dict[str, Any],
    document_title: str,
    file_name: str,
) -> tuple[list[int], list[int], list[str]]:
    direct_chunk_indexes = [int(item) for item in (section.get("_direct_chunk_indexes") or [])]
    direct_chunk_texts = [str(item) for item in (section.get("_direct_chunk_texts") or []) if str(item).strip()]
    subtree_chunk_indexes = list(direct_chunk_indexes)
    subtree_page_nos: list[int] = []
    subtree_texts = list(direct_chunk_texts)

    page_no = section.get("page_no")
    if page_no is not None:
        try:
            subtree_page_nos.append(int(page_no))
        except (TypeError, ValueError):
            pass

    for child in section.get("children") or []:
        child_chunk_indexes, child_page_nos, child_texts = _finalize_section_enrichment(
            section=child,
            document_title=document_title,
            file_name=file_name,
        )
        subtree_chunk_indexes.extend(child_chunk_indexes)
        subtree_page_nos.extend(child_page_nos)
        subtree_texts.extend(child_texts)

    heading_family = [
        item.strip()
        for item in str(section.get("normalized_section_path") or "").split(">")
        if item.strip()
    ]
    if heading_family:
        section["heading_family"] = heading_family
    elif section.get("normalized_heading"):
        section["heading_family"] = [str(section.get("normalized_heading"))]

    if subtree_page_nos:
        section["page_span"] = [min(subtree_page_nos), max(subtree_page_nos)]
    if subtree_chunk_indexes:
        section["content_span"] = {
            "chunk_start": min(subtree_chunk_indexes),
            "chunk_end": max(subtree_chunk_indexes),
        }

    section_summary = ""
    summary_has_image_marker = False
    summary_sources: list[list[str]] = []
    if direct_chunk_texts:
        summary_sources.append(direct_chunk_texts)
    summary_sources.append(subtree_texts)
    for source_texts in summary_sources:
        candidate_summary, candidate_has_image_marker = _build_section_summary(
            source_heading=str(section.get("source_heading") or section.get("title") or ""),
            chunk_texts=source_texts,
        )
        summary_has_image_marker = summary_has_image_marker or candidate_has_image_marker
        if candidate_summary:
            section_summary = candidate_summary
            break
    if section_summary:
        section["section_summary"] = section_summary
    if summary_has_image_marker:
        section["_section_summary_has_image_marker"] = True
    taxonomy = classify_section_taxonomy(section=section, section_summary=section_summary, subtree_texts=subtree_texts)
    section["section_type"] = taxonomy["section_type"]
    section["equipment_type"] = taxonomy["equipment_type"]
    section["content_form"] = taxonomy["content_form"]
    section["taxonomy_hints"] = extract_taxonomy_hints(
        str(section.get("section_path") or ""),
        section_summary,
        *subtree_texts[:2],
    )
    section["domain_terms"] = extract_domain_terms(
        "\n".join(
            part
            for part in (
                str(section.get("section_path") or ""),
                section_summary,
                *subtree_texts[:2],
            )
            if part
        )
    )
    section["section_retrieval_text"] = build_section_retrieval_text(
        section=section,
        document_title=document_title,
        file_name=file_name,
        section_summary=section_summary,
    )
    section["contextual_text"] = section["section_retrieval_text"]
    section["semantic_retrieval_text"] = section["section_retrieval_text"]

    section.pop("_direct_chunk_indexes", None)
    section.pop("_direct_chunk_texts", None)
    return subtree_chunk_indexes, subtree_page_nos, subtree_texts


def _build_section_summary(*, source_heading: str, chunk_texts: list[str]) -> tuple[str, bool]:
    normalized_heading = normalize_section_heading(source_heading)
    snippets: list[str] = []
    has_image_marker = False
    for text in chunk_texts:
        if IMAGE_MARKER_PATTERN.search(str(text or "")):
            has_image_marker = True
        snippet = _normalize_section_summary_text(text)
        if not snippet:
            continue
        if normalized_heading and normalize_section_heading(snippet) == normalized_heading:
            continue
        snippets.append(snippet)
        if len(snippets) >= 2:
            break
    if not snippets:
        return "", has_image_marker
    summary = _join_section_summary_snippets(snippets)
    summary = _postprocess_section_summary(summary)
    if not summary:
        return "", has_image_marker
    summary = _truncate_summary_text(summary, limit=260)
    return summary, has_image_marker


def _normalize_section_summary_text(text: str) -> str:
    source_lines = [
        line
        for line in str(text or "").splitlines()
        if line.strip() and not str(line).lstrip().startswith("#")
    ]
    tabular_summary = _summarize_tab_delimited_property_rows(source_lines)
    if tabular_summary:
        return tabular_summary
    lines = [_sanitize_section_summary_line(line) for line in source_lines]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    filtered_lines = list(lines)
    while filtered_lines and _looks_like_sentence_like_summary_heading(filtered_lines[0]):
        filtered_lines = filtered_lines[1:]
    if not filtered_lines:
        return ""
    if any(_line_has_sentence_signal(line) for line in filtered_lines):
        filtered_lines = [line for line in filtered_lines if not _looks_like_summary_label_fragment(line)]
    if not filtered_lines:
        return ""
    table_lines = [line for line in filtered_lines if "|" in line]
    if len(table_lines) >= 2:
        cells: list[str] = []
        for line in table_lines[:3]:
            for cell in line.split("|"):
                normalized = re.sub(r"\s+", " ", cell).strip()
                normalized = _collapse_summary_cjk_spacing(normalized)
                normalized = _normalize_summary_punctuation_spacing(normalized)
                if not normalized or set(normalized) <= {"-", ":"}:
                    continue
                if normalized not in cells:
                    cells.append(normalized)
            if len(cells) >= 6:
                break
        return " ".join(cells[:6])
    normalized = re.sub(r"\s+", " ", " ".join(filtered_lines)).strip()
    normalized = _collapse_summary_cjk_spacing(normalized)
    normalized = _normalize_summary_punctuation_spacing(normalized)
    normalized = _compact_summary_ordinal_clauses(normalized)
    return _truncate_summary_text(normalized, limit=180)


def _summarize_tab_delimited_property_rows(lines: list[str]) -> str:
    if sum(1 for line in lines if "\t" in line) < 4:
        return ""

    records: list[str] = []
    seen: set[str] = set()
    pending_value = ""

    for raw_line in lines:
        cells = [_normalize_summary_table_cell(cell) for cell in str(raw_line).split("\t")]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        first = cells[0]
        second = cells[1] if len(cells) >= 2 else ""
        first_has_cjk = bool(SUMMARY_CJK_CHAR_PATTERN.search(first))
        second_has_cjk = bool(SUMMARY_CJK_CHAR_PATTERN.search(second))

        record = ""
        if first_has_cjk:
            record = _compose_summary_property_record(first, second or pending_value)
            pending_value = ""
        elif len(cells) >= 2:
            if second_has_cjk:
                record = second
                pending_value = ""
            else:
                pending_value = second
                continue
        elif SUMMARY_CJK_CHAR_PATTERN.search(first):
            record = _compose_summary_property_record(first, pending_value)
            pending_value = ""
        else:
            if _looks_like_summary_noise_fragment(first):
                continue
            pending_value = first
            continue

        record = _normalize_summary_property_record(record)
        if not record:
            continue
        dedupe_key = normalize_section_heading(record)
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(record)
        if len(records) >= 8:
            break

    if len(records) < 4:
        return ""
    summary = "；".join(records)
    if len(summary) > 180:
        summary = summary[:179].rstrip("； ") + "…"
    return summary


def _normalize_summary_table_cell(text: str) -> str:
    cleaned = IMAGE_MARKER_PATTERN.sub(" ", str(text or ""))
    cleaned = SUMMARY_INLINE_FIGURE_CAPTION_PATTERN.sub(" ", cleaned)
    cleaned = SUMMARY_INLINE_FIGURE_STUB_PATTERN.sub(" ", cleaned)
    cleaned = SUMMARY_LEADING_FIGURE_CAPTION_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _collapse_summary_cjk_spacing(cleaned)
    cleaned = _collapse_summary_split_latin_sequences(cleaned)
    cleaned = _normalize_summary_punctuation_spacing(cleaned)
    cleaned = SUMMARY_TRAILING_ENGLISH_VALUE_NOTE_PATTERN.sub("", cleaned).strip()
    cleaned = _strip_summary_trailing_visual_reference(cleaned)
    cleaned = _strip_summary_trailing_field_label(cleaned)
    cleaned = _strip_summary_trailing_caption_label(cleaned)
    cleaned = _strip_summary_trailing_orphan_colon(cleaned)
    return cleaned


def _compose_summary_property_record(label: str, value: str) -> str:
    normalized_label = _normalize_summary_table_cell(label)
    normalized_value = _normalize_summary_table_cell(value)
    if not normalized_label:
        return normalized_value
    if not normalized_value:
        return normalized_label
    if normalized_value.startswith(normalized_label):
        return normalized_value
    return f"{normalized_label} {normalized_value}".strip()


def _normalize_summary_property_record(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = _strip_summary_trailing_orphan_colon(normalized)
    if not normalized:
        return ""
    if not SUMMARY_CJK_CHAR_PATTERN.search(normalized):
        return ""
    if SUMMARY_LOW_SIGNAL_LABEL_PATTERN.fullmatch(normalized):
        return ""
    return normalized


def _line_has_sentence_signal(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if any(token in normalized for token in SUMMARY_SENTENCE_LIKE_PUNCTUATION + ("：", ":")):
        return True
    return len(normalize_section_heading(normalized)) >= 18


def _looks_like_summary_label_fragment(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized or any(token in normalized for token in SUMMARY_SENTENCE_LIKE_PUNCTUATION + ("：", ":", "（", "）", "(", ")")):
        return False
    compact = normalize_section_heading(normalized)
    if not compact or len(compact) > 12:
        return False
    if re.fullmatch(r"[A-Za-z0-9\s./-]+", normalized):
        return True
    if SUMMARY_CJK_CHAR_PATTERN.search(normalized) and re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff\s./-]+", normalized):
        cjk_count = len(SUMMARY_CJK_CHAR_PATTERN.findall(normalized))
        return cjk_count <= 4 or any(ch.isdigit() for ch in normalized)
    return False


def _looks_like_sentence_like_summary_heading(text: str) -> bool:
    stripped = _sanitize_section_summary_line(text)
    if not stripped:
        return False
    if len(list(SUMMARY_ORDINAL_CLAUSE_PATTERN.finditer(stripped))) >= 2:
        return False
    candidate = stripped
    if not parse_heading_ordinal_tokens(candidate):
        ordinal_match = SUMMARY_INLINE_ORDINAL_PATTERN.search(candidate)
        if ordinal_match is None or ordinal_match.start() > 16:
            return False
        candidate = candidate[ordinal_match.start() :].strip()
    if not parse_heading_ordinal_tokens(candidate):
        return False
    normalized = normalize_section_heading(candidate)
    if len(normalized) < SUMMARY_SENTENCE_LIKE_MIN_LENGTH:
        return False
    punctuation_hits = sum(candidate.count(token) for token in SUMMARY_SENTENCE_LIKE_PUNCTUATION)
    has_clause_delimiter = any(token in candidate for token in SUMMARY_SENTENCE_LIKE_CLAUSE_DELIMITERS)
    return has_clause_delimiter or punctuation_hits >= 2


def _sanitize_section_summary_line(text: str) -> str:
    stripped = SUMMARY_MARKDOWN_EMPHASIS_PATTERN.sub(" ", str(text or "").strip())
    stripped = SUMMARY_LIST_MARKER_PATTERN.sub("", stripped)
    stripped = SUMMARY_PAGE_HEADER_PREFIX_PATTERN.sub("", stripped).strip()
    stripped = SUMMARY_INLINE_FIGURE_CAPTION_PATTERN.sub(" ", stripped)
    stripped = SUMMARY_INLINE_FIGURE_STUB_PATTERN.sub(" ", stripped)
    stripped = SUMMARY_LEADING_FIGURE_CAPTION_PATTERN.sub("", stripped)
    stripped = IMAGE_MARKER_PATTERN.sub(" ", stripped)
    prefix, separator, suffix = stripped.partition(" - ")
    if separator and suffix:
        prefix = SUMMARY_PAGE_HEADER_PREFIX_PATTERN.sub("", prefix).strip()
        if prefix and not re.search(r"[\u4e00-\u9fff]", prefix):
            ascii_like = re.fullmatch(r"[A-Za-z0-9&;.,()'\\/\s-]+", prefix)
            if ascii_like and len(prefix) >= 12:
                stripped = suffix.strip()
    ordinal_match = SUMMARY_INLINE_ORDINAL_PATTERN.search(stripped)
    if ordinal_match:
        prefix = stripped[: ordinal_match.start()].strip()
        if prefix and not re.search(r"[\u4e00-\u9fff]", prefix):
            ascii_like = re.fullmatch(r"[A-Za-z0-9&;.,()'%\\/\s-]+", prefix)
            if ascii_like and len(prefix) >= 12:
                stripped = stripped[ordinal_match.start() :].strip()
    stripped = SUMMARY_UPPERCASE_ASCII_NOISE_PATTERN.sub("", stripped)
    stripped = SUMMARY_MISC_PAGE_NOISE_PATTERN.sub("", stripped)
    stripped = SUMMARY_MID_NUMERIC_NOISE_PATTERN.sub(" ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    stripped = _collapse_summary_cjk_spacing(stripped)
    stripped = _collapse_summary_split_latin_sequences(stripped)
    stripped = _strip_summary_leading_pipe_header_prefix(stripped)
    stripped = _strip_summary_leading_field_header_prefix(stripped)
    stripped = SUMMARY_LEADING_PLACEHOLDER_NOTE_PATTERN.sub("", stripped).strip()
    stripped = _strip_summary_noise_edges(stripped)
    stripped = _strip_summary_trailing_visual_reference(stripped)
    stripped = _strip_summary_trailing_field_label(stripped)
    stripped = _strip_summary_trailing_caption_label(stripped)
    stripped = _strip_summary_trailing_orphan_colon(stripped)
    stripped = _normalize_summary_punctuation_spacing(stripped)
    stripped = _normalize_summary_inline_boundaries(stripped)
    if _looks_like_summary_placeholder_note(stripped):
        return ""
    if _looks_like_summary_header_stub(stripped):
        return ""
    if _looks_like_summary_table_header_fragment(stripped):
        return ""
    if _looks_like_summary_field_name_chain(stripped):
        return ""
    if SUMMARY_SHORT_CAPTION_LABEL_PATTERN.fullmatch(stripped):
        return ""
    if SUMMARY_LOW_SIGNAL_LABEL_PATTERN.fullmatch(stripped):
        return ""
    if stripped and not any(ch.isalnum() or SUMMARY_CJK_CHAR_PATTERN.match(ch) for ch in stripped):
        return ""
    if stripped and not SUMMARY_CJK_CHAR_PATTERN.search(stripped) and _looks_like_summary_noise_fragment(stripped):
        return ""
    return stripped


def _collapse_summary_cjk_spacing(text: str) -> str:
    return SUMMARY_CJK_SPLIT_SPACE_PATTERN.sub("", str(text or "").strip())


def _collapse_summary_split_latin_sequences(text: str) -> str:
    collapsed = SUMMARY_SPLIT_INITIAL_WORD_PATTERN.sub(r"\1\2", str(text or "").strip())

    def _replace(match: re.Match[str]) -> str:
        return re.sub(r"\s+", "", str(match.group(0) or ""))

    return SUMMARY_SPLIT_LATIN_SEQUENCE_PATTERN.sub(_replace, collapsed)


def _normalize_summary_punctuation_spacing(text: str) -> str:
    normalized = re.sub(r"\s*、\s*", "、", str(text or "").strip())
    normalized = re.sub(r"\s+([，。；：！？])", r"\1", normalized)
    normalized = re.sub(r"([，。；：！？])\s+(?=[\u4e00-\u9fff（(])", r"\1", normalized)
    normalized = re.sub(r"（\s+", "（", normalized)
    normalized = re.sub(r"\s+）", "）", normalized)
    normalized = re.sub(r"\(\s+", "(", normalized)
    normalized = re.sub(r"\s+\)", ")", normalized)
    normalized = re.sub(r"([）\)])\s+(?=[\u4e00-\u9fff])", r"\1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_summary_inline_boundaries(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = SUMMARY_INLINE_LABEL_BOUNDARY_PATTERN.sub(r"\g<head>。\g<label>", normalized)
    normalized = SUMMARY_INLINE_ORDINAL_LIST_LEAD_PATTERN.sub(r"\g<head>：\g<item>", normalized)
    return normalized


def _compact_summary_ordinal_clauses(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    matches = list(SUMMARY_ORDINAL_CLAUSE_PATTERN.finditer(normalized))
    if len(matches) < 2:
        return normalized
    if len(matches) == 2 and "：" not in normalized and ":" not in normalized and len(normalized) < 80:
        return normalized

    clauses: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        clause = normalized[start:end].strip()
        clause = SUMMARY_INLINE_FIGURE_STUB_PATTERN.sub(" ", clause)
        clause = re.sub(r"\s+", " ", clause).strip(" ；;,")
        clause = _normalize_summary_punctuation_spacing(clause)
        clause = _normalize_summary_inline_boundaries(clause)
        if clause:
            clauses.append(clause)

    if len(clauses) < 2:
        return normalized

    display_clauses = clauses[:5]
    pieces: list[str] = []
    for index, clause in enumerate(display_clauses):
        normalized_clause = clause.strip()
        if index < len(display_clauses) - 1:
            normalized_clause = normalized_clause.rstrip("。；;")
        pieces.append(normalized_clause)
    compacted = "；".join(piece for piece in pieces if piece)
    if len(clauses) > len(display_clauses):
        compacted = compacted.rstrip("。；") + "…"
    return compacted or normalized


def _truncate_summary_text(text: str, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if not normalized or len(normalized) <= limit:
        return normalized
    minimum_cut = max(24, int(limit * 0.55))
    for delimiter in ("；", "。", ";", "，", ",", " "):
        boundary = normalized.rfind(delimiter, 0, limit + 1)
        if boundary >= minimum_cut:
            trimmed = normalized[:boundary].rstrip(" ，,；;。")
            if trimmed:
                return trimmed + "…"
    return normalized[: limit - 1].rstrip() + "…"


def _join_section_summary_snippets(snippets: list[str]) -> str:
    merged = ""
    for raw_snippet in snippets:
        snippet = str(raw_snippet or "").strip()
        if not snippet:
            continue
        if not merged:
            merged = snippet
            continue
        separator = " "
        if merged.endswith(("。", "！", "？", "；", ";", "：", ":")) or merged.endswith("…"):
            separator = " "
        elif SUMMARY_CJK_CHAR_PATTERN.match(snippet[:1]) or snippet[:1].isalnum():
            separator = "；" if any(token in merged for token in ("：", ":", "；", ";")) else "。"
        merged = f"{merged}{separator}{snippet}"
    return merged.strip()


def _strip_summary_leading_pipe_header_prefix(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized.startswith("|"):
        return normalized
    return SUMMARY_LEADING_PIPE_HEADER_PREFIX_PATTERN.sub("", normalized).strip()


def _strip_summary_leading_field_header_prefix(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    match = SUMMARY_LEADING_FIELD_HEADER_PREFIX_PATTERN.match(normalized)
    if match is not None:
        remainder = str(match.group("rest") or "").strip()
        while remainder:
            stripped_remainder = remainder
            for label in sorted(SUMMARY_FIELD_HEADER_PREFIX_LABELS, key=len, reverse=True):
                if stripped_remainder.startswith(label):
                    stripped_remainder = stripped_remainder[len(label) :].lstrip()
                    break
            if stripped_remainder == remainder:
                break
            remainder = stripped_remainder
        if remainder and SUMMARY_CJK_CHAR_PATTERN.search(remainder) and (
            any(token in remainder for token in SUMMARY_FIELD_CHAIN_VERB_HINTS)
            or any(token in remainder for token in ("以", "由", "应", "可", "将", "并"))
            or len(normalize_section_heading(remainder)) >= 8
        ):
            return remainder
    parts = normalized.split()
    header_hits = 0
    for idx, part in enumerate(parts):
        compact = normalize_section_heading(part)
        if compact in SUMMARY_FIELD_HEADER_PREFIX_LABELS or re.fullmatch(r"[A-Za-z0-9*×x/().-]{2,}", part):
            header_hits += 1
            continue
        if header_hits >= 4 and SUMMARY_CJK_CHAR_PATTERN.search(part) and (
            any(token in part for token in SUMMARY_FIELD_CHAIN_VERB_HINTS)
            or any(token in part for token in ("以", "由", "应", "可", "将", "并"))
            or len(normalize_section_heading(part)) >= 8
        ):
            return " ".join(parts[idx:]).strip()
        break
    return normalized


def _strip_summary_leading_interface_signal_prefix(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    match = SUMMARY_LEADING_INTERFACE_SIGNAL_PREFIX_PATTERN.match(normalized)
    if match is None:
        return normalized
    remainder = str(match.group("rest") or "").strip()
    if remainder and SUMMARY_CJK_CHAR_PATTERN.search(remainder) and (
        "：" in remainder
        or ":" in remainder
        or any(token in remainder for token in SUMMARY_FIELD_CHAIN_VERB_HINTS)
        or len(normalize_section_heading(remainder)) >= 8
    ):
        return remainder
    return normalized


def _strip_summary_trailing_visual_reference(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    cleaned = SUMMARY_TRAILING_VISUAL_REFERENCE_PATTERN.sub("", normalized)
    if cleaned != normalized:
        return cleaned.rstrip(" ，,:：;；")
    return normalized


def _strip_summary_trailing_field_label(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    cleaned = SUMMARY_TRAILING_FIELD_LABEL_PATTERN.sub("", normalized)
    if cleaned != normalized:
        return cleaned.rstrip(" ，,;；").strip()
    return normalized


def _strip_summary_trailing_caption_label(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    cleaned = SUMMARY_TRAILING_CAPTION_LABEL_PATTERN.sub("", normalized)
    if cleaned != normalized:
        return cleaned.rstrip(" ，,:：;；").strip()
    return normalized


def _strip_summary_trailing_orphan_colon(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized or normalized[-1] not in {"：", ":"}:
        return normalized
    stem = normalized[:-1].rstrip()
    if not stem:
        return ""
    if stem.endswith(("如下", "说明", "如下所示", "示意如下", "参数如下")):
        return normalized
    return stem


def _looks_like_summary_value_suffix(*, prefix: str, suffix: str) -> bool:
    normalized_suffix = str(suffix or "").strip()
    if not normalized_suffix or not any(ch.isdigit() for ch in normalized_suffix):
        return False
    if not SUMMARY_TRAILING_VALUE_SUFFIX_PATTERN.fullmatch(normalized_suffix):
        return False
    last_field_separator = max(prefix.rfind("："), prefix.rfind(":"))
    if last_field_separator < 0:
        return False
    field_tail = prefix[last_field_separator + 1 :].strip()
    if not field_tail or any(token in field_tail for token in ("。", "；", ";")):
        return False
    return True


def _strip_summary_noise_edges(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = _strip_summary_leading_noise_prefix(normalized)
    normalized = _strip_summary_intermediate_noise_segments(normalized)
    normalized = _strip_summary_trailing_noise_suffix(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _strip_summary_leading_noise_prefix(text: str) -> str:
    match = SUMMARY_CJK_CHAR_PATTERN.search(text)
    if not match:
        return text
    prefix = text[: match.start()].strip()
    if prefix and SUMMARY_NUMERIC_PREFIX_PATTERN.fullmatch(prefix):
        return text[match.start() :].lstrip()
    if not prefix or not _looks_like_summary_noise_fragment(prefix):
        return text
    model_match = SUMMARY_TRAILING_MODEL_TOKEN_PATTERN.search(prefix)
    if model_match:
        leading_fragment = prefix[: model_match.start()].strip()
        trailing_model = str(model_match.group(1) or "").strip()
        if leading_fragment and _looks_like_summary_noise_fragment(leading_fragment):
            normalized = f"{trailing_model} {text[match.start():].lstrip()}".strip()
            return re.sub(r"^(?:\d{1,4}\s+){1,3}(?=[\u4e00-\u9fff])", "", normalized).strip()
    normalized = text[match.start() :].lstrip()
    return re.sub(r"^(?:\d{1,4}\s+){1,3}(?=[\u4e00-\u9fff])", "", normalized).strip()


def _strip_summary_trailing_noise_suffix(text: str) -> str:
    matches = list(SUMMARY_CJK_CHAR_PATTERN.finditer(text))
    if not matches:
        return text
    last_cjk = matches[-1]
    suffix = text[last_cjk.end() :]
    if not suffix.strip():
        return text
    punct_match = re.match(r"^(?P<punct>[\s。；，,:：]*)?(?P<rest>.*)$", suffix)
    if punct_match is None:
        return text
    punctuation = str(punct_match.group("punct") or "")
    remainder = str(punct_match.group("rest") or "").strip()
    if remainder and _looks_like_summary_value_suffix(prefix=text[: last_cjk.end()], suffix=remainder):
        return text
    if remainder and _looks_like_summary_noise_fragment(remainder):
        return f"{text[: last_cjk.end()]}{punctuation}".rstrip()
    return text


def _strip_summary_intermediate_noise_segments(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized or not SUMMARY_CJK_CHAR_PATTERN.search(normalized):
        return normalized
    pattern = re.compile(r"(?P<sep>[：:。；，]\s*)(?P<noise>[^\u4e00-\u9fff]{12,}?)(?=\s*[\u4e00-\u9fff])")

    def _replace(match: re.Match[str]) -> str:
        separator = str(match.group("sep") or "")
        noise = str(match.group("noise") or "").strip()
        if _looks_like_summary_noise_fragment(noise):
            return separator
        return match.group(0)

    return pattern.sub(_replace, normalized)


def _looks_like_summary_noise_fragment(text: str) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = raw.strip(" -_.,;:()[]")
    if not normalized:
        return False
    if SUMMARY_CJK_CHAR_PATTERN.search(normalized):
        return False
    if "|" in raw:
        alpha_count = sum(1 for ch in raw if ch.isalpha())
        if alpha_count >= 8:
            return True
    lowered = normalized.casefold()
    if any(keyword in lowered for keyword in SUMMARY_REPORT_NOISE_KEYWORDS):
        return True
    tokens = SUMMARY_NOISE_TOKEN_PATTERN.findall(normalized)
    token_count = len(tokens)
    single_char_token_count = sum(1 for token in tokens if len(token.strip()) == 1)
    long_digit_run_count = len(re.findall(r"\d{4,}", normalized))
    punctuation_count = sum(1 for ch in normalized if not ch.isalnum() and not ch.isspace())
    punctuation_ratio = punctuation_count / max(len(normalized), 1)
    letters = [ch for ch in normalized if ch.isalpha()]
    uppercase_ratio = (sum(1 for ch in letters if ch.isupper()) / len(letters)) if letters else 0.0
    if SUMMARY_CYRILLIC_PATTERN.search(normalized) or SUMMARY_NOISE_HARD_SYMBOL_PATTERN.search(normalized):
        return True
    if SUMMARY_ORDINAL_CODE_PATTERN.fullmatch(normalized):
        return False
    if SUMMARY_NUMERIC_OCR_LINE_PATTERN.fullmatch(normalized):
        return True
    if SUMMARY_SHORT_UPPER_CODE_PATTERN.fullmatch(normalized):
        return True
    if normalized.isalpha() and len(normalized) <= 8 and normalized[:1].isupper() and normalized[1:].islower():
        return True
    if any(ch.isdigit() for ch in normalized) and len(normalized) <= 24 and (" " in normalized or punctuation_count >= 2):
        return True
    if SUMMARY_SHORT_DOTTED_CODE_PATTERN.fullmatch(normalized):
        return True
    if SUMMARY_SHORT_OCR_ID_PATTERN.fullmatch(normalized):
        return True
    if raw.startswith(".") and raw.count(".") >= 3:
        return True
    if len(normalized) >= 12 and token_count >= 2 and uppercase_ratio >= 0.65:
        return True
    if token_count >= 6 and (single_char_token_count >= 3 or punctuation_ratio >= 0.16):
        return True
    if token_count >= 5 and long_digit_run_count >= 1:
        return True
    if len(normalized) >= 20 and token_count >= 6:
        return True
    return False


def _postprocess_section_summary(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    normalized = SUMMARY_INLINE_FIGURE_STUB_PATTERN.sub(" ", normalized)
    normalized = SUMMARY_MID_NUMERIC_NOISE_PATTERN.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = _strip_summary_leading_pipe_header_prefix(normalized)
    normalized = _strip_summary_leading_field_header_prefix(normalized)
    normalized = _strip_summary_leading_interface_signal_prefix(normalized)
    normalized = SUMMARY_LEADING_PLACEHOLDER_NOTE_PATTERN.sub("", normalized).strip()
    normalized = _strip_summary_noise_edges(normalized)
    normalized = _strip_summary_trailing_visual_reference(normalized)
    normalized = _strip_summary_trailing_field_label(normalized)
    normalized = _strip_summary_trailing_caption_label(normalized)
    normalized = _strip_summary_trailing_orphan_colon(normalized)
    normalized = _normalize_summary_punctuation_spacing(normalized)
    normalized = _normalize_summary_inline_boundaries(normalized)
    if _looks_like_summary_placeholder_note(normalized):
        return ""
    if _looks_like_summary_header_stub(normalized):
        return ""
    if _looks_like_summary_table_header_fragment(normalized):
        return ""
    if _looks_like_summary_field_name_chain(normalized):
        return ""
    if SUMMARY_SHORT_CAPTION_LABEL_PATTERN.fullmatch(normalized):
        return ""
    if SUMMARY_LOW_SIGNAL_LABEL_PATTERN.fullmatch(normalized):
        return ""
    if not normalized:
        return ""
    if not any(ch.isalnum() or SUMMARY_CJK_CHAR_PATTERN.match(ch) for ch in normalized):
        return ""
    if not SUMMARY_CJK_CHAR_PATTERN.search(normalized) and _looks_like_summary_noise_fragment(normalized):
        return ""
    return normalized


def _strip_internal_section_fields(sections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
    for section in sections:
        section.pop("_section_summary_has_image_marker", None)
        _strip_internal_section_fields(section.get("children") or [])


def _looks_like_summary_field_name_chain(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False
    if len(list(SUMMARY_ORDINAL_CLAUSE_PATTERN.finditer(normalized))) >= 2:
        return False
    compact = normalize_section_heading(normalized)
    if len(compact) < 20:
        return False
    if any(token in normalized for token in SUMMARY_SENTENCE_LIKE_PUNCTUATION + ("（", "）", "(", ")")):
        return False
    if any(token in normalized for token in SUMMARY_FIELD_CHAIN_VERB_HINTS):
        return False
    keyword_hits = [keyword for keyword in SUMMARY_FIELD_CHAIN_KEYWORDS if keyword in normalized]
    if len(keyword_hits) >= 4:
        return True
    if len(keyword_hits) >= 3 and re.search(r"图\s*\d+\s*[:：]", normalized):
        return True
    return False


def _looks_like_summary_header_stub(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False
    compact = normalize_section_heading(normalized)
    if not compact or len(compact) > 24:
        return False
    if any(token in normalized for token in SUMMARY_SENTENCE_LIKE_PUNCTUATION + ("：", ":")):
        return False
    if any(token in normalized for token in SUMMARY_FIELD_CHAIN_VERB_HINTS):
        return False
    header_hits = sum(1 for label in SUMMARY_FIELD_HEADER_PREFIX_LABELS if label in normalized)
    if header_hits >= 2:
        return True
    for label in SUMMARY_FIELD_HEADER_PREFIX_LABELS:
        if normalized.startswith(label):
            if "|" in normalized:
                return True
            tail = normalized[len(label) :].strip()
            if tail and len(normalize_section_heading(tail)) <= 8:
                return True
    return False


def _looks_like_summary_placeholder_note(text: str) -> bool:
    normalized = normalize_section_heading(str(text or ""))
    if not normalized:
        return False
    if len(normalized) > 8:
        return False
    return normalized in {"待定", "暂定", "预留", "备用", "尺寸暂定", "参数待定"}


def _looks_like_summary_table_header_fragment(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if "|" not in normalized:
        return False
    cells = [re.sub(r"\s+", " ", cell).strip() for cell in normalized.split("|") if cell.strip()]
    if not cells:
        return False
    label_count = 0
    garbled_count = 0
    for cell in cells:
        compact = normalize_section_heading(cell).replace(" ", "").casefold()
        if compact in SUMMARY_TABLE_HEADER_LABELS:
            label_count += 1
            continue
        ascii_tail = re.sub(r"[\u4e00-\u9fff\s]+", " ", cell).strip()
        if ascii_tail and SUMMARY_GARBLED_TABLE_ASCII_PATTERN.fullmatch(ascii_tail):
            garbled_count += 1
    if label_count >= 2:
        return True
    return label_count >= 1 and garbled_count >= 1 and not any(_line_has_sentence_signal(cell) for cell in cells)


def _segment_chunk_section_payloads(
    *,
    chunk: ChunkPayload,
    flat_sections: list[dict[str, Any]],
    current_section_index: int,
    current_section: dict[str, Any] | None,
) -> list[tuple[int, dict[str, Any], str]]:
    segments = _extract_chunk_heading_segments(chunk)
    if not segments:
        return []

    segment_section_index = current_section_index
    segment_section = current_section
    payloads: list[tuple[int, dict[str, Any], str]] = []
    for heading_text, segment_text in segments:
        segment_chunk = ChunkPayload(
            chunk_index=chunk.chunk_index,
            chunk_type=chunk.chunk_type,
            content=segment_text,
            token_count=max(1, len(segment_text) // 4),
            heading_path=heading_text,
            metadata=chunk.metadata,
        )
        resolved_section_index, active_section, _anchor_source = _resolve_chunk_section_anchor(
            chunk=segment_chunk,
            flat_sections=flat_sections,
            current_section_index=segment_section_index,
            current_section=segment_section,
        )
        if active_section is None:
            continue
        segment_section_index = resolved_section_index
        segment_section = active_section
        payloads.append((resolved_section_index, active_section, segment_text))
    return payloads


def _extract_chunk_heading_segments(chunk: ChunkPayload) -> list[tuple[str, str]]:
    lines = str(chunk.content or "").splitlines()
    if not lines:
        return []

    segments: list[tuple[str, str]] = []
    current_heading = str(chunk.heading_path or "").strip()
    current_lines: list[str] = []
    saw_heading = False
    for line in lines:
        match = HEADING_PATTERN.match(line.strip())
        if match:
            segment_text = "\n".join(current_lines).strip()
            if segment_text and _normalize_section_summary_text(segment_text):
                segments.append((current_heading or match.group(2).strip(), segment_text))
            current_heading = match.group(2).strip() or current_heading
            current_lines = [line]
            saw_heading = True
            continue
        current_lines.append(line)

    tail_text = "\n".join(current_lines).strip()
    if tail_text and _normalize_section_summary_text(tail_text):
        segments.append((current_heading, tail_text))

    if not saw_heading:
        return []
    return segments


def _resolve_chunk_section_anchor(
    *,
    chunk: ChunkPayload,
    flat_sections: list[dict[str, Any]],
    current_section_index: int,
    current_section: dict[str, Any] | None,
    current_section_active_until: int | None = None,
) -> tuple[int, dict[str, Any] | None, str]:
    matched_section_index, matched_section = _match_chunk_to_section(
        chunk=chunk,
        flat_sections=flat_sections,
        start_index=max(current_section_index, 0),
    )
    if matched_section is not None:
        return matched_section_index, matched_section, "heading_match"

    fallback_index, fallback_section = _match_chunk_to_section_by_ordinal_prefix(
        chunk=chunk,
        flat_sections=flat_sections,
        start_index=max(current_section_index, 0),
    )
    if fallback_section is not None:
        return fallback_index, fallback_section, "ordinal_fallback"

    if _should_contextually_carry_forward_chunk(
        chunk=chunk,
        current_section=current_section,
        current_section_active_until=current_section_active_until,
    ):
        return current_section_index, current_section, "contextual_carry_forward"

    if not str(chunk.heading_path or "").strip() and current_section is not None:
        return current_section_index, current_section, "carry_forward"
    return -1, None, "unmatched"


def _expand_reusable_chunk_payloads(
    *,
    chunk: ChunkPayload,
    flat_sections: list[dict[str, Any]],
    current_section_index: int,
    current_section: dict[str, Any] | None,
    current_section_active_until: int | None,
    chunker: Chunker,
) -> tuple[list[tuple[ChunkPayload, dict[str, Any] | None, str]], int, dict[str, Any] | None, int | None]:
    segment_payloads = _extract_chunk_heading_segments(chunk)
    if segment_payloads:
        expanded: list[tuple[ChunkPayload, dict[str, Any] | None, str]] = []
        segment_section_index = current_section_index
        segment_section = current_section
        segment_section_active_until = current_section_active_until
        for heading_text, segment_text in segment_payloads:
            segment_chunk = chunker._build_chunk(
                chunk_index=chunk.chunk_index,
                content=segment_text,
                heading_path=heading_text,
                base_metadata=chunk.metadata,
            )
            resolved_section_index, active_section, anchor_source = _resolve_chunk_section_anchor(
                chunk=segment_chunk,
                flat_sections=flat_sections,
                current_section_index=segment_section_index,
                current_section=segment_section,
                current_section_active_until=segment_section_active_until,
            )
            if active_section is not None:
                segment_section_index = resolved_section_index
                segment_section = active_section
                segment_section_active_until = _get_section_active_end_index(
                    active_section,
                    fallback=segment_chunk.chunk_index,
                )
            expanded.append((segment_chunk, active_section, anchor_source))
        if expanded:
            return expanded, segment_section_index, segment_section, segment_section_active_until

    resolved_section_index, active_section, anchor_source = _resolve_chunk_section_anchor(
        chunk=chunk,
        flat_sections=flat_sections,
        current_section_index=current_section_index,
        current_section=current_section,
        current_section_active_until=current_section_active_until,
    )
    if active_section is not None:
        updated_active_until = _get_section_active_end_index(
            active_section,
            fallback=chunk.chunk_index,
        )
        return [(chunk, active_section, anchor_source)], resolved_section_index, active_section, updated_active_until
    return [(chunk, None, anchor_source)], current_section_index, current_section, current_section_active_until


def _match_chunk_to_section_by_ordinal_prefix(
    *,
    chunk: ChunkPayload,
    flat_sections: list[dict[str, Any]],
    start_index: int,
) -> tuple[int, dict[str, Any] | None]:
    chunk_tokens = parse_heading_ordinal_tokens(str(chunk.heading_path or ""))
    if len(chunk_tokens) < 2:
        return -1, None

    best_index = -1
    best_section: dict[str, Any] | None = None
    best_score: tuple[int, int] | None = None
    for index, section in enumerate(flat_sections):
        section_tokens = parse_heading_ordinal_tokens(str(section.get("ordinal") or section.get("source_heading") or ""))
        if not section_tokens or len(section_tokens) >= len(chunk_tokens):
            continue
        if ordinal_prefix_length(section_tokens, chunk_tokens) != len(section_tokens):
            continue
        score = (len(section_tokens), -abs(index - start_index))
        if best_score is None or score > best_score:
            best_index = index
            best_section = section
            best_score = score
    return best_index, best_section
