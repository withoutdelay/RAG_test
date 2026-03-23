from __future__ import annotations

import re
from collections import defaultdict

from app.presidio_config import PLACEHOLDER_LABELS, PLACEHOLDER_TO_ENTITY, normalize_entity_type


PLACEHOLDER_PARSE_PATTERN = re.compile(
    r"\[(?P<label>Person|Company|Amount|ID_Card|Phone|BankCard|Address|Project)_(?P<token>[A-Za-z0-9]+)\]"
)


class PlaceholderRegistry:
    def __init__(self, existing_mappings: dict[str, str] | None = None) -> None:
        self._by_type_and_value: dict[str, dict[str, str]] = defaultdict(dict)
        self._next_index: dict[str, int] = defaultdict(int)

        for placeholder, original in (existing_mappings or {}).items():
            parsed = self._parse_placeholder(placeholder)
            if parsed is None:
                continue
            entity_type, index = parsed
            self._by_type_and_value[entity_type][original] = placeholder
            self._next_index[entity_type] = max(self._next_index[entity_type], index)

    def get_or_create(self, entity_type: str, original: str) -> tuple[str, bool]:
        normalized = normalize_entity_type(entity_type)
        existing = self._by_type_and_value[normalized].get(original)
        if existing is not None:
            return existing, False

        next_index = self._next_index[normalized] + 1
        self._next_index[normalized] = next_index
        placeholder = self._format_placeholder(normalized, next_index)
        self._by_type_and_value[normalized][original] = placeholder
        return placeholder, True

    def allocate_many(self, entity_pairs: list[tuple[str, str]]) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
        allocations: dict[tuple[str, str], str] = {}
        created_mappings: dict[str, str] = {}

        for entity_type, original in entity_pairs:
            normalized = normalize_entity_type(entity_type)
            key = (normalized, original)
            if key in allocations:
                continue

            placeholder, is_new = self.get_or_create(normalized, original)
            allocations[key] = placeholder
            if is_new:
                created_mappings[placeholder] = original

        return allocations, created_mappings

    def _parse_placeholder(self, placeholder: str) -> tuple[str, int] | None:
        match = PLACEHOLDER_PARSE_PATTERN.fullmatch(placeholder)
        if match is None:
            return None

        entity_type = PLACEHOLDER_TO_ENTITY.get(match.group("label"))
        if entity_type is None:
            return None

        token = match.group("token")
        if entity_type == "COMPANY":
            index = self._alpha_to_index(token)
        else:
            index = int(token)
        return entity_type, index

    @staticmethod
    def _format_placeholder(entity_type: str, index: int) -> str:
        label = PLACEHOLDER_LABELS[entity_type]
        token = PlaceholderRegistry._index_to_alpha(index) if entity_type == "COMPANY" else str(index)
        return f"[{label}_{token}]"

    @staticmethod
    def _index_to_alpha(index: int) -> str:
        if index <= 0:
            raise ValueError("Company placeholder index must be positive")

        chars: list[str] = []
        value = index
        while value > 0:
            value -= 1
            chars.append(chr(ord("A") + (value % 26)))
            value //= 26
        return "".join(reversed(chars))

    @staticmethod
    def _alpha_to_index(token: str) -> int:
        value = 0
        for char in token.upper():
            if not ("A" <= char <= "Z"):
                raise ValueError(f"Invalid company placeholder token: {token}")
            value = (value * 26) + (ord(char) - ord("A") + 1)
        return value
