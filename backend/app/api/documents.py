from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
import hashlib
import json
import logging
import mimetypes
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import BACKEND_ROOT, get_settings
from app.db import get_db_session, get_session_factory
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.figure_asset import FigureAsset
from app.models.job import Job
from app.models.project import Project
from app.models.raw_document import RawDocument
from app.schemas.artifacts import JobAcceptedData
from app.schemas.common import APIResponse
from app.schemas.document import (
    ChunkRead,
    DocumentRead,
    DocumentUploadAccepted,
    FigureAssetRead,
    HistoryLibraryRefreshStatusRead,
)
from app.schemas.retrieval import AssetSearchRequest, AssetSearchResponse
from app.services.knowledge import (
    delete_uploaded_document_library_cache,
    read_case_library_refresh_status,
    submit_case_library_refresh_job,
    write_uploaded_document_library_cache,
)
from app.services.parsing.docling_parser import ParsedDocument
from app.services.parsing.parser import CloudParseRequiredError, ParserService
from app.services.parsing.rfp_light_parser import (
    RfpLightParseError,
    RfpLightParseInsufficient,
    RfpLightParser,
)
from app.services.parsing.section_catalog import flatten_section_catalog, normalize_section_heading
from app.services.parsing.table_profile import build_table_profile
from app.services.retrieval import AssetRetrievalService
from app.services.task_queue import get_background_task_queue
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.ingestion_filter import SafeIngestionFilter
from app.services.vectorstore.qdrant_client import QdrantService
from app.services.v2_errors import ArtifactNotFoundError
from app.utils.object_storage import MaterializedObject, get_object_storage


router = APIRouter()
logger = logging.getLogger(__name__)

LIBRARY_IMPORT_DOC_TYPE_BY_ROUTE = {
    "main_indexed": "historical_proposal",
    "review_pending": "historical_review",
    "holdout_eval": "holdout_eval",
}
LIBRARY_IMPORT_DOC_TYPES = frozenset(LIBRARY_IMPORT_DOC_TYPE_BY_ROUTE.values())
DEDUPABLE_LIBRARY_PARSE_STATUSES = {"pending", "queued", "parsing", "done", "parse_insufficient", "failed"}
RECOVERABLE_DOCUMENT_PARSE_STATUSES = {"pending", "queued", "parsing"}

# RFP light parse: full extracted text is persisted under data/rfp_text/<document_id>.txt
# so requirement extraction can read it without going through Chunk/Qdrant.
RFP_TEXT_STORAGE_DIR: Path = BACKEND_ROOT / "data" / "rfp_text"


def _extract_document_id_from_parse_job(job: Job) -> UUID | None:
    for payload in (job.input_ref or {}, job.output_ref or {}):
        raw_document_id = None
        if isinstance(payload, dict):
            raw_document_id = payload.get("document_id")
            progress = payload.get("progress")
            if raw_document_id is None and isinstance(progress, dict):
                raw_document_id = progress.get("document_id")
        if not raw_document_id:
            continue
        try:
            return UUID(str(raw_document_id))
        except (TypeError, ValueError):
            continue
    return None


def _should_refresh_history_library(*, doc_type: str) -> bool:
    return str(doc_type or "").strip().lower() == "historical_proposal"


def _normalize_library_import_route(route: str | None) -> str:
    normalized = str(route or "main_indexed").strip()
    if normalized not in LIBRARY_IMPORT_DOC_TYPE_BY_ROUTE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid library import route. Use main_indexed, review_pending, or holdout_eval.",
        )
    return normalized


def _resolve_document_parse_outcome(*, doc_type: str, parsed_metadata: dict[str, object] | None) -> dict[str, object]:
    metadata = dict(parsed_metadata or {})
    if not _should_refresh_history_library(doc_type=doc_type):
        return {
            "parse_status": "done",
            "history_library_eligible": True,
            "parse_gate_reason": None,
        }

    parse_gate_status = str(metadata.get("parse_gate_status") or "ready").strip().lower()
    parse_gate_reason = str(metadata.get("parse_gate_reason") or "").strip() or None
    if parse_gate_status == "insufficient":
        return {
            "parse_status": "parse_insufficient",
            "history_library_eligible": False,
            "parse_gate_reason": parse_gate_reason,
        }
    return {
        "parse_status": "done",
        "history_library_eligible": True,
        "parse_gate_reason": parse_gate_reason,
    }


def _build_parse_failure_metadata(exc: Exception) -> dict[str, object]:
    metadata: dict[str, object] = {"parse_error": str(exc)}
    if isinstance(exc, CloudParseRequiredError):
        metadata.update(
            {
                "requires_cloud_parse": True,
                "parse_gate_status": "insufficient",
                "parse_gate_reason": "cloud_parse_required",
            }
        )
    return metadata


def _should_parse_document_with_cloud(*, document: Document, base_metadata: dict, settings: object) -> bool:
    if getattr(settings, "parser_backend", "") in {"aliyun_docmind", "docmind"}:
        return False
    if not bool(getattr(settings, "parser_cloud_fallback_enabled", False)):
        return False
    metadata = {**(document.meta or {}), **base_metadata}
    if bool(metadata.get("requires_cloud_parse")):
        return True
    parse_gate_reason = str(metadata.get("parse_gate_reason") or "").strip().lower()
    if parse_gate_reason in {"cloud_parse_required", "fallback_binary_parser"}:
        return True
    min_bytes = int(getattr(settings, "parser_cloud_direct_min_bytes", 0) or 0)
    return min_bytes > 0 and int(document.file_size_bytes or 0) >= min_bytes


def _cloud_parse_reason_for_document(*, document: Document, base_metadata: dict, settings: object) -> str:
    metadata = {**(document.meta or {}), **base_metadata}
    parse_gate_reason = str(metadata.get("parse_gate_reason") or "").strip()
    if bool(metadata.get("requires_cloud_parse")):
        return parse_gate_reason or "previous_local_parse_failed"
    if parse_gate_reason:
        return parse_gate_reason
    min_bytes = int(getattr(settings, "parser_cloud_direct_min_bytes", 0) or 0)
    if min_bytes > 0 and int(document.file_size_bytes or 0) >= min_bytes:
        return f"large_document:{document.file_size_bytes}_bytes"
    return "cloud_parser_selected"


def get_asset_retrieval_service() -> AssetRetrievalService:
    return AssetRetrievalService()


def _resolve_section_anchor_from_catalog(
    *,
    section_catalog: list[dict[str, object]],
    heading_path: str | None,
) -> dict[str, str | None]:
    raw_heading = str(heading_path or "").strip()
    if not raw_heading or not section_catalog:
        return {
            "source_section_id": None,
            "section_path": None,
            "source_heading": None,
        }

    raw_leaf = raw_heading.split(">")[-1].strip()
    normalized_heading = normalize_section_heading(raw_heading)
    normalized_leaf = normalize_section_heading(raw_leaf)
    normalized_heading_fold = normalized_heading.casefold()
    normalized_leaf_fold = normalized_leaf.casefold()
    normalized_heading_compact_fold = "".join(normalized_heading.split()).casefold()
    normalized_leaf_compact_fold = "".join(normalized_leaf.split()).casefold()
    best_section: dict[str, object] | None = None
    best_key = (0, 0, 0)

    for section in flatten_section_catalog(section_catalog):
        section_path = str(section.get("section_path") or "").strip()
        source_heading = str(section.get("source_heading") or section.get("title") or "").strip()
        normalized_section_heading = str(section.get("normalized_heading") or normalize_section_heading(source_heading)).strip()
        normalized_section_path = str(section.get("normalized_section_path") or "").strip()
        aliases = {
            normalize_section_heading(str(item))
            for item in (section.get("heading_aliases") or [])
            if str(item).strip()
        }
        normalized_section_heading_fold = normalized_section_heading.casefold()
        normalized_section_path_fold = normalized_section_path.casefold()
        aliases_fold = {item.casefold() for item in aliases}
        normalized_section_heading_compact_fold = "".join(normalized_section_heading.split()).casefold()
        normalized_section_path_compact_fold = "".join(normalized_section_path.split()).casefold()
        aliases_compact_fold = {"".join(item.split()).casefold() for item in aliases}
        score = 0
        if raw_heading and raw_heading == section_path:
            score = max(score, 8)
        if raw_heading and raw_heading in {source_heading, str(section.get("title") or "").strip()}:
            score = max(score, 7)
        if raw_leaf and raw_leaf in {source_heading, str(section.get("title") or "").strip()}:
            score = max(score, 6)
        if normalized_heading and normalized_heading in {normalized_section_heading, normalized_section_path}:
            score = max(score, 5)
        if normalized_leaf and normalized_leaf in {normalized_section_heading, *aliases}:
            score = max(score, 5)
        if normalized_heading_fold and normalized_heading_fold in {
            normalized_section_heading_fold,
            normalized_section_path_fold,
        }:
            score = max(score, 5)
        if normalized_leaf_fold and normalized_leaf_fold in {normalized_section_heading_fold, *aliases_fold}:
            score = max(score, 5)
        if normalized_heading_compact_fold and normalized_heading_compact_fold in {
            normalized_section_heading_compact_fold,
            normalized_section_path_compact_fold,
        }:
            score = max(score, 5)
        if normalized_leaf_compact_fold and normalized_leaf_compact_fold in {
            normalized_section_heading_compact_fold,
            *aliases_compact_fold,
        }:
            score = max(score, 5)
        if section_path and raw_heading and section_path.endswith(raw_heading):
            score = max(score, 4)
        if section_path and raw_leaf and section_path.endswith(raw_leaf):
            score = max(score, 4)
        normalized_path_segments = [normalize_section_heading(part) for part in section_path.split(">") if part.strip()]
        if normalized_leaf and normalized_leaf in normalized_path_segments:
            score = max(score, 4)
        if normalized_leaf_fold and normalized_leaf_fold in {part.casefold() for part in normalized_path_segments}:
            score = max(score, 4)
        if normalized_leaf_compact_fold and normalized_leaf_compact_fold in {
            "".join(part.split()).casefold() for part in normalized_path_segments
        }:
            score = max(score, 4)
        if score <= 0:
            continue
        key = (score, int(section.get("level") or 0), len(section_path))
        if key > best_key:
            best_key = key
            best_section = section

    if best_section is None:
        return {
            "source_section_id": None,
            "section_path": None,
            "source_heading": None,
        }
    return {
        "source_section_id": str(best_section.get("section_id") or "").strip() or None,
        "section_path": str(best_section.get("section_path") or "").strip() or None,
        "source_heading": str(best_section.get("source_heading") or best_section.get("title") or "").strip() or None,
    }


