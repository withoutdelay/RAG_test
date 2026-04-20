from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db import get_session_factory
from app.services.catalog import ProductCatalogService
from app.services.catalog.defaults import DEFAULT_CATALOG_VERSION

from import_product_material_manifest import resolve_default_manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap the product-driven catalog and materials registry for the current DATABASE_URL."
    )
    parser.add_argument(
        "--catalog-version",
        default=DEFAULT_CATALOG_VERSION,
        help=f"Catalog version to seed. Defaults to {DEFAULT_CATALOG_VERSION}.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=(
            "Path to the materials manifest. Defaults to "
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
        "--replace-existing-materials",
        action="store_true",
        help="Replace existing materials with the same material_key instead of skipping them.",
    )
    parser.add_argument(
        "--replace-existing-catalog",
        action="store_true",
        help="Replace the existing catalog version before seeding defaults.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest).expanduser() if args.manifest else resolve_default_manifest_path(repo_root)

    service = ProductCatalogService()
    async with get_session_factory()() as session:
        catalog_result = await service.import_default_catalog(
            session=session,
            catalog_version=args.catalog_version,
            publish=True,
            replace_existing=args.replace_existing_catalog,
        )
        material_result = await service.import_material_manifest(
            session=session,
            manifest_path=str(manifest_path),
            replace_existing=args.replace_existing_materials,
            source_kind=args.source_kind,
        )

    print(f"catalog_version={catalog_result['catalog_version']}")
    print(f"catalog_reused_existing={catalog_result['reused_existing']}")
    print(f"catalog_imported_series_count={catalog_result['imported_series_count']}")
    print(f"manifest={material_result['manifest_path']}")
    print(f"imported_material_count={material_result['imported_material_count']}")
    print(f"skipped_existing_count={material_result['skipped_existing_count']}")
    for family_code, count in sorted(material_result["family_counts"].items()):
        print(f"family_count[{family_code}]={count}")


if __name__ == "__main__":
    asyncio.run(main())
