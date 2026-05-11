from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


DEFAULT_TAXONOMY_DIR = Path(__file__).resolve().parents[3] / "data" / "domain_taxonomy"
_SEPARATOR_PATTERN = re.compile(r"[/+|,_-]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NEVER_MATCH_PATTERN = re.compile(r"a^")


class TaxonomySchemaError(ValueError):
    """Raised when a domain taxonomy JSON file has an invalid schema."""


@dataclass(frozen=True)
class TaxonomyRule:
    key: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class SynonymRegistry:
    groups: tuple[tuple[str, ...], ...]
    alias_to_group: Mapping[str, tuple[str, ...]]
    searchable_aliases: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "synonyms.json") -> "SynonymRegistry":
        _require_schema_version(payload, filename)
        groups: list[tuple[str, ...]] = []
        for index, group in enumerate(_require_list(payload.get("synonym_groups"), "synonym_groups", filename)):
            field = f"{filename}:synonym_groups[{index}]"
            if not isinstance(group, Mapping):
                raise TaxonomySchemaError(f"{field} must be an object")
            canonical = _require_string(group, "canonical", field)
            aliases = _string_tuple(group.get("aliases", []), f"{field}.aliases")
            normalized = tuple(_dedupe_keep_order((canonical, *aliases)))
            if len(normalized) < 2:
                raise TaxonomySchemaError(f"{field} must contain canonical plus at least one alias")
            groups.append(normalized)
        if not groups:
            raise TaxonomySchemaError(f"{filename}: synonym_groups cannot be empty")
        alias_to_group = {alias: group for group in groups for alias in group}
        searchable_aliases = tuple(sorted(alias_to_group.keys(), key=len, reverse=True))
        return cls(groups=tuple(groups), alias_to_group=alias_to_group, searchable_aliases=searchable_aliases)

    def expand_term(self, term: str) -> tuple[str, ...]:
        expanded: list[str] = []
        for variant in _iter_base_variants(term):
            expanded.append(variant)
            group = self.alias_to_group.get(variant)
            if group:
                expanded.extend(group)
            for alias in self.searchable_aliases:
                if alias == variant or alias not in variant:
                    continue
                expanded.append(alias)
                expanded.extend(self.alias_to_group[alias])
        return tuple(_dedupe_keep_order(expanded))

    def expand_terms(self, terms: Iterable[str]) -> list[str]:
        expanded: list[str] = []
        for term in terms:
            expanded.extend(self.expand_term(str(term or "")))
        return _dedupe_keep_order(expanded)

    def extract_terms(self, text: str) -> list[str]:
        haystack = str(text or "").casefold()
        compact_haystack = _normalize_match_text(text)
        matches: list[str] = []
        for alias in self.searchable_aliases:
            alias_compact = _normalize_match_text(alias)
            if alias in haystack or (alias_compact and alias_compact in compact_haystack):
                matches.extend(self.alias_to_group[alias])
        return _dedupe_keep_order(matches)

    def contains(self, text: str, term: str) -> bool:
        haystack = str(text or "").casefold()
        compact_haystack = _normalize_match_text(text)
        for candidate in self.expand_term(term):
            compact_candidate = _normalize_match_text(candidate)
            if candidate in haystack or (compact_candidate and compact_candidate in compact_haystack):
                return True
        return False


