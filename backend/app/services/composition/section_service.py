from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
GENERIC_REUSE_HEADINGS = {
    "产品简介",
    "技术方案",
    "总体方案",
    "总体说明",
    "项目概述",
    "系统方案",
}
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
) -> tuple[str, list[dict[str, Any]]]:
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=limit,
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
        ):
            contextual_matches.append(asset)
            continue

    if strong_matches:
        primary = strong_matches[:1]
        support = [
            asset
            for asset in contextual_matches
            if asset.get("asset_id") != primary[0].get("asset_id")
        ]
        return primary + support[:2]
    if contextual_matches:
        return contextual_matches[:2]
    return recommended_assets[:1]


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
        "只做必要的统一、去重和替换，不要自由扩写背景，不要补充空泛套话。",
        "删除旧客户、旧项目和样板来源痕迹，严格使用当前项目字段。",
    ]
    if reuse_pack.get("required_asset_placeholders"):
        lines.append("若章节需要图表引用，请保留已有 [[ASSET:...]] 占位符，并放在合适位置。")
    if bool(section.get("parameter_sensitive")):
        lines.append("参数相关表述必须保持谨慎，不得虚构未确认参数。")
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))


def select_preferred_reuse_content(
    *,
    assembled_content: str,
    rewritten_content: str | None,
    section_title: str,
) -> str:
    assembled = sanitize_generated_section_content(content_md=assembled_content, section_title=section_title)
    if not rewritten_content:
        return assembled
    rewritten = sanitize_generated_section_content(content_md=rewritten_content, section_title=section_title)
    assembled_body = _strip_section_heading(assembled)
    rewritten_body = _strip_section_heading(rewritten)
    if not rewritten_body.strip():
        return assembled
    if any(token in rewritten_body for token in REWRITE_LEAKAGE_TOKENS):
        return assembled
    if len(rewritten_body) < max(260, int(len(assembled_body) * 0.68)):
        return assembled
    if _technical_density(rewritten_body) < (_technical_density(assembled_body) * 0.72):
        return assembled
    if rewritten_body.count("\n\n") + 1 < max(2, (assembled_body.count("\n\n") + 1) // 2):
        return assembled
    return rewritten


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


def _build_asset_search_context(
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reusable_blocks = list(reusable_blocks or [])
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
    return {
        "section_title": str(section.get("title") or ""),
        "expected_evidence_types": list(section.get("expected_evidence_types") or []),
        "keywords": list(section.get("keywords") or []),
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
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_reuse_query(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or "").strip(),
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_asset_types(section: dict[str, Any]) -> list[str] | None:
    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
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
    return [item for _, item in ranked[:limit]]


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
                "title": asset.get("title") or asset.get("caption") or "参考资产",
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
                title_text = asset.get("title") or asset.get("caption") or "参考资产"
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

    appendix_lines = ["", "### 建议插入图表", ""]
    for item in missing:
        appendix_lines.append(f"- {item.get('placeholder')} {item.get('title') or '参考资产'}")
    return content_md.rstrip() + "\n" + "\n".join(appendix_lines).rstrip() + "\n"


def _build_reuse_query_terms(*, section: dict[str, Any], global_params: dict[str, Any]) -> list[str]:
    parts = [
        str(section.get("title") or ""),
        str(section.get("purpose") or ""),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or ""),
        str(global_params.get("product_line") or ""),
        str(global_params.get("industry") or ""),
    ]
    tokens: list[str] = []
    for part in parts:
        for token in _tokenize_reuse_text(part):
            if token not in tokens:
                tokens.append(token)
    for hint in extract_taxonomy_hints(*parts):
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


class SectionDraftService:
    def __init__(
        self,
        *,
        executor: ExecutorAgent | None = None,
        asset_retriever: AssetRetrievalService | None = None,
        case_library: CaseLibraryService | None = None,
    ) -> None:
        self.executor = executor or ExecutorAgent()
        self.asset_retriever = asset_retriever or AssetRetrievalService()
        self.case_library = case_library or CaseLibraryService()

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
            reusable_blocks = build_reusable_blocks(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                case_library_matches=self._retrieve_case_library_matches(
                    section=section,
                    evidence_bundle=evidence_bundle,
                    global_params=global_params,
                ),
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
                },
            )
            session.add(draft)
            generated_drafts.append(draft)

        project.current_draft_version = draft_version
        project.status = "DRAFT_READY"
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

        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == target_draft_version,
            )
            .order_by(SectionDraft.section_id.asc())
        )
        drafts = list(result.all())
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
        global_params = build_section_global_params(requirement_card.content)
        context, citations = build_section_context(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        reusable_blocks = build_reusable_blocks(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
            case_library_matches=self._retrieve_case_library_matches(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            ),
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
        draft.title = str(section.get("title") or draft.title)
        draft.content_md = content_md
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params
        draft.status = draft_status
        draft.validator_result = {
            "recommended_assets": recommended_assets,
            "generation_mode": generation_mode,
            "reuse_pack": reuse_pack,
            "generation_details": generation_details,
        }
        project.status = "DRAFT_READY"

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
        }
        project.status = "DRAFT_READY"
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
    ) -> list[dict[str, Any]]:
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
            return []
        query = build_section_reuse_query(section=section, global_params=global_params)
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
        return [*base_matches, *neighbor_matches]

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
        if generation_mode == "reuse_first" and reusable_blocks:
            retrieved_context = ""
            assembly_blocks = _filter_reuse_blocks_for_assembly(
                reusable_blocks=reusable_blocks,
                target_taxonomy=infer_target_taxonomy(section),
            )
            effective_citations = build_reuse_citations(assembly_blocks)

        if generation_mode == "manual_only":
            return (
                build_manual_only_section_content(section=section, reuse_pack=reuse_pack),
                "manual_required",
                effective_citations,
                {"effective_path": "manual_only"},
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
            rewritten_content: str | None = None
            refinement_status = "fallback_assembled"
            refinement_error: str | None = None
            try:
                response = await self.executor.rewrite_section(
                    task_id=task_id,
                    section_context=build_reuse_refinement_context(
                        section=section,
                        reuse_pack=assembly_reuse_pack,
                        global_params=global_params,
                    ),
                    selected_text=assembled_content,
                    instruction=build_reuse_refinement_instruction(section=section, reuse_pack=assembly_reuse_pack),
                    global_params=global_params,
                )
                rewritten_content = response.content
                refinement_status = "rewrite_applied"
            except Exception as exc:  # noqa: BLE001
                refinement_error = str(exc)
            content_md = select_preferred_reuse_content(
                assembled_content=assembled_content,
                rewritten_content=rewritten_content,
                section_title=section_title,
            )
            content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=assembly_reuse_pack)
            return (
                content_md,
                "generated",
                effective_citations,
                {
                    "effective_path": "extractive_reuse",
                    "refinement_status": refinement_status,
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
            {"effective_path": "llm_write"},
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
