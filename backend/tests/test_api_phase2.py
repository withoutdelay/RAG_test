from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://copilot:copilot@localhost:55432/copilot_db")
os.environ.setdefault("QDRANT_LOCATION", ":memory:")
os.environ.setdefault("PARSER_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_DIMENSION", "16")

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine, reset_db_state


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


if __name__ == "__main__":
    unittest.main()
