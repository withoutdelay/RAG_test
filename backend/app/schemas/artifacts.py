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


class SolutionDesignRequest(BaseModel):
    requirement_card_id: UUID | None = None
    force_refresh: bool = True


class SolutionSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    requirement_card_id: UUID | None
    version: int
    status: str
    solution_summary: str
    selected_products: list[dict[str, Any]]
    interface_plan: dict[str, Any]
    key_constraints: list[str]
    open_questions: list[str]
    suggested_chapters: list[str]
    selection_reason: dict[str, Any]
    source_catalog_version: str | None = None
    confirmation_notes: str | None
    confirmed_by_user: bool
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SolutionUpdateRequest(BaseModel):
    solution_summary: str | None = None
    selected_products: list[dict[str, Any]] | None = None
    interface_plan: dict[str, Any] | None = None
    key_constraints: list[str] | None = None
    open_questions: list[str] | None = None
    suggested_chapters: list[str] | None = None
    selection_reason: dict[str, Any] | None = None
    confirmation_notes: str | None = None


class SolutionConfirmRequest(BaseModel):
    confirmation_notes: str | None = None
    confirmed_by_user: bool = True


class OutlineGenerateRequest(BaseModel):
    requirement_card_id: UUID | None = None
    evidence_bundle_id: UUID | None = None
    instructions: str | None = None


class ProposalOutlineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    version: int
    outline_json: dict
    requirement_card_id: UUID | None
    evidence_bundle_id: UUID | None
    validator_status: str
    created_at: datetime
    updated_at: datetime


class OutlineUpdateRequest(BaseModel):
    outline_json: dict


class OutlineApproveRequest(BaseModel):
    outline_json: dict | None = None
    reviewer_notes: str | None = None
    approved_by_user: bool = True


class SectionGenerateRequest(BaseModel):
    outline_id: UUID | None = None


class SectionDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_version: int
    section_id: str
    title: str
    content_md: str
    citation_refs: list
    assumptions: list
    global_param_snapshot: dict
    recommended_assets: list = Field(default_factory=list)
    status: str
    validator_result: dict
    created_at: datetime
    updated_at: datetime


class SectionDraftUpdateRequest(BaseModel):
    content_md: str
    citation_refs: list | None = None
    assumptions: list | None = None


class SectionRegenerateRequest(BaseModel):
    outline_id: UUID | None = None
    preferred_citation_ids: list[str] | None = None


class ValidationTriggerRequest(BaseModel):
    draft_version: int | None = Field(default=None, ge=1)
    outline_id: UUID | None = None


class ValidationReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_version: int
    outline_id: UUID | None
    requirement_card_id: UUID | None
    evidence_bundle_id: UUID | None
    status: str
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    review_tasks_created: list[str]
    created_at: datetime


class ReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    task_type: str
    blocking_level: str
    payload: dict[str, Any]
    assignee_user_id: UUID | None
    status: str
    created_at: datetime
    resolved_at: datetime | None


class ReviewTaskResolveRequest(BaseModel):
    resolution: Any = None
    status: str = Field(default="resolved", pattern="^(resolved|rejected)$")
    assignee_user_id: UUID | None = None


class ExportRequest(BaseModel):
    format: str = Field(default="markdown", pattern="^(markdown)$")


class ExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_version: int
    outline_id: UUID | None
    requirement_card_id: UUID | None
    evidence_bundle_id: UUID | None
    validation_report_id: UUID | None
    file_name: str
    file_type: str
    storage_path: str
    content_md: str
    snapshot: dict[str, Any]
    status: str
    created_at: datetime
