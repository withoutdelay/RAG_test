"""Add export records for V2."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260323_0004"
down_revision = "20260323_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column(
            "outline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposal_outlines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requirement_card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirement_cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "evidence_bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_bundles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "validation_report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("validation_reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=20), server_default=sa.text("'md'"), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_exports_project_created", "exports", ["project_id", "created_at"])
    op.create_index("idx_exports_project_draft", "exports", ["project_id", "draft_version"])


def downgrade() -> None:
    op.drop_index("idx_exports_project_draft", table_name="exports")
    op.drop_index("idx_exports_project_created", table_name="exports")
    op.drop_table("exports")
