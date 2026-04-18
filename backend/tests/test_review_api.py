from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.generation import get_generation_service
from app.api.router import api_router
from app.db import get_db_session
from app.services.generation import GenerationService


class _FakeReviewService:
    def __init__(self) -> None:
        self.task_id = uuid4()
        self.first_review_id = uuid4()
        self.follow_up_review_id = uuid4()
        self.review_rejected = False

    async def list_reviews(self, *, session, task_id):
        now = datetime.now(timezone.utc)
        if self.review_rejected:
            return [
                SimpleNamespace(
                    id=self.first_review_id,
                    task_id=self.task_id,
                    section_index=0,
                    review_type="content_review",
                    description="首轮审批已处理。",
                    payload={},
                    status="revised",
                    user_feedback="请加强交付表述",
                    created_at=now,
                    resolved_at=now,
                ),
                SimpleNamespace(
                    id=self.follow_up_review_id,
                    task_id=self.task_id,
                    section_index=0,
                    review_type="content_review",
                    description="章节已根据反馈重写，请重新确认。",
                    payload={"previous_review_id": str(self.first_review_id)},
                    status="pending",
                    user_feedback=None,
                    created_at=now,
                    resolved_at=None,
                ),
            ]
        return [
            SimpleNamespace(
                id=self.first_review_id,
                task_id=self.task_id,
                section_index=0,
                review_type="content_review",
                description="章节《项目概述》已生成，请确认内容是否满足要求。",
                payload={"section_title": "项目概述"},
                status="pending",
                user_feedback=None,
                created_at=now,
                resolved_at=None,
            )
        ]

    async def approve_review(self, *, session, review_id, feedback=None):
        return {
            "review_id": review_id,
            "task_id": self.task_id,
            "status": "approved",
            "task_status": "completed",
            "section_index": 0,
            "follow_up_review_id": None,
        }

    async def reject_review(self, *, session, review_id, feedback):
        self.review_rejected = True
        return {
            "review_id": review_id,
            "task_id": self.task_id,
            "status": "revised",
            "task_status": "section_review",
            "section_index": 0,
            "follow_up_review_id": self.follow_up_review_id,
        }


async def _fake_db_session():
    yield object()


class ReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeReviewService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_generation_service] = lambda: self.service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_review_routes_support_listing_reject_and_approve(self) -> None:
        with TestClient(self.app) as client:
            reviews_response = client.get(f"/api/v1/generation/{self.service.task_id}/reviews")
            self.assertEqual(reviews_response.status_code, 200)
            self.assertEqual(len(reviews_response.json()["data"]), 1)

            reject_response = client.post(
                f"/api/v1/review/{self.service.first_review_id}/reject",
                json={"feedback": "请加强交付表述"},
            )
            self.assertEqual(reject_response.status_code, 200)
            reject_payload = reject_response.json()["data"]
            self.assertEqual(reject_payload["status"], "revised")
            self.assertEqual(reject_payload["task_status"], "section_review")
            self.assertEqual(reject_payload["follow_up_review_id"], str(self.service.follow_up_review_id))

            follow_up_reviews = client.get(f"/api/v1/generation/{self.service.task_id}/reviews")
            self.assertEqual(follow_up_reviews.status_code, 200)
            self.assertEqual(len(follow_up_reviews.json()["data"]), 2)

            approve_response = client.post(
                f"/api/v1/review/{self.service.follow_up_review_id}/approve",
                json={"feedback": "修改后可以通过"},
            )
            self.assertEqual(approve_response.status_code, 200)
            approve_payload = approve_response.json()["data"]
            self.assertEqual(approve_payload["status"], "approved")
            self.assertEqual(approve_payload["task_status"], "completed")


class LegacyReviewApiDisabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_generation_service] = lambda: GenerationService(
            settings=SimpleNamespace(legacy_generation_enabled=False)
        )
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_review_routes_return_gone_when_legacy_flow_disabled(self) -> None:
        with TestClient(self.app) as client:
            reviews_response = client.get(f"/api/v1/generation/{uuid4()}/reviews")
            self.assertEqual(reviews_response.status_code, 410)
            self.assertIn("/generate-sections", reviews_response.json()["detail"])

            approve_response = client.post(
                f"/api/v1/review/{uuid4()}/approve",
                json={"feedback": "通过"},
            )
            self.assertEqual(approve_response.status_code, 410)

            reject_response = client.post(
                f"/api/v1/review/{uuid4()}/reject",
                json={"feedback": "退回"},
            )
            self.assertEqual(reject_response.status_code, 410)

    def test_review_routes_are_hidden_from_openapi_schema(self) -> None:
        with TestClient(self.app) as client:
            schema = client.get("/openapi.json").json()
            self.assertNotIn("/api/v1/generation/{task_id}/reviews", schema["paths"])
            self.assertNotIn("/api/v1/review/{review_id}/approve", schema["paths"])
            self.assertNotIn("/api/v1/review/{review_id}/reject", schema["paths"])


if __name__ == "__main__":
    unittest.main()
