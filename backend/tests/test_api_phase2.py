from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://copilot:copilot@localhost:55432/copilot_db")
os.environ.setdefault("QDRANT_LOCATION", ":memory:")
os.environ.setdefault("PARSER_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_DIMENSION", "16")

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine, reset_db_state
from app.services.parsing.docling_parser import ParsedAsset, ParsedDocument


class Phase2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        reset_db_state()
        asyncio.run(self._truncate_tables())

    def _make_client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    async def _truncate_tables(self) -> None:
        async with get_engine().begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        audit_logs,
                        jobs,
                        review_tasks,
                        section_drafts,
                        proposal_outlines,
                        evidence_bundles,
                        requirement_cards,
                        knowledge_chunks,
                        figure_assets,
                        parsed_blocks,
                        raw_documents,
                        review_points,
                        generation_tasks,
                        chunks,
                        documents,
                        masking_audit_logs,
                        projects
                    RESTART IDENTITY CASCADE
                    """
                )
            )

    def test_project_create_and_list(self) -> None:
        with self._make_client() as client:
            create_response = client.post(
                "/api/v1/projects",
                json={
                    "name": "2026年国网变电站智能化项目",
                    "industry": "电气",
                    "description": "110kV变电站综合自动化改造",
                },
            )
            self.assertEqual(create_response.status_code, 201)
            project_id = create_response.json()["data"]["id"]

            list_response = client.get("/api/v1/projects")
            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(list_response.json()["data"]["total"], 1)
            self.assertEqual(list_response.json()["data"]["items"][0]["id"], project_id)

    def test_document_upload_chunk_listing_and_retrieval_search(self) -> None:
        with self._make_client() as client:
            project_response = client.post(
                "/api/v1/projects",
                json={"name": "检索测试项目", "industry": "电气", "description": "Phase 2 integration"},
            )
            project_id = project_response.json()["data"]["id"]

            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
                handle.write(
                    "# 硬件配置清单\n\n"
                    "本项目采用 110kV 变电站综合自动化方案。\n\n"
                    "| 设备名称 | 型号 | 数量 |\n|---|---|---|\n| 变频器 | ABB ACS880 | 3 |\n"
                )
                upload_path = Path(handle.name)

            try:
                with upload_path.open("rb") as file_handle:
                    upload_response = client.post(
                        f"/api/v1/projects/{project_id}/documents/upload",
                        files={"file": ("sample.md", file_handle, "text/markdown")},
                        data={
                            "doc_type": "historical_proposal",
                            "metadata": '{"industry":"电气","year":2025,"amount_range":"500万以上"}',
                        },
                    )
            finally:
                upload_path.unlink(missing_ok=True)

            self.assertEqual(upload_response.status_code, 202)
            document_id = upload_response.json()["data"]["id"]

            chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks")
            self.assertEqual(chunks_response.status_code, 200)
            chunks = chunks_response.json()["data"]
            self.assertGreaterEqual(len(chunks), 2)
            self.assertTrue(any(chunk["chunk_type"] == "TABLE" for chunk in chunks))

            search_response = client.post(
                "/api/v1/retrieval/search",
                json={
                    "query": "ABB ACS880 变频器配置",
                    "project_id": project_id,
                    "top_k": 5,
                    "filters": {
                        "industry": "电气",
                        "year_gte": 2024,
                        "chunk_type": ["TABLE", "PLAIN"],
                        "doc_type": "historical_proposal",
                    },
                    "search_mode": "hybrid",
                },
            )
            self.assertEqual(search_response.status_code, 200)
            payload = search_response.json()["data"]
            self.assertGreaterEqual(payload["total"], 1)
            self.assertTrue(any("ABB ACS880" in result["content"] for result in payload["results"]))

    def test_document_upload_applies_safe_ingestion_filter(self) -> None:
        with self._make_client() as client:
            project_response = client.post(
                "/api/v1/projects",
                json={"name": "安全入库测试项目", "industry": "电气", "description": "Phase 2 safe ingestion"},
            )
            project_id = project_response.json()["data"]["id"]

            rows = "\n".join(f"| 参数{i} | 数值{i} |" for i in range(35))
            markdown = (
                "# 电机及软起动成套装置技术方案\n\n"
                "项目名称 : 买方 : 卖方 : 2024 年 6 月 9 日\n\n"
                "## 系统功能描述\n\n"
                "系统采用一拖一变频软起方案，LCU 负责与 DCS 系统通信，并提供运行状态、报警和联锁控制。\n\n"
                "## 参数表\n\n"
                "| 名称 | 值 |\n|---|---|\n"
                f"{rows}\n"
            )

            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
                handle.write(markdown)
                upload_path = Path(handle.name)

            try:
                with upload_path.open("rb") as file_handle:
                    upload_response = client.post(
                        f"/api/v1/projects/{project_id}/documents/upload",
                        files={"file": ("sample.md", file_handle, "text/markdown")},
                        data={"doc_type": "historical_proposal", "metadata": '{"industry":"电气"}'},
                    )
            finally:
                upload_path.unlink(missing_ok=True)

            self.assertEqual(upload_response.status_code, 202)
            document_id = upload_response.json()["data"]["id"]

            document_response = client.get(f"/api/v1/documents/{document_id}")
            self.assertEqual(document_response.status_code, 200)
            document_payload = document_response.json()["data"]
            self.assertEqual(document_payload["metadata"]["chunk_count"], 4)
            self.assertEqual(document_payload["metadata"]["indexed_chunk_count"], 1)
            self.assertEqual(document_payload["metadata"]["skipped_chunk_count"], 3)
            self.assertEqual(document_payload["metadata"]["document_profile"]["name"], "text_digital")
            self.assertEqual(document_payload["metadata"]["ingestion_recommendation"], "main_vector_ready")
            self.assertEqual(document_payload["metadata"]["preserved_table_asset_count"], 1)
            self.assertEqual(document_payload["metadata"]["pending_table_reconstruction_count"], 1)
            self.assertEqual(document_payload["metadata"]["queued_table_reconstruction_count"], 0)

            chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks")
            self.assertEqual(chunks_response.status_code, 200)
            chunks = chunks_response.json()["data"]

            skipped = [chunk for chunk in chunks if not chunk["metadata"]["indexable"]]
            indexed = [chunk for chunk in chunks if chunk["metadata"]["indexable"]]
            self.assertGreaterEqual(len(skipped), 2)
            self.assertEqual(len(indexed), 1)
            self.assertTrue(any("front_matter_noise" in chunk["metadata"]["indexing_reasons"] for chunk in skipped))
            self.assertTrue(any("oversized_table" in chunk["metadata"]["indexing_reasons"] for chunk in skipped))
            self.assertTrue(all(chunk["qdrant_point_id"] is None for chunk in skipped))
            self.assertTrue(all(chunk["qdrant_point_id"] is not None for chunk in indexed))
            oversized_table_chunk = next(
                chunk for chunk in skipped if "oversized_table" in chunk["metadata"]["indexing_reasons"]
            )
            self.assertTrue(oversized_table_chunk["metadata"]["table_asset_preserved"])
            self.assertEqual(oversized_table_chunk["metadata"]["table_asset_status"], "pending_reconstruction")
            self.assertIsNotNone(oversized_table_chunk["metadata"]["table_asset_id"])

            table_assets_response = client.get(f"/api/v1/documents/{document_id}/table-assets")
            self.assertEqual(table_assets_response.status_code, 200)
            table_assets = table_assets_response.json()["data"]
            self.assertEqual(len(table_assets), 1)
            self.assertEqual(table_assets[0]["asset_type"], "table")
            self.assertEqual(table_assets[0]["reuse_mode"], "reconstruct_only")
            self.assertEqual(table_assets[0]["metadata"]["reconstruction_status"], "pending")
            self.assertEqual(table_assets[0]["metadata"]["ingestion_strategy"], "table_asset_only")
            self.assertFalse(table_assets[0]["metadata"]["preserve_in_vector_db"])
            self.assertIn("oversized_table", table_assets[0]["metadata"]["indexing_reasons"])
            self.assertIn("| 参数0 | 数值0 |", table_assets[0]["metadata"]["raw_table_markdown"])
            self.assertEqual(table_assets[0]["metadata"]["table_profile"]["profile_name"], "parameter_matrix")
            self.assertIsNone(table_assets[0]["metadata"]["reconstruction_backend"])

            reconstruction_response = client.post(
                f"/api/v1/documents/{document_id}/table-assets/{table_assets[0]['id']}/reconstruct"
            )
            self.assertEqual(reconstruction_response.status_code, 202)
            reconstruction_payload = reconstruction_response.json()["data"]

            updated_document_response = client.get(f"/api/v1/documents/{document_id}")
            self.assertEqual(updated_document_response.status_code, 200)
            updated_document_payload = updated_document_response.json()["data"]
            self.assertEqual(updated_document_payload["metadata"]["pending_table_reconstruction_count"], 0)
            self.assertEqual(updated_document_payload["metadata"]["queued_table_reconstruction_count"], 1)

            updated_table_assets_response = client.get(f"/api/v1/documents/{document_id}/table-assets")
            self.assertEqual(updated_table_assets_response.status_code, 200)
            updated_table_asset = updated_table_assets_response.json()["data"][0]
            self.assertEqual(updated_table_asset["metadata"]["reconstruction_status"], "queued")
            self.assertEqual(updated_table_asset["metadata"]["reconstruction_backend"], "reserved_queue")
            self.assertEqual(updated_table_asset["metadata"]["reconstruction_job_id"], reconstruction_payload["job_id"])

            refreshed_chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks")
            refreshed_chunks = refreshed_chunks_response.json()["data"]
            refreshed_table_chunk = next(chunk for chunk in refreshed_chunks if chunk["id"] == oversized_table_chunk["id"])
            self.assertEqual(refreshed_table_chunk["metadata"]["table_asset_status"], "queued_reconstruction")
            self.assertEqual(
                refreshed_table_chunk["metadata"]["table_asset_job_id"],
                reconstruction_payload["job_id"],
            )

            job_response = client.get(reconstruction_payload["next_poll"])
            self.assertEqual(job_response.status_code, 200)
            job_payload = job_response.json()["data"]
            self.assertEqual(job_payload["job_type"], "table_reconstruction")
            self.assertEqual(job_payload["status"], "queued")
            self.assertEqual(job_payload["input_ref"]["table_asset_id"], table_assets[0]["id"])

            asset_search_response = client.post(
                f"/api/v1/projects/{project_id}/assets/search",
                json={
                    "query": "硬件配置清单 参数表 电机配置",
                    "asset_types": ["table"],
                    "top_k": 3,
                    "section_context": {
                        "section_title": "硬件配置清单",
                        "expected_evidence_types": ["table", "parameter"],
                    },
                },
            )
            self.assertEqual(asset_search_response.status_code, 200)
            asset_search_payload = asset_search_response.json()["data"]
            self.assertEqual(asset_search_payload["total"], 1)
            self.assertEqual(asset_search_payload["results"][0]["asset_type"], "table")
            self.assertTrue(asset_search_payload["results"][0]["review_required"])
            self.assertIn("表格资产", asset_search_payload["results"][0]["reason"])

            search_response = client.post(
                "/api/v1/retrieval/search",
                json={
                    "query": "DCS 联锁控制",
                    "project_id": project_id,
                    "top_k": 5,
                    "filters": {"industry": "电气", "chunk_type": ["PLAIN", "TABLE"], "doc_type": "historical_proposal"},
                    "search_mode": "hybrid",
                },
            )
            self.assertEqual(search_response.status_code, 200)
            payload = search_response.json()["data"]
            self.assertEqual(payload["total"], 1)
            self.assertIn("LCU 负责与 DCS 系统通信", payload["results"][0]["content"])

    def test_document_upload_drops_numeric_table_fragments_before_asset_preservation(self) -> None:
        with self._make_client() as client:
            project_response = client.post(
                "/api/v1/projects",
                json={"name": "数字表残片过滤项目", "industry": "电气", "description": "Phase 2 numeric table fragment filter"},
            )
            project_id = project_response.json()["data"]["id"]

            markdown = (
                "# 技术说明\n\n"
                "系统采用一拖一高压变频方案，支持 DCS 联锁和状态监测。\n\n"
                "## 47.8 17.5\n\n"
                "| 37.5 | 17.5 | 20.0 |\n"
                "|---|---|---|\n"
                "| 47.3 | 17.5 | 29.8 |\n"
                "| 43.5 | 17.5 | 26 |\n"
            )

            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
                handle.write(markdown)
                upload_path = Path(handle.name)

            try:
                with upload_path.open("rb") as file_handle:
                    upload_response = client.post(
                        f"/api/v1/projects/{project_id}/documents/upload",
                        files={"file": ("sample.md", file_handle, "text/markdown")},
                        data={"doc_type": "historical_proposal", "metadata": '{"industry":"电气"}'},
                    )
            finally:
                upload_path.unlink(missing_ok=True)

            self.assertEqual(upload_response.status_code, 202)
            document_id = upload_response.json()["data"]["id"]

            document_response = client.get(f"/api/v1/documents/{document_id}")
            self.assertEqual(document_response.status_code, 200)
            document_payload = document_response.json()["data"]
            self.assertEqual(document_payload["metadata"]["chunk_count"], 3)
            self.assertEqual(document_payload["metadata"]["indexed_chunk_count"], 1)
            self.assertEqual(document_payload["metadata"]["skipped_chunk_count"], 2)
            self.assertEqual(document_payload["metadata"]["preserved_table_asset_count"], 0)

            chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks")
            self.assertEqual(chunks_response.status_code, 200)
            chunks = chunks_response.json()["data"]

            skipped = [chunk for chunk in chunks if not chunk["metadata"]["indexable"]]
            self.assertEqual(len(skipped), 2)
            self.assertTrue(any("numeric_table_fragment" in chunk["metadata"]["indexing_reasons"] for chunk in skipped))
            self.assertTrue(any("heading_only" in chunk["metadata"]["indexing_reasons"] for chunk in skipped))
            numeric_table_chunk = next(
                chunk for chunk in skipped if "numeric_table_fragment" in chunk["metadata"]["indexing_reasons"]
            )
            self.assertFalse(numeric_table_chunk["metadata"]["preserve_for_assets"])

            table_assets_response = client.get(f"/api/v1/documents/{document_id}/table-assets")
            self.assertEqual(table_assets_response.status_code, 200)
            self.assertEqual(table_assets_response.json()["data"], [])

    def test_document_upload_persists_figure_assets(self) -> None:
        with self._make_client() as client:
            project_response = client.post(
                "/api/v1/projects",
                json={"name": "图纸提取测试项目", "industry": "电气", "description": "Phase 2 figure asset coverage"},
            )
            project_id = project_response.json()["data"]["id"]

            parsed_document = ParsedDocument(
                markdown="# 图纸说明\n\n正文内容。",
                metadata={"parser_backend_used": "mock-docling", "format": "pdf"},
                assets=[
                    ParsedAsset(
                        asset_type="figure",
                        page_no=3,
                        title="系统一次原理图",
                        caption="一次原理图",
                        heading_path="5.1.1 变频器系统示意图",
                        context_before="本系统一次原理图如下：",
                        context_after="请结合电压波形理解。",
                        bbox={"l": 10, "t": 20, "r": 30, "b": 40},
                        source_ref="#/pictures/7",
                        image_bytes=b"fake-png-bytes",
                        image_ext=".png",
                        meta={"width": 320, "height": 240},
                    )
                ],
            )

            with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
                handle.write(b"%PDF-1.4 fake")
                upload_path = Path(handle.name)

            try:
                with patch(
                    "app.api.documents.ParserService.parse_document",
                    new=AsyncMock(return_value=parsed_document),
                ):
                    with upload_path.open("rb") as file_handle:
                        upload_response = client.post(
                            f"/api/v1/projects/{project_id}/documents/upload",
                            files={"file": ("sample.pdf", file_handle, "application/pdf")},
                            data={"doc_type": "rfp", "metadata": '{"industry":"电气"}'},
                        )
            finally:
                upload_path.unlink(missing_ok=True)

            self.assertEqual(upload_response.status_code, 202)
            document_id = upload_response.json()["data"]["id"]

            figure_assets_response = client.get(f"/api/v1/documents/{document_id}/figure-assets")
            self.assertEqual(figure_assets_response.status_code, 200)
            figure_assets = figure_assets_response.json()["data"]
            self.assertEqual(len(figure_assets), 1)
            self.assertEqual(figure_assets[0]["page_no"], 3)
            self.assertEqual(figure_assets[0]["asset_type"], "figure")
            self.assertEqual(figure_assets[0]["title"], "系统一次原理图")
            self.assertEqual(figure_assets[0]["metadata"]["heading_path"], "5.1.1 变频器系统示意图")
            self.assertEqual(figure_assets[0]["metadata"]["context_before"], "本系统一次原理图如下：")

            asset_search_response = client.post(
                f"/api/v1/projects/{project_id}/assets/search",
                json={
                    "query": "变频器系统示意图 一次原理图 波形",
                    "asset_types": ["figure"],
                    "top_k": 3,
                    "section_context": {
                        "section_title": "技术架构",
                        "expected_evidence_types": ["figure"],
                    },
                },
            )
            self.assertEqual(asset_search_response.status_code, 200)
            asset_search_payload = asset_search_response.json()["data"]
            self.assertEqual(asset_search_payload["total"], 1)
            self.assertEqual(asset_search_payload["results"][0]["title"], "系统一次原理图")
            self.assertEqual(asset_search_payload["results"][0]["visual_role"], "engineering_figure")
            self.assertTrue(asset_search_payload["results"][0]["review_required"])
            self.assertIn("技术架构", asset_search_payload["results"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
