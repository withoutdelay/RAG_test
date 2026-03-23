from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.generation_task import GenerationTask
from app.models.project import Project
from app.models.review_point import ReviewPoint
from app.schemas.generation import (
    GenerationStartRequest,
    OutlineConfirmRequest,
    SectionRewriteRequest,
)
from app.services.agents import WorkflowOrchestrator, WorkflowState, WorkflowStatus
from app.services.llm.client import LLMClient, LLMRequest, TaskType
from app.services.llm.prompts import (
    PLANNER_OUTLINE_SCHEMA,
    build_holistic_prompts,
    build_outline_prompts,
    build_rewrite_prompts,
    build_section_prompts,
)


class GenerationNotFoundError(LookupError):
    pass


class GenerationValidationError(ValueError):
    pass


class GenerationService:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        workflow: WorkflowOrchestrator | None = None,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.workflow = workflow or WorkflowOrchestrator()

    async def start_generation(
        self,
        *,
        session: AsyncSession,
        payload: GenerationStartRequest,
    ) -> GenerationTask:
        project = await session.get(Project, payload.project_id)
        if not project:
            raise GenerationValidationError("Project not found")

        document = await self._resolve_rfp_document(
            session=session,
            project_id=payload.project_id,
            rfp_document_id=payload.rfp_document_id,
        )

        task = GenerationTask(
            project_id=payload.project_id,
            rfp_document_id=document.id if document else None,
            status=WorkflowStatus.OUTLINING.value,
            global_params=payload.global_params,
            sections=[],
        )
        session.add(task)
        await session.flush()

        try:
            rfp_context = await self._load_rfp_context(session=session, document_id=document.id if document else None)
            state = self._build_workflow_state(
                task=task,
                project_name=project.name,
                rfp_context=rfp_context,
            )
            response = await self.workflow.generate_outline(state=state, instructions=payload.instructions)
            outline = self._parse_outline_response(
                response.content,
                project_name=project.name,
                instructions=payload.instructions,
            )
            task.outline = outline
            task.sections = self._seed_sections(outline)
            task.status = WorkflowStatus.OUTLINE_REVIEW.value
            task.total_tokens = state.token_usage
            task.estimated_cost = state.estimated_cost.quantize(Decimal("0.0001"))
            await session.commit()
            await session.refresh(task)
            return task
        except Exception:
            task.status = WorkflowStatus.FAILED.value
            await session.commit()
            raise

    async def get_task(self, *, session: AsyncSession, task_id: UUID) -> GenerationTask:
        task = await session.get(GenerationTask, task_id)
        if not task:
            raise GenerationNotFoundError("Generation task not found")
        return task

    async def confirm_outline(
        self,
        *,
        session: AsyncSession,
        task_id: UUID,
        payload: OutlineConfirmRequest,
    ) -> GenerationTask:
        task = await self.get_task(session=session, task_id=task_id)
        if payload.outline is not None:
            task.outline = self._normalize_outline(payload.outline)
            task.sections = self._seed_sections(task.outline)

        if not task.outline:
            raise GenerationValidationError("Task outline is not ready")

        project = await session.get(Project, task.project_id)
        if not project:
            raise GenerationValidationError("Project not found")

        task.status = WorkflowStatus.GENERATING.value
        rfp_context = await self._load_rfp_context(session=session, document_id=task.rfp_document_id)
        state = self._build_workflow_state(task=task, project_name=project.name, rfp_context=rfp_context)
        sections_state, review_drafts = await self.workflow.generate_sections(
            session=session,
            state=state,
            document_id=task.rfp_document_id,
        )
        task.sections = sections_state
        task.total_tokens = state.token_usage
        task.estimated_cost = state.estimated_cost.quantize(Decimal("0.0001"))

        await session.execute(delete(ReviewPoint).where(ReviewPoint.task_id == task.id))
        for review_draft in review_drafts:
            session.add(
                ReviewPoint(
                    task_id=task.id,
                    section_index=review_draft.section_index,
                    review_type=review_draft.review_type,
                    description=review_draft.description,
                    payload=review_draft.payload,
                    status="pending",
                )
            )

        if review_drafts:
            task.status = WorkflowStatus.SECTION_REVIEW.value
            task.final_markdown = None
            task.completed_at = None
        else:
            await self._finalize_task(session=session, task=task, project_name=project.name, state=state)

        await session.commit()
        await session.refresh(task)
        return task

    async def rewrite_section(
        self,
        *,
        session: AsyncSession,
        task_id: UUID,
        section_index: int,
        payload: SectionRewriteRequest,
    ) -> dict[str, Any]:
        task = await self.get_task(session=session, task_id=task_id)
        sections_state = list(task.sections or [])
        if section_index < 0 or section_index >= len(sections_state):
            raise GenerationValidationError("Section index out of range")

        section_state = sections_state[section_index]
        section_context = str(section_state.get("content") or "")
        project = await session.get(Project, task.project_id)
        if not project:
            raise GenerationValidationError("Project not found")

        state = self._build_workflow_state(task=task, project_name=project.name)
        response = await self.workflow.rewrite_section(
            state=state,
            section_index=section_index,
            section_context=section_context,
            selected_text=payload.selected_text,
            instruction=payload.instruction,
        )

        section_state["content"] = response.content
        section_state["status"] = "completed"
        section_state["rewrite_instruction"] = payload.instruction
        section_state["token_count"] = response.total_tokens
        sections_state[section_index] = section_state
        task.sections = sections_state
        task.final_markdown = self._compose_sections_markdown(
            sections_state,
            title=task.outline.get("title") if task.outline else None,
        )
        task.total_tokens = state.token_usage
        task.estimated_cost = state.estimated_cost.quantize(Decimal("0.0001"))
        await session.commit()

        return {
            "section_index": section_index,
            "new_content": response.content,
            "citations": [],
        }

    async def list_reviews(self, *, session: AsyncSession, task_id: UUID) -> list[ReviewPoint]:
        await self.get_task(session=session, task_id=task_id)
        result = await session.scalars(
            select(ReviewPoint)
            .where(ReviewPoint.task_id == task_id)
            .order_by(ReviewPoint.created_at.asc(), ReviewPoint.section_index.asc())
        )
        return result.all()

    async def approve_review(
        self,
        *,
        session: AsyncSession,
        review_id: UUID,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        review = await self._get_review(session=session, review_id=review_id)
        if review.status != "pending":
            raise GenerationValidationError("Review point is not pending")

        task = await self.get_task(session=session, task_id=review.task_id)
        review.status = "approved"
        review.user_feedback = feedback
        review.resolved_at = datetime.now(timezone.utc)
        self._set_section_status(task=task, section_index=review.section_index, status="approved")
        await session.flush()

        pending_count = await self._count_pending_reviews(session=session, task_id=task.id)
        if pending_count == 0:
            project = await session.get(Project, task.project_id)
            if not project:
                raise GenerationValidationError("Project not found")
            state = self._build_workflow_state(task=task, project_name=project.name)
            await self._finalize_task(session=session, task=task, project_name=project.name, state=state)
        else:
            task.status = WorkflowStatus.SECTION_REVIEW.value

        await session.commit()
        return {
            "review_id": review.id,
            "task_id": task.id,
            "status": review.status,
            "task_status": task.status,
            "section_index": review.section_index,
            "follow_up_review_id": None,
        }

    async def reject_review(
        self,
        *,
        session: AsyncSession,
        review_id: UUID,
        feedback: str,
    ) -> dict[str, Any]:
        review = await self._get_review(session=session, review_id=review_id)
        if review.status != "pending":
            raise GenerationValidationError("Review point is not pending")

        task = await self.get_task(session=session, task_id=review.task_id)
        project = await session.get(Project, task.project_id)
        if not project:
            raise GenerationValidationError("Project not found")

        sections_state = list(task.sections or [])
        if review.section_index < 0 or review.section_index >= len(sections_state):
            raise GenerationValidationError("Section index out of range")

        rfp_context = await self._load_rfp_context(session=session, document_id=task.rfp_document_id)
        state = self._build_workflow_state(task=task, project_name=project.name, rfp_context=rfp_context)
        current_content = str(sections_state[review.section_index].get("content") or "")
        response = await self.workflow.rewrite_section(
            state=state,
            section_index=review.section_index,
            section_context=current_content,
            selected_text=current_content,
            instruction=feedback,
        )

        sections_state[review.section_index]["content"] = response.content
        sections_state[review.section_index]["status"] = "reviewing"
        sections_state[review.section_index]["token_count"] = response.total_tokens
        sections_state[review.section_index]["rewrite_instruction"] = feedback
        task.sections = sections_state
        task.total_tokens = state.token_usage
        task.estimated_cost = state.estimated_cost.quantize(Decimal("0.0001"))
        task.status = WorkflowStatus.SECTION_REVIEW.value
        task.final_markdown = None
        task.completed_at = None

        review.status = "revised"
        review.user_feedback = feedback
        review.resolved_at = datetime.now(timezone.utc)

        follow_up_review = ReviewPoint(
            task_id=task.id,
            section_index=review.section_index,
            review_type=review.review_type,
            description=f"章节《{sections_state[review.section_index].get('title', f'章节 {review.section_index + 1}')}》已根据反馈重写，请重新确认。",
            payload={
                "previous_review_id": str(review.id),
                "instruction": feedback,
                "section_title": sections_state[review.section_index].get("title"),
                "section_preview": response.content[:240],
            },
            status="pending",
        )
        session.add(follow_up_review)
        await session.commit()

        return {
            "review_id": review.id,
            "task_id": task.id,
            "status": review.status,
            "task_status": task.status,
            "section_index": review.section_index,
            "follow_up_review_id": follow_up_review.id,
        }

    async def iter_stream_events(
        self,
        *,
        session: AsyncSession,
        task_id: UUID,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        task = await self.get_task(session=session, task_id=task_id)

        if task.outline:
            yield "outline_ready", {"outline": task.outline}

        for section in task.sections or []:
            title = section.get("title", "未命名章节")
            yield "section_start", {"section_index": section.get("index", 0), "title": title}
            content = str(section.get("content") or "")
            for chunk in _chunk_text(content, size=180):
                yield "section_chunk", {"section_index": section.get("index", 0), "delta": chunk}
            if content:
                yield "section_done", {
                    "section_index": section.get("index", 0),
                    "token_count": int(section.get("token_count") or 0),
                }

        reviews = await self.list_reviews(session=session, task_id=task_id)
        for review in reviews:
            if review.status == "pending":
                yield "review_required", {
                    "review_point_id": str(review.id),
                    "section_index": review.section_index,
                    "type": review.review_type,
                    "description": review.description,
                }

        if task.status == WorkflowStatus.COMPLETED.value:
            yield "generation_complete", {
                "task_id": str(task.id),
                "total_tokens": task.total_tokens,
                "estimated_cost": float(task.estimated_cost or 0),
            }
        elif task.status == WorkflowStatus.FAILED.value:
            yield "error", {"message": "生成任务失败，请检查后重试。"}

    async def _resolve_rfp_document(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        rfp_document_id: UUID | None,
    ) -> Document | None:
        if rfp_document_id is None:
            return None
        document = await session.get(Document, rfp_document_id)
        if not document or document.project_id != project_id:
            raise GenerationValidationError("RFP document not found in the project")
        return document

    async def _load_rfp_context(
        self,
        *,
        session: AsyncSession,
        document_id: UUID | None,
        max_chunks: int = 5,
    ) -> str:
        if document_id is None:
            return ""
        result = await session.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index.asc())
            .limit(max_chunks)
        )
        chunks = result.all()
        return "\n\n".join(chunk.content for chunk in chunks)

    def _parse_outline_response(self, text: str, *, project_name: str, instructions: str) -> dict:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            payload = json.loads(match.group(0)) if match else {}

        if not payload:
            payload = self._fallback_outline(project_name=project_name, instructions=instructions)
        return self._normalize_outline(payload)

    def _normalize_outline(self, payload: dict) -> dict:
        title = str(payload.get("title") or "技术方案")
        raw_sections = payload.get("sections") or []
        sections: list[dict[str, Any]] = []
        for index, section in enumerate(raw_sections):
            section_title = str(section.get("title") or f"章节 {index + 1}")
            sections.append(
                {
                    "index": index,
                    "title": section_title,
                    "description": str(section.get("description") or ""),
                    "keywords": [str(item) for item in section.get("keywords") or []],
                    "subsections": [
                        {
                            "index": sub_index,
                            "title": str(subsection.get("title") or f"{section_title}-{sub_index + 1}"),
                            "description": str(subsection.get("description") or ""),
                            "keywords": [str(item) for item in subsection.get("keywords") or []],
                        }
                        for sub_index, subsection in enumerate(section.get("subsections") or [])
                    ],
                }
            )
        if not sections:
            return self._fallback_outline(project_name=title, instructions="")
        return {"title": title, "sections": sections}

    def _fallback_outline(self, *, project_name: str, instructions: str) -> dict:
        return {
            "title": f"{project_name}技术方案",
            "sections": [
                {
                    "index": 0,
                    "title": "项目概述",
                    "description": f"围绕{project_name}的建设背景、目标与范围展开。",
                    "keywords": ["项目概述", "建设目标", instructions[:20] or "项目背景"],
                    "subsections": [],
                },
                {
                    "index": 1,
                    "title": "需求分析",
                    "description": "梳理业务需求、技术约束和关键指标。",
                    "keywords": ["需求分析", "关键指标"],
                    "subsections": [],
                },
                {
                    "index": 2,
                    "title": "技术架构",
                    "description": "说明系统总体方案、功能模块与部署关系。",
                    "keywords": ["技术架构", "系统设计"],
                    "subsections": [],
                },
                {
                    "index": 3,
                    "title": "硬件配置清单",
                    "description": "列出关键设备与容量建议。",
                    "keywords": ["硬件配置", "设备清单"],
                    "subsections": [],
                },
                {
                    "index": 4,
                    "title": "实施排期",
                    "description": "规划实施阶段、交付节点与验收安排。",
                    "keywords": ["实施计划", "交付"],
                    "subsections": [],
                },
                {
                    "index": 5,
                    "title": "售后服务",
                    "description": "描述运维、培训和质保承诺。",
                    "keywords": ["售后服务", "培训", "质保"],
                    "subsections": [],
                },
            ],
        }

    def _seed_sections(self, outline: dict) -> list[dict[str, Any]]:
        return [
            {
                "index": section.get("index", index),
                "title": section.get("title"),
                "description": section.get("description"),
                "keywords": section.get("keywords", []),
                "status": "pending",
                "content": "",
                "token_count": 0,
            }
            for index, section in enumerate(outline.get("sections", []))
        ]

    def _compose_sections_markdown(self, sections: list[dict[str, Any]], title: str | None = None) -> str:
        body = "\n\n".join(str(section.get("content") or "").strip() for section in sections if section.get("content"))
        if not title:
            return body
        return f"# {title}\n\n{body}".strip()

    def _build_workflow_state(
        self,
        *,
        task: GenerationTask,
        project_name: str,
        rfp_context: str = "",
    ) -> WorkflowState:
        completed_sections = {
            int(section.get("index", index)): str(section.get("content") or "")
            for index, section in enumerate(task.sections or [])
            if section.get("content")
        }
        return WorkflowState(
            task_id=str(task.id),
            project_name=project_name,
            global_params=task.global_params or {},
            outline=task.outline,
            completed_sections=completed_sections,
            token_usage=int(task.total_tokens or 0),
            estimated_cost=_decimal_cost(task.estimated_cost or Decimal("0")),
            rfp_context=rfp_context,
        )

    async def _get_review(self, *, session: AsyncSession, review_id: UUID) -> ReviewPoint:
        review = await session.get(ReviewPoint, review_id)
        if not review:
            raise GenerationNotFoundError("Review point not found")
        return review

    async def _count_pending_reviews(self, *, session: AsyncSession, task_id: UUID) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(ReviewPoint)
            .where(ReviewPoint.task_id == task_id, ReviewPoint.status == "pending")
        )
        return int(count or 0)

    async def _finalize_task(
        self,
        *,
        session: AsyncSession,
        task: GenerationTask,
        project_name: str,
        state: WorkflowState | None = None,
    ) -> None:
        workflow_state = state or self._build_workflow_state(task=task, project_name=project_name)
        task.status = WorkflowStatus.FINALIZING.value
        response = await self.workflow.finalize(state=workflow_state)
        task.final_markdown = response.content
        task.total_tokens = workflow_state.token_usage
        task.estimated_cost = workflow_state.estimated_cost.quantize(Decimal("0.0001"))
        task.status = WorkflowStatus.COMPLETED.value
        task.completed_at = datetime.now(timezone.utc)

    def _set_section_status(self, *, task: GenerationTask, section_index: int, status: str) -> None:
        sections_state = list(task.sections or [])
        if 0 <= section_index < len(sections_state):
            sections_state[section_index]["status"] = status
            task.sections = sections_state


def _chunk_text(text: str, *, size: int) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]


def _decimal_cost(value: Decimal | float | int) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.0001"))
    return Decimal(str(value)).quantize(Decimal("0.0001"))
