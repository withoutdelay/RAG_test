from __future__ import annotations

import asyncio
import json
import math
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
    VISION = "vision"


class TaskType(str, Enum):
    EXTRACTION = "extraction"
    KNOWLEDGE_COMPILE = "knowledge_compile"
    EVIDENCE_SELECT = "evidence_select"
    ASSET_REVIEW = "asset_review"
    ASSET_SUMMARY = "asset_summary"
    ASSET_RERANK = "asset_rerank"
    SECTION_QUALITY = "section_quality"
    OUTLINE = "outline"
    SECTION_WRITE = "section_write"
    EVIDENCE_JUDGE = "evidence_judge"
    HOLISTIC = "holistic"
    REWRITE = "rewrite"
    QUESTION_GEN = "question_gen"


ROUTING_TABLE = {
    TaskType.EXTRACTION: ModelType.DEEPSEEK,
    TaskType.KNOWLEDGE_COMPILE: ModelType.DEEPSEEK,
    TaskType.EVIDENCE_SELECT: ModelType.DOUBAO,
    TaskType.ASSET_REVIEW: ModelType.DOUBAO,
    TaskType.ASSET_SUMMARY: ModelType.DOUBAO,
    TaskType.ASSET_RERANK: ModelType.DOUBAO,
    TaskType.SECTION_QUALITY: ModelType.DOUBAO,
    TaskType.OUTLINE: ModelType.DOUBAO,
    TaskType.SECTION_WRITE: ModelType.DOUBAO,
    TaskType.EVIDENCE_JUDGE: ModelType.DOUBAO,
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
    ModelType.VISION: ModelType.OPENAI,
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
        ModelType.VISION: 0.000006,
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
        if request.task_type == TaskType.EVIDENCE_JUDGE:
            return self._render_evidence_judge(request)
        if request.task_type == TaskType.HOLISTIC:
            return self._render_holistic(request)
        if request.task_type == TaskType.REWRITE:
            return self._render_rewrite(model_type, request)
        if request.task_type == TaskType.EXTRACTION:
            return json.dumps({"summary": request.user_prompt[:120]}, ensure_ascii=False)
        if request.task_type == TaskType.KNOWLEDGE_COMPILE:
            return self._render_knowledge_compile(request)
        if request.task_type == TaskType.EVIDENCE_SELECT:
            return self._render_evidence_select(request)
        if request.task_type == TaskType.ASSET_REVIEW:
            return self._render_asset_review(request)
        if request.task_type == TaskType.ASSET_SUMMARY:
            return self._render_asset_summary(request)
        if request.task_type == TaskType.ASSET_RERANK:
            return self._render_asset_rerank(request)
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
        description = str(section.get("description") or "")
        context = str(request.metadata.get("retrieved_context") or "暂无补充资料。")
        prompt_excerpt = request.user_prompt.splitlines()[0] if request.user_prompt else title
        return (
            f"## {title}\n\n"
            f"{description or '本章节围绕项目目标、范围与实施要点进行说明。'}\n\n"
            f"本节基于{model_type.value.upper()}模型草拟，重点覆盖：{prompt_excerpt}。\n\n"
            f"参考摘要：{context[:180]}\n"
        )

    def _render_evidence_judge(self, request: LLMRequest) -> str:
        candidates = request.metadata.get("candidates") or []
        items: list[dict[str, Any]] = []
        noise_tokens = (
            "培训",
            "售后",
            "维保",
            "经营",
            "运营",
            "实施进度",
            "进度计划",
            "节能效益",
            "所有权",
        )
        for item in candidates:
            candidate_id = str(item.get("candidate_id") or "")
            text = "\n".join(
                str(item.get(key) or "")
                for key in ("heading_path", "source_heading", "section_type", "excerpt")
            )
            is_noise = any(token in text for token in noise_tokens)
            items.append(
                {
                    "candidate_id": candidate_id,
                    "decision": "noise" if is_noise else "core",
                    "confidence": 0.86 if is_noise else 0.72,
                    "reason": "mock evidence judge",
                }
            )
        return json.dumps({"summary": "mock evidence judge completed", "items": items}, ensure_ascii=False)

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

    def _render_knowledge_compile(self, request: LLMRequest) -> str:
        seed_items = request.metadata.get("seed_items") or []
        asset_candidates = request.metadata.get("asset_candidates") or []
        items: list[dict[str, Any]] = []
        for seed in seed_items:
            if not isinstance(seed, dict):
                continue
            item_type = str(seed.get("item_type") or "")
            if item_type not in {"product_family", "section_template", "term_alias"}:
                continue
            evidence = [item for item in (seed.get("evidence") or []) if isinstance(item, dict)]
            if not evidence:
                continue
            items.append(
                {
                    "item_type": item_type,
                    "canonical_name": str(seed.get("canonical_name") or ""),
                    "aliases": [str(item) for item in (seed.get("aliases") or []) if str(item)],
                    "summary": str(seed.get("summary") or "mock knowledge compiler candidate"),
                    "source_documents": [
                        str(item) for item in (seed.get("source_documents") or []) if str(item)
                    ],
                    "evidence": evidence[:3],
                    "confidence": 0.78,
                    "reason": "mock knowledge compiler summarized a deterministic seed item",
                    "asset_type_label": "",
                }
            )
            if len(items) >= 2:
                break
        for asset in asset_candidates:
            if not isinstance(asset, dict):
                continue
            evidence = asset.get("evidence")
            if not isinstance(evidence, dict):
                continue
            label = str(asset.get("visual_role") or "unknown")
            items.append(
                {
                    "item_type": "asset_type_rule",
                    "canonical_name": str(asset.get("title") or asset.get("heading_path") or label),
                    "aliases": [label] if label else [],
                    "summary": "mock image audit candidate based on asset metadata",
                    "source_documents": [
                        str(evidence.get("source_document") or "") or str(asset.get("source_document") or "")
                    ],
                    "evidence": [evidence],
                    "confidence": 0.64,
                    "reason": "mock visual audit used asset metadata only",
                    "asset_type_label": label,
                }
            )
            break
        return json.dumps(
            {"status": "ok" if items else "insufficient_evidence", "items": items},
            ensure_ascii=False,
        )

    def _render_evidence_select(self, request: LLMRequest) -> str:
        candidates = request.metadata.get("candidates") if isinstance(request.metadata, dict) else {}
        sections = [item for item in (candidates or {}).get("sections", []) if isinstance(item, dict)]
        blocks = [item for item in (candidates or {}).get("blocks", []) if isinstance(item, dict)]
        assets = [item for item in (candidates or {}).get("assets", []) if isinstance(item, dict)]

        def _selected(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
            return [
                {
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "confidence": 0.78,
                    "reason": "mock evidence selector kept the candidate",
                }
                for item in items[:limit]
                if str(item.get("candidate_id") or "")
            ]

        return json.dumps(
            {
                "selected_sections": _selected(sections, limit=4),
                "selected_blocks": _selected(blocks, limit=6),
                "selected_assets": _selected(assets, limit=3),
                "rejected_candidates": [],
                "selection_reason": "mock evidence selector kept deterministic candidates",
                "risk_flags": [],
                "confidence": 0.78,
            },
            ensure_ascii=False,
        )

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

    def _render_asset_rerank(self, request: LLMRequest) -> str:
        candidates = request.metadata.get("candidates") or []
        items: list[dict[str, Any]] = []
        for item in candidates:
            asset_id = str(item.get("asset_id") or "")
            visual_role = str(item.get("visual_role") or "engineering_figure")
            score = float(item.get("score") or 0.0)
            review_required = bool(item.get("review_required"))
            items.append(
                {
                    "asset_id": asset_id,
                    "visual_relevance": max(0.0, min(1.0, score if score else 0.72)),
                    "confidence": 0.72 if not review_required else 0.58,
                    "visual_role": visual_role,
                    "should_recommend": not review_required,
                    "reason": "mock runtime asset rerank",
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
            payload = self._build_payload_for_config(config, model_type, request, stream=True)
            try:
                return await self._invoke_with_retry(
                    operation=lambda: self._collect_responses_stream(
                        config=config,
                        request=request,
                        model_type=model_type,
                        payload=payload,
                    ),
                )
            except RuntimeError as exc:
                fallback_config = self._responses_chat_fallback_config(config=config, model_type=model_type)
                if fallback_config is None:
                    raise
                fallback_payload = self._build_payload_for_config(fallback_config, model_type, request, stream=False)
                try:
                    return await self._invoke_with_retry(
                        operation=lambda: self._invoke_chat_completion(
                            config=fallback_config,
                            request=request,
                            model_type=model_type,
                            payload=fallback_payload,
                        ),
                    )
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"{exc}; chat completions fallback also failed: {fallback_exc}"
                    ) from fallback_exc
        payload = self._build_payload_for_config(config, model_type, request, stream=False)
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
            payload = self._build_payload_for_config(config, model_type, request, stream=True)
            try:
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
            except RuntimeError:
                fallback_config = self._responses_chat_fallback_config(config=config, model_type=model_type)
                if fallback_config is None:
                    raise
                fallback_payload = self._build_payload_for_config(fallback_config, model_type, request, stream=True)
                async for delta in self._iterate_with_retry(
                    operation_factory=lambda: self._iterate_chat_completions_stream(
                        config=fallback_config,
                        payload=fallback_payload,
                        model_type=model_type,
                    ),
                ):
                    if delta:
                        yield delta
            return
        payload = self._build_payload_for_config(config, model_type, request, stream=True)
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
            openai_api_style = _resolve_openai_api_style(
                api_style=settings.openai_api_style,
                base_url=settings.openai_base_url,
            )
            configs[ModelType.OPENAI] = ProviderEndpointConfig(
                provider_name="openai",
                base_url=_normalize_openai_base_url(settings.openai_base_url),
                api_key=settings.openai_api_key,
                model_name=settings.openai_model_name,
                path=_endpoint_path_for_api_style(openai_api_style),
                api_style=openai_api_style,
            )

        vision_base_url = settings.vision_llm_base_url
        if settings.vision_llm_api_key and vision_base_url:
            vision_api_style = _resolve_openai_api_style(
                api_style=settings.vision_llm_api_style,
                base_url=vision_base_url,
            )
            configs[ModelType.VISION] = ProviderEndpointConfig(
                provider_name="vision_llm",
                base_url=_normalize_openai_base_url(vision_base_url),
                api_key=settings.vision_llm_api_key,
                model_name=settings.vision_llm_model_name,
                path=_endpoint_path_for_api_style(vision_api_style),
                api_style=vision_api_style,
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
        return self._build_payload_for_config(config, model_type, request, stream=stream)

    def _build_payload_for_config(
        self,
        config: ProviderEndpointConfig,
        model_type: ModelType,
        request: LLMRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
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

    def _responses_chat_fallback_config(
        self,
        *,
        config: ProviderEndpointConfig,
        model_type: ModelType,
    ) -> ProviderEndpointConfig | None:
        if model_type == ModelType.OPENAI:
            configured_style = self.settings.openai_api_style
        elif model_type == ModelType.VISION:
            configured_style = self.settings.vision_llm_api_style
        else:
            return None
        if config.api_style != "responses":
            return None
        if str(configured_style or "auto").strip().lower() != "auto":
            return None
        return replace(config, path="/chat/completions", api_style="chat_completions")

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
        candidates = self._supported_candidates(request)
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
        candidates = self._supported_candidates(request)
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

    def _supported_candidates(self, request: LLMRequest) -> list[ModelType]:
        candidates = [
            model_type
            for model_type in self._model_candidates(request)
            if self.provider.supports_model(model_type)
        ]
        if candidates:
            return candidates
        return self._model_candidates(request)

    @staticmethod
    def _model_candidates(request: LLMRequest) -> list[ModelType]:
        task_type = request.task_type
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
        if request.input_images and task_type in {
            TaskType.ASSET_REVIEW,
            TaskType.ASSET_SUMMARY,
            TaskType.ASSET_RERANK,
            TaskType.KNOWLEDGE_COMPILE,
        }:
            ordered = [ModelType.VISION, *[model_type for model_type in ordered if model_type != ModelType.VISION]]
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


def _resolve_openai_api_style(*, api_style: str, base_url: str) -> str:
    normalized_style = str(api_style or "auto").strip().lower()
    if normalized_style in {"responses", "chat_completions"}:
        return normalized_style
    return "responses" if _is_official_openai_base_url(base_url) else "chat_completions"


def _is_official_openai_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    return host == "api.openai.com"


def _endpoint_path_for_api_style(api_style: str) -> str:
    return "/responses" if api_style == "responses" else "/chat/completions"


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
