import unittest

from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.document_profile import build_document_profile


class DocumentProfileTests(unittest.TestCase):
    def test_text_digital_profile_for_markdown_text(self) -> None:
        profile = build_document_profile(
            markdown="# 技术说明\n\n本项目采用 PLC 与 DCS 联动控制，支持联锁、报警和状态监测。",
            metadata={"format": "md", "parser_backend_used": "fallback", "table_count": 0, "image_count": 0},
            assets=[],
        )

        self.assertEqual(profile.name, "text_digital")
        self.assertEqual(profile.ingestion_recommendation, "main_vector_ready")
        self.assertEqual(profile.parse_gate_status, "ready")
        self.assertIsNone(profile.parse_gate_reason)
        self.assertEqual(profile.high_risk_content_flags, ())

    def test_mixed_engineering_profile_for_pdf_with_figures_tables_and_formula(self) -> None:
        profile = build_document_profile(
            markdown=(
                "# 方案说明\n\n"
                "本系统一次原理图如下，详见波形图。\n\n"
                "系统采用一拖一电励磁同步电机方案，LCU 与 DCS 之间通过通讯接口交换状态、联锁和报警信息。\n\n"
                "输出满足 V_out = I_load * R_eq，参数如下。\n\n"
                "装置包括输入变压器、输出变压器、变频器控制盘和励磁控制柜。\n"
            ),
            metadata={"format": "pdf", "parser_backend_used": "docling", "table_count": 3, "image_count": 4},
            assets=[
                ParsedAsset(
                    asset_type="figure",
                    page_no=6,
                    title="一次原理图",
                    caption="原理图",
                    heading_path="3 电机控制系统方案",
                    context_before="本系统一次原理图如下。",
                    context_after="详见波形图。",
                    bbox={"l": 1, "t": 2, "r": 3, "b": 4},
                    source_ref="#/pictures/6",
                    image_bytes=b"fake",
                    meta={"visual_role": "engineering_figure", "width": 800, "height": 600},
                )
            ],
        )

        self.assertEqual(profile.name, "mixed_engineering_pdf")
        self.assertEqual(profile.ingestion_recommendation, "text_primary_with_asset_review")
        self.assertIn("engineering_figures_present", profile.high_risk_content_flags)
        self.assertIn("formula_like_content", profile.high_risk_content_flags)

    def test_scanned_profile_for_sparse_pdf_with_many_images(self) -> None:
        profile = build_document_profile(
            markdown="# 扫描件\n\n图片页。",
            metadata={"format": "pdf", "parser_backend_used": "docling", "table_count": 0, "image_count": 9},
            assets=[
                ParsedAsset(
                    asset_type="figure",
                    page_no=page,
                    title=f"扫描页{page}",
                    caption=None,
                    heading_path=None,
                    context_before=None,
                    context_after=None,
                    bbox={"l": 1, "t": 2, "r": 3, "b": 4},
                    source_ref=f"#/pictures/{page}",
                    image_bytes=b"fake",
                    meta={"visual_role": "illustration", "width": 320, "height": 480},
                )
                for page in range(1, 5)
            ],
        )

        self.assertEqual(profile.name, "scanned_pdf")
        self.assertEqual(profile.ingestion_recommendation, "asset_only_review")
        self.assertEqual(profile.parse_gate_status, "ready")
        self.assertIn("scanned_content_likely", profile.high_risk_content_flags)

    def test_legacy_doc_profile_requires_conversion(self) -> None:
        profile = build_document_profile(
            markdown="# legacy.doc\n\nLegacy DOC binary format is not directly supported.",
            metadata={"format": "doc", "parser_backend_used": "legacy_doc_placeholder", "table_count": 0, "image_count": 0},
            assets=[],
        )

        self.assertEqual(profile.name, "legacy_word_doc")
        self.assertEqual(profile.ingestion_recommendation, "conversion_required")
        self.assertEqual(profile.parse_gate_status, "insufficient")
        self.assertEqual(profile.parse_gate_reason, "legacy_doc_requires_conversion")
        self.assertIn("legacy_doc_requires_conversion", profile.high_risk_content_flags)

    def test_pdf_fallback_parser_marks_parse_gate_insufficient(self) -> None:
        profile = build_document_profile(
            markdown="# 扫描文本\n\nfallback parser output",
            metadata={"format": "pdf", "parser_backend_used": "fallback", "table_count": 0, "image_count": 0},
            assets=[],
        )

        self.assertEqual(profile.parse_gate_status, "insufficient")
        self.assertEqual(profile.parse_gate_reason, "fallback_binary_parser")


if __name__ == "__main__":
    unittest.main()
