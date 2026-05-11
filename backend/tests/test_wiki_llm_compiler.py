from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.knowledge.wiki_compiler import compile_knowledge_wiki
from app.services.knowledge.wiki_llm_compiler import compile_llm_wiki_candidates, merge_llm_wiki_items
from app.services.knowledge.wiki_storage import write_knowledge_wiki_bundle
from app.services.llm.client import LLMResponse, TaskType


class _StaticLLMClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            model_used="deepseek:unit-test",
            prompt_tokens=120,
            completion_tokens=60,
            total_tokens=180,
            cost_estimate=0.0,
        )


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


def _seed_items() -> list[dict[str, object]]:
    return [
        {
            "item_id": "product_family:vfd-system",
            "item_type": "product_family",
            "canonical_name": "高压变频器方案族",
            "aliases": ["高压变频器", "VFD"],
            "summary": "围绕高压变频器方案族汇总的知识卡。",
            "source_documents": ["案例A.pdf"],
            "evidence": [_evidence()],
            "quality_score": 0.86,
            "quality_flags": [],
            "status": "auto_approved",
            "created_by": "compiler",
            "updated_at": "2026-05-05T00:00:00+00:00",
        }
    ]


class WikiLLMCompilerTests(unittest.IsolatedAsyncioTestCase):
    async def test_compile_llm_wiki_candidates_accepts_schema_backed_product_family(self) -> None:
        client = _StaticLLMClient(
            {
                "status": "ok",
                "items": [
                    {
                        "item_type": "product_family",
                        "canonical_name": "高压变频器方案族",
                        "aliases": ["高压变频器"],
                        "summary": "高压变频器方案族来自主回路、接口和系统方案章节。",
                        "source_documents": ["案例A.pdf"],
                        "evidence": [_evidence()],
                        "confidence": 0.82,
                        "reason": "候选术语和章节均来自历史文档证据。",
                        "asset_type_label": "",
                    }
                ],
            }
        )

        result = await compile_llm_wiki_candidates(
            wiki_items=_seed_items(),
            structured_assets={},
            outline_entries=[],
            block_entries=[],
            llm_client=client,
            enabled=True,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(client.requests[0].task_type, TaskType.KNOWLEDGE_COMPILE)
        item = result["items"][0]
        self.assertTrue(item["item_id"].startswith("llm:product_family:"))
        self.assertEqual(item["status"], "review_required")
        self.assertEqual(item["created_by"], "llm_compiler")
        self.assertIn("llm_compiled_candidate", item["quality_flags"])
        self.assertEqual(item["metadata"]["llm_compiler"]["related_item_id"], "product_family:vfd-system")

    async def test_compile_llm_wiki_candidates_rejects_unseen_professional_term(self) -> None:
        client = _StaticLLMClient(
            {
                "status": "ok",
                "items": [
                    {
                        "item_type": "product_family",
                        "canonical_name": "量子储能变频器方案族",
                        "aliases": ["量子储能变频器"],
                        "summary": "无历史证据的产品族。",
                        "source_documents": ["案例A.pdf"],
                        "evidence": [_evidence()],
                        "confidence": 0.93,
                        "reason": "测试幻觉词拒绝。",
                        "asset_type_label": "",
                    }
                ],
            }
        )

        result = await compile_llm_wiki_candidates(
            wiki_items=_seed_items(),
            structured_assets={},
            outline_entries=[],
            block_entries=[],
            llm_client=client,
            enabled=True,
        )

        item = result["items"][0]
        self.assertEqual(item["status"], "rejected")
        self.assertIn("blocking:llm_unseen_term", item["quality_flags"])
        self.assertLess(item["quality_score"], 0.6)

    async def test_compile_llm_wiki_candidates_keeps_visual_audit_in_review_layer(self) -> None:
        client = _StaticLLMClient(
            {
                "status": "ok",
                "items": [
                    {
                        "item_type": "asset_type_rule",
                        "canonical_name": "系统一次原理图",
                        "aliases": ["single_line_diagram"],
                        "summary": "该图片候选用于审核单线图类型，不直接生成正文。",
                        "source_documents": ["案例A.pdf"],
                        "evidence": [
                            _evidence(
                                heading_path="5.1.1 变频器系统示意图",
                                evidence_quote="本系统一次原理图如下。",
                                asset_id="asset-1",
                            )
                        ],
                        "confidence": 0.71,
                        "reason": "图片标题和上下文指向一次原理图。",
                        "asset_type_label": "single_line_diagram",
                    }
                ],
            }
        )

        result = await compile_llm_wiki_candidates(
            wiki_items=_seed_items(),
            structured_assets={},
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "sample-a",
                    "raw_document_id": "raw-a",
                    "file_name": "案例A.pdf",
                    "source_section_id": "5.1.1",
                    "heading_path": "5.1.1 变频器系统示意图",
                    "content_form": "figure",
                    "asset_id": "asset-1",
                    "caption": "本系统一次原理图如下。",
                    "metadata": {
                        "visual_role": "engineering_figure",
                        "image_url": "data:image/png;base64,AA==",
                    },
                }
            ],
            llm_client=client,
            enabled=True,
        )

        item = result["items"][0]
        self.assertEqual(item["item_type"], "asset_type_rule")
        self.assertEqual(item["status"], "review_required")
        self.assertIn("visual_audit_candidate", item["quality_flags"])
        self.assertEqual(item["metadata"]["llm_compiler"]["asset_type_label"], "single_line_diagram")
        self.assertEqual(client.requests[0].input_images[0].image_url, "data:image/png;base64,AA==")

    async def test_insufficient_evidence_payload_returns_no_items(self) -> None:
        result = await compile_llm_wiki_candidates(
            wiki_items=_seed_items(),
            structured_assets={},
            outline_entries=[],
            block_entries=[],
            llm_client=_StaticLLMClient({"status": "insufficient_evidence", "items": []}),
            enabled=True,
        )

        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["items"], [])

    async def test_merge_llm_items_keeps_candidates_out_of_published_layer(self) -> None:
        bundle = compile_knowledge_wiki(
            outline_entries=[
                {
                    "sample_id": "sample-a",
                    "file_name": "案例A.pdf",
                    "document_title": "高压变频器系统方案",
                    "top_level_titles": ["系统方案", "主回路方案"],
                }
            ],
            block_entries=[
                {
                    "sample_id": "sample-a",
                    "raw_document_id": "raw-a",
                    "file_name": "案例A.pdf",
                    "source_section_id": "2.1",
                    "heading_path": "2.1 高压变频器主回路方案",
                    "equipment_type": "vfd",
                    "section_type": "main_circuit_scheme",
                    "section_summary": "高压变频器主回路采用输入输出隔离和旁路切换。",
                    "content": "高压变频器主回路采用输入输出隔离和旁路切换。",
                }
            ],
            term_lexicon={"vfd": ["vfd", "变频器", "变频柜"]},
        )
        client = _StaticLLMClient(
            {
                "status": "ok",
                "items": [
                    {
                        "item_type": "product_family",
                        "canonical_name": "高压变频器方案族",
                        "aliases": ["高压变频器"],
                        "summary": "LLM 归纳候选，等待人工审核。",
                        "source_documents": ["案例A.pdf"],
                        "evidence": [_evidence()],
                        "confidence": 0.78,
                        "reason": "历史库证据支持。",
                        "asset_type_label": "",
                    }
                ],
            }
        )
        llm_result = await compile_llm_wiki_candidates(
            wiki_items=bundle["wiki_items"],
            structured_assets=bundle["structured_assets"],
            outline_entries=[],
            block_entries=[],
            llm_client=client,
            enabled=True,
        )
        merged = merge_llm_wiki_items(bundle=bundle, llm_result=llm_result)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary = write_knowledge_wiki_bundle(output_dir=root, bundle=merged)
            draft_items = json.loads((root / "draft" / "wiki_items.json").read_text(encoding="utf-8"))
            published_items = json.loads((root / "published" / "wiki_items.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["draft_items"], len(bundle["wiki_items"]) + 1)
        self.assertTrue(any(str(item["item_id"]).startswith("llm:product_family:") for item in draft_items))
        self.assertFalse(any(str(item["item_id"]).startswith("llm:") for item in published_items))
