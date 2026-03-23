from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifacts import get_export_service
from app.api.router import api_router
from app.db import get_db_session


class _FakeExportService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.job_id = uuid4()
        self.export_id = uuid4()

    async def export_project(self, *, session, project_id, format="markdown"):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.export_id,
                project_id=project_id,
                draft_version=1,
                outline_id=uuid4(),
                requirement_card_id=uuid4(),
                evidence_bundle_id=uuid4(),
                validation_report_id=uuid4(),
                file_name="demo-export.md",
                file_type="md",
                storage_path="data/uploads/demo-export.md",
                content_md="# demo\n\ncontent",
                snapshot={"draft_version": 1},
                status="succeeded",
                created_at=datetime.now(timezone.utc),
            ),
        )

    async def get_latest_export(self, *, session, project_id):
        return (
            await self.export_project(session=session, project_id=project_id)
        )[1]


async def _fake_db_session():
    yield object()


class ExportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeExportService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_export_service] = lambda: self.service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_export_routes_support_start_and_latest(self) -> None:
        with TestClient(self.app) as client:
            export_response = client.post(
                f"/api/v1/projects/{self.service.project_id}/export",
                json={"format": "markdown"},
            )
            self.assertEqual(export_response.status_code, 202)
            self.assertEqual(export_response.json()["data"]["resource_id"], str(self.service.export_id))

            latest_response = client.get(f"/api/v1/projects/{self.service.project_id}/exports/latest")
            self.assertEqual(latest_response.status_code, 200)
            payload = latest_response.json()["data"]
            self.assertEqual(payload["id"], str(self.service.export_id))
            self.assertEqual(payload["file_type"], "md")
            self.assertEqual(payload["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
