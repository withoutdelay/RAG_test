from __future__ import annotations

from pathlib import Path


class ImageExtractor:
    async def extract(self, file_path: str) -> list[dict]:
        suffix = Path(file_path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return [{"kind": "native_image", "path": file_path}]
        return []
