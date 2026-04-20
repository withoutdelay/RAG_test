from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductFamily(Base):
    __tablename__ = "product_families"
    __table_args__ = (
        UniqueConstraint("catalog_version", "code", name="uq_product_families_version_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=sql_text("'planned'"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"), nullable=False)
    parent_family_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    aliases = relationship(
        "ProductFamilyAlias",
        back_populates="family",
        cascade="all, delete-orphan",
        order_by="ProductFamilyAlias.sort_order.asc(), ProductFamilyAlias.alias.asc()",
    )
