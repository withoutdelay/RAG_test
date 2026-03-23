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
    LLMRequest,
    LLMResponse,
    ModelType,
    MockLLMProvider,
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


class LLMClientTests(unittest.TestCase):
    def _make_client(self, provider: BaseLLMProvider) -> LLMClient:
        transport = httpx.ASGITransport(app=gateway_app)
        gateway_client = GatewayClient(base_url="http://gateway.test", transport=transport)
        return LLMClient(gateway_client=gateway_client, provider=provider)

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_invoke_falls_back_and_restores_masked_entities(self) -> None:
        client = self._make_client(_FallbackProvider())

        response = asyncio.run(
            client.invoke(
                LLMRequest(
                    task_type=TaskType.OUTLINE,
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
                    task_type=TaskType.SECTION_WRITE,
                    session_id="stream-session",
                    system_prompt="system",
                    user_prompt="请为上海电气集团生成实施章节。",
                )
            ):
                chunks.append(chunk)
            return "".join(chunks)

        content = asyncio.run(collect())
        self.assertEqual(content, "根据分析，上海电气集团需要新的实施方案。")

    def test_mock_provider_routes_section_write_to_qwen(self) -> None:
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

        self.assertEqual(response.model_used, "qwen:mock")
        self.assertIn("项目概述", response.content)

    def test_live_provider_falls_back_from_deepseek_to_qwen(self) -> None:
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
                        task_type=TaskType.OUTLINE,
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

        self.assertEqual(seen_hosts, ["deepseek", "qwen"])
        self.assertEqual(response.content, '{"company":"上海电气集团"}')
        self.assertEqual(response.model_used, "qwen-plus")

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
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer relay-key")
            self.assertEqual(payload["model"], "gpt-4o-mini")
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-openai",
                    "model": "gpt-4o-mini",
                    "choices": [{"message": {"role": "assistant", "content": '{"company":"[Company_A]"}'}}],
                    "usage": {"prompt_tokens": 14, "completion_tokens": 5, "total_tokens": 19},
                },
            )

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
                "OPENAI_BASE_URL": "https://relay.test/v1",
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
                            "properties": {"company": {"type": "string"}},
                            "required": ["company"],
                        },
                    )
                )
            )

        self.assertEqual(response.content, '{"company":"上海电气集团"}')
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


if __name__ == "__main__":
    unittest.main()
