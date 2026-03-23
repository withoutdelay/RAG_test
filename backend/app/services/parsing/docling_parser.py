from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:
    from app.config import get_settings
except Exception:  # pragma: no cover - optional during lightweight test runs
    get_settings = None

try:
    from docling.document_converter import DocumentConverter
except ImportError:  # pragma: no cover - optional runtime dependency
    DocumentConverter = None


@dataclass
class ParsedDocument:
    markdown: str
    metadata: dict


class DoclingParser:
    """
    Minimal parser facade for Phase 2.

    This keeps the service boundary stable so real Docling integration can replace
    the fallback implementation without changing the API layer.
    """

    def __init__(self) -> None:
        if get_settings is not None:
            settings = get_settings()
            self.backend_mode = settings.parser_backend
        else:
            self.backend_mode = os.getenv("PARSER_BACKEND", "auto")

    async def parse(self, file_path: str) -> ParsedDocument:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if self._should_use_docling(suffix):
            try:
                converter = DocumentConverter()
                result = converter.convert(file_path)
                markdown = result.document.export_to_markdown()
                return ParsedDocument(
                    markdown=self._normalize_text(markdown, path.name),
                    metadata={
                        "source_name": path.name,
                        "parser": "docling",
                        "parser_backend_requested": self.backend_mode,
                        "parser_backend_used": "docling",
                        "format": suffix.lstrip("."),
                    },
                )
            except Exception:
                if self.backend_mode == "docling":
                    raise

        if suffix in {".md", ".txt"}:
            text = path.read_text(encoding="utf-8")
        else:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="ignore")

        normalized = self._normalize_text(text, path.name)
        return ParsedDocument(
            markdown=normalized,
            metadata={
                "source_name": path.name,
                "parser": "fallback-docling-parser",
                "parser_backend_requested": self.backend_mode,
                "parser_backend_used": "fallback",
                "format": suffix.lstrip("."),
            },
        )

    def _should_use_docling(self, suffix: str) -> bool:
        if suffix in {".md", ".txt"}:
            return False
        if self.backend_mode == "fallback":
            return False
        if DocumentConverter is None:
            if self.backend_mode == "docling":
                raise RuntimeError("Docling backend requested but docling is not installed")
            return False
        return True

    def _normalize_text(self, text: str, filename: str) -> str:
        stripped = text.strip()
        if not stripped:
            return f"# {filename}\n\n文档内容为空或暂未能解析出可用文本。"
        return stripped if stripped.startswith("#") else f"# {filename}\n\n{stripped}"
