from __future__ import annotations

import math
from typing import Protocol

from app.config import get_settings
from app.services.parsing.section_catalog import normalize_section_heading
from app.services.retrieval.hybrid import tokenize_hybrid_text


class Reranker(Protocol):
    @property
    def available(self) -> bool: ...

    def score_many(self, *, query: str, texts: list[str]) -> list[float]: ...


class NoopReranker:
    @property
    def available(self) -> bool:
        return False

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        return [0.0 for _ in texts]


class HeuristicReranker:
    @property
    def available(self) -> bool:
        return True

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        return [self._score_pair(query=query, text=text) for text in texts]

    def _score_pair(self, *, query: str, text: str) -> float:
        normalized_query = str(query or "").strip()
        normalized_text = str(text or "").strip()
        if not normalized_query or not normalized_text:
            return 0.0

        query_tokens = set(tokenize_hybrid_text(normalized_query, max_tokens=160))
        text_tokens = set(tokenize_hybrid_text(normalized_text, max_tokens=320))
        if not query_tokens or not text_tokens:
            return 0.0

        overlap = len(query_tokens & text_tokens) / max(len(query_tokens), 1)
        compact_query = normalize_section_heading(normalized_query).replace(" ", "")
        compact_text = normalize_section_heading(normalized_text).replace(" ", "")
        exact_ratio = 1.0 if compact_query and compact_query in compact_text else 0.0

        leading_text = "\n".join(normalized_text.splitlines()[:3])
        leading_tokens = set(tokenize_hybrid_text(leading_text, max_tokens=96))
        heading_overlap = len(query_tokens & leading_tokens) / max(len(query_tokens), 1) if leading_tokens else 0.0

        length_penalty = min(0.12, max(0.0, (len(text_tokens) - len(query_tokens) * 6) * 0.002))
        raw_score = (0.5 * exact_ratio) + (0.3 * overlap) + (0.2 * heading_overlap) - length_penalty
        return max(0.0, min(1.0, raw_score))


class CrossEncoderReranker:
    def __init__(self) -> None:
        self._model = None
        self._enabled = False
        settings = get_settings()
        if settings.reranker_backend not in {"auto", "cross-encoder"}:
            return
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                settings.reranker_model,
                local_files_only=settings.reranker_local_files_only,
            )
            self._enabled = True
        except Exception:
            self._model = None
            self._enabled = False

    @property
    def available(self) -> bool:
        return self._enabled and self._model is not None

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        if not self.available:
            return [0.0 for _ in texts]
        pairs = [(query, text) for text in texts]
        predictions = self._model.predict(pairs)
        scores: list[float] = []
        for raw_value in predictions:
            value = float(raw_value)
            scores.append(1.0 / (1.0 + math.exp(-value)))
        return scores


def build_default_reranker() -> Reranker:
    settings = get_settings()
    if settings.reranker_backend == "none":
        return NoopReranker()
    if settings.reranker_backend in {"auto", "cross-encoder"}:
        cross_encoder = CrossEncoderReranker()
        if cross_encoder.available:
            return cross_encoder
    return HeuristicReranker()
