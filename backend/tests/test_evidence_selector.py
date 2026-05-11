from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

from app.services.composition.evidence_selector import select_evidence_for_section
from app.services.llm.client import TaskType


def _settings(**overrides):
    values = {
        "evidence_selector_mode": "strict",
        "evidence_selector_max_sections": 6,
        "evidence_selector_max_blocks": 10,
        "evidence_selector_max_assets": 8,
        "evidence_selector_min_confidence": 0.62,
        "evidence_selector_timeout_seconds": 5.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _SelectorLLMClient:
    def __init__(self, *, confidence: float = 0.78, fail: bool = False) -> None:
        self.confidence = confidence
        self.fail = fail
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("selector unavailable")
        candidates = request.metadata["candidates"]
        payload = {
            "selected_sections": [
                {"candidate_id": item["candidate_id"], "confidence": self.confidence, "reason": "section kept"}
                for item in candidates.get("sections", [])
            ],
            "selected_blocks": [
                {"candidate_id": item["candidate_id"], "confidence": self.confidence, "reason": "block kept"}
                for item in candidates.get("blocks", [])
            ],
            "selected_assets": [
                {"candidate_id": item["candidate_id"], "confidence": self.confidence, "reason": "asset kept"}
                for item in candidates.get("assets", [])
            ],
            "rejected_candidates": [],
            "selection_reason": "unit-test selector",
            "risk_flags": [],
            "confidence": self.confidence,
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), model_used="selector:test")


class EvidenceSelectorTests(unittest.TestCase):
    def test_selector_filters_noise_blocks_and_product_photo_before_prompt(self) -> None:
        llm_client = _SelectorLLMClient()
        blocks = [
            {
                "block_id": "block-training",
                "source_title": "历史方案A.docx",
                "source_heading": "供方培训计划",
                "heading_path": ["5.5 供方培训计划"],
                "content_md": "供方提供操作培训和售后服务。",
                "selection_score": 0.91,
                "metadata": {"section_type": "service_support", "content_form": "narrative"},
            },
            {
                "block_id": "block-main-circuit",
                "source_title": "历史方案A.docx",
                "source_heading": "高压变频器主回路方案说明",
                "heading_path": ["2.2 高压变频器主回路方案说明"],
                "content_md": "主回路采用输入隔离、变频器、输出隔离和工频旁路回路。",
                "selection_score": 0.86,
                "metadata": {"section_type": "main_circuit_scheme", "content_form": "narrative"},
            },
        ]
        assets = [
            {
                "asset_id": "asset-photo",
                "asset_type": "figure",
                "visual_role": "product_photo",
                "display_title": "产品照片",
                "heading_path": "设备外观",
                "score": 0.92,
            },
            {
                "asset_id": "asset-topology",
                "asset_type": "figure",
                "visual_role": "engineering_figure",
                "display_title": "主回路拓扑图",
                "heading_path": "主回路方案",
                "preview_text": "输入隔离、变频器、输出隔离和旁路回路。",
                "score": 0.82,
            },
        ]

        result = asyncio.run(
            select_evidence_for_section(
                llm_client=llm_client,
                section={
                    "title": "主回路系统方案",
                    "purpose": "说明主回路拓扑与旁路切换方式。",
                    "generation_mode": "reuse_first",
                },
                global_params={"project_name": "测试项目"},
                case_trace={"section_candidates": []},
                reusable_blocks=blocks,
                recommended_assets=assets,
                asset_candidates=assets,
                published_wiki={"product_cards": [{"title": "高压变频器方案族"}]},
                task_id="selector-test",
                settings=_settings(),
            )
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual([block["block_id"] for block in result["filtered_reusable_blocks"]], ["block-main-circuit"])
        self.assertEqual([asset["asset_id"] for asset in result["filtered_recommended_assets"]], ["asset-topology"])
        self.assertEqual(result["rejected_candidates"][0]["risk_flags"], ["cross_section_contamination"])
        self.assertTrue(any("product_photo_for_topology" in item["risk_flags"] for item in result["rejected_candidates"]))
        self.assertEqual(llm_client.requests[0].task_type, TaskType.EVIDENCE_SELECT)

    def test_selector_falls_back_to_deterministic_result_when_llm_fails(self) -> None:
        llm_client = _SelectorLLMClient(fail=True)
        blocks = [
            {
                "block_id": "block-training",
                "source_heading": "培训计划",
                "heading_path": ["培训计划"],
                "content_md": "培训和售后服务安排。",
                "metadata": {"section_type": "service_support"},
            },
            {
                "block_id": "block-control",
                "source_heading": "控制逻辑",
                "heading_path": ["控制逻辑"],
                "content_md": "控制系统采集运行状态并执行联锁保护。",
                "metadata": {"section_type": "control_logic"},
            },
        ]

        result = asyncio.run(
            select_evidence_for_section(
                llm_client=llm_client,
                section={"title": "控制系统方案", "purpose": "说明控制逻辑与联锁边界。"},
                global_params={},
                case_trace={},
                reusable_blocks=blocks,
                recommended_assets=[],
                asset_candidates=[],
                task_id="selector-fallback",
                settings=_settings(),
            )
        )

        self.assertEqual(result["status"], "fallback_error")
        self.assertEqual([block["block_id"] for block in result["filtered_reusable_blocks"]], ["block-control"])
        self.assertIn("selector_fallback", result["risk_flags"])

    def test_selector_marks_low_confidence_in_trace_without_blocking_selection(self) -> None:
        llm_client = _SelectorLLMClient(confidence=0.41)
        block = {
            "block_id": "block-main",
            "source_heading": "主回路方案",
            "heading_path": ["主回路方案"],
            "content_md": "主回路采用输入隔离和旁路切换。",
            "metadata": {"section_type": "main_circuit_scheme"},
        }

        result = asyncio.run(
            select_evidence_for_section(
                llm_client=llm_client,
                section={"title": "主回路方案", "purpose": "说明主回路拓扑。"},
                global_params={},
                case_trace={},
                reusable_blocks=[block],
                recommended_assets=[],
                asset_candidates=[],
                task_id="selector-low-confidence",
                settings=_settings(evidence_selector_min_confidence=0.62),
            )
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["filtered_reusable_blocks"], [block])
        self.assertIn("low_confidence_selection", result["risk_flags"])
