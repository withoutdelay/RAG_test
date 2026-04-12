from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.services.composition.section_quality import (
    SectionQualityGateService,
    analyze_section_heading_quality,
)


class _StubLLMClient:
    async def invoke(self, request):
        if "建议插入图表" in request.user_prompt or "### A. 概述" in request.user_prompt:
            payload = {
                "pass": False,
                "score": 0.61,
                "summary": "章节存在内部标题和风格不一致的小标题。",
                "issues": [
                    {
                        "code": "SQLLM01",
                        "severity": "high",
                        "target": "建议插入图表",
                        "message": "存在内部编辑标题。",
                        "suggested_fix": "删除内部标题，把资产自然融入正文。",
                    }
                ],
                "rewrite_instruction": "统一小标题风格，删除内部标题，并保留技术信息密度。",
            }
        else:
            payload = {
                "pass": True,
                "score": 0.92,
                "summary": "章节标题风格和客户口径均已达标。",
                "issues": [],
                "rewrite_instruction": "保持当前表达。",
            }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _StubExecutor:
    async def rewrite_section(self, *, task_id, section_context, selected_text, instruction, global_params):
        rewritten = selected_text.replace("### A. 概述", "### 系统组成与控制分工")
        rewritten = rewritten.replace("### 建议插入图表\n\n", "")
        return SimpleNamespace(content=rewritten)


class SectionQualityGateTests(unittest.TestCase):
    def test_analyze_section_heading_quality_flags_internal_and_latin_headings(self) -> None:
        issues = analyze_section_heading_quality(
            section_title="总体方案",
            content_md=(
                "## 总体方案\n\n"
                "### A. 概述\n\n"
                "正文。\n\n"
                "### 建议插入图表\n\n"
                "- [[ASSET:FIGURE:1]] 图1\n"
            ),
        )

        self.assertEqual({issue.code for issue in issues}, {"SQ001", "SQ002"})

    def test_review_and_repair_rewrites_bad_headings_before_returning(self) -> None:
        service = SectionQualityGateService(
            llm_client=_StubLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-test",
                section={"title": "3 总体方案", "purpose": "说明总体控制和主回路方案。"},
                outline_title="测试项目技术方案",
                global_params={"voltage_level": "10kV"},
                content_md=(
                    "## 3 总体方案\n\n"
                    "### A. 概述\n\n"
                    "本节说明系统组成。\n\n"
                    "### 建议插入图表\n\n"
                    "- [[ASSET:FIGURE:1]] 主回路图\n"
                ),
                recommended_assets=[{"asset_id": "1", "asset_type": "figure", "title": "主回路图"}],
                allow_rewrite=True,
            )
        )

        self.assertEqual(status, "generated")
        self.assertNotIn("### A. 概述", content)
        self.assertNotIn("建议插入图表", content)
        self.assertIn("### 系统组成与控制分工", content)
        self.assertTrue(meta["rewrite_attempted"])
        self.assertTrue(meta["rewrite_applied"])
        self.assertEqual(meta["status"], "passed")

    def _run(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)
