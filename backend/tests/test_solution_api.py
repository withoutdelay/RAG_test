from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifacts import get_solution_service
from app.api.router import api_router
from app.db import get_db_session


class _FakeSolutionService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.snapshot_id = uuid4()
        self.previous_snapshot_id = uuid4()

    async def design_solution(self, *, session, project_id, requirement_card_id=None, force_refresh=True):
        return self._make_snapshot(project_id=project_id, status="draft", confirmed_by_user=False)

    async def get_latest_solution(self, *, session, project_id):
        return self._make_snapshot(project_id=project_id, status="draft", confirmed_by_user=False)

    async def list_solutions(self, *, session, project_id, limit=10):
        return [
            self._make_snapshot(project_id=project_id, status="draft", confirmed_by_user=False),
            self._make_snapshot(
                project_id=project_id,
                status="confirmed",
                confirmed_by_user=True,
                version=0,
                snapshot_id=self.previous_snapshot_id,
            ),
        ]

    async def update_solution(self, *, session, project_id, snapshot_id, payload):
        snapshot = self._make_snapshot(project_id=project_id, status="draft", confirmed_by_user=False)
        snapshot.solution_summary = payload.get("solution_summary") or snapshot.solution_summary
        snapshot.confirmation_notes = payload.get("confirmation_notes")
        return snapshot

    async def confirm_solution(self, *, session, project_id, snapshot_id, confirmation_notes=None, confirmed_by_user=True):
        snapshot = self._make_snapshot(project_id=project_id, status="confirmed", confirmed_by_user=confirmed_by_user)
        snapshot.confirmation_notes = confirmation_notes
        snapshot.confirmed_at = datetime.now(timezone.utc)
        return snapshot

    def _make_snapshot(self, *, project_id, status, confirmed_by_user, version=1, snapshot_id=None):
        return SimpleNamespace(
            id=snapshot_id or self.snapshot_id,
            project_id=project_id,
            requirement_card_id=None,
            version=version,
            status=status,
            solution_summary="推荐采用高压变频基线方案。",
            selected_products=[{"role": "主驱动", "series_code": "hv_vfd_multilevel", "quantity": 1}],
            interface_plan={"dcs_protocol": "Modbus TCP", "io_allocation": {"DI": 16, "DO": 8}},
            key_constraints=["需要确认现场供电边界。"],
            open_questions=["待确认电机额定功率。"],
            suggested_chapters=["总体方案", "主回路方案"],
            selection_reason={"matching_signals": ["project.product_line=hv_vfd"]},
            source_catalog_version="seed-20260419-v1",
            confirmation_notes=None,
            confirmed_by_user=confirmed_by_user,
            confirmed_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


async def _fake_db_session():
    yield object()


class SolutionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeSolutionService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_solution_service] = lambda: self.service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_solution_routes(self) -> None:
        with TestClient(self.app) as client:
            create_response = client.post(
                f"/api/v1/projects/{self.service.project_id}/design-solution",
                json={"force_refresh": True},
            )
            self.assertEqual(create_response.status_code, 200)
            self.assertEqual(create_response.json()["data"]["status"], "draft")

            latest_response = client.get(f"/api/v1/projects/{self.service.project_id}/solutions/latest")
            self.assertEqual(latest_response.status_code, 200)
            self.assertEqual(latest_response.json()["data"]["version"], 1)

            history_response = client.get(f"/api/v1/projects/{self.service.project_id}/solutions?limit=5")
            self.assertEqual(history_response.status_code, 200)
            self.assertEqual(len(history_response.json()["data"]), 2)
            self.assertEqual(history_response.json()["data"][1]["status"], "confirmed")

            update_response = client.patch(
                f"/api/v1/projects/{self.service.project_id}/solutions/{self.service.snapshot_id}",
                json={"solution_summary": "更新后的方案摘要", "confirmation_notes": "补充接口边界"},
            )
            self.assertEqual(update_response.status_code, 200)
            self.assertEqual(update_response.json()["data"]["solution_summary"], "更新后的方案摘要")

            confirm_response = client.post(
                f"/api/v1/projects/{self.service.project_id}/solutions/{self.service.snapshot_id}/confirm",
                json={"confirmation_notes": "已确认", "confirmed_by_user": True},
            )
            self.assertEqual(confirm_response.status_code, 200)
            self.assertEqual(confirm_response.json()["data"]["status"], "confirmed")
            self.assertTrue(confirm_response.json()["data"]["confirmed_by_user"])


if __name__ == "__main__":
    unittest.main()
