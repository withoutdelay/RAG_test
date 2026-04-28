from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any
from urllib.parse import urljoin

import httpx

try:
    from app.config import get_settings
except Exception:  # pragma: no cover - optional during lightweight test runs
    get_settings = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional runtime dependency
    SentenceTransformer = None


class Embedder:
    def __init__(self) -> None:
        if get_settings is not None:
            settings = get_settings()
            self.backend_mode = settings.embedding_backend
            self.dimension = settings.embedding_dimension
            self.model_name = settings.embedding_model
            self.local_files_only = settings.embedding_local_files_only
            self.device = settings.embedding_device
            self.api_key = settings.embedding_api_key or settings.qwen_api_key or settings.openai_api_key
            self.base_url = settings.embedding_base_url or settings.qwen_base_url or settings.openai_base_url
            self.endpoint_path = settings.embedding_endpoint_path
            self.timeout_seconds = settings.embedding_timeout_seconds
            self.batch_size = settings.embedding_batch_size
        else:
            self.backend_mode = os.getenv("EMBEDDING_BACKEND", "fallback")
            self.dimension = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
            self.model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
            self.local_files_only = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            self.device = os.getenv("EMBEDDING_DEVICE", "cpu")
            self.api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("QWEN_API_KEY") or os.getenv("OPENAI_API_KEY")
            self.base_url = (
                os.getenv("EMBEDDING_BASE_URL")
                or os.getenv("QWEN_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            self.endpoint_path = os.getenv("EMBEDDING_ENDPOINT_PATH", "/embeddings")
            self.timeout_seconds = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "45"))
            self.batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))
        self._model = None
        self.backend_mode = self._normalize_backend_mode(self.backend_mode)
        self.backend_name = "fallback"

        if self.backend_mode == "fallback":
            return

        if self.backend_mode == "openai-compatible":
            self._validate_openai_compatible_config(strict=True)
            self.backend_name = "openai-compatible"
            return

        if self.backend_mode == "sentence-transformers":
            self._model = self._load_sentence_transformer(strict=True)
        elif self.backend_mode == "auto":
            if self._validate_openai_compatible_config(strict=False):
                self.backend_name = "openai-compatible"
                return
            self._model = self._load_sentence_transformer(strict=False)

        if self._model is not None:
            self.backend_name = "sentence-transformers"

    async def embed_text(self, text: str) -> list[float]:
        """
        Prefer a real sentence-transformers model when available, otherwise
        fall back to a deterministic pseudo-embedding that preserves the vector
        contract for local development.
        """
        vectors = await self.embed_texts([text])
        return vectors[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.backend_name == "openai-compatible":
            vectors: list[list[float]] = []
            for start in range(0, len(texts), max(1, int(self.batch_size or 1))):
                vectors.extend(await self._embed_texts_openai_compatible(texts[start : start + self.batch_size]))
            return vectors
        if self._model is not None:
            vectors = await asyncio.to_thread(self._model.encode, texts, normalize_embeddings=True)
            return [self._coerce_vector(vector) for vector in vectors]
        return [self._fallback_vector(text) for text in texts]

    def _fallback_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = list(digest) * ((self.dimension // len(digest)) + 1)
        return [(byte / 255.0) * 2 - 1 for byte in seed[: self.dimension]]

    def _load_sentence_transformer(self, *, strict: bool) -> SentenceTransformer | None:
        if SentenceTransformer is None:
            if strict:
                raise RuntimeError("sentence-transformers backend requested but dependency is not installed")
            return None
        if self.model_name.startswith("fallback"):
            if strict:
                raise RuntimeError("sentence-transformers backend requested with fallback model name")
            return None
        try:
            device = str(self.device or "cpu").strip() or "cpu"
            return SentenceTransformer(self.model_name, local_files_only=self.local_files_only, device=device)
        except Exception:
            if strict:
                raise
            return None

    async def _embed_texts_openai_compatible(self, texts: list[str]) -> list[list[float]]:
        url = self._embedding_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": texts,
        }
        async with httpx.AsyncClient(timeout=float(self.timeout_seconds or 45.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        items = data.get("data")
        if not isinstance(items, list):
            raise RuntimeError("embedding provider returned no data list")
        ordered_items = sorted(items, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors: list[list[float]] = []
        for item in ordered_items:
            if not isinstance(item, dict) or "embedding" not in item:
                raise RuntimeError("embedding provider returned an invalid embedding item")
            vectors.append(self._coerce_vector(item["embedding"]))
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(texts)} inputs")
        return vectors

    def _embedding_url(self) -> str:
        base_url = str(self.base_url or "").strip()
        endpoint_path = str(self.endpoint_path or "/embeddings").strip() or "/embeddings"
        if not base_url:
            raise RuntimeError("EMBEDDING_BASE_URL is required for openai-compatible embeddings")
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path
        return urljoin(base_url.rstrip("/") + "/", endpoint_path.lstrip("/"))

    def _validate_openai_compatible_config(self, *, strict: bool) -> bool:
        missing = []
        if self._is_blank_or_placeholder(self.api_key):
            missing.append("EMBEDDING_API_KEY")
        if not str(self.base_url or "").strip():
            missing.append("EMBEDDING_BASE_URL")
        if not str(self.model_name or "").strip():
            missing.append("EMBEDDING_MODEL")
        if missing:
            if strict:
                raise RuntimeError(
                    "openai-compatible embedding backend requires "
                    + ", ".join(missing)
                    + " (QWEN_API_KEY/OPENAI_API_KEY can be used as API key fallback)"
                )
            return False
        return True

    def _coerce_vector(self, vector: Any) -> list[float]:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if not isinstance(vector, list):
            raise RuntimeError("embedding provider returned a non-list vector")
        values = [float(value) for value in vector]
        if self.dimension > 0 and len(values) != self.dimension:
            raise RuntimeError(
                f"embedding vector dimension mismatch: configured {self.dimension}, provider returned {len(values)}"
            )
        return values

    @staticmethod
    def _normalize_backend_mode(value: str) -> str:
        normalized = str(value or "fallback").strip().lower().replace("_", "-")
        if normalized in {"openai", "openai-compatible", "api", "remote"}:
            return "openai-compatible"
        return normalized

    @staticmethod
    def _is_blank_or_placeholder(value: str | None) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"", "sk-xxxxx", "replace-with-real-key", "your-api-key", "xxx"}
