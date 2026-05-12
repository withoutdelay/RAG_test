from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.composition.outline_service import (
    OutlineService,
    _advance_project_to_outline_state,
    build_outline_inputs,
    normalize_outline_payload,
)
from app.config import get_settings
from app.services.composition.section_service import (
    _build_asset_retrieval_trace,
    _asset_is_auto_body_eligible,
    _build_child_retrieval_sections,
    _build_composition_retrieval_trace,
    _build_evidence_retrieval_trace,
    _build_generation_summary,
    _build_parameter_snapshot_section_content,
    _build_selected_block_trace,
    _deterministic_prefilter_evidence_judge_candidates,
    _enrich_table_asset_from_source_chunks,
    _compute_next_section_draft_version,
    _build_preceding_context,
    _build_preceding_context_from_existing_drafts,
    _can_short_circuit_parameter_snapshot_retrieval,
    _should_skip_optional_asset_search,
    _should_skip_reuse_scenario_noise,
    _use_deterministic_reuse_builder,
    _new_inter_section_state,
    _record_inter_section_context,
    SectionDraftService,
    _filter_reuse_blocks_for_assembly,
    _remove_mismatched_asset_placeholders,
    _normalize_invalid_asset_placeholders,
    _build_asset_search_context,
    _build_reuse_query_terms,
    _collect_parameter_evidence_candidates,
    _merge_parameter_evidence_context,
    _normalize_technical_spacing,
    _select_section_scope_candidates,
    _sync_context_after_evidence_judge,
    build_extractive_reuse_section_content,
    build_manual_only_section_content,
    build_reuse_pack,
    build_reuse_citations,
    build_reusable_blocks,
    build_generation_sections,
    build_reuse_refinement_instruction,
    build_section_asset_query,
    build_section_asset_types,
    build_section_context,
    build_section_global_params,
    build_section_reuse_query_intents,
    ensure_required_asset_placeholders,
    filter_recommended_assets_for_section,
    polish_extractive_reuse_section_content,
    prioritize_reusable_blocks_for_citations,
    prioritize_recommended_assets,
    resolve_reuse_generation_strategy,
    resolve_reuse_refinement_content,
    sanitize_generated_section_content,
    select_preferred_reuse_content,
    should_use_extractive_reuse,
    tighten_recommended_assets_for_reuse,
)
from app.services.agents.executor import ExecutorAgent
from app.services.llm.prompts.rewrite import build_rewrite_prompts
from app.services.llm.prompts.section import _build_section_guidance, build_section_prompts
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


