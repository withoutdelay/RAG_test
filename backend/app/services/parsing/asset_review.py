from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import mimetypes
import re
from typing import Any

from app.config import Settings, get_settings
from app.services.llm.client import LLMClient, LLMInputImage, LLMRequest, TaskType
from app.services.parsing.docling_parser import ParsedAsset


FIGURE_HINT_PATTERN = re.compile(
    r"(原理图|接线图|示意图|波形|电压|电流|主回路|系统图|拓扑|一次|二次|schematic|diagram|waveform|circuit)",
    re.IGNORECASE,
)
PAGE_FURNITURE_HINT_PATTERN = re.compile(r"(版本|页码|总页数|目录|买方|卖方|dayu electric|logo)", re.IGNORECASE)
GARBLED_TITLE_PATTERN = re.compile(r"^(?:\[[A-Za-z]\]\s*){3,}$")

ASSET_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "visual_role": {
                        "type": "string",
                        "enum": ["engineering_figure", "page_furniture", "illustration"],
                    },
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "title_hint": {"type": "string"},
                },
                "required": ["candidate_index", "visual_role", "confidence", "reason", "title_hint"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class AssetReviewStats:
    reviewed_count: int = 0
    overridden_count: int = 0
    title_refined_count: int = 0
    vision_attached_count: int = 0
    error: str | None = None


class AssetReviewService:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client or LLMClient()

    async def review_assets(self, assets: list[ParsedAsset]) -> tuple[list[ParsedAsset], AssetReviewStats]:
        stats = AssetReviewStats()
        if not self.settings.parser_llm_asset_review_enabled:
            return assets, stats

        candidates = self._collect_candidates(assets)
        if not candidates:
            return assets, stats

        input_images, vision_candidate_indices = self._build_input_images(candidates)
        stats.vision_attached_count = len(input_images)

        request = LLMRequest(
            task_type=TaskType.ASSET_REVIEW,
            system_prompt=_build_system_prompt(),
            user_prompt=_build_user_prompt(candidates, vision_candidate_indices=vision_candidate_indices),
            temperature=0.0,
            max_tokens=1400,
            json_schema=ASSET_REVIEW_SCHEMA,
            input_images=input_images,
            metadata={"candidates": [payload for _index, payload in candidates]},
        )

        try:
            response = await self.llm_client.invoke(request)
            payload = json.loads(response.content)
        except Exception as exc:
            stats.error = str(exc)
            for asset_index, _candidate in candidates:
                asset = assets[asset_index]
                asset.meta = {
                    **dict(asset.meta or {}),
                    "llm_asset_review": {
                        "status": "failed",
                        "error": str(exc),
                    },
                }
            return assets, stats

        decisions = payload.get("items") or []
        for decision in decisions:
            try:
                asset_index = int(decision.get("candidate_index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= asset_index < len(assets):
                continue
            asset = assets[asset_index]
            confidence = _coerce_float(decision.get("confidence"))
            suggested_role = str(decision.get("visual_role") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            title_hint = str(decision.get("title_hint") or "").strip()
            current_role = str((asset.meta or {}).get("visual_role") or "")

            review_meta = {
                "status": "reviewed",
                "confidence": confidence,
                "reason": reason,
                "suggested_visual_role": suggested_role,
                "title_hint": title_hint,
            }
            asset.meta = {
                **dict(asset.meta or {}),
                "llm_asset_review": review_meta,
            }
            stats.reviewed_count += 1

            if suggested_role and confidence >= self.settings.parser_llm_asset_review_confidence_threshold:
                if suggested_role != current_role:
                    asset.meta["visual_role"] = suggested_role
                    stats.overridden_count += 1
                if title_hint and _should_replace_title(asset.title):
                    asset.title = title_hint[:120]
                    if not asset.heading_path:
                        asset.heading_path = asset.title
                    stats.title_refined_count += 1

        return assets, stats

    def _collect_candidates(self, assets: list[ParsedAsset]) -> list[tuple[int, dict[str, Any]]]:
        candidates: list[tuple[int, dict[str, Any]]] = []
        limit = max(0, int(self.settings.parser_llm_asset_review_max_assets))
        if limit <= 0:
            return candidates

        for index, asset in enumerate(assets):
            if len(candidates) >= limit:
                break
            if not self._should_review_asset(asset):
                continue
            candidates.append((index, _build_candidate_payload(index=index, asset=asset)))
        return candidates

    def _build_input_images(self, candidates: list[tuple[int, dict[str, Any]]]) -> tuple[list[LLMInputImage], list[int]]:
        if not self.settings.parser_llm_asset_review_use_vision:
            return [], []

        input_images: list[LLMInputImage] = []
        candidate_indices: list[int] = []
        max_bytes = max(0, int(self.settings.parser_llm_asset_review_max_image_bytes))
        detail = str(self.settings.parser_llm_asset_review_image_detail or "auto")

        for asset_index, candidate in candidates:
            asset = candidate.get("_asset")
            if not isinstance(asset, ParsedAsset):
                continue
            image_bytes = asset.image_bytes
            if not image_bytes:
                continue
            if max_bytes and len(image_bytes) > max_bytes:
                continue
            mime_type = _guess_mime_type(asset.image_ext)
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            input_images.append(LLMInputImage(image_url=data_url, detail=detail))
            candidate_indices.append(asset_index)
        return input_images, candidate_indices

    def _should_review_asset(self, asset: ParsedAsset) -> bool:
        if asset.asset_type != "figure":
            return False

        metadata = dict(asset.meta or {})
        current_role = str(metadata.get("visual_role") or "")
        combined = " ".join(
            value
            for value in (asset.title, asset.heading_path, asset.caption, asset.context_before, asset.context_after)
            if isinstance(value, str) and value
        )

        if _looks_like_page_banner(metadata) and current_role != "page_furniture":
            return True
        if PAGE_FURNITURE_HINT_PATTERN.search(combined) and current_role != "page_furniture":
            return True
        if _should_replace_title(asset.title):
            return True
        if current_role in {"illustration", "reference_figure"} and FIGURE_HINT_PATTERN.search(combined):
            return True
        if current_role == "engineering_figure" and not FIGURE_HINT_PATTERN.search(combined):
            return True
        return False


def _build_system_prompt() -> str:
    return (
        "你是 PDF 图资产复核器。请基于标题、上下文、页面几何信息，判断图片资产属于哪一类：\n"
        "1. engineering_figure: 真实工程示意图、原理图、接线图、波形图、系统图。\n"
        "2. page_furniture: 页眉页脚、公司 logo、页码条、目录装饰、版权或边角装饰图。\n"
        "3. illustration: 普通插图、外形图、说明性配图，但不是页眉页脚噪声。\n\n"
        "判定原则：\n"
        "- 页面顶部/底部的细长小图、banner、logo，优先判为 page_furniture。\n"
        "- 只有在上下文明确显示原理图/接线图/波形图/系统结构图时，才判为 engineering_figure。\n"
        "- 不要因为上下文里出现“如下图所示”就把页脚 logo 判成工程图。\n"
        "- 如果同时提供了图片，请优先根据图片视觉内容判定；文字上下文仅作为辅助。\n"
        "- title_hint 只输出短标题；如果无法改进，就返回空字符串。\n"
        "- 只返回 JSON。"
    )


def _build_user_prompt(candidates: list[tuple[int, dict[str, Any]]], *, vision_candidate_indices: list[int]) -> str:
    payload = {"candidates": [_sanitize_candidate(item) for _index, item in candidates]}
    if vision_candidate_indices:
        payload["vision_candidate_indices"] = vision_candidate_indices
    return (
        "请审核以下 PDF 图片资产候选，并输出 JSON。\n"
        "如果附带图片输入，则图片顺序与 vision_candidate_indices 中列出的 candidate_index 顺序完全一致。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_candidate_payload(*, index: int, asset: ParsedAsset) -> dict[str, Any]:
    metadata = dict(asset.meta or {})
    return {
        "candidate_index": index,
        "page_no": asset.page_no,
        "title": asset.title or "",
        "heading_path": asset.heading_path or "",
        "caption": asset.caption or "",
        "context_before": asset.context_before or "",
        "context_after": asset.context_after or "",
        "current_visual_role": str(metadata.get("visual_role") or ""),
        "bbox": asset.bbox or {},
        "image_width": metadata.get("width"),
        "image_height": metadata.get("height"),
        "page_width": metadata.get("page_width"),
        "page_height": metadata.get("page_height"),
        "source_ref": asset.source_ref or "",
        "_asset": asset,
    }


def _looks_like_page_banner(metadata: dict[str, Any]) -> bool:
    bbox = metadata.get("bbox") or {}
    page_width = float(metadata.get("page_width") or 0)
    page_height = float(metadata.get("page_height") or 0)
    image_width = int(metadata.get("width") or 0)
    image_height = int(metadata.get("height") or 0)
    if not bbox or page_width <= 0 or page_height <= 0:
        return False

    try:
        left = float(bbox.get("l") or 0.0)
        right = float(bbox.get("r") or 0.0)
        top = float(bbox.get("t") or 0.0)
        bottom = float(bbox.get("b") or 0.0)
    except (TypeError, ValueError):
        return False

    box_width = max(0.0, right - left)
    box_height = max(0.0, top - bottom)
    if box_width <= 0 or box_height <= 0:
        return False

    near_top = top >= page_height * 0.88
    near_bottom = bottom <= page_height * 0.12
    narrow_band = box_height <= page_height * 0.12
    slim_band = box_height <= page_height * 0.08
    small_area = box_width * box_height <= page_width * page_height * 0.02
    wide_banner = box_width >= box_height * 1.6
    small_image = image_width > 0 and image_height > 0 and image_width * image_height <= 40000
    edge_margin = left <= page_width * 0.16 or right >= page_width * 0.84

    return (near_top or near_bottom or edge_margin) and narrow_band and (slim_band or small_area or wide_banner or small_image)


def _should_replace_title(title: str | None) -> bool:
    if not title:
        return True
    normalized = str(title).strip()
    if not normalized:
        return True
    if GARBLED_TITLE_PATTERN.fullmatch(normalized):
        return True
    return len(normalized) <= 2


def _coerce_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _guess_mime_type(image_ext: str | None) -> str:
    extension = str(image_ext or ".png").strip() or ".png"
    if not extension.startswith("."):
        extension = f".{extension}"
    return mimetypes.guess_type(f"asset{extension}")[0] or "image/png"


def _sanitize_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "_asset"}
