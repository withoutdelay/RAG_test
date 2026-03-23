from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenerationStartRequest(BaseModel):
    project_id: UUID
    rfp_document_id: UUID | None = None
    instructions: str = Field(min_length=1)
    global_params: dict = Field(default_factory=dict)


class GenerationStartData(BaseModel):
    task_id: UUID
    status: str
    message: str


class GenerationTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    rfp_document_id: UUID | None
    status: str
    outline: dict | None
    global_params: dict
    sections: list[dict]
    final_markdown: str | None
    total_tokens: int
    estimated_cost: Decimal | None
    created_at: datetime
    completed_at: datetime | None


class OutlineConfirmRequest(BaseModel):
    outline: dict | None = None


class OutlineConfirmData(BaseModel):
    task_id: UUID
    status: str
    section_count: int
    pending_review_count: int = 0
    final_markdown: str


class SectionRewriteRequest(BaseModel):
    instruction: str = Field(min_length=1)
    selected_text: str = Field(min_length=1)


class SectionRewriteData(BaseModel):
    section_index: int
    new_content: str
    citations: list[dict] = Field(default_factory=list)