def _build_chunk_contextual_text(
    *,
    document_name: str,
    base_metadata: dict,
    chunk_content: str,
    chunk_type: str,
    heading_path: str | None,
    section_anchor: dict[str, str | None],
    chunk_metadata: dict,
    chunk_index: int = 0,
) -> dict[str, str]:
    document_label = str(document_name or "").strip()
    industry = str(base_metadata.get("industry") or "").strip()
    year = str(base_metadata.get("year") or "").strip()
    section_path = str(section_anchor.get("section_path") or heading_path or "").strip()
    source_heading = str(section_anchor.get("source_heading") or heading_path or "").strip()
    section_type = str(chunk_metadata.get("section_type") or "").strip()
    equipment_type = str(chunk_metadata.get("equipment_type") or "").strip()
    content_form = str(chunk_metadata.get("content_form") or "").strip()
    content = str(chunk_content or "").strip()
    chunk_label = f"第{max(int(chunk_index), 0) + 1}段"

    contextual_parts = [
        document_label,
        industry,
        year,
        section_path,
        source_heading,
        chunk_label,
        section_type,
        equipment_type,
        content_form,
        content,
    ]
    block_parts = [
        document_label,
        section_path,
        source_heading,
        chunk_label,
        content_form,
        content,
    ]
    semantic_parts = [
        document_label,
        section_path,
        source_heading,
        chunk_label,
        section_type,
        equipment_type,
        chunk_type,
        content,
    ]
    return {
        "contextual_text": "\n".join(part for part in contextual_parts if part).strip(),
        "contextualized_block_text": "\n".join(part for part in block_parts if part).strip(),
        "semantic_retrieval_text": "\n".join(part for part in semantic_parts if part).strip(),
        "semantic_retrieval_version": "layer2_contextual_hybrid_v1",
    }