@dataclass(frozen=True)
class SectionTypeRegistry:
    labels: Mapping[str, str]
    rules: tuple[TaxonomyRule, ...]
    commercial_manual: Mapping[str, tuple[str, ...]]
    content_form_terms: Mapping[str, tuple[str, ...]]
    support_forms: Mapping[str, frozenset[str]]
    related_types: Mapping[str, frozenset[str]]
    heading_focus_terms: Mapping[str, tuple[str, ...]]
    heading_noise_terms: Mapping[str, tuple[str, ...]]
    heading_rules: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "section_types.json") -> "SectionTypeRegistry":
        _require_schema_version(payload, filename)
        labels: dict[str, str] = {}
        rules: list[TaxonomyRule] = []
        support_forms: dict[str, frozenset[str]] = {}
        related_types: dict[str, frozenset[str]] = {}
        for index, item in enumerate(_require_list(payload.get("section_types"), "section_types", filename)):
            field = f"{filename}:section_types[{index}]"
            if not isinstance(item, Mapping):
                raise TaxonomySchemaError(f"{field} must be an object")
            key = _require_key(item, "key", field)
            labels[key] = str(item.get("label") or key).strip()
            rules.append(TaxonomyRule(key=key, keywords=_string_tuple(item.get("keywords", []), f"{field}.keywords")))
            support_forms[key] = frozenset(_string_tuple(item.get("support_content_forms", []), f"{field}.support_content_forms"))
            related_types[key] = frozenset(
                value.lower()
                for value in _string_tuple(item.get("related_section_types", []), f"{field}.related_section_types")
            )
        if not rules:
            raise TaxonomySchemaError(f"{filename}: section_types cannot be empty")
        commercial_manual = _string_tuple_mapping(payload.get("commercial_manual", {}), f"{filename}:commercial_manual")
        content_form_terms = _string_tuple_mapping(payload.get("content_form_terms", {}), f"{filename}:content_form_terms")
        heading_focus_terms = _string_tuple_mapping(payload.get("heading_focus_terms", {}), f"{filename}:heading_focus_terms")
        heading_noise_terms = _string_tuple_mapping(payload.get("heading_noise_terms", {}), f"{filename}:heading_noise_terms")
        heading_rules = _require_mapping(payload.get("heading_rules", {}), f"{filename}:heading_rules")
        return cls(
            labels=labels,
            rules=tuple(rules),
            commercial_manual=commercial_manual,
            content_form_terms=content_form_terms,
            support_forms=support_forms,
            related_types=related_types,
            heading_focus_terms=heading_focus_terms,
            heading_noise_terms=heading_noise_terms,
            heading_rules=dict(heading_rules),
        )

    def match(self, *, heading_text: str, content_text: str, synonyms: SynonymRegistry, default: str = "unknown") -> str:
        return _match_weighted_rules(
            heading_text=heading_text,
            content_text=content_text,
            rules=self.rules,
            synonyms=synonyms,
            default=default,
        )

    def is_commercial_manual_text(self, *texts: str) -> bool:
        compact = _normalize_match_text("\n".join(str(text or "") for text in texts))
        if not compact:
            return False
        has_supply_scope = _compact_contains_any(compact, self.commercial_manual.get("supply_scope_terms", ()))
        has_strong_delivery = _compact_contains_any(compact, self.commercial_manual.get("strong_delivery_terms", ()))
        if has_supply_scope and not has_strong_delivery:
            return False
        if _compact_contains_any(compact, self.commercial_manual.get("manual_only_terms", ())):
            return True
        if _compact_contains_any(compact, self.commercial_manual.get("exact_compound_terms", ())):
            return True
        if _compact_contains_any(compact, self.commercial_manual.get("conditional_terms", ())):
            return _compact_contains_any(compact, self.commercial_manual.get("context_terms", ()))
        if _compact_contains_any(compact, self.commercial_manual.get("technical_list_only_terms", ())):
            return False
        return False

    def classify_content_form(
        self,
        *,
        haystack: str,
        chunk_type: str,
        front_matter: bool,
        needs_asset_lookup: bool,
        section_type: str,
        formula_like: bool,
        synonyms: SynonymRegistry,
    ) -> str:
        if front_matter:
            return "page_furniture"
        if self._contains_any(haystack, "certificate", synonyms):
            return "certificate"
        if str(chunk_type or "").upper() == "TABLE":
            if str(section_type or "").lower() == "communication_interface" and self._contains_any(haystack, "interface", synonyms):
                return "interface_table"
            if self._contains_any(haystack, "bom", synonyms):
                return "bom_table"
            if self._contains_any(haystack, "interface", synonyms):
                return "interface_table"
            if self._contains_any(haystack, "protection", synonyms):
                return "protection_table"
            return "parameter_table"
        if str(section_type or "").lower() == "communication_interface" and self._contains_any(haystack, "interface", synonyms):
            return "narrative"
        if formula_like:
            return "formula"
        if needs_asset_lookup and self._contains_any(haystack, "figure", synonyms):
            return "figure"
        return "narrative"

    def apply_heading_overrides(self, *, section_type: str, heading_text: str, synonyms: SynonymRegistry) -> str:
        heading_lower = str(heading_text or "").casefold()
        compact_heading = _normalize_match_text(heading_text)
        overall_terms = self.heading_rules.get("overall_solution_heading_terms", [])
        specialized_terms = self.heading_rules.get("specialized_heading_terms", [])
        if _direct_compact_contains_any(compact_heading, overall_terms) and not _direct_compact_contains_any(
            compact_heading,
            specialized_terms,
        ):
            section_type = "overall_solution"
        interface_all_terms = _string_tuple(self.heading_rules.get("interface_heading_all_terms", []), "heading_rules.interface_heading_all_terms")
        if interface_all_terms and all(term.casefold().replace(" ", "") in compact_heading for term in interface_all_terms):
            return "communication_interface"
        if "性能要求" in heading_lower:
            for route in self.heading_rules.get("performance_routes", []):
                if not isinstance(route, Mapping):
                    continue
                target = str(route.get("section_type") or "").strip().lower()
                keywords = _string_tuple(route.get("keywords", []), "heading_rules.performance_routes.keywords")
                if target and _contains_any_with_synonyms(heading_lower, keywords, synonyms):
                    return target
        return section_type

    def support_content_forms(self, section_type: str) -> set[str]:
        normalized = str(section_type or "").lower()
        return set(self.support_forms.get(normalized, frozenset({"narrative"}))) or {"narrative"}

    def related_section_types(self, section_type: str) -> set[str]:
        return set(self.related_types.get(str(section_type or "").lower(), frozenset()))

    def heading_focus_adjustment(self, *, target_section_type: str, heading_text: str, synonyms: SynonymRegistry) -> tuple[float, list[str]]:
        normalized_type = str(target_section_type or "").lower()
        haystack = str(heading_text or "").casefold()
        compact_haystack = _normalize_match_text(haystack)
        if not normalized_type or normalized_type == "unknown" or not haystack:
            return 0.0, []
        score = 0.0
        reasons: list[str] = []
        if any(
            synonyms.contains(haystack, term) or term.casefold().replace(" ", "") in compact_haystack
            for term in self.heading_focus_terms.get(normalized_type, ())
        ):
            score += 0.14
            reasons.append("heading_focus_match")
        if any(
            synonyms.contains(haystack, term) or term.casefold().replace(" ", "") in compact_haystack
            for term in self.heading_noise_terms.get(normalized_type, ())
        ):
            score -= 0.22
            reasons.append("heading_noise_penalty")
        return score, reasons

    def _contains_any(self, text: str, group_name: str, synonyms: SynonymRegistry) -> bool:
        return any(synonyms.contains(text, keyword) for keyword in self.content_form_terms.get(group_name, ()))


