from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.retrieval.visual_backend import (
    build_visual_embedding_cache_snapshot,
    collect_visual_cache_asset_rows,
    VisualEmbedder,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build offline visual embedding cache for figure assets.")
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional project UUID to limit the build scope.",
    )
    parser.add_argument(
        "--output",
        default="data/visual_index/asset_embedding_cache.json",
        help="Target cache JSON path.",
    )
    parser.add_argument(
        "--asset-types",
        nargs="*",
        default=["figure"],
        help="Asset types to include. Defaults to figure only.",
    )
    parser.add_argument(
        "--backend-mode",
        default="auto",
        help="Visual embedding backend mode. Recommended: auto or clip.",
    )
    parser.add_argument(
        "--model-name",
        default="",
        help="Optional visual embedding model override.",
    )
    parser.add_argument(
        "--device",
        default="",
        help="Optional device override, e.g. cpu / cuda / auto.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max asset count for a single run.",
    )
    parser.add_argument(
        "--include-proxy-fallback",
        action="store_true",
        help="Also persist proxy-fallback vectors when true image embeddings are unavailable.",
    )
    parser.add_argument(
        "--skip-qdrant-sync",
        action="store_true",
        help="Only write cache JSON and skip syncing the visual collections in Qdrant.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    visual_embedder = VisualEmbedder(
        backend_mode=str(args.backend_mode or "auto"),
        model_name=str(args.model_name or "").strip() or None,
        device=str(args.device or "").strip() or None,
        cache_path=output_path,
        load_cache=False,
    )
    rows = await collect_visual_cache_asset_rows(
        project_id=str(args.project_id or "").strip(),
        asset_types=[str(item) for item in (args.asset_types or [])],
        limit=int(args.limit or 0),
    )
    payload, stats = await build_visual_embedding_cache_snapshot(
        rows=rows,
        visual_embedder=visual_embedder,
        project_id=str(args.project_id or "").strip(),
        asset_types=[str(item) for item in (args.asset_types or [])],
        limit=int(args.limit or 0),
        backend_mode=str(args.backend_mode or "auto"),
        include_proxy_fallback=bool(args.include_proxy_fallback),
    )
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.skip_qdrant_sync:
        try:
            from app.services.retrieval.visual_backend import sync_visual_embedding_cache_payload_to_qdrant

            stats.update(sync_visual_embedding_cache_payload_to_qdrant(payload=payload))
        except Exception as exc:  # noqa: BLE001
            stats["visual_qdrant_sync_status"] = "failed"
            stats["visual_qdrant_sync_error"] = str(exc)
    else:
        stats["visual_qdrant_sync_status"] = "skipped"

    print(f"Wrote visual embedding cache to {output_path.resolve()}")
    print(f"  backend: {stats['visual_cache_backend']}")
    print(f"  model: {stats['visual_cache_model']}")
    print(f"  entries: {stats['visual_cache_entry_count']}")
    print(f"  total assets: {stats['visual_cache_total_assets']}")
    print(f"  skipped proxy fallback: {stats['visual_cache_skipped_proxy_fallback']}")
    print(f"  qdrant sync: {stats.get('visual_qdrant_sync_status', 'unknown')}")
    if stats.get("visual_qdrant_indexed_points") is not None:
        print(f"  qdrant indexed points: {stats.get('visual_qdrant_indexed_points')}")
    if stats["visual_cache_failed_assets"]:
        print(f"  failed assets: {stats['visual_cache_failed_assets']}")
        for item in stats["visual_cache_failed_asset_samples"]:
            print(f"    - {item}")
    if stats.get("visual_qdrant_sync_error"):
        print(f"  qdrant sync error: {stats['visual_qdrant_sync_error']}")


if __name__ == "__main__":
    asyncio.run(main())
