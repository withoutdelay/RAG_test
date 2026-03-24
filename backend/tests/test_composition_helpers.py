from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.composition.outline_service import normalize_outline_payload
from app.services.composition.section_service import (
    SectionDraftService,
    build_manual_only_section_content,
    build_reuse_pack,
    build_reusable_blocks,
    build_section_asset_query,
    build_section_context,
    build_section_global_params,
    ensure_required_asset_placeholders,
)
from app.services.llm.prompts.section import build_section_prompts


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
                    "children": [
                        {
                            "title": "项目背景",
                            "purpose": "补充背景与约束。",
                        }
                    ],
                }
            ],
        }
        normalized = normalize_outline_payload(payload, project_name="测试项目")
        self.assertEqual(normalized["outline_status"], "candidate")
        self.assertEqual(normalized["generation_strategy"], "reuse_first")
        section = normalized["sections"][0]
        self.assertEqual(section["section_id"], "1")
        self.assertEqual(section["purpose"], "说明系统架构")
        self.assertEqual(section["section_class"], "architecture")
        self.assertEqual(section["reuse_level"], "high")
        self.assertEqual(section["generation_mode"], "reuse_first")
        self.assertTrue(section["asset_required"])
        self.assertIn("figure", section["expected_evidence_types"])
        self.assertTrue(section["needs_human_review"])
        self.assertIn("技术架构", section["keywords"])
        child = section["children"][0]
        self.assertEqual(child["section_id"], "1.1")
        self.assertEqual(child["generation_mode"], "baseline")

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

    def test_build_section_global_params_enriches_writer_context(self) -> None:
        params = build_section_global_params(
            {
                "project_name": "测试项目",
                "industry": "电气",
                "product_line": "hv_vfd",
                "business_objective": "提升站内自动化运行可靠性",
                "key_parameters": {"voltage_level": "110kV"},
            }
        )
        self.assertEqual(params["project_name"], "测试项目")
        self.assertEqual(params["industry"], "电气")
        self.assertEqual(params["product_line"], "hv_vfd")
        self.assertEqual(params["business_objective"], "提升站内自动化运行可靠性")
        self.assertEqual(params["voltage_level"], "110kV")

    def test_build_section_prompts_avoids_internal_process_language(self) -> None:
        system_prompt, user_prompt = build_section_prompts(
            section={
                "title": "硬件配置清单",
                "description": "列出关键设备与配套建议。",
                "keywords": ["硬件配置清单", "table"],
                "generation_mode": "reuse_first",
            },
            global_params={"project_name": "测试项目", "industry": "电气"},
            retrieved_context="历史方案A 配置清单: 包含设备与数量",
            outline_title="测试项目技术方案",
            recommended_assets=[
                {
                    "title": "电机参数表",
                    "page_no": 12,
                    "heading_path": "4.3 电机技术参数",
                    "usage_mode": "reference_only",
                    "reason": "与当前章节高度相关",
                }
            ],
            reuse_pack={
                "generation_mode": "reuse_first",
                "must_replace_fields": ["project_name", "quantity"],
                "replacement_hints": {"project_name": "测试项目", "quantity": "2"},
                "banned_terms": ["旧项目A"],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:TABLE:asset-001]]", "title": "电机参数表"}
                ],
                "reusable_blocks": [
                    {
                        "source_title": "历史方案A",
                        "heading_path": ["4.3", "设备配置"],
                        "content_md": "推荐沿用高压变频器双机冗余配置。",
                        "reusability_score": 0.92,
                        "must_replace_fields": ["project_name", "quantity"],
                    }
                ],
            },
        )
        self.assertIn("客户外发口径", system_prompt)
        self.assertIn("优先复用给定复用块", system_prompt)
        self.assertIn("已确认的设备型号和数量", system_prompt)
        self.assertIn("建议参考资产", user_prompt)
        self.assertIn("复用包", user_prompt)
        self.assertIn("必须替换字段", user_prompt)
        self.assertIn("禁止沿用词", user_prompt)
        self.assertIn("[[ASSET:TABLE:asset-001]]", user_prompt)
        self.assertIn("电机参数表", user_prompt)
        self.assertIn("不要解释写作过程", user_prompt)

    def test_build_section_asset_query_merges_section_and_project_context(self) -> None:
        query = build_section_asset_query(
            section={
                "title": "技术架构",
                "purpose": "说明系统架构与关键接口",
                "keywords": ["IEC 61850", "站控层"],
            },
            global_params={
                "project_name": "湛江中纸项目",
                "product_line": "hv_vfd",
                "industry": "电气",
            },
        )
        self.assertIn("技术架构", query)
        self.assertIn("IEC 61850", query)
        self.assertIn("湛江中纸项目", query)

    def test_build_reusable_blocks_prefers_raw_content_and_replace_fields(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_001",
                        "type": "section",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "heading_path": ["第4章", "技术架构"],
                        "summary": "摘要",
                        "raw_content": "项目名称：旧项目A\n采用双机冗余架构，电压等级为10kV。",
                        "reusability_score": 0.88,
                        "metadata": {"front_matter": False, "needs_asset_lookup": False},
                    }
                ]
            }
        )
        blocks = build_reusable_blocks(
            section={"title": "技术架构", "expected_evidence_types": ["section"]},
            evidence_bundle=bundle,
            global_params={"project_name": "新项目", "voltage_level": "10kV"},
        )
        self.assertEqual(len(blocks), 1)
        self.assertIn("双机冗余架构", blocks[0]["content_md"])
        self.assertIn("project_name", blocks[0]["must_replace_fields"])
        self.assertIn("voltage_level", blocks[0]["must_replace_fields"])
        self.assertIn("旧项目A", blocks[0]["banned_terms"])

    def test_build_manual_only_section_content_includes_assets_and_reuse_hint(self) -> None:
        reuse_pack = build_reuse_pack(
            section={"title": "商务条款", "generation_mode": "manual_only"},
            global_params={"project_name": "测试项目"},
            reusable_blocks=[
                {
                    "source_title": "历史方案B",
                    "heading_path": ["第8章", "商务条款"],
                    "reusability_score": 0.75,
                }
            ],
            recommended_assets=[
                {
                    "asset_type": "table",
                    "asset_id": "asset-001",
                    "title": "报价清单模板",
                }
            ],
        )
        content = build_manual_only_section_content(
            section={"title": "商务条款"},
            reuse_pack=reuse_pack,
        )
        self.assertIn("人工编写", content)
        self.assertIn("[[ASSET:TABLE:asset-001]]", content)
        self.assertIn("历史方案B", content)

    def test_ensure_required_asset_placeholders_appends_missing_placeholders(self) -> None:
        content = ensure_required_asset_placeholders(
            content_md="## 技术架构\n\n正文内容。",
            reuse_pack={
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "系统架构图"},
                    {"placeholder": "[[ASSET:TABLE:asset-002]]", "title": "接口参数表"},
                ]
            },
        )
        self.assertIn("### 建议插入图表", content)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", content)
        self.assertIn("[[ASSET:TABLE:asset-002]]", content)


if __name__ == "__main__":
    unittest.main()
