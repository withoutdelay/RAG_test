from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProductMaterial(Base):
    __tablename__ = "product_materials"
    __table_args__ = (
        UniqueConstraint("material_key", name="uq_product_materials_material_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    material_key: Mapped[str] = mapped_column(String(160), nullable=False)
    family_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    material_type: Mapped[str] = mapped_column(String(60), server_default=sql_text("'proposal_sample'"), nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(40), server_default=sql_text("'private_sample'"), nullable=False)
    availability_status: Mapped[str] = mapped_column(String(40), server_default=sql_text("'available'"), nullable=False)
    file_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    assigned_track: Mapped[str | None] = mapped_column(String(40), nullable=True)
    suggested_track: Mapped[str | None] = mapped_column(String(40), nullable=True)
    priority_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
