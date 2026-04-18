from __future__ import annotations

import hashlib
import os

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
        self._model = None
        self.backend_name = "fallback"

        if self.backend_mode == "fallback":
            return

        if self.backend_mode == "sentence-transformers":
            self._model = self._load_sentence_transformer(strict=True)
        elif self.backend_mode == "auto":
            self._model = self._load_sentence_transformer(strict=False)

        if self._model is not None:
            self.backend_name = "sentence-transformers"

    async def embed_text(self, text: str) -> list[float]:
        """
        Prefer a real sentence-transformers model when available, otherwise
        fall back to a deterministic pseudo-embedding that preserves the vector
        contract for local development.
        """
        if self._model is not None:
            vector = self._model.encode(text, normalize_embeddings=True)
            return [float(value) for value in vector.tolist()]
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = list(digest) * ((self.dimension // len(digest)) + 1)
        vector = [(byte / 255.0) * 2 - 1 for byte in seed[: self.dimension]]
        return vector

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
            return SentenceTransformer(self.model_name, local_files_only=self.local_files_only)
        except Exception:
            if strict:
                raise
            return None
