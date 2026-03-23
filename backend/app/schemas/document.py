from __future__ import annotations

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
