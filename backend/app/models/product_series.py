from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductSeries(Base):
    __tablename__ = "product_series"
    __table_args__ = (
        UniqueConstraint("catalog_version", "code", name="uq_product_series_version_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"), nullable=False)
    role_type: Mapped[str] = mapped_column(String(20), server_default=sql_text("'support'"), nullable=False)
    family: Mapped[str] = mapped_column(String(120), nullable=False)
    family_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    series_name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    voltage_levels: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    min_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    topology: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applicable_motors: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    applicable_loads: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    communication_protocols: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    io_allocation: Mapped[dict] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"), nullable=False)
    protection_features: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    preferred_scenarios: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    default_chapters: Mapped[list] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    standard_configs = relationship(
        "ProductStandardConfig",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="ProductStandardConfig.config_name.asc()",
    )
    constraints = relationship(
        "ProductConstraint",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="ProductConstraint.constraint_type.asc()",
    )
    models = relationship(
        "ProductModel",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="ProductModel.model_number.asc()",
    )
    interfaces = relationship(
        "ProductInterface",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="ProductInterface.sort_order.asc(), ProductInterface.interface_type.asc()",
    )
