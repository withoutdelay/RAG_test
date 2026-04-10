from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.parsing.formula_candidates import has_garbled_formula_text, is_formula_like_text
from app.services.vectorstore.block_taxonomy import classify_block_taxonomy


FIGURE_KEYWORD_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)
PAGE_FURNITURE_PATTERN = re.compile(r"(DAYU ELECTRIC|项目名称|买方|卖方|日期|版本|页码|总页数)")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CHAPTER_HEADING_PATTERN = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+章\s*.+$")
SECTION_HEADING_PATTERN = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+节\s*.+$")
ARTICLE_HEADING_PATTERN = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+条\s*.+$")
CHINESE_NUMERIC_HEADING_PATTERN = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*.+$")
PAREN_HEADING_PATTERN = re.compile(r"^[（(][一二三四五六七八九十0-9]+[)）]\s*.+$")
ARABIC_DOTTED_HEADING_PATTERN = re.compile(r"^(?P<ordinal>[0-9]+(?:\.[0-9]+){0,3})(?:[、.．])?\s*.+$")
ARABIC_SIMPLE_HEADING_PATTERN = re.compile(r"^[0-9]{1,2}(?:[、.．]|\s+).+$")
ARABIC_COMPACT_CJK_HEADING_PATTERN = re.compile(r"^[0-9]{1,2}[\u4e00-\u9fff].+$")


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
        max_chars: int = 2400,
        *,
        table_preserve_threshold_chars: int = 2200,
        table_preserve_threshold_rows: int = 28,
        max_table_rows_per_chunk: int = 10,
        split_tables: bool = False,
        max_heading_depth_to_split: int = 1,
        max_markdown_heading_level_to_split: int = 2,
    ) -> None:
        self.max_chars = max_chars
        self.table_preserve_threshold_chars = table_preserve_threshold_chars
        self.table_preserve_threshold_rows = table_preserve_threshold_rows
        self.max_table_rows_per_chunk = max_table_rows_per_chunk
        self.split_tables = split_tables
        self.max_heading_depth_to_split = max_heading_depth_to_split
        self.max_markdown_heading_level_to_split = max_markdown_heading_level_to_split

    def split(self, markdown: str, base_metadata: dict | None = None) -> list[ChunkPayload]:
        base_metadata = base_metadata or {}
        blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
        chunks: list[ChunkPayload] = []
        heading_path: str | None = None
        buffer = ""

        for block in blocks:
            heading_info = self._parse_heading_block(block)
            if heading_info is not None:
                markdown_level, heading_text = heading_info
                if self._should_start_new_chunk_on_heading(
                    markdown_level=markdown_level,
                    heading_text=heading_text,
                    current_heading_path=heading_path,
                ):
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
                    heading_path = heading_text
                elif not heading_path:
                    heading_path = heading_text

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
        taxonomy = classify_block_taxonomy(
            content=content,
            heading_path=heading_path,
            chunk_type=chunk_type,
            front_matter=front_matter,
            needs_asset_lookup=needs_asset_lookup,
        )
        chunk_metadata = {
            **dict(base_metadata),
            "content_risk_level": content_risk_level,
            "front_matter": front_matter,
            "needs_asset_lookup": needs_asset_lookup,
            **taxonomy,
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

    def _parse_heading_block(self, block: str) -> tuple[int, str] | None:
        first_line = block.splitlines()[0].strip() if block.strip() else ""
        match = MARKDOWN_HEADING_PATTERN.match(first_line)
        if not match:
            return None
        return len(match.group(1)), match.group(2).strip()

    def _should_start_new_chunk_on_heading(
        self,
        *,
        markdown_level: int,
        heading_text: str,
        current_heading_path: str | None,
    ) -> bool:
        if not current_heading_path:
            return True
        heading_depth = self._heading_structural_depth(heading_text)
        if heading_depth is not None:
            return heading_depth <= self.max_heading_depth_to_split
        return markdown_level <= self.max_markdown_heading_level_to_split

    def _heading_structural_depth(self, heading_text: str) -> int | None:
        stripped = str(heading_text or "").strip()
        if not stripped:
            return None
        if CHAPTER_HEADING_PATTERN.match(stripped):
            return 1
        if SECTION_HEADING_PATTERN.match(stripped) or ARTICLE_HEADING_PATTERN.match(stripped):
            return 2
        if CHINESE_NUMERIC_HEADING_PATTERN.match(stripped):
            return 1
        if PAREN_HEADING_PATTERN.match(stripped):
            return 2
        dotted_match = ARABIC_DOTTED_HEADING_PATTERN.match(stripped)
        if dotted_match:
            parts = [part for part in dotted_match.group("ordinal").split(".") if part]
            return len(parts)
        if ARABIC_SIMPLE_HEADING_PATTERN.match(stripped) or ARABIC_COMPACT_CJK_HEADING_PATTERN.match(stripped):
            return 1
        return None

    def _split_table_block(self, block: str) -> list[str]:
        if not self.split_tables:
            return [block]
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
