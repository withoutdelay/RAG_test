from __future__ import annotations

import asyncio
import io
import os
import unittest
from uuid import UUID


os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://copilot:copilot@localhost:5432/copilot_db")
os.environ.setdefault("QDRANT_LOCATION", ":memory:")
os.environ.setdefault("PARSER_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_BACKEND", "fallback")
os.environ.setdefault("EMBEDDING_DIMENSION", "16")
os.environ.setdefault("GATEWAY_MASKING_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER_BACKEND", "mock")

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import get_engine, get_session_factory, reset_db_state
from app.models.project import Project
from app.models.review_task import ReviewTask
from app.models.section_draft import SectionDraft
from app.models.validation_report import ValidationReport
from app.services.vectorstore.qdrant_client import _CLIENT_CACHE


class V2PipelineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        reset_db_state()
        asyncio.run(self._reset_state())

    def _make_client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    async def _reset_state(self) -> None:
        _CLIENT_CACHE.clear()
        async with get_engine().begin() as connection:
            await connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        validation_reports,
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

    async def _load_project_state(self, project_id: UUID) -> dict[str, object]:
        async with get_session_factory()() as session:
            project = await session.get(Project, project_id)
            report = (
                await session.scalars(
                    select(ValidationReport)
                    .where(ValidationReport.project_id == project_id)
                    .order_by(ValidationReport.created_at.desc())
                    .limit(1)
                )
            ).first()
            review_tasks = (
                await session.scalars(
                    select(ReviewTask)
                    .where(ReviewTask.project_id == project_id)
                    .order_by(ReviewTask.created_at.asc())
                )
            ).all()
            section_drafts = (
                await session.scalars(
                    select(SectionDraft)
                    .where(
                        SectionDraft.project_id == project_id,
                        SectionDraft.draft_version == int(project.current_draft_version or 0),
                    )
                    .order_by(SectionDraft.section_id.asc())
                )
            ).all()
            return {
                "project": project,
                "report": report,
                "review_tasks": list(review_tasks),
                "section_drafts": list(section_drafts),
            }

    def test_v2_pipeline_runs_end_to_end_against_postgres(self) -> None:
        markdown = (
            "# 110kV变电站综合自动化改造方案\n\n"
            "本项目面向 110kV 变电站场景，提供 HV-VFD 高压变频器及综合自动化系统改造。\n\n"
            "## 技术架构\n\n"
            "方案采用站控层、间隔层和网络层的分层架构，支持 IEC 61850 协议集成。\n\n"
            "## 硬件配置清单\n\n"
            "| 设备名称 | 型号 | 数量 |\n"
            "| --- | --- | --- |\n"
            "| 高压变频器 | HV-VFD-5000 | 2 |\n"
            "| 站控主机 | IPC-9000 | 1 |\n\n"
            "## 实施计划\n\n"
            "项目分为勘察、设计、实施、调试和验收五个阶段。"
        )

        with self._make_client() as client:
            project_id: str | None = None
            document_id: str | None = None
            try:
                project_response = client.post(
                    "/api/v1/projects",
                    json={
                        "name": "2026年国网变电站智能化项目",
                        "product_line": "hv_vfd",
                        "industry": "电气",
                        "description": "为 110kV 变电站提供综合自动化改造和高压变频器配置方案。",
                    },
                )
                self.assertEqual(project_response.status_code, 201)
                project_id = project_response.json()["data"]["id"]

                upload_response = client.post(
                    f"/api/v1/projects/{project_id}/documents/upload",
                    files={"file": ("rfp.md", io.BytesIO(markdown.encode("utf-8")), "text/markdown")},
                    data={
                        "doc_type": "historical_proposal",
                        "metadata": '{"industry":"电气","year":2026,"amount_range":"500万以上"}',
                    },
                )
                self.assertEqual(upload_response.status_code, 202)
                document_id = upload_response.json()["data"]["id"]
                self.assertEqual(upload_response.json()["data"]["parse_status"], "done")

                chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks")
                self.assertEqual(chunks_response.status_code, 200)
                chunks = chunks_response.json()["data"]
                self.assertGreaterEqual(len(chunks), 3)
                self.assertTrue(any(chunk["chunk_type"] == "TABLE" for chunk in chunks))

                extract_response = client.post(
                    f"/api/v1/projects/{project_id}/extract-requirement",
                    json={"rfp_document_id": document_id},
                )
                self.assertEqual(extract_response.status_code, 202)

                requirement_response = client.get(f"/api/v1/projects/{project_id}/requirement-card/latest")
                self.assertEqual(requirement_response.status_code, 200)
                requirement_card = requirement_response.json()["data"]
                self.assertEqual(requirement_card["content"]["product_line"], "hv_vfd")
                self.assertEqual(requirement_card["blocking_items"], [])

                retrieve_response = client.post(
                    f"/api/v1/projects/{project_id}/retrieve-evidence",
                    json={"top_k": 6, "doc_type": "historical_proposal"},
                )
                self.assertEqual(retrieve_response.status_code, 202)

                evidence_response = client.get(f"/api/v1/projects/{project_id}/evidence-bundles/latest")
                self.assertEqual(evidence_response.status_code, 200)
                evidence_bundle = evidence_response.json()["data"]
                self.assertGreaterEqual(len(evidence_bundle["content"]["results"]), 1)

                outline_response = client.post(f"/api/v1/projects/{project_id}/generate-outline", json={})
                self.assertEqual(outline_response.status_code, 202)

                latest_outline_response = client.get(f"/api/v1/projects/{project_id}/outlines/latest")
                self.assertEqual(latest_outline_response.status_code, 200)
                outline = latest_outline_response.json()["data"]
                self.assertGreaterEqual(len(outline["outline_json"]["sections"]), 5)

                sections_response = client.post(f"/api/v1/projects/{project_id}/generate-sections", json={})
                self.assertEqual(sections_response.status_code, 202)

                validate_response = client.post(
                    f"/api/v1/projects/{project_id}/validate",
                    json={"draft_version": 1},
                )
                self.assertEqual(validate_response.status_code, 202)
                validation_report_id = validate_response.json()["data"]["resource_id"]

                latest_validation_response = client.get(f"/api/v1/projects/{project_id}/validation/latest")
                self.assertEqual(latest_validation_response.status_code, 200)
                validation_report = latest_validation_response.json()["data"]
                self.assertEqual(validation_report["id"], validation_report_id)
                self.assertEqual(validation_report["draft_version"], 1)
                self.assertEqual(validation_report["status"], "review_required")
                self.assertGreaterEqual(len(validation_report["review_tasks_created"]), 1)

                review_tasks_response = client.get(f"/api/v1/projects/{project_id}/review-tasks")
                self.assertEqual(review_tasks_response.status_code, 200)
                review_tasks = review_tasks_response.json()["data"]
                task_types = {task["task_type"] for task in review_tasks}
                self.assertIn("final_review", task_types)

                project_detail_response = client.get(f"/api/v1/projects/{project_id}")
                self.assertEqual(project_detail_response.status_code, 200)
                project = project_detail_response.json()["data"]
                self.assertEqual(project["status"], "REVIEW_REQUIRED")
                self.assertEqual(project["current_draft_version"], 1)
                self.assertEqual(project["current_outline_id"], outline["id"])
            finally:
                if document_id and project_id:
                    client.delete(f"/api/v1/documents/{document_id}")

        state = asyncio.run(self._load_project_state(UUID(project_id)))
        stored_project = state["project"]
        stored_report = state["report"]
        stored_review_tasks = state["review_tasks"]
        stored_section_drafts = state["section_drafts"]

        self.assertIsNotNone(stored_project)
        self.assertIsNotNone(stored_report)
        self.assertEqual(stored_project.status, "REVIEW_REQUIRED")
        self.assertEqual(stored_report.status, "review_required")
        self.assertEqual(len(stored_section_drafts), len(outline["outline_json"]["sections"]))
        self.assertGreaterEqual(len(stored_review_tasks), len(review_tasks))
        self.assertTrue(any(task.task_type == "final_review" for task in stored_review_tasks))


if __name__ == "__main__":
    unittest.main()
