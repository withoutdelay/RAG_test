from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifacts import get_validation_service
from app.api.router import api_router
from app.db import get_db_session


class _FakeValidationService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.job_id = uuid4()
        self.report_id = uuid4()
        self.review_task_id = uuid4()

    async def validate_project(self, *, session, project_id, draft_version=None, outline_id=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.report_id,
                project_id=project_id,
                draft_version=draft_version or 1,
                outline_id=outline_id,
                requirement_card_id=uuid4(),
                evidence_bundle_id=uuid4(),
                status="review_required",
                errors=[{"code": "VAL003", "level": "P0", "message": "参数冲突"}],
                warnings=[{"code": "VAL103", "level": "P1", "message": "证据质量偏低"}],
                review_tasks_created=[str(self.review_task_id)],
                created_at=datetime.now(timezone.utc),
            ),
        )

    async def get_latest_validation_report(self, *, session, project_id):
        return (
            await self.validate_project(session=session, project_id=project_id)
        )[1]

    async def list_review_tasks(self, *, session, project_id):
        return [
            SimpleNamespace(
                id=self.review_task_id,
                project_id=project_id,
                task_type="param_conflict",
                blocking_level="P0",
                payload={"draft_version": 1, "param_name": "total_power"},
                assignee_user_id=None,
                status="open",
                created_at=datetime.now(timezone.utc),
                resolved_at=None,
            )
        ]

    async def resolve_review_task(self, *, session, project_id, task_id, resolution=None, status="resolved", assignee_user_id=None):
        return SimpleNamespace(
            id=task_id,
            project_id=project_id,
            task_type="param_conflict",
            blocking_level="P0",
            payload={"draft_version": 1, "param_name": "total_power", "resolution": resolution},
            assignee_user_id=assignee_user_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )


async def _fake_db_session():
    yield object()


class ValidationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeValidationService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_validation_service] = lambda: self.service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_validation_routes_support_validate_report_and_review_resolution(self) -> None:
        with TestClient(self.app) as client:
            validate_response = client.post(
                f"/api/v1/projects/{self.service.project_id}/validate",
                json={"draft_version": 1},
            )
            self.assertEqual(validate_response.status_code, 202)
            self.assertEqual(validate_response.json()["data"]["resource_id"], str(self.service.report_id))

            latest_report_response = client.get(
                f"/api/v1/projects/{self.service.project_id}/validation/latest"
            )
            self.assertEqual(latest_report_response.status_code, 200)
            self.assertEqual(latest_report_response.json()["data"]["status"], "review_required")

            review_tasks_response = client.get(
                f"/api/v1/projects/{self.service.project_id}/review-tasks"
            )
            self.assertEqual(review_tasks_response.status_code, 200)
            self.assertEqual(review_tasks_response.json()["data"][0]["task_type"], "param_conflict")

            resolve_response = client.post(
                f"/api/v1/projects/{self.service.project_id}/review-tasks/{self.service.review_task_id}/resolve",
                json={"resolution": {"value": "5000kW"}},
            )
            self.assertEqual(resolve_response.status_code, 200)
            self.assertEqual(resolve_response.json()["data"]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
