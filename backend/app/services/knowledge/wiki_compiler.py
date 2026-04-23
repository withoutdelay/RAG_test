from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Mapping


INTERFACE_SECTION_TYPES = {
    "communication_interface",
    "protection_interlock",
    "control_logic",
}
EQUIPMENT_TYPE_LABELS = {
    "vfd": "高压变频器",
    "soft_starter": "高压软起动装置",
    "motor": "电机",
    "transformer": "变压器",
    "switchgear": "开关柜",
    "fan_blower": "风机/鼓风机",
    "compressor": "压缩机",
    "cooling_system": "冷却系统",
    "lci": "LCI 变频软起系统",
    "dcs_plc_interface": "DCS/PLC 接口",
}
SECTION_TYPE_LABELS = {
    "vfd_spec": "变频器规格",
    "starter_spec": "软起动规格",
    "motor_spec": "电机参数",
    "transformer_spec": "变压器参数",
    "communication_interface": "通讯接口",
    "protection_interlock": "联锁保护",
    "control_logic": "控制逻辑",
    "main_circuit_scheme": "主回路方案",
    "overall_solution": "总体方案",
    "site_conditions": "现场条件",
    "design_basis": "设计依据",
    "service_support": "服务支持",
    "commissioning_acceptance": "调试验收",
    "bom_or_supply_list": "供货清单",
    "supply_scope": "供货范围",
    "cabinet_layout": "柜体布置",
    "commercial_manual_only": "商务说明",
}
PRODUCT_FAMILY_DEFINITIONS = {
    "vfd_system": {
        "title": "高压变频器方案族",
        "aliases": ["高压变频器", "中压变频器", "变频装置", "变频柜", "VFD"],
    },
    "lci_system": {
        "title": "LCI 变频软起方案族",
        "aliases": ["LCI", "LCI 软起", "变频软起"],
    },
    "soft_starter_system": {
        "title": "高压软起动方案族",
        "aliases": ["软起动", "软启动", "高压软起动装置"],
    },
    "water_resistance_starter": {
        "title": "水电阻起动柜方案族",
        "aliases": ["水电阻柜", "水电阻起动柜"],
    },
    "permanent_magnet_vfd_upgrade": {
        "title": "永磁电机变频改造方案族",
        "aliases": ["永磁", "永磁电机", "节能改造", "变频改造"],
    },
    "maintenance_service": {
        "title": "运维巡检方案族",
        "aliases": ["维保", "巡检", "检修", "维护"],
    },
    "generic_engineering_solution": {
        "title": "通用工程方案族",
        "aliases": ["工程方案", "技术方案", "系统方案"],
    },
}
MODULE_CARD_DEFINITIONS = {
    "power-cell": {
        "title": "功率单元",
        "aliases": ["功率单元", "功率模块", "逆变单元", "整流单元", "H桥"],
    },
    "control-cabinet": {
        "title": "控制柜",
        "aliases": ["控制柜", "控制单元", "PLC柜"],
    },
    "transformer-cabinet": {
        "title": "变压器柜",
        "aliases": ["变压器柜", "隔离变压器", "整流变压器", "移相变压器"],
    },
    "precharge-cabinet": {
        "title": "预充柜",
        "aliases": ["预充柜"],
    },
    "power-cabinet": {
        "title": "功率柜",
        "aliases": ["功率柜"],
    },
    "bypass-cabinet": {
        "title": "旁路柜",
        "aliases": ["旁路柜", "旁路开关柜"],
    },
    "output-reactor-cabinet": {
        "title": "输出电抗器柜",
        "aliases": ["输出电抗器柜"],
    },
    "water-resistance-cabinet": {
        "title": "水电阻柜",
        "aliases": ["水电阻柜"],
    },
    "thyristor-stack": {
        "title": "晶闸管",
        "aliases": ["晶闸管"],
    },
    "igbt-stack": {
        "title": "IGBT",
        "aliases": ["IGBT"],
    },
    "cooling-fan": {
        "title": "冷却风机",
        "aliases": ["冷却风机"],
    },
}
SECTION_TEMPLATE_GUIDANCE = {
    "communication_interface": [
        "说明 DCS/PLC/现场设备之间的接口边界。",
        "给出 DI/DO、AI/AO、通讯协议和关键联锁信号。",
        "说明信号方向、责任边界和异常处理方式。",
    ],
    "protection_interlock": [
        "说明启动、停机、报警、跳闸和旁路条件。",
        "给出关键联锁链路与故障响应逻辑。",
        "避免用营销语言替代动作条件和闭锁关系。",
    ],
    "control_logic": [
        "按阶段说明控制流程、状态切换和关键判断条件。",
        "明确本地控制、远方控制和自动控制的边界。",
        "补充异常状态的保护与恢复路径。",
    ],
    "main_circuit_scheme": [
        "说明电力主回路、隔离、旁路和切换路径。",
        "突出输入输出变压器、功率单元和电机连接方式。",
        "优先使用系统图或一次图辅助说明。",
    ],
}
FORBIDDEN_PHRASE_SEEDS = [
    {"phrase": "我公司", "reason": "客户稿中口径不稳，容易暴露供应方视角", "preferred": "本方案 / 本系统 / 本装置"},
    {"phrase": "我们团队经验证明", "reason": "主观营销口径，不适合作为技术依据", "preferred": "根据既有项目经验 / 按设计边界说明"},
    {"phrase": "国内领先", "reason": "缺乏可验证依据", "preferred": "按参数、标准、功能事实描述"},
    {"phrase": "国际先进", "reason": "泛化宣传语，无法校验", "preferred": "列出标准、指标和配置事实"},
    {"phrase": "绝对满足", "reason": "绝对化表达存在风险", "preferred": "在给定边界条件下满足"},
    {"phrase": "唯一方案", "reason": "过度结论化", "preferred": "推荐方案 / 标准配置 / 备选方案"},
]
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
        "log_entry": log_entry,
    }


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
