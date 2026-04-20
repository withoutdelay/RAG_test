from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.requirement_card import RequirementCard
from app.schemas.retrieval import RetrievalFilters, RetrievalSearchRequest
from app.services.retrieval.case_service import CaseLibraryService
from app.services.retrieval.query_hints import PRODUCT_LINE_QUERY_HINTS
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.services.vectorstore.chunk_quality import flatten_heading_text, is_noise_chunk
from app.services.vectorstore.retriever import Retriever

TECHNICAL_QUERY_TERMS = (
    "LCI",
    "变频",
    "变频器",
    "软起",
    "软起动",
    "同步电机",
    "异步电机",
    "永磁电机",
    "鼓风机",
    "高炉鼓风机",
    "环冷风机",
    "压缩机",
    "DCS",
    "PLC",
    "联锁",
    "接口",
    "供货范围",
    "输入变压器",
    "输出变压器",
    "控制盘",
)
TECHNICAL_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kV|KV|V|MW|kW|KW|MVA|kVA|Hz|A)\b|"
    r"(?:同步电机|异步电机|永磁电机|高炉鼓风机|鼓风机|压缩机|LCI|DCS|PLC|联锁|供货范围|接口|变频器|软起动)",
    re.IGNORECASE,
)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
READABLE_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


def _strip_nul_text(value: str) -> str:
    return CONTROL_CHAR_PATTERN.sub("", str(value or ""))


