from __future__ import annotations

import unittest

from app.api.documents import (
    _build_chunk_contextual_text,
    _build_document_upload_message,
    _extract_document_id_from_parse_job,
    _resolve_document_parse_outcome,
    _resolve_section_anchor_from_catalog,
    _should_refresh_history_library,
)
from app.services.knowledge.library_refresh import filter_baseline_case_library_entries
from app.services.parsing.document_sources import is_library_ready_entry


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


if __name__ == "__main__":
    unittest.main()
