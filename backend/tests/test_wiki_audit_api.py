from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.wiki import get_wiki_audit_service, router as wiki_router
from app.services.knowledge.wiki_audit import WikiAuditService


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _evidence(section_id: str = "1") -> dict[str, str]:
    return {
        "sample_id": "sample-alpha",
        "raw_document_id": "raw-alpha",
        "source_section_id": section_id,
        "heading_path": "1. 总体方案",
        "source_document": "alpha.docx",
        "evidence_quote": "系统采用高压变频器方案，包含功率柜、控制柜与冷却单元。",
    }


def _item(
    item_id: str,
    *,
    name: str,
    status: str = "review_required",
    quality_score: float = 0.81,
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "item_type": "product_family",
        "canonical_name": name,
        "aliases": [name.replace("方案族", ""), "VFD"],
        "summary": f"围绕 {name} 汇总的方案族知识卡。",
        "source_documents": ["alpha.docx"],
        "evidence": [_evidence()],
        "quality_score": quality_score,
        "quality_flags": quality_flags or [],
        "status": status,
        "created_by": "compiler",
        "updated_at": "2026-05-05T00:00:00+00:00",
    }


def _product_card(item_id: str, *, title: str) -> dict[str, Any]:
    slug = item_id.split(":", 1)[1].replace("-", "_")
    return {
        "product_family": slug,
        "title": title,
        "aliases": [title.replace("方案族", "")],
        "source_documents": ["alpha.docx"],
        "representative_snippets": ["系统采用高压变频器方案。"],
    }


def _seed_wiki_root(root: Path) -> None:
    draft_items = [
        _item("product_family:alpha-system", name="Alpha 方案族"),
        _item("product_family:beta-system", name="Beta 方案族", status="review_required"),
        _item("product_family:gamma-system", name="Gamma 方案族", status="review_required"),
        _item(
            "product_family:blocking-system",
            name="Blocking 方案族",
            quality_score=0.52,
            quality_flags=["blocking:incomplete_evidence"],
        )
        | {"evidence": [_evidence(section_id="")]},
    ]
    published_items = [
        _item("product_family:beta-system", name="Beta 旧方案族", status="published", quality_score=0.9),
        _item("product_family:stale-system", name="Stale 方案族", status="published", quality_score=0.9),
    ]
    rejected_items: list[dict[str, Any]] = []
    for layer, items in {
        "draft": draft_items,
        "published": published_items,
        "rejected": rejected_items,
    }.items():
        _write_json(root / layer / "wiki_items.json", items)
        _write_json(root / layer / "manifest.json", {"generated_at": "2026-05-05T00:00:00+00:00"})
        _write_json(root / layer / "glossary.json", [])
        _write_json(root / layer / "section_templates.json", [])
        _write_json(
            root / layer / "product_cards.json",
            [_product_card(item["item_id"], title=item["canonical_name"]) for item in items],
        )
        for name in ("module_cards", "equipment_cards", "interface_cards", "forbidden_phrases"):
            _write_json(root / layer / f"{name}.json", [])


class WikiAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "knowledge_wiki"
        _seed_wiki_root(self.root)
        self.service = WikiAuditService(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_summary_and_diff_report_layer_counts(self) -> None:
        summary = self.service.summary()

        self.assertEqual(summary["draft_total"], 4)
        self.assertEqual(summary["published_total"], 2)
        self.assertEqual(summary["review_required_total"], 4)
        self.assertEqual(summary["diff_counts"]["added"], 3)
        self.assertEqual(summary["diff_counts"]["removed"], 1)
        self.assertGreaterEqual(summary["diff_counts"]["changed"], 1)

    def test_approve_publishes_item_and_structured_asset(self) -> None:
        result = self.service.approve_item("product_family:alpha-system", actor="tester", note="证据完整")

        self.assertEqual(result["item"]["status"], "published")
        published_items = json.loads((self.root / "published" / "wiki_items.json").read_text(encoding="utf-8"))
        self.assertIn("product_family:alpha-system", {item["item_id"] for item in published_items})
        product_cards = json.loads((self.root / "published" / "product_cards.json").read_text(encoding="utf-8"))
        alpha_card = next(card for card in product_cards if card.get("governance_item_id") == "product_family:alpha-system")
        self.assertEqual(alpha_card["title"], "Alpha 方案族")

    def test_approve_rejects_blocking_item(self) -> None:
        with self.assertRaisesRegex(ValueError, "complete publishable evidence"):
            self.service.approve_item("product_family:blocking-system", actor="tester")

    def test_reject_removes_published_item(self) -> None:
        result = self.service.reject_item("product_family:beta-system", actor="tester", reason="口径不准")

        self.assertEqual(result["item"]["status"], "rejected")
        published_items = json.loads((self.root / "published" / "wiki_items.json").read_text(encoding="utf-8"))
        self.assertNotIn("product_family:beta-system", {item["item_id"] for item in published_items})
        rejected_items = json.loads((self.root / "rejected" / "wiki_items.json").read_text(encoding="utf-8"))
        self.assertIn("product_family:beta-system", {item["item_id"] for item in rejected_items})

    def test_edit_and_merge_record_human_governance(self) -> None:
        edited = self.service.edit_item(
            "product_family:alpha-system",
            actor="tester",
            updates={"canonical_name": "Alpha 人工修订方案族", "aliases": ["Alpha", "高压变频"]},
        )
        self.assertEqual(edited["item"]["canonical_name"], "Alpha 人工修订方案族")
        self.assertIn("human_edited", edited["item"]["quality_flags"])

        merged = self.service.merge_items(
            "product_family:alpha-system",
            source_item_ids=["product_family:gamma-system"],
            actor="tester",
            note="同义方案族",
        )
        self.assertIn("product_family:gamma-system", merged["item"]["merged_from"])
        self.assertEqual(merged["merged_sources"][0]["merged_into"], "product_family:alpha-system")


class WikiAuditApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "knowledge_wiki"
        _seed_wiki_root(self.root)
        self.service = WikiAuditService(self.root)
        self.app = FastAPI()
        self.app.include_router(wiki_router, prefix="/api/v1")
        self.app.dependency_overrides[get_wiki_audit_service] = lambda: self.service

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()

    def test_wiki_audit_routes_list_detail_and_actions(self) -> None:
        with TestClient(self.app) as client:
            summary_response = client.get("/api/v1/wiki/audit/summary")
            self.assertEqual(summary_response.status_code, 200)
            self.assertEqual(summary_response.json()["data"]["draft_total"], 4)

            list_response = client.get("/api/v1/wiki/audit/items", params={"status": "review_required"})
            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(list_response.json()["data"]["total"], 4)

            detail_response = client.get("/api/v1/wiki/audit/items/product_family:alpha-system")
            self.assertEqual(detail_response.status_code, 200)
            self.assertEqual(detail_response.json()["data"]["item"]["item_id"], "product_family:alpha-system")

            edit_response = client.post(
                "/api/v1/wiki/audit/items/product_family:alpha-system/edit",
                json={"summary": "人工更新后的摘要。"},
            )
            self.assertEqual(edit_response.status_code, 200)
            self.assertEqual(edit_response.json()["data"]["item"]["summary"], "人工更新后的摘要。")

            approve_response = client.post(
                "/api/v1/wiki/audit/items/product_family:alpha-system/approve",
                json={"note": "通过"},
            )
            self.assertEqual(approve_response.status_code, 200)
            self.assertEqual(approve_response.json()["data"]["item"]["status"], "published")

            reject_response = client.post(
                "/api/v1/wiki/audit/items/product_family:alpha-system/reject",
                json={"reason": "撤回发布", "status": "quarantined"},
            )
            self.assertEqual(reject_response.status_code, 200)
            self.assertEqual(reject_response.json()["data"]["item"]["status"], "quarantined")

            diff_response = client.get("/api/v1/wiki/audit/diff")
            self.assertEqual(diff_response.status_code, 200)
            self.assertIn("added", diff_response.json()["data"])


if __name__ == "__main__":
    unittest.main()
