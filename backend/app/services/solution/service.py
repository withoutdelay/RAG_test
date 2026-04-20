from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_compatibility import ProductCompatibility
from app.models.product_interface import ProductInterface
from app.models.product_material import ProductMaterial
from app.models.product_model import ProductModel
from app.models.product_series import ProductSeries
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.models.solution_snapshot import SolutionSnapshot
from app.services.catalog import CatalogCandidate, CatalogSignals, ProductCatalogService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError

MATERIAL_SECTION_TYPE_MAP: dict[str, tuple[str, ...]] = {
    "product_manual": (
        "overall_solution",
        "design_basis",
        "main_circuit_scheme",
        "starter_spec",
        "motor_spec",
        "communication_interface",
        "control_logic",
    ),
    "standard_bom": (
        "bom_or_supply_list",
        "supply_scope",
    ),
    "interface_schedule": (
        "communication_interface",
        "control_logic",
        "protection_interlock",
    ),
    "selection_rule": (
        "design_basis",
        "overall_solution",
        "main_circuit_scheme",
    ),
    "diagram_template": (
        "overall_solution",
        "main_circuit_scheme",
        "communication_interface",
        "control_logic",
        "installation_conditions",
    ),
    "service_plan": (
        "service_support",
    ),
    "proposal_sample": (
        "overall_solution",
        "design_basis",
        "main_circuit_scheme",
        "communication_interface",
        "bom_or_supply_list",
        "supply_scope",
        "commissioning_acceptance",
        "service_support",
    ),
}

MATERIAL_TYPE_PRIORITY = {
    "product_manual": 5.0,
    "standard_bom": 4.0,
    "interface_schedule": 4.0,
    "selection_rule": 3.5,
    "diagram_template": 3.2,
    "proposal_sample": 2.2,
    "service_plan": 1.8,
}


