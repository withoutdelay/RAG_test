from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalFilters(BaseModel):
    industry: str | None = None
    year_gte: int | None = None
    chunk_type: list[str] | None = None
    doc_type: str | None = None
    document_names: list[str] | None = None


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


class AssetSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    asset_types: list[str] | None = None
    doc_types: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    section_context: dict[str, Any] | None = None


class AssetSearchResult(BaseModel):
    asset_card_id: str
    asset_id: UUID
    document_id: UUID | None = None
    raw_document_id: UUID
    document_name: str | None = None
    doc_type: str | None = None
    asset_type: Literal["figure", "table", "formula_candidate"]
    visual_role: str | None = None
    risk_level: Literal["low", "medium", "high"]
    usage_mode: str
    review_required: bool
    page_no: int | None = None
    heading_path: str | None = None
    title: str | None = None
    caption: str | None = None
    source_ref: str | None = None
    asset_uri: str
    preview_text: str
    reason: str
    score: float
    metadata: dict[str, Any]


class AssetSearchResponse(BaseModel):
    results: list[AssetSearchResult]
    total: int
