from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import get_session_factory
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.proposal_outline import ProposalOutline
from app.models.project import Project
from app.models.section_draft import SectionDraft
from app.services.composition.outline_service import normalize_outline_payload


HEADING_PREFIX_RE = re.compile(r"^\s*(?:#+\s*)?(?P<number>\d+(?:\.\d+)*)(?:[、.\s]+)?(?P<title>.+?)\s*$")
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,5}\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class HeadingItem:
    section_id: str
    title: str
    order: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate holdout outline and draft separately, and emit a baseline-outline override JSON."
    )
    parser.add_argument("--project-id", required=True, help="Generated project UUID.")
    parser.add_argument("--baseline-document-name", required=True, help="Holdout/baseline document filename or substring.")
    parser.add_argument("--draft-version", type=int, default=0, help="Draft version. Defaults to latest/current.")
    parser.add_argument(
        "--output",
        default="output/holdout-outline-draft-eval.md",
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="JSON output path. Defaults to --output with .json suffix.",
    )
    parser.add_argument(
        "--outline-override-output",
        default="",
        help="Baseline outline override JSON path. Defaults to --output stem + -outline-override.json.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    project_id = UUID(args.project_id)
    output_path = _resolve_repo_relative_path(args.output)
    json_output_path = _resolve_repo_relative_path(args.json_output) if args.json_output else output_path.with_suffix(".json")
    override_path = (
        _resolve_repo_relative_path(args.outline_override_output)
        if args.outline_override_output
        else output_path.with_name(f"{output_path.stem}-outline-override.json")
    )

    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise SystemExit(f"Project not found: {project_id}")

        outline = await _load_latest_outline(session=session, project_id=project_id)
        generated_outline = dict(outline.outline_json or {})
        baseline_doc = await _find_baseline_document(session=session, name=args.baseline_document_name)
        baseline_headings = await _collect_baseline_headings(session=session, document_id=baseline_doc.id)
        draft_version = args.draft_version or int(project.current_draft_version or 0)
        drafts = await _load_section_drafts(session=session, project_id=project_id, draft_version=draft_version)

    generated_sections = _flatten_outline_sections(generated_outline.get("sections") or [])
    outline_eval = _evaluate_outline(generated_sections=generated_sections, baseline_headings=baseline_headings)
    draft_eval = _evaluate_drafts(drafts=drafts, baseline_headings=baseline_headings)
    override_outline = _build_baseline_outline_override(
        project_name=project.name,
        source_outline=generated_outline,
        baseline_headings=baseline_headings,
    )

    payload = {
        "project_id": str(project_id),
        "project_name": project.name,
        "baseline_document": baseline_doc.filename,
        "outline_id": str(outline.id),
        "draft_version": draft_version,
        "baseline_heading_count": len(baseline_headings),
        "generated_outline_count": len(generated_sections),
        "outline_eval": outline_eval,
        "draft_eval": draft_eval,
        "outline_override_path": str(override_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    override_path.write_text(json.dumps(override_outline, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.write_text(_render_markdown(payload=payload, baseline_headings=baseline_headings), encoding="utf-8")
    print(str(output_path))


async def _load_latest_outline(*, session: Any, project_id: UUID) -> ProposalOutline:
    result = await session.scalars(
        select(ProposalOutline)
        .where(ProposalOutline.project_id == project_id)
        .order_by(ProposalOutline.version.desc(), ProposalOutline.created_at.desc())
        .limit(1)
    )
    outline = result.first()
    if outline is None:
        raise SystemExit(f"Outline not found for project: {project_id}")
    return outline


async def _find_baseline_document(*, session: Any, name: str) -> Document:
    result = await session.scalars(
        select(Document)
        .where(Document.filename.ilike(f"%{name}%"))
        .order_by(Document.created_at.desc())
    )
    documents = list(result.all())
    if not documents:
        raise SystemExit(f"Baseline document not found by name: {name}")
    holdout = [item for item in documents if str(item.doc_type or "") == "holdout_eval"]
    return holdout[0] if holdout else documents[0]


async def _collect_baseline_headings(*, session: Any, document_id: UUID) -> list[HeadingItem]:
    result = await session.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index.asc())
    )
    seen: OrderedDict[str, HeadingItem] = OrderedDict()
    for chunk in result.all():
        candidates = [
            str((chunk.meta or {}).get("source_heading") or "").strip(),
            str((chunk.meta or {}).get("section_path") or "").strip(),
            str(chunk.heading_path or "").strip(),
        ]
        candidates.extend(_markdown_headings(str(chunk.content or "")))
        for candidate in candidates:
            item = _normalize_heading_item(candidate, order=int(chunk.chunk_index or 0))
            if item is None:
                continue
            key = item.section_id or item.title
            if key not in seen:
                seen[key] = item
    items = list(seen.values())
    numbered = [item for item in items if item.section_id]
    return numbered or items


async def _load_section_drafts(*, session: Any, project_id: UUID, draft_version: int) -> list[SectionDraft]:
    result = await session.scalars(
        select(SectionDraft)
        .where(SectionDraft.project_id == project_id, SectionDraft.draft_version == draft_version)
        .order_by(SectionDraft.section_id.asc())
    )
    return list(result.all())


def _markdown_headings(content: str) -> list[str]:
    return [match.group(1).strip() for line in content.splitlines() if (match := MARKDOWN_HEADING_RE.match(line))]


def _normalize_heading_item(value: str, *, order: int) -> HeadingItem | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return None
    if text in {"版本", "页码", "目录"}:
        return None
    match = HEADING_PREFIX_RE.match(text)
    if match:
        number = match.group("number").strip(". ")
        suffix = match.group("title").strip()
        if _looks_like_heading_noise(suffix):
            return None
        title = f"{number} {suffix}"
        return HeadingItem(section_id=number, title=title, order=order)
    if len(text) > 36 or not any("\u4e00" <= char <= "\u9fff" for char in text):
        return None
    return HeadingItem(section_id="", title=text, order=order)


def _looks_like_heading_noise(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    alpha_words = re.findall(r"[A-Za-z]{2,}", text)
    if chinese_count == 0 and not alpha_words:
        return True
    digit_count = sum(char.isdigit() for char in text)
    if digit_count >= 10 and chinese_count == 0 and len(alpha_words) <= 2:
        return True
    return False


def _flatten_outline_sections(sections: list[dict[str, Any]], *, parent: str = "") -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        fallback_id = f"{parent}.{index}" if parent else str(index)
        section_id = str(section.get("section_id") or fallback_id)
        flattened.append({"section_id": section_id, "title": str(section.get("title") or "").strip(), "raw": section})
        children = section.get("children") or []
        if isinstance(children, list):
            flattened.extend(_flatten_outline_sections(children, parent=section_id))
    return flattened


def _evaluate_outline(*, generated_sections: list[dict[str, Any]], baseline_headings: list[HeadingItem]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for baseline in baseline_headings:
        best = max(
            (
                {
                    "section_id": str(section.get("section_id") or ""),
                    "title": str(section.get("title") or ""),
                    "score": _title_similarity(baseline.title, str(section.get("title") or "")),
                }
                for section in generated_sections
            ),
            key=lambda item: item["score"],
            default={"section_id": "", "title": "", "score": 0.0},
        )
        matches.append({"baseline": baseline.__dict__, "best_generated": best})
    missing = [item for item in matches if float(item["best_generated"]["score"]) < 0.34]
    strong = [item for item in matches if float(item["best_generated"]["score"]) >= 0.55]
    return {
        "coverage": round(len(strong) / max(1, len(baseline_headings)), 4),
        "weak_or_missing_count": len(missing),
        "weak_or_missing": missing[:20],
        "matches": matches,
    }


def _evaluate_drafts(*, drafts: list[SectionDraft], baseline_headings: list[HeadingItem]) -> dict[str, Any]:
    baseline_titles = [item.title for item in baseline_headings]
    section_reports: list[dict[str, Any]] = []
    for draft in drafts:
        assets = draft.recommended_assets
        placeholders = re.findall(r"\[\[ASSET:[^\]]+\]\]", draft.content_md or "")
        diagnostics = (
            ((draft.validator_result or {}).get("generation_details") or {})
            .get("retrieval_trace", {})
            .get("layers", {})
            .get("assets", {})
            .get("diagnostics", {})
        )
        section_reports.append(
            {
                "section_id": draft.section_id,
                "title": draft.title,
                "best_baseline_score": max((_title_similarity(draft.title, title) for title in baseline_titles), default=0.0),
                "asset_placeholder_count": len(placeholders),
                "recommended_asset_count": len(assets),
                "recommended_figures": [
                    {
                        "asset_id": str(asset.get("asset_id") or ""),
                        "title": asset.get("title") or asset.get("display_title"),
                        "document_name": asset.get("document_name"),
                        "score": asset.get("score"),
                    }
                    for asset in assets
                    if str(asset.get("asset_type") or "") == "figure"
                ],
                "asset_diagnostics": diagnostics,
            }
        )
    no_asset_sections = [
        item
        for item in section_reports
        if item["recommended_asset_count"] == 0 and _text_has_asset_intent(str(item["title"]))
    ]
    return {
        "section_count": len(section_reports),
        "sections_with_placeholders": sum(1 for item in section_reports if item["asset_placeholder_count"] > 0),
        "asset_intent_without_recommendation": no_asset_sections[:20],
        "sections": section_reports,
    }


def _build_baseline_outline_override(
    *,
    project_name: str,
    source_outline: dict[str, Any],
    baseline_headings: list[HeadingItem],
) -> dict[str, Any]:
    top_sections: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in baseline_headings:
        section_id = item.section_id or str(len(top_sections) + 1)
        parts = section_id.split(".")
        top_id = parts[0]
        if top_id not in top_sections:
            top_sections[top_id] = {
                "section_id": top_id,
                "title": item.title if len(parts) == 1 else top_id,
                "purpose": f"按基准方案《{source_outline.get('title') or project_name}》对应章节结构生成。",
                "children": [],
                "expected_evidence_types": ["section"],
                "asset_required": _text_has_asset_intent(item.title),
            }
        if len(parts) > 1:
            top_sections[top_id]["children"].append(
                {
                    "section_id": section_id,
                    "title": item.title,
                    "purpose": f"覆盖基准章节：{item.title}",
                    "children": [],
                    "expected_evidence_types": ["section", "figure"] if _text_has_asset_intent(item.title) else ["section"],
                    "asset_required": _text_has_asset_intent(item.title),
                }
            )
    payload = {
        "title": source_outline.get("title") or f"{project_name}技术方案",
        "sections": list(top_sections.values()),
        "outline_status": "candidate",
        "generation_strategy": "reuse_first",
        "holdout_eval_override": True,
    }
    return normalize_outline_payload(payload, project_name=project_name)


def _title_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    left_chars = set(_char_ngrams(left))
    right_chars = set(_char_ngrams(right))
    char_score = 0.0
    if left_chars and right_chars:
        char_score = len(left_chars & right_chars) / len(left_chars | right_chars)
    return max(token_score, char_score)


def _tokens(value: str) -> list[str]:
    normalized = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", str(value or "").casefold())
    return TOKEN_RE.findall(normalized)


def _char_ngrams(value: str) -> list[str]:
    normalized = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", str(value or "").casefold())
    normalized = re.sub(r"[\s:：,，.。()（）\-_/]+", "", normalized)
    chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff" or char.isalnum()]
    if len(chars) < 2:
        return chars
    return ["".join(chars[index : index + 2]) for index in range(len(chars) - 1)]


def _text_has_asset_intent(value: str) -> bool:
    return bool(re.search(r"(图|示意|单线|接线|原理|拓扑|曲线|波形|表|参数|清单|配置|规格|数据)", str(value or "")))


def _render_markdown(*, payload: dict[str, Any], baseline_headings: list[HeadingItem]) -> str:
    outline_eval = payload["outline_eval"]
    draft_eval = payload["draft_eval"]
    lines = [
        "# Holdout Outline / Draft Evaluation",
        "",
        f"- Project: {payload['project_name']} (`{payload['project_id']}`)",
        f"- Baseline: {payload['baseline_document']}",
        f"- Outline coverage: {outline_eval['coverage']}",
        f"- Weak or missing baseline headings: {outline_eval['weak_or_missing_count']}",
        f"- Draft sections: {draft_eval['section_count']}",
        f"- Draft sections with asset placeholders: {draft_eval['sections_with_placeholders']}",
        f"- Baseline outline override: `{payload['outline_override_path']}`",
        "",
        "## Baseline Headings",
        "",
    ]
    lines.extend(f"- {item.title}" for item in baseline_headings[:80])
    lines.extend(["", "## Weak / Missing Outline Matches", ""])
    for item in outline_eval["weak_or_missing"][:20]:
        baseline = item["baseline"]
        best = item["best_generated"]
        lines.append(f"- {baseline['title']} -> {best['title'] or 'NO_MATCH'} ({best['score']:.2f})")
    lines.extend(["", "## Asset Gaps", ""])
    for item in draft_eval["asset_intent_without_recommendation"][:20]:
        lines.append(f"- {item['section_id']} {item['title']}")
    return "\n".join(lines) + "\n"


def _resolve_repo_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[2]
    if len(path.parts) >= 2 and path.parts[0] == ".." and path.parts[1] == "output":
        return (repo_root / "output" / Path(*path.parts[2:])).resolve()
    return (repo_root / path).resolve()


if __name__ == "__main__":
    asyncio.run(main())
