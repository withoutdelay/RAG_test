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

from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base, get_engine, get_session_factory, reset_db_state
from app.models.project import Project
from app.models.project_export import ProjectExport
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
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        exports,
                        validation_reports,
                        audit_logs,
                        jobs,
                        review_tasks,
                        solution_snapshots,
                        section_drafts,
                        proposal_outlines,
                        evidence_bundles,
                        requirement_cards,
                        product_interfaces,
                        product_models,
                        product_compatibility,
                        product_family_aliases,
                        product_families,
                        product_constraints,
                        product_standard_configs,
                        product_series,
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
            export_record = (
                await session.scalars(
                    select(ProjectExport)
                    .where(ProjectExport.project_id == project_id)
                    .order_by(ProjectExport.created_at.desc())
                    .limit(1)
                )
            ).first()
            return {
                "project": project,
                "report": report,
                "review_tasks": list(review_tasks),
                "section_drafts": list(section_drafts),
                "export": export_record,
            }

    def test_v2_pipeline_runs_end_to_end_against_postgres(self) -> None:
        markdown = (
            "# 高炉鼓风机电机及 LCI 变频软起动系统改造方案\n\n"
            "本项目面向钢铁厂高炉鼓风机 10kV 同步电机场景，提供 LCI/SFC 变频软起动系统改造。\n\n"
            "## 技术架构\n\n"
            "方案采用 LCI/SFC 变频软起动装置、本地 PLC 控制单元、DCS 接口、断路器反馈和励磁联锁组成的分层控制架构。\n\n"
            "## 硬件配置清单\n\n"
            "| 设备名称 | 型号 | 数量 |\n"
            "| --- | --- | --- |\n"
            "| LCI/SFC 变频软起动系统 | LCI-SFC-10kV | 1 |\n"
            "| 同步电机接口 | 10kV | 2 |\n"
            "| 本地控制单元 PLC | PLC-LOCAL | 1 |\n\n"
            "## 实施计划\n\n"
            "项目分为现场勘察、系统设计、设备成套、安装调试、同步切换试验和验收交付六个阶段。"
        )

        with self._make_client() as client:
            project_id: str | None = None
            document_id: str | None = None
            try:
                project_response = client.post(
                    "/api/v1/projects",
                    json={
                        "name": "某钢铁集团高炉鼓风机 LCI 软起动改造项目",
                        "product_line": "lci",
                        "industry": "钢铁",
                        "description": "为高炉鼓风机 10kV 同步电机配置 LCI/SFC 变频软起动系统。",
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
                self.assertEqual(requirement_card["content"]["product_line"], "lci")
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
                nested_outline_json = dict(outline["outline_json"])
                nested_sections = [dict(section) for section in nested_outline_json["sections"]]
                first_section = dict(nested_sections[0])
                first_section["children"] = [
                    {
                        "section_id": f"{first_section['section_id']}.1",
                        "title": f"{first_section['title']}背景",
                        "purpose": "补充该章节的背景与约束条件。",
                        "mandatory": False,
                        "expected_evidence_types": ["section"],
                        "needs_human_review": False,
                        "children": [],
                    }
                ]
                nested_sections[0] = first_section
                nested_outline_json["sections"] = nested_sections

                update_outline_response = client.patch(
                    f"/api/v1/projects/{project_id}/outlines/{outline['id']}",
                    json={"outline_json": nested_outline_json},
                )
                self.assertEqual(update_outline_response.status_code, 200)
                self.assertEqual(update_outline_response.json()["data"]["outline_json"]["outline_status"], "candidate")

                blocked_sections_response = client.post(
                    f"/api/v1/projects/{project_id}/generate-sections",
                    json={},
                )
                self.assertEqual(blocked_sections_response.status_code, 400)
                self.assertIn("approved", blocked_sections_response.json()["detail"])

                approve_outline_response = client.post(
                    f"/api/v1/projects/{project_id}/outlines/{outline['id']}/approve",
                    json={"reviewer_notes": "售前已确认大纲结构"},
                )
                self.assertEqual(approve_outline_response.status_code, 200)
                approved_outline = approve_outline_response.json()["data"]
                self.assertEqual(approved_outline["outline_json"]["outline_status"], "approved")

                sections_response = client.post(f"/api/v1/projects/{project_id}/generate-sections", json={})
                self.assertEqual(sections_response.status_code, 202)

                list_sections_response = client.get(f"/api/v1/projects/{project_id}/sections")
                self.assertEqual(list_sections_response.status_code, 200)
                section_drafts_payload = list_sections_response.json()["data"]
                section_ids = {draft["section_id"] for draft in section_drafts_payload}
                self.assertIn(first_section["section_id"], section_ids)
                self.assertIn(f"{first_section['section_id']}.1", section_ids)

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
                self.assertEqual(validation_report["status"], "passed")

                review_tasks_response = client.get(f"/api/v1/projects/{project_id}/review-tasks")
                self.assertEqual(review_tasks_response.status_code, 200)
                review_tasks = review_tasks_response.json()["data"]
                task_types = {task["task_type"] for task in review_tasks}
                self.assertNotIn("final_review", task_types)

                project_detail_response = client.get(f"/api/v1/projects/{project_id}")
                self.assertEqual(project_detail_response.status_code, 200)
                project = project_detail_response.json()["data"]
                self.assertEqual(project["status"], "EXPORTABLE")
                self.assertEqual(project["current_draft_version"], 1)
                self.assertEqual(project["current_outline_id"], outline["id"])

                for task in review_tasks:
                    resolution = {"confirmed": True}
                    if task["task_type"] == "param_conflict":
                        resolution = {"value": "5000kW"}
                    resolve_response = client.post(
                        f"/api/v1/projects/{project_id}/review-tasks/{task['id']}/resolve",
                        json={"resolution": resolution},
                    )
                    self.assertEqual(resolve_response.status_code, 200)

                exportable_project_response = client.get(f"/api/v1/projects/{project_id}")
                self.assertEqual(exportable_project_response.status_code, 200)
                self.assertEqual(exportable_project_response.json()["data"]["status"], "EXPORTABLE")

                export_response = client.post(
                    f"/api/v1/projects/{project_id}/export",
                    json={"format": "markdown"},
                )
                self.assertEqual(export_response.status_code, 202)
                export_id = export_response.json()["data"]["resource_id"]

                latest_export_response = client.get(f"/api/v1/projects/{project_id}/exports/latest")
                self.assertEqual(latest_export_response.status_code, 200)
                export_payload = latest_export_response.json()["data"]
                self.assertEqual(export_payload["id"], export_id)
                self.assertEqual(export_payload["status"], "succeeded")
                self.assertEqual(export_payload["snapshot"]["draft_version"], 1)
                self.assertIn("## 项目概述", export_payload["content_md"])
                self.assertIn("## 引用清单", export_payload["content_md"])

                exported_project_response = client.get(f"/api/v1/projects/{project_id}")
                self.assertEqual(exported_project_response.status_code, 200)
                self.assertEqual(exported_project_response.json()["data"]["status"], "EXPORTED")
            finally:
                if document_id and project_id:
                    client.delete(f"/api/v1/documents/{document_id}")

        state = asyncio.run(self._load_project_state(UUID(project_id)))
        stored_project = state["project"]
        stored_report = state["report"]
        stored_review_tasks = state["review_tasks"]
        stored_section_drafts = state["section_drafts"]
        stored_export = state["export"]

        self.assertIsNotNone(stored_project)
        self.assertIsNotNone(stored_report)
        self.assertIsNotNone(stored_export)
        self.assertEqual(stored_project.status, "EXPORTED")
        self.assertEqual(stored_report.status, "passed")
        self.assertEqual(len(stored_section_drafts), len(section_drafts_payload))
        self.assertGreaterEqual(len(stored_review_tasks), len(review_tasks))
        self.assertFalse(any(task.task_type == "final_review" for task in stored_review_tasks))
        self.assertEqual(stored_export.status, "succeeded")

    def test_v2_pipeline_blocks_export_when_compatibility_gap_is_not_reflected_in_sections(self) -> None:
        markdown = (
            "# 高炉鼓风机电机及 LCI 变频软起动系统改造方案\n\n"
            "本项目面向钢铁厂高炉鼓风机 10kV 同步电机场景，提供 LCI/SFC 变频软起动系统改造，并要求保留旁路工频切换能力。\n\n"
            "## 技术架构\n\n"
            "方案采用 LCI/SFC 变频软起动装置、本地 PLC 控制单元、DCS 接口、断路器反馈和励磁联锁组成的分层控制架构。\n\n"
            "## 硬件配置清单\n\n"
            "| 设备名称 | 型号 | 数量 |\n"
            "| --- | --- | --- |\n"
            "| LCI/SFC 变频软起动系统 | LCI-SFC-10kV | 1 |\n"
            "| 同步电机接口 | 10kV | 2 |\n"
            "| 本地控制单元 PLC | PLC-LOCAL | 1 |\n\n"
            "## 切换要求\n\n"
            "项目要求保留启动、同步切换与工频旁路边界。"
        )

        with self._make_client() as client:
            project_id: str | None = None
            document_id: str | None = None
            try:
                project_response = client.post(
                    "/api/v1/projects",
                    json={
                        "name": "某钢铁集团高炉鼓风机 LCI 软起动切换项目",
                        "product_line": "lci",
                        "industry": "钢铁",
                        "description": "为高炉鼓风机 10kV 同步电机配置 LCI/SFC 变频软起动系统，并保留工频切换能力。",
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

                extract_response = client.post(
                    f"/api/v1/projects/{project_id}/extract-requirement",
                    json={"rfp_document_id": document_id},
                )
                self.assertEqual(extract_response.status_code, 202)

                requirement_response = client.get(f"/api/v1/projects/{project_id}/requirement-card/latest")
                self.assertEqual(requirement_response.status_code, 200)
                requirement_card_id = requirement_response.json()["data"]["id"]

                retrieve_response = client.post(
                    f"/api/v1/projects/{project_id}/retrieve-evidence",
                    json={"top_k": 6, "doc_type": "historical_proposal"},
                )
                self.assertEqual(retrieve_response.status_code, 202)

                evidence_response = client.get(f"/api/v1/projects/{project_id}/evidence-bundles/latest")
                self.assertEqual(evidence_response.status_code, 200)
                evidence_bundle_id = evidence_response.json()["data"]["id"]

                solution_response = client.post(
                    f"/api/v1/projects/{project_id}/design-solution",
                    json={"force_refresh": True, "requirement_card_id": requirement_card_id},
                )
                self.assertEqual(solution_response.status_code, 200)
                solution_snapshot = solution_response.json()["data"]
                snapshot_id = solution_snapshot["id"]
                self.assertEqual(solution_snapshot["source_catalog_version"], "seed-20260419-v1")
                self.assertEqual(
                    solution_snapshot["selected_products"][0].get("model_number"),
                    "GBT.LCI.SO-A0606-211N465",
                )
                self.assertEqual(
                    solution_snapshot["selected_products"][0].get("source_material_key"),
                    "vera-46268861",
                )
                self.assertEqual(
                    solution_snapshot["selection_reason"]["catalog_model_matches"][0]["model_number"],
                    "GBT.LCI.SO-A0606-211N465",
                )
                self.assertEqual(
                    solution_snapshot["interface_plan"]["catalog_interface_entries"][0]["protocol"],
                    "Profibus-DP",
                )

                patched_selection_reason = dict(solution_snapshot["selection_reason"])
                patched_selection_reason["risk_flags"] = [
                    *(patched_selection_reason.get("risk_flags") or []),
                    "缺少推荐配套目录项：inspection_transfer_unit",
                ]
                patched_selection_reason["compatibility_actions"] = [
                    *(patched_selection_reason.get("compatibility_actions") or []),
                    {
                        "source_family_code": "lci_sync_drive",
                        "target_family_code": "transfer_unit_missing",
                        "relation_type": "recommended",
                        "condition": "检修不停机切换场景",
                        "applies": True,
                        "preferred_series_codes": ["inspection_transfer_unit"],
                        "optional_series_codes": [],
                        "covered_series_codes": [],
                        "added_series_codes": [],
                        "missing_series_codes": ["inspection_transfer_unit"],
                    },
                ]
                update_solution_response = client.patch(
                    f"/api/v1/projects/{project_id}/solutions/{snapshot_id}",
                    json={"selection_reason": patched_selection_reason},
                )
                self.assertEqual(update_solution_response.status_code, 200)

                confirm_solution_response = client.post(
                    f"/api/v1/projects/{project_id}/solutions/{snapshot_id}/confirm",
                    json={"confirmed_by_user": True, "confirmation_notes": "保留带 compatibility 缺口的校验样本"},
                )
                self.assertEqual(confirm_solution_response.status_code, 200)
                self.assertTrue(confirm_solution_response.json()["data"]["confirmed_by_user"])

                outline_response = client.post(
                    f"/api/v1/projects/{project_id}/generate-outline",
                    json={"requirement_card_id": requirement_card_id, "evidence_bundle_id": evidence_bundle_id},
                )
                self.assertEqual(outline_response.status_code, 202)

                latest_outline_response = client.get(f"/api/v1/projects/{project_id}/outlines/latest")
                self.assertEqual(latest_outline_response.status_code, 200)
                outline = latest_outline_response.json()["data"]

                outline_json = dict(outline["outline_json"])
                sections = [dict(section) for section in outline_json["sections"]]
                sections.append(
                    {
                        "section_id": "9",
                        "title": "启动及切换配套说明",
                        "purpose": "说明启动、切换与配套边界。",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "needs_human_review": False,
                        "section_class": "architecture",
                        "reuse_level": "medium",
                        "asset_required": False,
                        "parameter_sensitive": True,
                        "customer_specificity": "medium",
                        "generation_mode": "baseline",
                        "keywords": ["启动", "切换", "配套", "旁路"],
                        "children": [],
                    }
                )
                outline_json["sections"] = sections

                update_outline_response = client.patch(
                    f"/api/v1/projects/{project_id}/outlines/{outline['id']}",
                    json={"outline_json": outline_json},
                )
                self.assertEqual(update_outline_response.status_code, 200)

                approve_outline_response = client.post(
                    f"/api/v1/projects/{project_id}/outlines/{outline['id']}/approve",
                    json={"reviewer_notes": "用于 compatibility 缺口校验"},
                )
                self.assertEqual(approve_outline_response.status_code, 200)

                sections_response = client.post(f"/api/v1/projects/{project_id}/generate-sections", json={})
                self.assertEqual(sections_response.status_code, 202)

                list_sections_response = client.get(f"/api/v1/projects/{project_id}/sections")
                self.assertEqual(list_sections_response.status_code, 200)
                section_drafts_payload = list_sections_response.json()["data"]
                compatibility_section = next(item for item in section_drafts_payload if item["section_id"] == "9")

                update_section_response = client.patch(
                    f"/api/v1/projects/{project_id}/sections/{compatibility_section['section_id']}",
                    json={
                        "content_md": "## 启动及切换配套说明\n\n本章仅说明一般启动流程、控制原则和常规切换步骤，不展开当前项目的额外配套缺口或目录补齐要求。",
                        "citation_refs": compatibility_section["citation_refs"],
                        "assumptions": compatibility_section["assumptions"],
                    },
                )
                self.assertEqual(update_section_response.status_code, 200)

                validate_response = client.post(
                    f"/api/v1/projects/{project_id}/validate",
                    json={"draft_version": 1},
                )
                self.assertEqual(validate_response.status_code, 202)

                latest_validation_response = client.get(f"/api/v1/projects/{project_id}/validation/latest")
                self.assertEqual(latest_validation_response.status_code, 200)
                validation_report = latest_validation_response.json()["data"]
                self.assertEqual(validation_report["status"], "blocked")
                self.assertIn("VAL014", {item["code"] for item in validation_report["errors"]})

                review_tasks_response = client.get(f"/api/v1/projects/{project_id}/review-tasks")
                self.assertEqual(review_tasks_response.status_code, 200)
                review_tasks = review_tasks_response.json()["data"]
                compatibility_tasks = [
                    task
                    for task in review_tasks
                    if task["task_type"] == "content_review" and task["payload"].get("code") == "VAL014"
                ]
                self.assertTrue(compatibility_tasks)
                self.assertTrue(all(task["blocking_level"] == "P0" for task in compatibility_tasks))

                project_detail_response = client.get(f"/api/v1/projects/{project_id}")
                self.assertEqual(project_detail_response.status_code, 200)
                self.assertEqual(project_detail_response.json()["data"]["status"], "REVIEW_REQUIRED")

                export_response = client.post(
                    f"/api/v1/projects/{project_id}/export",
                    json={"format": "markdown"},
                )
                self.assertEqual(export_response.status_code, 400)
                self.assertIn("not exportable", export_response.json()["detail"])
            finally:
                if document_id and project_id:
                    client.delete(f"/api/v1/documents/{document_id}")

        state = asyncio.run(self._load_project_state(UUID(project_id)))
        stored_project = state["project"]
        stored_report = state["report"]
        stored_review_tasks = state["review_tasks"]

        self.assertIsNotNone(stored_project)
        self.assertIsNotNone(stored_report)
        self.assertEqual(stored_project.status, "REVIEW_REQUIRED")
        self.assertEqual(stored_report.status, "blocked")
        self.assertIn("VAL014", {item.get("code") for item in (stored_report.errors or [])})
        self.assertTrue(
            any(
                task.task_type == "content_review" and str((task.payload or {}).get("code") or "") == "VAL014"
                for task in stored_review_tasks
            )
        )


if __name__ == "__main__":
    unittest.main()