async def _purge_document_raw_artifacts(
    *,
    session: AsyncSession,
    document: Document,
    storage: object,
) -> None:
    raw_document_id = (document.meta or {}).get("raw_document_id")
    if not raw_document_id:
        return
    try:
        raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
    except (TypeError, ValueError):
        raw_document = None
    if raw_document is None:
        return

    figure_assets = (
        await session.scalars(select(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
    ).all()
    for asset in figure_assets:
        if (asset.meta or {}).get("storage_fallback"):
            continue
        _safe_delete_storage_path(storage=storage, storage_path=asset.asset_uri)
    await session.execute(delete(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
    await session.delete(raw_document)
    await session.flush()


async def _parse_and_index_document(
    *,
    session: AsyncSession,
    document: Document,
    base_metadata: dict,
    parsed_document: ParsedDocument | None = None,
) -> None:
    settings = get_settings()
    qdrant = QdrantService()
    storage = get_object_storage()

    existing_chunks = (
        await session.scalars(select(Chunk).where(Chunk.document_id == document.id))
    ).all()
    qdrant.delete_points([str(chunk.qdrant_point_id) for chunk in existing_chunks if chunk.qdrant_point_id])
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.flush()

    if parsed_document is None:
        parser = ParserService()
        materialized: MaterializedObject = storage.materialize(document.storage_path)
        try:
            if _should_parse_document_with_cloud(document=document, base_metadata=base_metadata, settings=settings):
                parsed_document = await parser.parse_document_with_cloud(
                    str(materialized.path),
                    reason=_cloud_parse_reason_for_document(
                        document=document,
                        base_metadata=base_metadata,
                        settings=settings,
                    ),
                )
            else:
                parsed_document = await parser.parse_document(str(materialized.path))
        finally:
            materialized.cleanup()

    parse_outcome = _resolve_document_parse_outcome(
        doc_type=document.doc_type,
        parsed_metadata=parsed_document.metadata,
    )
    if not bool(parse_outcome.get("history_library_eligible", True)):
        await _purge_document_raw_artifacts(session=session, document=document, storage=storage)
        delete_uploaded_document_library_cache(document_id=str(document.id))
        document.parse_status = str(parse_outcome.get("parse_status") or "parse_insufficient")
        document.meta = {
            **base_metadata,
            **parsed_document.metadata,
            "raw_document_id": None,
            "figure_asset_count": 0,
            "chunk_count": 0,
            "indexed_chunk_count": 0,
            "skipped_chunk_count": 0,
            **_build_table_asset_counter_fields({}),
        }
        return

    chunker = Chunker()
    embedder = Embedder()
    ingestion_filter = SafeIngestionFilter() if settings.safe_ingestion_enabled else None

    chunk_payloads = chunker.split(
        parsed_document.markdown,
        base_metadata={
            **base_metadata,
            "doc_type": document.doc_type,
            "document_name": document.filename,
            "project_id": str(document.project_id) if document.project_id else None,
        },
    )
    section_catalog = (
        list((parsed_document.structure or {}).get("section_catalog") or [])
        if isinstance(parsed_document.structure, dict)
        else []
    )

    indexed_chunk_count = 0
    skipped_chunk_count = 0
    preserved_table_chunks: list[Chunk] = []
    index_records: list[tuple[uuid.UUID, str, dict]] = []

    for payload in chunk_payloads:
        section_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path=payload.heading_path,
        )
        decision = ingestion_filter.decide(payload) if ingestion_filter is not None else None
        indexable = True if decision is None else decision.indexable
        indexing_reasons = [] if decision is None else list(decision.reasons)
        review_required = False if decision is None else decision.review_required
        preserve_for_assets = False if decision is None else decision.preserve_for_assets
        point_id = uuid.uuid4() if indexable else None
        chunk_meta = {
            **payload.metadata,
            "indexable": indexable,
            "indexing_reasons": indexing_reasons,
            "review_required": review_required,
            "preserve_for_assets": preserve_for_assets,
            **section_anchor,
        }
        chunk_meta.update(
            _build_chunk_contextual_text(
                document_name=document.filename,
                base_metadata=base_metadata,
                chunk_index=payload.chunk_index,
                chunk_content=payload.content,
                chunk_type=payload.chunk_type,
                heading_path=payload.heading_path,
                section_anchor=section_anchor,
                chunk_metadata=chunk_meta,
            )
        )
        chunk = Chunk(
            document_id=document.id,
            chunk_index=payload.chunk_index,
            chunk_type=payload.chunk_type,
            content=payload.content,
            token_count=payload.token_count,
            heading_path=payload.heading_path,
            qdrant_point_id=point_id,
            meta=chunk_meta,
        )
        session.add(chunk)
        await session.flush()

        if indexable and point_id is not None:
            index_records.append(
                (
                    point_id,
                    chunk_meta.get("semantic_retrieval_text") or payload.content,
                    {
                        "project_id": str(document.project_id) if document.project_id else None,
                        "document_id": str(document.id),
                        "document_name": document.filename,
                        "chunk_id": str(chunk.id),
                        "chunk_index": payload.chunk_index,
                        "chunk_type": payload.chunk_type,
                        "heading_path": payload.heading_path,
                        **section_anchor,
                        "content": payload.content,
                        "contextual_text": chunk_meta.get("contextual_text"),
                        "contextualized_block_text": chunk_meta.get("contextualized_block_text"),
                        "semantic_retrieval_text": chunk_meta.get("semantic_retrieval_text"),
                        "semantic_retrieval_version": chunk_meta.get("semantic_retrieval_version"),
                        "industry": base_metadata.get("industry"),
                        "year": base_metadata.get("year"),
                        "amount_range": base_metadata.get("amount_range"),
                        "doc_type": document.doc_type,
                        "image_url": None,
                        "token_count": payload.token_count,
                        "indexable": True,
                        **payload.metadata,
                    },
                )
            )
        else:
            skipped_chunk_count += 1
            if payload.chunk_type == "TABLE" and preserve_for_assets:
                preserved_table_chunks.append(chunk)

    if index_records:
        vectors = await embedder.embed_texts([text for _point_id, text, _payload in index_records])
        for (point_id, _text, qdrant_payload), vector in zip(index_records, vectors):
            qdrant.upsert_chunk(point_id=point_id, vector=vector, payload=qdrant_payload)
        indexed_chunk_count = len(index_records)

    raw_document = await _upsert_raw_document(session=session, document=document, base_metadata=base_metadata)
    figure_asset_count = await _replace_figure_assets(
        session=session,
        document=document,
        raw_document=raw_document,
        parsed_document=parsed_document,
        storage=storage,
        preserved_table_chunks=preserved_table_chunks,
    )
    table_asset_counts = await _summarize_table_assets(
        session=session,
        raw_document=raw_document,
    )

    document.parse_status = str(parse_outcome.get("parse_status") or "done")
    document.meta = {
        **base_metadata,
        **parsed_document.metadata,
        "raw_document_id": str(raw_document.id),
        "figure_asset_count": figure_asset_count,
        "chunk_count": len(chunk_payloads),
        "indexed_chunk_count": indexed_chunk_count,
        "skipped_chunk_count": skipped_chunk_count,
        **_build_table_asset_counter_fields(table_asset_counts),
    }
    raw_document.parse_status = str(parse_outcome.get("parse_status") or "done")
    raw_document.meta = {
        **(raw_document.meta or {}),
        **parsed_document.metadata,
        "legacy_document_id": str(document.id),
        "figure_asset_count": figure_asset_count,
        "chunk_count": len(chunk_payloads),
        "indexed_chunk_count": indexed_chunk_count,
        "skipped_chunk_count": skipped_chunk_count,
        **_build_table_asset_counter_fields(table_asset_counts),
    }
    try:
        write_uploaded_document_library_cache(
            document=document,
            parsed_document=parsed_document,
        )
    except Exception:
        pass


def _build_document_upload_message(*, doc_type: str, parse_status: str, reparsed: bool = False) -> str:
    if _should_refresh_history_library(doc_type=doc_type):
        if parse_status in {"pending", "parsing", "queued"}:
            return "文档已接收，正在后台解析入库；解析完成后会刷新历史方案库、AI Wiki 与视觉索引"
        if parse_status == "parse_insufficient":
            if reparsed:
                return "文档已重新解析，但当前解析质量不足，已跳过历史方案库 / AI Wiki / 视觉索引入库；后台刷新会同步移除旧的历史库结果"
            return "文档已保存，但当前解析质量不足，已跳过历史方案库 / AI Wiki / 视觉索引入库"
        return (
            "文档已重新解析并入库，历史方案库、AI Wiki 与视觉索引正在后台刷新"
            if reparsed
            else "文档已接收并完成解析入库，历史方案库、AI Wiki 与视觉索引正在后台刷新"
        )
    if parse_status in {"pending", "parsing", "queued"}:
        return "文档已接收，正在后台解析入库"
    return "文档已重新解析并入库" if reparsed else "文档已接收并完成解析入库"


async def _run_document_parse_job(job_id: UUID, document_id: UUID, base_metadata: dict) -> None:
    parsed_doc_type = ""
    async with get_session_factory()() as session:
        job = await session.get(Job, job_id)
        document = await session.get(Document, document_id)
        if job is None or document is None:
            return
        parsed_doc_type = str(document.doc_type or "")
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.output_ref = {
            **(job.output_ref or {}),
            "progress": {"stage": "parsing", "document_id": str(document_id)},
        }
        document.parse_status = "parsing"
        document.meta = {
            key: value
            for key, value in (document.meta or {}).items()
            if key not in {"parse_error", "requires_cloud_parse"}
        }
        await session.commit()

        try:
            await _parse_and_index_document(
                session=session,
                document=document,
                base_metadata=base_metadata,
            )
            job.status = "succeeded"
            job.output_ref = {
                **(job.output_ref or {}),
                "document_id": str(document.id),
                "parse_status": document.parse_status,
                "progress": {"stage": "completed", "document_id": str(document.id)},
            }
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            document.parse_status = "failed"
            document.meta = {**(document.meta or {}), **_build_parse_failure_metadata(exc)}
            job.status = "failed"
            job.error_code = "CloudParseRequired" if isinstance(exc, CloudParseRequiredError) else exc.__class__.__name__[:50]
            job.output_ref = {
                **(job.output_ref or {}),
                "error": str(exc),
                "progress": {"stage": "failed", "document_id": str(document.id)},
            }
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            raise

    if _should_refresh_history_library(doc_type=parsed_doc_type):
        # Review R2 #2 fix: do NOT block this library_parse worker on the full
        # history-library refresh.  Submit the refresh into the maintenance
        # queue (bounded by ``MAINTENANCE_JOB_WORKER_COUNT`` and globally
        # deduplicated) so the parse worker is freed for the next document.
        submit_case_library_refresh_job()


def _save_rfp_light_full_text(*, document_id: UUID, text: str) -> str:
    """Write the full extracted RFP text to ``data/rfp_text/<document_id>.txt``.

    Returns the absolute path as a string so it can be persisted on
    ``Document.meta.rfp_text_storage_path`` for later requirement extraction.
    """

    RFP_TEXT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = RFP_TEXT_STORAGE_DIR / f"{document_id}.txt"
    target.write_text(text, encoding="utf-8")
    return str(target)


async def _run_rfp_light_parse_job(job_id: UUID, document_id: UUID) -> None:
    """Execute the lightweight RFP parse pipeline on the ``interactive`` queue.

    This deliberately does NOT call ``_parse_and_index_document`` / Chunker /
    Embedder / Qdrant / ``_upsert_raw_document`` / ``_replace_figure_assets`` /
    ``request_case_library_refresh`` so that customer realtime flows are not
    blocked by the historical-ingestion pipeline (see plan §2.5).
    """

    settings = get_settings()
    parser = RfpLightParser(settings=settings)
    storage = get_object_storage()

    timings: dict[str, float] = {}
    overall_started = time.perf_counter()

    async with get_session_factory()() as session:
        job = await session.get(Job, job_id)
        document = await session.get(Document, document_id)
        if job is None or document is None:
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.output_ref = {
            **(job.output_ref or {}),
            "progress": {
                "stage": "materializing_file",
                "document_id": str(document_id),
                "rfp_parse_mode": "light",
            },
        }
        document.parse_status = "parsing"
        document.meta = {
            key: value
            for key, value in (document.meta or {}).items()
            if key
            not in {
                "parse_error",
                "rfp_parse_error_reason",
                "rfp_parse_error_message",
            }
        }
        await session.commit()

        materialized: MaterializedObject | None = None
        try:
            stage_start = time.perf_counter()
            materialized = storage.materialize(document.storage_path)
            timings["materializing_file"] = time.perf_counter() - stage_start

            stage_start = time.perf_counter()
            job.output_ref = {
                **(job.output_ref or {}),
                "progress": {
                    "stage": "extracting_text",
                    "document_id": str(document_id),
                    "timings": dict(timings),
                    "rfp_parse_mode": "light",
                },
            }
            await session.commit()

            try:
                result = await parser.parse(materialized.path)
            except RfpLightParseInsufficient as exc:
                timings["extracting_text"] = time.perf_counter() - stage_start
                document.parse_status = "parse_insufficient"
                document.meta = {
                    **(document.meta or {}),
                    "rfp_parse_mode": "light",
                    "rfp_parse_error_reason": exc.reason,
                    "rfp_parse_error_message": str(exc),
                    **{f"rfp_parse_{key}": value for key, value in (exc.metadata or {}).items()},
                }
                job.status = "succeeded"
                job.error_code = (exc.reason or "")[:50] or None
                job.output_ref = {
                    **(job.output_ref or {}),
                    "document_id": str(document.id),
                    "parse_status": document.parse_status,
                    "rfp_parse_mode": "light",
                    "rfp_parse_error_reason": exc.reason,
                    "chunk_count": 0,
                    "indexed_chunk_count": 0,
                    "figure_asset_count": 0,
                    "raw_document_id": None,
                    "progress": {
                        "stage": "completed",
                        "document_id": str(document.id),
                        "timings": dict(timings),
                        "elapsed_seconds": time.perf_counter() - overall_started,
                        "rfp_parse_mode": "light",
                    },
                }
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(
                    "RFP light parse insufficient: document_id=%s reason=%s",
                    document_id,
                    exc.reason,
                )
                return
            timings["extracting_text"] = time.perf_counter() - stage_start

            stage_start = time.perf_counter()
            text_storage_path: str | None = None
            if settings.rfp_light_parse_store_full_text:
                text_storage_path = await asyncio.to_thread(
                    _save_rfp_light_full_text,
                    document_id=document_id,
                    text=result.text,
                )
            timings["saving_requirement_text"] = time.perf_counter() - stage_start

            document.parse_status = "done"
            document.meta = {
                **(document.meta or {}),
                "rfp_parse_mode": "light",
                "rfp_text_excerpt": result.excerpt,
                "rfp_text_storage_path": text_storage_path,
                "rfp_text_char_count": result.char_count,
                "rfp_light_parse_pages": result.page_count,
                "rfp_light_parse_elapsed_seconds": result.elapsed_seconds,
                "rfp_light_parse_source_format": result.source_format,
                "rfp_light_parse_truncated_chars": result.truncated_chars,
                "rfp_light_parse_truncated_pages": result.truncated_pages,
            }

            job.status = "succeeded"
            job.error_code = None
            job.output_ref = {
                **(job.output_ref or {}),
                "document_id": str(document.id),
                "parse_status": document.parse_status,
                "rfp_parse_mode": "light",
                "rfp_text_excerpt": result.excerpt,
                "rfp_text_storage_path": text_storage_path,
                "rfp_text_char_count": result.char_count,
                "rfp_light_parse_pages": result.page_count,
                "rfp_light_parse_elapsed_seconds": result.elapsed_seconds,
                "chunk_count": 0,
                "indexed_chunk_count": 0,
                "figure_asset_count": 0,
                "raw_document_id": None,
                "progress": {
                    "stage": "completed",
                    "document_id": str(document.id),
                    "timings": dict(timings),
                    "elapsed_seconds": time.perf_counter() - overall_started,
                    "rfp_parse_mode": "light",
                },
            }
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(
                "RFP light parse done: document_id=%s pages=%s chars=%s elapsed=%.2fs",
                document_id,
                result.page_count,
                result.char_count,
                time.perf_counter() - overall_started,
            )
        except Exception as exc:  # noqa: BLE001 - status persisted; re-raised for queue logging
            document.parse_status = "failed"
            document.meta = {
                **(document.meta or {}),
                **_build_parse_failure_metadata(exc),
                "rfp_parse_mode": "light",
            }
            job.status = "failed"
            job.error_code = exc.__class__.__name__[:50]
            job.output_ref = {
                **(job.output_ref or {}),
                "error": str(exc),
                "rfp_parse_mode": "light",
                "progress": {
                    "stage": "failed",
                    "document_id": str(document.id),
                    "timings": dict(timings),
                    "rfp_parse_mode": "light",
                },
            }
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            raise
        finally:
            if materialized is not None:
                materialized.cleanup()


async def recover_document_parse_jobs_on_startup() -> dict[str, int]:
    """Requeue document parse jobs left queued/running by a previous backend process.

    Handles both heavyweight ``document_parse`` (historical proposals -> ``library_parse``
    queue) and the lightweight ``rfp_light_parse`` job type (project RFP uploads ->
    ``interactive`` queue) so a backend restart never strands customer-facing
    requirement parsing alongside long-running historical ingestion.
    """

    library_queue = get_background_task_queue("library_parse")
    interactive_queue = get_background_task_queue("interactive")
    task_specs: list[tuple[str, UUID, UUID, dict, str]] = []
    recovered = 0
    skipped_terminal = 0
    failed_invalid = 0

    async with get_session_factory()() as session:
        result = await session.scalars(
            select(Job)
            .where(Job.job_type.in_(["document_parse", "rfp_light_parse"]))
            .where(Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at.asc())
        )
        jobs = result.all()
        for job in jobs:
            document_id = _extract_document_id_from_parse_job(job)
            if document_id is None:
                job.status = "failed"
                job.error_code = "MissingDocumentId"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": "document_parse job is missing document_id",
                    "progress": {"stage": "failed"},
                }
                job.completed_at = datetime.now(timezone.utc)
                failed_invalid += 1
                continue

            document = await session.get(Document, document_id)
            if document is None:
                job.status = "failed"
                job.error_code = "DocumentNotFound"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": f"Document not found: {document_id}",
                    "progress": {"stage": "failed", "document_id": str(document_id)},
                }
                job.completed_at = datetime.now(timezone.utc)
                failed_invalid += 1
                continue

            # Review R2 #4 fix: a legacy ``document_parse`` job whose document
            # is actually an RFP would, on recovery, route through the heavy
            # ingestion pipeline (chunking + embedding + Qdrant + figure_assets
            # + case_library_refresh).  Auto-migrate it to the lightweight
            # parser instead of forcing the user to re-upload: close the old
            # heavy job with ``MigratedToRfpLightParse`` and enqueue a fresh
            # ``rfp_light_parse`` job onto the interactive queue so the user
            # sees parsing resume automatically after the restart.
            if job.job_type == "document_parse" and str(document.doc_type or "").lower() == "rfp":
                new_job_id = uuid.uuid4()
                # Review R3 #2 fix: ``trace_id`` is declared NOT NULL on
                # ``Job`` (see ``app/models/job.py``).  The previous patch
                # forgot to set it and the previous ``new_job_id`` was never
                # bound to ``Job(id=...)``, so ``session.flush()`` would raise
                # an IntegrityError and the migration silently failed for
                # every legacy RFP job on startup.  We now bind both.
                new_job = Job(
                    id=new_job_id,
                    project_id=document.project_id,
                    job_type="rfp_light_parse",
                    status="queued",
                    trace_id=uuid.uuid4().hex,
                    input_ref={
                        "document_id": str(document.id),
                        "filename": document.filename,
                        "migrated_from_job_id": str(job.id),
                        "trigger": "startup_recovery_migration",
                    },
                    output_ref={
                        "progress": {
                            "stage": "queued",
                            "document_id": str(document.id),
                            "reason": "migrated_from_legacy_document_parse",
                        }
                    },
                )
                session.add(new_job)

                job.status = "failed"
                job.error_code = "MigratedToRfpLightParse"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "error": (
                        "Legacy document_parse job targeting an RFP document was migrated "
                        "to the lightweight RFP parser on recovery to avoid the heavy "
                        "ingestion pipeline."
                    ),
                    "progress": {
                        "stage": "failed",
                        "document_id": str(document.id),
                        "doc_type": "rfp",
                        "reason": "migrated_to_rfp_light_parse",
                        "migrated_to_job_id": str(new_job_id),
                    },
                }
                job.completed_at = datetime.now(timezone.utc)
                failed_invalid += 1

                document.parse_status = "parsing"
                document.meta = {
                    key: value
                    for key, value in (document.meta or {}).items()
                    if key not in {"parse_error", "requires_cloud_parse"}
                }

                # Flush so ``new_job`` has its server-assigned id available for
                # the dedupe_key path below.
                await session.flush()
                task_specs.append(
                    (
                        "rfp_light_parse",
                        new_job.id,
                        document.id,
                        dict(document.meta or {}),
                        document.filename,
                    )
                )
                continue

            if document.parse_status in {"done", "parse_insufficient"}:
                job.status = "succeeded"
                job.error_code = None
                job.output_ref = {
                    **(job.output_ref or {}),
                    "document_id": str(document.id),
                    "parse_status": document.parse_status,
                    "progress": {"stage": "completed", "document_id": str(document.id), "recovered": True},
                }
                job.completed_at = job.completed_at or datetime.now(timezone.utc)
                skipped_terminal += 1
                continue

            if document.parse_status == "failed":
                job.status = "failed"
                job.error_code = job.error_code or "DocumentParseFailed"
                job.output_ref = {
                    **(job.output_ref or {}),
                    "document_id": str(document.id),
                    "parse_status": document.parse_status,
                    "progress": {"stage": "failed", "document_id": str(document.id), "recovered": True},
                }
                job.completed_at = job.completed_at or datetime.now(timezone.utc)
                skipped_terminal += 1
                continue

            if document.parse_status not in RECOVERABLE_DOCUMENT_PARSE_STATUSES:
                skipped_terminal += 1
                continue

            document.parse_status = "parsing"
            job.status = "queued"
            job.started_at = None
            job.completed_at = None
            job.error_code = None
            job.output_ref = {
                **(job.output_ref or {}),
                "progress": {"stage": "recovered_queued", "document_id": str(document.id)},
            }
            task_specs.append(
                (job.job_type, job.id, document.id, dict(document.meta or {}), document.filename)
            )

        await session.commit()

    # Review R4 #2: ``BackgroundTaskQueueManager.submit`` returns the existing
    # job id when ``dedupe_key`` matches an already-active job, and silently
    # drops the duplicate submission.  Without checking the return value, any
    # second/third recovered job on the same document would stay
    # ``status="queued"`` in the DB forever — a worker never picks it up
    # because it is not in the in-memory queue.  We compare the returned id
    # against our own ``job_id`` and, if they differ, mark the duplicate
    # ``failed`` with a stable error code so the frontend stops polling and
    # operators can see the dedupe in audit logs.
    duplicates_deduplicated = 0
    duplicates_to_finalise: list[tuple[UUID, UUID]] = []
    for job_type, job_id, document_id, base_metadata, filename in task_specs:
        if job_type == "rfp_light_parse":
            submitted_job_id = interactive_queue.submit(
                job_id=job_id,
                job_type="rfp_light_parse",
                label=f"recovered-rfp-light-parse:{filename}",
                run=lambda job_id=job_id, document_id=document_id: _run_rfp_light_parse_job(
                    job_id,
                    document_id,
                ),
                dedupe_key=f"rfp_light_parse:{document_id}",
                priority=20,
            )
        else:
            submitted_job_id = library_queue.submit(
                job_id=job_id,
                job_type="document_parse",
                label=f"recovered-document-parse:{filename}",
                run=lambda job_id=job_id, document_id=document_id, base_metadata=base_metadata: _run_document_parse_job(
                    job_id,
                    document_id,
                    base_metadata,
                ),
                dedupe_key=f"document_parse:{document_id}",
                priority=20,
            )
        if submitted_job_id != job_id:
            duplicates_to_finalise.append((job_id, submitted_job_id))
        else:
            recovered += 1

    if duplicates_to_finalise:
        async with get_session_factory()() as session:
            now = datetime.now(timezone.utc)
            for dup_job_id, active_job_id in duplicates_to_finalise:
                dup_job = await session.get(Job, dup_job_id)
                if dup_job is None:
                    continue
                # Only finalise rows we actually re-queued in this run.  If a
                # concurrent process already moved it, leave it alone.
                if dup_job.status not in ("queued", "running"):
                    continue
                dup_job.status = "failed"
                dup_job.error_code = "DuplicateRecoveredJob"
                dup_job.completed_at = now
                dup_job.output_ref = {
                    **(dup_job.output_ref or {}),
                    "deduplicated_to_job_id": str(active_job_id),
                    "reason": "duplicate_recovered_job_deduplicated_to_active",
                    "progress": {
                        "stage": "deduplicated",
                        "deduplicated_to_job_id": str(active_job_id),
                    },
                }
                duplicates_deduplicated += 1
            await session.commit()

    if recovered or skipped_terminal or failed_invalid or duplicates_deduplicated:
        logger.info(
            "Recovered document parse jobs on startup: recovered=%s skipped_terminal=%s "
            "failed_invalid=%s duplicates_deduplicated=%s",
            recovered,
            skipped_terminal,
            failed_invalid,
            duplicates_deduplicated,
        )

    return {
        "recovered": recovered,
        "skipped_terminal": skipped_terminal,
        "failed_invalid": failed_invalid,
        "duplicates_deduplicated": duplicates_deduplicated,
    }


