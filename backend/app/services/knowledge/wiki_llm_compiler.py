from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from app.config import Settings, get_settings
from app.services.llm.client import LLMClient, LLMInputImage, LLMRequest, TaskType
from app.services.knowledge.wiki_quality import (
    is_blocking_quality_flag,
    is_publishable_evidence_item,
    normalize_quality_flags,
)


ALLOWED_LLM_WIKI_ITEM_TYPES = {
    "product_family",
    "section_template",
    "term_alias",
    "asset_type_rule",
}
VISUAL_AUDIT_TYPES = {
    "engineering_figure",
    "single_line_diagram",
    "main_circuit_topology",
    "layout_drawing",
    "cabinet_outline",
    "product_photo",
    "table_image",
    "text_fragment",
    "asset_fragment",
    "page_furniture",
    "unknown",
}
LLM_ITEM_ID_PREFIX = "llm"
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")

WIKI_LLM_COMPILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "insufficient_evidence"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "enum": sorted(ALLOWED_LLM_WIKI_ITEM_TYPES),
                    },
                    "canonical_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "source_documents": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sample_id": {"type": "string"},
                                "raw_document_id": {"type": "string"},
                                "source_section_id": {"type": "string"},
                                "heading_path": {"type": "string"},
                                "source_document": {"type": "string"},
                                "evidence_quote": {"type": "string"},
                                "asset_id": {"type": "string"},
                            },
                            "required": [
                                "sample_id",
                                "raw_document_id",
                                "source_section_id",
                                "heading_path",
                                "source_document",
                                "evidence_quote",
                                "asset_id",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "asset_type_label": {
                        "type": "string",
                        "enum": ["", *sorted(VISUAL_AUDIT_TYPES)],
                    },
                },
                "required": [
                    "item_type",
                    "canonical_name",
                    "aliases",
                    "summary",
                    "source_documents",
                    "evidence",
                    "confidence",
                    "reason",
                    "asset_type_label",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "items"],
    "additionalProperties": False,
}


