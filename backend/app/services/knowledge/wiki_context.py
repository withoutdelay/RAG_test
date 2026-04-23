from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from app.config import BACKEND_ROOT
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


DEFAULT_KNOWLEDGE_WIKI_ROOT = BACKEND_ROOT / "data" / "knowledge_wiki"
TEXT_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_text(value: Any) -> str:
    return WHITESPACE_PATTERN.sub(" ", str(value or "").casefold()).strip()


def _tokenize_text(value: Any) -> set[str]:
    return {match.group(0).casefold() for match in TEXT_TOKEN_PATTERN.finditer(str(value or ""))}


def _truncate_text(value: Any, *, limit: int = 72) -> str:
    text = WHITESPACE_PATTERN.sub(" ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _score_text_match(*, candidate: str, query_text: str, query_tokens: set[str]) -> int:
    normalized_candidate = _normalize_text(candidate)
    if not normalized_candidate:
        return 0
    score = 3 if normalized_candidate in query_text else 0
    candidate_tokens = _tokenize_text(normalized_candidate)
    score += sum(1 for token in query_tokens if token in candidate_tokens)
    return score


def _best_distribution_count(*, distribution: list[dict[str, Any]], key: str, expected_value: str) -> float:
    normalized_expected = str(expected_value or "").strip().lower()
    if not normalized_expected or normalized_expected in {"generic", "unknown"}:
        return 0.0
    best = 0.0
    for item in distribution:
        if not isinstance(item, dict):
            continue
        if str(item.get(key) or "").strip().lower() != normalized_expected:
            continue
        best = max(best, float(item.get("count") or 0))
    return best


def _pick_best_snippet(*, snippets: list[Any], query_text: str, query_tokens: set[str]) -> str:
    best_snippet = ""
    best_score = 0
    for snippet in snippets:
        snippet_text = str(snippet).strip()
        score = _score_text_match(candidate=snippet_text, query_text=query_text, query_tokens=query_tokens)
        if score > best_score:
            best_snippet = snippet_text
            best_score = score
    return _truncate_text(best_snippet) if best_score > 0 else ""


class KnowledgeWikiContextProvider:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_glossary_terms: int = 6,
        max_product_cards: int = 2,
        max_module_cards: int = 3,
        max_equipment_cards: int = 2,
        max_forbidden_phrases: int = 4,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_KNOWLEDGE_WIKI_ROOT
        self.max_glossary_terms = max_glossary_terms
        self.max_product_cards = max_product_cards
        self.max_module_cards = max_module_cards
        self.max_equipment_cards = max_equipment_cards
        self.max_forbidden_phrases = max_forbidden_phrases
        self._assets: dict[str, list[dict[str, Any]]] | None = None

    @property
    def available(self) -> bool:
        return (self.root / "manifest.json").exists()

    def build_section_context(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> str:
        try:
            selected_assets = self._select_section_assets(section=section, global_params=global_params)
            if not selected_assets:
                return ""
            section_type = str(selected_assets.get("section_type") or "").strip()
            query_text = str(selected_assets.get("query_text") or "")
            query_tokens = set(selected_assets.get("query_tokens") or set())
            glossary_entries = list(selected_assets.get("glossary_entries") or [])
            template = selected_assets.get("template") if isinstance(selected_assets.get("template"), dict) else None
            product_cards = list(selected_assets.get("product_cards") or [])
            module_cards = list(selected_assets.get("module_cards") or [])
            equipment_cards = list(selected_assets.get("equipment_cards") or [])
            interface_card = selected_assets.get("interface_card") if isinstance(selected_assets.get("interface_card"), dict) else None
            forbidden_phrases = list(selected_assets.get("forbidden_phrases") or [])[: self.max_forbidden_phrases]

            parts = ["AI Wiki 编译知识（术语、结构、口径约束）"]

            if glossary_entries:
                lines = ["术语别名："]
                for entry in glossary_entries:
                    primary_term = str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip()
                    aliases = [
                        str(alias).strip()
                        for alias in (
                            entry.get("display_aliases")
                            or entry.get("aliases")
                            or []
                        )
                        if str(alias).strip()
                    ]
                    if primary_term:
                        lines.append(
                            f"- {primary_term}：{' / '.join(aliases)}"
                            if aliases
                            else f"- {primary_term}"
                        )
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if template:
                lines = [f"章节骨架（{str(template.get('title') or section_type or '当前章节').strip()}）："]
                for guidance in (template.get("guidance") or [])[:3]:
                    text = str(guidance).strip()
                    if text:
                        lines.append(f"- {text}")
                common_headings = [
                    str(item).strip()
                    for item in (template.get("common_headings") or [])[:2]
                    if str(item).strip()
                ]
                if common_headings:
                    lines.append(f"- 常见小节：{' / '.join(common_headings)}")
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if product_cards:
                lines = ["产品族知识卡："]
                for card in product_cards:
                    title = str(card.get("title") or card.get("product_family") or "").strip()
                    section_labels = [
                        str(item.get("label") or item.get("section_type") or "").strip()
                        for item in (card.get("top_section_types") or [])[:2]
                        if isinstance(item, dict) and str(item.get("label") or item.get("section_type") or "").strip()
                    ]
                    equipment_labels = [
                        str(item.get("label") or item.get("equipment_type") or "").strip()
                        for item in (card.get("top_equipment_types") or [])[:2]
                        if isinstance(item, dict) and str(item.get("label") or item.get("equipment_type") or "").strip()
                    ]
                    representative_titles = [
                        str(item).strip()
                        for item in (card.get("representative_titles") or [])[:1]
                        if str(item).strip()
                    ]
                    detail_parts = []
                    if equipment_labels:
                        detail_parts.append(f"常见设备={ '/'.join(equipment_labels) }")
                    if section_labels:
                        detail_parts.append(f"高频章节={ '/'.join(section_labels) }")
                    if representative_titles:
                        detail_parts.append(f"代表方案={representative_titles[0]}")
                    if title:
                        lines.append(
                            f"- {title}：{'；'.join(detail_parts)}"
                            if detail_parts
                            else f"- {title}"
                        )
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if module_cards:
                lines = ["模块知识卡："]
                for card in module_cards:
                    title = str(card.get("title") or card.get("module_key") or "").strip()
                    equipment_labels = [
                        str(item.get("label") or item.get("equipment_type") or "").strip()
                        for item in (card.get("top_equipment_types") or [])[:2]
                        if isinstance(item, dict) and str(item.get("label") or item.get("equipment_type") or "").strip()
                    ]
                    snippet = _pick_best_snippet(
                        snippets=list(card.get("representative_snippets") or []),
                        query_text=query_text,
                        query_tokens=query_tokens,
                    )
                    headings = [
                        str(item).strip()
                        for item in (card.get("top_headings") or [])[:1]
                        if str(item).strip()
                    ]
                    detail_parts = []
                    if equipment_labels:
                        detail_parts.append(f"常见设备={ '/'.join(equipment_labels) }")
                    if headings:
                        detail_parts.append(f"典型章节={headings[0]}")
                    if snippet:
                        detail_parts.append(f"参考要点={snippet}")
                    if title:
                        lines.append(
                            f"- {title}：{'；'.join(detail_parts)}"
                            if detail_parts
                            else f"- {title}"
                        )
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if equipment_cards:
                lines = ["设备知识卡："]
                for card in equipment_cards:
                    title = str(card.get("title") or card.get("equipment_type") or "").strip()
                    snippet = _pick_best_snippet(
                        snippets=list(card.get("representative_snippets") or []),
                        query_text=query_text,
                        query_tokens=query_tokens,
                    )
                    section_labels = [
                        str(item.get("label") or item.get("section_type") or "").strip()
                        for item in (card.get("top_section_types") or [])[:2]
                        if isinstance(item, dict) and str(item.get("label") or item.get("section_type") or "").strip()
                    ]
                    detail_parts = []
                    if section_labels:
                        detail_parts.append(f"高频章节={ '/'.join(section_labels) }")
                    if snippet:
                        detail_parts.append(f"参考要点={snippet}")
                    if title:
                        lines.append(
                            f"- {title}：{'；'.join(detail_parts)}"
                            if detail_parts
                            else f"- {title}"
                        )
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if interface_card:
                lines = [f"接口/联锁知识卡（{str(interface_card.get('title') or section_type).strip()}）："]
                for guidance in (interface_card.get("guidance") or [])[:3]:
                    text = str(guidance).strip()
                    if text:
                        lines.append(f"- {text}")
                top_equipment_types = [
                    str(item.get("label") or item.get("equipment_type") or "").strip()
                    for item in (interface_card.get("top_equipment_types") or [])[:2]
                    if isinstance(item, dict) and str(item.get("label") or item.get("equipment_type") or "").strip()
                ]
                if top_equipment_types:
                    lines.append(f"- 高频设备：{' / '.join(top_equipment_types)}")
                top_headings = [
                    str(item).strip()
                    for item in (interface_card.get("top_headings") or [])[:2]
                    if str(item).strip()
                ]
                if top_headings:
                    lines.append(f"- 常见接口主题：{' / '.join(top_headings)}")
                snippet = _pick_best_snippet(
                    snippets=list(interface_card.get("representative_snippets") or []),
                    query_text=query_text,
                    query_tokens=query_tokens,
                )
                if snippet:
                    lines.append(f"- 典型上下文：{snippet}")
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            if forbidden_phrases:
                lines = ["禁用表述："]
                for item in forbidden_phrases:
                    phrase = str(item.get("phrase") or "").strip()
                    preferred = str(item.get("preferred") or "").strip()
                    if phrase and preferred:
                        lines.append(f"- 避免“{phrase}”，改用“{preferred}”")
                    elif phrase:
                        lines.append(f"- 避免“{phrase}”")
                if len(lines) > 1:
                    parts.append("\n".join(lines))

            return "\n\n".join(part for part in parts if part.strip()).strip()
        except Exception:  # noqa: BLE001
            return ""

    def collect_quality_review_bundle(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            selected_assets = self._select_section_assets(section=section, global_params=global_params)
            if not selected_assets:
                return {}
            return {
                "section_type": str(selected_assets.get("section_type") or "").strip(),
                "glossary_entries": list(selected_assets.get("glossary_entries") or []),
                "forbidden_phrases": list(selected_assets.get("forbidden_phrases") or [])[: self.max_forbidden_phrases],
            }
        except Exception:  # noqa: BLE001
            return {}

    def build_quality_review_context(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> str:
        review_bundle = self.collect_quality_review_bundle(section=section, global_params=global_params)
        if not review_bundle:
            return ""
        glossary_entries = list(review_bundle.get("glossary_entries") or [])
        forbidden_phrases = list(review_bundle.get("forbidden_phrases") or [])
        parts = ["AI Wiki 质检约束"]

        preferred_term_lines = ["统一术语："]
        for entry in glossary_entries[:4]:
            primary_term = str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip()
            aliases = [
                str(alias).strip()
                for alias in (entry.get("display_aliases") or entry.get("aliases") or [])
                if str(alias).strip()
            ]
            if primary_term and aliases:
                preferred_term_lines.append(f"- 优先使用“{primary_term}”，避免与“{' / '.join(aliases)}”混用")
        if len(preferred_term_lines) > 1:
            parts.append("\n".join(preferred_term_lines))

        forbidden_lines = ["禁用表述："]
        for item in forbidden_phrases[:4]:
            phrase = str(item.get("phrase") or "").strip()
            preferred = str(item.get("preferred") or "").strip()
            if phrase and preferred:
                forbidden_lines.append(f"- 避免“{phrase}”，改用“{preferred}”")
            elif phrase:
                forbidden_lines.append(f"- 避免“{phrase}”")
        if len(forbidden_lines) > 1:
            parts.append("\n".join(forbidden_lines))

        return "\n\n".join(parts).strip() if len(parts) > 1 else ""

    def collect_query_expansion_terms(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
        max_terms: int = 8,
    ) -> list[str]:
        retrieval_bundle = self.collect_retrieval_prior_bundle(
            section=section,
            global_params=global_params,
            max_terms=max_terms,
        )
        return list(retrieval_bundle.get("query_expansion_terms") or [])

    def collect_retrieval_prior_bundle(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
        max_terms: int = 12,
    ) -> dict[str, Any]:
        try:
            selected_assets = self._select_section_assets(section=section, global_params=global_params)
            if not selected_assets:
                return {}
        except Exception:  # noqa: BLE001
            return {}

        terms: list[str] = []
        glossary_entries = list(selected_assets.get("glossary_entries") or [])
        product_cards = list(selected_assets.get("product_cards") or [])
        module_cards = list(selected_assets.get("module_cards") or [])
        section_type = str(selected_assets.get("section_type") or "").strip().lower()
        allow_card_term_expansion = section_type not in {"supply_scope", "bom_or_supply_list"}

        for entry in glossary_entries:
            primary_term = str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip()
            aliases = [
                str(alias).strip()
                for alias in (entry.get("display_aliases") or entry.get("aliases") or [])
                if str(alias).strip()
            ]
            for item in [primary_term, *aliases]:
                if item and item not in terms:
                    terms.append(item)
                if len(terms) >= max_terms:
                    return {
                        "query_expansion_terms": terms,
                        "glossary_entries": glossary_entries,
                        "product_cards": product_cards,
                        "module_cards": module_cards,
                    }

        if allow_card_term_expansion:
            for card in product_cards:
                candidate_values = [
                    str(card.get("title") or "").strip(),
                    *[str(item).strip() for item in (card.get("aliases") or []) if str(item).strip()],
                    *[str(item).strip() for item in (card.get("representative_titles") or [])[:1] if str(item).strip()],
                ]
                for item in candidate_values:
                    if item and item not in terms:
                        terms.append(item)
                    if len(terms) >= max_terms:
                        return {
                            "query_expansion_terms": terms,
                            "glossary_entries": glossary_entries,
                            "product_cards": product_cards,
                            "module_cards": module_cards,
                        }

            for card in module_cards:
                candidate_values = [
                    str(card.get("title") or "").strip(),
                    *[str(item).strip() for item in (card.get("aliases") or []) if str(item).strip()],
                ]
                for item in candidate_values:
                    if item and item not in terms:
                        terms.append(item)
                    if len(terms) >= max_terms:
                        return {
                            "query_expansion_terms": terms,
                            "glossary_entries": glossary_entries,
                            "product_cards": product_cards,
                            "module_cards": module_cards,
                        }

        return {
            "query_expansion_terms": terms,
            "glossary_entries": glossary_entries,
            "product_cards": product_cards,
            "module_cards": module_cards,
        }

    def _build_query_text(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
        section_type: str,
    ) -> str:
        raw_values: list[str] = [
            str(section.get("title") or ""),
            str(section.get("purpose") or ""),
            str(section.get("description") or ""),
            str(section.get("section_class") or ""),
            section_type,
        ]
        raw_values.extend(str(item or "") for item in (section.get("keywords") or []))
        raw_values.extend(
            str(value).strip()
            for value in global_params.values()
            if isinstance(value, str) and str(value).strip()
        )
        return _normalize_text(" ".join(raw_values))

    def _select_section_assets(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        assets = self._load_assets()
        if not assets:
            return {}
        taxonomy = infer_target_taxonomy(section)
        section_type = str(taxonomy.get("section_type") or section.get("section_type") or "").strip().lower()
        equipment_type = str(taxonomy.get("equipment_type") or section.get("equipment_type") or "").strip().lower()
        query_text = self._build_query_text(section=section, global_params=global_params, section_type=section_type)
        query_tokens = _tokenize_text(query_text)
        glossary_entries = self._match_glossary_entries(
            glossary_entries=assets.get("glossary") or [],
            query_text=query_text,
            query_tokens=query_tokens,
        )
        template = self._match_template(
            templates=assets.get("section_templates") or [],
            section_type=section_type,
        )
        product_cards = self._match_product_cards(
            cards=assets.get("product_cards") or [],
            section_type=section_type,
            equipment_type=equipment_type,
            query_text=query_text,
            query_tokens=query_tokens,
        )
        module_cards = self._match_module_cards(
            cards=assets.get("module_cards") or [],
            section_type=section_type,
            equipment_type=equipment_type,
            query_text=query_text,
            query_tokens=query_tokens,
        )
        equipment_cards = self._match_equipment_cards(
            cards=assets.get("equipment_cards") or [],
            section_type=section_type,
            equipment_type=equipment_type,
            query_text=query_text,
            query_tokens=query_tokens,
        )
        interface_card = self._match_interface_card(
            cards=assets.get("interface_cards") or [],
            section_type=section_type,
        )
        return {
            "section_type": section_type,
            "equipment_type": equipment_type,
            "query_text": query_text,
            "query_tokens": query_tokens,
            "glossary_entries": glossary_entries,
            "template": template,
            "product_cards": product_cards,
            "module_cards": module_cards,
            "equipment_cards": equipment_cards,
            "interface_card": interface_card,
            "forbidden_phrases": list(assets.get("forbidden_phrases") or []),
        }

    def _match_glossary_entries(
        self,
        *,
        glossary_entries: list[dict[str, Any]],
        query_text: str,
        query_tokens: set[str],
    ) -> list[dict[str, Any]]:
        scored_entries: list[tuple[int, dict[str, Any]]] = []
        for entry in glossary_entries:
            candidate_values = [
                str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip(),
                *[str(item).strip() for item in (entry.get("display_aliases") or [])],
                *[str(item).strip() for item in (entry.get("aliases") or [])],
            ]
            normalized_candidates = [
                _normalize_text(item)
                for item in candidate_values
                if _normalize_text(item)
            ]
            if not normalized_candidates:
                continue
            score = sum(_score_text_match(candidate=candidate, query_text=query_text, query_tokens=query_tokens) for candidate in normalized_candidates)
            if score <= 0:
                continue
            scored_entries.append((score, entry))
        scored_entries.sort(
            key=lambda item: (
                item[0],
                len(str(item[1].get("display_primary_term") or item[1].get("primary_term") or "")),
            ),
            reverse=True,
        )
        return [entry for _score, entry in scored_entries[: self.max_glossary_terms]]

    def _match_template(
        self,
        *,
        templates: list[dict[str, Any]],
        section_type: str,
    ) -> dict[str, Any] | None:
        for template in templates:
            if str(template.get("section_type") or "").strip().lower() == section_type:
                return template
        return None

    def _match_product_cards(
        self,
        *,
        cards: list[dict[str, Any]],
        section_type: str,
        equipment_type: str,
        query_text: str,
        query_tokens: set[str],
    ) -> list[dict[str, Any]]:
        scored_cards: list[tuple[float, dict[str, Any]]] = []
        for card in cards:
            top_section_types = [item for item in (card.get("top_section_types") or []) if isinstance(item, dict)]
            top_equipment_types = [item for item in (card.get("top_equipment_types") or []) if isinstance(item, dict)]
            section_match_score = _best_distribution_count(
                distribution=top_section_types,
                key="section_type",
                expected_value=section_type,
            )
            equipment_match_score = _best_distribution_count(
                distribution=top_equipment_types,
                key="equipment_type",
                expected_value=equipment_type,
            )
            candidate_values = [
                str(card.get("title") or "").strip(),
                *[str(item).strip() for item in (card.get("aliases") or [])],
                *[str(item).strip() for item in (card.get("representative_titles") or [])],
                *[str(item).strip() for item in (card.get("representative_headings") or [])],
            ]
            normalized_candidates = [
                _normalize_text(item)
                for item in candidate_values
                if _normalize_text(item)
            ]
            lexical_score = sum(
                _score_text_match(candidate=candidate, query_text=query_text, query_tokens=query_tokens)
                for candidate in normalized_candidates
            )
            if lexical_score <= 0 and section_match_score <= 0:
                continue
            if lexical_score < 2 and section_match_score <= 0:
                continue
            if equipment_type not in {"", "generic", "unknown"} and equipment_match_score <= 0:
                if lexical_score < 3 and section_match_score <= 0:
                    continue
            total_score = section_match_score + lexical_score + equipment_match_score
            if total_score <= 0:
                continue
            scored_cards.append((total_score, card))
        scored_cards.sort(
            key=lambda item: (
                item[0],
                float(item[1].get("entry_count") or 0),
            ),
            reverse=True,
        )
        return [card for _score, card in scored_cards[: self.max_product_cards]]

    def _match_equipment_cards(
        self,
        *,
        cards: list[dict[str, Any]],
        section_type: str,
        equipment_type: str,
        query_text: str,
        query_tokens: set[str],
    ) -> list[dict[str, Any]]:
        scored_cards: list[tuple[float, dict[str, Any]]] = []
        for card in cards:
            top_section_types = [
                item
                for item in (card.get("top_section_types") or [])
                if isinstance(item, dict)
            ]
            section_match_score = 0.0
            for item in top_section_types:
                if str(item.get("section_type") or "").strip().lower() == section_type:
                    section_match_score = max(section_match_score, float(item.get("count") or 0))
            candidate_values = [
                str(card.get("title") or "").strip(),
                str(card.get("equipment_type") or "").strip(),
                *[str(item).strip() for item in (card.get("top_headings") or [])],
                *[str(item).strip() for item in (card.get("representative_snippets") or [])],
            ]
            normalized_candidates = [
                _normalize_text(item)
                for item in candidate_values
                if _normalize_text(item)
            ]
            lexical_score = sum(
                _score_text_match(candidate=candidate, query_text=query_text, query_tokens=query_tokens)
                for candidate in normalized_candidates
            )
            direct_equipment_match = 3.0 if str(card.get("equipment_type") or "").strip().lower() == equipment_type else 0.0
            if lexical_score <= 0 and section_match_score <= 0 and direct_equipment_match <= 0:
                continue
            total_score = section_match_score + lexical_score + direct_equipment_match
            if total_score <= 0:
                continue
            scored_cards.append((total_score, card))
        scored_cards.sort(
            key=lambda item: (
                item[0],
                float(item[1].get("entry_count") or 0),
            ),
            reverse=True,
        )
        return [card for _score, card in scored_cards[: self.max_equipment_cards]]

    def _match_module_cards(
        self,
        *,
        cards: list[dict[str, Any]],
        section_type: str,
        equipment_type: str,
        query_text: str,
        query_tokens: set[str],
    ) -> list[dict[str, Any]]:
        scored_cards: list[tuple[float, dict[str, Any]]] = []
        for card in cards:
            top_section_types = [item for item in (card.get("top_section_types") or []) if isinstance(item, dict)]
            top_equipment_types = [item for item in (card.get("top_equipment_types") or []) if isinstance(item, dict)]
            section_match_score = _best_distribution_count(
                distribution=top_section_types,
                key="section_type",
                expected_value=section_type,
            )
            equipment_match_score = _best_distribution_count(
                distribution=top_equipment_types,
                key="equipment_type",
                expected_value=equipment_type,
            )
            candidate_values = [
                str(card.get("title") or "").strip(),
                *[str(item).strip() for item in (card.get("aliases") or [])],
                *[str(item).strip() for item in (card.get("top_headings") or [])],
                *[str(item).strip() for item in (card.get("representative_snippets") or [])],
            ]
            normalized_candidates = [
                _normalize_text(item)
                for item in candidate_values
                if _normalize_text(item)
            ]
            lexical_score = sum(
                _score_text_match(candidate=candidate, query_text=query_text, query_tokens=query_tokens)
                for candidate in normalized_candidates
            )
            if lexical_score <= 0 and section_match_score <= 0:
                continue
            total_score = section_match_score + lexical_score + equipment_match_score
            if total_score <= 0:
                continue
            scored_cards.append((total_score, card))
        scored_cards.sort(
            key=lambda item: (
                item[0],
                float(item[1].get("entry_count") or 0),
            ),
            reverse=True,
        )
        return [card for _score, card in scored_cards[: self.max_module_cards]]

    def _match_interface_card(
        self,
        *,
        cards: list[dict[str, Any]],
        section_type: str,
    ) -> dict[str, Any] | None:
        for card in cards:
            if str(card.get("section_type") or "").strip().lower() == section_type:
                return card
        return None

    def _load_assets(self) -> dict[str, list[dict[str, Any]]]:
        if self._assets is not None:
            return self._assets
        if not self.available:
            self._assets = {}
            return self._assets
        self._assets = {
            "glossary": self._load_json_list("glossary.json"),
            "product_cards": self._load_json_list("product_cards.json"),
            "module_cards": self._load_json_list("module_cards.json"),
            "equipment_cards": self._load_json_list("equipment_cards.json"),
            "interface_cards": self._load_json_list("interface_cards.json"),
            "section_templates": self._load_json_list("section_templates.json"),
            "forbidden_phrases": self._load_json_list("forbidden_phrases.json"),
        }
        return self._assets

    def _load_json_list(self, file_name: str) -> list[dict[str, Any]]:
        path = self.root / file_name
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
