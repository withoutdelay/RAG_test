from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import mimetypes
import re
from typing import Any

from app.config import Settings, get_settings
from app.services.llm.client import LLMClient, LLMInputImage, LLMRequest, TaskType
from app.services.parsing.docling_parser import ParsedAsset


FIGURE_SEMANTIC_HINT_PATTERN = re.compile(
    r"(原理图|接线图|示意图|波形|系统图|拓扑|主回路|控制回路|联锁|同步|切换|励磁|变频|LCI|PLC|电机|变压器|柜|通信|diagram|schematic|circuit|waveform)",
    re.IGNORECASE,
)

ASSET_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "title_hint": {"type": "string"},
                    "diagram_type": {"type": "string"},
                    "summary": {"type": "string"},
                    "problem_solved": {"type": "string"},
                    "principle_summary": {"type": "string"},
                    "key_components": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "signals_or_loops": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "applicable_sections": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "retrieval_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number"},
                    "review_required": {"type": "boolean"},
                    "review_notes": {"type": "string"},
                },
                "required": [
                    "candidate_index",
                    "title_hint",
                    "diagram_type",
                    "summary",
                    "problem_solved",
                    "principle_summary",
                    "key_components",
                    "signals_or_loops",
                    "applicable_sections",
                    "retrieval_keywords",
                    "confidence",
                    "review_required",
                    "review_notes",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class AssetSemanticSummaryStats:
    candidate_count: int = 0
    summarized_count: int = 0
    vision_attached_count: int = 0
    request_count: int = 0
    error: str | None = None


class AssetSemanticSummaryService:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client or LLMClient()

    async def summarize_assets(self, assets: list[ParsedAsset]) -> tuple[list[ParsedAsset], AssetSemanticSummaryStats]:
        stats = AssetSemanticSummaryStats()
        if not self.settings.parser_llm_asset_summary_enabled:
            return assets, stats

        candidates = self._collect_candidates(assets)
        stats.candidate_count = len(candidates)
        if not candidates:
            return assets, stats

        batch_size = max(1, int(self.settings.parser_llm_asset_summary_max_assets))
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            await self._summarize_batch(assets=assets, batch=batch, stats=stats)

        return assets, stats

    def _collect_candidates(self, assets: list[ParsedAsset]) -> list[tuple[int, dict[str, Any]]]:
        candidates: list[tuple[int, dict[str, Any]]] = []
        for index, asset in enumerate(assets):
            if not self._should_summarize_asset(asset):
                continue
            candidates.append((index, _build_candidate_payload(index=index, asset=asset)))
        return candidates

    def _build_input_images(self, candidates: list[tuple[int, dict[str, Any]]]) -> tuple[list[LLMInputImage], list[int]]:
        if not self.settings.parser_llm_asset_summary_use_vision:
            return [], []

        detail = self.settings.parser_llm_asset_summary_image_detail
        max_bytes = max(0, int(self.settings.parser_llm_asset_summary_max_image_bytes))
        input_images: list[LLMInputImage] = []
        candidate_indices: list[int] = []
        for asset_index, payload in candidates:
            asset = payload["_asset"]
            image_bytes = getattr(asset, "image_bytes", None)
            if not image_bytes:
                continue
            if max_bytes and len(image_bytes) > max_bytes:
                continue
            mime_type = _guess_mime_type(getattr(asset, "image_ext", ".png"))
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            input_images.append(LLMInputImage(image_url=data_url, detail=detail))
            candidate_indices.append(asset_index)
        return input_images, candidate_indices

    def _should_summarize_asset(self, asset: ParsedAsset) -> bool:
        if asset.asset_type != "figure":
            return False
        metadata = dict(asset.meta or {})
        current_role = str(metadata.get("visual_role") or "")
        if current_role == "page_furniture":
            return False
        if isinstance(metadata.get("semantic_summary"), dict) and str(metadata["semantic_summary"].get("status") or "") == "summarized":
            return False

        combined = " ".join(
            value
            for value in (
                asset.title,
                asset.heading_path,
                asset.caption,
                asset.context_before,
                asset.context_after,
            )
            if isinstance(value, str) and value
        )
        if current_role == "engineering_figure":
            return True
        if FIGURE_SEMANTIC_HINT_PATTERN.search(combined):
            return True
        if current_role in {"illustration", "reference_figure"} and _looks_like_diagram_candidate(metadata):
            return True
        return False

    async def _summarize_batch(
        self,
        *,
        assets: list[ParsedAsset],
        batch: list[tuple[int, dict[str, Any]]],
        stats: AssetSemanticSummaryStats,
    ) -> None:
        input_images, vision_candidate_indices = self._build_input_images(batch)
        stats.vision_attached_count += len(input_images)
        stats.request_count += 1

        request = LLMRequest(
            task_type=TaskType.ASSET_SUMMARY,
            system_prompt=_build_system_prompt(),
            user_prompt=_build_user_prompt(batch, vision_candidate_indices=vision_candidate_indices),
            temperature=0.1,
            max_tokens=2200,
            json_schema=ASSET_SUMMARY_SCHEMA,
            input_images=input_images,
            metadata={"candidates": [payload for _index, payload in batch]},
        )

        try:
            response = await self.llm_client.invoke(request)
            payload = json.loads(response.content)
        except Exception as exc:
            if len(batch) > 1:
                stats.error = str(exc)
                for candidate in batch:
                    await self._summarize_batch(assets=assets, batch=[candidate], stats=stats)
                return
            stats.error = str(exc)
            asset_index, _candidate = batch[0]
            asset = assets[asset_index]
            asset.meta = {
                **dict(asset.meta or {}),
                "semantic_summary": {
                    "status": "failed",
                    "error": str(exc),
                },
            }
            return

        for decision in payload.get("items") or []:
            try:
                asset_index = int(decision.get("candidate_index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= asset_index < len(assets):
                continue

            asset = assets[asset_index]
            summary_meta = _build_summary_meta(decision)
            if not summary_meta["summary"] and not summary_meta["problem_solved"] and not summary_meta["principle_summary"]:
                continue
            asset.meta = {
                **dict(asset.meta or {}),
                "semantic_summary": summary_meta,
            }
            stats.summarized_count += 1


def _build_system_prompt() -> str:
    return (
        "你是电气技术方案图语义摘要器。请结合图片、标题、章节标题、前后文，对每个候选图资产做保守且结构化的总结。\n"
        "目标：增强图检索与章节生成，不是做工程签审。\n\n"
        "输出要求：\n"
        "1. 只总结图中明确可见或上下文明确给出的信息，不要编造未出现的参数、型号或逻辑。\n"
        "2. title_hint 输出一个适合界面展示和检索的短标题，长度尽量控制在 8 到 24 个字；无法判断时返回空字符串。\n"
        "3. summary 用一句话概括这张图的用途与主题。\n"
        "4. problem_solved 说明它用于解决什么问题。\n"
        "5. principle_summary 说明核心工作原理或结构关系。\n"
        "6. key_components 列关键部件、设备、柜体、回路或模块。\n"
        "7. signals_or_loops 列关键控制信号、联锁、主回路或通讯回路，没有就返回空数组。\n"
        "8. applicable_sections 列适合复用到哪些章节标题或章节类型。\n"
        "9. retrieval_keywords 给出便于检索的关键词，优先专业术语。\n"
        "10. 如果图意不清、文字太小或只能看出大概，请降低 confidence 并把 review_required 设为 true。\n"
        "11. 只返回 JSON。"
    )


def _build_user_prompt(candidates: list[tuple[int, dict[str, Any]]], *, vision_candidate_indices: list[int]) -> str:
    payload = {"candidates": [_sanitize_candidate(item) for _index, item in candidates]}
    if vision_candidate_indices:
        payload["vision_candidate_indices"] = vision_candidate_indices
    return (
        "请为以下方案图候选生成结构化语义摘要。\n"
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
        "document_name": str(metadata.get("document_name") or ""),
        "bbox": asset.bbox or {},
        "image_width": metadata.get("width"),
        "image_height": metadata.get("height"),
        "page_width": metadata.get("page_width"),
        "page_height": metadata.get("page_height"),
        "source_ref": asset.source_ref or "",
        "_asset": asset,
    }


def _build_summary_meta(decision: dict[str, Any]) -> dict[str, Any]:
    summary = _sanitize_text(decision.get("summary"), limit=220)
    problem_solved = _sanitize_text(decision.get("problem_solved"), limit=220)
    principle_summary = _sanitize_text(decision.get("principle_summary"), limit=260)
    return {
        "status": "summarized",
        "title_hint": _sanitize_text(decision.get("title_hint"), limit=48),
        "diagram_type": _sanitize_text(decision.get("diagram_type"), limit=60),
        "summary": summary,
        "problem_solved": problem_solved,
        "principle_summary": principle_summary,
        "key_components": _sanitize_list(decision.get("key_components"), limit=8, item_limit=48),
        "signals_or_loops": _sanitize_list(decision.get("signals_or_loops"), limit=8, item_limit=48),
        "applicable_sections": _sanitize_list(decision.get("applicable_sections"), limit=6, item_limit=48),
        "retrieval_keywords": _sanitize_list(decision.get("retrieval_keywords"), limit=12, item_limit=32),
        "confidence": _coerce_float(decision.get("confidence")),
        "review_required": bool(decision.get("review_required")),
        "review_notes": _sanitize_text(decision.get("review_notes"), limit=180),
    }


def _sanitize_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())[:limit]


def _sanitize_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _sanitize_text(item, limit=item_limit)
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


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


def _looks_like_diagram_candidate(metadata: dict[str, Any]) -> bool:
    image_width = int(metadata.get("width") or 0)
    image_height = int(metadata.get("height") or 0)
    if image_width > 0 and image_height > 0 and image_width * image_height >= 180000:
        return True

    bbox = metadata.get("bbox") or {}
    page_width = float(metadata.get("page_width") or 0)
    page_height = float(metadata.get("page_height") or 0)
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
    return box_width * box_height >= page_width * page_height * 0.08
