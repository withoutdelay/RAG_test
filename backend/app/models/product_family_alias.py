from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductFamilyAlias(Base):
    __tablename__ = "product_family_aliases"
    __table_args__ = (
        UniqueConstraint("family_id", "alias", name="uq_product_family_aliases_family_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_families.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(40), server_default=sql_text("'business'"), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    family = relationship("ProductFamily", back_populates="aliases")
