from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.qdrant_client import QdrantService


class Retriever:
    def __init__(self) -> None:
        self.embedder = Embedder()
        self.qdrant = QdrantService()

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
        hits = self.qdrant.search(query_vector=query_vector, top_k=request.top_k, filters=filters)

        results: list[RetrievalResult] = []
        for hit in hits:
            stmt = (
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id == hit.payload.get("chunk_id"))
            )
            row = (await session.execute(stmt)).first()
            if not row:
                continue
            chunk, document = row
            if request.project_id and document.project_id != request.project_id:
                continue
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_name=document.filename,
                    heading_path=chunk.heading_path,
                    chunk_type=chunk.chunk_type,
                    content=chunk.content,
                    score=hit.score,
                    metadata=chunk.meta,
                )
            )

        return RetrievalSearchResponse(results=results, total=len(results))
