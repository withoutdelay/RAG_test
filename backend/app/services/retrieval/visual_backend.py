from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from qdrant_client.models import PointStruct
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models.figure_asset import FigureAsset
from app.models.raw_document import RawDocument
from app.services.vectorstore.qdrant_client import QdrantSearchHit, QdrantService
from app.services.vectorstore.embedder import Embedder
from app.utils.object_storage import get_object_storage

try:  # pragma: no cover - optional runtime dependency
    import torch
except Exception:  # pragma: no cover - optional runtime dependency
    torch = None

try:  # pragma: no cover - optional runtime dependency
    from PIL import Image
except Exception:  # pragma: no cover - optional runtime dependency
    Image = None

try:  # pragma: no cover - optional runtime dependency
    from transformers import CLIPModel, CLIPProcessor
except Exception:  # pragma: no cover - optional runtime dependency
    CLIPModel = None
    CLIPProcessor = None


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


@dataclass(frozen=True)
class VisualEmbeddingCacheEntry:
    asset_id: str
    embedding: list[float]
    backend_name: str
    model_name: str
    visual_source: str
    asset_uri: str = ""
    raw_document_id: str = ""
    project_id: str = ""
    generated_at: str = ""
    metadata: dict[str, Any] | None = None


VISUAL_INDEX_CHANNELS = ("image", "text_proxy")
MIN_REUSABLE_FIGURE_DIMENSION = 80
MIN_REUSABLE_FIGURE_AREA = 12000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_visual_channel(visual_source: str | None) -> str:
    normalized = str(visual_source or "").strip().lower()
    if normalized.endswith("_cache"):
        normalized = normalized[: -len("_cache")]
    if normalized.endswith("_index"):
        normalized = normalized[: -len("_index")]
    return normalized if normalized in VISUAL_INDEX_CHANNELS else "text_proxy"


def build_visual_qdrant_collection_name(*, channel: str, collection_prefix: str | None = None) -> str:
    settings = get_settings()
    prefix = str(collection_prefix or settings.visual_qdrant_collection_prefix).strip() or settings.visual_qdrant_collection_prefix
    normalized_channel = normalize_visual_channel(channel)
    return f"{prefix}_{normalized_channel}"


def load_visual_embedding_cache(cache_path: Path | str) -> tuple[dict[str, VisualEmbeddingCacheEntry], dict[str, Any]]:
    path = Path(cache_path)
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}, {}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    cache: dict[str, VisualEmbeddingCacheEntry] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        embedding = item.get("embedding") if isinstance(item.get("embedding"), list) else []
        if not asset_id or not embedding:
            continue
        cache[asset_id] = VisualEmbeddingCacheEntry(
            asset_id=asset_id,
            embedding=[float(value) for value in embedding],
            backend_name=str(item.get("backend_name") or payload.get("backend_name") or "").strip(),
            model_name=str(item.get("model_name") or payload.get("model_name") or "").strip(),
            visual_source=str(item.get("visual_source") or "image").strip() or "image",
            asset_uri=str(item.get("asset_uri") or "").strip(),
            raw_document_id=str(item.get("raw_document_id") or "").strip(),
            project_id=str(item.get("project_id") or "").strip(),
            generated_at=str(item.get("generated_at") or payload.get("generated_at") or "").strip(),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
        )
    return cache, payload


def build_visual_embedding_cache_payload(
    *,
    backend_name: str,
    model_name: str,
    entries: list[VisualEmbeddingCacheEntry],
    generated_at: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "generated_at": generated_at or _utc_now_iso(),
        "backend_name": backend_name,
        "model_name": model_name,
        "entry_count": len(entries),
        "entries": [
            {
                "asset_id": entry.asset_id,
                "embedding": entry.embedding,
                "backend_name": entry.backend_name,
                "model_name": entry.model_name,
                "visual_source": entry.visual_source,
                "asset_uri": entry.asset_uri,
                "raw_document_id": entry.raw_document_id,
                "project_id": entry.project_id,
                "generated_at": entry.generated_at,
                "metadata": entry.metadata or {},
            }
            for entry in entries
        ],
    }
    if extra_metadata:
        payload["metadata"] = dict(extra_metadata)
    return payload


