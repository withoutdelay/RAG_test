from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.requirement.service import (
    build_clarification_items,
    build_requirement_content,
    derive_business_objective,
    looks_like_internal_objective,
    resolve_clarification_state,
)
from app.services.retrieval.service import build_evidence_items, build_requirement_query


class RequirementPipelineHelperTests(unittest.TestCase):
    def test_derive_business_objective_filters_internal_workflow_description(self) -> None:
        project = SimpleNamespace(
            name="测试项目",
            description="生成完整导出稿并做逐章质量review。",
            product_line="hv_vfd",
            industry="电气",
        )
        objective = derive_business_objective(
            project=project,
            source_excerpt="本项目面向110kV变电站场景，提供综合自动化与高压变频器配套方案。",
        )
        self.assertEqual(objective, "本项目面向110kV变电站场景，提供综合自动化与高压变频器配套方案")

    def test_looks_like_internal_objective_detects_internal_terms(self) -> None:
        self.assertTrue(looks_like_internal_objective("用于导出稿联调和review验证"))
        self.assertFalse(looks_like_internal_objective("提升变电站自动化运行可靠性"))

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

    def test_build_requirement_content_uses_excerpt_when_description_is_internal(self) -> None:
        project = SimpleNamespace(
            name="测试项目",
            description="用于smoke和导出联调。",
            product_line="hv_vfd",
            industry="电气",
        )
        content = build_requirement_content(
            project=project,
            source_excerpt="本项目面向110kV变电站场景，提供综合自动化与高压变频器配套方案。",
        )
        self.assertEqual(content["business_objective"], "本项目面向110kV变电站场景，提供综合自动化与高压变频器配套方案")


if __name__ == "__main__":
    unittest.main()
