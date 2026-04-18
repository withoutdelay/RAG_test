from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
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
from app.services.agents.holistic import HolisticAgent
from app.services.evidence_binding import resolve_outline_evidence_bundle
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.services.validation.service import flatten_outline_sections
from app.utils.object_storage import get_object_storage


SUPPORTED_EXPORT_FORMATS = {"markdown": "md"}
ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:(FIGURE|TABLE|FORMULA):([^\]]+)\]\]")


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
        content = _render_export_section_content(draft).strip()
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


def render_export_sections_markdown(
    *,
    outline: ProposalOutline,
    section_drafts: list[SectionDraft],
    render_assets: bool = True,
) -> str:
    sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
    drafts_by_id = {draft.section_id: draft for draft in section_drafts}
    lines: list[str] = []
    for section in sections:
        draft = drafts_by_id.get(str(section.get("section_id") or ""))
        if draft is None:
            continue
        content = (_render_export_section_content(draft) if render_assets else str(draft.content_md or "")).strip()
        if not content.startswith("#"):
            content = f"## {draft.title}\n\n{content}"
        lines.extend([content, ""])
    return "\n".join(lines).strip() + "\n"


def render_export_citations_markdown(section_drafts: list[SectionDraft]) -> str:
    citation_sections: list[str] = []
    for draft in section_drafts:
        citations = draft.citation_refs if isinstance(draft.citation_refs, list) else []
        if not citations:
            continue
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
    if not citation_sections:
        return ""
    return "\n".join(["---", "", "## 引用清单", "", *citation_sections]).strip() + "\n"


def render_export_markdown_from_sections(*, sections_markdown: str, section_drafts: list[SectionDraft]) -> str:
    citations_markdown = render_export_citations_markdown(section_drafts).strip()
    if not citations_markdown:
        return sections_markdown.strip() + "\n"
    return "\n".join([sections_markdown.strip(), "", citations_markdown]).strip() + "\n"


def _render_export_section_content(draft: SectionDraft) -> str:
    content = draft.content_md or ""
    raw_validator_result = getattr(draft, "validator_result", {})
    validator_result = raw_validator_result if isinstance(raw_validator_result, dict) else {}
    recommended_assets = validator_result.get("recommended_assets") if isinstance(validator_result.get("recommended_assets"), list) else []
    asset_lookup = _build_recommended_asset_lookup(recommended_assets)

    def _replace(match: re.Match[str]) -> str:
        placeholder_type = str(match.group(1) or "").upper()
        asset_id = str(match.group(2) or "").strip()
        asset = asset_lookup.get((placeholder_type, asset_id))
        return _render_asset_reference_block(
            placeholder_type=placeholder_type,
            asset_id=asset_id,
            asset=asset,
        )

    return ASSET_PLACEHOLDER_PATTERN.sub(_replace, content)


def _render_asset_placeholders_in_markdown(content_md: str, section_drafts: list[SectionDraft]) -> str:
    asset_lookup: dict[tuple[str, str], dict] = {}
    for draft in section_drafts:
        raw_validator_result = getattr(draft, "validator_result", {})
        validator_result = raw_validator_result if isinstance(raw_validator_result, dict) else {}
        recommended_assets = validator_result.get("recommended_assets") if isinstance(validator_result.get("recommended_assets"), list) else []
        asset_lookup.update(_build_recommended_asset_lookup(recommended_assets))

    def _replace(match: re.Match[str]) -> str:
        placeholder_type = str(match.group(1) or "").upper()
        asset_id = str(match.group(2) or "").strip()
        return _render_asset_reference_block(
            placeholder_type=placeholder_type,
            asset_id=asset_id,
            asset=asset_lookup.get((placeholder_type, asset_id)),
        )

    return ASSET_PLACEHOLDER_PATTERN.sub(_replace, str(content_md or ""))


def _build_recommended_asset_lookup(recommended_assets: list[dict]) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    for asset in recommended_assets:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            continue
        asset_type = _normalize_asset_placeholder_type(str(asset.get("asset_type") or ""))
        if not asset_type:
            continue
        lookup[(asset_type, asset_id)] = asset
    return lookup


