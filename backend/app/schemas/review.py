from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    section_index: int
    review_type: str
    description: str
    payload: dict
    status: str
    user_feedback: str | None
    created_at: datetime
    resolved_at: datetime | None


class ReviewActionRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=4000)


class ReviewRejectRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=4000)


class ReviewActionData(BaseModel):
    review_id: UUID
    task_id: UUID
    status: str
    task_status: str
    section_index: int
    follow_up_review_id: UUID | None = None
