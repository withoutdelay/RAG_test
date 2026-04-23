from __future__ import annotations

import asyncio

from app.services.knowledge import warm_uploaded_document_library_cache


async def main() -> None:
    stats = await warm_uploaded_document_library_cache()
    print("Warmed uploaded history library cache")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")


if __name__ == "__main__":
    asyncio.run(main())
