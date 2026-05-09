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
    auth_enabled: bool = Field(default=False, validation_alias="AUTH_ENABLED")
    auth_username: str = Field(default="admin", validation_alias="AUTH_USERNAME")
    auth_password: str | None = Field(default=None, validation_alias="AUTH_PASSWORD")
    auth_password_hash: str | None = Field(default=None, validation_alias="AUTH_PASSWORD_HASH")
    auth_users: str = Field(default="", validation_alias="AUTH_USERS")
    auth_session_cookie_name: str = Field(default="presale_session", validation_alias="AUTH_SESSION_COOKIE_NAME")
    auth_session_ttl_seconds: int = Field(default=28800, validation_alias="AUTH_SESSION_TTL_SECONDS")
    auth_cookie_secure: bool = Field(default=False, validation_alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        validation_alias="AUTH_COOKIE_SAMESITE",
    )
    cors_allow_origins: str = Field(default="", validation_alias="CORS_ALLOW_ORIGINS")

    database_url: str = "postgresql+asyncpg://copilot:copilot@localhost:5432/copilot_db"
    redis_url: str = "redis://localhost:6379/0"

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_location: str | None = None
    qdrant_collection: str = "presale_knowledge"
    case_library_outline_path: str = str(CASE_LIBRARY_ROOT / "outline_library.json")
    case_library_block_path: str = str(CASE_LIBRARY_ROOT / "block_library.json")
    hf_endpoint: str | None = Field(default=None, validation_alias="HF_ENDPOINT")
    hf_home: str | None = Field(default=None, validation_alias="HF_HOME")
    parser_backend: Literal["auto", "docling", "fallback", "aliyun_docmind", "docmind"] = "auto"
    parser_cpu_threads: int = Field(default=1, validation_alias="PARSER_CPU_THREADS")
    parser_process_isolation_enabled: bool = Field(default=True, validation_alias="PARSER_PROCESS_ISOLATION_ENABLED")
    parser_document_timeout_seconds: float = Field(default=7200.0, validation_alias="PARSER_DOCUMENT_TIMEOUT_SECONDS")
    parser_cloud_fallback_enabled: bool = Field(default=True, validation_alias="PARSER_CLOUD_FALLBACK_ENABLED")
    parser_cloud_direct_min_bytes: int = Field(default=30 * 1024 * 1024, validation_alias="PARSER_CLOUD_DIRECT_MIN_BYTES")
    docling_libreoffice_cmd: str | None = None
    docling_cache_dir: str | None = Field(default=None, validation_alias="DOCLING_CACHE_DIR")
    docling_artifacts_path: str | None = Field(default=None, validation_alias="DOCLING_ARTIFACTS_PATH")
    docling_prewarm_models_on_startup: bool = Field(
        default=False,
        validation_alias="DOCLING_PREWARM_MODELS_ON_STARTUP",
    )
    aliyun_docmind_access_key_id: str | None = Field(default=None, validation_alias="ALIYUN_DOCMIND_ACCESS_KEY_ID")
    aliyun_docmind_access_key_secret: str | None = Field(default=None, validation_alias="ALIYUN_DOCMIND_ACCESS_KEY_SECRET")
    aliyun_docmind_endpoint: str = Field(
        default="docmind-api.cn-hangzhou.aliyuncs.com",
        validation_alias="ALIYUN_DOCMIND_ENDPOINT",
    )
    aliyun_docmind_poll_interval_seconds: float = Field(default=2.0, validation_alias="ALIYUN_DOCMIND_POLL_INTERVAL_SECONDS")
    aliyun_docmind_timeout_seconds: float = Field(default=900.0, validation_alias="ALIYUN_DOCMIND_TIMEOUT_SECONDS")
    aliyun_docmind_llm_enhancement: bool = Field(default=False, validation_alias="ALIYUN_DOCMIND_LLM_ENHANCEMENT")
    aliyun_docmind_enhancement_mode: str = Field(default="", validation_alias="ALIYUN_DOCMIND_ENHANCEMENT_MODE")
    aliyun_docmind_formula_enhancement: bool = Field(default=False, validation_alias="ALIYUN_DOCMIND_FORMULA_ENHANCEMENT")
    aliyun_docmind_output_html_table: bool = Field(default=False, validation_alias="ALIYUN_DOCMIND_OUTPUT_HTML_TABLE")
    aliyun_docmind_fetch_image_assets: bool = Field(default=True, validation_alias="ALIYUN_DOCMIND_FETCH_IMAGE_ASSETS")
    aliyun_docmind_max_image_assets: int = Field(default=80, validation_alias="ALIYUN_DOCMIND_MAX_IMAGE_ASSETS")
    aliyun_docmind_max_image_bytes: int = Field(default=5000000, validation_alias="ALIYUN_DOCMIND_MAX_IMAGE_BYTES")
    aliyun_docmind_convert_office_to_pdf_min_bytes: int = Field(
        default=30 * 1024 * 1024,
        validation_alias="ALIYUN_DOCMIND_CONVERT_OFFICE_TO_PDF_MIN_BYTES",
    )
    parser_llm_asset_review_enabled: bool = False
    parser_llm_asset_review_max_assets: int = 12
    parser_llm_asset_review_confidence_threshold: float = 0.72
    parser_llm_asset_review_use_vision: bool = True
    parser_llm_asset_review_image_detail: Literal["auto", "low", "high"] = "auto"
    parser_llm_asset_review_max_image_bytes: int = 800000
    parser_llm_asset_review_timeout_seconds: float = 45.0
    parser_llm_asset_summary_enabled: bool = False
    parser_llm_asset_summary_max_assets: int = 12
    parser_llm_asset_summary_use_vision: bool = True
    parser_llm_asset_summary_image_detail: Literal["auto", "low", "high"] = "auto"
    parser_llm_asset_summary_max_image_bytes: int = 800000
    parser_llm_asset_summary_timeout_seconds: float = 60.0
    runtime_asset_vision_gate_enabled: bool = Field(default=True, validation_alias="RUNTIME_ASSET_VISION_GATE_ENABLED")
    runtime_asset_vision_gate_max_assets: int = Field(default=4, validation_alias="RUNTIME_ASSET_VISION_GATE_MAX_ASSETS")
    runtime_asset_vision_gate_image_detail: Literal["auto", "low", "high"] = Field(
        default="auto",
        validation_alias="RUNTIME_ASSET_VISION_GATE_IMAGE_DETAIL",
    )
    runtime_asset_vision_gate_max_image_bytes: int = Field(
        default=800000,
        validation_alias="RUNTIME_ASSET_VISION_GATE_MAX_IMAGE_BYTES",
    )
    runtime_asset_vision_gate_timeout_seconds: float = Field(
        default=18.0,
        validation_alias="RUNTIME_ASSET_VISION_GATE_TIMEOUT_SECONDS",
    )
    parser_asset_quality_gate_enabled: bool = True
    safe_ingestion_enabled: bool = True
    formula_ocr_backend: Literal["none", "pix2tex"] = "none"
    formula_ocr_max_assets: int = 3
    formula_ocr_max_regions_per_asset: int = 3
    embedding_backend: Literal[
        "auto",
        "sentence-transformers",
        "openai-compatible",
        "openai_compatible",
        "dashscope",
        "multimodal",
        "dashscope-multimodal",
        "dashscope_multimodal",
        "fallback",
    ] = "fallback"
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_dimension: int = 1024
    embedding_local_files_only: bool = True
    embedding_device: str = Field(default="cpu", validation_alias="EMBEDDING_DEVICE")
    embedding_api_key: str | None = Field(default=None, validation_alias="EMBEDDING_API_KEY")
    embedding_base_url: str | None = Field(default=None, validation_alias="EMBEDDING_BASE_URL")
    embedding_endpoint_path: str = Field(default="/embeddings", validation_alias="EMBEDDING_ENDPOINT_PATH")
    embedding_timeout_seconds: float = Field(default=45.0, validation_alias="EMBEDDING_TIMEOUT_SECONDS")
    embedding_batch_size: int = Field(default=16, validation_alias="EMBEDDING_BATCH_SIZE")
    background_job_worker_count: int = Field(default=1, validation_alias="BACKGROUND_JOB_WORKER_COUNT")
    visual_embedding_backend: Literal[
        "proxy",
        "auto",
        "clip",
        "dashscope",
        "dashscope-multimodal",
        "dashscope_multimodal",
        "multimodal",
    ] = "proxy"
    visual_embedding_model: str = "openai/clip-vit-base-patch32"
    visual_embedding_local_files_only: bool = True
    visual_embedding_device: str = "cpu"
    visual_embedding_cache_path: str = str(BACKEND_ROOT / "data" / "visual_index" / "asset_embedding_cache.json")
    visual_qdrant_collection_prefix: str = "presale_visual_assets"
    reranker_backend: Literal["none", "auto", "cross-encoder", "heuristic"] = "heuristic"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_local_files_only: bool = True

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
    section_generation_concurrency: int = Field(default=1, validation_alias="SECTION_GENERATION_CONCURRENCY")
    section_generation_quality_gate: Literal["full", "skip"] = Field(
        default="full",
        validation_alias="SECTION_GENERATION_QUALITY_GATE",
    )
    section_generation_fast_coherence_pass: bool = Field(
        default=True,
        validation_alias="SECTION_GENERATION_FAST_COHERENCE_PASS",
    )
    section_generation_item_timeout_seconds: float = Field(
        default=240.0,
        validation_alias="SECTION_GENERATION_ITEM_TIMEOUT_SECONDS",
    )
    section_generation_granularity: Literal["top_level", "all_nodes"] = Field(
        default="top_level",
        validation_alias="SECTION_GENERATION_GRANULARITY",
    )
    section_reuse_context_mode: Literal["section_pack", "auto", "prefer_full_section"] = Field(
        default="prefer_full_section",
        validation_alias="SECTION_REUSE_CONTEXT_MODE",
    )
    section_reuse_candidate_limit: int = Field(default=16, validation_alias="SECTION_REUSE_CANDIDATE_LIMIT")
    section_reuse_full_section_block_limit: int = Field(
        default=32,
        validation_alias="SECTION_REUSE_FULL_SECTION_BLOCK_LIMIT",
    )
    section_reuse_full_section_budget_enabled: bool = Field(
        default=False,
        validation_alias="SECTION_REUSE_FULL_SECTION_BUDGET_ENABLED",
    )
    section_reuse_full_section_max_tokens: int = Field(
        default=4000,
        validation_alias="SECTION_REUSE_FULL_SECTION_MAX_TOKENS",
    )
    evidence_judge_mode: Literal["off", "auto", "strict"] = Field(
        default="auto",
        validation_alias="EVIDENCE_JUDGE_MODE",
    )
    evidence_judge_max_candidates: int = Field(default=10, validation_alias="EVIDENCE_JUDGE_MAX_CANDIDATES")
    evidence_judge_timeout_seconds: float = Field(default=18.0, validation_alias="EVIDENCE_JUDGE_TIMEOUT_SECONDS")
    evidence_selector_mode: Literal["off", "auto", "strict"] = Field(
        default="off",
        validation_alias="EVIDENCE_SELECTOR_MODE",
    )
    evidence_selector_max_sections: int = Field(default=6, validation_alias="EVIDENCE_SELECTOR_MAX_SECTIONS")
    evidence_selector_max_blocks: int = Field(default=10, validation_alias="EVIDENCE_SELECTOR_MAX_BLOCKS")
    evidence_selector_max_assets: int = Field(default=8, validation_alias="EVIDENCE_SELECTOR_MAX_ASSETS")
    evidence_selector_min_confidence: float = Field(default=0.62, validation_alias="EVIDENCE_SELECTOR_MIN_CONFIDENCE")
    evidence_selector_timeout_seconds: float = Field(default=18.0, validation_alias="EVIDENCE_SELECTOR_TIMEOUT_SECONDS")
    case_library_section_rerank_enabled: bool = Field(
        default=False,
        validation_alias="CASE_LIBRARY_SECTION_RERANK_ENABLED",
    )
    case_library_section_semantic_enabled: bool = Field(
        default=False,
        validation_alias="CASE_LIBRARY_SECTION_SEMANTIC_ENABLED",
    )
    llm_provider_backend: Literal["mock", "live"] = "mock"
    llm_timeout_seconds: float = 60.0
    llm_stream_timeout_seconds: float = 90.0
    llm_retry_attempts: int = 1
    llm_retry_backoff_seconds: float = 1.0
    llm_mock_stream_chunk_size: int = 48
    wiki_llm_compile_enabled: bool = Field(default=False, validation_alias="WIKI_LLM_COMPILE_ENABLED")
    wiki_llm_compile_max_seed_items: int = Field(default=24, validation_alias="WIKI_LLM_COMPILE_MAX_SEED_ITEMS")
    wiki_llm_compile_max_evidence_per_item: int = Field(
        default=3,
        validation_alias="WIKI_LLM_COMPILE_MAX_EVIDENCE_PER_ITEM",
    )
    wiki_llm_compile_timeout_seconds: float = Field(
        default=60.0,
        validation_alias="WIKI_LLM_COMPILE_TIMEOUT_SECONDS",
    )
    wiki_llm_compile_use_vision: bool = Field(default=True, validation_alias="WIKI_LLM_COMPILE_USE_VISION")
    wiki_llm_compile_image_detail: Literal["auto", "low", "high"] = Field(
        default="auto",
        validation_alias="WIKI_LLM_COMPILE_IMAGE_DETAIL",
    )
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
    openai_api_style: Literal["auto", "responses", "chat_completions"] = Field(
        default="auto",
        validation_alias="OPENAI_API_STYLE",
    )
    vision_llm_api_key: str | None = Field(default=None, validation_alias="VISION_LLM_API_KEY")
    vision_llm_base_url: str | None = Field(default=None, validation_alias="VISION_LLM_BASE_URL")
    vision_llm_model_name: str = Field(default="gpt-4o-mini", validation_alias="VISION_LLM_MODEL")
    vision_llm_api_style: Literal["auto", "responses", "chat_completions"] = Field(
        default="auto",
        validation_alias="VISION_LLM_API_STYLE",
    )

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
    if settings.wiki_llm_compile_max_seed_items < 1:
        settings.wiki_llm_compile_max_seed_items = 1
    if settings.wiki_llm_compile_max_evidence_per_item < 1:
        settings.wiki_llm_compile_max_evidence_per_item = 1
    if settings.wiki_llm_compile_timeout_seconds <= 0 or not math.isfinite(settings.wiki_llm_compile_timeout_seconds):
        settings.wiki_llm_compile_timeout_seconds = max(settings.llm_timeout_seconds, 1.0)
    if settings.parser_llm_asset_review_timeout_seconds <= 0 or not math.isfinite(
        settings.parser_llm_asset_review_timeout_seconds
    ):
        settings.parser_llm_asset_review_timeout_seconds = 45.0
    if settings.parser_llm_asset_summary_timeout_seconds <= 0 or not math.isfinite(
        settings.parser_llm_asset_summary_timeout_seconds
    ):
        settings.parser_llm_asset_summary_timeout_seconds = 60.0
    if settings.aliyun_docmind_poll_interval_seconds <= 0 or not math.isfinite(
        settings.aliyun_docmind_poll_interval_seconds
    ):
        settings.aliyun_docmind_poll_interval_seconds = 2.0
    if settings.aliyun_docmind_timeout_seconds <= 0 or not math.isfinite(settings.aliyun_docmind_timeout_seconds):
        settings.aliyun_docmind_timeout_seconds = 900.0
    if settings.aliyun_docmind_max_image_assets < 0:
        settings.aliyun_docmind_max_image_assets = 0
    if settings.aliyun_docmind_max_image_bytes < 0:
        settings.aliyun_docmind_max_image_bytes = 0
    if settings.aliyun_docmind_convert_office_to_pdf_min_bytes < 0:
        settings.aliyun_docmind_convert_office_to_pdf_min_bytes = 0
    if settings.parser_cpu_threads < 1:
        settings.parser_cpu_threads = 1
    if settings.parser_cpu_threads > 2:
        settings.parser_cpu_threads = 2
    if settings.parser_document_timeout_seconds <= 0 or not math.isfinite(settings.parser_document_timeout_seconds):
        settings.parser_document_timeout_seconds = 7200.0
    if settings.parser_cloud_direct_min_bytes < 0:
        settings.parser_cloud_direct_min_bytes = 0
    if settings.section_generation_concurrency < 1:
        settings.section_generation_concurrency = 1
    if settings.section_generation_item_timeout_seconds <= 0 or not math.isfinite(
        settings.section_generation_item_timeout_seconds
    ):
        settings.section_generation_item_timeout_seconds = 240.0
    if settings.background_job_worker_count < 1:
        settings.background_job_worker_count = 1
    if settings.background_job_worker_count > 4:
        settings.background_job_worker_count = 4
    if settings.embedding_timeout_seconds <= 0 or not math.isfinite(settings.embedding_timeout_seconds):
        settings.embedding_timeout_seconds = 45.0
    if settings.embedding_batch_size < 1:
        settings.embedding_batch_size = 1
    if settings.embedding_batch_size > 128:
        settings.embedding_batch_size = 128
    if settings.section_reuse_candidate_limit < 5:
        settings.section_reuse_candidate_limit = 5
    if settings.section_reuse_candidate_limit > 64:
        settings.section_reuse_candidate_limit = 64
    if settings.section_reuse_full_section_block_limit < settings.section_reuse_candidate_limit:
        settings.section_reuse_full_section_block_limit = settings.section_reuse_candidate_limit
    if settings.section_reuse_full_section_block_limit > 96:
        settings.section_reuse_full_section_block_limit = 96
    if settings.section_reuse_full_section_max_tokens < 1000:
        settings.section_reuse_full_section_max_tokens = 1000
    if settings.section_reuse_full_section_max_tokens > 20000:
        settings.section_reuse_full_section_max_tokens = 20000
    if settings.evidence_judge_max_candidates < 1:
        settings.evidence_judge_max_candidates = 1
    if settings.evidence_judge_max_candidates > 16:
        settings.evidence_judge_max_candidates = 16
    if settings.evidence_judge_timeout_seconds <= 0 or not math.isfinite(settings.evidence_judge_timeout_seconds):
        settings.evidence_judge_timeout_seconds = 18.0
    if settings.evidence_selector_max_sections < 1:
        settings.evidence_selector_max_sections = 1
    if settings.evidence_selector_max_blocks < 1:
        settings.evidence_selector_max_blocks = 1
    if settings.evidence_selector_max_assets < 1:
        settings.evidence_selector_max_assets = 1
    if settings.evidence_selector_min_confidence < 0:
        settings.evidence_selector_min_confidence = 0.0
    if settings.evidence_selector_min_confidence > 1:
        settings.evidence_selector_min_confidence = 1.0
    if settings.evidence_selector_timeout_seconds <= 0 or not math.isfinite(settings.evidence_selector_timeout_seconds):
        settings.evidence_selector_timeout_seconds = 18.0
    return settings
