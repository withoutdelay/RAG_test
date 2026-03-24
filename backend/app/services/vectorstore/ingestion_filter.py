from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.parsing.formula_candidates import has_garbled_formula_text, is_formula_like_text
from app.services.vectorstore.chunker import ChunkPayload


FIGURE_KEYWORD_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)

PAGE_FURNITURE_PATTERN = re.compile(r"(版本|页码|总页数|DAYU ELECTRIC|项目名称|买方|卖方)")


@dataclass(frozen=True)
class ChunkIngestionDecision:
    indexable: bool
    reasons: tuple[str, ...]
    review_required: bool = False


class SafeIngestionFilter:
    def __init__(
        self,
        *,
        max_table_chars: int = 2200,
        max_table_rows: int = 28,
        max_heading_only_chars: int = 60,
    ) -> None:
        self.max_table_chars = max_table_chars
        self.max_table_rows = max_table_rows
        self.max_heading_only_chars = max_heading_only_chars

    def decide(self, payload: ChunkPayload) -> ChunkIngestionDecision:
        content = payload.content.strip()
        normalized = " ".join(content.split())
        reasons: list[str] = []

        if self._is_front_matter(payload, normalized):
            reasons.append("front_matter_noise")
        if self._is_heading_only(payload, normalized):
            reasons.append("heading_only")
        if self._is_page_furniture(normalized):
            reasons.append("page_furniture")
        if self._is_oversized_table(payload):
            reasons.append("oversized_table")
        if has_garbled_formula_text(normalized):
            reasons.append("garbled_formula_text")
        if self._is_figure_reference_chunk(payload, normalized):
            reasons.append("figure_reference_only")

        review_required = bool(
            reasons
            or FIGURE_KEYWORD_PATTERN.search(normalized)
            or is_formula_like_text(normalized)
        )
        return ChunkIngestionDecision(indexable=not reasons, reasons=tuple(reasons), review_required=review_required)

    def _is_front_matter(self, payload: ChunkPayload, normalized: str) -> bool:
        if payload.chunk_index > 6:
            return False
        return bool(PAGE_FURNITURE_PATTERN.search(normalized))

    def _is_heading_only(self, payload: ChunkPayload, normalized: str) -> bool:
        if payload.chunk_type != "PLAIN" or not normalized.startswith("#"):
            return False
        lines = [line.strip() for line in payload.content.splitlines() if line.strip()]
        if not lines:
            return False
        if any(not line.startswith("#") for line in lines):
            return False
        return len(normalized) <= self.max_heading_only_chars

    def _is_page_furniture(self, normalized: str) -> bool:
        if not normalized:
            return False
        return bool(PAGE_FURNITURE_PATTERN.search(normalized) and len(normalized) <= 120)

    def _is_oversized_table(self, payload: ChunkPayload) -> bool:
        if payload.chunk_type != "TABLE":
            return False
        row_count = sum(1 for line in payload.content.splitlines() if "|" in line)
        return len(payload.content) > self.max_table_chars or row_count > self.max_table_rows

    def _is_figure_reference_chunk(self, payload: ChunkPayload, normalized: str) -> bool:
        if not FIGURE_KEYWORD_PATTERN.search(normalized):
            return False
        if payload.chunk_type == "TABLE":
            return False
        if len(normalized) <= 260:
            return True
        marker_count = sum(
            1
            for marker in ("如下", "见下图", "详见", "原理图", "波形", "示意图", "接线图")
            if marker in normalized
        )
        prose_score = sum(1 for marker in ("因此", "采用", "控制", "系统", "支持", "说明", "过程", "要求") if marker in normalized)
        return marker_count >= 2 and prose_score == 0
