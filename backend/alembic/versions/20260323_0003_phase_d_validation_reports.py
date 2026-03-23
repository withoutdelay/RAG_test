"""Add Phase D validation reports."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260323_0003"
down_revision = "20260323_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_reports",
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
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "review_tasks_created",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_validation_reports_project_created", "validation_reports", ["project_id", "created_at"])
    op.create_index("idx_validation_reports_project_draft", "validation_reports", ["project_id", "draft_version"])


def downgrade() -> None:
    op.drop_index("idx_validation_reports_project_draft", table_name="validation_reports")
    op.drop_index("idx_validation_reports_project_created", table_name="validation_reports")
    op.drop_table("validation_reports")
