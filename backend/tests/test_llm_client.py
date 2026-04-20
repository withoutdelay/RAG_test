from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.config import get_settings
from app.services.gateway_client import GatewayClient
from app.services.llm.client import (
    BaseLLMProvider,
    HTTPChatCompletionsProvider,
    LLMClient,
    LLMInputImage,
    LLMRequest,
    LLMResponse,
    ModelType,
    MockLLMProvider,
    RetryableLLMError,
    TaskType,
)


gateway_app = FastAPI()


@gateway_app.post("/mask")
async def fake_mask(payload: dict) -> dict:
    return {
        "code": 200,
        "message": "success",
        "data": {
            "masked_text": payload["text"].replace("上海电气集团", "[Company_A]"),
            "entity_count": 1,
            "entities_detected": [
                {
                    "original": "上海电气集团",
                    "placeholder": "[Company_A]",
                    "type": "COMPANY",
                    "start": 0,
                    "end": 6,
                }
            ],
        },
    }


@gateway_app.post("/restore")
async def fake_restore(payload: dict) -> dict:
    return {
        "code": 200,
        "message": "success",
        "data": {
            "restored_text": payload["text"].replace("[Company_A]", "上海电气集团"),
            "restored_count": payload["text"].count("[Company_A]"),
        },
    }


class _FallbackProvider(BaseLLMProvider):
    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        if model_type == ModelType.DEEPSEEK:
            raise RuntimeError("primary failed")
        return LLMResponse(
            content="根据分析，[Company_A]方案已生成。",
            model_used=f"{model_type.value}:fallback-test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_estimate=0.0015,
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest):
        if model_type == ModelType.DEEPSEEK:
            raise RuntimeError("primary failed")
        for chunk in ["根据分析，[Com", "pany_A]需要", "新的实施方案。"]:
            yield chunk


class _HangingStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        await asyncio.sleep(0.05)
        if False:
            yield b""

    async def aclose(self) -> None:
        return None


class LLMClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._gateway_masking_patch = patch.dict(os.environ, {"GATEWAY_MASKING_ENABLED": "true"}, clear=False)
        self._gateway_masking_patch.start()
        get_settings.cache_clear()

    def _make_client(self, provider: BaseLLMProvider) -> LLMClient:
        transport = httpx.ASGITransport(app=gateway_app)
        gateway_client = GatewayClient(base_url="http://gateway.test", transport=transport)
        return LLMClient(gateway_client=gateway_client, provider=provider)

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self._gateway_masking_patch.stop()

    def test_invoke_falls_back_and_restores_masked_entities(self) -> None:
        client = self._make_client(_FallbackProvider())

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.EXTRACTION,
                    session_id="llm-test-session",
                    system_prompt="system",
                    user_prompt="请为上海电气集团生成技术方案。",
                )
            )
        )

        self.assertEqual(response.content, "根据分析，上海电气集团方案已生成。")
        self.assertEqual(response.model_used, "qwen:fallback-test")

    def test_invoke_stream_restores_split_placeholders(self) -> None:
        client = self._make_client(_FallbackProvider())

        async def collect() -> str:
            chunks: list[str] = []
            async for chunk in client.invoke_stream(
                LLMRequest(
                    task_type=TaskType.EXTRACTION,
                    session_id="stream-session",
                    system_prompt="system",
                    user_prompt="请为上海电气集团生成实施章节。",
                )
            ):
                chunks.append(chunk)
            return "".join(chunks)

        content = asyncio.run(collect())
        self.assertEqual(content, "根据分析，上海电气集团需要新的实施方案。")

    def test_mock_provider_routes_section_write_to_doubao(self) -> None:
        client = self._make_client(MockLLMProvider(chunk_size=12))

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_WRITE,
                    session_id="route-session",
                    system_prompt="system",
                    user_prompt="请撰写上海电气集团项目概述。",
                    metadata={"section": {"title": "项目概述", "description": "介绍项目背景。"}},
                )
            )
        )

        self.assertEqual(response.model_used, "doubao:mock")
        self.assertIn("项目概述", response.content)

    def test_mock_provider_section_write_uses_retrieved_context_without_outline_description(self) -> None:
        client = self._make_client(MockLLMProvider(chunk_size=12))

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_WRITE,
                    session_id="context-session",
                    system_prompt="system",
                    user_prompt="请撰写需求分析。",
                    metadata={
                        "section": {"title": "需求分析", "description": "梳理客户核心需求、约束条件与关键指标。"},
                        "retrieved_context": (
                            "- 历史方案A 需求分析: 系统需支持 10kV / 4500kW 同步电机软起动，并与 DCS 保持联锁一致。\n"
                            "- 历史方案B 约束条件: 旁路切换需保留原高压开关柜接口边界。"
                        ),
                    },
                )
            )
        )

        self.assertIn("### 关键信息提炼", response.content)
        self.assertIn("系统需支持 10kV / 4500kW 同步电机软起动", response.content)
        self.assertNotIn("梳理客户核心需求、约束条件与关键指标。", response.content)

    def test_mock_provider_section_write_prefers_assembled_draft(self) -> None:
        client = self._make_client(MockLLMProvider(chunk_size=12))

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_WRITE,
                    session_id="assembled-draft-session",
                    system_prompt="system",
                    user_prompt="请整理成正式客户稿。",
                    metadata={
                        "section": {"title": "技术架构", "description": "说明系统总体架构、模块划分与接口关系。"},
                        "assembled_draft": (
                            "## 技术架构\n\n"
                            "### 系统组成\n\n"
                            "主回路由 LCI 软起动装置、整流变压器、励磁控制柜和旁路切换柜组成。\n"
                        ),
                    },
                )
            )
        )

        self.assertIn("### 系统组成", response.content)
        self.assertIn("LCI 软起动装置", response.content)
        self.assertNotIn("说明系统总体架构、模块划分与接口关系。", response.content)

    def test_mock_provider_section_write_skips_unreadable_reuse_blocks(self) -> None:
        client = self._make_client(MockLLMProvider(chunk_size=12))

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_WRITE,
                    session_id="unreadable-reuse-session",
                    system_prompt="system",
                    user_prompt="请撰写需求分析。",
                    metadata={
                        "section": {"title": "需求分析", "description": "梳理客户核心需求、约束条件与关键指标。"},
                        "reuse_pack": {
                            "reusable_blocks": [
                                {
                                    "source_heading": "¤¤¤ ／／ ###",
                                    "content_md": "## 乱码块\n\n¤¤¤ ／／ ■■ …… ——",
                                }
                            ]
                        },
                        "retrieved_context": "- 案例来源：历史方案A.docx\n- 系统需支持 10kV / 4500kW 同步电机软起动，并保留 DCS 联锁接口。",
                    },
                )
            )
        )

        self.assertIn("### 关键信息提炼", response.content)
        self.assertIn("10kV / 4500kW 同步电机软起动", response.content)
        self.assertNotIn("¤¤¤", response.content)
        self.assertNotIn("梳理客户核心需求、约束条件与关键指标。", response.content)

    def test_mock_provider_baseline_section_write_prefers_structured_global_params_over_reuse_blocks(self) -> None:
        client = self._make_client(MockLLMProvider(chunk_size=12))

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_WRITE,
                    session_id="baseline-structured-session",
                    system_prompt="system",
                    user_prompt="请撰写项目概述。",
                    metadata={
                        "section": {
                            "title": "项目概述",
                            "description": "介绍项目背景、建设目标与总体范围。",
                            "generation_mode": "baseline",
                            "section_class": "overview",
                        },
                        "global_params": {
                            "solution_summary": "本项目方案围绕 LCI 同步电机变频软起动系统组织主回路、接口与供货配置。",
                            "selected_products": "主驱动:LCI 同步电机变频软起动系统 x1；整流变压器:整流变压器 x1",
                            "primary_model_number": "GBT.LCI.SO-A0606-211N465",
                            "catalog_interface_summary": "LCI 同步电机变频软起动系统通讯接口采用 Profibus-DP，接口范围覆盖 LCI 主驱动、高压开关柜、润滑油站与冷却系统",
                        },
                        "reuse_pack": {
                            "reusable_blocks": [
                                {
                                    "source_heading": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                                    "content_md": "变频器已配置的选项 Converter Selected Options...",
                                }
                            ]
                        },
                        "retrieved_context": "- 宝山钢铁股份有限公司三鼓风LCI改造方案.docx 项目概述: 变频器已配置的选项 Converter Selected Options...",
                    },
                )
            )
        )

        self.assertIn("### 项目背景与方案范围", response.content)
        self.assertIn("本项目方案围绕 LCI 同步电机变频软起动系统组织主回路、接口与供货配置。", response.content)
        self.assertIn("GBT.LCI.SO-A0606-211N465", response.content)
        self.assertNotIn("Converter Selected Options", response.content)

    def test_live_provider_falls_back_from_deepseek_to_qwen_for_extraction(self) -> None:
        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            if request.url.host == "deepseek.test":
                seen_hosts.append("deepseek")
                self.assertEqual(request.url.path, "/v1/chat/completions")
                self.assertEqual(request.headers["Authorization"], "Bearer deepseek-key")
                self.assertEqual(payload["model"], "deepseek-chat")
                self.assertEqual(payload["response_format"]["type"], "json_object")
                return httpx.Response(500, json={"error": "deepseek down"})

            if request.url.host == "dashscope.test":
                seen_hosts.append("qwen")
                self.assertEqual(request.url.path, "/compatible-mode/v1/chat/completions")
                self.assertEqual(request.headers["Authorization"], "Bearer qwen-key")
                self.assertEqual(payload["model"], "qwen-plus")
                self.assertEqual(payload["response_format"]["type"], "json_schema")
                return httpx.Response(
                    200,
                    json={
                        "id": "chatcmpl-qwen",
                        "model": "qwen-plus",
                        "choices": [{"message": {"role": "assistant", "content": '{"company":"[Company_A]"}'}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
                    },
                )

            return httpx.Response(404, json={"error": "not found"})

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.test/v1",
                "DEEPSEEK_MODEL": "deepseek-chat",
                "QWEN_API_KEY": "qwen-key",
                "QWEN_BASE_URL": "https://dashscope.test/compatible-mode/v1",
                "QWEN_MODEL": "qwen-plus",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            client = self._make_client(provider)
            response = asyncio.run(
                client.invoke(
                    LLMRequest(
                        task_type=TaskType.EXTRACTION,
                        session_id="live-fallback-session",
                        system_prompt="请输出 JSON。",
                        user_prompt="请为上海电气集团生成结构化摘要。",
                        json_schema={
                            "type": "object",
                            "properties": {"company": {"type": "string"}},
                            "required": ["company"],
                        },
                    )
                )
            )

        self.assertEqual(seen_hosts, ["deepseek", "deepseek", "qwen"])
        self.assertEqual(response.content, '{"company":"上海电气集团"}')
        self.assertEqual(response.model_used, "qwen-plus")

    def test_live_client_uses_doubao_responses_endpoint_for_generation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            self.assertEqual(request.url.host, "ark.test")
            self.assertEqual(request.url.path, "/api/v3/responses")
            self.assertEqual(request.headers["Authorization"], "Bearer doubao-key")
            self.assertEqual(payload["model"], "doubao-model")
            self.assertTrue(payload["stream"])
            result_text = '{"title":"豆包大纲","sections":[{"index":1,"title":"项目概述","subsections":[{"index":1,"title":"背景"}]}]}'
            stream_body = (
                "event: response.created\n"
                f"data: {json.dumps({'type': 'response.created', 'response': {'id': 'resp-doubao', 'model': 'doubao-model'}}, ensure_ascii=False)}\n\n"
                "event: response.output_text.delta\n"
                f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': result_text}, ensure_ascii=False)}\n\n"
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': {'id': 'resp-doubao', 'model': 'doubao-model', 'usage': {'input_tokens': 18, 'output_tokens': 7, 'total_tokens': 25}}}, ensure_ascii=False)}\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_body)

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "",
                "QWEN_API_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
                "DOUBAO_API_KEY": "doubao-key",
                "DOUBAO_BASE_URL": "https://ark.test/api/v3",
                "DOUBAO_MODEL": "doubao-model",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            client = self._make_client(provider)
            response = asyncio.run(
                client.invoke(
                    LLMRequest(
                        task_type=TaskType.OUTLINE,
                        session_id="doubao-outline-session",
                        system_prompt="请输出 JSON。",
                        user_prompt="请为上海电气集团生成结构化摘要。",
                        json_schema={
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "sections": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "index": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "subsections": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "index": {"type": "integer"},
                                                        "title": {"type": "string"},
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            "required": ["title", "sections"],
                        },
                    )
                )
            )

        self.assertEqual(
            response.content,
            '{"title":"豆包大纲","sections":[{"index":1,"title":"项目概述","subsections":[{"index":1,"title":"背景"}]}]}',
        )
        self.assertEqual(response.model_used, "doubao-model")

    def test_live_provider_calls_azure_chat_completions_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            self.assertEqual(request.url.host, "azure.test")
            self.assertEqual(request.url.path, "/openai/deployments/gpt-4o-mini/chat/completions")
            self.assertEqual(request.url.params["api-version"], "2024-10-21")
            self.assertEqual(request.headers["api-key"], "azure-key")
            self.assertNotIn("model", payload)
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-azure",
                    "model": "gpt-4o-mini",
                    "choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
                },
            )

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "AZURE_OPENAI_API_KEY": "azure-key",
                "AZURE_OPENAI_ENDPOINT": "https://azure.test",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini",
                "AZURE_OPENAI_API_VERSION": "2024-10-21",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            response = asyncio.run(
                provider.invoke(
                    ModelType.AZURE,
                    LLMRequest(
                        task_type=TaskType.OUTLINE,
                        system_prompt="请输出 JSON。",
                        user_prompt="请返回结构化结果。",
                        json_schema={
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                        },
                    ),
                )
            )

        self.assertEqual(response.content, '{"ok":true}')
        self.assertEqual(response.model_used, "gpt-4o-mini")

    def test_live_client_uses_openai_compatible_endpoint_when_only_openai_configured(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            self.assertEqual(request.url.host, "relay.test")
            self.assertEqual(request.url.path, "/v1/responses")
            self.assertEqual(request.headers["Authorization"], "Bearer relay-key")
            self.assertEqual(payload["model"], "gpt-4o-mini")
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertFalse(payload["text"]["format"]["schema"]["additionalProperties"])
            nested_items = payload["text"]["format"]["schema"]["properties"]["sections"]["items"]
            self.assertEqual(
                nested_items["required"],
                ["index", "title", "subsections"],
            )
            self.assertEqual(
                nested_items["properties"]["subsections"]["items"]["required"],
                ["index", "title"],
            )
            result_text = '{"title":"结构化摘要","sections":[{"index":1,"title":"项目概述","subsections":[{"index":1,"title":"背景"}]}]}'
            stream_body = (
                "event: response.created\n"
                f"data: {json.dumps({'type': 'response.created', 'response': {'id': 'resp-openai', 'model': 'gpt-4o-mini'}}, ensure_ascii=False)}\n\n"
                "event: response.output_text.delta\n"
                f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': result_text}, ensure_ascii=False)}\n\n"
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': {'id': 'resp-openai', 'model': 'gpt-4o-mini', 'usage': {'input_tokens': 14, 'output_tokens': 5, 'total_tokens': 19}}}, ensure_ascii=False)}\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_body)

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "",
                "QWEN_API_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
                "OPENAI_API_KEY": "relay-key",
                "OPENAI_BASE_URL": "https://relay.test",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            client = self._make_client(provider)
            response = asyncio.run(
                client.invoke(
                    LLMRequest(
                        task_type=TaskType.OUTLINE,
                        session_id="openai-relay-session",
                        system_prompt="请输出 JSON。",
                        user_prompt="请为上海电气集团生成结构化摘要。",
                        json_schema={
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "sections": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "index": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "subsections": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "index": {"type": "integer"},
                                                        "title": {"type": "string"},
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            "required": ["title", "sections"],
                        },
                    )
                )
            )

        self.assertEqual(
            response.content,
            '{"title":"结构化摘要","sections":[{"index":1,"title":"项目概述","subsections":[{"index":1,"title":"背景"}]}]}',
        )
        self.assertEqual(response.model_used, "gpt-4o-mini")

    def test_live_provider_streams_sse_chunks(self) -> None:
        stream_body = (
            'data: {"choices":[{"delta":{"content":"Hello "},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":"world"},"index":0}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "deepseek.test")
            payload = json.loads(request.content.decode("utf-8"))
            self.assertTrue(payload["stream"])
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_body)

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.test/v1",
                "DEEPSEEK_MODEL": "deepseek-chat",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))

            async def collect() -> str:
                chunks: list[str] = []
                async for chunk in provider.invoke_stream(
                    ModelType.DEEPSEEK,
                    LLMRequest(
                        task_type=TaskType.OUTLINE,
                        system_prompt="system",
                        user_prompt="请返回文本。",
                    ),
                ):
                    chunks.append(chunk)
                return "".join(chunks)

            content = asyncio.run(collect())

        self.assertEqual(content, "Hello world")

    def test_live_client_includes_input_images_for_responses_api(self) -> None:
        seen_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_payload
            seen_payload = json.loads(request.content.decode("utf-8"))
            result_text = '{"items":[{"candidate_index":0,"visual_role":"page_furniture","confidence":0.9,"reason":"logo","title_hint":""}]}'
            stream_body = (
                "event: response.created\n"
                f"data: {json.dumps({'type': 'response.created', 'response': {'id': 'resp-openai-vision', 'model': 'gpt-4.1-mini'}}, ensure_ascii=False)}\n\n"
                "event: response.output_text.delta\n"
                f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': result_text}, ensure_ascii=False)}\n\n"
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': {'id': 'resp-openai-vision', 'model': 'gpt-4.1-mini', 'usage': {'input_tokens': 18, 'output_tokens': 7, 'total_tokens': 25}}}, ensure_ascii=False)}\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_body)

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "",
                "QWEN_API_KEY": "",
                "DOUBAO_API_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
                "OPENAI_API_KEY": "relay-key",
                "OPENAI_BASE_URL": "https://relay.test",
                "OPENAI_MODEL": "gpt-4.1-mini",
                "GATEWAY_MASKING_ENABLED": "false",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            client = self._make_client(provider)
            response = asyncio.run(
                client.invoke(
                    LLMRequest(
                        task_type=TaskType.ASSET_REVIEW,
                        system_prompt="请输出 JSON。",
                        user_prompt="请审核图片资产。",
                        input_images=[LLMInputImage(image_url="data:image/png;base64,ZmFrZQ==", detail="low")],
                        json_schema={
                            "type": "object",
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "candidate_index": {"type": "integer"},
                                            "visual_role": {"type": "string"},
                                            "confidence": {"type": "number"},
                                            "reason": {"type": "string"},
                                            "title_hint": {"type": "string"},
                                        },
                                        "required": ["candidate_index", "visual_role", "confidence", "reason", "title_hint"],
                                    },
                                }
                            },
                            "required": ["items"],
                        },
                    )
                )
            )

        user_content = seen_payload["input"][1]["content"]
        self.assertEqual(user_content[0]["type"], "input_text")
        self.assertEqual(user_content[1]["type"], "input_image")
        self.assertEqual(user_content[1]["image_url"], "data:image/png;base64,ZmFrZQ==")
        self.assertEqual(user_content[1]["detail"], "low")
        self.assertIn('"visual_role":"page_furniture"', response.content)

    def test_live_provider_retries_retryable_responses_failure_once(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, json={"error": {"message": "Service temporarily unavailable"}})
            result_text = '{"title":"重试成功","sections":[]}'
            stream_body = (
                "event: response.created\n"
                f"data: {json.dumps({'type': 'response.created', 'response': {'id': 'resp-openai', 'model': 'gpt-4o-mini'}}, ensure_ascii=False)}\n\n"
                "event: response.output_text.delta\n"
                f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': result_text}, ensure_ascii=False)}\n\n"
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': {'id': 'resp-openai', 'model': 'gpt-4o-mini', 'usage': {'input_tokens': 14, 'output_tokens': 5, 'total_tokens': 19}}}, ensure_ascii=False)}\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream_body)

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "",
                "QWEN_API_KEY": "",
                "DOUBAO_API_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
                "OPENAI_API_KEY": "relay-key",
                "OPENAI_BASE_URL": "https://relay.test",
                "OPENAI_MODEL": "gpt-4o-mini",
                "LLM_RETRY_ATTEMPTS": "1",
                "LLM_RETRY_BACKOFF_SECONDS": "0",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = HTTPChatCompletionsProvider(transport=httpx.MockTransport(handler))
            response = asyncio.run(
                provider.invoke(
                    ModelType.OPENAI,
                    LLMRequest(
                        task_type=TaskType.OUTLINE,
                        system_prompt="请输出 JSON。",
                        user_prompt="请返回结构化结果。",
                        json_schema={
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "sections": {"type": "array"},
                            },
                            "required": ["title", "sections"],
                        },
                    ),
                )
            )

        self.assertEqual(calls, 2)
        self.assertEqual(response.content, '{"title":"重试成功","sections":[]}')

    def test_live_provider_times_out_stalled_responses_stream(self) -> None:
        class _TimeoutProvider(HTTPChatCompletionsProvider):
            async def _collect_responses_stream(self, *, config, request, model_type, payload):
                raise RetryableLLMError(
                    self._format_stream_timeout(config.provider_name, model_type, self.stream_timeout_seconds)
                )

        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_BACKEND": "live",
                "DEEPSEEK_API_KEY": "",
                "QWEN_API_KEY": "",
                "DOUBAO_API_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
                "OPENAI_API_KEY": "relay-key",
                "OPENAI_BASE_URL": "https://relay.test",
                "OPENAI_MODEL": "gpt-4o-mini",
                "LLM_STREAM_TIMEOUT_SECONDS": "0.01",
                "LLM_RETRY_ATTEMPTS": "0",
                "LLM_RETRY_BACKOFF_SECONDS": "0",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            provider = _TimeoutProvider(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(
                    provider.invoke(
                        ModelType.OPENAI,
                        LLMRequest(
                            task_type=TaskType.OUTLINE,
                            system_prompt="请输出 JSON。",
                            user_prompt="请返回结构化结果。",
                            json_schema={
                                "type": "object",
                                "properties": {"title": {"type": "string"}},
                                "required": ["title"],
                            },
                        ),
                    )
                )

        self.assertIn("stream timed out", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
