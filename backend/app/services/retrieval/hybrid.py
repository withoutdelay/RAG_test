from __future__ import annotations

from collections import Counter
import math
import re
from typing import Iterable

from app.services.parsing.section_catalog import normalize_section_heading


HYBRID_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")


def tokenize_hybrid_text(text: str, *, max_tokens: int = 512) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    tokens: list[str] = []
    for raw_token in HYBRID_TOKEN_PATTERN.findall(normalized):
        token = str(raw_token).strip().casefold()
        if not token:
            continue
        tokens.append(token)
        if _contains_cjk(token):
            _append_cjk_ngrams(tokens, token=token, max_tokens=max_tokens)
        if len(tokens) >= max_tokens:
            break
    return tokens[:max_tokens]


def build_term_counts(text: str, *, max_tokens: int = 512) -> Counter[str]:
    return Counter(tokenize_hybrid_text(text, max_tokens=max_tokens))


def build_document_frequencies(
    *,
    query_terms: Iterable[str],
    candidate_term_counts: Iterable[Counter[str]],
) -> dict[str, int]:
    normalized_terms = [str(term or "").strip().casefold() for term in query_terms if str(term or "").strip()]
    counters = list(candidate_term_counts)
    return {
        term: sum(1 for counts in counters if counts.get(term, 0) > 0)
        for term in normalized_terms
    }


def bm25_sparse_score(
    *,
    query_text: str,
    query_terms: list[str],
    candidate_text: str,
    candidate_term_counts: Counter[str],
    document_frequencies: dict[str, int],
    corpus_size: int,
    avg_doc_length: float,
    k1: float = 1.2,
    b: float = 0.75,
    phrase_bonus: float = 0.8,
) -> float:
    text = str(candidate_text or "").strip()
    if not text or not candidate_term_counts:
        return 0.0
    compact_query = normalize_section_heading(query_text).replace(" ", "")
    compact_candidate = normalize_section_heading(text).replace(" ", "")
    score = 0.0
    doc_length = sum(candidate_term_counts.values())
    normalized_doc_length = max(avg_doc_length, 1.0)
    for raw_term in query_terms:
        term = str(raw_term or "").strip().casefold()
        if not term:
            continue
        tf = candidate_term_counts.get(term, 0)
        if tf <= 0:
            continue
        df = int(document_frequencies.get(term) or 0)
        if df <= 0:
            continue
        idf = math.log(1.0 + ((corpus_size - df + 0.5) / (df + 0.5)))
        numerator = tf * (k1 + 1.0)
        denominator = tf + k1 * (1.0 - b + b * (doc_length / normalized_doc_length))
        if denominator <= 0:
            continue
        score += idf * (numerator / denominator)
    if compact_query and compact_query in compact_candidate:
        score += phrase_bonus
    return score


def normalize_score_list(scores: list[float]) -> list[float]:
    if not scores:
        return []
    cleaned = [max(0.0, float(score or 0.0)) for score in scores]
    max_score = max(cleaned)
    min_score = min(cleaned)
    if max_score <= 0:
        return [0.0 for _ in cleaned]
    if math.isclose(max_score, min_score):
        return [min(1.0, value / max_score) for value in cleaned]
    spread = max_score - min_score
    return [(value - min_score) / spread for value in cleaned]


def blend_hybrid_score(
    *,
    dense_score: float,
    sparse_score: float,
    rerank_score: float,
    dense_weight: float = 0.48,
    sparse_weight: float = 0.27,
    rerank_weight: float = 0.25,
) -> float:
    weights = [
        max(0.0, dense_weight),
        max(0.0, sparse_weight),
        max(0.0, rerank_weight),
    ]
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    value = (
        max(0.0, dense_score) * weights[0]
        + max(0.0, sparse_score) * weights[1]
        + max(0.0, rerank_score) * weights[2]
    ) / total_weight
    return max(0.0, min(1.0, value))


def _contains_cjk(token: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in token)


def _append_cjk_ngrams(tokens: list[str], *, token: str, max_tokens: int) -> None:
    normalized = str(token or "").strip()
    if len(normalized) < 4:
        return
    if len(normalized) > 24:
        normalized = normalized[:24]
    for size in (2, 3, 4):
        if len(normalized) < size:
            continue
        for index in range(len(normalized) - size + 1):
            tokens.append(normalized[index : index + size])
            if len(tokens) >= max_tokens:
                return
