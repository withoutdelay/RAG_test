from __future__ import annotations

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
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.services.vectorstore.retriever import Retriever


def build_requirement_query(content: dict[str, Any]) -> str:
    parts = [
        str(content.get("product_line") or "").strip(),
        str(content.get("industry") or "").strip(),
        str(content.get("business_objective") or "").strip(),
        str(content.get("project_name") or "").strip(),
    ]
    query = " ".join(part for part in parts if part)
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


def build_evidence_items(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
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
        raw_content = str(result.get("content") or "")
        items.append(
            {
                "evidence_id": f"ev_{index:03d}",
                "type": evidence_type,
                "source_chunk_id": str(result.get("chunk_id")),
                "source_chunk_type": chunk_type,
                "source_doc_id": str(result.get("document_id")),
                "source_title": result.get("document_name"),
                "page_range": [],
                "heading_path": path_segments,
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


class EvidenceBundleService:
    def __init__(
        self,
        *,
        retriever: Retriever | None = None,
        case_library: CaseLibraryService | None = None,
    ) -> None:
        self.retriever = retriever or Retriever()
        self.case_library = case_library or CaseLibraryService()

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
        case_candidates = self.case_library.retrieve_cases(query=query, top_k=min(top_k, 3), library_tracks={"pilot_main"})
        scoped_document_names = [str(item.get("file_name") or "").strip() for item in case_candidates if str(item.get("file_name") or "").strip()]
        filters = RetrievalFilters(
            industry=card.content.get("industry"),
            doc_type=doc_type or "historical_proposal",
            chunk_type=["PLAIN", "TABLE", "IMAGE"],
            document_names=scoped_document_names or None,
        )
        response = await self.retriever.search(
            session=session,
            request=RetrievalSearchRequest(
                query=query,
                project_id=project_id,
                top_k=top_k,
                filters=filters,
                search_mode="hybrid",
            ),
        )
        results = [item.model_dump(mode="json") for item in response.results]
        retrieval_strategy = "case_first" if scoped_document_names else "global_fallback"
        if not results and scoped_document_names:
            fallback_filters = RetrievalFilters(
                industry=card.content.get("industry"),
                doc_type=doc_type or "historical_proposal",
                chunk_type=["PLAIN", "TABLE", "IMAGE"],
            )
            response = await self.retriever.search(
                session=session,
                request=RetrievalSearchRequest(
                    query=query,
                    project_id=project_id,
                    top_k=top_k,
                    filters=fallback_filters,
                    search_mode="hybrid",
                ),
            )
            results = [item.model_dump(mode="json") for item in response.results]
            retrieval_strategy = "case_first_fallback_global"
        evidence_items = build_evidence_items(results)
        quality_score = self._compute_quality_score(results)
        retrieval_version = await self._next_version(session=session, project_id=project_id)

        bundle = EvidenceBundle(
            project_id=project_id,
            requirement_card_id=card.id,
            retrieval_version=retrieval_version,
            content={
                "query": query,
                "filters": filters.model_dump(exclude_none=True),
                "retrieval_strategy": retrieval_strategy,
                "case_candidates": case_candidates,
                "results": evidence_items,
                "source_requirement_card_id": str(card.id),
            },
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

    def _compute_quality_score(self, results: list[dict[str, Any]]) -> Decimal:
        if not results:
            return Decimal("0.0000")
        score = sum(float(item.get("score") or 0) for item in results[:3]) / min(len(results), 3)
        bounded = min(max(score, 0.0), 1.0)
        return Decimal(f"{bounded:.4f}")
