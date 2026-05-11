from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session, get_session_factory
from app.models.job import Job
from app.models.project import Project
from app.schemas.artifacts import (
    ClarificationResolveRequest,
    EvidenceBundleRead,
    EvidenceRetrieveRequest,
    ExportRead,
    ExportRequest,
    JobAcceptedData,
    JobRead,
    OutlineApproveRequest,
    OutlineGenerateRequest,
    OutlineUpdateRequest,
    ProposalOutlineRead,
    ReviewTaskRead,
    ReviewTaskResolveRequest,
    RequirementCardRead,
    RequirementCardUpdateRequest,
    RequirementExtractRequest,
    SectionDraftRead,
    SectionDraftUpdateRequest,
    SectionGenerateRequest,
    SectionRegenerateRequest,
    ValidationReportRead,
    ValidationTriggerRequest,
)
from app.schemas.common import APIResponse
from app.services.composition import OutlineService, SectionDraftService
from app.services.export import ExportService
from app.services.jobs import JobService
from app.services.requirement import RequirementService
from app.services.retrieval import EvidenceBundleService
from app.services.task_queue import get_background_task_queue, get_background_task_queue_manager
from app.services.validation import ValidationService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.utils.object_storage import get_object_storage


router = APIRouter()
logger = logging.getLogger(__name__)


def get_requirement_service() -> RequirementService:
    return RequirementService()


def get_evidence_bundle_service() -> EvidenceBundleService:
    return EvidenceBundleService()


def get_outline_service() -> OutlineService:
    return OutlineService()


def get_section_draft_service() -> SectionDraftService:
    return SectionDraftService()


def get_job_service() -> JobService:
    return JobService()


def get_validation_service() -> ValidationService:
    return ValidationService()


def get_export_service() -> ExportService:
    return ExportService()


async def _run_generate_sections_job(
    service: SectionDraftService,
    job_id: UUID,
    project_id: UUID,
    outline_id: UUID | None,
) -> None:
    async with get_session_factory()() as session:
        try:
            await service.generate_sections(
                session=session,
                project_id=project_id,
                outline_id=outline_id,
                job_id=job_id,
            )
        except Exception as exc:
            await session.rollback()
            logger.exception("Section generation job failed", extra={"job_id": str(job_id), "project_id": str(project_id)})
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error_code = exc.__class__.__name__[:50]
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": str(exc),
                    "progress": {
                        **((job.output_ref or {}).get("progress") or {}),
                        "stage": "failed",
                    },
                }
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()


async def _run_generate_outline_job(
    service: OutlineService,
    job_id: UUID,
    project_id: UUID,
    requirement_card_id: UUID | None,
    evidence_bundle_id: UUID | None,
    instructions: str | None,
) -> None:
    async with get_session_factory()() as session:
        job = await session.get(Job, job_id)
        if job is not None:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.output_ref = {"progress": {"stage": "running"}}
            await session.commit()
        try:
            delegated_job, outline = await service.generate_outline(
                session=session,
                project_id=project_id,
                requirement_card_id=requirement_card_id,
                evidence_bundle_id=evidence_bundle_id,
                instructions=instructions,
            )
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "succeeded"
                job.output_ref = {
                    "outline_id": str(outline.id),
                    "delegated_job_id": str(delegated_job.id),
                    "progress": {"stage": "completed"},
                }
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("Outline generation job failed", extra={"job_id": str(job_id), "project_id": str(project_id)})
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error_code = exc.__class__.__name__[:50]
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": str(exc),
                    "progress": {"stage": "failed"},
                }
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()


async def _mark_job_failed(session: AsyncSession, job_id: UUID, exc: Exception) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = "failed"
    job.error_code = exc.__class__.__name__[:50]
    job.output_ref = {
        **(job.output_ref or {}),
        "error": str(exc),
        "progress": {
            **((job.output_ref or {}).get("progress") or {}),
            "stage": "failed",
        },
    }
    job.completed_at = datetime.now(timezone.utc)
    await session.commit()


