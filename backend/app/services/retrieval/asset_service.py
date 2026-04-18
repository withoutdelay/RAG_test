from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.figure_asset import FigureAsset
from app.models.project import Project
from app.models.raw_document import RawDocument
from app.schemas.retrieval import AssetSearchResponse, AssetSearchResult
from app.services.v2_errors import ArtifactNotFoundError
from app.services.vectorstore.block_taxonomy import (
    classify_block_taxonomy,
    heading_family_similarity,
    heading_focus_adjustment,
    infer_target_taxonomy,
    related_section_types,
)
from app.services.vectorstore.embedder import Embedder


ENGINEERING_VISUAL_PATTERN = re.compile(
    r"(波形|波特图|时序图|点阵图|电路图|原理图|接线图|示意图|电气图|FFT|Bode|waveform|circuit|schematic|diagram)",
    re.IGNORECASE,
)
FORMULA_VISUAL_PATTERN = re.compile(r"(公式|equation|latex|math|推导|算式)", re.IGNORECASE)
KEYWORD_PATTERN = re.compile(r"[A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}")
PAGE_FURNITURE_PATTERN = re.compile(r"(版本|页码|总页数|目录|dayu electric|买方|卖方)", re.IGNORECASE)
TITLE_NOISE_PATTERN = re.compile(r"(?:\[[A-Za-z]\]\s*){3,}|(?:\d[\d .,*_\-\[\]()]{10,})")
GENERIC_ASSET_TITLE_PATTERN = re.compile(
    r"^(系统功能描述|系统方案|系统构成|性能要求|整体要求|项目名称|技术方案|图|附图|page\s*\d+|figure\s*\d+)$",
    re.IGNORECASE,
)
GENERIC_DIAGRAM_TYPE_PATTERN = re.compile(r"^(other|工程示意图|图示|图形资产|主图|系统图|示意图)$", re.IGNORECASE)
HARD_FRAGMENT_PATTERN = re.compile(
    r"(cropped\s+figure\s+fragment|figure\s+fragment|symbol\s*/\s*cropped|符号局部|局部图案|"
    r"仅显示(?:一个|上下|单线图中的)?|无法(?:确认|识别|辨认|提炼)|缺乏可识别|信息非常有限|图意不清|文字太小|"
    r"文字切片|标题文字|封面字样|text_fragment)",
    re.IGNORECASE,
)
PARTIAL_FRAGMENT_PATTERN = re.compile(r"(局部|fragment|裁剪|符号|symbol)", re.IGNORECASE)
COMPLETE_DIAGRAM_PATTERN = re.compile(
    r"(系统图|单线图|一次图|一次接线|原理图|接线图|主回路|系统示意|拓扑|完整|总图)",
    re.IGNORECASE,
)
LAYOUT_ILLUSTRATION_PATTERN = re.compile(
    r"(外观图|高度关系|平面间距|间距示意|外形|柜体分段|顶部通风|布置图|尺寸图|检修通道)",
    re.IGNORECASE,
)
LOGO_ASSET_PATTERN = re.compile(
    r"(logo|标\s*识|商标|公司徽标|公司全称|股份有限公司|dayu\s*electric|大\s*禹\s*电\s*气|大\s*禹\s*标\s*识)",
    re.IGNORECASE,
)
CONTROL_INTERFACE_FOCUS_PATTERN = re.compile(
    r"(控制|监控|监视|联锁|保护|告警|报警|故障|接口|信号|点表|PLC|DCS|励磁|断路器|反馈)",
    re.IGNORECASE,
)
CONTROL_INTERFACE_TABLE_NOISE_PATTERN = re.compile(
    r"(备品备件|备件|spare|售后|服务|培训|维保|rated\s*data|额定数据|供货范围)",
    re.IGNORECASE,
)
VFD_AUXILIARY_CURVE_NOISE_PATTERN = re.compile(r"(润滑油|油站|冷却器|冷却水|辅机)", re.IGNORECASE)
VFD_FOCUS_PATTERN = re.compile(r"(LCI|SFC|变频软起|软起动|软启动|同步切换|工频切换|晶闸管|主回路)", re.IGNORECASE)


