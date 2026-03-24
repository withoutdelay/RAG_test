import base64
from io import BytesIO
import unittest
from unittest.mock import patch

from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.formula_ocr import FormulaOCRService

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageDraw = None


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jwV0AAAAASUVORK5CYII="
)


class FormulaOCRServiceTests(unittest.TestCase):
    def test_analyze_marks_garbled_text_lines_for_manual_review(self) -> None:
        service = FormulaOCRService(backend="none")

        results = service.analyze(markdown="4Ă TH₴₩÷\n", assets=[])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_type, "text_line")
        self.assertEqual(results[0].status, "manual_review")
        self.assertEqual(results[0].line_number, 1)

    def test_analyze_runs_ocr_for_formula_like_asset(self) -> None:
        service = FormulaOCRService(backend="pix2tex", predictor=lambda _image: r"V_{out} = I_{load} \cdot R_{eq}")

        if Image is None or ImageDraw is None:
            self.skipTest("pillow required")
        image = Image.new("RGB", (420, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 55), "V_out = I_load * R_eq", fill="black")
        handle = BytesIO()
        image.save(handle, format="PNG")

        results = service.analyze(
            markdown="4Ă TH₴₩÷\n",
            assets=[
                ParsedAsset(
                    asset_type="figure",
                    page_no=6,
                    title="启动公式图",
                    caption=None,
                    heading_path="4.1 LCI 变频软起系统方案",
                    context_before="4Ă TH₴₩÷",
                    context_after="输出满足 Vout = Iload * Req",
                    bbox={"l": 1, "t": 2, "r": 3, "b": 4},
                    source_ref="#/pictures/12",
                    image_bytes=handle.getvalue(),
                    meta={"visual_role": "engineering_figure", "width": 500, "height": 300},
                )
            ],
        )

        asset_results = [item for item in results if item.source_type == "asset"]
        self.assertEqual(len(asset_results), 1)
        self.assertEqual(asset_results[0].status, "succeeded")
        self.assertEqual(asset_results[0].quality, "likely_formula")
        self.assertIn(1, asset_results[0].linked_line_numbers)
        self.assertIsNotNone(asset_results[0].crop_bbox)

    def test_analyze_reports_unavailable_when_backend_cannot_load(self) -> None:
        service = FormulaOCRService(backend="pix2tex")
        with patch("app.services.parsing.formula_ocr.LatexOCR", None):
            service._predictor = None
            service._predictor_loaded = False
            results = service.analyze(
                markdown="",
                assets=[
                    ParsedAsset(
                        asset_type="figure",
                        page_no=6,
                        title="公式图",
                        caption=None,
                        heading_path="公式图",
                        context_before="公式",
                        context_after=None,
                        bbox=None,
                        source_ref="#/pictures/1",
                        image_bytes=ONE_PIXEL_PNG,
                        meta={"visual_role": "engineering_figure", "width": 500, "height": 300},
                    )
                ],
            )

        asset_results = [item for item in results if item.source_type == "asset"]
        self.assertEqual(len(asset_results), 1)
        self.assertEqual(asset_results[0].status, "unavailable")


if __name__ == "__main__":
    unittest.main()
