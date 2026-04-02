import os
import asyncio
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.config import get_settings
from app.services.retrieval.asset_service import AssetCard, _asset_anchor_boost, _asset_noise_penalty, _asset_taxonomy_boost
from app.services.retrieval.service import build_evidence_items, build_evidence_search_plan, filter_evidence_results
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


class RetrievalBuildingBlockTests(unittest.TestCase):
    def test_chunker_preserves_table_blocks(self) -> None:
        markdown = "# 标题\n\n说明文字\n\n| 设备 | 型号 |\n|---|---|\n| 变频器 | ABB |\n"
        chunks = Chunker(max_chars=80).split(markdown, base_metadata={"industry": "电气"})

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(any(chunk.chunk_type == "TABLE" for chunk in chunks))
        self.assertTrue(all("content_risk_level" in chunk.metadata for chunk in chunks))

    def test_chunker_splits_medium_tables_into_multiple_chunks(self) -> None:
        rows = "\n".join(f"| 参数{i} | 数值{i} | 补充说明{i} |" for i in range(12))
        markdown = f"# 参数表\n\n| 名称 | 值 | 说明 |\n|---|---|---|\n{rows}\n"

        chunks = Chunker(max_chars=160, max_table_rows_per_chunk=4).split(markdown, base_metadata={"industry": "电气"})

        table_chunks = [chunk for chunk in chunks if chunk.chunk_type == "TABLE"]
        self.assertGreaterEqual(len(table_chunks), 3)
        self.assertTrue(all(chunk.token_count > 0 for chunk in table_chunks))
        self.assertTrue(all(chunk.metadata["content_risk_level"] == "medium" for chunk in table_chunks))

    def test_chunker_marks_asset_lookup_for_diagram_references(self) -> None:
        markdown = "# 控制原理\n\n原理图与波形图详见附件，接线图如下。"

        chunks = Chunker().split(markdown, base_metadata={"industry": "电气"})

        self.assertTrue(any(chunk.metadata["needs_asset_lookup"] for chunk in chunks))
        self.assertTrue(any(chunk.metadata["content_risk_level"] == "medium" for chunk in chunks))
        self.assertTrue(any(chunk.metadata["content_form"] == "figure" for chunk in chunks))

    def test_build_evidence_items_keeps_raw_content_and_reusability_score(self) -> None:
        items = build_evidence_items(
            [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "第4章 > 技术架构",
                    "chunk_type": "PLAIN",
                    "content": "项目名称：旧项目A\n采用双机冗余架构。",
                    "score": 0.82,
                    "metadata": {
                        "content_risk_level": "low",
                        "front_matter": False,
                        "needs_asset_lookup": False,
                        "section_type": "overall_solution",
                        "equipment_type": "vfd",
                        "content_form": "narrative",
                    },
                }
            ]
        )
        self.assertEqual(items[0]["source_chunk_type"], "PLAIN")
        self.assertIn("双机冗余架构", items[0]["raw_content"])
        self.assertGreater(items[0]["reusability_score"], 0.8)
        self.assertEqual(items[0]["section_type"], "overall_solution")
        self.assertEqual(items[0]["equipment_type"], "vfd")

    def test_filter_evidence_results_drops_short_garbled_plain_fragment(self) -> None:
        results = filter_evidence_results(
            [
                {
                    "chunk_id": "chunk-bad",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "6 Л",
                    "chunk_type": "PLAIN",
                    "content": "# 6 Л\n\nof",
                    "score": 0.52,
                    "metadata": {},
                },
                {
                    "chunk_id": "chunk-good",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "4.2 控制接口",
                    "chunk_type": "PLAIN",
                    "content": "## 4.2 控制接口\n\nDCS 至变频器提供 DI/DO、AI/AO 以及 Modbus/RS485 通讯接口。",
                    "score": 0.61,
                    "metadata": {},
                },
            ]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "chunk-good")

    def test_filter_evidence_results_drops_numeric_table_fragment(self) -> None:
        results = filter_evidence_results(
            [
                {
                    "chunk_id": "chunk-bad-table",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "47.8 17.5",
                    "chunk_type": "TABLE",
                    "content": "| 37.5 | 17.5 | 20.0 |\n|---|---|---|\n| 47.3 | 17.5 | 29.8 |\n| 43.5 | 17.5 | 26 |",
                    "score": 0.49,
                    "metadata": {},
                },
                {
                    "chunk_id": "chunk-good-table",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "5.1 乙方提供设备清单",
                    "chunk_type": "TABLE",
                    "content": "| 编号 | 名称 | 规格 | 数量 |\n|---|---|---|---|\n| 1 | 变频柜 | GB/T-MVSG0900 | 1套 |",
                    "score": 0.77,
                    "metadata": {},
                },
            ]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "chunk-good-table")

    def test_build_evidence_search_plan_uses_global_scope_for_historical_proposals(self) -> None:
        plan = build_evidence_search_plan(
            project_id=uuid4(),
            industry="钢铁",
            doc_type="historical_proposal",
            chunk_types=["PLAIN", "TABLE"],
            scoped_document_names=["样板A.docx", "样板B.pdf"],
        )

        self.assertEqual(plan[0][0], "case_first")
        self.assertIsNone(plan[0][1])
        self.assertEqual(plan[0][2].document_names, ["样板A.docx", "样板B.pdf"])
        self.assertEqual(plan[1][0], "case_first_relaxed_industry")
        self.assertIsNone(plan[1][2].industry)
        self.assertEqual(plan[2][0], "case_first_fallback_global")

    def test_build_evidence_search_plan_adds_relaxed_global_fallback_without_case_candidates(self) -> None:
        project_id = uuid4()
        plan = build_evidence_search_plan(
            project_id=project_id,
            industry="电气",
            doc_type="rfp",
            chunk_types=["PLAIN"],
            scoped_document_names=[],
        )

        self.assertEqual(plan[0][0], "global_fallback")
        self.assertEqual(plan[0][1], project_id)
        self.assertEqual(plan[0][2].industry, "电气")
        self.assertEqual(plan[1][0], "global_fallback_relaxed_industry")
        self.assertIsNone(plan[1][2].industry)

    def test_embedder_returns_configured_dimension(self) -> None:
        embedder = Embedder()
        vector = asyncio.run(embedder.embed_text("110kV 变电站综合自动化方案"))
        self.assertEqual(len(vector), embedder.dimension)

    def test_embedder_uses_fallback_backend_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_BACKEND": "fallback",
                "EMBEDDING_DIMENSION": "16",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            embedder = Embedder()

        vector = asyncio.run(embedder.embed_text("ABB ACS880"))
        get_settings.cache_clear()

        self.assertEqual(embedder.backend_name, "fallback")
        self.assertEqual(len(vector), 16)

    def test_embedder_fails_fast_for_missing_explicit_sentence_transformers_backend(self) -> None:
        with patch("app.services.vectorstore.embedder.SentenceTransformer", None):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "sentence-transformers",
                    "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                with self.assertRaises(RuntimeError):
                    Embedder()
        get_settings.cache_clear()

    def test_asset_taxonomy_boost_prefers_main_circuit_figure(self) -> None:
        target = infer_target_taxonomy(
            {
                "title": "主回路系统方案",
                "purpose": "说明高压变频器主回路、旁路切换和一次接线方案。",
                "expected_evidence_types": ["section", "figure"],
            }
        )
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 旁路切换",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(_asset_taxonomy_boost(card=card, target_taxonomy=target), 0.2)

    def test_asset_noise_penalty_hits_certificate_like_assets(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="page_furniture",
            risk_level="low",
            usage_mode="reference_only",
            review_required=False,
            page_no=1,
            heading_path="4.2 电力工业电气设备质量检验测试中心检测报告",
            title="检测报告",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="检测报告",
            retrieval_text="检测报告 认证",
            section_type="commissioning_acceptance",
            equipment_type="generic",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(_asset_noise_penalty(card=card, target_section_type="main_circuit_scheme"), 0.3)

    def test_asset_anchor_boost_prefers_same_document_and_heading_family(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 旁路切换",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={},
        )

        boost = _asset_anchor_boost(
            card=card,
            anchor_document_names={"临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx"},
            anchor_headings=["2.2 高压变频器主回路方案说明", "2.3 高压变频器主要技术参数"],
        )

        self.assertGreater(boost, 0.3)


if __name__ == "__main__":
    unittest.main()