@dataclass(frozen=True)
class EquipmentTypeRegistry:
    labels: Mapping[str, str]
    rules: tuple[TaxonomyRule, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "equipment_types.json") -> "EquipmentTypeRegistry":
        _require_schema_version(payload, filename)
        labels: dict[str, str] = {}
        rules: list[TaxonomyRule] = []
        for index, item in enumerate(_require_list(payload.get("equipment_types"), "equipment_types", filename)):
            field = f"{filename}:equipment_types[{index}]"
            if not isinstance(item, Mapping):
                raise TaxonomySchemaError(f"{field} must be an object")
            key = _require_key(item, "key", field)
            labels[key] = str(item.get("label") or key).strip()
            rules.append(TaxonomyRule(key=key, keywords=_string_tuple(item.get("keywords", []), f"{field}.keywords")))
        if not rules:
            raise TaxonomySchemaError(f"{filename}: equipment_types cannot be empty")
        return cls(labels=labels, rules=tuple(rules))

    def match(self, *, heading_text: str, content_text: str, synonyms: SynonymRegistry, default: str = "generic") -> str:
        return _match_weighted_rules(
            heading_text=heading_text,
            content_text=content_text,
            rules=self.rules,
            synonyms=synonyms,
            default=default,
        )


@dataclass(frozen=True)
class DomainTaxonomyRegistry:
    synonyms: SynonymRegistry
    section_types: SectionTypeRegistry
    equipment_types: EquipmentTypeRegistry

    def infer(
        self,
        text: str,
        *,
        heading_path: str | None = None,
        chunk_type: str = "PLAIN",
        front_matter: bool = False,
        needs_asset_lookup: bool = False,
        formula_like: bool = False,
    ) -> dict[str, str]:
        heading_text = str(heading_path or "")
        content_text = str(text or "")[:2000]
        haystack = f"{heading_text}\n{content_text}".casefold()
        section_type = self.section_types.match(
            heading_text=heading_text,
            content_text=content_text,
            synonyms=self.synonyms,
            default="unknown",
        )
        if section_type not in {"supply_scope", "bom_or_supply_list"} and self.section_types.is_commercial_manual_text(
            heading_text,
            content_text,
        ):
            section_type = "commercial_manual_only"
        equipment_type = self.equipment_types.match(
            heading_text=heading_text,
            content_text=content_text,
            synonyms=self.synonyms,
            default="generic",
        )
        content_form = self.section_types.classify_content_form(
            haystack=haystack,
            chunk_type=chunk_type,
            front_matter=front_matter,
            needs_asset_lookup=needs_asset_lookup,
            section_type=section_type,
            formula_like=formula_like,
            synonyms=self.synonyms,
        )
        section_type = self.section_types.apply_heading_overrides(
            section_type=section_type,
            heading_text=heading_text,
            synonyms=self.synonyms,
        )
        return {
            "section_type": section_type,
            "equipment_type": equipment_type,
            "content_form": content_form,
            "taxonomy_source": "domain_taxonomy",
        }

    def extract_hints(self, *texts: str) -> list[str]:
        haystack = "\n".join(str(text or "") for text in texts)
        hints: list[str] = []
        for rule in (*self.section_types.rules, *self.equipment_types.rules):
            for keyword in rule.keywords:
                if self.synonyms.contains(haystack, keyword) and keyword not in hints:
                    hints.append(keyword)
        return hints


