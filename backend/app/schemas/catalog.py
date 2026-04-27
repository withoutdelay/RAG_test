from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CatalogImportRequest(BaseModel):
    catalog_version: str | None = None
    publish: bool = True
    replace_existing: bool = True


class CatalogMaterialImportRequest(BaseModel):
    manifest_path: str
    replace_existing: bool = False
    source_kind: str | None = None


class CatalogMaterialPreviewRequest(BaseModel):
    manifest_path: str
    replace_existing: bool = False
    source_kind: str | None = None


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


class ProductFamilyAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    alias: str
    alias_type: str
    source: str | None = None
    sort_order: int = 0


class ProductCompatibilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    source_family_code: str
    target_family_code: str
    relation_type: str
    condition: str | None = None
    description: str | None = None
    preferred_series_codes: list[str]
    optional_series_codes: list[str]
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductFamilyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    code: str
    name: str
    display_name: str | None = None
    description: str | None = None
    status: str
    sort_order: int = 0
    parent_family_code: str | None = None
    aliases: list[ProductFamilyAliasRead]
    compatibilities: list[ProductCompatibilityRead]
    series_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductMaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    material_key: str
    family_code: str | None = None
    material_type: str
    document_name: str
    source_path: str | None = None
    source_kind: str
    availability_status: str
    file_format: str | None = None
    file_size_bytes: int | None = None
    assigned_track: str | None = None
    suggested_track: str | None = None
    priority_tier: str | None = None
    tags: list[str]
    notes: str | None = None
    details: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    series_id: UUID | None = None
    series_code: str
    model_number: str
    rated_voltage: str | None = None
    rated_power_kw: float | None = None
    rated_current: str | None = None
    specs: dict[str, Any]
    source_material_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductInterfaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    series_id: UUID | None = None
    series_code: str
    interface_type: str
    protocol: str | None = None
    signal_spec: dict[str, Any]
    notes: str | None = None
    source_material_key: str | None = None
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductSeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    catalog_version: str
    is_published: bool
    role_type: str
    family: str
    family_code: str | None = None
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
    imported_model_count: int = 0
    imported_interface_count: int = 0
    published: bool
    reused_existing: bool


class CatalogPublishResultRead(BaseModel):
    catalog_version: str
    published_series_count: int
    published_model_count: int = 0
    published_interface_count: int = 0


class CatalogMaterialImportResultRead(BaseModel):
    manifest_path: str
    imported_material_count: int
    skipped_existing_count: int
    family_counts: dict[str, int]


class CatalogMaterialManifestPreviewIssueRead(BaseModel):
    issue_type: str
    severity: str
    message: str
    material_key: str | None = None
    document_name: str | None = None


class CatalogMaterialManifestPreviewEntryRead(BaseModel):
    material_key: str
    document_name: str
    family_code: str | None = None
    material_type: str
    availability_status: str
    source_kind: str
    source_path: str | None = None
    source_path_exists: bool | None = None
    explicit_family_code: bool = False
    explicit_material_type: bool = False
    explicit_availability_status: bool = False
    existing_material: bool = False
    duplicate_material_key: bool = False
    non_synthetic_source: bool = True
    counted_toward_gate: bool = False
    issues: list[str]


class CatalogMaterialManifestPreviewRead(BaseModel):
    manifest_path: str
    source_kind: str
    replace_existing: bool
    import_blocked: bool
    total_entry_count: int
    unique_material_key_count: int
    duplicate_material_key_count: int
    existing_material_count: int
    new_material_count: int
    would_import_count: int
    would_skip_existing_count: int
    would_replace_existing_count: int
    gate_ready_material_count: int
    non_synthetic_material_count: int
    inferred_family_count: int
    inferred_material_type_count: int
    inferred_status_count: int
    missing_source_path_count: int
    missing_source_file_count: int
    family_counts: dict[str, int]
    material_type_counts: dict[str, int]
    availability_status_counts: dict[str, int]
    source_kind_counts: dict[str, int]
    gate_ready_family_material_counts: dict[str, dict[str, int]]
    duplicate_material_keys: list[str]
    issues: list[CatalogMaterialManifestPreviewIssueRead]
    preview_entries: list[CatalogMaterialManifestPreviewEntryRead]


class CatalogMaterialReadinessCheckRead(BaseModel):
    check_key: str
    label: str
    passed: bool
    required_count: int
    actual_count: int
    matched_family_codes: list[str]
    matched_material_keys: list[str]
    matched_document_names: list[str]
    missing_detail: str | None = None


class CatalogMaterialReadinessPhaseRead(BaseModel):
    phase: str
    label: str
    allowed: bool
    reason: str


class CatalogMaterialReadinessRead(BaseModel):
    catalog_version: str | None = None
    target_family_codes: list[str]
    gate_passed: bool
    available_material_count: int
    required_core_manual_family_count: int
    checklist: list[CatalogMaterialReadinessCheckRead]
    missing_items: list[str]
    phase_allowances: list[CatalogMaterialReadinessPhaseRead]
    material_type_counts: dict[str, int]
    family_material_counts: dict[str, dict[str, int]]
