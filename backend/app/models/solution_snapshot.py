from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SolutionSnapshot(Base):
    __tablename__ = "solution_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirement_cards.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), server_default=sql_text("'draft'"), nullable=False)
    solution_summary: Mapped[str] = mapped_column(Text, nullable=False)
    selected_products: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    interface_plan: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    key_constraints: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    open_questions: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    suggested_chapters: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    selection_reason: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    source_catalog_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="solution_snapshots")
    requirement_card = relationship("RequirementCard", back_populates="solution_snapshots")
