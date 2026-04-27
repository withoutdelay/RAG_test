from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from app.config import get_settings
from app.services.llm.client import BaseLLMProvider, LLMClient, LLMRequest, LLMResponse, ModelType
from app.services.parsing.asset_semantic_summary import AssetSemanticSummaryService, _build_system_prompt, _build_user_prompt
from app.services.parsing.docling_parser import ParsedAsset


class _AssetSummaryProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.seen_request: LLMRequest | None = None

    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        self.seen_request = request
        return LLMResponse(
            content=json.dumps(
                {
                    "items": [
                        {
                            "candidate_index": 0,
                            "title_hint": "LCI主回路系统图",
                            "diagram_type": "LCI 主回路原理图",
                            "summary": "该图用于说明 LCI 软启动系统与同步电机、变压器之间的主回路关系。",
                            "problem_solved": "解决同步电机启动阶段的变频软起、并网切换与主回路配置说明问题。",
                            "principle_summary": "系统经整流、逆变和励磁配合完成软启动，并在满足条件后切换至工频运行。",
                            "key_components": ["LCI 柜", "同步电机", "变压器", "励磁系统"],
                            "signals_or_loops": ["主回路", "切换联锁"],
                            "applicable_sections": ["LCI 变频软起装置方案", "主回路系统方案"],
                            "retrieval_keywords": ["LCI", "同步电机", "主回路", "软启动", "工频切换"],
                            "confidence": 0.88,
                            "review_required": False,
                            "review_notes": "图中文字可读，结构关系明确。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            model_used=f"{model_type.value}:asset-summary-test",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            cost_estimate=0.0,
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest):
        yield ""


class _FlakyBatchAssetSummaryProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.request_sizes: list[int] = []

    async def invoke(self, model_type: ModelType, request: LLMRequest) -> LLMResponse:
        size = len(request.metadata.get("candidates") or [])
        self.request_sizes.append(size)
        if size > 1:
            raise RuntimeError("batch payload too large")
        candidate = (request.metadata.get("candidates") or [])[0]
        return LLMResponse(
            content=json.dumps(
                {
                    "items": [
                        {
                            "candidate_index": int(candidate.get("candidate_index") or 0),
                            "title_hint": "电机联锁控制图",
                            "diagram_type": "控制原理图",
                            "summary": "该图用于说明电机控制与保护逻辑。",
                            "problem_solved": "解释联锁与保护方案。",
                            "principle_summary": "PLC 协调启动、保护和联锁。",
                            "key_components": ["PLC", "电机"],
                            "signals_or_loops": ["联锁"],
                            "applicable_sections": ["控制系统方案"],
                            "retrieval_keywords": ["联锁", "保护"],
                            "confidence": 0.8,
                            "review_required": False,
                            "review_notes": "",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            model_used=f"{model_type.value}:asset-summary-test",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            cost_estimate=0.0,
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest):
        yield ""


class AssetSemanticSummaryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {
                "PARSER_LLM_ASSET_SUMMARY_ENABLED": "true",
                "PARSER_LLM_ASSET_SUMMARY_MAX_ASSETS": "4",
                "PARSER_LLM_ASSET_SUMMARY_USE_VISION": "true",
                "GATEWAY_MASKING_ENABLED": "false",
            },
            clear=False,
        )
        self._env_patch.start()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self._env_patch.stop()

    def test_summarize_assets_writes_semantic_summary(self) -> None:
        provider = _AssetSummaryProvider()
        service = AssetSemanticSummaryService(
            llm_client=LLMClient(provider=provider),
            settings=get_settings(),
        )
        asset = ParsedAsset(
            asset_type="figure",
            page_no=7,
            title="LCI 变频软起系统单线图",
            caption=None,
            heading_path="4 LCI 变频软起系统方案",
            context_before="LCI 变频软起系统单线图如下所示。",
            context_after="系统含整流、逆变和同步切换环节。",
            bbox={"l": 120.0, "r": 520.0, "b": 120.0, "t": 520.0},
            source_ref="#/pictures/3",
            image_bytes=b"fake-png-bytes",
            meta={
                "visual_role": "engineering_figure",
                "width": 640,
                "height": 480,
                "page_width": 595.32,
                "page_height": 841.92,
            },
        )

        summarized_assets, stats = asyncio.run(service.summarize_assets([asset]))

        summary = summarized_assets[0].meta["semantic_summary"]
        self.assertEqual(summary["status"], "summarized")
        self.assertEqual(summary["title_hint"], "LCI主回路系统图")
        self.assertIn("LCI", summary["summary"])
        self.assertIn("主回路", summary["problem_solved"])
        self.assertIn("同步电机", summary["key_components"])
        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.summarized_count, 1)
        self.assertEqual(stats.vision_attached_count, 1)
        self.assertIsNotNone(provider.seen_request)
        self.assertEqual(len(provider.seen_request.input_images), 1)

    def test_summarize_assets_falls_back_to_single_candidate_retry(self) -> None:
        provider = _FlakyBatchAssetSummaryProvider()
        service = AssetSemanticSummaryService(
            llm_client=LLMClient(provider=provider),
            settings=get_settings(),
        )
        assets = [
            ParsedAsset(
                asset_type="figure",
                page_no=6,
                title="控制原理图A",
                caption=None,
                heading_path="控制系统方案",
                context_before="控制原理图如下。",
                context_after="包含联锁信号。",
                bbox={"l": 10.0, "r": 500.0, "b": 10.0, "t": 500.0},
                source_ref="#/pictures/1",
                image_bytes=b"fake-png-bytes",
                meta={"visual_role": "engineering_figure", "width": 640, "height": 480},
            ),
            ParsedAsset(
                asset_type="figure",
                page_no=7,
                title="控制原理图B",
                caption=None,
                heading_path="控制系统方案",
                context_before="联锁控制框图如下。",
                context_after="包含保护信号。",
                bbox={"l": 10.0, "r": 500.0, "b": 10.0, "t": 500.0},
                source_ref="#/pictures/2",
                image_bytes=b"fake-png-bytes",
                meta={"visual_role": "engineering_figure", "width": 640, "height": 480},
            ),
        ]

        summarized_assets, stats = asyncio.run(service.summarize_assets(assets))

        self.assertEqual(stats.summarized_count, 2)
        self.assertEqual(provider.request_sizes[0], 2)
        self.assertEqual(provider.request_sizes[-2:], [1, 1])
        self.assertGreaterEqual(provider.request_sizes.count(2), 1)
        self.assertEqual(summarized_assets[0].meta["semantic_summary"]["status"], "summarized")
        self.assertEqual(summarized_assets[1].meta["semantic_summary"]["status"], "summarized")

    def test_summary_prompt_requires_visual_evidence_over_title(self) -> None:
        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(
            [
                (
                    0,
                    {
                        "candidate_index": 0,
                        "title": "系统拓扑图",
                        "current_visual_role": "engineering_figure",
                    },
                )
            ],
            vision_candidate_indices=[0],
        )

        self.assertIn("标题、caption、heading_path、前后文只是弱证据", system_prompt)
        self.assertIn("不要把产品照片、布局图、文字截图或碎片摘要成主接线图", system_prompt)
        self.assertIn("视觉内容与文字上下文冲突", system_prompt)
        self.assertIn("宁可保守标注待人工确认", user_prompt)


if __name__ == "__main__":
    unittest.main()
