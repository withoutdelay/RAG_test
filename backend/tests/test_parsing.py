import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

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
        self.assertIn("docling_libreoffice_available", parsed.metadata)
        self.assertIsInstance(parsed.metadata["docling_libreoffice_available"], bool)

    def test_legacy_doc_returns_conversion_placeholder(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".doc", delete=False) as handle:
            handle.write(b"\xd0\xcf\x11\xe0legacy-doc")
            path = Path(handle.name)

        try:
            parsed = asyncio.run(DoclingParser().parse(str(path)))
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(parsed.metadata["parser_backend_used"], "legacy_doc_placeholder")
        self.assertEqual(parsed.metadata["parse_warning"], "legacy_doc_requires_conversion")
        self.assertEqual(parsed.metadata["format"], "doc")
        self.assertIn("Please convert this file to DOCX or PDF", parsed.markdown)

    def test_docling_parser_detects_configured_libreoffice_binary(self) -> None:
        with patch.dict(os.environ, {"DOCLING_LIBREOFFICE_CMD": "/bin/sh"}, clear=False):
            get_settings.cache_clear()
            parser = DoclingParser()
            get_settings.cache_clear()

        self.assertEqual(parser.resolved_libreoffice_cmd, "/bin/sh")

    def test_docx_with_vector_media_prefers_pdf_conversion(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/media/image1.wmf", b"wmf-placeholder")
            with patch.dict(os.environ, {"DOCLING_LIBREOFFICE_CMD": "/bin/sh"}, clear=False):
                get_settings.cache_clear()
                parser = DoclingParser()
                get_settings.cache_clear()
            self.assertTrue(parser._docx_prefers_pdf_conversion(path))
        finally:
            path.unlink(missing_ok=True)

    def test_plain_docx_without_vector_media_does_not_prefer_pdf_conversion(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/media/image1.png", b"png-placeholder")
            with patch.dict(os.environ, {"DOCLING_LIBREOFFICE_CMD": "/bin/sh"}, clear=False):
                get_settings.cache_clear()
                parser = DoclingParser()
                get_settings.cache_clear()
            self.assertFalse(parser._docx_prefers_pdf_conversion(path))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
