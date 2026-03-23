from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.composition.outline_service import normalize_outline_payload
from app.services.composition.section_service import SectionDraftService, build_section_context


class CompositionHelperTests(unittest.TestCase):
    def test_normalize_outline_payload_enriches_v2_fields(self) -> None:
        payload = {
            "title": "测试项目技术方案",
            "sections": [
                {
                    "index": 0,
                    "title": "技术架构",
                    "description": "说明系统架构",
                    "keywords": ["技术架构"],
                }
            ],
        }
        normalized = normalize_outline_payload(payload, project_name="测试项目")
        section = normalized["sections"][0]
        self.assertEqual(section["section_id"], "1")
        self.assertEqual(section["purpose"], "说明系统架构")
        self.assertIn("figure", section["expected_evidence_types"])
        self.assertTrue(section["needs_human_review"])

    def test_build_section_context_filters_by_expected_types(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_001",
                        "type": "table",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "heading_path": ["第3章", "配置清单"],
                        "summary": "包含设备与数量",
                        "relevance_score": 0.91,
                    },
                    {
                        "evidence_id": "ev_002",
                        "type": "section",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "heading_path": ["第2章", "项目概述"],
                        "summary": "背景说明",
                        "relevance_score": 0.75,
                    },
                ]
            }
        )
        context, citations = build_section_context(
            section={"title": "硬件配置清单", "expected_evidence_types": ["table", "parameter"]},
            evidence_bundle=bundle,
        )
        self.assertIn("历史方案A", context)
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["evidence_id"], "ev_001")

    def test_find_section_supports_nested_outline_children(self) -> None:
        service = SectionDraftService()
        outline = SimpleNamespace(
            outline_json={
                "title": "测试项目技术方案",
                "sections": [
                    {
                        "section_id": "1",
                        "title": "项目概述",
                        "children": [
                            {
                                "section_id": "1.1",
                                "title": "项目背景",
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        )
        section = service._find_section(outline=outline, section_id="1.1")
        self.assertEqual(section["title"], "项目背景")


if __name__ == "__main__":
    unittest.main()
