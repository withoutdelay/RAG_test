from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.api.documents import (
    _apply_successful_conversion_route,
    _build_chunk_contextual_text,
    _build_document_upload_message,
    _clean_successful_parse_metadata,
    _extract_document_id_from_parse_job,
    _rfp_light_cloud_fallback_reason,
    _rfp_light_parse_metadata_for_response,
    _sanitize_rfp_cloud_metadata,
    _resolve_document_parse_outcome,
    _resolve_section_anchor_from_catalog,
    _should_refresh_history_library,
)
from app.services.knowledge.library_refresh import dedupe_case_library_entries, filter_baseline_case_library_entries
from app.services.parsing.document_sources import is_library_ready_entry
from app.services.parsing.rfp_light_parser import RfpLightParseResult


class DocumentApiHelperTests(unittest.TestCase):
    def test_extract_document_id_from_parse_job_uses_input_ref(self) -> None:
        class JobLike:
            input_ref = {"document_id": "11111111-1111-1111-1111-111111111111"}
            output_ref = {}

        self.assertEqual(
            str(_extract_document_id_from_parse_job(JobLike())),
            "11111111-1111-1111-1111-111111111111",
        )

    def test_extract_document_id_from_parse_job_uses_progress_fallback(self) -> None:
        class JobLike:
            input_ref = {}
            output_ref = {"progress": {"document_id": "22222222-2222-2222-2222-222222222222"}}

        self.assertEqual(
            str(_extract_document_id_from_parse_job(JobLike())),
            "22222222-2222-2222-2222-222222222222",
        )

    def test_resolve_section_anchor_from_catalog_matches_leaf_heading(self) -> None:
        anchor = _resolve_section_anchor_from_catalog(
            section_catalog=[
                {
                    "section_id": "3.2.4",
                    "title": "2.4 控制信号接口说明",
                    "source_heading": "2.4 控制信号接口说明",
                    "normalized_heading": "控制信号接口说明",
                    "heading_aliases": ["控制信号接口说明", "接口说明"],
                    "level": 3,
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                    "normalized_section_path": "系统及方案介绍 > 系统方案 > 控制信号接口说明",
                    "children": [],
                }
            ],
            heading_path="2.4 控制信号接口说明",
        )

        self.assertEqual(anchor["source_section_id"], "3.2.4")
        self.assertEqual(anchor["section_path"], "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明")

    def test_resolve_section_anchor_from_catalog_matches_alias_in_hierarchical_path(self) -> None:
        anchor = _resolve_section_anchor_from_catalog(
            section_catalog=[
                {
                    "section_id": "5.1.1",
                    "title": "5.1.1 变频器系统示意图",
                    "source_heading": "5.1.1 变频器系统示意图",
                    "normalized_heading": "变频器系统示意图",
                    "heading_aliases": ["变频器系统示意图", "系统示意图"],
                    "level": 2,
                    "section_path": "第五章 图纸说明 > 5.1.1 变频器系统示意图",
                    "normalized_section_path": "图纸说明 > 变频器系统示意图",
                    "children": [],
                }
            ],
            heading_path="第五章 图纸说明 > 系统示意图",
        )

        self.assertEqual(anchor["source_section_id"], "5.1.1")
        self.assertEqual(anchor["source_heading"], "5.1.1 变频器系统示意图")

    def test_resolve_section_anchor_from_catalog_matches_mixed_case_bilingual_heading(self) -> None:
        section_catalog = [
            {
                "section_id": "3",
                "title": "3. 系统方案 SYSTEM SOLUTION",
                "source_heading": "3. 系统方案 SYSTEM SOLUTION",
                "normalized_heading": "系统方案 SYSTEM SOLUTION",
                "heading_aliases": ["系统方案 SYSTEM SOLUTION"],
                "level": 2,
                "section_path": "3. 系统方案 SYSTEM SOLUTION",
                "normalized_section_path": "系统方案 SYSTEM SOLUTION",
                "children": [
                    {
                        "section_id": "3.1",
                        "title": "3.1. 变频软起系统单线图 SINGLE LINE DIAGRAM",
                        "source_heading": "3.1. 变频软起系统单线图 SINGLE LINE DIAGRAM",
                        "normalized_heading": "变频软起系统单线图 SINGLE LINE DIAGRAM",
                        "heading_aliases": [
                            "变频软起系统单线图 SINGLE LINE DIAGRAM",
                            "变频软起系统单线图SINGLELINEDIAGRAM",
                        ],
                        "level": 3,
                        "section_path": "3. 系统方案 SYSTEM SOLUTION > 3.1. 变频软起系统单线图 SINGLE LINE DIAGRAM",
                        "normalized_section_path": "系统方案 SYSTEM SOLUTION > 变频软起系统单线图 SINGLE LINE DIAGRAM",
                        "children": [],
                    }
                ],
            }
        ]

        child_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path="3.1 变频软起系统单线图 Single line Diagram",
        )
        parent_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path="3 系统方案 System Solution",
        )

        self.assertEqual(child_anchor["source_section_id"], "3.1")
        self.assertEqual(parent_anchor["source_section_id"], "3")

    def test_resolve_section_anchor_from_catalog_matches_bilingual_heading_without_spacing(self) -> None:
        section_catalog = [
            {
                "section_id": "1",
                "title": "1. 工厂设计环境 PLANT DESIGN DATA",
                "source_heading": "1. 工厂设计环境 PLANT DESIGN DATA",
                "normalized_heading": "工厂设计环境 PLANT DESIGN DATA",
                "heading_aliases": ["工厂设计环境 PLANT DESIGN DATA"],
                "level": 2,
                "section_path": "1. 工厂设计环境 PLANT DESIGN DATA",
                "normalized_section_path": "工厂设计环境 PLANT DESIGN DATA",
                "children": [
                    {
                        "section_id": "1.2",
                        "title": "1.2. 供电条件SUPPLY NETWORK",
                        "source_heading": "1.2. 供电条件SUPPLY NETWORK",
                        "normalized_heading": "供电条件SUPPLY NETWORK",
                        "heading_aliases": ["供电条件SUPPLY NETWORK"],
                        "level": 3,
                        "section_path": "1. 工厂设计环境 PLANT DESIGN DATA > 1.2. 供电条件SUPPLY NETWORK",
                        "normalized_section_path": "工厂设计环境 PLANT DESIGN DATA > 供电条件SUPPLY NETWORK",
                        "children": [],
                    }
                ],
            },
            {
                "section_id": "2",
                "title": "2. 供货范围SCOPES OF SUPPLY",
                "source_heading": "2. 供货范围SCOPES OF SUPPLY",
                "normalized_heading": "供货范围SCOPES OF SUPPLY",
                "heading_aliases": ["供货范围SCOPES OF SUPPLY"],
                "level": 2,
                "section_path": "2. 供货范围SCOPES OF SUPPLY",
                "normalized_section_path": "供货范围SCOPES OF SUPPLY",
                "children": [],
            },
        ]

        supply_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path="1.2 供电条件 Supply Network",
        )
        scope_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path="2 供货范围 Scopes of supply",
        )

        self.assertEqual(supply_anchor["source_section_id"], "1.2")
        self.assertEqual(scope_anchor["source_section_id"], "2")

    def test_resolve_section_anchor_from_catalog_returns_empty_for_unknown_heading(self) -> None:
        anchor = _resolve_section_anchor_from_catalog(
            section_catalog=[
                {
                    "section_id": "1",
                    "title": "第一章 项目概述",
                    "source_heading": "第一章 项目概述",
                    "normalized_heading": "项目概述",
                    "heading_aliases": ["项目概述"],
                    "level": 1,
                    "section_path": "第一章 项目概述",
                    "normalized_section_path": "项目概述",
                    "children": [],
                }
            ],
            heading_path="应急润滑油需求曲线",
        )

        self.assertIsNone(anchor["source_section_id"])
        self.assertIsNone(anchor["section_path"])

    def test_build_chunk_contextual_text_contains_document_section_and_taxonomy(self) -> None:
        contextual = _build_chunk_contextual_text(
            document_name="高压变频方案A.pdf",
            base_metadata={"industry": "冶金", "year": 2024},
            chunk_content="DCS 至变频器提供 DI/DO、AI/AO 以及通讯接口。",
            chunk_type="PLAIN",
            heading_path="4.2 控制接口说明",
            section_anchor={
                "source_section_id": "4.2",
                "section_path": "第四章 系统方案 > 4.2 控制接口说明",
                "source_heading": "4.2 控制接口说明",
            },
            chunk_metadata={
                "section_type": "communication_interface",
                "equipment_type": "dcs_plc_interface",
                "content_form": "narrative",
            },
        )

        self.assertIn("高压变频方案A.pdf", contextual["contextual_text"])
        self.assertIn("第四章 系统方案 > 4.2 控制接口说明", contextual["contextual_text"])
        self.assertIn("communication_interface", contextual["semantic_retrieval_text"])
        self.assertIn("DCS 至变频器提供", contextual["contextualized_block_text"])

    def test_should_refresh_history_library_only_for_historical_proposals(self) -> None:
        self.assertTrue(_should_refresh_history_library(doc_type="historical_proposal"))
        self.assertFalse(_should_refresh_history_library(doc_type="rfp"))

    def test_resolve_document_parse_outcome_blocks_historical_fallback_binary_parser(self) -> None:
        outcome = _resolve_document_parse_outcome(
            doc_type="historical_proposal",
            parsed_metadata={
                "format": "pdf",
                "parser_backend_used": "fallback",
                "parse_gate_status": "insufficient",
                "parse_gate_reason": "fallback_binary_parser",
            },
        )

        self.assertEqual(outcome["parse_status"], "parse_insufficient")
        self.assertFalse(outcome["history_library_eligible"])
        self.assertEqual(outcome["parse_gate_reason"], "fallback_binary_parser")

    def test_resolve_document_parse_outcome_keeps_markdown_history_document_done(self) -> None:
        outcome = _resolve_document_parse_outcome(
            doc_type="historical_proposal",
            parsed_metadata={
                "format": "md",
                "parser_backend_used": "fallback",
                "parse_gate_status": "ready",
            },
        )

        self.assertEqual(outcome["parse_status"], "done")
        self.assertTrue(outcome["history_library_eligible"])

    def test_apply_successful_conversion_route_moves_to_review_pending(self) -> None:
        document = SimpleNamespace(project_id=None, doc_type="legacy_conversion")

        metadata = _apply_successful_conversion_route(
            document=document,  # type: ignore[arg-type]
            metadata={"material_route": "conversion_required", "library_track": "pilot_main"},
        )

        self.assertEqual(document.doc_type, "historical_review")
        self.assertEqual(metadata["material_route"], "review_pending")
        self.assertEqual(metadata["library_track"], "review_pending")
        self.assertTrue(metadata["auto_route_after_conversion"])
        self.assertEqual(metadata["previous_material_route"], "conversion_required")

    def test_clean_successful_parse_metadata_clears_stale_failure_flags(self) -> None:
        cleaned = _clean_successful_parse_metadata(
            metadata={
                "parse_error": "old failure",
                "requires_cloud_parse": True,
                "parse_gate_status": "insufficient",
                "parse_gate_reason": "cloud_parse_required",
                "asset_enrichment_deferred": True,
            },
            parse_status="done",
            figure_asset_count=3,
        )

        self.assertNotIn("parse_error", cleaned)
        self.assertNotIn("asset_enrichment_deferred", cleaned)
        self.assertFalse(cleaned["requires_cloud_parse"])
        self.assertEqual(cleaned["parse_gate_status"], "ready")
        self.assertIsNone(cleaned["parse_gate_reason"])

    def test_rfp_light_cloud_fallback_reason_detects_scanned_pdf_density(self) -> None:
        settings = SimpleNamespace(
            rfp_light_parse_cloud_fallback_enabled=True,
            rfp_light_parse_cloud_fallback_min_chars=500,
            rfp_light_parse_cloud_fallback_min_chars_per_page=30,
        )
        result = RfpLightParseResult(
            text="少量文字",
            excerpt="少量文字",
            page_count=8,
            char_count=4,
            source_format="pdf",
        )

        reason = _rfp_light_cloud_fallback_reason(result=result, settings=settings)

        self.assertIsNotNone(reason)
        self.assertIn("low_pdf_text_chars", reason or "")

    def test_rfp_light_cloud_fallback_reason_skips_normal_text_pdf(self) -> None:
        settings = SimpleNamespace(
            rfp_light_parse_cloud_fallback_enabled=True,
            rfp_light_parse_cloud_fallback_min_chars=500,
            rfp_light_parse_cloud_fallback_min_chars_per_page=30,
        )
        result = RfpLightParseResult(
            text="需求" * 1000,
            excerpt="需求" * 20,
            page_count=5,
            char_count=2000,
            source_format="pdf",
        )

        self.assertIsNone(_rfp_light_cloud_fallback_reason(result=result, settings=settings))

    def test_sanitize_rfp_cloud_metadata_drops_large_docmind_payloads(self) -> None:
        sanitized = _sanitize_rfp_cloud_metadata(
            {
                "parser": "aliyun-docmind",
                "docmind_job_id": "job-1",
                "docmind_status": {"large": True},
                "docmind_result": {"large": True},
                "docmind_endpoint": "docmind-api.cn-hangzhou.aliyuncs.com",
            }
        )

        self.assertEqual(sanitized["parser"], "aliyun-docmind")
        self.assertEqual(sanitized["docmind_job_id"], "job-1")
        self.assertNotIn("docmind_status", sanitized)
        self.assertNotIn("docmind_result", sanitized)

    def test_rfp_light_parse_metadata_for_response_keeps_cloud_diagnostics_only(self) -> None:
        result = RfpLightParseResult(
            text="需求",
            excerpt="需求",
            char_count=2,
            source_format="aliyun_docmind",
            metadata={
                "cloud_fallback_used": True,
                "cloud_docmind_job_id": "job-1",
                "subprocess_extras": {"ignored": True},
            },
        )

        response_metadata = _rfp_light_parse_metadata_for_response(result)

        self.assertTrue(response_metadata["rfp_light_parse_cloud_fallback_used"])
        self.assertEqual(response_metadata["rfp_light_parse_cloud_docmind_job_id"], "job-1")
        self.assertNotIn("rfp_light_parse_subprocess_extras", response_metadata)

    def test_build_document_upload_message_explains_parse_insufficient_history_document(self) -> None:
        message = _build_document_upload_message(
            doc_type="historical_proposal",
            parse_status="parse_insufficient",
        )

        self.assertIn("解析质量不足", message)
        self.assertIn("AI Wiki", message)

    def test_is_library_ready_entry_rejects_parse_insufficient_documents(self) -> None:
        self.assertFalse(
            is_library_ready_entry(
                {
                    "parse_gate_status": "insufficient",
                    "ingestion_recommendation": "main_vector_ready",
                }
            )
        )

    def test_filter_baseline_case_library_entries_drops_previous_uploaded_entries(self) -> None:
        entries = [
            {"sample_id": "sample-a", "source": "manifest"},
            {"sample_id": "uploaded-1", "source": "uploaded_documents"},
            {"sample_id": "sample-b"},
        ]

        filtered = filter_baseline_case_library_entries(entries)

        self.assertEqual([item["sample_id"] for item in filtered], ["sample-a", "sample-b"])

    def test_dedupe_case_library_entries_prefers_source_marked_uploaded_entries(self) -> None:
        entries = [
            {"sample_id": "uploaded-1", "file_name": "demo.docx", "library_track": "pilot_main"},
            {
                "sample_id": "uploaded-1",
                "file_name": "demo.docx",
                "library_track": "pilot_main",
                "source": "uploaded_documents",
            },
            {"sample_id": "sample-a", "file_name": "base.docx", "library_track": "pilot_main"},
        ]

        deduped = dedupe_case_library_entries(entries, kind="outline")

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["source"], "uploaded_documents")


if __name__ == "__main__":
    unittest.main()
