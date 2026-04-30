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
    DASHSCOPE_MULTIMODAL_ENDPOINT = "/services/embeddings/multimodal-embedding/multimodal-embedding"

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

        if self.backend_mode == "dashscope-multimodal":
            self._validate_dashscope_multimodal_config(strict=True)
            self.backend_name = "dashscope-multimodal"
            return

        if self.backend_mode == "sentence-transformers":
            self._model = self._load_sentence_transformer(strict=True)
        elif self.backend_mode == "auto":
            if self._looks_like_dashscope_multimodal_config() and self._validate_dashscope_multimodal_config(
                strict=False
            ):
                self.backend_name = "dashscope-multimodal"
                return
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
        if self.backend_name == "dashscope-multimodal":
            vectors = []
            for start in range(0, len(texts), max(1, int(self.batch_size or 1))):
                vectors.extend(await self._embed_texts_dashscope_multimodal(texts[start : start + self.batch_size]))
            return vectors
        if self._model is not None:
            vectors = await asyncio.to_thread(self._model.encode, texts, normalize_embeddings=True)
            return [self._coerce_vector(vector) for vector in vectors]
        return [self._fallback_vector(text) for text in texts]

    def embed_text_sync(self, text: str) -> list[float]:
        vectors = self.embed_texts_sync([text])
        return vectors[0]

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.backend_name == "openai-compatible":
            vectors: list[list[float]] = []
            batch_size = max(1, int(self.batch_size or 1))
            for start in range(0, len(texts), batch_size):
                vectors.extend(self._embed_texts_openai_compatible_sync(texts[start : start + batch_size]))
            return vectors
        if self.backend_name == "dashscope-multimodal":
            vectors = []
            batch_size = max(1, int(self.batch_size or 1))
            for start in range(0, len(texts), batch_size):
                vectors.extend(self._embed_texts_dashscope_multimodal_sync(texts[start : start + batch_size]))
            return vectors
        if self._model is not None:
            vectors = self._model.encode(texts, normalize_embeddings=True)
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
        return self._parse_openai_compatible_vectors(data, expected_count=len(texts))

    def _embed_texts_openai_compatible_sync(self, texts: list[str]) -> list[list[float]]:
        url = self._embedding_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": texts,
        }
        with httpx.Client(timeout=float(self.timeout_seconds or 45.0)) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return self._parse_openai_compatible_vectors(data, expected_count=len(texts))

    async def _embed_texts_dashscope_multimodal(self, texts: list[str]) -> list[list[float]]:
        payload = self._dashscope_multimodal_payload(texts)
        data = await self._post_embedding_request(self._dashscope_multimodal_embedding_url(), payload)
        vectors = self._parse_dashscope_multimodal_vectors(data)
        if len(vectors) == len(texts):
            return vectors

        if len(texts) > 1:
            # qwen3-vl-embedding should return one vector per separate content item.
            # If a relay/provider returns a fused vector instead, fall back to one
            # request per text to preserve the vectorstore contract.
            fallback_vectors: list[list[float]] = []
            for text in texts:
                single_data = await self._post_embedding_request(
                    self._dashscope_multimodal_embedding_url(),
                    self._dashscope_multimodal_payload([text]),
                )
                single_vectors = self._parse_dashscope_multimodal_vectors(single_data)
                if len(single_vectors) != 1:
                    raise RuntimeError(
                        f"dashscope multimodal embedding provider returned {len(single_vectors)} vectors "
                        "for one input"
                    )
                fallback_vectors.extend(single_vectors)
            return fallback_vectors

        raise RuntimeError(
            f"dashscope multimodal embedding provider returned {len(vectors)} vectors for {len(texts)} inputs"
        )

    def _embed_texts_dashscope_multimodal_sync(self, texts: list[str]) -> list[list[float]]:
        payload = self._dashscope_multimodal_payload(texts)
        data = self._post_embedding_request_sync(self._dashscope_multimodal_embedding_url(), payload)
        vectors = self._parse_dashscope_multimodal_vectors(data)
        if len(vectors) == len(texts):
            return vectors

        if len(texts) > 1:
            fallback_vectors: list[list[float]] = []
            for text in texts:
                single_data = self._post_embedding_request_sync(
                    self._dashscope_multimodal_embedding_url(),
                    self._dashscope_multimodal_payload([text]),
                )
                single_vectors = self._parse_dashscope_multimodal_vectors(single_data)
                if len(single_vectors) != 1:
                    raise RuntimeError(
                        f"dashscope multimodal embedding provider returned {len(single_vectors)} vectors "
                        "for one input"
                    )
                fallback_vectors.extend(single_vectors)
            return fallback_vectors

        raise RuntimeError(
            f"dashscope multimodal embedding provider returned {len(vectors)} vectors for {len(texts)} inputs"
        )

    async def _post_embedding_request(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=float(self.timeout_seconds or 45.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return self._validate_embedding_response_payload(data)

    def _post_embedding_request_sync(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=float(self.timeout_seconds or 45.0)) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return self._validate_embedding_response_payload(data)

    def _embedding_url(self) -> str:
        base_url = str(self.base_url or "").strip()
        endpoint_path = str(self.endpoint_path or "/embeddings").strip() or "/embeddings"
        if not base_url:
            raise RuntimeError("EMBEDDING_BASE_URL is required for openai-compatible embeddings")
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path
        return urljoin(base_url.rstrip("/") + "/", endpoint_path.lstrip("/"))

    def _dashscope_multimodal_embedding_url(self) -> str:
        base_url = str(self.base_url or "").strip()
        if not base_url:
            raise RuntimeError("EMBEDDING_BASE_URL is required for dashscope-multimodal embeddings")

        endpoint_path = str(self.endpoint_path or "").strip()
        if not endpoint_path or endpoint_path == "/embeddings":
            endpoint_path = self.DASHSCOPE_MULTIMODAL_ENDPOINT
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path

        normalized_base = base_url.rstrip("/")
        if normalized_base.endswith("/compatible-mode/v1"):
            normalized_base = normalized_base[: -len("/compatible-mode/v1")] + "/api/v1"
        elif normalized_base.endswith("/compatible-mode"):
            normalized_base = normalized_base[: -len("/compatible-mode")] + "/api/v1"
        elif not normalized_base.endswith("/api/v1"):
            normalized_base = normalized_base + "/api/v1"
        return urljoin(normalized_base.rstrip("/") + "/", endpoint_path.lstrip("/"))

    def _dashscope_multimodal_payload(self, texts: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": {"contents": [{"text": text} for text in texts]},
        }
        if int(self.dimension or 0) > 0:
            payload["parameters"] = {"dimension": int(self.dimension)}
        return payload

    def _parse_dashscope_multimodal_vectors(self, data: dict[str, Any]) -> list[list[float]]:
        output = data.get("output")
        if not isinstance(output, dict):
            raise RuntimeError("dashscope multimodal embedding provider returned no output object")
        items = output.get("embeddings")
        if not isinstance(items, list):
            raise RuntimeError("dashscope multimodal embedding provider returned no embeddings list")
        ordered_items = sorted(
            items,
            key=lambda item: int(item.get("index", item.get("text_index", 0))) if isinstance(item, dict) else 0,
        )
        vectors: list[list[float]] = []
        for item in ordered_items:
            if isinstance(item, dict) and "message" in item and "embedding" not in item:
                raise RuntimeError(f"dashscope multimodal embedding item failed: {item.get('message')}")
            vectors.append(self._extract_embedding_vector(item))
        return vectors

    def _parse_openai_compatible_vectors(self, data: dict[str, Any], *, expected_count: int) -> list[list[float]]:
        items = data.get("data")
        if not isinstance(items, list):
            raise RuntimeError("embedding provider returned no data list")
        ordered_items = sorted(items, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors: list[list[float]] = []
        for item in ordered_items:
            if not isinstance(item, dict) or "embedding" not in item:
                raise RuntimeError("embedding provider returned an invalid embedding item")
            vectors.append(self._coerce_vector(item["embedding"]))
        if len(vectors) != expected_count:
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {expected_count} inputs")
        return vectors

    def _validate_embedding_response_payload(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise RuntimeError("embedding provider returned a non-object response")
        status_code = data.get("status_code")
        if isinstance(status_code, int) and status_code >= 400:
            message = data.get("message") or data.get("code") or "unknown error"
            raise RuntimeError(f"embedding provider failed: {message}")
        if str(data.get("code") or "").strip():
            message = data.get("message") or data.get("code")
            raise RuntimeError(f"embedding provider failed: {message}")
        return data

    def _extract_embedding_vector(self, item: Any) -> list[float]:
        if isinstance(item, dict):
            if "embedding" not in item:
                raise RuntimeError("embedding provider returned an invalid embedding item")
            return self._coerce_vector(item["embedding"])
        return self._coerce_vector(item)

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

    def _validate_dashscope_multimodal_config(self, *, strict: bool) -> bool:
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
                    "dashscope-multimodal embedding backend requires "
                    + ", ".join(missing)
                    + " (QWEN_API_KEY can be used as API key fallback)"
                )
            return False
        return True

    def _looks_like_dashscope_multimodal_config(self) -> bool:
        model_name = str(self.model_name or "").strip().lower()
        endpoint_path = str(self.endpoint_path or "").strip().lower()
        base_url = str(self.base_url or "").strip().lower()
        return (
            "vl-embedding" in model_name
            or "multimodal-embedding" in endpoint_path
            or ("dashscope.aliyuncs.com" in base_url and endpoint_path != "/embeddings")
        )

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
        if normalized in {"dashscope", "dashscope-multimodal", "dashscope-multimodal-embedding", "multimodal"}:
            return "dashscope-multimodal"
        return normalized

    @staticmethod
    def _is_blank_or_placeholder(value: str | None) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"", "sk-xxxxx", "replace-with-real-key", "your-api-key", "xxx"}
