from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

from app.presidio_config import SUPPORTED_ENTITY_TYPES, normalize_entity_types


T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int
    message: str = "success"
    data: T


class MaskRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    entity_types: list[str] = Field(default_factory=lambda: list(SUPPORTED_ENTITY_TYPES))

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, value: list[str]) -> list[str]:
        return list(normalize_entity_types(value))


class MaskedEntityRead(BaseModel):
    original: str
    placeholder: str
    type: str
    start: int
    end: int


class MaskResponseData(BaseModel):
    masked_text: str
    entity_count: int
    entities_detected: list[MaskedEntityRead]


class RestoreRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)


class RestoreResponseData(BaseModel):
    restored_text: str
    restored_count: int
