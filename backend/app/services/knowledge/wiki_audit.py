from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal

from app.config import BACKEND_ROOT
from app.services.knowledge.wiki_quality import (
    is_blocking_quality_flag,
    is_publishable_evidence_item,
    item_can_enter_generation,
)


WikiLayer = Literal["draft", "published", "rejected"]
WikiDecisionStatus = Literal["rejected", "quarantined"]

DEFAULT_KNOWLEDGE_WIKI_AUDIT_ROOT = BACKEND_ROOT / "data" / "knowledge_wiki"
WIKI_LAYERS: tuple[WikiLayer, ...] = ("draft", "published", "rejected")
STRUCTURED_ASSET_FILES = {
    "glossary": "glossary.json",
    "product_cards": "product_cards.json",
    "module_cards": "module_cards.json",
    "equipment_cards": "equipment_cards.json",
    "interface_cards": "interface_cards.json",
    "section_templates": "section_templates.json",
    "forbidden_phrases": "forbidden_phrases.json",
}
STRUCTURED_KEY_BY_ITEM_TYPE = {
    "term_alias": "glossary",
    "product_family": "product_cards",
    "section_template": "section_templates",
}


class WikiAuditError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class WikiAuditService:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_KNOWLEDGE_WIKI_AUDIT_ROOT

    def summary(self) -> dict[str, Any]:
        layers = {layer: self._layer_summary(layer) for layer in WIKI_LAYERS}
        draft_items = self._load_layer_items("draft")
        published_items = self._load_layer_items("published")
        rejected_items = self._load_layer_items("rejected")
        diff = self.diff()
        return {
            "root": str(self.root),
            "layers": layers,
            "draft_total": len(draft_items),
            "published_total": len(published_items),
            "rejected_total": len(rejected_items),
            "review_required_total": sum(
                1 for item in draft_items if _normalized_status(item) == "review_required"
            ),
            "quarantined_total": sum(1 for item in rejected_items if _normalized_status(item) == "quarantined"),
            "status_counts": _count_items_by_status([*draft_items, *published_items, *rejected_items]),
            "type_counts": _count_items_by_type([*draft_items, *published_items, *rejected_items]),
            "diff_counts": {
                "added": len(diff["added"]),
                "changed": len(diff["changed"]),
                "removed": len(diff["removed"]),
                "status_changed": len(diff["status_changed"]),
            },
            "recent_events": self._read_audit_log(limit=20),
        }

    def list_items(
        self,
        *,
        layer: str | None = None,
        status: str | None = None,
        item_type: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        selected_layers = self._selected_layers(layer=layer, status=status)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for selected_layer in selected_layers:
            for item in self._load_layer_items(selected_layer):
                item_id = str(item.get("item_id") or "")
                dedupe_key = f"{selected_layer}:{item_id}" if layer else item_id
                if not layer and dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                records.append(self._audit_record(item=item, layer=selected_layer))

        normalized_status = str(status or "").strip().lower()
        if normalized_status and normalized_status != "all":
            records = [
                record
                for record in records
                if str(record.get("status") or "").strip().lower() == normalized_status
                or str(record.get("layer") or "").strip().lower() == normalized_status
            ]

        normalized_type = str(item_type or "").strip()
        if normalized_type and normalized_type != "all":
            records = [record for record in records if record.get("item_type") == normalized_type]

        normalized_query = str(query or "").strip().casefold()
        if normalized_query:
            records = [
                record
                for record in records
                if normalized_query
                in " ".join(
                    [
                        str(record.get("item_id") or ""),
                        str(record.get("canonical_name") or ""),
                        str(record.get("summary") or ""),
                        " ".join(str(alias) for alias in record.get("aliases") or []),
                        " ".join(str(doc) for doc in record.get("source_documents") or []),
                    ]
                ).casefold()
            ]

        records.sort(key=lambda item: (str(item.get("layer") or ""), str(item.get("item_type") or ""), str(item.get("canonical_name") or "")))
        total = len(records)
        safe_offset = max(0, offset)
        safe_limit = max(1, min(500, limit))
        return {
            "items": records[safe_offset : safe_offset + safe_limit],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "filters": {
                "layer": layer,
                "status": status,
                "item_type": item_type,
                "query": query,
            },
        }

    def item_detail(self, item_id: str) -> dict[str, Any]:
        matches = self._find_all_items(item_id)
        if not matches:
            raise WikiAuditError("Wiki item not found", status_code=404)
        primary_layer, primary_item = matches[0]
        structured_assets = {
            layer: self._find_structured_asset(layer=layer, item=primary_item)
            for layer in WIKI_LAYERS
        }
        return {
            "item": self._audit_record(item=primary_item, layer=primary_layer),
            "layers": {layer: self._audit_record(item=item, layer=layer) for layer, item in matches},
            "structured_assets": structured_assets,
            "audit_events": self._read_item_events(item_id=item_id, limit=50),
        }

    def approve_item(self, item_id: str, *, actor: str, note: str | None = None) -> dict[str, Any]:
        layer, item = self._find_item_for_action(item_id=item_id)
        if not _item_has_publishable_evidence(item):
            raise WikiAuditError("Wiki item does not have complete publishable evidence", status_code=409)
        blocking_flags = [flag for flag in _item_quality_flags(item) if is_blocking_quality_flag(flag)]
        if blocking_flags:
            raise WikiAuditError(
                f"Wiki item has blocking quality flags: {', '.join(blocking_flags)}",
                status_code=409,
            )

        updated = {
            **item,
            "status": "published",
            "approved_by": actor,
            "approved_at": _utc_now_iso(),
            "published_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        if note:
            updated["approval_note"] = note

        self._upsert_layer_item("published", updated)
        if layer == "draft" or self._has_layer_item("draft", item_id):
            self._upsert_layer_item("draft", updated)
        self._remove_layer_item("rejected", item_id)
        self._upsert_structured_asset_for_item(source_layer="draft", target_layer="published", item=updated)
        self._append_audit_event(event="wiki_item_approved", item_id=item_id, actor=actor, note=note, item=updated)
        return self.item_detail(item_id)

    def reject_item(
        self,
        item_id: str,
        *,
        actor: str,
        reason: str | None = None,
        status: WikiDecisionStatus = "rejected",
    ) -> dict[str, Any]:
        _layer, item = self._find_item_for_action(item_id=item_id)
        updated = {
            **item,
            "status": status,
            "rejected_by": actor,
            "rejected_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        if reason:
            updated["rejected_reason"] = reason

        self._upsert_layer_item("rejected", updated)
        if self._has_layer_item("draft", item_id):
            self._upsert_layer_item("draft", updated)
        self._remove_layer_item("published", item_id)
        self._remove_structured_asset_for_item(layer="published", item=updated)
        self._append_audit_event(event=f"wiki_item_{status}", item_id=item_id, actor=actor, note=reason, item=updated)
        return self.item_detail(item_id)

    def edit_item(self, item_id: str, *, actor: str, updates: dict[str, Any]) -> dict[str, Any]:
        layer, item = self._find_item_for_action(item_id=item_id)
        clean_updates = self._clean_edit_updates(updates)
        if not clean_updates:
            raise WikiAuditError("No editable fields were provided", status_code=400)

        quality_flags = _dedupe_keep_order([*_item_quality_flags(item), "human_edited"])
        updated = {
            **item,
            **clean_updates,
            "quality_flags": quality_flags,
            "updated_by": actor,
            "updated_at": _utc_now_iso(),
        }

        self._upsert_layer_item(layer, updated)
        if layer != "draft" and self._has_layer_item("draft", item_id):
            self._upsert_layer_item("draft", updated)
        if layer != "published" and self._has_layer_item("published", item_id):
            self._upsert_layer_item("published", updated)
        if self._has_layer_item("published", item_id) or _normalized_status(updated) == "published":
            self._upsert_structured_asset_for_item(source_layer=layer, target_layer="published", item=updated)
        self._append_audit_event(
            event="wiki_item_edited",
            item_id=item_id,
            actor=actor,
            note=", ".join(sorted(clean_updates)),
            item=updated,
        )
        return self.item_detail(item_id)

    def merge_items(
        self,
        item_id: str,
        *,
        source_item_ids: list[str],
        actor: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        if not source_item_ids:
            raise WikiAuditError("source_item_ids is required", status_code=400)
        target_layer, target = self._find_item_for_action(item_id=item_id)
        merged_sources: list[dict[str, Any]] = []
        for source_item_id in source_item_ids:
            if source_item_id == item_id:
                raise WikiAuditError("source_item_ids cannot include the target item", status_code=400)
            _source_layer, source = self._find_item_for_action(item_id=source_item_id)
            if str(source.get("item_type") or "") != str(target.get("item_type") or ""):
                raise WikiAuditError("Only wiki items with the same item_type can be merged", status_code=409)
            merged_sources.append(source)

        merged_target = dict(target)
        merged_target["aliases"] = _dedupe_keep_order(
            [
                *[str(alias) for alias in target.get("aliases") or []],
                *[
                    str(alias)
                    for source in merged_sources
                    for alias in source.get("aliases") or []
                ],
            ]
        )
        merged_target["source_documents"] = _dedupe_keep_order(
            [
                *[str(doc) for doc in target.get("source_documents") or []],
                *[
                    str(doc)
                    for source in merged_sources
                    for doc in source.get("source_documents") or []
                ],
            ]
        )
        merged_target["evidence"] = _dedupe_evidence(
            [
                *[item for item in target.get("evidence") or [] if isinstance(item, dict)],
                *[
                    evidence
                    for source in merged_sources
                    for evidence in source.get("evidence") or []
                    if isinstance(evidence, dict)
                ],
            ]
        )
        merged_target["quality_flags"] = _dedupe_keep_order([*_item_quality_flags(target), "human_merged"])
        merged_target["merged_from"] = _dedupe_keep_order(
            [*[str(item) for item in target.get("merged_from") or []], *source_item_ids]
        )
        merged_target["merged_by"] = actor
        merged_target["merged_at"] = _utc_now_iso()
        merged_target["updated_at"] = _utc_now_iso()
        if note:
            merged_target["merge_note"] = note

        self._upsert_layer_item(target_layer, merged_target)
        if target_layer != "draft" and self._has_layer_item("draft", item_id):
            self._upsert_layer_item("draft", merged_target)
        if target_layer != "published" and self._has_layer_item("published", item_id):
            self._upsert_layer_item("published", merged_target)
            self._upsert_structured_asset_for_item(source_layer=target_layer, target_layer="published", item=merged_target)

        rejected_sources: list[dict[str, Any]] = []
        for source in merged_sources:
            source_item_id = str(source.get("item_id") or "")
            rejected_source = {
                **source,
                "status": "rejected",
                "merged_into": item_id,
                "rejected_reason": f"merged_into:{item_id}",
                "rejected_by": actor,
                "rejected_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
            rejected_sources.append(rejected_source)
            self._upsert_layer_item("rejected", rejected_source)
            if self._has_layer_item("draft", source_item_id):
                self._upsert_layer_item("draft", rejected_source)
            self._remove_layer_item("published", source_item_id)
            self._remove_structured_asset_for_item(layer="published", item=rejected_source)

        self._append_audit_event(
            event="wiki_items_merged",
            item_id=item_id,
            actor=actor,
            note=note,
            item=merged_target,
            metadata={"source_item_ids": source_item_ids},
        )
        return {
            **self.item_detail(item_id),
            "merged_sources": [self._audit_record(item=item, layer="rejected") for item in rejected_sources],
        }

    def diff(self) -> dict[str, Any]:
        draft_items = {str(item.get("item_id") or ""): item for item in self._load_layer_items("draft")}
        published_items = {str(item.get("item_id") or ""): item for item in self._load_layer_items("published")}
        draft_ids = {item_id for item_id in draft_items if item_id}
        published_ids = {item_id for item_id in published_items if item_id}

        added = [
            self._diff_record(item_id=item_id, draft_item=draft_items[item_id], published_item=None)
            for item_id in sorted(draft_ids - published_ids)
        ]
        removed = [
            self._diff_record(item_id=item_id, draft_item=None, published_item=published_items[item_id])
            for item_id in sorted(published_ids - draft_ids)
        ]
        changed: list[dict[str, Any]] = []
        status_changed: list[dict[str, Any]] = []
        for item_id in sorted(draft_ids & published_ids):
            draft_item = draft_items[item_id]
            published_item = published_items[item_id]
            field_changes = _changed_fields(draft_item=draft_item, published_item=published_item)
            if field_changes:
                changed.append(
                    self._diff_record(
                        item_id=item_id,
                        draft_item=draft_item,
                        published_item=published_item,
                        changed_fields=field_changes,
                    )
                )
            if _normalized_status(draft_item) != _normalized_status(published_item):
                status_changed.append(
                    self._diff_record(
                        item_id=item_id,
                        draft_item=draft_item,
                        published_item=published_item,
                        changed_fields=["status"],
                    )
                )
        return {
            "added": added,
            "changed": changed,
            "removed": removed,
            "status_changed": status_changed,
        }

    def _layer_summary(self, layer: WikiLayer) -> dict[str, Any]:
        items = self._load_layer_items(layer)
        manifest = self._load_layer_manifest(layer)
        return {
            "layer": layer,
            "path": str(self.root / layer),
            "available": (self.root / layer / "wiki_items.json").exists(),
            "item_count": len(items),
            "status_counts": _count_items_by_status(items),
            "type_counts": _count_items_by_type(items),
            "generated_at": manifest.get("generated_at"),
            "manifest": manifest,
        }

    def _selected_layers(self, *, layer: str | None, status: str | None) -> tuple[WikiLayer, ...]:
        normalized_layer = str(layer or "").strip().lower()
        if normalized_layer:
            if normalized_layer not in WIKI_LAYERS:
                raise WikiAuditError("Invalid wiki layer", status_code=400)
            return (normalized_layer,)  # type: ignore[return-value]
        normalized_status = str(status or "").strip().lower()
        if normalized_status == "published":
            return ("published",)
        if normalized_status in {"rejected", "quarantined"}:
            return ("rejected",)
        return WIKI_LAYERS

    def _audit_record(self, *, item: dict[str, Any], layer: WikiLayer) -> dict[str, Any]:
        evidence = [entry for entry in item.get("evidence") or [] if isinstance(entry, dict)]
        source_documents = [str(doc) for doc in item.get("source_documents") or [] if str(doc).strip()]
        first_evidence = evidence[0] if evidence else {}
        quality_flags = _item_quality_flags(item)
        return {
            **item,
            "layer": layer,
            "hit_count": len(evidence),
            "source_document_count": len(source_documents),
            "blocking_flag_count": sum(1 for flag in quality_flags if is_blocking_quality_flag(flag)),
            "generation_eligible": item_can_enter_generation(item),
            "evidence_preview": str(first_evidence.get("evidence_quote") or "")[:500] if first_evidence else "",
            "source_document": first_evidence.get("source_document") if first_evidence else None,
            "source_section_id": first_evidence.get("source_section_id") if first_evidence else None,
            "heading_path": first_evidence.get("heading_path") if first_evidence else None,
        }

    def _diff_record(
        self,
        *,
        item_id: str,
        draft_item: dict[str, Any] | None,
        published_item: dict[str, Any] | None,
        changed_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        item = draft_item or published_item or {}
        return {
            "item_id": item_id,
            "item_type": item.get("item_type"),
            "canonical_name": item.get("canonical_name"),
            "draft_status": draft_item.get("status") if draft_item else None,
            "published_status": published_item.get("status") if published_item else None,
            "draft_updated_at": draft_item.get("updated_at") if draft_item else None,
            "published_updated_at": published_item.get("updated_at") if published_item else None,
            "changed_fields": changed_fields or [],
            "draft_item": self._audit_record(item=draft_item, layer="draft") if draft_item else None,
            "published_item": self._audit_record(item=published_item, layer="published") if published_item else None,
        }

    def _find_item_for_action(self, *, item_id: str) -> tuple[WikiLayer, dict[str, Any]]:
        matches = self._find_all_items(item_id)
        if not matches:
            raise WikiAuditError("Wiki item not found", status_code=404)
        return matches[0]

    def _find_all_items(self, item_id: str) -> list[tuple[WikiLayer, dict[str, Any]]]:
        normalized_item_id = str(item_id or "").strip()
        matches: list[tuple[WikiLayer, dict[str, Any]]] = []
        for layer in WIKI_LAYERS:
            for item in self._load_layer_items(layer):
                if str(item.get("item_id") or "").strip() == normalized_item_id:
                    matches.append((layer, item))
        matches.sort(key=lambda match: WIKI_LAYERS.index(match[0]))
        return matches

    def _has_layer_item(self, layer: WikiLayer, item_id: str) -> bool:
        return any(str(item.get("item_id") or "") == item_id for item in self._load_layer_items(layer))

    def _load_layer_items(self, layer: WikiLayer) -> list[dict[str, Any]]:
        payload = _read_json(self.root / layer / "wiki_items.json", fallback=[])
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _load_layer_manifest(self, layer: WikiLayer) -> dict[str, Any]:
        payload = _read_json(self.root / layer / "manifest.json", fallback={})
        return payload if isinstance(payload, dict) else {}

    def _save_layer_items(self, layer: WikiLayer, items: list[dict[str, Any]]) -> None:
        layer_dir = self.root / layer
        layer_dir.mkdir(parents=True, exist_ok=True)
        _write_json(layer_dir / "wiki_items.json", sorted(items, key=lambda item: str(item.get("item_id") or "")))
        manifest = self._load_layer_manifest(layer)
        manifest["wiki_layer"] = layer
        manifest["wiki_item_counts"] = _count_items_by_status(items)
        manifest["wiki_item_type_counts"] = _count_items_by_type(items)
        manifest["updated_at"] = _utc_now_iso()
        _write_json(layer_dir / "manifest.json", manifest)

    def _upsert_layer_item(self, layer: WikiLayer, item: dict[str, Any]) -> None:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            raise WikiAuditError("Wiki item is missing item_id", status_code=400)
        items = [existing for existing in self._load_layer_items(layer) if str(existing.get("item_id") or "") != item_id]
        items.append(item)
        self._save_layer_items(layer, items)

    def _remove_layer_item(self, layer: WikiLayer, item_id: str) -> None:
        items = [item for item in self._load_layer_items(layer) if str(item.get("item_id") or "") != item_id]
        self._save_layer_items(layer, items)

    def _load_structured_assets(self, layer: WikiLayer) -> dict[str, list[dict[str, Any]]]:
        layer_dir = self.root / layer
        assets: dict[str, list[dict[str, Any]]] = {}
        for key, file_name in STRUCTURED_ASSET_FILES.items():
            payload = _read_json(layer_dir / file_name, fallback=[])
            assets[key] = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        return assets

    def _save_structured_asset_list(self, layer: WikiLayer, key: str, assets: list[dict[str, Any]]) -> None:
        if key not in STRUCTURED_ASSET_FILES:
            return
        layer_dir = self.root / layer
        layer_dir.mkdir(parents=True, exist_ok=True)
        _write_json(layer_dir / STRUCTURED_ASSET_FILES[key], assets)

    def _find_structured_asset(self, *, layer: WikiLayer, item: dict[str, Any]) -> dict[str, Any] | None:
        key = STRUCTURED_KEY_BY_ITEM_TYPE.get(str(item.get("item_type") or ""))
        if not key:
            return None
        for asset in self._load_structured_assets(layer).get(key, []):
            if _structured_asset_item_id(key=key, asset=asset) == str(item.get("item_id") or ""):
                return asset
        return None

    def _upsert_structured_asset_for_item(self, *, source_layer: WikiLayer, target_layer: WikiLayer, item: dict[str, Any]) -> None:
        key = STRUCTURED_KEY_BY_ITEM_TYPE.get(str(item.get("item_type") or ""))
        if not key:
            return
        source_asset = self._find_structured_asset(layer=source_layer, item=item) or self._find_structured_asset(layer=target_layer, item=item)
        if source_asset is None:
            source_asset = _minimal_structured_asset_for_item(item=item, key=key)
        updated_asset = _apply_item_edits_to_structured_asset(asset=source_asset, item=item, key=key)
        assets = [
            existing
            for existing in self._load_structured_assets(target_layer).get(key, [])
            if _structured_asset_item_id(key=key, asset=existing) != str(item.get("item_id") or "")
        ]
        assets.append(updated_asset)
        self._save_structured_asset_list(target_layer, key, sorted(assets, key=lambda asset: _structured_asset_item_id(key=key, asset=asset)))

    def _remove_structured_asset_for_item(self, *, layer: WikiLayer, item: dict[str, Any]) -> None:
        key = STRUCTURED_KEY_BY_ITEM_TYPE.get(str(item.get("item_type") or ""))
        if not key:
            return
        item_id = str(item.get("item_id") or "")
        assets = [
            asset
            for asset in self._load_structured_assets(layer).get(key, [])
            if _structured_asset_item_id(key=key, asset=asset) != item_id
        ]
        self._save_structured_asset_list(layer, key, assets)

    def _append_audit_event(
        self,
        *,
        event: str,
        item_id: str,
        actor: str,
        note: str | None,
        item: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_path = self.root / "audit_log.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "event": event,
            "created_at": _utc_now_iso(),
            "item_id": item_id,
            "item_type": item.get("item_type"),
            "canonical_name": item.get("canonical_name"),
            "status": item.get("status"),
            "actor": actor,
            "note": note,
            "metadata": metadata or {},
        }
        previous = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
        audit_path.write_text(previous + json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    def _read_audit_log(self, *, limit: int) -> list[dict[str, Any]]:
        audit_path = self.root / "audit_log.jsonl"
        if not audit_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events[-limit:][::-1]

    def _read_item_events(self, *, item_id: str, limit: int) -> list[dict[str, Any]]:
        return [event for event in self._read_audit_log(limit=1000) if event.get("item_id") == item_id][:limit]

    @staticmethod
    def _clean_edit_updates(updates: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        if "canonical_name" in updates:
            canonical_name = str(updates.get("canonical_name") or "").strip()
            if canonical_name:
                clean["canonical_name"] = canonical_name
        if "summary" in updates:
            summary = str(updates.get("summary") or "").strip()
            if summary:
                clean["summary"] = summary
        if "aliases" in updates:
            aliases = [
                str(alias).strip()
                for alias in (updates.get("aliases") or [])
                if str(alias).strip()
            ]
            clean["aliases"] = _dedupe_keep_order(aliases)
        return clean


def _read_json(path: Path, *, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "unknown").strip().lower() or "unknown"


def _item_quality_flags(item: dict[str, Any]) -> list[str]:
    return [str(flag) for flag in item.get("quality_flags") or [] if str(flag).strip()]


def _count_items_by_status(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = _normalized_status(item)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _count_items_by_type(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("item_type") or "unknown").strip() or "unknown"
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _changed_fields(*, draft_item: dict[str, Any], published_item: dict[str, Any]) -> list[str]:
    fields = [
        "canonical_name",
        "aliases",
        "summary",
        "source_documents",
        "quality_flags",
        "quality_score",
    ]
    changed: list[str] = []
    for field in fields:
        if _stable_json_value(draft_item.get(field)) != _stable_json_value(published_item.get(field)):
            changed.append(field)
    return changed


def _stable_json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _item_has_publishable_evidence(item: dict[str, Any]) -> bool:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    if not evidence:
        return False
    return all(is_publishable_evidence_item(evidence_item) for evidence_item in evidence)


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _dedupe_evidence(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = "|".join(
            str(item.get(field) or "")
            for field in ("sample_id", "raw_document_id", "source_section_id", "heading_path", "evidence_quote", "asset_id")
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _structured_asset_item_id(*, key: str, asset: dict[str, Any]) -> str:
    explicit_item_id = str(asset.get("item_id") or asset.get("governance_item_id") or "").strip()
    if explicit_item_id:
        return explicit_item_id
    if key == "glossary":
        return f"term_alias:{_item_slug(str(asset.get('display_primary_term') or asset.get('primary_term') or ''))}"
    if key == "product_cards":
        return f"product_family:{_slugify(str(asset.get('product_family') or asset.get('title') or ''))}"
    if key == "section_templates":
        return f"section_template:{_slugify(str(asset.get('section_type') or asset.get('title') or ''))}"
    return ""


def _minimal_structured_asset_for_item(*, item: dict[str, Any], key: str) -> dict[str, Any]:
    item_id = str(item.get("item_id") or "")
    canonical_name = str(item.get("canonical_name") or "")
    aliases = [str(alias) for alias in item.get("aliases") or []]
    if key == "glossary":
        return {
            "primary_term": canonical_name,
            "display_primary_term": canonical_name,
            "aliases": aliases,
            "display_aliases": aliases,
            "governance_item_id": item_id,
        }
    if key == "product_cards":
        return {
            "product_family": item_id.split(":", 1)[-1].replace("-", "_"),
            "title": canonical_name,
            "aliases": aliases,
            "source_documents": list(item.get("source_documents") or []),
            "representative_snippets": [evidence.get("evidence_quote") for evidence in item.get("evidence") or [] if isinstance(evidence, dict) and evidence.get("evidence_quote")],
            "governance_item_id": item_id,
        }
    if key == "section_templates":
        return {
            "section_type": item_id.split(":", 1)[-1].replace("-", "_"),
            "title": canonical_name,
            "source_documents": list(item.get("source_documents") or []),
            "guidance": [str(item.get("summary") or "")],
            "governance_item_id": item_id,
        }
    return {"governance_item_id": item_id}


def _apply_item_edits_to_structured_asset(*, asset: dict[str, Any], item: dict[str, Any], key: str) -> dict[str, Any]:
    updated = {**asset, "governance_item_id": str(item.get("item_id") or "")}
    aliases = [str(alias) for alias in item.get("aliases") or [] if str(alias).strip()]
    if key == "glossary":
        updated["display_primary_term"] = str(item.get("canonical_name") or updated.get("display_primary_term") or "")
        updated["display_aliases"] = aliases
    elif key == "product_cards":
        updated["title"] = str(item.get("canonical_name") or updated.get("title") or "")
        updated["aliases"] = aliases
        updated["source_documents"] = list(item.get("source_documents") or updated.get("source_documents") or [])
    elif key == "section_templates":
        updated["title"] = str(item.get("canonical_name") or updated.get("title") or "")
        updated["source_documents"] = list(item.get("source_documents") or updated.get("source_documents") or [])
    return updated


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
