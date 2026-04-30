"""Add document content hash for exact upload dedupe."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0005"
down_revision = "20260323_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_sha256", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_documents_global_content_sha256",
        "documents",
        ["content_sha256"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL AND content_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_global_content_sha256", table_name="documents")
    op.drop_column("documents", "content_sha256")
