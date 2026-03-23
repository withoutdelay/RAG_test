from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkPayload:
    chunk_index: int
    chunk_type: str
    content: str
    token_count: int
    heading_path: str | None
    metadata: dict


class Chunker:
    def __init__(self, max_chars: int = 1200) -> None:
        self.max_chars = max_chars

    def split(self, markdown: str, base_metadata: dict | None = None) -> list[ChunkPayload]:
        base_metadata = base_metadata or {}
        blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
        chunks: list[ChunkPayload] = []
        heading_path: str | None = None
        buffer = ""

        for block in blocks:
            if block.startswith("#"):
                if buffer:
                    chunks.append(
                        self._build_chunk(
                            chunk_index=len(chunks),
                            content=buffer,
                            heading_path=heading_path,
                            base_metadata=base_metadata,
                        )
                    )
                    buffer = ""
                heading_path = block.lstrip("#").strip()

            if self._is_table_block(block):
                if buffer:
                    chunks.append(
                        self._build_chunk(
                            chunk_index=len(chunks),
                            content=buffer,
                            heading_path=heading_path,
                            base_metadata=base_metadata,
                        )
                    )
                    buffer = ""
                chunks.append(
                    self._build_chunk(
                        chunk_index=len(chunks),
                        content=block,
                        heading_path=heading_path,
                        base_metadata=base_metadata,
                    )
                )
                continue

            candidate = f"{buffer}\n\n{block}".strip() if buffer else block
            if len(candidate) <= self.max_chars:
                buffer = candidate
                continue

            if buffer:
                chunks.append(
                    self._build_chunk(
                        chunk_index=len(chunks),
                        content=buffer,
                        heading_path=heading_path,
                        base_metadata=base_metadata,
                    )
                )
            buffer = block

        if buffer:
            chunks.append(
                self._build_chunk(
                    chunk_index=len(chunks),
                    content=buffer,
                    heading_path=heading_path,
                    base_metadata=base_metadata,
                )
            )

        return chunks

    def _build_chunk(
        self,
        chunk_index: int,
        content: str,
        heading_path: str | None,
        base_metadata: dict,
    ) -> ChunkPayload:
        chunk_type = "TABLE" if "|" in content and "\n|" in content else "PLAIN"
        return ChunkPayload(
            chunk_index=chunk_index,
            chunk_type=chunk_type,
            content=content,
            token_count=max(1, len(content) // 4),
            heading_path=heading_path,
            metadata=dict(base_metadata),
        )

    def _is_table_block(self, block: str) -> bool:
        return "|" in block and "\n|" in block