async def _upsert_raw_document(
    *,
    session: AsyncSession,
    document: Document,
    base_metadata: dict,
) -> RawDocument:
    corpus_scope = "global" if document.project_id is None else "project"
    checksum = str(base_metadata.get("content_sha256") or document.content_sha256 or "").strip().lower() or None
    raw_document: RawDocument | None = None
    raw_document_id = (document.meta or {}).get("raw_document_id")
    if raw_document_id:
        try:
            raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
        except (TypeError, ValueError):
            raw_document = None

    if raw_document is None:
        raw_document = RawDocument(
            project_id=document.project_id,
            corpus_scope=corpus_scope,
            doc_type=document.doc_type,
            file_uri=document.storage_path,
            file_name=document.filename,
            checksum=checksum,
            parse_status=document.parse_status,
            confidentiality_level=str(base_metadata.get("confidentiality_level") or "") or None,
            meta={"legacy_document_id": str(document.id)},
        )
        session.add(raw_document)
        await session.flush()
        return raw_document

    raw_document.project_id = document.project_id
    raw_document.corpus_scope = corpus_scope
    raw_document.doc_type = document.doc_type
    raw_document.file_uri = document.storage_path
    raw_document.file_name = document.filename
    raw_document.checksum = checksum
    raw_document.parse_status = document.parse_status
    raw_document.confidentiality_level = str(base_metadata.get("confidentiality_level") or "") or None
    return raw_document


