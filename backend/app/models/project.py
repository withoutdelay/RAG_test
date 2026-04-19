from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), server_default=text("'CREATED'"), nullable=False)
    current_requirement_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirement_cards.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_outline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proposal_outlines.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_draft_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    generation_tasks = relationship("GenerationTask", back_populates="project", cascade="all, delete-orphan")
    raw_documents = relationship("RawDocument", back_populates="project", cascade="all, delete-orphan")
    requirement_cards = relationship(
        "RequirementCard",
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="RequirementCard.project_id",
    )
    evidence_bundles = relationship("EvidenceBundle", back_populates="project", cascade="all, delete-orphan")
    proposal_outlines = relationship(
        "ProposalOutline",
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="ProposalOutline.project_id",
    )
    section_drafts = relationship("SectionDraft", back_populates="project", cascade="all, delete-orphan")
    solution_snapshots = relationship("SolutionSnapshot", back_populates="project", cascade="all, delete-orphan")
    review_tasks = relationship("ReviewTask", back_populates="project", cascade="all, delete-orphan")
    validation_reports = relationship("ValidationReport", back_populates="project", cascade="all, delete-orphan")
    exports = relationship("ProjectExport", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="project", cascade="all, delete-orphan")
    current_requirement_card = relationship("RequirementCard", foreign_keys=[current_requirement_card_id], post_update=True)
    current_outline = relationship("ProposalOutline", foreign_keys=[current_outline_id], post_update=True)
