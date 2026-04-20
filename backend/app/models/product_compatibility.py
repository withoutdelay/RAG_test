from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProductCompatibility(Base):
    __tablename__ = "product_compatibility"
    __table_args__ = (
        UniqueConstraint(
            "catalog_version",
            "source_family_code",
            "target_family_code",
            "relation_type",
            name="uq_product_compatibility_version_relation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"), nullable=False)
    source_family_code: Mapped[str] = mapped_column(String(120), nullable=False)
    target_family_code: Mapped[str] = mapped_column(String(120), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(30), server_default=sql_text("'recommended'"), nullable=False)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_series_codes: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    optional_series_codes: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
