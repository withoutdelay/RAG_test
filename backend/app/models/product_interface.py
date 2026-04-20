from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductInterface(Base):
    __tablename__ = "product_interfaces"
    __table_args__ = (
        UniqueConstraint(
            "catalog_version",
            "series_code",
            "interface_type",
            "sort_order",
            name="uq_product_interfaces_version_series_slot",
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
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_series.id", ondelete="CASCADE"),
        nullable=False,
    )
    series_code: Mapped[str] = mapped_column(String(120), nullable=False)
    interface_type: Mapped[str] = mapped_column(String(40), nullable=False)
    protocol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    signal_spec: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_material_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    series = relationship("ProductSeries", back_populates="interfaces")
