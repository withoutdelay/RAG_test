from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Iterable, Mapping

from app.config import Settings, get_settings
from app.services.llm.client import LLMRequest, TaskType
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


EVIDENCE_SELECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "selected_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "selected_assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "rejected_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "risk_flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["candidate_id", "reason", "risk_flags"],
                "additionalProperties": False,
            },
        },
        "selection_reason": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "selected_sections",
        "selected_blocks",
        "selected_assets",
        "rejected_candidates",
        "selection_reason",
        "risk_flags",
        "confidence",
    ],
    "additionalProperties": False,
}

BUSINESS_NOISE_TOKENS = (
    "培训",
    "售后",
    "服务承诺",
    "维保",
    "质保",
    "供货周期",
    "交付",
    "商务",
    "报价",
    "付款",
    "公司简介",
)
TECHNICAL_TOKENS = (
    "主回路",
    "拓扑",
    "控制",
    "联锁",
    "接口",
    "通信",
    "变频",
    "电机",
    "变压器",
    "参数",
    "信号",
    "柜体",
    "保护",
)
MAIN_CIRCUIT_TOKENS = ("主回路", "拓扑", "一次系统", "一次原理", "接线", "旁路")
SPARE_PARTS_TOKENS = ("备件", "备品", "随机备件")
SITE_CONDITION_TOKENS = ("环境条件", "供电条件", "海拔", "温度", "湿度", "现场条件")
SELECTED_TRACE_LIMIT = 8


async def select_evidence_for_section(
    *,
    llm_client: Any,
    section: dict[str, Any],
    global_params: dict[str, Any],
    case_trace: Mapping[str, Any] | None,
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]] | None = None,
    published_wiki: Mapping[str, Any] | None = None,
    task_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    mode = str(resolved_settings.evidence_selector_mode or "off").lower()
    base_result = _base_result(
        mode=mode,
        reusable_blocks=reusable_blocks,
        recommended_assets=recommended_assets,
        asset_candidates=asset_candidates or [],
    )
    if mode == "off":
        return {**base_result, "status": "disabled", "reason": "evidence selector disabled"}

    candidates = _build_selector_candidates(
        section=section,
        case_trace=case_trace or {},
        reusable_blocks=reusable_blocks,
        recommended_assets=recommended_assets,
        asset_candidates=asset_candidates or [],
        settings=resolved_settings,
    )
    if not candidates["sections"] and not candidates["blocks"] and not candidates["assets"]:
        return {**base_result, "status": "skipped", "reason": "no evidence candidates"}

    deterministic = _deterministic_prefilter(section=section, candidates=candidates)
    filtered_blocks = _items_by_candidate_ids(
        reusable_blocks,
        deterministic["kept_block_ids"],
        id_getter=lambda item, index: _block_candidate_id(item, index),
    )
    filtered_assets = _items_by_candidate_ids(
        recommended_assets,
        deterministic["kept_asset_ids"],
        id_getter=lambda item, index: _asset_candidate_id(item, index),
    )
    filtered_asset_candidates = _items_by_candidate_ids(
        asset_candidates or [],
        deterministic["kept_asset_ids"],
        id_getter=lambda item, index: _asset_candidate_id(item, index),
    )
    deterministic_result = {
        **base_result,
        "status": "deterministic",
        "reason": "deterministic_prefilter_only",
        "selected_sections": _selected_candidate_summaries(
            candidates["sections"],
            deterministic["kept_section_ids"],
        ),
        "selected_blocks": _selected_block_summaries(filtered_blocks),
        "selected_assets": _selected_asset_summaries(filtered_assets),
        "rejected_candidates": deterministic["rejected"],
        "risk_flags": list(deterministic["risk_flags"]),
        "filtered_reusable_blocks": filtered_blocks,
        "filtered_recommended_assets": filtered_assets,
        "filtered_asset_candidates": filtered_asset_candidates,
        "selection_reason": "deterministic prefilter removed hard off-topic evidence",
    }
    if not deterministic["llm_candidates_available"]:
        return {
            **deterministic_result,
            "status": "applied",
            "reason": "all_candidates_rejected_by_deterministic_prefilter",
        }

    llm_candidates = {
        "sections": [
            item for item in candidates["sections"] if item["candidate_id"] in deterministic["kept_section_ids"]
        ],
        "blocks": [
            item for item in candidates["blocks"] if item["candidate_id"] in deterministic["kept_block_ids"]
        ],
        "assets": [
            item for item in candidates["assets"] if item["candidate_id"] in deterministic["kept_asset_ids"]
        ],
    }
    try:
        payload = {
            "section": _section_payload(section),
            "target_taxonomy": _json_safe_value(infer_target_taxonomy(section)),
            "global_params": _safe_global_params(global_params),
            "published_wiki": _published_wiki_payload(published_wiki or {}),
            "candidates": llm_candidates,
        }
        response = await _invoke_selector_llm(
            llm_client=llm_client,
            task_id=task_id,
            payload=payload,
            timeout_seconds=float(resolved_settings.evidence_selector_timeout_seconds),
        )
        decision_payload = _load_json_object(response.content)
        applied = _apply_llm_selector_decisions(
            mode=mode,
            candidates=candidates,
            deterministic=deterministic,
            decision_payload=decision_payload,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
            asset_candidates=asset_candidates or [],
            min_confidence=float(resolved_settings.evidence_selector_min_confidence),
        )
        applied["model_used"] = response.model_used
        return applied
    except Exception as exc:  # noqa: BLE001
        return {
            **deterministic_result,
            "status": "fallback_error",
            "reason": "llm_selector_failed",
            "error": str(exc),
            "risk_flags": _dedupe_keep_order([*deterministic_result["risk_flags"], "selector_fallback"]),
        }


