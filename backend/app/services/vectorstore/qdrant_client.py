from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, FieldCondition, Filter, MatchAny, MatchValue, PointIdsList, PointStruct, Range, VectorParams

from app.config import get_settings

_CLIENT_CACHE: dict[tuple[str | None, str | None, int], QdrantClient] = {}


@dataclass
class QdrantSearchHit:
    id: str
    score: float
    payload: dict


class QdrantService:
    def __init__(self, *, collection_name: str | None = None, dimension: int | None = None) -> None:
        settings = get_settings()
        self.collection_name = str(collection_name or settings.qdrant_collection).strip() or settings.qdrant_collection
        self.dimension = int(dimension or settings.embedding_dimension)
        location = settings.qdrant_location
        host = settings.qdrant_host
        port = settings.qdrant_port
        cache_key = (location, host, port)

        if cache_key not in _CLIENT_CACHE:
            if location:
                _CLIENT_CACHE[cache_key] = QdrantClient(location=location)
            elif host in {":memory:", "memory"}:
                _CLIENT_CACHE[cache_key] = QdrantClient(location=":memory:")
            else:
                _CLIENT_CACHE[cache_key] = QdrantClient(host=host, port=port)

        self.client = _CLIENT_CACHE[cache_key]

    def ensure_collection(self) -> None:
        try:
            collection = self.client.get_collection(self.collection_name)
            current_vectors = getattr(getattr(collection, "config", None), "params", None)
            current_vector_params = getattr(current_vectors, "vectors", None)
            current_size = getattr(current_vector_params, "size", None)
            if current_size and int(current_size) != self.dimension:
                self.client.delete_collection(self.collection_name)
                raise ValueError("collection dimension mismatch")
        except (UnexpectedResponse, ValueError):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )

    def recreate_collection(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
        )

    def upsert_chunk(self, *, point_id: uuid.UUID, vector: list[float], payload: dict) -> None:
        self.ensure_collection()
        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=str(point_id), vector=vector, payload=payload)],
        )

    def upsert_points(self, *, points: list[PointStruct]) -> None:
        if not points:
            return
        self.ensure_collection()
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

    def delete_points(self, point_ids: list[str]) -> None:
        if not point_ids:
            return
        self.ensure_collection()
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(points=point_ids),
        )

    def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[QdrantSearchHit]:
        self.ensure_collection()
        qdrant_filter = self._build_filter(filters or {})
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
        )
        hits = response.points
        return [
            QdrantSearchHit(
                id=str(hit.id),
                score=float(hit.score),
                payload=hit.payload or {},
            )
            for hit in hits
        ]

    def _build_filter(self, filters: dict) -> Filter | None:
        must: list[FieldCondition] = []
        if project_id := filters.get("project_id"):
            must.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
        if industry := filters.get("industry"):
            must.append(FieldCondition(key="industry", match=MatchValue(value=industry)))
        if year_gte := filters.get("year_gte"):
            must.append(FieldCondition(key="year", range=Range(gte=year_gte)))
        if chunk_types := filters.get("chunk_type"):
            must.append(FieldCondition(key="chunk_type", match=MatchAny(any=chunk_types)))
        if doc_type := filters.get("doc_type"):
            must.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))
        if document_names := filters.get("document_names"):
            must.append(FieldCondition(key="document_name", match=MatchAny(any=document_names)))
        if asset_type := filters.get("asset_type"):
            must.append(FieldCondition(key="asset_type", match=MatchValue(value=asset_type)))
        if asset_types := filters.get("asset_types"):
            must.append(FieldCondition(key="asset_type", match=MatchAny(any=asset_types)))
        if asset_id := filters.get("asset_id"):
            must.append(FieldCondition(key="asset_id", match=MatchValue(value=asset_id)))
        if asset_ids := filters.get("asset_ids"):
            must.append(FieldCondition(key="asset_id", match=MatchAny(any=asset_ids)))
        return Filter(must=must) if must else None
