from __future__ import annotations

import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.models.section_draft import SectionDraft
from app.services.agents.executor import ExecutorAgent
from app.services.composition.outline_service import outline_is_approved
from app.services.composition.section_quality import SectionQualityGateService
from app.services.retrieval import AssetRetrievalService
from app.services.retrieval.case_service import CaseLibraryService
from app.services.validation.service import flatten_outline_sections
from app.services.vectorstore.block_taxonomy import (
    content_form_is_table,
    extract_taxonomy_hints,
    heading_family_similarity,
    heading_focus_adjustment,
    heading_looks_like_document_title,
    infer_target_taxonomy,
    related_section_types,
    support_content_forms,
)
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError

DEFAULT_REUSE_LIMIT = 5
REUSE_CANDIDATE_MULTIPLIER = 3
REUSE_MIN_CANDIDATES = 6
FULL_SECTION_MIN_SCORE = 0.72
FULL_SECTION_MIN_LEAD = 0.08
FULL_SECTION_MAX_SOURCE_TOKENS = 1800
REUSE_TRACE_SECTION_LIMIT = 4
REUSE_TRACE_BLOCK_LIMIT = 6
REUSE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
TECHNICAL_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}\d*|\d+(?:\.\d+)+|[\u4e00-\u9fff]{2,}")
REUSE_STOPWORDS = {
    "项目",
    "方案",
    "系统",
    "技术",
    "章节",
    "当前",
    "相关",
    "说明",
    "用于",
    "以及",
    "进行",
}
FIGURE_ASSET_HINTS = (
    "图",
    "示意",
    "单线",
    "接线",
    "原理",
    "波形",
    "曲线",
    "布局",
    "布置",
    "外形",
    "拓扑",
    "流程",
    "结构",
    "主回路",
    "总体方案",
    "架构",
)
TABLE_ASSET_HINTS = (
    "表",
    "参数",
    "性能",
    "数据",
    "清单",
    "配置",
    "供货",
    "范围",
    "点表",
    "规格",
    "容量",
    "型号",
    "数量",
    "尺寸",
)
FORMULA_ASSET_HINTS = ("公式", "equation", "推导", "算式")
SECTION_ASSET_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "overall_solution": ("系统示意图", "总体架构图", "单线图", "主回路图"),
    "main_circuit_scheme": ("主回路图", "单线图", "一次接线图", "原理图"),
    "communication_interface": ("接口示意图", "通信拓扑图", "控制逻辑图", "点表"),
    "control_logic": ("控制逻辑图", "联锁逻辑图", "信号流程图", "运行流程图"),
    "protection_interlock": ("联锁关系图", "保护关系图", "控制逻辑图", "信号流程图"),
    "cabinet_layout": ("柜体外形图", "设备布置图", "柜内结构图", "接线示意图"),
    "installation_conditions": ("安装布置图", "基础图", "进出线布置图", "设备外形图"),
    "vfd_spec": ("系统示意图", "单线图", "结构图", "外形图"),
    "starter_spec": ("系统示意图", "启动曲线", "单线图", "结构图"),
    "motor_spec": ("系统示意图", "启动曲线", "负载曲线", "外形图"),
    "transformer_spec": ("原理图", "绕组示意图", "电压波形图", "参数表"),
    "bom_or_supply_list": ("供货清单", "配置表", "参数表"),
    "supply_scope": ("供货清单", "配置表", "参数表"),
}
EXTRACTIVE_SECTION_CLASSES = {"architecture", "configuration", "implementation", "custom"}
EXTRACTIVE_SECTION_TYPES = {
    "overall_solution",
    "design_basis",
    "site_conditions",
    "supply_scope",
    "bom_or_supply_list",
    "motor_spec",
    "vfd_spec",
    "starter_spec",
    "transformer_spec",
    "main_circuit_scheme",
    "control_logic",
    "communication_interface",
    "protection_interlock",
    "cabinet_layout",
    "installation_conditions",
}
SECTION_TEMPLATE_HEADINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "main_circuit_scheme": {
        "主回路结构与运行切换": ("主回路", "主接线", "一次接线", "旁路", "切换", "隔离", "结构"),
        "设备选型与容量配置": ("选型", "配置", "容量", "功率单元", "整流变压器", "器件"),
        "关键技术参数": ("参数", "技术数据", "规格", "额定", "性能"),
        "保护与联锁条件": ("保护", "联锁", "闭锁", "报警"),
    },
    "communication_interface": {
        "通信架构与接口方式": ("通信", "通讯", "接口", "dcs", "plc", "modbus", "profibus", "profinet"),
        "接口与信号清单": ("信号", "点表", "ai", "ao", "di", "do", "清单"),
        "联锁与调试约束": ("联锁", "调试", "试验", "投运"),
    },
    "bom_or_supply_list": {
        "主要设备及供货范围": ("供货", "范围", "清单", "设备"),
        "关键参数与配置说明": ("参数", "规格", "配置", "说明"),
    },
    "supply_scope": {
        "主要设备及供货范围": ("供货", "范围", "清单", "设备"),
        "关键参数与配置说明": ("参数", "规格", "配置", "说明"),
    },
}
EXTRACTIVE_SECTION_OPENINGS = {
    "main_circuit_scheme": "本项目主回路按照安全隔离、旁路切换和连续运行要求进行配置，具体结构如下。",
    "communication_interface": "本项目控制系统接口按照上位机协同、信号闭环和调试可实施的原则进行配置，具体如下。",
    "supply_scope": "以下内容用于说明本项目主要设备供货边界和系统组成，最终以双方确认的供货清单为准。",
    "bom_or_supply_list": "以下内容用于说明本项目主要设备供货边界和系统组成，最终以双方确认的供货清单为准。",
}
EXTRACTIVE_TABLE_LEADS: dict[str, dict[str, str]] = {
    "main_circuit_scheme": {
        "设备选型与容量配置": "主要设备配置如下表所示。",
        "关键技术参数": "主要技术参数如下表所示。",
    },
    "communication_interface": {
        "接口与信号清单": "建议接口与信号清单如下表所示。",
    },
    "supply_scope": {
        "主要设备及供货范围": "主要设备供货范围如下表所示。",
        "关键参数与配置说明": "关键参数与配置说明如下表所示。",
    },
    "bom_or_supply_list": {
        "主要设备及供货范围": "主要设备供货范围如下表所示。",
        "关键参数与配置说明": "关键参数与配置说明如下表所示。",
    },
}
GENERIC_REUSE_HEADINGS = {
    "产品简介",
    "技术方案",
    "总体方案",
    "总体说明",
    "项目概述",
    "系统方案",
}
INTERNAL_REUSE_HEADING_PATTERNS = (
    re.compile(r"^(建议插入图表|建议图表|建议参考资产|推荐资产|可用参考资料|可用复用包|替换与禁用约束|参考摘要)$", re.IGNORECASE),
    re.compile(r"^(图表建议|插图建议|图表清单)$", re.IGNORECASE),
)
LATIN_ENUM_REUSE_HEADING_PATTERN = re.compile(r"^[A-Z]\.\s*.+$")
GENERIC_LABEL_REUSE_PATTERN = re.compile(r"^(概述|说明|补充说明|其他|附加说明)$")
ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:[A-Z]+:([^\]]+)\]\]")
BANNED_TERM_PATTERNS = (
    re.compile(r"(?:项目名称|买方|卖方|客户|用户)\s*[:：]\s*([^\n]{2,80})"),
)
STANDARD_REPLACE_FIELDS = (
    "project_name",
    "customer_name",
    "buyer_name",
    "seller_name",
    "location",
    "factory_name",
    "production_line_name",
    "voltage_level",
    "power_rating",
    "quantity",
    "delivery_scope",
)
SECTION_OUTPUT_NOISE_PATTERNS = (
    re.compile(r"^\s*本节基于.+草拟.*$", re.IGNORECASE),
    re.compile(r"^\s*目标章节标题[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*参考摘要[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*可用参考资料[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*请撰写章节.+$", re.IGNORECASE),
)
REWRITE_LEAKAGE_TOKENS = (
    "章节标题:",
    "章节目的:",
    "章节类型:",
    "当前关键参数:",
    "必须替换字段:",
    "禁止沿用词:",
    "已根据要求完成重写",
    "QWEN模型",
)


def build_section_context(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any] | None = None,
    limit: int = 3,
    preferred_evidence_ids: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=limit,
        preferred_evidence_ids=preferred_evidence_ids,
    )

    context_lines = []
    citations: list[dict[str, Any]] = []
    for item in selected:
        heading_path = item.get("heading_path") or []
        heading_text = " > ".join(str(segment) for segment in heading_path if segment)
        raw_excerpt = str(item.get("raw_content") or item.get("summary") or "").strip()[:600]
        context_lines.append(f"- {item.get('source_title')} {heading_text}: {raw_excerpt}".strip())
        citations.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": item.get("relevance_score"),
                "type": item.get("type"),
                "excerpt": raw_excerpt,
            }
        )
    return "\n".join(context_lines), citations


