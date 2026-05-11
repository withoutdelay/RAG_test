from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.knowledge.wiki_quality import (
    can_publish_wiki_item,
    evaluate_wiki_item_quality,
    item_can_enter_generation,
)
from app.services.knowledge.wiki_storage import write_knowledge_wiki_bundle


def _evidence(**overrides: str) -> dict[str, str]:
    payload = {
        "sample_id": "sample-a",
        "raw_document_id": "raw-a",
        "source_section_id": "2.1",
        "heading_path": "2.1 高压变频器主回路方案",
        "source_document": "案例A.pdf",
        "evidence_quote": "高压变频器主回路采用输入输出隔离和旁路切换。",
        "asset_id": "",
    }
    payload.update(overrides)
    return payload


def _item(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "item_id": "product_family:vfd-system",
        "item_type": "product_family",
        "canonical_name": "高压变频器方案族",
        "aliases": ["高压变频器"],
        "summary": "围绕高压变频器方案族汇总的知识卡。",
        "source_documents": ["案例A.pdf", "案例B.pdf"],
        "evidence": [
            _evidence(source_document="案例A.pdf"),
            _evidence(
                sample_id="sample-b",
                raw_document_id="raw-b",
                source_section_id="3.1",
                heading_path="3.1 高压变频器技术方案",
                source_document="案例B.pdf",
                evidence_quote="变频器配置功率单元、控制柜和旁路回路。",
            ),
        ],
        "quality_score": 0.86,
        "quality_flags": [],
        "status": "auto_approved",
        "created_by": "compiler",
        "updated_at": "2026-05-05T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


class WikiQualityTests(unittest.TestCase):
    def test_can_publish_requires_schema_status_evidence_and_no_blocking_flags(self) -> None:
        self.assertTrue(can_publish_wiki_item(_item()))
        self.assertFalse(can_publish_wiki_item(_item(status="review_required")))
        self.assertFalse(can_publish_wiki_item(_item(quality_flags=["cross_section_contamination"])))
        self.assertFalse(
            can_publish_wiki_item(
                _item(evidence=[_evidence(source_section_id="")], quality_flags=[], quality_score=0.86)
            )
        )

    def test_generation_entry_requires_published_and_clean_quality_gate(self) -> None:
        self.assertTrue(item_can_enter_generation(_item(status="published")))
        self.assertFalse(item_can_enter_generation(_item(status="auto_approved")))
        self.assertFalse(item_can_enter_generation(_item(status="published", quality_flags=["ocr_noise_high"])))

    def test_quality_evaluator_detects_phase5_blocking_flags(self) -> None:
        generic = evaluate_wiki_item_quality(
            item_type="term_alias",
            canonical_name="系统",
            aliases=[],
            evidence=[_evidence()],
            source_documents=["案例A.pdf"],
        )
        noisy = evaluate_wiki_item_quality(
            item_type="section_template",
            canonical_name="主回路方案",
            aliases=[],
            evidence=[
                _evidence(
                    evidence_quote="主回路方案 �������� □□□□ ???? 仅供测试 OCR 噪声。",
                )
            ],
            source_documents=["案例A.pdf"],
        )
        contaminated = evaluate_wiki_item_quality(
            item_type="section_template",
            canonical_name="主回路方案",
            aliases=[],
            evidence=[_evidence(heading_path="培训计划", evidence_quote="本章说明培训计划和售后服务承诺。")],
            source_documents=["案例A.pdf"],
        )
        conflicted = evaluate_wiki_item_quality(
            item_type="product_family",
            canonical_name="高压变频器方案族",
            aliases=["VFD"],
            evidence=[_evidence()],
            source_documents=["案例A.pdf"],
            existing_item={"canonical_name": "高压变频器旧方案族", "aliases": ["旧VFD"]},
        )

        self.assertIn("over_generic_term", generic.quality_flags)
        self.assertEqual(generic.status, "rejected")
        self.assertIn("ocr_noise_high", noisy.quality_flags)
        self.assertIn("cross_section_contamination", contaminated.quality_flags)
        self.assertIn("conflicts_with_published", conflicted.quality_flags)

    def test_storage_publish_gate_excludes_exact_blocking_flags(self) -> None:
        bundle = {
            "pages": {"index.md": "# index\n"},
            "manifest": {},
            "structured_assets": {},
            "wiki_items": [
                _item(item_id="product_family:vfd-system"),
                _item(
                    item_id="product_family:noisy-system",
                    canonical_name="噪声方案族",
                    quality_flags=["ocr_noise_high"],
                    quality_score=0.9,
                ),
            ],
            "log_entry": "compile\n",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary = write_knowledge_wiki_bundle(output_dir=root, bundle=bundle)
            published_items = json.loads((root / "published" / "wiki_items.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["published_items"], 1)
        self.assertEqual([item["item_id"] for item in published_items], ["product_family:vfd-system"])
