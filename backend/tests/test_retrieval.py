import os
import asyncio
import unittest
from unittest.mock import patch

from app.config import get_settings
from app.services.retrieval.service import build_evidence_items
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder


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
                    "metadata": {"content_risk_level": "low", "front_matter": False, "needs_asset_lookup": False},
                }
            ]
        )
        self.assertEqual(items[0]["source_chunk_type"], "PLAIN")
        self.assertIn("双机冗余架构", items[0]["raw_content"])
        self.assertGreater(items[0]["reusability_score"], 0.8)

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


if __name__ == "__main__":
    unittest.main()
