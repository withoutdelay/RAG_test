from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    import cv2
except ImportError:  # pragma: no cover - optional runtime dependency
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional runtime dependency
    np = None


@dataclass(frozen=True)
class FormulaRegion:
    crop_index: int
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    score: float
    strategy: Literal["component", "row_band", "fallback"]

    def to_dict(self) -> dict[str, int | float | str]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
            "score": round(self.score, 3),
            "strategy": self.strategy,
        }


def propose_formula_regions(
    image: object,
    *,
    max_regions: int = 3,
    explicit_formula_context: bool = False,
    prefer_top: bool = False,
) -> list[FormulaRegion]:
    if np is None or cv2 is None or not hasattr(image, "size") or not hasattr(image, "convert"):
        return [_full_image_region(image)]

    width, height = image.size
    if width < 64 or height < 32:
        return [_full_image_region(image)]

    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _threshold, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_w = max(9, min(41, width // 18))
    kernel_h = max(3, min(9, height // 45))
    kernel = np.ones((kernel_h, kernel_w), dtype=np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    proposals = _component_regions(
        closed,
        width=width,
        height=height,
        explicit_formula_context=explicit_formula_context,
        prefer_top=prefer_top,
    )
    if not proposals:
        proposals = _row_band_regions(
            closed,
            width=width,
            height=height,
            explicit_formula_context=explicit_formula_context,
            prefer_top=prefer_top,
        )
    if not proposals:
        return [_full_image_region(image)]

    proposals = _dedupe_regions(proposals)
    proposals.sort(key=lambda item: (-item.score, item.top, item.left))
    limited = proposals[:max_regions]
    return [
        FormulaRegion(
            crop_index=index,
            left=item.left,
            top=item.top,
            right=item.right,
            bottom=item.bottom,
            width=item.width,
            height=item.height,
            score=item.score,
            strategy=item.strategy,
        )
        for index, item in enumerate(limited, start=1)
    ]


def _component_regions(
    binary: object,
    *,
    width: int,
    height: int,
    explicit_formula_context: bool,
    prefer_top: bool,
) -> list[FormulaRegion]:
    proposals: list[FormulaRegion] = []
    _num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    min_area = max(180, int(width * height * 0.003))

    for component_index in range(1, stats.shape[0]):
        x, y, box_width, box_height, area = stats[component_index]
        if area < min_area:
            continue
        if box_width < max(60, width // 8):
            continue
        if box_height < max(14, height // 30):
            continue
        if box_height > height * (0.45 if explicit_formula_context else 0.30):
            continue
        if box_width > width * 0.98 and box_height > height * 0.60:
            continue

        density = area / max(box_width * box_height, 1)
        aspect = box_width / max(box_height, 1)
        if aspect < (1.1 if explicit_formula_context else 1.5):
            continue

        left, top, right, bottom = _expand_bounds(
            x=x,
            y=y,
            width=box_width,
            height=box_height,
            image_width=width,
            image_height=height,
        )
        score = _score_region(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            image_width=width,
            image_height=height,
            density=density,
            aspect=aspect,
            explicit_formula_context=explicit_formula_context,
            prefer_top=prefer_top,
        )
        proposals.append(
            FormulaRegion(
                crop_index=0,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                width=right - left,
                height=bottom - top,
                score=score,
                strategy="component",
            )
        )
    return proposals


def _row_band_regions(
    binary: object,
    *,
    width: int,
    height: int,
    explicit_formula_context: bool,
    prefer_top: bool,
) -> list[FormulaRegion]:
    row_density = (binary > 0).mean(axis=1)
    threshold = max(0.01, float(np.quantile(row_density, 0.80)) * 0.5)
    active_rows = row_density >= threshold
    ranges = _collect_true_ranges(active_rows)
    proposals: list[FormulaRegion] = []

    for start, end in ranges:
        band_height = end - start
        if band_height < max(12, height // 40):
            continue
        if band_height > height * (0.40 if explicit_formula_context else 0.22):
            continue

        band = binary[start:end, :]
        col_density = (band > 0).mean(axis=0)
        col_threshold = max(0.01, float(np.quantile(col_density, 0.75)) * 0.45)
        active_cols = col_density >= col_threshold
        col_ranges = _collect_true_ranges(active_cols)
        if not col_ranges:
            continue

        left = min(range_start for range_start, _range_end in col_ranges)
        right = max(range_end for _range_start, range_end in col_ranges)
        left, top, right, bottom = _expand_bounds(
            x=left,
            y=start,
            width=right - left,
            height=band_height,
            image_width=width,
            image_height=height,
        )
        aspect = (right - left) / max(bottom - top, 1)
        density = float((band > 0).mean())
        if aspect < (1.1 if explicit_formula_context else 1.6):
            continue
        score = _score_region(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            image_width=width,
            image_height=height,
            density=density,
            aspect=aspect,
            explicit_formula_context=explicit_formula_context,
            prefer_top=prefer_top,
        )
        proposals.append(
            FormulaRegion(
                crop_index=0,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                width=right - left,
                height=bottom - top,
                score=score,
                strategy="row_band",
            )
        )
    return proposals


def _expand_bounds(
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    margin_x = max(8, int(width * 0.08))
    margin_y = max(6, int(height * 0.35))
    left = max(0, x - margin_x)
    top = max(0, y - margin_y)
    right = min(image_width, x + width + margin_x)
    bottom = min(image_height, y + height + margin_y)
    return left, top, right, bottom


def _score_region(
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    image_width: int,
    image_height: int,
    density: float,
    aspect: float,
    explicit_formula_context: bool,
    prefer_top: bool,
) -> float:
    region_width = right - left
    region_height = bottom - top
    area_ratio = (region_width * region_height) / max(image_width * image_height, 1)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2

    score = 0.0
    score += min(4.0, aspect / 2.0)
    score += min(2.5, area_ratio * 12.0)
    if 0.02 <= density <= 0.65:
        score += 1.0
    if abs(center_x - image_width / 2) <= image_width * 0.22:
        score += 0.6
    if prefer_top and center_y <= image_height * 0.45:
        score += 0.8
    if explicit_formula_context:
        score += 0.6
    if region_height <= image_height * 0.18:
        score += 0.6
    return score


def _collect_true_ranges(mask: object) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(mask.tolist()):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(mask)))
    return ranges


def _dedupe_regions(regions: list[FormulaRegion]) -> list[FormulaRegion]:
    kept: list[FormulaRegion] = []
    for region in sorted(regions, key=lambda item: (-item.score, item.top, item.left)):
        overlaps = False
        for existing in kept:
            if _intersection_over_union(region, existing) >= 0.55:
                overlaps = True
                break
        if not overlaps:
            kept.append(region)
    return kept


def _intersection_over_union(first: FormulaRegion, second: FormulaRegion) -> float:
    left = max(first.left, second.left)
    top = max(first.top, second.top)
    right = min(first.right, second.right)
    bottom = min(first.bottom, second.bottom)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    first_area = (first.right - first.left) * (first.bottom - first.top)
    second_area = (second.right - second.left) * (second.bottom - second.top)
    union = first_area + second_area - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def _full_image_region(image: object) -> FormulaRegion:
    width, height = getattr(image, "size", (0, 0))
    return FormulaRegion(
        crop_index=1,
        left=0,
        top=0,
        right=int(width),
        bottom=int(height),
        width=int(width),
        height=int(height),
        score=0.0,
        strategy="fallback",
    )
