from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.generation import get_generation_service
from app.db import get_db_session
from app.schemas.common import APIResponse
from app.schemas.review import ReviewActionData, ReviewActionRequest, ReviewPointRead, ReviewRejectRequest
from app.services.generation import GenerationNotFoundError, GenerationService, GenerationValidationError
from app.services.generation import LegacyGenerationDisabledError


router = APIRouter()


@router.get(
    "/generation/{task_id}/reviews",
    response_model=APIResponse[list[ReviewPointRead]],
    include_in_schema=False,
)
async def list_generation_reviews(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[list[ReviewPointRead]]:
    try:
        reviews = await service.list_reviews(session=session, task_id=task_id)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(
        code=200,
        message="success",
        data=[ReviewPointRead.model_validate(review) for review in reviews],
    )


@router.post(
    "/review/{review_id}/approve",
    response_model=APIResponse[ReviewActionData],
    include_in_schema=False,
)
async def approve_review(
    review_id: UUID,
    payload: ReviewActionRequest,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[ReviewActionData]:
    try:
        result = await service.approve_review(session=session, review_id=review_id, feedback=payload.feedback)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ReviewActionData.model_validate(result))


@router.post(
    "/review/{review_id}/reject",
    response_model=APIResponse[ReviewActionData],
    include_in_schema=False,
)
async def reject_review(
    review_id: UUID,
    payload: ReviewRejectRequest,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[ReviewActionData]:
    try:
        result = await service.reject_review(session=session, review_id=review_id, feedback=payload.feedback)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=ReviewActionData.model_validate(result))
