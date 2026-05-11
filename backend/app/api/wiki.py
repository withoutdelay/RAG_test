from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.schemas.common import APIResponse
from app.services.auth import AuthenticatedUser, require_authenticated_user
from app.services.knowledge import request_case_library_refresh
from app.services.knowledge.library_refresh import read_case_library_refresh_status
from app.services.knowledge.wiki_audit import WikiAuditError, WikiAuditService


router = APIRouter()


class WikiItemActionRequest(BaseModel):
    note: str | None = None


class WikiItemRejectRequest(BaseModel):
    reason: str | None = None
    status: Literal["rejected", "quarantined"] = "rejected"


class WikiItemEditRequest(BaseModel):
    canonical_name: str | None = None
    aliases: list[str] | None = None
    summary: str | None = None


class WikiItemMergeRequest(BaseModel):
    source_item_ids: list[str] = Field(default_factory=list)
    note: str | None = None


def get_wiki_audit_service() -> WikiAuditService:
    return WikiAuditService()


def _api_response(data: dict[str, Any], *, code: int = 200) -> APIResponse[dict[str, Any]]:
    return APIResponse(code=code, message="success", data=data)


def _raise_http_error(error: WikiAuditError) -> None:
    raise HTTPException(status_code=error.status_code, detail=str(error))


@router.get("/wiki/audit/summary", response_model=APIResponse[dict[str, Any]])
async def get_wiki_audit_summary(
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    return _api_response(service.summary())


@router.get("/wiki/audit/items", response_model=APIResponse[dict[str, Any]])
async def list_wiki_audit_items(
    layer: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    item_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        data = service.list_items(
            layer=layer,
            status=status_filter,
            item_type=item_type,
            query=q,
            limit=limit,
            offset=offset,
        )
    except WikiAuditError as error:
        _raise_http_error(error)
    return _api_response(data)


@router.get("/wiki/audit/items/{item_id}", response_model=APIResponse[dict[str, Any]])
async def get_wiki_audit_item(
    item_id: str,
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        return _api_response(service.item_detail(item_id))
    except WikiAuditError as error:
        _raise_http_error(error)


@router.post("/wiki/audit/items/{item_id}/approve", response_model=APIResponse[dict[str, Any]])
async def approve_wiki_audit_item(
    item_id: str,
    payload: WikiItemActionRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        return _api_response(service.approve_item(item_id, actor=user.username, note=payload.note))
    except WikiAuditError as error:
        _raise_http_error(error)


@router.post("/wiki/audit/items/{item_id}/reject", response_model=APIResponse[dict[str, Any]])
async def reject_wiki_audit_item(
    item_id: str,
    payload: WikiItemRejectRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        return _api_response(
            service.reject_item(
                item_id,
                actor=user.username,
                reason=payload.reason,
                status=payload.status,
            )
        )
    except WikiAuditError as error:
        _raise_http_error(error)


@router.post("/wiki/audit/items/{item_id}/edit", response_model=APIResponse[dict[str, Any]])
async def edit_wiki_audit_item(
    item_id: str,
    payload: WikiItemEditRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        return _api_response(
            service.edit_item(
                item_id,
                actor=user.username,
                updates=payload.model_dump(exclude_none=True),
            )
        )
    except WikiAuditError as error:
        _raise_http_error(error)


@router.post("/wiki/audit/items/{item_id}/merge", response_model=APIResponse[dict[str, Any]])
async def merge_wiki_audit_items(
    item_id: str,
    payload: WikiItemMergeRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    try:
        return _api_response(
            service.merge_items(
                item_id,
                source_item_ids=payload.source_item_ids,
                actor=user.username,
                note=payload.note,
            )
        )
    except WikiAuditError as error:
        _raise_http_error(error)


@router.post(
    "/wiki/audit/rebuild",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_202_ACCEPTED,
)
async def rebuild_wiki_audit(
    background_tasks: BackgroundTasks,
) -> APIResponse[dict[str, Any]]:
    background_tasks.add_task(request_case_library_refresh)
    return _api_response({"status": "queued", "refresh_status": read_case_library_refresh_status()}, code=202)


@router.get("/wiki/audit/diff", response_model=APIResponse[dict[str, Any]])
async def get_wiki_audit_diff(
    service: WikiAuditService = Depends(get_wiki_audit_service),
) -> APIResponse[dict[str, Any]]:
    return _api_response(service.diff())
