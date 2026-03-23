import os
import asyncio
import unittest
from unittest.mock import patch

from app.config import get_settings
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder


class RetrievalBuildingBlockTests(unittest.TestCase):
    def test_chunker_preserves_table_blocks(self) -> None:
        markdown = "# 标题\n\n说明文字\n\n| 设备 | 型号 |\n|---|---|\n| 变频器 | ABB |\n"
        chunks = Chunker(max_chars=80).split(markdown, base_metadata={"industry": "电气"})

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(any(chunk.chunk_type == "TABLE" for chunk in chunks))

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