class CompositionHelperTests(unittest.TestCase):
    def test_compute_next_section_draft_version_uses_existing_max_when_cursor_was_reset(self) -> None:
        self.assertEqual(
            _compute_next_section_draft_version(
                current_draft_version=0,
                existing_max_draft_version=1,
            ),
            2,
        )
        self.assertEqual(
            _compute_next_section_draft_version(
                current_draft_version=3,
                existing_max_draft_version=1,
            ),
            4,
        )

    def test_advance_project_to_outline_state_resets_current_draft_cursor(self) -> None:
        project = SimpleNamespace(
            current_outline_id="old-outline",
            current_draft_version=3,
            status="DRAFT_READY",
        )
        outline = SimpleNamespace(id="new-outline")

        _advance_project_to_outline_state(project=project, outline=outline, status="OUTLINE_APPROVED")

        self.assertEqual(project.current_outline_id, "new-outline")
        self.assertEqual(project.current_draft_version, 0)
        self.assertEqual(project.status, "OUTLINE_APPROVED")

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

    def test_normalize_outline_payload_routes_document_delivery_without_asset_bias(self) -> None:
        payload = {
            "title": "测试项目技术方案",
            "sections": [
                {
                    "title": "项目交付资料与文档清单",
                    "purpose": "列明设计图纸、技术说明书、操作维护手册、测试报告及合格证等交付文档。",
                    "keywords": ["交付文档", "技术图纸", "table", "parameter"],
                    "expected_evidence_types": ["table", "parameter", "section"],
                    "section_class": "configuration",
                    "asset_required": True,
                    "parameter_sensitive": True,
                }
            ],
        }

        normalized = normalize_outline_payload(payload, project_name="测试项目")
        section = normalized["sections"][0]

        self.assertEqual(section["target_section_type"], "commercial_manual_only")
        self.assertEqual(section["section_class"], "service")
        self.assertEqual(section["expected_evidence_types"], ["section"])
        self.assertFalse(section["asset_required"])
        self.assertFalse(section["parameter_sensitive"])
        self.assertNotIn("table", section["keywords"])
        self.assertNotIn("parameter", section["keywords"])

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

    def test_build_section_context_prioritizes_preferred_citation(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_001",
                        "type": "section",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "heading_path": ["第3章", "标准说明"],
                        "summary": "标准说明",
                        "relevance_score": 0.95,
                    },
                    {
                        "evidence_id": "ev_002",
                        "type": "section",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "heading_path": ["第4章", "主回路方案"],
                        "summary": "主回路说明",
                        "relevance_score": 0.72,
                    },
                ]
            }
        )

        _, citations = build_section_context(
            section={"title": "技术架构", "expected_evidence_types": ["section"]},
            evidence_bundle=bundle,
            preferred_evidence_ids={"ev_002"},
        )

        self.assertEqual(citations[0]["evidence_id"], "ev_002")
        self.assertEqual(citations[0]["excerpt"], "主回路说明")

    def test_build_section_context_drops_internal_case_summary_only_items(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "case_ev_001",
                        "type": "case_summary",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "heading_path": ["1 工厂设计环境", "2 供货范围"],
                        "raw_content": (
                            "匹配原因：query_overlap=LCI,变频器\n"
                            "可参考章节：4 LCI 变频软起系统方案"
                        ),
                        "relevance_score": 0.93,
                    }
                ]
            }
        )

        context, citations = build_section_context(
            section={"title": "综合自动化系统方案", "expected_evidence_types": ["section"]},
            evidence_bundle=bundle,
        )

        self.assertEqual(context, "")
        self.assertEqual(citations, [])

    def test_build_outline_inputs_pulls_back_half_requirements_from_source_context(self) -> None:
        """R5 #1 regression: outline prompt must read the full filtered RFP
        context, not the 600-char preview.  Otherwise the planner never sees
        back-half technical parameters (IP55 / ≥2.5MW / 评分条款)."""

        requirement_card = SimpleNamespace(
            content={
                "project_name": "后半段项目",
                "business_objective": "完成高压电机软起",
                "industry": "冶金",
                "product_line": "lci",
                # Short preview only carries the head — selector's back-half
                # picks live on ``source_context``.
                "source_excerpt": "项目背景说明。",
                "source_context": (
                    "项目背景说明。\n\n"
                    "技术参数：电机防护等级 IP55，额定功率 ≥2.5MW，必须支持双冗余控制。\n\n"
                    "评分标准：技术分占 60%。"
                ),
                "key_parameters": {"voltage_level": "10kV"},
            }
        )
        evidence_bundle = SimpleNamespace(content={})

        _, _, rfp_context, _ = build_outline_inputs(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
        )

        self.assertIn("IP55", rfp_context)
        self.assertIn("≥2.5MW", rfp_context)
        self.assertIn("评分标准", rfp_context)

    def test_build_outline_inputs_falls_back_to_legacy_excerpt(self) -> None:
        """Cards without ``source_context`` (legacy) must still flow through."""

        requirement_card = SimpleNamespace(
            content={
                "project_name": "旧需求卡",
                "business_objective": "完成项目",
                "source_excerpt": "legacy 短摘要内容。",
            }
        )
        _, _, rfp_context, _ = build_outline_inputs(
            requirement_card=requirement_card,
            evidence_bundle=SimpleNamespace(content={}),
        )
        self.assertIn("legacy 短摘要内容", rfp_context)

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
                    "project_name": "某钢铁企业鼓风机项目",
                    "document_title": "某钢铁企业鼓风机高压电机及启动装置成套技术方案",
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
        self.assertEqual(parsed["title"], "某钢铁企业鼓风机高压电机及启动装置成套技术方案")
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

    def test_section_draft_service_defers_asset_retriever_initialization(self) -> None:
        with patch(
            "app.services.composition.section_service.AssetRetrievalService",
            side_effect=AssertionError("asset retriever should be lazy"),
        ):
            service = SectionDraftService()
            with self.assertRaises(AssertionError):
                _ = service.asset_retriever

    def test_build_section_global_params_enriches_writer_context(self) -> None:
        params = build_section_global_params(
            {
                "project_name": "测试项目",
                "industry": "电气",
                "product_line": "hv_vfd",
                "business_objective": "提升站内自动化运行可靠性",
                "key_parameters": {"voltage_level": "110kV"},
                "source_excerpt": "需求原文参数摘录",
            }
        )
        self.assertEqual(params["project_name"], "测试项目")
        self.assertEqual(params["industry"], "电气")
        self.assertEqual(params["product_line"], "hv_vfd")
        self.assertEqual(params["business_objective"], "提升站内自动化运行可靠性")
        self.assertEqual(params["voltage_level"], "110kV")
        self.assertEqual(params["_source_excerpt"], "需求原文参数摘录")

    def test_build_section_global_params_prefers_full_source_context_over_preview(self) -> None:
        """R5 #1 regression: when both fields are present, the full filtered
        context wins so section parameter-evidence collection can see the
        back-half requirements that the 600-char preview drops."""

        params = build_section_global_params(
            {
                "project_name": "后半段项目",
                "source_excerpt": "短摘要不含后半段。",
                "source_context": (
                    "项目背景说明。\n\n"
                    "技术参数：电机防护等级 IP55，额定功率 ≥2.5MW，必须支持双冗余控制。"
                ),
            }
        )
        self.assertIn("IP55", params["_source_excerpt"])
        self.assertIn("≥2.5MW", params["_source_excerpt"])
        self.assertNotEqual(params["_source_excerpt"], "短摘要不含后半段。")

    def test_build_section_global_params_falls_back_to_legacy_excerpt_only_card(self) -> None:
        """Legacy cards persisted before R5 only have ``source_excerpt``;
        the helper must still feed something to ``_source_excerpt`` so the
        section pipeline does not lose context entirely."""

        params = build_section_global_params(
            {"project_name": "旧需求卡", "source_excerpt": "需求原文参数摘录"}
        )
        self.assertEqual(params["_source_excerpt"], "需求原文参数摘录")

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
                    "asset_id": "asset-rec-001",
                    "title": "电机参数表",
                    "asset_type": "figure",
                    "visual_role": "layout_drawing",
                    "review_required": True,
                    "page_no": 12,
                    "heading_path": "4.3 电机技术参数",
                    "usage_mode": "reference_only",
                    "metadata": {"asset_audit_status": "review_pending", "asset_quality_score": 0.42},
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
        self.assertIn("<section_request>", user_prompt)
        self.assertIn("<recommended_assets>", user_prompt)
        self.assertIn("<reuse_pack>", user_prompt)
        self.assertIn("<replacement_constraints>", user_prompt)
        self.assertIn("[[ASSET:TABLE:asset-001]]", user_prompt)
        self.assertIn("电机参数表", user_prompt)
        self.assertIn("不要复述任务说明、XML 标签", user_prompt)
        self.assertIn("视觉类型 layout_drawing", user_prompt)
        self.assertIn("审核状态 review_pending", user_prompt)
        self.assertIn("质量分 0.42", user_prompt)
        self.assertIn("占位符 [[ASSET:FIGURE:asset-rec-001]]", user_prompt)
        self.assertIn("证据类型", system_prompt)
        system_prompt_hidden, user_prompt_with_params = build_section_prompts(
            section={"title": "技术参数", "generation_mode": "reuse_first"},
            global_params={"project_name": "测试项目", "_source_excerpt": "这段原文不应进入全局参数"},
            retrieved_context="",
            outline_title="测试项目技术方案",
            reuse_pack={
                "generation_mode": "reuse_first",
                "parameter_candidates": {
                    "evidence": [
                        {
                            "source_title": "需求卡关键参数",
                            "heading_path": ["技术参数"],
                            "score": 1.0,
                            "content_md": "| 参数 | 值 |\n| --- | --- |\n| system_voltage | 10kV |",
                        }
                    ]
                },
            },
        )
        self.assertNotIn("这段原文不应进入全局参数", system_prompt_hidden)
        self.assertIn("需求卡关键参数", user_prompt_with_params)
        self.assertIn("system_voltage", user_prompt_with_params)
        self.assertIn("不得自行改写成 [[ASSET:标题]]", system_prompt)

    def test_build_section_prompts_includes_assembled_draft_for_reuse_finalize(self) -> None:
        system_prompt, user_prompt = build_section_prompts(
            section={
                "title": "总体方案",
                "description": "说明系统总体方案。",
                "keywords": ["总体方案", "LCI"],
                "generation_mode": "reuse_first",
            },
            global_params={"project_name": "测试项目"},
            retrieved_context="",
            outline_title="测试项目技术方案",
            recommended_assets=[],
            reuse_pack={
                "generation_mode": "reuse_first",
                "assembled_draft": "## 总体方案\n\n### 系统组成\n\n组装稿内容。",
                "reusable_blocks": [
                    {
                        "source_title": "历史方案A",
                        "heading_path": ["3", "系统组成"],
                        "content_md": "组装稿内容。",
                        "reusability_score": 0.95,
                    }
                ],
            },
        )

        self.assertIn("必须将该组装稿视为主素材", system_prompt)
        self.assertIn("<assembled_draft_bundle>", user_prompt)
        self.assertIn("<assembled_draft>", user_prompt)
        self.assertIn("组装稿内容", user_prompt)

    def test_build_section_prompts_includes_all_reuse_blocks_for_full_section_mode(self) -> None:
        _, user_prompt = build_section_prompts(
            section={
                "title": "总体方案",
                "generation_mode": "reuse_first",
            },
            global_params={"project_name": "测试项目"},
            retrieved_context="",
            outline_title="测试项目技术方案",
            recommended_assets=[],
            reuse_pack={
                "generation_mode": "reuse_first",
                "retrieval_mode": "full_section",
                "reusable_blocks": [
                    {
                        "source_title": "历史方案A",
                        "heading_path": ["3", f"3.{index}"],
                        "content_md": f"第 {index} 个整章复用块正文。",
                        "reusability_score": 0.9,
                    }
                    for index in range(1, 7)
                ],
            },
        )

        self.assertIn("第 6 个整章复用块正文。", user_prompt)

    def test_build_section_prompts_keeps_section_pack_reuse_block_limit(self) -> None:
        _, user_prompt = build_section_prompts(
            section={
                "title": "总体方案",
                "generation_mode": "reuse_first",
            },
            global_params={"project_name": "测试项目"},
            retrieved_context="",
            outline_title="测试项目技术方案",
            recommended_assets=[],
            reuse_pack={
                "generation_mode": "reuse_first",
                "retrieval_mode": "section_pack",
                "reusable_blocks": [
                    {
                        "source_title": "历史方案A",
                        "heading_path": ["3", f"3.{index}"],
                        "content_md": f"第 {index} 个候选复用块正文。",
                        "reusability_score": 0.9,
                    }
                    for index in range(1, 7)
                ],
            },
        )

        self.assertIn("第 5 个候选复用块正文。", user_prompt)
        self.assertNotIn("第 6 个候选复用块正文。", user_prompt)

    def test_build_section_guidance_matches_real_world_title_patterns(self) -> None:
        design_basis = _build_section_guidance("2 设计依据与适用边界条件")
        project_overview = _build_section_guidance("1 项目概述与改造目标")
        service_commitment = _build_section_guidance("14 技术资料与服务承诺")
        motor_interface = _build_section_guidance("5 高炉鼓风机同步电机适配与接口方案")

        self.assertIn("不要混入启动时序", design_basis)
        self.assertIn("不要再细分过多四级编号", project_overview)
        self.assertIn("交付和响应边界", service_commitment)
        self.assertIn("信号类别、控制方向、保护出口归属和切换判据来源", motor_interface)
        self.assertIn("集中在末尾统一归纳", motor_interface)

    def test_inter_section_context_includes_summaries_glossary_and_covered_topics(self) -> None:
        state = _new_inter_section_state(
            task_id="task-001",
            outline_title="测试项目技术方案",
            global_params={"project_name": "测试项目"},
        )
        covered_topics: dict[int, str] = {}

        _record_inter_section_context(
            state=state,
            covered_topics=covered_topics,
            section_index=0,
            section_title="1 项目概述与改造目标",
            draft_status="generated",
            content_md="## 1 项目概述与改造目标\n\n本项目围绕高炉鼓风机改造，变频器 VFD 与 PLC 协同实现软起与联锁控制。",
        )
        _record_inter_section_context(
            state=state,
            covered_topics=covered_topics,
            section_index=1,
            section_title="2 设计依据与适用边界条件",
            draft_status="review_required",
            content_md="## 2 设计依据与适用边界条件\n\n本章说明适用标准、现场环境边界和接口边界。",
        )

        context = _build_preceding_context(
            state=state,
            covered_topics=covered_topics,
            current_index=2,
        )

        self.assertIn("前序章节已覆盖内容（请勿重复）", context)
        self.assertIn("全文统一术语：变频器（不要写成“VFD”）", context)
        self.assertIn("已覆盖主题：项目概述与改造目标、设计依据与适用边界条件", context)

    def test_build_preceding_context_from_existing_drafts_uses_prior_outline_sections(self) -> None:
        context = _build_preceding_context_from_existing_drafts(
            task_id="task-002",
            outline_title="测试项目技术方案",
            global_params={"project_name": "测试项目"},
            sections=[
                {"section_id": "1", "title": "1 项目概述与改造目标"},
                {"section_id": "2", "title": "2 设计依据与适用边界条件"},
                {"section_id": "3", "title": "3 LCI 变频软起动装置方案"},
            ],
            current_section_id="3",
            existing_drafts=[
                SimpleNamespace(
                    section_id="1",
                    status="generated",
                    content_md="## 1 项目概述与改造目标\n\n本项目需要完成高炉鼓风机变频软起改造。",
                ),
                SimpleNamespace(
                    section_id="2",
                    status="edited",
                    content_md="## 2 设计依据与适用边界条件\n\n适用标准、环境边界和接口条件以现有装置资料为准。",
                ),
                SimpleNamespace(
                    section_id="3",
                    status="review_required",
                    content_md="## 3 LCI 变频软起动装置方案\n\n当前章节内容。",
                ),
            ],
        )

        self.assertIn("项目概述与改造目标", context)
        self.assertIn("设计依据与适用边界条件", context)
        self.assertIn("已覆盖主题：项目概述与改造目标、设计依据与适用边界条件", context)

    def test_build_section_asset_query_merges_section_and_project_context(self) -> None:
        query = build_section_asset_query(
            section={
                "title": "技术架构",
                "purpose": "说明系统架构与关键接口",
                "keywords": ["IEC 61850", "站控层"],
            },
            global_params={
                "project_name": "某造纸企业项目",
                "product_line": "hv_vfd",
                "industry": "电气",
            },
        )
        self.assertIn("技术架构", query)
        self.assertIn("IEC 61850", query)
        self.assertIn("某造纸企业项目", query)
        self.assertIn("工程示意图", query)

    def test_build_section_asset_types_infers_table_for_parameter_sections(self) -> None:
        asset_types = build_section_asset_types(
            {
                "title": "设备技术参数与性能指标",
                "purpose": "汇总主要技术参数、容量与性能数据。",
                "keywords": ["技术参数", "性能指标", "额定容量"],
                "expected_evidence_types": ["section"],
            }
        )

        self.assertEqual(asset_types, ["table"])

    def test_build_section_asset_types_keeps_required_vfd_spec_figures(self) -> None:
        asset_types = build_section_asset_types(
            {
                "title": "变频器技术规格",
                "purpose": "说明变频器配置、系统示意和主要技术数据。",
                "keywords": ["变频器", "技术规格"],
                "expected_evidence_types": ["section"],
                "asset_required": True,
            }
        )

        self.assertEqual(asset_types, ["table", "figure"])

    def test_build_section_asset_types_keeps_figures_for_parameter_sensitive_interlock_sections(self) -> None:
        asset_types = build_section_asset_types(
            {
                "title": "7 电机控制盘、励磁与接口联锁方案",
                "purpose": "说明电机控制盘、励磁系统、DCS/PLC 接口、断路器反馈、联锁保护和故障诊断设计。",
                "keywords": ["电机控制盘", "励磁", "DCS", "PLC", "联锁", "断路器反馈"],
                "expected_evidence_types": ["section", "parameter"],
                "section_class": "architecture",
                "parameter_sensitive": True,
            }
        )

        self.assertEqual(asset_types, ["table", "figure"])

    def test_build_section_asset_types_skips_optional_tables_for_control_logic(self) -> None:
        asset_types = build_section_asset_types(
            {
                "title": "运行模式与控制策略",
                "purpose": "介绍系统可支持的启动、升速、并切换及相关运行模式。",
                "keywords": ["运行模式", "控制策略", "升速控制", "运行切换"],
                "expected_evidence_types": ["section"],
                "asset_required": False,
            }
        )

        self.assertIsNone(asset_types)

    def test_build_section_asset_types_suppresses_document_delivery_assets(self) -> None:
        section = {
            "title": "项目交付资料与文档清单",
            "purpose": "列明设计图纸、操作维护手册、测试报告及合格证等交付文档。",
            "keywords": ["交付文档", "技术图纸", "table", "parameter"],
            "expected_evidence_types": ["table", "parameter", "section"],
            "asset_required": True,
            "parameter_sensitive": True,
        }

        self.assertIsNone(build_section_asset_types(section))
        self.assertTrue(
            _should_skip_optional_asset_search(
                section=section,
                target_taxonomy=infer_target_taxonomy(section),
                asset_types=None,
            )
        )

    def test_should_skip_optional_asset_search_for_non_required_installation_section(self) -> None:
        section = {
            "title": "11 安装布置与基础条件",
            "purpose": "说明设备布置、基础及进出线条件。",
            "keywords": ["安装布置", "基础条件"],
            "expected_evidence_types": ["section"],
            "asset_required": False,
            "parameter_sensitive": False,
        }

        self.assertEqual(build_section_asset_types(section), ["figure"])
        self.assertTrue(
            _should_skip_optional_asset_search(
                section=section,
                target_taxonomy=infer_target_taxonomy(section),
                asset_types=["figure"],
            )
        )

    def test_build_rewrite_prompts_enforces_structure_preservation(self) -> None:
        system_prompt, user_prompt = build_rewrite_prompts(
            section_context="章节标题: 主回路系统方案\n当前关键参数: project_name=测试项目",
            selected_text="## 主回路系统方案\n\n### 关键技术参数\n\n| 参数 | 数值 |\n| --- | --- |\n| 电压 | 10kV |",
            user_instruction="统一措辞并替换旧项目名称",
            global_params={"project_name": "测试项目"},
        )

        self.assertIn("不低于原稿的 80%", system_prompt)
        self.assertIn("字段标签仅供理解", system_prompt)
        self.assertIn("<section_context>", user_prompt)
        self.assertIn("<draft_markdown>", user_prompt)
        self.assertIn("不要附加解释", user_prompt)

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

    def test_build_reusable_blocks_strips_internal_retrieval_summary_lines(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "ev_002",
                        "type": "section",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "heading_path": ["第8章", "控制保护与系统可靠性设计"],
                        "raw_content": (
                            "匹配原因：query_overlap=控制,保护,联锁\n"
                            "可参考章节：第8章 控制保护与系统可靠性设计\n"
                            "系统应向 DCS 提供运行、故障、闭锁和远方/就地状态信号。"
                        ),
                        "reusability_score": 0.9,
                        "metadata": {
                            "front_matter": False,
                            "needs_asset_lookup": False,
                            "section_type": "protection_interlock",
                            "equipment_type": "vfd",
                            "content_form": "narrative",
                        },
                    }
                ]
            }
        )

        blocks = build_reusable_blocks(
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                "keywords": ["联锁", "保护", "DCS"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=bundle,
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertEqual(len(blocks), 1)
        self.assertNotIn("匹配原因", blocks[0]["content_md"])
        self.assertNotIn("可参考章节", blocks[0]["content_md"])
        self.assertIn("系统应向 DCS 提供运行、故障、闭锁", blocks[0]["content_md"])

    def test_build_reusable_blocks_skips_case_fallback_summaries(self) -> None:
        bundle = SimpleNamespace(
            content={
                "results": [
                    {
                        "evidence_id": "case_ev_001",
                        "type": "case_summary",
                        "source_chunk_type": "CASE_SUMMARY",
                        "source_doc_id": "sample-001",
                        "source_title": "历史方案A.docx",
                        "heading_path": ["1 项目概述", "2 技术方案"],
                        "raw_content": "匹配原因：query_overlap=变频器\n可参考章节：2 技术方案",
                        "reusability_score": 0.82,
                        "metadata": {"fallback_source": "case_library", "sample_id": "sample-001"},
                    }
                ]
            }
        )

        blocks = build_reusable_blocks(
            section={"title": "主回路系统方案", "expected_evidence_types": ["section"]},
            evidence_bundle=bundle,
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertEqual(blocks, [])

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

    def test_prioritize_reusable_blocks_for_citations_prefers_matching_block(self) -> None:
        prioritized = prioritize_reusable_blocks_for_citations(
            [
                {
                    "block_id": "case:sample-a:17",
                    "source_doc_id": "sample-a",
                    "source_title": "历史方案A",
                    "heading_path": ["5. 接口"],
                },
                {
                    "block_id": "case:sample-b:9",
                    "source_doc_id": "sample-b",
                    "source_title": "历史方案B",
                    "heading_path": ["2. 主回路"],
                },
            ],
            preferred_citation_ids=["case:sample-b:9"],
        )

        self.assertEqual(prioritized[0]["block_id"], "case:sample-b:9")

    def test_build_reuse_citations_include_excerpt(self) -> None:
        citations = build_reuse_citations(
            [
                {
                    "block_id": "case:sample-a:17",
                    "source_doc_id": "sample-a",
                    "source_title": "历史方案A",
                    "heading_path": ["5. 接口"],
                    "selection_score": 0.91,
                    "block_type": "section",
                    "content_md": "## 5. 接口\n\n变频器向 DCS 提供状态量、报警量和运行反馈。",
                }
            ]
        )

        self.assertIn("变频器向 DCS 提供状态量", citations[0]["excerpt"])

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

    def test_filter_reuse_blocks_for_design_basis_skips_startup_process_noise(self) -> None:
        reusable_blocks = [
            {
                "selection_score": 1.0,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 10,
                "heading_path": ["2.1 设计依据与边界条件"],
                "content_md": "设计依据包括现场环境条件、接口边界和适用标准。",
                "metadata": {
                    "section_type": "design_basis",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            },
            {
                "selection_score": 0.92,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 11,
                "heading_path": ["3.2 启动和同步过程描述"],
                "content_md": "纯加速时间约 29s，工频切换时间约 30s，并在失败后输出去磁电流。",
                "metadata": {
                    "section_type": "control_logic",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            },
        ]

        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=reusable_blocks,
            target_taxonomy=infer_target_taxonomy(
                {
                    "title": "2 设计依据与适用边界条件",
                    "purpose": "说明适用标准、现场环境和接口边界条件。",
                    "expected_evidence_types": ["section"],
                }
            ),
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertIn("2.1 设计依据与边界条件", headings)
        self.assertNotIn("3.2 启动和同步过程描述", headings)

    def test_filter_reuse_blocks_for_motor_spec_skips_generic_control_narrative(self) -> None:
        reusable_blocks = [
            {
                "selection_score": 0.96,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 10,
                "heading_path": ["3 高浓磨机电机控制及电机辅助设备监控系统方案"],
                "content_md": "本地控制单元 PLC 监控油站、冷却器及高低压柜状态信号。",
                "metadata": {
                    "section_type": "protection_interlock",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            },
            {
                "selection_score": 0.9,
                "sample_id": "case-a",
                "source_doc_id": "case-a",
                "chunk_index": 11,
                "heading_path": ["5.4 励磁与转子回路接口"],
                "content_md": "应核对励磁系统、转子回路、旋转整流部件及相关反馈接口。",
                "metadata": {
                    "section_type": "motor_spec",
                    "equipment_type": "motor",
                    "content_form": "narrative",
                },
            },
        ]

        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=reusable_blocks,
            target_taxonomy=infer_target_taxonomy(
                {
                    "title": "5 高炉鼓风机同步电机适配与接口方案",
                    "purpose": "说明同步电机适配、励磁接口和辅助测点边界。",
                    "expected_evidence_types": ["section", "parameter"],
                    "parameter_sensitive": True,
                    "asset_required": True,
                }
            ),
            section={
                "title": "5 高炉鼓风机同步电机适配与接口方案",
                "purpose": "说明同步电机适配、励磁接口和辅助测点边界。",
                "expected_evidence_types": ["section", "parameter"],
                "parameter_sensitive": True,
                "asset_required": True,
            },
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertIn("5.4 励磁与转子回路接口", headings)
        self.assertNotIn("3 高浓磨机电机控制及电机辅助设备监控系统方案", headings)

    def test_build_reusable_blocks_skips_curve_load_data_for_transformer_spec(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "6 变压器技术规范",
                "purpose": "说明输入变压器和输出变压器的容量、绝缘、温升和联结组要求。",
                "expected_evidence_types": ["section", "parameter"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={},
            case_library_matches=[
                {
                    "sample_id": "sample-lci",
                    "file_name": "LCI方案.docx",
                    "chunk_index": 1,
                    "chunk_type": "PLAIN",
                    "heading_path": "3.3.1 负载数据 Load data",
                    "content": "The characteristic is based on the estimated value. 转动惯量 J=18695kg.m2，起动阻力矩为空载57000N.m。",
                    "score": 0.92,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-transformer",
                    "file_name": "变压器方案.docx",
                    "chunk_index": 2,
                    "chunk_type": "PLAIN",
                    "heading_path": "6.1 输入变压器技术规范",
                    "content": "输入变压器采用干式变压器，容量5458kVA，联结组别Dy5，绝缘等级满足高压系统要求。",
                    "score": 0.84,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "transformer_spec",
                    "equipment_type": "transformer",
                    "content_form": "narrative",
                },
            ],
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in blocks}
        self.assertIn("6.1 输入变压器技术规范", headings)
        self.assertNotIn("3.3.1 负载数据 Load data", headings)

    def test_build_reusable_blocks_skips_curve_load_data_for_motor_spec(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "7 电机技术规范",
                "purpose": "说明同步电机额定功率、额定电压、绝缘、防护等级和接口要求。",
                "expected_evidence_types": ["section", "parameter"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={},
            case_library_matches=[
                {
                    "sample_id": "sample-lci",
                    "file_name": "LCI方案.docx",
                    "chunk_index": 1,
                    "chunk_type": "PLAIN",
                    "heading_path": "3.3.2 变频启动曲线 Start curve by SFC",
                    "content": "曲线表示SFC启动过程中的转矩和转速变化，用于说明启动特性。",
                    "score": 0.92,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-motor",
                    "file_name": "电机方案.docx",
                    "chunk_index": 2,
                    "chunk_type": "PLAIN",
                    "heading_path": "7.1 同步电机技术规范",
                    "content": "同步电机额定功率4208kW，额定电压10kV，绝缘等级F级，防护等级按现场要求配置。",
                    "score": 0.84,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "motor_spec",
                    "equipment_type": "motor",
                    "content_form": "narrative",
                },
            ],
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in blocks}
        self.assertIn("7.1 同步电机技术规范", headings)
        self.assertNotIn("3.3.2 变频启动曲线 Start curve by SFC", headings)

    def test_build_reusable_blocks_skips_interface_signal_for_transformer_spec(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "6 变压器技术规范",
                "purpose": "说明输入变压器和输出变压器的容量、绕组、绝缘和温升要求。",
                "expected_evidence_types": ["section", "parameter"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={},
            case_library_matches=[
                {
                    "sample_id": "sample-interface",
                    "file_name": "接口方案.docx",
                    "chunk_index": 1,
                    "chunk_type": "PLAIN",
                    "heading_path": "5 变频启动装置与上位机的接口",
                    "content": "上位机接口提供变压器超温、断路器位置、故障报警等开关量信号。",
                    "score": 0.91,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "communication_interface",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-transformer",
                    "file_name": "变压器方案.docx",
                    "chunk_index": 2,
                    "chunk_type": "PLAIN",
                    "heading_path": "6.1 输入变压器技术规范",
                    "content": "输入变压器容量5458kVA，短路阻抗满足系统要求，绝缘等级和温升按高压干式变压器配置。",
                    "score": 0.84,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "transformer_spec",
                    "equipment_type": "transformer",
                    "content_form": "narrative",
                },
            ],
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in blocks}
        self.assertIn("6.1 输入变压器技术规范", headings)
        self.assertNotIn("5 变频启动装置与上位机的接口", headings)

    def test_build_reusable_blocks_skips_system_solution_for_motor_spec(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "7 电机技术规范",
                "purpose": "说明同步电机额定功率、额定电压、绝缘、防护等级和轴承要求。",
                "expected_evidence_types": ["section", "parameter"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={},
            case_library_matches=[
                {
                    "sample_id": "sample-lci",
                    "file_name": "LCI系统方案.docx",
                    "chunk_index": 1,
                    "chunk_type": "PLAIN",
                    "heading_path": "3 系统方案 System Solution",
                    "content": "同步电机的启动和同步由变频器SFC控制，系统根据转子位置完成励磁投入和并网切换。",
                    "score": 0.91,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-motor",
                    "file_name": "电机方案.docx",
                    "chunk_index": 2,
                    "chunk_type": "PLAIN",
                    "heading_path": "7.1 同步电机技术规范",
                    "content": "同步电机额定功率4208kW，额定电压10kV，绝缘等级F级，防护等级IP54，轴承按连续运行工况选型。",
                    "score": 0.84,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "motor_spec",
                    "equipment_type": "motor",
                    "content_form": "narrative",
                },
            ],
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in blocks}
        self.assertIn("7.1 同步电机技术规范", headings)
        self.assertNotIn("3 系统方案 System Solution", headings)

    def test_sync_context_after_evidence_judge_drops_rejected_citations(self) -> None:
        context, citations = _sync_context_after_evidence_judge(
            context="- 坏证据: 标签和喷漆要求",
            citations=[
                {
                    "evidence_id": "ev_bad",
                    "source_title": "坏证据.docx",
                    "heading_path": ["6. 标签和喷漆"],
                    "excerpt": "标签和喷漆要求",
                }
            ],
            reusable_blocks=[
                {
                    "block_id": "ev_good",
                    "source_doc_id": "doc_good",
                    "source_title": "好证据.docx",
                    "heading_path": ["7.1 同步电机技术规范"],
                    "content_md": "同步电机额定功率4208kW，额定电压10kV，绝缘等级F级。",
                    "selection_score": 0.91,
                    "block_type": "section",
                }
            ],
            evidence_judge_trace={
                "status": "applied",
                "input_count": 2,
                "kept_count": 1,
                "dropped_count": 1,
            },
        )

        self.assertIn("好证据.docx", context)
        self.assertNotIn("坏证据", context)
        self.assertEqual([item["evidence_id"] for item in citations], ["ev_good"])

    def test_collect_parameter_evidence_candidates_uses_requirement_key_params_for_transformer_spec(self) -> None:
        candidates = _collect_parameter_evidence_candidates(
            section={
                "title": "6 变压器技术规范",
                "purpose": "说明输入/输出变压器容量、阻抗、绕组和绝缘要求。",
                "generation_mode": "reuse_first",
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={
                "input_transformer": "5458 kVA / 10kV / dry type / Dy5",
                "output_transformer": "4807 kVA / 10kV / dry type / Dy5",
                "system_voltage": "10000 V ±10%",
                "_source_excerpt": "内部原文不应直接作为全局参数展示",
            },
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["source"], "requirement_key_parameters")
        self.assertIn("input_transformer", candidates[0]["content_md"])
        self.assertIn("output_transformer", candidates[0]["content_md"])

    def test_merge_parameter_evidence_context_adds_parameter_citation(self) -> None:
        context, citations = _merge_parameter_evidence_context(
            context="",
            citations=[],
            parameter_evidence_candidates=[
                {
                    "evidence_id": "param:motor:requirements",
                    "source_title": "需求卡关键参数",
                    "heading_path": ["7 电机技术规范"],
                    "type": "parameter",
                    "score": 1.0,
                    "content_md": "| 参数 | 值 |\n| --- | --- |\n| motor_type | 高压同步电机 |",
                }
            ],
        )

        self.assertIn("需求卡关键参数", context)
        self.assertEqual(citations[0]["evidence_id"], "param:motor:requirements")
        self.assertEqual(citations[0]["type"], "parameter")

    def test_build_parameter_snapshot_section_content_uses_requirement_params_for_motor_spec(self) -> None:
        result = _build_parameter_snapshot_section_content(
            section={
                "title": "7 电机技术规范",
                "purpose": "说明电机额定参数、惯量和启动边界。",
                "parameter_sensitive": True,
                "generation_mode": "reuse_first",
            },
            global_params={
                "motor_type": "高压同步电机",
                "system_voltage": "10000 V ±10%",
                "frequency": "50 Hz ±2%",
                "vfd_output_power": "4208 kW",
                "converter_operation_current": "277.5 A",
            },
            reuse_pack={
                "reusable_blocks": [],
                "recommended_assets": [],
                "asset_candidates": [],
                "required_asset_placeholders": [],
                "parameter_candidates": {"evidence": [{"evidence_id": "param:motor"}]},
            },
            retrieval_mode="baseline_fallback",
        )

        self.assertIsNotNone(result)
        content, details = result or ("", {})
        self.assertIn("## 7 电机技术规范", content)
        self.assertIn("电机类型", content)
        self.assertIn("高压同步电机", content)
        self.assertEqual(details["effective_path"], "parameter_snapshot_deterministic")
        self.assertEqual(details["parameter_snapshot"]["parameter_count"], 5)

    def test_build_parameter_snapshot_section_content_skips_when_assets_need_selection(self) -> None:
        result = _build_parameter_snapshot_section_content(
            section={
                "title": "7 电机技术规范",
                "purpose": "说明电机额定参数、惯量和启动边界。",
                "parameter_sensitive": True,
                "generation_mode": "reuse_first",
            },
            global_params={
                "motor_type": "高压同步电机",
                "system_voltage": "10000 V",
                "frequency": "50 Hz",
                "vfd_output_power": "4208 kW",
            },
            reuse_pack={
                "reusable_blocks": [],
                "recommended_assets": [],
                "asset_candidates": [{"asset_id": "asset-1"}],
                "required_asset_placeholders": [],
                "parameter_candidates": {"evidence": []},
            },
            retrieval_mode="baseline_fallback",
        )

        self.assertIsNone(result)

    def test_can_short_circuit_parameter_snapshot_retrieval_requires_no_figure_need(self) -> None:
        global_params = {
            "motor_type": "高压同步电机",
            "system_voltage": "10000 V",
            "frequency": "50 Hz",
            "vfd_output_power": "4208 kW",
        }

        self.assertTrue(
            _can_short_circuit_parameter_snapshot_retrieval(
                section={
                    "title": "7 电机技术规范",
                    "purpose": "说明电机额定参数、惯量和启动边界。",
                    "parameter_sensitive": True,
                    "expected_evidence_types": ["parameter"],
                },
                global_params=global_params,
            )
        )
        self.assertFalse(
            _can_short_circuit_parameter_snapshot_retrieval(
                section={
                    "title": "7 电机技术规范",
                    "purpose": "说明电机额定参数，并展示电机接口图。",
                    "parameter_sensitive": True,
                    "expected_evidence_types": ["figure", "parameter"],
                    "asset_required": True,
                },
                global_params=global_params,
            )
        )

    def test_build_reusable_blocks_skips_lci_process_control_noise_for_hv_vfd_protection_section(self) -> None:
        bundle = SimpleNamespace(content={"results": []})

        blocks = build_reusable_blocks(
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明高压变频系统的联锁逻辑、保护配置、报警和可靠性设计。",
                "keywords": ["高压变频", "联锁", "保护", "报警", "DCS"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=bundle,
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
            case_library_matches=[
                {
                    "sample_id": "sample-bad",
                    "file_name": "历史方案坏块",
                    "chunk_index": 3,
                    "chunk_type": "PLAIN",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "content": "本地控制单元 PLC 监控高浓磨机油站、冷却器和励磁柜信号，LCI 与同步电机软起过程受控。",
                    "score": 0.88,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "protection_interlock",
                    "equipment_type": "motor",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-good",
                    "file_name": "历史方案好块",
                    "chunk_index": 4,
                    "chunk_type": "PLAIN",
                    "heading_path": "8 控制保护与系统可靠性设计",
                    "content": "系统应配置启停闭锁、故障跳闸、报警分级、通信异常告警及关键信号冗余采集逻辑。",
                    "score": 0.84,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "protection_interlock",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            ],
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in blocks}
        self.assertIn("8 控制保护与系统可靠性设计", headings)
        self.assertNotIn("3 高浓磨机电机控制及电机辅助设备监控系统方案", headings)

    def test_build_reusable_blocks_skips_lci_process_noise_for_substation_automation_section(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "4 110kV变电站综合自动化系统方案",
                "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构、监控策略和远方通信。",
                "keywords": ["综合自动化系统", "站控层", "间隔层", "网络层"],
                "expected_evidence_types": ["section", "figure"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
            case_library_matches=[
                {
                    "sample_id": "sample-lci",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "chunk_index": 4,
                    "chunk_type": "PLAIN",
                    "heading_path": "4 LCI 变频软起系统方案",
                    "content": "磨机总启动时间69s，LCI切换内部晶闸管换相模式并完成同步切换。",
                    "score": 0.92,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "vfd_spec",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                }
            ],
        )

        self.assertEqual(blocks, [])

    def test_filter_reuse_blocks_for_substation_automation_does_not_fallback_to_lci_blocks(self) -> None:
        section = {
            "title": "4 110kV变电站综合自动化系统方案",
            "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构、监控策略和远方通信。",
            "keywords": ["综合自动化系统", "站控层", "间隔层", "网络层"],
            "expected_evidence_types": ["section", "figure"],
        }
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["4 LCI 变频软起系统方案"],
                    "metadata": {"section_type": "vfd_spec", "equipment_type": "lci", "content_form": "narrative"},
                    "selection_score": 0.95,
                    "content_md": "磨机总启动时间69s，LCI切换内部晶闸管换相模式并完成同步切换。",
                }
            ],
            target_taxonomy=infer_target_taxonomy(section),
            section=section,
        )

        self.assertEqual(filtered, [])

    def test_filter_reuse_blocks_for_generic_vfd_skips_lci_sequence_but_keeps_vfd_parameter_table(self) -> None:
        section = {
            "title": "6 HV-VFD高压变频器配置方案",
            "purpose": "说明高压变频器配置原则、控制方式、运行模式、保护功能和节能价值。",
            "keywords": ["HV-VFD", "高压变频器", "控制方式", "保护功能"],
            "expected_evidence_types": ["table", "parameter"],
            "parameter_sensitive": True,
        }
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["5 变频器技术数据"],
                    "metadata": {"section_type": "vfd_spec", "equipment_type": "vfd", "content_form": "parameter_table"},
                    "selection_score": 0.96,
                    "content_md": "| 电网电压 | 10kV |\n| 电网频率 | 50Hz |",
                },
                {
                    "heading_path": ["4 LCI 变频软起系统方案"],
                    "metadata": {"section_type": "vfd_spec", "equipment_type": "lci", "content_form": "narrative"},
                    "selection_score": 0.9,
                    "content_md": "LCI大约需要5s切换内部晶闸管脉冲换相模式，电机与电网同步约30s。",
                },
            ],
            target_taxonomy=infer_target_taxonomy(section),
            section=section,
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertIn("5 变频器技术数据", headings)
        self.assertNotIn("4 LCI 变频软起系统方案", headings)

    def test_filter_reuse_blocks_for_protection_section_skips_low_focus_sync_parameter_block(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["配置网侧和机侧 PT1 和 PT2 同期参数要求:"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.92,
                    "content_md": "用于同期，同时还有用于测量和保护功能，测量和保护按照常规标准配置即可。",
                },
                {
                    "heading_path": ["8 控制保护与系统可靠性设计"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.9,
                    "content_md": "系统应配置联锁闭锁、故障跳闸、报警分级和通信异常保护逻辑。",
                },
            ],
            target_taxonomy={"section_type": "protection_interlock", "equipment_type": "vfd"},
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                "expected_evidence_types": ["section"],
            },
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertIn("8 控制保护与系统可靠性设计", headings)
        self.assertNotIn("配置网侧和机侧 PT1 和 PT2 同期参数要求:", headings)

    def test_filter_reuse_blocks_for_protection_section_does_not_fallback_to_raw_top_blocks(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["A. 概述"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.95,
                    "content_md": "励磁柜具有失步保护和旋转整流器故障显示功能。",
                },
                {
                    "heading_path": ["3 系统方案 System Solution"],
                    "metadata": {"section_type": "unknown", "content_form": "narrative"},
                    "selection_score": 0.91,
                    "content_md": "The starting SFC system is designed for two subsequent starts in one cycle.",
                },
            ],
            target_taxonomy={"section_type": "protection_interlock", "equipment_type": "vfd"},
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                "expected_evidence_types": ["section"],
            },
        )

        self.assertEqual(filtered, [])

    def test_filter_reuse_blocks_for_assembly_strips_internal_retrieval_summary_lines(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["8", "控制保护与系统可靠性设计"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.86,
                    "content_md": (
                        "匹配原因：query_overlap=联锁,保护\n"
                        "可参考章节：8 控制保护与系统可靠性设计\n"
                        "系统应具备联锁闭锁、报警分级和故障跳闸功能。"
                    ),
                }
            ],
            target_taxonomy=infer_target_taxonomy(
                {
                    "title": "控制保护与系统可靠性设计",
                    "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                    "expected_evidence_types": ["section"],
                }
            ),
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                "expected_evidence_types": ["section"],
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertNotIn("匹配原因", filtered[0]["content_md"])
        self.assertNotIn("可参考章节", filtered[0]["content_md"])
        self.assertIn("联锁闭锁、报警分级和故障跳闸", filtered[0]["content_md"])

    def test_filter_reuse_blocks_for_installation_drops_system_solution_startup_content(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["3 系统方案 System Solution"],
                    "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                    "selection_score": 1.2,
                    "content_md": "#### 3.1 变频软起系统单线图\n\nLCI、ICB、OCB、RCB 单线图。\n\n#### 3.2 启动和同步过程描述\n\nSFC控制同步电机启动。",
                },
                {
                    "heading_path": ["5 总布置图"],
                    "metadata": {"section_type": "installation_conditions", "content_form": "narrative"},
                    "selection_score": 0.82,
                    "content_md": "设备布置应满足通风散热、维护通道、进出线和基础安装要求。",
                },
            ],
            target_taxonomy={"section_type": "installation_conditions", "equipment_type": "lci"},
            section={
                "title": "系统布置与安装要求",
                "purpose": "说明LCI系统、变压器、开关设备及辅助单元的现场布置原则、空间需求和散热条件。",
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["heading_path"], ["5 总布置图"])

    def test_filter_reuse_blocks_for_installation_does_not_fallback_to_startup_content(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["3 系统方案 System Solution"],
                    "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                    "selection_score": 1.2,
                    "content_md": "#### 3.1 变频软起系统单线图\n\nLCI、ICB、OCB、RCB 单线图。\n\n#### 3.2 启动和同步过程描述\n\nSFC 控制同步电机启动。",
                },
                {
                    "heading_path": ["3.3.2 变频启动曲线"],
                    "metadata": {"section_type": "starter_spec", "content_form": "figure"},
                    "selection_score": 1.0,
                    "content_md": "启动曲线、纯加速、建磁、同步时间。",
                },
            ],
            target_taxonomy={"section_type": "installation_conditions", "equipment_type": "lci"},
            section={
                "title": "系统布置与安装要求",
                "purpose": "说明现场布置、基础、通风散热和维护通道要求。",
            },
        )

        self.assertEqual(filtered, [])

    def test_filter_reuse_blocks_for_spare_section_drops_service_narrative_support(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["6 备品备件清单"],
                    "metadata": {"section_type": "service_support", "content_form": "bom_table"},
                    "selection_score": 1.2,
                    "content_md": "备品备件清单\n\n| 序号 | 名称 | 数量 |\n|---|---|---|\n| 1 | 晶闸管 | 3 |",
                },
                {
                    "heading_path": ["10 售后服务"],
                    "metadata": {"section_type": "service_support", "content_form": "narrative"},
                    "selection_score": 0.8,
                    "content_md": "卖方提供售后服务和安全保障备件中心，备品备件在停产后十年内保证供应。",
                },
            ],
            target_taxonomy={"section_type": "service_support", "equipment_type": "generic"},
            section={
                "title": "备品备件清单",
                "purpose": "列出推荐备品备件名称和数量。",
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["heading_path"], ["6 备品备件清单"])

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

    def test_select_section_scope_candidates_prefers_specific_paths_over_root_prefix(self) -> None:
        selected = _select_section_scope_candidates(
            [
                {
                    "section_id": "3",
                    "section_path": "第三章 系统及方案介绍",
                    "level": 1,
                    "score": 0.54,
                },
                {
                    "section_id": "3.2",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案",
                    "level": 2,
                    "score": 0.58,
                },
                {
                    "section_id": "3.2.4",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4控制信号接口说明",
                    "level": 3,
                    "score": 0.66,
                },
            ]
        )

        self.assertEqual([item["section_id"] for item in selected], ["3.2.4"])

    def test_select_section_scope_candidates_keeps_broad_anchor_when_title_match_is_strong(self) -> None:
        selected = _select_section_scope_candidates(
            [
                {
                    "section_id": "3",
                    "section_path": "第三章 系统及方案介绍",
                    "level": 1,
                    "score": 0.52,
                    "reason": "normalized_section_title_match",
                },
                {
                    "section_id": "3.2",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案",
                    "level": 2,
                    "score": 0.58,
                    "reason": "section_path_title_match",
                },
                {
                    "section_id": "3.2.4",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4控制信号接口说明",
                    "level": 3,
                    "score": 0.66,
                    "reason": "section_path_title_match; detail_overlap=接口",
                },
            ]
        )

        self.assertEqual([item["section_id"] for item in selected], ["3.2", "3.2.4"])

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

    def test_build_reusable_blocks_applies_knowledge_wiki_product_and_module_priors(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "主回路系统方案",
                "purpose": "说明高压变频器主回路结构、功率单元配置和旁路切换逻辑。",
                "keywords": ["高压变频器", "主回路", "功率单元"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={"product_line": "hv_vfd", "project_name": "测试项目"},
            case_library_matches=[
                {
                    "sample_id": "sample-generic",
                    "file_name": "历史方案概述章节",
                    "chunk_index": 3,
                    "chunk_type": "PLAIN",
                    "heading_path": "2 系统概述",
                    "content": "本节概述系统组成、供货范围和实施安排，适用于多个项目。",
                    "score": 0.8,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "overall_solution",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-vfd-main-circuit",
                    "file_name": "高压变频器系统方案",
                    "chunk_index": 7,
                    "chunk_type": "PLAIN",
                    "heading_path": "4 主回路结构与切换逻辑",
                    "content": "高压变频器主回路采用移相整流变压器配合功率单元串联结构，并设置旁路切换回路。",
                    "score": 0.73,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            ],
            extra_query_terms=["高压变频器方案族", "高压变频器", "功率单元"],
            knowledge_retrieval_bundle={
                "query_expansion_terms": ["高压变频器方案族", "高压变频器", "功率单元"],
                "product_cards": [
                    {
                        "title": "高压变频器方案族",
                        "aliases": ["高压变频器"],
                        "top_equipment_types": [
                            {"equipment_type": "vfd", "label": "高压变频器", "count": 5}
                        ],
                        "top_section_types": [
                            {
                                "section_type": "main_circuit_scheme",
                                "label": "主回路方案",
                                "count": 3,
                            }
                        ],
                        "representative_titles": ["高压变频器系统方案"],
                        "representative_headings": ["主回路结构与切换逻辑"],
                    }
                ],
                "module_cards": [
                    {
                        "title": "功率单元",
                        "aliases": ["功率模块"],
                        "top_headings": ["功率单元配置"],
                    }
                ],
            },
            limit=2,
        )

        self.assertEqual(blocks[0]["source_title"], "高压变频器系统方案")
        self.assertIn("knowledge_wiki_product_match", blocks[0]["selection_reasons"])
        self.assertIn("knowledge_wiki_module_match", blocks[0]["selection_reasons"])
        self.assertIn("knowledge_wiki_product_section_prior", blocks[0]["selection_reasons"])
        self.assertIn("knowledge_wiki_product_equipment_prior", blocks[0]["selection_reasons"])
        self.assertEqual(blocks[0]["selection_score_breakdown"]["knowledge_wiki_prior_total"], 0.13)
        self.assertEqual(blocks[0]["selection_score_breakdown"]["knowledge_wiki_module_match"], 0.04)
        self.assertGreater(blocks[0]["selection_score"], blocks[1]["selection_score"])

    def test_build_reusable_blocks_gates_knowledge_wiki_priors_by_query_structure(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "供货范围",
                "purpose": "说明 LCI 软起装置、本地控制柜和随机资料的供货边界。",
                "keywords": ["供货范围", "LCI", "控制柜"],
                "expected_evidence_types": ["section"],
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={"product_line": "lci", "project_name": "测试项目"},
            case_library_matches=[
                {
                    "sample_id": "sample-lci-scope",
                    "file_name": "LCI 供货范围章节",
                    "chunk_index": 2,
                    "chunk_type": "PLAIN",
                    "heading_path": "2 供货范围",
                    "content": "本供货范围包括 LCI 软起装置、本地控制柜、随机资料和备品备件。",
                    "score": 0.78,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "supply_scope",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-main-circuit",
                    "file_name": "LCI 主回路方案",
                    "chunk_index": 7,
                    "chunk_type": "PLAIN",
                    "heading_path": "4 主回路结构与切换逻辑",
                    "content": "LCI 软起系统采用晶闸管主回路结构，并配置功率单元与旁路切换逻辑。",
                    "score": 0.8,
                    "front_matter": False,
                    "needs_asset_lookup": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            ],
            extra_query_terms=["LCI 变频软起方案族", "LCI", "控制柜", "功率单元"],
            knowledge_retrieval_bundle={
                "query_expansion_terms": ["LCI 变频软起方案族", "LCI", "控制柜", "功率单元"],
                "product_cards": [
                    {
                        "title": "LCI 变频软起方案族",
                        "aliases": ["LCI", "LCI 软起"],
                        "top_equipment_types": [
                            {"equipment_type": "lci", "label": "LCI 变频软起系统", "count": 4}
                        ],
                        "top_section_types": [
                            {
                                "section_type": "supply_scope",
                                "label": "供货范围",
                                "count": 3,
                            }
                        ],
                        "representative_titles": ["LCI 供货范围章节"],
                        "representative_headings": ["供货范围"],
                    }
                ],
                "module_cards": [
                    {
                        "title": "控制柜",
                        "aliases": ["控制单元"],
                        "top_headings": ["供货范围"],
                    }
                ],
            },
            limit=2,
        )

        self.assertEqual(blocks[0]["source_title"], "LCI 供货范围章节")
        wrong_candidate = next(item for item in blocks if item["source_title"] == "LCI 主回路方案")
        self.assertEqual(wrong_candidate["selection_score_breakdown"]["knowledge_wiki_prior_total"], 0.0)
        self.assertNotIn("knowledge_wiki_product_match", wrong_candidate["selection_reasons"])
        self.assertNotIn("knowledge_wiki_module_match", wrong_candidate["selection_reasons"])

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
                    "document_name": "sample-lci-blower-starting-solution.docx",
                    "heading_path": "2.2 高压变频器主回路方案说明",
                    "metadata": {"section_type": "main_circuit_scheme"},
                },
                {
                    "asset_id": "asset_transformer",
                    "document_name": "sample-lci-blower-starting-solution.docx",
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
                    "source_title": "sample-lci-blower-starting-solution.docx",
                    "heading_path": ["2.2", "高压变频器主回路方案说明"],
                },
                {
                    "source_title": "sample-lci-blower-starting-solution.docx",
                    "heading_path": ["2.3", "高压变频器主要技术参数"],
                },
            ],
        )

        self.assertEqual([item["asset_id"] for item in tightened], ["asset_main", "asset_transformer"])

    def test_tighten_recommended_assets_for_reuse_keeps_multiple_candidates_when_anchor_is_weak(self) -> None:
        tightened = tighten_recommended_assets_for_reuse(
            [
                {
                    "asset_id": "asset_scheme",
                    "document_name": "案例A.docx",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "title": "4.1 LCI 变频软起系统方案",
                    "metadata": {"section_type": "vfd_spec"},
                },
                {
                    "asset_id": "asset_control",
                    "document_name": "案例A.docx",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "title": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "metadata": {"section_type": "protection_interlock"},
                },
                {
                    "asset_id": "asset_curve",
                    "document_name": "案例B.docx",
                    "heading_path": "4.4.2 变频启动曲线",
                    "title": "4.4.2 变频启动曲线",
                    "metadata": {"section_type": "control_logic"},
                },
            ],
            section={
                "title": "控制系统及联锁保护方案",
                "purpose": "说明启停逻辑、联锁和信号交互方式。",
                "expected_evidence_types": ["section", "figure"],
            },
            reusable_blocks=[
                {
                    "source_title": "案例A.docx",
                    "heading_path": ["4", "与本节无强同族关系的标题"],
                }
            ],
        )

        self.assertEqual([item["asset_id"] for item in tightened], ["asset_scheme", "asset_control", "asset_curve"])

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

    def test_filter_recommended_assets_for_section_drops_low_score_assets(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_bad",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "参数表",
                    "score": -0.04,
                    "metadata": {"section_type": "vfd_spec"},
                },
                {
                    "asset_id": "asset_good",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "变频器主要技术参数",
                    "score": 0.23,
                    "metadata": {"section_type": "vfd_spec"},
                },
            ],
            section={
                "title": "变频器技术规格",
                "purpose": "说明变频器配置、系统示意和主要技术数据。",
                "expected_evidence_types": ["section", "table", "parameter"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_good"])

    def test_filter_recommended_assets_for_spare_section_drops_environment_tables(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_env",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "工厂设计环境与供电条件",
                    "heading_path": "1 工厂设计环境",
                    "preview_text": "系统电压、频率、短路容量、海拔、环境温度",
                    "metadata": {"section_type": "site_conditions"},
                },
                {
                    "asset_id": "asset_spare",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "备品备件清单",
                    "heading_path": "7 备品备件清单",
                    "preview_text": "备品备件名称、型号、数量、推荐数量",
                    "metadata": {"section_type": "service_support"},
                },
            ],
            section={
                "title": "备品备件清单",
                "purpose": "列出系统投运初期推荐配置的关键备件。",
                "expected_evidence_types": ["table", "parameter"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_spare"])

    def test_filter_recommended_assets_for_installation_drops_startup_figures_when_no_layout_focus(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_start_curve",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "3.3.2 变频启动曲线 Start curve by SFC",
                    "heading_path": "3 系统方案 System Solution",
                    "preview_text": "启动曲线、纯加速、建磁、同步时间。",
                    "metadata": {"section_type": "starter_spec"},
                },
                {
                    "asset_id": "asset_load_data",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "3.3.1 负载数据 Load data",
                    "heading_path": "3 系统方案 System Solution",
                    "preview_text": "风机负载数据和起动阻力矩。",
                    "metadata": {"section_type": "starter_spec"},
                },
            ],
            section={
                "title": "系统布置与安装要求",
                "purpose": "说明系统现场布置、基础、通风散热和维护通道要求。",
                "expected_evidence_types": ["figure", "table"],
            },
        )

        self.assertEqual(filtered, [])

    def test_filter_recommended_assets_for_overall_solution_drops_generic_unanchored_reference_figure(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_generic",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "参考图",
                    "heading_path": None,
                    "preview_text": "封面参考图",
                    "metadata": {"section_type": "vfd_spec", "content_form": "formula"},
                },
                {
                    "asset_id": "asset_curve",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "3.3.2 变频启动曲线 Start curve by SFC",
                    "heading_path": "3.3.2 变频启动曲线 Start curve by SFC",
                    "preview_text": "风机启动曲线",
                    "metadata": {"section_type": "vfd_spec", "content_form": "formula", "source_section_id": "3.3.2"},
                },
            ],
            section={
                "title": "系统方案",
                "purpose": "说明 LCI 变频软起系统单线图、启动同步过程和启动特性。",
                "expected_evidence_types": ["figure"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_curve"])

    def test_remove_mismatched_asset_placeholders_drops_asset_under_wrong_heading(self) -> None:
        content = """## 系统方案

### 3.1 变频软起系统单线图

[[ASSET:FIGURE:asset_load]]

### 3.3.1 负载数据 Load Data

[[ASSET:FIGURE:asset_load]]
"""
        cleaned = _remove_mismatched_asset_placeholders(
            content_md=content,
            recommended_assets=[
                {
                    "asset_id": "asset_load",
                    "asset_type": "figure",
                    "title": "3.3.1 负载数据 Load data",
                    "heading_path": "3.3.1 负载数据 Load data",
                    "metadata": {"source_section_id": "3.3.1"},
                }
            ],
        )

        self.assertEqual(cleaned.count("[[ASSET:FIGURE:asset_load]]"), 1)
        self.assertIn("### 3.3.1 负载数据 Load Data\n\n[[ASSET:FIGURE:asset_load]]", cleaned)
        self.assertNotIn("### 3.1 变频软起系统单线图\n\n[[ASSET:FIGURE:asset_load]]", cleaned)

    def test_remove_mismatched_asset_placeholders_keeps_same_source_recovered_asset(self) -> None:
        content = """## LCI/SFC变频软起动系统总体方案

### 主回路拓扑与关键设备
[[ASSET:FIGURE:asset_single_line]] 图X LCI/SFC变频软起动系统主接线示意图
"""
        cleaned = _remove_mismatched_asset_placeholders(
            content_md=content,
            recommended_assets=[
                {
                    "asset_id": "asset_single_line",
                    "asset_type": "figure",
                    "title": "3.1 变频软起系统单线图 Single line Diagram",
                    "heading_path": "3.1 变频软起系统单线图 Single line Diagram",
                    "score_breakdown": {
                        "source_section_asset_recovery": 0.2,
                        "source_section_relation": "descendant",
                    },
                }
            ],
        )

        self.assertIn("[[ASSET:FIGURE:asset_single_line]]", cleaned)

    def test_remove_mismatched_asset_placeholders_removes_empty_related_asset_section(self) -> None:
        content = """## 系统方案

### 相关图表

- [[ASSET:FIGURE:asset_curve]] 3.3.2 变频启动曲线

### 启动过程

正文。
"""
        cleaned = _remove_mismatched_asset_placeholders(
            content_md=content,
            recommended_assets=[
                {
                    "asset_id": "asset_curve",
                    "asset_type": "figure",
                    "title": "3.3.2 变频启动曲线 Start curve by SFC",
                    "heading_path": "3.3.2 变频启动曲线 Start curve by SFC",
                    "metadata": {"source_section_id": "3.3.2"},
                }
            ],
        )

        self.assertNotIn("### 相关图表", cleaned)
        self.assertNotIn("asset_curve", cleaned)
        self.assertIn("### 启动过程", cleaned)

    def test_filter_recommended_assets_for_main_circuit_drops_control_diagram_noise(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_control",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "高浓磨机电机控制总图",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "preview_text": "系统监控与辅助设备接口",
                    "metadata": {"section_type": "protection_interlock"},
                },
                {
                    "asset_id": "asset_main",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI软起系统主回路",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "preview_text": "输入变压器、LCI、输出变压器及同步电机主回路",
                    "metadata": {"section_type": "vfd_spec"},
                },
            ],
            section={
                "title": "输入输出变压器及主回路配置",
                "purpose": "说明主回路连接、输入输出变压器和隔离配置。",
                "expected_evidence_types": ["section", "figure"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_main"])

    def test_filter_recommended_assets_for_system_diagram_drops_curve_and_cover_figures(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_load",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "3.3.1 负载数据 Load data",
                    "heading_path": "3.3.1 负载数据 Load data",
                    "preview_text": "起动阻力矩、转动惯量等负载数据。",
                    "score": 0.31,
                    "metadata": {"section_type": "vfd_spec", "content_form": "formula", "source_section_id": "3.3.1"},
                },
                {
                    "asset_id": "asset_cover",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "参考图",
                    "heading_path": None,
                    "preview_text": "某钢铁集团高炉鼓风LCI变频启动装置设备技术协议",
                    "score": 0.28,
                    "metadata": {"section_type": "vfd_spec", "content_form": "formula"},
                },
                {
                    "asset_id": "asset_system",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI 变频软起系统单线图",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "preview_text": "输入变压器、LCI、输出变压器、同步电机和励磁系统的主回路拓扑。",
                    "score": 0.24,
                    "metadata": {"section_type": "vfd_spec", "content_form": "figure", "source_section_id": "4.1"},
                },
            ],
            section={
                "title": "LCI变频软起动系统架构",
                "purpose": "说明LCI变频软起系统单线图、启动和同步过程、晶闸管变流装置及与同步电机和励磁系统的接口。",
                "expected_evidence_types": ["section", "figure"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_system"])

    def test_filter_recommended_assets_applies_phase7_wrong_image_gate(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_photo",
                    "asset_type": "figure",
                    "visual_role": "product_photo",
                    "title": "变频器产品照片",
                    "heading_path": "产品照片",
                    "preview_text": "设备实拍照片。",
                    "score": 0.52,
                    "metadata": {
                        "section_type": "vfd_spec",
                        "content_form": "figure",
                        "source_section_id": "4.1",
                        "retrieval_quality": {"product_photo": True},
                    },
                },
                {
                    "asset_id": "asset_table",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "变频器参数表",
                    "heading_path": "4.2 变频器参数",
                    "preview_text": "| 参数 | 值 |",
                    "score": 0.49,
                    "metadata": {
                        "section_type": "vfd_spec",
                        "content_form": "parameter_table",
                        "source_section_id": "4.2",
                    },
                },
                {
                    "asset_id": "asset_system",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI变频软起系统单线图",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "preview_text": "输入变压器、LCI、输出变压器和同步电机主回路拓扑。",
                    "score": 0.41,
                    "metadata": {
                        "section_type": "main_circuit_scheme",
                        "content_form": "figure",
                        "source_section_id": "4.1",
                    },
                },
            ],
            section={
                "title": "LCI变频软起动系统架构",
                "purpose": "说明LCI变频软起系统单线图、主回路拓扑和同步切换接口。",
                "expected_evidence_types": ["section", "figure"],
                "asset_required": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_system"])
        gate = filtered[0]["metadata"]["asset_stability_gate"]
        self.assertEqual(gate["status"], "candidate")

    def test_asset_auto_body_eligibility_requires_source_section_for_figures(self) -> None:
        section = {
            "title": "LCI变频软起动系统架构",
            "purpose": "说明系统单线图和主回路拓扑。",
            "expected_evidence_types": ["figure"],
            "asset_required": True,
        }

        weak_asset = {
            "asset_id": "asset_weak",
            "asset_type": "figure",
            "visual_role": "engineering_figure",
            "title": "LCI系统图",
            "heading_path": "4.1 LCI 变频软起系统方案",
            "metadata": {"sample_id": "sample-a"},
        }
        strong_asset = {
            **weak_asset,
            "asset_id": "asset_strong",
            "metadata": {"sample_id": "sample-a", "source_section_id": "4.1"},
        }

        self.assertFalse(_asset_is_auto_body_eligible(asset=weak_asset, section=section))
        self.assertTrue(_asset_is_auto_body_eligible(asset=strong_asset, section=section))

    def test_asset_trace_exposes_source_binding_and_filtered_assets(self) -> None:
        trace = _build_asset_retrieval_trace(
            query="主回路系统图",
            asset_types=["figure"],
            skipped_optional_search=False,
            recommended_assets=[
                {
                    "asset_id": "asset_system",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI系统图",
                    "metadata": {
                        "source_binding": {"tier": "high", "source_section_id": "4.1"},
                        "asset_stability_gate": {"status": "candidate", "blocking_flags": [], "warning_flags": []},
                    },
                }
            ],
            asset_candidates=[],
            diagnostics={
                "asset_stability": {
                    "filtered_assets": [
                        {
                            "asset_id": "asset_photo",
                            "metadata": {
                                "asset_stability_gate": {
                                    "status": "blocked",
                                    "blocking_flags": ["product_photo_as_topology"],
                                }
                            },
                        }
                    ]
                }
            },
        )

        self.assertEqual(trace["selected_assets"][0]["source_binding"]["tier"], "high")
        self.assertEqual(trace["selected_assets"][0]["asset_stability_gate"]["status"], "candidate")
        self.assertEqual(
            trace["diagnostics"]["asset_stability"]["filtered_assets"][0]["metadata"]["asset_stability_gate"]["blocking_flags"],
            ["product_photo_as_topology"],
        )

    def test_filter_recommended_assets_for_overall_solution_drops_project_tables_and_numeric_fragments(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_schedule",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "变频器改造工程概要及进度安排",
                    "heading_path": "二、系统方案",
                    "preview_text": "项目实施进度、安装调试和投运计划",
                    "metadata": {"section_type": "overall_solution"},
                },
                {
                    "asset_id": "asset_numeric",
                    "asset_type": "figure",
                    "visual_role": "illustration",
                    "title": "47.8 17.5",
                    "heading_path": "47.8 17.5",
                    "preview_text": "",
                    "metadata": {"section_type": "unknown"},
                },
                {
                    "asset_id": "asset_control",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "控制信号接口说明",
                    "heading_path": "2.4 控制信号接口说明",
                    "preview_text": "DCS 硬接线 I/O 信号表",
                    "metadata": {"section_type": "communication_interface"},
                },
                {
                    "asset_id": "asset_topology",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "高压变频器主回路一次系统图",
                    "heading_path": "2.2 高压变频器主回路方案说明",
                    "preview_text": "输入隔离、变频器、输出隔离、工频旁路及主接线拓扑",
                    "metadata": {"section_type": "main_circuit_scheme", "source_section_id": "sec-2.2"},
                },
            ],
            section={
                "title": "第三章 高压变频系统总体方案",
                "purpose": "提供环冷风机变频改造主接线拓扑与系统架构设计，说明功率单元串联多电平技术路线、冷却方式及柜体布置思路。",
                "expected_evidence_types": ["section", "figure"],
                "section_class": "architecture",
                "asset_required": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_topology"])

    def test_filter_recommended_assets_keeps_same_source_recovered_illustration_diagram(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_single_line",
                    "asset_type": "figure",
                    "visual_role": "illustration",
                    "title": "3.1 变频软起系统单线图 Single line Diagram",
                    "heading_path": "3.1 变频软起系统单线图 Single line Diagram",
                    "preview_text": "单套变频驱动系统的单线图如下所示。",
                    "score": 0.24,
                    "score_breakdown": {
                        "final": 0.24,
                        "source_section_asset_recovery": 0.2,
                        "source_section_relation": "descendant",
                    },
                    "metadata": {
                        "asset_audit_status": "review_passed",
                        "asset_quality_score": 0.86,
                    },
                }
            ],
            section={
                "title": "LCI/SFC变频软起动系统总体方案",
                "purpose": "阐述LCI/SFC软起动系统的整体架构与工作原理，涵盖输入隔离、功率变换单元、励磁配合及旁路回路设计。",
                "expected_evidence_types": ["section", "figure"],
                "section_class": "architecture",
                "asset_required": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_single_line"])

    def test_filter_recommended_assets_for_protection_section_drops_main_circuit_noise(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_control",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "高炉鼓风机电机控制总图",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "preview_text": "PLC、联锁、报警及故障处理关系",
                    "metadata": {"section_type": "motor_spec"},
                },
                {
                    "asset_id": "asset_main",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI软起系统主回路",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "preview_text": "输入变压器、输出变压器和同步电机主回路",
                    "metadata": {"section_type": "vfd_spec"},
                },
                {
                    "asset_id": "asset_wave",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "变压器侧波形与谐波",
                    "heading_path": "4.4.2 变频启动曲线",
                    "preview_text": "谐波波形曲线",
                    "metadata": {"section_type": "unknown"},
                },
                {
                    "asset_id": "asset_power_unit",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "功率单元结构示意图",
                    "heading_path": "3 功率单元原理",
                    "preview_text": "功率单元、电容和IGBT结构",
                    "metadata": {"section_type": "vfd_spec"},
                },
                {
                    "asset_id": "asset_primary",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "高压固态软起动一次图",
                    "heading_path": "1 一次方案图",
                    "preview_text": "10kV一次回路和旁路接触器",
                    "metadata": {"section_type": "main_circuit_scheme"},
                },
                {
                    "asset_id": "asset_nameplate",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "异步电动机铭牌",
                    "heading_path": "1.1 电机配置及参数",
                    "preview_text": "电机额定电压、额定电流和功率铭牌",
                    "metadata": {"section_type": "motor_spec"},
                },
                {
                    "asset_id": "asset_partial",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "外形组成框图",
                    "heading_path": "高压回路开门保护",
                    "preview_text": "局部框图，文字不完整",
                    "metadata": {
                        "section_type": "protection_interlock",
                        "retrieval_quality": {"partial_fragment": True, "complete_diagram": False},
                    },
                },
            ],
            section={
                "title": "控制系统及联锁保护方案",
                "purpose": "说明启停逻辑、关键联锁、报警跳闸及运行监视。",
                "expected_evidence_types": ["section", "figure"],
                "asset_required": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_control"])

    def test_filter_recommended_assets_for_interlock_section_drops_unfocused_tables(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_spares",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "8 备品备件清单",
                    "heading_path": "8 备品备件清单",
                    "preview_text": "备件名称、型号、数量",
                    "metadata": {"section_type": "supply_scope"},
                },
                {
                    "asset_id": "asset_service",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "12 售后服务",
                    "heading_path": "12 售后服务",
                    "preview_text": "售后服务与培训计划",
                    "metadata": {"section_type": "service_support"},
                },
                {
                    "asset_id": "asset_rated",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "A. Rated data 额定数据",
                    "heading_path": "A. Rated data 额定数据",
                    "preview_text": "额定电压、额定电流、额定功率",
                    "metadata": {"section_type": "motor_spec"},
                },
                {
                    "asset_id": "asset_signal",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "DCS/PLC 接口信号表",
                    "heading_path": "7.4 DCS/PLC 接口信号表",
                    "preview_text": "启动允许、故障、报警、断路器反馈、联锁保护信号",
                    "metadata": {"section_type": "protection_interlock"},
                },
                {
                    "asset_id": "asset_control",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "电机控制系统总图",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "preview_text": "PLC、励磁、DCS、联锁和报警接口关系",
                    "metadata": {"section_type": "motor_spec"},
                },
            ],
            section={
                "title": "7 电机控制盘、励磁与接口联锁方案",
                "purpose": "说明电机控制盘、励磁系统、DCS/PLC 接口、断路器反馈、联锁保护和故障诊断设计。",
                "expected_evidence_types": ["section", "parameter"],
                "asset_required": False,
                "parameter_sensitive": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_signal", "asset_control"])

    def test_filter_recommended_assets_for_section_drops_low_confidence_non_diagrams(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_low_conf",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "电机起动超时保护",
                    "heading_path": "电机起动超时保护",
                    "preview_text": "OCR 文本较少，无法确认图意。",
                    "metadata": {
                        "section_type": "protection_interlock",
                        "retrieval_quality": {
                            "low_information": False,
                            "complete_diagram": False,
                            "low_confidence_summary": True,
                        },
                    },
                },
                {
                    "asset_id": "asset_control",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "电机控制系统总图",
                    "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    "preview_text": "PLC、励磁、DCS、联锁和报警接口关系",
                    "metadata": {
                        "section_type": "motor_spec",
                        "retrieval_quality": {
                            "low_information": False,
                            "complete_diagram": True,
                            "low_confidence_summary": False,
                        },
                    },
                },
            ],
            section={
                "title": "7 电机控制盘、励磁与接口联锁方案",
                "purpose": "说明电机控制盘、励磁系统、DCS/PLC 接口、断路器反馈、联锁保护和故障诊断设计。",
                "expected_evidence_types": ["section", "figure"],
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_control"])

    def test_filter_recommended_assets_for_vfd_spec_drops_auxiliary_lube_curve(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_lci",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "LCI变频软起系统图",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "preview_text": "LCI、SFC、同步电机、励磁和断路器接口",
                    "metadata": {"section_type": "vfd_spec"},
                },
                {
                    "asset_id": "asset_lube",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "应急润滑油需求曲线",
                    "heading_path": "9 应急润滑油需求曲线",
                    "preview_text": "润滑油流量与时间曲线",
                    "metadata": {"section_type": "motor_spec"},
                },
            ],
            section={
                "title": "5 启动过程与同步切换控制方案",
                "purpose": "描述 SFC 启动、约 95% 转速同步、励磁调节、运行断路器合闸、LCI 电流降零退出等控制步骤。",
                "expected_evidence_types": ["section", "figure"],
                "asset_required": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_lci"])

    def test_filter_recommended_assets_for_motor_interface_drops_cabinet_tables(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_cabinet_table",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "10.2 开关柜参数",
                    "heading_path": "10.2 开关柜参数",
                    "preview_text": "开关柜尺寸、电缆室和柜体参数",
                    "metadata": {"section_type": "cabinet_layout"},
                },
                {
                    "asset_id": "asset_motor_table",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "同步电机参数与励磁接口",
                    "heading_path": "5.2 同步电机参数与励磁接口",
                    "preview_text": "同步电机定子、转子、励磁及测温接口参数",
                    "metadata": {"section_type": "motor_spec"},
                },
            ],
            section={
                "title": "5 高炉鼓风机同步电机适配与接口方案",
                "purpose": "说明同步电机参数适配、励磁接口和辅助测点边界。",
                "expected_evidence_types": ["section", "parameter"],
                "asset_required": True,
                "parameter_sensitive": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_motor_table"])

    def test_filter_recommended_assets_for_supply_scope_drops_service_tables(self) -> None:
        filtered = filter_recommended_assets_for_section(
            [
                {
                    "asset_id": "asset_service",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "12 售后服务",
                    "heading_path": "12 售后服务",
                    "preview_text": "售后响应和巡检计划",
                    "metadata": {"section_type": "service_support"},
                },
                {
                    "asset_id": "asset_motor",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "A. Rated data 额定数据",
                    "heading_path": "A. Rated data 额定数据",
                    "preview_text": "电机额定数据、安装数据及自然环境条件",
                    "metadata": {"section_type": "motor_spec"},
                },
                {
                    "asset_id": "asset_scope",
                    "asset_type": "table",
                    "visual_role": "table_asset",
                    "title": "2 供货范围 Scopes of supply",
                    "heading_path": "2 供货范围 Scopes of supply",
                    "preview_text": "主要设备供货范围、软件及资料交付边界",
                    "metadata": {"section_type": "supply_scope"},
                },
            ],
            section={
                "title": "12 供货范围与接口分工",
                "purpose": "明确主设备供货范围、随机资料和接口责任边界。",
                "expected_evidence_types": ["table", "parameter", "section"],
                "asset_required": True,
                "parameter_sensitive": True,
            },
        )

        self.assertEqual([item["asset_id"] for item in filtered], ["asset_scope"])

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

    def test_sanitize_generated_section_content_removes_xml_prompt_tags_and_internal_headings(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="控制系统方案",
            content_md=(
                "<section_request>\n"
                "## 控制系统方案\n\n"
                "### 建议插入图表\n\n"
                "- 推荐原因：匹配控制系统示意图\n"
                "- [[ASSET:FIGURE:asset-001]] 控制系统总图\n\n"
                "### 控制架构与联锁分工\n\n"
                "本地控制单元 PLC 与 LCI PLC 协同完成启停顺控和联锁保护。\n"
                "</section_request>\n"
            ),
        )

        self.assertNotIn("<section_request>", cleaned)
        self.assertNotIn("建议插入图表", cleaned)
        self.assertNotIn("推荐原因：", cleaned)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", cleaned)
        self.assertIn("### 控制架构与联锁分工", cleaned)

    def test_sanitize_generated_section_content_removes_leading_section_purpose(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="需求分析",
            section_purpose="梳理客户核心需求、约束条件与关键指标。",
            content_md=(
                "## 需求分析\n\n"
                "梳理客户核心需求、约束条件与关键指标。\n\n"
                "### 核心需求\n\n"
                "- 系统需支持 10kV 高压变频调速。\n"
            ),
        )

        self.assertNotIn("梳理客户核心需求、约束条件与关键指标。", cleaned)
        self.assertIn("### 核心需求", cleaned)

    def test_sanitize_generated_section_content_removes_generic_goal_opening(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="第三章 高压变频系统总体方案",
            content_md=(
                "## 第三章 高压变频系统总体方案\n\n"
                "本章节主要说明高压变频系统总体方案、主接线拓扑结构和设备配置。\n\n"
                "### 主接线拓扑结构\n\n"
                "主回路采用一拖一手动带输入输出隔离拓扑。\n"
            ),
        )

        self.assertNotIn("本章节主要说明", cleaned)
        self.assertIn("主回路采用一拖一手动带输入输出隔离拓扑", cleaned)

    def test_sanitize_generated_section_content_removes_around_explaining_opening(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="第四章 核心设备与技术参数",
            content_md=(
                "## 第四章 核心设备与技术参数\n\n"
                "本章围绕高压变频装置的核心性能指标、输入输出特性、运行控制功能及关键电气参数进行说明，"
                "确保设备满足2026年某钢铁厂烧结环冷风机在连续稳定运行、高效节能及工艺适配性方面的综合要求。\n\n"
                "### 核心控制功能\n\n"
                "- 自动校正功能：装置支持检测参数自动校正。\n"
            ),
        )

        self.assertNotIn("本章围绕", cleaned)
        self.assertIn("### 核心控制功能", cleaned)

    def test_sanitize_generated_section_content_removes_targeting_opening(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="第四章 核心设备与技术参数",
            content_md=(
                "## 第四章 核心设备与技术参数\n\n"
                "本章节针对2026年某钢铁厂烧结环冷风机高压变频节能改造所配置的高压变频装置，"
                "明确核心设备的技术架构、关键性能指标、电气特性及环境适应性要求。\n\n"
                "### 4.1 高压变频装置整体技术要求\n\n"
                "高压变频装置采用单元串联多电平拓扑结构。\n"
            ),
        )

        self.assertNotIn("本章节针对", cleaned)
        self.assertIn("高压变频装置采用单元串联多电平拓扑结构", cleaned)

    def test_sanitize_generated_section_content_removes_tbd_title_marker(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="7 电机技术规范（ TBD ）",
            content_md="电机采用高压同步电机，系统电压为10kV。\n",
        )

        self.assertIn("## 7 电机技术规范", cleaned)
        self.assertNotIn("TBD", cleaned)

    def test_sanitize_generated_section_content_keeps_technical_project_opening(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="主接线拓扑结构",
            content_md=(
                "## 主接线拓扑结构\n\n"
                "本项目主回路采用一拖一手动带输入输出隔离拓扑，配置输入侧和输出侧隔离刀闸。\n\n"
                "维护工况下应先分断高压断路器，再断开隔离刀闸。\n"
            ),
        )

        self.assertIn("本项目主回路采用一拖一手动带输入输出隔离拓扑", cleaned)

    def test_sanitize_generated_section_content_removes_bilingual_repetition(self) -> None:
        cleaned = sanitize_generated_section_content(
            section_title="LCI/SFC变频软起动系统总体方案",
            content_md=(
                "## LCI/SFC变频软起动系统总体方案\n\n"
                "### 系统方案 SYSTEM SOLUTION\n\n"
                "The diagram below shows single line diagram for a set of VFD system.\n\n"
                "- Start-up and synchronization of the synchronous motor is controlled by the SFC.\n"
                "- After having received all necessary feedback signals the SFC closes the incoming breaker ICB and OCB.\n"
                "在收到所有必要的反馈信号后，SFC 闭合 ICB 和 OCB，并按照加速转矩曲线驱动电机升速。\n\n"
                "### 负载数据 Load data\n\n"
                "The characteristic is based on the estimated value as follow 变频启动特性基于下列预估值：转动惯量 J=18695 kg.m2。\n\n"
                "- [[ASSET:FIGURE:7a2db162-6f63-4535-9332-9488ec7a10c7]] 3.1 变频软起系统单线图 Single line Diagram\n"
            ),
        )

        self.assertIn("### 系统方案", cleaned)
        self.assertIn("### 负载数据", cleaned)
        self.assertIn("LCI/SFC变频软起动系统总体方案", cleaned)
        self.assertIn("SFC 闭合 ICB 和 OCB", cleaned)
        self.assertIn("[[ASSET:FIGURE:7a2db162-6f63-4535-9332-9488ec7a10c7]]", cleaned)
        self.assertNotIn("SYSTEM SOLUTION", cleaned)
        self.assertNotIn("The diagram below", cleaned)
        self.assertNotIn("Start-up and synchronization", cleaned)
        self.assertNotIn("After having received", cleaned)
        self.assertNotIn("The characteristic is based", cleaned)
        self.assertNotIn("Single line Diagram", cleaned)

    def test_enrich_table_asset_from_source_ref_table_chunk(self) -> None:
        asset = {
            "asset_type": "table",
            "source_ref": "#/tables/2",
            "title": "2.3 高压变频器主要技术参数",
            "preview_text": "2.3 高压变频器主要技术参数",
            "metadata": {"source_ref": "#/tables/2"},
        }
        table_chunks = [
            SimpleNamespace(id="chunk-0", chunk_index=10, content="not a table"),
            SimpleNamespace(
                id="chunk-1",
                chunk_index=11,
                content="| 序号 | 设备名称 |\n| --- | --- |\n| 1 | 环冷风机 |",
            ),
            SimpleNamespace(
                id="chunk-2",
                chunk_index=12,
                content="| 序号 | 规范 | 参数 |\n| --- | --- | --- |\n| 1 | 输入电压 | 10kV |",
            ),
        ]

        enriched = _enrich_table_asset_from_source_chunks(asset, table_chunks)

        self.assertTrue(enriched)
        self.assertIn("输入电压", asset["metadata"]["raw_table_markdown"])
        self.assertIn("输入电压", asset["preview_text"])

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

    def test_build_section_reuse_query_intents_splits_title_detail_and_context(self) -> None:
        intents = build_section_reuse_query_intents(
            section={
                "title": "控制接口与通讯方案",
                "purpose": "说明 DCS/PLC 接口、信号点表与通讯边界。",
                "keywords": ["DCS", "PLC", "通信接口"],
                "expected_evidence_types": ["section", "table"],
                "section_class": "architecture",
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd", "industry": "钢铁"},
        )

        self.assertIn("控制接口与通讯方案", intents["title_text"])
        self.assertIn("DCS/PLC", intents["detail_text"])
        self.assertIn("hv_vfd", intents["context_text"])
        self.assertIn("钢铁", intents["context_terms"])

    def test_build_section_reuse_query_intents_sanitizes_document_delivery_query(self) -> None:
        intents = build_section_reuse_query_intents(
            section={
                "title": "项目交付资料与文档清单",
                "purpose": "列明设计图纸、技术说明书、操作维护手册、测试报告及合格证等交付文档。",
                "keywords": ["交付文档", "技术图纸", "table", "parameter"],
                "expected_evidence_types": ["table", "parameter", "section"],
                "section_class": "configuration",
                "asset_required": True,
                "parameter_sensitive": True,
            },
            global_params={"project_name": "某钢铁厂 LCI 变频软起项目", "product_line": "hv_vfd", "industry": "钢铁"},
            extra_terms=["LCI", "变频软起方案族", "控制柜", "PLC"],
        )

        self.assertIn("交付文档", intents["title_text"])
        self.assertIn("提交资料", intents["detail_text"])
        self.assertNotIn("table", intents["title_text"])
        self.assertNotIn("parameter", intents["title_text"])
        self.assertNotIn("LCI", intents["detail_text"])
        self.assertEqual(intents["context_text"], "")

    def test_build_reuse_query_terms_expands_domain_synonyms(self) -> None:
        intents = build_section_reuse_query_intents(
            section={
                "title": "VFD整体方案",
                "purpose": "说明可编程逻辑控制器与分布式控制系统之间的通讯接口。",
                "keywords": ["频率转换器", "可编程逻辑控制器", "分布式控制系统"],
                "expected_evidence_types": ["section"],
                "section_class": "architecture",
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertIn("变频器", intents["title_terms"])
        self.assertIn("总体方案", intents["title_terms"])
        self.assertIn("plc", intents["detail_terms"])
        self.assertIn("dcs", intents["detail_terms"])
        self.assertIn("通信", intents["detail_terms"])

        query_terms = _build_reuse_query_terms(
            section={
                "title": "VFD整体方案",
                "purpose": "说明可编程逻辑控制器与分布式控制系统之间的通讯接口。",
                "keywords": ["频率转换器", "可编程逻辑控制器", "分布式控制系统"],
                "expected_evidence_types": ["section"],
                "section_class": "architecture",
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertIn("变频器", query_terms)
        self.assertIn("plc", query_terms)
        self.assertIn("dcs", query_terms)

    def test_build_reuse_query_terms_accepts_ai_wiki_extra_terms(self) -> None:
        query_terms = _build_reuse_query_terms(
            section={
                "title": "主回路系统方案",
                "purpose": "说明主回路结构与旁路切换。",
                "keywords": ["主回路"],
            },
            global_params={"project_name": "测试项目"},
            extra_terms=["VFD", "变频柜"],
        )

        self.assertIn("vfd", query_terms)
        self.assertIn("变频器", query_terms)
        self.assertIn("变频柜", query_terms)

    def test_retrieve_case_library_matches_records_knowledge_wiki_terms_in_trace(self) -> None:
        class _FakeKnowledgeWiki:
            def collect_query_expansion_terms(self, *, section, global_params):
                return ["变频器", "VFD", "变频柜"]

            def build_section_context(self, *, section, global_params):
                return ""

        class _FakeCaseLibrary:
            def retrieve_sections(self, **kwargs):
                self.last_section_query = kwargs["query"]
                return [
                    {
                        "sample_id": "sample-001",
                        "file_name": "历史方案A.docx",
                        "section_id": "2.1",
                        "section_path": "第二章 > 2.1 主回路方案",
                        "heading_path": "第二章 > 2.1 主回路方案",
                        "source_heading": "2.1 主回路方案",
                        "score": 0.91,
                        "reason": "query_intent_heading_overlap",
                        "level": 2,
                    }
                ]

            def retrieve_blocks(self, **kwargs):
                self.last_block_query = kwargs["query"]
                return []

            def expand_related_blocks(self, **kwargs):
                return []

        service = SectionDraftService(
            case_library=_FakeCaseLibrary(),
            knowledge_wiki=_FakeKnowledgeWiki(),
        )

        result = service._retrieve_case_library_matches(
            section={"title": "VFD 主回路方案", "purpose": "说明变频器主回路结构。", "keywords": ["VFD"]},
            evidence_bundle=SimpleNamespace(
                content={"case_candidates": [{"sample_id": "sample-001", "library_track": "pilot_main"}]}
            ),
            global_params={"project_name": "测试项目"},
        )

        self.assertIn("knowledge_wiki_terms", result["trace"])
        self.assertEqual(result["trace"]["knowledge_wiki_terms"], ["变频器", "VFD", "变频柜"])
        self.assertIn("变频器", result["trace"]["query"])
        self.assertIn("VFD", result["trace"]["query"])

    def test_retrieve_case_library_matches_records_knowledge_wiki_cards_in_trace(self) -> None:
        class _FakeKnowledgeWiki:
            def collect_retrieval_prior_bundle(self, *, section, global_params):
                return {
                    "query_expansion_terms": ["高压变频器", "功率单元"],
                    "product_cards": [{"title": "高压变频器方案族"}],
                    "module_cards": [{"title": "功率单元"}],
                }

            def build_section_context(self, *, section, global_params):
                return ""

        class _FakeCaseLibrary:
            def retrieve_sections(self, **kwargs):
                self.last_section_query = kwargs["query"]
                return []

            def retrieve_blocks(self, **kwargs):
                self.last_block_query = kwargs["query"]
                return []

            def expand_related_blocks(self, **kwargs):
                return []

        case_library = _FakeCaseLibrary()
        service = SectionDraftService(
            case_library=case_library,
            knowledge_wiki=_FakeKnowledgeWiki(),
        )

        result = service._retrieve_case_library_matches(
            section={"title": "主回路系统方案", "purpose": "说明高压变频器主回路结构。", "keywords": ["功率单元"]},
            evidence_bundle=SimpleNamespace(content={"case_candidates": [{"sample_id": "sample-001"}]}),
            global_params={"project_name": "测试项目"},
        )

        self.assertEqual(result["trace"]["knowledge_wiki_terms"], ["高压变频器", "功率单元"])
        self.assertEqual(result["trace"]["knowledge_wiki_product_cards"], ["高压变频器方案族"])
        self.assertEqual(result["trace"]["knowledge_wiki_module_cards"], ["功率单元"])
        self.assertIn("高压变频器", case_library.last_section_query)
        self.assertIn("功率单元", case_library.last_section_query)

    def test_retrieve_case_library_matches_preserves_structured_section_trace(self) -> None:
        class _FakeKnowledgeWiki:
            def collect_retrieval_prior_bundle(self, *, section, global_params):
                return {"query_expansion_terms": [], "product_cards": [], "module_cards": []}

            def build_section_context(self, *, section, global_params):
                return ""

        class _FakeCaseLibrary:
            def retrieve_sections(self, **kwargs):
                return [
                    {
                        "sample_id": "sample-001",
                        "file_name": "历史方案A.docx",
                        "section_id": "4.1",
                        "section_path": "第四章 技术架构 > 4.1 总体架构",
                        "source_heading": "4.1 总体架构",
                        "level": 2,
                        "score": 0.86,
                        "reason": "normalized_section_title_match; hybrid_rrf_boost=0.080",
                        "reason_trace": [
                            "normalized_section_title_match",
                            "hybrid_rrf_boost=0.080",
                            "hybrid_rrf_sources=sparse,semantic",
                        ],
                        "score_breakdown": {
                            "base": 0.78,
                            "sparse": 0.64,
                            "semantic": 0.91,
                            "hybrid_rrf": 0.08,
                            "rerank": 0.0,
                            "hybrid_rerank": 0.0,
                            "final": 0.86,
                        },
                    }
                ]

            def retrieve_blocks(self, **kwargs):
                return []

            def expand_related_blocks(self, **kwargs):
                return []

        service = SectionDraftService(
            case_library=_FakeCaseLibrary(),
            knowledge_wiki=_FakeKnowledgeWiki(),
        )

        result = service._retrieve_case_library_matches(
            section={"title": "技术架构", "purpose": "说明总体架构。"},
            evidence_bundle=SimpleNamespace(content={"case_candidates": [{"sample_id": "sample-001"}]}),
            global_params={"project_name": "测试项目"},
        )

        candidate = result["trace"]["section_candidates"][0]
        self.assertEqual(candidate["section_id"], "4.1")
        self.assertEqual(candidate["reason_trace"][1], "hybrid_rrf_boost=0.080")
        self.assertEqual(candidate["score_breakdown"]["semantic"], 0.91)
        self.assertEqual(candidate["score_breakdown"]["final"], 0.86)

    def test_build_reusable_blocks_preserves_case_library_retrieval_breakdown(self) -> None:
        blocks = build_reusable_blocks(
            section={"title": "技术架构", "generation_mode": "reuse_first"},
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={"project_name": "测试项目"},
            case_library_matches=[
                {
                    "sample_id": "sample-001",
                    "chunk_index": 7,
                    "file_name": "历史方案A.docx",
                    "source_section_id": "4.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                    "content": "系统采用站控层、网络层和装置层分层设计。",
                    "chunk_type": "section",
                    "score": 0.86,
                    "reason": "normalized_section_title_match; hybrid_rrf_boost=0.080",
                    "reason_trace": ["normalized_section_title_match", "hybrid_rrf_boost=0.080"],
                    "score_breakdown": {
                        "base": 0.78,
                        "sparse": 0.64,
                        "semantic": 0.91,
                        "hybrid_rrf": 0.08,
                        "final": 0.86,
                    },
                    "section_type": "architecture",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                }
            ],
        )

        self.assertEqual(blocks[0]["retrieval_reason_trace"][1], "hybrid_rrf_boost=0.080")
        self.assertEqual(blocks[0]["retrieval_score_breakdown"]["semantic"], 0.91)

    def test_build_reusable_blocks_for_overall_solution_drops_service_and_project_noise(self) -> None:
        blocks = build_reusable_blocks(
            section={
                "title": "第三章 高压变频系统总体方案",
                "purpose": "提供环冷风机变频改造主接线拓扑与系统架构设计，说明功率单元串联多电平技术路线、冷却方式及柜体布置思路。",
                "expected_evidence_types": ["section", "figure"],
                "section_class": "architecture",
                "generation_mode": "reuse_first",
            },
            evidence_bundle=SimpleNamespace(content={"results": []}),
            global_params={"project_name": "测试项目"},
            case_library_matches=[
                {
                    "sample_id": "sample-001",
                    "chunk_index": 12,
                    "file_name": "历史方案A.docx",
                    "heading_path": ["第五章", "5.5 供方培训计划"],
                    "source_heading": "5.5 供方培训计划",
                    "section_path": "第五章 > 5.5 供方培训计划",
                    "content": "## 5.5 供方培训计划\n项目提供两天培训，包含操作、维护和售后服务内容。",
                    "chunk_type": "section",
                    "score": 0.98,
                    "section_type": "service_support",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-001",
                    "chunk_index": 13,
                    "file_name": "历史方案A.docx",
                    "heading_path": ["第五章", "5.2 建设、经营方案"],
                    "source_heading": "5.2 建设、经营方案",
                    "section_path": "第五章 > 5.2 建设、经营方案",
                    "content": "## 5.2 建设、经营方案\n项目采用节能效益分享模式，合同期满后设备所有权转移。",
                    "chunk_type": "section",
                    "score": 0.96,
                    "section_type": "unknown",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-001",
                    "chunk_index": 14,
                    "file_name": "历史方案A.docx",
                    "heading_path": ["第三章 系统及方案介绍", "2.4 控制信号接口说明"],
                    "source_heading": "2.4 控制信号接口说明",
                    "section_path": "第三章 系统及方案介绍 > 2.4 控制信号接口说明",
                    "content": "DCS 与变频器之间采用硬接线 I/O 信号，包括启动允许、运行反馈和故障报警。",
                    "chunk_type": "section",
                    "score": 0.93,
                    "section_type": "communication_interface",
                    "equipment_type": "dcs_plc_interface",
                    "content_form": "narrative",
                },
                {
                    "sample_id": "sample-001",
                    "chunk_index": 15,
                    "file_name": "历史方案A.docx",
                    "heading_path": ["第三章 系统及方案介绍", "2.2 高压变频器主回路方案说明"],
                    "source_heading": "2.2 高压变频器主回路方案说明",
                    "section_path": "第三章 系统及方案介绍 > 2.2 高压变频器主回路方案说明",
                    "content": "高压变频器主回路采用一拖一手动旁路方案，系统包含输入隔离、变频器、输出隔离和工频旁路回路。",
                    "chunk_type": "section",
                    "score": 0.78,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                },
            ],
        )

        self.assertEqual([block["source_heading"] for block in blocks], ["2.2 高压变频器主回路方案说明"])

    def test_evidence_judge_filters_noise_before_reuse_pack(self) -> None:
        class _JudgeLLMClient:
            def __init__(self) -> None:
                self.requests = []

            async def invoke(self, request):
                self.requests.append(request)
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "summary": "保留主回路证据。",
                            "items": [
                                {
                                    "candidate_id": "c01",
                                    "decision": "core",
                                    "confidence": 0.84,
                                    "reason": "主回路和旁路拓扑可支撑总体方案。",
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    model_used="judge:test",
                )

        service = SectionDraftService(executor=ExecutorAgent(llm_client=_JudgeLLMClient()))
        service.evidence_judge_mode = "strict"
        blocks = [
            {
                "block_id": "case:sample-001:12",
                "source_title": "历史方案A.docx",
                "source_heading": "5.5 供方培训计划",
                "heading_path": ["第五章", "5.5 供方培训计划"],
                "section_path": "第五章 > 5.5 供方培训计划",
                "content_md": "供方提供操作培训和售后服务。",
                "selection_score": 1.0,
                "metadata": {"section_type": "service_support", "content_form": "narrative"},
            },
            {
                "block_id": "case:sample-001:15",
                "source_title": "历史方案A.docx",
                "source_heading": "2.2 高压变频器主回路方案说明",
                "heading_path": ["第三章 系统及方案介绍", "2.2 高压变频器主回路方案说明"],
                "section_path": "第三章 系统及方案介绍 > 2.2 高压变频器主回路方案说明",
                "content_md": "高压变频器主回路采用输入隔离、变频器、输出隔离和工频旁路回路。",
                "selection_score": 0.82,
                "metadata": {"section_type": "main_circuit_scheme", "equipment_type": "vfd", "content_form": "narrative"},
            },
        ]

        filtered, trace = asyncio.run(
            service._filter_reusable_blocks_with_evidence_judge(
                task_id="judge-test",
                section={
                    "title": "第三章 高压变频系统总体方案",
                    "purpose": "提供变频改造主接线拓扑与系统架构设计。",
                    "expected_evidence_types": ["section", "figure"],
                    "generation_mode": "reuse_first",
                },
                global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
                reusable_blocks=blocks,
            )
        )

        self.assertEqual([block["source_heading"] for block in filtered], ["2.2 高压变频器主回路方案说明"])
        self.assertEqual(trace["status"], "applied")
        self.assertEqual(trace["dropped_count"], 1)
        self.assertEqual(trace["deterministic_prefilter"]["dropped_count"], 1)
        self.assertEqual(filtered[0]["evidence_judge"]["decision"], "core")
        self.assertEqual(service.executor.llm_client.requests[0].task_type.value, "evidence_judge")

    def test_evidence_judge_skips_llm_when_deterministic_filter_drops_all_candidates(self) -> None:
        class _NoCallJudgeLLMClient:
            def __init__(self) -> None:
                self.requests = []

            async def invoke(self, request):
                self.requests.append(request)
                raise AssertionError("LLM judge should not be called for deterministic drops")

        service = SectionDraftService(executor=ExecutorAgent(llm_client=_NoCallJudgeLLMClient()))
        service.evidence_judge_mode = "auto"
        filtered, trace = asyncio.run(
            service._filter_reusable_blocks_with_evidence_judge(
                task_id="judge-deterministic-test",
                section={
                    "title": "7 电机技术规范",
                    "purpose": "说明电机本体额定参数、绝缘、防护、冷却和测温要求。",
                    "generation_mode": "reuse_first",
                },
                global_params={"project_name": "测试项目"},
                reusable_blocks=[
                    {
                        "block_id": "case:sample-001:11",
                        "source_title": "历史方案A.docx",
                        "source_heading": "5.1.2 调速装置的状态信息",
                        "heading_path": ["5. 变频启动装置与上位机的接口", "5.1.2 调速装置的状态信息"],
                        "content_md": "调速装置向DCS提供待机状态、正常运行状态、故障状态等开关量。",
                        "selection_score": 0.57,
                        "metadata": {"section_type": "communication_interface", "content_form": "narrative"},
                    },
                    {
                        "block_id": "case:sample-001:12",
                        "source_title": "历史方案A.docx",
                        "source_heading": "6. 标签和喷漆",
                        "heading_path": ["6. 标签和喷漆"],
                        "content_md": "开关柜正背面应有标签，喷漆颜色采用供货商标准。",
                        "selection_score": 0.29,
                        "selection_reasons": [
                            "keyword_mismatch_penalty",
                            "unknown_section_type_penalty",
                            "equipment_type_mismatch_penalty",
                        ],
                        "metadata": {"section_type": "unknown", "equipment_type": "switchgear", "content_form": "narrative"},
                    }
                ],
            )
        )

        self.assertEqual(filtered, [])
        self.assertEqual(trace["status"], "applied")
        self.assertEqual(trace["dropped_count"], 2)
        self.assertEqual(trace["deterministic_prefilter"]["reason"], "deterministic_noise_filter")
        self.assertEqual(service.executor.llm_client.requests, [])

    def test_evidence_judge_falls_back_on_invalid_response(self) -> None:
        class _BadJudgeLLMClient:
            async def invoke(self, request):
                return SimpleNamespace(content="not-json", model_used="judge:test")

        service = SectionDraftService(executor=ExecutorAgent(llm_client=_BadJudgeLLMClient()))
        service.evidence_judge_mode = "strict"
        blocks = [
            {
                "block_id": "case:sample-001:15",
                "source_heading": "2.2 高压变频器主回路方案说明",
                "heading_path": ["2.2 高压变频器主回路方案说明"],
                "content_md": "高压变频器主回路采用输入隔离和旁路回路。",
                "selection_score": 0.82,
                "metadata": {"section_type": "main_circuit_scheme", "content_form": "narrative"},
            }
        ]

        filtered, trace = asyncio.run(
            service._filter_reusable_blocks_with_evidence_judge(
                task_id="judge-fallback-test",
                section={
                    "title": "第三章 高压变频系统总体方案",
                    "purpose": "提供变频改造主接线拓扑与系统架构设计。",
                    "generation_mode": "reuse_first",
                },
                global_params={"project_name": "测试项目"},
                reusable_blocks=blocks,
            )
        )

        self.assertEqual(filtered, blocks)
        self.assertEqual(trace["status"], "fallback_error")

    def test_build_reuse_pack_keeps_retrieval_trace(self) -> None:
        reuse_pack = build_reuse_pack(
            section={"title": "技术架构", "generation_mode": "reuse_first"},
            global_params={"project_name": "测试项目"},
            reusable_blocks=[],
            recommended_assets=[],
            retrieval_trace={
                "query": "技术架构 站控层 网络层",
                "scoped_sections": [
                    {
                        "section_id": "4.1",
                        "section_path": "第四章 技术架构 > 4.1 总体架构",
                        "score": 0.84,
                        "reason": "normalized_section_title_match",
                    }
                ],
            },
        )

        self.assertEqual(reuse_pack["retrieval_trace"]["query"], "技术架构 站控层 网络层")
        self.assertEqual(reuse_pack["retrieval_trace"]["scoped_sections"][0]["section_id"], "4.1")

    def test_build_reuse_pack_keeps_evidence_selector_trace(self) -> None:
        selector_trace = {
            "status": "applied",
            "selected_blocks": [{"block_id": "block-main"}],
            "selected_assets": [{"asset_id": "asset-main"}],
            "risk_flags": ["low_confidence_selection"],
        }
        reuse_pack = build_reuse_pack(
            section={"title": "主回路方案", "generation_mode": "reuse_first"},
            global_params={"project_name": "测试项目"},
            reusable_blocks=[],
            recommended_assets=[],
            evidence_selector_trace=selector_trace,
        )
        trace = _build_composition_retrieval_trace(
            evidence_trace={},
            asset_trace={},
            reuse_pack=reuse_pack,
            generation_details={},
        )

        self.assertEqual(reuse_pack["evidence_selector_trace"], selector_trace)
        self.assertEqual(trace["layers"]["reuse"]["evidence_selector"]["status"], "applied")
        self.assertEqual(trace["layers"]["assets"]["evidence_selector"]["risk_flags"], ["low_confidence_selection"])

    def test_build_selected_block_trace_preserves_retrieval_breakdown(self) -> None:
        trace = _build_selected_block_trace(
            [
                {
                    "block_id": "case:sample-001:7",
                    "source_title": "历史方案A.docx",
                    "source_section_id": "4.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                    "selection_score": 0.93,
                    "selection_reasons": ["normalized_section_title_match"],
                    "retrieval_reason": "semantic_match=0.880; hybrid_rrf_boost=0.080",
                    "retrieval_reason_trace": ["semantic_match=0.880", "hybrid_rrf_boost=0.080"],
                    "retrieval_score_breakdown": {
                        "base": 0.78,
                        "semantic": 0.88,
                        "hybrid_rrf": 0.08,
                        "final": 0.86,
                    },
                    "selection_score_breakdown": {"final_score": 0.93},
                }
            ]
        )

        self.assertEqual(trace[0]["retrieval_reason_trace"][0], "semantic_match=0.880")
        self.assertEqual(trace[0]["retrieval_score_breakdown"]["semantic"], 0.88)
        self.assertEqual(trace[0]["selection_score_breakdown"]["final_score"], 0.93)

    def test_build_reuse_pack_treats_table_assets_as_reference_for_supply_scope(self) -> None:
        reuse_pack = build_reuse_pack(
            section={
                "title": "供货范围",
                "purpose": "说明 LCI 系统供货范围。",
                "generation_mode": "reuse_first",
                "asset_required": True,
                "expected_evidence_types": ["table", "section"],
            },
            global_params={"project_name": "测试项目"},
            reusable_blocks=[],
            recommended_assets=[
                {"asset_type": "table", "asset_id": "asset-table", "title": "供货清单"},
                {
                    "asset_type": "figure",
                    "asset_id": "asset-figure",
                    "title": "系统示意图",
                    "metadata": {"source_section_id": "2.1", "sample_id": "sample-a"},
                },
            ],
        )

        placeholders = [item["placeholder"] for item in reuse_pack["required_asset_placeholders"]]
        self.assertNotIn("[[ASSET:TABLE:asset-table]]", placeholders)
        self.assertIn("[[ASSET:FIGURE:asset-figure]]", placeholders)

    def test_build_composition_retrieval_trace_keeps_three_retrieval_layers(self) -> None:
        evidence_trace = _build_evidence_retrieval_trace(
            citations=[
                {
                    "evidence_id": "ev_001",
                    "source_doc_id": "doc_1",
                    "source_title": "历史方案A",
                    "heading_path": ["第3章", "主回路方案"],
                    "type": "section",
                    "relevance_score": 0.91,
                }
            ],
            preferred_evidence_ids={"ev_001"},
        )
        asset_trace = _build_asset_retrieval_trace(
            query="主回路 单线图",
            asset_types=["figure"],
            skipped_optional_search=False,
            search_trace={
                "visual_branch_enabled": True,
                "visual_backend": "clip",
                "visual_collection_available": True,
            },
            recommended_assets=[
                {
                    "asset_id": "asset-001",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "display_title": "主回路一次原理图",
                    "document_name": "历史方案A.pdf",
                    "heading_path": "2.2 主回路方案",
                    "score": 0.88,
                    "reason_trace": ["branch=textual+visual", "final=0.880"],
                    "score_breakdown": {"branch": "textual+visual", "final": 0.88},
                }
            ],
        )
        reuse_pack = build_reuse_pack(
            section={"title": "主回路系统方案", "generation_mode": "reuse_first"},
            global_params={"project_name": "测试项目"},
            reusable_blocks=[],
            recommended_assets=[],
            retrieval_trace={
                "query": "主回路系统方案 单线图",
                "query_intents": {"title_text": "主回路系统方案"},
                "knowledge_wiki_terms": ["高压变频器", "功率单元"],
                "knowledge_wiki_product_cards": ["高压变频器方案族"],
                "knowledge_wiki_module_cards": ["功率单元"],
                "section_candidates": [{"section_id": "2.2", "score": 0.86}],
                "scoped_sections": [{"section_id": "2.2", "score": 0.86}],
            },
        )

        trace = _build_composition_retrieval_trace(
            evidence_trace=evidence_trace,
            asset_trace=asset_trace,
            reuse_pack=reuse_pack,
            generation_details={
                "retrieval_mode": "section_pack",
                "selected_sections": [{"section_id": "2.2", "score": 0.86}],
                "selected_blocks": [
                    {
                        "source_title": "历史方案A",
                        "selection_score": 0.93,
                        "selection_score_breakdown": {
                            "knowledge_wiki_prior_total": 0.07,
                            "knowledge_wiki_product_match": 0.03,
                            "knowledge_wiki_module_match": 0.04,
                            "final_score": 0.93,
                        },
                    }
                ],
                "knowledge_wiki_prior_summary": {
                    "selected_block_count": 1,
                    "prior_hit_block_count": 1,
                    "prior_hit_ratio": 1.0,
                    "total_prior_boost": 0.07,
                    "max_prior_boost": 0.07,
                    "reason_hits": {"knowledge_wiki_module_match": 1, "knowledge_wiki_product_match": 1},
                },
                "selection_reason": {"mode": "section_pack", "reasons": ["section_pack_due_to_low_section_lead"]},
                "token_budget": {"within_budget": True},
            },
        )

        self.assertEqual(trace["pipeline"], "composition_main")
        self.assertEqual(trace["layers"]["evidence"]["role"], "需求/证据层")
        self.assertEqual(trace["layers"]["reuse"]["role"], "历史方案复用层")
        self.assertEqual(trace["layers"]["assets"]["role"], "图表/公式层")
        self.assertEqual(trace["layers"]["evidence"]["selected_items"][0]["evidence_id"], "ev_001")
        self.assertEqual(trace["layers"]["reuse"]["knowledge_wiki_terms"], ["高压变频器", "功率单元"])
        self.assertEqual(trace["layers"]["reuse"]["knowledge_wiki_product_cards"], ["高压变频器方案族"])
        self.assertEqual(trace["layers"]["reuse"]["knowledge_wiki_prior_summary"]["total_prior_boost"], 0.07)
        self.assertEqual(trace["layers"]["reuse"]["selected_sections"][0]["section_id"], "2.2")
        self.assertEqual(trace["layers"]["reuse"]["selection_reason"]["mode"], "section_pack")
        self.assertEqual(trace["layers"]["assets"]["selected_assets"][0]["asset_id"], "asset-001")
        self.assertTrue(trace["layers"]["assets"]["search_trace"]["visual_branch_enabled"])
        self.assertEqual(trace["layers"]["assets"]["selected_assets"][0]["reason_trace"][0], "branch=textual+visual")
        self.assertEqual(trace["layers"]["assets"]["selected_assets"][0]["score_breakdown"]["final"], 0.88)

    def test_ensure_required_asset_placeholders_appends_missing_placeholders(self) -> None:
        content = ensure_required_asset_placeholders(
            content_md="## 技术架构\n\n正文内容。",
            reuse_pack={
                "recommended_assets": [
                    {"asset_id": "asset-001", "asset_type": "figure"},
                    {"asset_id": "asset-002", "asset_type": "table"},
                ],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "系统架构图"},
                    {"placeholder": "[[ASSET:TABLE:asset-002]]", "title": "接口参数表"},
                ]
            },
        )
        self.assertIn("### 相关图表", content)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", content)
        self.assertIn("[[ASSET:TABLE:asset-002]]", content)

    def test_ensure_required_asset_placeholders_skips_table_placeholders_for_table_chapters(self) -> None:
        content = ensure_required_asset_placeholders(
            content_md="## 供货范围\n\n| 序号 | 设备 |\n| --- | --- |\n| 1 | 变频器 |\n",
            reuse_pack={
                "target_taxonomy": {"section_type": "supply_scope"},
                "recommended_assets": [
                    {"asset_id": "asset-001", "asset_type": "figure"},
                    {"asset_id": "asset-002", "asset_type": "table"},
                ],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "系统图", "asset_type": "figure"},
                    {"placeholder": "[[ASSET:TABLE:asset-002]]", "title": "供货清单", "asset_type": "table"},
                ],
            },
        )

        self.assertIn("[[ASSET:FIGURE:asset-001]]", content)
        self.assertNotIn("[[ASSET:TABLE:asset-002]]", content)

    def test_ensure_required_asset_placeholders_inlines_matching_assets_under_subheading(self) -> None:
        content = ensure_required_asset_placeholders(
            content_md=(
                "## 总体方案\n\n"
                "### 高浓磨机电机控制及电机辅助设备监控系统方案\n\n"
                "本地控制单元 PLC 负责对辅助设备进行集中监控。\n\n"
                "### LCI 变频软起系统方案\n\n"
                "LCI 负责同步电机变频软起动及并网切换控制。\n"
            ),
            reuse_pack={
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "高浓磨机电机控制总图"},
                    {"placeholder": "[[ASSET:FIGURE:asset-002]]", "title": "LCI软起系统主回路"},
                ],
                "recommended_assets": [
                    {
                        "asset_id": "asset-001",
                        "display_title": "高浓磨机电机控制总图",
                        "heading_path": "3 高浓磨机电机控制及电机辅助设备监控系统方案",
                    },
                    {
                        "asset_id": "asset-002",
                        "display_title": "LCI软起系统主回路",
                        "heading_path": "4.1 LCI 变频软起系统方案",
                        "metadata": {
                            "semantic_summary": {
                                "applicable_sections": ["LCI变频软起系统方案"],
                            }
                        },
                    },
                ],
            },
        )

        self.assertNotIn("### 相关图表", content)
        self.assertIn(
            "### 高浓磨机电机控制及电机辅助设备监控系统方案\n\n本地控制单元 PLC 负责对辅助设备进行集中监控。\n\n[[ASSET:FIGURE:asset-001]]",
            content,
        )
        self.assertIn(
            "### LCI 变频软起系统方案\n\nLCI 负责同步电机变频软起动及并网切换控制。\n[[ASSET:FIGURE:asset-002]]",
            content,
        )

    def test_filter_reuse_blocks_for_assembly_skips_generic_latin_enum_heading(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["3 高浓磨机电机控制及电机辅助设备监控系统方案"],
                    "metadata": {"section_type": "motor_spec", "content_form": "narrative"},
                    "selection_score": 1.0,
                    "content_md": "本地控制单元 PLC 负责对辅助设备进行集中监控。",
                },
                {
                    "heading_path": ["A. 概述"],
                    "metadata": {"section_type": "motor_spec", "content_form": "narrative"},
                    "selection_score": 0.96,
                    "content_md": "负责与用户上位机系统接口，并提供运行界面。",
                },
            ],
            target_taxonomy={"section_type": "overall_solution", "equipment_type": "motor"},
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["heading_path"], ["3 高浓磨机电机控制及电机辅助设备监控系统方案"])

    def test_filter_reuse_blocks_for_assembly_skips_bilingual_generic_solution_heading(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["3 系统方案 System Solution"],
                    "metadata": {"section_type": "unknown", "content_form": "narrative"},
                    "selection_score": 0.94,
                    "content_md": "The starting SFC system is designed for two subsequent starts in one cycle.",
                },
                {
                    "heading_path": ["8 控制保护与系统可靠性设计"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.91,
                    "content_md": "系统应配置故障跳闸、报警分级、联锁闭锁和信号冗余采集逻辑。",
                },
            ],
            target_taxonomy={"section_type": "protection_interlock", "equipment_type": "vfd"},
            section={
                "title": "控制保护与系统可靠性设计",
                "purpose": "说明系统联锁、保护策略和可靠性设计要求。",
                "expected_evidence_types": ["section"],
            },
        )

        headings = {" > ".join(item.get("heading_path") or []) for item in filtered}
        self.assertIn("8 控制保护与系统可靠性设计", headings)
        self.assertNotIn("3 系统方案 System Solution", headings)

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

    def test_build_extractive_reuse_section_content_preserves_overall_solution_topology(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "系统方案",
                "purpose": "说明 LCI 变频软起系统单线图、启动同步过程和启动特性。",
                "keywords": ["LCI", "单线图", "启动同步"],
                "section_class": "architecture",
                "expected_evidence_types": ["section", "figure"],
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["3 系统方案 System Solution"],
                        "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                        "selection_score": 1.2,
                        "content_md": (
                            "#### 3.1 变频软起系统单线图 Single line Diagram\n\n"
                            "单套变频驱动系统的单线图如下所示。\n\n"
                            "**10 kV 母线**\n\n**ICB**\n\n**RCB**\n\n**LCI**\n\n**OCB**\n\n**SM**\n\n"
                            "<!-- image -->\n\n"
                            "#### 3.2 启动和同步过程描述 Description of Start and Sychronization\n\n"
                            "同步电机的启动和同步由变频器(SFC)控制。\n\n"
                            "SFC 按照预调整的加速转矩曲线将电机加速至约 95% 额定转速。\n\n"
                            "达到同步条件后，SFC 向运行断路器 RCB 发出合闸命令，随后退出运行，电机转入工频运行。"
                        ),
                    }
                ]
            },
            global_params={"project_name": "测试项目", "product_line": "lci"},
        )

        self.assertIn("单线拓扑包含", content)
        self.assertIn("ICB", content)
        self.assertIn("RCB", content)
        self.assertIn("约 95%", content)
        self.assertIn("工频运行", content)

    def test_build_extractive_reuse_section_content_orders_overall_solution_before_load_data(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "系统方案",
                "purpose": "说明 LCI 变频软起系统单线图、启动同步过程和启动特性。",
                "keywords": ["LCI", "单线图", "启动同步"],
                "section_class": "architecture",
                "expected_evidence_types": ["section"],
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["3.3 LCI 变频启动特性", "3.3.1 负载数据 Load data"],
                        "metadata": {"section_type": "starter_spec", "content_form": "formula"},
                        "selection_score": 1.2,
                        "content_md": "##### 3.3.1 负载数据 Load data\n\n负载数据用于描述风机启动转动惯量、起动阻力矩和静阻力矩。",
                    },
                    {
                        "heading_path": ["3 系统方案"],
                        "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                        "selection_score": 1.1,
                        "content_md": "#### 3.1 变频软起系统单线图\n\n**10 kV 母线**\n\n**ICB**\n\n**RCB**\n\n**LCI**\n\n单套变频驱动系统的单线图如下所示。",
                    },
                ]
            },
            global_params={"project_name": "测试项目", "product_line": "lci"},
        )

        self.assertIn("单线拓扑包含", content)
        self.assertNotIn("负载数据用于描述", content)

    def test_build_extractive_reuse_section_content_trims_bilingual_main_circuit_noise(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "高炉鼓风机 LCI 软起动主回路方案",
                "purpose": "说明主回路拓扑、切换路径和工频运行状态。",
                "keywords": ["主回路", "ICB", "RCB", "切换"],
                "section_class": "architecture",
                "expected_evidence_types": ["section"],
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["某钢铁企业鼓风机 LCI 技术协议"],
                        "metadata": {"section_type": "starter_spec", "content_form": "formula"},
                        "selection_score": 0.96,
                        "content_md": (
                            "启动和同步过程描述 Description of Start and Sychronization\n"
                            "Start-up and synchronization of the synchronous motor is controlled by the SFC.\n"
                            "同步电机的启动和同步由变频器(SFC)控制。\n"
                            "After having received all necessary feedback signals the SFC closes the incoming breaker ICB and OCB to the converter transformer.\n"
                            "在收到所有必要的反馈信号后，SFC闭合进线侧与变频变压器相连的断路器(ICB、OCB)。\n"
                            "到达95%转速后，同步装置开始运行。\n"
                            "达到同步条件后，运行断路器 RCB 合闸，LCI 退出主回路，电机转入工频运行。\n"
                            "LCI变频启动特性 LCI Start-up Characteristic\n"
                            "负载数据 Load data\n"
                            "总启动时间 147s，纯加速时间 112s。\n"
                            "变频器技术数据 Component Technical Data\n"
                            "变频器系统示意图 Converter System Overview\n"
                        ),
                    }
                ]
            },
            global_params={"project_name": "测试项目", "product_line": "lci"},
        )

        self.assertIn("SFC闭合进线侧与变频变压器相连的断路器(ICB、OCB)", content)
        self.assertIn("运行断路器 RCB 合闸", content)
        self.assertIn("电机转入工频运行", content)
        self.assertNotIn("Description of Start and Sychronization", content)
        self.assertNotIn("Load data", content)
        self.assertNotIn("总启动时间 147s", content)
        self.assertNotIn("Component Technical Data", content)

    def test_build_extractive_reuse_section_content_structures_site_conditions_section(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "工厂设计环境与边界条件",
                "purpose": "明确装置适用的自然环境、安装环境、运输储存条件和现场边界。",
                "keywords": ["环境温度", "安装条件", "运输储存"],
                "section_class": "configuration",
                "generation_mode": "reuse_first",
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["1 工厂设计环境 Plant design data"],
                        "metadata": {"section_type": "site_conditions", "content_form": "interface_table"},
                        "selection_score": 1.0,
                        "content_md": (
                            "| Location 安装位置 | |\n"
                            "| --- | --- |\n"
                            "| Altitude 海拔 | <1000m |\n"
                            "| Ambient temperature 环境温度 | Max. 40°C, Min. -10°C |\n"
                            "| Installation Location 安装位置 | Indoor 户内 |\n"
                            "| Hazardous Area 危险区域 | N.A 不适用 |\n"
                        ),
                    }
                ]
            },
            global_params={"project_name": "测试项目", "product_line": "lci"},
        )

        self.assertIn("## 工厂设计环境与边界条件", content)
        self.assertIn("### 环境与安装边界", content)
        self.assertIn("| 安装场所 |", content)
        self.assertIn("小于 1000 m", content)
        self.assertIn("最高 40°C，最低 -10°C", content)
        self.assertIn("### 公用工程与配套条件", content)
        self.assertIn("### 运输与储存要求", content)

    def test_build_extractive_reuse_section_content_structures_power_condition_section(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "供电系统条件与负载参数",
                "purpose": "梳理电网条件、母线电压等级和负载启动约束。",
                "keywords": ["供电系统", "负载参数", "短路容量"],
                "section_class": "configuration",
                "generation_mode": "reuse_first",
                "parameter_sensitive": True,
            },
            reuse_pack={
                "reusable_blocks": [
                    {
                        "heading_path": ["1 工厂设计环境 Plant design data"],
                        "metadata": {"section_type": "site_conditions", "content_form": "interface_table"},
                        "selection_score": 0.86,
                        "content_md": (
                            "| System rated voltage 系统额定电压 | 10500V ± 7% |\n"
                            "| --- | --- |\n"
                            "| System rated frequency 系统额定频率 | 50Hz ± 2% |\n"
                            "| Short-circuit power 短路容量 | Max. 472 MVA |\n"
                        ),
                    }
                ]
            },
            global_params={"project_name": "测试项目", "product_line": "lci", "voltage_level": "10kV", "business_objective": "为高炉鼓风机 10kV 同步电机配置 LCI/SFC 变频软起动系统。"},
        )

        self.assertIn("## 供电系统条件与负载参数", content)
        self.assertIn("### 项目已确认的设计输入", content)
        self.assertIn("| 系统电压等级 | 10kV | 项目需求 / 全局参数 |", content)
        self.assertIn("| 系统频率 | 50Hz | 项目需求 / 对标资料 |", content)
        self.assertIn("### 对标资料提取的供电与系统参考边界", content)
        self.assertIn("### 需在联络阶段锁定的项目参数", content)
        self.assertIn("短路容量", content)

    def test_use_deterministic_reuse_builder_for_site_and_power_sections(self) -> None:
        site_section = {
            "title": "工厂设计环境与边界条件",
            "purpose": "明确装置适用的自然环境、安装环境和现场边界。",
        }
        power_section = {
            "title": "供电系统条件与负载参数",
            "purpose": "梳理电网条件、负载启动约束和关键参数。",
            "parameter_sensitive": True,
        }
        generic_section = {
            "title": "控制保护与系统可靠性设计",
            "purpose": "说明系统联锁、保护配置和可靠性设计。",
        }

        self.assertTrue(
            _use_deterministic_reuse_builder(
                section=site_section,
                target_taxonomy=infer_target_taxonomy(site_section),
            )
        )
        self.assertTrue(
            _use_deterministic_reuse_builder(
                section=power_section,
                target_taxonomy=infer_target_taxonomy(power_section),
            )
        )
        self.assertFalse(
            _use_deterministic_reuse_builder(
                section=generic_section,
                target_taxonomy=infer_target_taxonomy(generic_section),
            )
        )

    def test_use_deterministic_reuse_builder_does_not_capture_startup_process_sections(self) -> None:
        section = {
            "title": "启动与同步过程描述",
            "purpose": "阐述电机从静止到并网运行的全过程，包括建磁、加速、同步捕捉及电网切换。",
        }

        self.assertFalse(
            _use_deterministic_reuse_builder(
                section=section,
                target_taxonomy=infer_target_taxonomy(section),
            )
        )

    def test_polish_extractive_reuse_section_content_adds_opening_and_table_lead(self) -> None:
        polished = polish_extractive_reuse_section_content(
            section={
                "title": "主回路系统方案",
                "section_class": "architecture",
                "generation_mode": "reuse_first",
                "keywords": ["主回路", "旁路"],
            },
            content_md=(
                "## 主回路系统方案\n\n"
                "### 设备选型与容量配置\n\n"
                "| 项目 | 配置 |\n| --- | --- |\n| 整流变压器 | 1套 |\n"
            ),
        )

        self.assertIn("本项目主回路按照安全隔离、旁路切换和连续运行要求进行配置", polished)
        self.assertIn("主要设备配置如下表所示。", polished)

    def test_resolve_reuse_generation_strategy_prefers_full_section_for_clear_winner(self) -> None:
        strategy = resolve_reuse_generation_strategy(
            section={
                "title": "技术架构",
                "generation_mode": "reuse_first",
            },
            reuse_pack={
                "retrieval_trace": {
                    "section_candidates": [
                        {
                            "sample_id": "sample-arch",
                            "file_name": "历史方案A.docx",
                            "section_id": "4.1",
                            "section_path": "第四章 技术架构 > 4.1 总体架构",
                            "score": 0.86,
                            "reason": "normalized_section_title_match",
                        },
                        {
                            "sample_id": "sample-other",
                            "file_name": "历史方案B.docx",
                            "section_id": "2.1",
                            "section_path": "第二章 项目概述 > 2.1 项目背景",
                            "score": 0.61,
                            "reason": "heading_family_match",
                        },
                    ],
                    "scoped_sections": [
                        {
                            "sample_id": "sample-arch",
                            "file_name": "历史方案A.docx",
                            "section_id": "4.1",
                            "section_path": "第四章 技术架构 > 4.1 总体架构",
                            "score": 0.86,
                            "reason": "normalized_section_title_match",
                        }
                    ],
                }
            },
            reusable_blocks=[
                {
                    "block_id": "case:sample-arch:7",
                    "sample_id": "sample-arch",
                    "source_title": "历史方案A.docx",
                    "source_section_id": "4.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                    "content_md": "系统采用站控层、网络层和装置层分层设计，支持 IEC 61850。",
                    "selection_score": 0.91,
                    "reusability_score": 0.84,
                },
                {
                    "block_id": "case:sample-arch:8",
                    "sample_id": "sample-arch",
                    "source_title": "历史方案A.docx",
                    "source_section_id": "4.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                    "content_md": "各子系统通过工业以太网互联，控制边界与接口职责明确。",
                    "selection_score": 0.88,
                    "reusability_score": 0.82,
                },
                {
                    "block_id": "case:sample-arch:9",
                    "sample_id": "sample-arch",
                    "source_title": "历史方案A.docx",
                    "source_section_id": "4.1.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构 > 4.1.1 网络层",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构", "4.1.1 网络层"],
                    "content_md": "网络层通过冗余环网实现站内通信。",
                    "selection_score": 0.86,
                    "reusability_score": 0.80,
                },
                {
                    "block_id": "case:sample-other:3",
                    "sample_id": "sample-other",
                    "source_title": "历史方案B.docx",
                    "source_section_id": "2.1",
                    "section_path": "第二章 项目概述 > 2.1 项目背景",
                    "heading_path": ["第二章 项目概述", "2.1 项目背景"],
                    "content_md": "项目背景与建设意义说明。",
                    "selection_score": 0.63,
                    "reusability_score": 0.60,
                },
            ],
            recommended_assets=[],
        )

        self.assertEqual(strategy["retrieval_mode"], "full_section")
        self.assertEqual(len(strategy["prompt_blocks"]), 3)
        self.assertEqual(strategy["selected_sections"][0]["section_id"], "4.1")
        self.assertTrue(strategy["token_budget"]["within_budget"])
        self.assertEqual(strategy["selection_reason"]["mode"], "full_section")
        self.assertIn("selected_full_section", strategy["selection_reason"]["reasons"])

    def test_resolve_reuse_generation_strategy_prefers_full_section_in_mvp_mode_when_lead_is_small(self) -> None:
        strategy = resolve_reuse_generation_strategy(
            section={
                "title": "技术架构",
                "generation_mode": "reuse_first",
            },
            reuse_pack={
                "retrieval_trace": {
                    "section_candidates": [
                        {
                            "sample_id": "sample-arch",
                            "file_name": "历史方案A.docx",
                            "section_id": "4.1",
                            "section_path": "第四章 技术架构 > 4.1 总体架构",
                            "score": 0.86,
                            "reason": "normalized_section_title_match",
                        },
                        {
                            "sample_id": "sample-arch-b",
                            "file_name": "历史方案B.docx",
                            "section_id": "5.1",
                            "section_path": "第五章 技术架构 > 5.1 架构说明",
                            "score": 0.82,
                            "reason": "normalized_section_title_match",
                        },
                    ],
                    "scoped_sections": [
                        {
                            "sample_id": "sample-arch",
                            "file_name": "历史方案A.docx",
                            "section_id": "4.1",
                            "section_path": "第四章 技术架构 > 4.1 总体架构",
                            "score": 0.86,
                            "reason": "normalized_section_title_match",
                        }
                    ],
                }
            },
            reusable_blocks=[
                {
                    "block_id": "case:sample-arch:7",
                    "sample_id": "sample-arch",
                    "source_title": "历史方案A.docx",
                    "source_section_id": "4.1",
                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                    "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                    "content_md": "系统采用站控层、网络层和装置层分层设计，支持 IEC 61850。",
                    "selection_score": 0.91,
                    "reusability_score": 0.84,
                }
            ],
            recommended_assets=[],
        )

        self.assertEqual(strategy["retrieval_mode"], "full_section")
        self.assertEqual(strategy["selection_reason"]["mode"], "full_section")
        self.assertEqual(strategy["selection_reason"]["context_mode"], "prefer_full_section")
        self.assertFalse(strategy["selection_reason"]["full_section_budget_enabled"])
        self.assertIn("selected_full_section", strategy["selection_reason"]["reasons"])

    def test_resolve_reuse_generation_strategy_dedupes_same_source_section_before_lead(self) -> None:
        strategy = resolve_reuse_generation_strategy(
            section={
                "title": "系统方案",
                "purpose": "说明LCI变频软起系统单线图、启动同步过程和启动特性。",
                "generation_mode": "reuse_first",
                "target_section_type": "overall_solution",
            },
            reuse_pack={
                "retrieval_trace": {
                    "section_candidates": [
                        {
                            "sample_id": "lci-sample",
                            "file_name": "样例LCI方案.docx",
                            "section_id": "3",
                            "section_path": "3. 系统方案 SYSTEM SOLUTION",
                            "score": 1.3673,
                            "reason": "section_title_match",
                        },
                        {
                            "sample_id": "lci-sample",
                            "file_name": "样例LCI方案.docx",
                            "section_id": "3",
                            "section_path": "3. 系统方案 SYSTEM SOLUTION",
                            "score": 1.3616,
                            "reason": "semantic_match",
                        },
                    ],
                    "scoped_sections": [],
                }
            },
            reusable_blocks=[
                {
                    "block_id": "case:lci:14:7",
                    "sample_id": "lci-sample",
                    "source_title": "样例LCI方案.docx",
                    "source_section_id": "3",
                    "section_path": "3. 系统方案 SYSTEM SOLUTION",
                    "heading_path": ["3. 系统方案 SYSTEM SOLUTION"],
                    "content_md": "#### 3.1 变频软起系统单线图\n\n单套变频驱动系统的单线图如下所示。",
                    "selection_score": 1.2,
                    "reusability_score": 1.1,
                    "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                },
                {
                    "block_id": "case:lci:14:8",
                    "sample_id": "lci-sample",
                    "source_title": "样例LCI方案.docx",
                    "source_section_id": "3",
                    "section_path": "3. 系统方案 SYSTEM SOLUTION",
                    "heading_path": ["3. 系统方案 SYSTEM SOLUTION"],
                    "content_md": "#### 3.2 启动和同步过程描述\n\n同步电机的启动和同步由变频器(SFC)控制。",
                    "selection_score": 1.2,
                    "reusability_score": 1.1,
                    "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                },
            ],
            recommended_assets=[],
        )

        self.assertEqual(strategy["retrieval_mode"], "full_section")
        self.assertEqual(strategy["selection_reason"]["mode"], "full_section")
        self.assertEqual(strategy["selection_reason"]["full_section_block_count"], 2)

    def test_resolve_reuse_generation_strategy_matches_child_section_inside_parent_block(self) -> None:
        strategy = resolve_reuse_generation_strategy(
            section={
                "title": "启动与同步过程描述",
                "purpose": "阐述SFC启动和同步切换全过程。",
                "generation_mode": "reuse_first",
            },
            reuse_pack={
                "retrieval_trace": {
                    "section_candidates": [
                        {
                            "sample_id": "lci-sample",
                            "file_name": "样例LCI方案.docx",
                            "section_id": "3.2",
                            "section_path": "3. 系统方案 SYSTEM SOLUTION > 3.2. 启动和同步过程描述",
                            "source_heading": "3.2. 启动和同步过程描述",
                            "score": 0.8821,
                            "reason": "section_title_match",
                        },
                        {
                            "sample_id": "lci-sample",
                            "file_name": "样例LCI方案.docx",
                            "section_id": "3",
                            "section_path": "3. 系统方案 SYSTEM SOLUTION",
                            "score": 0.8774,
                            "reason": "same_parent_section",
                        },
                    ]
                }
            },
            reusable_blocks=[
                {
                    "block_id": "case:lci:14",
                    "sample_id": "lci-sample",
                    "source_title": "样例LCI方案.docx",
                    "source_section_id": "3",
                    "section_path": "3. 系统方案 SYSTEM SOLUTION",
                    "heading_path": ["3. 系统方案 SYSTEM SOLUTION"],
                    "content_md": "#### 3.2 启动和同步过程描述\n\n同步电机的启动和同步由变频器(SFC)控制。",
                    "selection_score": 1.0,
                    "reusability_score": 0.9,
                    "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
                },
            ],
            recommended_assets=[],
        )

        self.assertEqual(strategy["retrieval_mode"], "full_section")
        self.assertEqual(strategy["selection_reason"]["full_section_block_count"], 1)

    def test_top_level_generation_marks_child_aware_retrieval(self) -> None:
        generated = build_generation_sections(
            [
                {
                    "section_id": "3",
                    "title": "第三章 系统方案",
                    "generation_mode": "reuse_first",
                    "children": [
                        {
                            "section_id": "3.1",
                            "title": "变频软起系统单线图",
                        },
                        {
                            "section_id": "3.2",
                            "title": "启动和同步过程描述",
                        },
                    ],
                }
            ],
            granularity="top_level",
        )

        self.assertEqual(len(generated), 1)
        self.assertTrue(generated[0]["child_aware_retrieval"])
        retrieval_sections = _build_child_retrieval_sections(generated[0])
        self.assertEqual([item["section_id"] for item in retrieval_sections], ["3.1", "3.2"])
        self.assertIn("所属大章节：第三章 系统方案", retrieval_sections[0]["purpose"])
        self.assertNotIn("启动和同步过程描述", retrieval_sections[0]["keywords"])

    def test_child_aware_strategy_forces_section_pack_and_expands_prompt_blocks(self) -> None:
        reusable_blocks = [
            {
                "block_id": f"case:lci:{index}",
                "sample_id": "lci-sample",
                "source_title": "样例LCI方案.docx",
                "source_section_id": "3",
                "section_path": "3. 系统方案 SYSTEM SOLUTION",
                "heading_path": ["3. 系统方案 SYSTEM SOLUTION", f"3.{index} 子节"],
                "content_md": f"LCI系统方案内容 {index}",
                "selection_score": 1.0 - index * 0.01,
                "reusability_score": 0.9,
                "metadata": {"section_type": "overall_solution", "content_form": "narrative"},
            }
            for index in range(1, 15)
        ]

        strategy = resolve_reuse_generation_strategy(
            section={
                "title": "第三章 系统方案",
                "generation_mode": "reuse_first",
                "child_aware_retrieval": True,
            },
            reuse_pack={
                "retrieval_trace": {
                    "child_aware": True,
                    "section_candidates": [
                        {
                            "sample_id": "lci-sample",
                            "file_name": "样例LCI方案.docx",
                            "section_id": "3",
                            "section_path": "3. 系统方案 SYSTEM SOLUTION",
                            "score": 0.95,
                            "reason": "section_title_match",
                        }
                    ],
                }
            },
            reusable_blocks=reusable_blocks,
            recommended_assets=[],
        )

        self.assertEqual(strategy["context_mode"], "section_pack")
        self.assertEqual(strategy["retrieval_mode"], "section_pack")
        self.assertEqual(len(strategy["prompt_blocks"]), 12)
        self.assertIn("section_pack_forced_by_context_mode", strategy["selection_reason"]["reasons"])

    def test_resolve_reuse_generation_strategy_keeps_auto_budgeted_behavior_when_configured(self) -> None:
        with patch.dict("os.environ", {"SECTION_REUSE_CONTEXT_MODE": "auto"}, clear=False):
            get_settings.cache_clear()
            try:
                strategy = resolve_reuse_generation_strategy(
                    section={
                        "title": "技术架构",
                        "generation_mode": "reuse_first",
                    },
                    reuse_pack={
                        "retrieval_trace": {
                            "section_candidates": [
                                {
                                    "sample_id": "sample-arch",
                                    "file_name": "历史方案A.docx",
                                    "section_id": "4.1",
                                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                                    "score": 0.86,
                                    "reason": "normalized_section_title_match",
                                },
                                {
                                    "sample_id": "sample-arch-b",
                                    "file_name": "历史方案B.docx",
                                    "section_id": "5.1",
                                    "section_path": "第五章 技术架构 > 5.1 架构说明",
                                    "score": 0.82,
                                    "reason": "normalized_section_title_match",
                                },
                            ],
                            "scoped_sections": [
                                {
                                    "sample_id": "sample-arch",
                                    "file_name": "历史方案A.docx",
                                    "section_id": "4.1",
                                    "section_path": "第四章 技术架构 > 4.1 总体架构",
                                    "score": 0.86,
                                    "reason": "normalized_section_title_match",
                                }
                            ],
                        }
                    },
                    reusable_blocks=[
                        {
                            "block_id": "case:sample-arch:7",
                            "sample_id": "sample-arch",
                            "source_title": "历史方案A.docx",
                            "source_section_id": "4.1",
                            "section_path": "第四章 技术架构 > 4.1 总体架构",
                            "heading_path": ["第四章 技术架构", "4.1 总体架构"],
                            "content_md": "系统采用站控层、网络层和装置层分层设计，支持 IEC 61850。",
                            "selection_score": 0.91,
                            "reusability_score": 0.84,
                        }
                    ],
                    recommended_assets=[],
                )
            finally:
                get_settings.cache_clear()

        self.assertEqual(strategy["retrieval_mode"], "section_pack")
        self.assertEqual(strategy["selection_reason"]["mode"], "section_pack")
        self.assertEqual(strategy["selection_reason"]["context_mode"], "auto")
        self.assertIn("section_pack_due_to_low_section_lead", strategy["selection_reason"]["reasons"])

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
        self.assertIn("### 主设备供货清单", content)
        self.assertIn("备注：本次供货仅提供供货范围表内的设备", content)
        self.assertIn("| 序号 | 设备名称 | 主要内容 | 数量 | 单位 | 说明 |", content)
        self.assertIn("| 1 | LCI 软起动装置 |", content)
        self.assertNotIn("Remark: ABB only provide", content)
        self.assertNotIn("| Index | Component | Type | Qty |", content)
        self.assertNotIn("LCI.SO A1212-211N465", content)
        self.assertNotIn("本次供货 本次供货", content)
        # Supply-scope/table-heavy sections should materialize table content
        # into Markdown instead of leaving raw TABLE placeholders in the draft.
        self.assertNotIn("[[ASSET:TABLE:asset-001]]", content)

    def test_build_extractive_reuse_section_content_completes_lci_supply_boundary_items(self) -> None:
        content = build_extractive_reuse_section_content(
            section={
                "title": "供货范围",
                "purpose": "列明 LCI 软起系统、输入/输出变压器、励磁控制盘及非供货边界。",
                "keywords": ["LCI", "输入变压器", "输出变压器", "励磁控制盘"],
                "section_class": "configuration",
                "generation_mode": "reuse_first",
                "expected_evidence_types": ["table", "section"],
                "asset_required": True,
            },
            reuse_pack={
                "target_taxonomy": {"section_type": "supply_scope"},
                "reusable_blocks": [
                    {
                        "heading_path": ["2", "供货范围"],
                        "metadata": {"section_type": "supply_scope", "content_form": "bom_table"},
                        "selection_score": 0.96,
                        "content_md": (
                            "| 序号 | 设备 | 型号 | 制造商 | 数量 | 单位 |\n"
                            "| --- | --- | --- | --- | --- | --- |\n"
                            "| 1 | 变频器（含同期并网装置） | LCI.SO A1212-211N465 | ABB | 1 | 台 |"
                        ),
                    }
                ],
                "required_asset_placeholders": [
                    {"placeholder": "[[ASSET:TABLE:asset-001]]", "title": "供货清单", "asset_type": "table"}
                ],
            },
            global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
        )

        self.assertIn("| 2 | 输入变压器 | 与 LCI 启动回路配套 | 1 | 台 | 额定参数以最终确认资料为准 |", content)
        self.assertIn("| 3 | 输出变压器 | 与电机侧启动回路配套 | 1 | 台 | 与主回路方案同步定型 |", content)
        self.assertIn("| 4 | 励磁控制盘 | 待技术确认 | 1 | 套 | 待技术确认 |", content)
        self.assertNotIn("[[ASSET:TABLE:asset-001]]", content)

    def test_build_asset_search_context_anchors_on_top_reuse_blocks(self) -> None:
        context = _build_asset_search_context(
            section={
                "title": "主回路系统方案",
                "purpose": "说明主回路结构与一次接线方案。",
                "section_class": "architecture",
                "expected_evidence_types": ["section", "figure"],
                "keywords": ["主回路", "旁路切换"],
            },
            reusable_blocks=[
                {
                    "source_title": "sample-lci-blower-starting-solution.docx",
                    "heading_path": ["2.2", "高压变频器主回路方案说明"],
                },
                {
                    "source_title": "sample-lci-blower-starting-solution.docx",
                    "heading_path": ["2.3", "高压变频器主要技术参数"],
                },
            ],
        )

        self.assertEqual(context["section_title"], "主回路系统方案")
        self.assertEqual(context["purpose"], "说明主回路结构与一次接线方案。")
        self.assertEqual(context["section_class"], "architecture")
        self.assertEqual(
            context["anchor_document_names"],
            ["sample-lci-blower-starting-solution.docx"],
        )
        self.assertIn("2.2 > 高压变频器主回路方案说明", context["anchor_heading_paths"])
        self.assertIn("主回路图", context["keywords"])

    def test_build_asset_search_context_marks_image_reference_blocks_as_asset_anchors(self) -> None:
        context = _build_asset_search_context(
            section={
                "title": "主接线拓扑结构",
                "purpose": "说明主回路结构与一次接线方案。",
                "expected_evidence_types": ["section", "figure"],
            },
            reusable_blocks=[
                {
                    "source_title": "某钢铁厂技术方案.docx",
                    "sample_id": "sample-a",
                    "source_section_id": "3.2",
                    "heading_path": ["二、系统方案"],
                    "content_md": "主回路采用一拖一输入输出隔离方案，一次原理如下图所示：\n<!-- image -->",
                },
                {
                    "source_title": "其他方案.docx",
                    "sample_id": "sample-b",
                    "source_section_id": "5.5",
                    "heading_path": ["培训计划"],
                    "content_md": "培训计划正文。",
                },
            ],
        )

        self.assertEqual(context["anchor_image_document_names"], ["某钢铁厂技术方案.docx"])
        self.assertEqual(context["anchor_image_sample_ids"], ["sample-a"])
        self.assertEqual(context["anchor_image_source_section_ids"], ["3.2"])

    def test_build_generation_summary_aggregates_effective_paths_and_fallback_rate(self) -> None:
        summary = _build_generation_summary(
            [
                {
                    "section_id": "3",
                    "effective_path": "extractive_reuse_llm_finalize",
                    "refinement_status": "fallback_assembled",
                    "refinement_error": "openai 503",
                    "quality_gate_status": "review_required",
                    "draft_status": "review_required",
                    "knowledge_wiki_prior_hit_block_count": 2,
                    "knowledge_wiki_prior_total_boost": 0.13,
                },
                {
                    "section_id": "4",
                    "effective_path": "llm_write",
                    "refinement_status": "not_applicable",
                    "refinement_error": "",
                    "quality_gate_status": "passed",
                    "draft_status": "generated",
                    "knowledge_wiki_prior_hit_block_count": 0,
                    "knowledge_wiki_prior_total_boost": 0.0,
                },
            ]
        )

        self.assertEqual(summary["total_sections"], 2)
        self.assertEqual(summary["effective_paths"]["extractive_reuse_llm_finalize"], 1)
        self.assertEqual(summary["effective_paths"]["llm_write"], 1)
        self.assertEqual(summary["quality_gate_statuses"]["passed"], 1)
        self.assertEqual(summary["quality_gate_statuses"]["review_required"], 1)
        self.assertEqual(summary["refinement_error_count"], 1)
        self.assertEqual(summary["fallback_rate"], 0.5)
        self.assertEqual(summary["knowledge_wiki_prior_sections"], 1)
        self.assertEqual(summary["knowledge_wiki_prior_block_count"], 2)
        self.assertEqual(summary["knowledge_wiki_prior_total_boost"], 0.13)

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

    def test_resolve_reuse_refinement_content_returns_fallback_reason(self) -> None:
        content, status, fallback_reason = resolve_reuse_refinement_content(
            assembled_content=(
                "## 主回路系统方案\n\n"
                "高压变频器主回路采用移相整流变压器配合功率单元串联结构，"
                "输入侧设置隔离开关和快速熔断器。\n"
            ),
            rewritten_content=(
                "## 主回路系统方案\n\n"
                "章节标题: 主回路系统方案\n"
                "当前关键参数: project_name=测试项目\n"
                "已根据要求完成重写。\n"
            ),
            section_title="主回路系统方案",
        )

        self.assertEqual(status, "fallback_assembled")
        self.assertEqual(fallback_reason, "rewrite_leakage")
        self.assertIn("移相整流变压器", content)

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

    def test_generate_section_content_forces_llm_finalize_after_extractive_assembly(self) -> None:
        class _FakeKnowledgeWiki:
            def build_section_context(self, *, section, global_params):
                return "AI Wiki 编译知识（术语、结构、口径约束）\n\n术语别名：\n- 变频器：VFD / 变频柜"

        class _FakeExecutor:
            def __init__(self) -> None:
                self.write_calls: list[dict] = []

            async def write_section(
                self,
                *,
                task_id,
                section,
                global_params,
                retrieved_context,
                outline_title,
                recommended_assets=None,
                reuse_pack=None,
                assembled_draft=None,
                preceding_context="",
            ):
                self.write_calls.append(
                    {
                        "task_id": task_id,
                        "section": section,
                        "assembled_draft": assembled_draft,
                        "retrieved_context": retrieved_context,
                        "preceding_context": preceding_context,
                    }
                )
                return SimpleNamespace(content=assembled_draft)

        executor = _FakeExecutor()
        service = SectionDraftService(executor=executor, knowledge_wiki=_FakeKnowledgeWiki())

        async def _run():
            return await service._generate_section_content(
                task_id="task-001",
                section={
                    "title": "主回路系统方案",
                    "purpose": "说明主回路结构与切换方式。",
                    "keywords": ["主回路", "旁路切换"],
                    "generation_mode": "reuse_first",
                    "section_class": "architecture",
                    "asset_required": True,
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
                retrieved_context="",
                citations=[],
                recommended_assets=[
                    {"asset_id": "asset-001", "asset_type": "figure", "display_title": "主回路示意图"}
                ],
                reusable_blocks=[
                    {
                        "heading_path": ["2.2", "高压变频器主回路方案说明"],
                        "metadata": {"section_type": "main_circuit_scheme", "content_form": "narrative"},
                        "selection_score": 0.92,
                        "content_md": "高压变频器主回路采用移相整流变压器配合功率单元串联结构。",
                    },
                    {
                        "heading_path": ["2.3", "旁路切换逻辑"],
                        "metadata": {"section_type": "control_logic", "content_form": "narrative"},
                        "selection_score": 0.84,
                        "content_md": "旁路切换时系统先确认主回路状态，再投入旁路接触器。",
                    },
                ],
                reuse_pack={
                    "generation_mode": "reuse_first",
                    "recommended_assets": [
                        {"asset_id": "asset-001", "asset_type": "figure", "display_title": "主回路示意图"}
                    ],
                    "reusable_blocks": [
                        {
                            "heading_path": ["2.2", "高压变频器主回路方案说明"],
                            "metadata": {"section_type": "main_circuit_scheme", "content_form": "narrative"},
                            "selection_score": 0.92,
                            "content_md": "高压变频器主回路采用移相整流变压器配合功率单元串联结构。",
                        },
                        {
                            "heading_path": ["2.3", "旁路切换逻辑"],
                            "metadata": {"section_type": "control_logic", "content_form": "narrative"},
                            "selection_score": 0.84,
                            "content_md": "旁路切换时系统先确认主回路状态，再投入旁路接触器。",
                        },
                    ],
                    "required_asset_placeholders": [
                        {"placeholder": "[[ASSET:FIGURE:asset-001]]", "title": "主回路示意图"}
                    ],
                },
                preceding_context="前序章节已覆盖内容（请勿重复）：\n- 项目概述：已说明改造目标。\n已覆盖主题：项目概述与改造目标",
            )

        content_md, draft_status, _, generation_details = asyncio.run(_run())

        self.assertEqual(draft_status, "generated")
        self.assertEqual(generation_details["effective_path"], "extractive_reuse_llm_finalize")
        self.assertEqual(len(executor.write_calls), 1)
        self.assertEqual(executor.write_calls[0]["task_id"], "task-001-finalize")
        self.assertIn("## 主回路系统方案", executor.write_calls[0]["assembled_draft"])
        self.assertIn("已覆盖主题：项目概述与改造目标", executor.write_calls[0]["preceding_context"])
        self.assertIn("AI Wiki 编译知识", executor.write_calls[0]["preceding_context"])
        self.assertGreater(generation_details["preceding_context_chars"], 0)
        self.assertGreater(generation_details["knowledge_wiki_context_chars"], 0)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", content_md)

    def test_generate_section_content_passes_preceding_context_to_llm_write(self) -> None:
        class _FakeKnowledgeWiki:
            def build_section_context(self, *, section, global_params):
                return "AI Wiki 编译知识（术语、结构、口径约束）\n\n禁用表述：\n- 避免“我公司”，改用“本方案 / 本系统 / 本装置”"

        class _FakeExecutor:
            def __init__(self) -> None:
                self.write_calls: list[dict] = []

            async def write_section(
                self,
                *,
                task_id,
                section,
                global_params,
                retrieved_context,
                outline_title,
                recommended_assets=None,
                reuse_pack=None,
                assembled_draft=None,
                preceding_context="",
            ):
                self.write_calls.append(
                    {
                        "task_id": task_id,
                        "preceding_context": preceding_context,
                        "retrieved_context": retrieved_context,
                        "assembled_draft": assembled_draft,
                    }
                )
                return SimpleNamespace(content="## 7 控制系统及联锁保护方案\n\n正文。")

        executor = _FakeExecutor()
        service = SectionDraftService(executor=executor, knowledge_wiki=_FakeKnowledgeWiki())

        async def _run():
            return await service._generate_section_content(
                task_id="task-002",
                section={
                    "title": "7 控制系统及联锁保护方案",
                    "purpose": "说明控制架构与联锁边界。",
                    "keywords": ["控制系统", "联锁保护"],
                    "generation_mode": "baseline",
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                retrieved_context="控制架构参考资料",
                citations=[],
                recommended_assets=[],
                reusable_blocks=[],
                reuse_pack={},
                preceding_context="前序章节已覆盖内容（请勿重复）：\n- 项目概述：已说明改造目标。\n已覆盖主题：项目概述与改造目标",
            )

        _, draft_status, _, generation_details = asyncio.run(_run())

        self.assertEqual(draft_status, "generated")
        self.assertEqual(executor.write_calls[0]["task_id"], "task-002")
        self.assertIn("已覆盖主题：项目概述与改造目标", executor.write_calls[0]["preceding_context"])
        self.assertIn("AI Wiki 编译知识", executor.write_calls[0]["preceding_context"])
        self.assertEqual(generation_details["effective_path"], "llm_write")
        self.assertGreater(generation_details["preceding_context_chars"], 0)
        self.assertGreater(generation_details["knowledge_wiki_context_chars"], 0)

    def test_generate_section_content_uses_llm_write_when_reuse_blocks_are_filtered_out(self) -> None:
        class _FakeExecutor:
            def __init__(self) -> None:
                self.write_calls: list[dict] = []

            async def write_section(
                self,
                *,
                task_id,
                section,
                global_params,
                retrieved_context,
                outline_title,
                recommended_assets=None,
                reuse_pack=None,
                assembled_draft=None,
                preceding_context="",
            ):
                self.write_calls.append(
                    {
                        "task_id": task_id,
                        "reuse_pack": reuse_pack,
                        "assembled_draft": assembled_draft,
                    }
                )
                return SimpleNamespace(content="## 4 110kV变电站综合自动化系统方案\n\n正式正文。")

        executor = _FakeExecutor()
        service = SectionDraftService(executor=executor)

        async def _run():
            return await service._generate_section_content(
                task_id="task-003",
                section={
                    "title": "4 110kV变电站综合自动化系统方案",
                    "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构、监控策略和远方通信。",
                    "keywords": ["综合自动化系统", "站控层", "间隔层", "网络层"],
                    "generation_mode": "reuse_first",
                    "section_class": "architecture",
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目", "product_line": "hv_vfd"},
                retrieved_context="",
                citations=[],
                recommended_assets=[],
                reusable_blocks=[
                    {
                        "heading_path": ["4 LCI 变频软起系统方案"],
                        "metadata": {"section_type": "vfd_spec", "equipment_type": "lci", "content_form": "narrative"},
                        "selection_score": 0.92,
                        "content_md": "磨机总启动时间69s，LCI切换内部晶闸管换相模式并完成同步切换。",
                    }
                ],
                reuse_pack={
                    "generation_mode": "reuse_first",
                    "reusable_blocks": [
                        {
                            "heading_path": ["4 LCI 变频软起系统方案"],
                            "metadata": {"section_type": "vfd_spec", "equipment_type": "lci", "content_form": "narrative"},
                            "selection_score": 0.92,
                            "content_md": "磨机总启动时间69s，LCI切换内部晶闸管换相模式并完成同步切换。",
                        }
                    ],
                },
            )

        _, draft_status, _, generation_details = asyncio.run(_run())

        self.assertEqual(draft_status, "generated")
        self.assertEqual(generation_details["effective_path"], "llm_write")
        self.assertEqual(executor.write_calls[0]["task_id"], "task-003")
        self.assertIsNone(executor.write_calls[0]["assembled_draft"])
        self.assertEqual(executor.write_calls[0]["reuse_pack"]["reusable_blocks"], [])

    def test_generate_section_content_marks_spec_without_evidence_review_required(self) -> None:
        class _FakeExecutor:
            async def write_section(self, **kwargs):
                return SimpleNamespace(content="## 7 电机技术规范\n\n同步电机额定功率4208kW。")

        service = SectionDraftService(executor=_FakeExecutor())

        async def _run():
            return await service._generate_section_content(
                task_id="task-spec-no-evidence",
                section={
                    "title": "7 电机技术规范",
                    "purpose": "说明同步电机额定功率、额定电压和绝缘等级。",
                    "generation_mode": "reuse_first",
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目", "product_line": "lci"},
                retrieved_context="",
                citations=[],
                recommended_assets=[],
                reusable_blocks=[],
                reuse_pack={"generation_mode": "reuse_first", "reusable_blocks": []},
            )

        _, draft_status, citations, generation_details = asyncio.run(_run())

        self.assertEqual(draft_status, "review_required")
        self.assertEqual(citations, [])
        self.assertEqual(generation_details["effective_path"], "llm_write")
        self.assertEqual(generation_details["review_required_reason"], "generated_without_reuse_evidence")

    def test_generate_section_content_returns_review_required_fallback_when_llm_write_fails(self) -> None:
        class _FailingExecutor:
            async def write_section(self, **kwargs):
                raise RuntimeError("relay timeout")

        service = SectionDraftService(executor=_FailingExecutor())

        async def _run():
            return await service._generate_section_content(
                task_id="task-004",
                section={
                    "title": "4 110kV变电站综合自动化系统方案",
                    "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构。",
                    "keywords": ["综合自动化系统", "站控层", "间隔层"],
                    "generation_mode": "baseline",
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                retrieved_context="",
                citations=[],
                recommended_assets=[],
                reusable_blocks=[],
                reuse_pack={},
            )

        content_md, draft_status, _, generation_details = asyncio.run(_run())

        self.assertEqual(draft_status, "review_required")
        self.assertEqual(generation_details["effective_path"], "llm_write_fallback")
        self.assertIn("relay timeout", generation_details["write_error"])
        self.assertIn("待补充技术要点", content_md)

    def test_executor_agent_uses_smaller_budget_for_plain_section_write(self) -> None:
        class _CaptureLLMClient:
            def __init__(self) -> None:
                self.requests = []

            async def invoke(self, request):
                self.requests.append(request)
                return SimpleNamespace(content="## 测试章节\n\n正文。")

        client = _CaptureLLMClient()
        agent = ExecutorAgent(llm_client=client)

        async def _run():
            await agent.write_section(
                task_id="plain-write",
                section={"title": "测试章节", "purpose": "说明测试内容。"},
                global_params={"project_name": "测试项目"},
                retrieved_context="",
                outline_title="测试方案",
            )
            await agent.write_section(
                task_id="finalize-write",
                section={"title": "测试章节", "purpose": "说明测试内容。"},
                global_params={"project_name": "测试项目"},
                retrieved_context="",
                outline_title="测试方案",
                assembled_draft="## 测试章节\n\n已有草稿。",
            )

        asyncio.run(_run())

        self.assertEqual(client.requests[0].max_tokens, 900)
        self.assertEqual(client.requests[1].max_tokens, 3200)

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
                    "asset_id": "asset_spaced_cert",
                    "asset_type": "figure",
                    "visual_role": "engineering_figure",
                    "title": "4.2 电 力 工业电气设备 质 量 检 验测试中 心检 测 报告",
                    "heading_path": "4.2 电 力 工业电气设备 质 量 检 验测试中 心检 测 报告",
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

    def test_filter_reuse_blocks_for_assembly_skips_control_narrative_for_main_circuit(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["4", "LCI 变频软起系统方案"],
                    "metadata": {"section_type": "vfd_spec", "content_form": "narrative"},
                    "selection_score": 0.76,
                    "content_md": "磨机的总启动时间为69s，包括励磁等待和同步切换时间。",
                },
                {
                    "heading_path": ["3", "高浓磨机电机控制及电机辅助设备监控系统方案"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.59,
                    "content_md": "本地控制单元 PLC 监控油站、冷却器和励磁柜信号。",
                },
                {
                    "heading_path": ["5", "变频器技术数据"],
                    "metadata": {"section_type": "vfd_spec", "content_form": "parameter_table"},
                    "selection_score": 0.47,
                    "content_md": "| 参数 | 输入变压器 | 输出变压器 |\n| --- | --- | --- |\n| 额定容量 | 5458 | 4807 |",
                },
            ],
            target_taxonomy={
                "section_type": "main_circuit_scheme",
                "equipment_type": "transformer",
                "support_content_forms": {"narrative", "parameter_table"},
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["metadata"]["content_form"], "parameter_table")

    def test_filter_reuse_blocks_for_assembly_skips_main_circuit_doc_title_noise(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["某钢铁集团高炉鼓风机电机及变频软起动系统技术协议"],
                    "metadata": {"section_type": "unknown", "content_form": "narrative"},
                    "selection_score": 1.0,
                    "content_md": (
                        "Fieldbus adapter Profibus-DP（暂定）\n"
                        "I/O Rating (for digital inputs) 24VDC\n"
                        "Cable Length between Motor and LCI 300米\n"
                        "变频器遵循的标准和证书 Converter Standard and Certification\n"
                        "Routine Test 例行测试\n"
                    ),
                },
                {
                    "heading_path": ["某钢铁集团高炉鼓风机电机及变频软起动系统技术协议"],
                    "metadata": {"section_type": "unknown", "content_form": "narrative"},
                    "selection_score": 0.98,
                    "content_md": (
                        "启动和同步过程描述 Description of Start and Sychronization\n"
                        "同步电机的启动和同步由变频器(SFC)控制。\n"
                        "在收到所有必要反馈信号后，SFC闭合进线侧与变频变压器相连的断路器(ICB)，"
                        "并按照预设加速转矩曲线将电机升速至约95%额定转速。\n"
                        "达到同步条件后，同步装置发出切换指令，LCI退出主回路，电机转入工频运行。\n"
                    ),
                },
            ],
            target_taxonomy={
                "section_type": "main_circuit_scheme",
                "equipment_type": "lci",
                "support_content_forms": {"narrative", "parameter_table"},
            },
        )

        self.assertEqual(len(filtered), 1)
        self.assertIn("启动和同步过程描述", filtered[0]["content_md"])

    def test_filter_reuse_blocks_for_assembly_prefers_parameter_blocks_for_parameter_summary(self) -> None:
        filtered = _filter_reuse_blocks_for_assembly(
            reusable_blocks=[
                {
                    "heading_path": ["5", "变频器技术数据"],
                    "metadata": {"section_type": "vfd_spec", "content_form": "narrative"},
                    "selection_score": 1.0,
                    "content_md": "变频器额定交流电流 2180A，晶闸管数量及型号 12 × 3BHB010088。",
                },
                {
                    "heading_path": ["4", "LCI 变频软起系统方案"],
                    "metadata": {"section_type": "vfd_spec", "content_form": "formula"},
                    "selection_score": 0.99,
                    "content_md": "单线图如下所示。DAYU ELECTRIC 1508 375 196 108。",
                },
                {
                    "heading_path": ["3", "高浓磨机电机控制及电机辅助设备监控系统方案"],
                    "metadata": {"section_type": "protection_interlock", "content_form": "narrative"},
                    "selection_score": 0.82,
                    "content_md": "本地控制单元 PLC 监控油站、冷却器和励磁柜信号。",
                },
                {
                    "heading_path": ["5", "变频器技术数据"],
                    "metadata": {"section_type": "vfd_spec", "content_form": "parameter_table"},
                    "selection_score": 0.9,
                    "content_md": "| 项目 | 数值 |\n| --- | --- |\n| 额定容量 | 5458 |",
                },
            ],
            target_taxonomy={
                "section_type": "vfd_spec",
                "equipment_type": "lci",
                "support_content_forms": {"narrative", "parameter_table", "bom_table", "figure"},
            },
            section={
                "title": "设备技术参数与性能指标",
                "purpose": "汇总LCI装置、变压器及相关辅助设备的主要技术参数和性能指标。",
                "parameter_sensitive": True,
                "expected_evidence_types": ["section"],
            },
        )

        self.assertEqual(
            [(item["metadata"]["section_type"], item["metadata"]["content_form"]) for item in filtered],
            [("vfd_spec", "narrative"), ("vfd_spec", "parameter_table")],
        )

    def test_normalize_invalid_asset_placeholders_keeps_valid_ids_and_rewrites_fake_refs(self) -> None:
        normalized = _normalize_invalid_asset_placeholders(
            content_md=(
                "效率待确认：[[ASSET:TABLE:5.1.2 变频器主要数据]]。\n"
                "参考图位：[[ASSET:FIGURE:asset-001]]。"
            ),
            recommended_assets=[
                {"asset_id": "asset-001"},
                {"asset_id": "asset-002"},
            ],
        )

        self.assertIn("待根据《5.1.2 变频器主要数据》进一步确认", normalized)
        self.assertIn("[[ASSET:FIGURE:asset-001]]", normalized)
        self.assertNotIn("[[ASSET:TABLE:5.1.2 变频器主要数据]]", normalized)

    def test_normalize_invalid_asset_placeholders_resolves_title_only_asset_reference(self) -> None:
        normalized = _normalize_invalid_asset_placeholders(
            content_md="具体参数详见 [[ASSET:2.3 高压变频器主要技术参数]]。",
            recommended_assets=[
                {
                    "asset_id": "asset-table",
                    "asset_type": "table",
                    "title": "2.3 高压变频器主要技术参数",
                },
            ],
        )

        self.assertIn("[[ASSET:TABLE:asset-table]]", normalized)
        self.assertNotIn("[[ASSET:2.3 高压变频器主要技术参数]]", normalized)

    def test_normalize_invalid_asset_placeholders_resolves_typed_title_reference(self) -> None:
        normalized = _normalize_invalid_asset_placeholders(
            content_md="具体参数详见 [[ASSET:TABLE:2.3 高压变频器主要技术参数]]。",
            recommended_assets=[
                {
                    "asset_id": "asset-table",
                    "asset_type": "table",
                    "metadata": {"raw_title": "2.3高压变频器主要技术参数"},
                },
            ],
        )

        self.assertIn("[[ASSET:TABLE:asset-table]]", normalized)
        self.assertNotIn("[[ASSET:TABLE:2.3 高压变频器主要技术参数]]", normalized)

    def test_normalize_invalid_asset_placeholders_rewrites_unknown_title_only_reference(self) -> None:
        normalized = _normalize_invalid_asset_placeholders(
            content_md="参考 [[ASSET:不存在的图表标题]]。",
            recommended_assets=[
                {
                    "asset_id": "asset-table",
                    "asset_type": "table",
                    "title": "2.3 高压变频器主要技术参数",
                },
            ],
        )

        self.assertIn("待根据《不存在的图表标题》进一步确认", normalized)
        self.assertNotIn("[[ASSET:不存在的图表标题]]", normalized)

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
