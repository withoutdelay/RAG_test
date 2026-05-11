from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

try:
    from app.config import get_settings
except Exception:  # pragma: no cover - optional during lightweight test runs
    get_settings = None

from app.services.parsing.docling_runtime import configure_docling_runtime
from app.services.parsing.section_catalog import build_section_catalog

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

MIN_REUSABLE_FIGURE_DIMENSION = 80
MIN_REUSABLE_FIGURE_AREA = 12000
DOCX_RASTER_MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
DOCX_VECTOR_MEDIA_EXTENSIONS = {".wmf", ".emf"}
OFFICE_HTML_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
LAYOUT_DRAWING_MARKERS = ("外观图", "高度关系", "平面间距", "间距示意", "外形", "柜体分段", "顶部通风", "布置图", "尺寸图", "检修通道")
COMPOSITE_FIGURE_TITLE_MARKERS = (
    "单线图",
    "接线图",
    "主接线",
    "一次图",
    "一次原理",
    "原理图",
    "系统图",
    "系统示意",
    "拓扑",
    "框图",
    "diagram",
    "schematic",
    "topology",
)
OOXML_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OOXML_EMU_PER_PIXEL = 9525


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

    async def parse(self, file_path: str, *, include_assets: bool = True) -> ParsedDocument:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".doc" and (
            self.backend_mode == "fallback" or DocumentConverter is None or not self.resolved_libreoffice_cmd
        ):
            return self._legacy_doc_placeholder(path)

        if self._should_use_docling(suffix):
            try:
                self._configure_docling_environment()
                effective_path = path
                conversion_note: dict[str, Any] = {}
                if suffix == ".doc":
                    converted_docx = self._convert_legacy_doc_to_docx(path)
                    if converted_docx is not None:
                        effective_path = converted_docx
                        conversion_note = {
                            "docling_office_conversion": "libreoffice_docx",
                            "docling_office_conversion_source_format": "doc",
                            "docling_office_conversion_target_format": "docx",
                        }
                    else:
                        return self._legacy_doc_placeholder(path)
                elif self._office_document_prefers_pdf_conversion(path):
                    converted_pdf = self._convert_office_document_to_pdf(path)
                    if converted_pdf is not None:
                        effective_path = converted_pdf
                        conversion_note = {
                            "docling_office_conversion": "libreoffice_pdf",
                            "docling_office_conversion_source_format": suffix.lstrip("."),
                            "docling_office_conversion_target_format": "pdf",
                        }
                converter = self._build_converter(effective_path.suffix.lower(), include_assets=include_assets)
                result = converter.convert(str(effective_path))
                items = list(result.document.iterate_items())
                markdown = result.document.export_to_markdown()
                normalized_markdown = self._normalize_text(markdown, path.name)
                assets = self._extract_assets(result.document, items=items) if include_assets else []
                asset_repair_note: dict[str, Any] = {}
                if include_assets:
                    assets, asset_repair_note = self._repair_missing_figure_images(
                        assets,
                        source_path=path,
                        effective_path=effective_path,
                    )
                    assets, composite_repair_note = self._repair_docx_composite_figures(
                        assets,
                        source_path=path,
                        effective_path=effective_path,
                    )
                    asset_repair_note.update(composite_repair_note)
                structure = self._extract_structure_hints(items, markdown=normalized_markdown)
                return ParsedDocument(
                    markdown=normalized_markdown,
                    metadata={
                        "source_name": path.name,
                        "parser": "docling",
                        "parser_backend_requested": self.backend_mode,
                        "parser_backend_used": "docling",
                        "docling_libreoffice_cmd": self.resolved_libreoffice_cmd,
                        "docling_libreoffice_available": bool(self.resolved_libreoffice_cmd),
                        "asset_extraction_enabled": include_assets,
                        "parser_structure_heading_count": len(structure.get("heading_hints") or []),
                        "format": effective_path.suffix.lower().lstrip(".") or suffix.lstrip("."),
                        "original_format": suffix.lstrip("."),
                        **conversion_note,
                        **asset_repair_note,
                    },
                    assets=assets,
                    structure=structure,
                )
            except Exception:
                if self.backend_mode == "docling":
                    raise
                if suffix == ".doc":
                    return self._legacy_doc_placeholder(path)

        if suffix == ".doc":
            return self._legacy_doc_placeholder(path)

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
                "asset_extraction_enabled": include_assets,
                "format": suffix.lstrip("."),
            },
            structure={},
        )

    def _legacy_doc_placeholder(self, path: Path) -> ParsedDocument:
        return ParsedDocument(
            markdown=self._normalize_text(
                "Legacy DOC binary format could not be converted automatically in the current runtime.\n\n"
                "Please convert this file to DOCX or PDF before using it for main retrieval or reuse-first generation.",
                path.name,
            ),
            metadata={
                "source_name": path.name,
                "parser": "legacy-doc-placeholder",
                "parser_backend_requested": self.backend_mode,
                "parser_backend_used": "legacy_doc_placeholder",
                "parse_warning": "legacy_doc_requires_conversion",
                "docling_libreoffice_cmd": self.resolved_libreoffice_cmd,
                "docling_libreoffice_available": bool(self.resolved_libreoffice_cmd),
                "format": path.suffix.lower().lstrip("."),
            },
        )

    def _configure_docling_environment(self) -> None:
        if get_settings is not None:
            configure_docling_runtime(get_settings())
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

    def _office_document_prefers_pdf_conversion(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if not self.resolved_libreoffice_cmd:
            return False
        if suffix == ".docx":
            return self._docx_prefers_pdf_conversion(path)
        return False

    def _convert_office_document_to_pdf(self, path: Path) -> Path | None:
        return self._convert_office_document(path, target_ext=".pdf", convert_to="pdf")

    def _convert_legacy_doc_to_docx(self, path: Path) -> Path | None:
        return self._convert_office_document(path, target_ext=".docx", convert_to="docx")

    def _convert_office_document(self, path: Path, *, target_ext: str, convert_to: str) -> Path | None:
        if not self.resolved_libreoffice_cmd:
            return None
        normalized_ext = target_ext if target_ext.startswith(".") else f".{target_ext}"
        temp_dir = tempfile.mkdtemp(prefix=f"docling-office-{normalized_ext.lstrip('.')}-")
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
            convert_to,
            "--outdir",
            temp_dir,
            str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        converted_path = Path(temp_dir) / f"{path.stem}{normalized_ext}"
        if converted_path.exists():
            return converted_path
        return None

    def _repair_missing_figure_images(
        self,
        assets: list[ParsedAsset],
        *,
        source_path: Path,
        effective_path: Path,
    ) -> tuple[list[ParsedAsset], dict[str, Any]]:
        missing_indexes = [
            index
            for index, asset in enumerate(assets)
            if asset.asset_type == "figure" and not asset.image_bytes
        ]
        if not missing_indexes:
            return assets, {
                "asset_repair_attempted": False,
                "asset_repair_missing_figure_count": 0,
                "asset_repair_success_count": 0,
            }

        stats: dict[str, Any] = {
            "asset_repair_attempted": True,
            "asset_repair_missing_figure_count": len(missing_indexes),
            "asset_repair_success_count": 0,
            "asset_repair_raster_media_success_count": 0,
            "asset_repair_html_media_success_count": 0,
            "asset_repair_pdf_render_success_count": 0,
            "asset_repair_vector_media_count": 0,
            "asset_repair_unsupported_media_count": 0,
            "asset_repair_methods": [],
        }

        remaining = self._repair_from_docx_media(
            assets,
            missing_indexes=missing_indexes,
            effective_path=effective_path,
            stats=stats,
        )
        if remaining:
            remaining = self._repair_from_office_html_media(
                assets,
                missing_indexes=remaining,
                source_path=source_path,
                effective_path=effective_path,
                stats=stats,
            )
        if remaining:
            remaining = self._repair_from_pdf_render(
                assets,
                missing_indexes=remaining,
                source_path=source_path,
                effective_path=effective_path,
                stats=stats,
            )
        stats["asset_repair_success_count"] = sum(
            1
            for index in missing_indexes
            if assets[index].asset_type == "figure" and bool(assets[index].image_bytes)
        )
        stats["asset_repair_unresolved_count"] = len(remaining)
        return assets, stats

    def _repair_docx_composite_figures(
        self,
        assets: list[ParsedAsset],
        *,
        source_path: Path,
        effective_path: Path,
    ) -> tuple[list[ParsedAsset], dict[str, Any]]:
        docx_path = self._select_docx_source_for_composite_repair(source_path=source_path, effective_path=effective_path)
        if docx_path is None:
            return assets, {
                "docx_composite_repair_attempted": False,
                "docx_composite_group_count": 0,
                "docx_composite_export_success_count": 0,
                "docx_composite_child_suppressed_count": 0,
            }

        composites = self._extract_docx_composite_groups(docx_path)
        if not composites:
            return assets, {
                "docx_composite_repair_attempted": False,
                "docx_composite_group_count": 0,
                "docx_composite_export_success_count": 0,
                "docx_composite_child_suppressed_count": 0,
            }

        media_targets_by_asset_index = self._match_assets_to_docx_media_targets(assets, docx_path)
        new_assets: list[ParsedAsset] = []
        suppressed_count = 0
        exported_count = 0

        for composite_index, composite in enumerate(composites):
            media_targets = set(composite.get("media_targets") or [])
            child_indexes = [
                index
                for index, media_target in media_targets_by_asset_index.items()
                if media_target in media_targets
            ]
            if not child_indexes and not self._docx_composite_heading_has_figure_intent(composite.get("heading_path")):
                continue
            if not child_indexes and not composite.get("image_bytes"):
                continue

            child_context = assets[child_indexes[0]] if child_indexes else None
            title = (
                (child_context.title if child_context else None)
                or composite.get("heading_path")
                or composite.get("name")
                or "DOCX composite figure"
            )
            heading_path = (
                (child_context.heading_path if child_context else None)
                or composite.get("heading_path")
                or title
            )
            context_before = child_context.context_before if child_context else None
            context_after = child_context.context_after if child_context else None

            composite_asset_added = False
            image_bytes = composite.get("image_bytes")
            image_size = composite.get("image_size")
            if isinstance(image_bytes, bytes) and image_bytes:
                new_assets.append(
                    ParsedAsset(
                        asset_type="figure",
                        page_no=None,
                        title=title,
                        caption=composite.get("name"),
                        heading_path=heading_path,
                        context_before=context_before,
                        context_after=context_after,
                        bbox=None,
                        source_ref=str(composite.get("source_ref") or f"docx-composite:{composite_index}"),
                        image_bytes=image_bytes,
                        image_ext=".png",
                        meta={
                            "width": image_size[0] if image_size else None,
                            "height": image_size[1] if image_size else None,
                            "visual_role": "engineering_figure",
                            "quality_flags": [],
                            "preserve_in_vector_db": True,
                            "asset_repair_method": "docx_ooxml_composite_group",
                            "asset_repair_precision": "word_group_object",
                            "asset_repair_source": str(docx_path),
                            "composite_figure": True,
                            "composite_group_name": composite.get("name"),
                            "composite_group_index": composite_index,
                            "composite_child_media": sorted(media_targets),
                            "storage_fallback": False,
                            "review_required": True,
                        },
                    )
                )
                composite_asset_added = True
                exported_count += 1

            for child_index in child_indexes:
                child = assets[child_index]
                child_flags = list((child.meta or {}).get("quality_flags") or [])
                for flag in (
                    "child_of_docx_composite_figure",
                    "not_preserved_for_vector_db",
                    "visual_role:asset_fragment",
                ):
                    if flag not in child_flags:
                        child_flags.append(flag)
                child.meta = {
                    **(child.meta or {}),
                    "visual_role": "asset_fragment",
                    "quality_flags": child_flags,
                    "preserve_in_vector_db": False,
                    "composite_child_asset": True,
                    "composite_parent_name": composite.get("name"),
                    "composite_parent_source_ref": composite.get("source_ref"),
                    "composite_required": not composite_asset_added,
                    "review_required": True,
                }
                suppressed_count += 1

        if new_assets:
            assets = [*assets, *new_assets]

        return assets, {
            "docx_composite_repair_attempted": True,
            "docx_composite_group_count": len(composites),
            "docx_composite_export_success_count": exported_count,
            "docx_composite_child_suppressed_count": suppressed_count,
        }

    def _select_docx_source_for_composite_repair(self, *, source_path: Path, effective_path: Path) -> Path | None:
        if source_path.suffix.lower() == ".docx" and source_path.exists():
            return source_path
        if effective_path.suffix.lower() == ".docx" and effective_path.exists():
            return effective_path
        return None

    def _match_assets_to_docx_media_targets(self, assets: list[ParsedAsset], docx_path: Path) -> dict[int, str]:
        media_items = self._extract_docx_media_items(docx_path)
        if not media_items:
            return {}

        media_by_digest: dict[str, list[str]] = {}
        media_by_size: dict[tuple[int, int], list[str]] = {}
        for item in media_items:
            raw_bytes = bytes(item.get("bytes") or b"")
            if not raw_bytes:
                continue
            target = str(item.get("name") or "")
            media_by_digest.setdefault(hashlib.sha256(raw_bytes).hexdigest(), []).append(target)
            image_size = self._read_image_size(raw_bytes)
            if image_size is not None:
                media_by_size.setdefault(image_size, []).append(target)

        matched: dict[int, str] = {}
        for index, asset in enumerate(assets):
            if asset.asset_type != "figure" or not asset.image_bytes:
                continue
            digest = hashlib.sha256(asset.image_bytes).hexdigest()
            digest_matches = media_by_digest.get(digest) or []
            if len(digest_matches) == 1:
                matched[index] = digest_matches[0]
                continue

            width = (asset.meta or {}).get("width")
            height = (asset.meta or {}).get("height")
            try:
                image_size = (int(width), int(height))
            except (TypeError, ValueError):
                image_size = self._read_image_size(asset.image_bytes)
            if image_size is None:
                continue
            size_matches = media_by_size.get(image_size) or []
            if len(size_matches) == 1:
                matched[index] = size_matches[0]
        return matched

    def _extract_docx_composite_groups(self, docx_path: Path) -> list[dict[str, Any]]:
        try:
            with zipfile.ZipFile(docx_path) as archive:
                document_xml = archive.read("word/document.xml")
                relationships = self._read_docx_relationships(archive, "word/_rels/document.xml.rels")
                root = ET.fromstring(document_xml)
                paragraphs = [node for node in root.iter() if self._xml_local_name(node.tag) == "p"]
                composites: list[dict[str, Any]] = []
                current_heading: str | None = None
                for paragraph_index, paragraph in enumerate(paragraphs):
                    paragraph_text = self._xml_text_content(paragraph)
                    if self._looks_like_docx_heading_text(paragraph_text):
                        current_heading = paragraph_text
                    group_nodes = [
                        node
                        for node in paragraph.iter()
                        if self._xml_local_name(node.tag) == "wgp"
                    ]
                    for group_index, group_node in enumerate(group_nodes):
                        composite = self._build_docx_composite_group(
                            archive=archive,
                            relationships=relationships,
                            group_node=group_node,
                            heading_path=current_heading,
                            paragraph_index=paragraph_index,
                            group_index=group_index,
                        )
                        if composite is not None:
                            composites.append(composite)
                return composites
        except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError):
            return []

    def _read_docx_relationships(self, archive: zipfile.ZipFile, rels_path: str) -> dict[str, str]:
        try:
            root = ET.fromstring(archive.read(rels_path))
        except (KeyError, ET.ParseError):
            return {}
        relationships: dict[str, str] = {}
        for relationship in root:
            if self._xml_local_name(relationship.tag) != "Relationship":
                continue
            rel_id = relationship.attrib.get("Id")
            target = relationship.attrib.get("Target")
            if not rel_id or not target:
                continue
            if not target.startswith("word/"):
                target = f"word/{target.lstrip('/')}"
            relationships[rel_id] = target
        return relationships

    def _build_docx_composite_group(
        self,
        *,
        archive: zipfile.ZipFile,
        relationships: dict[str, str],
        group_node: ET.Element,
        heading_path: str | None,
        paragraph_index: int,
        group_index: int,
    ) -> dict[str, Any] | None:
        media_targets = self._collect_docx_group_media_targets(group_node, relationships=relationships)
        shape_count = sum(1 for node in group_node.iter() if self._xml_local_name(node.tag) in {"wsp", "cxnSp", "sp"})
        picture_count = sum(1 for node in group_node.iter() if self._xml_local_name(node.tag) == "pic")
        if shape_count + picture_count < 2:
            return None

        image_bytes, image_size = self._render_docx_composite_group(
            archive=archive,
            relationships=relationships,
            group_node=group_node,
        )
        return {
            "source_ref": f"docx-composite:{paragraph_index}:{group_index}",
            "name": self._docx_group_name(group_node) or f"DOCX composite group {paragraph_index + 1}.{group_index + 1}",
            "heading_path": heading_path,
            "media_targets": sorted(media_targets),
            "shape_count": shape_count,
            "picture_count": picture_count,
            "image_bytes": image_bytes,
            "image_size": image_size,
        }

    def _collect_docx_group_media_targets(
        self,
        group_node: ET.Element,
        *,
        relationships: dict[str, str],
    ) -> set[str]:
        targets: set[str] = set()
        for node in group_node.iter():
            if self._xml_local_name(node.tag) != "blip":
                continue
            rel_id = (
                node.attrib.get(f"{{{OOXML_REL_NS}}}embed")
                or node.attrib.get(f"{{{OOXML_REL_NS}}}link")
                or node.attrib.get("embed")
                or node.attrib.get("link")
            )
            target = relationships.get(str(rel_id or ""))
            if target:
                targets.add(target)
        return targets

    def _render_docx_composite_group(
        self,
        *,
        archive: zipfile.ZipFile,
        relationships: dict[str, str],
        group_node: ET.Element,
    ) -> tuple[bytes | None, tuple[int, int] | None]:
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return None, None

        transform = self._docx_group_transform(group_node)
        if transform is None:
            return None, None
        transform = self._docx_expand_group_transform_to_content_bounds(group_node=group_node, transform=transform)
        canvas_width, canvas_height = transform["canvas_size"]
        if canvas_width <= 0 or canvas_height <= 0:
            return None, None

        canvas = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(canvas)
        for child in list(group_node):
            local_name = self._xml_local_name(child.tag)
            if local_name == "pic":
                self._draw_docx_composite_picture(
                    canvas=canvas,
                    archive=archive,
                    relationships=relationships,
                    picture_node=child,
                    transform=transform,
                )
            elif local_name in {"wsp", "cxnSp", "sp"}:
                self._draw_docx_composite_shape(draw=draw, shape_node=child, transform=transform)

        bbox = canvas.getbbox()
        if bbox:
            margin = 12
            left = max(0, bbox[0] - margin)
            upper = max(0, bbox[1] - margin)
            right = min(canvas.size[0], bbox[2] + margin)
            lower = min(canvas.size[1], bbox[3] + margin)
            canvas = canvas.crop((left, upper, right, lower))
        if canvas.width <= 0 or canvas.height <= 0:
            return None, None
        handle = BytesIO()
        canvas.save(handle, format="PNG")
        return handle.getvalue(), canvas.size

    def _docx_group_transform(self, group_node: ET.Element) -> dict[str, Any] | None:
        xfrm = self._find_first_xml_descendant(group_node, "xfrm")
        if xfrm is None:
            return None
        ext = self._find_first_xml_child(xfrm, "ext")
        ch_ext = self._find_first_xml_child(xfrm, "chExt")
        ch_off = self._find_first_xml_child(xfrm, "chOff")
        if ext is None or ch_ext is None:
            return None
        try:
            group_width = max(1, int(float(ext.attrib.get("cx") or 0)))
            group_height = max(1, int(float(ext.attrib.get("cy") or 0)))
            child_width = max(1.0, float(ch_ext.attrib.get("cx") or group_width))
            child_height = max(1.0, float(ch_ext.attrib.get("cy") or group_height))
            child_left = float(ch_off.attrib.get("x") or 0) if ch_off is not None else 0.0
            child_top = float(ch_off.attrib.get("y") or 0) if ch_off is not None else 0.0
        except (TypeError, ValueError):
            return None
        canvas_width = max(1, min(2400, int(round(group_width / OOXML_EMU_PER_PIXEL))))
        canvas_height = max(1, min(2400, int(round(group_height / OOXML_EMU_PER_PIXEL))))

        def map_point(x_value: float, y_value: float) -> tuple[float, float]:
            x = (float(x_value) - child_left) * canvas_width / child_width
            y = (float(y_value) - child_top) * canvas_height / child_height
            return x, y

        def map_box(off_x: float, off_y: float, ext_x: float, ext_y: float) -> tuple[float, float, float, float]:
            left, upper = map_point(off_x, off_y)
            right, lower = map_point(float(off_x) + float(ext_x), float(off_y) + float(ext_y))
            return left, upper, right, lower

        return {
            "canvas_size": (canvas_width, canvas_height),
            "child_size": (child_width, child_height),
            "map_point": map_point,
            "map_box": map_box,
        }

    def _docx_expand_group_transform_to_content_bounds(
        self,
        *,
        group_node: ET.Element,
        transform: dict[str, Any],
    ) -> dict[str, Any]:
        bounds = self._docx_group_content_bounds(group_node=group_node, transform=transform)
        if bounds is None:
            return transform

        canvas_width, canvas_height = transform["canvas_size"]
        min_x, min_y, max_x, max_y = bounds
        margin = 32
        final_left = min(0, int(round(min_x - margin)))
        final_upper = min(0, int(round(min_y - margin)))
        final_right = max(canvas_width, int(round(max_x + margin)))
        final_lower = max(canvas_height, int(round(max_y + margin)))
        final_width = final_right - final_left
        final_height = final_lower - final_upper
        if final_width <= 0 or final_height <= 0:
            return transform
        if final_width > 3600 or final_height > 3600:
            return transform
        if final_left == 0 and final_upper == 0 and final_right == canvas_width and final_lower == canvas_height:
            return transform

        base_map_point = transform["map_point"]

        def map_point(x_value: float, y_value: float) -> tuple[float, float]:
            x, y = base_map_point(x_value, y_value)
            return x - final_left, y - final_upper

        def map_box(off_x: float, off_y: float, ext_x: float, ext_y: float) -> tuple[float, float, float, float]:
            left, upper = map_point(off_x, off_y)
            right, lower = map_point(float(off_x) + float(ext_x), float(off_y) + float(ext_y))
            return left, upper, right, lower

        return {
            **transform,
            "canvas_size": (final_width, final_height),
            "map_point": map_point,
            "map_box": map_box,
            "pixel_bounds_expanded": True,
            "pixel_bounds_offset": (-final_left, -final_upper),
        }

    def _docx_group_content_bounds(
        self,
        *,
        group_node: ET.Element,
        transform: dict[str, Any],
    ) -> tuple[float, float, float, float] | None:
        boxes: list[tuple[float, float, float, float]] = []
        for child in list(group_node):
            local_name = self._xml_local_name(child.tag)
            if local_name not in {"pic", "wsp", "cxnSp", "sp"}:
                continue
            xfrm = self._find_first_xml_descendant(child, "xfrm")
            box = self._docx_xfrm_box(xfrm, transform=transform)
            if box is None:
                continue
            boxes.append(box)
            if local_name in {"wsp", "cxnSp", "sp"}:
                point_bounds = self._docx_custom_geometry_point_bounds(
                    shape_node=child,
                    box=self._normalize_pixel_box(box),
                    transform=transform,
                )
                if point_bounds is not None:
                    boxes.append(point_bounds)

        if not boxes:
            return None
        min_x = min(min(left, right) for left, _upper, right, _lower in boxes)
        min_y = min(min(upper, lower) for _left, upper, _right, lower in boxes)
        max_x = max(max(left, right) for left, _upper, right, _lower in boxes)
        max_y = max(max(upper, lower) for _left, upper, _right, lower in boxes)
        return min_x, min_y, max_x, max_y

    def _docx_custom_geometry_point_bounds(
        self,
        *,
        shape_node: ET.Element,
        box: tuple[int, int, int, int],
        transform: dict[str, Any],
    ) -> tuple[float, float, float, float] | None:
        points: list[tuple[float, float]] = []
        for path in (node for node in shape_node.iter() if self._xml_local_name(node.tag) == "path"):
            try:
                path_width = max(1.0, float(path.attrib.get("w") or 1.0))
                path_height = max(1.0, float(path.attrib.get("h") or 1.0))
            except (TypeError, ValueError):
                path_width, path_height = 1.0, 1.0
            for command in list(path):
                point_node = self._find_first_xml_child(command, "pt")
                if point_node is None:
                    continue
                try:
                    raw_x = float(point_node.attrib.get("x") or 0)
                    raw_y = float(point_node.attrib.get("y") or 0)
                except (TypeError, ValueError):
                    continue
                points.append(
                    self._map_docx_custom_point(
                        raw_x=raw_x,
                        raw_y=raw_y,
                        path_width=path_width,
                        path_height=path_height,
                        box=box,
                        transform=transform,
                    )
                )

        if not points:
            return None
        return (
            min(x for x, _y in points),
            min(y for _x, y in points),
            max(x for x, _y in points),
            max(y for _x, y in points),
        )

    def _draw_docx_composite_picture(
        self,
        *,
        canvas: Any,
        archive: zipfile.ZipFile,
        relationships: dict[str, str],
        picture_node: ET.Element,
        transform: dict[str, Any],
    ) -> None:
        try:
            from PIL import Image
        except Exception:
            return
        rel_id = None
        for node in picture_node.iter():
            if self._xml_local_name(node.tag) == "blip":
                rel_id = (
                    node.attrib.get(f"{{{OOXML_REL_NS}}}embed")
                    or node.attrib.get(f"{{{OOXML_REL_NS}}}link")
                    or node.attrib.get("embed")
                    or node.attrib.get("link")
                )
                break
        target = relationships.get(str(rel_id or ""))
        if not target:
            return
        xfrm = self._find_first_xml_descendant(picture_node, "xfrm")
        box = self._docx_xfrm_box(xfrm, transform=transform)
        if box is None:
            return
        left, upper, right, lower = self._normalize_pixel_box(box)
        width = max(1, right - left)
        height = max(1, lower - upper)
        try:
            with Image.open(BytesIO(archive.read(target))) as image:
                prepared = image.convert("RGBA").resize((width, height))
                canvas.alpha_composite(prepared, (left, upper))
        except Exception:
            return

    def _draw_docx_composite_shape(self, *, draw: Any, shape_node: ET.Element, transform: dict[str, Any]) -> None:
        xfrm = self._find_first_xml_descendant(shape_node, "xfrm")
        box = self._docx_xfrm_box(xfrm, transform=transform)
        if box is None:
            return
        left, upper, right, lower = self._normalize_pixel_box(box)
        outline = self._docx_shape_line_color(shape_node) or (0, 0, 0, 255)
        width = self._docx_shape_line_width(shape_node)
        fill = self._docx_shape_fill_color(shape_node)
        geometry = self._docx_shape_geometry(shape_node)
        if geometry == "line":
            draw.line(self._docx_line_points(shape_node=shape_node, box=(left, upper, right, lower)), fill=outline, width=width)
        elif geometry in {"rect", "roundRect"}:
            draw.rectangle((left, upper, right, lower), outline=outline, fill=fill, width=width)
        elif geometry in {"ellipse", "oval"}:
            draw.ellipse((left, upper, right, lower), outline=outline, fill=fill, width=width)
        elif geometry == "triangle":
            draw.polygon(((left + right) / 2, upper, right, lower, left, lower), outline=outline, fill=fill)
        else:
            self._draw_docx_custom_geometry(draw=draw, shape_node=shape_node, box=(left, upper, right, lower), transform=transform, outline=outline, width=width)

        text_lines = self._docx_shape_text_lines(shape_node)
        if text_lines:
            font = self._load_docx_composite_font(max(8, min(18, int((lower - upper) * 0.45) or 10)))
            line_height = max(8, int((lower - upper) / max(1, len(text_lines))))
            for line_index, text in enumerate(text_lines):
                draw.text((left, upper + line_index * line_height), text, fill=outline, font=font)

    def _docx_line_points(
        self,
        *,
        shape_node: ET.Element,
        box: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        left, upper, right, lower = box
        xfrm = self._find_first_xml_descendant(shape_node, "xfrm")
        flip_h = str((xfrm.attrib if xfrm is not None else {}).get("flipH") or "").strip() in {"1", "true"}
        flip_v = str((xfrm.attrib if xfrm is not None else {}).get("flipV") or "").strip() in {"1", "true"}
        start_x, start_y = left, upper
        end_x, end_y = right, lower
        if flip_h:
            start_x, end_x = end_x, start_x
        if flip_v:
            start_y, end_y = end_y, start_y
        return start_x, start_y, end_x, end_y

    def _docx_shape_text_lines(self, shape_node: ET.Element) -> list[str]:
        text_container = None
        for node in shape_node.iter():
            if self._xml_local_name(node.tag) in {"txBody", "txbxContent"}:
                text_container = node
                break
        if text_container is None:
            text = self._xml_text_content(shape_node)
            return [text] if text else []

        lines: list[str] = []
        for paragraph in (node for node in text_container.iter() if self._xml_local_name(node.tag) == "p"):
            fragments = [
                "".join(text_node.itertext())
                for text_node in paragraph.iter()
                if self._xml_local_name(text_node.tag) == "t"
            ]
            line = "".join(fragments).strip()
            if line:
                lines.append(line)
        if lines:
            return lines
        text = self._xml_text_content(text_container)
        return [text] if text else []

    def _draw_docx_custom_geometry(
        self,
        *,
        draw: Any,
        shape_node: ET.Element,
        box: tuple[int, int, int, int],
        transform: dict[str, Any],
        outline: tuple[int, int, int, int],
        width: int,
    ) -> None:
        paths = [node for node in shape_node.iter() if self._xml_local_name(node.tag) == "path"]
        if not paths:
            left, upper, right, lower = box
            if right > left and lower > upper:
                draw.rectangle((left, upper, right, lower), outline=outline, width=width)
            return
        for path in paths:
            try:
                path_width = max(1.0, float(path.attrib.get("w") or 1.0))
                path_height = max(1.0, float(path.attrib.get("h") or 1.0))
            except (TypeError, ValueError):
                path_width, path_height = 1.0, 1.0
            points: list[tuple[float, float]] = []
            start_point: tuple[float, float] | None = None
            for command in list(path):
                command_name = self._xml_local_name(command.tag)
                if command_name == "close":
                    if start_point is not None and points:
                        points.append(start_point)
                    continue
                point_node = self._find_first_xml_child(command, "pt")
                if point_node is None:
                    continue
                try:
                    raw_x = float(point_node.attrib.get("x") or 0)
                    raw_y = float(point_node.attrib.get("y") or 0)
                except (TypeError, ValueError):
                    continue
                point = self._map_docx_custom_point(
                    raw_x=raw_x,
                    raw_y=raw_y,
                    path_width=path_width,
                    path_height=path_height,
                    box=box,
                    transform=transform,
                )
                if command_name == "moveTo":
                    if len(points) > 1:
                        draw.line(points, fill=outline, width=width)
                    points = [point]
                    start_point = point
                elif command_name == "lnTo":
                    points.append(point)
            if len(points) > 1:
                draw.line(points, fill=outline, width=width)

    def _map_docx_custom_point(
        self,
        *,
        raw_x: float,
        raw_y: float,
        path_width: float,
        path_height: float,
        box: tuple[int, int, int, int],
        transform: dict[str, Any],
    ) -> tuple[float, float]:
        outside_local_path = raw_x < 0 or raw_y < 0 or raw_x > path_width * 1.25 or raw_y > path_height * 1.25
        if outside_local_path:
            return transform["map_point"](raw_x, raw_y)
        left, upper, right, lower = box
        return (
            left + raw_x / path_width * max(1, right - left),
            upper + raw_y / path_height * max(1, lower - upper),
        )

    def _docx_xfrm_box(self, xfrm: ET.Element | None, *, transform: dict[str, Any]) -> tuple[float, float, float, float] | None:
        if xfrm is None:
            return None
        off = self._find_first_xml_child(xfrm, "off")
        ext = self._find_first_xml_child(xfrm, "ext")
        if off is None or ext is None:
            return None
        try:
            off_x = float(off.attrib.get("x") or 0.0)
            off_y = float(off.attrib.get("y") or 0.0)
            ext_x = float(ext.attrib.get("cx") or 0.0)
            ext_y = float(ext.attrib.get("cy") or 0.0)
        except (TypeError, ValueError):
            return None
        return transform["map_box"](off_x, off_y, ext_x, ext_y)

    def _normalize_pixel_box(self, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, upper, right, lower = box
        normalized_left = int(round(min(left, right)))
        normalized_right = int(round(max(left, right)))
        normalized_upper = int(round(min(upper, lower)))
        normalized_lower = int(round(max(upper, lower)))
        return normalized_left, normalized_upper, normalized_right, normalized_lower

    def _docx_group_name(self, group_node: ET.Element) -> str | None:
        for node in group_node.iter():
            if self._xml_local_name(node.tag) in {"cNvPr", "docPr"}:
                name = str(node.attrib.get("name") or "").strip()
                if name:
                    return name
        return None

    def _docx_shape_geometry(self, node: ET.Element) -> str:
        for child in node.iter():
            if self._xml_local_name(child.tag) == "prstGeom":
                return str(child.attrib.get("prst") or "").strip()
        if any(self._xml_local_name(child.tag) == "custGeom" for child in node.iter()):
            return "custom"
        return "rect"

    def _docx_shape_line_color(self, node: ET.Element) -> tuple[int, int, int, int] | None:
        line_node = self._find_first_xml_descendant(node, "ln")
        if line_node is None:
            return None
        if any(self._xml_local_name(child.tag) == "noFill" for child in list(line_node)):
            return None
        return self._first_srgb_color(line_node) or (0, 0, 0, 255)

    def _docx_shape_fill_color(self, node: ET.Element) -> tuple[int, int, int, int] | None:
        sp_pr = self._find_first_xml_descendant(node, "spPr")
        if sp_pr is None:
            return None
        for child in list(sp_pr):
            if self._xml_local_name(child.tag) == "noFill":
                return None
            if self._xml_local_name(child.tag) == "solidFill":
                return self._first_srgb_color(child)
        return None

    def _first_srgb_color(self, node: ET.Element) -> tuple[int, int, int, int] | None:
        for child in node.iter():
            if self._xml_local_name(child.tag) != "srgbClr":
                continue
            value = str(child.attrib.get("val") or "").strip()
            if re.fullmatch(r"[0-9a-fA-F]{6}", value):
                return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)
        return None

    def _docx_shape_line_width(self, node: ET.Element) -> int:
        line_node = self._find_first_xml_descendant(node, "ln")
        if line_node is None:
            return 1
        try:
            return max(1, min(8, int(round(float(line_node.attrib.get("w") or OOXML_EMU_PER_PIXEL) / OOXML_EMU_PER_PIXEL))))
        except (TypeError, ValueError):
            return 1

    def _load_docx_composite_font(self, size: int) -> Any:
        try:
            from PIL import ImageFont

            for candidate in (
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            ):
                if Path(candidate).exists():
                    return ImageFont.truetype(candidate, size=size)
            return ImageFont.load_default()
        except Exception:
            return None

    def _looks_like_docx_heading_text(self, text: str | None) -> bool:
        normalized = " ".join(str(text or "").split())
        if not normalized or len(normalized) > 180:
            return False
        if re.match(r"^第[一二三四五六七八九十百]+章", normalized):
            return True
        if re.match(r"^\d+(?:\.\d+){0,4}\s+[^\d\s]", normalized):
            return True
        if re.match(r"^\d+[、.]\s*[^\d\s]", normalized):
            return True
        return self._docx_composite_heading_has_figure_intent(normalized)

    def _docx_composite_heading_has_figure_intent(self, text: Any) -> bool:
        lowered = str(text or "").strip().lower()
        return bool(lowered) and any(marker in lowered for marker in COMPOSITE_FIGURE_TITLE_MARKERS)

    def _xml_text_content(self, node: ET.Element) -> str:
        return " ".join("".join(node.itertext()).split())

    def _xml_local_name(self, tag: Any) -> str:
        raw = str(tag or "")
        if "}" in raw:
            return raw.rsplit("}", 1)[-1]
        return raw

    def _find_first_xml_descendant(self, node: ET.Element, local_name: str) -> ET.Element | None:
        for child in node.iter():
            if self._xml_local_name(child.tag) == local_name:
                return child
        return None

    def _find_first_xml_child(self, node: ET.Element, local_name: str) -> ET.Element | None:
        for child in list(node):
            if self._xml_local_name(child.tag) == local_name:
                return child
        return None

    def _repair_from_docx_media(
        self,
        assets: list[ParsedAsset],
        *,
        missing_indexes: list[int],
        effective_path: Path,
        stats: dict[str, Any],
    ) -> list[int]:
        if effective_path.suffix.lower() != ".docx" or not missing_indexes:
            return missing_indexes

        media_items = self._extract_docx_media_items(effective_path)
        if not media_items:
            return missing_indexes

        raster_items = [item for item in media_items if item["extension"] in DOCX_RASTER_MEDIA_EXTENSIONS]
        vector_items = [item for item in media_items if item["extension"] in DOCX_VECTOR_MEDIA_EXTENSIONS]
        unsupported_items = [
            item
            for item in media_items
            if item["extension"] not in DOCX_RASTER_MEDIA_EXTENSIONS and item["extension"] not in DOCX_VECTOR_MEDIA_EXTENSIONS
        ]
        stats["asset_repair_vector_media_count"] = len(vector_items)
        stats["asset_repair_unsupported_media_count"] = len(unsupported_items)
        if media_items:
            stats["asset_repair_docx_media_count"] = len(media_items)
            stats["asset_repair_docx_media_extensions"] = sorted({item["extension"].lstrip(".") for item in media_items})

        remaining: list[int] = []
        raster_iter = iter(raster_items)
        for index in missing_indexes:
            media_item = next(raster_iter, None)
            if media_item is None:
                remaining.append(index)
                continue
            asset = assets[index]
            image_bytes, image_ext, image_size = self._prepare_repaired_image_bytes(
                bytes(media_item["bytes"]),
                source_ext=str(media_item["extension"]),
            )
            asset.image_bytes = image_bytes
            asset.image_ext = image_ext
            asset.meta = {
                **(asset.meta or {}),
                "asset_repair_method": "docx_embedded_raster_media",
                "asset_repair_source": media_item["name"],
                "storage_fallback": False,
                "review_required": True,
            }
            if image_size is not None:
                asset.meta["width"] = image_size[0]
                asset.meta["height"] = image_size[1]
            self._record_asset_repair_method(stats, "docx_embedded_raster_media")
            stats["asset_repair_raster_media_success_count"] += 1
        return remaining

    def _extract_docx_media_items(self, path: Path) -> list[dict[str, Any]]:
        try:
            with zipfile.ZipFile(path) as archive:
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().startswith("word/media/")
                    and Path(name).suffix.lower()
                    and not name.endswith("/")
                )
                return [
                    {
                        "name": name,
                        "extension": Path(name).suffix.lower(),
                        "bytes": archive.read(name),
                    }
                    for name in names
                ]
        except (OSError, zipfile.BadZipFile, KeyError):
            return []

    def _repair_from_office_html_media(
        self,
        assets: list[ParsedAsset],
        *,
        missing_indexes: list[int],
        source_path: Path,
        effective_path: Path,
        stats: dict[str, Any],
    ) -> list[int]:
        if not missing_indexes:
            return []

        media_items: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for candidate_path in (source_path, effective_path):
            candidate_key = str(candidate_path)
            if candidate_key in seen_paths:
                continue
            seen_paths.add(candidate_key)
            media_items = self._extract_office_html_media_items(candidate_path)
            if media_items:
                stats["asset_repair_html_media_count"] = len(media_items)
                stats["asset_repair_html_media_extensions"] = sorted({item["extension"].lstrip(".") for item in media_items})
                break
        if not media_items:
            return missing_indexes

        remaining: list[int] = []
        media_iter = iter(media_items)
        for index in missing_indexes:
            media_item = next(media_iter, None)
            if media_item is None:
                remaining.append(index)
                continue
            image_bytes, image_ext, image_size = self._prepare_repaired_image_bytes(
                bytes(media_item["bytes"]),
                source_ext=str(media_item["extension"]),
            )
            if not image_bytes:
                remaining.append(index)
                continue
            asset = assets[index]
            asset.image_bytes = image_bytes
            asset.image_ext = image_ext
            asset.meta = {
                **(asset.meta or {}),
                "asset_repair_method": "libreoffice_html_media",
                "asset_repair_source": media_item["name"],
                "asset_repair_precision": "embedded_media_export",
                "storage_fallback": False,
                "review_required": True,
            }
            if image_size is not None:
                asset.meta["width"] = image_size[0]
                asset.meta["height"] = image_size[1]
            self._record_asset_repair_method(stats, "libreoffice_html_media")
            stats["asset_repair_html_media_success_count"] += 1
        return remaining

    def _extract_office_html_media_items(self, path: Path) -> list[dict[str, Any]]:
        if not self.resolved_libreoffice_cmd:
            return []
        html_path = self._convert_office_document(path, target_ext=".html", convert_to="html")
        if html_path is None:
            return []
        directory = html_path.parent
        media_paths = sorted(
            candidate
            for candidate in directory.iterdir()
            if candidate.is_file()
            and candidate != html_path
            and candidate.suffix.lower() in OFFICE_HTML_IMAGE_EXTENSIONS
        )
        media_items: list[dict[str, Any]] = []
        for media_path in media_paths:
            try:
                media_items.append(
                    {
                        "name": media_path.name,
                        "extension": media_path.suffix.lower(),
                        "bytes": media_path.read_bytes(),
                    }
                )
            except OSError:
                continue
        return media_items

    def _prepare_repaired_image_bytes(
        self,
        image_bytes: bytes,
        *,
        source_ext: str,
    ) -> tuple[bytes | None, str, tuple[int, int] | None]:
        normalized_ext = source_ext.lower() if source_ext.startswith(".") else f".{source_ext.lower()}"
        if normalized_ext not in {".gif", ".bmp", ".tif", ".tiff"}:
            return image_bytes, normalized_ext or ".png", self._read_image_size(image_bytes)
        try:
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as image:
                prepared = self._remove_dominant_magenta_background(image.convert("RGBA"))
                handle = BytesIO()
                prepared.save(handle, format="PNG")
                return handle.getvalue(), ".png", prepared.size
        except Exception:
            return image_bytes, normalized_ext or ".png", self._read_image_size(image_bytes)

    def _read_image_size(self, image_bytes: bytes) -> tuple[int, int] | None:
        try:
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as image:
                return image.size
        except Exception:
            return None

    def _remove_dominant_magenta_background(self, image: Any) -> Any:
        try:
            raw_pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
            pixels = list(raw_pixels)
            if not pixels:
                return image
            magenta_count = sum(1 for red, green, blue, _alpha in pixels if red >= 240 and green <= 30 and blue >= 240)
            if magenta_count / len(pixels) < 0.15:
                return image
            cleaned = image.copy()
            cleaned.putdata(
                [
                    (255, 255, 255, 0) if red >= 240 and green <= 30 and blue >= 240 else (red, green, blue, alpha)
                    for red, green, blue, alpha in pixels
                ]
            )
            alpha = cleaned.getchannel("A")
            bbox = alpha.getbbox()
            if bbox:
                return cleaned.crop(bbox)
            return cleaned
        except Exception:
            return image

    def _repair_from_pdf_render(
        self,
        assets: list[ParsedAsset],
        *,
        missing_indexes: list[int],
        source_path: Path,
        effective_path: Path,
        stats: dict[str, Any],
    ) -> list[int]:
        if not missing_indexes:
            return []

        pdf_path = effective_path if effective_path.suffix.lower() == ".pdf" else None
        if pdf_path is None:
            pdf_path = self._convert_office_document_to_pdf(source_path)
        if pdf_path is None and effective_path != source_path:
            pdf_path = self._convert_office_document_to_pdf(effective_path)
        if pdf_path is None:
            stats["asset_repair_pdf_render_error"] = "pdf_conversion_unavailable"
            return missing_indexes

        page_count = self._get_pdf_page_count(pdf_path)
        if page_count <= 0:
            stats["asset_repair_pdf_render_error"] = "pdf_has_no_renderable_pages"
            return missing_indexes
        stats["asset_repair_pdf_page_count"] = page_count

        unresolved: list[int] = []
        fallback_page_no = 1
        for index in missing_indexes:
            asset = assets[index]
            image = None
            method = ""
            page_no = asset.page_no
            bbox = asset.bbox if isinstance(asset.bbox, dict) else None
            page_size = self._asset_page_size(asset)
            if page_no is not None and bbox and page_size:
                image = self._render_pdf_crop(pdf_path, page_no=page_no, bbox=bbox, page_size=page_size)
                method = "pdf_bbox_crop"
            if image is None:
                selected_page = self._clamp_page_no(page_no or fallback_page_no, page_count=page_count)
                image = self._render_pdf_page_candidate(pdf_path, page_no=selected_page)
                page_no = selected_page
                method = "pdf_page_render_candidate"
                fallback_page_no = min(page_count, selected_page + 1)
            image_bytes = self._to_png_bytes(image)
            if not image_bytes:
                unresolved.append(index)
                continue
            asset.image_bytes = image_bytes
            asset.image_ext = ".png"
            asset.page_no = page_no
            asset.meta = {
                **(asset.meta or {}),
                "asset_repair_method": method,
                "asset_repair_source": str(pdf_path),
                "asset_repair_precision": "bbox" if method == "pdf_bbox_crop" else "page_candidate",
                "storage_fallback": False,
                "review_required": True,
            }
            if method == "pdf_page_render_candidate":
                quality_flags = list(asset.meta.get("quality_flags") or [])
                if "pdf_page_render_candidate_requires_review" not in quality_flags:
                    quality_flags.append("pdf_page_render_candidate_requires_review")
                asset.meta["quality_flags"] = quality_flags
            self._record_asset_repair_method(stats, method)
            stats["asset_repair_pdf_render_success_count"] += 1
        return unresolved

    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        try:
            import pypdfium2 as pdfium  # type: ignore[import-not-found]

            pdf = pdfium.PdfDocument(str(pdf_path))
            return len(pdf)
        except Exception:
            return 0

    def _render_pdf_crop(
        self,
        pdf_path: Path,
        *,
        page_no: int,
        bbox: dict[str, Any],
        page_size: tuple[float, float],
    ) -> Any | None:
        image = self._render_pdf_page_image(pdf_path, page_no=page_no)
        if image is None:
            return None
        page_width, page_height = page_size
        if page_width <= 0 or page_height <= 0:
            return None
        try:
            left_value = float(bbox.get("l") or 0.0)
            right_value = float(bbox.get("r") or 0.0)
            top_value = float(bbox.get("t") or 0.0)
            bottom_value = float(bbox.get("b") or 0.0)
        except (TypeError, ValueError):
            return None
        scale_x = image.size[0] / page_width
        scale_y = image.size[1] / page_height
        left = max(0, int(left_value * scale_x))
        right = min(image.size[0], int(right_value * scale_x))
        upper = max(0, int(image.size[1] - top_value * scale_y))
        lower = min(image.size[1], int(image.size[1] - bottom_value * scale_y))
        if right <= left or lower <= upper:
            return None
        return image.crop((left, upper, right, lower))

    def _render_pdf_page_candidate(self, pdf_path: Path, *, page_no: int) -> Any | None:
        image = self._render_pdf_page_image(pdf_path, page_no=page_no)
        if image is None:
            return None
        return self._trim_page_candidate(image)

    def _render_pdf_page_image(self, pdf_path: Path, *, page_no: int, scale: float = 2.0) -> Any | None:
        try:
            import pypdfium2 as pdfium  # type: ignore[import-not-found]

            pdf = pdfium.PdfDocument(str(pdf_path))
            if len(pdf) <= 0:
                return None
            page_index = self._clamp_page_no(page_no, page_count=len(pdf)) - 1
            page = pdf[page_index]
            bitmap = page.render(scale=scale)
            return bitmap.to_pil()
        except Exception:
            return None

    def _trim_page_candidate(self, image: Any) -> Any:
        try:
            from PIL import ImageChops

            background = image.convert("RGB").point(lambda _value: 255)
            diff = ImageChops.difference(image.convert("RGB"), background)
            bbox = diff.getbbox()
            if not bbox:
                return image
            margin = 24
            left = max(0, bbox[0] - margin)
            upper = max(0, bbox[1] - margin)
            right = min(image.size[0], bbox[2] + margin)
            lower = min(image.size[1], bbox[3] + margin)
            if right <= left or lower <= upper:
                return image
            return image.crop((left, upper, right, lower))
        except Exception:
            return image

    def _asset_page_size(self, asset: ParsedAsset) -> tuple[float, float] | None:
        metadata = asset.meta or {}
        try:
            page_width = float(metadata.get("page_width") or 0)
            page_height = float(metadata.get("page_height") or 0)
        except (TypeError, ValueError):
            return None
        if page_width <= 0 or page_height <= 0:
            return None
        return page_width, page_height

    def _clamp_page_no(self, page_no: int, *, page_count: int) -> int:
        try:
            normalized = int(page_no)
        except (TypeError, ValueError):
            normalized = 1
        return max(1, min(max(page_count, 1), normalized))

    def _record_asset_repair_method(self, stats: dict[str, Any], method: str) -> None:
        methods = list(stats.get("asset_repair_methods") or [])
        if method not in methods:
            methods.append(method)
        stats["asset_repair_methods"] = methods

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

    def _build_converter(self, suffix: str, *, include_assets: bool = True) -> DocumentConverter:
        if suffix != ".pdf" or InputFormat is None or PdfPipelineOptions is None or PdfFormatOption is None:
            return DocumentConverter()

        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_page_images = bool(include_assets)
        pipeline_options.generate_picture_images = bool(include_assets)
        pipeline_options.images_scale = 2.0 if include_assets else 1.0
        if hasattr(pipeline_options, "generate_table_images"):
            pipeline_options.generate_table_images = bool(include_assets)
        if get_settings is not None:
            artifacts_path = str(get_settings().docling_artifacts_path or "").strip()
            if artifacts_path:
                pipeline_options.artifacts_path = artifacts_path
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
            quality_flags = []
            preserve_in_vector_db = True
            if asset_type == "figure" and visual_role == "asset_fragment":
                quality_flags.append("tiny_visual_fragment")
                preserve_in_vector_db = False

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
                        "quality_flags": quality_flags,
                        "preserve_in_vector_db": preserve_in_vector_db,
                    },
                )
            )

        return assets

    def _extract_structure_hints(self, items: list[tuple[Any, int]], *, markdown: str = "") -> dict[str, Any]:
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
        structure = {
            "heading_hints": heading_hints,
        }
        normalized_markdown = str(markdown or "").strip()
        if normalized_markdown:
            catalog = build_section_catalog(normalized_markdown, structure_hints=structure)
            sections = catalog.get("sections") or []
            if sections:
                structure["document_title"] = catalog.get("document_title")
                structure["section_catalog"] = sections
        return structure

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

        if self._looks_like_page_furniture_asset(
            bbox=bbox,
            page_size=page_size,
            image_size=image_size,
        ):
            return "page_furniture"

        if self._looks_like_tiny_visual_fragment(
            bbox=bbox,
            page_size=page_size,
            image_size=image_size,
        ):
            return "asset_fragment"

        if any(marker in joined_context for marker in LAYOUT_DRAWING_MARKERS):
            return "layout_drawing"

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

    def _looks_like_tiny_visual_fragment(
        self,
        *,
        bbox: Any | None,
        page_size: tuple[float, float] | None,
        image_size: tuple[int, int] | None,
    ) -> bool:
        if image_size is not None:
            width, height = int(image_size[0] or 0), int(image_size[1] or 0)
            if width <= 0 or height <= 0:
                return False
            area = width * height
            if min(width, height) < MIN_REUSABLE_FIGURE_DIMENSION:
                return True
            if area < MIN_REUSABLE_FIGURE_AREA and max(width, height) < MIN_REUSABLE_FIGURE_DIMENSION * 2:
                return True

        if bbox is None or page_size is None:
            return False

        page_width, page_height = page_size
        if page_width <= 0 or page_height <= 0:
            return False
        try:
            box_width = max(0.0, float(bbox.r) - float(bbox.l))
            box_height = max(0.0, float(bbox.t) - float(bbox.b))
        except (TypeError, ValueError):
            return False
        if box_width <= 0 or box_height <= 0:
            return False

        box_area = box_width * box_height
        page_area = page_width * page_height
        return box_area <= page_area * 0.005 and min(box_width, box_height) <= min(page_width, page_height) * 0.08

    def _looks_like_page_furniture_asset(
        self,
        *,
        bbox: Any | None,
        page_size: tuple[float, float] | None,
        image_size: tuple[int, int] | None,
    ) -> bool:
        if bbox is None or page_size is None:
            return False

        page_width, page_height = page_size
        if page_width <= 0 or page_height <= 0:
            return False

        box_width = max(0.0, float(bbox.r) - float(bbox.l))
        box_height = max(0.0, float(bbox.t) - float(bbox.b))
        if box_width <= 0 or box_height <= 0:
            return False

        near_top = float(bbox.t) >= page_height * 0.88
        near_bottom = float(bbox.b) <= page_height * 0.12
        narrow_band = box_height <= page_height * 0.12
        slim_band = box_height <= page_height * 0.08
        small_area = box_width * box_height <= page_width * page_height * 0.02
        wide_banner = box_width >= box_height * 1.6
        small_image = image_size is not None and image_size[0] * image_size[1] <= 40000

        return (near_top or near_bottom) and narrow_band and (slim_band or small_area or wide_banner or small_image)

    def _to_png_bytes(self, image: Any | None) -> bytes | None:
        if image is None:
            return None
        handle = BytesIO()
        image.save(handle, format="PNG")
        return handle.getvalue()
