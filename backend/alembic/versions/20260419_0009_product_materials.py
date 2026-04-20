"""Add product materials registry."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0009"
down_revision = "20260419_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_materials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_key", sa.String(length=160), nullable=False),
        sa.Column("family_code", sa.String(length=120), nullable=True),
        sa.Column("material_type", sa.String(length=60), server_default=sa.text("'proposal_sample'"), nullable=False),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(length=40), server_default=sa.text("'private_sample'"), nullable=False),
        sa.Column("availability_status", sa.String(length=40), server_default=sa.text("'available'"), nullable=False),
        sa.Column("file_format", sa.String(length=20), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("assigned_track", sa.String(length=40), nullable=True),
        sa.Column("suggested_track", sa.String(length=40), nullable=True),
        sa.Column("priority_tier", sa.String(length=10), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("material_key", name="uq_product_materials_material_key"),
    )
    op.create_index(
        "idx_product_materials_family_type_status",
        "product_materials",
        ["family_code", "material_type", "availability_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_product_materials_family_type_status", table_name="product_materials")
    op.drop_table("product_materials")