@dataclass(frozen=True)
class AssetCard:
    asset_card_id: str
    asset_id: UUID
    document_id: UUID | None
    raw_document_id: UUID
    project_id: UUID | None
    document_name: str | None
    doc_type: str | None
    asset_type: str
    visual_role: str | None
    risk_level: str
    usage_mode: str
    review_required: bool
    page_no: int | None
    heading_path: str | None
    title: str | None
    display_title: str | None
    caption: str | None
    source_ref: str | None
    asset_uri: str
    preview_text: str
    retrieval_text: str
    section_type: str
    equipment_type: str
    content_form: str
    metadata: dict[str, Any]
    semantic_summary_text: str | None = None
    semantic_summary_confidence: float = 0.0


class AssetRetrievalService:
    def __init__(self, *, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or Embedder()

    async def search_project_assets(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        query: str,
        top_k: int = 5,
        asset_types: list[str] | None = None,
        doc_types: list[str] | None = None,
        section_context: dict[str, Any] | None = None,
        include_global_historical: bool = False,
    ) -> AssetSearchResponse:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        cards = await self._load_asset_cards(
            session=session,
            project_id=project_id,
            asset_types=asset_types,
            doc_types=doc_types,
            anchor_document_names=(section_context or {}).get("anchor_document_names"),
            include_global_historical=include_global_historical,
        )
        if not cards:
            return AssetSearchResponse(results=[], total=0)

        query_vector = await self.embedder.embed_text(query)
        section_title = str((section_context or {}).get("section_title") or (section_context or {}).get("title") or "")
        expected_types = [str(item) for item in ((section_context or {}).get("expected_evidence_types") or [])]
        taxonomy_context = dict(section_context or {})
        if not taxonomy_context.get("title") and taxonomy_context.get("section_title"):
            taxonomy_context["title"] = taxonomy_context.get("section_title")
        target_taxonomy = infer_target_taxonomy(taxonomy_context)
        anchor_document_names = {
            str(item)
            for item in ((section_context or {}).get("anchor_document_names") or [])
            if str(item).strip()
        }
        anchor_headings = [
            str(item)
            for item in ((section_context or {}).get("anchor_heading_paths") or [])
            if str(item).strip()
        ]

        scored_cards: list[tuple[float, AssetCard]] = []
        for card in cards:
            retrieval_vector = await self.embedder.embed_text(card.retrieval_text)
            semantic_score = max(0.0, _cosine_similarity(query_vector, retrieval_vector))
            metadata_boost = _keyword_overlap_boost(query, card.retrieval_text)
            section_boost = _keyword_overlap_boost(section_title, f"{card.heading_path or ''} {card.title or ''} {card.caption or ''}")
            summary_boost = _asset_summary_boost(query=query, section_title=section_title, card=card)
            type_boost = _expected_type_boost(card.asset_type, expected_types)
            taxonomy_boost = _asset_taxonomy_boost(card=card, target_taxonomy=target_taxonomy)
            anchor_boost = _asset_anchor_boost(
                card=card,
                anchor_document_names=anchor_document_names,
                anchor_headings=anchor_headings,
            )
            noise_penalty = _asset_noise_penalty(card=card, target_section_type=str(target_taxonomy.get("section_type") or "unknown"))
            risk_penalty = {"high": 0.08, "medium": 0.03}.get(card.risk_level, 0.0)
            final_score = (
                semantic_score
                + metadata_boost
                + section_boost
                + summary_boost
                + type_boost
                + taxonomy_boost
                + anchor_boost
                - risk_penalty
                - noise_penalty
            )
            scored_cards.append((final_score, card))

        scored_cards.sort(key=lambda item: item[0], reverse=True)
        preferred_cards = [
            (score, card)
            for score, card in scored_cards
            if not _asset_quality_flags(card=card)["low_information"]
        ]
        if len(preferred_cards) >= top_k:
            result_pool = preferred_cards
        else:
            preferred_ids = {card.asset_id for _score, card in preferred_cards}
            result_pool = [
                *preferred_cards,
                *[(score, card) for score, card in scored_cards if card.asset_id not in preferred_ids],
            ]
        results = [
            _to_result(card=card, score=score, section_title=section_title)
            for score, card in result_pool[:top_k]
        ]
        return AssetSearchResponse(results=results, total=len(results))

    async def _load_asset_cards(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        asset_types: list[str] | None,
        doc_types: list[str] | None,
        anchor_document_names: list[str] | None,
        include_global_historical: bool,
    ) -> list[AssetCard]:
        scope_filters = [RawDocument.project_id == project_id]
        if include_global_historical:
            scope_filters.append(RawDocument.corpus_scope == "global")
        rows = (
            await session.execute(
                select(FigureAsset, RawDocument)
                .join(RawDocument, FigureAsset.raw_document_id == RawDocument.id)
                .where(or_(*scope_filters))
                .order_by(FigureAsset.created_at.asc())
            )
        ).all()
        if not rows:
            return []

        legacy_document_ids: set[UUID] = set()
        for asset, _ in rows:
            legacy_document_id = (asset.meta or {}).get("legacy_document_id")
            if not legacy_document_id:
                continue
            try:
                legacy_document_ids.add(UUID(str(legacy_document_id)))
            except (TypeError, ValueError):
                continue

        document_map: dict[UUID, Document] = {}
        if legacy_document_ids:
            documents = (
                await session.scalars(select(Document).where(Document.id.in_(legacy_document_ids)))
            ).all()
            document_map = {document.id: document for document in documents}

        normalized_doc_types = {str(item) for item in (doc_types or []) if item}
        normalized_asset_types = {str(item) for item in (asset_types or []) if item}
        normalized_anchor_document_names = {str(item).strip() for item in (anchor_document_names or []) if str(item).strip()}

        cards: list[AssetCard] = []
        fallback_cards: list[AssetCard] = []
        for asset, raw_document in rows:
            card = _build_asset_card(asset=asset, raw_document=raw_document, document_map=document_map)
            if card.asset_type == "figure" and card.visual_role == "page_furniture":
                continue
            if normalized_doc_types and (card.doc_type or "") not in normalized_doc_types:
                continue
            if normalized_asset_types and card.asset_type not in normalized_asset_types:
                continue
            if normalized_anchor_document_names and (card.document_name or "") not in normalized_anchor_document_names:
                fallback_cards.append(card)
                continue
            cards.append(card)
        if cards:
            return cards
        return fallback_cards


def _build_asset_card(
    *,
    asset: FigureAsset,
    raw_document: RawDocument,
    document_map: dict[UUID, Document],
) -> AssetCard:
    metadata = dict(asset.meta or {})
    linked_document: Document | None = None
    legacy_document_id = metadata.get("legacy_document_id")
    if legacy_document_id:
        try:
            linked_document = document_map.get(UUID(str(legacy_document_id)))
        except (TypeError, ValueError):
            linked_document = None

    heading_path = _normalize_text(metadata.get("heading_path"))
    title = _normalize_text(asset.title)
    caption = _normalize_text(asset.caption)
    context_before = _normalize_text(metadata.get("context_before"))
    context_after = _normalize_text(metadata.get("context_after"))
    visual_role = _derive_visual_role(
        asset=asset,
        metadata=metadata,
        title=title,
        caption=caption,
        context_before=context_before,
        context_after=context_after,
    )
    asset_type = _derive_asset_type(asset=asset, visual_role=visual_role)
    review_required = _derive_review_required(
        asset=asset,
        metadata=metadata,
        asset_type=asset_type,
        visual_role=visual_role,
    )
    risk_level = _derive_risk_level(
        metadata=metadata,
        asset_type=asset_type,
        visual_role=visual_role,
        review_required=review_required,
    )
    usage_mode = "reference_only" if review_required else str(asset.reuse_mode or "reference_only")
    semantic_summary = _normalize_semantic_summary(metadata.get("semantic_summary"))
    semantic_summary_text = _build_semantic_summary_text(semantic_summary)
    semantic_summary_confidence = _coerce_confidence((semantic_summary or {}).get("confidence"))
    display_title = _build_asset_display_title(
        title=title,
        heading_path=heading_path,
        caption=caption,
        page_no=asset.page_no,
        semantic_summary=semantic_summary,
    )
    preview_text = _build_preview_text(
        title=title,
        caption=caption,
        context_before=context_before,
        context_after=context_after,
        metadata=metadata,
    )
    retrieval_text = _build_retrieval_text(
        asset_type=asset_type,
        visual_role=visual_role,
        title=title,
        caption=caption,
        heading_path=heading_path,
        context_before=context_before,
        context_after=context_after,
        document_name=linked_document.filename if linked_document else raw_document.file_name,
        doc_type=linked_document.doc_type if linked_document else raw_document.doc_type,
        metadata=metadata,
    )
    taxonomy = classify_block_taxonomy(
        content=" ".join(
            item
            for item in (
                title,
                caption,
                heading_path,
                context_before,
                context_after,
                _normalize_text(metadata.get("source_ref")),
            )
            if item
        ),
        heading_path=heading_path or title or caption or "",
        chunk_type="TABLE" if asset_type == "table" else "PLAIN",
        front_matter=visual_role == "page_furniture",
        needs_asset_lookup=asset_type == "figure",
    )

    return AssetCard(
        asset_card_id=f"asset:{asset.id}",
        asset_id=asset.id,
        document_id=linked_document.id if linked_document else None,
        raw_document_id=raw_document.id,
        project_id=raw_document.project_id,
        document_name=linked_document.filename if linked_document else raw_document.file_name,
        doc_type=linked_document.doc_type if linked_document else raw_document.doc_type,
        asset_type=asset_type,
        visual_role=visual_role,
        risk_level=risk_level,
        usage_mode=usage_mode,
        review_required=review_required,
        page_no=asset.page_no,
        heading_path=heading_path,
        title=title,
        display_title=display_title,
        caption=caption,
        source_ref=_normalize_text(metadata.get("source_ref")),
        asset_uri=asset.asset_uri,
        preview_text=preview_text,
        retrieval_text=retrieval_text,
        section_type=str(taxonomy.get("section_type") or "unknown"),
        equipment_type=str(taxonomy.get("equipment_type") or "generic"),
        content_form=str(taxonomy.get("content_form") or ("figure" if asset_type == "figure" else "parameter_table")),
        semantic_summary_text=semantic_summary_text,
        semantic_summary_confidence=semantic_summary_confidence,
        metadata=metadata,
    )


def _to_result(*, card: AssetCard, score: float, section_title: str) -> AssetSearchResult:
    metadata = dict(card.metadata or {})
    metadata.setdefault("section_type", card.section_type)
    metadata.setdefault("equipment_type", card.equipment_type)
    metadata.setdefault("content_form", card.content_form)
    metadata.setdefault("raw_title", card.title)
    metadata.setdefault("display_title", card.display_title or card.title)
    metadata["retrieval_quality"] = _asset_quality_flags(card=card)
    return AssetSearchResult(
        asset_card_id=card.asset_card_id,
        asset_id=card.asset_id,
        document_id=card.document_id,
        raw_document_id=card.raw_document_id,
        document_name=card.document_name,
        doc_type=card.doc_type,
        asset_type=card.asset_type,  # type: ignore[arg-type]
        visual_role=card.visual_role,
        risk_level=card.risk_level,  # type: ignore[arg-type]
        usage_mode=card.usage_mode,
        review_required=card.review_required,
        page_no=card.page_no,
        heading_path=card.heading_path,
        title=card.display_title or card.title,
        display_title=card.display_title,
        caption=card.caption,
        source_ref=card.source_ref,
        asset_uri=card.asset_uri,
        preview_text=card.preview_text,
        reason=_build_reason(card=card, section_title=section_title),
        score=round(score, 4),
        metadata=metadata,
    )


def _build_retrieval_text(
    *,
    asset_type: str,
    visual_role: str | None,
    title: str | None,
    caption: str | None,
    heading_path: str | None,
    context_before: str | None,
    context_after: str | None,
    document_name: str | None,
    doc_type: str | None,
    metadata: dict[str, Any],
) -> str:
    semantic_summary_text = _build_semantic_summary_text(_normalize_semantic_summary(metadata.get("semantic_summary")))
    parts = [
        f"asset_type:{asset_type}",
        f"visual_role:{visual_role}" if visual_role else "",
        f"document_name:{document_name}" if document_name else "",
        f"doc_type:{doc_type}" if doc_type else "",
        f"heading:{heading_path}" if heading_path else "",
        f"title:{title}" if title else "",
        f"caption:{caption}" if caption else "",
        f"context_before:{context_before}" if context_before else "",
        f"context_after:{context_after}" if context_after else "",
        f"semantic_summary:{semantic_summary_text}" if semantic_summary_text else "",
    ]
    table_profile = metadata.get("table_profile") or {}
    if table_profile:
        parts.append(f"table_profile:{table_profile.get('profile_name')}")
        header_fields = table_profile.get("header_fields") or []
        if header_fields:
            parts.append("table_headers:" + " ".join(str(item) for item in header_fields[:6]))
    indexing_reasons = metadata.get("indexing_reasons") or []
    if indexing_reasons:
        parts.append("risk_reasons:" + " ".join(str(item) for item in indexing_reasons))
    return "\n".join(part for part in parts if part)


def _asset_taxonomy_boost(*, card: AssetCard, target_taxonomy: dict[str, Any]) -> float:
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic").lower()
    score = 0.0
    if target_section_type != "unknown" and card.section_type == target_section_type:
        score += 0.22
    elif card.section_type in related_section_types(target_section_type):
        score += 0.12
    elif target_section_type not in {"unknown", "overall_solution"} and card.section_type == "unknown":
        score -= 0.06
    if target_equipment_type != "generic" and card.equipment_type == target_equipment_type:
        score += 0.1
    elif target_equipment_type != "generic" and card.equipment_type not in {"generic", target_equipment_type}:
        score -= 0.1
    heading_adjustment, _ = heading_focus_adjustment(
        target_section_type=target_section_type,
        heading_text=" ".join(part for part in (card.heading_path, card.display_title, card.title, card.caption) if part),
    )
    score += heading_adjustment
    return score


def _asset_anchor_boost(
    *,
    card: AssetCard,
    anchor_document_names: set[str],
    anchor_headings: list[str],
) -> float:
    score = 0.0
    if anchor_document_names and (card.document_name or "") in anchor_document_names:
        score += 0.18
    if anchor_headings and card.heading_path:
        heading_bonus = max(
            (heading_family_similarity(anchor_heading, card.heading_path) for anchor_heading in anchor_headings),
            default=0.0,
        )
        score += heading_bonus
        if any(card.heading_path == anchor_heading for anchor_heading in anchor_headings):
            score += 0.14
    return score


def _asset_noise_penalty(*, card: AssetCard, target_section_type: str) -> float:
    text = " ".join(part for part in (card.heading_path, card.display_title, card.title, card.caption, card.preview_text) if part).casefold()
    penalty = 0.0
    quality_flags = _asset_quality_flags(card=card)
    if quality_flags["low_information"]:
        penalty += 0.72
    elif quality_flags["partial_fragment"]:
        penalty += 0.22
    if quality_flags["summary_review_required"]:
        penalty += 0.14
    if quality_flags["low_confidence_summary"]:
        penalty += 0.1
    if (
        target_section_type in {"main_circuit_scheme", "overall_solution", "control_logic", "protection_interlock"}
        and str(card.visual_role or "").lower() == "illustration"
        and LAYOUT_ILLUSTRATION_PATTERN.search(text)
    ):
        penalty += 0.34
    if target_section_type in {"protection_interlock", "control_logic", "communication_interface"} and card.asset_type == "table":
        focus_match = bool(CONTROL_INTERFACE_FOCUS_PATTERN.search(text))
        if not focus_match:
            penalty += 0.45
        if CONTROL_INTERFACE_TABLE_NOISE_PATTERN.search(text) and not focus_match:
            penalty += 0.24
    if target_section_type in {"vfd_spec", "starter_spec"} and VFD_AUXILIARY_CURVE_NOISE_PATTERN.search(text):
        if not VFD_FOCUS_PATTERN.search(text):
            penalty += 0.34
    if card.visual_role == "page_furniture":
        penalty += 0.32
    if any(token in text for token in ("检测报告", "检验", "认证", "证书", "质量保证", "文档控制", "公司简介")):
        penalty += 0.22
    if target_section_type in {"main_circuit_scheme", "overall_solution", "communication_interface"} and any(
        token in text for token in ("检测报告", "认证", "证书")
    ):
        penalty += 0.18
    return penalty


def _asset_quality_flags(*, card: AssetCard) -> dict[str, Any]:
    summary = _normalize_semantic_summary(card.metadata.get("semantic_summary"))
    summary_text = _build_semantic_summary_text(summary) or ""
    signal_text = " ".join(
        str(item)
        for item in (
            card.display_title,
            card.title,
            card.caption,
            card.heading_path,
            card.preview_text,
            summary_text,
        )
        if item
    )
    normalized_signal_text = unicodedata.normalize("NFKC", signal_text)
    compact_signal_text = re.sub(r"\s+", "", normalized_signal_text)
    logo_like = bool(LOGO_ASSET_PATTERN.search(normalized_signal_text) or LOGO_ASSET_PATTERN.search(compact_signal_text))
    low_information = bool(HARD_FRAGMENT_PATTERN.search(normalized_signal_text) or logo_like)
    partial_fragment = bool(PARTIAL_FRAGMENT_PATTERN.search(signal_text))
    complete_diagram = bool(COMPLETE_DIAGRAM_PATTERN.search(signal_text))
    summary_review_required = bool((summary or {}).get("review_required"))
    summary_confidence = _coerce_confidence((summary or {}).get("confidence"))
    low_confidence_summary = bool(summary) and summary_confidence > 0 and summary_confidence < 0.45

    if low_information and not logo_like and complete_diagram and summary_confidence >= 0.72 and not summary_review_required:
        low_information = False
    if complete_diagram and not low_information:
        partial_fragment = False

    return {
        "low_information": low_information,
        "partial_fragment": partial_fragment and not low_information,
        "complete_diagram": complete_diagram,
        "summary_review_required": summary_review_required,
        "low_confidence_summary": low_confidence_summary,
        "summary_confidence": summary_confidence,
    }


def _build_preview_text(
    *,
    title: str | None,
    caption: str | None,
    context_before: str | None,
    context_after: str | None,
    metadata: dict[str, Any],
) -> str:
    semantic_summary = _build_semantic_summary_text(_normalize_semantic_summary(metadata.get("semantic_summary")))
    table_profile = metadata.get("table_profile") or {}
    header_fields = table_profile.get("header_fields") or []
    candidates = [
        title,
        caption,
        context_before,
        context_after,
        semantic_summary,
        " / ".join(str(item) for item in header_fields[:4]) if header_fields else None,
    ]
    for item in candidates:
        if item:
            return str(item)[:220]
    return "参考原始资产"


def _derive_visual_role(
    *,
    asset: FigureAsset,
    metadata: dict[str, Any],
    title: str | None,
    caption: str | None,
    context_before: str | None,
    context_after: str | None,
) -> str | None:
    if asset.asset_type == "table":
        return "table_asset"
    combined = " ".join(item for item in (title, caption, context_before, context_after) if item)
    if _looks_like_page_furniture_asset(metadata=metadata, text=combined):
        return "page_furniture"
    if metadata.get("visual_role"):
        return str(metadata["visual_role"])
    if FORMULA_VISUAL_PATTERN.search(combined):
        return "formula_candidate"
    if ENGINEERING_VISUAL_PATTERN.search(combined):
        return "engineering_figure"
    return "reference_figure"


def _looks_like_page_furniture_asset(*, metadata: dict[str, Any], text: str) -> bool:
    if PAGE_FURNITURE_PATTERN.search(text):
        return True

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

    return (near_top or near_bottom) and narrow_band and (slim_band or small_area or wide_banner or small_image)


def _derive_asset_type(*, asset: FigureAsset, visual_role: str | None) -> str:
    if asset.asset_type == "table":
        return "table"
    if visual_role == "formula_candidate":
        return "formula_candidate"
    return "figure"


def _derive_review_required(
    *,
    asset: FigureAsset,
    metadata: dict[str, Any],
    asset_type: str,
    visual_role: str | None,
) -> bool:
    if bool(metadata.get("review_required")):
        return True
    if not bool(metadata.get("preserve_in_vector_db", True)):
        return True
    if asset_type in {"table", "formula_candidate"}:
        return True
    return visual_role == "engineering_figure" or str(asset.reuse_mode or "") == "reconstruct_only"


def _derive_risk_level(
    *,
    metadata: dict[str, Any],
    asset_type: str,
    visual_role: str | None,
    review_required: bool,
) -> str:
    reconstruction_status = str(metadata.get("reconstruction_status") or "")
    if asset_type == "formula_candidate":
        return "high"
    if asset_type == "table" and reconstruction_status in {"pending", "queued", "running", "failed", ""}:
        return "high"
    if visual_role == "engineering_figure":
        return "medium"
    if review_required:
        return "medium"
    return "low"


def _keyword_overlap_boost(query: str, text: str) -> float:
    if not query or not text:
        return 0.0
    normalized_text = text.lower()
    keywords = {item.lower() for item in KEYWORD_PATTERN.findall(query)}
    stripped_query = query.strip().lower()
    if stripped_query:
        keywords.add(stripped_query)
    hits = sum(1 for keyword in keywords if keyword and keyword in normalized_text)
    return min(0.24, hits * 0.06)


def _expected_type_boost(asset_type: str, expected_types: list[str]) -> float:
    if not expected_types:
        return 0.0
    normalized = {item.lower() for item in expected_types}
    if asset_type == "table" and {"table", "parameter"} & normalized:
        return 0.12
    if asset_type == "figure" and {"figure", "diagram"} & normalized:
        return 0.12
    if asset_type == "formula_candidate" and {"formula", "equation", "figure"} & normalized:
        return 0.12
    return 0.0


def _asset_summary_boost(*, query: str, section_title: str, card: AssetCard) -> float:
    if not card.semantic_summary_text:
        return 0.0
    query_overlap = _semantic_phrase_overlap_boost(query, card.semantic_summary_text)
    section_overlap = _semantic_phrase_overlap_boost(section_title, card.semantic_summary_text)
    confidence_factor = 0.65 + (card.semantic_summary_confidence * 0.35)
    return min(0.3, (query_overlap * 1.8 + section_overlap * 1.2) * confidence_factor)


def _build_reason(*, card: AssetCard, section_title: str) -> str:
    parts: list[str] = []
    if section_title:
        if _keyword_overlap_boost(section_title, f"{card.heading_path or ''} {card.display_title or card.title or ''}") > 0:
            parts.append(f"与当前章节“{section_title}”主题接近")
        else:
            parts.append(f"可为当前章节“{section_title}”提供参考素材")
    if card.asset_type == "table":
        parts.append("属于原方案中的表格资产，适合作为参数或配置参考")
    elif card.asset_type == "formula_candidate":
        parts.append("属于公式候选资产，可作为技术说明参考")
    else:
        parts.append("属于原方案中的图形资产，可辅助说明系统结构或工程示意")
    if card.review_required:
        parts.append("建议仅作为参考材料，由售前工程师人工复用或替换")
    return "，".join(parts) + "。"


def _normalize_text(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    return str(value).strip() or None


def _normalize_semantic_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if str(value.get("status") or "") not in {"summarized", "reviewed"}:
        return None
    return value


def _build_semantic_summary_text(summary: dict[str, Any] | None) -> str | None:
    if not summary:
        return None
    parts: list[str] = []
    for key in ("title_hint", "diagram_type", "summary", "problem_solved", "principle_summary", "review_notes"):
        value = _normalize_text(summary.get(key))
        if value:
            parts.append(value)
    for key in ("key_components", "signals_or_loops", "applicable_sections", "retrieval_keywords"):
        values = summary.get(key) or []
        if isinstance(values, list):
            normalized = [str(item).strip() for item in values if str(item).strip()]
            if normalized:
                parts.append(" ".join(normalized))
    if not parts:
        return None
    return "；".join(parts)


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _semantic_phrase_overlap_boost(query: str, text: str) -> float:
    if not query or not text:
        return 0.0
    normalized_text = text.lower()
    fragments: set[str] = set()
    for keyword in KEYWORD_PATTERN.findall(query):
        lowered = keyword.lower()
        if len(lowered) >= 2:
            fragments.add(lowered)
        if any("\u4e00" <= char <= "\u9fff" for char in lowered):
            max_size = min(6, len(lowered))
            for size in range(2, max_size + 1):
                for index in range(0, len(lowered) - size + 1):
                    fragments.add(lowered[index : index + size])
    hits = sum(1 for fragment in fragments if fragment in normalized_text)
    return min(0.36, hits * 0.03)


def _build_asset_display_title(
    *,
    title: str | None,
    heading_path: str | None,
    caption: str | None,
    page_no: int | None,
    semantic_summary: dict[str, Any] | None,
) -> str | None:
    if title and not _looks_like_low_value_title(title=title, heading_path=heading_path):
        return title

    title_hint = _normalize_text((semantic_summary or {}).get("title_hint"))
    if title_hint and not _looks_like_generic_diagram_type(title_hint):
        return title_hint

    diagram_type = _normalize_text((semantic_summary or {}).get("diagram_type"))
    if diagram_type and not _looks_like_generic_diagram_type(diagram_type):
        return diagram_type

    summary = _normalize_text((semantic_summary or {}).get("summary"))
    if summary:
        compact = _compact_summary_title(summary)
        if compact:
            return compact

    for fallback in (caption, title, heading_path):
        normalized = _normalize_text(fallback)
        if normalized:
            return normalized
    if page_no is not None:
        return f"参考图 {page_no}"
    return "参考图"


def _looks_like_low_value_title(*, title: str, heading_path: str | None) -> bool:
    normalized = title.strip()
    if not normalized:
        return True
    compact = re.sub(r"\s+", "", normalized)
    if len(compact) <= 2:
        return True
    if TITLE_NOISE_PATTERN.search(normalized):
        return True
    if GENERIC_ASSET_TITLE_PATTERN.fullmatch(normalized):
        return True
    if heading_path and compact == re.sub(r"\s+", "", heading_path):
        return True
    digit_count = sum(char.isdigit() for char in normalized)
    alpha_count = sum(char.isalpha() for char in normalized)
    chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in normalized)
    if digit_count >= 8 and chinese_count == 0 and alpha_count <= 6:
        return True
    if re.fullmatch(r"[0-9A-Za-z .,*_\-\[\]()]+", normalized) and chinese_count == 0 and digit_count >= 4:
        return True
    return False


def _looks_like_generic_diagram_type(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return True
    return bool(GENERIC_DIAGRAM_TYPE_PATTERN.fullmatch(normalized))


def _compact_summary_title(summary: str) -> str | None:
    normalized = summary.strip().strip("。；;")
    normalized = re.sub(r"^(该图|该资产|本图)\s*(为|是|用于|展示|说明)?", "", normalized)
    normalized = re.split(r"[，。；;：:]", normalized, maxsplit=1)[0].strip()
    normalized = normalized[:48]
    if not normalized:
        return None
    if not any(token in normalized for token in ("图", "回路", "系统", "波形", "结构", "接线", "示意", "联锁", "接口")):
        normalized = f"{normalized}示意图"
    return normalized


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(left[index] * left[index] for index in range(size)))
    right_norm = math.sqrt(sum(right[index] * right[index] for index in range(size)))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
