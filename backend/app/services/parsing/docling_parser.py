from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from typing import Any
import zipfile

try:
    from app.config import get_settings
except Exception:  # pragma: no cover - optional during lightweight test runs
    get_settings = None

try:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem
except ImportError:  # pragma: no cover - optional runtime dependency
    DocumentConverter = None
    InputFormat = None
    PdfPipelineOptions = None
    PdfFormatOption = None
    PictureItem = None
    SectionHeaderItem = None
    TableItem = None
    TextItem = None


DOC_LING_TEXT_TYPES = tuple(item for item in (TextItem, SectionHeaderItem) if item is not None)
DOC_LING_ASSET_TYPES = tuple(item for item in (PictureItem, TableItem) if item is not None)
DOC_LING_HEADING_TYPES = tuple(item for item in (SectionHeaderItem, TextItem) if item is not None)


@dataclass
class ParsedAsset:
    asset_type: str
    page_no: int | None
    title: str | None
    caption: str | None
    heading_path: str | None
    context_before: str | None
    context_after: str | None
    bbox: dict[str, Any] | None
    source_ref: str | None
    image_bytes: bytes | None
    image_ext: str = ".png"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    markdown: str
    metadata: dict
    assets: list[ParsedAsset] = field(default_factory=list)
    structure: dict[str, Any] = field(default_factory=dict)


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
            self.docling_libreoffice_cmd = settings.docling_libreoffice_cmd
        else:
            self.backend_mode = os.getenv("PARSER_BACKEND", "auto")
            self.docling_libreoffice_cmd = os.getenv("DOCLING_LIBREOFFICE_CMD")
        self.resolved_libreoffice_cmd = self._resolve_libreoffice_cmd()

    async def parse(self, file_path: str) -> ParsedDocument:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".doc":
            return ParsedDocument(
                markdown=self._normalize_text(
                    "Legacy DOC binary format is not directly supported in the current pipeline.\n\n"
                    "Please convert this file to DOCX or PDF before using it for main retrieval or reuse-first generation.",
                    path.name,
                ),
                metadata={
                    "source_name": path.name,
                    "parser": "legacy-doc-placeholder",
                    "parser_backend_requested": self.backend_mode,
                    "parser_backend_used": "legacy_doc_placeholder",
                    "parse_warning": "legacy_doc_requires_conversion",
                    "format": suffix.lstrip("."),
                },
            )

        if self._should_use_docling(suffix):
            try:
                self._configure_docling_environment()
                effective_path = path
                conversion_note: dict[str, Any] = {}
                if suffix == ".docx" and self._docx_prefers_pdf_conversion(path):
                    converted_pdf = self._convert_office_document_to_pdf(path)
                    if converted_pdf is not None:
                        effective_path = converted_pdf
                        conversion_note = {
                            "docling_docx_conversion": "libreoffice_pdf",
                            "docling_docx_conversion_source_format": "docx",
                            "docling_docx_conversion_target_format": "pdf",
                        }
                converter = self._build_converter(effective_path.suffix.lower())
                result = converter.convert(str(effective_path))
                items = list(result.document.iterate_items())
                markdown = result.document.export_to_markdown()
                assets = self._extract_assets(result.document, items=items)
                structure = self._extract_structure_hints(items)
                return ParsedDocument(
                    markdown=self._normalize_text(markdown, path.name),
                    metadata={
                        "source_name": path.name,
                        "parser": "docling",
                        "parser_backend_requested": self.backend_mode,
                        "parser_backend_used": "docling",
                        "docling_libreoffice_cmd": self.resolved_libreoffice_cmd,
                        "docling_libreoffice_available": bool(self.resolved_libreoffice_cmd),
                        "parser_structure_heading_count": len(structure.get("heading_hints") or []),
                        "format": suffix.lstrip("."),
                        **conversion_note,
                    },
                    assets=assets,
                    structure=structure,
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
                "docling_libreoffice_cmd": self.resolved_libreoffice_cmd,
                "docling_libreoffice_available": bool(self.resolved_libreoffice_cmd),
                "format": suffix.lstrip("."),
            },
            structure={},
        )

    def _configure_docling_environment(self) -> None:
        if self.resolved_libreoffice_cmd:
            os.environ["DOCLING_LIBREOFFICE_CMD"] = self.resolved_libreoffice_cmd

    def _docx_prefers_pdf_conversion(self, path: Path) -> bool:
        if not self.resolved_libreoffice_cmd or path.suffix.lower() != ".docx":
            return False
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile:
            return False

        media_names = [name.lower() for name in names if name.lower().startswith("word/media/")]
        if any(name.endswith((".wmf", ".emf")) for name in media_names):
            return True
        return False

    def _convert_office_document_to_pdf(self, path: Path) -> Path | None:
        if not self.resolved_libreoffice_cmd:
            return None
        temp_dir = tempfile.mkdtemp(prefix="docling-office-pdf-")
        executable = (
            self.resolved_libreoffice_cmd
            if Path(self.resolved_libreoffice_cmd).exists()
            else shutil.which(Path(self.resolved_libreoffice_cmd).name)
        )
        if executable is None:
            return None
        command = [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            temp_dir,
            str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        pdf_path = Path(temp_dir) / f"{path.stem}.pdf"
        if pdf_path.exists():
            return pdf_path
        return None

    def _resolve_libreoffice_cmd(self) -> str | None:
        candidates: list[str | None] = [
            self.docling_libreoffice_cmd,
            os.getenv("DOCLING_LIBREOFFICE_CMD"),
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/LibreOffice",
            "/opt/homebrew/bin/soffice",
            "/usr/local/bin/soffice",
            "/usr/bin/soffice",
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.exists():
                return str(path.resolve())
        return None

    def _build_converter(self, suffix: str) -> DocumentConverter:
        if suffix != ".pdf" or InputFormat is None or PdfPipelineOptions is None or PdfFormatOption is None:
            return DocumentConverter()

        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_page_images = True
        pipeline_options.generate_picture_images = True
        pipeline_options.images_scale = 2.0
        if hasattr(pipeline_options, "generate_table_images"):
            pipeline_options.generate_table_images = True
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
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

    def _extract_assets(self, document: Any, *, items: list[tuple[Any, int]] | None = None) -> list[ParsedAsset]:
        if not DOC_LING_ASSET_TYPES:
            return []

        items = items or list(document.iterate_items())
        assets: list[ParsedAsset] = []
        current_heading: str | None = None

        for index, (element, _level) in enumerate(items):
            if SectionHeaderItem is not None and isinstance(element, SectionHeaderItem):
                heading_text = self._extract_text(element)
                if heading_text:
                    current_heading = heading_text
                continue

            if not isinstance(element, DOC_LING_ASSET_TYPES):
                continue

            page_no, bbox = self._extract_page_and_bbox(element)
            image = self._extract_asset_image(document, element, page_no=page_no, bbox=bbox)
            image_bytes = self._to_png_bytes(image) if image is not None else None
            image_size = image.size if image is not None else None
            caption = self._extract_caption(document, element)
            context_before = self._collect_neighbor_text(items, start_index=index - 1, step=-1)
            context_after = self._collect_neighbor_text(items, start_index=index + 1, step=1)
            asset_type = "table" if TableItem is not None and isinstance(element, TableItem) else "figure"
            page_size = self._get_page_size(document, page_no=page_no)
            visual_role = self._classify_visual_role(
                asset_type=asset_type,
                heading_path=current_heading,
                caption=caption,
                context_before=context_before,
                context_after=context_after,
                bbox=bbox,
                page_size=page_size,
                image_size=image_size,
            )

            assets.append(
                ParsedAsset(
                    asset_type=asset_type,
                    page_no=page_no,
                    title=current_heading,
                    caption=caption,
                    heading_path=current_heading,
                    context_before=context_before,
                    context_after=context_after,
                    bbox=self._bbox_to_dict(bbox),
                    source_ref=str(getattr(element, "self_ref", "") or ""),
                    image_bytes=image_bytes,
                    meta={
                        "width": image_size[0] if image_size else None,
                        "height": image_size[1] if image_size else None,
                        "page_width": page_size[0] if page_size else None,
                        "page_height": page_size[1] if page_size else None,
                        "label": str(getattr(element, "label", "") or ""),
                        "caption": caption,
                        "visual_role": visual_role,
                    },
                )
            )

        return assets

    def _extract_structure_hints(self, items: list[tuple[Any, int]]) -> dict[str, Any]:
        if not DOC_LING_HEADING_TYPES:
            return {}

        heading_hints: list[dict[str, Any]] = []
        for index, (element, level) in enumerate(items):
            if not isinstance(element, DOC_LING_HEADING_TYPES):
                continue
            text = self._extract_text(element)
            if not text:
                continue
            page_no, _bbox = self._extract_page_and_bbox(element)
            heading_hints.append(
                {
                    "index": index,
                    "text": text,
                    "parser_level": int(level) if level is not None else None,
                    "item_type": type(element).__name__,
                    "page_no": page_no,
                    "source_ref": str(getattr(element, "self_ref", "") or ""),
                }
            )
        return {
            "heading_hints": heading_hints,
        }

    def _extract_text(self, element: Any) -> str | None:
        text = getattr(element, "text", None)
        if isinstance(text, str):
            normalized = " ".join(text.split())
            return normalized or None
        return None

    def _collect_neighbor_text(
        self,
        items: list[tuple[Any, int]],
        *,
        start_index: int,
        step: int,
        max_items: int = 2,
    ) -> str | None:
        collected: list[str] = []
        index = start_index
        while 0 <= index < len(items) and len(collected) < max_items:
            element, _level = items[index]
            text = self._extract_text(element)
            if text and text != "目录":
                collected.append(text)
            index += step
        if step < 0:
            collected.reverse()
        if not collected:
            return None
        return " | ".join(collected)

    def _extract_caption(self, document: Any, element: Any) -> str | None:
        if not hasattr(element, "caption_text"):
            return None
        try:
            caption = element.caption_text(document)
        except Exception:
            return None
        if not isinstance(caption, str):
            return None
        normalized = " ".join(caption.split())
        return normalized or None

    def _extract_page_and_bbox(self, element: Any) -> tuple[int | None, Any | None]:
        prov = getattr(element, "prov", None)
        if not prov:
            return None, None
        first = prov[0]
        return getattr(first, "page_no", None), getattr(first, "bbox", None)

    def _extract_asset_image(self, document: Any, element: Any, *, page_no: int | None, bbox: Any | None) -> Any | None:
        image = None
        if hasattr(element, "get_image"):
            try:
                image = element.get_image(document)
            except Exception:
                image = None
        if image is not None:
            return image
        if page_no is None or bbox is None:
            return None
        return self._crop_from_page(document, page_no=page_no, bbox=bbox)

    def _get_page_size(self, document: Any, *, page_no: int | None) -> tuple[float, float] | None:
        if page_no is None:
            return None
        pages = getattr(document, "pages", None) or {}
        page_item = pages.get(page_no)
        page_size = getattr(page_item, "size", None)
        if page_size is None:
            return None
        return float(page_size.width), float(page_size.height)

    def _crop_from_page(self, document: Any, *, page_no: int, bbox: Any) -> Any | None:
        pages = getattr(document, "pages", None) or {}
        page_item = pages.get(page_no)
        if page_item is None or not hasattr(page_item, "image") or page_item.image is None:
            return None

        page_image = getattr(page_item.image, "pil_image", None)
        page_size = getattr(page_item, "size", None)
        if page_image is None or page_size is None:
            return None

        scale_x = page_image.size[0] / float(page_size.width)
        scale_y = page_image.size[1] / float(page_size.height)
        left = max(0, int(float(bbox.l) * scale_x))
        right = min(page_image.size[0], int(float(bbox.r) * scale_x))
        upper = max(0, int(page_image.size[1] - float(bbox.t) * scale_y))
        lower = min(page_image.size[1], int(page_image.size[1] - float(bbox.b) * scale_y))
        if right <= left or lower <= upper:
            return None
        return page_image.crop((left, upper, right, lower))

    def _bbox_to_dict(self, bbox: Any | None) -> dict[str, Any] | None:
        if bbox is None:
            return None
        return {
            "l": float(bbox.l),
            "t": float(bbox.t),
            "r": float(bbox.r),
            "b": float(bbox.b),
            "coord_origin": str(getattr(bbox, "coord_origin", "")),
        }

    def _classify_visual_role(
        self,
        *,
        asset_type: str,
        heading_path: str | None,
        caption: str | None,
        context_before: str | None,
        context_after: str | None,
        bbox: Any | None,
        page_size: tuple[float, float] | None,
        image_size: tuple[int, int] | None,
    ) -> str:
        if asset_type == "table":
            return "table_asset"

        joined_context = " ".join(
            value for value in [heading_path, caption, context_before, context_after] if isinstance(value, str) and value
        )
        if any(marker in joined_context for marker in ("版本", "页码", "总页数", "目录", "DAYU ELECTRIC", "买方", "卖方")):
            return "page_furniture"

        if any(
            marker in joined_context
            for marker in ("原理图", "接线图", "示意图", "波形", "电压", "电流", "circuit", "waveform", "schematic")
        ):
            return "engineering_figure"

        if bbox is not None and page_size is not None:
            page_width, page_height = page_size
            box_width = float(bbox.r) - float(bbox.l)
            box_height = float(bbox.t) - float(bbox.b)
            near_top = float(bbox.t) >= page_height * 0.88
            near_bottom = float(bbox.b) <= page_height * 0.12
            narrow_band = box_height <= page_height * 0.12
            if narrow_band and (near_top or near_bottom):
                return "page_furniture"
            if box_width * box_height >= page_width * page_height * 0.08:
                return "engineering_figure"

        if image_size is not None and image_size[0] * image_size[1] >= 160000:
            return "engineering_figure"

        return "illustration"

    def _to_png_bytes(self, image: Any | None) -> bytes | None:
        if image is None:
            return None
        handle = BytesIO()
        image.save(handle, format="PNG")
        return handle.getvalue()
