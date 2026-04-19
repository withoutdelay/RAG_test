"""Add product catalog tables and solution catalog version."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0006"
down_revision = "20260419_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_series",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("role_type", sa.String(length=20), server_default=sa.text("'support'"), nullable=False),
        sa.Column("family", sa.String(length=120), nullable=False),
        sa.Column("series_name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("vendor", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("voltage_levels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("min_power_kw", sa.Float(), nullable=True),
        sa.Column("max_power_kw", sa.Float(), nullable=True),
        sa.Column("topology", sa.String(length=120), nullable=True),
        sa.Column("applicable_motors", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("applicable_loads", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("communication_protocols", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("io_allocation", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("protection_features", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("preferred_scenarios", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("default_chapters", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("catalog_version", "code", name="uq_product_series_version_code"),
    )
    op.create_index("idx_product_series_catalog", "product_series", ["catalog_version", "is_published"])
    op.create_index("idx_product_series_role_family", "product_series", ["role_type", "family"])

    op.create_table(
        "product_standard_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_name", sa.String(length=120), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("applicable_scenarios", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("series_id", "config_name", name="uq_product_standard_configs_series_name"),
    )
    op.create_index("idx_product_standard_configs_series", "product_standard_configs", ["series_id"])

    op.create_table(
        "product_constraints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("constraint_type", sa.String(length=80), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default=sa.text("'warning'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_product_constraints_series", "product_constraints", ["series_id"])

    op.add_column("solution_snapshots", sa.Column("source_catalog_version", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("solution_snapshots", "source_catalog_version")
    op.drop_index("idx_product_constraints_series", table_name="product_constraints")
    op.drop_table("product_constraints")
    op.drop_index("idx_product_standard_configs_series", table_name="product_standard_configs")
    op.drop_table("product_standard_configs")
    op.drop_index("idx_product_series_role_family", table_name="product_series")
    op.drop_index("idx_product_series_catalog", table_name="product_series")
    op.drop_table("product_series")
