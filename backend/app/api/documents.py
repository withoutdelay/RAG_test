from __future__ import annotations

import json
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.project import Project
from app.schemas.common import APIResponse
from app.schemas.document import ChunkRead, DocumentRead, DocumentUploadAccepted
from app.services.parsing.docling_parser import ParsedDocument
from app.services.parsing.parser import ParserService
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.qdrant_client import QdrantService
from app.utils.object_storage import MaterializedObject, get_object_storage


router = APIRouter()


async def _parse_and_index_document(
    *,
    session: AsyncSession,
    document: Document,
    base_metadata: dict,
    parsed_markdown: str | None = None,
    parsed_metadata: dict | None = None,
) -> None:
    parser = ParserService()
    chunker = Chunker()
    embedder = Embedder()
    qdrant = QdrantService()
    storage = get_object_storage()

    existing_chunks = (
        await session.scalars(select(Chunk).where(Chunk.document_id == document.id))
    ).all()
    qdrant.delete_points([str(chunk.qdrant_point_id) for chunk in existing_chunks if chunk.qdrant_point_id])
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.flush()

    if parsed_markdown is None:
        materialized: MaterializedObject = storage.materialize(document.storage_path)
        try:
            parsed_document = await parser.parse_document(str(materialized.path))
        finally:
            materialized.cleanup()
    else:
        parsed_document = ParsedDocument(
            markdown=parsed_markdown,
            metadata=parsed_metadata or {},
        )

    chunk_payloads = chunker.split(
        parsed_document.markdown,
        base_metadata={
            **base_metadata,
            "doc_type": document.doc_type,
            "document_name": document.filename,
            "project_id": str(document.project_id) if document.project_id else None,
        },
    )

    for payload in chunk_payloads:
        point_id = uuid.uuid4()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=payload.chunk_index,
            chunk_type=payload.chunk_type,
            content=payload.content,
            token_count=payload.token_count,
            heading_path=payload.heading_path,
            qdrant_point_id=point_id,
            meta=payload.metadata,
        )
        session.add(chunk)
        await session.flush()

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
            },
        )

    document.parse_status = "done"
    document.meta = {**base_metadata, **parsed_document.metadata}


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
            parsed_markdown=parsed_document.markdown,
            parsed_metadata=parsed_document.metadata,
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
    get_object_storage().delete(document.storage_path)
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
