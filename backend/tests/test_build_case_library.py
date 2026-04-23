from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.services.parsing.docling_parser import ParsedDocument
from scripts.build_case_library import _parse_case_library_document


class _FakeFullParser:
    def __init__(self, parsed: ParsedDocument) -> None:
        self.parsed = parsed
        self.calls: list[str] = []

    async def parse_document(self, file_path: str) -> ParsedDocument:
        self.calls.append(file_path)
        return self.parsed


class _FakeLightweightParser:
    def __init__(self, parsed: ParsedDocument) -> None:
        self.parsed = parsed
        self.calls: list[tuple[str, bool]] = []

    async def parse(self, file_path: str, *, include_assets: bool = True) -> ParsedDocument:
        self.calls.append((file_path, include_assets))
        return self.parsed


class BuildCaseLibraryTests(unittest.TestCase):
    def test_parse_case_library_document_uses_full_parser_when_requested(self) -> None:
        expected = ParsedDocument(markdown="# 文档\n\n正文", metadata={"mode": "full"})
        parser = _FakeFullParser(expected)

        parsed = asyncio.run(
            _parse_case_library_document(
                "/tmp/demo.pdf",
                lightweight=False,
                full_parser=parser,
            )
        )

        self.assertIs(parsed, expected)
        self.assertEqual(parser.calls, ["/tmp/demo.pdf"])

    def test_parse_case_library_document_lightweight_cleans_markdown_and_disables_assets(self) -> None:
        raw = ParsedDocument(
            markdown="# 示例.pdf\n\n版本 页码\n\n## 1 系统方案\n\n正文内容。",
            metadata={"parser": "docling"},
            structure={"heading_hints": [{"text": "1 系统方案"}]},
        )
        parser = _FakeLightweightParser(raw)

        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)

        try:
            parsed = asyncio.run(
                _parse_case_library_document(
                    str(path),
                    lightweight=True,
                    lightweight_parser=parser,
                )
            )
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(parser.calls, [(str(path), False)])
        self.assertEqual(parsed.metadata["case_library_parse_mode"], "lightweight")
        self.assertEqual(parsed.metadata["cleaner"], "pdf-basic-v1")
        self.assertIn("## 1 系统方案", parsed.markdown)
        self.assertNotIn("版本 页码", parsed.markdown)
        self.assertEqual(parsed.structure, raw.structure)


if __name__ == "__main__":
    unittest.main()
