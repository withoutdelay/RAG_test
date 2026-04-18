from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.generation import get_generation_service
from app.api.router import api_router
from app.db import get_db_session
from app.services.generation import GenerationService


class _FakeGenerationService:
    def __init__(self) -> None:
        self.task_id = uuid4()
        self.started = False
        self.confirmed = False
        self.approved = False

    async def start_generation(self, *, session, payload):
        self.started = True
        return SimpleNamespace(
            id=self.task_id,
            status="outline_review",
        )

    async def get_task(self, *, session, task_id):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            id=task_id,
            project_id=uuid4(),
            rfp_document_id=None,
            status="completed" if self.approved else ("section_review" if self.confirmed else "outline_review"),
            outline={
                "title": "2026年国网变电站智能化项目技术方案",
                "sections": [
                    {"index": 0, "title": "项目概述", "description": "介绍项目背景。", "keywords": ["项目概述"]},
                    {"index": 1, "title": "技术架构", "description": "说明系统设计。", "keywords": ["技术架构"]},
                ],
            },
            global_params={"total_budget": "800万"},
            sections=[
                {
                    "index": 0,
                    "title": "项目概述",
                    "status": "approved" if self.approved else "reviewing",
                    "content": "## 项目概述\n\n已生成章节内容。",
                    "token_count": 128,
                },
                {
                    "index": 1,
                    "title": "技术架构",
                    "status": "approved" if self.approved else ("reviewing" if self.confirmed else "pending"),
                    "content": "## 技术架构\n\n已生成技术架构内容。" if self.confirmed else "",
                    "token_count": 156 if self.confirmed else 0,
                },
            ],
            final_markdown="# 2026年国网变电站智能化项目技术方案\n\n## 项目概述\n\n已生成章节内容。" if self.approved else None,
            total_tokens=512,
            estimated_cost=Decimal("0.1234"),
            created_at=now,
            completed_at=now if self.approved else None,
        )

    async def confirm_outline(self, *, session, task_id, payload):
        self.confirmed = True
        return SimpleNamespace(
            id=task_id,
            status="section_review",
            sections=[
                {"index": 0, "title": "项目概述", "status": "reviewing"},
                {"index": 1, "title": "技术架构", "status": "reviewing"},
            ],
            final_markdown="",
        )

    async def rewrite_section(self, *, session, task_id, section_index, payload):
        return {
            "section_index": section_index,
            "new_content": f"已根据要求重写：{payload.instruction}",
            "citations": [],
        }

    async def list_reviews(self, *, session, task_id):
        now = datetime.now(timezone.utc)
        if not self.confirmed:
            return []
        return [
            SimpleNamespace(
                id=uuid4(),
                task_id=task_id,
                section_index=0,
                review_type="content_review",
                description="章节《项目概述》已生成，请确认内容是否满足要求。",
                payload={},
                status="pending",
                user_feedback=None,
                created_at=now,
                resolved_at=None,
            ),
            SimpleNamespace(
                id=uuid4(),
                task_id=task_id,
                section_index=1,
                review_type="content_review",
                description="章节《技术架构》已生成，请确认内容是否满足要求。",
                payload={},
                status="pending",
                user_feedback=None,
                created_at=now,
                resolved_at=None,
            ),
        ]

    async def iter_stream_events(self, *, session, task_id):
        yield "outline_ready", {"outline": {"title": "测试大纲", "sections": []}}
        if self.confirmed and not self.approved:
            yield "review_required", {
                "review_point_id": str(uuid4()),
                "section_index": 0,
                "type": "content_review",
                "description": "章节《项目概述》已生成，请确认内容是否满足要求。",
            }
        if self.approved:
            yield "generation_complete", {
                "task_id": str(task_id),
                "total_tokens": 512,
                "estimated_cost": 0.1234,
            }


async def _fake_db_session():
    yield object()


class GenerationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeGenerationService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_generation_service] = lambda: self.service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_generation_routes_support_start_confirm_rewrite_and_stream(self) -> None:
        with TestClient(self.app) as client:
            start_response = client.post(
                "/api/v1/generation/start",
                json={
                    "project_id": str(uuid4()),
                    "instructions": "请生成一份110kV变电站综合自动化技术方案。",
                    "global_params": {"total_budget": "800万"},
                },
            )
            self.assertEqual(start_response.status_code, 202)
            task_id = start_response.json()["data"]["task_id"]
            self.assertTrue(self.service.started)

            task_response = client.get(f"/api/v1/generation/{task_id}")
            self.assertEqual(task_response.status_code, 200)
            self.assertEqual(task_response.json()["data"]["status"], "outline_review")

            stream_before = client.get(f"/api/v1/generation/{task_id}/stream")
            self.assertEqual(stream_before.status_code, 200)
            self.assertIn("event: outline_ready", stream_before.text)

            confirm_response = client.post(
                f"/api/v1/generation/{task_id}/outline/confirm",
                json={},
            )
            self.assertEqual(confirm_response.status_code, 200)
            self.assertEqual(confirm_response.json()["data"]["status"], "section_review")
            self.assertEqual(confirm_response.json()["data"]["pending_review_count"], 2)

            rewrite_response = client.post(
                f"/api/v1/generation/{task_id}/sections/0/rewrite",
                json={
                    "instruction": "加入更专业的交付措辞",
                    "selected_text": "原始内容",
                },
            )
            self.assertEqual(rewrite_response.status_code, 200)
            self.assertIn("加入更专业的交付措辞", rewrite_response.json()["data"]["new_content"])

            stream_after = client.get(f"/api/v1/generation/{task_id}/stream")
            self.assertEqual(stream_after.status_code, 200)
            self.assertIn("event: review_required", stream_after.text)


class LegacyGenerationDisabledApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_generation_service] = lambda: GenerationService(
            settings=SimpleNamespace(legacy_generation_enabled=False)
        )
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_generation_routes_return_gone_when_legacy_flow_disabled(self) -> None:
        with TestClient(self.app) as client:
            start_response = client.post(
                "/api/v1/generation/start",
                json={
                    "project_id": str(uuid4()),
                    "instructions": "请生成一份测试方案。",
                    "global_params": {},
                },
            )
            self.assertEqual(start_response.status_code, 410)
            self.assertIn("/generate-outline", start_response.json()["detail"])

            task_response = client.get(f"/api/v1/generation/{uuid4()}")
            self.assertEqual(task_response.status_code, 410)

            confirm_response = client.post(
                f"/api/v1/generation/{uuid4()}/outline/confirm",
                json={},
            )
            self.assertEqual(confirm_response.status_code, 410)

            rewrite_response = client.post(
                f"/api/v1/generation/{uuid4()}/sections/0/rewrite",
                json={"instruction": "重写", "selected_text": "原文"},
            )
            self.assertEqual(rewrite_response.status_code, 410)

    def test_generation_routes_are_hidden_from_openapi_schema(self) -> None:
        with TestClient(self.app) as client:
            schema = client.get("/openapi.json").json()
            self.assertNotIn("/api/v1/generation/start", schema["paths"])
            self.assertNotIn("/api/v1/generation/{task_id}", schema["paths"])
            self.assertNotIn("/api/v1/generation/{task_id}/stream", schema["paths"])
            self.assertNotIn("/api/v1/generation/{task_id}/outline/confirm", schema["paths"])
            self.assertNotIn("/api/v1/generation/{task_id}/sections/{section_index}/rewrite", schema["paths"])


if __name__ == "__main__":
    unittest.main()