def _invoke_selector_llm(
    *,
    llm_client: Any,
    task_id: str,
    payload: dict[str, Any],
    timeout_seconds: float,
):
    async def _invoke():
        async with asyncio.timeout(max(1.0, timeout_seconds)):
            return await llm_client.invoke(
                LLMRequest(
                    task_type=TaskType.EVIDENCE_SELECT,
                    session_id=f"{task_id}-evidence-selector",
                    system_prompt=_build_system_prompt(),
                    user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
                    temperature=0.0,
                    max_tokens=1600,
                    json_schema=EVIDENCE_SELECTOR_SCHEMA,
                    metadata={"candidates": payload.get("candidates") or {}},
                )
            )

    return _invoke()


def _apply_llm_selector_decisions(
    *,
    mode: str,
    candidates: dict[str, list[dict[str, Any]]],
    deterministic: dict[str, Any],
    decision_payload: Mapping[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]],
    min_confidence: float,
) -> dict[str, Any]:
    selected_section_ids = _selected_ids_from_payload(decision_payload.get("selected_sections"))
    selected_block_ids = _selected_ids_from_payload(decision_payload.get("selected_blocks"))
    selected_asset_ids = _selected_ids_from_payload(decision_payload.get("selected_assets"))
    if not selected_section_ids:
        selected_section_ids = set(deterministic["kept_section_ids"])
    if not selected_block_ids and mode != "strict":
        selected_block_ids = set(deterministic["kept_block_ids"])
    if not selected_asset_ids and mode != "strict":
        selected_asset_ids = set(deterministic["kept_asset_ids"])

    selected_block_ids &= set(deterministic["kept_block_ids"])
    selected_asset_ids &= set(deterministic["kept_asset_ids"])
    selected_section_ids &= set(deterministic["kept_section_ids"])

    filtered_blocks = _items_by_candidate_ids(
        reusable_blocks,
        selected_block_ids,
        id_getter=lambda item, index: _block_candidate_id(item, index),
    )
    filtered_assets = _items_by_candidate_ids(
        recommended_assets,
        selected_asset_ids,
        id_getter=lambda item, index: _asset_candidate_id(item, index),
    )
    filtered_asset_candidates = _items_by_candidate_ids(
        asset_candidates,
        selected_asset_ids,
        id_getter=lambda item, index: _asset_candidate_id(item, index),
    )

    confidence = _safe_float(decision_payload.get("confidence"), default=0.0)
    risk_flags = _dedupe_keep_order(
        [
            *deterministic["risk_flags"],
            *[str(item) for item in (decision_payload.get("risk_flags") or []) if str(item).strip()],
        ]
    )
    if confidence < min_confidence:
        risk_flags.append("low_confidence_selection")

    rejected = [
        *deterministic["rejected"],
        *_normalize_rejected_candidates(decision_payload.get("rejected_candidates") or []),
    ]

    return {
        "mode": mode,
        "status": "applied",
        "reason": "llm_selector_applied",
        "input_counts": {
            "sections": len(candidates["sections"]),
            "blocks": len(candidates["blocks"]),
            "assets": len(candidates["assets"]),
        },
        "selected_sections": _selected_candidate_summaries(candidates["sections"], selected_section_ids),
        "selected_blocks": _selected_block_summaries(filtered_blocks),
        "selected_assets": _selected_asset_summaries(filtered_assets),
        "rejected_candidates": rejected[:SELECTED_TRACE_LIMIT],
        "selection_reason": str(decision_payload.get("selection_reason") or "").strip(),
        "risk_flags": _dedupe_keep_order(risk_flags),
        "confidence": confidence,
        "filtered_reusable_blocks": filtered_blocks,
        "filtered_recommended_assets": filtered_assets,
        "filtered_asset_candidates": filtered_asset_candidates,
    }


