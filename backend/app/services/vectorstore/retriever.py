from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse, RetrievalSearchTrace
from app.services.retrieval.hybrid import blend_hybrid_score, bm25_sparse_score, build_document_frequencies, build_term_counts, normalize_score_list, tokenize_hybrid_text
from app.services.retrieval.reranker import Reranker, build_default_reranker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.qdrant_client import QdrantService

MAX_SPARSE_QUERY_TERMS = 8


class Retriever:
    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        qdrant: QdrantService | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.embedder = embedder or Embedder()
        self.qdrant = qdrant or QdrantService()
        self.reranker = reranker or build_default_reranker()

    async def search(
        self,
        *,
        session: AsyncSession,
        request: RetrievalSearchRequest,
    ) -> RetrievalSearchResponse:
        query_vector = await self.embedder.embed_text(request.query)
        filters = request.filters.model_dump(exclude_none=True) if request.filters else {}
        if request.project_id:
            filters["project_id"] = str(request.project_id)
        search_limit = max(request.top_k * 8, request.top_k)
        sparse_search_limit = max(request.top_k * 12, request.top_k + 12)
        hits = self.qdrant.search(query_vector=query_vector, top_k=search_limit, filters=filters)
        sparse_query_terms = _extract_sparse_query_terms(request.query)

        candidate_ids: list[UUID] = []
        dense_score_by_chunk_id: dict[str, float] = {}
        payload_by_chunk_id: dict[str, dict[str, Any]] = {}
        for hit in hits:
            raw_chunk_id = hit.payload.get("chunk_id")
            if not raw_chunk_id:
                continue
            chunk_id_text = str(raw_chunk_id)
            try:
                chunk_id = UUID(chunk_id_text)
            except (TypeError, ValueError):
                continue
            current_score = max(0.0, float(hit.score or 0.0))
            if chunk_id_text in dense_score_by_chunk_id and dense_score_by_chunk_id[chunk_id_text] >= current_score:
                continue
            if chunk_id_text not in payload_by_chunk_id:
                candidate_ids.append(chunk_id)
            dense_score_by_chunk_id[chunk_id_text] = current_score
            payload_by_chunk_id[chunk_id_text] = dict(hit.payload or {})

        candidates: list[dict[str, Any]] = []
        dense_candidate_count = 0
        if candidate_ids:
            stmt = (
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id.in_(candidate_ids))
            )
            rows = (await session.execute(stmt)).all()
            row_map = {str(chunk.id): (chunk, document) for chunk, document in rows}

            for candidate_id in candidate_ids:
                chunk_id_text = str(candidate_id)
                row = row_map.get(chunk_id_text)
                if not row:
                    continue
                chunk, document = row
                if request.project_id and document.project_id != request.project_id:
                    continue
                retrieval_text = _build_chunk_retrieval_text(
                    chunk=chunk,
                    document_name=document.filename,
                    payload=payload_by_chunk_id.get(chunk_id_text) or {},
                )
                candidates.append(
                    {
                        "chunk": chunk,
                        "document": document,
                        "dense_score": dense_score_by_chunk_id.get(chunk_id_text, 0.0),
                        "retrieval_text": retrieval_text,
                        "dense_hit": True,
                        "sparse_hit": False,
                    }
                )
            dense_candidate_count = len(candidates)

        sparse_candidates = (
            await _collect_sparse_candidates(
                session=session,
                query_text=request.query,
                query_terms=sparse_query_terms,
                filters=filters,
                search_limit=sparse_search_limit,
            )
            if request.search_mode != "vector" and sparse_query_terms
            else []
        )
        sparse_candidate_count = len(sparse_candidates)
        if sparse_candidates:
            candidate_map = {str(candidate["chunk"].id): candidate for candidate in candidates}
            for sparse_candidate in sparse_candidates:
                chunk_id_text = str(sparse_candidate["chunk"].id)
                existing = candidate_map.get(chunk_id_text)
                if existing is None:
                    candidate_map[chunk_id_text] = sparse_candidate
                    candidates.append(sparse_candidate)
                    continue
                existing["sparse_hit"] = True
                existing["retrieval_text"] = str(existing.get("retrieval_text") or sparse_candidate.get("retrieval_text") or "")

        if not candidates:
            return RetrievalSearchResponse(
                results=[],
                total=0,
                search_trace=RetrievalSearchTrace(
                    search_mode=request.search_mode,
                    dense_search_limit=search_limit,
                    dense_hit_count=len(hits),
                    sparse_search_limit=sparse_search_limit if request.search_mode != "vector" else 0,
                    sparse_hit_count=len(sparse_candidates),
                    dense_candidate_count=dense_candidate_count,
                    sparse_candidate_count=sparse_candidate_count,
                    candidate_count=0,
                    ranked_count=0,
                    returned_count=0,
                    reranker_enabled=bool(self.reranker is not None and self.reranker.available),
                    reranker_backend=_resolve_reranker_backend(self.reranker),
                ),
            )

        ranked_candidates = _rank_chunk_candidates(
            query_text=request.query,
            search_mode=request.search_mode,
            candidates=candidates,
            reranker=self.reranker,
        )

        results: list[RetrievalResult] = []
        for candidate in ranked_candidates[: request.top_k]:
            chunk = candidate["chunk"]
            document = candidate["document"]
            metadata = {
                **dict(chunk.meta or {}),
                "hybrid_score_breakdown": candidate["score_breakdown"],
                "retrieval_reason": candidate["reason"],
                "retrieval_reason_trace": candidate["reason_trace"],
                "candidate_sources": _candidate_sources(candidate),
                "retrieval_text_preview": str(candidate.get("retrieval_text") or "")[:320],
            }
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_name=document.filename,
                    heading_path=chunk.heading_path,
                    chunk_type=chunk.chunk_type,
                    content=chunk.content,
                    score=round(float(candidate.get("hybrid_score") or 0.0), 4),
                    reason=str(candidate.get("reason") or ""),
                    reason_trace=[str(item) for item in (candidate.get("reason_trace") or []) if str(item).strip()],
                    score_breakdown=dict(candidate.get("score_breakdown") or {}),
                    metadata=metadata,
                )
            )

        return RetrievalSearchResponse(
            results=results,
            total=len(ranked_candidates),
            search_trace=RetrievalSearchTrace(
                search_mode=request.search_mode,
                dense_search_limit=search_limit,
                dense_hit_count=len(hits),
                sparse_search_limit=sparse_search_limit if request.search_mode != "vector" else 0,
                sparse_hit_count=len(sparse_candidates),
                dense_candidate_count=dense_candidate_count,
                sparse_candidate_count=sparse_candidate_count,
                candidate_count=len(candidates),
                ranked_count=len(ranked_candidates),
                returned_count=len(results),
                reranker_enabled=bool(self.reranker is not None and self.reranker.available),
                reranker_backend=_resolve_reranker_backend(self.reranker),
            ),
        )


