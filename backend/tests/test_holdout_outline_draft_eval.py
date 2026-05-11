from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from scripts.evaluate_holdout_outline_draft import (
    _evaluate_asset_stability,
    _evaluate_evidence_quality,
    _evaluate_export,
    _evaluate_phase8_gates,
    _evaluate_runtime,
    _evaluate_writing_quality,
)


class HoldoutOutlineDraftEvalTests(unittest.TestCase):
    def test_evidence_quality_flags_cross_section_pollution(self) -> None:
        drafts = [
            SimpleNamespace(
                section_id="3",
                title="系统方案",
                content_md="本节说明主回路。",
                validator_result={
                    "reuse_pack": {
                        "retrieval_trace": {
                            "selected_sections": [
                                {"title": "培训", "section_path": "19 培训"},
                            ]
                        }
                    }
                },
            ),
            SimpleNamespace(
                section_id="9",
                title="项目交付资料与文档清单",
                content_md="提交资料如下。",
                validator_result={
                    "reuse_pack": {
                        "retrieval_trace": {
                            "selected_sections": [
                                {"title": "负载数据 Load data", "section_path": "3.3.1 负载数据 Load data"},
                            ]
                        }
                    }
                },
            ),
        ]

        result = _evaluate_evidence_quality(drafts=drafts)

        self.assertEqual(result["technical_section_count"], 1)
        self.assertEqual(result["technical_evidence_accuracy"], 0.0)
        self.assertGreaterEqual(result["evidence_pollution_count"], 2)
        self.assertEqual(len(result["polluted_sections"]), 2)

    def test_writing_quality_detects_internal_residue_and_filler(self) -> None:
        drafts = [
            SimpleNamespace(
                section_id="4",
                title="核心设备与技术参数",
                content_md=(
                    "本章围绕高压变频装置的核心性能指标进行说明。\n\n"
                    "禁用表述：这里不应进入客户稿。"
                ),
            )
        ]

        result = _evaluate_writing_quality(drafts=drafts)

        self.assertEqual(result["internal_residue_count"], 1)
        self.assertEqual(result["filler_hit_count"], 2)
        self.assertEqual(result["filler_ratio"], 1.0)

    def test_asset_stability_counts_source_bound_top3_and_wrong_body_asset(self) -> None:
        drafts = [
            SimpleNamespace(
                section_id="3.1",
                title="主回路单线图",
                content_md="详见 [[ASSET:FIGURE:asset-1]]。",
                validator_result={
                    "recommended_assets": [
                        {
                            "asset_id": "asset-1",
                            "asset_type": "figure",
                            "metadata": {
                                "source_binding": {"source_section_id": "s-1"},
                                "asset_stability_gate": {"blocking_flags": ["asset_fragment"]},
                            },
                        }
                    ],
                    "asset_candidates": [],
                    "asset_trace": {"diagnostics": {"asset_stability": {"filtered_count": 2}}},
                },
            )
        ]

        result = _evaluate_asset_stability(drafts=drafts)

        self.assertEqual(result["figure_required_sections"], 1)
        self.assertEqual(result["top3_source_bound_rate"], 1.0)
        self.assertEqual(result["primary_source_bound_rate"], 1.0)
        self.assertEqual(result["wrong_figure_body_rate"], 1.0)
        self.assertEqual(result["filtered_asset_count"], 2)

    def test_runtime_and_export_metrics_feed_phase8_gate(self) -> None:
        started = datetime(2026, 5, 6, 1, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 5, 6, 1, 4, tzinfo=timezone.utc)
        runtime = _evaluate_runtime(
            job=SimpleNamespace(
                id="job-1",
                status="succeeded",
                started_at=started,
                completed_at=completed,
                output_ref={"generation_summary": {"parallel_generation": True}},
            )
        )
        export = _evaluate_export(
            export_record=SimpleNamespace(
                file_type="docx",
                status="forced",
                file_name="draft.docx",
                storage_path="exports/draft.docx",
            )
        )
        gate = _evaluate_phase8_gates(
            outline_eval={"coverage": 0.9},
            evidence_eval={"technical_evidence_accuracy": 0.85},
            asset_stability_eval={"top3_source_bound_rate": 0.8, "wrong_figure_body_rate": 0.0},
            writing_eval={"internal_residue_count": 0},
            runtime_eval=runtime,
            export_eval=export,
        )

        self.assertEqual(runtime["duration_seconds"], 240.0)
        self.assertTrue(runtime["within_target_5m"])
        self.assertTrue(export["word_export_available"])
        self.assertEqual(gate["status"], "passed")


if __name__ == "__main__":
    unittest.main()
