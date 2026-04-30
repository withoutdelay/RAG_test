from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BIGINT, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "uq_documents_global_content_sha256",
            "content_sha256",
            unique=True,
            postgresql_where=text("project_id IS NULL AND content_sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"), nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
