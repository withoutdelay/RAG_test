from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.models.project import Project
from app.schemas.common import APIResponse
from app.schemas.project import ProjectCreate, ProjectList, ProjectRead, ProjectUpdate


router = APIRouter()


@router.post("", response_model=APIResponse[ProjectRead], status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ProjectRead]:
    project = Project(**payload.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return APIResponse(code=201, message="success", data=ProjectRead.model_validate(project))


@router.get("", response_model=APIResponse[ProjectList])
async def list_projects(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    industry: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ProjectList]:
    filters = []
    if industry:
        filters.append(Project.industry == industry)

    total_stmt = select(func.count()).select_from(Project)
    items_stmt = select(Project).order_by(Project.created_at.desc())
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)

    total = await session.scalar(total_stmt)
    result = await session.scalars(items_stmt.offset((page - 1) * size).limit(size))
    items = [ProjectRead.model_validate(project) for project in result.all()]
    return APIResponse(
        code=200,
        message="success",
        data=ProjectList(items=items, total=total or 0, page=page, size=size),
    )


@router.get("/{project_id}", response_model=APIResponse[ProjectRead])
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ProjectRead]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return APIResponse(code=200, message="success", data=ProjectRead.model_validate(project))


@router.put("/{project_id}", response_model=APIResponse[ProjectRead])
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ProjectRead]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)

    await session.commit()
    await session.refresh(project)
    return APIResponse(code=200, message="success", data=ProjectRead.model_validate(project))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
