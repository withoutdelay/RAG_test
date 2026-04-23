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
    reason: str | None = None
    reason_trace: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    metadata: dict


class RetrievalSearchTrace(BaseModel):
    search_mode: Literal["vector", "keyword", "hybrid"]
    dense_search_limit: int
    dense_hit_count: int
    sparse_search_limit: int = 0
    sparse_hit_count: int = 0
    dense_candidate_count: int = 0
    sparse_candidate_count: int = 0
    candidate_count: int
    ranked_count: int
    returned_count: int
    reranker_enabled: bool
    reranker_backend: str


class RetrievalSearchResponse(BaseModel):
    results: list[RetrievalResult]
    total: int
    search_trace: RetrievalSearchTrace | None = None


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
    display_title: str | None = None
    caption: str | None = None
    source_ref: str | None = None
    asset_uri: str
    preview_text: str
    reason: str
    reason_trace: list[str] = Field(default_factory=list)
    score: float
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any]


class AssetSearchTrace(BaseModel):
    visual_branch_enabled: bool = False
    visual_backend: str = "disabled"
    candidate_count: int = 0
    returned_count: int = 0
    requested_asset_types: list[str] = Field(default_factory=list)
    expected_evidence_types: list[str] = Field(default_factory=list)
    visual_collection_available: bool = False
    visual_candidate_count: int = 0
    image_collection_hits: int = 0
    text_proxy_collection_hits: int = 0
    direct_visual_fallback: bool = False
    result_source_breakdown: dict[str, int] = Field(default_factory=dict)
    result_branch_breakdown: dict[str, int] = Field(default_factory=dict)


class AssetSearchResponse(BaseModel):
    results: list[AssetSearchResult]
    total: int
    search_trace: AssetSearchTrace | None = None
