from __future__ import annotations

from pathlib import Path

from app.services.parsing.docling_parser import ParsedAsset


class ImageExtractor:
    async def extract(self, file_path: str) -> list[ParsedAsset]:
        suffix = Path(file_path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            path = Path(file_path)
            return [
                ParsedAsset(
                    asset_type="native_image",
                    page_no=1,
                    title=path.stem,
                    caption=None,
                    heading_path=path.stem,
                    context_before=None,
                    context_after=None,
                    bbox=None,
                    source_ref=str(path),
                    image_bytes=path.read_bytes(),
                    image_ext=path.suffix.lower(),
                    meta={"source_path": str(path)},
                )
            ]
        return []
