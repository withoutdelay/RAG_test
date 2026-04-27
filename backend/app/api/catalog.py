from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.schemas.catalog import (
    CatalogImportRequest,
    CatalogImportResultRead,
    CatalogMaterialImportRequest,
    CatalogMaterialImportResultRead,
    CatalogMaterialManifestPreviewRead,
    CatalogMaterialPreviewRequest,
    CatalogMaterialReadinessRead,
    CatalogPublishResultRead,
    ProductFamilyRead,
    ProductInterfaceRead,
    ProductMaterialRead,
    ProductModelRead,
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
    family_code: str | None = Query(default=None),
    search: str | None = Query(default=None),
    catalog_version: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductSeriesRead]]:
    series_rows = await service.list_series(
        session=session,
        published_only=published_only,
        family=family,
        family_code=family_code,
        search=search,
        catalog_version=catalog_version,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductSeriesRead.model_validate(row) for row in series_rows],
    )


@router.get("/families", response_model=APIResponse[list[ProductFamilyRead]])
async def list_catalog_families(
    published_only: bool = Query(default=True),
    search: str | None = Query(default=None),
    catalog_version: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductFamilyRead]]:
    family_rows = await service.list_families(
        session=session,
        published_only=published_only,
        search=search,
        catalog_version=catalog_version,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductFamilyRead.model_validate(row) for row in family_rows],
    )


@router.get("/models", response_model=APIResponse[list[ProductModelRead]])
async def list_catalog_models(
    published_only: bool = Query(default=True),
    family_code: str | None = Query(default=None),
    series_code: str | None = Query(default=None),
    search: str | None = Query(default=None),
    catalog_version: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductModelRead]]:
    model_rows = await service.list_models(
        session=session,
        published_only=published_only,
        family_code=family_code,
        series_code=series_code,
        search=search,
        catalog_version=catalog_version,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductModelRead.model_validate(row) for row in model_rows],
    )


@router.get("/interfaces", response_model=APIResponse[list[ProductInterfaceRead]])
async def list_catalog_interfaces(
    published_only: bool = Query(default=True),
    family_code: str | None = Query(default=None),
    series_code: str | None = Query(default=None),
    interface_type: str | None = Query(default=None),
    protocol: str | None = Query(default=None),
    search: str | None = Query(default=None),
    catalog_version: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductInterfaceRead]]:
    interface_rows = await service.list_interfaces(
        session=session,
        published_only=published_only,
        family_code=family_code,
        series_code=series_code,
        interface_type=interface_type,
        protocol=protocol,
        search=search,
        catalog_version=catalog_version,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductInterfaceRead.model_validate(row) for row in interface_rows],
    )


@router.get("/materials", response_model=APIResponse[list[ProductMaterialRead]])
async def list_catalog_materials(
    family_code: str | None = Query(default=None),
    material_type: str | None = Query(default=None),
    availability_status: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[list[ProductMaterialRead]]:
    materials = await service.list_materials(
        session=session,
        family_code=family_code,
        material_type=material_type,
        availability_status=availability_status,
        source_kind=source_kind,
        search=search,
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ProductMaterialRead.model_validate(row) for row in materials],
    )


@router.get("/material-readiness", response_model=APIResponse[CatalogMaterialReadinessRead])
async def get_catalog_material_readiness(
    family_code: list[str] | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[CatalogMaterialReadinessRead]:
    readiness = await service.get_material_readiness(
        session=session,
        target_family_codes=family_code,
        project_id=project_id,
    )
    return APIResponse(
        code=200,
        message="success",
        data=CatalogMaterialReadinessRead.model_validate(readiness),
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


@router.post("/materials/import-manifest", response_model=APIResponse[CatalogMaterialImportResultRead])
async def import_catalog_material_manifest(
    payload: CatalogMaterialImportRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[CatalogMaterialImportResultRead]:
    try:
        result = await service.import_material_manifest(
            session=session,
            manifest_path=payload.manifest_path,
            replace_existing=payload.replace_existing,
            source_kind=payload.source_kind,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=CatalogMaterialImportResultRead.model_validate(result))


@router.post("/materials/preview-manifest", response_model=APIResponse[CatalogMaterialManifestPreviewRead])
async def preview_catalog_material_manifest(
    payload: CatalogMaterialPreviewRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ProductCatalogService = Depends(get_product_catalog_service),
) -> APIResponse[CatalogMaterialManifestPreviewRead]:
    try:
        result = await service.preview_material_manifest(
            session=session,
            manifest_path=payload.manifest_path,
            replace_existing=payload.replace_existing,
            source_kind=payload.source_kind,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=CatalogMaterialManifestPreviewRead.model_validate(result))


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
