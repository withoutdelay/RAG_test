import unittest

from app.services.parsing.markdown_cleaner import clean_markdown


class MarkdownCleanerTests(unittest.TestCase):
    def test_clean_markdown_removes_pdf_noise(self) -> None:
        raw = """# sample.pdf

## 项目名称 :
## 2024 年 6 月 9 日
版本 页码 2 总页数 37 A
DAYU ELECTRIC <!-- image -->
买方 :
卖方 :
变电站⾃动化⽅案
## 目录
| 1 工厂设计环境 ..............................................................4 |
|---------------------------------------------------------------|
## 1 工厂设计环境
正文内容
"""
        cleaned, metadata = clean_markdown(raw, file_path="/tmp/sample.pdf")

        self.assertNotIn("<!-- image -->", cleaned)
        self.assertNotIn("sample.pdf", cleaned)
        self.assertNotIn("版本 页码", cleaned)
        self.assertNotIn("DAYU ELECTRIC", cleaned)
        self.assertNotIn("目录", cleaned)
        self.assertNotIn("项目名称 :", cleaned)
        self.assertNotIn("2024 年 6 月 9 日", cleaned)
        self.assertNotIn("买方 :", cleaned)
        self.assertNotIn("卖方 :", cleaned)
        self.assertIn("变电站自动化方案", cleaned)
        self.assertIn("## 1 工厂设计环境", cleaned)
        self.assertEqual(metadata["cleaner"], "pdf-basic-v1")
        self.assertGreaterEqual(metadata["lines_removed"], 3)
        self.assertGreaterEqual(metadata["front_matter_lines_removed"], 3)
        self.assertGreaterEqual(metadata["brand_lines_removed"], 1)

    def test_clean_markdown_is_noop_for_non_pdf(self) -> None:
        cleaned, metadata = clean_markdown("# title\n\nbody", file_path="/tmp/sample.md")
        self.assertEqual(cleaned, "# title\n\nbody")
        self.assertEqual(metadata["cleaner"], "noop")


if __name__ == "__main__":
    unittest.main()
