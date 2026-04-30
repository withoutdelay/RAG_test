from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.retrieval.visual_backend import (
    VisualEmbedder,
    build_visual_qdrant_collection_name,
    build_visual_asset_fallback_text,
    build_visual_embedding_cache_snapshot,
    build_visual_embedding_cache_payload,
    load_visual_embedding_cache,
    sync_visual_embedding_cache_payload_to_qdrant,
    VisualEmbeddingCacheEntry,
    _should_include_visual_cache_asset,
)


class FakeTextEmbedder:
    async def embed_text(self, text: str) -> list[float]:
        size = float(max(len(text), 1))
        return [size, size / 10.0]


class FakeTrueVisualBackend:
    backend_name = "clip"
    is_true_visual = True

    def __init__(self, *, fail_asset: bool = False) -> None:
        self.fail_asset = fail_asset

    async def embed_query(self, text: str) -> list[float]:
        size = float(max(len(text), 1))
        return [size / 100.0, 1.0]

    async def embed_asset(self, *, asset_uri: str, fallback_text: str) -> list[float]:
        del asset_uri
        del fallback_text
        if self.fail_asset:
            raise RuntimeError("image backend unavailable")
        return [0.2, 0.8]


class FakeQdrantService:
    collections: dict[str, list[object]] = {}

    def __init__(self, *, collection_name: str | None = None, dimension: int | None = None) -> None:
        self.collection_name = str(collection_name or "")
        self.dimension = int(dimension or 0)

    def recreate_collection(self) -> None:
        self.collections[self.collection_name] = []

    def upsert_points(self, *, points: list[object]) -> None:
        self.collections.setdefault(self.collection_name, []).extend(points)


class VisualBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_mode_uses_text_proxy_for_query_and_asset(self) -> None:
        embedder = VisualEmbedder(
            text_embedder=FakeTextEmbedder(),
            backend_mode="proxy",
        )

        query_vectors = await embedder.build_query_vectors("主回路系统图")
        asset_vector, source = await embedder.embed_asset(
            asset_uri="/tmp/demo.png",
            fallback_text="display_title:主回路系统图",
        )

        self.assertEqual(embedder.backend_name, "text-proxy")
        self.assertEqual(list(query_vectors), ["text_proxy"])
        self.assertEqual(source, "text_proxy")
        self.assertEqual(asset_vector, [20.0, 2.0])

    async def test_auto_mode_exposes_image_query_vector_and_falls_back_on_asset_failure(self) -> None:
        embedder = VisualEmbedder(
            text_embedder=FakeTextEmbedder(),
            backend_mode="auto",
            true_visual_backend=FakeTrueVisualBackend(fail_asset=True),
        )

        query_vectors = await embedder.build_query_vectors("控制接口系统图")
        asset_vector, source = await embedder.embed_asset(
            asset_uri="/tmp/missing.png",
            fallback_text="display_title:控制接口系统图",
        )

        self.assertEqual(embedder.backend_name, "clip")
        self.assertIn("image", query_vectors)
        self.assertIn("text_proxy", query_vectors)
        self.assertEqual(source, "text_proxy")
        self.assertEqual(asset_vector, [21.0, 2.1])

    async def test_auto_mode_uses_true_visual_backend_when_asset_embedding_succeeds(self) -> None:
        embedder = VisualEmbedder(
            text_embedder=FakeTextEmbedder(),
            backend_mode="auto",
            true_visual_backend=FakeTrueVisualBackend(fail_asset=False),
        )

        query_vectors = await embedder.build_query_vectors("LCI系统图")
        asset_vector, source = await embedder.embed_asset(
            asset_uri="/tmp/lci.png",
            fallback_text="display_title:LCI系统图",
        )

        self.assertIn("image", query_vectors)
        self.assertEqual(source, "image")
        self.assertEqual(asset_vector, [0.2, 0.8])

    async def test_auto_mode_prefers_cached_image_embedding_when_available(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "asset_embedding_cache.json"
            payload = build_visual_embedding_cache_payload(
                backend_name="clip",
                model_name="openai/clip-vit-base-patch32",
                entries=[
                    VisualEmbeddingCacheEntry(
                        asset_id="asset-1",
                        embedding=[0.4, 0.6],
                        backend_name="clip",
                        model_name="openai/clip-vit-base-patch32",
                        visual_source="image",
                    )
                ],
            )
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            embedder = VisualEmbedder(
                text_embedder=FakeTextEmbedder(),
                backend_mode="auto",
                model_name="openai/clip-vit-base-patch32",
                cache_path=cache_path,
                true_visual_backend=FakeTrueVisualBackend(fail_asset=True),
            )

            asset_vector, source = await embedder.embed_asset(
                asset_id="asset-1",
                asset_uri="/tmp/demo.png",
                fallback_text="display_title:主回路系统图",
            )

            self.assertEqual(source, "image_cache")
            self.assertEqual(asset_vector, [0.4, 0.6])

    async def test_auto_mode_marks_cached_text_proxy_embedding_as_text_proxy_cache(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "asset_embedding_cache.json"
            payload = build_visual_embedding_cache_payload(
                backend_name="clip",
                model_name="openai/clip-vit-base-patch32",
                entries=[
                    VisualEmbeddingCacheEntry(
                        asset_id="asset-2",
                        embedding=[0.7, 0.3],
                        backend_name="clip",
                        model_name="openai/clip-vit-base-patch32",
                        visual_source="text_proxy",
                    )
                ],
            )
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            embedder = VisualEmbedder(
                text_embedder=FakeTextEmbedder(),
                backend_mode="auto",
                model_name="openai/clip-vit-base-patch32",
                cache_path=cache_path,
                true_visual_backend=FakeTrueVisualBackend(fail_asset=False),
            )

            asset_vector, source = await embedder.embed_asset(
                asset_id="asset-2",
                asset_uri="/tmp/demo.png",
                fallback_text="display_title:控制接口系统图",
            )

            self.assertEqual(source, "text_proxy_cache")
            self.assertEqual(asset_vector, [0.7, 0.3])

    async def test_build_visual_embedding_cache_snapshot_collects_stats(self) -> None:
        embedder = VisualEmbedder(
            text_embedder=FakeTextEmbedder(),
            backend_mode="auto",
            true_visual_backend=FakeTrueVisualBackend(fail_asset=True),
            load_cache=False,
        )
        rows = [
            (
                SimpleNamespace(
                    id="asset-1",
                    title="主回路系统图",
                    caption="高压变频器主回路",
                    asset_uri="/tmp/demo.png",
                    asset_type="figure",
                    page_no=3,
                    meta={
                        "heading_path": "4.1 主回路",
                        "context_before": "高压变频器采用 LCI",
                        "semantic_summary": {"diagram_type": "主回路系统图", "retrieval_keywords": ["LCI", "同步切换"]},
                    },
                ),
                SimpleNamespace(id="raw-1", project_id="project-1", file_name="方案A.pdf"),
            )
        ]

        payload, stats = await build_visual_embedding_cache_snapshot(
            rows=rows,
            visual_embedder=embedder,
            asset_types=["figure"],
            include_proxy_fallback=True,
            backend_mode="auto",
        )

        self.assertEqual(payload["entry_count"], 1)
        self.assertEqual(payload["entries"][0]["visual_source"], "text_proxy")
        self.assertEqual(stats["visual_cache_entry_count"], 1)
        self.assertEqual(stats["visual_cache_source_breakdown"]["text_proxy"], 1)

    def test_visual_cache_excludes_storage_fallback_figures(self) -> None:
        asset = SimpleNamespace(
            asset_type="figure",
            meta={
                "storage_fallback": True,
                "visual_role": "engineering_figure",
            },
        )

        self.assertFalse(_should_include_visual_cache_asset(asset))

    def test_build_visual_asset_fallback_text_includes_semantic_summary(self) -> None:
        text = build_visual_asset_fallback_text(
            title="控制接口图",
            caption="DCS 与变频器",
            metadata={
                "heading_path": "5.2 控制接口",
                "context_before": "包含硬接点与通讯接口",
                "semantic_summary": {
                    "summary": "描述控制回路与信号流向",
                    "retrieval_keywords": ["PLC", "DCS"],
                },
            },
        )

        self.assertIn("控制接口图", text)
        self.assertIn("DCS 与变频器", text)
        self.assertIn("描述控制回路与信号流向", text)
        self.assertIn("PLC", text)

    def test_load_visual_embedding_cache_reads_payload(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "asset_embedding_cache.json"
            payload = build_visual_embedding_cache_payload(
                backend_name="clip",
                model_name="openai/clip-vit-base-patch32",
                entries=[
                    VisualEmbeddingCacheEntry(
                        asset_id="asset-1",
                        embedding=[0.1, 0.9],
                        backend_name="clip",
                        model_name="openai/clip-vit-base-patch32",
                        visual_source="image",
                    )
                ],
            )
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            cache, meta = load_visual_embedding_cache(cache_path)

            self.assertIn("asset-1", cache)
            self.assertEqual(cache["asset-1"].embedding, [0.1, 0.9])
            self.assertEqual(meta["backend_name"], "clip")

    def test_sync_visual_embedding_cache_payload_to_qdrant_groups_by_channel(self) -> None:
        payload = build_visual_embedding_cache_payload(
            backend_name="clip",
            model_name="openai/clip-vit-base-patch32",
            entries=[
                VisualEmbeddingCacheEntry(
                    asset_id="asset-image",
                    embedding=[0.1, 0.9],
                    backend_name="clip",
                    model_name="openai/clip-vit-base-patch32",
                    visual_source="image",
                    metadata={"asset_type": "figure"},
                ),
                VisualEmbeddingCacheEntry(
                    asset_id="asset-proxy",
                    embedding=[0.7, 0.3, 0.2],
                    backend_name="clip",
                    model_name="openai/clip-vit-base-patch32",
                    visual_source="text_proxy",
                    metadata={"asset_type": "figure"},
                ),
            ],
        )
        FakeQdrantService.collections = {}

        with patch("app.services.retrieval.visual_backend.QdrantService", FakeQdrantService):
            stats = sync_visual_embedding_cache_payload_to_qdrant(
                payload=payload,
                collection_prefix="rag_visual_test",
            )

        self.assertEqual(stats["visual_qdrant_sync_status"], "succeeded")
        self.assertEqual(stats["visual_qdrant_indexed_points"], 2)
        self.assertEqual(
            stats["visual_qdrant_collections"]["image"],
            build_visual_qdrant_collection_name(channel="image", collection_prefix="rag_visual_test"),
        )
        self.assertEqual(
            stats["visual_qdrant_collections"]["text_proxy"],
            build_visual_qdrant_collection_name(channel="text_proxy", collection_prefix="rag_visual_test"),
        )
        self.assertEqual(len(FakeQdrantService.collections[stats["visual_qdrant_collections"]["image"]]), 1)
        self.assertEqual(len(FakeQdrantService.collections[stats["visual_qdrant_collections"]["text_proxy"]]), 1)


if __name__ == "__main__":
    unittest.main()