class SolutionService:
    def __init__(self, *, product_catalog: ProductCatalogService | None = None) -> None:
        self.product_catalog = product_catalog or ProductCatalogService()

    async def design_solution(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None = None,
        force_refresh: bool = True,
    ) -> SolutionSnapshot:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        requirement_card = await self._resolve_requirement_card(
            session=session,
            project_id=project_id,
            requirement_card_id=requirement_card_id,
        )

        if not force_refresh:
            existing = await self._fetch_latest_solution(session=session, project_id=project_id)
            if existing is not None:
                return existing

        signals, candidates = await self.product_catalog.shortlist_primary_products(
            session=session,
            project=project,
            requirement_card=requirement_card,
            limit=3,
        )
        if not candidates:
            raise ArtifactValidationError("No published catalog candidate found for current project")

        primary_candidate = candidates[0]
        source_catalog_version = str(primary_candidate.series.catalog_version or "")
        series_map = await self.product_catalog.get_series_map(
            session=session,
            catalog_version=source_catalog_version or None,
            published_only=True,
        )
        compatibility_rules = await self.product_catalog.list_compatibility_rules(
            session=session,
            published_only=True,
            catalog_version=source_catalog_version or None,
            source_family_code=str(primary_candidate.series.family_code or primary_candidate.series.code),
        )
        catalog_models = await self.product_catalog.list_models(
            session=session,
            published_only=True,
            catalog_version=source_catalog_version or None,
        )
        catalog_interfaces = await self.product_catalog.list_interfaces(
            session=session,
            published_only=True,
            catalog_version=source_catalog_version or None,
        )
        catalog_materials = await self.product_catalog.list_materials(
            session=session,
            availability_status="available",
        )
        payload = self._build_solution_payload(
            signals=signals,
            candidates=candidates,
            series_map=series_map,
            source_catalog_version=source_catalog_version,
            compatibility_rules=compatibility_rules,
            catalog_models_by_series=self._group_models_by_series(catalog_models),
            catalog_interfaces_by_series=self._group_interfaces_by_series(catalog_interfaces),
            catalog_material_rows=catalog_materials,
        )

        next_version = await self._next_version(session=session, project_id=project_id)
        snapshot = SolutionSnapshot(
            project_id=project.id,
            requirement_card_id=requirement_card.id if requirement_card else None,
            version=next_version,
            status="draft",
            solution_summary=payload["solution_summary"],
            selected_products=payload["selected_products"],
            interface_plan=payload["interface_plan"],
            key_constraints=payload["key_constraints"],
            open_questions=payload["open_questions"],
            suggested_chapters=payload["suggested_chapters"],
            selection_reason=payload["selection_reason"],
            source_catalog_version=payload["source_catalog_version"],
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    async def get_latest_solution(self, *, session: AsyncSession, project_id: UUID) -> SolutionSnapshot:
        snapshot = await self._fetch_latest_solution(session=session, project_id=project_id)
        if snapshot is None:
            raise ArtifactNotFoundError("Solution snapshot not found")
        return snapshot

    async def list_solutions(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        limit: int = 10,
    ) -> list[SolutionSnapshot]:
        rows = await session.scalars(
            select(SolutionSnapshot)
            .where(SolutionSnapshot.project_id == project_id)
            .order_by(SolutionSnapshot.version.desc(), SolutionSnapshot.created_at.desc())
            .limit(limit)
        )
        return rows.all()

    async def update_solution(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        snapshot_id: UUID,
        payload: dict[str, Any],
    ) -> SolutionSnapshot:
        snapshot = await self._get_snapshot(session=session, project_id=project_id, snapshot_id=snapshot_id)

        for field in (
            "solution_summary",
            "selected_products",
            "interface_plan",
            "key_constraints",
            "open_questions",
            "suggested_chapters",
            "selection_reason",
            "confirmation_notes",
        ):
            if field in payload and payload[field] is not None:
                setattr(snapshot, field, payload[field])

        snapshot.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    async def confirm_solution(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        snapshot_id: UUID,
        confirmation_notes: str | None = None,
        confirmed_by_user: bool = True,
    ) -> SolutionSnapshot:
        snapshot = await self._get_snapshot(session=session, project_id=project_id, snapshot_id=snapshot_id)
        snapshot.confirmed_by_user = bool(confirmed_by_user)
        snapshot.status = "confirmed" if confirmed_by_user else "draft"
        snapshot.confirmed_at = datetime.now(timezone.utc) if confirmed_by_user else None
        if confirmation_notes is not None:
            snapshot.confirmation_notes = confirmation_notes
        snapshot.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None,
    ) -> RequirementCard | None:
        if requirement_card_id is not None:
            card = await session.get(RequirementCard, requirement_card_id)
            if not card or card.project_id != project_id:
                raise ArtifactValidationError("Requirement card not found for this project")
            return card

        result = await session.scalars(
            select(RequirementCard)
            .where(RequirementCard.project_id == project_id)
            .order_by(RequirementCard.version.desc(), RequirementCard.created_at.desc())
            .limit(1)
        )
        return result.first()

    async def _fetch_latest_solution(self, *, session: AsyncSession, project_id: UUID) -> SolutionSnapshot | None:
        result = await session.scalars(
            select(SolutionSnapshot)
            .where(SolutionSnapshot.project_id == project_id)
            .order_by(SolutionSnapshot.version.desc(), SolutionSnapshot.created_at.desc())
            .limit(1)
        )
        return result.first()

    async def _next_version(self, *, session: AsyncSession, project_id: UUID) -> int:
        max_version = await session.scalar(
            select(func.max(SolutionSnapshot.version)).where(SolutionSnapshot.project_id == project_id)
        )
        return int(max_version or 0) + 1

    async def _get_snapshot(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        snapshot_id: UUID,
    ) -> SolutionSnapshot:
        snapshot = await session.get(SolutionSnapshot, snapshot_id)
        if not snapshot or snapshot.project_id != project_id:
            raise ArtifactNotFoundError("Solution snapshot not found")
        return snapshot

    def _build_solution_payload(
        self,
        *,
        signals: CatalogSignals,
        candidates: list[CatalogCandidate],
        series_map: dict[str, ProductSeries],
        source_catalog_version: str,
        compatibility_rules: list[ProductCompatibility] | None = None,
        catalog_models_by_series: dict[str, list[ProductModel]] | None = None,
        catalog_interfaces_by_series: dict[str, list[ProductInterface]] | None = None,
        catalog_material_rows: list[ProductMaterial] | None = None,
    ) -> dict[str, Any]:
        primary_candidate = candidates[0]
        primary_series = primary_candidate.series
        selected_config = primary_candidate.selected_config
        quantity = max(int(signals.quantity or 1), 1)
        requested_voltage = self._resolve_voltage_label(primary_series=primary_series, voltage_value=signals.voltage_value)
        supported_protocols = [str(item).strip() for item in (primary_series.communication_protocols or []) if str(item).strip()]
        selected_protocol = self._select_protocol(
            requested_protocol=signals.requested_protocol,
            supported_protocols=supported_protocols,
        )

        selected_products: list[dict[str, Any]] = [
            self._build_series_entry(
                series=primary_series,
                role="主驱动",
                quantity=quantity,
                rated_voltage=requested_voltage,
                rated_power_kw=signals.power_value,
                config_name=selected_config.config_name if selected_config else None,
                rationale=self._build_primary_rationale(primary_candidate=primary_candidate, signals=signals),
            )
        ]

        component_series_rows: list[ProductSeries] = []
        selected_series_codes = {primary_series.code}
        component_specs = list(selected_config.components or []) if selected_config else []
        for component in component_specs:
            component_code = str(component.get("series_code") or "").strip()
            component_series = series_map.get(component_code)
            if component_series is not None:
                component_series_rows.append(component_series)
                selected_series_codes.add(component_series.code)
            selected_products.append(
                self._build_component_entry(
                    component=component,
                    series=component_series,
                    base_quantity=quantity,
                    rated_voltage=requested_voltage,
                    rated_power_kw=signals.power_value,
                )
            )

        compatibility_actions = self._apply_compatibility_rules(
            compatibility_rules=compatibility_rules or [],
            selected_products=selected_products,
            selected_series_codes=selected_series_codes,
            component_series_rows=component_series_rows,
            series_map=series_map,
            signals=signals,
            primary_series=primary_series,
            selected_config_name=selected_config.config_name if selected_config else None,
            rated_voltage=requested_voltage,
            rated_power_kw=signals.power_value,
            quantity=quantity,
        )

        catalog_model_matches = self._build_catalog_model_matches(
            selected_products=selected_products,
            catalog_models_by_series=catalog_models_by_series or {},
        )
        catalog_interface_entries = self._build_catalog_interface_entries(
            selected_products=selected_products,
            series_map=series_map,
            catalog_interfaces_by_series=catalog_interfaces_by_series or {},
        )
        catalog_material_entries = self._build_catalog_material_entries(
            primary_series=primary_series,
            component_series_rows=component_series_rows,
            material_rows=catalog_material_rows or [],
        )

        interface_plan = self._build_interface_plan(
            primary_series=primary_series,
            selected_products=selected_products,
            series_map=series_map,
            selected_protocol=selected_protocol,
            requested_protocol=signals.requested_protocol,
            selected_config_name=selected_config.config_name if selected_config else None,
            catalog_interface_entries=catalog_interface_entries,
        )

        key_constraints = self._build_key_constraints(
            primary_series=primary_series,
            component_series_rows=component_series_rows,
            selected_protocol=selected_protocol,
            requested_voltage=requested_voltage,
        )
        open_questions = self._build_open_questions(
            signals=signals,
            primary_series=primary_series,
            selected_protocol=selected_protocol,
            selected_series_codes=selected_series_codes,
        )
        suggested_chapters = self._dedupe_list(
            list(primary_series.default_chapters or [])
            + [chapter for series in component_series_rows for chapter in list(series.default_chapters or [])]
        )

        why_selected = list(primary_candidate.reasons)
        if selected_config and selected_config.description:
            why_selected.append(f"{selected_config.config_name} 已覆盖当前场景的标准配套边界。")
        why_selected.extend(self._build_compatibility_why_selected(compatibility_actions=compatibility_actions))
        if catalog_model_matches:
            why_selected.append(
                f"已为当前方案绑定 {len(catalog_model_matches)} 条目录型号证据，可直接下沉到大纲与章节生成。"
            )
        if catalog_interface_entries:
            why_selected.append(
                f"已为当前方案绑定 {len(catalog_interface_entries)} 条目录接口定义，可直接进入接口章节与联锁描述。"
            )
        if catalog_material_entries:
            why_selected.append(
                f"已绑定 {len(catalog_material_entries)} 份产品资料库材料，可按章节类型优先锚定真实样本与产品手册。"
            )
        why_selected.append("主设备、配套设备、型号、接口和章节建议已拆成结构化字段，可直接进入 Outline 与章节生成。")

        catalog_source_material_keys = self._dedupe_list(
            [
                *[
                    str(item.get("source_material_key") or "").strip()
                    for item in catalog_model_matches
                    if str(item.get("source_material_key") or "").strip()
                ],
                *[
                    str(item.get("source_material_key") or "").strip()
                    for item in catalog_interface_entries
                    if str(item.get("source_material_key") or "").strip()
                ],
                *[
                    str(item.get("material_key") or "").strip()
                    for item in catalog_material_entries
                    if str(item.get("material_key") or "").strip()
                ],
            ]
        )

        risk_flags = self._dedupe_list(
            open_questions
            + self._extract_blocking_risks([primary_series, *component_series_rows])
            + self._build_compatibility_risk_flags(compatibility_actions=compatibility_actions)
            + self._build_catalog_material_risk_flags(
                primary_series=primary_series,
                catalog_material_entries=catalog_material_entries,
            )
        )

        power_label = self._format_power_label(signals.power_value)
        summary = (
            f"本项目方案围绕 {primary_series.series_name} 组织主回路、接口与供货配置，"
            f"适配 {signals.motor_type} 的 {requested_voltage} / {power_label} 场景。"
        )
        if selected_config:
            summary += f" 当前按 {selected_config.config_name} 组织设备成套。"

        return {
            "solution_summary": summary,
            "selected_products": selected_products,
            "interface_plan": interface_plan,
            "key_constraints": key_constraints,
            "open_questions": open_questions,
            "suggested_chapters": suggested_chapters or list(primary_series.default_chapters or []),
            "selection_reason": {
                "matching_signals": signals.matching_signals,
                "why_selected": why_selected,
                "risk_flags": risk_flags,
                "source_mode": "catalog_plus_requirement_card",
                "catalog_version": source_catalog_version,
                "compatibility_actions": compatibility_actions,
                "catalog_model_matches": catalog_model_matches,
                "catalog_material_entries": catalog_material_entries,
                "catalog_source_material_keys": catalog_source_material_keys,
                "candidate_scores": [
                    {
                        "series_code": candidate.series.code,
                        "series_name": candidate.series.series_name,
                        "score": round(float(candidate.score), 4),
                        "reasons": candidate.reasons,
                    }
                    for candidate in candidates
                ],
            },
            "source_catalog_version": source_catalog_version or None,
        }

    def _build_catalog_material_entries(
        self,
        *,
        primary_series: ProductSeries,
        component_series_rows: list[ProductSeries],
        material_rows: list[ProductMaterial],
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        if not material_rows:
            return []

        primary_family_code = str(primary_series.family_code or primary_series.code or "").strip()
        secondary_family_codes = {
            str(item.family_code or item.code or "").strip()
            for item in component_series_rows
            if str(item.family_code or item.code or "").strip()
        }

        def _score(row: ProductMaterial) -> tuple[float, float, str]:
            family_code = str(row.family_code or "").strip()
            score = MATERIAL_TYPE_PRIORITY.get(str(row.material_type or "").strip(), 1.0)
            if family_code and family_code == primary_family_code:
                score += 3.0
            elif family_code and family_code in secondary_family_codes:
                score += 1.6
            if str(row.availability_status or "").strip() == "available":
                score += 0.4
            details = row.details if isinstance(row.details, dict) else {}
            quality_tier = str(details.get("quality_tier") or "").strip().lower()
            if quality_tier == "high":
                score += 0.5
            elif quality_tier == "medium":
                score += 0.25
            assigned_track = str(row.assigned_track or "").strip()
            if assigned_track == "pilot_main":
                score += 0.3
            return (score, float(row.file_size_bytes or 0), str(row.document_name or ""))

        relevant_rows = [
            row
            for row in material_rows
            if str(row.availability_status or "").strip() == "available"
            and str(row.family_code or "").strip() in {primary_family_code, *secondary_family_codes}
        ]
        prioritized_rows = sorted(relevant_rows, key=_score, reverse=True)
        payloads: list[dict[str, Any]] = []
        for row in prioritized_rows[:limit]:
            details = row.details if isinstance(row.details, dict) else {}
            payloads.append(
                {
                    "material_key": row.material_key,
                    "document_name": row.document_name,
                    "family_code": row.family_code,
                    "material_type": row.material_type,
                    "availability_status": row.availability_status,
                    "assigned_track": row.assigned_track,
                    "priority_tier": row.priority_tier,
                    "solution_family": str(details.get("solution_family") or "").strip() or None,
                    "quality_tier": str(details.get("quality_tier") or "").strip() or None,
                    "key_equipment": [str(item).strip() for item in (details.get("key_equipment") or []) if str(item).strip()],
                    "preferred_section_types": list(
                        MATERIAL_SECTION_TYPE_MAP.get(str(row.material_type or "").strip(), MATERIAL_SECTION_TYPE_MAP["proposal_sample"])
                    ),
                    "notes": str(row.notes or "").strip() or None,
                }
            )
        return payloads

    def _build_catalog_material_risk_flags(
        self,
        *,
        primary_series: ProductSeries,
        catalog_material_entries: list[dict[str, Any]],
    ) -> list[str]:
        primary_family_code = str(primary_series.family_code or primary_series.code or "").strip()
        if not primary_family_code:
            return []
        primary_entries = [
            item
            for item in catalog_material_entries
            if str(item.get("family_code") or "").strip() == primary_family_code
        ]
        if not primary_entries:
            return [f"当前主产品族 {primary_family_code} 尚未挂接可用产品资料，章节仍会更多依赖历史方案样本。"]
        if not any(str(item.get("material_type") or "").strip() == "product_manual" for item in primary_entries):
            return [f"当前主产品族 {primary_family_code} 尚缺产品手册级资料，部分章节仍需依赖方案样本补全。"]
        return []

    def _build_series_entry(
        self,
        *,
        series: ProductSeries,
        role: str,
        quantity: int,
        rated_voltage: str,
        rated_power_kw: float | None,
        config_name: str | None,
        rationale: str,
    ) -> dict[str, Any]:
        return {
            "role": role,
            "series_code": series.code,
            "name": series.series_name,
            "family": series.family,
            "topology": series.topology,
            "rated_voltage": rated_voltage,
            "rated_power_kw": rated_power_kw,
            "quantity": quantity,
            "config": config_name or "标准配置",
            "vendor": series.vendor or "标准产品",
            "rationale": rationale,
        }

    def _build_component_entry(
        self,
        *,
        component: dict[str, Any],
        series: ProductSeries | None,
        base_quantity: int,
        rated_voltage: str,
        rated_power_kw: float | None,
    ) -> dict[str, Any]:
        unit_quantity = self._to_positive_int(component.get("quantity"), default=1)
        quantity = unit_quantity * max(base_quantity, 1)
        role = str(component.get("role") or (series.series_name if series else "配套设备"))
        config_name = str(component.get("config") or "标准配置")

        if series is None:
            series_code = str(component.get("series_code") or "")
            return {
                "role": role,
                "series_code": series_code,
                "name": role,
                "family": "配套设备",
                "topology": None,
                "rated_voltage": rated_voltage,
                "rated_power_kw": rated_power_kw,
                "quantity": quantity,
                "config": config_name,
                "vendor": "标准产品",
                "rationale": "目录中缺少配套设备详情，先按标准配置占位，待补齐正式产品卡。",
            }

        return self._build_series_entry(
            series=series,
            role=role,
            quantity=quantity,
            rated_voltage=rated_voltage if rated_voltage != "待确认电压" else self._first_voltage_level(series),
            rated_power_kw=rated_power_kw,
            config_name=config_name,
            rationale=self._build_component_rationale(role=role, series=series),
        )

    def _build_interface_plan(
        self,
        *,
        primary_series: ProductSeries,
        selected_products: list[dict[str, Any]],
        series_map: dict[str, ProductSeries],
        selected_protocol: str,
        requested_protocol: str | None,
        selected_config_name: str | None,
        catalog_interface_entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        primary_quantity = self._to_positive_int(selected_products[0].get("quantity"), default=1) if selected_products else 1
        io_totals = self._multiply_io_allocation(primary_series.io_allocation or {}, primary_quantity)
        for product in selected_products[1:]:
            component_code = str(product.get("series_code") or "").strip()
            component_series = series_map.get(component_code)
            if component_series is None:
                continue
            component_quantity = self._to_positive_int(product.get("quantity"), default=1)
            self._merge_io_allocation(
                target=io_totals,
                source=component_series.io_allocation or {},
                multiplier=component_quantity,
            )

        notes: list[str] = []
        if selected_config_name:
            notes.append(f"主驱动采用 {selected_config_name}，接口点数已按整套供货配置累计。")
        else:
            notes.append("接口点数已按主驱动与标准配套设备累计。")
        if requested_protocol and requested_protocol != selected_protocol:
            notes.append(f"需求偏好为 {requested_protocol}，当前目录优先落在 {selected_protocol}，需确认现场总线兼容性。")
        elif not requested_protocol:
            notes.append("DCS 标准协议尚未确认，当前先按目录默认协议生成。")
        if catalog_interface_entries:
            notes.append(f"已匹配 {len(catalog_interface_entries)} 条目录接口定义，可直接用于接口/联锁章节。")

        return {
            "dcs_protocol": selected_protocol,
            "io_allocation": io_totals,
            "catalog_interface_entries": catalog_interface_entries,
            "catalog_source_material_keys": self._dedupe_list(
                [
                    str(item.get("source_material_key") or "").strip()
                    for item in catalog_interface_entries
                    if str(item.get("source_material_key") or "").strip()
                ]
            ),
            "notes": " ".join(notes),
        }

    def _build_key_constraints(
        self,
        *,
        primary_series: ProductSeries,
        component_series_rows: list[ProductSeries],
        selected_protocol: str,
        requested_voltage: str,
    ) -> list[str]:
        constraints: list[str] = []
        if requested_voltage == "待确认电压":
            constraints.append("主驱动电压等级尚未锁定，详细一次系统边界需结合现场供电方案确认。")
        else:
            constraints.append(f"主驱动按 {requested_voltage} 等级配置，详细一次系统边界需结合现场供电条件最终校核。")

        for series in [primary_series, *component_series_rows]:
            for constraint in list(series.constraints or []):
                severity = "阻断项" if str(constraint.severity or "").lower() == "blocking" else "约束项"
                constraints.append(
                    f"{severity}：{series.series_name} 在“{constraint.condition}”条件下，{constraint.action}"
                )

        if selected_protocol == "Profibus-DP":
            constraints.append("Profibus-DP 接口需明确站点划分、GSD 文件版本、点表边界和调试前提。")
        elif selected_protocol == "IEC 61850":
            constraints.append("IEC 61850 场景需明确 IED 建模边界、SCD 文件交付方式和联调责任。")

        return self._dedupe_list(constraints)

    def _build_open_questions(
        self,
        *,
        signals: CatalogSignals,
        primary_series: ProductSeries,
        selected_protocol: str,
        selected_series_codes: set[str],
    ) -> list[str]:
        questions: list[str] = []
        if signals.power_value is None:
            questions.append("待确认电机额定功率，以便锁定主驱动容量和配套设备边界。")
        if signals.voltage_value is None:
            questions.append("待确认电机或供电电压等级，以便完成主回路设备选型。")
        if signals.motor_type == "电机":
            questions.append("待确认电机类型是否为同步电机，以便最终确认主驱动与励磁配置。")
        if signals.requested_protocol is None:
            questions.append("待确认 DCS 标准通讯协议，当前仅按目录默认接口生成方案。")
        elif not self._protocol_supported(primary_series, signals.requested_protocol):
            questions.append(
                f"需求偏好为 {signals.requested_protocol}，但当前推荐系列默认支持 {selected_protocol}，需确认协议兼容或网关方案。"
            )
        if signals.bypass_required and not self._has_bypass_scope(primary_series) and not self._bypass_unit_selected(selected_series_codes):
            questions.append("需求包含旁路或工频切换，但当前主驱动系列未显式声明旁路配置，需确认是否追加切换单元。")
        return self._dedupe_list(questions)

    def _build_primary_rationale(self, *, primary_candidate: CatalogCandidate, signals: CatalogSignals) -> str:
        if primary_candidate.reasons:
            return "；".join(primary_candidate.reasons)
        if signals.motor_type == "同步电机":
            return "同步电机场景优先采用目录中匹配的主驱动系列。"
        return "根据目录适用范围与需求关键词综合匹配得到当前主驱动基线。"

    def _build_component_rationale(self, *, role: str, series: ProductSeries) -> str:
        if series.code == "rectifier_transformer":
            return "LCI 主回路需要整流变压器提供隔离与降压配套。"
        if series.code == "excitation_cabinet":
            return "同步电机场景需独立励磁控制，保证启动与同步切换稳定。"
        if series.code == "bypass_cabinet":
            return "旁路或工频切换场景需要独立切换单元保证连续运行与检修边界。"
        return f"{role} 已按标准产品目录纳入供货范围。"

    def _build_compatibility_rationale(
        self,
        *,
        series: ProductSeries,
        relation_type: str,
        primary_series: ProductSeries,
        condition: str | None,
        description: str | None,
    ) -> str:
        if relation_type == "requires":
            prefix = f"{primary_series.series_name} 的产品族兼容规则要求补齐该配套设备。"
        else:
            prefix = f"{primary_series.series_name} 的产品族兼容规则建议补齐该配套设备。"
        details = " ".join(part for part in [condition, description] if part)
        if details:
            return f"{prefix} {details}"
        return prefix

    def _resolve_voltage_label(self, *, primary_series: ProductSeries, voltage_value: float | None) -> str:
        if voltage_value is not None:
            return f"{voltage_value:g}kV"
        return self._first_voltage_level(primary_series)

    def _first_voltage_level(self, series: ProductSeries) -> str:
        levels = [str(item).strip() for item in (series.voltage_levels or []) if str(item).strip()]
        return levels[0] if levels else "待确认电压"

    def _format_power_label(self, power_value: float | None) -> str:
        return f"{power_value:g}kW" if power_value is not None else "待确认功率"

    def _select_protocol(self, *, requested_protocol: str | None, supported_protocols: list[str]) -> str:
        if requested_protocol and requested_protocol in supported_protocols:
            return requested_protocol
        if supported_protocols:
            return supported_protocols[0]
        return requested_protocol or "待确认"

    def _protocol_supported(self, series: ProductSeries, protocol: str) -> bool:
        return protocol in {str(item).strip() for item in (series.communication_protocols or []) if str(item).strip()}

    def _has_bypass_scope(self, series: ProductSeries) -> bool:
        return any("旁路" in str(config.config_name or "") for config in (series.standard_configs or []))

    def _bypass_unit_selected(self, selected_series_codes: set[str]) -> bool:
        return any("bypass" in str(code).lower() or "旁路" in str(code) for code in selected_series_codes)

    def _multiply_io_allocation(self, allocation: dict[str, Any], multiplier: int) -> dict[str, int]:
        return {
            str(key): int(value) * multiplier
            for key, value in allocation.items()
            if self._looks_like_int(value)
        }

    def _merge_io_allocation(self, *, target: dict[str, int], source: dict[str, Any], multiplier: int) -> None:
        for key, value in source.items():
            if not self._looks_like_int(value):
                continue
            label = str(key)
            target[label] = int(target.get(label, 0)) + int(value) * multiplier

    def _extract_blocking_risks(self, series_rows: list[ProductSeries]) -> list[str]:
        risks: list[str] = []
        for series in series_rows:
            for constraint in list(series.constraints or []):
                if str(constraint.severity or "").lower() == "blocking":
                    risks.append(f"{series.series_name}：{constraint.action}")
        return risks

    def _apply_compatibility_rules(
        self,
        *,
        compatibility_rules: list[ProductCompatibility],
        selected_products: list[dict[str, Any]],
        selected_series_codes: set[str],
        component_series_rows: list[ProductSeries],
        series_map: dict[str, ProductSeries],
        signals: CatalogSignals,
        primary_series: ProductSeries,
        selected_config_name: str | None,
        rated_voltage: str,
        rated_power_kw: float | None,
        quantity: int,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for rule in compatibility_rules:
            preferred_codes = self._dedupe_list([str(item).strip() for item in (rule.preferred_series_codes or []) if str(item).strip()])
            optional_codes = self._dedupe_list([str(item).strip() for item in (rule.optional_series_codes or []) if str(item).strip()])
            applies = self._compatibility_rule_applies(
                rule=rule,
                signals=signals,
                selected_config_name=selected_config_name,
            )
            action: dict[str, Any] = {
                "source_family_code": rule.source_family_code,
                "target_family_code": rule.target_family_code,
                "relation_type": rule.relation_type,
                "condition": rule.condition,
                "applies": applies,
                "preferred_series_codes": preferred_codes,
                "optional_series_codes": optional_codes,
                "covered_series_codes": [],
                "added_series_codes": [],
                "missing_series_codes": [],
            }
            if not applies:
                actions.append(action)
                continue

            candidate_codes = list(preferred_codes)
            if self._compatibility_optional_applies(rule=rule, signals=signals, selected_config_name=selected_config_name):
                candidate_codes.extend(optional_codes)

            for series_code in candidate_codes:
                if series_code in selected_series_codes:
                    action["covered_series_codes"].append(series_code)
                    continue
                series = series_map.get(series_code)
                if series is None:
                    action["missing_series_codes"].append(series_code)
                    continue
                selected_products.append(
                    self._build_series_entry(
                        series=series,
                        role=series.series_name,
                        quantity=quantity,
                        rated_voltage=rated_voltage if rated_voltage != "待确认电压" else self._first_voltage_level(series),
                        rated_power_kw=rated_power_kw,
                        config_name="兼容规则补充",
                        rationale=self._build_compatibility_rationale(
                            series=series,
                            relation_type=str(rule.relation_type or "recommended"),
                            primary_series=primary_series,
                            condition=rule.condition,
                            description=rule.description,
                        ),
                    )
                )
                selected_series_codes.add(series.code)
                component_series_rows.append(series)
                action["added_series_codes"].append(series.code)

            actions.append(action)
        return actions

    def _compatibility_rule_applies(
        self,
        *,
        rule: ProductCompatibility,
        signals: CatalogSignals,
        selected_config_name: str | None,
    ) -> bool:
        relation_type = str(rule.relation_type or "recommended").lower()
        if relation_type == "requires":
            return True

        trigger_text = " ".join(
            part for part in [str(rule.condition or "").strip(), str(rule.description or "").strip(), str(selected_config_name or "").strip()]
            if part
        )
        if any(token in trigger_text for token in ("旁路", "切换", "检修不停机", "不停机")):
            return signals.bypass_required or ("旁路" in str(selected_config_name or ""))
        return False

    def _compatibility_optional_applies(
        self,
        *,
        rule: ProductCompatibility,
        signals: CatalogSignals,
        selected_config_name: str | None,
    ) -> bool:
        relation_type = str(rule.relation_type or "recommended").lower()
        if relation_type == "requires":
            return signals.bypass_required or ("旁路" in str(selected_config_name or ""))
        return self._compatibility_rule_applies(rule=rule, signals=signals, selected_config_name=selected_config_name)

    def _build_compatibility_why_selected(self, *, compatibility_actions: list[dict[str, Any]]) -> list[str]:
        reasons: list[str] = []
        for action in compatibility_actions:
            added = list(action.get("added_series_codes") or [])
            covered = list(action.get("covered_series_codes") or [])
            relation_type = str(action.get("relation_type") or "recommended")
            if added:
                verb = "要求补齐" if relation_type == "requires" else "建议补齐"
                reasons.append(
                    f"产品族兼容规则{verb} {', '.join(added)}，已自动纳入当前方案。"
                )
            elif covered:
                reasons.append(
                    f"产品族兼容规则已由当前配置覆盖 {', '.join(covered)}。"
                )
        return reasons

    def _build_compatibility_risk_flags(self, *, compatibility_actions: list[dict[str, Any]]) -> list[str]:
        flags: list[str] = []
        for action in compatibility_actions:
            missing = list(action.get("missing_series_codes") or [])
            if not missing:
                continue
            relation_type = str(action.get("relation_type") or "recommended")
            prefix = "缺少必需配套目录项" if relation_type == "requires" else "缺少推荐配套目录项"
            flags.append(f"{prefix}：{', '.join(missing)}")
        return flags

    def _group_models_by_series(self, rows: list[ProductModel]) -> dict[str, list[ProductModel]]:
        grouped: dict[str, list[ProductModel]] = {}
        for row in rows:
            grouped.setdefault(str(row.series_code or ""), []).append(row)
        return grouped

    def _group_interfaces_by_series(self, rows: list[ProductInterface]) -> dict[str, list[ProductInterface]]:
        grouped: dict[str, list[ProductInterface]] = {}
        for row in rows:
            grouped.setdefault(str(row.series_code or ""), []).append(row)
        return grouped

    def _build_catalog_model_matches(
        self,
        *,
        selected_products: list[dict[str, Any]],
        catalog_models_by_series: dict[str, list[ProductModel]],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for product in selected_products:
            series_code = str(product.get("series_code") or "").strip()
            if not series_code:
                continue
            model_rows = catalog_models_by_series.get(series_code) or []
            if not model_rows:
                continue
            matched_model = self._pick_preferred_model(
                product=product,
                model_rows=model_rows,
            )
            if matched_model is None:
                continue
            product["model_number"] = matched_model.model_number
            if matched_model.source_material_key:
                product["source_material_key"] = matched_model.source_material_key
            matches.append(
                {
                    "series_code": series_code,
                    "series_name": str(product.get("name") or series_code),
                    "role": str(product.get("role") or "设备"),
                    "family": str(product.get("family") or "").strip() or None,
                    "model_number": matched_model.model_number,
                    "rated_voltage": matched_model.rated_voltage,
                    "rated_power_kw": matched_model.rated_power_kw,
                    "rated_current": matched_model.rated_current,
                    "source_material_key": matched_model.source_material_key,
                    "specs_summary": self._summarize_dict(matched_model.specs, limit=3),
                }
            )
        return matches

    def _pick_preferred_model(
        self,
        *,
        product: dict[str, Any],
        model_rows: list[ProductModel],
    ) -> ProductModel | None:
        if not model_rows:
            return None
        if len(model_rows) == 1:
            return model_rows[0]

        requested_voltage = str(product.get("rated_voltage") or "").strip()
        requested_power = product.get("rated_power_kw")

        def _score(row: ProductModel) -> tuple[float, float]:
            score = 0.0
            if requested_voltage and str(row.rated_voltage or "").strip() == requested_voltage:
                score += 2.0
            if isinstance(requested_power, (int, float)) and row.rated_power_kw not in (None, ""):
                score += max(0.0, 1.0 - abs(float(row.rated_power_kw) - float(requested_power)) / max(float(requested_power), 1.0))
            return (score, float(row.rated_power_kw or 0.0))

        return max(model_rows, key=_score)

    def _build_catalog_interface_entries(
        self,
        *,
        selected_products: list[dict[str, Any]],
        series_map: dict[str, ProductSeries],
        catalog_interfaces_by_series: dict[str, list[ProductInterface]],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for product in selected_products:
            series_code = str(product.get("series_code") or "").strip()
            if not series_code:
                continue
            series = series_map.get(series_code)
            for row in catalog_interfaces_by_series.get(series_code) or []:
                signature = (series_code, str(row.interface_type or ""), int(row.sort_order or 0))
                if signature in seen:
                    continue
                seen.add(signature)
                entries.append(
                    {
                        "series_code": series_code,
                        "series_name": str(product.get("name") or (series.series_name if series else series_code)),
                        "role": str(product.get("role") or "设备"),
                        "family": str(product.get("family") or (series.family if series else "")).strip() or None,
                        "interface_type": row.interface_type,
                        "protocol": row.protocol,
                        "signal_spec": row.signal_spec or {},
                        "signal_summary": self._summarize_dict(row.signal_spec, limit=3),
                        "notes": row.notes,
                        "source_material_key": row.source_material_key,
                        "sort_order": int(row.sort_order or 0),
                    }
                )
        return entries

    def _summarize_dict(self, payload: dict[str, Any] | None, *, limit: int = 3) -> list[str]:
        if not isinstance(payload, dict):
            return []
        summary: list[str] = []
        for key, value in list(payload.items())[:limit]:
            summary.append(f"{key}={self._format_summary_value(value)}")
        return summary

    def _format_summary_value(self, value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value[:4])
        if isinstance(value, dict):
            return ", ".join(f"{key}:{self._format_summary_value(item)}" for key, item in list(value.items())[:3])
        return str(value)

    def _dedupe_list(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _to_positive_int(self, value: Any, *, default: int) -> int:
        if value is None:
            return default
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return default

    def _looks_like_int(self, value: Any) -> bool:
        try:
            int(value)
        except (TypeError, ValueError):
            return False
        return True
