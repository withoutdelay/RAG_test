from __future__ import annotations

import unittest

from app.services.requirement.service import (
    build_clarification_items,
    build_requirement_content,
    resolve_clarification_state,
)
from app.services.retrieval.service import build_evidence_items, build_requirement_query


class RequirementPipelineHelperTests(unittest.TestCase):
    def test_build_clarification_items_marks_missing_product_line_as_blocking(self) -> None:
        missing_items, blocking_items = build_clarification_items(
            {
                "project_name": "测试项目",
                "product_line": None,
                "industry": "电气",
                "business_objective": "替换老旧设备",
            }
        )
        self.assertEqual(len(blocking_items), 1)
        self.assertEqual(blocking_items[0]["field_name"], "product_line")
        self.assertEqual(blocking_items[0]["status"], "open")
        self.assertTrue(any(item["field_name"] == "product_line" for item in missing_items))

    def test_build_requirement_query_prefers_structured_fields(self) -> None:
        query = build_requirement_query(
            {
                "project_name": "华东客户项目",
                "product_line": "hv_vfd",
                "industry": "电气",
                "business_objective": "提升系统稳定性",
            }
        )
        self.assertIn("hv_vfd", query)
        self.assertIn("电气", query)

    def test_build_evidence_items_translates_retrieval_results(self) -> None:
        items = build_evidence_items(
            [
                {
                    "document_id": "doc-1",
                    "document_name": "历史方案.md",
                    "heading_path": "第3章 > 控制策略",
                    "chunk_type": "TABLE",
                    "content": "控制策略摘要",
                    "score": 0.88,
                    "metadata": {"industry": "电气"},
                }
            ]
        )
        self.assertEqual(items[0]["type"], "table")
        self.assertEqual(items[0]["heading_path"], ["第3章", "控制策略"])
        self.assertAlmostEqual(items[0]["relevance_score"], 0.88)

    def test_resolve_clarification_state_recomputes_missing_items_from_content(self) -> None:
        missing_items, blocking_items = resolve_clarification_state(
            content={
                "project_name": "测试项目",
                "product_line": "hv_vfd",
                "industry": "电气",
                "business_objective": "替换老旧设备",
            }
        )
        self.assertEqual(missing_items, [])
        self.assertEqual(blocking_items, [])


if __name__ == "__main__":
    unittest.main()
