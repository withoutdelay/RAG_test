from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from app.services.knowledge.wiki_quality import (
    can_publish_wiki_item,
    is_publishable_evidence_item,
    item_has_blocking_quality_flags,
)


STRUCTURED_ASSET_FILES = {
    "glossary": "glossary.json",
    "product_cards": "product_cards.json",
    "module_cards": "module_cards.json",
    "equipment_cards": "equipment_cards.json",
    "interface_cards": "interface_cards.json",
    "section_templates": "section_templates.json",
    "forbidden_phrases": "forbidden_phrases.json",
}


def write_knowledge_wiki_bundle(*, output_dir: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    published_dir = output_dir / "published"
    draft_dir = output_dir / "draft"
    rejected_dir = output_dir / "rejected"
    for directory in (published_dir, draft_dir, rejected_dir):
        directory.mkdir(parents=True, exist_ok=True)

    draft_items = [item for item in (bundle.get("wiki_items") or []) if isinstance(item, dict)]
    published_items = [_published_item(item) for item in draft_items if _can_publish_item(item)]
    rejected_items = [
        item
        for item in draft_items
        if str(item.get("status") or "").strip().lower() in {"rejected", "quarantined"}
    ]

    draft_bundle = _bundle_with_items(bundle=bundle, items=draft_items, layer="draft")
    published_bundle = _bundle_with_items(
        bundle=_filter_bundle_for_published_items(bundle=bundle, published_items=published_items),
        items=published_items,
        layer="published",
    )
    rejected_bundle = _bundle_with_items(bundle=bundle, items=rejected_items, layer="rejected", structured_assets={}, pages={})

    _write_layer_bundle(output_dir=draft_dir, bundle=draft_bundle, write_pages=True)
    _write_layer_bundle(output_dir=published_dir, bundle=published_bundle, write_pages=True)
    _write_layer_bundle(output_dir=rejected_dir, bundle=rejected_bundle, write_pages=False)
    _append_audit_log(
        output_dir=output_dir,
        draft_items=draft_items,
        published_items=published_items,
        rejected_items=rejected_items,
    )
    return {
        "draft_items": len(draft_items),
        "published_items": len(published_items),
        "rejected_items": len(rejected_items),
        "published_dir": str(published_dir),
        "draft_dir": str(draft_dir),
        "rejected_dir": str(rejected_dir),
    }


def _write_layer_bundle(*, output_dir: Path, bundle: dict[str, Any], write_pages: bool) -> None:
    if write_pages:
        _prune_stale_wiki_pages(output_dir=output_dir, active_page_paths=set(bundle.get("pages") or {}))
        for relative_path, content in (bundle.get("pages") or {}).items():
            target_path = output_dir / str(relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(str(content), encoding="utf-8")

    (output_dir / "manifest.json").write_text(
        json.dumps(bundle.get("manifest") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "wiki_items.json").write_text(
        json.dumps(bundle.get("wiki_items") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    structured_assets = bundle.get("structured_assets") if isinstance(bundle.get("structured_assets"), dict) else {}
    for key, file_name in STRUCTURED_ASSET_FILES.items():
        (output_dir / file_name).write_text(
            json.dumps(structured_assets.get(key) or [], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if write_pages:
        log_path = output_dir / "log.md"
        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else "# AI Wiki Log\n\n"
        if not previous.endswith("\n"):
            previous += "\n"
        log_path.write_text(previous + "\n" + str(bundle.get("log_entry") or ""), encoding="utf-8")


def _bundle_with_items(
    *,
    bundle: dict[str, Any],
    items: list[dict[str, Any]],
    layer: str,
    structured_assets: dict[str, Any] | None = None,
    pages: dict[str, str] | None = None,
) -> dict[str, Any]:
    manifest = dict(bundle.get("manifest") or {})
    manifest["wiki_layer"] = layer
    manifest["wiki_item_counts"] = _count_items_by_status(items)
    return {
        **bundle,
        "manifest": manifest,
        "structured_assets": structured_assets if structured_assets is not None else dict(bundle.get("structured_assets") or {}),
        "pages": pages if pages is not None else dict(bundle.get("pages") or {}),
        "wiki_items": items,
    }


def _filter_bundle_for_published_items(*, bundle: dict[str, Any], published_items: list[dict[str, Any]]) -> dict[str, Any]:
    published_ids = {str(item.get("item_id") or "") for item in published_items}
    structured_assets = dict(bundle.get("structured_assets") or {})
    filtered_assets = {
        **structured_assets,
        "glossary": [
            item
            for item in (structured_assets.get("glossary") or [])
            if f"term_alias:{_item_slug(str(item.get('display_primary_term') or item.get('primary_term') or ''))}" in published_ids
        ],
        "product_cards": [
            item
            for item in (structured_assets.get("product_cards") or [])
            if f"product_family:{_slugify(str(item.get('product_family') or ''))}" in published_ids
        ],
        "section_templates": [
            item
            for item in (structured_assets.get("section_templates") or [])
            if f"section_template:{_slugify(str(item.get('section_type') or ''))}" in published_ids
        ],
    }
    return {
        **bundle,
        "structured_assets": filtered_assets,
        "pages": _filter_pages_for_published_items(pages=dict(bundle.get("pages") or {}), published_ids=published_ids),
    }


def _filter_pages_for_published_items(*, pages: dict[str, str], published_ids: set[str]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    always_keep = {"index.md", "glossary.md", "policies/forbidden-phrases.md"}
    for path, content in pages.items():
        normalized_path = str(path)
        if normalized_path in always_keep:
            filtered[normalized_path] = content
            continue
        if normalized_path.startswith("products/"):
            item_id = f"product_family:{_slugify(Path(normalized_path).stem)}"
            if item_id in published_ids:
                filtered[normalized_path] = content
            continue
        if normalized_path.startswith("templates/"):
            item_id = f"section_template:{_slugify(Path(normalized_path).stem)}"
            if item_id in published_ids:
                filtered[normalized_path] = content
            continue
        if normalized_path.startswith(("modules/", "equipment/", "interfaces/")):
            filtered[normalized_path] = content
    return filtered


def _can_publish_item(item: dict[str, Any]) -> bool:
    return can_publish_wiki_item(item)


def _published_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "status": "published",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


def _item_has_publishable_evidence(item: dict[str, Any]) -> bool:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    if not evidence:
        return False
    return all(is_publishable_evidence_item(evidence_item) for evidence_item in evidence)


def _count_items_by_status(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown").strip().lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _append_audit_log(
    *,
    output_dir: Path,
    draft_items: list[dict[str, Any]],
    published_items: list[dict[str, Any]],
    rejected_items: list[dict[str, Any]],
) -> None:
    audit_path = output_dir / "audit_log.jsonl"
    entry = {
        "event": "wiki_compile",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "draft_items": len(draft_items),
        "published_items": len(published_items),
        "rejected_items": len(rejected_items),
        "review_required_items": sum(1 for item in draft_items if str(item.get("status") or "") == "review_required"),
        "blocking_flag_items": sum(1 for item in draft_items if item_has_blocking_quality_flags(item)),
    }
    previous = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    audit_path.write_text(previous + json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


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


def _slugify(text: str) -> str:
    normalized = str(text or "").strip().lower().replace("_", "-")
    normalized = "".join(char if char.isalnum() else "-" for char in normalized)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized or "item"


def _item_slug(text: str) -> str:
    normalized = str(text or "").strip()
    slug = _slugify(normalized)
    if normalized.isascii() and slug != "item":
        return slug
    if not normalized:
        return slug
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
