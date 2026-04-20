"""Add product compatibility table."""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0008"
down_revision = "20260419_0007"
branch_labels = None
depends_on = None


_DEFAULT_COMPATIBILITY = [
    {
        "source_family_code": "lci_sync_drive",
        "target_family_code": "support_equipment",
        "relation_type": "requires",
        "condition": "同步电机软起及主回路成套场景",
        "description": "LCI 主驱动通常需要整流变压器和励磁控制柜；若要求工频旁路，则追加旁路柜。",
        "preferred_series_codes": ["rectifier_transformer", "excitation_cabinet"],
        "optional_series_codes": ["bypass_cabinet"],
        "sort_order": 10,
    },
    {
        "source_family_code": "hv_vfd_multilevel",
        "target_family_code": "support_equipment",
        "relation_type": "recommended",
        "condition": "要求工频旁路、检修不停机或改造保留原系统切换",
        "description": "高压变频主驱动在特定改造场景下推荐配置旁路切换柜。",
        "preferred_series_codes": ["bypass_cabinet"],
        "optional_series_codes": [],
        "sort_order": 20,
    },
    {
        "source_family_code": "hv_solid_state_starter",
        "target_family_code": "support_equipment",
        "relation_type": "recommended",
        "condition": "高压固态软起动需要切换柜或成套配电侧联动时",
        "description": "高压固态软起产品族通常需要与切换柜、旁路柜一起定义成套边界。",
        "preferred_series_codes": ["bypass_cabinet"],
        "optional_series_codes": [],
        "sort_order": 30,
    },
]


def upgrade() -> None:
    op.create_table(
        "product_compatibility",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_family_code", sa.String(length=120), nullable=False),
        sa.Column("target_family_code", sa.String(length=120), nullable=False),
        sa.Column("relation_type", sa.String(length=30), server_default=sa.text("'recommended'"), nullable=False),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("preferred_series_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("optional_series_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint(
            "catalog_version",
            "source_family_code",
            "target_family_code",
            "relation_type",
            name="uq_product_compatibility_version_relation",
        ),
    )
    op.create_index(
        "idx_product_compatibility_catalog_source",
        "product_compatibility",
        ["catalog_version", "is_published", "source_family_code"],
    )

    bind = op.get_bind()
    families = sa.table(
        "product_families",
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
    )
    compat = sa.table(
        "product_compatibility",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("catalog_version", sa.String(length=80)),
        sa.column("is_published", sa.Boolean()),
        sa.column("source_family_code", sa.String(length=120)),
        sa.column("target_family_code", sa.String(length=120)),
        sa.column("relation_type", sa.String(length=30)),
        sa.column("condition", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("preferred_series_codes", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("optional_series_codes", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("sort_order", sa.Integer()),
    )
    version_rows = bind.execute(
        sa.select(
            families.c.catalog_version,
            sa.func.bool_or(families.c.is_published).label("is_published"),
        ).group_by(families.c.catalog_version)
    ).fetchall()
    for version_row in version_rows:
        rows = []
        for item in _DEFAULT_COMPATIBILITY:
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "catalog_version": str(version_row.catalog_version),
                    "is_published": bool(version_row.is_published),
                    "source_family_code": item["source_family_code"],
                    "target_family_code": item["target_family_code"],
                    "relation_type": item["relation_type"],
                    "condition": item["condition"],
                    "description": item["description"],
                    "preferred_series_codes": item["preferred_series_codes"],
                    "optional_series_codes": item["optional_series_codes"],
                    "sort_order": item["sort_order"],
                }
            )
        if rows:
            op.bulk_insert(compat, rows)


def downgrade() -> None:
    op.drop_index("idx_product_compatibility_catalog_source", table_name="product_compatibility")
    op.drop_table("product_compatibility")