def _deterministic_prefilter(*, section: dict[str, Any], candidates: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rejected: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    kept_section_ids = {str(item.get("candidate_id") or "") for item in candidates["sections"]}
    kept_block_ids: set[str] = set()
    kept_asset_ids: set[str] = set()
    for candidate in candidates["blocks"]:
        candidate_id = str(candidate.get("candidate_id") or "")
        reason, flags = _deterministic_reject_reason(section=section, candidate=candidate)
        if reason:
            rejected.append({"candidate_id": candidate_id, "reason": reason, "risk_flags": flags})
            risk_flags.extend(flags)
            continue
        kept_block_ids.add(candidate_id)
    for candidate in candidates["assets"]:
        candidate_id = str(candidate.get("candidate_id") or "")
        reason, flags = _deterministic_reject_reason(section=section, candidate=candidate)
        if reason:
            rejected.append({"candidate_id": candidate_id, "reason": reason, "risk_flags": flags})
            risk_flags.extend(flags)
            continue
        kept_asset_ids.add(candidate_id)
    return {
        "kept_section_ids": kept_section_ids,
        "kept_block_ids": kept_block_ids,
        "kept_asset_ids": kept_asset_ids,
        "rejected": rejected,
        "risk_flags": _dedupe_keep_order(risk_flags),
        "llm_candidates_available": bool(kept_section_ids or kept_block_ids or kept_asset_ids),
    }


def _deterministic_reject_reason(*, section: dict[str, Any], candidate: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "").lower()
    section_text = _section_text(section)
    text = str(candidate.get("text") or "").casefold()
    candidate_type = str(candidate.get("candidate_type") or "")
    candidate_section_type = str(candidate.get("section_type") or "").lower()
    if candidate_type == "block" and not str(candidate.get("content") or "").strip() and str(candidate.get("reason") or "").strip():
        return "case fallback has match reason but no body evidence", ["reason_without_body_evidence"]
    if _is_technical_section(target_section_type=target_section_type, section_text=section_text):
        if any(token in text for token in BUSINESS_NOISE_TOKENS) and not any(token in text for token in TECHNICAL_TOKENS):
            return "business or delivery material is not valid technical evidence", ["cross_section_contamination"]
    if _is_spare_parts_section(target_section_type=target_section_type, section_text=section_text):
        if candidate_section_type in {"site_conditions", "design_basis"} or any(token in text for token in SITE_CONDITION_TOKENS):
            return "site or power condition evidence does not support spare parts content", ["spare_parts_wrong_context"]
    if candidate_type == "asset" and _is_main_circuit_section(target_section_type=target_section_type, section_text=section_text):
        visual_role = str(candidate.get("visual_role") or "").lower()
        asset_type = str(candidate.get("asset_type") or "").lower()
        if visual_role == "product_photo" or "产品照片" in text:
            return "product photo cannot support main circuit or topology section", ["product_photo_for_topology"]
        if asset_type == "figure" and visual_role in {"text_fragment", "asset_fragment", "page_furniture"}:
            return "figure fragment cannot support main circuit or topology section", ["visual_fragment_for_topology"]
    return None, []


def _build_selector_candidates(
    *,
    section: dict[str, Any],
    case_trace: Mapping[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, list[dict[str, Any]]]:
    sections = [
        _section_candidate_payload(item, index)
        for index, item in enumerate(
            [
                *[item for item in (case_trace.get("scoped_sections") or []) if isinstance(item, dict)],
                *[item for item in (case_trace.get("section_candidates") or []) if isinstance(item, dict)],
            ]
        )
    ]
    sections = _dedupe_candidates(sections)[: int(settings.evidence_selector_max_sections)]
    blocks = [
        _block_candidate_payload(item, index)
        for index, item in enumerate(reusable_blocks[: int(settings.evidence_selector_max_blocks)])
    ]
    assets_source = asset_candidates or recommended_assets
    assets = [
        _asset_candidate_payload(item, index)
        for index, item in enumerate(assets_source[: int(settings.evidence_selector_max_assets)])
    ]
    return {"sections": sections, "blocks": blocks, "assets": assets}


def _section_candidate_payload(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    section_path = str(item.get("section_path") or item.get("heading_path") or "").strip()
    source_heading = str(item.get("source_heading") or item.get("title") or "").strip()
    return {
        "candidate_id": _section_candidate_id(item, index),
        "candidate_type": "section",
        "source_document": str(item.get("file_name") or item.get("source_title") or "").strip(),
        "source_section_id": str(item.get("section_id") or "").strip(),
        "heading_path": section_path,
        "source_heading": source_heading,
        "score": _safe_float(item.get("score"), default=0.0),
        "reason": str(item.get("reason") or "").strip(),
        "text": _truncate(" ".join([source_heading, section_path, str(item.get("reason") or "")]), limit=500),
    }


def _block_candidate_payload(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    heading_path = " > ".join(str(value) for value in (item.get("heading_path") or []) if str(value).strip())
    content = str(item.get("content_md") or item.get("content") or "").strip()
    return {
        "candidate_id": _block_candidate_id(item, index),
        "candidate_type": "block",
        "block_id": str(item.get("block_id") or "").strip(),
        "source_document": str(item.get("source_title") or "").strip(),
        "source_section_id": str(item.get("source_section_id") or "").strip(),
        "heading_path": str(item.get("section_path") or heading_path).strip(),
        "source_heading": str(item.get("source_heading") or "").strip(),
        "section_type": str(metadata.get("section_type") or item.get("section_type") or "").strip(),
        "equipment_type": str(metadata.get("equipment_type") or item.get("equipment_type") or "").strip(),
        "content_form": str(metadata.get("content_form") or item.get("content_form") or "").strip(),
        "score": _safe_float(item.get("selection_score") or item.get("reusability_score"), default=0.0),
        "reason": " | ".join(str(value) for value in (item.get("selection_reasons") or []) if str(value).strip()),
        "content": _truncate(content, limit=900),
        "text": _truncate(
            " ".join(
                [
                    str(item.get("source_heading") or ""),
                    str(item.get("section_path") or heading_path),
                    content,
                    " ".join(str(value) for value in (item.get("selection_reasons") or [])),
                ]
            ),
            limit=1100,
        ),
    }


def _asset_candidate_payload(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    preview = str(item.get("preview_text") or metadata.get("preview_text") or "").strip()
    return {
        "candidate_id": _asset_candidate_id(item, index),
        "candidate_type": "asset",
        "asset_id": str(item.get("asset_id") or "").strip(),
        "asset_type": str(item.get("asset_type") or "").strip(),
        "visual_role": str(item.get("visual_role") or metadata.get("visual_role") or "").strip(),
        "source_document": str(item.get("document_name") or metadata.get("source_document") or "").strip(),
        "heading_path": str(item.get("heading_path") or metadata.get("heading_path") or "").strip(),
        "display_title": str(item.get("display_title") or item.get("title") or "").strip(),
        "score": _safe_float(item.get("score"), default=0.0),
        "reason": str(item.get("reason") or "").strip(),
        "content": _truncate(preview, limit=700),
        "text": _truncate(
            " ".join(
                str(value or "")
                for value in (
                    item.get("display_title"),
                    item.get("title"),
                    item.get("caption"),
                    item.get("heading_path"),
                    preview,
                    item.get("visual_role") or metadata.get("visual_role"),
                    item.get("reason"),
                )
            ),
            limit=900,
        ),
    }


def _base_result(
    *,
    mode: str,
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "input_counts": {
            "sections": 0,
            "blocks": len(reusable_blocks),
            "assets": len(asset_candidates or recommended_assets),
        },
        "selected_sections": [],
        "selected_blocks": _selected_block_summaries(reusable_blocks),
        "selected_assets": _selected_asset_summaries(recommended_assets),
        "rejected_candidates": [],
        "selection_reason": "",
        "risk_flags": [],
        "confidence": None,
        "filtered_reusable_blocks": reusable_blocks,
        "filtered_recommended_assets": recommended_assets,
        "filtered_asset_candidates": asset_candidates,
    }


def _items_by_candidate_ids(
    items: list[dict[str, Any]],
    candidate_ids: Iterable[str],
    *,
    id_getter,
) -> list[dict[str, Any]]:
    wanted = {str(item) for item in candidate_ids if str(item).strip()}
    if not wanted:
        return []
    selected: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if id_getter(item, index) in wanted:
            selected.append(item)
    return selected


def _selected_candidate_summaries(candidates: list[dict[str, Any]], selected_ids: Iterable[str]) -> list[dict[str, Any]]:
    selected_id_set = {str(item) for item in selected_ids if str(item).strip()}
    return [
        {
            "candidate_id": item["candidate_id"],
            "source_document": item.get("source_document"),
            "source_section_id": item.get("source_section_id"),
            "heading_path": item.get("heading_path"),
            "score": item.get("score"),
            "reason": item.get("reason"),
        }
        for item in candidates
        if item["candidate_id"] in selected_id_set
    ][:SELECTED_TRACE_LIMIT]


def _selected_block_summaries(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, block in enumerate(blocks[:SELECTED_TRACE_LIMIT]):
        summaries.append(
            {
                "candidate_id": _block_candidate_id(block, index),
                "block_id": str(block.get("block_id") or ""),
                "source_document": str(block.get("source_title") or ""),
                "source_section_id": str(block.get("source_section_id") or ""),
                "heading_path": " > ".join(str(value) for value in (block.get("heading_path") or []) if str(value).strip())
                or str(block.get("section_path") or ""),
                "score": _safe_float(block.get("selection_score") or block.get("reusability_score"), default=0.0),
            }
        )
    return summaries


def _selected_asset_summaries(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, asset in enumerate(assets[:SELECTED_TRACE_LIMIT]):
        summaries.append(
            {
                "candidate_id": _asset_candidate_id(asset, index),
                "asset_id": str(asset.get("asset_id") or ""),
                "asset_type": str(asset.get("asset_type") or ""),
                "visual_role": str(asset.get("visual_role") or ""),
                "display_title": str(asset.get("display_title") or asset.get("title") or ""),
                "heading_path": str(asset.get("heading_path") or ""),
                "score": _safe_float(asset.get("score"), default=0.0),
            }
        )
    return summaries


def _selected_ids_from_payload(values: Any) -> set[str]:
    selected: set[str] = set()
    if not isinstance(values, list):
        return selected
    for item in values:
        if not isinstance(item, Mapping):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if candidate_id:
            selected.add(candidate_id)
    return selected


def _normalize_rejected_candidates(values: Any) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return rejected
    for item in values:
        if not isinstance(item, Mapping):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        rejected.append(
            {
                "candidate_id": candidate_id,
                "reason": str(item.get("reason") or "").strip(),
                "risk_flags": [str(flag) for flag in (item.get("risk_flags") or []) if str(flag).strip()],
            }
        )
    return rejected


def _section_payload(section: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(section.get("title") or ""),
        "purpose": str(section.get("purpose") or section.get("description") or ""),
        "keywords": [str(item) for item in (section.get("keywords") or []) if str(item).strip()],
        "generation_mode": str(section.get("generation_mode") or ""),
        "children": [
            {
                "title": str(item.get("title") or ""),
                "purpose": str(item.get("purpose") or item.get("description") or ""),
            }
            for item in (section.get("children") or [])
            if isinstance(item, Mapping)
        ][:8],
    }


def _published_wiki_payload(published_wiki: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "glossary_entries": [
            {
                "primary": str(item.get("display_primary_term") or item.get("primary_term") or ""),
                "aliases": [str(alias) for alias in (item.get("display_aliases") or item.get("aliases") or [])],
            }
            for item in (published_wiki.get("glossary_entries") or [])[:6]
            if isinstance(item, Mapping)
        ],
        "product_cards": [
            str(item.get("title") or item.get("product_family") or "")
            for item in (published_wiki.get("product_cards") or [])[:4]
            if isinstance(item, Mapping)
        ],
        "module_cards": [
            str(item.get("title") or item.get("module_key") or "")
            for item in (published_wiki.get("module_cards") or [])[:4]
            if isinstance(item, Mapping)
        ],
    }


def _safe_global_params(global_params: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "project_name",
        "product_line",
        "industry",
        "voltage_level",
        "power_rating",
        "quantity",
        "business_objective",
    }
    return {key: value for key, value in global_params.items() if key in allowed}


def _build_system_prompt() -> str:
    return (
        "你是售前技术方案生成前的证据筛选器，只能筛选证据，不生成正文。\n"
        "请根据当前章节标题、目的、taxonomy、子结构、历史章节候选、片段候选、图表候选和 published_wiki 相关项，"
        "选择真正能进入章节生成 prompt 的证据。\n\n"
        "必须拒绝：与章节目标不一致的证据；技术章节中的培训、交付、商务资料；备件章节中的环境/供电条件表；"
        "主回路/拓扑章节中的产品照片；只有匹配原因但没有正文证据的 case fallback。\n"
        "输出必须是 JSON，不要输出正文或 Markdown。"
    )


def _load_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Evidence selector returned a non-object JSON payload")
    return payload


def _section_text(section: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(section.get("title") or ""),
            str(section.get("purpose") or section.get("description") or ""),
            " ".join(str(item) for item in (section.get("keywords") or [])),
        ]
    ).casefold()


def _is_technical_section(*, target_section_type: str, section_text: str) -> bool:
    return target_section_type in {
        "main_circuit_scheme",
        "overall_solution",
        "vfd_spec",
        "transformer_spec",
        "motor_spec",
        "starter_spec",
        "communication_interface",
        "control_logic",
        "protection_interlock",
    } or any(token in section_text for token in TECHNICAL_TOKENS)


def _is_main_circuit_section(*, target_section_type: str, section_text: str) -> bool:
    return target_section_type in {"main_circuit_scheme", "overall_solution"} or any(
        token in section_text for token in MAIN_CIRCUIT_TOKENS
    )


def _is_spare_parts_section(*, target_section_type: str, section_text: str) -> bool:
    return target_section_type in {"spare_parts", "supply_scope"} or any(token in section_text for token in SPARE_PARTS_TOKENS)


def _section_candidate_id(item: Mapping[str, Any], index: int) -> str:
    raw = str(item.get("section_id") or item.get("section_path") or item.get("heading_path") or item.get("source_heading") or index)
    return f"section:{_short_hash(raw)}"


def _block_candidate_id(item: Mapping[str, Any], index: int) -> str:
    raw = str(item.get("block_id") or item.get("source_section_id") or item.get("section_path") or index)
    return f"block:{_short_hash(raw)}"


def _asset_candidate_id(item: Mapping[str, Any], index: int) -> str:
    raw = str(item.get("asset_id") or item.get("heading_path") or item.get("title") or index)
    return f"asset:{_short_hash(raw)}"


def _dedupe_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(candidate)
    return deduped


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truncate(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return value
