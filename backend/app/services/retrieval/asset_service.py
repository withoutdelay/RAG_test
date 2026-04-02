from __future__ import annotations

from dataclasses import dataclass
import math
import re
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
    caption: str | None
    source_ref: str | None
    asset_uri: str
    preview_text: str
    retrieval_text: str
    section_type: str
    equipment_type: str
    content_form: str
    metadata: dict[str, Any]


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
        target_taxonomy = infer_target_taxonomy(section_context or {})
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
            type_boost = _expected_type_boost(card.asset_type, expected_types)
            taxonomy_boost = _asset_taxonomy_boost(card=card, target_taxonomy=target_taxonomy)
            anchor_boost = _asset_anchor_boost(
                card=card,
                anchor_document_names=anchor_document_names,
                anchor_headings=anchor_headings,
            )
            noise_penalty = _asset_noise_penalty(card=card, target_section_type=str(target_taxonomy.get("section_type") or "unknown"))
            risk_penalty = {"high": 0.08, "medium": 0.03}.get(card.risk_level, 0.0)
            final_score = semantic_score + metadata_boost + section_boost + type_boost + taxonomy_boost + anchor_boost - risk_penalty - noise_penalty
            scored_cards.append((final_score, card))

        scored_cards.sort(key=lambda item: item[0], reverse=True)
        results = [
            _to_result(card=card, score=score, section_title=section_title)
            for score, card in scored_cards[:top_k]
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
        caption=caption,
        source_ref=_normalize_text(metadata.get("source_ref")),
        asset_uri=asset.asset_uri,
        preview_text=preview_text,
        retrieval_text=retrieval_text,
        section_type=str(taxonomy.get("section_type") or "unknown"),
        equipment_type=str(taxonomy.get("equipment_type") or "generic"),
        content_form=str(taxonomy.get("content_form") or ("figure" if asset_type == "figure" else "parameter_table")),
        metadata=metadata,
    )


def _to_result(*, card: AssetCard, score: float, section_title: str) -> AssetSearchResult:
    metadata = dict(card.metadata or {})
    metadata.setdefault("section_type", card.section_type)
    metadata.setdefault("equipment_type", card.equipment_type)
    metadata.setdefault("content_form", card.content_form)
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
        title=card.title,
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
        heading_text=" ".join(part for part in (card.heading_path, card.title, card.caption) if part),
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
    text = " ".join(part for part in (card.heading_path, card.title, card.caption, card.preview_text) if part).casefold()
    penalty = 0.0
    if card.visual_role == "page_furniture":
        penalty += 0.32
    if any(token in text for token in ("检测报告", "检验", "认证", "证书", "质量保证", "文档控制", "公司简介")):
        penalty += 0.22
    if target_section_type in {"main_circuit_scheme", "overall_solution", "communication_interface"} and any(
        token in text for token in ("检测报告", "认证", "证书")
    ):
        penalty += 0.18
    return penalty


def _build_preview_text(
    *,
    title: str | None,
    caption: str | None,
    context_before: str | None,
    context_after: str | None,
    metadata: dict[str, Any],
) -> str:
    table_profile = metadata.get("table_profile") or {}
    header_fields = table_profile.get("header_fields") or []
    candidates = [
        title,
        caption,
        context_before,
        context_after,
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
    if metadata.get("visual_role"):
        return str(metadata["visual_role"])
    if asset.asset_type == "table":
        return "table_asset"
    combined = " ".join(item for item in (title, caption, context_before, context_after) if item)
    if FORMULA_VISUAL_PATTERN.search(combined):
        return "formula_candidate"
    if ENGINEERING_VISUAL_PATTERN.search(combined):
        return "engineering_figure"
    return "reference_figure"


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


def _build_reason(*, card: AssetCard, section_title: str) -> str:
    parts: list[str] = []
    if section_title:
        if _keyword_overlap_boost(section_title, f"{card.heading_path or ''} {card.title or ''}") > 0:
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
