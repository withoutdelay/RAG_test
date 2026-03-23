from __future__ import annotations

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

    def record_usage(self, *, total_tokens: int, cost_estimate: Decimal | float | int) -> None:
        self.token_usage += int(total_tokens)
        self.estimated_cost += Decimal(str(cost_estimate)).quantize(Decimal("0.0001"))

    def set_section_content(self, section_index: int, content: str) -> None:
        self.completed_sections[section_index] = content

    def to_sections_markdown(self, outline_title: str | None = None) -> str:
        ordered_sections = [self.completed_sections[index] for index in sorted(self.completed_sections)]
        body = "\n\n".join(section.strip() for section in ordered_sections if section.strip())
        if not outline_title:
            return body
        return f"# {outline_title}\n\n{body}".strip()
