from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from app.services.parsing.docx_fast_extract import extract_docx_markdown


DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Title"/></w:pPr>
      <w:r><w:t>某钢铁企业鼓风机电机及启动装置技术方案</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>第一章 系统概述</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>本章说明项目背景与改造范围。</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>参数</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>数值</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>电机容量</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>4500kW</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
</w:styles>
"""


class DocxFastExtractTests(unittest.TestCase):
    def test_extract_docx_markdown_preserves_headings_and_tables(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".docx", delete=False) as handle:
            path = Path(handle.name)
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", DOCUMENT_XML)
                archive.writestr("word/styles.xml", STYLES_XML)

            markdown, metadata = extract_docx_markdown(path)
        finally:
            path.unlink(missing_ok=True)

        self.assertIn("# 某钢铁企业鼓风机电机及启动装置技术方案", markdown)
        self.assertIn("# 第一章 系统概述", markdown)
        self.assertIn("本章说明项目背景与改造范围。", markdown)
        self.assertIn("| 参数 | 数值 |", markdown)
        self.assertEqual(metadata["parser_backend_used"], "docx_fast_extract")
        self.assertEqual(metadata["format"], "docx")


if __name__ == "__main__":
    unittest.main()
