from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import _build_parse_failure_metadata, _parse_and_index_document
from app.config import BACKEND_ROOT, REPO_ROOT
from app.db import get_db_session, get_session_factory
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.figure_asset import FigureAsset
from app.models.job import Job
from app.models.raw_document import RawDocument
from app.schemas.artifacts import JobAcceptedData
from app.schemas.common import APIResponse
from app.services.knowledge import submit_case_library_refresh_job
from app.services.task_queue import get_background_task_queue
from app.utils.object_storage import get_object_storage


logger = logging.getLogger(__name__)
router = APIRouter()

MaterialRoute = Literal[
    "main_indexed",
    "review_pending",
    "holdout_eval",
    "conversion_required",
    "conversion_failed",
    "excluded",
]

MANIFEST_PATH = BACKEND_ROOT / "data" / "sample_manifests" / "sample_manifest.json"
MATERIAL_STATE_PATH = BACKEND_ROOT / "data" / "sample_manifests" / "material_audit_state.json"
DEFAULT_ROUTE_BY_PHASE = {
    "pilot_main": "main_indexed",
    "needs_review": "review_pending",
    "holdout_eval": "holdout_eval",
    "ocr_asset_only": "conversion_required",
}
DOC_TYPE_BY_ROUTE = {
    "main_indexed": "historical_proposal",
    "review_pending": "historical_review",
    "holdout_eval": "holdout_eval",
    "conversion_required": "legacy_conversion",
    "conversion_failed": "legacy_conversion",
    "excluded": "excluded",
}
DOC_TYPE_TO_ROUTE = {
    "historical_proposal": "main_indexed",
    "historical_review": "review_pending",
    "holdout_eval": "holdout_eval",
    "legacy_conversion": "conversion_required",
    "excluded": "excluded",
}
LIBRARY_DOC_TYPES = set(DOC_TYPE_TO_ROUTE)
MATERIAL_STATUS_LABELS = {
    "not_ingested": "未入库",
    "processing": "解析中",
    "cloud_parse_required": "需阿里云解析",
    "parse_failed": "解析失败",
    "parse_insufficient": "解析不充分",
    "main_indexed": "主库已入库",
    "review_pending": "待审阅",
    "holdout_eval": "验证集",
    "conversion_required": "待转换",
    "conversion_failed": "转换失败",
    "excluded": "已排除",
}


class MaterialRouteRequest(BaseModel):
    route: MaterialRoute
    reason: str | None = None


class MaterialRebuildRequest(BaseModel):
    sample_ids: list[str] | None = None
    force: bool = False


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_manifest_entries() -> list[dict[str, Any]]:
    manifest = _load_json(MANIFEST_PATH)
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _load_material_state() -> dict[str, Any]:
    payload = _load_json(MATERIAL_STATE_PATH)
    return {
        "schema_version": 1,
        "materials": dict(payload.get("materials") or {}),
    }


def _save_material_state(payload: dict[str, Any]) -> None:
    _write_json(MATERIAL_STATE_PATH, payload)


def _default_route_for_entry(entry: dict[str, Any]) -> MaterialRoute:
    phase_track = str(entry.get("phase_b_track") or "").strip()
    route = DEFAULT_ROUTE_BY_PHASE.get(phase_track)
    if route:
        return route  # type: ignore[return-value]
    recommendation = str(entry.get("ingestion_recommendation") or "").strip()
    if recommendation == "conversion_required":
        return "conversion_required"
    if recommendation in {"main_vector_ready", "text_primary_with_asset_review"}:
        return "review_pending"
    return "review_pending"


def _route_for_entry(entry: dict[str, Any], state: dict[str, Any]) -> MaterialRoute:
    entry_route = str(entry.get("route") or "").strip()
    if entry_route in DOC_TYPE_BY_ROUTE:
        return entry_route  # type: ignore[return-value]
    sample_id = str(entry.get("sample_id") or "")
    material_state = dict((state.get("materials") or {}).get(sample_id) or {})
    route = str(material_state.get("route") or "").strip()
    if route in DOC_TYPE_BY_ROUTE:
        return route  # type: ignore[return-value]
    return _default_route_for_entry(entry)


def _sample_source_path(entry: dict[str, Any]) -> Path:
    raw_path = str(entry.get("file_path") or "").strip()
    if raw_path:
        return Path(raw_path)
    return REPO_ROOT / "private_samples" / "real_proposals" / str(entry.get("file_name") or "")


def _route_for_uploaded_document(document: Document) -> MaterialRoute:
    metadata = dict(document.meta or {})
    route = str(metadata.get("material_route") or "").strip()
    if route in DOC_TYPE_BY_ROUTE:
        return route  # type: ignore[return-value]
    return DOC_TYPE_TO_ROUTE.get(str(document.doc_type or "").strip(), "review_pending")  # type: ignore[return-value]


