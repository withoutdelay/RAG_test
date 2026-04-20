from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import Settings, get_settings
from app.services.gateway_client import GatewayClient


class ModelType(str, Enum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    DOUBAO = "doubao"
    AZURE = "azure"
    OPENAI = "openai"


class TaskType(str, Enum):
    EXTRACTION = "extraction"
    ASSET_REVIEW = "asset_review"
    ASSET_SUMMARY = "asset_summary"
    SECTION_QUALITY = "section_quality"
    OUTLINE = "outline"
    SECTION_WRITE = "section_write"
    HOLISTIC = "holistic"
    REWRITE = "rewrite"
    QUESTION_GEN = "question_gen"


ROUTING_TABLE = {
    TaskType.EXTRACTION: ModelType.DEEPSEEK,
    TaskType.ASSET_REVIEW: ModelType.DOUBAO,
    TaskType.ASSET_SUMMARY: ModelType.DOUBAO,
    TaskType.SECTION_QUALITY: ModelType.DOUBAO,
    TaskType.OUTLINE: ModelType.DOUBAO,
    TaskType.SECTION_WRITE: ModelType.DOUBAO,
    TaskType.HOLISTIC: ModelType.DOUBAO,
    TaskType.REWRITE: ModelType.DOUBAO,
    TaskType.QUESTION_GEN: ModelType.DEEPSEEK,
}

FALLBACK_TABLE = {
    ModelType.DEEPSEEK: ModelType.QWEN,
    ModelType.QWEN: ModelType.DOUBAO,
    ModelType.DOUBAO: ModelType.QWEN,
    ModelType.AZURE: ModelType.QWEN,
    ModelType.OPENAI: ModelType.DOUBAO,
}


@dataclass(slots=True)
class LLMRequest:
    task_type: TaskType
    system_prompt: str
    user_prompt: str
    temperature: float = 0.3
    max_tokens: int = 4096
    json_schema: dict | None = None
    stream: bool = False
    session_id: str | None = None
    entity_types: list[str] | None = None
    input_images: list["LLMInputImage"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMInputImage:
    image_url: str
    detail: str = "auto"


@dataclass(slots=True)
class LLMResponse:
    content: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_estimate: float


@dataclass(slots=True)
class ProviderEndpointConfig:
    provider_name: str
    base_url: str
    api_key: str
    model_name: str | None = None
    path: str = "/chat/completions"
    api_style: str = "chat_completions"
    auth_header_name: str = "Authorization"
    auth_scheme: str | None = "Bearer"
    query_params: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path}"


class RetryableLLMError(RuntimeError):
    pass


class BaseLLMProvider(ABC):
    def supports_model(self, model_type: ModelType) -> bool:
        return True

    @abstractmethod
    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    @abstractmethod
    async def invoke_stream(self, model_type: ModelType, request: LLMRequest) -> AsyncIterator[str]:
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    MODEL_RATES = {
        ModelType.DEEPSEEK: 0.000002,
        ModelType.QWEN: 0.000003,
        ModelType.DOUBAO: 0.000003,
        ModelType.AZURE: 0.000004,
        ModelType.OPENAI: 0.000004,
    }

    def __init__(self, *, chunk_size: int = 48, failing_models: set[ModelType] | None = None) -> None:
        self.chunk_size = max(1, chunk_size)
        self.failing_models = failing_models or set()

    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        self._raise_if_needed(model_type)
        content = self._render_content(model_type, request)
        prompt_tokens = _estimate_tokens(f"{request.system_prompt}\n{request.user_prompt}")
        completion_tokens = _estimate_tokens(content)
        total_tokens = prompt_tokens + completion_tokens
        cost_estimate = round(total_tokens * self.MODEL_RATES[model_type], 6)
        return LLMResponse(
            content=content,
            model_used=f"{model_type.value}:mock",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=cost_estimate,
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.invoke(model_type, request)
        for index in range(0, len(response.content), self.chunk_size):
            yield response.content[index : index + self.chunk_size]

    def _raise_if_needed(self, model_type: ModelType) -> None:
        if model_type in self.failing_models:
            raise RuntimeError(f"Mock provider forced failure for {model_type.value}")

    def _render_content(self, model_type: ModelType, request: LLMRequest) -> str:
        if request.task_type == TaskType.OUTLINE:
            return self._render_outline(request)
        if request.task_type == TaskType.SECTION_WRITE:
            return self._render_section(model_type, request)
        if request.task_type == TaskType.HOLISTIC:
            return self._render_holistic(request)
        if request.task_type == TaskType.REWRITE:
            return self._render_rewrite(model_type, request)
        if request.task_type == TaskType.EXTRACTION:
            return json.dumps({"summary": request.user_prompt[:120]}, ensure_ascii=False)
        if request.task_type == TaskType.ASSET_REVIEW:
            return self._render_asset_review(request)
        if request.task_type == TaskType.ASSET_SUMMARY:
            return self._render_asset_summary(request)
        if request.task_type == TaskType.SECTION_QUALITY:
            return self._render_section_quality(request)
        if request.task_type == TaskType.QUESTION_GEN:
            return "1. 关键参数是否已经最终确认？\n2. 现场实施窗口是否已锁定？"
        return request.user_prompt

    def _render_outline(self, request: LLMRequest) -> str:
        project_name = str(request.metadata.get("project_name") or "售前项目")
        instructions = str(request.metadata.get("instructions") or request.user_prompt)
        key_params = request.metadata.get("global_params") or {}
        sections = [
            {
                "index": 0,
                "title": "项目概述",
                "description": f"介绍{project_name}的建设目标与方案范围。",
                "keywords": [project_name, "项目背景", "建设目标"],
            },
            {
                "index": 1,
                "title": "需求分析",
                "description": "梳理客户核心需求、约束条件与关键指标。",
                "keywords": ["需求分析", "关键指标", "约束条件"],
            },
            {
                "index": 2,
                "title": "技术架构",
                "description": "说明系统总体架构、模块划分与接口关系。",
                "keywords": ["技术架构", "系统设计", "接口集成"],
            },
            {
                "index": 3,
                "title": "硬件配置清单",
                "description": "列出关键设备、数量及配置建议。",
                "keywords": ["硬件配置", "设备清单", "容量规划"],
            },
            {
                "index": 4,
                "title": "实施排期",
                "description": "规划实施阶段、里程碑与验收安排。",
                "keywords": ["实施计划", "里程碑", "验收"],
            },
            {
                "index": 5,
                "title": "售后服务",
                "description": "说明运维、培训与服务承诺。",
                "keywords": ["售后服务", "运维保障", "培训"],
            },
        ]
        title = f"{project_name}技术方案"
        if key_params:
            title = f"{project_name}技术方案（{next(iter(key_params.values()))}）"
        outline = {
            "title": title,
            "sections": sections,
            "notes": instructions[:120],
        }
        return json.dumps(outline, ensure_ascii=False)

    def _render_section(self, model_type: ModelType, request: LLMRequest) -> str:
        section = request.metadata.get("section") or {}
        title = str(section.get("title") or "未命名章节")
        assembled_draft = str(request.metadata.get("assembled_draft") or "").strip()
        if assembled_draft:
            return self._normalize_section_markdown(title=title, content=assembled_draft)

        reuse_pack = request.metadata.get("reuse_pack") if isinstance(request.metadata.get("reuse_pack"), dict) else {}
        reusable_blocks = reuse_pack.get("reusable_blocks") if isinstance(reuse_pack.get("reusable_blocks"), list) else []
        global_params = request.metadata.get("global_params") if isinstance(request.metadata.get("global_params"), dict) else {}
        context = str(request.metadata.get("retrieved_context") or "").strip()
        generation_mode = str(section.get("generation_mode") or reuse_pack.get("generation_mode") or "baseline").strip().lower()

        sections: list[tuple[str | None, str]] = []
        if generation_mode == "reuse_first":
            sections.extend(self._build_mock_reuse_sections(title=title, reusable_blocks=reusable_blocks))
        if not sections:
            sections.extend(
                self._build_mock_structured_sections(
                    title=title,
                    section=section,
                    global_params=global_params,
                )
            )
        if not sections:
            context_body = self._build_mock_context_summary(context)
            if context_body:
                sections.append(("关键信息提炼", context_body))

        if not sections:
            sections.append((None, "本章节结合当前项目已确认资料编制，详细参数和实施边界以最终确认文件为准。"))

        lines = [f"## {title}", ""]
        seen_headings: set[str] = set()
        title_key = self._normalize_heading_key(title)
        for heading, body in sections:
            normalized_body = str(body or "").strip()
            if not normalized_body:
                continue
            heading_key = self._normalize_heading_key(heading or "")
            if heading and heading_key and heading_key not in seen_headings and heading_key != title_key:
                lines.extend([f"### {heading}", ""])
                seen_headings.add(heading_key)
            lines.extend([normalized_body, ""])
        return "\n".join(lines).rstrip() + "\n"

    def _build_mock_structured_sections(
        self,
        *,
        title: str,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> list[tuple[str | None, str]]:
        if not global_params:
            return []

        section_class = str(section.get("section_class") or "").strip().lower()
        if section_class == "overview" or "概述" in title:
            return self._build_mock_overview_sections(global_params)
        if section_class == "requirement" or "需求" in title:
            return self._build_mock_requirement_sections(global_params)
        return []

    def _build_mock_overview_sections(self, global_params: dict[str, Any]) -> list[tuple[str | None, str]]:
        summary = str(global_params.get("solution_summary") or "").strip()
        selected_products = self._humanize_selected_products(global_params.get("selected_products"))
        primary_model = str(global_params.get("primary_model_number") or "").strip()
        model_summary = str(global_params.get("catalog_model_summary") or "").strip()
        interface_summary = self._humanize_catalog_interface_summary(global_params.get("catalog_interface_summary"))
        compatibility_summary = self._humanize_compatibility_summary(global_params.get("compatibility_summary"))
        protocol = str(global_params.get("dcs_protocol") or "").strip()

        overview_paragraphs: list[str] = []
        if summary:
            overview_paragraphs.append(summary)
        if selected_products:
            overview_paragraphs.append(
                f"本次方案范围覆盖{self._join_list_as_cn(selected_products[:4])}，用于收口主设备、配套设备与供货边界。"
            )
        if primary_model:
            overview_paragraphs.append(f"目录侧已匹配主设备型号 {primary_model}，可作为后续技术确认与成套收口的参考。")

        boundary_paragraphs: list[str] = []
        if model_summary:
            boundary_paragraphs.append(model_summary)
        if interface_summary:
            boundary_paragraphs.append(interface_summary)
        elif protocol:
            boundary_paragraphs.append(f"控制接口按 {protocol} 进行组织，后续需进一步冻结站点、点表和联锁边界。")
        if compatibility_summary:
            boundary_paragraphs.append(compatibility_summary)

        sections: list[tuple[str | None, str]] = []
        if overview_paragraphs:
            sections.append(("项目背景与方案范围", "\n\n".join(overview_paragraphs)))
        if boundary_paragraphs:
            sections.append(("当前方案边界", "\n\n".join(boundary_paragraphs)))
        return sections

    def _build_mock_requirement_sections(self, global_params: dict[str, Any]) -> list[tuple[str | None, str]]:
        summary = str(global_params.get("solution_summary") or "").strip()
        selected_products = self._humanize_selected_products(global_params.get("selected_products"))
        matching_signals = self._humanize_matching_signals(global_params.get("matching_signals"))
        compatibility_summary = self._humanize_compatibility_summary(global_params.get("compatibility_summary"))
        risk_flags = self._parse_list_like_value(global_params.get("solution_risk_flags"))
        voltage_level = str(global_params.get("voltage_level") or "").strip()
        power_rating = str(global_params.get("power_rating") or "").strip()
        protocol = str(global_params.get("dcs_protocol") or "").strip()
        interface_summary = self._humanize_catalog_interface_summary(global_params.get("catalog_interface_summary"))

        requirement_items: list[str] = []
        if summary:
            requirement_items.append(summary)
        if selected_products:
            requirement_items.append(f"当前拟配置设备包括{self._join_list_as_cn(selected_products[:4])}。")
        if voltage_level or power_rating:
            scenario = " / ".join(item for item in [voltage_level, power_rating] if item)
            requirement_items.append(f"主设备容量与电气边界需围绕 {scenario} 场景完成校核。")
        if interface_summary:
            requirement_items.append(interface_summary)
        elif protocol:
            requirement_items.append(f"控制系统需接入 {protocol}，并明确站点划分、点表边界与联锁条件。")

        boundary_items: list[str] = []
        if compatibility_summary:
            boundary_items.append(compatibility_summary)
        boundary_items.extend(f"需重点体现：{item}" for item in matching_signals[:3])
        boundary_items.extend(f"待确认事项：{item}" for item in risk_flags[:3])

        sections: list[tuple[str | None, str]] = []
        if requirement_items:
            sections.append(("核心需求", self._render_mock_bullets(requirement_items)))
        if boundary_items:
            sections.append(("约束与边界", self._render_mock_bullets(boundary_items)))
        return sections

    def _render_mock_bullets(self, items: list[str]) -> str:
        normalized_items: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = re.sub(r"\s+", " ", str(item or "")).strip()
            if len(normalized) < 12 or normalized in seen:
                continue
            seen.add(normalized)
            normalized_items.append(f"- {normalized}")
        return "\n".join(normalized_items).strip()

    def _parse_list_like_value(self, value: Any) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        normalized = text.replace("；", ";").replace(" / ", ";").replace("\n", ";")
        parts = [re.sub(r"\s+", " ", item).strip() for item in normalized.split(";")]
        return [item for item in parts if item]

    def _join_list_as_cn(self, items: list[str]) -> str:
        normalized = [str(item).strip() for item in items if str(item).strip()]
        if not normalized:
            return ""
        if len(normalized) == 1:
            return normalized[0]
        return "、".join(normalized)

    def _humanize_selected_products(self, value: Any) -> list[str]:
        items = self._parse_list_like_value(value)
        humanized: list[str] = []
        for item in items:
            normalized = item.replace(" x", " ").strip()
            if ":" in normalized:
                role, name = [part.strip() for part in normalized.split(":", 1)]
                normalized = f"{role}{name}"
            normalized = re.sub(r"\b(\d+)\b$", r"\1 套", normalized)
            humanized.append(normalized)
        return humanized

    def _humanize_matching_signals(self, value: Any) -> list[str]:
        items = self._parse_list_like_value(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            key = item.casefold().replace("project.", "")
            if key in seen:
                continue
            seen.add(key)
            if "=" in key:
                field, raw_value = [part.strip() for part in key.split("=", 1)]
                mapped_value = self._replace_catalog_codes(raw_value)
                if field == "industry":
                    deduped.append(f"行业场景需按{mapped_value}工况组织方案。")
                    continue
                if field == "product_line":
                    deduped.append(f"当前方案需与{mapped_value}产品线能力保持一致。")
                    continue
            deduped.append(self._replace_catalog_codes(item))
        return deduped

    def _humanize_catalog_interface_summary(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = self._replace_catalog_codes(text)
        parts = [part.strip() for part in normalized.split("/") if part.strip()]
        protocols = [part for part in parts if re.search(r"profibus|profinet|modbus|iec|ethernet", part, re.IGNORECASE)]
        has_io_signal = any(part.lower() in {"i/o signal", "io signal"} for part in parts)
        series_name = next(
            (
                part
                for part in parts
                if not re.search(r"communication|signal|profibus|profinet|modbus|iec|ethernet", part, re.IGNORECASE)
            ),
            "主驱动系统",
        )
        clauses: list[str] = []
        if protocols:
            clauses.append(f"{series_name}通信接口按 {protocols[0]} 规划")
        if has_io_signal:
            clauses.append("并预留主驱动与配套设备之间的必要 I/O 信号")
        if clauses:
            return "，".join(clauses) + "。"
        return normalized

    def _humanize_compatibility_summary(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = self._replace_catalog_codes(text)
        normalized = normalized.replace("必需配套 -> 配套设备", "当前方案需配置配套设备")
        normalized = normalized.replace("建议配套 -> ", "建议补充")
        normalized = normalized.replace("可选配套 -> ", "可选补充")
        normalized = normalized.replace("冲突关系 -> ", "需避免与")
        normalized = normalized.replace("已补齐 ", "已补齐")
        normalized = normalized.replace("已覆盖 ", "已覆盖")
        normalized = normalized.replace("目录缺失 ", "目录中尚缺")
        normalized = normalized.replace("可选系列 ", "可选配置包括")
        normalized = normalized.replace("优先系列 ", "优先配置")
        normalized = normalized.replace("条件 ", "适用条件为")
        normalized = re.sub(r"\s*->\s*", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _replace_catalog_codes(self, value: str) -> str:
        code_map = {
            "lci_sync_drive": "LCI 同步电机变频软起动系统",
            "support_equipment": "配套设备",
            "rectifier_transformer": "整流变压器",
            "excitation_cabinet": "励磁控制柜",
            "bypass_cabinet": "旁路柜",
            "io_signal": "I/O Signal",
            "communication": "Communication",
        }
        normalized = str(value or "")
        for code, label in code_map.items():
            normalized = re.sub(rf"\b{re.escape(code)}\b", label, normalized, flags=re.IGNORECASE)
        return normalized

    def _normalize_section_markdown(self, *, title: str, content: str) -> str:
        normalized = str(content or "").strip()
        if not normalized:
            return f"## {title}\n"
        if normalized.lstrip().startswith("#"):
            return normalized.rstrip() + "\n"
        return f"## {title}\n\n{normalized}\n"

    def _build_mock_reuse_sections(
        self,
        *,
        title: str,
        reusable_blocks: list[dict[str, Any]],
    ) -> list[tuple[str | None, str]]:
        sections: list[tuple[str | None, str]] = []
        seen_bodies: set[str] = set()
        title_key = self._normalize_heading_key(title)
        for block in reusable_blocks[:4]:
            body = self._condense_mock_block_body(str(block.get("content_md") or ""))
            body_key = re.sub(r"\s+", " ", body).strip().casefold()
            if not body or not self._looks_readable_source_text(body) or body_key in seen_bodies:
                continue
            seen_bodies.add(body_key)
            heading = str(block.get("source_heading") or "").strip() or None
            if heading and not self._looks_readable_source_text(heading):
                heading = None
            if heading and self._normalize_heading_key(heading) == title_key:
                heading = None
            sections.append((heading, body))
        return sections

    def _condense_mock_block_body(self, content: str) -> str:
        lines = str(content or "").splitlines()
        blocks: list[str] = []
        current: list[str] = []
        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if current:
                    blocks.append("\n".join(current).strip())
                    current = []
                continue
            if stripped.startswith("#"):
                continue
            current.append(line)
        if current:
            blocks.append("\n".join(current).strip())

        selected: list[str] = []
        for block in blocks:
            normalized = re.sub(r"\s+", " ", block).strip()
            if len(normalized) < 18 or not self._looks_readable_source_text(normalized):
                continue
            selected.append(block)
            if len(selected) >= 3:
                break
        return "\n\n".join(selected).strip()

    def _build_mock_context_summary(self, context: str) -> str:
        items: list[str] = []
        seen: set[str] = set()
        for raw_line in str(context or "").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            item = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if ":" in item:
                prefix, suffix = item.split(":", 1)
                if re.search(r"\.(?:docx?|pdf|pptx?|xlsx?)\b", prefix, re.IGNORECASE):
                    continue
                item = suffix.strip()
            item = re.sub(r"\s+", " ", item).strip()
            if len(item) < 18 or item in seen or not self._looks_readable_source_text(item):
                continue
            seen.add(item)
            items.append(f"- {item}")
            if len(items) >= 5:
                break
        return "\n".join(items).strip()

    def _looks_readable_source_text(self, value: str) -> bool:
        normalized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value or "")).strip()
        if not normalized:
            return False
        sample = normalized[:900]
        readable_count = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", sample))
        return readable_count / max(len(sample), 1) >= 0.35

    def _normalize_heading_key(self, value: str) -> str:
        normalized = re.sub(r"[()（）【】\[\]《》·:：,，/\\\-\s]+", "", str(value or "")).casefold()
        return normalized.strip()

    def _render_holistic(self, request: LLMRequest) -> str:
        sections_markdown = str(request.metadata.get("sections_markdown") or request.user_prompt)
        title = str(request.metadata.get("outline_title") or "技术方案终稿")
        return f"# {title}\n\n{sections_markdown}"

    def _render_rewrite(self, model_type: ModelType, request: LLMRequest) -> str:
        section_context = str(request.metadata.get("section_context") or "")
        selected_text = str(request.metadata.get("selected_text") or "")
        instruction = str(request.metadata.get("instruction") or "")
        revised_paragraph = (
            f"已根据要求完成重写：{instruction}。"
            f"当前内容强调了{model_type.value.upper()}模型输出的专业表达与业务一致性。"
        )
        if selected_text and selected_text in section_context:
            return section_context.replace(selected_text, revised_paragraph)
        if section_context:
            return f"{section_context}\n\n{revised_paragraph}"
        return revised_paragraph

    def _render_asset_review(self, request: LLMRequest) -> str:
        candidates = request.metadata.get("candidates") or []
        items: list[dict[str, Any]] = []
        for item in candidates:
            candidate_index = int(item.get("candidate_index") or 0)
            current_role = str(item.get("current_visual_role") or "illustration")
            title = str(item.get("title") or item.get("heading_path") or "")
            items.append(
                {
                    "candidate_index": candidate_index,
                    "visual_role": current_role,
                    "confidence": 0.55,
                    "reason": "mock asset review kept the current classification",
                    "title_hint": title[:80],
                }
            )
        return json.dumps({"items": items}, ensure_ascii=False)

    def _render_asset_summary(self, request: LLMRequest) -> str:
        candidates = request.metadata.get("candidates") or []
        items: list[dict[str, Any]] = []
        for item in candidates:
            candidate_index = int(item.get("candidate_index") or 0)
            title = str(item.get("title") or item.get("heading_path") or "方案图")
            context = " ".join(
                part
                for part in (
                    str(item.get("caption") or "").strip(),
                    str(item.get("context_before") or "").strip(),
                    str(item.get("context_after") or "").strip(),
                )
                if part
            )
            items.append(
                {
                    "candidate_index": candidate_index,
                    "title_hint": title[:48],
                    "diagram_type": "工程示意图",
                    "summary": f"{title}，用于说明系统结构、关键设备关系或控制逻辑。",
                    "problem_solved": context[:120] or "用于辅助解释系统方案中的关键技术问题。",
                    "principle_summary": f"{title}展示主要设备、接口与控制关系，便于章节复用时说明工作原理。",
                    "key_components": [title[:48]] if title else [],
                    "signals_or_loops": [],
                    "applicable_sections": ["总体方案", "系统方案", "控制系统方案"],
                    "retrieval_keywords": [title[:32]] if title else [],
                    "confidence": 0.66,
                    "review_required": False,
                    "review_notes": "mock semantic summary",
                }
            )
        return json.dumps({"items": items}, ensure_ascii=False)

    def _render_section_quality(self, request: LLMRequest) -> str:
        draft_text = str(request.user_prompt or "")
        issues: list[dict[str, Any]] = []
        if "建议插入图表" in draft_text:
            issues.append(
                {
                    "code": "SQ001",
                    "severity": "high",
                    "target": "建议插入图表",
                    "message": "章节包含内部图表建议标题，不适合直接给客户展示。",
                    "suggested_fix": "删除“建议插入图表”这类内部标题，把图表自然融入正文。",
                }
            )
        if "### A. 概述" in draft_text or "\n### A." in draft_text:
            issues.append(
                {
                    "code": "SQ002",
                    "severity": "medium",
                    "target": "A. 概述",
                    "message": "章节小标题存在英文字母编号风格，与中文客户稿不一致。",
                    "suggested_fix": "改成直接表达技术主题的中文小标题，不要使用 A./B. 编号。",
                }
            )
        passed = not issues
        return json.dumps(
            {
                "pass": passed,
                "score": 0.9 if passed else 0.62,
                "summary": "章节标题风格和结构基本合格。" if passed else "章节存在小标题风格或内部提示语问题。",
                "issues": issues,
                "rewrite_instruction": (
                    "统一小标题风格，删除内部图表建议标题，并将 A./B. 样式改为中文技术主题标题。"
                    if issues
                    else "保持当前章节结构和技术表达。"
                ),
            },
            ensure_ascii=False,
        )


class HTTPChatCompletionsProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout_seconds = self.settings.llm_timeout_seconds
        self.stream_timeout_seconds = max(self.settings.llm_stream_timeout_seconds, self.timeout_seconds, 1.0)
        self.retry_attempts = max(0, int(self.settings.llm_retry_attempts))
        self.retry_backoff_seconds = max(0.0, self.settings.llm_retry_backoff_seconds)
        self.transport = transport
        self._configs = self._build_configs(self.settings)

    def supports_model(self, model_type: ModelType) -> bool:
        config = self._configs.get(model_type)
        return config is not None and bool(config.api_key and config.base_url)

    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        config = self._get_config(model_type)
        if config.api_style == "responses":
            payload = self._build_payload(model_type, request, stream=True)
            return await self._invoke_with_retry(
                operation=lambda: self._collect_responses_stream(
                    config=config,
                    request=request,
                    model_type=model_type,
                    payload=payload,
                ),
            )
        payload = self._build_payload(model_type, request, stream=False)
        return await self._invoke_with_retry(
            operation=lambda: self._invoke_chat_completion(
                config=config,
                request=request,
                model_type=model_type,
                payload=payload,
            ),
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest) -> AsyncIterator[str]:
        config = self._get_config(model_type)
        if config.api_style == "responses":
            payload = self._build_payload(model_type, request, stream=True)
            async for delta in self._iterate_with_retry(
                operation_factory=lambda: self._iterate_responses_stream(
                    config=config,
                    payload=payload,
                    model_type=model_type,
                ),
            ):
                if delta:
                    yield delta
            return
        payload = self._build_payload(model_type, request, stream=True)
        async for delta in self._iterate_with_retry(
            operation_factory=lambda: self._iterate_chat_completions_stream(
                config=config,
                payload=payload,
                model_type=model_type,
            ),
        ):
            if delta:
                yield delta

    async def _invoke_chat_completion(
        self,
        *,
        config: ProviderEndpointConfig,
        request: LLMRequest,
        model_type: ModelType,
        payload: dict[str, Any],
    ) -> LLMResponse:
        response_json = await self._post_json(config=config, payload=payload, model_type=model_type)
        return self._parse_completion_response(model_type=model_type, request=request, data=response_json)

    async def _invoke_with_retry(self, *, operation) -> LLMResponse:
        last_error: RetryableLLMError | None = None
        for attempt in range(self.retry_attempts + 1):
            try:
                return await operation()
            except RetryableLLMError as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    break
                await self._sleep_before_retry(attempt + 1)
        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("LLM invocation failed without a retryable error")

    async def _iterate_with_retry(self, *, operation_factory) -> AsyncIterator[str]:
        last_error: RetryableLLMError | None = None
        for attempt in range(self.retry_attempts + 1):
            emitted = False
            try:
                async for chunk in operation_factory():
                    emitted = True
                    yield chunk
                return
            except RetryableLLMError as exc:
                last_error = exc
                if emitted or attempt >= self.retry_attempts:
                    raise RuntimeError(str(exc)) from exc
                await self._sleep_before_retry(attempt + 1)
        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error

    async def _sleep_before_retry(self, attempt_number: int) -> None:
        if self.retry_backoff_seconds <= 0:
            return
        await asyncio.sleep(self.retry_backoff_seconds * max(attempt_number, 1))

    def _build_configs(self, settings: Settings) -> dict[ModelType, ProviderEndpointConfig]:
        configs: dict[ModelType, ProviderEndpointConfig] = {}

        if settings.deepseek_api_key:
            configs[ModelType.DEEPSEEK] = ProviderEndpointConfig(
                provider_name="deepseek",
                base_url=settings.deepseek_base_url,
                api_key=settings.deepseek_api_key,
                model_name=settings.deepseek_model_name,
            )

        if settings.qwen_api_key:
            configs[ModelType.QWEN] = ProviderEndpointConfig(
                provider_name="qwen",
                base_url=settings.qwen_base_url,
                api_key=settings.qwen_api_key,
                model_name=settings.qwen_model_name,
            )

        if settings.doubao_api_key:
            configs[ModelType.DOUBAO] = ProviderEndpointConfig(
                provider_name="doubao",
                base_url=_normalize_openai_base_url(settings.doubao_base_url),
                api_key=settings.doubao_api_key,
                model_name=settings.doubao_model_name,
                path="/responses",
                api_style="responses",
            )

        if settings.azure_openai_api_key and settings.azure_openai_endpoint and settings.azure_openai_deployment:
            configs[ModelType.AZURE] = ProviderEndpointConfig(
                provider_name="azure",
                base_url=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                model_name=settings.azure_openai_deployment,
                path=f"/openai/deployments/{settings.azure_openai_deployment}/chat/completions",
                auth_header_name="api-key",
                auth_scheme=None,
                query_params={"api-version": settings.azure_openai_api_version},
            )

        if settings.openai_api_key and settings.openai_base_url:
            configs[ModelType.OPENAI] = ProviderEndpointConfig(
                provider_name="openai",
                base_url=_normalize_openai_base_url(settings.openai_base_url),
                api_key=settings.openai_api_key,
                model_name=settings.openai_model_name,
                path="/responses",
                api_style="responses",
            )

        return configs

    def _get_config(self, model_type: ModelType) -> ProviderEndpointConfig:
        config = self._configs.get(model_type)
        if config is None:
            raise RuntimeError(f"No live provider configuration found for {model_type.value}")
        return config

    def _build_headers(self, config: ProviderEndpointConfig) -> dict[str, str]:
        if config.auth_scheme:
            auth_value = f"{config.auth_scheme} {config.api_key}"
        else:
            auth_value = config.api_key
        return {
            config.auth_header_name: auth_value,
            "Content-Type": "application/json",
        }

    def _build_payload(self, model_type: ModelType, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        config = self._get_config(model_type)
        if config.api_style == "responses":
            user_content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user_prompt}]
            for image in request.input_images:
                user_content.append(
                    {
                        "type": "input_image",
                        "image_url": image.image_url,
                        "detail": image.detail,
                    }
                )
            payload: dict[str, Any] = {
                "input": [
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": request.system_prompt}],
                    },
                    {
                        "role": "user",
                        "content": user_content,
                    },
                ],
                "temperature": request.temperature,
                "max_output_tokens": request.max_tokens,
            }
            if config.model_name:
                payload["model"] = config.model_name
            response_format = self._build_response_format(model_type, request, api_style=config.api_style)
            if response_format is not None:
                payload["text"] = {"format": response_format}
            if stream:
                payload["stream"] = True
            return payload

        user_content: str | list[dict[str, Any]]
        if request.input_images:
            user_content = [{"type": "text", "text": request.user_prompt}]
            for image in request.input_images:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image.image_url,
                            "detail": image.detail,
                        },
                    }
                )
        else:
            user_content = request.user_prompt

        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        if config.provider_name != "azure" and config.model_name:
            payload["model"] = config.model_name

        response_format = self._build_response_format(model_type, request, api_style=config.api_style)
        if response_format is not None:
            payload["response_format"] = response_format

        if stream:
            payload["stream"] = True

        return payload

    def _build_response_format(
        self,
        model_type: ModelType,
        request: LLMRequest,
        *,
        api_style: str,
    ) -> dict[str, Any] | None:
        if request.json_schema is None:
            return None
        if api_style == "responses":
            strict_schema = _ensure_strict_json_schema(request.json_schema)
            return {
                "type": "json_schema",
                "name": f"{request.task_type.value}_response",
                "strict": True,
                "schema": strict_schema,
            }
        if model_type == ModelType.DEEPSEEK:
            return {"type": "json_object"}
        strict_schema = _ensure_strict_json_schema(request.json_schema)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{request.task_type.value}_response",
                "strict": True,
                "schema": strict_schema,
            },
        }

    async def _post_json(
        self,
        *,
        config: ProviderEndpointConfig,
        payload: dict[str, Any],
        model_type: ModelType | None = None,
    ) -> dict[str, Any]:
        headers = self._build_headers(config)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(
                    config.url,
                    json=payload,
                    headers=headers,
                    params=config.query_params,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                await exc.response.aread()
            except Exception:
                pass
            raise self._wrap_http_error(config.provider_name, model_type, exc) from exc
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(config.provider_name, model_type, exc) from exc

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{config.provider_name} returned a non-JSON response") from exc

    async def _collect_responses_stream(
        self,
        *,
        config: ProviderEndpointConfig,
        request: LLMRequest,
        model_type: ModelType,
        payload: dict[str, Any],
    ) -> LLMResponse:
        content_parts: list[str] = []
        response_payload: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(self.stream_timeout_seconds):
                async for event in self._iterate_responses_stream(
                    config=config,
                    payload=payload,
                    model_type=model_type,
                    yield_events=True,
                ):
                    event_type = str(event.get("type") or "")
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            content_parts.append(delta)
                    elif event_type == "response.output_text.done":
                        text = str(event.get("text") or "")
                        if text:
                            content_parts = [text]
                    elif event_type == "response.completed":
                        response_payload = event.get("response") if isinstance(event.get("response"), dict) else None
        except TimeoutError as exc:
            raise RetryableLLMError(
                self._format_stream_timeout(config.provider_name, model_type, self.stream_timeout_seconds)
            ) from exc

        content = "".join(content_parts)
        usage = (response_payload or {}).get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or _estimate_tokens(f"{request.system_prompt}\n{request.user_prompt}"))
        completion_tokens = int(usage.get("output_tokens") or _estimate_tokens(content))
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return LLMResponse(
            content=content,
            model_used=str((response_payload or {}).get("model") or config.model_name or model_type.value),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=0.0,
        )

    async def _iterate_responses_stream(
        self,
        *,
        config: ProviderEndpointConfig,
        payload: dict[str, Any],
        model_type: ModelType,
        yield_events: bool = False,
    ) -> AsyncIterator[Any]:
        headers = self._build_headers(config)
        try:
            async with asyncio.timeout(self.stream_timeout_seconds):
                async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                    async with client.stream(
                        "POST",
                        config.url,
                        json=payload,
                        headers=headers,
                        params=config.query_params,
                    ) as response:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            await exc.response.aread()
                            raise
                        async for line in response.aiter_lines():
                            data = line.strip()
                            if not data.startswith("data:"):
                                continue
                            chunk = data[5:].strip()
                            if not chunk or chunk == "[DONE]":
                                continue
                            try:
                                payload_json = json.loads(chunk)
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(f"{config.provider_name} returned invalid responses stream payload") from exc
                            if yield_events:
                                yield payload_json
                            else:
                                event_type = str(payload_json.get("type") or "")
                                if event_type == "response.output_text.delta":
                                    delta = str(payload_json.get("delta") or "")
                                    if delta:
                                        yield delta
        except TimeoutError as exc:
            raise RetryableLLMError(
                self._format_stream_timeout(config.provider_name, model_type, self.stream_timeout_seconds)
            ) from exc
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(config.provider_name, model_type, exc) from exc

    async def _iterate_chat_completions_stream(
        self,
        *,
        config: ProviderEndpointConfig,
        payload: dict[str, Any],
        model_type: ModelType,
    ) -> AsyncIterator[str]:
        headers = self._build_headers(config)
        try:
            async with asyncio.timeout(self.stream_timeout_seconds):
                async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                    async with client.stream(
                        "POST",
                        config.url,
                        json=payload,
                        headers=headers,
                        params=config.query_params,
                    ) as response:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            await exc.response.aread()
                            raise
                        async for line in response.aiter_lines():
                            data = line.strip()
                            if not data.startswith("data:"):
                                continue
                            chunk = data[5:].strip()
                            if not chunk:
                                continue
                            if chunk == "[DONE]":
                                return
                            try:
                                payload_json = json.loads(chunk)
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(
                                    f"{config.provider_name} returned invalid stream payload for {model_type.value}"
                                ) from exc
                            for delta in self._extract_stream_deltas(payload_json):
                                if delta:
                                    yield delta
        except TimeoutError as exc:
            raise RetryableLLMError(
                self._format_stream_timeout(config.provider_name, model_type, self.stream_timeout_seconds)
            ) from exc
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(config.provider_name, model_type, exc) from exc

    def _parse_completion_response(
        self,
        *,
        model_type: ModelType,
        request: LLMRequest,
        data: dict[str, Any],
    ) -> LLMResponse:
        config = self._get_config(model_type)
        if config.api_style == "responses":
            content = _extract_responses_output_text(data)
            usage = data.get("usage") or {}
            prompt_tokens = int(
                usage.get("input_tokens") or _estimate_tokens(f"{request.system_prompt}\n{request.user_prompt}")
            )
            completion_tokens = int(usage.get("output_tokens") or _estimate_tokens(content))
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            return LLMResponse(
                content=content,
                model_used=str(data.get("model") or config.model_name or model_type.value),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_estimate=0.0,
            )

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{model_type.value} returned no completion choices")

        choice = choices[0]
        message = choice.get("message") or {}
        content = _normalize_content(message.get("content"))
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or _estimate_tokens(f"{request.system_prompt}\n{request.user_prompt}"))
        completion_tokens = int(usage.get("completion_tokens") or _estimate_tokens(content))
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return LLMResponse(
            content=content,
            model_used=str(data.get("model") or self._get_config(model_type).model_name or model_type.value),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate=0.0,
        )

    @staticmethod
    def _extract_stream_deltas(payload: dict[str, Any]) -> list[str]:
        deltas: list[str] = []
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            content = _normalize_content(delta.get("content"))
            if content:
                deltas.append(content)
        return deltas

    @staticmethod
    def _format_http_error(provider_name: str, model_type: ModelType | None, exc: httpx.HTTPError) -> str:
        label = model_type.value if model_type else provider_name
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = exc.response.text.strip()
            except Exception:
                body = "<streaming response body unavailable>"
            if len(body) > 200:
                body = f"{body[:200]}..."
            return f"{provider_name} request failed for {label}: {exc.response.status_code} {body}"
        return f"{provider_name} request failed for {label}: {exc}"

    @staticmethod
    def _format_stream_timeout(
        provider_name: str,
        model_type: ModelType | None,
        timeout_seconds: float,
    ) -> str:
        label = model_type.value if model_type else provider_name
        return f"{provider_name} stream timed out for {label} after {timeout_seconds:.1f}s"

    @classmethod
    def _wrap_http_error(
        cls,
        provider_name: str,
        model_type: ModelType | None,
        exc: httpx.HTTPError,
    ) -> RuntimeError:
        message = cls._format_http_error(provider_name, model_type, exc)
        if cls._is_retryable_http_error(exc):
            return RetryableLLMError(message)
        return RuntimeError(message)

    @staticmethod
    def _is_retryable_http_error(exc: httpx.HTTPError) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = int(exc.response.status_code)
            return status_code in {408, 409, 425, 429} or status_code >= 500
        return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def get_default_provider(
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
) -> BaseLLMProvider:
    resolved_settings = settings or get_settings()
    if resolved_settings.llm_provider_backend == "live":
        return HTTPChatCompletionsProvider(settings=resolved_settings, transport=transport)
    return MockLLMProvider(chunk_size=resolved_settings.llm_mock_stream_chunk_size)


