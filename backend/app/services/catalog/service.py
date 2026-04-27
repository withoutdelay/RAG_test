from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product_compatibility import ProductCompatibility
from app.models.product_constraint import ProductConstraint
from app.models.product_family import ProductFamily
from app.models.product_family_alias import ProductFamilyAlias
from app.models.product_interface import ProductInterface
from app.models.product_material import ProductMaterial
from app.models.product_model import ProductModel
from app.models.product_series import ProductSeries
from app.models.product_standard_config import ProductStandardConfig
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.services.catalog.defaults import (
    DEFAULT_CATALOG_VERSION,
    DEFAULT_PRODUCT_CATALOG,
    DEFAULT_PRODUCT_COMPATIBILITY,
    DEFAULT_PRODUCT_FAMILIES,
    DEFAULT_PRODUCT_INTERFACES,
    DEFAULT_PRODUCT_MODELS,
)
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


_PROTOCOL_PATTERNS = (
    ("Profibus-DP", re.compile(r"profibus", re.IGNORECASE)),
    ("Profinet", re.compile(r"profinet", re.IGNORECASE)),
    ("Modbus TCP", re.compile(r"modbus\s*tcp", re.IGNORECASE)),
    ("Modbus RTU", re.compile(r"modbus", re.IGNORECASE)),
    ("IEC 61850", re.compile(r"61850", re.IGNORECASE)),
)
_VOLTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kV|KV|kv|千伏)")
_POWER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kW|KW|kw|千瓦)")
_QUANTITY_PATTERN = re.compile(r"(\d+)\s*(?:台|套|组)")
_LOAD_TOKENS = ("鼓风机", "风机", "泵", "压缩机", "磨机", "输送机")
_MATERIAL_TYPE_HINTS = (
    ("product_manual", ("手册", "样本册", "样本", "catalog", "manual")),
    ("standard_bom", ("bom", "配置清单", "标准配置", "供货清单")),
    ("interface_schedule", ("点表", "接口", "通讯表", "i/o", "io表")),
    ("selection_rule", ("选型规则", "约束", "降额", "规则")),
    ("model_alias_map", ("型号映射", "编码映射", "术语映射", "别名映射")),
    ("diagram_template", ("系统图", "单线图", "框图", "图纸模板")),
)
_DEFAULT_READINESS_FAMILY_CODES = (
    "lci_sync_drive",
    "hv_solid_state_starter",
    "hv_vfd_multilevel",
)
_READINESS_CHECK_DEFINITIONS = (
    ("standard_bom", "至少一套标准配置 / BOM 已到位", "standard_bom"),
    ("interface_schedule", "至少一套接口 / 点表资料已到位", "interface_schedule"),
    ("selection_rule", "至少一份约束 / 选型规则资料已到位", "selection_rule"),
    ("model_alias_map", "至少一份型号与术语映射表已到位", "model_alias_map"),
)
_NON_GATE_SOURCE_KINDS = {
    "synthetic_test_only",
}


@dataclass(frozen=True)
class CatalogSignals:
    raw_text: str
    normalized_text: str
    matching_signals: list[str]
    voltage_value: float | None
    power_value: float | None
    quantity: int
    motor_type: str
    bypass_required: bool
    requested_protocol: str | None
    application_tokens: list[str]
    product_line: str


@dataclass(frozen=True)
class CatalogCandidate:
    series: ProductSeries
    selected_config: ProductStandardConfig | None
    score: float
    reasons: list[str]


