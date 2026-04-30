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
        return self.embedder is not None and getattr(self.embedder, "backend_name", "fallback") != "fallback"

    def score(self, *, query: str, text: str) -> float:
        scores = self.score_many(query=query, texts=[text])
        return scores[0] if scores else 0.0

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        query_text = str(query or "").strip()
        target_texts = [str(text or "").strip() for text in texts]
        if not self.available or not query_text:
            return [0.0 for _text in target_texts]
        query_vector = self._embed_texts_sync([query_text])[0]
        if not query_vector:
            return [0.0 for _text in target_texts]
        target_vectors = self._embed_texts_sync(target_texts)
        return [_cosine_like_similarity(query_vector, target_vector) for target_vector in target_vectors]

    def _embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        normalized_texts = [str(text or "").strip() for text in texts]
        results: list[list[float] | None] = []
        missing: list[str] = []
        missing_positions: list[int] = []
        for index, text in enumerate(normalized_texts):
            cached = self._cache.get(text)
            if cached is not None:
                results.append(cached)
                continue
            if not text:
                results.append([])
                continue
            results.append(None)
            missing.append(text)
            missing_positions.append(index)
        if missing:
            vectors = self._embed_missing_texts_sync(missing)
            for position, text, vector in zip(missing_positions, missing, vectors):
                self._cache[text] = vector
                results[position] = vector
        return [list(vector or []) for vector in results]

    def _embed_missing_texts_sync(self, texts: list[str]) -> list[list[float]]:
        if not self.embedder:
            return [[] for _text in texts]
        if hasattr(self.embedder, "embed_texts_sync"):
            return self.embedder.embed_texts_sync(texts)
        return [self._embed_sync(text) for text in texts]

    def _embed_sync(self, text: str) -> list[float]:
        if not self.embedder:
            return []
        if hasattr(self.embedder, "embed_text_sync"):
            return self.embedder.embed_text_sync(text)
        if self._model is None:
            return []
        vector = self._model.encode(text, normalize_embeddings=True)
        return [float(item) for item in vector.tolist()]

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


def _cosine_like_similarity(query_vector: list[float], target_vector: list[float]) -> float:
    if not query_vector or not target_vector:
        return 0.0
    similarity = sum(left * right for left, right in zip(query_vector, target_vector))
    return max(0.0, min(1.0, float(similarity)))