class _GatewayStreamRestorer:
    def __init__(self, *, gateway_client: GatewayClient, session_id: str) -> None:
        self.gateway_client = gateway_client
        self.session_id = session_id
        self._buffer = ""

    async def push(self, chunk: str) -> str:
        text = self._buffer + chunk
        safe_text, self._buffer = self._split_safe_prefix(text)
        if not safe_text:
            return ""
        restored = await self.gateway_client.restore_text(session_id=self.session_id, text=safe_text)
        return restored.restored_text

    async def flush(self) -> str:
        if not self._buffer:
            return ""
        restored = await self.gateway_client.restore_text(session_id=self.session_id, text=self._buffer)
        self._buffer = ""
        return restored.restored_text

    @staticmethod
    def _split_safe_prefix(text: str) -> tuple[str, str]:
        last_open = text.rfind("[")
        last_close = text.rfind("]")
        if last_open == -1 or last_close > last_open:
            return text, ""
        return text[:last_open], text[last_open:]


class LLMClient:
    def __init__(
        self,
        *,
        gateway_client: GatewayClient | None = None,
        provider: BaseLLMProvider | None = None,
    ) -> None:
        settings = get_settings()
        self.gateway_client = gateway_client or GatewayClient(timeout_seconds=settings.llm_timeout_seconds)
        self.provider = provider or get_default_provider(settings=settings)

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        session_id = request.session_id or str(uuid.uuid4())
        masked_request = await self._mask_request(request, session_id=session_id)
        candidates = self._supported_candidates(request.task_type)
        last_error: Exception | None = None

        for model_type in candidates:
            try:
                response = await self.provider.invoke(model_type, masked_request)
                restored = await self.gateway_client.restore_text(
                    session_id=session_id,
                    text=response.content,
                )
                return replace(response, content=restored.restored_text)
            except Exception as exc:
                last_error = exc

        raise RuntimeError(f"LLM invocation failed for task {request.task_type.value}") from last_error

    async def invoke_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        session_id = request.session_id or str(uuid.uuid4())
        masked_request = await self._mask_request(request, session_id=session_id)
        candidates = self._supported_candidates(request.task_type)
        last_error: Exception | None = None

        for model_type in candidates:
            restorer = _GatewayStreamRestorer(gateway_client=self.gateway_client, session_id=session_id)
            try:
                async for chunk in self.provider.invoke_stream(model_type, masked_request):
                    restored_text = await restorer.push(chunk)
                    if restored_text:
                        yield restored_text
                flushed = await restorer.flush()
                if flushed:
                    yield flushed
                return
            except Exception as exc:
                last_error = exc

        raise RuntimeError(f"LLM stream invocation failed for task {request.task_type.value}") from last_error

    async def _mask_request(self, request: LLMRequest, *, session_id: str) -> LLMRequest:
        masked = await self.gateway_client.mask_text(
            session_id=session_id,
            text=request.user_prompt,
            entity_types=request.entity_types,
        )
        return replace(request, user_prompt=masked.masked_text, session_id=session_id)

    def _supported_candidates(self, task_type: TaskType) -> list[ModelType]:
        candidates = [
            model_type
            for model_type in self._model_candidates(task_type)
            if self.provider.supports_model(model_type)
        ]
        if candidates:
            return candidates
        return self._model_candidates(task_type)

    @staticmethod
    def _model_candidates(task_type: TaskType) -> list[ModelType]:
        primary = ROUTING_TABLE.get(task_type, ModelType.QWEN)
        fallback = FALLBACK_TABLE.get(primary)
        ordered = [primary]
        if fallback and fallback != primary:
            ordered.append(fallback)
        if ModelType.DOUBAO not in ordered:
            ordered.append(ModelType.DOUBAO)
        if ModelType.AZURE not in ordered:
            ordered.append(ModelType.AZURE)
        if ModelType.OPENAI not in ordered:
            ordered.append(ModelType.OPENAI)
        return ordered


