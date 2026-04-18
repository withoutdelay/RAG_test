from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class WorkflowStatus(str, Enum):
    CREATED = "created"
    OUTLINING = "outlining"
    OUTLINE_REVIEW = "outline_review"
    GENERATING = "generating"
    SECTION_REVIEW = "section_review"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


# Maximum length (in characters) for the rolling summary of preceding sections.
_PRECEDING_SUMMARY_MAX_CHARS = 600
# Maximum length for a single section summary.
_SECTION_SUMMARY_MAX_CHARS = 200


@dataclass(slots=True)
class WorkflowState:
    task_id: str
    project_name: str
    global_params: dict
    outline: dict | None
    completed_sections: dict[int, str] = field(default_factory=dict)
    referenced_images: list[dict] = field(default_factory=list)
    referenced_sources: list[dict] = field(default_factory=list)
    current_agent: str = "planner"
    current_section_idx: int = -1
    token_usage: int = 0
    estimated_cost: Decimal = field(default_factory=lambda: Decimal("0.0000"))
    rfp_context: str = ""

    # ── Inter-section context (Change 3) ──────────────────────────
    # Maps section_index → short summary of that section's content.
    section_summaries: dict[int, str] = field(default_factory=dict)
    # Accumulated term glossary extracted from generated sections.
    # Maps a canonical term → set of aliases to avoid.
    term_glossary: dict[str, str] = field(default_factory=dict)

    def record_usage(self, *, total_tokens: int, cost_estimate: Decimal | float | int) -> None:
        self.token_usage += int(total_tokens)
        self.estimated_cost += Decimal(str(cost_estimate)).quantize(Decimal("0.0001"))

    def set_section_content(self, section_index: int, content: str) -> None:
        self.completed_sections[section_index] = content

    def record_section_summary(self, section_index: int, title: str, content: str) -> None:
        """Extract and store a short summary for the completed section."""
        summary = _extract_section_summary(title=title, content=content)
        self.section_summaries[section_index] = summary
        # Extract potential terms from the section for glossary building.
        new_terms = _extract_terms(content)
        for canonical, alias in new_terms.items():
            if canonical not in self.term_glossary:
                self.term_glossary[canonical] = alias

    def build_preceding_context(self, current_index: int) -> str:
        """Build a rolling summary of all preceding sections for context passing.

        Returns a compact text block that Executor can use to avoid repetition
        and maintain terminology consistency.
        """
        if not self.section_summaries:
            return ""
        parts: list[str] = []
        for idx in sorted(self.section_summaries):
            if idx >= current_index:
                break
            parts.append(self.section_summaries[idx])

        if not parts:
            return ""

        summaries_text = "\n".join(parts)
        # Truncate if too long to avoid eating into the real prompt budget.
        if len(summaries_text) > _PRECEDING_SUMMARY_MAX_CHARS:
            summaries_text = summaries_text[:_PRECEDING_SUMMARY_MAX_CHARS] + "…"

        glossary_text = ""
        if self.term_glossary:
            glossary_entries = [
                canonical + "（不要写成“" + alias + "”）"
                for canonical, alias in list(self.term_glossary.items())[:10]
            ]
            glossary_text = f"\n全文统一术语：{'、'.join(glossary_entries)}"

        return (
            f"前序章节已覆盖内容（请勿重复）：\n{summaries_text}"
            f"{glossary_text}"
        )

    def to_sections_markdown(self, outline_title: str | None = None) -> str:
        ordered_sections = [self.completed_sections[index] for index in sorted(self.completed_sections)]
        body = "\n\n".join(section.strip() for section in ordered_sections if section.strip())
        if not outline_title:
            return body
        return f"# {outline_title}\n\n{body}".strip()


def _extract_section_summary(*, title: str, content: str) -> str:
    """Extract a 1-2 sentence summary from a section's content."""
    # Take the first meaningful paragraph (skip headings and empty lines).
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return f"- {title}：（内容待补充）"

    # Use first substantive paragraph up to the limit.
    first_paragraph = lines[0]
    if len(first_paragraph) > _SECTION_SUMMARY_MAX_CHARS:
        first_paragraph = first_paragraph[:_SECTION_SUMMARY_MAX_CHARS] + "…"
    return f"- {title}：{first_paragraph}"


# Common technical term pairs in the electrical/industrial domain.
_TERM_PAIRS = [
    ("变频器", "VFD"),
    ("可编程逻辑控制器", "PLC"),
    ("集散控制系统", "DCS"),
    ("人机界面", "HMI"),
    ("不间断电源", "UPS"),
    ("软启动器", "软启"),
    ("断路器", "空开"),
]


def _extract_terms(content: str) -> dict[str, str]:
    """Detect which standardized terms appear in the content and build a glossary."""
    glossary: dict[str, str] = {}
    lowered = content.lower()
    for canonical, alias in _TERM_PAIRS:
        # If both forms appear, lock in the canonical form.
        if canonical in content and alias.lower() in lowered:
            glossary[canonical] = alias
        # If only the alias appears, still record for future sections.
        elif alias.lower() in lowered and canonical not in content:
            glossary[canonical] = alias
    return glossary
