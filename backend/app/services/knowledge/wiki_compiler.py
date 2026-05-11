from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from app.services.domain.taxonomy_registry import (
    get_forbidden_phrase_registry,
    get_retrieval_policy_registry,
    get_taxonomy_registry,
)
from app.services.knowledge.wiki_quality import (
    evaluate_wiki_item_quality,
    is_publishable_evidence_item,
    quality_score_for_item,
    status_for_quality,
)

_DOMAIN_TAXONOMY = get_taxonomy_registry()
_RETRIEVAL_POLICIES = get_retrieval_policy_registry()

INTERFACE_SECTION_TYPES = {"communication_interface", "protection_interlock", "control_logic"}
EQUIPMENT_TYPE_LABELS = dict(_DOMAIN_TAXONOMY.equipment_types.labels)
SECTION_TYPE_LABELS = dict(_DOMAIN_TAXONOMY.section_types.labels)
PRODUCT_FAMILY_DEFINITIONS = _RETRIEVAL_POLICIES.wiki_mapping("product_families")
MODULE_CARD_DEFINITIONS = _RETRIEVAL_POLICIES.wiki_mapping("module_cards")
SECTION_TEMPLATE_GUIDANCE = _RETRIEVAL_POLICIES.wiki_mapping("section_template_guidance")
FORBIDDEN_PHRASE_SEEDS = [dict(item) for item in get_forbidden_phrase_registry().items]
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
LOW_VALUE_HEADING_SUBSTRINGS = (
    "公司简介",
    "质量保证",
    "实施规划",
    "培训计划",
    "供方培训",
)


@dataclass(frozen=True)
class WikiPage:
    path: str
    title: str
    category: str
    summary: str
    content: str


