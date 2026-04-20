from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from app.services.parsing.sample_manifest import (
    TRACK_PILOT_MAIN,
    apply_profile_to_manifest_entry,
    build_sample_manifest_entry,
)


SUPPORTED_DOCUMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
LIBRARY_READY_RECOMMENDATIONS = frozenset({"main_vector_ready", "text_primary_with_asset_review"})


def iter_document_paths(raw_paths: Iterable[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            for suffix in ("*.pdf", "*.PDF", "*.doc", "*.DOC", "*.docx", "*.DOCX"):
                resolved.extend(sorted(item for item in path.rglob(suffix) if item.is_file()))
            continue
        if path.suffix.lower() not in SUPPORTED_DOCUMENT_SUFFIXES:
            raise SystemExit(f"Only PDF/DOC/DOCX files are supported: {raw_path}")
        resolved.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in resolved:
        normalized = path.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    if not unique:
        raise SystemExit("No supported document files were found.")
    return unique


def build_direct_source_entry(
    *,
    file_path: str | Path,
    parsed_metadata: dict[str, Any] | None = None,
    library_track: str = TRACK_PILOT_MAIN,
    source: str = "direct_paths",
) -> dict[str, Any]:
    resolved_track = str(library_track).strip() or TRACK_PILOT_MAIN
    entry = build_sample_manifest_entry(
        file_path=file_path,
        assigned_track=resolved_track,
    )
    metadata = parsed_metadata or {}
    if metadata:
        profile_metadata = dict(metadata.get("document_profile") or {})
        entry = apply_profile_to_manifest_entry(
            entry,
            profile_name=str(profile_metadata.get("name") or "unknown"),
            ingestion_recommendation=str(metadata.get("ingestion_recommendation") or "unknown"),
            high_risk_content_flags=list(metadata.get("high_risk_content_flags") or []),
            metrics=dict(profile_metadata.get("metrics") or {}),
        )

    payload = entry.to_dict()
    payload["phase_b_track"] = resolved_track
    payload["library_track"] = resolved_track
    payload["track"] = resolved_track
    payload["source"] = source
    return payload


def is_library_ready_entry(entry: dict[str, Any]) -> bool:
    recommendation = str(entry.get("ingestion_recommendation") or "").strip()
    if not recommendation:
        return True
    return recommendation in LIBRARY_READY_RECOMMENDATIONS


def resolve_library_source_path(entry: dict[str, Any]) -> tuple[Path, str]:
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    preferred_candidates = (
        (details.get("extracted_text_path"), "extracted_text"),
        (details.get("converted_asset_path"), "converted_asset"),
        (entry.get("file_path"), "original_file"),
    )
    for raw_path, source_kind in preferred_candidates:
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser()
        if path.exists():
            return path.resolve(), source_kind
    raise FileNotFoundError(str(entry.get("file_path") or "missing source path"))
