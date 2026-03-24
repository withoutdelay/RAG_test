import unittest

from app.services.parsing.pdf_audit import build_pdf_audit_report, render_pdf_audit_markdown
from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.formula_ocr import FormulaOCRResult


class PDFAuditTests(unittest.TestCase):
    def test_build_pdf_audit_report_flags_complex_diagram_risks(self) -> None:
        markdown = """# 电气设计说明

## 波形分析

波形图见下文，输出满足 V_out = I_load * R_eq，控制器参数为 x_1 = 0.95。

## 电路说明

原理图与接线图请参考附件，存在乱码字符：� Ã。
"""
        report = build_pdf_audit_report(
            file_path="/tmp/sample.pdf",
            markdown=markdown,
            metadata={
                "parser_backend_requested": "docling",
                "parser_backend_used": "docling",
                "table_count": 0,
                "image_count": 0,
            },
            assets=[],
        )

        self.assertEqual(report["parser_backend_used"], "docling")
        self.assertGreaterEqual(report["math_candidate_count"], 1)
        self.assertGreaterEqual(report["figure_keyword_count"], 2)
        self.assertGreaterEqual(report["suspicious_line_count"], 1)
        self.assertEqual(report["document_profile"]["name"], "mixed_engineering_pdf")
        self.assertEqual(report["ingestion_recommendation"], "text_primary_with_asset_review")
        self.assertIn("diagram_reference_dense", report["high_risk_content_flags"])
        self.assertEqual(report["samples"]["math_candidates"][0]["latex_hint"], "波形图见下文，输出满足 V_out = I_load * R_eq，控制器参数为 x_1 = 0.95。")
        self.assertTrue(
            any("Waveforms and circuit drawings likely need manual review" in item["message"] for item in report["findings"])
        )

    def test_render_pdf_audit_markdown_contains_sections(self) -> None:
        report = build_pdf_audit_report(
            file_path="/tmp/clean.pdf",
            markdown="# 标题\n\n正文内容。",
            metadata={
                "parser_backend_requested": "docling",
                "parser_backend_used": "fallback",
                "table_count": 0,
                "image_count": 0,
            },
            assets=[],
        )

        rendered = render_pdf_audit_markdown(report)
        self.assertIn("# PDF Parse Audit: clean.pdf", rendered)
        self.assertIn("## Findings", rendered)
        self.assertIn("## Quality Gate", rendered)
        self.assertIn("Document profile", rendered)
        self.assertIn("Docling path", rendered)

    def test_build_pdf_audit_report_summarizes_large_visual_assets(self) -> None:
        report = build_pdf_audit_report(
            file_path="/tmp/figure.pdf",
            markdown="# 标题\n\n详见波形图。",
            metadata={
                "parser_backend_requested": "docling",
                "parser_backend_used": "docling",
                "table_count": 0,
                "image_count": 2,
            },
            assets=[
                ParsedAsset(
                    asset_type="figure",
                    page_no=17,
                    title="典型波形图",
                    caption=None,
                    heading_path="5.3 输出电压波形",
                    context_before="详见第17页典型波形图。",
                    context_after=None,
                    bbox={"l": 1, "t": 2, "r": 3, "b": 4},
                    source_ref="#/pictures/17",
                    image_bytes=b"fake",
                    meta={"width": 934, "height": 449},
                )
            ],
        )

        self.assertEqual(report["large_visual_asset_count"], 1)
        self.assertTrue(any(item["level"] == "info" for item in report["findings"]))

    def test_build_pdf_audit_report_includes_formula_ocr_results(self) -> None:
        report = build_pdf_audit_report(
            file_path="/tmp/formula.pdf",
            markdown="# 标题\n\n4Ă TH₴₩÷",
            metadata={
                "parser_backend_requested": "docling",
                "parser_backend_used": "docling",
                "table_count": 0,
                "image_count": 1,
            },
            assets=[],
            formula_ocr_results=[
                FormulaOCRResult(
                    source_type="asset",
                    status="succeeded",
                    backend_requested="pix2tex",
                    backend_used="pix2tex",
                    page_no=6,
                    heading_path="公式图",
                    input_preview="4Ă TH₴₩÷",
                    latex=r"V_{out} = I_{load} \cdot R_{eq}",
                    quality="likely_formula",
                    reason="OCR output should still be reviewed against the source figure.",
                )
            ],
        )

        self.assertEqual(report["formula_ocr_backend_requested"], "pix2tex")
        self.assertEqual(report["formula_ocr_success_count"], 1)
        self.assertTrue(any("Recovered 1 formula OCR result" in item["message"] for item in report["findings"]))


if __name__ == "__main__":
    unittest.main()