async def _replace_figure_assets(
    *,
    session: AsyncSession,
    document: Document,
    raw_document: RawDocument,
    parsed_document: ParsedDocument,
    storage: object,
    preserved_table_chunks: list[Chunk],
) -> int:
    section_catalog = (
        list((parsed_document.structure or {}).get("section_catalog") or [])
        if isinstance(parsed_document.structure, dict)
        else []
    )
    existing_assets = (
        await session.scalars(select(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
    ).all()
    for asset in existing_assets:
        if (asset.meta or {}).get("storage_fallback"):
            continue
        _safe_delete_storage_path(storage=storage, storage_path=asset.asset_uri)

    await session.execute(delete(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
    await session.flush()

    saved_count = 0
    saved_table_assets: list[FigureAsset] = []
    for index, asset in enumerate(parsed_document.assets):
        section_anchor = _resolve_section_anchor_from_catalog(
            section_catalog=section_catalog,
            heading_path=asset.heading_path,
        )
        asset_uri = raw_document.file_uri
        if asset.image_bytes:
            figure_prefix_scope = str(document.project_id or "global")
            asset_uri = storage.save_bytes(
                asset.image_bytes,
                suffix=asset.image_ext or ".png",
                prefix=f"{figure_prefix_scope}_figure_{index}_",
            )

        saved_asset = FigureAsset(
            raw_document_id=raw_document.id,
            page_no=asset.page_no,
            asset_uri=asset_uri,
            asset_type=asset.asset_type,
            title=asset.title,
            caption=asset.caption,
            meta={
                **asset.meta,
                "sample_id": (document.meta or {}).get("sample_id"),
                "library_track": (document.meta or {}).get("library_track"),
                "material_route": (document.meta or {}).get("material_route"),
                "heading_path": asset.heading_path,
                "context_before": asset.context_before,
                "context_after": asset.context_after,
                "bbox": asset.bbox,
                "source_ref": asset.source_ref,
                "legacy_document_id": str(document.id),
                "storage_fallback": asset.image_bytes is None,
                **section_anchor,
            },
        )
        session.add(saved_asset)
        if asset.asset_type == "table":
            saved_table_assets.append(saved_asset)
        saved_count += 1

    await session.flush()
    saved_count += await _preserve_table_assets_for_reconstruction(
        session=session,
        document=document,
        raw_document=raw_document,
        saved_table_assets=saved_table_assets,
        preserved_table_chunks=preserved_table_chunks,
    )
    return saved_count


async def _preserve_table_assets_for_reconstruction(
    *,
    session: AsyncSession,
    document: Document,
    raw_document: RawDocument,
    saved_table_assets: list[FigureAsset],
    preserved_table_chunks: list[Chunk],
) -> int:
    if not preserved_table_chunks:
        return 0

    created_count = 0
    paired_count = min(len(saved_table_assets), len(preserved_table_chunks))
    for index in range(paired_count):
        asset = saved_table_assets[index]
        chunk = preserved_table_chunks[index]
        preserved_meta = _build_preserved_table_meta(document=document, raw_document=raw_document, chunk=chunk)
        asset.reuse_mode = "reconstruct_only"
        asset.meta = {
            **(asset.meta or {}),
            **preserved_meta,
        }
        chunk.meta = {
            **(chunk.meta or {}),
            "table_asset_preserved": True,
            "table_asset_status": "pending_reconstruction",
            "table_asset_source": "parsed_asset",
            "table_asset_id": str(asset.id),
            "table_profile": preserved_meta.get("table_profile"),
        }

    for chunk in preserved_table_chunks[paired_count:]:
        preserved_meta = _build_preserved_table_meta(document=document, raw_document=raw_document, chunk=chunk)
        fallback_asset = FigureAsset(
            raw_document_id=raw_document.id,
            page_no=None,
            asset_uri=raw_document.file_uri,
            asset_type="table",
            title=chunk.heading_path,
            caption=None,
            reuse_mode="reconstruct_only",
            meta={
                **preserved_meta,
                "synthetic_asset": True,
                "storage_fallback": True,
                "visual_role": "table_asset",
            },
        )
        session.add(fallback_asset)
        await session.flush()
        chunk.meta = {
            **(chunk.meta or {}),
            "table_asset_preserved": True,
            "table_asset_status": "pending_reconstruction",
            "table_asset_source": "synthetic_asset",
            "table_asset_id": str(fallback_asset.id),
            "table_profile": preserved_meta.get("table_profile"),
        }
        created_count += 1

    return created_count


def _build_preserved_table_meta(
    *,
    document: Document,
    raw_document: RawDocument,
    chunk: Chunk,
) -> dict:
    chunk_meta = chunk.meta or {}
    table_profile = build_table_profile(chunk.content)
    return {
        "legacy_document_id": str(document.id),
        "raw_document_id": str(raw_document.id),
        "heading_path": chunk.heading_path,
        "source_section_id": chunk_meta.get("source_section_id"),
        "section_path": chunk_meta.get("section_path"),
        "source_heading": chunk_meta.get("source_heading"),
        "source_chunk_index": chunk.chunk_index,
        "source_chunk_type": chunk.chunk_type,
        "source_chunk_id": str(chunk.id),
        "raw_table_markdown": chunk.content,
        "token_count": chunk.token_count,
        "indexing_reasons": list(chunk_meta.get("indexing_reasons", [])),
        "indexable": False,
        "review_required": bool(chunk_meta.get("review_required", True)),
        "ingestion_strategy": "table_asset_only",
        "reconstruction_status": "pending",
        "reconstruction_backend": None,
        "reconstruction_confidence": None,
        "reconstruction_job_id": None,
        "preserve_in_vector_db": False,
        "table_profile": table_profile.to_metadata(),
    }


async def _summarize_table_assets(
    *,
    session: AsyncSession,
    raw_document: RawDocument,
) -> dict[str, int]:
    assets = (
        await session.scalars(select(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
    ).all()
    counts = {
        "total": 0,
        "pending": 0,
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
    }
    for asset in assets:
        if asset.asset_type != "table":
            continue
        counts["total"] += 1
        status = str((asset.meta or {}).get("reconstruction_status") or "pending")
        if status not in counts:
            status = "pending"
        counts[status] += 1
    return counts


def _build_table_asset_counter_fields(counts: dict[str, int]) -> dict[str, int]:
    return {
        "preserved_table_asset_count": int(counts.get("total", 0)),
        "pending_table_reconstruction_count": int(counts.get("pending", 0)),
        "queued_table_reconstruction_count": int(counts.get("queued", 0)),
        "running_table_reconstruction_count": int(counts.get("running", 0)),
        "completed_table_reconstruction_count": int(counts.get("succeeded", 0)),
        "failed_table_reconstruction_count": int(counts.get("failed", 0)),
    }


async def _refresh_table_asset_counters(
    *,
    session: AsyncSession,
    document: Document,
    raw_document: RawDocument,
) -> None:
    counts = await _summarize_table_assets(session=session, raw_document=raw_document)
    counter_fields = _build_table_asset_counter_fields(counts)
    document.meta = {
        **(document.meta or {}),
        **counter_fields,
    }
    raw_document.meta = {
        **(raw_document.meta or {}),
        **counter_fields,
    }


def _safe_delete_storage_path(*, storage: object, storage_path: str | None) -> None:
    if not storage_path:
        return
    try:
        storage.delete(storage_path)
    except Exception:
        pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _latest_document_parse_job(*, session: AsyncSession, document_id: UUID) -> Job | None:
    result = await session.scalars(
        select(Job)
        .where(
            Job.job_type == "document_parse",
            cast(Job.input_ref["document_id"].astext, String) == str(document_id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return result.first()


async def _find_duplicate_global_library_document(
    *,
    session: AsyncSession,
    storage: object,
    content_sha256: str,
    file_size_bytes: int | None = None,
) -> Document | None:
    normalized = str(content_sha256 or "").strip().lower()
    if not normalized:
        return None
    result = await session.scalars(
        select(Document)
        .where(
            Document.project_id.is_(None),
            Document.content_sha256 == normalized,
            Document.doc_type.in_(LIBRARY_IMPORT_DOC_TYPES),
            Document.parse_status.in_(DEDUPABLE_LIBRARY_PARSE_STATUSES),
        )
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    duplicate = result.first()
    if duplicate is not None:
        return duplicate

    legacy_stmt = select(Document).where(
        Document.project_id.is_(None),
        Document.content_sha256.is_(None),
        Document.doc_type.in_(LIBRARY_IMPORT_DOC_TYPES),
        Document.parse_status.in_(DEDUPABLE_LIBRARY_PARSE_STATUSES),
    )
    if file_size_bytes is not None:
        legacy_stmt = legacy_stmt.where(Document.file_size_bytes == file_size_bytes)
    legacy_result = await session.scalars(legacy_stmt.order_by(Document.created_at.desc()))
    for candidate in legacy_result.all():
        materialized: MaterializedObject | None = None
        try:
            materialized = storage.materialize(candidate.storage_path)
            candidate_sha256 = _sha256_file(materialized.path)
        except Exception:
            continue
        finally:
            if materialized is not None:
                materialized.cleanup()
        if candidate_sha256 != normalized:
            continue
        candidate.content_sha256 = normalized
        candidate.meta = {**(candidate.meta or {}), "content_sha256": normalized}
        raw_document_id = (candidate.meta or {}).get("raw_document_id")
        if raw_document_id:
            try:
                raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
            except (TypeError, ValueError):
                raw_document = None
            if raw_document is not None:
                raw_document.checksum = normalized
        await session.flush()
        return candidate
    return None


async def _build_duplicate_upload_response(
    *,
    session: AsyncSession,
    duplicate: Document,
) -> APIResponse[DocumentUploadAccepted]:
    job = await _latest_document_parse_job(session=session, document_id=duplicate.id)
    active_job = job if job is not None and job.status in {"queued", "running"} else None
    if duplicate.parse_status in {"pending", "queued", "parsing"}:
        message = "该历史方案已在处理中，已忽略重复上传"
    elif duplicate.parse_status == "failed":
        message = "该历史方案已存在但解析失败，已忽略重复上传；可在历史方案库中重新解析"
    else:
        message = "该历史方案已存在，已忽略重复上传"
    return APIResponse(
        code=200,
        message="duplicate_ignored",
        data=DocumentUploadAccepted(
            id=duplicate.id,
            filename=duplicate.filename,
            parse_status=duplicate.parse_status,
            message=message,
            job_id=active_job.id if active_job is not None else None,
            next_poll=f"/api/v1/jobs/{active_job.id}" if active_job is not None else None,
            duplicate=True,
            duplicate_of_id=duplicate.id,
        ),
    )


def _parse_upload_metadata(metadata: str | None) -> dict[str, object]:
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid metadata JSON") from exc
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid metadata JSON")
    return parsed_metadata


async def _accept_document_upload(
    *,
    session: AsyncSession,
    file: UploadFile,
    project_id: UUID | None,
    doc_type: str,
    metadata: dict[str, object],
    storage_prefix: str,
    job_label_prefix: str,
    trace_prefix: str,
    priority: int,
    input_ref_extra: dict[str, object] | None = None,
    ensure_sample_id: bool = False,
    dedupe_global_library_upload: bool = False,
) -> APIResponse[DocumentUploadAccepted]:
    suffix = Path(file.filename or "").suffix or ".bin"
    storage = get_object_storage()

    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(await file.read())
        temp_path = Path(temp_file.name)

    content_sha256 = _sha256_file(temp_path) if dedupe_global_library_upload else None
    if content_sha256:
        duplicate = await _find_duplicate_global_library_document(
            session=session,
            storage=storage,
            content_sha256=content_sha256,
            file_size_bytes=temp_path.stat().st_size,
        )
        if duplicate is not None:
            temp_path.unlink(missing_ok=True)
            await session.commit()
            return await _build_duplicate_upload_response(session=session, duplicate=duplicate)
        metadata = {
            **metadata,
            "content_sha256": content_sha256,
            "upload_original_filename": file.filename or temp_path.name,
        }

    storage_path: str | None = None
    try:
        storage_path = storage.save(temp_path, prefix=storage_prefix)
        file_size_bytes = temp_path.stat().st_size
    finally:
        temp_path.unlink(missing_ok=True)

    document = Document(
        project_id=project_id,
        filename=file.filename or temp_path.name,
        file_type=suffix.lstrip(".").lower(),
        file_size_bytes=file_size_bytes,
        content_sha256=content_sha256,
        storage_path=storage_path,
        doc_type=doc_type,
        parse_status="parsing",
        meta=metadata,
    )
    session.add(document)
    try:
        await session.flush()
    except IntegrityError:
        if dedupe_global_library_upload and content_sha256:
            _safe_delete_storage_path(storage=storage, storage_path=storage_path)
            await session.rollback()
            duplicate = await _find_duplicate_global_library_document(
                session=session,
                storage=storage,
                content_sha256=content_sha256,
                file_size_bytes=file_size_bytes,
            )
            if duplicate is not None:
                await session.commit()
                return await _build_duplicate_upload_response(session=session, duplicate=duplicate)
        raise
    if ensure_sample_id and not str((document.meta or {}).get("sample_id") or "").strip():
        document.meta = {**(document.meta or {}), "sample_id": f"uploaded-{document.id}"}

    job = Job(
        project_id=project_id,
        job_type="document_parse",
        status="queued",
        input_ref={
            "document_id": str(document.id),
            "filename": document.filename,
            "doc_type": doc_type,
            **(input_ref_extra or {}),
        },
        output_ref={"progress": {"stage": "queued", "document_id": str(document.id)}},
        trace_id=f"{trace_prefix}-{uuid.uuid4()}",
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    document_id = document.id
    job_metadata = dict(document.meta or {})
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    queue = get_background_task_queue("library_parse")
    queue.submit(
        job_id=job_id,
        job_type="document_parse",
        label=f"{job_label_prefix}:{document.filename}",
        run=lambda: _run_document_parse_job(job_id, document_id, job_metadata),
        dedupe_key=f"document_parse:{document_id}",
        priority=priority,
    )

    return APIResponse(
        code=202,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message=_build_document_upload_message(
                doc_type=doc_type,
                parse_status=document.parse_status,
            ),
            job_id=job.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


async def _accept_rfp_light_upload(
    *,
    session: AsyncSession,
    file: UploadFile,
    project_id: UUID,
    metadata: dict[str, object],
    storage_prefix: str,
    job_label_prefix: str,
    trace_prefix: str,
    priority: int,
) -> APIResponse[DocumentUploadAccepted]:
    """Lightweight project-RFP upload helper.

    Mirrors :func:`_accept_document_upload` for the storage + Document/Job persistence
    contract but routes the parse work to ``rfp_light_parse`` on the ``interactive``
    queue.  The full historical-ingestion pipeline (Chunker / Embedder / Qdrant /
    raw_document / figure assets / case library refresh) is intentionally bypassed.
    """

    suffix = Path(file.filename or "").suffix or ".bin"
    storage = get_object_storage()

    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(await file.read())
        temp_path = Path(temp_file.name)

    storage_path: str | None = None
    try:
        storage_path = storage.save(temp_path, prefix=storage_prefix)
        file_size_bytes = temp_path.stat().st_size
    finally:
        temp_path.unlink(missing_ok=True)

    document = Document(
        project_id=project_id,
        filename=file.filename or temp_path.name,
        file_type=suffix.lstrip(".").lower(),
        file_size_bytes=file_size_bytes,
        content_sha256=None,
        storage_path=storage_path,
        doc_type="rfp",
        parse_status="parsing",
        meta={**metadata, "rfp_parse_mode": "light"},
    )
    session.add(document)
    await session.flush()

    job = Job(
        project_id=project_id,
        job_type="rfp_light_parse",
        status="queued",
        input_ref={
            "document_id": str(document.id),
            "filename": document.filename,
            "doc_type": "rfp",
            "rfp_parse_mode": "light",
        },
        output_ref={
            "progress": {
                "stage": "queued",
                "document_id": str(document.id),
                "rfp_parse_mode": "light",
            }
        },
        trace_id=f"{trace_prefix}-{uuid.uuid4()}",
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    document_id = document.id
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    queue = get_background_task_queue("interactive")
    queue.submit(
        job_id=job_id,
        job_type="rfp_light_parse",
        label=f"{job_label_prefix}:{document.filename}",
        run=lambda: _run_rfp_light_parse_job(job_id, document_id),
        dedupe_key=f"rfp_light_parse:{document_id}",
        priority=priority,
    )

    return APIResponse(
        code=202,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message="项目需求文档已接收，正在轻量解析（不进入历史方案库）",
            job_id=job.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.post(
    "/projects/{project_id}/documents/upload",
    response_model=APIResponse[DocumentUploadAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    metadata: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentUploadAccepted]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    settings = get_settings()
    parsed_metadata = _parse_upload_metadata(metadata)
    if str(doc_type or "").lower() == "rfp" and settings.rfp_light_parse_enabled:
        return await _accept_rfp_light_upload(
            session=session,
            file=file,
            project_id=project_id,
            metadata=parsed_metadata,
            storage_prefix=f"{project_id}_rfp_",
            job_label_prefix="rfp-light-parse",
            trace_prefix="rfp-light-parse",
            priority=20,
        )
    return await _accept_document_upload(
        session=session,
        file=file,
        project_id=project_id,
        doc_type=doc_type,
        metadata=parsed_metadata,
        storage_prefix=f"{project_id}_",
        job_label_prefix="parse",
        trace_prefix="document-parse",
        priority=40,
    )


@router.post(
    "/library/materials/import",
    response_model=APIResponse[DocumentUploadAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_library_material(
    file: UploadFile = File(...),
    route: str = Form(default="main_indexed"),
    metadata: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentUploadAccepted]:
    normalized_route = _normalize_library_import_route(route)
    doc_type = LIBRARY_IMPORT_DOC_TYPE_BY_ROUTE[normalized_route]
    base_metadata = {
        **_parse_upload_metadata(metadata),
        "source_kind": "uploaded_document",
        "material_route": normalized_route,
        "library_track": "pilot_main" if normalized_route == "main_indexed" else normalized_route,
        "ingestion_recommendation": "uploaded_historical_material",
    }
    return await _accept_document_upload(
        session=session,
        file=file,
        project_id=None,
        doc_type=doc_type,
        metadata=base_metadata,
        storage_prefix="library_upload_",
        job_label_prefix="import-library",
        trace_prefix="library-document-parse",
        priority=35,
        input_ref_extra={"library_route": normalized_route},
        ensure_sample_id=True,
        dedupe_global_library_upload=True,
    )


@router.get("/projects/{project_id}/documents", response_model=APIResponse[list[DocumentRead]])
async def list_project_documents(
    project_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[DocumentRead]]:
    result = await session.scalars(
        select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc())
    )
    return APIResponse(
        code=200,
        message="success",
        data=[DocumentRead.model_validate(document) for document in result.all()],
    )


@router.get("/documents/history-library/status", response_model=APIResponse[HistoryLibraryRefreshStatusRead])
async def get_history_library_refresh_status() -> APIResponse[HistoryLibraryRefreshStatusRead]:
    return APIResponse(
        code=200,
        message="success",
        data=HistoryLibraryRefreshStatusRead.model_validate(read_case_library_refresh_status()),
    )


@router.get("/documents/{document_id}", response_model=APIResponse[DocumentRead])
async def get_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentRead]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return APIResponse(code=200, message="success", data=DocumentRead.model_validate(document))


@router.post(
    "/documents/{document_id}/reparse",
    response_model=APIResponse[DocumentUploadAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentUploadAccepted]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    settings = get_settings()
    if str(document.doc_type or "").lower() == "rfp" and settings.rfp_light_parse_enabled:
        return await _reparse_rfp_light_document(session=session, document=document)

    dedupe_key = f"document_parse:{document.id}"
    queue = get_background_task_queue("library_parse")
    active_job_id = queue.active_job_id(dedupe_key)
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=DocumentUploadAccepted(
                    id=document.id,
                    filename=document.filename,
                    parse_status=document.parse_status,
                    message="文档已在解析队列中，请稍后刷新状态",
                    job_id=active_job.id,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    active_job = await session.scalar(
        select(Job)
        .where(Job.job_type == "document_parse")
        .where(Job.status.in_(["queued", "running"]))
        .where(cast(Job.input_ref, String).contains(str(document.id)))
        .order_by(Job.created_at.desc())
    )
    if active_job is not None:
        return APIResponse(
            code=202,
            message="success",
            data=DocumentUploadAccepted(
                id=document.id,
                filename=document.filename,
                parse_status=document.parse_status,
                message="文档已在解析队列中，请稍后刷新状态",
                job_id=active_job.id,
                next_poll=f"/api/v1/jobs/{active_job.id}",
            ),
        )

    document.parse_status = "parsing"
    job = Job(
        project_id=document.project_id,
        job_type="document_parse",
        status="queued",
        input_ref={
            "document_id": str(document.id),
            "filename": document.filename,
            "doc_type": document.doc_type,
            "reparse": True,
        },
        output_ref={"progress": {"stage": "queued", "document_id": str(document.id), "reparse": True}},
        trace_id=f"document-reparse-{uuid.uuid4()}",
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    base_metadata = dict(document.meta or {})
    await session.commit()
    await session.refresh(job)
    await session.refresh(document)

    queue.submit(
        job_id=job_id,
        job_type="document_parse",
        label=f"reparse:{document.filename}",
        run=lambda: _run_document_parse_job(job_id, document_id, base_metadata),
        dedupe_key=dedupe_key,
        priority=20,
    )

    return APIResponse(
        code=202,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message=_build_document_upload_message(
                doc_type=document.doc_type,
                parse_status=document.parse_status,
                reparsed=True,
            ),
            job_id=job.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


async def _reparse_rfp_light_document(
    *,
    session: AsyncSession,
    document: Document,
) -> APIResponse[DocumentUploadAccepted]:
    """Reparse path for project RFP documents.

    Reuses the existing storage object and creates a fresh ``rfp_light_parse`` job
    on the ``interactive`` queue.  De-dupes both prior ``rfp_light_parse`` runs
    AND legacy ``document_parse`` runs that may still be in flight from before
    the migration.
    """

    document_id = document.id
    light_dedupe_key = f"rfp_light_parse:{document_id}"
    legacy_dedupe_key = f"document_parse:{document_id}"

    interactive_queue = get_background_task_queue("interactive")
    library_queue = get_background_task_queue("library_parse")
    active_job_id = interactive_queue.active_job_id(light_dedupe_key) or library_queue.active_job_id(
        legacy_dedupe_key
    )
    if active_job_id is not None:
        active_job = await session.get(Job, active_job_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            return APIResponse(
                code=202,
                message="success",
                data=DocumentUploadAccepted(
                    id=document.id,
                    filename=document.filename,
                    parse_status=document.parse_status,
                    message="文档已在需求解析队列中，请稍后刷新状态",
                    job_id=active_job.id,
                    next_poll=f"/api/v1/jobs/{active_job.id}",
                ),
            )

    active_job = await session.scalar(
        select(Job)
        .where(Job.job_type.in_(["rfp_light_parse", "document_parse"]))
        .where(Job.status.in_(["queued", "running"]))
        .where(cast(Job.input_ref, String).contains(str(document_id)))
        .order_by(Job.created_at.desc())
    )
    if active_job is not None:
        return APIResponse(
            code=202,
            message="success",
            data=DocumentUploadAccepted(
                id=document.id,
                filename=document.filename,
                parse_status=document.parse_status,
                message="文档已在需求解析队列中，请稍后刷新状态",
                job_id=active_job.id,
                next_poll=f"/api/v1/jobs/{active_job.id}",
            ),
        )

    document.parse_status = "parsing"
    job = Job(
        project_id=document.project_id,
        job_type="rfp_light_parse",
        status="queued",
        input_ref={
            "document_id": str(document_id),
            "filename": document.filename,
            "doc_type": "rfp",
            "rfp_parse_mode": "light",
            "reparse": True,
        },
        output_ref={
            "progress": {
                "stage": "queued",
                "document_id": str(document_id),
                "rfp_parse_mode": "light",
                "reparse": True,
            }
        },
        trace_id=f"rfp-light-reparse-{uuid.uuid4()}",
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    await session.commit()
    await session.refresh(job)
    await session.refresh(document)

    interactive_queue.submit(
        job_id=job_id,
        job_type="rfp_light_parse",
        label=f"rfp-light-reparse:{document.filename}",
        run=lambda: _run_rfp_light_parse_job(job_id, document_id),
        dedupe_key=light_dedupe_key,
        priority=20,
    )

    return APIResponse(
        code=202,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message="项目需求文档已重新进入轻量解析",
            job_id=job.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.delete("/documents/{document_id}", response_model=APIResponse[dict[str, str]])
async def delete_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[dict[str, str]]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    qdrant = QdrantService()
    chunks = (await session.scalars(select(Chunk).where(Chunk.document_id == document.id))).all()
    qdrant.delete_points([str(chunk.qdrant_point_id) for chunk in chunks if chunk.qdrant_point_id])
    storage = get_object_storage()
    raw_document_id = (document.meta or {}).get("raw_document_id")
    if raw_document_id:
        try:
            raw_document = await session.get(RawDocument, UUID(str(raw_document_id)))
        except (TypeError, ValueError):
            raw_document = None
        if raw_document is not None:
            figure_assets = (
                await session.scalars(select(FigureAsset).where(FigureAsset.raw_document_id == raw_document.id))
            ).all()
            for asset in figure_assets:
                if (asset.meta or {}).get("storage_fallback"):
                    continue
                _safe_delete_storage_path(storage=storage, storage_path=asset.asset_uri)
            await session.delete(raw_document)
    _safe_delete_storage_path(storage=storage, storage_path=document.storage_path)
    delete_uploaded_document_library_cache(document_id=str(document.id))
    await session.delete(document)
    await session.commit()
    if _should_refresh_history_library(doc_type=document.doc_type):
        # Phase 1 / #4 review fix: route case library refresh through the
        # maintenance queue so concurrent deletes / uploads collapse into a
        # single bounded refresh visible on GET /api/v1/jobs/queue.
        submit_case_library_refresh_job()
    return APIResponse(code=200, message="success", data={"status": "deleted"})


@router.get("/documents/{document_id}/chunks", response_model=APIResponse[list[ChunkRead]])
async def get_document_chunks(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[ChunkRead]]:
    result = await session.scalars(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index.asc())
    )
    return APIResponse(
        code=200,
        message="success",
        data=[ChunkRead.model_validate(chunk) for chunk in result.all()],
    )


@router.get("/documents/{document_id}/figure-assets", response_model=APIResponse[list[FigureAssetRead]])
async def get_document_figure_assets(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[FigureAssetRead]]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    raw_document_id = (document.meta or {}).get("raw_document_id")
    if not raw_document_id:
        return APIResponse(code=200, message="success", data=[])

    try:
        raw_document_uuid = UUID(str(raw_document_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid raw document linkage") from exc

    result = await session.scalars(
        select(FigureAsset)
        .where(FigureAsset.raw_document_id == raw_document_uuid)
        .order_by(FigureAsset.page_no.asc().nulls_last(), FigureAsset.created_at.asc())
    )
    return APIResponse(
        code=200,
        message="success",
        data=[FigureAssetRead.model_validate(asset) for asset in result.all()],
    )


@router.get("/assets/{asset_id}/content")
async def get_asset_content(
    asset_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    asset = await session.get(FigureAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    storage = get_object_storage()
    try:
        materialized = storage.materialize(asset.asset_uri)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset content not found") from exc

    media_type = mimetypes.guess_type(materialized.path.name)[0] or "application/octet-stream"
    return FileResponse(
        path=materialized.path,
        media_type=media_type,
        filename=Path(materialized.path.name).name,
        background=BackgroundTask(materialized.cleanup),
    )


@router.get("/documents/{document_id}/table-assets", response_model=APIResponse[list[FigureAssetRead]])
async def get_document_table_assets(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[FigureAssetRead]]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    raw_document_id = (document.meta or {}).get("raw_document_id")
    if not raw_document_id:
        return APIResponse(code=200, message="success", data=[])

    try:
        raw_document_uuid = UUID(str(raw_document_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid raw document linkage") from exc

    result = await session.scalars(
        select(FigureAsset)
        .where(FigureAsset.raw_document_id == raw_document_uuid, FigureAsset.asset_type == "table")
        .order_by(FigureAsset.page_no.asc().nulls_last(), FigureAsset.created_at.asc())
    )
    return APIResponse(
        code=200,
        message="success",
        data=[FigureAssetRead.model_validate(asset) for asset in result.all()],
    )


@router.post(
    "/documents/{document_id}/table-assets/{asset_id}/reconstruct",
    response_model=APIResponse[JobAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_table_asset_reconstruction(
    document_id: UUID,
    asset_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[JobAcceptedData]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    raw_document_id = (document.meta or {}).get("raw_document_id")
    if not raw_document_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document has no raw document linkage")

    try:
        raw_document_uuid = UUID(str(raw_document_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid raw document linkage") from exc

    raw_document = await session.get(RawDocument, raw_document_uuid)
    if raw_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw document not found")

    asset = await session.get(FigureAsset, asset_id)
    if asset is None or asset.raw_document_id != raw_document_uuid or asset.asset_type != "table":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table asset not found")

    asset_meta = dict(asset.meta or {})
    reconstruction_status = str(asset_meta.get("reconstruction_status") or "pending")
    if reconstruction_status in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Table asset reconstruction already queued")
    if reconstruction_status == "succeeded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Table asset reconstruction already completed")

    job = Job(
        project_id=document.project_id,
        job_type="table_reconstruction",
        status="queued",
        trace_id=uuid.uuid4().hex,
        input_ref={
            "document_id": str(document.id),
            "raw_document_id": str(raw_document.id),
            "table_asset_id": str(asset.id),
            "source_chunk_id": asset_meta.get("source_chunk_id"),
            "strategy": asset_meta.get("ingestion_strategy"),
            "table_profile": (asset_meta.get("table_profile") or {}).get("profile_name"),
        },
        output_ref={},
    )
    session.add(job)
    await session.flush()

    asset.meta = {
        **asset_meta,
        "reconstruction_status": "queued",
        "reconstruction_backend": "reserved_queue",
        "reconstruction_confidence": None,
        "reconstruction_job_id": str(job.id),
        "reconstruction_requested_at": datetime.now(timezone.utc).isoformat(),
    }

    source_chunk_id = asset_meta.get("source_chunk_id")
    if source_chunk_id:
        try:
            source_chunk = await session.get(Chunk, UUID(str(source_chunk_id)))
        except (TypeError, ValueError):
            source_chunk = None
        if source_chunk is not None:
            source_chunk.meta = {
                **(source_chunk.meta or {}),
                "table_asset_status": "queued_reconstruction",
                "table_asset_job_id": str(job.id),
            }

    await _refresh_table_asset_counters(session=session, document=document, raw_document=raw_document)
    await session.commit()
    await session.refresh(job)
    await session.refresh(asset)

    return APIResponse(
        code=202,
        message="success",
        data=JobAcceptedData(
            job_id=job.id,
            status=job.status,
            resource_id=asset.id,
            next_poll=f"/api/v1/jobs/{job.id}",
        ),
    )


@router.post("/projects/{project_id}/assets/search", response_model=APIResponse[AssetSearchResponse])
async def search_project_assets(
    project_id: UUID,
    payload: AssetSearchRequest,
    session: AsyncSession = Depends(get_db_session),
    service: AssetRetrievalService = Depends(get_asset_retrieval_service),
) -> APIResponse[AssetSearchResponse]:
    try:
        result = await service.search_project_assets(
            session=session,
            project_id=project_id,
            query=payload.query,
            top_k=payload.top_k,
            asset_types=payload.asset_types,
            doc_types=payload.doc_types,
            section_context=payload.section_context,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return APIResponse(code=200, message="success", data=result)
