import os
import asyncio
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from app.config import get_settings
from app.services.parsing.parser import ParserService
from app.services.parsing.docling_parser import DoclingParser, ParsedAsset
from app.services.parsing.docling_runtime import configure_docling_runtime


class ParsingTests(unittest.TestCase):
    def test_configure_docling_runtime_applies_mirror_and_artifacts_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HF_ENDPOINT": "https://hf-mirror.example",
                "HF_HOME": "/tmp/hf-home",
                "DOCLING_CACHE_DIR": "/tmp/docling-cache",
                "DOCLING_ARTIFACTS_PATH": "/tmp/docling-models",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            settings = get_settings()
            configure_docling_runtime(settings)
            self.assertEqual(os.environ["HF_ENDPOINT"], "https://hf-mirror.example")
            self.assertEqual(os.environ["DOCLING_ARTIFACTS_PATH"], "/tmp/docling-models")
            get_settings.cache_clear()

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

    def test_legacy_doc_auto_converts_to_docx_when_libreoffice_is_available(self) -> None:
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
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            handle.write(b"docx-placeholder")
            docx_path = Path(handle.name)

        try:
            with patch.dict(os.environ, {"DOCLING_LIBREOFFICE_CMD": "/bin/sh"}, clear=False):
                get_settings.cache_clear()
                parser = DoclingParser()
                get_settings.cache_clear()
            fake_converter = FakeConverter()
            with (
                patch("app.services.parsing.docling_parser.DocumentConverter", object),
                patch.object(parser, "_convert_legacy_doc_to_docx", return_value=docx_path),
                patch.object(parser, "_build_converter", return_value=fake_converter),
            ):
                parsed = asyncio.run(parser.parse(str(doc_path), include_assets=False))
        finally:
            doc_path.unlink(missing_ok=True)
            docx_path.unlink(missing_ok=True)

        self.assertEqual(parsed.metadata["parser_backend_used"], "docling")
        self.assertEqual(parsed.metadata["format"], "docx")
        self.assertEqual(parsed.metadata["original_format"], "doc")
        self.assertEqual(parsed.metadata["docling_office_conversion"], "libreoffice_docx")
        self.assertEqual(parsed.metadata["docling_office_conversion_source_format"], "doc")
        self.assertEqual(parsed.metadata["docling_office_conversion_target_format"], "docx")
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

    def test_docx_media_repair_restores_missing_raster_figure(self) -> None:
        try:
            from PIL import Image
        except Exception:
            self.skipTest("pillow required")

        image_handle = BytesIO()
        Image.new("RGB", (12, 8), color="white").save(image_handle, format="PNG")
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/media/image1.png", image_handle.getvalue())
            assets = [
                ParsedAsset(
                    asset_type="figure",
                    page_no=None,
                    title="主回路图",
                    caption=None,
                    heading_path="3.2 主回路图",
                    context_before=None,
                    context_after=None,
                    bbox=None,
                    source_ref="fig-1",
                    image_bytes=None,
                    meta={"visual_role": "engineering_figure"},
                )
            ]

            repaired, metadata = DoclingParser()._repair_missing_figure_images(
                assets,
                source_path=path,
                effective_path=path,
            )
        finally:
            path.unlink(missing_ok=True)

        self.assertTrue(repaired[0].image_bytes)
        self.assertEqual(repaired[0].image_ext, ".png")
        self.assertEqual(repaired[0].meta["asset_repair_method"], "docx_embedded_raster_media")
        self.assertEqual(metadata["asset_repair_success_count"], 1)
        self.assertEqual(metadata["asset_repair_raster_media_success_count"], 1)

    def test_docx_composite_group_repair_adds_parent_asset_and_suppresses_child(self) -> None:
        try:
            from PIL import Image
        except Exception:
            self.skipTest("pillow required")

        image_handle = BytesIO()
        Image.new("RGBA", (20, 80), color=(255, 0, 0, 255)).save(image_handle, format="PNG")
        child_image_bytes = image_handle.getvalue()
        document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
  xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
  xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
  xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
  <w:body>
    <w:p><w:r><w:t>3.1 变频软起系统单线图 Single line Diagram</w:t></w:r></w:p>
    <w:p>
      <w:r>
        <w:drawing>
          <wp:anchor>
            <wp:extent cx="1905000" cy="952500"/>
            <wp:docPr id="37" name="组合 23"/>
            <a:graphic>
              <a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">
                <wpg:wgp>
                  <wpg:cNvGrpSpPr/>
                  <wpg:grpSpPr>
                    <a:xfrm>
                      <a:off x="0" y="0"/>
                      <a:ext cx="1905000" cy="952500"/>
                      <a:chOff x="0" y="0"/>
                      <a:chExt cx="2000" cy="1000"/>
                    </a:xfrm>
                  </wpg:grpSpPr>
                  <wps:wsp>
                    <wps:cNvPr id="2" name="矩形 1"/>
                    <wps:spPr>
                      <a:xfrm><a:off x="0" y="0"/><a:ext cx="2000" cy="1000"/></a:xfrm>
                      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                      <a:noFill/>
                      <a:ln w="9525"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln>
                    </wps:spPr>
                  </wps:wsp>
                  <pic:pic>
                    <pic:nvPicPr><pic:cNvPr id="3" name="图片 25"/><pic:cNvPicPr/></pic:nvPicPr>
                    <pic:blipFill><a:blip r:embed="rId7"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
                    <pic:spPr>
                      <a:xfrm><a:off x="800" y="100"/><a:ext cx="300" cy="800"/></a:xfrm>
                      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                    </pic:spPr>
                  </pic:pic>
                </wpg:wgp>
              </a:graphicData>
            </a:graphic>
          </wp:anchor>
        </w:drawing>
      </w:r>
    </w:p>
  </w:body>