def _uploaded_document_entry(document: Document) -> dict[str, Any]:
    metadata = dict(document.meta or {})
    route = _route_for_uploaded_document(document)
    sample_id = str(metadata.get("sample_id") or "").strip() or f"uploaded-{document.id}"
    file_type = str(document.file_type or "").strip().lower()
    return {
        "sample_id": sample_id,
        "file_name": document.filename,
        "file_format": file_type or Path(document.filename).suffix.lstrip(".").lower(),
        "file_size_bytes": document.file_size_bytes,
        "file_path": document.storage_path,
        "source_kind": "uploaded_document",
        "source_exists": True,
        "document_id": str(document.id),
        "phase_b_track": metadata.get("library_track") or "uploaded",
        "suggested_track": metadata.get("material_route") or route,
        "route": route,
        "detected_profile": metadata.get("detected_profile") or metadata.get("parser_backend_used"),
        "ingestion_recommendation": metadata.get("ingestion_recommendation") or "uploaded_historical_material",
        "metrics": {
            "image_count": int(metadata.get("image_count") or 0),
            "table_count": int(metadata.get("table_count") or 0),
            "chunk_count": int(metadata.get("chunk_count") or 0),
            "indexed_chunk_count": int(metadata.get("indexed_chunk_count") or 0),
        },
        "high_risk_content_flags": list(metadata.get("high_risk_content_flags") or []),
    }


def _material_base_metadata(entry: dict[str, Any], route: str) -> dict[str, Any]:
    return {
        "sample_id": entry.get("sample_id"),
        "phase_b_track": entry.get("phase_b_track"),
        "library_track": "pilot_main" if route == "main_indexed" else entry.get("phase_b_track"),
        "material_route": route,
        "ingestion_recommendation": entry.get("ingestion_recommendation"),
        "detected_profile": entry.get("detected_profile"),
        "document_profile": {
            "name": entry.get("detected_profile"),
            "metrics": dict(entry.get("metrics") or {}),
        },
        "high_risk_content_flags": list(entry.get("high_risk_content_flags") or []),
        "source_path": str(_sample_source_path(entry)),
        "source_kind": entry.get("source_kind") or "private_sample",
    }


