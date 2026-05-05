from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import BACKEND_ROOT, get_settings
from app.db import get_session_factory
from app.models.document import Document
from app.services.domain.term_lexicon import build_corpus_term_lexicon
from app.services.parsing.case_library import (
    build_outline_library_entry,
    build_reusable_block_entries,
    render_case_library_markdown,
    summarize_case_library,
)
from app.services.parsing.document_sources import is_library_ready_entry
from app.services.parsing.parser import ParserService
from app.services.knowledge.wiki_compiler import compile_knowledge_wiki
from app.services.retrieval.visual_backend import rebuild_visual_embedding_cache
from app.utils.object_storage import get_object_storage


logger = logging.getLogger(__name__)

UPLOADED_DOCUMENT_SOURCE = "uploaded_documents"
DEFAULT_LIBRARY_TRACK = "pilot_main"
LIBRARY_REFRESH_CACHE_SCHEMA_VERSION = 1
CASE_LIBRARY_PIPELINE = "case_library"
VISUAL_CACHE_PIPELINE = "visual_cache"
_CASE_LIBRARY_REFRESH_LOCK = asyncio.Lock()
_CASE_LIBRARY_REFRESH_PENDING = False
_VISUAL_CACHE_REFRESH_LOCK = asyncio.Lock()
_VISUAL_CACHE_REFRESH_PENDING = False


def get_case_library_refresh_status_path(*, root: Path | None = None) -> Path:
    base_root = root or (BACKEND_ROOT / "data" / "knowledge_wiki")
    return base_root / "refresh_status.json"


def get_uploaded_document_library_cache_dir(*, root: Path | None = None) -> Path:
    base_root = root or (BACKEND_ROOT / "data" / "knowledge_wiki")
    return base_root / "library_refresh_cache"


def get_uploaded_document_library_cache_path(*, document_id: str, root: Path | None = None) -> Path:
    return get_uploaded_document_library_cache_dir(root=root) / f"{document_id}.json"


def read_uploaded_document_library_cache(*, document_id: str, root: Path | None = None) -> dict[str, Any] | None:
    path = get_uploaded_document_library_cache_path(document_id=document_id, root=root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    outline_entry = payload.get("outline_entry")
    block_entries = payload.get("block_entries")
    if not isinstance(outline_entry, dict) or not isinstance(block_entries, list):
        return None
    return payload


def delete_uploaded_document_library_cache(*, document_id: str, root: Path | None = None) -> None:
    path = get_uploaded_document_library_cache_path(document_id=document_id, root=root)
    path.unlink(missing_ok=True)


def read_case_library_refresh_status(*, status_path: Path | None = None) -> dict[str, Any]:
    path = status_path or get_case_library_refresh_status_path()
    if not path.exists():
        return _default_refresh_status()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_refresh_status()
    if not isinstance(payload, dict):
        return _default_refresh_status()
    default_payload = _default_refresh_status()
    pipelines = {
        **default_payload["pipelines"],
        **(payload.get("pipelines") if isinstance(payload.get("pipelines"), dict) else {}),
    }
    pipelines = {
        name: {
            **_default_pipeline_status(),
            **(value if isinstance(value, dict) else {}),
        }
        for name, value in pipelines.items()
    }
    return {
        **default_payload,
        **payload,
        "stats": dict(payload.get("stats") or {}),
        "pipelines": pipelines,
    }


def filter_baseline_case_library_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in entries
        if str(entry.get("source") or "").strip() != UPLOADED_DOCUMENT_SOURCE
    ]


