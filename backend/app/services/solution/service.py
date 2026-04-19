from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_series import ProductSeries
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.models.solution_snapshot import SolutionSnapshot
from app.services.catalog import CatalogCandidate, CatalogSignals, ProductCatalogService
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


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

        source_catalog_version = str(candidates[0].series.catalog_version or "")
        series_map = await self.product_catalog.get_series_map(
            session=session,
            catalog_version=source_catalog_version or None,
            published_only=True,
        )
        payload = self._build_solution_payload(
            signals=signals,
            candidates=candidates,
            series_map=series_map,
            source_catalog_version=source_catalog_version,
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
        component_specs = list(selected_config.components or []) if selected_config else []
        for component in component_specs:
            component_code = str(component.get("series_code") or "").strip()
            component_series = series_map.get(component_code)
            if component_series is not None:
                component_series_rows.append(component_series)
            selected_products.append(
                self._build_component_entry(
                    component=component,
                    series=component_series,
                    base_quantity=quantity,
                    rated_voltage=requested_voltage,
                    rated_power_kw=signals.power_value,
                )
            )

        interface_plan = self._build_interface_plan(
            primary_series=primary_series,
            component_specs=component_specs,
            series_map=series_map,
            quantity=quantity,
            selected_protocol=selected_protocol,
            requested_protocol=signals.requested_protocol,
            selected_config_name=selected_config.config_name if selected_config else None,
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
        )
        suggested_chapters = self._dedupe_list(
            list(primary_series.default_chapters or [])
            + [chapter for series in component_series_rows for chapter in list(series.default_chapters or [])]
        )

        why_selected = list(primary_candidate.reasons)
        if selected_config and selected_config.description:
            why_selected.append(f"{selected_config.config_name} 已覆盖当前场景的标准配套边界。")
        why_selected.append("主设备、配套设备、接口和章节建议已拆成结构化字段，可直接进入 Outline 与章节生成。")

        risk_flags = self._dedupe_list(
            open_questions
            + self._extract_blocking_risks([primary_series, *component_series_rows])
        )

        power_label = self._format_power_label(signals.power_value)
        summary = (
            f"推荐采用 {primary_series.series_name} 作为主驱动基线，"
            f"围绕 {signals.motor_type} 的 {requested_voltage} / {power_label} 场景组织主回路、接口与供货配置。"
        )
        if selected_config:
            summary += f" 本轮默认采用 {selected_config.config_name}。"

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
        component_specs: list[dict[str, Any]],
        series_map: dict[str, ProductSeries],
        quantity: int,
        selected_protocol: str,
        requested_protocol: str | None,
        selected_config_name: str | None,
    ) -> dict[str, Any]:
        io_totals = self._multiply_io_allocation(primary_series.io_allocation or {}, quantity)
        for component in component_specs:
            component_code = str(component.get("series_code") or "").strip()
            component_series = series_map.get(component_code)
            if component_series is None:
                continue
            component_quantity = self._to_positive_int(component.get("quantity"), default=1) * quantity
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

        return {
            "dcs_protocol": selected_protocol,
            "io_allocation": io_totals,
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
        if signals.bypass_required and not self._has_bypass_scope(primary_series):
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
