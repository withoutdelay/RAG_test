import unittest

from app.services.parsing.formula_regions import FormulaRegion, cv2, np, propose_formula_regions

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageDraw = None


@unittest.skipUnless(Image is not None and ImageDraw is not None and np is not None and cv2 is not None, "cv2/numpy/pillow required")
class FormulaRegionTests(unittest.TestCase):
    def test_propose_formula_regions_finds_smaller_band(self) -> None:
        image = Image.new("RGB", (420, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((52, 48, 360, 84), outline="black", width=3)
        draw.line((86, 66, 146, 66), fill="black", width=3)
        draw.line((170, 66, 240, 66), fill="black", width=3)
        draw.line((264, 58, 264, 76), fill="black", width=3)
        draw.line((286, 58, 286, 76), fill="black", width=3)

        regions = propose_formula_regions(image, max_regions=3, explicit_formula_context=True, prefer_top=False)

        self.assertGreaterEqual(len(regions), 1)
        self.assertTrue(any(region.strategy != "fallback" for region in regions))
        top = regions[0]
        self.assertLess(top.height, image.size[1])
        self.assertLess(top.width, image.size[0])

    def test_propose_formula_regions_falls_back_for_tiny_images(self) -> None:
        image = Image.new("RGB", (20, 20), "white")

        regions = propose_formula_regions(image, max_regions=3)

        self.assertEqual(regions, [FormulaRegion(crop_index=1, left=0, top=0, right=20, bottom=20, width=20, height=20, score=0.0, strategy="fallback")])


if __name__ == "__main__":
    unittest.main()
