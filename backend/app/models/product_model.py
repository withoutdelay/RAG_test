from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductModel(Base):
    __tablename__ = "product_models"
    __table_args__ = (
        UniqueConstraint("catalog_version", "series_code", "model_number", name="uq_product_models_version_series_model"),
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
    model_number: Mapped[str] = mapped_column(String(160), nullable=False)
    rated_voltage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rated_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rated_current: Mapped[str | None] = mapped_column(String(80), nullable=True)
    specs: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    source_material_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    series = relationship("ProductSeries", back_populates="models")
