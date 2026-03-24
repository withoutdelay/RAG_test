import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import get_settings
from app.services.parsing.parser import ParserService
from app.services.parsing.docling_parser import DoclingParser


class ParsingTests(unittest.TestCase):
    def test_parser_service_extracts_basic_metadata(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write("# 示例文档\n\n| 列1 | 列2 |\n|---|---|\n| A | B |\n")
            path = Path(handle.name)

        try:
            parsed = asyncio.run(ParserService().parse_document(str(path)))
        finally:
            path.unlink(missing_ok=True)

        self.assertIn("# 示例文档", parsed.markdown)
        self.assertEqual(parsed.metadata["table_count"], 1)
        self.assertEqual(parsed.metadata["image_count"], 0)
        self.assertEqual(parsed.metadata["document_profile"]["name"], "text_digital")
        self.assertEqual(parsed.metadata["ingestion_recommendation"], "main_vector_ready")
        self.assertEqual(parsed.metadata["high_risk_content_flags"], [])

    def test_docling_parser_respects_fallback_backend(self) -> None:
        with patch.dict(os.environ, {"PARSER_BACKEND": "fallback"}, clear=False):
            get_settings.cache_clear()
            parser = DoclingParser()

        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
            handle.write(b"fallback parser body")
            path = Path(handle.name)

        try:
            parsed = asyncio.run(parser.parse(str(path)))
        finally:
            path.unlink(missing_ok=True)
            get_settings.cache_clear()

        self.assertEqual(parsed.metadata["parser_backend_requested"], "fallback")
        self.assertEqual(parsed.metadata["parser_backend_used"], "fallback")


if __name__ == "__main__":
    unittest.main()
