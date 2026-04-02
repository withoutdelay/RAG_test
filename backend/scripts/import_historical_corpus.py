from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.api.documents import _parse_and_index_document
from app.db import get_session_factory
from app.models.document import Document
from app.services.parsing.parser import ParserService
from app.services.vectorstore.qdrant_client import QdrantService
from app.utils.object_storage import get_object_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import manifest-backed historical proposals into the runtime corpus.")
    parser.add_argument(
        "--manifest",
        default="data/sample_manifests/sample_manifest.json",
        help="Path to the sample manifest JSON. Defaults to backend/data/sample_manifests/sample_manifest.json.",
    )
    parser.add_argument(
        "--tracks",
        default="pilot_main",
        help="Comma-separated phase_b_track values to import. Defaults to pilot_main.",
    )
    parser.add_argument(
        "--doc-type",
        default="historical_proposal",
        help="Document type to write into the runtime corpus. Defaults to historical_proposal.",
    )
    parser.add_argument(
        "--recreate-collection",
        action="store_true",
        help="Delete and recreate the Qdrant collection before importing. Use only for local rebuilds.",
    )
    return parser.parse_args()


def _build_base_metadata(entry: dict) -> dict:
    return {
        "source": "sample_manifest",
        "sample_id": entry.get("sample_id"),
        "assigned_track": entry.get("assigned_track"),
        "phase_b_track": entry.get("phase_b_track"),
        "library_track": entry.get("phase_b_track"),
        "industry": entry.get("industry"),
        "product_line": entry.get("product_line"),
        "solution_family": entry.get("solution_family"),
        "key_equipment": entry.get("key_equipment") or [],
        "quality_tier": entry.get("quality_tier"),
        "manual_notes": entry.get("manual_notes"),
        "document_type_hint": entry.get("document_type_hint"),
        "detected_profile": entry.get("detected_profile"),
        "ingestion_recommendation": entry.get("ingestion_recommendation"),
        "high_risk_content_flags": entry.get("high_risk_content_flags") or [],
        "metrics": entry.get("metrics") or {},
    }


async def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_tracks = {part.strip() for part in args.tracks.split(",") if part.strip()}
    entries = [
        entry
        for entry in manifest.get("entries") or []
        if str(entry.get("phase_b_track") or "").strip() in selected_tracks
    ]

    if args.recreate_collection:
        qdrant = QdrantService()
        try:
            qdrant.client.delete_collection(qdrant.collection_name)
        except Exception:
            pass

    parser = ParserService()
    storage = get_object_storage()
    session_factory = get_session_factory()

    imported: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    async with session_factory() as session:
        existing_names = set(
            await session.scalars(
                select(Document.filename).where(
                    Document.project_id.is_(None),
                    Document.doc_type == args.doc_type,
                )
            )
        )

        for entry in entries:
            file_path = Path(str(entry.get("file_path") or "")).expanduser()
            file_name = str(entry.get("file_name") or file_path.name)

            if not file_path.exists():
                failed.append(f"{file_name} (missing file)")
                continue
            if file_name in existing_names:
                skipped.append(file_name)
                continue

            parsed_document = await parser.parse_document(str(file_path))
            storage_path = storage.save(file_path, prefix="historical_")
            document = Document(
                project_id=None,
                filename=file_name,
                file_type=file_path.suffix.lstrip(".").lower(),
                file_size_bytes=file_path.stat().st_size,
                storage_path=storage_path,
                doc_type=args.doc_type,
                parse_status="parsing",
                meta=_build_base_metadata(entry),
            )

            try:
                session.add(document)
                await session.flush()
                await _parse_and_index_document(
                    session=session,
                    document=document,
                    base_metadata=document.meta or {},
                    parsed_document=parsed_document,
                )
                await session.commit()
                imported.append(file_name)
                existing_names.add(file_name)
            except Exception as exc:
                await session.rollback()
                try:
                    storage.delete(storage_path)
                except Exception:
                    pass
                failed.append(f"{file_name} ({exc})")

    print(f"Selected entries: {len(entries)}")
    print(f"Imported: {len(imported)}")
    for name in imported:
        print(f"  imported: {name}")
    print(f"Skipped existing: {len(skipped)}")
    for name in skipped:
        print(f"  skipped: {name}")
    print(f"Failed: {len(failed)}")
    for item in failed:
        print(f"  failed: {item}")


if __name__ == "__main__":
    asyncio.run(main())