def compile_knowledge_wiki(
    *,
    outline_entries: Iterable[dict[str, Any]],
    block_entries: Iterable[dict[str, Any]],
    term_lexicon: Mapping[str, Iterable[str]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    normalized_generated_at = generated_at or datetime.now(timezone.utc)
    outline_entries_list = [entry for entry in outline_entries if isinstance(entry, dict)]
    block_entries_list = [entry for entry in block_entries if isinstance(entry, dict)]
    normalized_lexicon = _normalize_term_lexicon(term_lexicon)

    glossary_entries = _build_glossary_entries(normalized_lexicon)
    product_cards = _build_product_cards(outline_entries_list, block_entries_list)
    module_cards = _build_module_cards(block_entries_list)
    equipment_cards = _build_equipment_cards(block_entries_list)
    interface_cards = _build_interface_cards(block_entries_list)
    section_templates = _build_section_templates(outline_entries_list, block_entries_list)
    forbidden_phrases = list(FORBIDDEN_PHRASE_SEEDS)

    pages: list[WikiPage] = []
    pages.append(_build_glossary_page(glossary_entries=glossary_entries))
    for card in product_cards:
        pages.append(_build_product_page(card=card))
    for card in module_cards:
        pages.append(_build_module_page(card=card))
    for card in equipment_cards:
        pages.append(_build_equipment_page(card=card))
    for card in interface_cards:
        pages.append(_build_interface_page(card=card))
    for template in section_templates:
        pages.append(_build_template_page(template=template))
    pages.append(_build_forbidden_phrases_page(forbidden_phrases=forbidden_phrases))

    manifest = {
        "generated_at": normalized_generated_at.isoformat(),
        "source_counts": {
            "outline_entries": len(outline_entries_list),
            "block_entries": len(block_entries_list),
            "term_groups": len(glossary_entries),
        },
        "categories": {
            "glossary": 1,
            "products": len(product_cards),
            "modules": len(module_cards),
            "equipment": len(equipment_cards),
            "interfaces": len(interface_cards),
            "templates": len(section_templates),
            "policies": 1,
        },
        "pages": [
            {
                "path": page.path,
                "title": page.title,
                "category": page.category,
                "summary": page.summary,
            }
            for page in pages
        ],
    }
    structured_assets = {
        "glossary": glossary_entries,
        "product_cards": product_cards,
        "module_cards": module_cards,
        "equipment_cards": equipment_cards,
        "interface_cards": interface_cards,
        "section_templates": section_templates,
        "forbidden_phrases": forbidden_phrases,
    }
    wiki_items = build_wiki_items(
        structured_assets=structured_assets,
        outline_entries=outline_entries_list,
        block_entries=block_entries_list,
        generated_at=normalized_generated_at,
    )

    index_page = _build_index_page(
        pages=pages,
        manifest=manifest,
        structured_assets=structured_assets,
    )
    log_entry = _build_log_entry(
        generated_at=normalized_generated_at,
        manifest=manifest,
    )
    page_map = {page.path: page.content for page in pages}
    page_map["index.md"] = index_page

    return {
        "pages": page_map,
        "manifest": manifest,
        "structured_assets": structured_assets,
        "wiki_items": wiki_items,
        "log_entry": log_entry,
    }


def build_wiki_items(
    *,
    structured_assets: Mapping[str, Any],
    outline_entries: Iterable[dict[str, Any]],
    block_entries: Iterable[dict[str, Any]],
    generated_at: datetime,
    published_items: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    outline_entries_list = [entry for entry in outline_entries if isinstance(entry, dict)]
    block_entries_list = [entry for entry in block_entries if isinstance(entry, dict)]
    existing_items = {
        str(item.get("item_id") or ""): item
        for item in (published_items or [])
        if isinstance(item, dict) and str(item.get("item_id") or "")
    }
    items: list[dict[str, Any]] = []

    for entry in structured_assets.get("glossary") or []:
        if not isinstance(entry, dict):
            continue
        primary = str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip()
        aliases = [str(item).strip() for item in (entry.get("display_aliases") or entry.get("aliases") or []) if str(item).strip()]
        if not primary:
            continue
        evidence = _find_evidence_for_terms(
            block_entries=block_entries_list,
            terms=[primary, *aliases, str(entry.get("primary_term") or "")],
            limit=4,
        )
        item_id = f"term_alias:{_item_slug(primary)}"
        items.append(
            _build_wiki_item(
                item_id=item_id,
                item_type="term_alias",
                canonical_name=primary,
                aliases=aliases,
                summary=f"统一“{primary}”及其常见别名，减少章节生成中的术语漂移。",
                evidence=evidence,
                source_documents=_source_documents_from_evidence(evidence),
                generated_at=generated_at,
                created_by="compiler",
                existing_item=existing_items.get(item_id),
            )
        )

    for card in structured_assets.get("product_cards") or []:
        if not isinstance(card, dict):
            continue
        family_key = str(card.get("product_family") or "").strip()
        title = str(card.get("title") or family_key).strip()
        if not family_key or not title:
            continue
        source_documents = [str(item).strip() for item in (card.get("source_documents") or []) if str(item).strip()]
        evidence = _find_evidence_for_card(
            block_entries=block_entries_list,
            source_documents=source_documents,
            section_types=[
                str(item.get("section_type") or "")
                for item in (card.get("top_section_types") or [])
                if isinstance(item, dict)
            ],
            equipment_types=[
                str(item.get("equipment_type") or "")
                for item in (card.get("top_equipment_types") or [])
                if isinstance(item, dict)
            ],
            fallback_terms=[title, *[str(item) for item in (card.get("aliases") or [])]],
            limit=5,
        )
        item_id = f"product_family:{_slugify(family_key)}"
        items.append(
            _build_wiki_item(
                item_id=item_id,
                item_type="product_family",
                canonical_name=title,
                aliases=[str(item).strip() for item in (card.get("aliases") or []) if str(item).strip()],
                summary=f"围绕 {title} 汇总的产品族/方案族知识卡。",
                evidence=evidence,
                source_documents=source_documents or _source_documents_from_evidence(evidence),
                generated_at=generated_at,
                created_by="compiler",
                existing_item=existing_items.get(item_id),
            )
        )

    for template in structured_assets.get("section_templates") or []:
        if not isinstance(template, dict):
            continue
        section_type = str(template.get("section_type") or "").strip()
        title = str(template.get("title") or section_type).strip()
        if not section_type or not title:
            continue
        source_documents = [str(item).strip() for item in (template.get("source_documents") or []) if str(item).strip()]
        evidence = _find_evidence_for_card(
            block_entries=block_entries_list,
            source_documents=source_documents,
            section_types=[section_type],
            equipment_types=[],
            fallback_terms=[title, *[str(item) for item in (template.get("common_headings") or [])]],
            limit=5,
        )
        item_id = f"section_template:{_slugify(section_type)}"
        items.append(
            _build_wiki_item(
                item_id=item_id,
                item_type="section_template",
                canonical_name=title,
                aliases=[str(item).strip() for item in (template.get("common_headings") or []) if str(item).strip()],
                summary=f"围绕 {title} 归纳出的章节模板。",
                evidence=evidence,
                source_documents=source_documents or _source_documents_from_evidence(evidence),
                generated_at=generated_at,
                created_by="compiler",
                existing_item=existing_items.get(item_id),
            )
        )

    return sorted(items, key=lambda item: (str(item.get("item_type") or ""), str(item.get("item_id") or "")))


def _normalize_term_lexicon(term_lexicon: Mapping[str, Iterable[str]] | None) -> dict[str, tuple[str, ...]]:
    if not term_lexicon:
        return {}
    normalized: dict[str, tuple[str, ...]] = {}
    for key, values in term_lexicon.items():
        raw_values = [str(key or "").strip(), *[str(value or "").strip() for value in values]]
        deduped = _dedupe_keep_order(raw_values)
        if len(deduped) < 2:
            continue
        for value in deduped:
            normalized[value] = tuple(deduped)
    return normalized


def _build_glossary_entries(term_lexicon: Mapping[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    groups: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for group in term_lexicon.values():
        if group in seen:
            continue
        seen.add(group)
        groups.append(group)
    entries: list[dict[str, Any]] = []
    for group in sorted(groups, key=lambda item: (_display_term(_pick_primary_term(item)), len(item)), reverse=False):
        primary = _pick_primary_term(group)
        aliases = [term for term in group if term != primary]
        entries.append(
            {
                "primary_term": primary,
                "display_primary_term": _display_term(primary),
                "aliases": list(aliases),
                "display_aliases": [_display_term(alias) for alias in aliases],
            }
        )
    return entries


def _build_equipment_cards(block_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in block_entries:
        equipment_type = str(entry.get("equipment_type") or "generic").strip()
        if not equipment_type or equipment_type == "generic":
            continue
        grouped[equipment_type].append(entry)
    cards: list[dict[str, Any]] = []
    for equipment_type, entries in sorted(grouped.items()):
        section_types = Counter(str(entry.get("section_type") or "unknown") for entry in entries)
        headings = Counter(_extract_heading_label(entry) for entry in entries if _extract_heading_label(entry))
        source_docs = Counter(str(entry.get("file_name") or "").strip() for entry in entries if str(entry.get("file_name") or "").strip())
        snippets = _extract_representative_snippets(entries)
        cards.append(
            {
                "equipment_type": equipment_type,
                "title": EQUIPMENT_TYPE_LABELS.get(equipment_type, equipment_type.replace("_", " ").title()),
                "entry_count": len(entries),
                "top_section_types": [
                    {
                        "section_type": key,
                        "label": SECTION_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in section_types.most_common(5)
                ],
                "top_headings": [heading for heading, _count in headings.most_common(6)],
                "source_documents": [name for name, _count in source_docs.most_common(4)],
                "representative_snippets": snippets,
            }
        )
    return cards


def _build_product_cards(
    outline_entries: list[dict[str, Any]],
    block_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks_by_sample_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in block_entries:
        sample_id = str(entry.get("sample_id") or "").strip()
        if sample_id:
            blocks_by_sample_id[sample_id].append(entry)

    grouped: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = defaultdict(list)
    for outline_entry in outline_entries:
        sample_id = str(outline_entry.get("sample_id") or "").strip()
        related_blocks = blocks_by_sample_id.get(sample_id, [])
        family_key = _infer_product_family(outline_entry=outline_entry, related_blocks=related_blocks)
        grouped[family_key].append((outline_entry, related_blocks))

    cards: list[dict[str, Any]] = []
    for family_key, items in sorted(grouped.items()):
        definition = PRODUCT_FAMILY_DEFINITIONS.get(family_key, PRODUCT_FAMILY_DEFINITIONS["generic_engineering_solution"])
        related_blocks = [block for _outline_entry, blocks in items for block in blocks]
        equipment_types = Counter(
            str(block.get("equipment_type") or "").strip()
            for block in related_blocks
            if str(block.get("equipment_type") or "").strip() and str(block.get("equipment_type") or "").strip() != "generic"
        )
        section_types = Counter(
            str(block.get("section_type") or "").strip()
            for block in related_blocks
            if str(block.get("section_type") or "").strip() and str(block.get("section_type") or "").strip() != "unknown"
        )
        source_docs = Counter(
            str((outline_entry.get("file_name") or "")).strip()
            for outline_entry, _blocks in items
            if str(outline_entry.get("file_name") or "").strip()
        )
        headings = Counter()
        representative_titles: list[str] = []
        for outline_entry, _blocks in items:
            title = _extract_outline_title(outline_entry)
            if title and title not in representative_titles:
                representative_titles.append(title)
            for heading in outline_entry.get("top_level_titles") or []:
                heading_text = str(heading or "").strip()
                if heading_text and not _is_low_value_heading(heading_text):
                    headings[heading_text] += 1
        cards.append(
            {
                "product_family": family_key,
                "title": str(definition.get("title") or family_key).strip(),
                "aliases": list(definition.get("aliases") or []),
                "entry_count": len(items),
                "top_equipment_types": [
                    {
                        "equipment_type": key,
                        "label": EQUIPMENT_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in equipment_types.most_common(4)
                ],
                "top_section_types": [
                    {
                        "section_type": key,
                        "label": SECTION_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in section_types.most_common(5)
                ],
                "representative_titles": representative_titles[:6],
                "representative_headings": [heading for heading, _count in headings.most_common(6)],
                "source_documents": [name for name, _count in source_docs.most_common(6)],
                "representative_snippets": _extract_representative_snippets(related_blocks),
            }
        )
    return cards


def _build_module_cards(block_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in block_entries:
        searchable_text = _build_module_match_text(entry)
        for module_key, definition in MODULE_CARD_DEFINITIONS.items():
            aliases = [str(item).strip() for item in definition.get("aliases") or [] if str(item).strip()]
            if aliases and any(_text_contains_keyword(searchable_text, alias) for alias in aliases):
                grouped[module_key].append(entry)

    cards: list[dict[str, Any]] = []
    for module_key, entries in sorted(grouped.items()):
        definition = MODULE_CARD_DEFINITIONS[module_key]
        equipment_types = Counter(
            str(entry.get("equipment_type") or "").strip()
            for entry in entries
            if str(entry.get("equipment_type") or "").strip() and str(entry.get("equipment_type") or "").strip() != "generic"
        )
        section_types = Counter(
            str(entry.get("section_type") or "").strip()
            for entry in entries
            if str(entry.get("section_type") or "").strip() and str(entry.get("section_type") or "").strip() != "unknown"
        )
        headings = Counter(_extract_heading_label(entry) for entry in entries if _extract_heading_label(entry))
        source_docs = Counter(str(entry.get("file_name") or "").strip() for entry in entries if str(entry.get("file_name") or "").strip())
        cards.append(
            {
                "module_key": module_key,
                "title": str(definition.get("title") or module_key).strip(),
                "aliases": list(definition.get("aliases") or []),
                "entry_count": len(entries),
                "top_equipment_types": [
                    {
                        "equipment_type": key,
                        "label": EQUIPMENT_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in equipment_types.most_common(4)
                ],
                "top_section_types": [
                    {
                        "section_type": key,
                        "label": SECTION_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in section_types.most_common(4)
                ],
                "top_headings": [heading for heading, _count in headings.most_common(6)],
                "source_documents": [name for name, _count in source_docs.most_common(5)],
                "representative_snippets": _extract_representative_snippets(entries),
            }
        )
    return cards


def _build_interface_cards(block_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in block_entries:
        section_type = str(entry.get("section_type") or "unknown").strip()
        if section_type not in INTERFACE_SECTION_TYPES:
            continue
        grouped[section_type].append(entry)
    cards: list[dict[str, Any]] = []
    for section_type, entries in sorted(grouped.items()):
        headings = Counter(_extract_heading_label(entry) for entry in entries if _extract_heading_label(entry))
        equipment_types = Counter(str(entry.get("equipment_type") or "generic") for entry in entries)
        source_docs = Counter(str(entry.get("file_name") or "").strip() for entry in entries if str(entry.get("file_name") or "").strip())
        snippets = _extract_representative_snippets(entries)
        cards.append(
            {
                "section_type": section_type,
                "title": SECTION_TYPE_LABELS.get(section_type, section_type),
                "entry_count": len(entries),
                "top_headings": [heading for heading, _count in headings.most_common(6)],
                "top_equipment_types": [
                    {
                        "equipment_type": key,
                        "label": EQUIPMENT_TYPE_LABELS.get(key, key),
                        "count": count,
                    }
                    for key, count in equipment_types.most_common(4)
                ],
                "source_documents": [name for name, _count in source_docs.most_common(4)],
                "representative_snippets": snippets,
            }
        )
    return cards


def _build_section_templates(
    outline_entries: list[dict[str, Any]],
    block_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in block_entries:
        section_type = str(entry.get("section_type") or "unknown").strip()
        if not section_type or section_type == "unknown":
            continue
        grouped[section_type].append(entry)
    templates: list[dict[str, Any]] = []
    for section_type, entries in sorted(grouped.items()):
        headings = Counter(_extract_heading_label(entry) for entry in entries if _extract_heading_label(entry))
        source_docs = Counter(str(entry.get("file_name") or "").strip() for entry in entries if str(entry.get("file_name") or "").strip())
        templates.append(
            {
                "section_type": section_type,
                "title": SECTION_TYPE_LABELS.get(section_type, section_type),
                "entry_count": len(entries),
                "common_headings": [heading for heading, _count in headings.most_common(6)],
                "source_documents": [name for name, _count in source_docs.most_common(4)],
                "guidance": list(SECTION_TEMPLATE_GUIDANCE.get(section_type, _default_template_guidance(section_type))),
                "outline_examples": _find_outline_examples(outline_entries, entries),
            }
        )
    return templates


def _build_glossary_page(*, glossary_entries: list[dict[str, Any]]) -> WikiPage:
    lines = ["# 术语表", "", "## 目标", "", "统一同义词、简称和推荐叫法，减少章节生成中的术语漂移。", "", "## 词表", ""]
    for entry in glossary_entries:
        aliases = " / ".join(entry["display_aliases"]) if entry["display_aliases"] else "-"
        lines.append(f"- `{entry['display_primary_term']}`: {aliases}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path="glossary.md",
        title="术语表",
        category="glossary",
        summary="统一术语、简称和推荐主叫法。",
        content=content,
    )


def _build_product_page(*, card: dict[str, Any]) -> WikiPage:
    lines = [f"# {card['title']}", "", "## 概要", "", f"- 相关方案文档数: {card['entry_count']}", ""]
    aliases = [str(item).strip() for item in (card.get("aliases") or []) if str(item).strip()]
    if aliases:
        lines.extend(["## 常见别名", ""])
        for alias in aliases:
            lines.append(f"- {alias}")
        lines.append("")
    lines.extend(["## 高频设备类型", ""])
    for item in card["top_equipment_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 高频章节类型", ""])
    for item in card["top_section_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 代表性方案标题", ""])
    for title in card["representative_titles"]:
        lines.append(f"- {title}")
    lines.extend(["", "## 高频章节标题", ""])
    for heading in card["representative_headings"]:
        lines.append(f"- {heading}")
    lines.extend(["", "## 代表性来源文档", ""])
    for name in card["source_documents"]:
        lines.append(f"- {name}")
    lines.extend(["", "## 代表性内容片段", ""])
    for snippet in card["representative_snippets"]:
        lines.append(f"- {snippet}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path=f"products/{_slugify(card['product_family'])}.md",
        title=card["title"],
        category="products",
        summary=f"围绕 {card['title']} 汇总的产品族/方案族知识卡。",
        content=content,
    )


def _build_module_page(*, card: dict[str, Any]) -> WikiPage:
    lines = [f"# {card['title']}", "", "## 概要", "", f"- 相关片段数: {card['entry_count']}", ""]
    aliases = [str(item).strip() for item in (card.get("aliases") or []) if str(item).strip()]
    if aliases:
        lines.extend(["## 常见别名", ""])
        for alias in aliases:
            lines.append(f"- {alias}")
        lines.append("")
    lines.extend(["## 高频设备类型", ""])
    for item in card["top_equipment_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 高频章节类型", ""])
    for item in card["top_section_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 代表性标题", ""])
    for heading in card["top_headings"]:
        lines.append(f"- {heading}")
    lines.extend(["", "## 代表性来源文档", ""])
    for name in card["source_documents"]:
        lines.append(f"- {name}")
    lines.extend(["", "## 代表性内容片段", ""])
    for snippet in card["representative_snippets"]:
        lines.append(f"- {snippet}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path=f"modules/{_slugify(card['module_key'])}.md",
        title=card["title"],
        category="modules",
        summary=f"围绕 {card['title']} 汇总的模块知识卡。",
        content=content,
    )


def _build_equipment_page(*, card: dict[str, Any]) -> WikiPage:
    lines = [f"# {card['title']}", "", "## 概要", "", f"- 相关片段数: {card['entry_count']}", ""]
    lines.extend(["## 高频章节类型", ""])
    for item in card["top_section_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 代表性标题", ""])
    for heading in card["top_headings"]:
        lines.append(f"- {heading}")
    lines.extend(["", "## 代表性来源文档", ""])
    for name in card["source_documents"]:
        lines.append(f"- {name}")
    lines.extend(["", "## 代表性内容片段", ""])
    for snippet in card["representative_snippets"]:
        lines.append(f"- {snippet}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path=f"equipment/{_slugify(card['equipment_type'])}.md",
        title=card["title"],
        category="equipment",
        summary=f"围绕 {card['title']} 汇总的设备知识卡。",
        content=content,
    )


def _build_interface_page(*, card: dict[str, Any]) -> WikiPage:
    lines = [f"# {card['title']}", "", "## 概要", "", f"- 相关片段数: {card['entry_count']}", ""]
    lines.extend(["## 高频设备类型", ""])
    for item in card["top_equipment_types"]:
        lines.append(f"- `{item['label']}`: {item['count']}")
    lines.extend(["", "## 代表性标题", ""])
    for heading in card["top_headings"]:
        lines.append(f"- {heading}")
    lines.extend(["", "## 代表性来源文档", ""])
    for name in card["source_documents"]:
        lines.append(f"- {name}")
    lines.extend(["", "## 代表性内容片段", ""])
    for snippet in card["representative_snippets"]:
        lines.append(f"- {snippet}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path=f"interfaces/{_slugify(card['section_type'])}.md",
        title=card["title"],
        category="interfaces",
        summary=f"围绕 {card['title']} 汇总的接口/联锁知识卡。",
        content=content,
    )


def _build_template_page(*, template: dict[str, Any]) -> WikiPage:
    lines = [f"# {template['title']} 模板", "", "## 推荐覆盖点", ""]
    for item in template["guidance"]:
        lines.append(f"- {item}")
    lines.extend(["", "## 常见标题", ""])
    for heading in template["common_headings"]:
        lines.append(f"- {heading}")
    lines.extend(["", "## 代表性来源文档", ""])
    for name in template["source_documents"]:
        lines.append(f"- {name}")
    lines.extend(["", "## Outline 例子", ""])
    for example in template["outline_examples"]:
        lines.append(f"- {example}")
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path=f"templates/{_slugify(template['section_type'])}.md",
        title=f"{template['title']} 模板",
        category="templates",
        summary=f"围绕 {template['title']} 归纳出的章节模板。",
        content=content,
    )


def _build_forbidden_phrases_page(*, forbidden_phrases: list[dict[str, str]]) -> WikiPage:
    lines = ["# 禁用表述", "", "## 原则", "", "客户稿优先使用可验证、可追溯、边界清晰的技术表达，避免营销性或绝对化措辞。", "", "## 禁用表达", ""]
    for item in forbidden_phrases:
        lines.append(
            f"- `{item['phrase']}`: {item['reason']}；建议改为 `{item['preferred']}`"
        )
    content = "\n".join(lines).strip() + "\n"
    return WikiPage(
        path="policies/forbidden-phrases.md",
        title="禁用表述",
        category="policies",
        summary="沉淀客户稿应避免的营销性和绝对化表达。",
        content=content,
    )


def _build_index_page(
    *,
    pages: list[WikiPage],
    manifest: dict[str, Any],
    structured_assets: dict[str, Any],
) -> str:
    lines = [
        "# AI Wiki Index",
        "",
        "## 概要",
        "",
        f"- 生成时间: `{manifest['generated_at']}`",
        f"- 原始 outline 文档数: `{manifest['source_counts']['outline_entries']}`",
        f"- 原始 block 片段数: `{manifest['source_counts']['block_entries']}`",
        f"- 术语组数: `{manifest['source_counts']['term_groups']}`",
        "",
        "## 页面目录",
        "",
    ]
    for category in ("glossary", "equipment", "interfaces", "templates", "policies"):
        category_pages = [page for page in pages if page.category == category]
        if not category_pages:
            continue
        lines.append(f"### {category}")
        lines.append("")
        for page in category_pages:
            lines.append(f"- [{page.title}]({page.path}): {page.summary}")
        lines.append("")
    for category in ("products", "modules"):
        category_pages = [page for page in pages if page.category == category]
        if not category_pages:
            continue
        lines.append(f"### {category}")
        lines.append("")
        for page in category_pages:
            lines.append(f"- [{page.title}]({page.path}): {page.summary}")
        lines.append("")
    lines.extend(
        [
            "## 结构化资产",
            "",
            f"- `glossary.json`: {len(structured_assets['glossary'])} 组术语",
            f"- `product_cards.json`: {len(structured_assets['product_cards'])} 张产品族卡",
            f"- `module_cards.json`: {len(structured_assets['module_cards'])} 张模块卡",
            f"- `equipment_cards.json`: {len(structured_assets['equipment_cards'])} 张设备卡",
            f"- `interface_cards.json`: {len(structured_assets['interface_cards'])} 张接口卡",
            f"- `section_templates.json`: {len(structured_assets['section_templates'])} 张章节模板",
            f"- `forbidden_phrases.json`: {len(structured_assets['forbidden_phrases'])} 条禁用表述",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _build_log_entry(*, generated_at: datetime, manifest: dict[str, Any]) -> str:
    timestamp = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    return (
        f"## [{timestamp}] compile | AI Wiki knowledge compilation\n"
        f"- outline_entries: {manifest['source_counts']['outline_entries']}\n"
        f"- block_entries: {manifest['source_counts']['block_entries']}\n"
        f"- glossary_groups: {manifest['source_counts']['term_groups']}\n"
        f"- pages: {len(manifest['pages']) + 1}\n"
    )


def _build_wiki_item(
    *,
    item_id: str,
    item_type: str,
    canonical_name: str,
    aliases: list[str],
    summary: str,
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    generated_at: datetime,
    created_by: str,
    existing_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = evaluate_wiki_item_quality(
        item_type=item_type,
        canonical_name=canonical_name,
        aliases=aliases,
        evidence=evidence,
        source_documents=source_documents,
        existing_item=existing_item,
    )
    return {
        "item_id": item_id,
        "item_type": item_type,
        "canonical_name": canonical_name,
        "aliases": _dedupe_keep_order(aliases),
        "summary": summary,
        "source_documents": _dedupe_keep_order(source_documents),
        "evidence": evidence,
        "quality_score": quality.quality_score,
        "quality_flags": quality.quality_flags,
        "status": quality.status,
        "created_by": created_by,
        "updated_at": generated_at.astimezone(timezone.utc).isoformat(),
    }


def _quality_flags_for_item(
    *,
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    existing_item: dict[str, Any] | None,
    canonical_name: str,
    aliases: list[str],
) -> list[str]:
    return evaluate_wiki_item_quality(
        item_type="wiki_item",
        canonical_name=canonical_name,
        aliases=aliases,
        evidence=evidence,
        source_documents=source_documents,
        existing_item=existing_item,
    ).quality_flags


def _quality_score_for_item(
    *,
    item_type: str,
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    quality_flags: list[str],
) -> float:
    return quality_score_for_item(
        item_type=item_type,
        evidence=evidence,
        source_documents=source_documents,
        quality_flags=quality_flags,
    )


def _status_for_item(*, quality_score: float, quality_flags: list[str]) -> str:
    return status_for_quality(quality_score=quality_score, quality_flags=quality_flags)


def _published_item_conflicts(*, existing_item: dict[str, Any], canonical_name: str, aliases: list[str]) -> bool:
    existing_name = str(existing_item.get("canonical_name") or "").strip()
    if existing_name and existing_name != canonical_name:
        return True
    existing_aliases = {str(item).strip() for item in (existing_item.get("aliases") or []) if str(item).strip()}
    new_aliases = {str(item).strip() for item in aliases if str(item).strip()}
    return bool(existing_aliases and new_aliases and existing_aliases != new_aliases)


def _evidence_has_required_fields(item: dict[str, Any]) -> bool:
    return is_publishable_evidence_item(item)


def _find_evidence_for_terms(
    *,
    block_entries: list[dict[str, Any]],
    terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    normalized_terms = [str(term).strip().casefold() for term in terms if str(term).strip()]
    evidence: list[dict[str, Any]] = []
    for entry in block_entries:
        text = " ".join(
            str(entry.get(key) or "")
            for key in ("heading_path", "source_heading", "section_summary", "content", "semantic_retrieval_text")
        ).casefold()
        if not any(term and term in text for term in normalized_terms):
            continue
        item = _evidence_from_entry(entry)
        if item:
            evidence.append(item)
        if len(evidence) >= limit:
            break
    return _dedupe_evidence(evidence)


def _find_evidence_for_card(
    *,
    block_entries: list[dict[str, Any]],
    source_documents: list[str],
    section_types: list[str],
    equipment_types: list[str],
    fallback_terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    source_document_set = {str(item).strip() for item in source_documents if str(item).strip()}
    section_type_set = {str(item).strip().lower() for item in section_types if str(item).strip()}
    equipment_type_set = {str(item).strip().lower() for item in equipment_types if str(item).strip()}
    fallback_terms_normalized = [str(term).strip().casefold() for term in fallback_terms if str(term).strip()]
    scored_entries: list[tuple[int, dict[str, Any]]] = []
    for entry in block_entries:
        score = 0
        file_name = str(entry.get("file_name") or "").strip()
        if source_document_set and file_name in source_document_set:
            score += 4
        if section_type_set and str(entry.get("section_type") or "").strip().lower() in section_type_set:
            score += 3
        if equipment_type_set and str(entry.get("equipment_type") or "").strip().lower() in equipment_type_set:
            score += 2
        if fallback_terms_normalized:
            text = " ".join(str(entry.get(key) or "") for key in ("heading_path", "section_summary", "content")).casefold()
            if any(term and term in text for term in fallback_terms_normalized):
                score += 1
        if score <= 0:
            continue
        scored_entries.append((score, entry))
    scored_entries.sort(key=lambda item: item[0], reverse=True)
    return _dedupe_evidence(
        item
        for _score, entry in scored_entries[: limit * 2]
        if (item := _evidence_from_entry(entry))
    )[:limit]


def _evidence_from_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    quote = _truncate_text(str(entry.get("section_summary") or entry.get("content") or "").strip(), limit=260)
    heading_path = str(entry.get("heading_path") or entry.get("source_heading") or entry.get("section_path") or "").strip()
    sample_id = str(entry.get("sample_id") or "").strip()
    raw_document_id = str(entry.get("raw_document_id") or entry.get("source_doc_id") or sample_id or entry.get("file_name") or "").strip()
    source_section_id = str(entry.get("source_section_id") or "").strip()
    if not any((quote, heading_path, sample_id, raw_document_id, source_section_id)):
        return None
    return {
        "sample_id": sample_id,
        "raw_document_id": raw_document_id,
        "source_section_id": source_section_id,
        "heading_path": heading_path,
        "source_document": str(entry.get("file_name") or "").strip(),
        "evidence_quote": quote,
    }


def _source_documents_from_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    return _dedupe_keep_order(str(item.get("source_document") or "").strip() for item in evidence if str(item.get("source_document") or "").strip())


def _dedupe_evidence(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in values:
        signature = (
            str(item.get("sample_id") or ""),
            str(item.get("raw_document_id") or ""),
            str(item.get("source_section_id") or ""),
            str(item.get("evidence_quote") or "")[:120],
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def _extract_heading_label(entry: dict[str, Any]) -> str:
    label = str(entry.get("source_heading") or entry.get("heading_path") or entry.get("section_path") or "").strip()
    return "" if _is_low_value_heading(label) else label


def _extract_outline_title(outline_entry: dict[str, Any]) -> str:
    for value in (outline_entry.get("document_title"), outline_entry.get("file_name"), outline_entry.get("sample_id")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_representative_snippets(entries: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for raw_value in (entry.get("section_summary"), entry.get("content")):
            text = _truncate_text(str(raw_value or "").strip(), limit=140)
            if not text or text in seen:
                continue
            snippets.append(text)
            seen.add(text)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _find_outline_examples(outline_entries: list[dict[str, Any]], block_entries: list[dict[str, Any]], *, limit: int = 4) -> list[str]:
    wanted_headings = {
        _extract_heading_label(entry)
        for entry in block_entries
        if _extract_heading_label(entry)
    }
    examples: list[str] = []
    seen: set[str] = set()
    for outline_entry in outline_entries:
        for section in _flatten_sections(outline_entry.get("section_catalog") or []):
            heading = str(section.get("title") or section.get("source_heading") or section.get("heading_path") or "").strip()
            if not heading or heading not in wanted_headings or heading in seen:
                continue
            examples.append(heading)
            seen.add(heading)
            if len(examples) >= limit:
                return examples
    return examples


def _infer_product_family(
    *,
    outline_entry: dict[str, Any],
    related_blocks: list[dict[str, Any]],
) -> str:
    title_text, heading_text = _build_product_family_text(outline_entry=outline_entry)
    equipment_counts = Counter(
        str(block.get("equipment_type") or "").strip()
        for block in related_blocks
        if str(block.get("equipment_type") or "").strip() and str(block.get("equipment_type") or "").strip() != "generic"
    )
    top_equipment = equipment_counts.most_common(1)[0][0] if equipment_counts else ""

    if any(keyword in title_text for keyword in ("巡检", "维保", "检修", "maintenance")):
        return "maintenance_service"
    if "水电阻" in title_text or "水电阻" in heading_text:
        return "water_resistance_starter"
    if "lci" in title_text or top_equipment == "lci":
        return "lci_system"
    if "永磁" in title_text and ("变频" in title_text or top_equipment in {"vfd", "motor"}):
        return "permanent_magnet_vfd_upgrade"
    if any(keyword in title_text for keyword in ("软起", "软启动", "软起动")) and "变频" not in title_text:
        return "soft_starter_system"
    if top_equipment == "soft_starter":
        return "soft_starter_system"
    if top_equipment == "vfd" or "变频" in title_text or "变频" in heading_text or "vfd" in title_text:
        return "vfd_system"
    return "generic_engineering_solution"


def _build_product_family_text(
    *,
    outline_entry: dict[str, Any],
 ) -> tuple[str, str]:
    title_values = [
        str(outline_entry.get("document_title") or "").strip(),
        str(outline_entry.get("file_name") or "").strip(),
    ]
    heading_values = [
        str(item).strip()
        for item in (outline_entry.get("top_level_titles") or [])[:8]
        if str(item).strip()
    ]
    return " ".join(title_values).casefold(), " ".join(heading_values).casefold()


def _build_module_match_text(entry: dict[str, Any]) -> str:
    values = [
        str(entry.get("source_heading") or "").strip(),
        str(entry.get("heading_path") or "").strip(),
        str(entry.get("section_summary") or "").strip(),
        str(entry.get("content") or "").strip(),
    ]
    return " ".join(value for value in values if value).casefold()


def _text_contains_keyword(text: str, keyword: str) -> bool:
    normalized_text = str(text or "").casefold()
    normalized_keyword = str(keyword or "").strip().casefold()
    return bool(normalized_text and normalized_keyword and normalized_keyword in normalized_text)


def _flatten_sections(sections: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        flattened.append(section)
        children = section.get("children") or []
        if children:
            flattened.extend(_flatten_sections(children))
    return flattened


def _default_template_guidance(section_type: str) -> list[str]:
    label = SECTION_TYPE_LABELS.get(section_type, section_type)
    return [
        f"围绕 `{label}` 的核心边界写清楚主题。",
        "优先写可验证的参数、条件、接口和动作逻辑。",
        "避免泛化复述，尽量保持结构和术语稳定。",
    ]


def _pick_primary_term(group: tuple[str, ...]) -> str:
    chinese_terms = [term for term in group if _contains_cjk(term)]
    if chinese_terms:
        return sorted(chinese_terms, key=lambda item: (-len(item), item))[0]
    return sorted(group, key=lambda item: (-len(item), item))[0]


def _display_term(term: str) -> str:
    normalized = str(term or "").strip()
    if not normalized:
        return normalized
    if normalized.isascii():
        return normalized.upper()
    return normalized


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _slugify(text: str) -> str:
    normalized = str(text or "").strip().lower().replace("_", "-")
    normalized = SLUG_PATTERN.sub("-", normalized)
    normalized = normalized.strip("-")
    return normalized or "page"


def _item_slug(text: str) -> str:
    slug = _slugify(text)
    if slug != "page":
        return slug
    normalized = str(text or "").strip()
    if not normalized:
        return slug
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _truncate_text(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)] + "..."


def _is_low_value_heading(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return True
    compact = normalized.replace(" ", "")
    if compact in {"目录", "说明", "contents"}:
        return True
    if any(fragment in compact for fragment in LOW_VALUE_HEADING_SUBSTRINGS):
        return True
    if compact.endswith("说明") and len(compact) <= 4:
        return True
    if compact.count(".") == 0 and compact.count("、") == 0 and compact.isascii() is False:
        chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in compact)
        non_chinese_chars = sum(not ("\u4e00" <= char <= "\u9fff") for char in compact)
        if 2 <= chinese_chars <= 4 and non_chinese_chars == 0:
            return True
    return False
