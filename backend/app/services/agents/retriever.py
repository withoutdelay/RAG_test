from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk


class RetrieverAgent:
    async def retrieve_context(
        self,
        *,
        session: AsyncSession,
        document_id,
        keywords: list[str],
        max_chunks: int = 4,
        scan_limit: int = 24,
    ) -> tuple[str, list[dict]]:
        if document_id is None:
            return "", []

        result = await session.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index.asc())
            .limit(scan_limit)
        )
        chunks = result.all()
        if not chunks:
            return "", []

        normalized_keywords = [keyword.casefold() for keyword in keywords if keyword]
        scored_chunks: list[tuple[int, int, Chunk]] = []
        for chunk in chunks:
            haystack = f"{chunk.heading_path or ''}\n{chunk.content}".casefold()
            score = sum(haystack.count(keyword) for keyword in normalized_keywords)
            scored_chunks.append((score, -chunk.chunk_index, chunk))

        scored_chunks.sort(reverse=True)
        selected = [chunk for score, _, chunk in scored_chunks if score > 0][:max_chunks]
        if not selected:
            selected = chunks[:max_chunks]

        references = [
            {
                "doc_name": chunk.meta.get("document_name") or "",
                "heading": chunk.heading_path or "",
                "chunk_id": str(chunk.id),
            }
            for chunk in selected
        ]
        context = "\n\n".join(chunk.content for chunk in selected)
        return context, references
