from __future__ import annotations

from typing import Protocol

from app.services.vectorstore.embedder import Embedder


class SemanticScorer(Protocol):
    @property
    def available(self) -> bool: ...

    def score(self, *, query: str, text: str) -> float: ...


class EmbeddingSemanticScorer:
    def __init__(self, *, embedder: Embedder | None = None) -> None:
        self.embedder = embedder
        self._model = getattr(embedder, "_model", None) if embedder is not None else None
        self._cache: dict[str, list[float]] = {}
        self._initialized = embedder is not None
        self._disabled = False

    @property
    def available(self) -> bool:
        self._ensure_backend()
        return self._model is not None and getattr(self.embedder, "backend_name", "fallback") != "fallback"

    def score(self, *, query: str, text: str) -> float:
        query_text = str(query or "").strip()
        target_text = str(text or "").strip()
        if not self.available or not query_text or not target_text:
            return 0.0
        query_vector = self._embed_sync(query_text)
        target_vector = self._embed_sync(target_text)
        if not query_vector or not target_vector:
            return 0.0
        similarity = sum(left * right for left, right in zip(query_vector, target_vector))
        return max(0.0, min(1.0, float(similarity)))

    def _embed_sync(self, text: str) -> list[float]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        vector = self._model.encode(normalized, normalize_embeddings=True)
        values = [float(item) for item in vector.tolist()]
        self._cache[normalized] = values
        return values

    def _ensure_backend(self) -> None:
        if self._initialized or self._disabled:
            return
        self._initialized = True
        try:
            self.embedder = Embedder()
            self._model = getattr(self.embedder, "_model", None)
        except Exception:
            self.embedder = None
            self._model = None
            self._disabled = True
