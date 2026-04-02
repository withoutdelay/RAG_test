from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.validation.service import (
    build_review_task_blueprints,
    collect_validation_findings,
    derive_validation_status,
)


class ValidationHelperTests(unittest.TestCase):
    def test_collect_validation_findings_returns_blockers_and_warnings(self) -> None:
        requirement_card = SimpleNamespace(
            content={"key_parameters": {"total_power": "5000kW"}},
            blocking_items=[],
        )
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.4200"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_001",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "type": "figure",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "1",
                        "title": "技术架构",
                        "mandatory": True,
                        "expected_evidence_types": ["figure", "parameter"],
                        "asset_required": True,
                        "needs_human_review": True,
                        "children": [],
                    },
                    {
                        "section_id": "2",
                        "title": "实施计划",
                        "mandatory": True,
                        "expected_evidence_types": ["section"],
                        "needs_human_review": False,
                        "children": [],
                    },
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="1",
                title="技术架构",
                content_md="当前方案待确认系统拓扑，使用 [Company_A] 占位，并引用旧项目A 的拓扑描述，补充了足够多的技术说明文字用于测试。",
                citation_refs=[
                    {
                        "evidence_id": "ev_001",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "type": "figure",
                    }
                ],
                assumptions=[],
                global_param_snapshot={"total_power": "5200kW"},
                validator_result={
                    "recommended_assets": [{"asset_id": "asset-001", "asset_type": "figure"}],
                    "reuse_pack": {"banned_terms": ["旧项目A"]},
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertEqual({item["code"] for item in errors}, {"VAL002", "VAL003", "VAL006", "VAL007", "VAL008"})
        self.assertEqual({item["code"] for item in warnings}, {"VAL101", "VAL102", "VAL103", "VAL104"})
        self.assertEqual({item["code"] for item in section_results["1"]["errors"]}, {"VAL006", "VAL007", "VAL008"})

    def test_collect_validation_findings_flags_similarity_and_parameter_replacement_risk(self) -> None:
        requirement_card = SimpleNamespace(
            content={"key_parameters": {"voltage_level": "10kV", "quantity": "3台"}},
            blocking_items=[],
        )
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.8800"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "4",
                        "title": "硬件配置清单",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "asset_required": False,
                        "parameter_sensitive": True,
                        "customer_specificity": "medium",
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        source_block = (
            "硬件配置清单如下：本方案配置高压变频器 2台，电压等级 6kV，采用站控层、间隔层和网络层的分层架构，"
            "配套原有控制柜和辅助系统，适用于历史项目的标准交付边界。"
        )
        section_drafts = [
            SimpleNamespace(
                section_id="4",
                title="硬件配置清单",
                content_md=source_block,
                citation_refs=[
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={"voltage_level": "10kV", "quantity": "3台"},
                validator_result={
                    "reuse_pack": {
                        "must_replace_fields": ["voltage_level", "quantity"],
                        "replacement_hints": {"voltage_level": "10kV", "quantity": "3台"},
                        "reusable_blocks": [
                            {
                                "block_id": "block-1",
                                "source_title": "历史方案B",
                                "content_md": source_block,
                            }
                        ],
                    }
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertIn("VAL009", {item["code"] for item in errors})
        self.assertIn("VAL105", {item["code"] for item in warnings})
        self.assertEqual(
            {item["code"] for item in section_results["4"]["errors"]},
            {"VAL009"},
        )
        self.assertEqual(
            {item["code"] for item in section_results["4"]["warnings"]},
            {"VAL105"},
        )

    def test_collect_validation_findings_accepts_reuse_block_citations(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "3",
                        "title": "技术架构",
                        "mandatory": True,
                        "expected_evidence_types": ["section"],
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="3",
                title="技术架构",
                content_md="系统采用分层控制架构，配置上位机接口与联锁回路。",
                citation_refs=[
                    {
                        "evidence_id": "case:sample-b:9",
                        "source_doc_id": "sample-b",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "reuse_pack": {
                        "reusable_blocks": [
                            {
                                "block_id": "case:sample-b:9",
                                "source_title": "历史方案B",
                                "content_md": "系统采用分层控制架构。",
                            }
                        ]
                    }
                },
            )
        ]

        errors, _, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL005", {item["code"] for item in errors})
        self.assertNotIn("VAL005", {item["code"] for item in section_results["3"]["errors"]})

    def test_build_review_task_blueprints_creates_manual_tasks_and_final_review(self) -> None:
        outline = SimpleNamespace(id=uuid4(), outline_json={"title": "测试方案"})
        existing_open_task = SimpleNamespace(
            status="open",
            payload={"signature": "content_review:1:1:VAL101"},
        )

        blueprints = build_review_task_blueprints(
            errors=[
                {
                    "code": "VAL003",
                    "details": {"param_name": "total_power", "values": ["5000kW", "5200kW"]},
                    "message": "参数冲突",
                },
                {
                    "code": "VAL006",
                    "section_id": "1",
                    "section_title": "技术架构",
                    "message": "图表待确认",
                },
            ],
            warnings=[
                {
                    "code": "VAL101",
                    "section_id": "1",
                    "section_title": "技术架构",
                    "message": "章节可能偏离目标",
                },
                {
                    "code": "VAL105",
                    "section_id": "2",
                    "section_title": "硬件配置清单",
                    "message": "复用相似度过高",
                }
            ],
            outline=outline,
            draft_version=1,
            existing_tasks=[existing_open_task],
        )

        task_types = {item["task_type"] for item in blueprints}
        self.assertEqual(task_types, {"param_conflict", "figure_confirm", "content_review", "final_review"})

    def test_derive_validation_status_marks_review_and_passed(self) -> None:
        self.assertEqual(
            derive_validation_status(errors=[{"code": "VAL002"}], open_review_task_count=0),
            "blocked",
        )
        self.assertEqual(
            derive_validation_status(errors=[{"code": "VAL003"}], open_review_task_count=1),
            "review_required",
        )
        self.assertEqual(derive_validation_status(errors=[], open_review_task_count=0), "passed")


if __name__ == "__main__":
    unittest.main()
