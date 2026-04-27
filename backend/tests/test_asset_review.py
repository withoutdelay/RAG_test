from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from app.config import get_settings
from app.services.llm.client import BaseLLMProvider, LLMClient, LLMRequest, LLMResponse, ModelType
from app.services.parsing.asset_review import AssetReviewService, _build_system_prompt, _build_user_prompt
from app.services.parsing.docling_parser import ParsedAsset


class _AssetReviewProvider(BaseLLMProvider):
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
                            "visual_role": "page_furniture",
                            "confidence": 0.93,
                            "reason": "footer banner with logo geometry",
                            "title_hint": "页脚公司标识",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            model_used=f"{model_type.value}:asset-review-test",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            cost_estimate=0.0,
        )

    async def invoke_stream(self, model_type: ModelType, request: LLMRequest):
        yield ""


class AssetReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {
                "PARSER_LLM_ASSET_REVIEW_ENABLED": "true",
                "PARSER_LLM_ASSET_REVIEW_MAX_ASSETS": "4",
                "PARSER_LLM_ASSET_REVIEW_CONFIDENCE_THRESHOLD": "0.72",
                "GATEWAY_MASKING_ENABLED": "false",
            },
            clear=False,
        )
        self._env_patch.start()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self._env_patch.stop()

    def test_review_assets_downgrades_footer_banner_like_figure(self) -> None:
        provider = _AssetReviewProvider()
        service = AssetReviewService(
            llm_client=LLMClient(provider=provider),
            settings=get_settings(),
        )
        asset = ParsedAsset(
            asset_type="figure",
            page_no=17,
            title="变压器一次侧和二次侧绕组间屏蔽层",
            caption=None,
            heading_path="变压器一次侧和二次侧绕组间屏蔽层",
            context_before="为实现一次侧和二次侧绕组的解耦，接地屏蔽层如下图所示。",
            context_after="HV: 高压侧正弦波电压",
            bbox={"l": 57.29, "r": 134.47, "b": 20.51, "t": 45.55},
            source_ref="#/pictures/42",
            image_bytes=b"fake-png-bytes",
            meta={
                "visual_role": "engineering_figure",
                "width": 154,
                "height": 50,
                "page_width": 595.32,
                "page_height": 841.92,
                "bbox": {"l": 57.29, "r": 134.47, "b": 20.51, "t": 45.55},
            },
        )

        reviewed_assets, stats = asyncio.run(service.review_assets([asset]))

        self.assertEqual(reviewed_assets[0].meta["visual_role"], "page_furniture")
        self.assertEqual(stats.reviewed_count, 1)
        self.assertEqual(stats.overridden_count, 1)
        self.assertEqual(stats.vision_attached_count, 1)
        self.assertEqual(reviewed_assets[0].meta["llm_asset_review"]["suggested_visual_role"], "page_furniture")
        self.assertIsNotNone(provider.seen_request)
        self.assertEqual(len(provider.seen_request.input_images), 1)
        self.assertTrue(provider.seen_request.input_images[0].image_url.startswith("data:image/png;base64,"))

    def test_asset_review_prompt_uses_general_visual_evidence_rules(self) -> None:
        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(
            [
                (
                    0,
                    {
                        "candidate_index": 0,
                        "title": "主接线图",
                        "current_visual_role": "engineering_figure",
                    },
                )
            ],
            vision_candidate_indices=[0],
        )

        self.assertIn("视觉内容与标题/上下文冲突", system_prompt)
        self.assertIn("标题、caption、heading_path 和邻近正文都可能来自 OCR", system_prompt)
        self.assertIn("不具备工程图结构", system_prompt)
        self.assertIn("title、heading_path、caption、context_before、context_after 只是弱证据", user_prompt)


if __name__ == "__main__":
    unittest.main()