def _normalize_asset_placeholder_type(asset_type: str) -> str:
    normalized = str(asset_type or "").strip().lower()
    if normalized == "figure":
        return "FIGURE"
    if normalized == "table":
        return "TABLE"
    if normalized == "formula_candidate":
        return "FORMULA"
    return ""


def _render_asset_reference_block(*, placeholder_type: str, asset_id: str, asset: dict | None) -> str:
    type_label = {
        "FIGURE": "建议插入图片",
        "TABLE": "建议插入表格",
        "FORMULA": "建议插入公式",
    }.get(placeholder_type, "建议插入资产")
    if not asset:
        return "\n".join(
            [
                f"> [{type_label}]",
                f"> 资产占位符：[[ASSET:{placeholder_type}:{asset_id}]]",
                "> 说明：当前未找到对应资产详情，请在审稿时手动补充。",
            ]
        )

    title = str(asset.get("display_title") or asset.get("title") or asset.get("caption") or asset.get("preview_text") or "参考资产").strip()
    document_name = str(asset.get("document_name") or "未知文档").strip()
    page_no = asset.get("page_no")
    heading_path = str(asset.get("heading_path") or "").strip()
    reason = str(asset.get("reason") or "").strip()
    preview_text = str(asset.get("preview_text") or "").strip()
    review_required = bool(asset.get("review_required"))

    source_parts = [document_name]
    if page_no is not None:
        source_parts.append(f"第 {page_no} 页")
    if heading_path:
        source_parts.append(heading_path)

    lines = [
        f"> [{type_label}] {title}",
        f"> 资产占位符：[[ASSET:{placeholder_type}:{asset_id}]]",
        f"> 来源：{' / '.join(part for part in source_parts if part)}",
    ]
    if reason:
        lines.append(f"> 推荐原因：{reason}")
    if preview_text:
        lines.append(f"> 参考说明：{preview_text}")
    lines.append("> 使用建议：需人工确认后复用或替换。" if review_required else "> 使用建议：可作为参考资产插入当前章节。")
    return "\n".join(lines)


def _extract_export_global_params(requirement_card: RequirementCard) -> dict:
    content = requirement_card.content if isinstance(requirement_card.content, dict) else {}
    if isinstance(content.get("global_params"), dict):
        return dict(content["global_params"])
    if isinstance(content.get("fields"), dict):
        return dict(content["fields"])
    return dict(content)


def _holistic_output_is_acceptable(content_md: str, *, section_drafts: list[SectionDraft]) -> bool:
    content = str(content_md or "").strip()
    if not content.startswith("#"):
        return False
    original_chars = sum(len(str(draft.content_md or "")) for draft in section_drafts)
    if original_chars and len(content) < max(200, int(original_chars * 0.65)):
        return False
    original_placeholders = set(ASSET_PLACEHOLDER_PATTERN.findall("\n".join(str(draft.content_md or "") for draft in section_drafts)))
    output_placeholders = set(ASSET_PLACEHOLDER_PATTERN.findall(content))
    return original_placeholders.issubset(output_placeholders)


def _holistic_section_output_is_acceptable(content_md: str, *, original_content_md: str) -> bool:
    content = str(content_md or "").strip()
    if not content.startswith("#"):
        return False
    original_chars = len(str(original_content_md or ""))
    if original_chars and len(content) < max(80, int(original_chars * 0.55)):
        return False
    original_placeholders = set(ASSET_PLACEHOLDER_PATTERN.findall(str(original_content_md or "")))
    output_placeholders = set(ASSET_PLACEHOLDER_PATTERN.findall(content))
    return original_placeholders.issubset(output_placeholders)


def _normalize_finalized_section_markdown(*, content_md: str, title: str) -> str:
    lines = str(content_md or "").strip().splitlines()
    if not lines:
        return f"## {title}\n"
    first = lines[0].strip()
    if first.startswith("# "):
        lines[0] = f"## {title}"
    elif not first.startswith("## "):
        lines.insert(0, f"## {title}")
    lines = _dedupe_leading_section_heading(lines=lines, title=title)
    return "\n".join(lines).strip() + "\n"


