"""Tests for :mod:`app.services.parsing.rfp_light_parser`.

Phase 2 / Task 2.1 of `.super-dev/changes/rfp-light-parse-20260511/tasks.md`.

These tests confirm the lightweight RFP parser:

- handles ``.txt`` / ``.md`` / ``.docx`` / ``.pdf`` inputs with UTF-8 + GBK fallback;
- raises ``RfpLightParseInsufficient`` for legacy ``.doc``, unsupported formats,
  empty files, and timeouts;
- respects the ``RFP_LIGHT_PARSE_MAX_PAGES`` / ``MAX_CHARS`` / ``MAX_SECONDS`` caps;
- never calls the heavy ingestion pipeline (Chunker / Embedder / Qdrant /
  ``request_case_library_refresh``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from app.config import get_settings
from app.services.parsing.rfp_light_parser import (
    ALL_SUPPORTED_SUFFIXES,
    LEGACY_DOC_SUFFIXES,
    RfpLightParseError,
    RfpLightParseInsufficient,
    RfpLightParseResult,
    RfpLightParser,
)


class _ParserSettings:
    """Minimal duck-typed Settings stand-in so tests don't depend on env state."""

    def __init__(
        self,
        *,
        max_seconds: float = 60.0,
        max_pages: int = 30,
        max_chars: int = 80_000,
        excerpt_chars: int = 20_000,
        max_workers: int = 2,
    ) -> None:
        self.rfp_light_parse_max_seconds = max_seconds
        self.rfp_light_parse_max_pages = max_pages
        self.rfp_light_parse_max_chars = max_chars
        self.rfp_light_parse_excerpt_chars = excerpt_chars
        self.rfp_light_parse_max_workers = max_workers


def _run(coro):
    return asyncio.run(coro)


