from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product_constraint import ProductConstraint
from app.models.product_series import ProductSeries
from app.models.product_standard_config import ProductStandardConfig
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.services.catalog.defaults import DEFAULT_CATALOG_VERSION, DEFAULT_PRODUCT_CATALOG
from app.services.v2_errors import ArtifactNotFoundError


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

    async def list_series(
        self,
        *,
        session: AsyncSession,
        published_only: bool = True,
        family: str | None = None,
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
        if catalog_version:
            stmt = stmt.where(ProductSeries.catalog_version == catalog_version)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProductSeries.series_name.ilike(like)
                | ProductSeries.code.ilike(like)
                | ProductSeries.family.ilike(like)
            )
        result = await session.scalars(stmt)
        return result.all()

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
        existing = await session.scalars(
            select(ProductSeries)
            .options(
                selectinload(ProductSeries.standard_configs),
                selectinload(ProductSeries.constraints),
            )
            .where(ProductSeries.catalog_version == version)
        )
        existing_rows = existing.all()
        if existing_rows and replace_existing:
            for row in existing_rows:
                await session.delete(row)
            await session.flush()
        elif existing_rows:
            return {
                "catalog_version": version,
                "imported_series_count": 0,
                "published": publish,
                "reused_existing": True,
            }

        imported_count = 0
        for item in DEFAULT_PRODUCT_CATALOG:
            series = ProductSeries(
                catalog_version=version,
                is_published=False,
                role_type=str(item.get("role_type") or "support"),
                family=str(item.get("family") or ""),
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

        all_rows = await session.scalars(select(ProductSeries))
        for row in all_rows:
            row.is_published = row.catalog_version == catalog_version
        await session.flush()
        if commit:
            await session.commit()
        return {
            "catalog_version": catalog_version,
            "published_series_count": len(target_rows),
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