def _build_chunk_retrieval_text(
    *,
    chunk: Chunk,
    document_name: str,
    payload: dict[str, Any],
) -> str:
    metadata = dict(chunk.meta or {})
    semantic_retrieval_text = str(
        metadata.get("semantic_retrieval_text")
        or payload.get("semantic_retrieval_text")
        or ""
    ).strip()
    if semantic_retrieval_text:
        return semantic_retrieval_text
    parts = [
        str(document_name or "").strip(),
        str(metadata.get("section_path") or payload.get("section_path") or chunk.heading_path or "").strip(),
        str(metadata.get("source_heading") or payload.get("source_heading") or "").strip(),
        str(metadata.get("contextual_text") or payload.get("contextual_text") or "").strip(),
        str(metadata.get("contextualized_block_text") or payload.get("contextualized_block_text") or "").strip(),
        str(chunk.content or "").strip(),
    ]
    return "\n".join(part for part in parts if part).strip()


def _rank_chunk_candidates(
    *,
    query_text: str,
    search_mode: str,
    candidates: list[dict[str, Any]],
    reranker: Reranker | None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    dense_scores = [max(0.0, float(candidate.get("dense_score") or 0.0)) for candidate in candidates]
    dense_norm = normalize_score_list(dense_scores)
    query_terms = tokenize_hybrid_text(query_text, max_tokens=192)
    candidate_term_counts: list[Counter[str]] = [
        build_term_counts(str(candidate.get("retrieval_text") or ""), max_tokens=512)
        for candidate in candidates
    ]
    document_frequencies = build_document_frequencies(
        query_terms=query_terms,
        candidate_term_counts=candidate_term_counts,
    )
    avg_doc_length = (
        sum(sum(counts.values()) for counts in candidate_term_counts) / max(len(candidate_term_counts), 1)
        if candidate_term_counts
        else 1.0
    )
    sparse_scores = [
        bm25_sparse_score(
            query_text=query_text,
            query_terms=query_terms,
            candidate_text=str(candidate.get("retrieval_text") or ""),
            candidate_term_counts=counts,
            document_frequencies=document_frequencies,
            corpus_size=len(candidate_term_counts),
            avg_doc_length=avg_doc_length,
        )
        for candidate, counts in zip(candidates, candidate_term_counts)
    ]
    sparse_norm = normalize_score_list(sparse_scores)

    reranker_enabled = reranker is not None and reranker.available
    rerank_scores = (
        reranker.score_many(
            query=query_text,
            texts=[str(candidate.get("retrieval_text") or "") for candidate in candidates],
        )
        if reranker_enabled
        else [0.0 for _ in candidates]
    )
    rerank_norm = normalize_score_list([max(0.0, float(score or 0.0)) for score in rerank_scores])

    ranked_candidates: list[dict[str, Any]] = []
    for candidate, dense_raw, dense_score, sparse_raw, sparse_score, rerank_raw, rerank_score in zip(
        candidates,
        dense_scores,
        dense_norm,
        sparse_scores,
        sparse_norm,
        rerank_scores,
        rerank_norm,
    ):
        dense_hit = bool(candidate.get("dense_hit")) or (search_mode != "keyword" and max(float(dense_raw or 0.0), float(dense_score or 0.0)) > 0)
        sparse_hit = bool(candidate.get("sparse_hit")) or (search_mode != "vector" and max(float(sparse_raw or 0.0), float(sparse_score or 0.0)) > 0)
        candidate["dense_hit"] = dense_hit
        candidate["sparse_hit"] = sparse_hit
        if search_mode == "vector":
            hybrid_shortlist = max(0.0, min(1.0, dense_score if dense_norm else dense_raw))
            hybrid_score = hybrid_shortlist
        elif search_mode == "keyword":
            hybrid_shortlist = blend_hybrid_score(
                dense_score=0.0,
                sparse_score=sparse_score,
                rerank_score=0.0,
                dense_weight=0.0,
                sparse_weight=1.0,
                rerank_weight=0.0,
            )
            hybrid_score = blend_hybrid_score(
                dense_score=0.0,
                sparse_score=sparse_score,
                rerank_score=rerank_score,
                dense_weight=0.0,
                sparse_weight=0.72,
                rerank_weight=0.28,
            )
        else:
            hybrid_shortlist = blend_hybrid_score(
                dense_score=dense_score,
                sparse_score=sparse_score,
                rerank_score=0.0,
                rerank_weight=0.0,
            )
            hybrid_score = blend_hybrid_score(
                dense_score=dense_score,
                sparse_score=sparse_score,
                rerank_score=rerank_score,
            )
        hybrid_rerank = max(0.0, hybrid_score - hybrid_shortlist)
        reason_trace = _build_reason_trace(
            search_mode=search_mode,
            semantic_score=dense_score,
            sparse_score=sparse_score,
            hybrid_shortlist=hybrid_shortlist,
            rerank_score=rerank_score,
            final_score=hybrid_score,
            reranker_enabled=reranker_enabled,
            dense_hit=dense_hit,
            sparse_hit=sparse_hit,
        )
        candidate["hybrid_score"] = hybrid_score
        candidate["score_breakdown"] = {
            "base": round(float(dense_score), 4),
            "semantic_raw": round(float(dense_raw), 4),
            "semantic": round(float(dense_score), 4),
            "dense_raw": round(float(dense_raw), 4),
            "dense": round(float(dense_score), 4),
            "sparse_raw": round(float(sparse_raw), 4),
            "sparse": round(float(sparse_score), 4),
            "hybrid_rrf": round(float(hybrid_shortlist), 4),
            "rerank_raw": round(float(rerank_raw), 4),
            "rerank": round(float(rerank_score), 4),
            "hybrid_rerank": round(float(hybrid_rerank), 4),
            "hybrid": round(float(hybrid_score), 4),
            "final": round(float(hybrid_score), 4),
        }
        candidate["reason_trace"] = reason_trace
        candidate["reason"] = "; ".join(reason_trace)
        ranked_candidates.append(candidate)

    return sorted(
        ranked_candidates,
        key=lambda item: (
            float(item.get("hybrid_score") or 0.0),
            float((item.get("score_breakdown") or {}).get("final") or 0.0),
            float((item.get("score_breakdown") or {}).get("sparse") or 0.0),
            float((item.get("score_breakdown") or {}).get("semantic") or 0.0),
            float(item.get("dense_score") or 0.0),
        ),
        reverse=True,
    )


def _build_reason_trace(
    *,
    search_mode: str,
    semantic_score: float,
    sparse_score: float,
    hybrid_shortlist: float,
    rerank_score: float,
    final_score: float,
    reranker_enabled: bool,
    dense_hit: bool,
    sparse_hit: bool,
) -> list[str]:
    trace: list[str] = []
    trace.append(f"candidate_sources={','.join(_build_candidate_sources(dense_hit=dense_hit, sparse_hit=sparse_hit))}")
    if search_mode in {"vector", "hybrid"}:
        trace.append(f"semantic_match={semantic_score:.3f}")
    if search_mode in {"keyword", "hybrid"}:
        trace.append(f"sparse_match={sparse_score:.3f}")
    trace.append(f"hybrid_shortlist={hybrid_shortlist:.3f}")
    if reranker_enabled:
        trace.append(f"rerank_score={rerank_score:.3f}")
    trace.append(f"final_score={final_score:.3f}")
    return trace


def _resolve_reranker_backend(reranker: Reranker | None) -> str:
    if reranker is None:
        return "none"
    name = type(reranker).__name__.replace("Reranker", "")
    normalized = name.strip().lower()
    return normalized or "unknown"


def _extract_sparse_query_terms(query_text: str) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokenize_hybrid_text(query_text, max_tokens=48):
        normalized = str(token or "").strip().casefold()
        if not normalized or normalized in seen:
            continue
        if len(normalized) < 2:
            continue
        if normalized.isdigit():
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= MAX_SPARSE_QUERY_TERMS:
            break
    return deduped


async def _collect_sparse_candidates(
    *,
    session: AsyncSession,
    query_text: str,
    query_terms: list[str],
    filters: dict[str, Any],
    search_limit: int,
) -> list[dict[str, Any]]:
    if not query_terms:
        return []
    stmt = select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
    stmt = _apply_document_filters(stmt=stmt, filters=filters)
    like_clauses = []
    for term in query_terms:
        pattern = f"%{term}%"
        like_clauses.extend(
            [
                Chunk.content.ilike(pattern),
                Chunk.heading_path.ilike(pattern),
                Document.filename.ilike(pattern),
            ]
        )
    if not like_clauses:
        return []
    rows = (await session.execute(stmt.where(or_(*like_clauses)).limit(search_limit))).all()
    if not rows:
        return []

    candidates: list[dict[str, Any]] = []
    candidate_term_counts: list[Counter[str]] = []
    for chunk, document in rows:
        retrieval_text = _build_chunk_retrieval_text(
            chunk=chunk,
            document_name=document.filename,
            payload={},
        )
        candidates.append(
            {
                "chunk": chunk,
                "document": document,
                "dense_score": 0.0,
                "retrieval_text": retrieval_text,
                "dense_hit": False,
                "sparse_hit": True,
            }
        )
        candidate_term_counts.append(build_term_counts(retrieval_text, max_tokens=512))
    if not candidates:
        return []

    document_frequencies = build_document_frequencies(
        query_terms=query_terms,
        candidate_term_counts=candidate_term_counts,
    )
    avg_doc_length = (
        sum(sum(counts.values()) for counts in candidate_term_counts) / max(len(candidate_term_counts), 1)
        if candidate_term_counts
        else 1.0
    )
    scored_candidates: list[tuple[float, dict[str, Any]]] = []
    for candidate, counts in zip(candidates, candidate_term_counts):
        sparse_score = bm25_sparse_score(
            query_text=query_text,
            query_terms=query_terms,
            candidate_text=str(candidate.get("retrieval_text") or ""),
            candidate_term_counts=counts,
            document_frequencies=document_frequencies,
            corpus_size=len(candidate_term_counts),
            avg_doc_length=avg_doc_length,
        )
        if sparse_score <= 0:
            continue
        scored_candidates.append((sparse_score, candidate))
    scored_candidates.sort(
        key=lambda item: (
            float(item[0] or 0.0),
            len(str(item[1]["chunk"].heading_path or "")),
        ),
        reverse=True,
    )
    return [candidate for _, candidate in scored_candidates[:search_limit]]


def _apply_document_filters(*, stmt: Any, filters: dict[str, Any]) -> Any:
    if project_id := filters.get("project_id"):
        stmt = stmt.where(Document.project_id == UUID(str(project_id)))
    if industry := filters.get("industry"):
        stmt = stmt.where(cast(Chunk.meta["industry"].astext, String) == str(industry))
    if year_gte := filters.get("year_gte"):
        stmt = stmt.where(cast(Chunk.meta["year"].astext, Integer) >= int(year_gte))
    if chunk_types := filters.get("chunk_type"):
        stmt = stmt.where(Chunk.chunk_type.in_(list(chunk_types)))
    if doc_type := filters.get("doc_type"):
        stmt = stmt.where(Document.doc_type == str(doc_type))
    if document_names := filters.get("document_names"):
        stmt = stmt.where(Document.filename.in_(list(document_names)))
    return stmt


def _build_candidate_sources(*, dense_hit: bool, sparse_hit: bool) -> list[str]:
    sources: list[str] = []
    if dense_hit:
        sources.append("dense")
    if sparse_hit:
        sources.append("sparse")
    return sources or ["none"]


def _candidate_sources(candidate: dict[str, Any]) -> list[str]:
    return _build_candidate_sources(
        dense_hit=bool(candidate.get("dense_hit")),
        sparse_hit=bool(candidate.get("sparse_hit")),
    )
