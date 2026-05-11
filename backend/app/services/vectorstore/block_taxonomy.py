from __future__ import annotations

import re
from typing import Any

from app.services.domain.taxonomy_registry import get_taxonomy_registry
from app.services.parsing.formula_candidates import has_garbled_formula_text, is_formula_like_text


_HEADING_NUMBER_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)*)")
_FILE_LIKE_HEADING_PATTERN = re.compile(r"\.(?:doc|docx|pdf|ppt|pptx|xls|xlsx)\b", re.IGNORECASE)


def is_commercial_manual_section_text(*texts: str) -> bool:
    return get_taxonomy_registry().section_types.is_commercial_manual_text(*texts)


def classify_block_taxonomy(
    *,
    content: str,
    heading_path: str | None,
    chunk_type: str,
    front_matter: bool = False,
    needs_asset_lookup: bool = False,
) -> dict[str, str]:
    content_text = str(content or "")[:2000]
    return get_taxonomy_registry().infer(
        content_text,
        heading_path=heading_path,
        chunk_type=chunk_type,
        front_matter=front_matter,
        needs_asset_lookup=needs_asset_lookup,
        formula_like=has_garbled_formula_text(content_text) or is_formula_like_text(content_text),
    )


def infer_target_taxonomy(section: dict[str, Any]) -> dict[str, Any]:
    title = str(section.get("title") or "")
    purpose = str(section.get("purpose") or section.get("description") or "")
    keywords = " ".join(str(item) for item in (section.get("keywords") or []) if item)
    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    prefer_table = bool(expected_types & {"table", "parameter"})
    taxonomy = classify_block_taxonomy(
        content=" ".join(part for part in [purpose, keywords] if part),
        heading_path=title,
        chunk_type="TABLE" if prefer_table else "PLAIN",
        front_matter=False,
        needs_asset_lookup=bool(section.get("asset_required")),
    )
    explicit_section_type = str(section.get("target_section_type") or "").strip().lower()
    explicit_equipment_type = str(section.get("target_equipment_type") or "").strip().lower()
    if explicit_section_type:
        taxonomy["section_type"] = explicit_section_type
    if explicit_equipment_type:
        taxonomy["equipment_type"] = explicit_equipment_type
    hint_text = f"{title}\n{purpose}\n{keywords}".casefold()
    if prefer_table and taxonomy["section_type"] == "supply_scope":
        if any(keyword.casefold() in hint_text for keyword in ("清单", "设备", "bom", "物料")):
            taxonomy["section_type"] = "bom_or_supply_list"

    if taxonomy["section_type"] == "commercial_manual_only":
        preferred_content_forms = {"narrative", "bom_table"}
    else:
        preferred_content_forms = {"narrative"}
    if taxonomy["section_type"] in {"bom_or_supply_list", "supply_scope"}:
        preferred_content_forms.add("bom_table")
    if prefer_table and taxonomy["section_type"] != "commercial_manual_only":
        preferred_content_forms.add("parameter_table")
    if bool(expected_types & {"interface", "communication"}):
        preferred_content_forms.add("interface_table")
    if bool(expected_types & {"figure", "diagram"}):
        preferred_content_forms.add("figure")
    if bool(expected_types & {"formula", "equation"}):
        preferred_content_forms.add("formula")

    return {
        **taxonomy,
        "preferred_content_forms": preferred_content_forms,
        "support_content_forms": support_content_forms(taxonomy["section_type"]),
        "related_section_types": related_section_types(taxonomy["section_type"]),
    }


def extract_taxonomy_hints(*texts: str) -> list[str]:
    return get_taxonomy_registry().extract_hints(*texts)


def related_section_types(section_type: str) -> set[str]:
    return get_taxonomy_registry().section_types.related_section_types(section_type)


def support_content_forms(section_type: str) -> set[str]:
    return get_taxonomy_registry().section_types.support_content_forms(section_type)


def heading_focus_adjustment(*, target_section_type: str, heading_text: str) -> tuple[float, list[str]]:
    registry = get_taxonomy_registry()
    return registry.section_types.heading_focus_adjustment(
        target_section_type=target_section_type,
        heading_text=heading_text,
        synonyms=registry.synonyms,
    )


def extract_heading_family(heading_text: str) -> tuple[str, ...]:
    match = _HEADING_NUMBER_PATTERN.match(str(heading_text or ""))
    if not match:
        return ()
    return tuple(part for part in match.group(1).split(".") if part)


def heading_family_similarity(anchor_heading: str, candidate_heading: str) -> float:
    anchor_family = extract_heading_family(anchor_heading)
    candidate_family = extract_heading_family(candidate_heading)
    if not anchor_family or not candidate_family:
        return 0.0
    if anchor_family[0] != candidate_family[0]:
        return 0.0
    if len(anchor_family) >= 2 and len(candidate_family) >= 2 and anchor_family[:2] == candidate_family[:2]:
        return 0.2
    return 0.12


def heading_looks_like_document_title(heading_text: str) -> bool:
    text = str(heading_text or "").strip()
    if not text:
        return False
    if _FILE_LIKE_HEADING_PATTERN.search(text):
        return True
    lowered = text.casefold()
    return lowered in {"目录", "目 录", "contents"}


def content_form_is_table(content_form: str) -> bool:
    return content_form in {"parameter_table", "bom_table", "interface_table", "protection_table"}