def dedupe_case_library_entries(entries: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    deduped_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    ordered_keys: list[tuple[Any, ...]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = _case_library_entry_dedupe_key(entry, kind=kind)
        existing = deduped_by_key.get(key)
        if existing is None:
            ordered_keys.append(key)
            deduped_by_key[key] = entry
            continue
        existing_source = str(existing.get("source") or "").strip()
        incoming_source = str(entry.get("source") or "").strip()
        if existing_source != UPLOADED_DOCUMENT_SOURCE and incoming_source == UPLOADED_DOCUMENT_SOURCE:
            deduped_by_key[key] = entry
    return [deduped_by_key[key] for key in ordered_keys]


def _case_library_entry_dedupe_key(entry: dict[str, Any], *, kind: str) -> tuple[Any, ...]:
    sample_id = str(entry.get("sample_id") or "").strip()
    file_name = str(entry.get("file_name") or "").strip()
    track = str(entry.get("library_track") or entry.get("track") or "").strip()
    if kind == "outline":
        return ("outline", sample_id, file_name, track)
    section_id = str(entry.get("source_section_id") or "").strip()
    section_path = str(entry.get("section_path") or entry.get("heading_path") or "").strip()
    content = str(entry.get("content") or "").strip()
    return (
        "block",
        sample_id,
        file_name,
        track,
        section_id,
        section_path,
        entry.get("chunk_index"),
        entry.get("subchunk_index"),
        content[:500],
    )


async def request_case_library_refresh() -> None:
    await asyncio.gather(
        _request_case_library_pipeline_refresh(),
        _request_visual_cache_pipeline_refresh(),
    )


async def rebuild_case_library_and_knowledge_wiki() -> dict[str, Any]:
    settings = get_settings()
    outline_path = Path(settings.case_library_outline_path)
    block_path = Path(settings.case_library_block_path)
    summary_path = outline_path.parent / "case_library_summary.md"
    wiki_output_dir = BACKEND_ROOT / "data" / "knowledge_wiki"

    outline_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_output_dir.mkdir(parents=True, exist_ok=True)

    existing_outline_payload = _load_json_payload(outline_path)
    existing_block_payload = _load_json_payload(block_path)
    baseline_outline_entries = filter_baseline_case_library_entries(
        list(existing_outline_payload.get("entries") or [])
    )
    baseline_block_entries = filter_baseline_case_library_entries(
        list(existing_block_payload.get("entries") or [])
    )

    uploaded_outline_entries, uploaded_block_entries, refresh_meta = await _build_uploaded_case_library_entries()
    raw_combined_outline_entries = [*baseline_outline_entries, *uploaded_outline_entries]
    raw_combined_block_entries = [*baseline_block_entries, *uploaded_block_entries]
    combined_outline_entries = dedupe_case_library_entries(raw_combined_outline_entries, kind="outline")
    combined_block_entries = dedupe_case_library_entries(raw_combined_block_entries, kind="block")
    term_lexicon = build_corpus_term_lexicon(
        outline_entries=combined_outline_entries,
        block_entries=combined_block_entries,
    )
    summary = summarize_case_library(
        outline_entries=combined_outline_entries,
        block_entries=combined_block_entries,
    )

    outline_payload = {"entries": combined_outline_entries, "term_lexicon": term_lexicon}
    block_payload = {"entries": combined_block_entries, "term_lexicon": term_lexicon}
    outline_path.write_text(json.dumps(outline_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    block_path.write_text(json.dumps(block_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(
        render_case_library_markdown(summary=summary, outline_entries=combined_outline_entries),
        encoding="utf-8",
    )
    _write_knowledge_wiki(
        output_dir=wiki_output_dir,
        outline_entries=combined_outline_entries,
        block_entries=combined_block_entries,
        term_lexicon=term_lexicon,
    )
    return {
        "case_library_status": "succeeded",
        "knowledge_wiki_status": "succeeded",
        "baseline_outline_documents": len(baseline_outline_entries),
        "baseline_reusable_blocks": len(baseline_block_entries),
        "uploaded_outline_documents": len(uploaded_outline_entries),
        "uploaded_reusable_blocks": len(uploaded_block_entries),
        "deduped_outline_documents": len(raw_combined_outline_entries) - len(combined_outline_entries),
        "deduped_reusable_blocks": len(raw_combined_block_entries) - len(combined_block_entries),
        **refresh_meta,
    }


def _load_json_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _default_refresh_status() -> dict[str, Any]:
    return {
        "status": "idle",
        "requested_at": None,
        "started_at": None,
        "finished_at": None,
        "last_success_at": None,
        "pending": False,
        "error": None,
        "stats": {},
        "pipelines": {
            CASE_LIBRARY_PIPELINE: _default_pipeline_status(),
            VISUAL_CACHE_PIPELINE: _default_pipeline_status(),
        },
    }


def _default_pipeline_status() -> dict[str, Any]:
    return {
        "status": "idle",
        "requested_at": None,
        "started_at": None,
        "finished_at": None,
        "last_success_at": None,
        "pending": False,
        "error": None,
        "duration_seconds": None,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return round(max(0.0, (finished - started).total_seconds()), 3)


def _update_refresh_status(
    *,
    stats: dict[str, Any] | None = None,
    pipeline_updates: dict[str, dict[str, Any]] | None = None,
    **fields: Any,
) -> None:
    path = get_case_library_refresh_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = read_case_library_refresh_status(status_path=path)
    if fields:
        payload.update(fields)
    payload["stats"] = {
        **dict(payload.get("stats") or {}),
        **dict(stats or {}),
    }
    pipelines = {
        name: {
            **_default_pipeline_status(),
            **(value if isinstance(value, dict) else {}),
        }
        for name, value in dict(payload.get("pipelines") or {}).items()
    }
    if pipeline_updates:
        for name, updates in pipeline_updates.items():
            pipelines[name] = {
                **_default_pipeline_status(),
                **pipelines.get(name, {}),
                **dict(updates or {}),
            }
    payload["pipelines"] = pipelines
    payload = _derive_aggregate_refresh_status(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _derive_aggregate_refresh_status(payload: dict[str, Any]) -> dict[str, Any]:
    pipelines = {
        name: {
            **_default_pipeline_status(),
            **(value if isinstance(value, dict) else {}),
        }
        for name, value in dict(payload.get("pipelines") or {}).items()
    }
    statuses = {name: str(info.get("status") or "idle").strip().lower() or "idle" for name, info in pipelines.items()}
    pending = any(bool(info.get("pending")) for info in pipelines.values())
    active = [name for name, status in statuses.items() if status == "running"]
    queued = [name for name, status in statuses.items() if status == "queued"]
    failed = [name for name, status in statuses.items() if status == "failed"]
    succeeded = [name for name, status in statuses.items() if status == "succeeded"]

    if active:
        overall_status = "running"
    elif queued:
        overall_status = "queued"
    elif failed and succeeded:
        overall_status = "partial_failed"
    elif failed:
        overall_status = "failed"
    elif succeeded:
        overall_status = "succeeded"
    else:
        overall_status = "idle"

    errors: list[str] = []
    for name in failed:
        error_text = str((pipelines.get(name) or {}).get("error") or "").strip() or "failed"
        errors.append(f"{name}: {error_text}")

    payload["status"] = overall_status
    payload["pending"] = pending
    payload["error"] = "; ".join(errors) if errors else None
    payload["requested_at"] = _max_iso_timestamp(info.get("requested_at") for info in pipelines.values())
    payload["started_at"] = _max_iso_timestamp(info.get("started_at") for info in pipelines.values())
    payload["finished_at"] = _max_iso_timestamp(info.get("finished_at") for info in pipelines.values())
    payload["last_success_at"] = _max_iso_timestamp(info.get("last_success_at") for info in pipelines.values())
    return payload


def _max_iso_timestamp(values: Any) -> str | None:
    present_values = [str(value).strip() for value in values if str(value or "").strip()]
    if not present_values:
        return None
    return max(present_values)


async def _request_case_library_pipeline_refresh() -> None:
    global _CASE_LIBRARY_REFRESH_PENDING
    _CASE_LIBRARY_REFRESH_PENDING = True
    _update_refresh_status(
        stats={
            "case_library_status": "queued",
            "knowledge_wiki_status": "queued",
            "case_library_error": None,
            "knowledge_wiki_error": None,
            "case_library_duration_seconds": None,
        },
        pipeline_updates={
            CASE_LIBRARY_PIPELINE: {
                "status": "queued",
                "requested_at": _utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "pending": True,
                "duration_seconds": None,
            }
        },
    )
    if _CASE_LIBRARY_REFRESH_LOCK.locked():
        return
    async with _CASE_LIBRARY_REFRESH_LOCK:
        while _CASE_LIBRARY_REFRESH_PENDING:
            _CASE_LIBRARY_REFRESH_PENDING = False
            started_at = _utc_now_iso()
            _update_refresh_status(
                stats={
                    "case_library_status": "running",
                    "knowledge_wiki_status": "running",
                    "case_library_error": None,
                    "knowledge_wiki_error": None,
                    "case_library_duration_seconds": None,
                },
                pipeline_updates={
                    CASE_LIBRARY_PIPELINE: {
                        "status": "running",
                        "started_at": started_at,
                        "finished_at": None,
                        "error": None,
                        "pending": False,
                        "duration_seconds": None,
                    }
                },
            )
            try:
                refresh_result = await rebuild_case_library_and_knowledge_wiki()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to rebuild case library and AI wiki from uploaded historical documents")
                finished_at = _utc_now_iso()
                duration_seconds = _duration_seconds(started_at, finished_at)
                _update_refresh_status(
                    stats={
                        "case_library_status": "failed",
                        "knowledge_wiki_status": "failed",
                        "case_library_error": "Failed to rebuild case library from uploaded historical documents",
                        "knowledge_wiki_error": "Failed to compile AI wiki from uploaded historical documents",
                        "case_library_duration_seconds": duration_seconds,
                    },
                    pipeline_updates={
                        CASE_LIBRARY_PIPELINE: {
                            "status": "failed",
                            "finished_at": finished_at,
                            "error": "Failed to rebuild case library and AI wiki from uploaded historical documents",
                            "pending": _CASE_LIBRARY_REFRESH_PENDING,
                            "duration_seconds": duration_seconds,
                        }
                    },
                )
            else:
                finished_at = _utc_now_iso()
                duration_seconds = _duration_seconds(started_at, finished_at)
                _update_refresh_status(
                    stats={
                        **refresh_result,
                        "case_library_duration_seconds": duration_seconds,
                    },
                    pipeline_updates={
                        CASE_LIBRARY_PIPELINE: {
                            "status": "succeeded",
                            "finished_at": finished_at,
                            "last_success_at": finished_at,
                            "pending": _CASE_LIBRARY_REFRESH_PENDING,
                            "error": None,
                            "duration_seconds": duration_seconds,
                        }
                    },
                )


async def _request_visual_cache_pipeline_refresh() -> None:
    global _VISUAL_CACHE_REFRESH_PENDING
    _VISUAL_CACHE_REFRESH_PENDING = True
    _update_refresh_status(
        stats={
            "visual_cache_status": "queued",
            "visual_cache_error": None,
            "visual_cache_duration_seconds": None,
        },
        pipeline_updates={
            VISUAL_CACHE_PIPELINE: {
                "status": "queued",
                "requested_at": _utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "pending": True,
                "duration_seconds": None,
            }
        },
    )
    if _VISUAL_CACHE_REFRESH_LOCK.locked():
        return
    async with _VISUAL_CACHE_REFRESH_LOCK:
        while _VISUAL_CACHE_REFRESH_PENDING:
            _VISUAL_CACHE_REFRESH_PENDING = False
            started_at = _utc_now_iso()
            _update_refresh_status(
                stats={
                    "visual_cache_status": "running",
                    "visual_cache_error": None,
                    "visual_cache_duration_seconds": None,
                },
                pipeline_updates={
                    VISUAL_CACHE_PIPELINE: {
                        "status": "running",
                        "started_at": started_at,
                        "finished_at": None,
                        "error": None,
                        "pending": False,
                        "duration_seconds": None,
                    }
                },
            )
            try:
                refresh_result = await rebuild_uploaded_history_visual_cache()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to rebuild visual embedding cache during uploaded history refresh")
                finished_at = _utc_now_iso()
                duration_seconds = _duration_seconds(started_at, finished_at)
                _update_refresh_status(
                    stats={
                        "visual_cache_status": "failed",
                        "visual_cache_error": str(exc),
                        "visual_cache_duration_seconds": duration_seconds,
                    },
                    pipeline_updates={
                        VISUAL_CACHE_PIPELINE: {
                            "status": "failed",
                            "finished_at": finished_at,
                            "error": str(exc),
                            "pending": _VISUAL_CACHE_REFRESH_PENDING,
                            "duration_seconds": duration_seconds,
                        }
                    },
                )
            else:
                finished_at = _utc_now_iso()
                duration_seconds = _duration_seconds(started_at, finished_at)
                _update_refresh_status(
                    stats={
                        **refresh_result,
                        "visual_cache_duration_seconds": duration_seconds,
                    },
                    pipeline_updates={
                        VISUAL_CACHE_PIPELINE: {
                            "status": "succeeded",
                            "finished_at": finished_at,
                            "last_success_at": finished_at,
                            "pending": _VISUAL_CACHE_REFRESH_PENDING,
                            "error": None,
                            "duration_seconds": duration_seconds,
                        }
                    },
                )


async def rebuild_uploaded_history_visual_cache() -> dict[str, Any]:
    settings = get_settings()
    return await rebuild_visual_embedding_cache(
        output_path=settings.visual_embedding_cache_path,
        asset_types=["figure"],
        raw_document_doc_type="historical_proposal",
        raw_document_parse_status="done",
        backend_mode=settings.visual_embedding_backend,
        model_name=settings.visual_embedding_model,
        device=settings.visual_embedding_device,
        include_proxy_fallback=True,
    )


async def warm_uploaded_document_library_cache() -> dict[str, int]:
    _outline_entries, _block_entries, stats = await _build_uploaded_case_library_entries()
    return {
        "attempted_uploaded_documents": int(stats.get("attempted_uploaded_documents", 0)),
        "skipped_nonready_documents": int(stats.get("skipped_nonready_documents", 0)),
        "failed_uploaded_documents": int(stats.get("failed_uploaded_documents", 0)),
        "cache_hit_uploaded_documents": int(stats.get("cache_hit_uploaded_documents", 0)),
        "cache_miss_uploaded_documents": int(stats.get("cache_miss_uploaded_documents", 0)),
    }


def build_uploaded_document_library_cache_payload(
    *,
    document: Any,
    parsed_document: Any,
) -> dict[str, Any]:
    sample_entry = _build_uploaded_document_sample_entry(document=document)
    outline_entry = build_outline_library_entry(
        sample_entry=sample_entry,
        markdown=parsed_document.markdown,
        structure_hints=parsed_document.structure,
    )
    block_entries = build_reusable_block_entries(
        sample_entry=sample_entry,
        markdown=parsed_document.markdown,
        structure_hints=parsed_document.structure,
    )
    return {
        "schema_version": LIBRARY_REFRESH_CACHE_SCHEMA_VERSION,
        "document_id": str(document.id),
        "raw_document_id": str((document.meta or {}).get("raw_document_id") or ""),
        "cached_at": _utc_now_iso(),
        "outline_entry": outline_entry,
        "block_entries": block_entries,
    }


def write_uploaded_document_library_cache(
    *,
    document: Any,
    parsed_document: Any,
    root: Path | None = None,
) -> dict[str, Any]:
    payload = build_uploaded_document_library_cache_payload(
        document=document,
        parsed_document=parsed_document,
    )
    path = get_uploaded_document_library_cache_path(document_id=str(document.id), root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _build_uploaded_document_sample_entry(*, document: Any) -> dict[str, Any]:
    metadata = dict(getattr(document, "meta", {}) or {})
    profile_metadata = dict(metadata.get("document_profile") or {})
    profile_name = str(profile_metadata.get("name") or "").strip()
    sample_entry = {
        "sample_id": str(metadata.get("sample_id") or "").strip() or f"uploaded-{document.id}",
        "file_name": str(getattr(document, "filename", "") or ""),
        "file_format": str(getattr(document, "file_type", "") or ""),
        "phase_b_track": str(metadata.get("phase_b_track") or metadata.get("library_track") or DEFAULT_LIBRARY_TRACK),
        "library_track": str(metadata.get("library_track") or metadata.get("phase_b_track") or DEFAULT_LIBRARY_TRACK),
        "track": str(metadata.get("library_track") or metadata.get("phase_b_track") or DEFAULT_LIBRARY_TRACK),
        "source": UPLOADED_DOCUMENT_SOURCE,
        "ingestion_recommendation": metadata.get("ingestion_recommendation"),
        "parse_gate_status": metadata.get("parse_gate_status"),
        "parse_gate_reason": metadata.get("parse_gate_reason"),
        "high_risk_content_flags": list(metadata.get("high_risk_content_flags") or []),
    }
    if profile_name:
        sample_entry["profile"] = profile_name
        sample_entry["detected_profile"] = profile_name
    return sample_entry


def _stamp_uploaded_source(entry: dict[str, Any], *, sample_entry: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(entry)
    stamped["source"] = UPLOADED_DOCUMENT_SOURCE
    stamped.setdefault("sample_id", sample_entry.get("sample_id"))
    stamped.setdefault("file_name", sample_entry.get("file_name"))
    stamped.setdefault("file_format", sample_entry.get("file_format"))
    stamped.setdefault("library_track", sample_entry.get("library_track") or sample_entry.get("track"))
    return stamped


async def _build_uploaded_case_library_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    parser: ParserService | None = None
    storage: Any | None = None
    outline_entries: list[dict[str, Any]] = []
    block_entries: list[dict[str, Any]] = []
    attempted = 0
    skipped_nonready = 0
    failed = 0
    cache_hits = 0
    cache_misses = 0

    async with get_session_factory()() as session:
        documents = list(
            (
                await session.scalars(
                    select(Document)
                    .where(
                        Document.doc_type == "historical_proposal",
                        Document.parse_status == "done",
                    )
                    .order_by(Document.created_at.asc())
                )
            ).all()
        )

    for document in documents:
        attempted += 1
        sample_entry = _build_uploaded_document_sample_entry(document=document)
        if not is_library_ready_entry(sample_entry):
            skipped_nonready += 1
            continue

        cache_payload = read_uploaded_document_library_cache(document_id=str(document.id))
        if cache_payload is not None:
            outline_entry = cache_payload.get("outline_entry")
            cached_blocks = cache_payload.get("block_entries")
            if isinstance(outline_entry, dict) and isinstance(cached_blocks, list):
                outline_entries.append(_stamp_uploaded_source(outline_entry, sample_entry=sample_entry))
                block_entries.extend(
                    _stamp_uploaded_source(item, sample_entry=sample_entry)
                    for item in cached_blocks
                    if isinstance(item, dict)
                )
                cache_hits += 1
                continue

        cache_misses += 1
        if parser is None:
            parser = ParserService()
        if storage is None:
            storage = get_object_storage()
        materialized = storage.materialize(document.storage_path)
        try:
            parsed_document = await parser.parse_document(
                str(materialized.path),
                include_asset_enrichment=False,
            )
            payload = write_uploaded_document_library_cache(
                document=document,
                parsed_document=parsed_document,
            )
            outline_entry = payload.get("outline_entry")
            cached_blocks = payload.get("block_entries")
            if isinstance(outline_entry, dict):
                outline_entries.append(_stamp_uploaded_source(outline_entry, sample_entry=sample_entry))
            block_entries.extend(
                _stamp_uploaded_source(item, sample_entry=sample_entry)
                for item in (cached_blocks or [])
                if isinstance(item, dict)
            )
        except Exception:  # noqa: BLE001
            failed += 1
            logger.exception(
                "Failed to materialize uploaded historical document for case-library refresh: %s",
                document.filename,
            )
        finally:
            materialized.cleanup()

    return outline_entries, block_entries, {
        "attempted_uploaded_documents": attempted,
        "skipped_nonready_documents": skipped_nonready,
        "failed_uploaded_documents": failed,
        "cache_hit_uploaded_documents": cache_hits,
        "cache_miss_uploaded_documents": cache_misses,
    }


def _write_knowledge_wiki(
    *,
    output_dir: Path,
    outline_entries: list[dict[str, Any]],
    block_entries: list[dict[str, Any]],
    term_lexicon: dict[str, Any],
) -> None:
    bundle = compile_knowledge_wiki(
        outline_entries=outline_entries,
        block_entries=block_entries,
        term_lexicon=term_lexicon,
    )
    _prune_stale_wiki_pages(output_dir=output_dir, active_page_paths=set(bundle["pages"]))

    for relative_path, content in bundle["pages"].items():
        target_path = output_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

    (output_dir / "manifest.json").write_text(
        json.dumps(bundle["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    structured_assets = bundle["structured_assets"]
    (output_dir / "glossary.json").write_text(
        json.dumps(structured_assets["glossary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "product_cards.json").write_text(
        json.dumps(structured_assets["product_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "module_cards.json").write_text(
        json.dumps(structured_assets["module_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "equipment_cards.json").write_text(
        json.dumps(structured_assets["equipment_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "interface_cards.json").write_text(
        json.dumps(structured_assets["interface_cards"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "section_templates.json").write_text(
        json.dumps(structured_assets["section_templates"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "forbidden_phrases.json").write_text(
        json.dumps(structured_assets["forbidden_phrases"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_path = output_dir / "log.md"
    previous = log_path.read_text(encoding="utf-8") if log_path.exists() else "# AI Wiki Log\n\n"
    if not previous.endswith("\n"):
        previous += "\n"
    log_path.write_text(previous + "\n" + bundle["log_entry"], encoding="utf-8")


def _prune_stale_wiki_pages(*, output_dir: Path, active_page_paths: set[str]) -> None:
    keep_paths = set(active_page_paths)
    keep_paths.add("log.md")
    for path in output_dir.rglob("*.md"):
        relative_path = path.relative_to(output_dir).as_posix()
        if relative_path in keep_paths:
            continue
        path.unlink(missing_ok=True)
    for directory in sorted((path for path in output_dir.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue
