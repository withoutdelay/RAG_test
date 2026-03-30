from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
import unicodedata
from typing import Any


TRACK_NEEDS_REVIEW = "needs_review"
TRACK_PILOT_MAIN = "pilot_main"
TRACK_HOLDOUT = "holdout_eval"
TRACK_OCR_ASSET_ONLY = "ocr_asset_only"


@dataclass(frozen=True)
class SampleManifestEntry:
    sample_id: str
    file_name: str
    file_path: str
    file_format: str
    file_size_bytes: int
    assigned_track: str
    suggested_track: str
    document_type_hint: str
    industry: str | None
    product_line: str | None
    solution_family: str | None
    key_equipment: list[str]
    quality_tier: str
    manual_notes: str
    detected_profile: str | None
    ingestion_recommendation: str | None
    high_risk_content_flags: list[str]
    metrics: dict[str, Any]
    phase_b_track: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_sample_manifest_entry(
    *,
    file_path: str | Path,
    assigned_track: str = TRACK_NEEDS_REVIEW,
    industry: str | None = None,
    product_line: str | None = None,
    solution_family: str | None = None,
    key_equipment: list[str] | None = None,
    quality_tier: str = "unknown",
    manual_notes: str = "",
) -> SampleManifestEntry:
    path = Path(file_path).expanduser().resolve()
    stat = path.stat()
    return SampleManifestEntry(
        sample_id=build_sample_id(path),
        file_name=path.name,
        file_path=str(path),
        file_format=path.suffix.lower().lstrip("."),
        file_size_bytes=stat.st_size,
        assigned_track=assigned_track,
        suggested_track=TRACK_NEEDS_REVIEW,
        document_type_hint="unknown",
        industry=industry,
        product_line=product_line,
        solution_family=solution_family,
        key_equipment=list(key_equipment or []),
        quality_tier=quality_tier,
        manual_notes=manual_notes,
        detected_profile=None,
        ingestion_recommendation=None,
        high_risk_content_flags=[],
        metrics={},
        phase_b_track=None,
    )


def apply_profile_to_manifest_entry(
    entry: SampleManifestEntry,
    *,
    profile_name: str,
    ingestion_recommendation: str,
    high_risk_content_flags: list[str] | tuple[str, ...] | None = None,
    metrics: dict[str, Any] | None = None,
) -> SampleManifestEntry:
    flags = list(high_risk_content_flags or [])
    metrics = dict(metrics or {})
    return SampleManifestEntry(
        **{
            **entry.to_dict(),
            "document_type_hint": _map_profile_to_document_type_hint(profile_name),
            "suggested_track": _map_ingestion_to_track(ingestion_recommendation),
            "detected_profile": profile_name,
            "ingestion_recommendation": ingestion_recommendation,
            "high_risk_content_flags": flags,
            "metrics": metrics,
            "phase_b_track": None,
        }
    )


