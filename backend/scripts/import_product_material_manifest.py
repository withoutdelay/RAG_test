from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db import get_session_factory
from app.services.catalog import ProductCatalogService


def resolve_default_manifest_path(repo_root: Path) -> Path:
    enriched_manifest = repo_root / "output" / "product-driven-sample-manifest-enriched.json"
    if enriched_manifest.exists():
        return enriched_manifest
    return repo_root / "output" / "product-driven-sample-manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a product materials manifest into the product_materials registry.")
    parser.add_argument(
        "--manifest",
        default=None,
        help=(
            "Path to the manifest JSON. Defaults to "
            "output/product-driven-sample-manifest-enriched.json when present, "
            "otherwise falls back to output/product-driven-sample-manifest.json."
        ),
    )
    parser.add_argument(
        "--source-kind",
        default="private_sample",
        help="source_kind recorded in product_materials. Defaults to private_sample.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing rows with the same material_key instead of skipping them.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest).expanduser() if args.manifest else resolve_default_manifest_path(repo_root)

    service = ProductCatalogService()
    async with get_session_factory()() as session:
        result = await service.import_material_manifest(
            session=session,
            manifest_path=str(manifest_path),
            replace_existing=args.replace_existing,
            source_kind=args.source_kind,
        )

    print(f"manifest={result['manifest_path']}")
    print(f"imported_material_count={result['imported_material_count']}")
    print(f"skipped_existing_count={result['skipped_existing_count']}")
    for family_code, count in sorted(result["family_counts"].items()):
        print(f"family_count[{family_code}]={count}")


if __name__ == "__main__":
    asyncio.run(main())
