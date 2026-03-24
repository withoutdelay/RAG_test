from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.parsing.formula_candidates import has_garbled_formula_text, is_formula_like_text


FIGURE_KEYWORD_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)
PAGE_FURNITURE_PATTERN = re.compile(r"(DAYU ELECTRIC|项目名称|买方|卖方|日期|版本|页码|总页数)")


@dataclass
class ChunkPayload:
    chunk_index: int
    chunk_type: str
    content: str
    token_count: int
    heading_path: str | None
    metadata: dict


class Chunker:
    def __init__(
        self,
        max_chars: int = 1200,
        *,
        table_preserve_threshold_chars: int = 2200,
        table_preserve_threshold_rows: int = 28,
        max_table_rows_per_chunk: int = 10,
    ) -> None:
        self.max_chars = max_chars
        self.table_preserve_threshold_chars = table_preserve_threshold_chars
        self.table_preserve_threshold_rows = table_preserve_threshold_rows
        self.max_table_rows_per_chunk = max_table_rows_per_chunk

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
                for table_block in self._split_table_block(block):
                    chunks.append(
                        self._build_chunk(
                            chunk_index=len(chunks),
                            content=table_block,
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
        normalized = " ".join(content.split())
        front_matter = bool(chunk_index <= 6 and PAGE_FURNITURE_PATTERN.search(normalized))
        needs_asset_lookup = bool(FIGURE_KEYWORD_PATTERN.search(normalized))
        if has_garbled_formula_text(normalized):
            content_risk_level = "high"
        elif chunk_type == "TABLE" or front_matter or needs_asset_lookup or is_formula_like_text(normalized):
            content_risk_level = "medium"
        else:
            content_risk_level = "low"
        chunk_metadata = {
            **dict(base_metadata),
            "content_risk_level": content_risk_level,
            "front_matter": front_matter,
            "needs_asset_lookup": needs_asset_lookup,
        }
        return ChunkPayload(
            chunk_index=chunk_index,
            chunk_type=chunk_type,
            content=content,
            token_count=max(1, len(content) // 4),
            heading_path=heading_path,
            metadata=chunk_metadata,
        )

    def _is_table_block(self, block: str) -> bool:
        return "|" in block and "\n|" in block

    def _split_table_block(self, block: str) -> list[str]:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        row_count = sum(1 for line in lines if "|" in line)
        if (
            len(block) > self.table_preserve_threshold_chars
            or row_count > self.table_preserve_threshold_rows
            or len(lines) < 4
        ):
            return [block]

        header_lines = lines[:2]
        if len(header_lines) < 2 or "---" not in header_lines[1]:
            return [block]

        data_lines = lines[2:]
        if len(block) <= self.max_chars and len(data_lines) <= self.max_table_rows_per_chunk:
            return [block]

        chunks: list[str] = []
        current_rows: list[str] = []
        for row in data_lines:
            candidate_rows = [*current_rows, row]
            candidate_block = "\n".join([*header_lines, *candidate_rows])
            if current_rows and (
                len(candidate_block) > self.max_chars
                or len(candidate_rows) > self.max_table_rows_per_chunk
            ):
                chunks.append("\n".join([*header_lines, *current_rows]))
                current_rows = [row]
            else:
                current_rows = candidate_rows

        if current_rows:
            chunks.append("\n".join([*header_lines, *current_rows]))

        return chunks or [block]