def build_sample_manifest(
    *,
    entries: list[SampleManifestEntry],
    root_paths: list[str] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    phase_b_plan = build_phase_b_plan(entries)
    phase_b_track_by_id = _build_phase_b_track_index(phase_b_plan)
    entry_payloads = [_entry_payload(entry, phase_b_track_by_id) for entry in entries]
    return {
        "version": 1,
        "generated_at": generated_at,
        "root_paths": list(root_paths or []),
        "total_samples": len(entries),
        "summary": summarize_sample_manifest(entries),
        "phase_b_plan": phase_b_plan,
        "entries": entry_payloads,
    }


def summarize_sample_manifest(entries: list[SampleManifestEntry]) -> dict[str, Any]:
    track_counts = Counter(entry.assigned_track for entry in entries)
    suggested_track_counts = Counter(entry.suggested_track for entry in entries)
    profile_counts = Counter(entry.detected_profile or "unknown" for entry in entries)
    recommendation_counts = Counter(entry.ingestion_recommendation or "unknown" for entry in entries)
    format_counts = Counter(entry.file_format or "unknown" for entry in entries)
    return {
        "track_counts": dict(sorted(track_counts.items())),
        "suggested_track_counts": dict(sorted(suggested_track_counts.items())),
        "profile_counts": dict(sorted(profile_counts.items())),
        "ingestion_recommendation_counts": dict(sorted(recommendation_counts.items())),
        "format_counts": dict(sorted(format_counts.items())),
    }


def render_sample_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Sample Manifest Summary",
        "",
        f"- Generated at: `{manifest.get('generated_at')}`",
        f"- Total samples: `{manifest.get('total_samples')}`",
    ]

    root_paths = manifest.get("root_paths") or []
    if root_paths:
        lines.append("- Root paths:")
        for path in root_paths:
            lines.append(f"  - `{path}`")

    summary = manifest.get("summary") or {}
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Assigned tracks: {_render_counter(summary.get('track_counts'))}",
            f"- Suggested tracks: {_render_counter(summary.get('suggested_track_counts'))}",
            f"- Detected profiles: {_render_counter(summary.get('profile_counts'))}",
            f"- Ingestion recommendations: {_render_counter(summary.get('ingestion_recommendation_counts'))}",
            f"- File formats: {_render_counter(summary.get('format_counts'))}",
            "",
            "## Track Glossary",
            "",
            "- `pilot_main`: 当前适合进入主试点链路的样板，用于目录库、块库和主复用效果调优。",
            "- `holdout_eval`: 暂时不参与调优，只用于阶段性评测和回归对比的保留样板。",
            "- `needs_review`: 当前解析质量、结构质量或样板代表性仍需人工确认，暂不直接进主试点。",
            "- `ocr_asset_only`: 暂不进入主文本复用链路，只保留为 OCR / 资产参考 / 后续转换对象。",
            "",
            "## Entries",
            "",
            "| sample_id | file_name | assigned_track | phase_b_track | suggested_track | profile | recommendation | quality_tier |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for entry in manifest.get("entries") or []:
        lines.append(
            "| {sample_id} | {file_name} | {assigned_track} | {phase_b_track} | {suggested_track} | {profile} | {recommendation} | {quality_tier} |".format(
                sample_id=entry.get("sample_id") or "",
                file_name=entry.get("file_name") or "",
                assigned_track=entry.get("assigned_track") or "",
                phase_b_track=entry.get("phase_b_track") or "",
                suggested_track=entry.get("suggested_track") or "",
                profile=entry.get("detected_profile") or "unknown",
                recommendation=entry.get("ingestion_recommendation") or "unknown",
                quality_tier=entry.get("quality_tier") or "unknown",
            )
        )

    phase_b_plan = manifest.get("phase_b_plan") or {}
    lines.extend(
        [
            "",
            "## Phase B Recommendation",
            "",
            f"- Holdout eval: {_render_name_list(phase_b_plan.get('holdout_eval'))}",
            f"- Pilot main: {_render_name_list(phase_b_plan.get('pilot_main'))}",
            f"- Needs review: {_render_name_list(phase_b_plan.get('needs_review'))}",
            f"- OCR / asset only: {_render_name_list(phase_b_plan.get('ocr_asset_only'))}",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def build_sample_id(path: str | Path) -> str:
    normalized_path = str(Path(path).expanduser().resolve())
    stem = unicodedata.normalize("NFKC", Path(path).stem).strip().lower()
    ascii_slug = stem.encode("ascii", "ignore").decode("ascii")
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", ascii_slug).strip("-")
    digest = hashlib.sha1(normalized_path.encode("utf-8")).hexdigest()[:8]
    if ascii_slug:
        return f"{ascii_slug[:40]}-{digest}"
    return f"sample-{digest}"


def _map_profile_to_document_type_hint(profile_name: str) -> str:
    if profile_name == "text_digital":
        return "text_digital"
    if profile_name == "mixed_engineering_pdf":
        return "mixed_engineering_pdf"
    if profile_name == "scanned_pdf":
        return "scanned_pdf"
    if profile_name == "legacy_word_doc":
        return "legacy_word_doc"
    return "unknown"


def _map_ingestion_to_track(ingestion_recommendation: str) -> str:
    if ingestion_recommendation == "conversion_required":
        return TRACK_OCR_ASSET_ONLY
    if ingestion_recommendation == "asset_only_review":
        return TRACK_OCR_ASSET_ONLY
    if ingestion_recommendation in {"main_vector_ready", "text_primary_with_asset_review"}:
        return TRACK_PILOT_MAIN
    return TRACK_NEEDS_REVIEW


def _render_counter(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "`none`"
    return ", ".join(f"`{key}={value}`" for key, value in payload.items())


def _render_name_list(entries: list[dict[str, Any]] | None) -> str:
    if not entries:
        return "`none`"
    return ", ".join(f"`{item.get('file_name')}`" for item in entries)


def build_phase_b_plan(entries: list[SampleManifestEntry]) -> dict[str, Any]:
    conversion_required = [
        entry for entry in entries if entry.ingestion_recommendation == "conversion_required"
    ]
    holdout_eval = _recommend_holdout_eval(entries)
    holdout_ids = {entry.sample_id for entry in holdout_eval}

    needs_review = [
        entry
        for entry in entries
        if entry.sample_id not in holdout_ids and _requires_manual_review(entry)
    ]
    excluded_ids = {entry.sample_id for entry in conversion_required} | {entry.sample_id for entry in needs_review} | holdout_ids
    pilot_main = [
        entry
        for entry in entries
        if entry.sample_id not in excluded_ids
        and entry.suggested_track == TRACK_PILOT_MAIN
    ]

    return {
        "holdout_eval": [_phase_b_entry(entry) for entry in holdout_eval],
        "pilot_main": [_phase_b_entry(entry) for entry in pilot_main],
        "needs_review": [_phase_b_entry(entry) for entry in needs_review],
        "ocr_asset_only": [_phase_b_entry(entry) for entry in conversion_required],
    }


def _recommend_holdout_eval(entries: list[SampleManifestEntry]) -> list[SampleManifestEntry]:
    mixed_candidates = [
        entry
        for entry in entries
        if entry.detected_profile == "mixed_engineering_pdf"
        and entry.ingestion_recommendation == "text_primary_with_asset_review"
    ]
    text_candidates = [
        entry
        for entry in entries
        if entry.detected_profile == "text_digital"
        and entry.ingestion_recommendation == "main_vector_ready"
    ]

    selected: list[SampleManifestEntry] = []
    if mixed_candidates:
        selected.append(max(mixed_candidates, key=_entry_weight))
    if text_candidates:
        selected.append(max(text_candidates, key=_entry_weight))
    return selected


def _requires_manual_review(entry: SampleManifestEntry) -> bool:
    if entry.ingestion_recommendation == "conversion_required":
        return False
    if entry.detected_profile != "mixed_engineering_pdf":
        return False
    markdown_chars = int(entry.metrics.get("markdown_char_count") or 0)
    table_count = int(entry.metrics.get("table_count") or 0)
    return markdown_chars < 12000 and table_count >= 12


def _entry_weight(entry: SampleManifestEntry) -> tuple[int, int]:
    metrics = entry.metrics or {}
    return (
        int(metrics.get("markdown_char_count") or 0),
        int(metrics.get("table_count") or 0) + int(metrics.get("image_count") or 0),
    )


def _phase_b_entry(entry: SampleManifestEntry) -> dict[str, Any]:
    return {
        "sample_id": entry.sample_id,
        "file_name": entry.file_name,
        "file_format": entry.file_format,
        "profile": entry.detected_profile,
        "ingestion_recommendation": entry.ingestion_recommendation,
    }


def _build_phase_b_track_index(phase_b_plan: dict[str, Any]) -> dict[str, str]:
    track_by_id: dict[str, str] = {}
    for track in (TRACK_HOLDOUT, TRACK_PILOT_MAIN, TRACK_NEEDS_REVIEW, TRACK_OCR_ASSET_ONLY):
        for entry in phase_b_plan.get(track) or []:
            sample_id = str(entry.get("sample_id") or "")
            if sample_id:
                track_by_id[sample_id] = track
    return track_by_id


def _entry_payload(entry: SampleManifestEntry, phase_b_track_by_id: dict[str, str]) -> dict[str, Any]:
    payload = entry.to_dict()
    payload["phase_b_track"] = phase_b_track_by_id.get(entry.sample_id)
    return payload
