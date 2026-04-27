from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_RULE_PATH = Path(__file__).resolve().parent / "rules" / "semantic_rules.json"


@dataclass(frozen=True)
class SemanticRules:
    token_groups: dict[str, tuple[str, ...]]
    section_type_groups: dict[str, frozenset[str]]
    metadata: dict[str, Any]

    def tokens(self, name: str, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
        return self.token_groups.get(name, fallback)

    def section_types(self, name: str, fallback: set[str] | frozenset[str] = frozenset()) -> set[str]:
        return set(self.section_type_groups.get(name, frozenset(fallback)))


def _normalize_token_groups(raw_groups: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw_groups, dict):
        return {}
    groups: dict[str, tuple[str, ...]] = {}
    for name, values in raw_groups.items():
        if not isinstance(name, str) or not isinstance(values, list):
            continue
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            token = str(value).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        groups[name] = tuple(normalized)
    return groups


def _normalize_section_type_groups(raw_groups: Any) -> dict[str, frozenset[str]]:
    if not isinstance(raw_groups, dict):
        return {}
    groups: dict[str, frozenset[str]] = {}
    for name, values in raw_groups.items():
        if not isinstance(name, str) or not isinstance(values, list):
            continue
        groups[name] = frozenset(str(value).strip().lower() for value in values if str(value).strip())
    return groups


@lru_cache(maxsize=4)
def get_semantic_rules(path: str | None = None) -> SemanticRules:
    configured_path = path or os.getenv("SEMANTIC_RULES_PATH")
    rule_path = Path(configured_path).expanduser() if configured_path else DEFAULT_RULE_PATH
    try:
        payload = json.loads(rule_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid semantic rule JSON: {rule_path}") from exc
    if not isinstance(payload, dict):
        payload = {}
    return SemanticRules(
        token_groups=_normalize_token_groups(payload.get("token_groups")),
        section_type_groups=_normalize_section_type_groups(payload.get("section_type_groups")),
        metadata=dict(payload.get("metadata") or {}),
    )