@dataclass(frozen=True)
class AssetRoleRegistry:
    patterns: Mapping[str, re.Pattern[str]]
    thresholds: Mapping[str, int]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "asset_roles.json") -> "AssetRoleRegistry":
        _require_schema_version(payload, filename)
        patterns_payload = _require_mapping(payload.get("patterns"), f"{filename}:patterns")
        patterns: dict[str, re.Pattern[str]] = {}
        for key, value in patterns_payload.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                raise TaxonomySchemaError(f"{filename}:patterns entries must be non-empty string patterns")
            try:
                patterns[key] = re.compile(value, re.IGNORECASE)
            except re.error as exc:
                raise TaxonomySchemaError(f"{filename}:patterns.{key} is not a valid regex") from exc
        thresholds_payload = _require_mapping(payload.get("thresholds", {}), f"{filename}:thresholds")
        thresholds: dict[str, int] = {}
        for key, value in thresholds_payload.items():
            try:
                thresholds[str(key)] = int(value)
            except (TypeError, ValueError) as exc:
                raise TaxonomySchemaError(f"{filename}:thresholds.{key} must be an integer") from exc
        return cls(patterns=patterns, thresholds=thresholds)

    def pattern(self, name: str) -> re.Pattern[str]:
        return self.patterns.get(name, _NEVER_MATCH_PATTERN)

    def threshold(self, name: str, default: int) -> int:
        return int(self.thresholds.get(name, default))


