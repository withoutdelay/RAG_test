from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from app.services.parsing.asset_quality import apply_asset_quality_gate
from app.services.parsing.docling_parser import ParsedAsset
from app.services.retrieval.asset_service import _asset_quality_flags, _build_asset_card


class AssetFragmentFilterTests(unittest.TestCase):
    def test_tiny_docling_picture_is_treated_as_fragment(self) -> None:
        asset_id = uuid4()
        raw_document_id = uuid4()
        raw_document = SimpleNamespace(
            id=raw_document_id,
            project_id=None,
            file_name="sample-blower-lci-retrofit.docx",
            doc_type="historical_proposal",
        )
        asset = SimpleNamespace(
            id=asset_id,
            raw_document_id=raw_document_id,
            asset_type="figure",
            title="4.1.1 变频器系统示意图 Converter System Overview",
            caption=None,
            page_no=None,
            asset_uri="minio://presale-documents/fragment.png",
            reuse_mode="reference_only",
            meta={
                "width": 41,
                "height": 35,
                "visual_role": "engineering_figure",
                "source_ref": "#/pictures/11",
            },
        )

        card = _build_asset_card(asset=asset, raw_document=raw_document, document_map={})

        self.assertEqual(card.visual_role, "asset_fragment")
        self.assertTrue(card.review_required)
        self.assertTrue(_asset_quality_flags(card=card)["low_information"])

    def test_quality_gate_marks_vision_product_photo_as_pending_low_quality(self) -> None:
        asset = ParsedAsset(
            asset_type="figure",
            page_no=3,
            title="一次主接线及旁路拓扑示意图",
            caption=None,
            heading_path="第三章 高压变频系统总体方案",
            context_before="一次主接线及旁路拓扑如下图所示。",
            context_after=None,
            bbox=None,
            source_ref="#/pictures/3",
            image_bytes=b"fake-image",
            meta={
                "visual_role": "product_photo",
                "llm_asset_review": {
                    "status": "reviewed",
                    "confidence": 0.94,
                    "suggested_visual_role": "product_photo",
                    "reason": "vision model sees a product photo",
                },
            },
        )

        reviewed = apply_asset_quality_gate([asset], confidence_threshold=0.72)[0]

        self.assertEqual(reviewed.meta["asset_audit_status"], "review_pending")
        self.assertLess(reviewed.meta["asset_quality_score"], 0.55)
        self.assertTrue(reviewed.meta["review_required"])
        self.assertIn("llm_limited_reuse_as:product_photo", reviewed.meta["asset_audit_reasons"])


if __name__ == "__main__":
    unittest.main()