def build_reuse_citations(reusable_blocks: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for index, block in enumerate(reusable_blocks[:limit], start=1):
        heading_path = [str(item) for item in (block.get("heading_path") or []) if str(item).strip()]
        signature = (str(block.get("source_title") or ""), tuple(heading_path))
        if signature in seen:
            continue
        seen.add(signature)
        citations.append(
            {
                "evidence_id": block.get("block_id") or f"reuse_{index:03d}",
                "source_doc_id": block.get("source_doc_id"),
                "source_title": block.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": block.get("selection_score") or block.get("reusability_score"),
                "type": block.get("block_type") or "section",
                "excerpt": _build_citation_excerpt(str(block.get("content_md") or "")),
            }
        )
    return citations


def prioritize_recommended_assets(recommended_assets: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    if not recommended_assets:
        return []

    def _priority(asset: dict[str, Any]) -> tuple[float, float]:
        visual_role = str(asset.get("visual_role") or "").lower()
        asset_type = str(asset.get("asset_type") or "").lower()
        score = float(asset.get("score") or 0)
        bonus = 0.0
        if visual_role == "engineering_figure":
            bonus += 0.35
        elif visual_role == "formula_candidate":
            bonus += 0.25
        elif asset_type == "table":
            bonus += 0.22
        elif visual_role == "illustration":
            bonus += 0.05
        if visual_role == "page_furniture":
            bonus -= 0.55
        return (score + bonus, score)

    prioritized = sorted(recommended_assets, key=_priority, reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, str, str]] = set()
    for item in prioritized:
        signature = (
            str(item.get("document_name") or "").strip(),
            str(item.get("heading_path") or "").strip(),
            str(item.get("title") or "").strip(),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(item)
    non_furniture = [item for item in prioritized if str(item.get("visual_role") or "").lower() != "page_furniture"]
    if deduped:
        non_furniture = [item for item in deduped if str(item.get("visual_role") or "").lower() != "page_furniture"]
        if non_furniture:
            return non_furniture[:limit]
        return deduped[:limit]
    if non_furniture:
        return non_furniture[:limit]
    return prioritized[:limit]


def filter_recommended_assets_for_section(
    recommended_assets: list[dict[str, Any]],
    *,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    if not recommended_assets:
        return []
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    technical_noise_tokens = ("认证", "检验", "检测", "报告", "证书", "试验")
    filtered: list[dict[str, Any]] = []
    for asset in recommended_assets:
        visual_role = str(asset.get("visual_role") or "").lower()
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        if str(asset.get("title") or "").strip() in {"目录", "目 录"}:
            continue
        if str(metadata.get("label") or "").strip() == "document_index":
            continue
        heading_text = " ".join(
            str(part)
            for part in (
                asset.get("heading_path"),
                asset.get("title"),
                asset.get("caption"),
                asset.get("preview_text"),
            )
            if part
        )
        if target_section_type in EXTRACTIVE_SECTION_TYPES and visual_role == "page_furniture":
            continue
        if target_section_type in EXTRACTIVE_SECTION_TYPES and any(token in heading_text for token in technical_noise_tokens):
            continue
        filtered.append(asset)
    if filtered:
        return filtered
    if target_section_type in EXTRACTIVE_SECTION_TYPES:
        return []
    return recommended_assets


def tighten_recommended_assets_for_reuse(
    recommended_assets: list[dict[str, Any]],
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not recommended_assets or not reusable_blocks:
        return recommended_assets
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type not in EXTRACTIVE_SECTION_TYPES:
        return recommended_assets
    preferred_limit = min(len(recommended_assets), 3)

    anchor_document_names = {
        str(block.get("source_title") or "").strip()
        for block in list(reusable_blocks)[:3]
        if str(block.get("source_title") or "").strip()
    }
    anchor_headings = [
        " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        for block in list(reusable_blocks)[:3]
        if block.get("heading_path")
    ]
    strong_matches: list[dict[str, Any]] = []
    contextual_matches: list[dict[str, Any]] = []
    related_types = related_section_types(target_section_type)

    def _dedupe_assets(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for asset in group:
                signature = str(
                    asset.get("asset_id")
                    or f"{asset.get('document_name')}|{asset.get('heading_path')}|{asset.get('title')}"
                )
                if signature in seen:
                    continue
                seen.add(signature)
                merged.append(asset)
        return merged

    for asset in recommended_assets:
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        asset_section_type = str(metadata.get("section_type") or "unknown").lower()
        document_name = str(asset.get("document_name") or "").strip()
        heading_text = str(asset.get("heading_path") or "").strip()
        if heading_looks_like_document_title(str(asset.get("title") or heading_text)):
            continue
        doc_match = bool(anchor_document_names) and document_name in anchor_document_names
        family_bonus = max(
            (heading_family_similarity(anchor_heading, heading_text) for anchor_heading in anchor_headings),
            default=0.0,
        )

        if doc_match and family_bonus >= 0.18:
            strong_matches.append(asset)
            continue
        if doc_match and (
            family_bonus >= 0.18
            or (asset_section_type == target_section_type and family_bonus >= 0.12)
            or (asset_section_type in related_types and family_bonus >= 0.08)
        ):
            contextual_matches.append(asset)
            continue

    if strong_matches:
        return _dedupe_assets([strong_matches, contextual_matches, recommended_assets])[:preferred_limit]
    if contextual_matches:
        return _dedupe_assets([contextual_matches, recommended_assets])[:preferred_limit]
    return recommended_assets[:preferred_limit]


def sanitize_generated_section_content(*, content_md: str, section_title: str) -> str:
    lines = str(content_md or "").splitlines()
    cleaned: list[str] = []
    for line in lines:
        if any(pattern.match(line) for pattern in SECTION_OUTPUT_NOISE_PATTERNS):
            continue
        cleaned.append(line)

    text = "\n".join(cleaned).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return f"## {section_title}\n"
    if not text.lstrip().startswith("#"):
        return f"## {section_title}\n\n{text}\n"
    return text.rstrip() + "\n"


def _derive_project_draft_status(drafts: list[SectionDraft]) -> str:
    statuses = {str(getattr(draft, "status", "") or "").lower() for draft in drafts}
    if statuses & {"review_required", "rejected", "manual_required"}:
        return "REVIEW_REQUIRED"
    if statuses:
        return "DRAFT_READY"
    return "OUTLINE_APPROVED"


def should_use_extractive_reuse(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> bool:
    if str(section.get("generation_mode") or "baseline") != "reuse_first":
        return False
    reusable_blocks = reuse_pack.get("reusable_blocks") or []
    if not reusable_blocks:
        return False
    if len(reusable_blocks) >= 2:
        return True
    section_class = str(section.get("section_class") or "").lower()
    if section_class in EXTRACTIVE_SECTION_CLASSES:
        return True
    if bool(section.get("parameter_sensitive")) or bool(section.get("asset_required")):
        return True
    block_section_types = {
        str((block.get("metadata") or {}).get("section_type") or "").lower()
        for block in reusable_blocks
    }
    return bool(block_section_types & EXTRACTIVE_SECTION_TYPES)


def build_extractive_reuse_section_content(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    global_params: dict[str, Any],
) -> str:
    title = str(section.get("title") or "未命名章节").strip() or "未命名章节"
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    target_taxonomy = infer_target_taxonomy(section)
    candidate_blocks = _filter_reuse_blocks_for_assembly(
        reusable_blocks=list(reuse_pack.get("reusable_blocks") or []),
        target_taxonomy=target_taxonomy,
    )
    candidate_blocks = _order_assembly_blocks(candidate_blocks=candidate_blocks, target_taxonomy=target_taxonomy)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type in {"bom_or_supply_list", "supply_scope"}:
        return _build_supply_scope_reuse_section_content(
            title=title,
            candidate_blocks=candidate_blocks,
            reuse_pack=reuse_pack,
        )
    lines = [f"## {title}", ""]
    seen_paragraphs: set[str] = set()
    used_subheadings: set[str] = set()
    total_chars = 0

    for block in candidate_blocks:
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        if not body:
            continue
        paragraphs = _extract_reuse_paragraphs(body)
        ranked_paragraphs = sorted(
            paragraphs,
            key=lambda paragraph: _score_reuse_paragraph(
                paragraph=paragraph,
                query_terms=query_terms,
                target_taxonomy=target_taxonomy,
            ),
            reverse=True,
        )
        selected_paragraphs: list[str] = []
        for paragraph in ranked_paragraphs:
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if len(normalized) < 24 or normalized in seen_paragraphs:
                continue
            selected_paragraphs.append(_normalize_technical_spacing(paragraph.strip()))
            seen_paragraphs.add(normalized)
            total_chars += len(paragraph)
            max_paragraphs = 3 if len(candidate_blocks) <= 2 else 2
            if len(selected_paragraphs) >= max_paragraphs or total_chars >= 4200:
                break
        if not selected_paragraphs:
            continue
        subheading = _derive_reuse_subheading(block=block, section_title=title, target_taxonomy=target_taxonomy)
        if subheading and subheading not in used_subheadings:
            lines.append(f"### {subheading}")
            lines.append("")
            used_subheadings.add(subheading)
        lines.extend(selected_paragraphs)
        lines.append("")
        if total_chars >= 4200:
            break

    if len(lines) <= 2:
        fallback_text = str(section.get("purpose") or "请基于历史方案复用块补充本章节内容。").strip()
        lines.extend([fallback_text, ""])

    return "\n".join(lines).rstrip() + "\n"


def polish_extractive_reuse_section_content(*, section: dict[str, Any], content_md: str) -> str:
    section_title = str(section.get("title") or "未命名章节")
    polished = sanitize_generated_section_content(content_md=content_md, section_title=section_title)
    target_section_type = str(infer_target_taxonomy(section).get("section_type") or "unknown").lower()
    opening_sentence = EXTRACTIVE_SECTION_OPENINGS.get(target_section_type)
    if opening_sentence and opening_sentence not in polished:
        polished = re.sub(
            r"^(## [^\n]+\n\n)(?=(### |\||\[\[ASSET:|- \[\[ASSET:))",
            lambda match: f"{match.group(1)}{opening_sentence}\n\n",
            polished,
            count=1,
        )
    for label, lead_sentence in (EXTRACTIVE_TABLE_LEADS.get(target_section_type) or {}).items():
        if lead_sentence in polished:
            continue
        polished = re.sub(
            rf"(### {re.escape(label)}\n\n)(?=\|)",
            lambda match: f"{match.group(1)}{lead_sentence}\n\n",
            polished,
            count=1,
        )
    return polished.rstrip() + "\n"


def _build_supply_scope_reuse_section_content(
    *,
    title: str,
    candidate_blocks: list[dict[str, Any]],
    reuse_pack: dict[str, Any],
) -> str:
    lines = [f"## {title}", "", "### 主要设备及供货范围", ""]
    note = _extract_supply_scope_note(candidate_blocks)
    if note:
        lines.extend([note, ""])

    primary_table = _select_primary_supply_scope_table(candidate_blocks)
    if primary_table:
        lines.extend([primary_table, ""])

    content = "\n".join(lines).rstrip() + "\n"
    return ensure_required_asset_placeholders(content_md=content, reuse_pack=reuse_pack)


def _extract_supply_scope_note(candidate_blocks: list[dict[str, Any]]) -> str:
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        if str(metadata.get("content_form") or "").lower() != "narrative":
            continue
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        if not body:
            continue
        paragraphs = _extract_reuse_paragraphs(body)
        for paragraph in paragraphs:
            lowered = paragraph.casefold()
            if not any(token in lowered for token in ("供货", "scope", "备注", "remark", "乙方提供", "不在", "不含")):
                continue
            cleaned = _clean_supply_scope_note(paragraph)
            if cleaned:
                return cleaned
    return ""


def _clean_supply_scope_note(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            continue
        line = re.sub(r"^\*+|\*+$", "", line).strip()
        if not line or line.startswith("<!--"):
            continue
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", line))
        ascii_word_count = len(re.findall(r"[A-Za-z]{2,}", line))
        if cjk_count == 0 and ascii_word_count >= 3:
            continue
        line = _normalize_technical_spacing(line)
        if line not in cleaned:
            cleaned.append(line)
    return "\n".join(cleaned[:6]).strip()


def _select_primary_supply_scope_table(candidate_blocks: list[dict[str, Any]]) -> str:
    table_candidates: list[tuple[float, str]] = []
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        content_form = str(metadata.get("content_form") or "").lower()
        if content_form not in {"bom_table", "parameter_table"}:
            continue
        table_text = _normalize_markdown_table_text(str(block.get("content_md") or ""))
        if not table_text:
            continue
        score = float(block.get("selection_score") or 0)
        if content_form == "bom_table":
            score += 0.18
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", table_text))
        english_words = len(re.findall(r"[A-Za-z]{2,}", table_text))
        score += min(chinese_chars / 200.0, 0.3)
        score -= min(english_words / 120.0, 0.12)
        table_candidates.append((score, table_text))
    if not table_candidates:
        return ""
    table_candidates.sort(key=lambda item: item[0], reverse=True)
    return table_candidates[0][1]


def _normalize_markdown_table_text(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    table_lines: list[str] = []
    for line in lines:
        if "|" not in line:
            continue
        normalized = _normalize_technical_spacing(line)
        table_lines.append(normalized)
    if len(table_lines) < 2:
        return ""
    return "\n".join(table_lines)


def build_reuse_refinement_context(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    global_params: dict[str, Any],
) -> str:
    replace_fields = ", ".join(reuse_pack.get("must_replace_fields") or []) or "无"
    banned_terms = ", ".join(str(item) for item in (reuse_pack.get("banned_terms") or [])) or "无"
    placeholder_text = ", ".join(
        str(item.get("placeholder") or "")
        for item in (reuse_pack.get("required_asset_placeholders") or [])
        if str(item.get("placeholder") or "")
    ) or "无"
    key_params = ", ".join(
        f"{key}={value}"
        for key, value in global_params.items()
        if key in {"project_name", "product_line", "industry", "voltage_level", "power_rating", "quantity"}
        and value not in (None, "", [], {})
    ) or "无"
    return "\n".join(
        [
            f"章节标题: {section.get('title') or ''}",
            f"章节目的: {section.get('purpose') or ''}",
            f"章节类型: {section.get('section_class') or ''}",
            f"当前关键参数: {key_params}",
            f"必须替换字段: {replace_fields}",
            f"禁止沿用词: {banned_terms}",
            f"必须保留资产占位符: {placeholder_text}",
        ]
    )


def build_reuse_refinement_instruction(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> str:
    lines = [
        "请将给定章节草稿整理成客户可阅读的正式技术章节。",
        "优先保留现有技术细节、设备构成、接口逻辑和参数表达，不要压缩信息密度。",
        "保持现有章节标题、三级小标题、表格和列表结构，非必要不要改写结构。",
        "改写后正文长度原则上不低于原稿的 80%，不要把技术段压缩成一句结论。",
        "只做必要的统一、去重和替换，不要自由扩写背景，不要补充空泛套话。",
        "删除旧客户、旧项目和样板来源痕迹，严格使用当前项目字段。",
    ]
    if reuse_pack.get("required_asset_placeholders"):
        lines.append("若章节需要图表引用，请保留已有 [[ASSET:...]] 占位符，并放在合适位置。")
    if bool(section.get("parameter_sensitive")):
        lines.append("参数相关表述必须保持谨慎，不得虚构未确认参数。")
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))


def resolve_reuse_refinement_content(
    *,
    assembled_content: str,
    rewritten_content: str | None,
    section_title: str,
) -> tuple[str, str, str | None]:
    assembled = sanitize_generated_section_content(content_md=assembled_content, section_title=section_title)
    if not rewritten_content:
        return assembled, "fallback_assembled", "rewrite_missing"
    rewritten = sanitize_generated_section_content(content_md=rewritten_content, section_title=section_title)
    assembled_body = _strip_section_heading(assembled)
    rewritten_body = _strip_section_heading(rewritten)
    if not rewritten_body.strip():
        return assembled, "fallback_assembled", "rewrite_empty"
    if any(token in rewritten_body for token in REWRITE_LEAKAGE_TOKENS):
        return assembled, "fallback_assembled", "rewrite_leakage"
    if len(rewritten_body) < max(260, int(len(assembled_body) * 0.68)):
        return assembled, "fallback_assembled", "rewrite_too_thin"
    if _technical_density(rewritten_body) < (_technical_density(assembled_body) * 0.72):
        return assembled, "fallback_assembled", "rewrite_low_density"
    if rewritten_body.count("\n\n") + 1 < max(2, (assembled_body.count("\n\n") + 1) // 2):
        return assembled, "fallback_assembled", "rewrite_structure_collapse"
    return rewritten, "rewrite_applied", None


def select_preferred_reuse_content(
    *,
    assembled_content: str,
    rewritten_content: str | None,
    section_title: str,
) -> str:
    content, _, _ = resolve_reuse_refinement_content(
        assembled_content=assembled_content,
        rewritten_content=rewritten_content,
        section_title=section_title,
    )
    return content


def _strip_section_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _technical_density(text: str) -> float:
    body = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", text)
    tokens = TECHNICAL_TOKEN_PATTERN.findall(body)
    return len(tokens) / max(len(body), 1)


def _normalize_reuse_block_body(text: str) -> str:
    lines = str(text or "").splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if stripped.startswith("#"):
            continue
        if stripped.lower().startswith("来源:"):
            continue
        cleaned.append(line.rstrip())
    body = "\n".join(cleaned)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _extract_terminal_heading_label(heading_text: str) -> str:
    parts = [segment.strip() for segment in str(heading_text or "").split(">") if segment.strip()]
    label = parts[-1] if parts else str(heading_text or "").strip()
    label = re.sub(r"^[一二三四五六七八九十0-9.\-、\s]+", "", label).strip()
    return label


def _heading_should_be_excluded_from_customer_reuse(heading_text: str) -> bool:
    label = _extract_terminal_heading_label(heading_text)
    if not label:
        return False
    if any(pattern.match(label) for pattern in INTERNAL_REUSE_HEADING_PATTERNS):
        return True
    if LATIN_ENUM_REUSE_HEADING_PATTERN.match(label):
        return True
    if GENERIC_LABEL_REUSE_PATTERN.match(label):
        return True
    if label in GENERIC_REUSE_HEADINGS:
        return True
    return False


def _extract_reuse_paragraphs(text: str) -> list[str]:
    paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", text) if segment.strip()]
    if not paragraphs and text.strip():
        return [text.strip()]
    return paragraphs


def _filter_reuse_blocks_for_assembly(
    *,
    reusable_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not reusable_blocks:
        return []
    top_score = max(float(block.get("selection_score") or 0) for block in reusable_blocks)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    filtered: list[dict[str, Any]] = []
    for block in reusable_blocks:
        score = float(block.get("selection_score") or 0)
        metadata = block.get("metadata") or {}
        section_type = str(metadata.get("section_type") or "unknown").lower()
        content_form = str(metadata.get("content_form") or "narrative").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        if score < max(0.38, top_score * 0.5):
            continue
        if _heading_should_be_excluded_from_customer_reuse(heading_text):
            continue
        if heading_looks_like_document_title(heading_text) and score < top_score * 0.9:
            continue
        if target_section_type not in {"unknown", "overall_solution"}:
            if section_type == "unknown" and score < top_score * 0.7:
                continue
            if section_type not in {target_section_type, *related_section_types(target_section_type)} and score < top_score * 0.78:
                continue
        if target_section_type in {"communication_interface", "bom_or_supply_list", "supply_scope"} and content_form == "formula" and score < top_score * 0.92:
            continue
        if (
            target_section_type in {"bom_or_supply_list", "supply_scope"}
            and any(token in heading_text.casefold() for token in ("启动", "同步过程", "运行过程", "控制逻辑"))
            and score < top_score * 0.98
        ):
            continue
        if (
            target_section_type == "communication_interface"
            and any(token in heading_text for token in ("性能要求", "整体要求", "技术要求"))
            and score < top_score * 0.95
        ):
            continue
        filtered.append(block)
    filtered = filtered or reusable_blocks[:2]
    return _augment_with_support_blocks(filtered=filtered, reusable_blocks=reusable_blocks, target_taxonomy=target_taxonomy)


def _normalize_technical_spacing(text: str) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"(?<=\b[A-Z])\s+(?=[A-Z0-9]\b)", "", normalized)
    normalized = re.sub(r"(?<=\d)\s+(?=[A-Za-z])", "", normalized)
    normalized = re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "", normalized)
    for _ in range(4):
        normalized = re.sub(r"(?<![A-Za-z0-9])([A-Z])\s+([A-Z]\d*)(?![A-Za-z0-9])", r"\1\2", normalized)
        normalized = re.sub(r"(?<![A-Za-z0-9])([A-Z])\s+([A-Z]{2,})(?![A-Za-z0-9])", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z])\s+([A-Z](?:\d+)?)\b", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z]{2,})\s+([A-Z]{1,3})\b", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z]{1,4})\s+(\d{1,2})\b", r"\1\2", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.strip()


def _augment_with_support_blocks(
    *,
    filtered: list[dict[str, Any]],
    reusable_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not filtered:
        return []
    anchor = filtered[0]
    anchor_sample_id = str(anchor.get("sample_id") or anchor.get("source_doc_id") or "")
    try:
        anchor_chunk_index = int(anchor.get("chunk_index"))
    except (TypeError, ValueError):
        anchor_chunk_index = None
    related_types = {
        str(item).lower()
        for item in (
            {str(target_taxonomy.get("section_type") or "").lower()}
            | {str(item).lower() for item in related_section_types(target_taxonomy.get("section_type") or "")}
        )
        if item
    }
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic").lower()
    support_forms = {
        str(item).lower()
        for item in (
            target_taxonomy.get("support_content_forms")
            or support_content_forms(target_section_type)
        )
        if item
    }
    anchor_heading = " > ".join(str(item).strip() for item in (anchor.get("heading_path") or []) if str(item).strip())
    selected_signatures = {
        (str(item.get("sample_id") or item.get("source_doc_id") or ""), item.get("chunk_index"))
        for item in filtered
    }
    support_candidates: list[tuple[float, dict[str, Any]]] = []
    for block in reusable_blocks:
        signature = (str(block.get("sample_id") or block.get("source_doc_id") or ""), block.get("chunk_index"))
        if signature in selected_signatures:
            continue
        if anchor_sample_id and signature[0] != anchor_sample_id:
            continue
        metadata = block.get("metadata") or {}
        section_type = str(metadata.get("section_type") or "unknown").lower()
        equipment_type = str(metadata.get("equipment_type") or "generic").lower()
        content_form = str(metadata.get("content_form") or "narrative").lower()
        if content_form in {"formula", "page_furniture", "certificate"}:
            continue
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        if _heading_should_be_excluded_from_customer_reuse(heading_text):
            continue
        if heading_looks_like_document_title(heading_text):
            continue
        try:
            chunk_index = int(block.get("chunk_index"))
        except (TypeError, ValueError):
            chunk_index = None
        distance = abs(chunk_index - anchor_chunk_index) if chunk_index is not None and anchor_chunk_index is not None else 99
        if distance > 12:
            continue
        score = float(block.get("selection_score") or 0)
        bonus = 0.0
        if section_type in related_types:
            bonus += 0.18
        if target_section_type != "unknown" and section_type == target_section_type:
            bonus += 0.12
        if target_equipment_type != "generic" and equipment_type == target_equipment_type:
            bonus += 0.08
        elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
            bonus -= 0.12
        if support_forms and content_form in support_forms:
            bonus += 0.08
        if content_form in {"bom_table", "parameter_table"}:
            bonus += 0.12
        elif content_form == "narrative":
            bonus += 0.06
        if target_section_type in {"bom_or_supply_list", "supply_scope"}:
            if content_form == "narrative" and not any(
                token in heading_text.casefold()
                for token in ("供货", "范围", "清单", "设备", "备件", "spare", "scope", "remark", "备注")
            ):
                continue
        heading_bonus = heading_family_similarity(anchor_heading, heading_text)
        if heading_bonus:
            bonus += heading_bonus
        focus_adjustment, _ = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=heading_text,
        )
        bonus += focus_adjustment
        if distance <= 3:
            bonus += 0.1
        elif distance <= 6:
            bonus += 0.05
        support_score = score + bonus
        if support_score < 0.45:
            continue
        support_candidates.append((support_score, block))

    support_candidates.sort(key=lambda item: item[0], reverse=True)
    augmented = list(filtered)
    for _, block in support_candidates[:2]:
        augmented.append(block)
    return augmented


def _score_reuse_paragraph(
    *,
    paragraph: str,
    query_terms: list[str],
    target_taxonomy: dict[str, Any],
) -> float:
    terms = set(_tokenize_reuse_text(paragraph[:1200]))
    overlap = len(set(query_terms) & terms)
    score = overlap * 1.5
    text = paragraph.strip()
    score += min(len(text) / 900.0, 1.2)
    if "：" in text or ":" in text:
        score += 0.15
    if any(token in text for token in ("主回路", "接口", "联锁", "保护", "控制", "柜", "变频器", "电机")):
        score += 0.3
    preferred_forms = {
        str(item).lower()
        for item in (target_taxonomy.get("preferred_content_forms") or [])
        if item
    }
    if preferred_forms and "table" in preferred_forms and "|" in text:
        score += 0.35
    return score


def _derive_reuse_subheading(
    *,
    block: dict[str, Any],
    section_title: str,
    target_taxonomy: dict[str, Any] | None = None,
) -> str | None:
    heading_path = [str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()]
    metadata = block.get("metadata") or {}
    target_section_type = str((target_taxonomy or {}).get("section_type") or "").lower()
    template_label = _derive_template_subheading(
        target_section_type=target_section_type,
        metadata=metadata,
        heading_path=heading_path,
        section_title=section_title,
    )
    if template_label:
        return template_label
    if not heading_path:
        return None
    label = re.sub(r"^[一二三四五六七八九十0-9.\-、\s]+", "", heading_path[-1]).strip()
    if not label:
        return None
    if label in GENERIC_REUSE_HEADINGS:
        return None
    if label == section_title or label in section_title or section_title in label:
        return None
    return label


def _derive_template_subheading(
    *,
    target_section_type: str,
    metadata: dict[str, Any],
    heading_path: list[str],
    section_title: str,
) -> str | None:
    section_type = str(metadata.get("section_type") or "").lower()
    if target_section_type and section_type not in {"", "unknown", target_section_type} and section_type not in related_section_types(target_section_type):
        return None
    heading_text = " > ".join(heading_path)
    normalized_heading = heading_text.casefold()
    content_form = str(metadata.get("content_form") or "narrative").lower()
    template = SECTION_TEMPLATE_HEADINGS.get(target_section_type)
    if not template:
        return None
    for label, keywords in template.items():
        if any(keyword.casefold() in normalized_heading for keyword in keywords):
            if label == section_title:
                return None
            return label
    if content_form == "parameter_table":
        return "关键技术参数"
    if content_form == "bom_table" and target_section_type in {"main_circuit_scheme", "bom_or_supply_list", "supply_scope"}:
        return "设备选型与容量配置" if target_section_type == "main_circuit_scheme" else "主要设备及供货范围"
    if content_form == "interface_table" and target_section_type == "communication_interface":
        return "接口与信号清单"
    if target_section_type == "main_circuit_scheme":
        return "主回路结构与运行切换"
    if target_section_type == "communication_interface":
        return "通信架构与接口方式"
    return None


def _order_assembly_blocks(
    *,
    candidate_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidate_blocks:
        return []
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()

    def _rank(block: dict[str, Any]) -> tuple[int, float]:
        metadata = block.get("metadata") or {}
        content_form = str(metadata.get("content_form") or "narrative").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()).casefold()
        selection_score = float(block.get("selection_score") or 0)
        priority = 5
        if target_section_type == "main_circuit_scheme":
            if any(token in heading_text for token in ("主回路", "主接线", "一次接线", "旁路", "切换", "隔离")):
                priority = 0
            elif any(token in heading_text for token in ("选型", "配置", "容量", "器件")) or content_form == "bom_table":
                priority = 1
            elif any(token in heading_text for token in ("参数", "技术数据", "规格", "额定")) or content_form == "parameter_table":
                priority = 2
            elif any(token in heading_text for token in ("保护", "联锁", "闭锁", "报警")):
                priority = 3
        elif target_section_type == "communication_interface":
            if any(token in heading_text for token in ("通信", "通讯", "接口", "上位机", "dcs", "plc", "modbus", "profibus", "profinet", "rs485")):
                priority = 0
            elif content_form == "interface_table" or any(token in heading_text for token in ("点表", "信号", "ai", "ao", "di", "do")):
                priority = 1
            elif any(token in heading_text for token in ("联锁", "调试", "试验")):
                priority = 2
            elif any(token in heading_text for token in ("性能要求", "整体要求", "技术要求")):
                priority = 4
        elif target_section_type in {"bom_or_supply_list", "supply_scope"}:
            if content_form == "bom_table" or any(token in heading_text for token in ("清单", "供货", "设备")):
                priority = 0
            elif content_form == "parameter_table" or any(token in heading_text for token in ("参数", "规格", "配置")):
                priority = 1
        return (priority, -selection_score)

    return sorted(candidate_blocks, key=_rank)


def _append_unique_text(target: list[str], value: str) -> None:
    normalized = str(value or "").strip()
    if normalized and normalized not in target:
        target.append(normalized)


def _section_asset_signal_text(section: dict[str, Any]) -> str:
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or section.get("description") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def _effective_section_evidence_types(section: dict[str, Any]) -> list[str]:
    raw_types = [str(item).strip().lower() for item in (section.get("expected_evidence_types") or []) if str(item).strip()]
    effective: list[str] = []
    for item in raw_types:
        _append_unique_text(effective, item)

    text = _section_asset_signal_text(section).casefold()
    target_taxonomy = infer_target_taxonomy(section)
    support_forms = {
        str(item).lower()
        for item in (target_taxonomy.get("support_content_forms") or [])
        if str(item).strip()
    }
    explicit_figure = bool({"figure", "diagram"} & set(raw_types))
    table_hint = any(token in text for token in TABLE_ASSET_HINTS)
    figure_hint = any(token in text for token in FIGURE_ASSET_HINTS)
    formula_hint = any(token in text for token in FORMULA_ASSET_HINTS)
    prefer_table_only = table_hint and not figure_hint and not explicit_figure

    if {"table", "parameter"} & set(raw_types) or table_hint or support_forms & {"parameter_table", "bom_table", "interface_table", "protection_table"}:
        _append_unique_text(effective, "table")
        _append_unique_text(effective, "parameter")
    if explicit_figure or figure_hint or ("figure" in support_forms and not prefer_table_only):
        _append_unique_text(effective, "figure")
    if {"formula", "equation"} & set(raw_types) or formula_hint:
        _append_unique_text(effective, "formula")
    return effective


def _derive_section_asset_query_hints(section: dict[str, Any]) -> list[str]:
    effective_types = _effective_section_evidence_types(section)
    inferred_section = {**section, "expected_evidence_types": effective_types}
    taxonomy_section = str(infer_target_taxonomy(inferred_section).get("section_type") or "unknown").lower()
    hints: list[str] = []
    for item in SECTION_ASSET_QUERY_HINTS.get(taxonomy_section, ()):
        _append_unique_text(hints, item)
    if "figure" in effective_types:
        _append_unique_text(hints, "工程示意图")
    if "table" in effective_types or "parameter" in effective_types:
        _append_unique_text(hints, "技术参数表")
    return hints[:6]


def _build_asset_search_context(
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reusable_blocks = list(reusable_blocks or [])
    effective_types = _effective_section_evidence_types(section)
    anchor_document_names: list[str] = []
    anchor_heading_paths: list[str] = []
    for block in reusable_blocks[:3]:
        source_title = str(block.get("source_title") or "").strip()
        if source_title and source_title not in anchor_document_names:
            anchor_document_names.append(source_title)
        heading_text = " > ".join(
            str(item).strip()
            for item in (block.get("heading_path") or [])
            if str(item).strip()
        )
        if heading_text and heading_text not in anchor_heading_paths:
            anchor_heading_paths.append(heading_text)
    keywords: list[str] = []
    for item in section.get("keywords") or []:
        _append_unique_text(keywords, str(item))
    for item in _derive_section_asset_query_hints(section):
        _append_unique_text(keywords, item)
    return {
        "section_title": str(section.get("title") or ""),
        "purpose": str(section.get("purpose") or section.get("description") or ""),
        "section_class": str(section.get("section_class") or ""),
        "expected_evidence_types": effective_types,
        "keywords": keywords,
        "anchor_document_names": anchor_document_names,
        "anchor_heading_paths": anchor_heading_paths,
    }


def section_outline_to_executor_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": section.get("title"),
        "description": section.get("purpose", ""),
        "keywords": [section.get("title", ""), *[str(item) for item in section.get("expected_evidence_types") or []]],
        "section_class": section.get("section_class"),
        "reuse_level": section.get("reuse_level"),
        "generation_mode": section.get("generation_mode"),
        "asset_required": bool(section.get("asset_required")),
    }


def build_section_global_params(requirement_content: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(requirement_content, dict):
        return {}

    merged: dict[str, Any] = {}
    key_parameters = requirement_content.get("key_parameters")
    if isinstance(key_parameters, dict):
        merged.update(key_parameters)

    for field_name in ("project_name", "industry", "product_line", "business_objective"):
        value = requirement_content.get(field_name)
        if value not in (None, "", [], {}):
            merged.setdefault(field_name, value)
    return merged


def build_section_asset_query(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    effective_types = _effective_section_evidence_types(section)
    query_hints = _derive_section_asset_query_hints(section)
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(effective_types),
        " ".join(query_hints),
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_reuse_query_intents(*, section: dict[str, Any], global_params: dict[str, Any]) -> dict[str, Any]:
    title_parts = [
        str(section.get("title") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
    ]
    detail_parts = [
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or "").strip(),
    ]
    context_parts = [
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    title_text = " ".join(part for part in title_parts if part).strip()
    detail_text = " ".join(part for part in detail_parts if part).strip()
    context_text = " ".join(part for part in context_parts if part).strip()
    return {
        "title_text": title_text,
        "detail_text": detail_text,
        "context_text": context_text,
        "title_terms": _tokenize_reuse_text(title_text),
        "detail_terms": _tokenize_reuse_text(detail_text),
        "context_terms": _tokenize_reuse_text(context_text),
    }


def build_section_reuse_query(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    intents = build_section_reuse_query_intents(section=section, global_params=global_params)
    parts = [
        intents["title_text"],
        intents["detail_text"],
        intents["context_text"],
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_asset_types(section: dict[str, Any]) -> list[str] | None:
    expected_types = set(_effective_section_evidence_types(section))
    asset_types: list[str] = []
    if {"table", "parameter"} & expected_types:
        asset_types.append("table")
    if {"figure", "diagram"} & expected_types:
        asset_types.append("figure")
    if {"formula", "equation"} & expected_types:
        asset_types.append("formula_candidate")
    return asset_types or None


def _select_evidence_items(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any] | None = None,
    limit: int,
    preferred_evidence_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    expected_types = {str(item) for item in (section.get("expected_evidence_types") or [])}
    results = (evidence_bundle.content or {}).get("results") or []
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params or {})
    target_taxonomy = infer_target_taxonomy(section)

    candidates: list[tuple[float, dict[str, Any]]] = []
    fallback_candidates: list[tuple[float, dict[str, Any]]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        score, _ = _score_reuse_candidate(
            section=section,
            item=result,
            raw_content=str(result.get("raw_content") or result.get("summary") or ""),
            heading_path=result.get("heading_path") or [],
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
        )
        result_type = str(result.get("type") or "")
        target_pool = candidates if (not expected_types or result_type in expected_types) else fallback_candidates
        target_pool.append((score, result))

    ranked = candidates or fallback_candidates
    ranked.sort(
        key=lambda item: (
            float(item[0]),
            float(item[1].get("reusability_score") or item[1].get("relevance_score") or 0),
        ),
        reverse=True,
    )
    prioritized = [item for _, item in ranked]
    if preferred_evidence_ids:
        prioritized.sort(
            key=lambda item: (
                not _matches_preferred_citation(item, preferred_evidence_ids),
                -float(item.get("reusability_score") or item.get("relevance_score") or 0),
            )
        )
    return prioritized[:limit]


def _build_citation_excerpt(text: str, *, limit: int = 220) -> str:
    normalized = _normalize_reuse_block_body(text)
    normalized = re.sub(r"<!--.*?-->", " ", normalized, flags=re.DOTALL)
    normalized = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _normalize_preferred_citation_ids(preferred_citation_ids: list[str] | None) -> set[str]:
    return {
        str(item).strip()
        for item in (preferred_citation_ids or [])
        if str(item).strip()
    }


def _citation_identity_values(item: dict[str, Any]) -> set[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    values = {
        str(item.get("evidence_id") or "").strip(),
        str(item.get("block_id") or "").strip(),
        str(item.get("source_doc_id") or "").strip(),
        str(item.get("sample_id") or "").strip(),
        str(item.get("source_title") or "").strip(),
        str(metadata.get("sample_id") or "").strip(),
        str(metadata.get("document_name") or "").strip(),
    }
    return {value for value in values if value}


def _matches_preferred_citation(item: dict[str, Any], preferred_citation_ids: set[str]) -> bool:
    if not preferred_citation_ids:
        return False
    return bool(_citation_identity_values(item) & preferred_citation_ids)


def prioritize_reusable_blocks_for_citations(
    reusable_blocks: list[dict[str, Any]],
    *,
    preferred_citation_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    preferred_ids = _normalize_preferred_citation_ids(preferred_citation_ids)
    if not reusable_blocks or not preferred_ids:
        return reusable_blocks

    primary: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    remainder: list[dict[str, Any]] = []
    anchor_sources: set[str] = set()

    for block in reusable_blocks:
        if _matches_preferred_citation(block, preferred_ids):
            primary.append(block)
            anchor_sources.update(_citation_identity_values(block))

    if not primary:
        return reusable_blocks

    primary_ids = {id(block) for block in primary}
    for block in reusable_blocks:
        if id(block) in primary_ids:
            continue
        if _citation_identity_values(block) & anchor_sources:
            secondary.append(block)
        else:
            remainder.append(block)
    return [*primary, *secondary, *remainder]


def _collect_replace_fields(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    lowered = raw_content.lower()
    replace_fields = [field for field in STANDARD_REPLACE_FIELDS if field in global_params and global_params.get(field)]
    if any(token in raw_content for token in ["买方", "卖方", "客户", "项目名称"]):
        replace_fields.extend(["customer_name", "buyer_name", "seller_name", "project_name"])
    if any(token in raw_content for token in ["110kV", "35kV", "6kV", "10kV", "电压"]):
        replace_fields.append("voltage_level")
    if any(token in lowered for token in ["kw", "mw", "mva", "数量", "台"]):
        replace_fields.extend(["power_rating", "quantity"])
    deduped: list[str] = []
    for field in replace_fields:
        if field not in deduped:
            deduped.append(field)
    return deduped


def _extract_banned_terms(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    current_project_name = str(global_params.get("project_name") or "").strip()
    terms: list[str] = []
    for pattern in BANNED_TERM_PATTERNS:
        for match in pattern.finditer(raw_content):
            candidate = re.sub(r"\s+", " ", str(match.group(1)).strip())
            candidate = candidate.strip(" :：;；,.，。()（）[]【】")
            if len(candidate) < 3:
                continue
            if current_project_name and candidate == current_project_name:
                continue
            if candidate not in terms:
                terms.append(candidate)
    return terms


def _build_required_asset_placeholders(recommended_assets: list[dict[str, Any]], *, asset_required: bool) -> list[dict[str, Any]]:
    if not asset_required:
        return []
    placeholders: list[dict[str, Any]] = []
    for asset in recommended_assets[:3]:
        asset_type = str(asset.get("asset_type") or "").lower()
        if asset_type not in {"figure", "table", "formula_candidate"}:
            continue
        asset_id = asset.get("asset_id")
        if not asset_id:
            continue
        normalized_type = "FORMULA" if asset_type == "formula_candidate" else asset_type.upper()
        placeholders.append(
            {
                "placeholder": f"[[ASSET:{normalized_type}:{asset_id}]]",
                "title": asset.get("display_title") or asset.get("title") or asset.get("caption") or "参考资产",
                "asset_type": asset_type,
            }
        )
    return placeholders


def build_reusable_blocks(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    case_library_matches: list[dict[str, Any]] | None = None,
    limit: int = DEFAULT_REUSE_LIMIT,
) -> list[dict[str, Any]]:
    blocks = _build_evidence_reusable_blocks(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=limit,
    )
    blocks.extend(
        _build_case_library_reusable_blocks(
            section=section,
            global_params=global_params,
            case_library_matches=case_library_matches or [],
            limit=limit,
        )
    )
    deduped = _dedupe_reusable_blocks(blocks)
    deduped.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    return deduped[:limit]


def _build_evidence_reusable_blocks(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    target_taxonomy = infer_target_taxonomy(section)
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=max(limit * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
    )
    blocks: list[dict[str, Any]] = []
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    for item in selected:
        raw_content = str(item.get("raw_content") or item.get("summary") or "").strip()
        if not raw_content:
            continue
        heading_path = item.get("heading_path") or []
        block_type = str(item.get("source_chunk_type") or item.get("type") or "section").lower()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        selection_score, selection_reasons = _score_reuse_candidate(
            section=section,
            item=item,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
        )
        blocks.append(
            {
                "block_id": str(item.get("evidence_id") or item.get("source_chunk_id") or ""),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "source_section_id": item.get("source_section_id") or metadata.get("source_section_id"),
                "section_path": " > ".join(str(segment) for segment in heading_path if segment) if heading_path else "",
                "source_heading": heading_path[-1] if heading_path else "",
                "heading_path": heading_path,
                "content_md": raw_content,
                "block_type": block_type,
                "reusability_score": float(item.get("reusability_score") or item.get("relevance_score") or 0),
                "selection_score": selection_score,
                "selection_reasons": selection_reasons,
                "customer_specificity_score": 0.8 if metadata.get("front_matter") else 0.25,
                "asset_dependency_level": "high" if metadata.get("needs_asset_lookup") else "low",
                "must_replace_fields": _collect_replace_fields(raw_content=raw_content, global_params=global_params),
                "banned_terms": _extract_banned_terms(raw_content=raw_content, global_params=global_params),
                "must_not_copy_spans": [],
                "metadata": metadata,
            }
        )
    blocks.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    return blocks[:limit]


def _build_case_library_reusable_blocks(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    case_library_matches: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    target_taxonomy = infer_target_taxonomy(section)
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    blocks: list[dict[str, Any]] = []
    for item in case_library_matches[: max(limit * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES)]:
        raw_content = str(item.get("content") or "").strip()
        if not raw_content:
            continue
        heading_path = _normalize_case_heading_path(item.get("heading_path"))
        metadata = {
            "front_matter": bool(item.get("front_matter")),
            "needs_asset_lookup": bool(item.get("needs_asset_lookup")),
            "section_type": item.get("section_type") or "unknown",
            "equipment_type": item.get("equipment_type") or "generic",
            "content_form": item.get("content_form") or "narrative",
        }
        score_input = {
            "type": str(item.get("chunk_type") or "section").lower(),
            "source_chunk_type": item.get("chunk_type"),
            "reusability_score": float(item.get("score") or 0),
            "metadata": metadata,
            "section_type": metadata["section_type"],
            "equipment_type": metadata["equipment_type"],
            "content_form": metadata["content_form"],
        }
        selection_score, selection_reasons = _score_reuse_candidate(
            section=section,
            item=score_input,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
        )
        blocks.append(
            {
                "block_id": f"case:{item.get('sample_id')}:{item.get('chunk_index')}",
                "sample_id": item.get("sample_id"),
                "chunk_index": item.get("chunk_index"),
                "source_doc_id": item.get("sample_id"),
                "source_title": item.get("file_name"),
                "source_section_id": item.get("source_section_id"),
                "section_path": item.get("section_path") or " > ".join(heading_path),
                "source_heading": item.get("source_heading") or (heading_path[-1] if heading_path else ""),
                "normalized_heading": item.get("normalized_heading"),
                "heading_path": heading_path,
                "content_md": raw_content,
                "block_type": str(item.get("chunk_type") or "section").lower(),
                "reusability_score": float(item.get("score") or 0),
                "selection_score": selection_score,
                "selection_reasons": selection_reasons,
                "customer_specificity_score": 0.8 if metadata.get("front_matter") else 0.25,
                "asset_dependency_level": "high" if metadata.get("needs_asset_lookup") else "low",
                "must_replace_fields": _collect_replace_fields(raw_content=raw_content, global_params=global_params),
                "banned_terms": _extract_banned_terms(raw_content=raw_content, global_params=global_params),
                "must_not_copy_spans": [],
                "metadata": metadata,
            }
        )
    return blocks


def _normalize_case_heading_path(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [segment.strip() for segment in str(value or "").split(">") if segment.strip()]


def _reuse_block_signature(block: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    return (
        str(block.get("source_title") or ""),
        tuple(str(item) for item in (block.get("heading_path") or [])),
        str(block.get("content_md") or "")[:160],
    )


def _dedupe_reusable_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
    for block in blocks:
        signature = _reuse_block_signature(block)
        current = deduped.get(signature)
        if current is None or float(block.get("selection_score") or 0) > float(current.get("selection_score") or 0):
            deduped[signature] = block
    return list(deduped.values())


def build_reuse_pack(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    retrieval_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    risk_flags: list[str] = []
    if bool(section.get("parameter_sensitive")):
        risk_flags.append("parameter_sensitive")
    if bool(section.get("asset_required")):
        risk_flags.append("asset_required")
    if bool(section.get("needs_human_review")):
        risk_flags.append("human_review_required")
    must_replace_fields: list[str] = []
    banned_terms: list[str] = []
    for block in reusable_blocks:
        for field_name in block.get("must_replace_fields") or []:
            if field_name not in must_replace_fields:
                must_replace_fields.append(field_name)
        for term in block.get("banned_terms") or []:
            if term not in banned_terms:
                banned_terms.append(term)
    required_asset_placeholders = _build_required_asset_placeholders(
        recommended_assets,
        asset_required=bool(section.get("asset_required")),
    )

    return {
        "section_title": str(section.get("title") or ""),
        "section_purpose": str(section.get("purpose") or ""),
        "generation_mode": str(section.get("generation_mode") or "baseline"),
        "reuse_level": str(section.get("reuse_level") or "medium"),
        "reusable_blocks": reusable_blocks,
        "recommended_assets": recommended_assets,
        "must_replace_fields": must_replace_fields,
        "banned_terms": banned_terms,
        "replacement_hints": {
            field_name: global_params.get(field_name)
            for field_name in must_replace_fields
            if global_params.get(field_name) not in (None, "", [], {})
        },
        "required_asset_placeholders": required_asset_placeholders,
        "parameter_candidates": {
            key: value
            for key, value in global_params.items()
            if key in {"project_name", "product_line", "industry", "business_objective", "voltage_level", "power_rating", "quantity"}
        },
        "do_not_reuse_signals": [
            "customer_specific_fields",
            "outdated_schedule",
            "unconfirmed_parameters",
        ],
        "risk_flags": risk_flags,
        "retrieval_trace": retrieval_trace or {},
    }


def render_reuse_pack_context(reuse_pack: dict[str, Any]) -> str:
    blocks = reuse_pack.get("reusable_blocks") or []
    if not blocks:
        return ""

    sections: list[str] = []
    for index, block in enumerate(blocks, start=1):
        heading_path = block.get("heading_path") or []
        path_text = " > ".join(str(item) for item in heading_path if item)
        replace_fields = ", ".join(block.get("must_replace_fields") or []) or "无"
        sections.append(
            "\n".join(
                [
                    f"[复用块 {index}]",
                    f"来源: {block.get('source_title') or '未知来源'}",
                    f"位置: {path_text or '未标注章节'}",
                    f"类型: {block.get('block_type')}",
                    f"选择评分: {block.get('selection_score')}",
                    f"复用评分: {block.get('reusability_score')}",
                    f"必须替换字段: {replace_fields}",
                    "正文:",
                    str(block.get("content_md") or ""),
                ]
            )
        )
    return "\n\n".join(sections)


def build_manual_only_section_content(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> str:
    title = str(section.get("title") or "未命名章节")
    lines = [
        f"## {title}",
        "",
        "> 本章节已标记为人工编写，系统暂不自动生成最终客户正文。",
        "",
    ]
    blocks = reuse_pack.get("reusable_blocks") or []
    if blocks:
        lines.extend(["### 可参考复用材料", ""])
        for block in blocks[:3]:
            heading_path = " > ".join(str(item) for item in (block.get("heading_path") or []) if item)
            lines.append(
                f"- {block.get('source_title') or '未知来源'}"
                f"{f' / {heading_path}' if heading_path else ''}"
                f" / 复用评分 {block.get('reusability_score')}"
            )
        lines.append("")
    assets = reuse_pack.get("recommended_assets") or []
    if assets:
        lines.extend(["### 建议插入资产", ""])
        placeholders = reuse_pack.get("required_asset_placeholders") or []
        if placeholders:
            for item in placeholders[:3]:
                lines.append(f"- {item.get('placeholder')} {item.get('title')}")
        else:
            for asset in assets[:3]:
                asset_type = str(asset.get("asset_type") or "asset").upper()
                asset_id = asset.get("asset_id")
                title_text = asset.get("display_title") or asset.get("title") or asset.get("caption") or "参考资产"
                lines.append(f"- [[ASSET:{asset_type}:{asset_id}]] {title_text}")
        lines.append("")
    lines.extend(
        [
            "### 编写提示",
            "",
            "- 优先基于上述复用材料和资产进行人工整理。",
            "- 涉及客户信息、参数和供货边界时，必须按当前项目重新确认。",
        ]
    )
    return "\n".join(lines)


def ensure_required_asset_placeholders(*, content_md: str, reuse_pack: dict[str, Any]) -> str:
    placeholders = reuse_pack.get("required_asset_placeholders") or []
    if not placeholders:
        return content_md
    missing = [
        item
        for item in placeholders
        if str(item.get("placeholder") or "") and str(item.get("placeholder")) not in content_md
    ]
    if not missing:
        return content_md

    content_with_inline_assets = _inline_missing_asset_placeholders(
        content_md=content_md,
        missing_placeholders=missing,
        reuse_pack=reuse_pack,
    )
    remaining = [
        item
        for item in placeholders
        if str(item.get("placeholder") or "") and str(item.get("placeholder")) not in content_with_inline_assets
    ]
    if not remaining:
        return content_with_inline_assets

    appendix_lines = ["", "### 相关图表", ""]
    for item in remaining:
        appendix_lines.append(f"- {item.get('placeholder')} {item.get('title') or '参考资产'}")
    return content_with_inline_assets.rstrip() + "\n" + "\n".join(appendix_lines).rstrip() + "\n"


def _inline_missing_asset_placeholders(
    *,
    content_md: str,
    missing_placeholders: list[dict[str, Any]],
    reuse_pack: dict[str, Any],
) -> str:
    lines = content_md.rstrip().splitlines()
    if not lines:
        return content_md
    asset_lookup = _build_asset_lookup(reuse_pack.get("recommended_assets") or [])
    for item in missing_placeholders:
        placeholder = str(item.get("placeholder") or "").strip()
        if not placeholder or placeholder in "\n".join(lines):
            continue
        asset_id = _extract_asset_id_from_placeholder(placeholder)
        asset = asset_lookup.get(asset_id)
        heading_index = _find_best_asset_anchor_heading(lines=lines, asset=item if asset is None else asset)
        if heading_index is None:
            continue
        insert_at = _find_asset_insertion_index(lines=lines, heading_index=heading_index)
        snippet = [placeholder, ""]
        lines[insert_at:insert_at] = snippet
    return "\n".join(lines).rstrip() + "\n"


def _build_asset_lookup(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "").strip()
        if asset_id:
            lookup[asset_id] = asset
    return lookup


def _extract_asset_id_from_placeholder(placeholder: str) -> str:
    match = ASSET_PLACEHOLDER_PATTERN.search(str(placeholder or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _find_best_asset_anchor_heading(*, lines: list[str], asset: dict[str, Any]) -> int | None:
    headings = [
        (index, line.strip()[4:].strip())
        for index, line in enumerate(lines)
        if line.strip().startswith("### ")
    ]
    if not headings:
        return None
    anchor_texts = _collect_asset_anchor_texts(asset)
    best_index: int | None = None
    best_score = 0.0
    for heading_index, heading_text in headings:
        score = _score_asset_heading_match(heading_text=heading_text, anchor_texts=anchor_texts)
        if score > best_score:
            best_score = score
            best_index = heading_index
    if best_score < 0.62:
        return None
    return best_index


def _collect_asset_anchor_texts(asset: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    semantic_summary = metadata.get("semantic_summary") if isinstance(metadata.get("semantic_summary"), dict) else {}
    for value in (
        asset.get("display_title"),
        asset.get("title"),
        asset.get("heading_path"),
        metadata.get("display_title"),
        metadata.get("raw_title"),
        metadata.get("heading_path"),
        semantic_summary.get("title_hint"),
    ):
        if isinstance(value, str) and value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    applicable_sections = semantic_summary.get("applicable_sections") if isinstance(semantic_summary.get("applicable_sections"), list) else []
    for value in applicable_sections:
        if isinstance(value, str) and value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    return texts


def _score_asset_heading_match(*, heading_text: str, anchor_texts: list[str]) -> float:
    heading_norm = _normalize_asset_anchor_text(heading_text)
    if not heading_norm:
        return 0.0
    best = 0.0
    for anchor_text in anchor_texts:
        anchor_norm = _normalize_asset_anchor_text(anchor_text)
        if not anchor_norm:
            continue
        score = SequenceMatcher(None, heading_norm, anchor_norm).ratio()
        if heading_norm == anchor_norm:
            score += 0.8
        elif heading_norm in anchor_norm or anchor_norm in heading_norm:
            score += 0.45
        best = max(best, score)
    return best


def _normalize_asset_anchor_text(text: str) -> str:
    normalized = _extract_terminal_heading_label(text)
    normalized = normalized.casefold()
    normalized = re.sub(r"[()（）【】\[\]《》·:：,，/\\\-\s]+", "", normalized)
    for token in ("系统方案", "方案", "系统图", "总图", "示意图", "框图", "原理图", "图"):
        normalized = normalized.replace(token, "")
    return normalized.strip()


def _find_asset_insertion_index(*, lines: list[str], heading_index: int) -> int:
    cursor = heading_index + 1
    seen_body = False
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if cursor > heading_index + 1 and stripped.startswith("### "):
            return cursor
        if stripped.startswith("[[ASSET:"):
            cursor += 1
            continue
        if stripped:
            seen_body = True
        elif seen_body:
            return cursor + 1
        cursor += 1
    return len(lines)


def _build_reuse_query_terms(*, section: dict[str, Any], global_params: dict[str, Any]) -> list[str]:
    intents = build_section_reuse_query_intents(section=section, global_params=global_params)
    tokens: list[str] = []
    weighted_parts = [
        intents["title_text"],
        intents["title_text"],
        intents["detail_text"],
        intents["context_text"],
    ]
    for part in weighted_parts:
        for token in _tokenize_reuse_text(part):
            if token not in tokens:
                tokens.append(token)
    for hint in extract_taxonomy_hints(*weighted_parts):
        normalized = hint.casefold()
        if normalized not in tokens:
            tokens.append(normalized)
    return tokens


def _tokenize_reuse_text(text: str) -> list[str]:
    tokens: list[str] = []
    for match in REUSE_TOKEN_PATTERN.findall(str(text or "")):
        token = match.strip().lower()
        if len(token) < 2 or token in REUSE_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _score_reuse_candidate(
    *,
    section: dict[str, Any],
    item: dict[str, Any],
    raw_content: str,
    heading_path: list[Any],
    query_terms: list[str],
    target_taxonomy: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    base_score = float(item.get("reusability_score") or item.get("relevance_score") or 0)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    result_type = str(item.get("type") or item.get("source_chunk_type") or "").lower()
    heading_text = " ".join(str(segment) for segment in heading_path if segment)
    heading_terms = set(_tokenize_reuse_text(heading_text))
    content_terms = set(_tokenize_reuse_text(raw_content[:1200]))
    query_set = set(query_terms)
    overlap_count = len(query_set & (heading_terms | content_terms))
    overlap_ratio = (overlap_count / len(query_set)) if query_set else 0.0
    score = base_score
    reasons: list[str] = []

    if overlap_ratio:
        bonus = min(0.22, overlap_ratio * 0.22)
        score += bonus
        reasons.append(f"keyword_overlap={overlap_count}")
    elif query_set:
        score -= 0.12
        reasons.append("keyword_mismatch_penalty")
    if query_set and heading_terms and (query_set & heading_terms):
        score += 0.12
        reasons.append("heading_match")

    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    if result_type and result_type in expected_types:
        score += 0.06
        reasons.append("expected_type")

    section_class = str(section.get("section_class") or "").lower()
    if section_class and any(section_class in token for token in heading_terms | content_terms):
        score += 0.08
        reasons.append("section_class_match")

    if metadata.get("front_matter"):
        score -= 0.18
        reasons.append("front_matter_penalty")
    content_form = str(
        item.get("content_form")
        or metadata.get("content_form")
        or "narrative"
    ).lower()
    section_type = str(
        item.get("section_type")
        or metadata.get("section_type")
        or "unknown"
    ).lower()
    equipment_type = str(
        item.get("equipment_type")
        or metadata.get("equipment_type")
        or "generic"
    ).lower()
    if content_form == "page_furniture":
        score -= 0.25
        reasons.append("page_furniture_penalty")
    if metadata.get("needs_asset_lookup") and not bool(section.get("asset_required")):
        score -= 0.08
        reasons.append("asset_dependency_penalty")

    customer_specificity = str(section.get("customer_specificity") or "medium").lower()
    if customer_specificity in {"medium", "high"} and _looks_customer_specific(raw_content):
        penalty = 0.08 if customer_specificity == "medium" else 0.12
        score -= penalty
        reasons.append("customer_specific_penalty")

    if bool(section.get("parameter_sensitive")) and result_type not in {"parameter", "table"}:
        score -= 0.04
        reasons.append("parameter_type_penalty")
    if bool(section.get("parameter_sensitive")) and not content_form_is_table(content_form):
        score -= 0.06
        reasons.append("parameter_content_form_penalty")

    if target_taxonomy:
        target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
        target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic").lower()
        preferred_content_forms = {
            str(item).lower()
            for item in (target_taxonomy.get("preferred_content_forms") or [])
            if item
        }
        support_forms = {
            str(item).lower()
            for item in (
                target_taxonomy.get("support_content_forms")
                or support_content_forms(target_section_type)
            )
            if item
        }
        if target_section_type != "company_profile" and section_type == "company_profile":
            score -= 0.18
            reasons.append("company_profile_penalty")
        if target_section_type != "unknown" and section_type == target_section_type:
            score += 0.2
            reasons.append("section_type_match")
        elif section_type in related_section_types(target_section_type):
            score += 0.1
            reasons.append("related_section_type_match")
        elif target_section_type not in {"unknown", "overall_solution"}:
            if section_type == "unknown":
                score -= 0.05
                reasons.append("unknown_section_type_penalty")
            else:
                score -= 0.12
                reasons.append("section_type_mismatch_penalty")
        if target_equipment_type != "generic" and equipment_type == target_equipment_type:
            score += 0.1
            reasons.append("equipment_type_match")
        elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
            score -= 0.12
            reasons.append("equipment_type_mismatch_penalty")
        if preferred_content_forms and content_form in preferred_content_forms:
            score += 0.08
            reasons.append("content_form_match")
        elif support_forms and content_form in support_forms:
            score += 0.04
            reasons.append("support_content_form_match")
        elif preferred_content_forms and content_form in {"formula", "certificate"}:
            score -= 0.12
            reasons.append("content_form_mismatch_penalty")
        heading_adjustment, heading_reasons = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=" > ".join(str(item).strip() for item in heading_path if str(item).strip()),
        )
        score += heading_adjustment
        reasons.extend(heading_reasons)

    normalized_score = round(min(max(score, 0.0), 1.2), 4)
    return normalized_score, reasons


def _looks_customer_specific(content: str) -> bool:
    text = str(content or "")
    return any(token in text for token in ("买方", "卖方", "客户", "项目名称", "用户"))


def _select_section_scope_candidates(section_candidates: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    if not section_candidates:
        return []
    candidates = [item for item in section_candidates if str(item.get("section_path") or item.get("heading_path") or "").strip()]
    if not candidates:
        return []
    selected: list[dict[str, Any]] = []
    selected_paths: list[str] = []

    anchor_candidates = [
        item
        for item in candidates
        if int(item.get("level") or 1) <= 2
        and any(
            token in str(item.get("reason") or "")
            for token in (
                "normalized_section_title_match",
                "section_path_title_match",
                "heading_alias_match",
                "section_title_match",
            )
        )
    ]
    if anchor_candidates:
        anchor = sorted(
            anchor_candidates,
            key=lambda item: (
                float(item.get("score") or 0),
                -int(item.get("level") or 0),
                len(str(item.get("section_path") or item.get("heading_path") or "")),
            ),
            reverse=True,
        )[0]
        path = str(anchor.get("section_path") or anchor.get("heading_path") or "").strip()
        if path:
            selected.append(anchor)
            selected_paths.append(path)

    specific_candidates = [item for item in candidates if int(item.get("level") or 1) >= 2]
    scoped = specific_candidates or candidates
    scoped = sorted(
        scoped,
        key=lambda item: (
            float(item.get("score") or 0),
            int(item.get("level") or 0),
            len(str(item.get("section_path") or item.get("heading_path") or "")),
        ),
        reverse=True,
    )
    for item in scoped:
        path = str(item.get("section_path") or item.get("heading_path") or "").strip()
        if not path:
            continue
        if path in selected_paths:
            continue
        if any(existing.startswith(f"{path} >") or existing == path for existing in selected_paths):
            continue
        selected.append(item)
        selected_paths.append(path)
        if len(selected) >= limit:
            break
    return selected or candidates[:limit]


def _serialize_section_candidate(section_candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": str(section_candidate.get("sample_id") or ""),
        "file_name": str(section_candidate.get("file_name") or ""),
        "section_id": str(section_candidate.get("section_id") or ""),
        "section_path": str(section_candidate.get("section_path") or section_candidate.get("heading_path") or ""),
        "source_heading": str(section_candidate.get("source_heading") or ""),
        "level": int(section_candidate.get("level") or 0),
        "score": float(section_candidate.get("score") or 0),
        "reason": str(section_candidate.get("reason") or ""),
    }


def _estimate_material_tokens(*, blocks: list[dict[str, Any]], assets: list[dict[str, Any]]) -> dict[str, Any]:
    section_material_tokens = sum(
        max(1, len(str(block.get("content_md") or "")) // 4)
        for block in blocks
    )
    asset_tokens = sum(
        max(
            1,
            len(
                " ".join(
                    str(item)
                    for item in (
                        asset.get("title"),
                        asset.get("caption"),
                        asset.get("heading_path"),
                        asset.get("preview_text"),
                    )
                    if item
                )
            )
            // 4,
        )
        for asset in assets[:3]
    )
    return {
        "section_material_tokens": section_material_tokens,
        "asset_tokens": asset_tokens,
        "within_budget": section_material_tokens <= FULL_SECTION_MAX_SOURCE_TOKENS,
    }


def _build_selected_block_trace(blocks: list[dict[str, Any]], *, limit: int = REUSE_TRACE_BLOCK_LIMIT) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for block in blocks[:limit]:
        heading_path = [str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()]
        trace.append(
            {
                "block_id": str(block.get("block_id") or ""),
                "source_title": str(block.get("source_title") or ""),
                "source_section_id": str(block.get("source_section_id") or ""),
                "section_path": str(block.get("section_path") or " > ".join(heading_path)),
                "heading_path": heading_path,
                "selection_score": float(block.get("selection_score") or 0),
                "selection_reasons": list(block.get("selection_reasons") or []),
            }
        )
    return trace


def _match_blocks_to_selected_section(
    *,
    reusable_blocks: list[dict[str, Any]],
    selected_section: dict[str, Any],
) -> list[dict[str, Any]]:
    target_sample_id = str(selected_section.get("sample_id") or "").strip()
    target_file_name = str(selected_section.get("file_name") or "").strip()
    target_section_id = str(selected_section.get("section_id") or "").strip()
    target_section_path = str(selected_section.get("section_path") or "").strip()
    matched: list[dict[str, Any]] = []
    for block in reusable_blocks:
        block_sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
        block_file_name = str(block.get("source_title") or "").strip()
        block_section_id = str(block.get("source_section_id") or "").strip()
        block_section_path = str(block.get("section_path") or " > ".join(str(item) for item in (block.get("heading_path") or []) if item)).strip()
        if target_sample_id and block_sample_id and block_sample_id != target_sample_id:
            continue
        if target_file_name and block_file_name and block_file_name != target_file_name:
            continue
        if target_section_id and block_section_id == target_section_id:
            matched.append(block)
            continue
        if target_section_path and block_section_path == target_section_path:
            matched.append(block)
    return matched


def resolve_reuse_generation_strategy(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_trace = reuse_pack.get("retrieval_trace") if isinstance(reuse_pack.get("retrieval_trace"), dict) else {}
    section_candidates = [
        item
        for item in (retrieval_trace.get("section_candidates") or [])
        if isinstance(item, dict)
    ]
    scoped_sections = [
        item
        for item in (retrieval_trace.get("scoped_sections") or [])
        if isinstance(item, dict)
    ]
    selected_sections = scoped_sections[:REUSE_TRACE_SECTION_LIMIT] or section_candidates[:REUSE_TRACE_SECTION_LIMIT]
    if str(section.get("generation_mode") or "baseline") != "reuse_first" or not reusable_blocks:
        prompt_blocks = reusable_blocks[:DEFAULT_REUSE_LIMIT]
        return {
            "retrieval_mode": "baseline_fallback",
            "prompt_blocks": prompt_blocks,
            "selected_sections": selected_sections,
            "selected_blocks": _build_selected_block_trace(prompt_blocks),
            "token_budget": _estimate_material_tokens(blocks=prompt_blocks, assets=recommended_assets),
        }

    top_section = section_candidates[0] if section_candidates else (selected_sections[0] if selected_sections else None)
    runner_up = section_candidates[1] if len(section_candidates) > 1 else None
    top_score = float((top_section or {}).get("score") or 0)
    lead_score = top_score - float((runner_up or {}).get("score") or 0)
    full_section_blocks = (
        _match_blocks_to_selected_section(
            reusable_blocks=reusable_blocks,
            selected_section=top_section,
        )
        if top_section
        else []
    )
    full_section_blocks = sorted(
        full_section_blocks,
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    full_section_budget = _estimate_material_tokens(blocks=full_section_blocks, assets=recommended_assets)
    if (
        top_section
        and top_score >= FULL_SECTION_MIN_SCORE
        and lead_score >= FULL_SECTION_MIN_LEAD
        and full_section_blocks
        and full_section_budget["within_budget"]
    ):
        prompt_blocks = full_section_blocks
        retrieval_mode = "full_section"
        token_budget = full_section_budget
    else:
        prompt_blocks = reusable_blocks[:DEFAULT_REUSE_LIMIT]
        retrieval_mode = "section_pack"
        token_budget = _estimate_material_tokens(blocks=prompt_blocks, assets=recommended_assets)
    return {
        "retrieval_mode": retrieval_mode,
        "prompt_blocks": prompt_blocks,
        "selected_sections": selected_sections,
        "selected_blocks": _build_selected_block_trace(prompt_blocks),
        "token_budget": token_budget,
    }


class SectionDraftService:
    def __init__(
        self,
        *,
        executor: ExecutorAgent | None = None,
        asset_retriever: AssetRetrievalService | None = None,
        case_library: CaseLibraryService | None = None,
        quality_gate: SectionQualityGateService | None = None,
    ) -> None:
        self.executor = executor or ExecutorAgent()
        self.asset_retriever = asset_retriever or AssetRetrievalService()
        self.case_library = case_library or CaseLibraryService()
        self.quality_gate = quality_gate or SectionQualityGateService(executor=self.executor)

    async def generate_sections(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None = None,
    ) -> tuple[Job, list[SectionDraft]]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before generating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)

        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        if not sections:
            raise ArtifactValidationError("Outline has no sections")

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "outline_id": str(outline.id)},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        draft_version = int(project.current_draft_version or 0) + 1
        generated_drafts: list[SectionDraft] = []
        global_params = build_section_global_params(requirement_card.content)

        for section in sections:
            generation_mode = str(section.get("generation_mode") or "baseline")
            context, citations = build_section_context(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            case_library_result = self._retrieve_case_library_matches(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            reusable_blocks = build_reusable_blocks(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                case_library_matches=case_library_result.get("matches") or [],
            )
            reusable_blocks = self._expand_reusable_blocks_from_neighbors(
                section=section,
                reusable_blocks=reusable_blocks,
                global_params=global_params,
                limit=DEFAULT_REUSE_LIMIT,
            )
            recommended_assets = await self._search_recommended_assets(
                session=session,
                project_id=project_id,
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
            )
            reuse_pack = build_reuse_pack(
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
                recommended_assets=recommended_assets,
                retrieval_trace=case_library_result.get("trace"),
            )
            content_md, draft_status, citations, generation_details = await self._generate_section_content(
                task_id=str(job.id),
                section=section,
                outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                global_params=global_params,
                retrieved_context=context,
                citations=citations,
                recommended_assets=recommended_assets,
                reusable_blocks=reusable_blocks,
                reuse_pack=reuse_pack,
            )
            quality_gate_result: dict[str, Any] = {}
            if draft_status == "generated":
                content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                    task_id=str(job.id),
                    section=section,
                    outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                    global_params=global_params,
                    content_md=content_md,
                    recommended_assets=recommended_assets,
                    allow_rewrite=True,
                )
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=str(section.get("title") or "未命名章节"),
                content_md=content_md,
                citation_refs=citations,
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status=draft_status,
                validator_result={
                    "recommended_assets": recommended_assets,
                    "generation_mode": generation_mode,
                    "reuse_pack": reuse_pack,
                    "generation_details": generation_details,
                    "quality_gate": quality_gate_result,
                },
            )
            session.add(draft)
            generated_drafts.append(draft)

        project.current_draft_version = draft_version
        project.status = _derive_project_draft_status(generated_drafts)
        job.status = "succeeded"
        job.output_ref = {"draft_version": draft_version, "section_count": len(generated_drafts)}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        for draft in generated_drafts:
            await session.refresh(draft)
        await session.refresh(job)
        return job, generated_drafts

    async def list_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int | None = None,
    ) -> list[SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        target_draft_version = draft_version or int(project.current_draft_version or 0)
        if target_draft_version <= 0:
            return []

        drafts = await self._load_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=target_draft_version,
        )
        if not drafts:
            return []

        return self._sort_drafts_by_outline(
            drafts=drafts,
            outline=await self._resolve_outline(session=session, project_id=project_id, outline_id=project.current_outline_id),
        )

    async def regenerate_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        outline_id: UUID | None = None,
        preferred_citation_ids: list[str] | None = None,
    ) -> tuple[Job, SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before regenerating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)
        section = self._find_section(outline=outline, section_id=section_id)
        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "section_id": section_id},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        generation_mode = str(section.get("generation_mode") or "baseline")
        normalized_preferred_citation_ids = _normalize_preferred_citation_ids(preferred_citation_ids)
        global_params = build_section_global_params(requirement_card.content)
        context, citations = build_section_context(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
            preferred_evidence_ids=normalized_preferred_citation_ids,
        )
        case_library_result = self._retrieve_case_library_matches(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        reusable_blocks = build_reusable_blocks(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
            case_library_matches=case_library_result.get("matches") or [],
        )
        reusable_blocks = prioritize_reusable_blocks_for_citations(
            reusable_blocks,
            preferred_citation_ids=preferred_citation_ids,
        )
        reusable_blocks = self._expand_reusable_blocks_from_neighbors(
            section=section,
            reusable_blocks=reusable_blocks,
            global_params=global_params,
            limit=DEFAULT_REUSE_LIMIT,
        )
        reusable_blocks = prioritize_reusable_blocks_for_citations(
            reusable_blocks,
            preferred_citation_ids=preferred_citation_ids,
        )
        recommended_assets = await self._search_recommended_assets(
            session=session,
            project_id=project_id,
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
        )
        reuse_pack = build_reuse_pack(
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
            retrieval_trace=case_library_result.get("trace"),
        )
        content_md, draft_status, citations, generation_details = await self._generate_section_content(
            task_id=str(job.id),
            section=section,
            outline_title=(outline.outline_json or {}).get("title", "技术方案"),
            global_params=global_params,
            retrieved_context=context,
            citations=citations,
            recommended_assets=recommended_assets,
            reusable_blocks=reusable_blocks,
            reuse_pack=reuse_pack,
        )
        quality_gate_result: dict[str, Any] = {}
        if draft_status == "generated":
            content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                task_id=str(job.id),
                section=section,
                outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                global_params=global_params,
                content_md=content_md,
                recommended_assets=recommended_assets,
                allow_rewrite=True,
            )
        draft.title = str(section.get("title") or draft.title)
        draft.content_md = content_md
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params
        draft.status = draft_status
        draft.validator_result = {
            "recommended_assets": recommended_assets,
            "generation_mode": generation_mode,
            "reuse_pack": reuse_pack,
            "quality_gate": quality_gate_result,
            "generation_details": {
                **generation_details,
                "selected_citation_ids": sorted(normalized_preferred_citation_ids),
            },
        }
        project.status = await self._compute_project_draft_status(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            fallback_drafts=[draft],
        )

        job.status = "succeeded"
        job.output_ref = {"draft_version": project.current_draft_version, "section_id": section_id}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(draft)
        return job, draft

    async def update_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        content_md: str,
        citation_refs: list | None = None,
        assumptions: list | None = None,
    ) -> SectionDraft:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )
        draft.content_md = content_md
        if citation_refs is not None:
            draft.citation_refs = citation_refs
        if assumptions is not None:
            draft.assumptions = assumptions
        draft.status = "edited"
        current_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        draft.validator_result = {
            "recommended_assets": current_result.get("recommended_assets", []),
            "generation_mode": current_result.get("generation_mode", "baseline"),
            "reuse_pack": current_result.get("reuse_pack", {}),
            "quality_gate": {
                "status": "stale",
                "summary": "manual edit pending validation",
                "issues": [],
            },
        }
        project.status = await self._compute_project_draft_status(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            fallback_drafts=[draft],
        )
        await session.commit()
        await session.refresh(draft)
        return draft

    async def _search_recommended_assets(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        global_params: dict[str, Any],
        reusable_blocks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        query = build_section_asset_query(section=section, global_params=global_params)
        if not query:
            return []
        response = await self.asset_retriever.search_project_assets(
            session=session,
            project_id=project_id,
            query=query,
            top_k=12,
            asset_types=build_section_asset_types(section),
            section_context=_build_asset_search_context(section=section, reusable_blocks=reusable_blocks),
            include_global_historical=True,
        )
        assets = [item.model_dump(mode="json") for item in response.results]
        assets = filter_recommended_assets_for_section(assets, section=section)
        assets = prioritize_recommended_assets(assets)
        return tighten_recommended_assets_for_reuse(
            assets,
            section=section,
            reusable_blocks=reusable_blocks,
        )

    def _retrieve_case_library_matches(
        self,
        *,
        section: dict[str, Any],
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        content = evidence_bundle.content if isinstance(evidence_bundle.content, dict) else {}
        case_candidates = content.get("case_candidates") or []
        sample_ids = {
            str(item.get("sample_id") or "").strip()
            for item in case_candidates
            if str(item.get("sample_id") or "").strip()
        }
        library_tracks = {
            str(item.get("library_track") or "").strip()
            for item in case_candidates
            if str(item.get("library_track") or "").strip()
        }
        if not sample_ids:
            return {"matches": [], "trace": {"query": "", "query_intents": {}, "section_candidates": [], "scoped_sections": []}}
        query = build_section_reuse_query(section=section, global_params=global_params)
        query_intents = build_section_reuse_query_intents(section=section, global_params=global_params)
        section_candidates = self.case_library.retrieve_sections(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=4,
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
        )
        scoped_sections = _select_section_scope_candidates(section_candidates, limit=4)
        section_ids = {
            str(item.get("section_id") or "").strip()
            for item in scoped_sections
            if str(item.get("section_id") or "").strip()
        }
        section_path_prefixes = {
            str(item.get("section_path") or item.get("heading_path") or "").strip()
            for item in scoped_sections
            if str(item.get("section_path") or item.get("heading_path") or "").strip()
        }
        base_matches = self.case_library.retrieve_blocks(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=max(DEFAULT_REUSE_LIMIT * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
            section_ids=section_ids or None,
            section_path_prefixes=section_path_prefixes or None,
        )
        if not base_matches:
            base_matches = self.case_library.retrieve_blocks(
                query=query,
                section_title=str(section.get("title") or ""),
                top_k=max(DEFAULT_REUSE_LIMIT * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
                sample_ids=sample_ids,
                library_tracks=library_tracks or None,
            )
        neighbor_matches = self.case_library.expand_related_blocks(
            seed_blocks=base_matches[: max(DEFAULT_REUSE_LIMIT, 3)],
            section_title=str(section.get("title") or ""),
            top_k=4,
        )
        return {
            "matches": [*base_matches, *neighbor_matches],
            "trace": {
                "query": query,
                "query_intents": query_intents,
                "section_candidates": [
                    _serialize_section_candidate(item)
                    for item in section_candidates[:REUSE_TRACE_SECTION_LIMIT]
                ],
                "scoped_sections": [
                    _serialize_section_candidate(item)
                    for item in scoped_sections[:REUSE_TRACE_SECTION_LIMIT]
                ],
            },
        }

    def _expand_reusable_blocks_from_neighbors(
        self,
        *,
        section: dict[str, Any],
        reusable_blocks: list[dict[str, Any]],
        global_params: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not reusable_blocks:
            return []
        target_taxonomy = infer_target_taxonomy(section)
        target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
        top_score = max(float(item.get("selection_score") or 0) for item in reusable_blocks)
        seed_blocks = [
            item
            for item in reusable_blocks
            if float(item.get("selection_score") or 0) >= max(0.55, top_score * 0.7)
            or str((item.get("metadata") or {}).get("section_type") or "").lower() == target_section_type
        ]
        if not seed_blocks:
            seed_blocks = reusable_blocks[:1]
        neighbor_matches = self.case_library.expand_related_blocks(
            seed_blocks=seed_blocks[:2],
            section_title=str(section.get("title") or ""),
            top_k=4,
        )
        if not neighbor_matches:
            return reusable_blocks[:limit]
        expanded = list(reusable_blocks)
        expanded.extend(
            _build_case_library_reusable_blocks(
                section=section,
                global_params=global_params,
                case_library_matches=neighbor_matches,
                limit=max(limit, len(neighbor_matches)),
            )
        )
        deduped = _dedupe_reusable_blocks(expanded)
        deduped.sort(
            key=lambda item: (
                float(item.get("selection_score") or 0),
                float(item.get("reusability_score") or 0),
            ),
            reverse=True,
        )
        expanded_limit = max(limit, len(reusable_blocks) + len(neighbor_matches))
        return deduped[:expanded_limit]

    async def _generate_section_content(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        outline_title: str,
        global_params: dict[str, Any],
        retrieved_context: str,
        citations: list[dict[str, Any]],
        recommended_assets: list[dict[str, Any]],
        reusable_blocks: list[dict[str, Any]],
        reuse_pack: dict[str, Any],
    ) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
        generation_mode = str(section.get("generation_mode") or "baseline")
        effective_citations = citations
        assembly_blocks = reusable_blocks
        reuse_strategy = resolve_reuse_generation_strategy(
            section=section,
            reuse_pack=reuse_pack,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
        )
        retrieval_mode = str(reuse_strategy.get("retrieval_mode") or "baseline_fallback")
        selected_sections = list(reuse_strategy.get("selected_sections") or [])
        selected_blocks = list(reuse_strategy.get("selected_blocks") or [])
        token_budget = dict(reuse_strategy.get("token_budget") or {})
        if generation_mode == "reuse_first" and reusable_blocks:
            retrieved_context = ""
            prompt_blocks = list(reuse_strategy.get("prompt_blocks") or reusable_blocks)
            if retrieval_mode == "full_section":
                assembly_blocks = prompt_blocks
            else:
                assembly_blocks = _filter_reuse_blocks_for_assembly(
                    reusable_blocks=prompt_blocks,
                    target_taxonomy=infer_target_taxonomy(section),
                )
            selected_blocks = _build_selected_block_trace(assembly_blocks)
            token_budget = _estimate_material_tokens(blocks=assembly_blocks, assets=recommended_assets)
            effective_citations = build_reuse_citations(assembly_blocks)

        if generation_mode == "manual_only":
            return (
                build_manual_only_section_content(section=section, reuse_pack=reuse_pack),
                "manual_required",
                effective_citations,
                {
                    "effective_path": "manual_only",
                    "retrieval_mode": retrieval_mode,
                    "selected_sections": selected_sections,
                    "selected_blocks": selected_blocks,
                    "token_budget": token_budget,
                },
            )

        section_title = str(section.get("title") or "未命名章节")
        if should_use_extractive_reuse(section=section, reuse_pack=reuse_pack):
            assembly_reuse_pack = dict(reuse_pack)
            assembly_reuse_pack["reusable_blocks"] = assembly_blocks
            assembled_content = build_extractive_reuse_section_content(
                section=section,
                reuse_pack=assembly_reuse_pack,
                global_params=global_params,
            )
            assembled_content = ensure_required_asset_placeholders(content_md=assembled_content, reuse_pack=assembly_reuse_pack)
            assembled_content = polish_extractive_reuse_section_content(
                section=section,
                content_md=assembled_content,
            )
            finalized_content: str | None = None
            refinement_status = "fallback_assembled"
            refinement_fallback_reason: str | None = "finalize_missing"
            refinement_error: str | None = None
            try:
                llm_reuse_pack = dict(assembly_reuse_pack)
                llm_reuse_pack["reusable_blocks"] = assembly_blocks[:3]
                response = await self.executor.write_section(
                    task_id=f"{task_id}-finalize",
                    section=section_outline_to_executor_payload(section),
                    global_params=global_params,
                    retrieved_context="",
                    outline_title=outline_title,
                    recommended_assets=recommended_assets,
                    reuse_pack=llm_reuse_pack,
                    assembled_draft=assembled_content,
                )
                finalized_content = response.content
                finalized_content = sanitize_generated_section_content(
                    content_md=finalized_content,
                    section_title=section_title,
                )
                _, refinement_status, refinement_fallback_reason = resolve_reuse_refinement_content(
                    assembled_content=assembled_content,
                    rewritten_content=finalized_content,
                    section_title=section_title,
                )
            except Exception as exc:  # noqa: BLE001
                refinement_error = str(exc)
                refinement_fallback_reason = "finalize_error"
            content_md, refinement_status, selection_fallback_reason = resolve_reuse_refinement_content(
                assembled_content=assembled_content,
                rewritten_content=finalized_content,
                section_title=section_title,
            )
            refinement_fallback_reason = refinement_fallback_reason or selection_fallback_reason
            content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=assembly_reuse_pack)
            content_md = polish_extractive_reuse_section_content(section=section, content_md=content_md)
            effective_path = "extractive_reuse_llm_finalize" if finalized_content is not None and refinement_error is None else "extractive_reuse"
            return (
                content_md,
                "generated",
                effective_citations,
                {
                    "effective_path": effective_path,
                    "retrieval_mode": retrieval_mode,
                    "selected_sections": selected_sections,
                    "selected_blocks": selected_blocks,
                    "token_budget": token_budget,
                    "refinement_status": refinement_status,
                    "refinement_fallback_reason": refinement_fallback_reason,
                    "refinement_error": refinement_error,
                    "assembled_block_count": len(assembly_blocks),
                },
            )

        response = await self.executor.write_section(
            task_id=task_id,
            section=section_outline_to_executor_payload(section),
            global_params=global_params,
            retrieved_context=retrieved_context,
            outline_title=outline_title,
            recommended_assets=recommended_assets,
            reuse_pack=reuse_pack,
        )
        content_md = sanitize_generated_section_content(
            content_md=response.content,
            section_title=section_title,
        )
        content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=reuse_pack)
        return (
            content_md,
            "generated",
            effective_citations,
            {
                "effective_path": "llm_write",
                "retrieval_mode": retrieval_mode,
                "selected_sections": selected_sections,
                "selected_blocks": selected_blocks,
                "token_budget": token_budget,
            },
        )

    async def _resolve_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None,
    ) -> ProposalOutline:
        if outline_id is not None:
            outline = await session.get(ProposalOutline, outline_id)
            if not outline or outline.project_id != project_id:
                raise ArtifactNotFoundError("Outline not found")
            return outline
        result = await session.scalars(
            select(ProposalOutline)
            .where(ProposalOutline.project_id == project_id)
            .order_by(ProposalOutline.version.desc(), ProposalOutline.created_at.desc())
            .limit(1)
        )
        outline = result.first()
        if not outline:
            raise ArtifactNotFoundError("Outline not found")
        return outline

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> RequirementCard:
        if outline.requirement_card_id is None:
            raise ArtifactValidationError("Outline is not bound to a requirement card")
        card = await session.get(RequirementCard, outline.requirement_card_id)
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _resolve_evidence_bundle(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> EvidenceBundle:
        if outline.evidence_bundle_id is None:
            raise ArtifactValidationError("Outline is not bound to an evidence bundle")
        bundle = await session.get(EvidenceBundle, outline.evidence_bundle_id)
        if not bundle:
            raise ArtifactNotFoundError("Evidence bundle not found")
        return bundle

    async def _get_current_section_draft(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
        section_id: str,
    ) -> SectionDraft:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
                SectionDraft.section_id == section_id,
            )
            .limit(1)
        )
        draft = result.first()
        if not draft:
            raise ArtifactNotFoundError("Section draft not found")
        return draft

    async def _load_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
    ) -> list[SectionDraft]:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
            )
            .order_by(SectionDraft.section_id.asc())
        )
        return list(result.all())

    async def _compute_project_draft_status(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
        fallback_drafts: list[SectionDraft] | None = None,
    ) -> str:
        drafts = await self._load_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=draft_version,
        )
        if not drafts:
            drafts = fallback_drafts or []
        return _derive_project_draft_status(drafts)

    def _find_section(self, *, outline: ProposalOutline, section_id: str) -> dict[str, Any]:
        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        for section in sections:
            if str(section.get("section_id")) == section_id:
                return section
        raise ArtifactNotFoundError("Section not found in outline")

    def _sort_drafts_by_outline(self, *, drafts: list[SectionDraft], outline: ProposalOutline) -> list[SectionDraft]:
        ordered_sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        order_map = {
            str(section.get("section_id") or ""): index
            for index, section in enumerate(ordered_sections)
        }
        return sorted(
            drafts,
            key=lambda draft: (order_map.get(draft.section_id, len(order_map)), draft.section_id),
        )
