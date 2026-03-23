from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ValidationReport(Base):
    __tablename__ = "validation_reports"

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
    status: Mapped[str] = mapped_column(String(20), server_default=sql_text("'pending'"), nullable=False)
    errors: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    review_tasks_created: Mapped[list] = mapped_column(
        JSONB,
        server_default=sql_text("'[]'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="validation_reports")