async def _latest_document_for_filename(*, session: AsyncSession, filename: str) -> Document | None:
    result = await session.scalars(
        select(Document)
        .where(Document.filename == filename)
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    return result.first()


async def _resolve_material_document(*, session: AsyncSession, entry: dict[str, Any]) -> Document | None:
    document_id = entry.get("document_id")
    if document_id:
        try:
            return await session.get(Document, UUID(str(document_id)))
        except (TypeError, ValueError):
            return None
    return await _latest_document_for_filename(session=session, filename=str(entry.get("file_name") or ""))


async def _load_uploaded_material_entries(*, session: AsyncSession, manifest_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest_sample_ids = {str(entry.get("sample_id") or "").strip() for entry in manifest_entries}
    manifest_file_names = {str(entry.get("file_name") or "").strip() for entry in manifest_entries}
    result = await session.scalars(
        select(Document)
        .where(Document.doc_type.in_(LIBRARY_DOC_TYPES))
        .order_by(Document.created_at.desc())
    )

    uploaded_entries: list[dict[str, Any]] = []
    seen_sample_ids = set(manifest_sample_ids)
    for document in result.all():
        metadata = dict(document.meta or {})
        sample_id = str(metadata.get("sample_id") or "").strip() or f"uploaded-{document.id}"
        if sample_id in seen_sample_ids:
            continue
        if not str(metadata.get("sample_id") or "").strip() and document.filename in manifest_file_names:
            continue
        seen_sample_ids.add(sample_id)
        uploaded_entries.append(_uploaded_document_entry(document))
    return uploaded_entries


async def _load_all_material_entries(*, session: AsyncSession) -> list[dict[str, Any]]:
    manifest_entries = _load_manifest_entries()
    uploaded_entries = await _load_uploaded_material_entries(session=session, manifest_entries=manifest_entries)
    return [*manifest_entries, *uploaded_entries]


async def _find_material_entry(*, session: AsyncSession, sample_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in await _load_all_material_entries(session=session) if str(item.get("sample_id") or "") == sample_id),
        None,
    )


async def _build_material_item(
    *,
    session: AsyncSession,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    sample_id = str(entry.get("sample_id") or "")
    route = entry.get("route") if str(entry.get("route") or "") in DOC_TYPE_BY_ROUTE else _route_for_entry(entry, state)
    state_item = dict((state.get("materials") or {}).get(sample_id) or {})
    source_kind = str(entry.get("source_kind") or "private_sample")
    path = _sample_source_path(entry)
    document = await _resolve_material_document(session=session, entry=entry)
    raw_document: RawDocument | None = None
    if document is not None:
        raw_document_id = (document.meta or {}).get("raw_document_id")
        if raw_document_id:
            try:
                raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
            except (TypeError, ValueError):
                raw_document = None

    chunk_count = 0
    indexed_chunk_count = 0
    if document is not None:
        chunk_count = int(
            await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)) or 0
        )
        indexed_chunk_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == document.id, Chunk.qdrant_point_id.is_not(None))
            )
            or 0
        )

    figure_asset_count = 0
    storage_fallback_asset_count = 0
    if raw_document is not None:
        assets = (
            await session.scalars(select(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
        ).all()
        figure_asset_count = len(assets)
        storage_fallback_asset_count = sum(1 for asset in assets if bool((asset.meta or {}).get("storage_fallback")))

    parse_metadata = dict(document.meta or {}) if document is not None else {}
    material_status = _resolve_material_status(route=route, document=document, parse_metadata=parse_metadata)
    return {
        "sample_id": sample_id,
        "file_name": entry.get("file_name"),
        "file_format": entry.get("file_format"),
        "file_size_bytes": entry.get("file_size_bytes"),
        "source_path": str(path),
        "source_kind": source_kind,
        "source_exists": bool(entry.get("source_exists")) if source_kind == "uploaded_document" else path.exists(),
        "phase_b_track": entry.get("phase_b_track"),
        "suggested_track": entry.get("suggested_track"),
        "route": route,
        "route_reason": state_item.get("reason"),
        "route_updated_at": state_item.get("updated_at"),
        "detected_profile": entry.get("detected_profile"),
        "ingestion_recommendation": entry.get("ingestion_recommendation"),
        "manifest_metrics": dict(entry.get("metrics") or {}),
        "high_risk_content_flags": list(entry.get("high_risk_content_flags") or []),
        "document_id": str(document.id) if document is not None else None,
        "raw_document_id": str(raw_document.id) if raw_document is not None else None,
        "doc_type": document.doc_type if document is not None else DOC_TYPE_BY_ROUTE.get(route),
        "parse_status": document.parse_status if document is not None else "not_ingested",
        "material_status": material_status["status"],
        "material_status_label": material_status["label"],
        "material_status_reason": material_status["reason"],
        "requires_cloud_parse": material_status["requires_cloud_parse"],
        "parser_backend": parse_metadata.get("parser_backend_used") or parse_metadata.get("parser_backend"),
        "parse_gate_status": parse_metadata.get("parse_gate_status"),
        "parse_gate_reason": parse_metadata.get("parse_gate_reason"),
        "chunk_count": chunk_count,
        "indexed_chunk_count": indexed_chunk_count,
        "skipped_chunk_count": int(parse_metadata.get("skipped_chunk_count") or 0),
        "figure_asset_count": figure_asset_count,
        "storage_fallback_asset_count": storage_fallback_asset_count,
        "image_count": int(parse_metadata.get("image_count") or 0),
        "table_count": int(parse_metadata.get("table_count") or 0),
        "quality_flags": _derive_quality_flags(
            route=route,
            document=document,
            parse_metadata=parse_metadata,
            material_status=material_status,
            figure_asset_count=figure_asset_count,
            storage_fallback_asset_count=storage_fallback_asset_count,
            entry=entry,
        ),
    }


def _resolve_material_status(*, route: str, document: Document | None, parse_metadata: dict[str, Any]) -> dict[str, Any]:
    if document is None:
        status = route if route in {"conversion_required", "conversion_failed", "excluded"} else "not_ingested"
        return {
            "status": status,
            "label": MATERIAL_STATUS_LABELS.get(status, status),
            "reason": None,
            "requires_cloud_parse": False,
        }

    parse_status = str(document.parse_status or "").strip().lower()
    requires_cloud_parse = _requires_cloud_parse(document=document, parse_metadata=parse_metadata)
    if parse_status in {"pending", "queued", "parsing"}:
        status = "processing"
    elif requires_cloud_parse:
        status = "cloud_parse_required"
    elif parse_status == "failed":
        status = "parse_failed"
    elif parse_status == "parse_insufficient":
        status = "parse_insufficient"
    elif parse_status == "done":
        status = route if route in MATERIAL_STATUS_LABELS else "review_pending"
    else:
        status = parse_status or "not_ingested"

    return {
        "status": status,
        "label": MATERIAL_STATUS_LABELS.get(status, status),
        "reason": _material_status_reason(status=status, parse_metadata=parse_metadata),
        "requires_cloud_parse": requires_cloud_parse,
    }


def _material_status_reason(*, status: str, parse_metadata: dict[str, Any]) -> str | None:
    if status == "cloud_parse_required":
        return (
            str(parse_metadata.get("parse_gate_reason") or "").strip()
            or str(parse_metadata.get("parse_error") or "").strip()
            or "local_parser_insufficient"
        )
    if status in {"parse_failed", "parse_insufficient"}:
        return str(parse_metadata.get("parse_error") or parse_metadata.get("parse_gate_reason") or "").strip() or None
    return None


def _requires_cloud_parse(*, document: Document, parse_metadata: dict[str, Any]) -> bool:
    if bool(parse_metadata.get("requires_cloud_parse")):
        return True
    parse_status = str(document.parse_status or "").strip().lower()
    if parse_status not in {"failed", "parse_insufficient"}:
        return False
    parser_backend = str(
        parse_metadata.get("parser_backend_used") or parse_metadata.get("parser_backend") or ""
    ).strip().lower()
    parse_gate_reason = str(parse_metadata.get("parse_gate_reason") or "").strip().lower()
    parse_error = str(parse_metadata.get("parse_error") or "").strip().lower()
    cloud_hints = (
        "cloudparserequired",
        "aliyun docmind",
        "document parsing timed out",
        "exit_code",
        "fallback_binary_parser",
        "local parser",
        "produced insufficient output",
    )
    return (
        parser_backend == "fallback"
        or parse_gate_reason in {"fallback_binary_parser", "cloud_parse_required"}
        or any(hint in parse_error for hint in cloud_hints)
    )


async def _build_material_detail(
    *,
    session: AsyncSession,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    item = await _build_material_item(session=session, entry=entry, state=state)
    document_id = item.get("document_id")
    raw_document_id = item.get("raw_document_id")
    chunks: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []

    if document_id:
        try:
            document_uuid = UUID(str(document_id))
        except (TypeError, ValueError):
            document_uuid = None
        if document_uuid is not None:
            chunk_rows = (
                await session.scalars(
                    select(Chunk)
                    .where(Chunk.document_id == document_uuid)
                    .order_by(Chunk.chunk_index.asc())
                    .limit(120)
                )
            ).all()
            chunks = [_chunk_to_audit_item(chunk) for chunk in chunk_rows]

    if raw_document_id:
        try:
            raw_document_uuid = UUID(str(raw_document_id))
        except (TypeError, ValueError):
            raw_document_uuid = None
        if raw_document_uuid is not None:
            asset_rows = (
                await session.scalars(
                    select(FigureAsset)
                    .where(FigureAsset.raw_document_id == raw_document_uuid)
                    .order_by(FigureAsset.asset_type.asc(), FigureAsset.page_no.asc().nulls_last(), FigureAsset.created_at.asc())
                )
            ).all()
            assets = [_asset_to_audit_item(asset) for asset in asset_rows]

    return {**item, "chunks": chunks, "assets": assets}


def _chunk_to_audit_item(chunk: Chunk) -> dict[str, Any]:
    metadata = dict(chunk.meta or {})
    content = str(chunk.content or "")
    return {
        "id": str(chunk.id),
        "chunk_index": chunk.chunk_index,
        "chunk_type": chunk.chunk_type,
        "heading_path": chunk.heading_path,
        "token_count": chunk.token_count,
        "indexed": chunk.qdrant_point_id is not None,
        "qdrant_point_id": str(chunk.qdrant_point_id) if chunk.qdrant_point_id else None,
        "content_preview": content[:600],
        "metadata": {
            "section_path": metadata.get("section_path"),
            "source_section_id": metadata.get("source_section_id"),
            "content_risk_level": metadata.get("content_risk_level"),
            "indexing_reasons": metadata.get("indexing_reasons"),
            "review_required": metadata.get("review_required"),
            "table_asset_id": metadata.get("table_asset_id"),
            "table_asset_status": metadata.get("table_asset_status"),
        },
    }


def _asset_to_audit_item(asset: FigureAsset) -> dict[str, Any]:
    metadata = dict(asset.meta or {})
    return {
        "id": str(asset.id),
        "asset_type": asset.asset_type,
        "title": asset.title,
        "caption": asset.caption,
        "page_no": asset.page_no,
        "asset_uri": asset.asset_uri,
        "reuse_mode": asset.reuse_mode,
        "parse_confidence": str(asset.parse_confidence) if asset.parse_confidence is not None else None,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "visual_role": metadata.get("visual_role"),
        "width": metadata.get("width") or metadata.get("image_width") or metadata.get("pixel_width"),
        "height": metadata.get("height") or metadata.get("image_height") or metadata.get("pixel_height"),
        "source_ref": metadata.get("source_ref"),
        "source_section_id": metadata.get("source_section_id"),
        "heading_path": metadata.get("heading_path"),
        "section_path": metadata.get("section_path"),
        "storage_fallback": bool(metadata.get("storage_fallback")),
        "preserve_in_vector_db": metadata.get("preserve_in_vector_db", True),
        "review_required": bool(metadata.get("review_required")),
        "asset_audit_status": metadata.get("asset_audit_status"),
        "asset_quality_score": metadata.get("asset_quality_score"),
        "asset_audit_reasons": list(metadata.get("asset_audit_reasons") or []),
        "quality_flags": list(metadata.get("quality_flags") or []),
        "raw_table_markdown": metadata.get("raw_table_markdown"),
        "table_profile": metadata.get("table_profile"),
        "semantic_summary": metadata.get("semantic_summary"),
        "context_before": metadata.get("context_before"),
        "context_after": metadata.get("context_after"),
        "metadata": metadata,
    }


def _derive_quality_flags(
    *,
    route: str,
    document: Document | None,
    parse_metadata: dict[str, Any],
    material_status: dict[str, Any],
    figure_asset_count: int,
    storage_fallback_asset_count: int,
    entry: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    manifest_image_count = int((entry.get("metrics") or {}).get("image_count") or 0)
    if document is None and route not in {"conversion_required", "excluded"}:
        flags.append("not_ingested")
    if document is not None and document.parse_status != "done":
        status = str(material_status.get("status") or "")
        if status == "cloud_parse_required":
            flags.append("requires_aliyun_docmind")
        elif status == "parse_failed":
            flags.append("parse_failed")
        elif status == "parse_insufficient":
            flags.append("parse_insufficient")
        elif status == "processing":
            flags.append("processing")
        else:
            flags.append(f"parse_status:{document.parse_status}")
    if manifest_image_count > 0 and figure_asset_count == 0:
        flags.append("expected_images_but_no_figure_assets")
    if figure_asset_count > 0 and storage_fallback_asset_count == figure_asset_count:
        flags.append("all_assets_are_storage_fallback")
    if route == "conversion_required":
        flags.append("conversion_required")
    return flags


def _summarize_materials(items: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: dict[str, int] = {}
    for item in items:
        route = str(item.get("route") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
    return {
        "total": len(items),
        "route_counts": route_counts,
        "ingested_count": sum(1 for item in items if item.get("document_id")),
        "main_indexed_count": route_counts.get("main_indexed", 0),
        "figure_asset_count": sum(int(item.get("figure_asset_count") or 0) for item in items),
        "indexed_chunk_count": sum(int(item.get("indexed_chunk_count") or 0) for item in items),
    }


@router.get("/library/materials", response_model=APIResponse[dict[str, Any]])
async def list_materials(session: AsyncSession = Depends(get_db_session)) -> APIResponse[dict[str, Any]]:
    entries = await _load_all_material_entries(session=session)
    state = _load_material_state()
    items = [await _build_material_item(session=session, entry=entry, state=state) for entry in entries]
    return APIResponse(code=200, message="success", data={"items": items, "summary": _summarize_materials(items)})


@router.get("/library/materials/{sample_id}", response_model=APIResponse[dict[str, Any]])
async def get_material(sample_id: str, session: AsyncSession = Depends(get_db_session)) -> APIResponse[dict[str, Any]]:
    entry = await _find_material_entry(session=session, sample_id=sample_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    state = _load_material_state()
    item = await _build_material_detail(session=session, entry=entry, state=state)
    return APIResponse(code=200, message="success", data=item)


@router.post("/library/materials/{sample_id}/route", response_model=APIResponse[dict[str, Any]])
async def route_material(
    sample_id: str,
    payload: MaterialRouteRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[dict[str, Any]]:
    entry = await _find_material_entry(session=session, sample_id=sample_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    state = _load_material_state()
    materials = dict(state.get("materials") or {})
    materials[sample_id] = {
        **dict(materials.get(sample_id) or {}),
        "route": payload.route,
        "reason": payload.reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state["materials"] = materials
    _save_material_state(state)

    document = await _resolve_material_document(session=session, entry=entry)
    if document is not None:
        document.doc_type = DOC_TYPE_BY_ROUTE[payload.route]
        document.meta = {
            **(document.meta or {}),
            **_material_base_metadata(entry, payload.route),
            "manual_route_reason": payload.reason,
            "manual_route_updated_at": materials[sample_id]["updated_at"],
        }
        raw_document_id = (document.meta or {}).get("raw_document_id")
        if raw_document_id:
            try:
                raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
            except (TypeError, ValueError):
                raw_document = None
            if raw_document is not None:
                raw_document.doc_type = document.doc_type
                raw_document.corpus_scope = "global" if payload.route == "main_indexed" else "review"
                raw_document.meta = {**(raw_document.meta or {}), **document.meta}
        await session.commit()

    if payload.route == "main_indexed":
        # Phase 1 / #4 review fix: route case library refresh through the
        # maintenance queue so it is bounded by MAINTENANCE_JOB_WORKER_COUNT and
        # visible in GET /api/v1/jobs/queue.
        submit_case_library_refresh_job()

    item = await _build_material_item(session=session, entry=entry, state=state)
    return APIResponse(code=200, message="success", data=item)


@router.post(
    "/library/materials/{sample_id}/reparse",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_material(
    sample_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[JobAcceptedData]:
    return await rebuild_materials(
        MaterialRebuildRequest(sample_ids=[sample_id], force=True),
        background_tasks=background_tasks,
        session=session,
    )


@router.post(
    "/library/materials/rebuild",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def rebuild_materials(
    payload: MaterialRebuildRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[JobAcceptedData]:
    entries = await _load_all_material_entries(session=session)
    sample_filter = {str(item) for item in (payload.sample_ids or []) if str(item).strip()}
    selected_entries = [
        entry for entry in entries if not sample_filter or str(entry.get("sample_id") or "") in sample_filter
    ]
    if sample_filter and len(selected_entries) != len(sample_filter):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more materials were not found")
    queue = get_background_task_queue("library_parse")
    if len(selected_entries) == 1:
        sample_id = str(selected_entries[0].get("sample_id") or "")
        active_job_id = queue.active_job_id(_material_dedupe_key(sample_id))
        if active_job_id is not None:
            active_job = await session.get(Job, active_job_id)
            if active_job is not None and active_job.status in {"queued", "running"}:
                return APIResponse(
                    code=202,
                    message="success",
                    data=JobAcceptedData(
                        job_id=active_job.id,
                        status=active_job.status,
                        resource_id=None,
                        next_poll=f"/api/v1/jobs/{active_job.id}",
                    ),
                )

    job = Job(
        project_id=None,
        job_type="library_rebuild",
        status="running",
        trace_id=uuid.uuid4().hex,
        input_ref={"sample_ids": list(sample_filter), "force": payload.force, "material_count": len(selected_entries)},
        started_at=datetime.now(timezone.utc),
        output_ref={
            "progress": {"stage": "queueing_materials", "completed_materials": 0, "total_materials": len(selected_entries)},
            "summary": {"queued": 0, "imported": 0, "skipped": 0, "failed": 0, "duplicates": 0},
            "child_job_ids": [],
        },
    )
    session.add(job)
    await session.flush()

    child_specs: list[tuple[UUID, dict[str, Any]]] = []
    summary = {"queued": 0, "imported": 0, "skipped": 0, "failed": 0, "duplicates": 0}
    state = _load_material_state()
    for entry in selected_entries:
        route = _route_for_entry(entry, state)
        sample_id = str(entry.get("sample_id") or "")
        file_name = str(entry.get("file_name") or "")
        if route in {"conversion_required", "conversion_failed", "excluded"}:
            summary["skipped"] += 1
            continue
        source_path = _sample_source_path(entry)
        if not source_path.exists():
            summary["failed"] += 1
            _record_material_state(sample_id=sample_id, route=route, status="missing_source", reason=str(source_path))
            continue
        existing = await _latest_document_for_filename(session=session, filename=file_name)
        if existing is not None and existing.parse_status == "done" and not payload.force:
            summary["skipped"] += 1
            continue
        if queue.active_job_id(_material_dedupe_key(sample_id)) is not None:
            summary["duplicates"] += 1
            summary["skipped"] += 1
            continue

        child_job = Job(
            project_id=None,
            job_type="library_material_rebuild",
            status="queued",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "parent_job_id": str(job.id),
                "sample_id": sample_id,
                "file_name": file_name,
                "force": payload.force,
            },
            output_ref={
                "progress": {
                    "stage": "queued",
                    "completed_materials": 0,
                    "total_materials": 1,
                    "current_sample_id": sample_id,
                    "current_file_name": file_name,
                }
            },
        )
        session.add(child_job)
        await session.flush()
        child_specs.append((child_job.id, dict(entry)))
        summary["queued"] += 1

    child_job_ids = [str(child_id) for child_id, _entry in child_specs]
    job.output_ref = {
        "progress": {
            "stage": "queued" if child_job_ids else ("failed" if summary["failed"] else "completed"),
            "completed_materials": summary["skipped"] + summary["failed"],
            "total_materials": len(selected_entries),
        },
        "summary": summary,
        "child_job_ids": child_job_ids,
    }
    if not child_job_ids:
        job.status = "failed" if summary["failed"] else "succeeded"
        job.error_code = "MaterialRebuildNoRunnableItems" if summary["failed"] else None
        job.completed_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(job)

    for child_id, entry in child_specs:
        queue.submit(
            job_id=child_id,
            job_type="library_material_rebuild",
            label=str(entry.get("file_name") or child_id),
            dedupe_key=_material_dedupe_key(str(entry.get("sample_id") or "")),
            priority=30,
            run=lambda child_id=child_id, parent_job_id=job.id, entry=dict(entry): _run_rebuild_material_item_job(
                child_id,
                parent_job_id,
                entry,
                payload.force,
            ),
        )

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(job_id=job.id, status=job.status, resource_id=None, next_poll=f"/api/v1/jobs/{job.id}"),
    )


def _material_dedupe_key(sample_id: str) -> str:
    return f"library:material:{sample_id}"


async def _run_rebuild_material_item_job(job_id: UUID, parent_job_id: UUID, entry: dict[str, Any], force: bool) -> None:
    state = _load_material_state()
    storage = get_object_storage()
    imported = 0
    skipped = 0
    failed = 0
    async with get_session_factory()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        route = _route_for_entry(entry, state)
        sample_id = str(entry.get("sample_id") or "")
        file_name = str(entry.get("file_name") or "")
        job.output_ref = {
            "progress": {
                "stage": "parsing",
                "completed_materials": 0,
                "total_materials": 1,
                "current_sample_id": sample_id,
                "current_file_name": file_name,
            }
        }
        await session.commit()

        try:
            source_path = _sample_source_path(entry)
            if not source_path.exists():
                failed += 1
                _record_material_state(sample_id=sample_id, route=route, status="missing_source", reason=str(source_path))
            elif route in {"conversion_required", "conversion_failed", "excluded"}:
                skipped += 1
            else:
                existing = await _latest_document_for_filename(session=session, filename=file_name)
                if existing is not None and existing.parse_status == "done" and not force:
                    skipped += 1
                else:
                    storage_path = storage.save(source_path, prefix=f"library_{sample_id}_")
                    metadata = _material_base_metadata(entry, route)
                    if existing is None:
                        document = Document(
                            project_id=None,
                            filename=file_name,
                            file_type=source_path.suffix.lower().lstrip("."),
                            file_size_bytes=source_path.stat().st_size,
                            storage_path=storage_path,
                            doc_type=DOC_TYPE_BY_ROUTE[route],
                            parse_status="parsing",
                            meta=metadata,
                        )
                        session.add(document)
                        await session.flush()
                    else:
                        document = existing
                        document.project_id = None
                        document.file_type = source_path.suffix.lower().lstrip(".")
                        document.file_size_bytes = source_path.stat().st_size
                        document.storage_path = storage_path
                        document.doc_type = DOC_TYPE_BY_ROUTE[route]
                        document.parse_status = "parsing"
                        document.meta = {**(document.meta or {}), **metadata}
                        await session.flush()
                    try:
                        await _parse_and_index_document(session=session, document=document, base_metadata=metadata)
                        imported += 1
                        _record_material_state(sample_id=sample_id, route=route, status="parsed", reason=None)
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        logger.exception("Failed to rebuild library material: %s", file_name)
                        document.parse_status = "failed"
                        document.meta = {**(document.meta or {}), **_build_parse_failure_metadata(exc)}
                        _record_material_state(sample_id=sample_id, route=route, status="failed", reason=str(exc))

            job.status = "succeeded" if failed == 0 else "failed"
            job.error_code = None if failed == 0 else "MaterialRebuildItemFailed"
            job.output_ref = {
                "progress": {
                    "stage": "completed" if failed == 0 else "failed",
                    "completed_materials": 1,
                    "total_materials": 1,
                    "current_sample_id": sample_id,
                    "current_file_name": file_name,
                },
                "summary": {"imported": imported, "skipped": skipped, "failed": failed},
            }
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("Material rebuild job failed")
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error_code = exc.__class__.__name__[:50]
                job.output_ref = {**(job.output_ref or {}), "error": str(exc)}
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
        finally:
            await _update_library_parent_job(parent_job_id)


async def recover_library_material_rebuild_jobs_on_startup() -> dict[str, int]:
    """Requeue library material rebuild jobs left queued/running by a previous backend process."""

    queue = get_background_task_queue("library_parse")
    recovered = 0
    skipped_terminal_parent = 0
    failed_invalid = 0
    task_specs: list[tuple[UUID, UUID, dict[str, Any], bool]] = []
    parent_ids_to_refresh: set[UUID] = set()

    async with get_session_factory()() as session:
        entries = await _load_all_material_entries(session=session)
        entries_by_sample_id = {str(entry.get("sample_id") or ""): dict(entry) for entry in entries}
        result = await session.scalars(
            select(Job)
            .where(Job.job_type == "library_material_rebuild")
            .where(Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at.asc())
        )
        jobs = result.all()
        for job in jobs:
            input_ref = dict(job.input_ref or {})
            sample_id = str(input_ref.get("sample_id") or "").strip()
            raw_force = input_ref.get("force")
            force = raw_force if isinstance(raw_force, bool) else str(raw_force or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
            try:
                parent_job_id = UUID(str(input_ref.get("parent_job_id") or ""))
            except (TypeError, ValueError):
                job.status = "failed"
                job.error_code = "MissingParentJobId"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": "library_material_rebuild job is missing parent_job_id",
                    "progress": {**((job.output_ref or {}).get("progress") or {}), "stage": "failed"},
                }
                job.completed_at = datetime.now(timezone.utc)
                failed_invalid += 1
                continue

            parent_job = await session.get(Job, parent_job_id)
            if parent_job is None or parent_job.status in {"succeeded", "failed"}:
                skipped_terminal_parent += 1
                continue

            entry = entries_by_sample_id.get(sample_id)
            if entry is None:
                job.status = "failed"
                job.error_code = "MaterialEntryNotFound"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": f"Material entry not found: {sample_id}",
                    "progress": {**((job.output_ref or {}).get("progress") or {}), "stage": "failed"},
                }
                job.completed_at = datetime.now(timezone.utc)
                failed_invalid += 1
                parent_ids_to_refresh.add(parent_job_id)
                continue

            job.status = "queued"
            job.started_at = None
            job.completed_at = None
            job.error_code = None
            job.output_ref = {
                **(job.output_ref or {}),
                "progress": {
                    **((job.output_ref or {}).get("progress") or {}),
                    "stage": "recovered_queued",
                    "current_sample_id": sample_id,
                    "current_file_name": str(entry.get("file_name") or input_ref.get("file_name") or ""),
                },
            }
            task_specs.append((job.id, parent_job_id, entry, force))

        await session.commit()

    for job_id, parent_job_id, entry, force in task_specs:
        queue.submit(
            job_id=job_id,
            job_type="library_material_rebuild",
            label=f"recovered-library-material:{entry.get('file_name') or job_id}",
            dedupe_key=_material_dedupe_key(str(entry.get("sample_id") or "")),
            priority=30,
            run=lambda job_id=job_id, parent_job_id=parent_job_id, entry=dict(entry), force=force: _run_rebuild_material_item_job(
                job_id,
                parent_job_id,
                entry,
                force,
            ),
        )
        recovered += 1

    for parent_job_id in parent_ids_to_refresh:
        await _update_library_parent_job(parent_job_id)

    if recovered or skipped_terminal_parent or failed_invalid:
        logger.info(
            "Recovered library_material_rebuild jobs on startup: recovered=%s skipped_terminal_parent=%s failed_invalid=%s",
            recovered,
            skipped_terminal_parent,
            failed_invalid,
        )
    return {
        "recovered": recovered,
        "skipped_terminal_parent": skipped_terminal_parent,
        "failed_invalid": failed_invalid,
    }


async def _update_library_parent_job(parent_job_id: UUID) -> None:
    async with get_session_factory()() as session:
        parent = await session.get(Job, parent_job_id)
        if parent is None or parent.status in {"succeeded", "failed"}:
            return
        output_ref = dict(parent.output_ref or {})
        child_ids = []
        for raw_id in output_ref.get("child_job_ids") or []:
            try:
                child_ids.append(UUID(str(raw_id)))
            except (TypeError, ValueError):
                continue
        children = []
        if child_ids:
            children = list((await session.scalars(select(Job).where(Job.id.in_(child_ids)))).all())

        base_summary = dict(output_ref.get("summary") or {})
        base_skipped = int(base_summary.get("skipped") or 0)
        base_failed = int(base_summary.get("failed") or 0)
        child_imported = sum(int((child.output_ref or {}).get("summary", {}).get("imported") or 0) for child in children)
        child_skipped = sum(int((child.output_ref or {}).get("summary", {}).get("skipped") or 0) for child in children)
        child_failed = sum(int((child.output_ref or {}).get("summary", {}).get("failed") or 0) for child in children)
        terminal_children = [child for child in children if child.status in {"succeeded", "failed"}]
        running_children = [child for child in children if child.status == "running"]
        total_materials = int((parent.input_ref or {}).get("material_count") or len(children))
        completed_materials = base_skipped + base_failed + len(terminal_children)
        failed = base_failed + child_failed
        summary = {
            **base_summary,
            "imported": child_imported,
            "skipped": base_skipped + child_skipped,
            "failed": failed,
            "queued": len(children),
        }
        progress = {
            "stage": "running" if len(terminal_children) < len(children) else ("failed" if failed else "completed"),
            "completed_materials": completed_materials,
            "total_materials": total_materials,
        }
        if running_children:
            running = running_children[0]
            running_progress = dict((running.output_ref or {}).get("progress") or {})
            progress["current_sample_id"] = running_progress.get("current_sample_id")
            progress["current_file_name"] = running_progress.get("current_file_name")

        parent.output_ref = {**output_ref, "summary": summary, "progress": progress}
        if len(terminal_children) >= len(children):
            parent.status = "failed" if failed else "succeeded"
            parent.error_code = "MaterialRebuildPartialFailure" if failed else None
            parent.completed_at = datetime.now(timezone.utc)
            # Review R2 #2 fix: route the post-rebuild refresh through the
            # maintenance queue rather than awaiting it inside the library_parse
            # worker that is finalising the parent rebuild job.
            submit_case_library_refresh_job()
        await session.commit()


def _record_material_state(*, sample_id: str, route: str, status: str, reason: str | None) -> None:
    state = _load_material_state()
    materials = dict(state.get("materials") or {})
    materials[sample_id] = {
        **dict(materials.get(sample_id) or {}),
        "route": route,
        "status": status,
        "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state["materials"] = materials
    _save_material_state(state)
