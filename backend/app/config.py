from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
CASE_LIBRARY_ROOT = BACKEND_ROOT / "data" / "case_library"
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    secret_key: str = "change-me-to-random-string"
    frontend_port: int = 3000

    database_url: str = "postgresql+asyncpg://copilot:copilot@localhost:5432/copilot_db"
    redis_url: str = "redis://localhost:6379/0"

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_location: str | None = None
    qdrant_collection: str = "presale_knowledge"
    case_library_outline_path: str = str(CASE_LIBRARY_ROOT / "outline_library.json")
    case_library_block_path: str = str(CASE_LIBRARY_ROOT / "block_library.json")
    parser_backend: Literal["auto", "docling", "fallback"] = "auto"
    docling_libreoffice_cmd: str | None = None
    parser_llm_asset_review_enabled: bool = False
    parser_llm_asset_review_max_assets: int = 6
    parser_llm_asset_review_confidence_threshold: float = 0.72
    parser_llm_asset_review_use_vision: bool = True
    parser_llm_asset_review_image_detail: Literal["auto", "low", "high"] = "auto"
    parser_llm_asset_review_max_image_bytes: int = 800000
    parser_llm_asset_summary_enabled: bool = False
    parser_llm_asset_summary_max_assets: int = 6
    parser_llm_asset_summary_use_vision: bool = True
    parser_llm_asset_summary_image_detail: Literal["auto", "low", "high"] = "auto"
    parser_llm_asset_summary_max_image_bytes: int = 800000
    safe_ingestion_enabled: bool = True
    formula_ocr_backend: Literal["none", "pix2tex"] = "none"
    formula_ocr_max_assets: int = 3
    formula_ocr_max_regions_per_asset: int = 3
    embedding_backend: Literal["auto", "sentence-transformers", "fallback"] = "fallback"
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_dimension: int = 1024
    embedding_local_files_only: bool = True

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "presale-documents"

    gateway_url: str = "http://localhost:8001"
    gateway_masking_enabled: bool = True
    legacy_generation_enabled: bool = False
    validation_require_final_review: bool = False
    export_holistic_finalization_enabled: bool = False
    export_holistic_finalization_mode: Literal["section", "document"] = "section"
    llm_provider_backend: Literal["mock", "live"] = "mock"
    llm_timeout_seconds: float = 60.0
    llm_stream_timeout_seconds: float = 90.0
    llm_retry_attempts: int = 1
    llm_retry_backoff_seconds: float = 1.0
    llm_mock_stream_chunk_size: int = 48
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", validation_alias="DEEPSEEK_BASE_URL")
    deepseek_model_name: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    qwen_api_key: str | None = Field(default=None, validation_alias="QWEN_API_KEY")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="QWEN_BASE_URL",
    )
    qwen_model_name: str = Field(default="qwen-plus", validation_alias="QWEN_MODEL")
    doubao_api_key: str | None = Field(default=None, validation_alias="DOUBAO_API_KEY")
    doubao_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3", validation_alias="DOUBAO_BASE_URL")
    doubao_model_name: str = Field(default="doubao-seed-1-6-250615", validation_alias="DOUBAO_MODEL")
    azure_openai_api_key: str | None = Field(default=None, validation_alias="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str | None = Field(default=None, validation_alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment: str | None = Field(default=None, validation_alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version: str = Field(default="2024-10-21", validation_alias="AZURE_OPENAI_API_VERSION")
    azure_model_name: str = Field(default="azure-gpt", validation_alias="AZURE_OPENAI_MODEL")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_model_name: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")

    next_public_api_base_url: str = "http://localhost:8000/api/v1"

    model_config = SettingsConfigDict(
        env_file=(
            str(REPO_ROOT / ".env"),
            str(BACKEND_ROOT / ".env"),
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.llm_stream_timeout_seconds <= 0:
        settings.llm_stream_timeout_seconds = max(settings.llm_timeout_seconds, 1.0)
    if settings.llm_retry_attempts < 0:
        settings.llm_retry_attempts = 0
    if settings.llm_retry_backoff_seconds < 0:
        settings.llm_retry_backoff_seconds = 0.0
    if not math.isfinite(settings.llm_stream_timeout_seconds):
        settings.llm_stream_timeout_seconds = max(settings.llm_timeout_seconds, 90.0)
    if not math.isfinite(settings.llm_retry_backoff_seconds):
        settings.llm_retry_backoff_seconds = 1.0
    return settings
