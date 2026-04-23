from __future__ import annotations

import unittest

from app.services.knowledge.wiki_prior_eval import (
    build_holdout_query_section,
    evaluate_block_ranking,
    select_holdout_sections,
    summarize_eval_records,
)


class KnowledgeWikiPriorEvalTests(unittest.TestCase):
    def test_select_holdout_sections_prefers_specific_leaf_sections(self) -> None:
        sections = [
            {
                "title": "系统概述",
                "section_type": "overall_solution",
                "equipment_type": "generic",
                "section_summary": "说明项目总体情况。",
                "level": 1,
                "section_path": "第一章 系统概述",
                "children": [],
            },
            {
                "title": "主回路系统方案",
                "section_type": "main_circuit_scheme",
                "equipment_type": "vfd",
                "section_summary": "说明高压变频器主回路、功率单元和旁路切换逻辑。",
                "level": 2,
                "section_path": "第二章 > 主回路系统方案",
                "children": [],
            },
            {
                "title": "变频器主要数据",
                "section_type": "vfd_spec",
                "equipment_type": "vfd",
                "section_summary": "说明高压变频器额定容量、冷却方式和防护等级。",
                "level": 3,
                "section_path": "第二章 > 主回路系统方案 > 变频器主要数据",
                "children": [],
            },
            {
                "title": "公司简介",
                "section_type": "company_profile",
                "equipment_type": "generic",
                "section_summary": "介绍公司情况。",
                "level": 1,
                "section_path": "封面 > 公司简介",
                "children": [],
            },
        ]

        selected = select_holdout_sections(sections, limit=2)

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["title"], "变频器主要数据")
        self.assertEqual(len({item["section_type"] for item in selected}), 2)
        self.assertNotIn("公司简介", [item["title"] for item in selected])

    def test_build_holdout_query_section_uses_domain_terms_and_hints(self) -> None:
        query_section = build_holdout_query_section(
            section={
                "title": "主回路系统方案",
                "section_summary": "说明高压变频器主回路结构、功率单元配置和旁路切换逻辑。",
                "domain_terms": ["高压变频器", "功率单元"],
                "taxonomy_hints": ["移相整流变压器"],
                "heading_aliases": ["主回路方案"],
                "section_type": "main_circuit_scheme",
                "equipment_type": "vfd",
                "content_form": "narrative",
            },
            document_name="测试文档.docx",
        )

        self.assertEqual(query_section["title"], "主回路系统方案")
        self.assertIn("高压变频器", query_section["keywords"])
        self.assertIn("功率单元", query_section["keywords"])
        self.assertIn("移相整流变压器", query_section["keywords"])
        self.assertIn("section", query_section["expected_evidence_types"])
        self.assertEqual(query_section["target_section_type"], "main_circuit_scheme")
        self.assertEqual(query_section["target_equipment_type"], "vfd")

    def test_build_holdout_query_section_strips_document_name_and_path_noise(self) -> None:
        query_section = build_holdout_query_section(
            section={
                "title": "输出变压器技术规范",
                "section_summary": (
                    "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx\n"
                    "5 输入/输出变压器技术规范 > 5.2 输出变压器技术规范\n"
                    "输出变压器技术规范\n"
                    "变压器技术 变压器"
                ),
                "taxonomy_hints": ["变压器技术", "变压器"],
                "section_type": "transformer_spec",
                "equipment_type": "transformer",
                "content_form": "narrative",
            },
            document_name="临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
        )

        self.assertEqual(query_section["purpose"], "变压器技术 变压器")

    def test_evaluate_block_ranking_collects_prior_boost_and_match_rank(self) -> None:
        metrics = evaluate_block_ranking(
            query_section={
                "target_section_type": "main_circuit_scheme",
                "target_equipment_type": "vfd",
            },
            blocks=[
                {
                    "source_title": "历史方案A",
                    "section_path": "4 主回路系统方案",
                    "selection_score": 0.93,
                    "selection_score_breakdown": {
                        "knowledge_wiki_prior_total": 0.07,
                        "knowledge_wiki_product_match": 0.03,
                        "knowledge_wiki_module_match": 0.04,
                    },
                    "metadata": {
                        "section_type": "main_circuit_scheme",
                        "equipment_type": "vfd",
                    },
                },
                {
                    "source_title": "历史方案B",
                    "selection_score": 0.82,
                    "selection_score_breakdown": {
                        "knowledge_wiki_prior_total": 0.0,
                    },
                    "metadata": {
                        "section_type": "overall_solution",
                        "equipment_type": "generic",
                    },
                },
            ],
        )

        self.assertTrue(metrics["top1_section_type_match"])
        self.assertTrue(metrics["top1_equipment_type_match"])
        self.assertEqual(metrics["first_section_type_match_rank"], 1)
        self.assertEqual(metrics["prior_hit_block_count"], 1)
        self.assertEqual(metrics["top1_prior_boost"], 0.07)
        self.assertEqual(metrics["total_prior_boost"], 0.07)

    def test_summarize_eval_records_counts_improvement(self) -> None:
        summary = summarize_eval_records(
            [
                {
                    "case_candidate_count": 2,
                    "target_equipment_type": "vfd",
                    "baseline": {
                        "top1_section_type_match": False,
                        "top3_section_type_hit": True,
                        "top1_equipment_type_match": False,
                        "top3_equipment_type_hit": False,
                        "first_section_type_match_rank": 2,
                        "first_equipment_type_match_rank": None,
                        "top1_source_title": "历史方案概述",
                    },
                    "with_prior": {
                        "top1_section_type_match": True,
                        "top3_section_type_hit": True,
                        "top1_equipment_type_match": True,
                        "top3_equipment_type_hit": True,
                        "first_section_type_match_rank": 1,
                        "first_equipment_type_match_rank": 1,
                        "prior_hit_block_count": 1,
                        "total_prior_boost": 0.07,
                        "top1_source_title": "历史方案主回路",
                    },
                }
            ]
        )

        self.assertEqual(summary["sections_with_case_candidates"], 1)
        self.assertEqual(summary["baseline"]["top3_section_type_hit"], 1)
        self.assertEqual(summary["with_prior"]["top1_section_type_match"], 1)
        self.assertEqual(summary["with_prior"]["prior_hit_sections"], 1)
        self.assertEqual(summary["with_prior"]["total_prior_boost"], 0.07)
        self.assertEqual(summary["deltas"]["section_match_rank_improved"], 1)
        self.assertEqual(summary["deltas"]["equipment_match_rank_improved"], 1)
        self.assertEqual(summary["deltas"]["top1_changed"], 1)
        self.assertEqual(summary["deltas"]["top1_section_match_gained"], 1)


if __name__ == "__main__":
    unittest.main()
