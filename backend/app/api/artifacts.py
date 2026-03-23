from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.schemas.artifacts import (
    ClarificationResolveRequest,
    EvidenceBundleRead,
    EvidenceRetrieveRequest,
    JobAcceptedData,
    JobRead,
    RequirementCardRead,
    RequirementCardUpdateRequest,
    RequirementExtractRequest,
)
from app.schemas.common import APIResponse
from app.services.jobs import JobService
from app.services.requirement import RequirementService
from app.services.retrieval import EvidenceBundleService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


router = APIRouter()


def get_requirement_service() -> RequirementService:
    return RequirementService()


def get_evidence_bundle_service() -> EvidenceBundleService:
    return EvidenceBundleService()


def get_job_service() -> JobService:
    return JobService()


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
    service: EvidenceBundleService = Depends(get_evidence_bundle_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, bundle = await service.retrieve_evidence(
            session=session,
            project_id=project_id,
            requirement_card_id=payload.requirement_card_id,
            top_k=payload.top_k,
            doc_type=payload.doc_type,
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
            resource_id=bundle.id,
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
