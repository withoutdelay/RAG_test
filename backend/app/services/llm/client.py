from __future__ import annotations

import json
import math
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services.gateway_client import GatewayClient


class ModelType(str, Enum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    AZURE = "azure"
    OPENAI = "openai"


class TaskType(str, Enum):
    EXTRACTION = "extraction"
    OUTLINE = "outline"
    SECTION_WRITE = "section_write"
    HOLISTIC = "holistic"
    REWRITE = "rewrite"
    QUESTION_GEN = "question_gen"


ROUTING_TABLE = {
    TaskType.EXTRACTION: ModelType.DEEPSEEK,
    TaskType.OUTLINE: ModelType.DEEPSEEK,
    TaskType.SECTION_WRITE: ModelType.QWEN,
    TaskType.HOLISTIC: ModelType.QWEN,
    TaskType.REWRITE: ModelType.QWEN,
    TaskType.QUESTION_GEN: ModelType.DEEPSEEK,
}

FALLBACK_TABLE = {
    ModelType.DEEPSEEK: ModelType.QWEN,
    ModelType.QWEN: ModelType.DEEPSEEK,
    ModelType.AZURE: ModelType.QWEN,
    ModelType.OPENAI: ModelType.QWEN,
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
    metadata: dict[str, Any] = field(default_factory=dict)


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
    auth_header_name: str = "Authorization"
    auth_scheme: str | None = "Bearer"
    query_params: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path}"


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


class HTTPChatCompletionsProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout_seconds = self.settings.llm_timeout_seconds
        self.transport = transport
        self._configs = self._build_configs(self.settings)

    def supports_model(self, model_type: ModelType) -> bool:
        config = self._configs.get(model_type)
        return config is not None and bool(config.api_key and config.base_url)

    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        config = self._get_config(model_type)
        payload = self._build_payload(model_type, request, stream=False)
        response_json = await self._post_json(config=config, payload=payload)
        return self._parse_completion_response(model_type=model_type, request=request, data=response_json)

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest) -> AsyncIterator[str]:
        config = self._get_config(model_type)
        payload = self._build_payload(model_type, request, stream=True)
        headers = self._build_headers(config)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                async with client.stream(
                    "POST",
                    config.url,
                    json=payload,
                    headers=headers,
                    params=config.query_params,
                ) as response:
                    response.raise_for_status()
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
        except httpx.HTTPError as exc:
            raise RuntimeError(self._format_http_error(config.provider_name, model_type, exc)) from exc

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
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                model_name=settings.openai_model_name,
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
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        config = self._get_config(model_type)
        if config.provider_name != "azure" and config.model_name:
            payload["model"] = config.model_name

        response_format = self._build_response_format(model_type, request)
        if response_format is not None:
            payload["response_format"] = response_format

        if stream:
            payload["stream"] = True

        return payload

    def _build_response_format(self, model_type: ModelType, request: LLMRequest) -> dict[str, Any] | None:
        if request.json_schema is None:
            return None
        if model_type == ModelType.DEEPSEEK:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{request.task_type.value}_response",
                "strict": True,
                "schema": request.json_schema,
            },
        }

    async def _post_json(self, *, config: ProviderEndpointConfig, payload: dict[str, Any]) -> dict[str, Any]:
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
        except httpx.HTTPError as exc:
            raise RuntimeError(self._format_http_error(config.provider_name, None, exc)) from exc

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{config.provider_name} returned a non-JSON response") from exc

    def _parse_completion_response(
        self,
        *,
        model_type: ModelType,
        request: LLMRequest,
        data: dict[str, Any],
    ) -> LLMResponse:
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
            body = exc.response.text.strip()
            if len(body) > 200:
                body = f"{body[:200]}..."
            return f"{provider_name} request failed for {label}: {exc.response.status_code} {body}"
        return f"{provider_name} request failed for {label}: {exc}"


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
