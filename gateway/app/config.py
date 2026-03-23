from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/1"
    redis_enabled: bool = False
    mapping_ttl_seconds: int = 3600
    mapping_lock_timeout_seconds: int = 10
    mapping_lock_blocking_timeout_seconds: int = 5
    enable_presidio: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
