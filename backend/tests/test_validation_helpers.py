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
                content_md="技术架构方案待确认系统拓扑，当前使用 [Company_A] 占位，并补充了足够多的技术说明文字用于测试。",
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
                validator_result={},
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertEqual({item["code"] for item in errors}, {"VAL002", "VAL003", "VAL006", "VAL007"})
        self.assertEqual({item["code"] for item in warnings}, {"VAL101", "VAL102", "VAL103"})
        self.assertEqual({item["code"] for item in section_results["1"]["errors"]}, {"VAL006", "VAL007"})

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
