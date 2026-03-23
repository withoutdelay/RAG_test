from __future__ import annotations

from dataclasses import dataclass

from app.mapping_store import get_mapping_store
from app.presidio_config import PLACEHOLDER_PATTERN


@dataclass(frozen=True, slots=True)
class RestoreResult:
    restored_text: str
    restored_count: int


class Restorer:
    def __init__(self, *, mapping_store=None) -> None:
        self.mapping_store = mapping_store or get_mapping_store()

    async def restore_text(self, *, session_id: str, text: str) -> RestoreResult:
        mappings = await self.mapping_store.get_all(session_id)
        restored_count = 0

        def replace_placeholder(match) -> str:
            nonlocal restored_count
            placeholder = match.group(0)
            original = mappings.get(placeholder)
            if original is None:
                return placeholder
            restored_count += 1
            return original

        restored_text = PLACEHOLDER_PATTERN.sub(replace_placeholder, text)
        return RestoreResult(restored_text=restored_text, restored_count=restored_count)
