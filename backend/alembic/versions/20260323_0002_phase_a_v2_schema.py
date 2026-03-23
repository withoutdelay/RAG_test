"""Add Phase A V2 schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260323_0002"
down_revision = "20260322_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("projects", sa.Column("product_line", sa.String(length=100), nullable=True))
    op.add_column(
        "projects",
        sa.Column("status", sa.String(length=50), server_default=sa.text("'CREATED'"), nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("current_draft_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    op.create_table(
        "raw_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("corpus_scope", sa.String(length=30), server_default=sa.text("'project'"), nullable=False),
        sa.Column("doc_type", sa.String(length=50), nullable=False),
        sa.Column("file_uri", sa.String(length=1000), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("version_label", sa.String(length=100), nullable=True),
        sa.Column("parse_status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("confidentiality_level", sa.String(length=30), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("checksum", "corpus_scope", name="uq_raw_documents_checksum_scope"),
    )
    op.create_index("idx_raw_documents_project", "raw_documents", ["project_id"])
    op.create_index("idx_raw_documents_parse_status", "raw_documents", ["parse_status"])

    op.create_table(
        "parsed_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("raw_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("raw_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_type", sa.String(length=30), nullable=False),
        sa.Column("heading_path", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("order_in_doc", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("block_hash", sa.String(length=128), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_parsed_blocks_document_order", "parsed_blocks", ["raw_document_id", "order_in_doc"])
    op.create_index("idx_parsed_blocks_type", "parsed_blocks", ["block_type"])

    op.create_table(
        "figure_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("raw_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("raw_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("asset_uri", sa.String(length=1000), nullable=False),
        sa.Column("asset_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("reuse_mode", sa.String(length=30), server_default=sa.text("'reference_only'"), nullable=False),
        sa.Column("parse_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_figure_assets_document", "figure_assets", ["raw_document_id"])
    op.create_index("idx_figure_assets_type", "figure_assets", ["asset_type"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("raw_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("raw_documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_block_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("chunk_type", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("qdrant_point_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source_of_truth", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("derived_type", sa.String(length=20), server_default=sa.text("'original'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_knowledge_chunks_document", "knowledge_chunks", ["raw_document_id"])
    op.create_index("idx_knowledge_chunks_type_truth", "knowledge_chunks", ["chunk_type", "source_of_truth"])
    op.create_index("idx_knowledge_chunks_qdrant_point", "knowledge_chunks", ["qdrant_point_id"])

    op.create_table(
        "requirement_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=30), server_default=sa.text("'v1'"), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("missing_items", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("blocking_items", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("confirmed_by_user", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_requirement_cards_project_version"),
    )
    op.create_index("idx_requirement_cards_project", "requirement_cards", ["project_id"])

    op.create_table(
        "evidence_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "requirement_card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirement_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("retrieval_version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("quality_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("project_id", "retrieval_version", name="uq_evidence_bundles_project_version"),
    )
    op.create_index("idx_evidence_bundles_project", "evidence_bundles", ["project_id"])
    op.create_index("idx_evidence_bundles_requirement_card", "evidence_bundles", ["requirement_card_id"])

    op.create_table(
        "proposal_outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("outline_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
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
        sa.Column("validator_status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_proposal_outlines_project_version"),
    )
    op.create_index("idx_proposal_outlines_project", "proposal_outlines", ["project_id"])

    op.create_table(
        "section_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("citation_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "global_param_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'generated'"), nullable=False),
        sa.Column("validator_result", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("project_id", "draft_version", "section_id", name="uq_section_drafts_project_version_section"),
    )
    op.create_index("idx_section_drafts_project_version", "section_drafts", ["project_id", "draft_version"])

    op.create_table(
        "review_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("blocking_level", sa.String(length=10), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_review_tasks_project_status", "review_tasks", ["project_id", "status"])

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("job_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("input_ref", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_ref", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_jobs_project_status", "jobs", ["project_id", "status"])
    op.create_index("idx_jobs_trace_id", "jobs", ["trace_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_audit_logs_project_created", "audit_logs", ["project_id", "created_at"])
    op.create_index("idx_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    op.add_column("projects", sa.Column("current_requirement_card_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("projects", sa.Column("current_outline_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_projects_current_requirement_card",
        "projects",
        "requirement_cards",
        ["current_requirement_card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_projects_current_outline",
        "projects",
        "proposal_outlines",
        ["current_outline_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_projects_status", "projects", ["status"])
    op.create_index("idx_projects_product_line_status", "projects", ["product_line", "status"])


def downgrade() -> None:
    op.drop_index("idx_projects_product_line_status", table_name="projects")
    op.drop_index("idx_projects_status", table_name="projects")
    op.drop_constraint("fk_projects_current_outline", "projects", type_="foreignkey")
    op.drop_constraint("fk_projects_current_requirement_card", "projects", type_="foreignkey")
    op.drop_column("projects", "current_outline_id")
    op.drop_column("projects", "current_requirement_card_id")

    op.drop_index("idx_audit_logs_entity", table_name="audit_logs")
    op.drop_index("idx_audit_logs_project_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("idx_jobs_trace_id", table_name="jobs")
    op.drop_index("idx_jobs_project_status", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("idx_review_tasks_project_status", table_name="review_tasks")
    op.drop_table("review_tasks")

    op.drop_index("idx_section_drafts_project_version", table_name="section_drafts")
    op.drop_table("section_drafts")

    op.drop_index("idx_proposal_outlines_project", table_name="proposal_outlines")
    op.drop_table("proposal_outlines")

    op.drop_index("idx_evidence_bundles_requirement_card", table_name="evidence_bundles")
    op.drop_index("idx_evidence_bundles_project", table_name="evidence_bundles")
    op.drop_table("evidence_bundles")

    op.drop_index("idx_requirement_cards_project", table_name="requirement_cards")
    op.drop_table("requirement_cards")

    op.drop_index("idx_knowledge_chunks_qdrant_point", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_type_truth", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_document", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("idx_figure_assets_type", table_name="figure_assets")
    op.drop_index("idx_figure_assets_document", table_name="figure_assets")
    op.drop_table("figure_assets")

    op.drop_index("idx_parsed_blocks_type", table_name="parsed_blocks")
    op.drop_index("idx_parsed_blocks_document_order", table_name="parsed_blocks")
    op.drop_table("parsed_blocks")

    op.drop_index("idx_raw_documents_parse_status", table_name="raw_documents")
    op.drop_index("idx_raw_documents_project", table_name="raw_documents")
    op.drop_table("raw_documents")

    op.drop_column("projects", "current_draft_version")
    op.drop_column("projects", "status")
    op.drop_column("projects", "product_line")
    op.drop_column("projects", "owner_user_id")