@dataclass(frozen=True)
class RetrievalPolicyRegistry:
    token_groups: Mapping[str, tuple[str, ...]]
    section_type_groups: Mapping[str, frozenset[str]]
    mappings: Mapping[str, Any]
    item_rules: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]
    wiki: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "retrieval_policies.json") -> "RetrievalPolicyRegistry":
        _require_schema_version(payload, filename)
        token_groups = _string_tuple_mapping(payload.get("token_groups", {}), f"{filename}:token_groups")
        section_type_groups = {
            key: frozenset(values)
            for key, values in _string_tuple_mapping(payload.get("section_type_groups", {}), f"{filename}:section_type_groups").items()
        }
        mappings = dict(_require_mapping(payload.get("mappings", {}), f"{filename}:mappings"))
        raw_item_rules = _require_mapping(payload.get("item_rules", {}), f"{filename}:item_rules")
        item_rules: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
        for name, rules in raw_item_rules.items():
            normalized_rules: list[tuple[str, tuple[str, ...]]] = []
            for index, rule in enumerate(_require_list(rules, f"item_rules.{name}", filename)):
                field = f"{filename}:item_rules.{name}[{index}]"
                if not isinstance(rule, Mapping):
                    raise TaxonomySchemaError(f"{field} must be an object")
                item = _require_string(rule, "item", field)
                triggers = _string_tuple(rule.get("triggers", []), f"{field}.triggers")
                normalized_rules.append((item, triggers))
            item_rules[str(name)] = tuple(normalized_rules)
        wiki = dict(_require_mapping(payload.get("wiki", {}), f"{filename}:wiki"))
        return cls(
            token_groups=token_groups,
            section_type_groups=section_type_groups,
            mappings=mappings,
            item_rules=item_rules,
            wiki=wiki,
        )

    def tokens(self, name: str, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
        return self.token_groups.get(name, fallback)

    def section_types(self, name: str, fallback: set[str] | frozenset[str] = frozenset()) -> set[str]:
        return set(self.section_type_groups.get(name, frozenset(fallback)))

    def tuple_mapping(self, name: str) -> dict[str, tuple[str, ...]]:
        raw = self.mappings.get(name, {})
        if not isinstance(raw, Mapping):
            return {}
        return {str(key): _string_tuple(value, f"mappings.{name}.{key}") for key, value in raw.items()}

    def nested_tuple_mapping(self, name: str) -> dict[str, dict[str, tuple[str, ...]]]:
        raw = self.mappings.get(name, {})
        if not isinstance(raw, Mapping):
            return {}
        normalized: dict[str, dict[str, tuple[str, ...]]] = {}
        for key, value in raw.items():
            if isinstance(value, Mapping):
                normalized[str(key)] = {
                    str(child_key): _string_tuple(child_value, f"mappings.{name}.{key}.{child_key}")
                    for child_key, child_value in value.items()
                }
        return normalized

    def text_mapping(self, name: str) -> dict[str, str]:
        raw = self.mappings.get(name, {})
        if not isinstance(raw, Mapping):
            return {}
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}

    def nested_text_mapping(self, name: str) -> dict[str, dict[str, str]]:
        raw = self.mappings.get(name, {})
        if not isinstance(raw, Mapping):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for key, value in raw.items():
            if isinstance(value, Mapping):
                normalized[str(key)] = {
                    str(child_key): str(child_value)
                    for child_key, child_value in value.items()
                    if isinstance(child_value, str)
                }
        return normalized

    def rules(self, name: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return self.item_rules.get(name, ())

    def wiki_mapping(self, name: str) -> dict[str, Any]:
        value = self.wiki.get(name, {})
        return dict(value) if isinstance(value, Mapping) else {}

    def resolve(self, taxonomy: Mapping[str, Any]) -> dict[str, Any]:
        section_type = str(taxonomy.get("section_type") or "unknown").lower()
        equipment_type = str(taxonomy.get("equipment_type") or "generic").lower()
        section_asset_hints = self.tuple_mapping("section_asset_query_hints")
        return {
            "section_type": section_type,
            "equipment_type": equipment_type,
            "asset_query_hints": list(section_asset_hints.get(section_type, ())),
            "extractive": section_type in self.section_types("extractive_section_types"),
            "table_reference_only": section_type in self.section_types("table_placeholder_reference_only_section_types"),
        }


@dataclass(frozen=True)
class ForbiddenPhraseRegistry:
    items: tuple[dict[str, str], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, filename: str = "forbidden_phrases.json") -> "ForbiddenPhraseRegistry":
        _require_schema_version(payload, filename)
        items: list[dict[str, str]] = []
        for index, item in enumerate(_require_list(payload.get("forbidden_phrases"), "forbidden_phrases", filename)):
            field = f"{filename}:forbidden_phrases[{index}]"
            if not isinstance(item, Mapping):
                raise TaxonomySchemaError(f"{field} must be an object")
            items.append(
                {
                    "phrase": _require_string(item, "phrase", field),
                    "reason": _require_string(item, "reason", field),
                    "preferred": _require_string(item, "preferred", field),
                }
            )
        return cls(items=tuple(items))


def load_domain_taxonomy_registries(directory: str | Path | None = None) -> dict[str, Any]:
    return {
        "taxonomy": get_taxonomy_registry(_cache_key(directory)),
        "asset_roles": get_asset_role_registry(_cache_key(directory)),
        "retrieval_policies": get_retrieval_policy_registry(_cache_key(directory)),
        "forbidden_phrases": get_forbidden_phrase_registry(_cache_key(directory)),
    }


@lru_cache(maxsize=8)
def get_taxonomy_registry(directory: str | None = None) -> DomainTaxonomyRegistry:
    return DomainTaxonomyRegistry(
        synonyms=get_synonym_registry(directory),
        section_types=get_section_type_registry(directory),
        equipment_types=get_equipment_type_registry(directory),
    )


@lru_cache(maxsize=8)
def get_synonym_registry(directory: str | None = None) -> SynonymRegistry:
    payload = _load_json_payload("synonyms.json", _MINIMAL_SYNONYM_PAYLOAD, directory)
    return SynonymRegistry.from_payload(payload)


@lru_cache(maxsize=8)
def get_section_type_registry(directory: str | None = None) -> SectionTypeRegistry:
    payload = _load_json_payload("section_types.json", _MINIMAL_SECTION_TYPE_PAYLOAD, directory)
    return SectionTypeRegistry.from_payload(payload)


@lru_cache(maxsize=8)
def get_equipment_type_registry(directory: str | None = None) -> EquipmentTypeRegistry:
    payload = _load_json_payload("equipment_types.json", _MINIMAL_EQUIPMENT_TYPE_PAYLOAD, directory)
    return EquipmentTypeRegistry.from_payload(payload)


@lru_cache(maxsize=8)
def get_asset_role_registry(directory: str | None = None) -> AssetRoleRegistry:
    payload = _load_json_payload("asset_roles.json", _MINIMAL_ASSET_ROLE_PAYLOAD, directory)
    return AssetRoleRegistry.from_payload(payload)


@lru_cache(maxsize=8)
def get_retrieval_policy_registry(directory: str | None = None) -> RetrievalPolicyRegistry:
    payload = _load_json_payload("retrieval_policies.json", _MINIMAL_RETRIEVAL_POLICY_PAYLOAD, directory)
    return RetrievalPolicyRegistry.from_payload(payload)


@lru_cache(maxsize=8)
def get_forbidden_phrase_registry(directory: str | None = None) -> ForbiddenPhraseRegistry:
    payload = _load_json_payload("forbidden_phrases.json", _MINIMAL_FORBIDDEN_PHRASE_PAYLOAD, directory)
    return ForbiddenPhraseRegistry.from_payload(payload)


def clear_domain_taxonomy_caches() -> None:
    get_taxonomy_registry.cache_clear()
    get_synonym_registry.cache_clear()
    get_section_type_registry.cache_clear()
    get_equipment_type_registry.cache_clear()
    get_asset_role_registry.cache_clear()
    get_retrieval_policy_registry.cache_clear()
    get_forbidden_phrase_registry.cache_clear()


def _cache_key(directory: str | Path | None) -> str | None:
    if directory is None:
        return None
    return str(Path(directory).expanduser())


def _taxonomy_dir(directory: str | None = None) -> Path:
    configured = directory or os.getenv("DOMAIN_TAXONOMY_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_TAXONOMY_DIR


def _load_json_payload(filename: str, fallback: Mapping[str, Any], directory: str | None = None) -> Mapping[str, Any]:
    path = _taxonomy_dir(directory) / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = fallback
    if not isinstance(payload, Mapping):
        raise TaxonomySchemaError(f"{filename}: root must be an object")
    return payload


def _require_schema_version(payload: Mapping[str, Any], filename: str) -> None:
    if not isinstance(payload.get("schema_version"), int):
        raise TaxonomySchemaError(f"{filename}: schema_version must be an integer")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaxonomySchemaError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str, filename: str) -> list[Any]:
    if not isinstance(value, list):
        raise TaxonomySchemaError(f"{filename}:{field} must be a list")
    return value


def _require_string(mapping: Mapping[str, Any], key: str, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaxonomySchemaError(f"{field}.{key} must be a non-empty string")
    return value.strip()


def _require_key(mapping: Mapping[str, Any], key: str, field: str) -> str:
    return _require_string(mapping, key, field).lower()


def _string_tuple(values: Any, field: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise TaxonomySchemaError(f"{field} must be a list")
    normalized = tuple(_dedupe_keep_order(str(value).strip() for value in values if str(value).strip()))
    return normalized


def _string_tuple_mapping(value: Any, field: str) -> dict[str, tuple[str, ...]]:
    mapping = _require_mapping(value, field)
    return {str(key).lower(): _string_tuple(values, f"{field}.{key}") for key, values in mapping.items()}


def _normalize_match_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub("", str(text or "").strip().casefold())


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _iter_base_variants(term: str) -> list[str]:
    normalized = str(term or "").strip().casefold()
    if not normalized:
        return []
    variants: list[str] = [normalized]
    compact = _normalize_match_text(normalized)
    if compact and compact != normalized:
        variants.append(compact)
    for part in _SEPARATOR_PATTERN.split(normalized):
        candidate = part.strip()
        if len(candidate) >= 2:
            variants.append(candidate)
    collapsed = _SEPARATOR_PATTERN.sub("", normalized).strip()
    if len(collapsed) >= 2 and collapsed not in variants:
        variants.append(collapsed)
    return _dedupe_keep_order(variants)


def _compact_contains_any(compact_haystack: str, terms: Iterable[str]) -> bool:
    return any(str(term or "").casefold().replace(" ", "") in compact_haystack for term in terms)


def _contains_any_with_synonyms(text: str, terms: Iterable[str], synonyms: SynonymRegistry) -> bool:
    compact = _normalize_match_text(text)
    return any(synonyms.contains(text, term) or str(term).casefold().replace(" ", "") in compact for term in terms)


def _direct_compact_contains_any(compact_text: str, terms: Iterable[str]) -> bool:
    return any(str(term or "").casefold().replace(" ", "") in compact_text for term in terms)


def _match_weighted_rules(
    *,
    heading_text: str,
    content_text: str,
    rules: tuple[TaxonomyRule, ...],
    synonyms: SynonymRegistry,
    default: str,
) -> str:
    best_label = default
    best_score = 0
    for rule in rules:
        heading_match = any(synonyms.contains(heading_text, keyword) for keyword in rule.keywords)
        content_match = any(synonyms.contains(content_text, keyword) for keyword in rule.keywords)
        score = 0
        if heading_match:
            score += 5
        if content_match:
            score += 1
        if score > best_score:
            best_label = rule.key
            best_score = score
    return best_label if best_score > 0 else default


_MINIMAL_SYNONYM_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "synonym_groups": [
        {"canonical": "变频器", "aliases": ["vfd"]},
        {"canonical": "通信", "aliases": ["通讯"]},
        {"canonical": "供货清单", "aliases": ["bom"]},
    ],
}

_MINIMAL_SECTION_TYPE_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "section_types": [
        {"key": "overall_solution", "label": "总体方案", "keywords": ["总体方案"], "support_content_forms": ["narrative"]},
        {"key": "bom_or_supply_list", "label": "供货清单", "keywords": ["供货清单", "bom"], "support_content_forms": ["bom_table", "narrative"]},
        {"key": "commercial_manual_only", "label": "商务说明", "keywords": ["交付资料"], "support_content_forms": ["narrative"]},
    ],
    "commercial_manual": {
        "manual_only_terms": ["交付资料"],
        "exact_compound_terms": [],
        "conditional_terms": [],
        "context_terms": [],
        "technical_list_only_terms": ["供货清单"],
        "supply_scope_terms": ["供货范围"],
        "strong_delivery_terms": ["交付资料"],
    },
    "content_form_terms": {
        "bom": ["供货", "清单"],
        "interface": ["接口", "通信"],
        "protection": ["保护", "联锁"],
        "figure": ["图", "示意图"],
        "certificate": ["证书"],
    },
    "heading_focus_terms": {},
    "heading_noise_terms": {},
    "heading_rules": {
        "overall_solution_heading_terms": ["总体方案"],
        "specialized_heading_terms": ["主回路", "接口", "清单"],
        "interface_heading_all_terms": ["上位机", "接口"],
        "performance_routes": [],
    },
}

_MINIMAL_EQUIPMENT_TYPE_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "equipment_types": [
        {"key": "vfd", "label": "高压变频器", "keywords": ["变频器", "vfd"]},
        {"key": "dcs_plc_interface", "label": "DCS/PLC 接口", "keywords": ["dcs", "plc", "通信接口"]},
    ],
}

