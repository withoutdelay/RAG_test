from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.evidence_bundle import EvidenceBundle
from app.models.job import Job
from app.models.project import Project
from app.models.project_export import ProjectExport
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.models.review_task import ReviewTask
from app.models.section_draft import SectionDraft
from app.models.validation_report import ValidationReport
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.services.validation.service import flatten_outline_sections
from app.utils.object_storage import get_object_storage


SUPPORTED_EXPORT_FORMATS = {"markdown": "md"}


def build_export_snapshot(
    *,
    project: Project,
    outline: ProposalOutline,
    requirement_card: RequirementCard,
    evidence_bundle: EvidenceBundle,
    validation_report: ValidationReport,
    review_tasks: list[ReviewTask],
) -> dict:
    return {
        "project_id": str(project.id),
        "project_status": project.status,
        "draft_version": int(project.current_draft_version or 0),
        "outline_id": str(outline.id),
        "outline_version": outline.version,
        "requirement_card_id": str(requirement_card.id),
        "requirement_card_version": requirement_card.version,
        "evidence_bundle_id": str(evidence_bundle.id),
        "evidence_bundle_version": evidence_bundle.retrieval_version,
        "validation_report_id": str(validation_report.id),
        "validation_status": validation_report.status,
        "review_tasks": [
            {
                "id": str(task.id),
                "task_type": task.task_type,
                "status": task.status,
                "blocking_level": task.blocking_level,
            }
            for task in review_tasks
        ],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def render_export_markdown(
    *,
    project: Project,
    outline: ProposalOutline,
    section_drafts: list[SectionDraft],
    snapshot: dict,
) -> str:
    sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
    drafts_by_id = {draft.section_id: draft for draft in section_drafts}

    lines = [
        f"# {(outline.outline_json or {}).get('title') or project.name}",
        "",
        f"> 项目：{project.name}",
        f"> 导出时间：{snapshot.get('exported_at')}",
        f"> Draft Version：{snapshot.get('draft_version')}",
        f"> Validation Report：{snapshot.get('validation_report_id')}",
        "",
    ]

    citation_sections: list[str] = []
    for section in sections:
        draft = drafts_by_id.get(str(section.get("section_id") or ""))
        if draft is None:
            continue
        content = draft.content_md.strip()
        if not content.startswith("#"):
            content = f"## {draft.title}\n\n{content}"
        lines.extend([content, ""])

        citations = draft.citation_refs if isinstance(draft.citation_refs, list) else []
        if citations:
            citation_sections.append(f"### {draft.title}")
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                heading_path = citation.get("heading_path") or []
                path_text = " > ".join(str(item) for item in heading_path if item)
                citation_sections.append(
                    "- "
                    + " / ".join(
                        part
                        for part in [
                            str(citation.get("evidence_id") or ""),
                            str(citation.get("source_title") or ""),
                            path_text,
                        ]
                        if part
                    )
                )
            citation_sections.append("")

    if citation_sections:
        lines.extend(["---", "", "## 引用清单", "", *citation_sections])

    return "\n".join(lines).strip() + "\n"


class ExportService:
    async def export_project(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        format: str = "markdown",
    ) -> tuple[Job, ProjectExport]:
        normalized_format = format.lower()
        if normalized_format not in SUPPORTED_EXPORT_FORMATS:
            raise ArtifactValidationError("Only markdown export is supported in the current MVP")

        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if project.status not in {"EXPORTABLE", "EXPORTED"}:
            raise ArtifactValidationError("Project is not exportable yet")

        outline = await self._resolve_outline(session=session, project=project)
        requirement_card = await self._resolve_requirement_card(session=session, project=project, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)
        validation_report = await self._resolve_validation_report(session=session, project=project)
        section_drafts = await self._load_section_drafts(session=session, project=project)
        review_tasks = await self._load_review_tasks(session=session, project=project)

        snapshot = build_export_snapshot(
            project=project,
            outline=outline,
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            validation_report=validation_report,
            review_tasks=review_tasks,
        )
        markdown = render_export_markdown(
            project=project,
            outline=outline,
            section_drafts=section_drafts,
            snapshot=snapshot,
        )
        extension = SUPPORTED_EXPORT_FORMATS[normalized_format]

        job = Job(
            project_id=project_id,
            job_type="export",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "draft_version": int(project.current_draft_version or 0),
                "validation_report_id": str(validation_report.id),
                "format": normalized_format,
            },
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        storage = get_object_storage()
        file_name = f"{self._slugify(project.name)}-draft-v{int(project.current_draft_version or 0)}.{extension}"
        with NamedTemporaryFile("w", suffix=f".{extension}", delete=False, encoding="utf-8") as handle:
            handle.write(markdown)
            temp_path = Path(handle.name)
        try:
            storage_path = storage.save(temp_path, prefix=f"{project.id}_export_")
        finally:
            temp_path.unlink(missing_ok=True)

        export_record = ProjectExport(
            project_id=project_id,
            draft_version=int(project.current_draft_version or 0),
            outline_id=outline.id,
            requirement_card_id=requirement_card.id,
            evidence_bundle_id=evidence_bundle.id,
            validation_report_id=validation_report.id,
            file_name=file_name,
            file_type=extension,
            storage_path=storage_path,
            content_md=markdown,
            snapshot=snapshot,
            status="succeeded",
        )
        session.add(export_record)
        await session.flush()

        project.status = "EXPORTED"
        session.add(
            AuditLog(
                project_id=project_id,
                action="export",
                entity_type="export",
                entity_id=export_record.id,
                payload={
                    "draft_version": export_record.draft_version,
                    "file_name": file_name,
                    "storage_path": storage_path,
                    "validation_report_id": str(validation_report.id),
                },
                trace_id=job.trace_id,
            )
        )

        job.status = "succeeded"
        job.output_ref = {"export_id": str(export_record.id), "storage_path": storage_path}
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(export_record)
        return job, export_record

    async def get_latest_export(self, *, session: AsyncSession, project_id: UUID) -> ProjectExport:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        result = await session.scalars(
            select(ProjectExport)
            .where(ProjectExport.project_id == project_id)
            .order_by(ProjectExport.created_at.desc())
            .limit(1)
        )
        export_record = result.first()
        if not export_record:
            raise ArtifactNotFoundError("Export not found")
        return export_record

    async def _resolve_outline(self, *, session: AsyncSession, project: Project) -> ProposalOutline:
        if project.current_outline_id is None:
            raise ArtifactValidationError("Project has no outline to export")
        outline = await session.get(ProposalOutline, project.current_outline_id)
        if not outline:
            raise ArtifactNotFoundError("Outline not found")
        return outline

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        project: Project,
        outline: ProposalOutline,
    ) -> RequirementCard:
        requirement_card_id = outline.requirement_card_id or project.current_requirement_card_id
        if requirement_card_id is None:
            raise ArtifactValidationError("Project has no requirement card to export")
        card = await session.get(RequirementCard, requirement_card_id)
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _resolve_evidence_bundle(self, *, session: AsyncSession, outline: ProposalOutline) -> EvidenceBundle:
        if outline.evidence_bundle_id is None:
            raise ArtifactValidationError("Outline has no evidence bundle binding")
        bundle = await session.get(EvidenceBundle, outline.evidence_bundle_id)
        if not bundle:
            raise ArtifactNotFoundError("Evidence bundle not found")
        return bundle

    async def _resolve_validation_report(self, *, session: AsyncSession, project: Project) -> ValidationReport:
        result = await session.scalars(
            select(ValidationReport)
            .where(ValidationReport.project_id == project.id)
            .order_by(ValidationReport.created_at.desc())
            .limit(1)
        )
        report = result.first()
        if not report:
            raise ArtifactValidationError("Project has not been validated yet")
        if report.status != "passed":
            raise ArtifactValidationError("Latest validation report is not exportable")
        if report.draft_version != int(project.current_draft_version or 0):
            raise ArtifactValidationError("Latest validation report does not match current draft version")
        return report

    async def _load_section_drafts(self, *, session: AsyncSession, project: Project) -> list[SectionDraft]:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project.id,
                SectionDraft.draft_version == int(project.current_draft_version or 0),
            )
            .order_by(SectionDraft.section_id.asc())
        )
        drafts = list(result.all())
        if not drafts:
            raise ArtifactValidationError("Project has no section drafts to export")
        return drafts

    async def _load_review_tasks(self, *, session: AsyncSession, project: Project) -> list[ReviewTask]:
        result = await session.scalars(
            select(ReviewTask)
            .where(ReviewTask.project_id == project.id)
            .order_by(ReviewTask.created_at.asc())
        )
        return list(result.all())

    def _slugify(self, value: str) -> str:
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip())
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized or "proposal-export"
