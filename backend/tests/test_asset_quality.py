from __future__ import annotations

import unittest

from app.services.parsing.asset_quality import apply_asset_quality_gate
from app.services.parsing.docling_parser import ParsedAsset


class AssetQualityTests(unittest.TestCase):
    def test_quality_gate_rejects_figure_without_image_bytes(self) -> None:
        assets = [
            ParsedAsset(
                asset_type="figure",
                page_no=1,
                title="主接线图",
                caption=None,
                heading_path="3.2 主接线",
                context_before=None,
                context_after=None,
                bbox=None,
                source_ref="fig-1",
                image_bytes=None,
                meta={"visual_role": "engineering_figure"},
            )
        ]

        gated = apply_asset_quality_gate(assets, confidence_threshold=0.6)

        metadata = gated[0].meta
        self.assertEqual(metadata["asset_audit_status"], "rejected")
        self.assertFalse(metadata["preserve_in_vector_db"])
        self.assertIn("figure_without_image_bytes", metadata["asset_audit_reasons"])


if __name__ == "__main__":
    unittest.main()
