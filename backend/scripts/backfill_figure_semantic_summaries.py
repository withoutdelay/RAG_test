from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import get_session_factory
from app.models.document import Document
from app.models.figure_asset import FigureAsset
from app.models.raw_document import RawDocument
from app.services.parsing.asset_semantic_summary import AssetSemanticSummaryService
from app.services.parsing.docling_parser import ParsedAsset
from app.utils.object_storage import get_object_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill semantic summaries for historical PDF figure assets.")
    parser.add_argument("--doc-type", default="historical_proposal", help="Document type filter. Defaults to historical_proposal.")
    parser.add_argument("--document-name", default="", help="Optional filename substring filter.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max document count to process.")
    parser.add_argument("--force", action="store_true", help="Regenerate summaries even if semantic_summary already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Run LLM summarization without committing metadata.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    session_factory = get_session_factory()
    storage = get_object_storage()
    summary_service = AssetSemanticSummaryService()

    processed_docs = 0
    summarized_docs = 0
    candidate_assets = 0
    summarized_assets = 0
    failed_docs: list[str] = []
    skipped_docs: list[str] = []

    async with session_factory() as session:
        query = select(Document).where(
            Document.project_id.is_(None),
            Document.file_type == "pdf",
            Document.doc_type == args.doc_type,
        ).order_by(Document.created_at.asc())
        documents = list((await session.scalars(query)).all())
        if args.document_name:
            documents = [doc for doc in documents if args.document_name in doc.filename]
        if args.limit > 0:
            documents = documents[: args.limit]

        for document in documents:
            processed_docs += 1
            raw_document = await _load_raw_document(session=session, document=document)
            if raw_document is None:
                skipped_docs.append(f"{document.filename} (missing raw_document)")
                continue

            figure_assets = list(
                (
                    await session.scalars(
                        select(FigureAsset)
                        .where(FigureAsset.raw_document_id == raw_document.id)
                        .order_by(FigureAsset.page_no.asc(), FigureAsset.created_at.asc())
                    )
                ).all()
            )
            if not figure_assets:
                skipped_docs.append(f"{document.filename} (no figure assets)")
                continue

            parsed_assets: list[ParsedAsset] = []
            mapped_assets: list[FigureAsset] = []
            for asset in figure_assets:
                if asset.asset_type != "figure":
                    continue
                meta = dict(asset.meta or {})
                if not args.force and str((meta.get("semantic_summary") or {}).get("status") or "") == "summarized":
                    continue
                if args.force:
                    meta.pop("semantic_summary", None)
                parsed_assets.append(
                    ParsedAsset(
                        asset_type="figure",
                        page_no=asset.page_no,
                        title=asset.title,
                        caption=asset.caption,
                        heading_path=str(meta.get("heading_path") or "") or None,
                        context_before=str(meta.get("context_before") or "") or None,
                        context_after=str(meta.get("context_after") or "") or None,
                        bbox=meta.get("bbox"),
                        source_ref=str(meta.get("source_ref") or "") or None,
                        image_bytes=_materialize_image_bytes(storage=storage, asset=asset),
                        image_ext=Path(asset.asset_uri).suffix.lower() or ".png",
                        meta={
                            **meta,
                            "document_name": document.filename,
                        },
                    )
                )
                mapped_assets.append(asset)

            if not parsed_assets:
                skipped_docs.append(f"{document.filename} (no pending semantic summaries)")
                continue

            try:
                summarized, stats = await summary_service.summarize_assets(parsed_assets)
            except Exception as exc:
                await session.rollback()
                failed_docs.append(f"{document.filename} ({exc})")
                continue
            candidate_assets += stats.candidate_count

            doc_summarized_count = 0
            for index, parsed_asset in enumerate(summarized):
                semantic_summary = dict(parsed_asset.meta or {}).get("semantic_summary")
                if not isinstance(semantic_summary, dict):
                    continue
                mapped_assets[index].meta = {
                    **dict(mapped_assets[index].meta or {}),
                    "semantic_summary": semantic_summary,
                }
                if str(semantic_summary.get("status") or "") == "summarized":
                    doc_summarized_count += 1

            if doc_summarized_count:
                summarized_docs += 1
                summarized_assets += doc_summarized_count
            raw_document.meta = {
                **(raw_document.meta or {}),
                "asset_llm_summary_backfill_count": doc_summarized_count,
            }
            document.meta = {
                **(document.meta or {}),
                "asset_llm_summary_backfill_count": doc_summarized_count,
            }

            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()

            print(
                f"{document.filename}: figures={len(parsed_assets)} candidates={stats.candidate_count} "
                f"summarized={doc_summarized_count} requests={stats.request_count} vision={stats.vision_attached_count}"
                + (f" error={stats.error}" if stats.error else "")
            )

    print(f"Processed docs: {processed_docs}")
    print(f"Docs with summaries: {summarized_docs}")
    print(f"Candidate assets: {candidate_assets}")
    print(f"Summarized assets: {summarized_assets}")
    print(f"Skipped docs: {len(skipped_docs)}")
    for item in skipped_docs:
        print(f"  skipped: {item}")
    print(f"Failed docs: {len(failed_docs)}")
    for item in failed_docs:
        print(f"  failed: {item}")


async def _load_raw_document(*, session: Any, document: Document) -> RawDocument | None:
    raw_document_id = (document.meta or {}).get("raw_document_id")
    if not raw_document_id:
        return None
    try:
        return await session.get(RawDocument, UUID(str(raw_document_id)))
    except (TypeError, ValueError):
        return None


def _materialize_image_bytes(*, storage: Any, asset: FigureAsset) -> bytes | None:
    if bool((asset.meta or {}).get("storage_fallback")):
        return None
    materialized = storage.materialize(asset.asset_uri)
    try:
        return materialized.path.read_bytes()
    finally:
        materialized.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