</w:document>
"""
        rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image3.png"/>
</Relationships>
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document_xml)
                archive.writestr("word/_rels/document.xml.rels", rels_xml)
                archive.writestr("word/media/image3.png", child_image_bytes)
            child_asset = ParsedAsset(
                asset_type="figure",
                page_no=None,
                title="3.1 变频软起系统单线图 Single line Diagram",
                caption=None,
                heading_path="3.1 变频软起系统单线图 Single line Diagram",
                context_before=None,
                context_after=None,
                bbox=None,
                source_ref="#/pictures/2",
                image_bytes=child_image_bytes,
                meta={"width": 20, "height": 80, "visual_role": "engineering_figure"},
            )

            repaired, metadata = DoclingParser()._repair_docx_composite_figures(
                [child_asset],
                source_path=path,
                effective_path=path,
            )
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(metadata["docx_composite_group_count"], 1)
        self.assertEqual(metadata["docx_composite_export_success_count"], 1)
        self.assertEqual(metadata["docx_composite_child_suppressed_count"], 1)
        self.assertEqual(len(repaired), 2)
        self.assertEqual(repaired[0].meta["visual_role"], "asset_fragment")
        self.assertFalse(repaired[0].meta["preserve_in_vector_db"])
        self.assertTrue(repaired[0].meta["composite_child_asset"])
        self.assertEqual(repaired[1].meta["asset_repair_method"], "docx_ooxml_composite_group")
        self.assertTrue(repaired[1].meta["composite_figure"])
        with Image.open(BytesIO(repaired[1].image_bytes or b"")) as composite_image:
            self.assertGreater(composite_image.width, 100)
            self.assertGreater(composite_image.height, 60)

    def test_vector_media_repair_falls_back_to_pdf_page_candidate(self) -> None:
        try:
            from PIL import Image
        except Exception:
            self.skipTest("pillow required")

        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
            pdf_path = Path(handle.name)
            handle.write(b"%PDF-placeholder")
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/media/image1.wmf", b"wmf-placeholder")
            assets = [
                ParsedAsset(
                    asset_type="figure",
                    page_no=None,
                    title="水阻柜原理图",
                    caption=None,
                    heading_path="2.1 水阻柜原理图",
                    context_before=None,
                    context_after=None,
                    bbox=None,
                    source_ref="fig-1",
                    image_bytes=None,
                    meta={"visual_role": "engineering_figure"},
                )
            ]
            parser = DoclingParser()
            with (
                patch.object(parser, "_extract_office_html_media_items", return_value=[]),
                patch.object(parser, "_convert_office_document_to_pdf", return_value=pdf_path),
                patch.object(parser, "_get_pdf_page_count", return_value=1),
                patch.object(parser, "_render_pdf_page_candidate", return_value=Image.new("RGB", (120, 80), color="white")),
            ):
                repaired, metadata = parser._repair_missing_figure_images(
                    assets,
                    source_path=path,
                    effective_path=path,
                )
        finally:
            path.unlink(missing_ok=True)
            pdf_path.unlink(missing_ok=True)

        self.assertTrue(repaired[0].image_bytes)
        self.assertEqual(repaired[0].meta["asset_repair_method"], "pdf_page_render_candidate")
        self.assertEqual(repaired[0].meta["asset_repair_precision"], "page_candidate")
        self.assertIn("pdf_page_render_candidate_requires_review", repaired[0].meta["quality_flags"])
        self.assertEqual(metadata["asset_repair_vector_media_count"], 1)
        self.assertEqual(metadata["asset_repair_pdf_render_success_count"], 1)

    def test_vector_media_repair_prefers_office_html_export_before_pdf_page_candidate(self) -> None:
        try:
            from PIL import Image
        except Exception:
            self.skipTest("pillow required")

        image_handle = BytesIO()
        html_export = Image.new("RGB", (120, 80), color=(255, 0, 255))
        for x in range(20, 100):
            html_export.putpixel((x, 20), (0, 0, 0))
            html_export.putpixel((x, 60), (0, 0, 0))
        for y in range(20, 61):
            html_export.putpixel((20, y), (0, 0, 0))
            html_export.putpixel((99, y), (0, 0, 0))
        html_export.save(image_handle, format="GIF")
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/media/image1.wmf", b"emf-with-wmf-extension")
            assets = [
                ParsedAsset(
                    asset_type="figure",
                    page_no=None,
                    title="水阻柜一次方案",
                    caption=None,
                    heading_path="2.1 一次方案",
                    context_before=None,
                    context_after=None,
                    bbox=None,
                    source_ref="fig-1",
                    image_bytes=None,
                    meta={"visual_role": "engineering_figure"},
                )
            ]
            parser = DoclingParser()
            with (
                patch.object(
                    parser,
                    "_extract_office_html_media_items",
                    return_value=[
                        {
                            "name": "exported.gif",
                            "extension": ".gif",
                            "bytes": image_handle.getvalue(),
                        }
                    ],
                ),
                patch.object(parser, "_convert_office_document_to_pdf") as pdf_mock,
            ):
                repaired, metadata = parser._repair_missing_figure_images(
                    assets,
                    source_path=path,
                    effective_path=path,
                )
        finally:
            path.unlink(missing_ok=True)

        self.assertTrue(repaired[0].image_bytes)
        self.assertEqual(repaired[0].image_ext, ".png")
        self.assertEqual(repaired[0].meta["asset_repair_method"], "libreoffice_html_media")
        self.assertEqual(repaired[0].meta["asset_repair_precision"], "embedded_media_export")
        self.assertEqual(metadata["asset_repair_html_media_success_count"], 1)
        self.assertEqual(metadata["asset_repair_success_count"], 1)
        with Image.open(BytesIO(repaired[0].image_bytes or b"")) as repaired_image:
            red, green, blue, alpha = repaired_image.convert("RGBA").getpixel((1, 1))
            self.assertEqual((red, green, blue, alpha), (255, 255, 255, 0))
        pdf_mock.assert_not_called()

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