def sync_visual_embedding_cache_payload_to_qdrant(
    *,
    payload: dict[str, Any],
    collection_prefix: str | None = None,
) -> dict[str, Any]:
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    grouped_entries: dict[str, list[dict[str, Any]]] = {channel: [] for channel in VISUAL_INDEX_CHANNELS}
    for item in entries:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        embedding = item.get("embedding") if isinstance(item.get("embedding"), list) else []
        if not asset_id or not embedding:
            continue
        channel = normalize_visual_channel(item.get("visual_source"))
        grouped_entries[channel].append(item)

    collections: dict[str, str] = {}
    source_points: dict[str, int] = {}
    indexed_point_count = 0
    for channel, channel_entries in grouped_entries.items():
        if not channel_entries:
            continue
        collection_name = build_visual_qdrant_collection_name(
            channel=channel,
            collection_prefix=collection_prefix,
        )
        vector_size = len(channel_entries[0].get("embedding") or [])
        if vector_size <= 0:
            continue
        qdrant = QdrantService(collection_name=collection_name, dimension=vector_size)
        qdrant.recreate_collection()
        qdrant.upsert_points(
            points=[
                PointStruct(
                    id=str(item["asset_id"]),
                    vector=[float(value) for value in (item.get("embedding") or [])],
                    payload={
                        "asset_id": str(item.get("asset_id") or "").strip(),
                        "asset_uri": str(item.get("asset_uri") or "").strip(),
                        "project_id": str(item.get("project_id") or "").strip() or None,
                        "raw_document_id": str(item.get("raw_document_id") or "").strip() or None,
                        "visual_source": str(item.get("visual_source") or "").strip(),
                        "visual_channel": channel,
                        **(
                            item.get("metadata")
                            if isinstance(item.get("metadata"), dict)
                            else {}
                        ),
                    },
                )
                for item in channel_entries
            ]
        )
        collections[channel] = collection_name
        source_points[channel] = len(channel_entries)
        indexed_point_count += len(channel_entries)

    return {
        "visual_qdrant_sync_status": "succeeded",
        "visual_qdrant_collection_prefix": (
            str(collection_prefix or get_settings().visual_qdrant_collection_prefix).strip()
            or get_settings().visual_qdrant_collection_prefix
        ),
        "visual_qdrant_collections": collections,
        "visual_qdrant_source_points": source_points,
        "visual_qdrant_indexed_points": indexed_point_count,
    }


def search_visual_embedding_index(
    *,
    query_vector: list[float],
    channel: str,
    top_k: int,
    collection_prefix: str | None = None,
) -> list[QdrantSearchHit]:
    if not query_vector or top_k <= 0:
        return []
    collection_name = build_visual_qdrant_collection_name(
        channel=channel,
        collection_prefix=collection_prefix,
    )
    qdrant = QdrantService(collection_name=collection_name, dimension=len(query_vector))
    try:
        return qdrant.search(
            query_vector=query_vector,
            top_k=top_k,
        )
    except Exception:
        return []


def build_visual_asset_fallback_text(
    *,
    title: str | None,
    caption: str | None,
    metadata: dict[str, Any] | None,
) -> str:
    normalized_metadata = dict(metadata or {})
    summary = normalized_metadata.get("semantic_summary") if isinstance(normalized_metadata.get("semantic_summary"), dict) else {}
    parts: list[str] = [
        str(title or "").strip(),
        str(caption or "").strip(),
        str(normalized_metadata.get("heading_path") or "").strip(),
        str(normalized_metadata.get("context_before") or "").strip(),
        str(normalized_metadata.get("context_after") or "").strip(),
    ]
    for key in ("title_hint", "diagram_type", "summary", "problem_solved", "principle_summary"):
        value = str(summary.get(key) or "").strip()
        if value:
            parts.append(value)
    for key in ("key_components", "retrieval_keywords", "applicable_sections"):
        values = summary.get(key) if isinstance(summary.get(key), list) else []
        for item in values:
            text = str(item).strip()
            if text:
                parts.append(text)
    return "\n".join(item for item in parts if item)


