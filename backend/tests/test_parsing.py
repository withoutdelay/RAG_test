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

    def test_parser_service_can_skip_asset_enrichment_for_refresh_backfill(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write("# 示例文档\n\n## 控制接口\n\n系统提供 DCS / PLC 控制接口，并包含 AI/AO、DI/DO 和联锁反馈。\n")
            path = Path(handle.name)

        class FailingReviewService:
            async def review_assets(self, assets):
                raise AssertionError("review_assets should not be called")

        class FailingSummaryService:
            async def summarize_assets(self, assets):
                raise AssertionError("summarize_assets should not be called")

        try:
            parser = ParserService()
            parser.asset_review_service = FailingReviewService()
            parser.asset_summary_service = FailingSummaryService()
            parsed = asyncio.run(parser.parse_document(str(path), include_asset_enrichment=False))
        finally:
            path.unlink(missing_ok=True)

        self.assertIn("# 示例文档", parsed.markdown)
        self.assertEqual(parsed.metadata["asset_llm_reviewed_count"], 0)
        self.assertEqual(parsed.metadata["asset_llm_summarized_count"], 0)

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
            with patch("app.services.parsing.docling_parser.DocumentConverter", None):
                parsed = asyncio.run(DoclingParser().parse(str(path)))
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(parsed.metadata["parser_backend_used"], "legacy_doc_placeholder")
        self.assertEqual(parsed.metadata["parse_warning"], "legacy_doc_requires_conversion")
        self.assertEqual(parsed.metadata["format"], "doc")
        self.assertIn("could not be converted automatically", parsed.markdown)

    def test_legacy_doc_auto_converts_to_pdf_when_libreoffice_is_available(self) -> None:
        class FakeDocument:
            def iterate_items(self):
                return []

            def export_to_markdown(self) -> str:
                return "# 转换后的 DOC\n\n自动转换正文。"

        class FakeResult:
            document = FakeDocument()

        class FakeConverter:
            def convert(self, path: str):
                self.converted_path = path
                return FakeResult()

        with tempfile.NamedTemporaryFile("wb", suffix=".doc", delete=False) as handle:
            handle.write(b"\xd0\xcf\x11\xe0legacy-doc")
            doc_path = Path(handle.name)
        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
            handle.write(b"%PDF-1.4")
            pdf_path = Path(handle.name)

        try:
            with patch.dict(os.environ, {"DOCLING_LIBREOFFICE_CMD": "/bin/sh"}, clear=False):
                get_settings.cache_clear()
                parser = DoclingParser()
                get_settings.cache_clear()
            fake_converter = FakeConverter()
            with (
                patch("app.services.parsing.docling_parser.DocumentConverter", object),
                patch.object(parser, "_convert_office_document_to_pdf", return_value=pdf_path),
                patch.object(parser, "_build_converter", return_value=fake_converter),
            ):
                parsed = asyncio.run(parser.parse(str(doc_path), include_assets=False))
        finally:
            doc_path.unlink(missing_ok=True)
            pdf_path.unlink(missing_ok=True)

        self.assertEqual(parsed.metadata["parser_backend_used"], "docling")
        self.assertEqual(parsed.metadata["format"], "pdf")
        self.assertEqual(parsed.metadata["original_format"], "doc")
        self.assertEqual(parsed.metadata["docling_office_conversion_source_format"], "doc")
        self.assertEqual(parsed.metadata["docling_office_conversion_target_format"], "pdf")
        self.assertIn("转换后的 DOC", parsed.markdown)

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

    def test_docling_parser_structure_hints_include_section_catalog(self) -> None:
        parser = DoclingParser()

        class FakeHeading:
            def __init__(self, text: str, page_no: int, *, source_ref: str = "") -> None:
                self.text = text
                self.prov = [SimpleNamespace(page_no=page_no, bbox=None)]
                self.self_ref = source_ref

        items = [
            (FakeHeading("第三章 系统及方案介绍 9", 9, source_ref="h1"), 1),
            (FakeHeading("二、系统方案", 14, source_ref="h2"), 2),
            (FakeHeading("2.1 高压变频器选型", 14, source_ref="h3"), 3),
        ]

        with patch("app.services.parsing.docling_parser.DOC_LING_HEADING_TYPES", (FakeHeading,)):
            structure = parser._extract_structure_hints(
                items,
                markdown="# 文档标题\n\n正文被解析成普通段落，没有稳定 markdown heading。",
            )

        self.assertEqual(len(structure["heading_hints"]), 3)
        self.assertEqual(structure["document_title"], "文档标题")
        self.assertEqual(structure["section_catalog"][0]["title"], "第三章 系统及方案介绍")
        self.assertEqual(structure["section_catalog"][0]["children"][0]["title"], "二、系统方案")
        self.assertEqual(structure["section_catalog"][0]["children"][0]["children"][0]["title"], "2.1 高压变频器选型")
        self.assertIn("parser_heading", structure["section_catalog"][0]["source_signals"])


if __name__ == "__main__":
    unittest.main()
