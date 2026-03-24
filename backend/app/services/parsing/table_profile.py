from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.parsing.formula_candidates import has_garbled_formula_text


TABLE_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-{2,}:?$")
NUMERIC_VALUE_PATTERN = re.compile(r"\d+(?:\.\d+)?")
UNIT_PATTERN = re.compile(
    r"(kV|V|A|kA|mA|Hz|kW|MW|mm|cm|m|kg|Nm|rpm|ms|s|%|Ω|MΩ|℃|bar|MPa|m3/h|L/min)",
    re.IGNORECASE,
)
SPEC_HEADER_PATTERN = re.compile(r"(参数|项目|规格|型号|名称|单位|数值|说明|备注|item|spec|model|type)", re.IGNORECASE)
GARBLED_PUNCT_PATTERN = re.compile(r"[#*@$]{3,}")


@dataclass(frozen=True)
class TableProfileResult:
    profile_name: str
    confidence: float
    recommended_strategy: str
    row_count: int
    column_count: int
    numeric_ratio: float
    unit_hits: int
    garbled_ratio: float
    header_fields: list[str]
    risk_flags: list[str]

    def to_metadata(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "confidence": round(self.confidence, 4),
            "recommended_strategy": self.recommended_strategy,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "numeric_ratio": round(self.numeric_ratio, 4),
            "unit_hits": self.unit_hits,
            "garbled_ratio": round(self.garbled_ratio, 4),
            "header_fields": list(self.header_fields),
            "risk_flags": list(self.risk_flags),
            "profile_version": "v1",
        }


def build_table_profile(markdown: str) -> TableProfileResult:
    rows = _extract_rows(markdown)
    if not rows:
        return TableProfileResult(
            profile_name="sparse_lookup",
            confidence=0.45,
            recommended_strategy="reference_only",
            row_count=0,
            column_count=0,
            numeric_ratio=0.0,
            unit_hits=0,
            garbled_ratio=0.0,
            header_fields=[],
            risk_flags=["empty_or_unparsed_table"],
        )

    row_count = len(rows)
    column_count = max(len(row) for row in rows)
    cells = [cell for row in rows for cell in row if cell]
    cell_count = max(len(cells), 1)
    numeric_hits = sum(1 for cell in cells if NUMERIC_VALUE_PATTERN.search(cell))
    unit_hits = sum(1 for cell in cells if UNIT_PATTERN.search(cell))
    garbled_hits = sum(1 for cell in cells if _looks_garbled(cell))
    numeric_ratio = numeric_hits / cell_count
    garbled_ratio = garbled_hits / cell_count
    header_fields = rows[0][: min(len(rows[0]), 8)]

    risk_flags: list[str] = []
    if garbled_ratio >= 0.12:
        risk_flags.append("garbled_cells")
    if unit_hits >= 2:
        risk_flags.append("unit_dense")
    if numeric_ratio >= 0.35:
        risk_flags.append("numeric_dense")
    if any(SPEC_HEADER_PATTERN.search(cell) for cell in header_fields):
        risk_flags.append("spec_headers")

    if garbled_ratio >= 0.12:
        return TableProfileResult(
            profile_name="garbled_table",
            confidence=min(0.96, 0.58 + garbled_ratio),
            recommended_strategy="ocr_reconstruction_priority",
            row_count=row_count,
            column_count=column_count,
            numeric_ratio=numeric_ratio,
            unit_hits=unit_hits,
            garbled_ratio=garbled_ratio,
            header_fields=header_fields,
            risk_flags=risk_flags or ["garbled_cells"],
        )

    if row_count >= 10 and numeric_ratio >= 0.3:
        return TableProfileResult(
            profile_name="parameter_matrix",
            confidence=0.84,
            recommended_strategy="table_reconstruction_candidate",
            row_count=row_count,
            column_count=column_count,
            numeric_ratio=numeric_ratio,
            unit_hits=unit_hits,
            garbled_ratio=garbled_ratio,
            header_fields=header_fields,
            risk_flags=risk_flags,
        )

    if unit_hits >= 2 or any(SPEC_HEADER_PATTERN.search(cell) for cell in header_fields):
        return TableProfileResult(
            profile_name="spec_sheet",
            confidence=0.77,
            recommended_strategy="table_reconstruction_candidate",
            row_count=row_count,
            column_count=column_count,
            numeric_ratio=numeric_ratio,
            unit_hits=unit_hits,
            garbled_ratio=garbled_ratio,
            header_fields=header_fields,
            risk_flags=risk_flags,
        )

    return TableProfileResult(
        profile_name="sparse_lookup",
        confidence=0.69,
        recommended_strategy="reference_only",
        row_count=row_count,
        column_count=column_count,
        numeric_ratio=numeric_ratio,
        unit_hits=unit_hits,
        garbled_ratio=garbled_ratio,
        header_fields=header_fields,
        risk_flags=risk_flags,
    )


def _extract_rows(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in markdown.splitlines():
        if "|" not in raw_line:
            continue
        stripped = raw_line.strip()
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not any(cells):
            continue
        if all(TABLE_SEPARATOR_CELL_PATTERN.fullmatch(cell or "") for cell in cells):
            continue
        rows.append(cells)
    return rows


def _looks_garbled(cell: str) -> bool:
    compact = cell.strip()
    if not compact:
        return False
    if has_garbled_formula_text(compact):
        return True
    if GARBLED_PUNCT_PATTERN.search(compact):
        return True
    punctuation_count = sum(1 for char in compact if char in "#*@$")
    return len(compact) >= 8 and punctuation_count / len(compact) >= 0.25
