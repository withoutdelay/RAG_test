from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SectionDraft(Base):
    __tablename__ = "section_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    section_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    citation_refs: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    assumptions: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    global_param_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), server_default=sql_text("'generated'"), nullable=False)
    validator_result: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="section_drafts")

    @property
    def recommended_assets(self) -> list:
        if isinstance(self.validator_result, dict):
            assets = self.validator_result.get("recommended_assets")
            if isinstance(assets, list):
                return assets
        return []
