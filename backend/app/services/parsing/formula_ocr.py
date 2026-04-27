from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any, Callable, Literal

from app.config import get_settings
from app.services.parsing.docling_parser import ParsedAsset
from app.services.parsing.formula_candidates import (
    extract_formula_candidates,
    has_garbled_formula_text,
    is_formula_like_text,
)
from app.services.parsing.formula_regions import FormulaRegion, propose_formula_regions

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional runtime dependency
    Image = None

try:
    from pix2tex.cli import LatexOCR
except ImportError:  # pragma: no cover - optional runtime dependency
    LatexOCR = None


FORMULA_ASSET_HINT_PATTERN = (
    "公式",
    "equation",
    "latex",
    "波形",
    "曲线",
    "电压",
    "电流",
    "fft",
    "bode",
)

FORMULA_EXPLICIT_HINT_PATTERN = (
    "公式",
    "equation",
    "latex",
)

SCHEMATIC_HINT_PATTERN = (
    "原理图",
    "接线图",
    "示意图",
    "schematic",
    "circuit",
    "diagram",
)


@dataclass
class FormulaOCRResult:
    source_type: Literal["asset", "text_line"]
    status: Literal["succeeded", "skipped", "unavailable", "error", "manual_review"]
    backend_requested: str
    backend_used: str
    page_no: int | None = None
    line_number: int | None = None
    heading_path: str | None = None
    input_preview: str | None = None
    source_ref: str | None = None
    latex: str | None = None
    quality: Literal["likely_formula", "uncertain"] | None = None
    reason: str | None = None
    linked_line_numbers: list[int] = field(default_factory=list)
    bbox: dict[str, Any] | None = None
    crop_bbox: dict[str, Any] | None = None
    crop_index: int | None = None
    crop_strategy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FormulaOCRService:
    def __init__(
        self,
        *,
        backend: str | None = None,
        max_assets: int | None = None,
        max_regions_per_asset: int | None = None,
        predictor: Callable[[Any], str] | None = None,
    ) -> None:
        settings = get_settings()
        self.backend = backend or settings.formula_ocr_backend
        self.max_assets = max_assets if max_assets is not None else settings.formula_ocr_max_assets
        self.max_regions_per_asset = (
            max_regions_per_asset if max_regions_per_asset is not None else settings.formula_ocr_max_regions_per_asset
        )
        self._predictor = predictor
        self._predictor_loaded = predictor is not None

    def analyze(self, *, markdown: str, assets: list[ParsedAsset]) -> list[FormulaOCRResult]:
        text_candidates = extract_formula_candidates(markdown)
        results: list[FormulaOCRResult] = [
            self._build_text_only_result(candidate.line_number, candidate.text)
            for candidate in text_candidates
            if candidate.garbled
        ]

        asset_candidates = self._select_asset_candidates(assets=assets, text_candidates=text_candidates)
        for asset in asset_candidates[: self.max_assets]:
            results.append(self._run_asset_ocr(asset, text_candidates=text_candidates))
        return results

    def _build_text_only_result(self, line_number: int, text: str) -> FormulaOCRResult:
        return FormulaOCRResult(
            source_type="text_line",
            status="manual_review",
            backend_requested=self.backend,
            backend_used="none",
            line_number=line_number,
            input_preview=text,
            reason="Garbled formula-like text was detected in markdown, but there is no reliable image region to OCR automatically.",
        )

    def _select_asset_candidates(
        self,
        *,
        assets: list[ParsedAsset],
        text_candidates: list[Any],
    ) -> list[ParsedAsset]:
        ranked_assets: list[tuple[int, ParsedAsset]] = []
        for asset in assets:
            if asset.asset_type == "table" or not asset.image_bytes:
                continue

            joined_text = self._asset_text(asset)
            visual_role = str(asset.meta.get("visual_role") or "")
            if visual_role in {"asset_fragment", "text_fragment", "page_furniture", "product_photo"}:
                continue
            if str(asset.meta.get("asset_audit_status") or "").strip().lower() == "rejected":
                continue
            if asset.meta.get("preserve_in_vector_db") is False:
                continue
            score = 0
            if visual_role == "engineering_figure":
                score += 1
            if self._contains_formula_hint(joined_text):
                score += 3
            if self._contains_schematic_hint(joined_text):
                score += 1
            if has_garbled_formula_text(joined_text):
                score += 4
            if is_formula_like_text(joined_text):
                score += 2
            if self._linked_line_numbers(asset, text_candidates):
                score += 3
            if score <= 0:
                continue
            ranked_assets.append((score, asset))

        ranked_assets.sort(
            key=lambda item: (
                -item[0],
                item[1].page_no or 0,
                -(int(item[1].meta.get("width") or 0) * int(item[1].meta.get("height") or 0)),
            )
        )
        return [asset for _score, asset in ranked_assets]

    def _run_asset_ocr(self, asset: ParsedAsset, *, text_candidates: list[Any]) -> FormulaOCRResult:
        linked_lines = self._linked_line_numbers(asset, text_candidates)
        explicit_formula_context = self._contains_explicit_formula_hint(self._asset_text(asset))
        base = FormulaOCRResult(
            source_type="asset",
            status="skipped",
            backend_requested=self.backend,
            backend_used="none",
            page_no=asset.page_no,
            heading_path=asset.heading_path,
            input_preview=self._asset_text(asset),
            source_ref=asset.source_ref,
            linked_line_numbers=linked_lines,
            bbox=asset.bbox,
        )

        if self.backend == "none":
            base.reason = "Formula OCR backend is disabled."
            return base

        predictor = self._get_predictor()
        if predictor is None:
            base.status = "unavailable"
            base.reason = f"Formula OCR backend `{self.backend}` is not installed or could not be loaded."
            return base

        if Image is None:
            base.status = "unavailable"
            base.reason = "Pillow is not installed, so image assets cannot be prepared for OCR."
            return base

        try:
            image = Image.open(BytesIO(asset.image_bytes))
        except Exception as exc:  # pragma: no cover - depends on runtime model
            base.status = "error"
            base.backend_used = self.backend
            base.reason = f"Image preparation failed before OCR: {exc}"
            return base

        regions = propose_formula_regions(
            image,
            max_regions=self.max_regions_per_asset,
            explicit_formula_context=explicit_formula_context,
            prefer_top=bool(linked_lines),
        )
        attempts: list[FormulaOCRResult] = []
        for region in regions[: self.max_regions_per_asset]:
            attempts.append(
                self._run_crop_ocr(
                    predictor=predictor,
                    image=image,
                    asset=asset,
                    region=region,
                    base=base,
                )
            )

        best = self._pick_best_result(attempts)
        if best is None:
            base.status = "error"
            base.backend_used = self.backend
            base.reason = "OCR did not yield any crop result."
            return base
        return best

    def _get_predictor(self) -> Callable[[Any], str] | None:
        if self._predictor_loaded:
            return self._predictor
        self._predictor_loaded = True
        if self.backend != "pix2tex" or LatexOCR is None:
            self._predictor = None
            return None
        try:
            model = LatexOCR()
        except Exception:  # pragma: no cover - depends on runtime model downloads
            self._predictor = None
            return None

        def predict(image: Any) -> str:
            return str(model(image))

        self._predictor = predict
        return self._predictor

    def _asset_text(self, asset: ParsedAsset) -> str:
        return " | ".join(
            value
            for value in [
                asset.heading_path,
                asset.caption,
                asset.context_before,
                asset.context_after,
            ]
            if isinstance(value, str) and value.strip()
        )

    def _contains_formula_hint(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in FORMULA_ASSET_HINT_PATTERN)

    def _contains_schematic_hint(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in SCHEMATIC_HINT_PATTERN)

    def _linked_line_numbers(self, asset: ParsedAsset, text_candidates: list[Any]) -> list[int]:
        joined_text = self._asset_text(asset)
        if not joined_text:
            return []
        linked: list[int] = []
        for candidate in text_candidates:
            if candidate.text in joined_text:
                linked.append(candidate.line_number)
                continue
            if candidate.garbled and has_garbled_formula_text(joined_text):
                linked.append(candidate.line_number)
        return linked

    def _normalize_latex(self, latex: str | None) -> str | None:
        if not latex:
            return None
        normalized = " ".join(str(latex).split())
        return normalized or None

    def _run_crop_ocr(
        self,
        *,
        predictor: Callable[[Any], str],
        image: Any,
        asset: ParsedAsset,
        region: FormulaRegion,
        base: FormulaOCRResult,
    ) -> FormulaOCRResult:
        crop = image.crop((region.left, region.top, region.right, region.bottom))
        result = FormulaOCRResult(
            source_type="asset",
            status="error",
            backend_requested=base.backend_requested,
            backend_used=self.backend,
            page_no=base.page_no,
            heading_path=base.heading_path,
            input_preview=base.input_preview,
            source_ref=base.source_ref,
            linked_line_numbers=list(base.linked_line_numbers),
            bbox=base.bbox,
            crop_bbox=region.to_dict(),
            crop_index=region.crop_index,
            crop_strategy=region.strategy,
        )
        try:
            latex = predictor(crop)
        except Exception as exc:  # pragma: no cover - depends on runtime model
            result.reason = f"OCR execution failed on crop {region.crop_index}: {exc}"
            return result

        normalized = self._normalize_latex(latex)
        if not normalized:
            result.reason = "OCR returned an empty formula candidate for this crop."
            return result

        result.latex = normalized
        result.quality = self._classify_formula_output(
            normalized,
            input_preview=result.input_preview or "",
        )
        if result.quality == "likely_formula":
            result.status = "succeeded"
            result.reason = "OCR output should still be reviewed against the source figure."
            return result

        result.status = "manual_review"
        result.reason = "OCR returned LaTeX-like text for this crop, but the result does not yet look trustworthy for automatic use."
        return result

    def _pick_best_result(self, attempts: list[FormulaOCRResult]) -> FormulaOCRResult | None:
        if not attempts:
            return None
        return sorted(
            attempts,
            key=lambda item: (
                0 if item.status == "succeeded" else 1 if item.status == "manual_review" else 2,
                0 if item.quality == "likely_formula" else 1,
                len(item.latex or ""),
                item.crop_index or 0,
            ),
        )[0]

    def _classify_formula_output(self, latex: str, *, input_preview: str) -> Literal["likely_formula", "uncertain"]:
        explicit_formula_context = self._contains_explicit_formula_hint(input_preview)
        if len(latex) > 180 and not explicit_formula_context:
            return "uncertain"
        if latex.count("\\") >= 12 and not explicit_formula_context:
            return "uncertain"
        if any(token in latex for token in ("\\begin{array}", "\\mathbb", "\\overline", "\\underbrace")) and not explicit_formula_context:
            return "uncertain"

        math_tokens = ("\\", "^", "_", "=", "\\frac", "\\sum", "\\sqrt", "\\alpha", "\\beta")
        if any(token in latex for token in math_tokens) or is_formula_like_text(latex):
            return "likely_formula"
        return "uncertain"

    def _contains_explicit_formula_hint(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in FORMULA_EXPLICIT_HINT_PATTERN)
