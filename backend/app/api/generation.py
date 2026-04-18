from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.schemas.common import APIResponse
from app.schemas.generation import (
    GenerationStartData,
    GenerationStartRequest,
    GenerationTaskRead,
    OutlineConfirmData,
    OutlineConfirmRequest,
    SectionRewriteData,
    SectionRewriteRequest,
)
from app.services.generation import (
    GenerationNotFoundError,
    GenerationService,
    GenerationValidationError,
    LegacyGenerationDisabledError,
)


router = APIRouter()


def get_generation_service() -> GenerationService:
    return GenerationService()


@router.post(
    "/start",
    response_model=APIResponse[GenerationStartData],
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def start_generation(
    payload: GenerationStartRequest,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[GenerationStartData]:
    try:
        task = await service.start_generation(session=session, payload=payload)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return APIResponse(
        code=202,
        message="success",
        data=GenerationStartData(
            task_id=task.id,
            status=task.status,
            message="大纲已生成，请确认后继续生成章节内容。",
        ),
    )


@router.get("/{task_id}", response_model=APIResponse[GenerationTaskRead], include_in_schema=False)
async def get_generation_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[GenerationTaskRead]:
    try:
        task = await service.get_task(session=session, task_id=task_id)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=GenerationTaskRead.model_validate(task))


@router.get("/{task_id}/stream", include_in_schema=False)
async def stream_generation(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> StreamingResponse:
    try:
        await service.get_task(session=session, task_id=task_id)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def event_source():
        try:
            async for event_name, payload in service.iter_stream_events(session=session, task_id=task_id):
                yield f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except GenerationNotFoundError:
            payload = json.dumps({"message": "Generation task not found"}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post(
    "/{task_id}/outline/confirm",
    response_model=APIResponse[OutlineConfirmData],
    include_in_schema=False,
)
async def confirm_outline(
    task_id: UUID,
    payload: OutlineConfirmRequest,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[OutlineConfirmData]:
    try:
        task = await service.confirm_outline(session=session, task_id=task_id, payload=payload)
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    pending_review_count = 0
    if task.status == "section_review":
        reviews = await service.list_reviews(session=session, task_id=task.id)
        pending_review_count = sum(1 for review in reviews if review.status == "pending")

    return APIResponse(
        code=200,
        message="success",
        data=OutlineConfirmData(
            task_id=task.id,
            status=task.status,
            section_count=len(task.sections or []),
            pending_review_count=pending_review_count,
            final_markdown=task.final_markdown or "",
        ),
    )


@router.post(
    "/{task_id}/sections/{section_index}/rewrite",
    response_model=APIResponse[SectionRewriteData],
    include_in_schema=False,
)
async def rewrite_section(
    task_id: UUID,
    section_index: int,
    payload: SectionRewriteRequest,
    session: AsyncSession = Depends(get_db_session),
    service: GenerationService = Depends(get_generation_service),
) -> APIResponse[SectionRewriteData]:
    try:
        result = await service.rewrite_section(
            session=session,
            task_id=task_id,
            section_index=section_index,
            payload=payload,
        )
    except LegacyGenerationDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except GenerationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return APIResponse(code=200, message="success", data=SectionRewriteData.model_validate(result))
