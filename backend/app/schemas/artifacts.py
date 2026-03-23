from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobAcceptedData(BaseModel):
    job_id: UUID
    status: str
    resource_id: UUID | None = None
    next_poll: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    job_type: str
    status: str
    input_ref: dict
    output_ref: dict
    retry_count: int
    error_code: str | None
    trace_id: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RequirementExtractRequest(BaseModel):
    rfp_document_id: UUID | None = None


class RequirementCardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    version: int
    schema_version: str
    content: dict
    missing_items: list
    blocking_items: list
    confidence: Decimal | None
    source_refs: list
    confirmed_by_user: bool
    created_at: datetime
    updated_at: datetime


class RequirementCardUpdateRequest(BaseModel):
    content: dict | None = None
    missing_items: list | None = None
    blocking_items: list | None = None
    confirmed_by_user: bool | None = None


class ClarificationResolveRequest(BaseModel):
    resolution: Any = None
    confirmed_by_user: bool | None = None


class EvidenceRetrieveRequest(BaseModel):
    requirement_card_id: UUID | None = None
    top_k: int = Field(default=6, ge=1, le=20)
    doc_type: str | None = None


class EvidenceBundleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    requirement_card_id: UUID
    retrieval_version: int
    content: dict
    quality_score: Decimal | None
    created_at: datetime
