from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.services.composition.outline_service import OutlineService, build_outline_inputs, normalize_outline_payload
from app.services.composition.section_service import (
    SectionDraftService,
    _filter_reuse_blocks_for_assembly,
    _build_asset_search_context,
    _normalize_technical_spacing,
    build_extractive_reuse_section_content,
    build_manual_only_section_content,
    build_reuse_pack,
    build_reuse_citations,
    build_reusable_blocks,
    build_reuse_refinement_instruction,
    build_section_asset_query,
    build_section_context,
    build_section_global_params,
    ensure_required_asset_placeholders,
    filter_recommended_assets_for_section,
    prioritize_recommended_assets,
    sanitize_generated_section_content,
    select_preferred_reuse_content,
    should_use_extractive_reuse,
    tighten_recommended_assets_for_reuse,
)
from app.services.llm.prompts.section import build_section_prompts
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


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

    def test_normalize_outline_payload_routes_supply_scope_to_reuse_first(self) -> None:
        payload = {
            "title": "测试项目技术方案",
            "sections": [
                {
                    "title": "供货范围与系统组成",
                    "description": "说明主要设备供货范围和系统组成。",
                }
            ],
        }

        normalized = normalize_outline_payload(payload, project_name="测试项目")
        section = normalized["sections"][0]

        self.assertEqual(section["section_class"], "configuration")
        self.assertEqual(section["customer_specificity"], "medium")
        self.assertEqual(section["generation_mode"], "reuse_first")
        self.assertIn("table", section["expected_evidence_types"])

    def test_normalize_outline_payload_routes_interface_section_to_reuse_first(self) -> None:
        payload = {
            "title": "测试项目技术方案",
            "sections": [
                {
                    "title": "控制接口与通讯方案",
                    "description": "说明DCS/PLC接口和通讯方式。",
                }
            ],
        }

        normalized = normalize_outline_payload(payload, project_name="测试项目")
        section = normalized["sections"][0]

        self.assertEqual(section["section_class"], "architecture")
        self.assertEqual(section["customer_specificity"], "low")
        self.assertEqual(section["generation_mode"], "reuse_first")
        self.assertIn("parameter", section["expected_evidence_types"])

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

    def test_build_outline_inputs_includes_case_examples_and_raw_content(self) -> None:
        requirement_card = SimpleNamespace(
            content={
                "project_name": "测试项目",
                "business_objective": "完成高压电机软起与联锁控制",
                "industry": "冶金",
                "product_line": "lci",
                "source_excerpt": "当前项目需要围绕高压电机启动、联锁、保护和变压器配置形成完整技术方案。",
                "key_parameters": {"voltage_level": "10kV"},
            }
        )
        evidence_bundle = SimpleNamespace(
            content={
                "case_candidates": [
                    {
                        "file_name": "高浓磨机LCI方案.pdf",
                        "score": 0.91,
                        "top_level_titles": ["LCI 变频软起系统方案", "变压器技术规范", "运行保护与联锁设计"],
                    }
                ],
                "results": [
                    {
                        "source_title": "高浓磨机LCI方案.pdf",
                        "summary": "摘要",
                        "raw_content": "LCI 变频软起系统采用晶闸管整流逆变结构，支持同步切换和联锁保护。",
                    }
                ],
            }
        )

        instructions, global_params, rfp_context, outline_examples = build_outline_inputs(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
        )

        self.assertIn("测试项目", instructions)
        self.assertEqual(global_params["voltage_level"], "10kV")
        self.assertIn("晶闸管整流逆变结构", rfp_context)
        self.assertEqual(outline_examples[0]["file_name"], "高浓磨机LCI方案.pdf")
        self.assertIn("LCI 变频软起系统方案", outline_examples[0]["top_level_titles"])

    def test_parse_outline_response_accepts_outline_array_shape(self) -> None:
        service = OutlineService()
        parsed = service._parse_outline_response(
            json.dumps(
                {
                    "project_name": "高炉鼓风机高压电机及启动装置成套技术方案大纲",
                    "outline": [
                        {
                            "title": "一、项目概述与编制依据",
                            "description": "说明项目背景与编制依据。",
                            "keywords": ["项目背景", "编制依据"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            project_name="测试项目",
        )
        self.assertEqual(parsed["title"], "高炉鼓风机高压电机及启动装置成套技术方案大纲")
        self.assertEqual(len(parsed["sections"]), 1)
        self.assertEqual(parsed["sections"][0]["title"], "一、项目概述与编制依据")

    def test_parse_outline_response_accepts_chapters_shape(self) -> None:
        service = OutlineService()
        parsed = service._parse_outline_response(
            json.dumps(
                {
                    "project_name": "临沂钢铁鼓风机项目",
                    "document_title": "临沂钢铁鼓风机高压电机及启动装置成套技术方案",
                    "chapters": [
                        {
                            "title": "一、项目概述与编制说明",
                            "description": "说明项目背景、建设目标和编制依据。",
                            "keywords": ["项目背景", "编制依据"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            project_name="测试项目",
        )
        self.assertEqual(parsed["title"], "临沂钢铁鼓风机高压电机及启动装置成套技术方案")
        self.assertEqual(len(parsed["sections"]), 1)
        self.assertEqual(parsed["sections"][0]["title"], "一、项目概述与编制说明")

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
        self.assertIn("不要复述任务说明", user_prompt)

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
        self.assertGreaterEqual(blocks[0]["selection_score"], blocks[0]["reusability_score"])

    def test_prioritize_recommended_assets_dedupes_same_heading_asset(self) -> None:
        prioritized = prioritize_recommended_assets(
            [
                {
                    "document_name": "历史方案A.pdf",
                    "heading_path": "5. 控制信号接口说明",
                    "title": "5. 控制信号接口说明",
                    "visual_role": "engineering_figure",
                    "score": 0.72,
                },
                {
                    "document_name": "历史方案A.pdf",
                    "heading_path": "5. 控制信号接口说明",
                    "title": "5. 控制信号接口说明",
                    "visual_role": "engineering_figure",
                    "score": 0.7,
                },
            ],
            limit=3,
        )

        self.assertEqual(len(prioritized), 1)

    def test_filter_reuse_blocks_for_supply_scope_skips_process_narrative_support(self) -> None:
        reusable_blocks = [
            {
                "selection_score": 1.0,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 10,
                "heading_path": ["2 供货范围 Scopes of supply"],
                "metadata": {
                    "section_type": "supply_scope",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            },
            {
                "selection_score": 0.78,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 11,
                "heading_path": ["3.2 启动和同步过程描述 Description of Start and Sychronization"],
                "metadata": {
                    "section_type": "control_logic",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            },
            {
                "selection_score": 0.76,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 12,
                "heading_path": ["5.1 乙方提供设备清单"],
                "metadata": {
                    "section_type": "bom_or_supply_list",
                    "equipment_type": "vfd",
                    "content_form": "bom_table",
                },
            },
        ]

        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=reusable_blocks,
            target_taxonomy=infer_target_taxonomy(
                {
                    "title": "供货范围与系统组成",
                    "purpose": "说明主要设备供货范围和系统组成。",
                    "expected_evidence_types": ["table", "parameter", "section"],
                }
            ),
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertNotIn("3.2 启动和同步过程描述 Description of Start and Sychronization", headings)
        self.assertIn("5.1 乙方提供设备清单", headings)

    def test_build_reusable_blocks_can_prefer_case_library_matches(self) -> None:
        bundle = SimpleNamespace(content={"results": []})
        blocks = build_reusable_blocks(
            section={
                "title": "控制接口与通讯方案",
                "purpose": "说明与 DCS/PLC 的接口、通信方式和状态反馈。",
                "keywords": ["DCS", "PLC", "通信接口"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=bundle,
            global_params={"project_name": "新项目", "product_line": "hv_vfd"},
            case_library_matches=[
                {
                    "sample_id": "sample-001",
                    "file_name": "历史方案接口章节",
                    "chunk_index": 7,
                    "chunk_type": "PLAIN",
                    "heading_path": "5. 变频启动装置与上位机的接口",
                    "content": "变频器可提供开关量、模拟量至DCS系统，并支持 PLC 与通讯接口联动。",
                    "score": 0.86,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                }
            ],
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["source_title"], "历史方案接口章节")
        self.assertIn("section_type_match", blocks[0]["selection_reasons"])
        self.assertIn("通讯接口", blocks[0]["content_md"])

    def test_build_reusable_blocks_reranks_by_section_match(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_generic",
                        "type": "section",
                        "source_doc_id": "doc_generic",
                        "source_title": "历史方案通用章",
                        "heading_path": ["第2章", "项目概述"],
                        "summary": "摘要",
                        "raw_content": "本项目总体说明与建设背景，适用于多个行业场景。",
                        "reusability_score": 0.93,
                        "metadata": {
                            "front_matter": False,
                            "needs_asset_lookup": False,
                            "section_type": "project_overview",
                            "equipment_type": "generic",
                            "content_form": "narrative",
                        },
                    },
                    {
                        "evidence_id": "ev_arch",
                        "type": "section",
                        "source_doc_id": "doc_arch",
                        "source_title": "历史方案技术章",
                        "heading_path": ["第4章", "技术架构"],
                        "summary": "摘要",
                        "raw_content": "技术架构采用站控层、间隔层和网络层分层设计，支持 IEC 61850 与高压变频器联动。",
                        "reusability_score": 0.78,
                        "metadata": {
                            "front_matter": False,
                            "needs_asset_lookup": False,
                            "section_type": "communication_interface",
                            "equipment_type": "vfd",
                            "content_form": "narrative",
                        },
                    },
                ]
            }
        )

        blocks = build_reusable_blocks(
            section={
                "title": "技术架构",
                "purpose": "说明系统架构与关键接口",
                "keywords": ["IEC 61850", "站控层", "高压变频器"],
                "section_class": "architecture",
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=bundle,
            global_params={"product_line": "hv_vfd", "industry": "电气"},
            limit=2,
        )

        self.assertEqual(blocks[0]["block_id"], "ev_arch")
        self.assertIn("heading_match", blocks[0]["selection_reasons"])
        self.assertIn("equipment_type_match", blocks[0]["selection_reasons"])
        self.assertGreater(blocks[0]["selection_score"], blocks[1]["selection_score"])

    def test_build_reusable_blocks_penalizes_formula_like_interface_block_when_narrative_exists(self) -> None:
        bundle = SimpleNamespace(content={"results": []})

        blocks = build_reusable_blocks(
            section={
                "title": "控制接口与通讯方案",
                "purpose": "说明与 DCS/PLC 的接口、通信方式和状态反馈。",
                "keywords": ["DCS", "PLC", "通信接口"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=bundle,
            global_params={"product_line": "hv_vfd"},
            case_library_matches=[
                {
                    "sample_id": "sample-interface-good",
                    "file_name": "历史方案接口章节",
                    "chunk_index": 7,
                    "chunk_type": "PLAIN",
                    "heading_path": "5. 变频启动装置与上位机的接口",
                    "content": "变频器可提供开关量、模拟量至DCS系统，并支持 PLC 与通讯接口联动。",
                    "score": 0.74,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "communication_interface",
                    "equipment_type": "dcs_plc_interface",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-interface-noisy",
                    "file_name": "历史方案性能章节",
                    "chunk_index": 9,
                    "chunk_type": "PLAIN",
                    "heading_path": "4. 变频器性能要求",
                    "content": "DI/DO AI/AO 通讯接口公式说明 THD≤3%，P=U×I。",
                    "score": 0.79,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "formula",
                },
            ],
            limit=2,
        )

        self.assertEqual(blocks[0]["source_title"], "历史方案接口章节")
        self.assertIn("content_form_match", blocks[0]["selection_reasons"])
        self.assertIn("content_form_mismatch_penalty", blocks[1]["selection_reasons"])

    def test_build_reuse_citations_prefers_reuse_blocks_for_reuse_first(self) -> None:
        citations = build_reuse_citations(
            [
                {
                    "block_id": "reuse_001",
                    "source_doc_id": "doc_1",
                    "source_title": "历史方案A",
                    "heading_path": ["2.2 主回路方案说明"],
                    "selection_score": 0.92,
                    "reusability_score": 0.71,
                    "block_type": "plain",
                },
                {
                    "block_id": "reuse_002",
                    "source_doc_id": "doc_1",
                    "source_title": "历史方案A",
                    "heading_path": ["2.2 主回路方案说明"],
                    "selection_score": 0.88,
                    "reusability_score": 0.66,
                    "block_type": "plain",
                },
            ]
        )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["source_title"], "历史方案A")
        self.assertEqual(citations[0]["heading_path"], ["2.2 主回路方案说明"])

    def test_prioritize_recommended_assets_demotes_page_furniture(self) -> None:
        assets = prioritize_recommended_assets(
            [
                {
                    "asset_id": "asset_furniture",
                    "asset_type": "figure",
                    "visual_role": "page_furniture",
                    "score": 0.91,
                },
                {
                    "asset_id": "asset_engineering",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "score": 0.72,
                },
            ]
        )

        self.assertEqual(assets[0]["asset_id"], "asset_engineering")

    def test_tighten_recommended_assets_for_reuse_keeps_same_family_assets(self) -> None:
        tightened = tighten_recommended_assets_for_reuse(
            [
                {
                    "asset_id": "asset_main",
                    "document_name": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": "2.2 高压变频器主回路方案说明",
                    "metadata": {"section_type": "main_circuit_scheme"},
                },
                {
                    "asset_id": "asset_transformer",
                    "document_name": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": "2 ．移 相变压器原理",
                    "metadata": {"section_type": "transformer_spec"},
                },
            ],
            section={
                "title": "主回路系统方案",
                "purpose": "说明主回路结构与一次接线方案。",
                "expected_evidence_types": ["section", "figure"],
            },
            reusable_blocks=[
                {
                    "source_title": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": ["2.2", "高压变频器主回路方案说明"],
                },
                {
                    "source_title": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": ["2.3", "高压变频器主要技术参数"],
                },
            ],
        )

        self.assertEqual([item["asset_id"] for item in tightened], ["asset_main"])

    def test_filter_recommended_assets_for_section_skips_document_index_table(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_index",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "目 录",
                    "metadata": {"label": "document_index"},
                },
                {
                    "asset_id": "asset_real",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "2.4 控制信号接口说明",
                    "metadata": {"label": "table"},
                },
            ],
            section={
                "title": "控制接口与通讯方案",
                "purpose": "说明DCS接口与通讯信号。",
                "expected_evidence_types": ["section", "parameter"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_real"])

    def test_sanitize_generated_section_content_removes_prompt_leakage(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="主回路系统方案",
            content_md=(
                "## 主回路系统方案\n\n"
                "本节基于QWEN模型草拟，重点覆盖：目标章节标题：主回路系统方案。\n\n"
                "参考摘要：暂无补充资料。\n\n"
                "主回路采用一拖一输入输出隔离方案。\n"
            ),
        )

        self.assertNotIn("本节基于QWEN模型草拟", cleaned)
        self.assertNotIn("参考摘要：暂无补充资料", cleaned)
        self.assertIn("主回路采用一拖一输入输出隔离方案", cleaned)

    def test_build_section_context_prefers_supply_list_table_by_taxonomy(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_text",
                        "type": "section",
                        "source_doc_id": "doc_text",
                        "source_title": "历史方案文本章",
                        "heading_path": ["第2章", "项目概述"],
                        "summary": "摘要",
                        "raw_content": "项目背景与建设目标说明。",
                        "relevance_score": 0.81,
                        "reusability_score": 0.81,
                        "metadata": {
                            "front_matter": False,
                            "needs_asset_lookup": False,
                            "section_type": "project_overview",
                            "equipment_type": "generic",
                            "content_form": "narrative",
                        },
                    },
                    {
                        "evidence_id": "ev_table",
                        "type": "table",
                        "source_doc_id": "doc_table",
                        "source_title": "历史方案供货表",
                        "heading_path": ["第11章", "供货范围与主要设备清单"],
                        "summary": "摘要",
                        "raw_content": "| 序号 | 设备名称 | 数量 |\n| --- | --- | --- |\n| 1 | 变频器 | 2 |",
                        "relevance_score": 0.75,
                        "reusability_score": 0.75,
                        "metadata": {
                            "front_matter": False,
                            "needs_asset_lookup": False,
                            "section_type": "bom_or_supply_list",
                            "equipment_type": "vfd",
                            "content_form": "bom_table",
                        },
                    },
                ]
            }
        )

        context, citations = build_section_context(
            section={
                "title": "供货范围与主要设备清单",
                "purpose": "列出关键设备、数量和供货范围。",
                "expected_evidence_types": ["table", "parameter"],
            },
            evidence_bundle=bundle,
            global_params={"product_line": "hv_vfd"},
        )

        self.assertIn("历史方案供货表", context)
        self.assertEqual(citations[0]["evidence_id"], "ev_table")

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

    def test_should_use_extractive_reuse_for_technical_reuse_sections(self) -> None:
        self.assertTrue(
            should_use_extractive_reuse(
                section={
                    "title": "主回路系统方案",
                    "generation_mode": "reuse_first",
                    "section_class": "architecture",
                    "asset_required": True,
                },
                reuse_pack={
                    "reusable_blocks": [
                        {"metadata": {"section_type": "main_circuit_scheme"}},
                        {"metadata": {"section_type": "vfd_spec"}},
                    ]
                },
            )
        )

    def test_build_extractive_reuse_section_content_keeps_dense_paragraphs(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "主回路系统方案",
                "purpose": "说明主回路结构与主要设备配置。",
                "keywords": ["主回路", "变频器", "旁路"],
                "section_class": "architecture",
                "expected_evidence_types": ["section", "figure"],
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["2.2", "高压变频器主回路方案说明"],
                        "metadata": {"section_type": "main_circuit_scheme", "content_form": "narrative"},
                        "selection_score": 0.92,
                        "content_md": (
                            "高压变频器主回路采用移相整流变压器配合功率单元串联结构，"
                            "输入侧设置隔离开关、快速熔断器和真空接触器，输出侧经旁路切换柜送至同步电机。\n\n"
                            "主回路具备检修隔离、旁路切换和故障闭锁功能，满足鼓风机连续运行要求。"
                        ),
                    },
                    {
                        "heading_path": ["2.1", "高压变频器选型"],
                        "metadata": {"section_type": "main_circuit_scheme", "content_form": "bom_table"},
                        "selection_score": 0.89,
                        "content_md": (
                            "| 项目 | 配置 |\n| --- | --- |\n| 整流变压器 | 1套 |\n| 旁路柜 | 1面 |"
                        ),
                    },
                    {
                        "heading_path": ["2.3", "高压变频器主要技术参数"],
                        "metadata": {"section_type": "main_circuit_scheme", "content_form": "parameter_table"},
                        "selection_score": 0.87,
                        "content_md": (
                            "| 参数 | 数值 |\n| --- | --- |\n| 额定电压 | 10kV |\n| 额定功率 | 4500kW |"
                        ),
                    },
                    {
                        "heading_path": ["4.1", "旁路切换逻辑"],
                        "metadata": {"section_type": "control_logic", "content_form": "narrative"},
                        "selection_score": 0.74,
                        "content_md": (
                            "旁路切换时，系统先完成主回路状态确认，再投入旁路接触器，避免带载误切换。"
                        ),
                    },
                ],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "主回路示意图"}
                ],
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertIn("## 主回路系统方案", content)
        self.assertIn("### 主回路结构与运行切换", content)
        self.assertIn("### 设备选型与容量配置", content)
        self.assertIn("### 关键技术参数", content)
        self.assertIn("移相整流变压器", content)
        self.assertIn("旁路切换时", content)

    def test_build_extractive_reuse_section_content_simplifies_supply_scope_output(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "供货范围与系统组成",
                "purpose": "说明主要设备供货范围和系统组成。",
                "keywords": ["供货范围", "设备清单", "配置清单"],
                "section_class": "configuration",
                "generation_mode": "reuse_first",
                "expected_evidence_types": ["table", "parameter", "section"],
                "asset_required": True,
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["2", "供货范围 Scopes of supply"],
                        "metadata": {"section_type": "supply_scope", "content_form": "narrative"},
                        "selection_score": 1.0,
                        "content_md": (
                            "**Remark: ABB only provide the equipment in the table, others included in the drive system is not ABB’s scope, such as:**\n"
                            "**备注：ABB 仅提供供货范围表内的设备，该套变频系统的如下部分不在 ABB 的供货范围内：**\n"
                            "- Control cable between system components\n"
                            "设备之间的控制电缆"
                        ),
                    },
                    {
                        "heading_path": ["5.1", "乙方提供设备清单"],
                        "metadata": {"section_type": "bom_or_supply_list", "content_form": "bom_table"},
                        "selection_score": 0.96,
                        "content_md": (
                            "| 序号 | 设备名称 | 供货型号 | 数量 |\n"
                            "| --- | --- | --- | --- |\n"
                            "| 1 | 高压变频柜 | GBT-MVSG0900-10/10C-Y | 2套 |"
                        ),
                    },
                    {
                        "heading_path": ["2", "供货范围 Scopes of supply"],
                        "metadata": {"section_type": "supply_scope", "content_form": "bom_table"},
                        "selection_score": 0.88,
                        "content_md": (
                            "| Index | Component | Type | Qty |\n"
                            "| --- | --- | --- | --- |\n"
                            "| 1 | Converter | LCI.SO A1212-211N465 | 1 |"
                        ),
                    },
                ],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:TABLE:asset-001]]", "title": "乙方提供设备清单"}
                ],
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertIn("## 供货范围与系统组成", content)
        self.assertIn("### 主要设备及供货范围", content)
        self.assertIn("备注：ABB 仅提供供货范围表内的设备", content)
        self.assertIn("| 序号 | 设备名称 | 供货型号 | 数量 |", content)
        self.assertNotIn("Remark: ABB only provide", content)
        self.assertNotIn("| Index | Component | Type | Qty |", content)
        self.assertIn("[[ASSET:TABLE:asset-001]]", content)

    def test_build_asset_search_context_anchors_on_top_reuse_blocks(self) -> None:
        context = _build_asset_search_context(
            section={
                "title": "主回路系统方案",
                "expected_evidence_types": ["section", "figure"],
                "keywords": ["主回路", "旁路切换"],
            },
            reusable_blocks=[
                {
                    "source_title": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": ["2.2", "高压变频器主回路方案说明"],
                },
                {
                    "source_title": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
                    "heading_path": ["2.3", "高压变频器主要技术参数"],
                },
            ],
        )

        self.assertEqual(context["section_title"], "主回路系统方案")
        self.assertEqual(
            context["anchor_document_names"],
            ["临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx"],
        )
        self.assertIn("2.2 > 高压变频器主回路方案说明", context["anchor_heading_paths"])

    def test_select_preferred_reuse_content_falls_back_when_rewrite_too_thin(self) -> None:
        assembled = (
            "## 主回路系统方案\n\n"
            "### 高压变频器主回路方案说明\n\n"
            "高压变频器主回路采用移相整流变压器配合功率单元串联结构，"
            "输入侧设置隔离开关、快速熔断器和真空接触器，输出侧经旁路切换柜送至同步电机。\n\n"
            "主回路具备检修隔离、旁路切换和故障闭锁功能，满足鼓风机连续运行要求。\n"
        )
        rewritten = "## 主回路系统方案\n\n系统方案满足项目运行需求。\n"

        preferred = select_preferred_reuse_content(
            assembled_content=assembled,
            rewritten_content=rewritten,
            section_title="主回路系统方案",
        )

        self.assertIn("移相整流变压器", preferred)
        self.assertNotIn("系统方案满足项目运行需求", preferred)

    def test_select_preferred_reuse_content_falls_back_when_rewrite_leaks_task_labels(self) -> None:
        assembled = (
            "## 主回路系统方案\n\n"
            "高压变频器主回路采用移相整流变压器配合功率单元串联结构，"
            "输入侧设置隔离开关和快速熔断器。\n"
        )
        rewritten = (
            "## 主回路系统方案\n\n"
            "章节标题: 主回路系统方案\n"
            "当前关键参数: project_name=测试项目\n"
            "已根据要求完成重写。\n"
        )

        preferred = select_preferred_reuse_content(
            assembled_content=assembled,
            rewritten_content=rewritten,
            section_title="主回路系统方案",
        )

        self.assertIn("移相整流变压器", preferred)
        self.assertNotIn("章节标题:", preferred)

    def test_build_reuse_refinement_instruction_mentions_density_and_placeholders(self) -> None:
        instruction = build_reuse_refinement_instruction(
            section={"parameter_sensitive": True},
            reuse_pack={
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "主回路示意图"}
                ]
            },
        )

        self.assertIn("不要压缩信息密度", instruction)
        self.assertIn("保留已有 [[ASSET:...]] 占位符", instruction)
        self.assertIn("不得虚构未确认参数", instruction)

    def test_filter_recommended_assets_for_section_drops_certification_noise(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_cert",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "产品认证",
                    "heading_path": "四、产品认证",
                },
                {
                    "asset_id": "asset_main",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "主回路一次原理图",
                    "heading_path": "2.2 主回路方案说明",
                },
            ],
            section={
                "title": "主回路系统方案",
                "section_class": "architecture",
                "generation_mode": "reuse_first",
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["asset_id"], "asset_main")

    def test_normalize_technical_spacing_merges_split_acronyms(self) -> None:
        normalized = _normalize_technical_spacing(
            "断开 隔离 刀闸Q S1 ,Q S2 即可，通讯方式为 MODB US 协议，支持 E PO 信号。"
        )

        self.assertIn("QS1", normalized)
        self.assertIn("QS2", normalized)
        self.assertIn("MODBUS", normalized)
        self.assertIn("EPO", normalized)


if __name__ == "__main__":
    unittest.main()