_MINIMAL_ASSET_ROLE_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "patterns": {
        "engineering_visual": "(图|diagram|schematic)",
        "formula_visual": "(公式|equation)",
        "page_furniture": "(页码|目录)",
        "generic_asset_title": "^(图|figure)$",
        "generic_diagram_type": "^(图示|示意图)$",
        "hard_fragment": "(fragment|局部)",
        "partial_fragment": "(fragment|局部)",
        "complete_diagram": "(系统图|单线图|diagram)",
        "layout_illustration": "(布置图|外形)",
        "product_photo": "(照片|photo)",
        "logo_asset": "(logo|商标)",
        "control_interface_focus": "(接口|信号|plc|dcs)",
        "control_interface_table_noise": "(备品备件|售后)",
        "vfd_auxiliary_curve_noise": "(油站|冷却器)",
        "vfd_focus": "(lci|sfc|变频)",
    },
    "thresholds": {
        "min_reusable_figure_dimension": 80,
        "min_reusable_figure_area": 12000,
    },
}

_MINIMAL_RETRIEVAL_POLICY_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "token_groups": {
        "asset_hints.figure": ["图", "示意图"],
        "asset_hints.table": ["表", "参数"],
        "asset_hints.formula": ["公式"],
        "main_circuit.focus": ["主回路"],
        "main_circuit.noise": ["控制"],
        "protection.focus": ["保护"],
        "protection.noise": ["主回路"],
    },
    "section_type_groups": {
        "extractive_section_types": ["overall_solution", "bom_or_supply_list"],
        "table_placeholder_reference_only_section_types": ["bom_or_supply_list"],
    },
    "mappings": {
        "section_asset_query_hints": {"overall_solution": ["系统示意图"]},
        "section_template_headings": {},
        "extractive_section_openings": {},
        "extractive_table_leads": {},
    },
    "item_rules": {"supply_scope_required_items": []},
    "wiki": {
        "product_families": {
            "generic_engineering_solution": {"title": "通用工程方案族", "aliases": ["工程方案"]}
        },
        "module_cards": {},
        "section_template_guidance": {},
    },
}

_MINIMAL_FORBIDDEN_PHRASE_PAYLOAD: Mapping[str, Any] = {
    "schema_version": 1,
    "forbidden_phrases": [
        {"phrase": "我公司", "reason": "供应方视角不稳定", "preferred": "本方案 / 本系统"},
    ],
}
