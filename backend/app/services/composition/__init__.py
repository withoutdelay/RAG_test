from __future__ import annotations

from typing import Any

__all__ = ["OutlineService", "SectionDraftService"]


def __getattr__(name: str) -> Any:
    if name == "OutlineService":
        from app.services.composition.outline_service import OutlineService

        return OutlineService
    if name == "SectionDraftService":
        from app.services.composition.section_service import SectionDraftService

        return SectionDraftService
    raise AttributeError(name)
