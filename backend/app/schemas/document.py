from __future__ import annotations

from typing import Any
from decimal import Decimal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    filename: str
    file_type: str
    file_size_bytes: int | None
    storage_path: str
    doc_type: str
    parse_status: str
    metadata: dict = Field(validation_alias="meta")
    created_at: datetime


class DocumentUploadAccepted(BaseModel):
    id: UUID
    filename: str
    parse_status: str
    message: str
    job_id: UUID | None = None
    next_poll: str | None = None
    duplicate: bool = False
    duplicate_of_id: UUID | None = None


class HistoryLibraryRefreshPipelineStatusRead(BaseModel):
    status: str
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_success_at: datetime | None = None
    pending: bool = False
    error: str | None = None
    duration_seconds: float | None = None


class HistoryLibraryRefreshStatusRead(BaseModel):
    status: str
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_success_at: datetime | None = None
    pending: bool = False
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    pipelines: dict[str, HistoryLibraryRefreshPipelineStatusRead] = Field(default_factory=dict)


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    chunk_type: str
    content: str
    token_count: int | None
    heading_path: str | None
    image_url: str | None
    qdrant_point_id: UUID | None
    metadata: dict = Field(validation_alias="meta")
    created_at: datetime


class FigureAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_document_id: UUID
    page_no: int | None
    asset_uri: str
    asset_type: str
    title: str | None
    caption: str | None
    reuse_mode: str
    parse_confidence: Decimal | None
    metadata: dict = Field(validation_alias="meta")
    created_at: datetime
