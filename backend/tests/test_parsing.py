import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from app.config import get_settings
from app.services.parsing.parser import ParserService
from app.services.parsing.docling_parser import DoclingParser
from app.services.parsing.document_sources import resolve_library_source_path


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

    def test_docling_parser_extracts_docx_text_in_fallback_mode(self) -> None:
        with patch.dict(os.environ, {"PARSER_BACKEND": "fallback"}, clear=False):
            get_settings.cache_clear()
            parser = DoclingParser()

        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)

        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                      <w:body>
                        <w:p><w:r><w:t>项目概述</w:t></w:r></w:p>
                        <w:p><w:r><w:t>支持10kV同步电机软起动，并保留DCS联锁接口。</w:t></w:r></w:p>
                      </w:body>
                    </w:document>
                    """,
                )
            parsed = asyncio.run(parser.parse(str(path)))
        finally:
            path.unlink(missing_ok=True)
            get_settings.cache_clear()

        self.assertIn("支持10kV同步电机软起动", parsed.markdown)
        self.assertNotIn("PK\x03\x04", parsed.markdown)
        self.assertEqual(parsed.metadata["parser_backend_requested"], "fallback")
        self.assertIn(parsed.metadata["parser_backend_used"], {"fallback_textutil", "fallback_docx_xml"})

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

    def test_pdf_fallback_returns_placeholder_without_binary_dump(self) -> None:
        with patch.dict(os.environ, {"PARSER_BACKEND": "fallback"}, clear=False):
            get_settings.cache_clear()
            parser = DoclingParser()

        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
            handle.write(b"%PDF-1.7\x00binary\x00content")
            path = Path(handle.name)

        try:
            parsed = asyncio.run(parser.parse(str(path)))
        finally:
            path.unlink(missing_ok=True)
            get_settings.cache_clear()

        self.assertEqual(parsed.metadata["parser_backend_requested"], "fallback")
        self.assertEqual(parsed.metadata["parser_backend_used"], "fallback_placeholder")
        self.assertTrue(parsed.metadata["parser_placeholder"])
        self.assertNotIn("\x00", parsed.markdown)
        self.assertNotIn("%PDF-1.7", parsed.markdown)

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

    def test_resolve_library_source_path_prefers_extracted_text(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as extracted:
            extracted.write("提取正文")
            extracted_path = Path(extracted.name)
        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as original:
            original.write(b"%PDF-1.7")
            original_path = Path(original.name)

        try:
            path, source_kind = resolve_library_source_path(
                {
                    "file_path": str(original_path),
                    "details": {"extracted_text_path": str(extracted_path)},
                }
            )
        finally:
            extracted_path.unlink(missing_ok=True)
            original_path.unlink(missing_ok=True)

        self.assertEqual(path, extracted_path.resolve())
        self.assertEqual(source_kind, "extracted_text")

    def test_resolve_library_source_path_prefers_converted_asset_for_legacy_doc(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as converted:
            converted.write(b"placeholder")
            converted_path = Path(converted.name)
        with tempfile.NamedTemporaryFile("wb", suffix=".doc", delete=False) as original:
            original.write(b"\xd0\xcf\x11\xe0legacy-doc")
            original_path = Path(original.name)

        try:
            path, source_kind = resolve_library_source_path(
                {
                    "file_path": str(original_path),
                    "details": {"converted_asset_path": str(converted_path)},
                }
            )
        finally:
            converted_path.unlink(missing_ok=True)
            original_path.unlink(missing_ok=True)

        self.assertEqual(path, converted_path.resolve())
        self.assertEqual(source_kind, "converted_asset")

    def test_docling_parser_marks_footer_banner_as_page_furniture_even_with_figure_context(self) -> None:
        parser = DoclingParser()

        role = parser._classify_visual_role(
            asset_type="figure",
            heading_path="变压器一次侧和二次侧绕组间屏蔽层",
            caption=None,
            context_before="变压器一次侧和二次侧绕组间屏蔽层 | 为实现一次侧和二次侧绕组的解耦，接地屏蔽层如下图所示。",
            context_after="HV: 高压侧正弦波电压",
            bbox=SimpleNamespace(l=57.29, r=134.47, b=20.51, t=45.55),
            page_size=(595.32, 841.92),
            image_size=(154, 50),
        )

        self.assertEqual(role, "page_furniture")


if __name__ == "__main__":
    unittest.main()
