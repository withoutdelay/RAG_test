from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
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
    SolutionConfirmRequest,
    SolutionDesignRequest,
    SolutionSnapshotRead,
    SolutionUpdateRequest,
    ValidationReportRead,
    ValidationTriggerRequest,
)
from app.schemas.common import APIResponse
from app.services.composition import OutlineService, SectionDraftService
from app.services.export import ExportService
from app.services.jobs import JobService
from app.services.requirement import RequirementService
from app.services.retrieval import EvidenceBundleService
from app.services.solution import SolutionService
from app.services.validation import ValidationService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


router = APIRouter()


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


def get_solution_service() -> SolutionService:
    return SolutionService()


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


@router.post("/projects/{project_id}/design-solution", response_model=APIResponse[SolutionSnapshotRead])
async def design_solution(
    project_id: UUID,
    payload: SolutionDesignRequest,
    session: AsyncSession = Depends(get_db_session),
    service: SolutionService = Depends(get_solution_service),
) -> APIResponse[SolutionSnapshotRead]:
    try:
        snapshot = await service.design_solution(
            session=session,
            project_id=project_id,
            requirement_card_id=payload.requirement_card_id,
            force_refresh=payload.force_refresh,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=SolutionSnapshotRead.model_validate(snapshot))


@router.get("/projects/{project_id}/solutions/latest", response_model=APIResponse[SolutionSnapshotRead])
async def get_latest_solution(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: SolutionService = Depends(get_solution_service),
) -> APIResponse[SolutionSnapshotRead]:
    try:
        snapshot = await service.get_latest_solution(session=session, project_id=project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=SolutionSnapshotRead.model_validate(snapshot))


@router.get("/projects/{project_id}/solutions", response_model=APIResponse[list[SolutionSnapshotRead]])
async def list_solutions(
    project_id: UUID,
    limit: int = Query(default=10, ge=1, le=30),
    session: AsyncSession = Depends(get_db_session),
    service: SolutionService = Depends(get_solution_service),
) -> APIResponse[list[SolutionSnapshotRead]]:
    rows = await service.list_solutions(session=session, project_id=project_id, limit=limit)
    return APIResponse(
        code=200,
        message="success",
        data=[SolutionSnapshotRead.model_validate(row) for row in rows],
    )


@router.patch("/projects/{project_id}/solutions/{snapshot_id}", response_model=APIResponse[SolutionSnapshotRead])
async def update_solution(
    project_id: UUID,
    snapshot_id: UUID,
    payload: SolutionUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: SolutionService = Depends(get_solution_service),
) -> APIResponse[SolutionSnapshotRead]:
    try:
        snapshot = await service.update_solution(
            session=session,
            project_id=project_id,
            snapshot_id=snapshot_id,
            payload=payload.model_dump(exclude_unset=True),
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=SolutionSnapshotRead.model_validate(snapshot))


@router.post("/projects/{project_id}/solutions/{snapshot_id}/confirm", response_model=APIResponse[SolutionSnapshotRead])
async def confirm_solution(
    project_id: UUID,
    snapshot_id: UUID,
    payload: SolutionConfirmRequest,
    session: AsyncSession = Depends(get_db_session),
    service: SolutionService = Depends(get_solution_service),
) -> APIResponse[SolutionSnapshotRead]:
    try:
        snapshot = await service.confirm_solution(
            session=session,
            project_id=project_id,
            snapshot_id=snapshot_id,
            confirmation_notes=payload.confirmation_notes,
            confirmed_by_user=payload.confirmed_by_user,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=SolutionSnapshotRead.model_validate(snapshot))


@router.post(
    "/projects/{project_id}/generate-outline",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_outline(
    project_id: UUID,
    payload: OutlineGenerateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: OutlineService = Depends(get_outline_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, outline = await service.generate_outline(
            session=session,
            project_id=project_id,
            requirement_card_id=payload.requirement_card_id,
            evidence_bundle_id=payload.evidence_bundle_id,
            instructions=payload.instructions,
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
            resource_id=outline.id,
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
    session: AsyncSession = Depends(get_db_session),
    service: SectionDraftService = Depends(get_section_draft_service),
) -> APIResponse[JobAcceptedData]:
    try:
        job, drafts = await service.generate_sections(
            session=session,
            project_id=project_id,
            outline_id=payload.outline_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    resource_id = drafts[0].id if drafts else None
    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=resource_id,
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
    try:
        job, draft = await service.regenerate_section(
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
    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=draft.id,
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