class RfpLightParserBasicTests(TestCase):
    def setUp(self) -> None:
        self.parser = RfpLightParser(settings=_ParserSettings())  # type: ignore[arg-type]

    # -- txt / md ----------------------------------------------------------------

    def test_parse_utf8_text_returns_full_text_and_excerpt(self) -> None:
        path = Path(self._make_tmpfile(".md", "# 标题\n\n需求一：UTF-8 中文文本\n", encoding="utf-8"))
        result = _run(self.parser.parse(path))
        self.assertIsInstance(result, RfpLightParseResult)
        self.assertEqual(result.source_format, "text")
        self.assertIn("需求一", result.text)
        self.assertEqual(result.excerpt, result.text)  # short enough to fit
        self.assertFalse(result.truncated_chars)
        self.assertFalse(result.truncated_pages)
        self.assertEqual(result.page_count, 0)
        self.assertGreater(result.char_count, 0)

    def test_parse_gbk_text_falls_back_to_gbk_decoder(self) -> None:
        gbk_payload = "招标范围：永磁同步电机改造".encode("gbk")
        path = Path(self._make_tmpfile_bytes(".txt", gbk_payload))
        result = _run(self.parser.parse(path))
        self.assertIn("永磁同步电机", result.text)

    def test_parse_returns_full_text_and_truncated_excerpt(self) -> None:
        """Review R4 #1: ``result.text`` is the full body; only ``excerpt`` is trimmed.

        The previous behaviour silently dropped any RFP content past
        ``max_chars`` (default 80,000), losing the back half of long RFPs at
        storage time.  The parser now hands back the entire body and the
        excerpt-only slice is what feeds the LLM prompt.
        """

        parser = RfpLightParser(settings=_ParserSettings(max_chars=50, excerpt_chars=20))  # type: ignore[arg-type]
        path = Path(self._make_tmpfile(".md", "A" * 200, encoding="utf-8"))
        result = _run(parser.parse(path))
        # Full text is preserved, never truncated by the parser itself.
        self.assertFalse(result.truncated_chars)
        self.assertEqual(result.char_count, 200)
        self.assertEqual(result.text, "A" * 200)
        # Only the LLM-facing excerpt is bounded by excerpt_chars.
        self.assertEqual(len(result.excerpt), 20)

    def test_parse_empty_text_raises_parse_insufficient(self) -> None:
        path = Path(self._make_tmpfile(".md", "   \n\n   \n", encoding="utf-8"))
        with self.assertRaises(RfpLightParseInsufficient) as ctx:
            _run(self.parser.parse(path))
        self.assertEqual(ctx.exception.reason, "empty_text")

    # -- docx --------------------------------------------------------------------

    def test_parse_docx_concatenates_paragraphs_and_tables(self) -> None:
        try:
            import docx
        except ImportError:
            self.skipTest("python-docx not installed in this environment")

        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            doc = docx.Document()
            doc.add_paragraph("需求一：系统应支持中文 RFP 解析。")
            doc.add_paragraph("需求二：必须在 60 秒内完成。")
            table = doc.add_table(rows=2, cols=2)
            table.rows[0].cells[0].text = "设备"
            table.rows[0].cells[1].text = "规格"
            table.rows[1].cells[0].text = "电机"
            table.rows[1].cells[1].text = "永磁同步"
            doc.save(tmp.name)
            tmp_path = Path(tmp.name)

        try:
            result = _run(self.parser.parse(tmp_path))
            self.assertEqual(result.source_format, "docx")
            self.assertIn("需求一", result.text)
            self.assertIn("需求二", result.text)
            # Table cells should appear, joined by ` | `
            self.assertIn("设备 | 规格", result.text)
            self.assertIn("电机 | 永磁同步", result.text)
        finally:
            tmp_path.unlink(missing_ok=True)

    # -- pdf ---------------------------------------------------------------------

    def test_parse_pdf_caps_pages_to_max(self) -> None:
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("pypdfium2 not installed in this environment")

        # Build a 3-page PDF in memory with pypdfium2 helpers (uses PdfDocument.new
        # which lacks pages; we synthesize via reportlab-style fallback). pypdfium2
        # does not include a PDF builder so we generate a tiny PDF with reportlab if
        # available, otherwise fall back to a hand-crafted minimal PDF.
        pdf_path = self._build_three_page_pdf()
        if pdf_path is None:
            self.skipTest("no PDF builder available in test environment")

        parser = RfpLightParser(settings=_ParserSettings(max_pages=2))  # type: ignore[arg-type]
        try:
            result = _run(parser.parse(pdf_path))
            self.assertEqual(result.source_format, "pdf")
            self.assertEqual(result.page_count, 2)
            self.assertTrue(result.truncated_pages)
        finally:
            pdf_path.unlink(missing_ok=True)

    # -- legacy & unsupported ----------------------------------------------------

    def test_parse_legacy_doc_raises_insufficient(self) -> None:
        path = Path(self._make_tmpfile_bytes(".doc", b"fake legacy doc bytes"))
        with self.assertRaises(RfpLightParseInsufficient) as ctx:
            _run(self.parser.parse(path))
        self.assertEqual(ctx.exception.reason, "legacy_doc_format")
        self.assertIn(".doc", str(ctx.exception))

    def test_parse_unsupported_format_raises_insufficient(self) -> None:
        path = Path(self._make_tmpfile_bytes(".png", b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(RfpLightParseInsufficient) as ctx:
            _run(self.parser.parse(path))
        self.assertEqual(ctx.exception.reason, "unsupported_format")

    def test_parse_missing_file_raises_runtime_error(self) -> None:
        with self.assertRaises(RfpLightParseError):
            _run(self.parser.parse("/tmp/does-not-exist-rfp-12345.md"))

    # -- timeout -----------------------------------------------------------------

    def test_parse_timeout_raises_parse_insufficient(self) -> None:
        parser = RfpLightParser(settings=_ParserSettings(max_seconds=0.1))  # type: ignore[arg-type]
        path = Path(self._make_tmpfile(".md", "hello", encoding="utf-8"))

        def _slow_extract(_path: Path, _max_pages: int) -> tuple[str, int, bool, str]:
            import time as _time

            _time.sleep(0.5)
            return "ignored", 0, False, "text"

        with patch.object(parser, "_extract_blocking", side_effect=_slow_extract):
            with self.assertRaises(RfpLightParseInsufficient) as ctx:
                _run(parser.parse(path))
        self.assertEqual(ctx.exception.reason, "timeout")
        self.assertGreaterEqual(ctx.exception.metadata.get("timeout_seconds", 0), 0.1)

    # -- safety guarantees -------------------------------------------------------

    def test_parser_does_not_import_heavy_ingestion_modules(self) -> None:
        """RfpLightParser must not pull in Chunker/Embedder/Qdrant/library_refresh.

        We import it in isolation and verify nothing in the heavy ingestion graph
        was loaded as a side effect.  This is a guard against future drift.
        """

        import importlib
        import sys

        forbidden = (
            "app.services.vectorstore.chunker",
            "app.services.vectorstore.embedder",
            "app.services.vectorstore.qdrant_client",
            "app.services.knowledge.library_refresh",
        )
        for name in forbidden:
            sys.modules.pop(name, None)
        # Re-import the parser fresh
        sys.modules.pop("app.services.parsing.rfp_light_parser", None)
        importlib.import_module("app.services.parsing.rfp_light_parser")
        for name in forbidden:
            self.assertNotIn(
                name,
                sys.modules,
                msg=f"rfp_light_parser must not import {name!r}",
            )

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def _make_tmpfile(suffix: str, content: str, *, encoding: str) -> str:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content.encode(encoding))
            return tmp.name

    @staticmethod
    def _make_tmpfile_bytes(suffix: str, content: bytes) -> str:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            return tmp.name

    @staticmethod
    def _build_three_page_pdf() -> Path | None:
        """Build a 3-page PDF using reportlab if available; return None otherwise."""

        from tempfile import NamedTemporaryFile

        try:
            from reportlab.pdfgen import canvas  # type: ignore[import-not-found]
        except ImportError:
            return None

        with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            path = Path(tmp.name)
        c = canvas.Canvas(str(path))
        for idx in range(3):
            c.drawString(72, 720, f"Requirements page {idx + 1}")
            c.drawString(72, 700, "ACME Inc. RFP - test fixture")
            c.showPage()
        c.save()
        return path


class RfpLightParserConfigClampTests(TestCase):
    """Verify clamps inside ``get_settings()`` keep the parser within sane bounds."""

    def test_settings_clamps_apply_default_bounds(self) -> None:
        # We don't override env; just observe defaults are within plan ranges.
        settings = get_settings()
        self.assertGreaterEqual(settings.rfp_light_parse_max_seconds, 5.0)
        self.assertLessEqual(settings.rfp_light_parse_max_seconds, 300.0)
        self.assertGreaterEqual(settings.rfp_light_parse_max_pages, 1)
        self.assertLessEqual(settings.rfp_light_parse_max_pages, 200)
        self.assertGreaterEqual(settings.rfp_light_parse_max_chars, 1000)
        self.assertLessEqual(settings.rfp_light_parse_max_chars, 500_000)
        self.assertLessEqual(settings.rfp_light_parse_excerpt_chars, settings.rfp_light_parse_max_chars)


class RfpLightParserModuleSurfaceTests(TestCase):
    def test_supported_suffix_sets_are_consistent(self) -> None:
        from app.services.parsing import rfp_light_parser as mod

        self.assertIn(".pdf", mod.SUPPORTED_PDF_SUFFIXES)
        self.assertIn(".docx", mod.SUPPORTED_DOCX_SUFFIXES)
        self.assertIn(".txt", mod.SUPPORTED_TEXT_SUFFIXES)
        self.assertIn(".md", mod.SUPPORTED_TEXT_SUFFIXES)
        self.assertIn(".doc", LEGACY_DOC_SUFFIXES)
        # legacy doc must NOT be in ALL_SUPPORTED
        self.assertNotIn(".doc", ALL_SUPPORTED_SUFFIXES)
