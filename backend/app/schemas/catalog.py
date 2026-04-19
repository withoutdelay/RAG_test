from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CatalogImportRequest(BaseModel):
    catalog_version: str | None = None
    publish: bool = True
    replace_existing: bool = True


class ProductConstraintRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    constraint_type: str
    condition: str
    action: str
    severity: str


class ProductStandardConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    config_name: str
    components: list[dict[str, Any]]
    applicable_scenarios: list[str]
    description: str | None = None


class ProductSeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    role_type: str
    family: str
    series_name: str
    code: str
    vendor: str | None = None
    description: str | None = None
    voltage_levels: list[str]
    min_power_kw: float | None = None
    max_power_kw: float | None = None
    topology: str | None = None
    applicable_motors: list[str]
    applicable_loads: list[str]
    communication_protocols: list[str]
    io_allocation: dict[str, Any]
    protection_features: list[str]
    preferred_scenarios: list[str]
    default_chapters: list[str]
    standard_configs: list[ProductStandardConfigRead]
    constraints: list[ProductConstraintRead]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CatalogVersionRead(BaseModel):
    catalog_version: str
    is_published: bool
    series_count: int = 0


class CatalogImportResultRead(BaseModel):
    catalog_version: str
    imported_series_count: int
    published: bool
    reused_existing: bool


class CatalogPublishResultRead(BaseModel):
    catalog_version: str
    published_series_count: int
