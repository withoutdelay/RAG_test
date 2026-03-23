from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifacts import (
    get_evidence_bundle_service,
    get_job_service,
    get_requirement_service,
)
from app.api.router import api_router
from app.db import get_db_session


class _FakeArtifactService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.card_id = uuid4()
        self.bundle_id = uuid4()
        self.job_id = uuid4()

    async def extract_requirement_card(self, *, session, project_id, rfp_document_id=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.card_id,
                project_id=project_id,
                version=1,
                schema_version="v1",
                content={"project_name": "测试项目", "product_line": None, "industry": "电气"},
                missing_items=[{"item_id": "product_line_p0", "field_name": "product_line", "status": "open"}],
                blocking_items=[{"item_id": "product_line_p0", "field_name": "product_line", "status": "open"}],
                confidence=Decimal("0.8200"),
                source_refs=[],
                confirmed_by_user=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    async def get_latest_requirement_card(self, *, session, project_id):
        return SimpleNamespace(
            id=self.card_id,
            project_id=project_id,
            version=1,
            schema_version="v1",
            content={"project_name": "测试项目", "product_line": "hv_vfd", "industry": "电气"},
            missing_items=[],
            blocking_items=[],
            confidence=Decimal("0.9000"),
            source_refs=[],
            confirmed_by_user=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def update_requirement_card(self, *, session, project_id, card_id, content, missing_items, blocking_items, confirmed_by_user):
        return SimpleNamespace(
            id=card_id,
            project_id=project_id,
            version=1,
            schema_version="v1",
            content=content or {"project_name": "测试项目"},
            missing_items=missing_items or [],
            blocking_items=blocking_items or [],
            confidence=Decimal("0.9100"),
            source_refs=[],
            confirmed_by_user=bool(confirmed_by_user),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def resolve_clarification(self, *, session, project_id, item_id, resolution, confirmed_by_user=None):
        return SimpleNamespace(
            id=self.card_id,
            project_id=project_id,
            version=1,
            schema_version="v1",
            content={"project_name": "测试项目", "product_line": resolution},
            missing_items=[{"item_id": item_id, "status": "resolved", "resolution": resolution}],
            blocking_items=[{"item_id": item_id, "status": "resolved", "resolution": resolution}],
            confidence=Decimal("0.9200"),
            source_refs=[],
            confirmed_by_user=bool(confirmed_by_user),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def retrieve_evidence(self, *, session, project_id, requirement_card_id=None, top_k=6, doc_type=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.bundle_id,
                project_id=project_id,
                requirement_card_id=requirement_card_id or self.card_id,
                retrieval_version=1,
                content={"query": "电气 hv_vfd", "results": [{"evidence_id": "ev_001"}]},
                quality_score=Decimal("0.8700"),
                created_at=datetime.now(timezone.utc),
            ),
        )

    async def get_latest_evidence_bundle(self, *, session, project_id):
        return SimpleNamespace(
            id=self.bundle_id,
            project_id=project_id,
            requirement_card_id=self.card_id,
            retrieval_version=1,
            content={"query": "电气 hv_vfd", "results": [{"evidence_id": "ev_001"}]},
            quality_score=Decimal("0.8700"),
            created_at=datetime.now(timezone.utc),
        )


class _FakeJobService:
    async def get_job(self, *, session, job_id):
        return SimpleNamespace(
            id=job_id,
            project_id=uuid4(),
            job_type="extract",
            status="succeeded",
            input_ref={"project_id": "demo"},
            output_ref={"requirement_card_id": "demo-card"},
            retry_count=0,
            error_code=None,
            trace_id="trace-demo",
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )


async def _fake_db_session():
    yield object()


class ArtifactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact_service = _FakeArtifactService()
        self.job_service = _FakeJobService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_requirement_service] = lambda: self.artifact_service
        self.app.dependency_overrides[get_evidence_bundle_service] = lambda: self.artifact_service
        self.app.dependency_overrides[get_job_service] = lambda: self.job_service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_requirement_and_evidence_routes(self) -> None:
        with TestClient(self.app) as client:
            extract_response = client.post(
                f"/api/v1/projects/{self.artifact_service.project_id}/extract-requirement",
                json={},
            )
            self.assertEqual(extract_response.status_code, 202)
            extract_payload = extract_response.json()["data"]
            self.assertEqual(extract_payload["status"], "succeeded")
            self.assertEqual(extract_payload["resource_id"], str(self.artifact_service.card_id))

            latest_card = client.get(f"/api/v1/projects/{self.artifact_service.project_id}/requirement-card/latest")
            self.assertEqual(latest_card.status_code, 200)
            self.assertEqual(latest_card.json()["data"]["content"]["product_line"], "hv_vfd")

            update_response = client.patch(
                f"/api/v1/projects/{self.artifact_service.project_id}/requirement-card/{self.artifact_service.card_id}",
                json={"content": {"project_name": "已更新项目"}, "confirmed_by_user": True},
            )
            self.assertEqual(update_response.status_code, 200)
            self.assertTrue(update_response.json()["data"]["confirmed_by_user"])

            resolve_response = client.post(
                f"/api/v1/projects/{self.artifact_service.project_id}/clarifications/product_line_p0/resolve",
                json={"resolution": "hv_vfd", "confirmed_by_user": True},
            )
            self.assertEqual(resolve_response.status_code, 200)
            self.assertEqual(resolve_response.json()["data"]["content"]["product_line"], "hv_vfd")

            retrieve_response = client.post(
                f"/api/v1/projects/{self.artifact_service.project_id}/retrieve-evidence",
                json={"top_k": 4},
            )
            self.assertEqual(retrieve_response.status_code, 202)
            self.assertEqual(retrieve_response.json()["data"]["resource_id"], str(self.artifact_service.bundle_id))

            latest_bundle = client.get(f"/api/v1/projects/{self.artifact_service.project_id}/evidence-bundles/latest")
            self.assertEqual(latest_bundle.status_code, 200)
            self.assertEqual(latest_bundle.json()["data"]["retrieval_version"], 1)

            job_response = client.get(f"/api/v1/jobs/{self.artifact_service.job_id}")
            self.assertEqual(job_response.status_code, 200)
            self.assertEqual(job_response.json()["data"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