def _normalize_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(value)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _normalize_openai_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    elif path.endswith("/responses"):
        path = path[: -len("/responses")]
    if not path:
        path = "/v1"
    normalized = parsed._replace(path=path)
    return urlunparse(normalized).rstrip("/")


def _extract_responses_output_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    if isinstance(output_text, list):
        parts = [str(item) for item in output_text if str(item).strip()]
        if parts:
            return "".join(parts)

    parts: list[str] = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    if parts:
        return "".join(parts)
    raise RuntimeError("openai returned no output_text content")


def _ensure_strict_json_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_ensure_strict_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            normalized[key] = {name: _ensure_strict_json_schema(child) for name, child in value.items()}
        elif key == "items":
            normalized[key] = _ensure_strict_json_schema(value)
        elif key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
            normalized[key] = [_ensure_strict_json_schema(item) for item in value]
        elif isinstance(value, (dict, list)):
            normalized[key] = _ensure_strict_json_schema(value)
        else:
            normalized[key] = value

    schema_type = normalized.get("type")
    if schema_type == "object":
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            property_names = list(properties.keys())
            normalized["required"] = property_names
        elif "required" not in normalized:
            normalized["required"] = []
        if "additionalProperties" not in normalized:
            normalized["additionalProperties"] = False

    return normalized
