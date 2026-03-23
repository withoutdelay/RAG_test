from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProjectExport(Base):
    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proposal_outlines.id", ondelete="SET NULL"),
        nullable=True,
    )
    requirement_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirement_cards.id", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_bundles.id", ondelete="SET NULL"),
        nullable=True,
    )
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_reports.id", ondelete="SET NULL"),
        nullable=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), server_default=sql_text("'md'"), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default=sql_text("'pending'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="exports")