async def _run_retrieve_evidence_job(
    job_id: UUID,
    project_id: UUID,
    requirement_card_id: UUID | None,
    top_k: int,
    doc_type: str | None,
) -> None:
    async with get_session_factory()() as session:
        try:
            await EvidenceBundleService().retrieve_evidence(
                session=session,
                project_id=project_id,
                requirement_card_id=requirement_card_id,
                top_k=top_k,
                doc_type=doc_type,
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("Evidence retrieval job failed", extra={"job_id": str(job_id), "project_id": str(project_id)})
            await _mark_job_failed(session, job_id, exc)


async def _run_regenerate_section_job(
    job_id: UUID,
    project_id: UUID,
    section_id: str,
    outline_id: UUID | None,
    preferred_citation_ids: list[str] | None,
) -> None:
    async with get_session_factory()() as session:
        try:
            await SectionDraftService().regenerate_section(
                session=session,
                project_id=project_id,
                section_id=section_id,
                outline_id=outline_id,
                preferred_citation_ids=preferred_citation_ids,
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception(
                "Section regeneration job failed",
                extra={"job_id": str(job_id), "project_id": str(project_id), "section_id": section_id},
            )
            await _mark_job_failed(session, job_id, exc)


@router.post(
    "/projects/{project_id}/extract-requirement",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_requirement(
    project_id: UUID,
    payload: RequirementExtractRequest,
    session: AsyncSession = Depends(get_db_session),
    service: RequirementService = Depends(get_requirement_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, card = await service.extract_requirement_card(
            session=session,
            project_id=project_id,
            rfp_document_id=payload.rfp_document_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=card.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/requirement-card/latest", response_model=APIResponse[RequirementCardRead])
async def get_latest_requirement_card(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: RequirementService = Depends(get_requirement_service),
) -> APIResponse[RequirementCardRead]:
    try:
        card = await service.get_latest_requirement_card(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=RequirementCardRead.model_validate(card))


@router.patch("/projects/{project_id}/requirement-card/{card_id}", response_model=APIResponse[RequirementCardRead])
async def update_requirement_card(
    project_id: UUID,
    card_id: UUID,
    payload: RequirementCardUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: RequirementService = Depends(get_requirement_service),
) -> APIResponse[RequirementCardRead]:
    try:
        card = await service.update_requirement_card(
            session=session,
            project_id=project_id,
            card_id=card_id,
            content=payload.content,
            missing_items=payload.missing_items,
            blocking_items=payload.blocking_items,
            confirmed_by_user=payload.confirmed_by_user,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=RequirementCardRead.model_validate(card))


@router.post("/projects/{project_id}/clarifications/{item_id}/resolve", response_model=APIResponse[RequirementCardRead])
async def resolve_clarification(
    project_id: UUID,
    item_id: str,
    payload: ClarificationResolveRequest,
    session: AsyncSession = Depends(get_db_session),
    service: RequirementService = Depends(get_requirement_service),
) -> APIResponse[RequirementCardRead]:
    try:
        card = await service.resolve_clarification(
            session=session,
            project_id=project_id,
            item_id=item_id,
            resolution=payload.resolution,
            confirmed_by_user=payload.confirmed_by_user,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=RequirementCardRead.model_validate(card))


@router.post(
    "/projects/{project_id}/retrieve-evidence",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retrieve_evidence(
    project_id: UUID,
    payload: EvidenceRetrieveRequest,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[JobAcceptedData]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    queue = get_background_task_queue("interactive")
    dedupe_key = (
        f"retrieve:{project_id}:"
        f"{payload.requirement_card_id or 'latest'}:"
        f"{payload.doc_type or 'historical_proposal'}:"
        f"{payload.top_k}"
    )
    active_job_id = queue.active_job_id(dedupe_key)
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    job = Job(
        project_id=project_id,
        job_type="retrieve",
        status="queued",
        trace_id=uuid.uuid4().hex,
        input_ref={
            "project_id": str(project_id),
            "requirement_card_id": str(payload.requirement_card_id) if payload.requirement_card_id else None,
            "top_k": payload.top_k,
            "doc_type": payload.doc_type,
        },
        output_ref={"progress": {"stage": "queued"}},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    submitted_job_id = queue.submit(
        job_id=job.id,
        job_type="retrieve",
        label=f"retrieve:{project_id}",
        dedupe_key=dedupe_key,
        priority=20,
        run=lambda: _run_retrieve_evidence_job(
            job.id,
            project_id,
            payload.requirement_card_id,
            payload.top_k,
            payload.doc_type,
        ),
    )
    if submitted_job_id != job.id:
        job.status = "failed"
        job.error_code = "DuplicateQueuedJob"
        job.output_ref = {
            **(job.output_ref or {}),
            "error": f"Duplicate retrieval job already active: {submitted_job_id}",
            "progress": {"stage": "deduplicated"},
        }
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        active_job = await session.get(Job, submitted_job_id)
        if active_job is not None:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=None,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/evidence-bundles/latest", response_model=APIResponse[EvidenceBundleRead])
async def get_latest_evidence_bundle(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: EvidenceBundleService = Depends(get_evidence_bundle_service),
) -> APIResponse[EvidenceBundleRead]:
    try:
        bundle = await service.get_latest_evidence_bundle(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=EvidenceBundleRead.model_validate(bundle))


@router.post(
    "/projects/{project_id}/generate-outline",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_outline(
    project_id: UUID,
    payload: OutlineGenerateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    service: OutlineService = Depends(get_outline_service),
) -> APIResponse[JobAcceptedData]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    queue = get_background_task_queue("interactive")
    dedupe_key = f"generate-outline:{project_id}"
    active_job_id = queue.active_job_id(dedupe_key)
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    job = Job(
        project_id=project_id,
        job_type="outline",
        status="queued",
        trace_id=uuid.uuid4().hex,
        input_ref={
            "project_id": str(project_id),
            "requirement_card_id": str(payload.requirement_card_id) if payload.requirement_card_id else None,
            "evidence_bundle_id": str(payload.evidence_bundle_id) if payload.evidence_bundle_id else None,
        },
        output_ref={"progress": {"stage": "queued"}},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    submitted_job_id = queue.submit(
        job_id=job.id,
        job_type="outline",
        label=f"generate-outline:{project_id}",
        dedupe_key=dedupe_key,
        priority=20,
        run=lambda: _run_generate_outline_job(
            service,
            job.id,
            project_id,
            payload.requirement_card_id,
            payload.evidence_bundle_id,
            payload.instructions,
        ),
    )
    if submitted_job_id != job.id:
        job.status = "failed"
        job.error_code = "DuplicateQueuedJob"
        job.output_ref = {
            **(job.output_ref or {}),
            "error": f"Duplicate outline job already active: {submitted_job_id}",
            "progress": {"stage": "deduplicated"},
        }
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        active_job = await session.get(Job, submitted_job_id)
        if active_job is not None:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=None,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/outlines/latest", response_model=APIResponse[ProposalOutlineRead])
async def get_latest_outline(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: OutlineService = Depends(get_outline_service),
) -> APIResponse[ProposalOutlineRead]:
    try:
        outline = await service.get_latest_outline(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ProposalOutlineRead.model_validate(outline))


@router.patch("/projects/{project_id}/outlines/{outline_id}", response_model=APIResponse[ProposalOutlineRead])
async def update_outline(
    project_id: UUID,
    outline_id: UUID,
    payload: OutlineUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: OutlineService = Depends(get_outline_service),
) -> APIResponse[ProposalOutlineRead]:
    try:
        outline = await service.update_outline(
            session=session,
            project_id=project_id,
            outline_id=outline_id,
            outline_json=payload.outline_json,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ProposalOutlineRead.model_validate(outline))


@router.post("/projects/{project_id}/outlines/{outline_id}/approve", response_model=APIResponse[ProposalOutlineRead])
async def approve_outline(
    project_id: UUID,
    outline_id: UUID,
    payload: OutlineApproveRequest,
    session: AsyncSession = Depends(get_db_session),
    service: OutlineService = Depends(get_outline_service),
) -> APIResponse[ProposalOutlineRead]:
    try:
        outline = await service.approve_outline(
            session=session,
            project_id=project_id,
            outline_id=outline_id,
            outline_json=payload.outline_json,
            reviewer_notes=payload.reviewer_notes,
            approved_by_user=payload.approved_by_user,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ProposalOutlineRead.model_validate(outline))


@router.post(
    "/projects/{project_id}/generate-sections",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_sections(
    project_id: UUID,
    payload: SectionGenerateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    service: SectionDraftService = Depends(get_section_draft_service),
) -> APIResponse[JobAcceptedData]:
    queue = get_background_task_queue("interactive")
    dedupe_key = f"generate-sections:{project_id}:{payload.outline_id or 'latest'}"
    active_job_id = queue.active_job_id(dedupe_key)
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    try:
        job = await service.create_generate_sections_job(
            session=session,
            project_id=project_id,
            outline_id=payload.outline_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    submitted_job_id = queue.submit(
        job_id=job.id,
        job_type="generate_draft",
        label=f"generate-sections:{project_id}",
        dedupe_key=dedupe_key,
        priority=20,
        run=lambda: _run_generate_sections_job(service, job.id, project_id, payload.outline_id),
    )
    if submitted_job_id != job.id:
        job.status = "failed"
        job.error_code = "DuplicateQueuedJob"
        job.output_ref = {
            **(job.output_ref or {}),
            "error": f"Duplicate generate-sections job already active: {submitted_job_id}",
            "progress": {"stage": "deduplicated"},
        }
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        active_job = await session.get(Job, submitted_job_id)
        if active_job is not None:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=None,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/sections", response_model=APIResponse[list[SectionDraftRead]])
async def list_section_drafts(
    project_id: UUID,
    draft_version: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_db_session),
    service: SectionDraftService = Depends(get_section_draft_service),
) -> APIResponse[list[SectionDraftRead]]:
    try:
        drafts = await service.list_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=draft_version,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=[SectionDraftRead.model_validate(draft) for draft in drafts])


@router.post(
    "/projects/{project_id}/sections/{section_id}/regenerate",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_section(
    project_id: UUID,
    section_id: str,
    payload: SectionRegenerateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: SectionDraftService = Depends(get_section_draft_service),
) -> APIResponse[JobAcceptedData]:
    queue = get_background_task_queue("interactive")
    dedupe_key = f"generate-section:{project_id}:{section_id}"
    active_job_id = queue.active_job_id(dedupe_key)
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    try:
        job = await service.create_regenerate_section_job(
            session=session,
            project_id=project_id,
            section_id=section_id,
            outline_id=payload.outline_id,
            preferred_citation_ids=payload.preferred_citation_ids,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    submitted_job_id = queue.submit(
        job_id=job.id,
        job_type="generate_section",
        label=f"generate-section:{project_id}:{section_id}",
        dedupe_key=dedupe_key,
        priority=10,
        run=lambda: _run_regenerate_section_job(
            job.id,
            project_id,
            section_id,
            payload.outline_id,
            payload.preferred_citation_ids,
        ),
    )
    if submitted_job_id != job.id:
        job.status = "failed"
        job.error_code = "DuplicateQueuedJob"
        job.output_ref = {
            **(job.output_ref or {}),
            "error": f"Duplicate section regeneration job already active: {submitted_job_id}",
            "progress": {"stage": "deduplicated"},
        }
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        active_job = await session.get(Job, submitted_job_id)
        if active_job is not None:
            return APIResponse(
                code=202,
                message="success",
                data=JobAcceptedData(
                    job_id=active_job.id,
                    status=active_job.status,
                    resource_id=None,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=None,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.patch("/projects/{project_id}/sections/{section_id}", response_model=APIResponse[SectionDraftRead])
async def update_section(
    project_id: UUID,
    section_id: str,
    payload: SectionDraftUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: SectionDraftService = Depends(get_section_draft_service),
) -> APIResponse[SectionDraftRead]:
    try:
        draft = await service.update_section(
            session=session,
            project_id=project_id,
            section_id=section_id,
            content_md=payload.content_md,
            citation_refs=payload.citation_refs,
            assumptions=payload.assumptions,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=SectionDraftRead.model_validate(draft))


@router.post(
    "/projects/{project_id}/validate",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_project(
    project_id: UUID,
    payload: ValidationTriggerRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ValidationService = Depends(get_validation_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, report = await service.validate_project(
            session=session,
            project_id=project_id,
            draft_version=payload.draft_version,
            outline_id=payload.outline_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=report.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/validation/latest", response_model=APIResponse[ValidationReportRead])
async def get_latest_validation_report(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ValidationService = Depends(get_validation_service),
) -> APIResponse[ValidationReportRead]:
    try:
        report = await service.get_latest_validation_report(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ValidationReportRead.model_validate(report))


@router.get("/projects/{project_id}/review-tasks", response_model=APIResponse[list[ReviewTaskRead]])
async def list_review_tasks(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ValidationService = Depends(get_validation_service),
) -> APIResponse[list[ReviewTaskRead]]:
    try:
        tasks = await service.list_review_tasks(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=[ReviewTaskRead.model_validate(task) for task in tasks])


@router.post("/projects/{project_id}/review-tasks/{task_id}/resolve", response_model=APIResponse[ReviewTaskRead])
async def resolve_review_task(
    project_id: UUID,
    task_id: UUID,
    payload: ReviewTaskResolveRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ValidationService = Depends(get_validation_service),
) -> APIResponse[ReviewTaskRead]:
    try:
        task = await service.resolve_review_task(
            session=session,
            project_id=project_id,
            task_id=task_id,
            resolution=payload.resolution,
            status=payload.status,
            assignee_user_id=payload.assignee_user_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ReviewTaskRead.model_validate(task))


@router.post(
    "/projects/{project_id}/export",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_project(
    project_id: UUID,
    payload: ExportRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ExportService = Depends(get_export_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, export_record = await service.export_project(
            session=session,
            project_id=project_id,
            format=payload.format,
            force=payload.force,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=export_record.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.get("/projects/{project_id}/exports/latest", response_model=APIResponse[ExportRead])
async def get_latest_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ExportService = Depends(get_export_service),
) -> APIResponse[ExportRead]:
    try:
        export_record = await service.get_latest_export(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ExportRead.model_validate(export_record))


@router.get("/projects/{project_id}/exports/latest/download")
async def download_latest_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ExportService = Depends(get_export_service),
) -> FileResponse:
    try:
        export_record = await service.get_latest_export(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    storage = get_object_storage()
    materialized = storage.materialize(export_record.storage_path)
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if export_record.file_type == "docx"
        else "text/markdown; charset=utf-8"
    )
    return FileResponse(
        path=materialized.path,
        media_type=media_type,
        filename=export_record.file_name,
        background=BackgroundTask(materialized.cleanup),
    )


@router.get("/jobs/queue", response_model=APIResponse[dict[str, Any]])
async def get_job_queue_status() -> APIResponse[dict[str, Any]]:
    return APIResponse(code=200, message="success", data=get_background_task_queue_manager().status())


# Job types whose runtime state lives only in the in-memory ``interactive``
# queue and the running asyncio event loop.  After a backend restart we cannot
# safely re-execute them (the orchestrator state, evidence bundles and section
# drafts may have moved on, and customers may have already retried) so we fail
# them with a stable ``error_code`` and a human-readable Chinese message that
# the frontend ``/api/v1/jobs/{job_id}`` poller surfaces verbatim.
#
# Review R3 #3 fix: the full-draft path enqueues a queue task with
# ``job_type="generate_draft"`` (see ``generate_sections`` in this file) but
# ``SectionDraftService.start_section_generation_job`` writes the DB row with
# ``job_type="generate"``.  The DB-level recovery scan must therefore cover
# *both* names — ``"generate_draft"`` for any future code path that aligns on
# the queue name, and ``"generate"`` for the actual rows produced today.
INTERACTIVE_RECOVERABLE_JOB_TYPES: tuple[str, ...] = (
    "retrieve",
    "outline",
    "generate_draft",
    "generate",
    "generate_section",
)


async def recover_interactive_jobs_on_startup() -> dict[str, int]:
    """Mark stale interactive-queue jobs as failed when the backend restarts.

    Review R2 #3 fix: ``recover_document_parse_jobs_on_startup`` already covers
    ``document_parse`` and ``rfp_light_parse``, but ``retrieve``, ``outline``,
    ``generate_draft`` and ``generate_section`` jobs would otherwise stay in
    ``queued`` / ``running`` state forever (the in-memory queue is gone, the
    asyncio coroutine is gone, and the frontend polls until it gives up).

    We deliberately do NOT requeue these jobs because:

    1. Inputs may reference orchestrator-resident state (e.g. ``Project``
       fields and section drafts the user already edited after the crash).
    2. The user has very likely re-triggered the action via the UI; a silent
       requeue would race with the new run and produce inconsistent output.
    3. Failing fast lets the frontend show a clear error and a retry CTA.

    The returned summary mirrors :func:`recover_document_parse_jobs_on_startup`
    so callers (``app.main.lifespan``) can log a single line per recovery
    function with consistent shape.
    """

    failed_stale = 0
    skipped_terminal = 0

    async with get_session_factory()() as session:
        result = await session.scalars(
            select(Job)
            .where(Job.job_type.in_(INTERACTIVE_RECOVERABLE_JOB_TYPES))
            .where(Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at.asc())
        )
        jobs = result.all()
        for job in jobs:
            previous_status = job.status
            job.status = "failed"
            job.error_code = "StaleAfterRestart"
            job.output_ref = {
                **(job.output_ref or {}),
                "error": (
                    "后端重启后，未完成的生成任务已自动失败。请在前端重新触发生成。"
                ),
                "progress": {
                    "stage": "failed",
                    "reason": "stale_after_restart",
                    "previous_status": previous_status,
                },
            }
            job.completed_at = datetime.now(timezone.utc)
            failed_stale += 1
        await session.commit()

    return {
        "failed_stale": failed_stale,
        "skipped_terminal": skipped_terminal,
    }


@router.get("/jobs/{job_id}", response_model=APIResponse[JobRead])
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: JobService = Depends(get_job_service),
) -> APIResponse[JobRead]:
    try:
        job = await service.get_job(session=session, job_id=job_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=JobRead.model_validate(job))
