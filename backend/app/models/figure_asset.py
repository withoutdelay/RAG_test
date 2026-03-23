from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FigureAsset(Base):
    __tablename__ = "figure_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    reuse_mode: Mapped[str] = mapped_column(String(30), server_default=sql_text("'reference_only'"), nullable=False)
    parse_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    raw_document = relationship("RawDocument", back_populates="figure_assets")
