from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from app.services.export.service import build_export_snapshot, render_export_markdown


class ExportHelperTests(unittest.TestCase):
    def test_build_export_snapshot_contains_bound_versions(self) -> None:
        snapshot = build_export_snapshot(
            project=SimpleNamespace(id=uuid4(), status="EXPORTABLE", current_draft_version=2),
            outline=SimpleNamespace(id=uuid4(), version=3),
            requirement_card=SimpleNamespace(id=uuid4(), version=4),
            evidence_bundle=SimpleNamespace(id=uuid4(), retrieval_version=5),
            validation_report=SimpleNamespace(id=uuid4(), status="passed"),
            review_tasks=[
                SimpleNamespace(id=uuid4(), task_type="final_review", status="resolved", blocking_level="P0")
            ],
        )

        self.assertEqual(snapshot["draft_version"], 2)
        self.assertEqual(snapshot["outline_version"], 3)
        self.assertEqual(snapshot["requirement_card_version"], 4)
        self.assertEqual(snapshot["evidence_bundle_version"], 5)
        self.assertEqual(snapshot["validation_status"], "passed")
        self.assertEqual(snapshot["review_tasks"][0]["task_type"], "final_review")

    def test_render_export_markdown_keeps_sections_and_citations(self) -> None:
        markdown = render_export_markdown(
            project=SimpleNamespace(name="测试项目"),
            outline=SimpleNamespace(
                outline_json={
                    "title": "测试项目技术方案",
                    "sections": [
                        {"section_id": "1", "title": "项目概述", "children": []},
                        {"section_id": "2", "title": "技术架构", "children": []},
                    ],
                }
            ),
            section_drafts=[
                SimpleNamespace(
                    section_id="1",
                    title="项目概述",
                    content_md="## 项目概述\n\n说明项目背景。",
                    citation_refs=[],
                ),
                SimpleNamespace(
                    section_id="2",
                    title="技术架构",
                    content_md="## 技术架构\n\n说明系统架构。",
                    citation_refs=[
                        {
                            "evidence_id": "ev_001",
                            "source_title": "历史方案A",
                            "heading_path": ["第2章", "技术架构"],
                        }
                    ],
                ),
            ],
            snapshot={
                "exported_at": "2026-03-23T12:00:00+08:00",
                "draft_version": 2,
                "validation_report_id": str(uuid4()),
            },
        )

        self.assertIn("# 测试项目技术方案", markdown)
        self.assertIn("## 项目概述", markdown)
        self.assertIn("## 技术架构", markdown)
        self.assertIn("## 引用清单", markdown)
        self.assertIn("ev_001 / 历史方案A / 第2章 > 技术架构", markdown)


if __name__ == "__main__":
    unittest.main()
