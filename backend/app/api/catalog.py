from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.schemas.catalog import (
    CatalogImportRequest,
    CatalogImportResultRead,
    CatalogPublishResultRead,
    CatalogVersionRead,
    ProductSeriesRead,
)
from app.schemas.common import APIResponse
from app.services.catalog import ProductCatalogService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


router = APIRouter()


def get_product_catalog_service() -> ProductCatalogService:
    return ProductCatalogService()


@router.get("/series", response_model=APIResponse[list[ProductSeriesRead]])
async def list_catalog_series(
    published_only: bool = Query(default=True),
    family: str | None = Query(default=None),
    search: str | None = Query(default=None),
    catalog_version: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductSeriesRead]]:
    series_rows = await service.list_series(
        session=session,
        published_only=published_only,
        family=family,
        search=search,
        catalog_version=catalog_version,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductSeriesRead.model_validate(row) for row in series_rows],
    )


@router.get("/versions", response_model=APIResponse[list[CatalogVersionRead]])
async def list_catalog_versions(
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[CatalogVersionRead]]:
    versions = await service.list_versions(session=session)
    return APIResponse(
        code=200,
        message="success",
        data=[CatalogVersionRead.model_validate(row) for row in versions],
    )


@router.post("/import-defaults", response_model=APIResponse[CatalogImportResultRead])
async def import_default_catalog(
    payload: CatalogImportRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[CatalogImportResultRead]:
    try:
        result = await service.import_default_catalog(
            session=session,
            catalog_version=payload.catalog_version,
            publish=payload.publish,
            replace_existing=payload.replace_existing,
        )
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=CatalogImportResultRead.model_validate(result))


@router.post("/versions/{catalog_version}/publish", response_model=APIResponse[CatalogPublishResultRead])
async def publish_catalog_version(
    catalog_version: str,
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[CatalogPublishResultRead]:
    try:
        result = await service.publish_catalog_version(
            session=session,
            catalog_version=catalog_version,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=CatalogPublishResultRead.model_validate(result))
