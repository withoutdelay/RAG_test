from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalFilters(BaseModel):
    industry: str | None = None
    year_gte: int | None = None
    chunk_type: list[str] | None = None
    doc_type: str | None = None


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    project_id: UUID | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    filters: RetrievalFilters | None = None
    search_mode: Literal["vector", "keyword", "hybrid"] = "hybrid"


class RetrievalResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_name: str
    heading_path: str | None
    chunk_type: str
    content: str
    score: float
    metadata: dict


class RetrievalSearchResponse(BaseModel):
    results: list[RetrievalResult]
    total: int
