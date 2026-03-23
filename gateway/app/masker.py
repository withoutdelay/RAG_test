from __future__ import annotations

from dataclasses import dataclass

from app.detector import DetectedEntity, EntityDetector
from app.mapping_store import get_mapping_store
from app.presidio_config import normalize_entity_type


@dataclass(frozen=True, slots=True)
class MaskedEntity:
    original: str
    placeholder: str
    entity_type: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MaskResult:
    masked_text: str
    entity_count: int
    entities_detected: list[MaskedEntity]


class Masker:
    def __init__(self, *, detector: EntityDetector | None = None, mapping_store=None) -> None:
        self.detector = detector or EntityDetector()
        self.mapping_store = mapping_store or get_mapping_store()

    async def mask_text(
        self,
        *,
        session_id: str,
        text: str,
        entity_types: list[str] | tuple[str, ...] | None = None,
    ) -> MaskResult:
        detected_entities = self.detector.detect(text, entity_types=entity_types)
        entity_pairs: list[tuple[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for entity in detected_entities:
            key = (normalize_entity_type(entity.entity_type), entity.original)
            if key in seen_pairs:
                continue
            entity_pairs.append(key)
            seen_pairs.add(key)

        placeholder_allocations = await self.mapping_store.allocate_placeholders(session_id, entity_pairs)
        masked_entities: list[MaskedEntity] = []
        for entity in detected_entities:
            key = (normalize_entity_type(entity.entity_type), entity.original)
            placeholder = placeholder_allocations[key]
            masked_entities.append(
                MaskedEntity(
                    original=entity.original,
                    placeholder=placeholder,
                    entity_type=entity.entity_type,
                    start=entity.start,
                    end=entity.end,
                )
            )

        masked_text = self._apply_replacements(text, masked_entities)
        return MaskResult(
            masked_text=masked_text,
            entity_count=len(masked_entities),
            entities_detected=masked_entities,
        )

    @staticmethod
    def _apply_replacements(text: str, masked_entities: list[MaskedEntity]) -> str:
        output = text
        for entity in sorted(masked_entities, key=lambda item: item.start, reverse=True):
            output = output[: entity.start] + entity.placeholder + output[entity.end :]
        return output