async def compile_llm_wiki_candidates(
    *,
    wiki_items: Iterable[dict[str, Any]],
    structured_assets: Mapping[str, Any] | None,
    outline_entries: Iterable[dict[str, Any]],
    block_entries: Iterable[dict[str, Any]],
    generated_at: datetime | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    if enabled is None:
        enabled = bool(resolved_settings.wiki_llm_compile_enabled)
    if not enabled:
        return _result(status="skipped", items=[], summary={"reason": "wiki LLM compiler disabled"})

    normalized_generated_at = generated_at or datetime.now(timezone.utc)
    wiki_items_list = [item for item in wiki_items if isinstance(item, dict)]
    outline_entries_list = [item for item in outline_entries if isinstance(item, dict)]
    block_entries_list = [item for item in block_entries if isinstance(item, dict)]
    seed_payload = _build_seed_payload(
        wiki_items=wiki_items_list,
        structured_assets=structured_assets or {},
        outline_entries=outline_entries_list,
        block_entries=block_entries_list,
        max_seed_items=resolved_settings.wiki_llm_compile_max_seed_items,
        max_evidence_per_item=resolved_settings.wiki_llm_compile_max_evidence_per_item,
    )
    if not seed_payload["seed_items"] and not seed_payload["asset_candidates"]:
        return _result(
            status="insufficient_evidence",
            items=[],
            summary={"reason": "no eligible deterministic wiki seeds or asset audit candidates"},
        )
    input_images = _build_visual_input_images(
        seed_payload=seed_payload,
        enabled=bool(resolved_settings.wiki_llm_compile_use_vision),
        detail=resolved_settings.wiki_llm_compile_image_detail,
    )

    request = LLMRequest(
        task_type=TaskType.KNOWLEDGE_COMPILE,
        system_prompt=_build_system_prompt(),
        user_prompt=_build_user_prompt(seed_payload),
        temperature=0.05,
        max_tokens=3600,
        json_schema=WIKI_LLM_COMPILE_SCHEMA,
        input_images=input_images,
        metadata={
            "seed_items": seed_payload["seed_items"],
            "asset_candidates": seed_payload["asset_candidates"],
        },
    )

    client = llm_client or LLMClient()
    try:
        async with asyncio.timeout(max(1.0, float(resolved_settings.wiki_llm_compile_timeout_seconds))):
            response = await client.invoke(request)
        payload = _load_json_object(response.content)
    except Exception as exc:  # noqa: BLE001
        return _result(
            status="failed",
            items=[],
            summary={
                "error": str(exc),
                "seed_item_count": len(seed_payload["seed_items"]),
                "asset_candidate_count": len(seed_payload["asset_candidates"]),
                "vision_attached_count": len(input_images),
            },
        )

    status = str(payload.get("status") or "").strip().lower()
    if status == "insufficient_evidence":
        return _result(
            status="insufficient_evidence",
            items=[],
            summary={
                "model_used": response.model_used,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "seed_item_count": len(seed_payload["seed_items"]),
                "asset_candidate_count": len(seed_payload["asset_candidates"]),
                "vision_attached_count": len(input_images),
            },
        )

    source_index = _build_source_index(seed_payload=seed_payload)
    known_terms = _known_terms_from_seed(seed_payload["seed_items"])
    items = _normalize_llm_items(
        payload=payload,
        generated_at=normalized_generated_at,
        model_used=response.model_used,
        existing_items=wiki_items_list,
        source_index=source_index,
        known_terms=known_terms,
        max_evidence_per_item=resolved_settings.wiki_llm_compile_max_evidence_per_item,
    )
    return _result(
        status="ok" if items else "insufficient_evidence",
        items=items,
        summary={
            "model_used": response.model_used,
            "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "seed_item_count": len(seed_payload["seed_items"]),
                "asset_candidate_count": len(seed_payload["asset_candidates"]),
                "vision_attached_count": len(input_images),
                "llm_item_count": len(items),
                "rejected_llm_item_count": sum(1 for item in items if item.get("status") == "rejected"),
            },
    )


def merge_llm_wiki_items(*, bundle: Mapping[str, Any], llm_result: Mapping[str, Any]) -> dict[str, Any]:
    existing_items = [dict(item) for item in (bundle.get("wiki_items") or []) if isinstance(item, dict)]
    incoming_items = [dict(item) for item in (llm_result.get("items") or []) if isinstance(item, dict)]
    if not incoming_items:
        return _bundle_with_llm_manifest(bundle=bundle, llm_result=llm_result, wiki_items=existing_items)

    seen_item_ids = {str(item.get("item_id") or "") for item in existing_items}
    merged_items = list(existing_items)
    for item in incoming_items:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            item_id = f"{LLM_ITEM_ID_PREFIX}:candidate:{_item_slug(str(item.get('canonical_name') or 'candidate'))}"
        if item_id in seen_item_ids:
            item_id = f"{item_id}:{_short_hash(json.dumps(item, ensure_ascii=False, sort_keys=True))}"
            item["item_id"] = item_id
        seen_item_ids.add(item_id)
        merged_items.append(item)

    return _bundle_with_llm_manifest(bundle=bundle, llm_result=llm_result, wiki_items=merged_items)


def _bundle_with_llm_manifest(
    *,
    bundle: Mapping[str, Any],
    llm_result: Mapping[str, Any],
    wiki_items: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = dict(bundle.get("manifest") or {})
    summary = dict(llm_result.get("summary") or {})
    summary["status"] = str(llm_result.get("status") or "unknown")
    manifest["llm_compiler"] = summary
    log_entry = str(bundle.get("log_entry") or "")
    if summary:
        log_entry = log_entry.rstrip() + "\n" + _build_llm_log_entry(summary=summary) + "\n"
    return {
        **dict(bundle),
        "manifest": manifest,
        "wiki_items": sorted(wiki_items, key=lambda item: (str(item.get("item_type") or ""), str(item.get("item_id") or ""))),
        "log_entry": log_entry,
    }


def _build_seed_payload(
    *,
    wiki_items: Iterable[dict[str, Any]],
    structured_assets: Mapping[str, Any],
    outline_entries: Iterable[dict[str, Any]],
    block_entries: Iterable[dict[str, Any]],
    max_seed_items: int,
    max_evidence_per_item: int,
) -> dict[str, Any]:
    seed_items: list[dict[str, Any]] = []
    for item in wiki_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("item_type") or "").strip()
        if item_type not in {"product_family", "section_template", "term_alias"}:
            continue
        evidence = _normalize_evidence_list(item.get("evidence") or [], limit=max_evidence_per_item)
        if not evidence:
            continue
        seed_items.append(
            {
                "item_id": str(item.get("item_id") or ""),
                "item_type": item_type,
                "canonical_name": str(item.get("canonical_name") or ""),
                "aliases": _dedupe_keep_order(str(alias) for alias in (item.get("aliases") or [])),
                "summary": _truncate_text(str(item.get("summary") or ""), limit=220),
                "source_documents": _dedupe_keep_order(str(doc) for doc in (item.get("source_documents") or [])),
                "evidence": evidence,
            }
        )
        if len(seed_items) >= max_seed_items:
            break

    asset_candidates = _collect_asset_candidates(
        block_entries=block_entries,
        structured_assets=structured_assets,
        max_items=max(4, max_seed_items // 4),
    )
    outline_snapshot = _outline_snapshot(outline_entries, limit=8)
    return {
        "seed_items": seed_items,
        "asset_candidates": asset_candidates,
        "outline_snapshot": outline_snapshot,
    }


def _collect_asset_candidates(
    *,
    block_entries: Iterable[dict[str, Any]],
    structured_assets: Mapping[str, Any],
    max_items: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in block_entries:
        if not isinstance(entry, dict):
            continue
        metadata = dict(entry.get("metadata") or entry.get("meta") or {})
        asset_id = str(entry.get("asset_id") or metadata.get("asset_id") or metadata.get("figure_asset_id") or "").strip()
        visual_role = str(entry.get("visual_role") or metadata.get("visual_role") or "").strip()
        content_form = str(entry.get("content_form") or metadata.get("content_form") or "").strip().lower()
        image_url = str(
            entry.get("image_url")
            or metadata.get("image_url")
            or metadata.get("data_url")
            or metadata.get("image_data_url")
            or ""
        ).strip()
        if not (asset_id or visual_role or content_form in {"figure", "image", "table_image"}):
            continue
        evidence = _evidence_from_entry(entry, asset_id=asset_id)
        if evidence is None:
            continue
        candidates.append(
            {
                "asset_id": asset_id,
                "visual_role": visual_role or "unknown",
                "content_form": content_form,
                "title": str(entry.get("title") or metadata.get("title") or "").strip(),
                "heading_path": evidence["heading_path"],
                "source_document": evidence["source_document"],
                "image_url": image_url,
                "context": _truncate_text(
                    " ".join(
                        str(value or "")
                        for value in (
                            entry.get("caption"),
                            metadata.get("caption"),
                            entry.get("section_summary"),
                            entry.get("content"),
                        )
                    ),
                    limit=260,
                ),
                "evidence": evidence,
            }
        )
        if len(candidates) >= max_items:
            return candidates
    if candidates:
        return candidates
    for asset in structured_assets.get("asset_audits") or []:
        if isinstance(asset, dict):
            candidates.append(dict(asset))
        if len(candidates) >= max_items:
            break
    return candidates


def _build_visual_input_images(
    *,
    seed_payload: Mapping[str, Any],
    enabled: bool,
    detail: str,
) -> list[LLMInputImage]:
    if not enabled:
        return []
    input_images: list[LLMInputImage] = []
    for asset in seed_payload.get("asset_candidates") or []:
        if not isinstance(asset, dict):
            continue
        image_url = str(asset.get("image_url") or "").strip()
        if not image_url:
            continue
        input_images.append(LLMInputImage(image_url=image_url, detail=detail))
    return input_images


def _normalize_llm_items(
    *,
    payload: Mapping[str, Any],
    generated_at: datetime,
    model_used: str,
    existing_items: list[dict[str, Any]],
    source_index: str,
    known_terms: set[str],
    max_evidence_per_item: int,
) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item_type = str(raw_item.get("item_type") or "").strip()
        canonical_name = str(raw_item.get("canonical_name") or "").strip()
        if item_type not in ALLOWED_LLM_WIKI_ITEM_TYPES or not canonical_name:
            continue
        aliases = _dedupe_keep_order(str(alias) for alias in (raw_item.get("aliases") or []))
        evidence = _normalize_evidence_list(raw_item.get("evidence") or [], limit=max_evidence_per_item)
        source_documents = _dedupe_keep_order(
            [
                *[str(doc) for doc in (raw_item.get("source_documents") or [])],
                *[str(item.get("source_document") or "") for item in evidence],
            ]
        )
        confidence = _clamp_float(raw_item.get("confidence"), default=0.0)
        reason = str(raw_item.get("reason") or "").strip()
        asset_type_label = str(raw_item.get("asset_type_label") or "").strip()
        related_item_id = _find_related_item_id(
            item_type=item_type,
            canonical_name=canonical_name,
            aliases=aliases,
            existing_items=existing_items,
        )
        flags = _quality_flags_for_llm_item(
            item_type=item_type,
            canonical_name=canonical_name,
            aliases=aliases,
            evidence=evidence,
            source_documents=source_documents,
            confidence=confidence,
            asset_type_label=asset_type_label,
            source_index=source_index,
            known_terms=known_terms,
        )
        quality_score = _quality_score_for_llm_item(
            evidence=evidence,
            source_documents=source_documents,
            confidence=confidence,
            quality_flags=flags,
        )
        normalized_items.append(
            {
                "item_id": _llm_item_id(item_type=item_type, canonical_name=canonical_name),
                "item_type": item_type,
                "canonical_name": canonical_name,
                "aliases": aliases,
                "summary": _truncate_text(str(raw_item.get("summary") or ""), limit=520),
                "source_documents": source_documents,
                "evidence": evidence,
                "quality_score": quality_score,
                "quality_flags": flags,
                "status": _status_for_llm_item(quality_flags=flags),
                "created_by": "llm_compiler",
                "updated_at": generated_at.astimezone(timezone.utc).isoformat(),
                "metadata": {
                    "llm_compiler": {
                        "model_used": model_used,
                        "confidence": confidence,
                        "reason": reason,
                        "related_item_id": related_item_id,
                        "asset_type_label": asset_type_label,
                    }
                },
            }
        )
    return normalized_items


def _quality_flags_for_llm_item(
    *,
    item_type: str,
    canonical_name: str,
    aliases: list[str],
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    confidence: float,
    asset_type_label: str,
    source_index: str,
    known_terms: set[str],
) -> list[str]:
    flags = ["llm_compiled_candidate"]
    if item_type == "asset_type_rule":
        flags.append("visual_audit_candidate")
        if asset_type_label not in VISUAL_AUDIT_TYPES:
            flags.append("asset_type_uncertain")
    if not evidence:
        flags.append("missing_source_evidence")
    elif any(not _evidence_has_required_fields(item) for item in evidence):
        flags.append("source_section_missing")
    if not source_documents:
        flags.append("low_source_document_coverage")
    if len(source_documents) < 2:
        flags.append("single_source")
    if confidence < 0.55:
        flags.append("low_llm_confidence")
    if not _candidate_has_history_backing(
        item_type=item_type,
        canonical_name=canonical_name,
        aliases=aliases,
        evidence=evidence,
        source_index=source_index,
        known_terms=known_terms,
    ):
        flags.append("blocking:llm_unseen_term")
    return normalize_quality_flags(flags)


def _quality_score_for_llm_item(
    *,
    evidence: list[dict[str, Any]],
    source_documents: list[str],
    confidence: float,
    quality_flags: list[str],
) -> float:
    if "missing_source_evidence" in quality_flags:
        return 0.0
    if "blocking:llm_unseen_term" in quality_flags:
        return 0.34
    if "source_section_missing" in quality_flags:
        return 0.52
    base = 0.54
    base += min(0.16, len(evidence) * 0.045)
    base += min(0.10, len(source_documents) * 0.035)
    base += max(0.0, min(0.18, confidence * 0.18))
    if "low_llm_confidence" in quality_flags:
        base -= 0.08
    if "asset_type_uncertain" in quality_flags:
        base = min(base, 0.68)
    return round(max(0.0, min(0.88, base)), 4)


def _status_for_llm_item(*, quality_flags: list[str]) -> str:
    if "missing_source_evidence" in quality_flags or "blocking:llm_unseen_term" in quality_flags:
        return "rejected"
    if any(is_blocking_quality_flag(flag) for flag in quality_flags):
        return "review_required"
    return "review_required"


def _candidate_has_history_backing(
    *,
    item_type: str,
    canonical_name: str,
    aliases: list[str],
    evidence: list[dict[str, Any]],
    source_index: str,
    known_terms: set[str],
) -> bool:
    if item_type == "asset_type_rule":
        return bool(evidence)
    evidence_text = " ".join(
        " ".join(str(item.get(key) or "") for key in ("heading_path", "source_document", "evidence_quote"))
        for item in evidence
    )
    terms = _dedupe_keep_order([canonical_name, *aliases])
    backed_terms = [
        term
        for term in terms
        if _term_is_known(term, known_terms=known_terms)
        or _contains_term(evidence_text, term)
        or _contains_term(source_index, term)
    ]
    if item_type == "term_alias":
        return len(backed_terms) >= min(2, len(terms))
    return bool(backed_terms)


def _term_is_known(term: str, *, known_terms: set[str]) -> bool:
    normalized = _normalize_term(term)
    return bool(normalized and normalized in known_terms)


def _known_terms_from_seed(seed_items: Iterable[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()
    for item in seed_items:
        if not isinstance(item, dict):
            continue
        for value in [item.get("canonical_name"), *(item.get("aliases") or [])]:
            normalized = _normalize_term(str(value or ""))
            if normalized:
                terms.add(normalized)
    return terms


def _build_source_index(*, seed_payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in seed_payload.get("seed_items") or []:
        if not isinstance(item, dict):
            continue
        parts.extend(
            [
                str(item.get("canonical_name") or ""),
                " ".join(str(alias) for alias in (item.get("aliases") or [])),
                str(item.get("summary") or ""),
                " ".join(str(doc) for doc in (item.get("source_documents") or [])),
            ]
        )
        for evidence in item.get("evidence") or []:
            if isinstance(evidence, dict):
                parts.append(
                    " ".join(
                        str(evidence.get(key) or "")
                        for key in ("heading_path", "source_document", "evidence_quote", "asset_id")
                    )
                )
    for item in seed_payload.get("asset_candidates") or []:
        if isinstance(item, dict):
            parts.append(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("asset_id", "visual_role", "content_form", "title", "heading_path", "context")
                )
            )
    for item in seed_payload.get("outline_snapshot") or []:
        if isinstance(item, dict):
            parts.append(
                " ".join(
                    [
                        str(item.get("document_title") or ""),
                        str(item.get("file_name") or ""),
                        " ".join(str(title) for title in (item.get("top_level_titles") or [])),
                    ]
                )
            )
    return " ".join(parts).casefold()


def _find_related_item_id(
    *,
    item_type: str,
    canonical_name: str,
    aliases: list[str],
    existing_items: list[dict[str, Any]],
) -> str | None:
    candidate_terms = {_normalize_term(term) for term in [canonical_name, *aliases] if _normalize_term(term)}
    if not candidate_terms:
        return None
    for item in existing_items:
        if str(item.get("item_type") or "") != item_type:
            continue
        existing_terms = {
            _normalize_term(term)
            for term in [str(item.get("canonical_name") or ""), *[str(alias) for alias in (item.get("aliases") or [])]]
            if _normalize_term(term)
        }
        if candidate_terms & existing_terms:
            return str(item.get("item_id") or "") or None
    return None


def _normalize_evidence_list(values: Any, *, limit: int) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    source_values = values if isinstance(values, list) else []
    for value in source_values:
        if not isinstance(value, dict):
            continue
        evidence.append(
            {
                "sample_id": str(value.get("sample_id") or "").strip(),
                "raw_document_id": str(value.get("raw_document_id") or "").strip(),
                "source_section_id": str(value.get("source_section_id") or "").strip(),
                "heading_path": str(value.get("heading_path") or "").strip(),
                "source_document": str(value.get("source_document") or "").strip(),
                "evidence_quote": _truncate_text(str(value.get("evidence_quote") or "").strip(), limit=300),
                "asset_id": str(value.get("asset_id") or "").strip(),
            }
        )
        if len(evidence) >= limit:
            break
    return _dedupe_evidence(evidence)


def _evidence_from_entry(entry: dict[str, Any], *, asset_id: str = "") -> dict[str, Any] | None:
    metadata = dict(entry.get("metadata") or entry.get("meta") or {})
    heading_path = str(
        entry.get("heading_path")
        or entry.get("source_heading")
        or entry.get("section_path")
        or metadata.get("heading_path")
        or metadata.get("section_path")
        or ""
    ).strip()
    sample_id = str(entry.get("sample_id") or metadata.get("sample_id") or "").strip()
    raw_document_id = str(
        entry.get("raw_document_id")
        or entry.get("source_doc_id")
        or metadata.get("raw_document_id")
        or sample_id
        or entry.get("file_name")
        or ""
    ).strip()
    source_section_id = str(
        entry.get("source_section_id")
        or metadata.get("source_section_id")
        or entry.get("section_id")
        or ""
    ).strip()
    quote = _truncate_text(
        " ".join(
            str(value or "")
            for value in (
                entry.get("caption"),
                metadata.get("caption"),
                entry.get("section_summary"),
                entry.get("content"),
            )
        ).strip(),
        limit=300,
    )
    if not any((heading_path, sample_id, raw_document_id, source_section_id, quote, asset_id)):
        return None
    return {
        "sample_id": sample_id,
        "raw_document_id": raw_document_id,
        "source_section_id": source_section_id,
        "heading_path": heading_path,
        "source_document": str(entry.get("file_name") or metadata.get("source_document") or "").strip(),
        "evidence_quote": quote,
        "asset_id": asset_id,
    }


def _evidence_has_required_fields(item: dict[str, Any]) -> bool:
    return is_publishable_evidence_item(item)


def _outline_snapshot(outline_entries: Iterable[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for entry in outline_entries:
        if not isinstance(entry, dict):
            continue
        snapshot.append(
            {
                "sample_id": str(entry.get("sample_id") or ""),
                "file_name": str(entry.get("file_name") or ""),
                "document_title": str(entry.get("document_title") or ""),
                "top_level_titles": [str(item) for item in (entry.get("top_level_titles") or [])[:8]],
            }
        )
        if len(snapshot) >= limit:
            break
    return snapshot


def _build_system_prompt() -> str:
    return (
        "你是售前历史库的离线知识编译器，只能基于输入的确定性候选和证据归纳知识。\n"
        "输出必须是符合 schema 的 JSON 对象，不要输出 Markdown 或解释性文字。\n"
        "允许处理的 item_type 只有 product_family、section_template、term_alias、asset_type_rule。\n"
        "每条结论必须保留 evidence，且 evidence 需要包含 sample_id、raw_document_id、source_section_id、heading_path，"
        "并至少包含 evidence_quote 或 asset_id。\n"
        "不得发明历史库未出现过的专业术语、产品族、章节模板或图像类型；证据不足时返回 status=insufficient_evidence 且 items=[]。\n"
        "图片类型审核只能基于图片本体、标题、caption、章节标题和上下文的证据判断，不能直接生成正文。"
    )


def _build_user_prompt(seed_payload: Mapping[str, Any]) -> str:
    return (
        "请对以下候选进行离线归纳，优先输出可进入人工审核 draft_wiki 的候选。\n"
        "要求：产品族必须说明来自哪些文档/章节；章节模板必须来自真实标题；术语别名必须说明术语出现位置；"
        "图片类型审核必须给出视觉类型标签。\n\n"
        f"{json.dumps(seed_payload, ensure_ascii=False, indent=2)}"
    )


def _load_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM knowledge compiler returned a non-object JSON payload")
    return payload


def _result(*, status: str, items: list[dict[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "items": items,
        "summary": dict(summary),
    }


def _build_llm_log_entry(*, summary: Mapping[str, Any]) -> str:
    return (
        "## LLM offline compile\n"
        f"- status: {summary.get('status') or 'unknown'}\n"
        f"- seed_items: {summary.get('seed_item_count', 0)}\n"
        f"- asset_candidates: {summary.get('asset_candidate_count', 0)}\n"
        f"- llm_items: {summary.get('llm_item_count', 0)}\n"
    )


def _normalize_term(term: str) -> str:
    return "".join(str(term or "").strip().casefold().split())


def _contains_term(haystack: str, needle: str) -> bool:
    normalized_needle = _normalize_term(needle)
    if not normalized_needle:
        return False
    return normalized_needle in _normalize_term(haystack)


def _llm_item_id(*, item_type: str, canonical_name: str) -> str:
    return f"{LLM_ITEM_ID_PREFIX}:{item_type}:{_item_slug(canonical_name)}"


def _item_slug(text: str) -> str:
    slug = _slugify(text)
    if slug != "item":
        return slug
    normalized = str(text or "").strip()
    if not normalized:
        return slug
    return _short_hash(normalized)


def _slugify(text: str) -> str:
    normalized = str(text or "").strip().lower().replace("_", "-")
    normalized = SLUG_PATTERN.sub("-", normalized)
    normalized = normalized.strip("-")
    return normalized or "item"


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _truncate_text(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)] + "..."


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


def _dedupe_evidence(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in values:
        signature = (
            str(item.get("sample_id") or ""),
            str(item.get("raw_document_id") or ""),
            str(item.get("source_section_id") or ""),
            str(item.get("asset_id") or ""),
            str(item.get("evidence_quote") or "")[:120],
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))
