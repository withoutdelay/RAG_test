from __future__ import annotations

from datetime import datetime, timezone
import json
import mimetypes
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import get_settings
from app.db import get_db_session
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.figure_asset import FigureAsset
from app.models.job import Job
from app.models.project import Project
from app.models.raw_document import RawDocument
from app.schemas.artifacts import JobAcceptedData
from app.schemas.common import APIResponse
from app.schemas.document import ChunkRead, DocumentRead, DocumentUploadAccepted, FigureAssetRead
from app.schemas.retrieval import AssetSearchRequest, AssetSearchResponse
from app.services.parsing.docling_parser import ParsedDocument
from app.services.parsing.parser import ParserService
from app.services.parsing.table_profile import build_table_profile
from app.services.retrieval import AssetRetrievalService
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.ingestion_filter import SafeIngestionFilter
from app.services.vectorstore.qdrant_client import QdrantService
from app.services.v2_errors import ArtifactNotFoundError
from app.utils.object_storage import MaterializedObject, get_object_storage


router = APIRouter()


def get_asset_retrieval_service() -> AssetRetrievalService:
    return AssetRetrievalService()


async def _parse_and_index_document(
    *,
    session: AsyncSession,
    document: Document,
    base_metadata: dict,
    parsed_document: ParsedDocument | None = None,
) -> None:
    settings = get_settings()
    parser = ParserService()
    chunker = Chunker()
    embedder = Embedder()
    ingestion_filter = SafeIngestionFilter() if settings.safe_ingestion_enabled else None
    qdrant = QdrantService()
    storage = get_object_storage()

    existing_chunks = (
        await session.scalars(select(Chunk).where(Chunk.document_id == document.id))
    ).all()
    qdrant.delete_points([str(chunk.qdrant_point_id) for chunk in existing_chunks if chunk.qdrant_point_id])
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.flush()

    if parsed_document is None:
        materialized: MaterializedObject = storage.materialize(document.storage_path)
        try:
            parsed_document = await parser.parse_document(str(materialized.path))
        finally:
            materialized.cleanup()

    parser_placeholder = bool((parsed_document.metadata or {}).get("parser_placeholder"))
    if parser_placeholder:
        chunk_payloads = []
    else:
        chunk_payloads = chunker.split(
            parsed_document.markdown,
            base_metadata={
                **base_metadata,
                "doc_type": document.doc_type,
                "document_name": document.filename,
                "project_id": str(document.project_id) if document.project_id else None,
            },
        )

    indexed_chunk_count = 0
    skipped_chunk_count = 0
    preserved_table_chunks: list[Chunk] = []

    for payload in chunk_payloads:
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
        }
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
            vector = await embedder.embed_text(payload.content)
            qdrant.upsert_chunk(
                point_id=point_id,
                vector=vector,
                payload={
                    "project_id": str(document.project_id) if document.project_id else None,
                    "document_id": str(document.id),
                    "document_name": document.filename,
                    "chunk_id": str(chunk.id),
                    "chunk_index": payload.chunk_index,
                    "chunk_type": payload.chunk_type,
                    "heading_path": payload.heading_path,
                    "content": payload.content,
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
            indexed_chunk_count += 1
        else:
            skipped_chunk_count += 1
            if payload.chunk_type == "TABLE" and preserve_for_assets:
                preserved_table_chunks.append(chunk)

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

    document.parse_status = "done"
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
    raw_document.parse_status = "done"
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


async def _upsert_raw_document(
    *,
    session: AsyncSession,
    document: Document,
    base_metadata: dict,
) -> RawDocument:
    corpus_scope = "global" if document.project_id is None else "project"
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
        asset_uri = raw_document.file_uri
        if asset.image_bytes:
            asset_uri = storage.save_bytes(
                asset.image_bytes,
                suffix=asset.image_ext or ".png",
                prefix=f"{document.project_id}_figure_{index}_",
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
                "heading_path": asset.heading_path,
                "context_before": asset.context_before,
                "context_after": asset.context_after,
                "bbox": asset.bbox,
                "source_ref": asset.source_ref,
                "legacy_document_id": str(document.id),
                "storage_fallback": asset.image_bytes is None,
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


@router.post(
    "/projects/{project_id}/documents/upload",
    response_model=APIResponse[DocumentUploadAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    project_id: UUID,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    metadata: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentUploadAccepted]:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid metadata JSON") from exc

    suffix = Path(file.filename or "").suffix or ".bin"
    storage = get_object_storage()
    parser = ParserService()

    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(await file.read())
        temp_path = Path(temp_file.name)

    parsed_document = await parser.parse_document(str(temp_path))
    storage_path = storage.save(temp_path, prefix=f"{project_id}_")
    document = Document(
        project_id=project_id,
        filename=file.filename or temp_path.name,
        file_type=suffix.lstrip(".").lower(),
        file_size_bytes=temp_path.stat().st_size,
        storage_path=storage_path,
        doc_type=doc_type,
        parse_status="parsing",
        meta=parsed_metadata,
    )
    session.add(document)
    await session.flush()

    try:
        await _parse_and_index_document(
            session=session,
            document=document,
            base_metadata=parsed_metadata,
            parsed_document=parsed_document,
        )
        await session.commit()
    except Exception as exc:
        document.parse_status = "failed"
        await session.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Document parsing failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

    return APIResponse(
        code=202,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message="文档已接收并完成解析入库",
        ),
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


@router.get("/documents/{document_id}", response_model=APIResponse[DocumentRead])
async def get_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentRead]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return APIResponse(code=200, message="success", data=DocumentRead.model_validate(document))


@router.post("/documents/{document_id}/reparse", response_model=APIResponse[DocumentUploadAccepted])
async def reparse_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DocumentUploadAccepted]:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document.parse_status = "parsing"
    await session.flush()

    try:
        await _parse_and_index_document(session=session, document=document, base_metadata=document.meta or {})
        await session.commit()
    except Exception as exc:
        document.parse_status = "failed"
        await session.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Document reparse failed: {exc}") from exc

    return APIResponse(
        code=200,
        message="success",
        data=DocumentUploadAccepted(
            id=document.id,
            filename=document.filename,
            parse_status=document.parse_status,
            message="文档已重新解析并入库",
        ),
    )


@router.delete("/documents/{document_id}", response_model=APIResponse[dict[str, str]])
async def delete_document(
    document_id: UUID,
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
    await session.delete(document)
    await session.commit()
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