class ProductCatalogService:
    def __init__(self, *, default_catalog_version: str = DEFAULT_CATALOG_VERSION) -> None:
        self.default_catalog_version = default_catalog_version

    async def _resolve_effective_catalog_version(
        self,
        *,
        session: AsyncSession,
        published_only: bool,
        catalog_version: str | None,
    ) -> str | None:
        effective_version = catalog_version
        if effective_version is None and published_only:
            effective_version = await self.get_active_catalog_version(session=session)
        return effective_version

    async def list_series(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        family: str | None = None,
        family_code: str | None = None,
        search: str | None = None,
        catalog_version: str | None = None,
    ) -> list[ProductSeries]:
        stmt = (
            select(ProductSeries)
            .options(
                selectinload(ProductSeries.standard_configs),
                selectinload(ProductSeries.constraints),
            )
            .order_by(ProductSeries.family.asc(), ProductSeries.series_name.asc(), ProductSeries.code.asc())
        )
        if published_only:
            stmt = stmt.where(ProductSeries.is_published.is_(True))
        if family:
            stmt = stmt.where(ProductSeries.family == family)
        if family_code:
            stmt = stmt.where(ProductSeries.family_code == family_code)
        if catalog_version:
            stmt = stmt.where(ProductSeries.catalog_version == catalog_version)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductSeries.series_name.ilike(like)
                | ProductSeries.code.ilike(like)
                | ProductSeries.family.ilike(like)
                | ProductSeries.family_code.ilike(like)
            )
        result = await session.scalars(stmt)
        return result.all()

    async def list_models(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        family_code: str | None = None,
        series_code: str | None = None,
        search: str | None = None,
        catalog_version: str | None = None,
    ) -> list[ProductModel]:
        effective_version = await self._resolve_effective_catalog_version(
            session=session,
            published_only=published_only,
            catalog_version=catalog_version,
        )
        if effective_version is None and published_only:
            return []

        stmt = (
            select(ProductModel)
            .join(ProductSeries, ProductModel.series_id == ProductSeries.id)
            .order_by(ProductSeries.family.asc(), ProductModel.series_code.asc(), ProductModel.model_number.asc())
        )
        if published_only:
            stmt = stmt.where(
                ProductModel.is_published.is_(True),
                ProductSeries.is_published.is_(True),
            )
        if effective_version:
            stmt = stmt.where(ProductModel.catalog_version == effective_version)
        if family_code:
            stmt = stmt.where(ProductSeries.family_code == family_code)
        if series_code:
            stmt = stmt.where(ProductModel.series_code == series_code)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductModel.model_number.ilike(like)
                | ProductModel.series_code.ilike(like)
                | ProductModel.rated_voltage.ilike(like)
                | ProductModel.rated_current.ilike(like)
                | ProductModel.source_material_key.ilike(like)
            )
        result = await session.scalars(stmt)
        return result.all()

    async def list_interfaces(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        family_code: str | None = None,
        series_code: str | None = None,
        interface_type: str | None = None,
        protocol: str | None = None,
        search: str | None = None,
        catalog_version: str | None = None,
    ) -> list[ProductInterface]:
        effective_version = await self._resolve_effective_catalog_version(
            session=session,
            published_only=published_only,
            catalog_version=catalog_version,
        )
        if effective_version is None and published_only:
            return []

        stmt = (
            select(ProductInterface)
            .join(ProductSeries, ProductInterface.series_id == ProductSeries.id)
            .order_by(
                ProductSeries.family.asc(),
                ProductInterface.series_code.asc(),
                ProductInterface.sort_order.asc(),
                ProductInterface.interface_type.asc(),
            )
        )
        if published_only:
            stmt = stmt.where(
                ProductInterface.is_published.is_(True),
                ProductSeries.is_published.is_(True),
            )
        if effective_version:
            stmt = stmt.where(ProductInterface.catalog_version == effective_version)
        if family_code:
            stmt = stmt.where(ProductSeries.family_code == family_code)
        if series_code:
            stmt = stmt.where(ProductInterface.series_code == series_code)
        if interface_type:
            stmt = stmt.where(ProductInterface.interface_type == interface_type)
        if protocol:
            stmt = stmt.where(ProductInterface.protocol.ilike(f"%{protocol.strip()}%"))
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductInterface.series_code.ilike(like)
                | ProductInterface.interface_type.ilike(like)
                | ProductInterface.protocol.ilike(like)
                | ProductInterface.notes.ilike(like)
                | ProductInterface.source_material_key.ilike(like)
            )
        result = await session.scalars(stmt)
        return result.all()

    async def list_families(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        search: str | None = None,
        catalog_version: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_version = await self._resolve_effective_catalog_version(
            session=session,
            published_only=published_only,
            catalog_version=catalog_version,
        )
        if effective_version is None and published_only:
            return []

        join_conditions = [
            ProductSeries.catalog_version == ProductFamily.catalog_version,
            ProductSeries.family_code == ProductFamily.code,
        ]
        if published_only:
            join_conditions.append(ProductSeries.is_published.is_(True))

        stmt = (
            select(ProductFamily, func.count(ProductSeries.id).label("series_count"))
            .outerjoin(ProductSeries, and_(*join_conditions))
            .options(selectinload(ProductFamily.aliases))
            .group_by(ProductFamily.id)
            .order_by(ProductFamily.sort_order.asc(), ProductFamily.code.asc())
        )
        if published_only:
            stmt = stmt.where(ProductFamily.is_published.is_(True))
        if effective_version:
            stmt = stmt.where(ProductFamily.catalog_version == effective_version)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductFamily.code.ilike(like)
                | ProductFamily.name.ilike(like)
                | ProductFamily.display_name.ilike(like)
                | ProductFamily.aliases.any(ProductFamilyAlias.alias.ilike(like))
            )

        rows = await session.execute(stmt)
        compatibility_stmt = select(ProductCompatibility).order_by(
            ProductCompatibility.source_family_code.asc(),
            ProductCompatibility.sort_order.asc(),
            ProductCompatibility.target_family_code.asc(),
        )
        if published_only:
            compatibility_stmt = compatibility_stmt.where(ProductCompatibility.is_published.is_(True))
        if effective_version:
            compatibility_stmt = compatibility_stmt.where(ProductCompatibility.catalog_version == effective_version)
        compatibility_rows = (await session.scalars(compatibility_stmt)).all()
        compatibility_map: dict[str, list[dict[str, Any]]] = {}
        for item in compatibility_rows:
            compatibility_map.setdefault(item.source_family_code, []).append(
                {
                    "id": item.id,
                    "catalog_version": item.catalog_version,
                    "is_published": item.is_published,
                    "source_family_code": item.source_family_code,
                    "target_family_code": item.target_family_code,
                    "relation_type": item.relation_type,
                    "condition": item.condition,
                    "description": item.description,
                    "preferred_series_codes": list(item.preferred_series_codes or []),
                    "optional_series_codes": list(item.optional_series_codes or []),
                    "sort_order": item.sort_order,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        payloads: list[dict[str, Any]] = []
        for family, series_count in rows.all():
            payloads.append(
                {
                    "id": family.id,
                    "catalog_version": family.catalog_version,
                    "is_published": family.is_published,
                    "code": family.code,
                    "name": family.name,
                    "display_name": family.display_name,
                    "description": family.description,
                    "status": family.status,
                    "sort_order": family.sort_order,
                    "parent_family_code": family.parent_family_code,
                    "aliases": [
                        {
                            "id": alias.id,
                            "alias": alias.alias,
                            "alias_type": alias.alias_type,
                            "source": alias.source,
                            "sort_order": alias.sort_order,
                        }
                        for alias in family.aliases
                    ],
                    "compatibilities": compatibility_map.get(family.code, []),
                    "series_count": int(series_count or 0),
                    "created_at": family.created_at,
                    "updated_at": family.updated_at,
                }
            )
        return payloads

    async def list_materials(
        self,
        *,
        session: AsyncSession,
        family_code: str | None = None,
        material_type: str | None = None,
        availability_status: str | None = None,
        source_kind: str | None = None,
        search: str | None = None,
    ) -> list[ProductMaterial]:
        stmt = select(ProductMaterial).order_by(
            ProductMaterial.family_code.asc(),
            ProductMaterial.material_type.asc(),
            ProductMaterial.document_name.asc(),
        )
        if family_code:
            stmt = stmt.where(ProductMaterial.family_code == family_code)
        if material_type:
            stmt = stmt.where(ProductMaterial.material_type == material_type)
        if availability_status:
            stmt = stmt.where(ProductMaterial.availability_status == availability_status)
        if source_kind:
            stmt = stmt.where(ProductMaterial.source_kind == source_kind)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductMaterial.material_key.ilike(like)
                | ProductMaterial.document_name.ilike(like)
                | ProductMaterial.source_path.ilike(like)
                | ProductMaterial.notes.ilike(like)
            )
        result = await session.scalars(stmt)
        return result.all()

    async def get_material_readiness(
        self,
        *,
        session: AsyncSession,
        target_family_codes: list[str] | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        normalized_target_families = await self._resolve_target_family_codes(
            session=session,
            family_codes=target_family_codes,
            project_id=project_id,
        )
        target_family_set = set(normalized_target_families)
        all_available_materials = await self.list_materials(
            session=session,
            availability_status="available",
        )
        available_materials = [
            row
            for row in all_available_materials
            if str(getattr(row, "source_kind", "") or "").strip() not in _NON_GATE_SOURCE_KINDS
        ]
        catalog_version = await self.get_active_catalog_version(session=session)

        material_type_counts: dict[str, int] = {}
        family_material_counts: dict[str, dict[str, int]] = {
            family_code: {"total": 0} for family_code in normalized_target_families
        }
        for row in available_materials:
            material_type = str(row.material_type or "").strip()
            family_key = str(row.family_code or "").strip() or "unclassified"
            family_bucket = family_material_counts.setdefault(family_key, {"total": 0})
            family_bucket["total"] = int(family_bucket.get("total", 0)) + 1
            if material_type:
                material_type_counts[material_type] = int(material_type_counts.get(material_type, 0)) + 1
                family_bucket[material_type] = int(family_bucket.get(material_type, 0)) + 1

        required_core_manual_family_count = max(1, min(2, len(normalized_target_families)))
        manual_matches = [
            row
            for row in available_materials
            if str(row.material_type or "").strip() == "product_manual"
            and str(row.family_code or "").strip() in target_family_set
        ]
        manual_family_codes = sorted(
            {
                family_code
                for family_code in (str(row.family_code or "").strip() for row in manual_matches)
                if family_code
            }
        )
        checklist = [
            self._build_material_readiness_check(
                check_key="core_product_manuals",
                label="至少两个核心产品族具备产品手册",
                matches=manual_matches,
                required_count=required_core_manual_family_count,
                actual_count=len(manual_family_codes),
                matched_family_codes=manual_family_codes,
                missing_detail=(
                    f"当前仅有 {len(manual_family_codes)} 个核心产品族具备产品手册，还差 "
                    f"{required_core_manual_family_count - len(manual_family_codes)} 个。"
                    if len(manual_family_codes) < required_core_manual_family_count
                    else None
                ),
            )
        ]

        for check_key, label, material_type in _READINESS_CHECK_DEFINITIONS:
            matches = [
                row for row in available_materials if str(row.material_type or "").strip() == material_type
            ]
            checklist.append(
                self._build_material_readiness_check(
                    check_key=check_key,
                    label=label,
                    matches=matches,
                    required_count=1,
                    actual_count=len(matches),
                    matched_family_codes=sorted(
                        {
                            family_code
                            for family_code in (str(row.family_code or "").strip() for row in matches)
                            if family_code
                        }
                    ),
                    missing_detail=None if matches else f"当前缺少：{label}。",
                )
            )

        missing_items = [item["check_key"] for item in checklist if not item["passed"]]
        blocked_reason = (
            "材料门槛未通过：" + "；".join(item["label"] for item in checklist if not item["passed"])
            if missing_items
            else "材料门槛已通过，可以继续进入长期路线图的产品化阶段。"
        )
        gate_passed = not missing_items

        return {
            "catalog_version": catalog_version,
            "target_family_codes": normalized_target_families,
            "gate_passed": gate_passed,
            "available_material_count": len(available_materials),
            "required_core_manual_family_count": required_core_manual_family_count,
            "checklist": checklist,
            "missing_items": missing_items,
            "phase_allowances": [
                {
                    "phase": "phase_0a",
                    "label": "Phase 0A / 开发基线分流",
                    "allowed": True,
                    "reason": "允许继续用 mock 默认推进基线、链路和挂载能力。",
                },
                {
                    "phase": "phase_0b_front_half",
                    "label": "样本驱动的 Phase 0B 前半段",
                    "allowed": True,
                    "reason": "允许继续用样本和 mock 推进承接层，但不能宣称真实资料库已完成。",
                },
                {
                    "phase": "phase_1",
                    "label": "Phase 1 / 产品知识驱动的方案设计层",
                    "allowed": gate_passed,
                    "reason": "Entry Gate 已通过。" if gate_passed else blocked_reason,
                },
                {
                    "phase": "phase_2",
                    "label": "Phase 2 / 图表优先的方案表达",
                    "allowed": gate_passed,
                    "reason": "Entry Gate 已通过。" if gate_passed else blocked_reason,
                },
                {
                    "phase": "phase_3",
                    "label": "Phase 3 / 校验、回写与长期闭环",
                    "allowed": gate_passed,
                    "reason": "Entry Gate 已通过。" if gate_passed else blocked_reason,
                },
            ],
            "material_type_counts": material_type_counts,
            "family_material_counts": family_material_counts,
        }

    async def list_compatibility_rules(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        catalog_version: str | None = None,
        source_family_code: str | None = None,
    ) -> list[ProductCompatibility]:
        stmt = select(ProductCompatibility).order_by(
            ProductCompatibility.source_family_code.asc(),
            ProductCompatibility.sort_order.asc(),
            ProductCompatibility.target_family_code.asc(),
        )
        if published_only:
            stmt = stmt.where(ProductCompatibility.is_published.is_(True))
        if catalog_version:
            stmt = stmt.where(ProductCompatibility.catalog_version == catalog_version)
        if source_family_code:
            stmt = stmt.where(ProductCompatibility.source_family_code == source_family_code)
        result = await session.scalars(stmt)
        return result.all()

    async def import_material_manifest(
        self,
        *,
        session: AsyncSession,
        manifest_path: str,
        replace_existing: bool = False,
        source_kind: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        manifest = self._load_material_manifest(manifest_path)
        records = [self._build_material_record(entry, source_kind=source_kind) for entry in manifest["entries"]]
        duplicate_material_keys = self._find_duplicate_material_keys(records)
        if duplicate_material_keys:
            duplicate_preview = ", ".join(duplicate_material_keys[:5])
            suffix = " ..." if len(duplicate_material_keys) > 5 else ""
            raise ArtifactValidationError(
                f"Material manifest contains duplicate material_key entries: {duplicate_preview}{suffix}"
            )
        keys = [record["material_key"] for record in records]

        existing_rows = []
        if keys:
            existing_rows = (await session.scalars(select(ProductMaterial).where(ProductMaterial.material_key.in_(keys)))).all()
        existing_map = {row.material_key: row for row in existing_rows}

        skipped_existing_count = 0
        if replace_existing:
            for row in existing_rows:
                await session.delete(row)
            if existing_rows:
                await session.flush()

        family_counts: dict[str, int] = {}
        imported_count = 0
        for record in records:
            if not replace_existing and record["material_key"] in existing_map:
                skipped_existing_count += 1
                continue
            session.add(ProductMaterial(**record))
            family_key = record["family_code"] or "unclassified"
            family_counts[family_key] = family_counts.get(family_key, 0) + 1
            imported_count += 1

        await session.flush()
        if commit:
            await session.commit()
        return {
            "manifest_path": str(Path(manifest_path).expanduser()),
            "imported_material_count": imported_count,
            "skipped_existing_count": skipped_existing_count,
            "family_counts": family_counts,
        }

    async def preview_material_manifest(
        self,
        *,
        session: AsyncSession,
        manifest_path: str,
        replace_existing: bool = False,
        source_kind: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._load_material_manifest(manifest_path)
        prepared_entries = [self._prepare_material_record(entry, source_kind=source_kind) for entry in manifest["entries"]]
        all_records = [record for record, _ in prepared_entries]
        duplicate_material_keys = self._find_duplicate_material_keys(all_records)
        duplicate_key_set = set(duplicate_material_keys)

        unique_records: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        preview_entries: list[dict[str, Any]] = []
        for record, diagnostics in prepared_entries:
            material_key = str(record["material_key"])
            if material_key not in unique_records:
                unique_records[material_key] = (record, diagnostics)

        unique_keys = list(unique_records.keys())
        existing_rows = []
        if unique_keys:
            existing_rows = (
                await session.scalars(select(ProductMaterial).where(ProductMaterial.material_key.in_(unique_keys)))
            ).all()
        existing_key_set = {str(row.material_key) for row in existing_rows}

        family_counts: dict[str, int] = {}
        material_type_counts: dict[str, int] = {}
        availability_status_counts: dict[str, int] = {}
        source_kind_counts: dict[str, int] = {}
        gate_ready_family_material_counts: dict[str, dict[str, int]] = {}
        inferred_family_count = 0
        inferred_material_type_count = 0
        inferred_status_count = 0
        missing_source_path_count = 0
        missing_source_file_count = 0
        non_synthetic_material_count = 0
        gate_ready_material_count = 0

        for record, diagnostics in prepared_entries:
            material_key = str(record["material_key"])
            material_type = str(record["material_type"] or "").strip()
            source_kind_value = str(record["source_kind"] or "").strip()
            availability_status = str(record["availability_status"] or "").strip()
            family_key = str(record["family_code"] or "").strip() or "unclassified"
            non_synthetic_source = source_kind_value not in _NON_GATE_SOURCE_KINDS
            counted_toward_gate = non_synthetic_source and availability_status == "available"

            entry_issues: list[str] = []
            if material_key in duplicate_key_set:
                entry_issues.append("Manifest 内 material_key 重复，正式导入前需要先去重。")
            if not diagnostics["explicit_family_code"]:
                entry_issues.append("family_code 由系统推断，正式材料建议显式提供。")
            if not diagnostics["explicit_material_type"]:
                entry_issues.append("material_type 由系统推断，正式材料建议显式提供。")
            if not diagnostics["explicit_availability_status"]:
                entry_issues.append("availability_status 由系统推断，正式材料建议显式提供。")
            if not record["source_path"]:
                entry_issues.append("缺少 source_path，当前只能做元数据导入。")
            elif diagnostics["source_path_exists"] is False:
                entry_issues.append("source_path 当前不存在，导入后无法直接读取原文。")

            preview_entries.append(
                {
                    "material_key": material_key,
                    "document_name": record["document_name"],
                    "family_code": record["family_code"],
                    "material_type": material_type,
                    "availability_status": availability_status,
                    "source_kind": source_kind_value,
                    "source_path": record["source_path"],
                    "source_path_exists": diagnostics["source_path_exists"],
                    "explicit_family_code": diagnostics["explicit_family_code"],
                    "explicit_material_type": diagnostics["explicit_material_type"],
                    "explicit_availability_status": diagnostics["explicit_availability_status"],
                    "existing_material": material_key in existing_key_set,
                    "duplicate_material_key": material_key in duplicate_key_set,
                    "non_synthetic_source": non_synthetic_source,
                    "counted_toward_gate": counted_toward_gate,
                    "issues": entry_issues,
                }
            )

        for material_key, (record, diagnostics) in unique_records.items():
            material_type = str(record["material_type"] or "").strip()
            source_kind_value = str(record["source_kind"] or "").strip()
            availability_status = str(record["availability_status"] or "").strip()
            family_key = str(record["family_code"] or "").strip() or "unclassified"
            non_synthetic_source = source_kind_value not in _NON_GATE_SOURCE_KINDS
            counted_toward_gate = non_synthetic_source and availability_status == "available"

            family_counts[family_key] = int(family_counts.get(family_key, 0)) + 1
            material_type_counts[material_type] = int(material_type_counts.get(material_type, 0)) + 1
            availability_status_counts[availability_status] = int(availability_status_counts.get(availability_status, 0)) + 1
            source_kind_counts[source_kind_value] = int(source_kind_counts.get(source_kind_value, 0)) + 1

            if not diagnostics["explicit_family_code"]:
                inferred_family_count += 1
            if not diagnostics["explicit_material_type"]:
                inferred_material_type_count += 1
            if not diagnostics["explicit_availability_status"]:
                inferred_status_count += 1
            if not record["source_path"]:
                missing_source_path_count += 1
            elif diagnostics["source_path_exists"] is False:
                missing_source_file_count += 1

            if non_synthetic_source:
                non_synthetic_material_count += 1
            if counted_toward_gate:
                gate_ready_material_count += 1
                family_bucket = gate_ready_family_material_counts.setdefault(family_key, {"total": 0})
                family_bucket["total"] = int(family_bucket.get("total", 0)) + 1
                family_bucket[material_type] = int(family_bucket.get(material_type, 0)) + 1

        unique_material_key_count = len(unique_records)
        existing_material_count = sum(1 for material_key in unique_records if material_key in existing_key_set)
        new_material_count = unique_material_key_count - existing_material_count
        import_blocked = bool(duplicate_material_keys)
        would_import_count = unique_material_key_count if replace_existing else new_material_count
        would_skip_existing_count = 0 if replace_existing else existing_material_count
        would_replace_existing_count = existing_material_count if replace_existing else 0

        issues: list[dict[str, Any]] = []
        if duplicate_material_keys:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="duplicate_material_key",
                    severity="blocking",
                    message=(
                        "Manifest 中存在重复的 material_key，正式导入会被阻断："
                        + "，".join(duplicate_material_keys[:8])
                        + (" ..." if len(duplicate_material_keys) > 8 else "")
                    ),
                )
            )
        if missing_source_path_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="missing_source_path",
                    severity="warning",
                    message=f"{missing_source_path_count} 条资料缺少 source_path，当前只能做元数据导入。",
                )
            )
        if missing_source_file_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="missing_source_file",
                    severity="warning",
                    message=f"{missing_source_file_count} 条资料的 source_path 当前不存在，后续无法直接读取原文。",
                )
            )
        if inferred_family_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="inferred_family_code",
                    severity="warning",
                    message=f"{inferred_family_count} 条资料的 family_code 依赖系统推断，正式材料建议显式补齐。",
                )
            )
        if inferred_material_type_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="inferred_material_type",
                    severity="warning",
                    message=f"{inferred_material_type_count} 条资料的 material_type 依赖系统推断，正式材料建议显式补齐。",
                )
            )
        if inferred_status_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="inferred_availability_status",
                    severity="info",
                    message=f"{inferred_status_count} 条资料的 availability_status 依赖系统推断，导入前最好显式确认。",
                )
            )
        synthetic_only_count = sum(
            1
            for material_key, (record, _) in unique_records.items()
            if str(record["source_kind"] or "").strip() in _NON_GATE_SOURCE_KINDS
        )
        if synthetic_only_count:
            issues.append(
                self._build_material_manifest_issue(
                    issue_type="non_gate_source_kind",
                    severity="info",
                    message=f"{synthetic_only_count} 条资料属于 synthetic_test_only，不计入长期路线图 Entry Gate。",
                )
            )

        return {
            "manifest_path": str(Path(manifest_path).expanduser()),
            "source_kind": str(source_kind or manifest.get("source_kind") or "mixed"),
            "replace_existing": replace_existing,
            "import_blocked": import_blocked,
            "total_entry_count": len(prepared_entries),
            "unique_material_key_count": unique_material_key_count,
            "duplicate_material_key_count": len(duplicate_material_keys),
            "existing_material_count": existing_material_count,
            "new_material_count": new_material_count,
            "would_import_count": would_import_count,
            "would_skip_existing_count": would_skip_existing_count,
            "would_replace_existing_count": would_replace_existing_count,
            "gate_ready_material_count": gate_ready_material_count,
            "non_synthetic_material_count": non_synthetic_material_count,
            "inferred_family_count": inferred_family_count,
            "inferred_material_type_count": inferred_material_type_count,
            "inferred_status_count": inferred_status_count,
            "missing_source_path_count": missing_source_path_count,
            "missing_source_file_count": missing_source_file_count,
            "family_counts": family_counts,
            "material_type_counts": material_type_counts,
            "availability_status_counts": availability_status_counts,
            "source_kind_counts": source_kind_counts,
            "gate_ready_family_material_counts": gate_ready_family_material_counts,
            "duplicate_material_keys": duplicate_material_keys,
            "issues": issues,
            "preview_entries": preview_entries,
        }

    async def list_versions(self, *, session: AsyncSession) -> list[dict[str, Any]]:
        rows = await session.execute(
            select(ProductSeries.catalog_version, ProductSeries.is_published)
            .order_by(ProductSeries.catalog_version.desc(), ProductSeries.is_published.desc())
        )
        versions: dict[str, dict[str, Any]] = {}
        for catalog_version, is_published in rows:
            payload = versions.setdefault(
                str(catalog_version),
                {
                    "catalog_version": str(catalog_version),
                    "is_published": False,
                    "series_count": 0,
                },
            )
            payload["is_published"] = payload["is_published"] or bool(is_published)
            payload["series_count"] = int(payload["series_count"]) + 1
        return list(versions.values())

    async def get_active_catalog_version(self, *, session: AsyncSession) -> str | None:
        published = await session.scalar(
            select(ProductSeries.catalog_version)
            .where(ProductSeries.is_published.is_(True))
            .order_by(ProductSeries.catalog_version.desc())
            .limit(1)
        )
        if published:
            return str(published)
        latest = await session.scalar(
            select(ProductSeries.catalog_version)
            .order_by(ProductSeries.catalog_version.desc())
            .limit(1)
        )
        return str(latest) if latest else None

    async def ensure_seed_catalog(self, *, session: AsyncSession) -> str:
        existing_count = await session.scalar(select(func.count()).select_from(ProductSeries))
        if int(existing_count or 0) > 0:
            return await self.get_active_catalog_version(session=session) or self.default_catalog_version
        await self.import_default_catalog(session=session, commit=False, publish=True, replace_existing=True)
        return self.default_catalog_version

    async def import_default_catalog(
        self,
        *,
        session: AsyncSession,
        catalog_version: str | None = None,
        publish: bool = True,
        replace_existing: bool = True,
        commit: bool = True,
    ) -> dict[str, Any]:
        version = str(catalog_version or self.default_catalog_version)
        existing_models = await session.scalars(select(ProductModel).where(ProductModel.catalog_version == version))
        existing_model_rows = existing_models.all()
        existing_interfaces = await session.scalars(
            select(ProductInterface).where(ProductInterface.catalog_version == version)
        )
        existing_interface_rows = existing_interfaces.all()
        existing = await session.scalars(
            select(ProductSeries)
            .options(
                selectinload(ProductSeries.standard_configs),
                selectinload(ProductSeries.constraints),
            )
            .where(ProductSeries.catalog_version == version)
        )
        existing_rows = existing.all()
        existing_families = await session.scalars(
            select(ProductFamily)
            .options(selectinload(ProductFamily.aliases))
            .where(ProductFamily.catalog_version == version)
        )
        existing_family_rows = existing_families.all()
        existing_compatibility = await session.scalars(
            select(ProductCompatibility).where(ProductCompatibility.catalog_version == version)
        )
        existing_compatibility_rows = existing_compatibility.all()
        if (
            existing_rows
            or existing_family_rows
            or existing_compatibility_rows
            or existing_model_rows
            or existing_interface_rows
        ) and replace_existing:
            for row in existing_model_rows:
                await session.delete(row)
            for row in existing_interface_rows:
                await session.delete(row)
            for row in existing_rows:
                await session.delete(row)
            for row in existing_family_rows:
                await session.delete(row)
            for row in existing_compatibility_rows:
                await session.delete(row)
            await session.flush()
        elif (
            existing_rows
            or existing_family_rows
            or existing_compatibility_rows
            or existing_model_rows
            or existing_interface_rows
        ):
            return {
                "catalog_version": version,
                "imported_series_count": 0,
                "imported_model_count": 0,
                "imported_interface_count": 0,
                "published": publish,
                "reused_existing": True,
            }

        for family in DEFAULT_PRODUCT_FAMILIES:
            family_row = ProductFamily(
                catalog_version=version,
                is_published=False,
                code=str(family.get("code") or ""),
                name=str(family.get("name") or ""),
                display_name=str(family.get("display_name") or "").strip() or None,
                description=str(family.get("description") or "").strip() or None,
                status=str(family.get("status") or "planned"),
                sort_order=int(family.get("sort_order") or 0),
                parent_family_code=str(family.get("parent_family_code") or "").strip() or None,
            )
            session.add(family_row)
            await session.flush()

            for idx, alias in enumerate(family.get("aliases") or []):
                session.add(
                    ProductFamilyAlias(
                        family_id=family_row.id,
                        alias=str(alias),
                        alias_type="business",
                        source="seed",
                        sort_order=idx,
                    )
                )
        for item in DEFAULT_PRODUCT_COMPATIBILITY:
            session.add(
                ProductCompatibility(
                    catalog_version=version,
                    is_published=False,
                    source_family_code=str(item.get("source_family_code") or ""),
                    target_family_code=str(item.get("target_family_code") or ""),
                    relation_type=str(item.get("relation_type") or "recommended"),
                    condition=str(item.get("condition") or "").strip() or None,
                    description=str(item.get("description") or "").strip() or None,
                    preferred_series_codes=item.get("preferred_series_codes") or [],
                    optional_series_codes=item.get("optional_series_codes") or [],
                    sort_order=int(item.get("sort_order") or 0),
                )
            )

        imported_count = 0
        series_by_code: dict[str, ProductSeries] = {}
        for item in DEFAULT_PRODUCT_CATALOG:
            series = ProductSeries(
                catalog_version=version,
                is_published=False,
                role_type=str(item.get("role_type") or "support"),
                family=str(item.get("family") or ""),
                family_code=str(item.get("family_code") or "").strip() or None,
                series_name=str(item.get("series_name") or ""),
                code=str(item.get("code") or ""),
                vendor=str(item.get("vendor") or "").strip() or None,
                description=str(item.get("description") or "").strip() or None,
                voltage_levels=item.get("voltage_levels") or [],
                min_power_kw=item.get("min_power_kw"),
                max_power_kw=item.get("max_power_kw"),
                topology=str(item.get("topology") or "").strip() or None,
                applicable_motors=item.get("applicable_motors") or [],
                applicable_loads=item.get("applicable_loads") or [],
                communication_protocols=item.get("communication_protocols") or [],
                io_allocation=item.get("io_allocation") or {},
                protection_features=item.get("protection_features") or [],
                preferred_scenarios=item.get("preferred_scenarios") or [],
                default_chapters=item.get("default_chapters") or [],
            )
            session.add(series)
            await session.flush()

            for config in item.get("standard_configs") or []:
                session.add(
                    ProductStandardConfig(
                        series_id=series.id,
                        config_name=str(config.get("config_name") or "标准配置"),
                        components=config.get("components") or [],
                        applicable_scenarios=config.get("applicable_scenarios") or [],
                        description=str(config.get("description") or "").strip() or None,
                    )
                )
            for constraint in item.get("constraints") or []:
                session.add(
                    ProductConstraint(
                        series_id=series.id,
                        constraint_type=str(constraint.get("constraint_type") or "general"),
                        condition=str(constraint.get("condition") or ""),
                        action=str(constraint.get("action") or ""),
                        severity=str(constraint.get("severity") or "warning"),
                    )
                )
            imported_count += 1
            series_by_code[series.code] = series

        imported_model_count = 0
        for item in DEFAULT_PRODUCT_MODELS:
            target_series = series_by_code.get(str(item.get("series_code") or ""))
            if target_series is None:
                raise ArtifactValidationError(
                    f"Missing series seed for product model {item.get('model_number')}: {item.get('series_code')}"
                )
            session.add(
                ProductModel(
                    catalog_version=version,
                    is_published=False,
                    series_id=target_series.id,
                    series_code=target_series.code,
                    model_number=str(item.get("model_number") or ""),
                    rated_voltage=str(item.get("rated_voltage") or "").strip() or None,
                    rated_power_kw=item.get("rated_power_kw"),
                    rated_current=str(item.get("rated_current") or "").strip() or None,
                    specs=item.get("specs") or {},
                    source_material_key=str(item.get("source_material_key") or "").strip() or None,
                )
            )
            imported_model_count += 1

        imported_interface_count = 0
        for item in DEFAULT_PRODUCT_INTERFACES:
            target_series = series_by_code.get(str(item.get("series_code") or ""))
            if target_series is None:
                raise ArtifactValidationError(
                    f"Missing series seed for product interface {item.get('interface_type')}: {item.get('series_code')}"
                )
            session.add(
                ProductInterface(
                    catalog_version=version,
                    is_published=False,
                    series_id=target_series.id,
                    series_code=target_series.code,
                    interface_type=str(item.get("interface_type") or ""),
                    protocol=str(item.get("protocol") or "").strip() or None,
                    signal_spec=item.get("signal_spec") or {},
                    notes=str(item.get("notes") or "").strip() or None,
                    source_material_key=str(item.get("source_material_key") or "").strip() or None,
                    sort_order=int(item.get("sort_order") or 0),
                )
            )
            imported_interface_count += 1

        await session.flush()
        if publish:
            await self.publish_catalog_version(
                session=session,
                catalog_version=version,
                commit=False,
            )
        if commit:
            await session.commit()
        return {
            "catalog_version": version,
            "imported_series_count": imported_count,
            "imported_model_count": imported_model_count,
            "imported_interface_count": imported_interface_count,
            "published": publish,
            "reused_existing": False,
        }

    async def publish_catalog_version(
        self,
        *,
        session: AsyncSession,
        catalog_version: str,
        commit: bool = True,
    ) -> dict[str, Any]:
        rows = await session.scalars(select(ProductSeries).where(ProductSeries.catalog_version == catalog_version))
        target_rows = rows.all()
        if not target_rows:
            raise ArtifactNotFoundError("Catalog version not found")
        target_models = (
            await session.scalars(select(ProductModel).where(ProductModel.catalog_version == catalog_version))
        ).all()
        target_interfaces = (
            await session.scalars(select(ProductInterface).where(ProductInterface.catalog_version == catalog_version))
        ).all()

        all_rows = await session.scalars(select(ProductSeries))
        for row in all_rows:
            row.is_published = row.catalog_version == catalog_version
        all_families = await session.scalars(select(ProductFamily))
        for family in all_families:
            family.is_published = family.catalog_version == catalog_version
        all_compatibility = await session.scalars(select(ProductCompatibility))
        for item in all_compatibility:
            item.is_published = item.catalog_version == catalog_version
        all_models = await session.scalars(select(ProductModel))
        for model in all_models:
            model.is_published = model.catalog_version == catalog_version
        all_interfaces = await session.scalars(select(ProductInterface))
        for interface in all_interfaces:
            interface.is_published = interface.catalog_version == catalog_version
        await session.flush()
        if commit:
            await session.commit()
        return {
            "catalog_version": catalog_version,
            "published_series_count": len(target_rows),
            "published_model_count": len(target_models),
            "published_interface_count": len(target_interfaces),
        }

    async def get_series_map(
        self,
        *,
        session: AsyncSession,
        catalog_version: str | None = None,
        published_only: bool = True,
    ) -> dict[str, ProductSeries]:
        effective_version = catalog_version or await self.get_active_catalog_version(session=session)
        if effective_version is None:
            return {}
        rows = await self.list_series(
            session=session,
            published_only=published_only,
            catalog_version=effective_version,
        )
        return {row.code: row for row in rows}

    async def shortlist_primary_products(
        self,
        *,
        session: AsyncSession,
        project: Project,
        requirement_card: RequirementCard | None,
        limit: int = 3,
    ) -> tuple[CatalogSignals, list[CatalogCandidate]]:
        await self.ensure_seed_catalog(session=session)
        series_rows = await self.list_series(session=session, published_only=True)
        signals = self.build_requirement_signals(project=project, requirement_card=requirement_card)
        candidates: list[CatalogCandidate] = []
        for row in series_rows:
            if str(row.role_type or "support") != "primary":
                continue
            score, reasons = self._score_primary_series(series=row, signals=signals)
            selected_config = self._select_standard_config(series=row, bypass_required=signals.bypass_required)
            candidates.append(CatalogCandidate(series=row, selected_config=selected_config, score=score, reasons=reasons))

        candidates.sort(
            key=lambda item: (
                item.score,
                item.series.series_name,
                item.series.code,
            ),
            reverse=True,
        )
        return signals, candidates[:limit]

    def build_requirement_signals(
        self,
        *,
        project: Project,
        requirement_card: RequirementCard | None,
    ) -> CatalogSignals:
        content = requirement_card.content if requirement_card and isinstance(requirement_card.content, dict) else {}
        parts: list[str] = [
            str(project.name or "").strip(),
            str(project.industry or "").strip(),
            str(project.product_line or "").strip(),
            str(project.description or "").strip(),
        ]
        matching_signals: list[str] = []
        for key, value in content.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                parts.append(str(value).strip())
                if key in {"industry", "product_line", "motor_type", "voltage_level", "power_rating", "communication_protocol"}:
                    matching_signals.append(f"{key}={value}")
            elif isinstance(value, list):
                flattened = ", ".join(str(item).strip() for item in value if str(item).strip())
                if flattened:
                    parts.append(flattened)
        raw_text = "\n".join(part for part in parts if part).strip()
        normalized = raw_text.lower()
        if project.product_line:
            matching_signals.append(f"project.product_line={project.product_line}")
        if project.industry:
            matching_signals.append(f"project.industry={project.industry}")

        application_tokens = [token for token in _LOAD_TOKENS if token in raw_text]
        return CatalogSignals(
            raw_text=raw_text,
            normalized_text=normalized,
            matching_signals=matching_signals,
            voltage_value=self._extract_number(raw_text, _VOLTAGE_PATTERN),
            power_value=self._extract_number(raw_text, _POWER_PATTERN),
            quantity=int(self._extract_number(raw_text, _QUANTITY_PATTERN) or 1),
            motor_type="同步电机" if "同步" in raw_text else "异步电机" if "异步" in raw_text else "电机",
            bypass_required=any(token in raw_text for token in ("旁路", "工频", "切换")),
            requested_protocol=self._detect_protocol(raw_text),
            application_tokens=application_tokens,
            product_line=str(project.product_line or "").strip().lower(),
        )

    def _load_material_manifest(self, manifest_path: str) -> dict[str, Any]:
        path = Path(manifest_path).expanduser()
        if not path.exists():
            raise ArtifactNotFoundError("Material manifest not found")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError("Material manifest is not valid JSON") from exc
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ArtifactValidationError("Material manifest missing entries list")
        return payload

    def _build_material_record(self, entry: dict[str, Any], *, source_kind: str | None = None) -> dict[str, Any]:
        record, _ = self._prepare_material_record(entry, source_kind=source_kind)
        return record

    def _prepare_material_record(
        self,
        entry: dict[str, Any],
        *,
        source_kind: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(entry, dict):
            raise ArtifactValidationError("Material manifest entry must be an object")

        material_key = str(entry.get("sample_id") or entry.get("material_key") or "").strip()
        document_name = str(entry.get("document_name") or entry.get("file_name") or "").strip()
        if not material_key or not document_name:
            raise ArtifactValidationError("Material manifest entry missing sample_id/material_key or file_name/document_name")

        file_size_bytes = entry.get("file_size_bytes")
        if file_size_bytes is not None:
            try:
                file_size_bytes = int(file_size_bytes)
            except (TypeError, ValueError) as exc:
                raise ArtifactValidationError("Material manifest file_size_bytes must be numeric") from exc

        resolved_source_path = str(entry.get("file_path") or entry.get("source_path") or "").strip() or None
        resolved_source_kind = str(source_kind or entry.get("source_kind") or "private_sample").strip() or "private_sample"
        record = {
            "material_key": material_key,
            "family_code": self._resolve_material_family_code(entry),
            "material_type": self._resolve_material_type(entry),
            "document_name": document_name,
            "source_path": resolved_source_path,
            "source_kind": resolved_source_kind,
            "availability_status": self._resolve_material_status(entry),
            "file_format": str(entry.get("file_format") or "").strip() or None,
            "file_size_bytes": file_size_bytes,
            "assigned_track": str(entry.get("assigned_track") or "").strip() or None,
            "suggested_track": str(entry.get("suggested_track") or "").strip() or None,
            "priority_tier": str(entry.get("priority_tier") or "").strip() or None,
            "tags": self._build_material_tags(entry),
            "notes": str(entry.get("manual_notes") or entry.get("notes") or "").strip() or None,
            "details": self._build_material_details(entry),
        }
        diagnostics = {
            "explicit_family_code": bool(str(entry.get("family_code") or "").strip()),
            "explicit_material_type": bool(str(entry.get("material_type") or "").strip()),
            "explicit_availability_status": bool(str(entry.get("availability_status") or "").strip()),
            "source_path_exists": Path(resolved_source_path).expanduser().exists() if resolved_source_path else None,
        }
        return record, diagnostics

    def _find_duplicate_material_keys(self, records: list[dict[str, Any]]) -> list[str]:
        key_counter = Counter(str(record.get("material_key") or "").strip() for record in records)
        return sorted(key for key, count in key_counter.items() if key and count > 1)

    def _build_material_manifest_issue(
        self,
        *,
        issue_type: str,
        severity: str,
        message: str,
        material_key: str | None = None,
        document_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "issue_type": issue_type,
            "severity": severity,
            "message": message,
            "material_key": material_key,
            "document_name": document_name,
        }

    def _resolve_material_family_code(self, entry: dict[str, Any]) -> str | None:
        explicit = str(entry.get("family_code") or "").strip()
        if explicit:
            return explicit
        return self._infer_material_family_code(entry)

    def _resolve_material_type(self, entry: dict[str, Any]) -> str:
        explicit = str(entry.get("material_type") or "").strip()
        if explicit:
            return explicit
        return self._infer_material_type(entry)

    def _resolve_material_status(self, entry: dict[str, Any]) -> str:
        explicit = str(entry.get("availability_status") or "").strip()
        if explicit:
            return explicit
        return self._infer_material_status(entry)

    def _build_material_details(self, entry: dict[str, Any]) -> dict[str, Any]:
        details = dict(entry.get("details") or {}) if isinstance(entry.get("details"), dict) else {}
        details.setdefault("document_type_hint", str(entry.get("document_type_hint") or "").strip() or None)
        details.setdefault("industry", str(entry.get("industry") or "").strip() or None)
        details.setdefault("product_line", str(entry.get("product_line") or "").strip() or None)
        details.setdefault("solution_family", str(entry.get("solution_family") or "").strip() or None)
        details.setdefault("key_equipment", list(entry.get("key_equipment") or []))
        details.setdefault("quality_tier", str(entry.get("quality_tier") or "").strip() or None)
        details.setdefault("detected_profile", str(entry.get("detected_profile") or "").strip() or None)
        details.setdefault("ingestion_recommendation", str(entry.get("ingestion_recommendation") or "").strip() or None)
        details.setdefault("phase_b_track", str(entry.get("phase_b_track") or "").strip() or None)
        details.setdefault("high_risk_content_flags", list(entry.get("high_risk_content_flags") or []))
        details.setdefault("metrics", entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {})
        return details

    def _infer_material_family_code(self, entry: dict[str, Any]) -> str | None:
        texts = [
            str(entry.get("solution_family") or ""),
            str(entry.get("product_line") or ""),
            str(entry.get("file_name") or entry.get("document_name") or ""),
            str(entry.get("manual_notes") or entry.get("notes") or ""),
        ]
        normalized_texts = [text.strip().casefold() for text in texts if str(text).strip()]
        if not normalized_texts:
            return None

        family_aliases: list[tuple[str, str]] = []
        for family in DEFAULT_PRODUCT_FAMILIES:
            code = str(family.get("code") or "").strip()
            if not code or code == "support_equipment":
                continue
            values = [code, str(family.get("name") or ""), str(family.get("display_name") or "")]
            values.extend(str(alias) for alias in family.get("aliases") or [])
            for value in values:
                token = value.strip()
                if token:
                    family_aliases.append((token.casefold(), code))
        family_aliases.sort(key=lambda item: len(item[0]), reverse=True)

        for text in normalized_texts:
            for alias, code in family_aliases:
                if alias in text:
                    return code
        return None

    def _infer_material_type(self, entry: dict[str, Any]) -> str:
        hint = str(entry.get("document_type_hint") or "").strip().casefold()
        if hint and hint not in {"unknown", "other"}:
            return hint.replace(" ", "_")

        document_name = str(entry.get("file_name") or entry.get("document_name") or "").strip().casefold()
        for material_type, patterns in _MATERIAL_TYPE_HINTS:
            if any(pattern.casefold() in document_name for pattern in patterns):
                return material_type
        return "proposal_sample"

    def _infer_material_status(self, entry: dict[str, Any]) -> str:
        assigned_track = str(entry.get("assigned_track") or "").strip().casefold()
        if assigned_track == "needs_review":
            return "review_needed"
        if assigned_track == "ocr_asset_only":
            return "asset_only"
        return "available"

    def _build_material_tags(self, entry: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        raw_tags = entry.get("tags")
        if isinstance(raw_tags, list):
            for raw_tag in raw_tags:
                tag = str(raw_tag or "").strip()
                if tag and tag not in tags:
                    tags.append(tag)
        for raw_value in (
            entry.get("file_format"),
            entry.get("assigned_track"),
            entry.get("suggested_track"),
            entry.get("quality_tier"),
            entry.get("phase_b_track"),
            entry.get("industry"),
            entry.get("product_line"),
            entry.get("detected_profile"),
            entry.get("ingestion_recommendation"),
        ):
            value = str(raw_value or "").strip()
            if value and value.lower() != "unknown" and value not in tags:
                tags.append(value)
        return tags

    async def _resolve_target_family_codes(
        self,
        *,
        session: AsyncSession,
        family_codes: list[str] | None,
        project_id: UUID | None,
    ) -> list[str]:
        normalized = self._dedupe_non_empty(family_codes or [])
        if normalized:
            return normalized

        project_scoped = await self._infer_project_target_family_codes(session=session, project_id=project_id)
        if project_scoped:
            return project_scoped

        material_scoped = await self._infer_available_material_family_codes(session=session)
        if material_scoped:
            return material_scoped

        return list(_DEFAULT_READINESS_FAMILY_CODES)

    async def _infer_project_target_family_codes(
        self,
        *,
        session: AsyncSession,
        project_id: UUID | None,
    ) -> list[str]:
        if project_id is None:
            return []

        project = await session.get(Project, project_id)
        if project is None:
            return []

        requirement_card = await self._resolve_latest_requirement_card(session=session, project_id=project_id)
        if requirement_card is None:
            return []

        try:
            _, candidates = await self.shortlist_primary_products(
                session=session,
                project=project,
                requirement_card=requirement_card,
                limit=3,
            )
        except ArtifactValidationError:
            return []

        return self._dedupe_non_empty(
            str(candidate.series.family_code or candidate.series.code or "").strip()
            for candidate in candidates
        )

    async def _infer_available_material_family_codes(
        self,
        *,
        session: AsyncSession,
    ) -> list[str]:
        rows = await self.list_materials(session=session, availability_status="available")
        family_codes = self._dedupe_non_empty(
            str(row.family_code or "").strip()
            for row in rows
            if str(getattr(row, "source_kind", "") or "").strip() not in _NON_GATE_SOURCE_KINDS
        )
        non_support = [code for code in family_codes if code != "support_equipment"]
        if non_support:
            return non_support
        return [] if family_codes == ["support_equipment"] else family_codes

    async def _resolve_latest_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
    ) -> RequirementCard | None:
        result = await session.scalars(
            select(RequirementCard)
            .where(RequirementCard.project_id == project_id)
            .order_by(RequirementCard.version.desc(), RequirementCard.created_at.desc())
            .limit(1)
        )
        return result.first()

    def _dedupe_non_empty(self, values: Any) -> list[str]:
        normalized: list[str] = []
        for item in values:
            value = str(item or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _build_material_readiness_check(
        self,
        *,
        check_key: str,
        label: str,
        matches: list[ProductMaterial],
        required_count: int,
        actual_count: int,
        matched_family_codes: list[str],
        missing_detail: str | None,
    ) -> dict[str, Any]:
        return {
            "check_key": check_key,
            "label": label,
            "passed": actual_count >= required_count,
            "required_count": required_count,
            "actual_count": actual_count,
            "matched_family_codes": matched_family_codes,
            "matched_material_keys": [str(row.material_key) for row in matches],
            "matched_document_names": [str(row.document_name) for row in matches],
            "missing_detail": missing_detail,
        }

    def _score_primary_series(self, *, series: ProductSeries, signals: CatalogSignals) -> tuple[float, list[str]]:
        score = 0.15
        reasons: list[str] = []
        lowered_name = f"{series.series_name} {series.family} {series.code}".lower()

        if signals.product_line and signals.product_line in lowered_name:
            score += 0.42
            reasons.append(f"产品线 {signals.product_line} 与目录系列匹配")
        if any(token.lower() in signals.normalized_text for token in [series.family.lower(), series.code.lower()]):
            score += 0.18
            reasons.append("需求文本直接命中目录系列或产品族")

        applicable_motors = {str(item).strip() for item in (series.applicable_motors or []) if str(item).strip()}
        if signals.motor_type in applicable_motors:
            score += 0.18
            reasons.append(f"支持 {signals.motor_type} 场景")
        elif applicable_motors and signals.motor_type != "电机":
            score -= 0.12

        applicable_loads = {str(item).strip() for item in (series.applicable_loads or []) if str(item).strip()}
        matched_loads = [token for token in signals.application_tokens if token in applicable_loads]
        if matched_loads:
            score += 0.16
            reasons.append(f"适用负载覆盖 {', '.join(matched_loads)}")

        if signals.voltage_value is not None:
            requested_voltage = self._format_voltage_level(signals.voltage_value)
            if requested_voltage in {str(item) for item in (series.voltage_levels or [])}:
                score += 0.14
                reasons.append(f"支持 {requested_voltage} 电压等级")
            else:
                score -= 0.08

        if signals.power_value is not None and self._power_supported(series=series, power_value=signals.power_value):
            score += 0.14
            reasons.append("功率范围匹配")

        if signals.requested_protocol:
            protocols = {str(item) for item in (series.communication_protocols or [])}
            if signals.requested_protocol in protocols:
                score += 0.08
                reasons.append(f"支持 {signals.requested_protocol} 通讯协议")

        if signals.bypass_required and self._has_bypass_config(series):
            score += 0.08
            reasons.append("支持旁路或工频切换配置")

        return score, reasons or ["目录系列满足基础场景要求"]

    def _has_bypass_config(self, series: ProductSeries) -> bool:
        return any("旁路" in str(config.config_name or "") for config in (series.standard_configs or []))

    def _select_standard_config(
        self,
        *,
        series: ProductSeries,
        bypass_required: bool,
    ) -> ProductStandardConfig | None:
        configs = list(series.standard_configs or [])
        if not configs:
            return None
        if bypass_required:
            for config in configs:
                if "旁路" in str(config.config_name or ""):
                    return config
        for config in configs:
            if "标准" in str(config.config_name or ""):
                return config
        return configs[0]

    def _extract_number(self, raw_text: str, pattern: re.Pattern[str]) -> float | None:
        match = pattern.search(raw_text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    def _detect_protocol(self, raw_text: str) -> str | None:
        for protocol, pattern in _PROTOCOL_PATTERNS:
            if pattern.search(raw_text):
                return protocol
        return None

    def _format_voltage_level(self, voltage_value: float) -> str:
        return f"{voltage_value:g}kV"

    def _power_supported(self, *, series: ProductSeries, power_value: float) -> bool:
        min_power = series.min_power_kw
        max_power = series.max_power_kw
        if min_power is not None and power_value < float(min_power):
            return False
        if max_power is not None and power_value > float(max_power):
            return False
        return True
