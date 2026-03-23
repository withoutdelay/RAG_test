from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifacts import (
    get_outline_service,
    get_section_draft_service,
)
from app.api.router import api_router
from app.db import get_db_session


class _FakeOutlineService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.outline_id = uuid4()
        self.job_id = uuid4()

    async def generate_outline(self, *, session, project_id, requirement_card_id=None, evidence_bundle_id=None, instructions=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.outline_id,
                project_id=project_id,
                version=1,
                outline_json={
                    "title": "测试项目技术方案",
                    "sections": [
                        {
                            "section_id": "1",
                            "title": "项目概述",
                            "purpose": "总结背景与范围",
                            "mandatory": True,
                            "expected_evidence_types": ["requirement", "case_summary"],
                            "needs_human_review": False,
                            "children": [],
                        }
                    ],
                },
                requirement_card_id=uuid4(),
                evidence_bundle_id=uuid4(),
                validator_status="pending",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    async def get_latest_outline(self, *, session, project_id):
        return (
            await self.generate_outline(session=session, project_id=project_id)
        )[1]

    async def update_outline(self, *, session, project_id, outline_id, outline_json):
        return SimpleNamespace(
            id=outline_id,
            project_id=project_id,
            version=1,
            outline_json=outline_json,
            requirement_card_id=uuid4(),
            evidence_bundle_id=uuid4(),
            validator_status="pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


class _FakeSectionDraftService:
    def __init__(self) -> None:
        self.job_id = uuid4()
        self.section_draft_id = uuid4()

    async def generate_sections(self, *, session, project_id, outline_id=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            [
                SimpleNamespace(
                    id=self.section_draft_id,
                    project_id=project_id,
                    draft_version=1,
                    section_id="1",
                    title="项目概述",
                    content_md="## 项目概述\n\n已生成。",
                    citation_refs=[],
                    assumptions=[],
                    global_param_snapshot={"total_power": "5000kW"},
                    status="generated",
                    validator_result={},
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            ],
        )

    async def regenerate_section(self, *, session, project_id, section_id, outline_id=None):
        return (
            SimpleNamespace(id=self.job_id, status="succeeded"),
            SimpleNamespace(
                id=self.section_draft_id,
                project_id=project_id,
                draft_version=1,
                section_id=section_id,
                title="项目概述",
                content_md="## 项目概述\n\n重生成内容。",
                citation_refs=[],
                assumptions=[],
                global_param_snapshot={},
                status="generated",
                validator_result={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    async def update_section(self, *, session, project_id, section_id, content_md, citation_refs=None, assumptions=None):
        return SimpleNamespace(
            id=self.section_draft_id,
            project_id=project_id,
            draft_version=1,
            section_id=section_id,
            title="项目概述",
            content_md=content_md,
            citation_refs=citation_refs or [],
            assumptions=assumptions or [],
            global_param_snapshot={},
            status="edited",
            validator_result={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


async def _fake_db_session():
    yield object()


class CompositionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.outline_service = _FakeOutlineService()
        self.section_service = _FakeSectionDraftService()
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_outline_service] = lambda: self.outline_service
        self.app.dependency_overrides[get_section_draft_service] = lambda: self.section_service
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_outline_and_section_routes(self) -> None:
        with TestClient(self.app) as client:
            generate_outline_response = client.post(
                f"/api/v1/projects/{self.outline_service.project_id}/generate-outline",
                json={},
            )
            self.assertEqual(generate_outline_response.status_code, 202)
            self.assertEqual(generate_outline_response.json()["data"]["resource_id"], str(self.outline_service.outline_id))

            get_outline_response = client.get(
                f"/api/v1/projects/{self.outline_service.project_id}/outlines/latest"
            )
            self.assertEqual(get_outline_response.status_code, 200)
            self.assertEqual(get_outline_response.json()["data"]["outline_json"]["sections"][0]["section_id"], "1")

            update_outline_response = client.patch(
                f"/api/v1/projects/{self.outline_service.project_id}/outlines/{self.outline_service.outline_id}",
                json={
                    "outline_json": {
                        "title": "调整后大纲",
                        "sections": [
                            {
                                "section_id": "1",
                                "title": "项目概述",
                                "purpose": "调整后说明",
                                "mandatory": True,
                                "expected_evidence_types": ["requirement"],
                                "needs_human_review": False,
                                "children": [],
                            }
                        ],
                    }
                },
            )
            self.assertEqual(update_outline_response.status_code, 200)
            self.assertEqual(update_outline_response.json()["data"]["outline_json"]["title"], "调整后大纲")

            generate_sections_response = client.post(
                f"/api/v1/projects/{self.outline_service.project_id}/generate-sections",
                json={},
            )
            self.assertEqual(generate_sections_response.status_code, 202)
            self.assertEqual(generate_sections_response.json()["data"]["status"], "succeeded")

            regenerate_response = client.post(
                f"/api/v1/projects/{self.outline_service.project_id}/sections/1/regenerate",
                json={},
            )
            self.assertEqual(regenerate_response.status_code, 202)
            self.assertEqual(regenerate_response.json()["data"]["resource_id"], str(self.section_service.section_draft_id))

            update_section_response = client.patch(
                f"/api/v1/projects/{self.outline_service.project_id}/sections/1",
                json={
                    "content_md": "## 项目概述\n\n人工修改后的内容。",
                    "assumptions": [{"assumption": "预算待确认"}],
                },
            )
            self.assertEqual(update_section_response.status_code, 200)
            self.assertEqual(update_section_response.json()["data"]["status"], "edited")


if __name__ == "__main__":
    unittest.main()