def _dedupe_leading_section_heading(*, lines: list[str], title: str) -> list[str]:
    if not lines:
        return lines
    target = _normalize_markdown_heading_text(f"## {title}")
    deduped = [lines[0].rstrip()]
    index = 1

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("#") and _normalize_markdown_heading_text(stripped) == target:
            index += 1
            continue
        break

    if index < len(lines):
        deduped.append("")
        deduped.extend(lines[index:])
    return deduped


def _normalize_markdown_heading_text(line: str) -> str:
    text = re.sub(r"^#+\s*", "", str(line or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def summarize_holistic_finalization_trace(trace: dict) -> dict:
    if not isinstance(trace, dict):
        return {"enabled": False, "status": "unknown"}
    sections = trace.get("sections") if isinstance(trace.get("sections"), list) else []
    succeeded_sections = int(trace.get("succeeded_sections") or 0)
    fallback_sections = sum(
        1
        for section in sections
        if isinstance(section, dict) and str(section.get("status") or "").lower() == "fallback"
    )
    summary = {
        "enabled": bool(trace.get("enabled")),
        "mode": str(trace.get("mode") or "section"),
        "status": str(trace.get("status") or "unknown"),
        "input_chars": int(trace.get("input_chars") or 0),
        "output_chars": int(trace.get("output_chars") or 0),
    }
    if sections:
        summary.update(
            {
                "section_count": len(sections),
                "succeeded_sections": succeeded_sections,
                "fallback_sections": fallback_sections,
            }
        )
    return summary


class ExportService:
    def __init__(self, *, settings: Settings | None = None, holistic: HolisticAgent | None = None) -> None:
        self.settings = settings or get_settings()
        self.holistic = holistic

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
        markdown, holistic_trace = await self._maybe_finalize_markdown(
            task_id=f"export-{project.id}-{int(project.current_draft_version or 0)}",
            project=project,
            outline=outline,
            requirement_card=requirement_card,
            section_drafts=section_drafts,
            current_markdown=markdown,
        )
        snapshot["holistic_finalization"] = holistic_trace
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
        job.output_ref = {
            "export_id": str(export_record.id),
            "storage_path": storage_path,
            "holistic_finalization": summarize_holistic_finalization_trace(holistic_trace),
        }
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(export_record)
        return job, export_record

    async def _maybe_finalize_markdown(
        self,
        *,
        task_id: str,
        project: Project,
        outline: ProposalOutline,
        requirement_card: RequirementCard,
        section_drafts: list[SectionDraft],
        current_markdown: str,
    ) -> tuple[str, dict]:
        enabled = bool(getattr(self.settings, "export_holistic_finalization_enabled", False))
        trace = {
            "enabled": enabled,
            "mode": str(getattr(self.settings, "export_holistic_finalization_mode", "section") or "section"),
            "status": "skipped",
            "input_chars": 0,
            "output_chars": len(current_markdown or ""),
        }
        if not enabled:
            return current_markdown, trace

        mode = str(trace["mode"]).lower()
        if mode == "document":
            return await self._finalize_markdown_as_document(
                task_id=task_id,
                project=project,
                outline=outline,
                requirement_card=requirement_card,
                section_drafts=section_drafts,
                current_markdown=current_markdown,
                trace=trace,
            )
        return await self._finalize_markdown_by_section(
            task_id=task_id,
            project=project,
            outline=outline,
            requirement_card=requirement_card,
            section_drafts=section_drafts,
            current_markdown=current_markdown,
            trace=trace,
        )

    async def _finalize_markdown_as_document(
        self,
        *,
        task_id: str,
        project: Project,
        outline: ProposalOutline,
        requirement_card: RequirementCard,
        section_drafts: list[SectionDraft],
        current_markdown: str,
        trace: dict,
    ) -> tuple[str, dict]:
        title = str((outline.outline_json or {}).get("title") or project.name)
        raw_sections_markdown = render_export_sections_markdown(
            outline=outline,
            section_drafts=section_drafts,
            render_assets=False,
        )
        holistic_input = f"# {title}\n\n{raw_sections_markdown.strip()}\n"
        trace["input_chars"] = len(holistic_input)
        try:
            holistic = self.holistic or HolisticAgent()
            response = await holistic.finalize(
                task_id=task_id,
                outline_title=title,
                global_params=_extract_export_global_params(requirement_card),
                all_sections_markdown=holistic_input,
            )
            finalized = str(response.content or "").strip()
            if not _holistic_output_is_acceptable(finalized, section_drafts=section_drafts):
                raise ArtifactValidationError("Holistic finalization output failed safety checks")
            finalized = _render_asset_placeholders_in_markdown(finalized, section_drafts)
            markdown = render_export_markdown_from_sections(
                sections_markdown=finalized,
                section_drafts=section_drafts,
            )
            trace.update({"status": "succeeded", "output_chars": len(markdown)})
            return markdown, trace
        except Exception as exc:  # noqa: BLE001
            trace.update({"status": "fallback", "error": str(exc), "output_chars": len(current_markdown or "")})
            return current_markdown, trace

    async def _finalize_markdown_by_section(
        self,
        *,
        task_id: str,
        project: Project,
        outline: ProposalOutline,
        requirement_card: RequirementCard,
        section_drafts: list[SectionDraft],
        current_markdown: str,
        trace: dict,
    ) -> tuple[str, dict]:
        title = str((outline.outline_json or {}).get("title") or project.name)
        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        drafts_by_id = {draft.section_id: draft for draft in section_drafts}
        ordered_drafts = [
            draft
            for section in sections
            if (draft := drafts_by_id.get(str(section.get("section_id") or ""))) is not None
        ]
        holistic = self.holistic or HolisticAgent()
        global_params = _extract_export_global_params(requirement_card)
        finalized_sections: list[str] = []
        section_traces: list[dict] = []
        success_count = 0
        input_chars = 0

        for index, draft in enumerate(ordered_drafts, start=1):
            original_content = str(draft.content_md or "").strip()
            section_input = original_content if original_content.startswith("#") else f"## {draft.title}\n\n{original_content}"
            input_chars += len(section_input)
            section_trace = {
                "section_id": str(draft.section_id),
                "title": str(draft.title),
                "input_chars": len(section_input),
                "status": "pending",
            }
            try:
                response = await holistic.finalize(
                    task_id=f"{task_id}-section-{index}",
                    outline_title=str(draft.title),
                    global_params=global_params,
                    all_sections_markdown=section_input,
                )
                finalized = str(response.content or "").strip()
                if not _holistic_section_output_is_acceptable(
                    finalized,
                    original_content_md=original_content,
                ):
                    raise ArtifactValidationError("Holistic section output failed safety checks")
                normalized = _normalize_finalized_section_markdown(content_md=finalized, title=str(draft.title))
                finalized_sections.append(normalized)
                section_trace.update({"status": "succeeded", "output_chars": len(normalized)})
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                fallback = section_input.strip() + "\n"
                finalized_sections.append(fallback)
                section_trace.update({"status": "fallback", "error": str(exc), "output_chars": len(fallback)})
            section_traces.append(section_trace)

        trace["input_chars"] = input_chars
        trace["sections"] = section_traces
        if success_count <= 0:
            trace.update({"status": "fallback", "output_chars": len(current_markdown or "")})
            return current_markdown, trace

        sections_markdown = f"# {title}\n\n" + "\n\n".join(item.strip() for item in finalized_sections if item.strip()) + "\n"
        sections_markdown = _render_asset_placeholders_in_markdown(sections_markdown, section_drafts)
        markdown = render_export_markdown_from_sections(
            sections_markdown=sections_markdown,
            section_drafts=section_drafts,
        )
        status = "succeeded" if success_count == len(ordered_drafts) else "partial_fallback"
        trace.update({"status": status, "output_chars": len(markdown), "succeeded_sections": success_count})
        return markdown, trace

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
        return await resolve_outline_evidence_bundle(session=session, outline=outline)

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
