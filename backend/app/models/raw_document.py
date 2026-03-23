from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RawDocument(Base):
    __tablename__ = "raw_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    corpus_scope: Mapped[str] = mapped_column(String(30), server_default=sql_text("'project'"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20), server_default=sql_text("'pending'"), nullable=False)
    confidentiality_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="raw_documents")
    parsed_blocks = relationship("ParsedBlock", back_populates="raw_document", cascade="all, delete-orphan")
    figure_assets = relationship("FigureAsset", back_populates="raw_document", cascade="all, delete-orphan")
    knowledge_chunks = relationship("KnowledgeChunk", back_populates="raw_document", cascade="all, delete-orphan")