async def collect_visual_cache_asset_rows(
    *,
    project_id: str = "",
    asset_types: list[str] | None = None,
    raw_document_doc_type: str | None = None,
    raw_document_parse_status: str | None = "done",
    limit: int = 0,
) -> list[tuple[FigureAsset, RawDocument]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = (
            select(FigureAsset, RawDocument)
            .join(RawDocument, FigureAsset.raw_document_id == RawDocument.id)
            .order_by(FigureAsset.created_at.asc())
        )
        if project_id:
            stmt = stmt.where(RawDocument.project_id == UUID(project_id))
        normalized_asset_types = [str(item).strip() for item in (asset_types or []) if str(item).strip()]
        if normalized_asset_types:
            stmt = stmt.where(FigureAsset.asset_type.in_(normalized_asset_types))
        normalized_doc_type = str(raw_document_doc_type or "").strip()
        if normalized_doc_type:
            stmt = stmt.where(RawDocument.doc_type == normalized_doc_type)
        normalized_parse_status = str(raw_document_parse_status or "").strip()
        if normalized_parse_status:
            stmt = stmt.where(RawDocument.parse_status == normalized_parse_status)
        if limit > 0:
            stmt = stmt.limit(limit)
        rows = await session.execute(stmt)
        return [
            (asset, raw_document)
            for asset, raw_document in rows.all()
            if _should_include_visual_cache_asset(asset)
        ]


def _should_include_visual_cache_asset(asset: FigureAsset) -> bool:
    if asset.asset_type != "figure":
        return True
    metadata = asset.meta if isinstance(asset.meta, dict) else {}
    if str(metadata.get("asset_audit_status") or "").strip().lower() == "rejected":
        return False
    if bool(metadata.get("storage_fallback")):
        return False
    if metadata.get("preserve_in_vector_db") is False:
        return False
    visual_role = str(metadata.get("visual_role") or "").strip().lower()
    if visual_role in {"page_furniture", "asset_fragment", "text_fragment"}:
        return False
    raw_width = metadata.get("width") or metadata.get("image_width") or metadata.get("pixel_width")
    raw_height = metadata.get("height") or metadata.get("image_height") or metadata.get("pixel_height")
    try:
        image_width = int(raw_width or 0)
        image_height = int(raw_height or 0)
    except (TypeError, ValueError):
        return True
    if image_width <= 0 or image_height <= 0:
        return True
    area = image_width * image_height
    if min(image_width, image_height) < MIN_REUSABLE_FIGURE_DIMENSION:
        return False
    return not (area < MIN_REUSABLE_FIGURE_AREA and max(image_width, image_height) < MIN_REUSABLE_FIGURE_DIMENSION * 2)


async def build_visual_embedding_cache_snapshot(
    *,
    rows: list[tuple[FigureAsset, RawDocument]],
    visual_embedder: "VisualEmbedder",
    project_id: str = "",
    asset_types: list[str] | None = None,
    limit: int = 0,
    backend_mode: str | None = None,
    include_proxy_fallback: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    entries: list[VisualEmbeddingCacheEntry] = []
    skipped_proxy = 0
    failed_assets: list[str] = []
    source_breakdown: dict[str, int] = {}

    for asset, raw_document in rows:
        fallback_text = build_visual_asset_fallback_text(
            title=asset.title,
            caption=asset.caption,
            metadata=asset.meta if isinstance(asset.meta, dict) else {},
        )
        try:
            embedding, visual_source = await visual_embedder.embed_asset(
                asset_id=str(asset.id),
                asset_uri=asset.asset_uri,
                fallback_text=fallback_text,
            )
        except Exception as exc:  # noqa: BLE001
            failed_assets.append(f"{asset.id} ({exc})")
            continue
        if visual_source == "text_proxy" and not include_proxy_fallback:
            skipped_proxy += 1
            continue
        source_breakdown[visual_source] = int(source_breakdown.get(visual_source, 0)) + 1
        entries.append(
            VisualEmbeddingCacheEntry(
                asset_id=str(asset.id),
                embedding=embedding,
                backend_name=visual_embedder.backend_name,
                model_name=visual_embedder.model_name,
                visual_source=visual_source,
                asset_uri=asset.asset_uri,
                raw_document_id=str(raw_document.id),
                project_id=str(raw_document.project_id or ""),
                metadata={
                    "asset_type": asset.asset_type,
                    "file_name": raw_document.file_name,
                    "page_no": asset.page_no,
                    "title": str(asset.title or "").strip(),
                    "caption": str(asset.caption or "").strip(),
                },
            )
        )

    normalized_asset_types = [str(item).strip() for item in (asset_types or []) if str(item).strip()]
    payload = build_visual_embedding_cache_payload(
        backend_name=visual_embedder.backend_name,
        model_name=visual_embedder.model_name,
        entries=entries,
        extra_metadata={
            "project_id": str(project_id or "").strip(),
            "asset_types": normalized_asset_types,
            "limit": int(limit or 0),
            "include_proxy_fallback": bool(include_proxy_fallback),
            "backend_mode": str(backend_mode or visual_embedder.backend_mode or "").strip(),
            "source_breakdown": source_breakdown,
            "failed_asset_count": len(failed_assets),
        },
    )
    stats = {
        "visual_cache_backend": visual_embedder.backend_name,
        "visual_cache_model": visual_embedder.model_name,
        "visual_cache_entry_count": len(entries),
        "visual_cache_total_assets": len(rows),
        "visual_cache_failed_assets": len(failed_assets),
        "visual_cache_failed_asset_samples": failed_assets[:10],
        "visual_cache_skipped_proxy_fallback": skipped_proxy,
        "visual_cache_source_breakdown": source_breakdown,
        "visual_cache_true_visual_enabled": bool(visual_embedder.is_true_visual),
    }
    return payload, stats


async def rebuild_visual_embedding_cache(
    *,
    output_path: Path | str | None = None,
    project_id: str = "",
    asset_types: list[str] | None = None,
    raw_document_doc_type: str | None = None,
    raw_document_parse_status: str | None = "done",
    backend_mode: str | None = None,
    model_name: str | None = None,
    device: str | None = None,
    limit: int = 0,
    include_proxy_fallback: bool = False,
    sync_to_qdrant: bool = True,
    collection_prefix: str | None = None,
) -> dict[str, Any]:
    if output_path is None:
        if get_settings is None:
            raise RuntimeError("visual embedding cache path is not configured")
        output_path = get_settings().visual_embedding_cache_path
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    visual_embedder = VisualEmbedder(
        backend_mode=str(backend_mode or "auto"),
        model_name=str(model_name or "").strip() or None,
        device=str(device or "").strip() or None,
        cache_path=target_path,
        load_cache=False,
    )
    rows = await collect_visual_cache_asset_rows(
        project_id=str(project_id or "").strip(),
        asset_types=[str(item) for item in (asset_types or [])],
        raw_document_doc_type=raw_document_doc_type,
        raw_document_parse_status=raw_document_parse_status,
        limit=int(limit or 0),
    )
    payload, stats = await build_visual_embedding_cache_snapshot(
        rows=rows,
        visual_embedder=visual_embedder,
        project_id=str(project_id or "").strip(),
        asset_types=[str(item) for item in (asset_types or [])],
        limit=int(limit or 0),
        backend_mode=backend_mode,
        include_proxy_fallback=include_proxy_fallback,
    )
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    qdrant_stats: dict[str, Any] = {
        "visual_qdrant_sync_status": "skipped",
        "visual_qdrant_collection_prefix": (
            str(collection_prefix or get_settings().visual_qdrant_collection_prefix).strip()
            or get_settings().visual_qdrant_collection_prefix
        ),
        "visual_qdrant_collections": {},
        "visual_qdrant_source_points": {},
        "visual_qdrant_indexed_points": 0,
        "visual_qdrant_sync_error": None,
    }
    if sync_to_qdrant:
        try:
            qdrant_stats = {
                **qdrant_stats,
                **sync_visual_embedding_cache_payload_to_qdrant(
                    payload=payload,
                    collection_prefix=collection_prefix,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            qdrant_stats["visual_qdrant_sync_status"] = "failed"
            qdrant_stats["visual_qdrant_sync_error"] = str(exc)
    return {
        **stats,
        **qdrant_stats,
        "visual_cache_output_path": str(target_path.resolve()),
        "visual_cache_status": "succeeded",
    }


class _TextProxyVisualBackend:
    backend_name = "text-proxy"
    is_true_visual = False

    def __init__(self, *, text_embedder: Embedder) -> None:
        self.text_embedder = text_embedder

    async def embed_query(self, text: str) -> list[float]:
        return await self.text_embedder.embed_text(text)

    async def embed_asset(self, *, asset_uri: str, fallback_text: str) -> list[float]:
        del asset_uri
        return await self.text_embedder.embed_text(fallback_text)


class _ClipVisualBackend:
    backend_name = "clip"
    is_true_visual = True

    def __init__(
        self,
        *,
        model_name: str,
        local_files_only: bool,
        device: str,
    ) -> None:
        if torch is None or Image is None or CLIPModel is None or CLIPProcessor is None:
            raise RuntimeError("CLIP visual backend requested but torch/transformers/Pillow is not installed")
        self.device = self._resolve_device(device)
        self.model = CLIPModel.from_pretrained(model_name, local_files_only=local_files_only).to(self.device)
        self.model.eval()
        self.processor = CLIPProcessor.from_pretrained(model_name, local_files_only=local_files_only)

    def _resolve_device(self, requested: str) -> str:
        normalized = str(requested or "cpu").strip().lower()
        if normalized == "auto":
            return "cuda" if torch is not None and torch.cuda.is_available() else "cpu"
        if normalized == "cuda" and not (torch is not None and torch.cuda.is_available()):
            return "cpu"
        return normalized or "cpu"

    async def embed_query(self, text: str) -> list[float]:
        inputs = self.processor(text=[text], return_tensors="pt", padding=True, truncation=True)
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.no_grad():
            features = self.model.get_text_features(**inputs)
        return _normalize_vector([float(value) for value in features[0].detach().cpu().tolist()])

    async def embed_asset(self, *, asset_uri: str, fallback_text: str) -> list[float]:
        del fallback_text
        storage = get_object_storage()
        materialized = storage.materialize(asset_uri)
        try:
            image = Image.open(materialized.path).convert("RGB")
            inputs = self.processor(images=image, return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.no_grad():
                features = self.model.get_image_features(**inputs)
            return _normalize_vector([float(value) for value in features[0].detach().cpu().tolist()])
        finally:
            materialized.cleanup()


class VisualEmbedder:
    def __init__(
        self,
        *,
        text_embedder: Embedder | None = None,
        backend_mode: str | None = None,
        model_name: str | None = None,
        local_files_only: bool | None = None,
        device: str | None = None,
        cache_path: Path | str | None = None,
        load_cache: bool = True,
        true_visual_backend: Any | None = None,
    ) -> None:
        self.text_embedder = text_embedder or Embedder()
        self.proxy_backend = _TextProxyVisualBackend(text_embedder=self.text_embedder)

        if get_settings is not None:
            settings = get_settings()
            configured_mode = settings.visual_embedding_backend
            configured_model_name = settings.visual_embedding_model
            configured_local_files_only = settings.visual_embedding_local_files_only
            configured_device = settings.visual_embedding_device
            configured_cache_path = settings.visual_embedding_cache_path
        else:
            configured_mode = os.getenv("VISUAL_EMBEDDING_BACKEND", "proxy")
            configured_model_name = os.getenv("VISUAL_EMBEDDING_MODEL", "openai/clip-vit-base-patch32")
            configured_local_files_only = os.getenv("VISUAL_EMBEDDING_LOCAL_FILES_ONLY", "true").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            configured_device = os.getenv("VISUAL_EMBEDDING_DEVICE", "cpu")
            configured_cache_path = os.getenv("VISUAL_EMBEDDING_CACHE_PATH", "")

        self.backend_mode = str(backend_mode or configured_mode or "proxy").strip().lower()
        self.model_name = str(model_name or configured_model_name or "openai/clip-vit-base-patch32").strip()
        self.local_files_only = (
            bool(configured_local_files_only)
            if local_files_only is None
            else bool(local_files_only)
        )
        self.device = str(device or configured_device or "cpu").strip()
        self.cache_path = Path(cache_path) if cache_path is not None else Path(str(configured_cache_path)) if str(configured_cache_path or "").strip() else None
        self._true_visual_backend = None
        self._cache_entries: dict[str, VisualEmbeddingCacheEntry] = {}
        self._cache_payload: dict[str, Any] = {}

        if load_cache and self.cache_path is not None:
            self._cache_entries, self._cache_payload = load_visual_embedding_cache(self.cache_path)

        if self.backend_mode == "proxy":
            self.backend_name = self.proxy_backend.backend_name
            self.is_true_visual = False
            return

        strict = self.backend_mode == "clip"
        self._true_visual_backend = true_visual_backend or self._build_true_visual_backend(strict=strict)
        if self._true_visual_backend is None:
            self.backend_name = self.proxy_backend.backend_name
            self.is_true_visual = False
        else:
            self.backend_name = str(getattr(self._true_visual_backend, "backend_name", "visual-backend"))
            self.is_true_visual = bool(getattr(self._true_visual_backend, "is_true_visual", True))

    def _build_true_visual_backend(self, *, strict: bool) -> Any | None:
        try:
            return _ClipVisualBackend(
                model_name=self.model_name,
                local_files_only=self.local_files_only,
                device=self.device,
            )
        except Exception:
            if strict:
                raise
            return None

    async def build_query_vectors(self, text: str) -> dict[str, list[float]]:
        vectors = {"text_proxy": await self.proxy_backend.embed_query(text)}
        if self._true_visual_backend is not None:
            vectors["image"] = await self._true_visual_backend.embed_query(text)
        return vectors

    def _get_cached_asset_embedding(self, *, asset_id: str) -> VisualEmbeddingCacheEntry | None:
        if not asset_id or not self._cache_entries or self._true_visual_backend is None:
            return None
        entry = self._cache_entries.get(asset_id)
        if entry is None:
            return None
        if entry.backend_name and entry.backend_name != self.backend_name:
            return None
        if entry.model_name and self.model_name and entry.model_name != self.model_name:
            return None
        return entry

    async def embed_asset(self, *, asset_uri: str, fallback_text: str, asset_id: str = "") -> tuple[list[float], str]:
        cached_entry = self._get_cached_asset_embedding(asset_id=asset_id)
        if cached_entry is not None:
            cached_source = str(cached_entry.visual_source or "image").strip().lower() or "image"
            if cached_source in {"image", "text_proxy"}:
                return list(cached_entry.embedding), f"{cached_source}_cache"
            return list(cached_entry.embedding), cached_source
        if self._true_visual_backend is None:
            return await self.proxy_backend.embed_asset(asset_uri=asset_uri, fallback_text=fallback_text), "text_proxy"
        try:
            return await self._true_visual_backend.embed_asset(asset_uri=asset_uri, fallback_text=fallback_text), "image"
        except Exception:
            if self.backend_mode == "auto":
                return await self.proxy_backend.embed_asset(asset_uri=asset_uri, fallback_text=fallback_text), "text_proxy"
            raise