def _looks_readable_text(value: str) -> bool:
    normalized = _strip_nul_text(value).strip()
    if not normalized:
        return False
    readable_count = len(READABLE_CHAR_PATTERN.findall(normalized))
    return readable_count / max(len(normalized), 1) >= 0.35


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_nul_text(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    return value


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _normalize_query_values(values: Any) -> list[str]:
    if isinstance(values, dict):
        flattened: list[str] = []
        for key, value in values.items():
            normalized_value = str(value or "").strip()
            if normalized_value:
                flattened.append(normalized_value)
            normalized_key = str(key or "").strip()
            if normalized_key and any(token in normalized_key.casefold() for token in ("voltage", "power", "motor", "control", "interface")):
                flattened.append(normalized_key)
        return _dedupe_keep_order(flattened)
    if isinstance(values, (list, tuple, set)):
        return _dedupe_keep_order([str(item) for item in values if str(item or "").strip()])
    normalized = str(values or "").strip()
    return [normalized] if normalized else []


def _extract_requirement_query_hints(content: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    product_line = str(content.get("product_line") or "").strip().lower()
    hints.extend(PRODUCT_LINE_QUERY_HINTS.get(product_line, ()))
    for value in _normalize_query_values(content.get("key_parameters") or {}):
        hints.append(value)
    for value in _normalize_query_values(content.get("constraints") or []):
        hints.append(value)
    source_text = " ".join(
        part
        for part in (
            str(content.get("business_objective") or "").strip(),
            str(content.get("source_excerpt") or "").strip(),
        )
        if part
    )
    if source_text:
        for pattern_match in TECHNICAL_PATTERN.findall(source_text):
            if isinstance(pattern_match, tuple):
                for item in pattern_match:
                    if item:
                        hints.append(str(item))
            elif pattern_match:
                hints.append(str(pattern_match))
        lowered_source = source_text.casefold()
        for token in TECHNICAL_QUERY_TERMS:
            if token.casefold() in lowered_source:
                hints.append(token)
    return _dedupe_keep_order(hints)


def build_requirement_query(content: dict[str, Any]) -> str:
    parts = [
        str(content.get("product_line") or "").strip(),
        str(content.get("industry") or "").strip(),
        str(content.get("business_objective") or "").strip(),
        str(content.get("project_name") or "").strip(),
    ]
    parts.extend(_extract_requirement_query_hints(content))
    query = " ".join(part for part in _dedupe_keep_order(parts) if part)
    return query or "售前方案 需求分析"


def _compute_reusability_score(result: dict[str, Any]) -> float:
    score = float(result.get("score") or 0)
    metadata = result.get("metadata") or {}
    content_risk_level = str(metadata.get("content_risk_level") or "").lower()
    if content_risk_level == "high":
        score *= 0.55
    elif content_risk_level == "medium":
        score *= 0.8
    if metadata.get("front_matter"):
        score *= 0.35
    if metadata.get("needs_asset_lookup"):
        score *= 0.85
    return round(min(max(score, 0.0), 1.0), 4)


def _is_noise_evidence_result(result: dict[str, Any]) -> bool:
    raw_content = str(result.get("content") or "").strip()
    if not raw_content:
        return True
    heading_text = _strip_nul_text(flatten_heading_text(result.get("heading_path")))
    chunk_type = str(result.get("chunk_type") or "PLAIN").upper()
    return is_noise_chunk(
        chunk_type=chunk_type,
        raw_content=raw_content,
        heading_path=heading_text,
    )


def filter_evidence_results(results: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    filtered = [item for item in results if not _is_noise_evidence_result(item)]
    if limit is not None:
        return filtered[:limit]
    return filtered


def build_evidence_search_plan(
    *,
    project_id: UUID,
    industry: str | None,
    doc_type: str,
    chunk_types: list[str],
    scoped_document_names: list[str],
) -> list[tuple[str, UUID | None, RetrievalFilters]]:
    search_project_id = None if doc_type == "historical_proposal" else project_id
    normalized_industry = str(industry).strip() or None if industry is not None else None
    normalized_document_names = [name for name in scoped_document_names if name]

    attempts: list[tuple[str, UUID | None, RetrievalFilters]] = []
    if normalized_document_names:
        attempts.append(
            (
                "case_first",
                search_project_id,
                RetrievalFilters(
                    industry=normalized_industry,
                    doc_type=doc_type,
                    chunk_type=chunk_types,
                    document_names=normalized_document_names,
                ),
            )
        )
        if normalized_industry:
            attempts.append(
                (
                    "case_first_relaxed_industry",
                    search_project_id,
                    RetrievalFilters(
                        doc_type=doc_type,
                        chunk_type=chunk_types,
                        document_names=normalized_document_names,
                    ),
                )
            )
        attempts.append(
            (
                "case_first_fallback_global",
                search_project_id,
                RetrievalFilters(
                    doc_type=doc_type,
                    chunk_type=chunk_types,
                ),
            )
        )
        return attempts

    attempts.append(
        (
            "global_fallback",
            search_project_id,
            RetrievalFilters(
                industry=normalized_industry,
                doc_type=doc_type,
                chunk_type=chunk_types,
            ),
        )
    )
    if normalized_industry:
        attempts.append(
            (
                "global_fallback_relaxed_industry",
                search_project_id,
                RetrievalFilters(
                    doc_type=doc_type,
                    chunk_type=chunk_types,
                ),
            )
        )
    return attempts


def build_evidence_items(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    filtered_results = filter_evidence_results(results)
    for index, result in enumerate(filtered_results, start=1):
        heading_path = result.get("heading_path")
        if heading_path:
            path_segments = [segment.strip() for segment in str(heading_path).split(">") if segment.strip()]
        else:
            path_segments = []
        chunk_type = str(result.get("chunk_type") or "section").upper()
        evidence_type = "section"
        if chunk_type == "TABLE":
            evidence_type = "table"
        elif chunk_type == "IMAGE":
            evidence_type = "figure"
        metadata = result.get("metadata") or {}
        raw_content = _strip_nul_text(str(result.get("content") or ""))
        items.append(
            {
                "evidence_id": f"ev_{index:03d}",
                "type": evidence_type,
                "source_chunk_id": str(result.get("chunk_id")),
                "source_chunk_type": chunk_type,
                "source_doc_id": str(result.get("document_id")),
                "source_title": _strip_nul_text(str(result.get("document_name") or "")),
                "page_range": [],
                "heading_path": [_strip_nul_text(segment) for segment in path_segments],
                "summary": raw_content[:180],
                "raw_content": raw_content,
                "relevance_score": float(result.get("score") or 0),
                "reusability_score": _compute_reusability_score(result),
                "recommended_use": f"可用于 {result.get('chunk_type', '章节')} 相关内容起草",
                "risk_note": None,
                "section_type": metadata.get("section_type") or "unknown",
                "equipment_type": metadata.get("equipment_type") or "generic",
                "content_form": metadata.get("content_form") or "narrative",
                "metadata": metadata,
            }
        )
    return items


def build_case_fallback_evidence_items(case_candidates: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, candidate in enumerate(case_candidates[:limit], start=1):
        top_level_titles = [
            _strip_nul_text(str(item).strip())
            for item in (candidate.get("top_level_titles") or [])
            if str(item).strip() and _looks_readable_text(str(item))
        ]
        retrieval_text = _strip_nul_text(str(candidate.get("retrieval_text") or "").strip())
        reason = _strip_nul_text(str(candidate.get("reason") or "").strip())
        source_title = _strip_nul_text(str(candidate.get("file_name") or "").strip())
        summary_parts = [
            f"匹配原因：{reason}" if reason else "",
            f"案例来源：{source_title}" if source_title else "",
        ]
        raw_content = "\n".join(part for part in summary_parts if part).strip() or retrieval_text[:420]
        items.append(
            {
                "evidence_id": f"case_ev_{index:03d}",
                "type": "case_summary",
                "source_chunk_id": f"case:{candidate.get('sample_id')}",
                "source_chunk_type": "CASE_SUMMARY",
                "source_doc_id": str(candidate.get("sample_id") or ""),
                "source_title": source_title,
                "page_range": [],
                "heading_path": [source_title] if source_title else top_level_titles[:3],
                "summary": raw_content[:180],
                "raw_content": raw_content,
                "relevance_score": float(candidate.get("score") or 0),
                "reusability_score": round(min(max(float(candidate.get("score") or 0) * 0.92, 0.0), 1.0), 4),
                "recommended_use": "可用于整份方案结构参考和章节定位。",
                "risk_note": "当前为案例级 fallback 证据，非精确 chunk 命中。",
                "section_type": "case_summary",
                "equipment_type": "generic",
                "content_form": "outline_summary",
                "metadata": {
                    "fallback_source": "case_library",
                    "sample_id": candidate.get("sample_id"),
                    "profile": candidate.get("profile"),
                    "library_track": candidate.get("library_track"),
                    "reason": reason,
                    "top_level_titles": top_level_titles[:8],
                },
            }
        )
    return items


class EvidenceBundleService:
    def __init__(
        self,
        *,
        retriever: Retriever | None = None,
        case_library: CaseLibraryService | None = None,
    ) -> None:
        self._retriever = retriever
        self.case_library = case_library or CaseLibraryService()

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever()
        return self._retriever

    async def retrieve_evidence(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None = None,
        top_k: int = 6,
        doc_type: str | None = None,
    ) -> tuple[Job, EvidenceBundle]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        card = await self._resolve_requirement_card(
            session=session,
            project_id=project_id,
            requirement_card_id=requirement_card_id,
        )
        if any(item.get("status") != "resolved" for item in (card.blocking_items or [])):
            raise ArtifactValidationError("Requirement card still has unresolved blocking items")

        job = Job(
            project_id=project_id,
            job_type="retrieve",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "requirement_card_id": str(card.id)},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        query = build_requirement_query(card.content or {})
        resolved_doc_type = doc_type or "historical_proposal"
        chunk_types = ["PLAIN", "TABLE", "IMAGE"]
        case_candidates = self.case_library.retrieve_cases(query=query, top_k=min(top_k, 3), library_tracks={"pilot_main"})
        scoped_document_names = [str(item.get("file_name") or "").strip() for item in case_candidates if str(item.get("file_name") or "").strip()]
        search_plan = build_evidence_search_plan(
            project_id=project_id,
            industry=card.content.get("industry"),
            doc_type=resolved_doc_type,
            chunk_types=chunk_types,
            scoped_document_names=scoped_document_names,
        )
        retrieval_strategy = search_plan[0][0]
        active_filters = search_plan[0][2]
        results: list[dict[str, Any]] = []
        retrieval_attempts: list[dict[str, Any]] = []

        for strategy_name, search_project_id, filters in search_plan:
            response = await self.retriever.search(
                session=session,
                request=RetrievalSearchRequest(
                    query=query,
                    project_id=search_project_id,
                    top_k=max(top_k * 3, top_k + 4),
                    filters=filters,
                    search_mode="hybrid",
                ),
            )
            raw_results = [item.model_dump(mode="json") for item in response.results]
            results = filter_evidence_results(raw_results, limit=top_k)
            retrieval_attempts.append(
                {
                    "strategy": strategy_name,
                    "project_scope": str(search_project_id) if search_project_id is not None else "global",
                    "raw_result_count": len(raw_results),
                    "filtered_result_count": len(results),
                    "filters": filters.model_dump(exclude_none=True),
                }
            )
            retrieval_strategy = strategy_name
            active_filters = filters
            if results:
                break
        evidence_items = build_evidence_items(results)
        fallback_items = build_case_fallback_evidence_items(case_candidates, limit=min(top_k, 3)) if not evidence_items else []
        bundle_results = evidence_items or fallback_items
        quality_source = "retrieval_results" if evidence_items else ("case_fallback" if fallback_items else "empty")
        quality_score = self._compute_quality_score(bundle_results, quality_source=quality_source)
        quality_trace = {
            "query": query,
            "query_hints": _extract_requirement_query_hints(card.content or {}),
            "search_attempts": retrieval_attempts,
            "case_candidate_count": len(case_candidates),
            "case_fallback_used": bool(fallback_items),
            "case_fallback_count": len(fallback_items),
            "case_fallback_top_score": max((float(item.get("relevance_score") or 0) for item in fallback_items), default=0.0),
            "primary_results_source": quality_source,
            "primary_result_count": len(bundle_results),
        }
        retrieval_version = await self._next_version(session=session, project_id=project_id)

        bundle = EvidenceBundle(
            project_id=project_id,
            requirement_card_id=card.id,
            retrieval_version=retrieval_version,
            content=_sanitize_json_value({
                "query": query,
                "filters": active_filters.model_dump(exclude_none=True),
                "retrieval_strategy": retrieval_strategy,
                "case_candidates": case_candidates,
                "results": bundle_results,
                "fallback_results": fallback_items,
                "quality_trace": quality_trace,
                "source_requirement_card_id": str(card.id),
            }),
            quality_score=quality_score,
        )
        session.add(bundle)
        await session.flush()

        project.status = "EVIDENCE_READY"
        job.status = "succeeded"
        job.output_ref = {"evidence_bundle_id": str(bundle.id)}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(bundle)
        return job, bundle

    async def get_latest_evidence_bundle(self, *, session: AsyncSession, project_id: UUID) -> EvidenceBundle:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        result = await session.scalars(
            select(EvidenceBundle)
            .where(EvidenceBundle.project_id == project_id)
            .order_by(EvidenceBundle.retrieval_version.desc(), EvidenceBundle.created_at.desc())
            .limit(1)
        )
        bundle = result.first()
        if not bundle:
            raise ArtifactNotFoundError("Evidence bundle not found")
        return bundle

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        requirement_card_id: UUID | None,
    ) -> RequirementCard:
        if requirement_card_id is not None:
            card = await session.get(RequirementCard, requirement_card_id)
            if not card or card.project_id != project_id:
                raise ArtifactNotFoundError("Requirement card not found")
            return card

        result = await session.scalars(
            select(RequirementCard)
            .where(RequirementCard.project_id == project_id)
            .order_by(RequirementCard.version.desc(), RequirementCard.created_at.desc())
            .limit(1)
        )
        card = result.first()
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _next_version(self, *, session: AsyncSession, project_id: UUID) -> int:
        latest = await session.scalar(
            select(func.max(EvidenceBundle.retrieval_version)).where(EvidenceBundle.project_id == project_id)
        )
        return int(latest or 0) + 1

    def _compute_quality_score(
        self,
        results: list[dict[str, Any]],
        *,
        quality_source: str = "retrieval_results",
    ) -> Decimal:
        if not results:
            return Decimal("0.0000")
        score_values = [
            float(item.get("relevance_score") or item.get("score") or 0)
            for item in results[:3]
        ]
        base_score = sum(score_values) / min(len(score_values), 3)
        if quality_source == "case_fallback":
            bounded = min(max(0.39 + (base_score * 0.35) + (min(len(score_values), 3) * 0.03), 0.0), 0.79)
        else:
            bounded = min(max(base_score, 0.0), 1.0)
        return Decimal(f"{bounded:.4f}")
