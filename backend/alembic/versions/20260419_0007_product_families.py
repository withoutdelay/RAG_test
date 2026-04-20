"""Add product family tables and normalized family code."""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0007"
down_revision = "20260419_0006"
branch_labels = None
depends_on = None


_DEFAULT_FAMILIES = [
    {
        "code": "lci_sync_drive",
        "name": "同步电机软起动",
        "display_name": "LCI / 同步电机变频软起动系统",
        "description": "适用于高压同步电机的大功率软起动、同步切换和工频旁路场景。",
        "status": "active",
        "sort_order": 10,
        "aliases": ["LCI", "LCI 软起", "LCI 改造", "LCI 变频软起", "同步电机软起动", "同步电机变频软起系统"],
    },
    {
        "code": "hv_vfd_multilevel",
        "name": "高压变频",
        "display_name": "高压变频器多电平驱动系统",
        "description": "适用于风机、泵、输送机等高压调速和节能改造场景。",
        "status": "active",
        "sort_order": 20,
        "aliases": ["高压变频", "高压变频器", "高压调速", "变频节能改造", "高压变频软起"],
    },
    {
        "code": "hv_solid_state_starter",
        "name": "高压固态软起动",
        "display_name": "高压固态软起动系统",
        "description": "用于高压电机限流启动和软起场景的固态起动系统。",
        "status": "planned",
        "sort_order": 30,
        "aliases": ["高压固态", "固态软起", "高压固态软起动", "高压固态起动柜", "固态及变频软起动"],
    },
    {
        "code": "autotransformer_starter",
        "name": "自耦变软起动",
        "display_name": "自耦变软起动系统",
        "description": "用于自耦降压起动场景的产品族占位。",
        "status": "planned",
        "sort_order": 40,
        "aliases": ["自耦变", "自耦变软起动", "自耦降压起动"],
    },
    {
        "code": "liquid_resistor_starter",
        "name": "水电阻 / 液阻软起动",
        "display_name": "水电阻 / 液阻软起动系统",
        "description": "用于水电阻、液阻软起动场景的产品族占位。",
        "status": "planned",
        "sort_order": 50,
        "aliases": ["水电阻", "液阻", "水电阻软起动", "液阻软起动"],
    },
    {
        "code": "pm_motor_vfd_retrofit",
        "name": "永磁电机 + 变频节能改造",
        "display_name": "永磁电机 + 高压变频节能改造",
        "description": "用于永磁电机和高压变频节能改造的相邻产品族占位。",
        "status": "planned",
        "sort_order": 60,
        "aliases": ["永磁电机", "永磁电机+变频", "变频节能改造", "风机节能改造"],
    },
    {
        "code": "support_equipment",
        "name": "配套设备",
        "display_name": "配套设备",
        "description": "用于整流变压器、励磁柜、旁路柜等配套设备的归一产品族。",
        "status": "active",
        "sort_order": 90,
        "aliases": ["配套设备", "整流变压器", "励磁柜", "旁路柜"],
    },
]


_SERIES_FAMILY_CODE_MAP = {
    "lci_sync_drive": "lci_sync_drive",
    "hv_vfd_multilevel": "hv_vfd_multilevel",
    "rectifier_transformer": "support_equipment",
    "excitation_cabinet": "support_equipment",
    "bypass_cabinet": "support_equipment",
}

_FAMILY_NAME_CODE_MAP = {
    "同步电机软起动": "lci_sync_drive",
    "高压变频": "hv_vfd_multilevel",
    "配套设备": "support_equipment",
}


def upgrade() -> None:
    op.create_table(
        "product_families",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parent_family_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("catalog_version", "code", name="uq_product_families_version_code"),
    )
    op.create_index("idx_product_families_catalog", "product_families", ["catalog_version", "is_published"])

    op.create_table(
        "product_family_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("alias_type", sa.String(length=40), server_default=sa.text("'business'"), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("family_id", "alias", name="uq_product_family_aliases_family_alias"),
    )
    op.create_index("idx_product_family_aliases_alias", "product_family_aliases", ["alias"])

    op.add_column("product_series", sa.Column("family_code", sa.String(length=120), nullable=True))
    op.create_index("idx_product_series_catalog_family_code", "product_series", ["catalog_version", "family_code"])

    bind = op.get_bind()
    series = sa.table(
        "product_series",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("code", sa.String(length=120)),
        sa.column("family", sa.String(length=120)),
        sa.column("family_code", sa.String(length=120)),
    )
    families = sa.table(
        "product_families",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("code", sa.String(length=120)),
        sa.column("name", sa.String(length=120)),
        sa.column("display_name", sa.String(length=200)),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String(length=20)),
        sa.column("sort_order", sa.Integer()),
        sa.column("parent_family_code", sa.String(length=120)),
    )
    aliases = sa.table(
        "product_family_aliases",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("family_id", postgresql.UUID(as_uuid=True)),
        sa.column("alias", sa.String(length=200)),
        sa.column("alias_type", sa.String(length=40)),
        sa.column("source", sa.String(length=120)),
        sa.column("sort_order", sa.Integer()),
    )

    version_rows = bind.execute(
        sa.select(
            series.c.catalog_version,
            sa.func.bool_or(series.c.is_published).label("is_published"),
        ).group_by(series.c.catalog_version)
    ).fetchall()

    for version_row in version_rows:
        catalog_version = str(version_row.catalog_version)
        family_rows = []
        alias_rows = []
        for family in _DEFAULT_FAMILIES:
            family_id = uuid.uuid4()
            family_rows.append(
                {
                    "id": family_id,
                    "catalog_version": catalog_version,
                    "is_published": bool(version_row.is_published),
                    "code": family["code"],
                    "name": family["name"],
                    "display_name": family["display_name"],
                    "description": family["description"],
                    "status": family["status"],
                    "sort_order": family["sort_order"],
                    "parent_family_code": None,
                }
            )
            for idx, alias in enumerate(family.get("aliases") or []):
                alias_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "family_id": family_id,
                        "alias": alias,
                        "alias_type": "business",
                        "source": "seed",
                        "sort_order": idx,
                    }
                )
        if family_rows:
            op.bulk_insert(families, family_rows)
        if alias_rows:
            op.bulk_insert(aliases, alias_rows)

    series_rows = bind.execute(sa.select(series.c.id, series.c.code, series.c.family)).fetchall()
    for row in series_rows:
        family_code = _SERIES_FAMILY_CODE_MAP.get(str(row.code)) or _FAMILY_NAME_CODE_MAP.get(str(row.family))
        if family_code:
            bind.execute(
                sa.update(series)
                .where(series.c.id == row.id)
                .values(family_code=family_code)
            )


def downgrade() -> None:
    op.drop_index("idx_product_series_catalog_family_code", table_name="product_series")
    op.drop_column("product_series", "family_code")
    op.drop_index("idx_product_family_aliases_alias", table_name="product_family_aliases")
    op.drop_table("product_family_aliases")
    op.drop_index("idx_product_families_catalog", table_name="product_families")
    op.drop_table("product_families")
