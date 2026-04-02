from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from app.config import get_settings
from app.services.parsing.section_catalog import build_heading_aliases, normalize_section_heading
from app.services.vectorstore.block_taxonomy import (
    content_form_is_table,
    extract_taxonomy_hints,
    heading_focus_adjustment,
    heading_family_similarity,
    heading_looks_like_document_title,
    infer_target_taxonomy,
    related_section_types,
    support_content_forms,
)


CASE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
CASE_STOPWORDS = {
    "项目",
    "方案",
    "技术",
    "系统",
    "说明",
    "当前",
    "相关",
    "以及",
    "进行",
}
LIGHT_CONTEXT_TERMS = {
    "钢铁",
    "冶金",
    "矿山",
    "水泥",
    "风机",
    "鼓风机",
    "环冷风机",
    "冷风机",
    "电机",
    "压缩机",
    "高压电机",
}
DETAIL_HINT_TERMS = (
    "系统",
    "架构",
    "结构",
    "模块",
    "接口",
    "边界",
    "方案",
    "总体",
    "拓扑",
    "流程",
    "控制",
    "通讯",
    "通信",
    "联锁",
    "保护",
)
GENERIC_SUMMARY_SECTION_HEADINGS = {
    "方案综述",
    "项目综述",
    "方案概述",
    "项目概述",
    "总体说明",
}
SECTION_NOISE_HINT_TERMS = (
    "必要性",
    "意义",
    "节电",
    "运行时",
    "工频运行",
    "变频运行",
    "储运",
    "培训",
    "服务",
    "备品",
)


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in CASE_TOKEN_PATTERN.findall(text)
        if token and token not in CASE_STOPWORDS
    ]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _extract_detail_hints(text: str) -> list[str]:
    haystack = str(text or "").casefold()
    hints: list[str] = []
    for term in DETAIL_HINT_TERMS:
        normalized = term.casefold()
        if normalized in haystack and normalized not in hints:
            hints.append(normalized)
    return hints


def _section_heading_noise_adjustment(
    *,
    section_title: str,
    normalized_heading: str,
    signals: set[str],
) -> tuple[float, list[str]]:
    compact_heading = normalized_heading.replace(" ", "")
    normalized_title = normalize_section_heading(section_title).replace(" ", "")
    if not compact_heading:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    if "toc" not in signals and compact_heading in GENERIC_SUMMARY_SECTION_HEADINGS:
        score -= 0.12
        reasons.append("generic_summary_heading_penalty")
    if "toc" not in signals and any(token in compact_heading for token in SECTION_NOISE_HINT_TERMS):
        if any(token in normalized_title for token in ("系统", "方案", "架构", "结构")):
            score -= 0.16
            reasons.append("heading_noise_penalty")
    if "#" in compact_heading and any(token in compact_heading for token in ("运行时", "工频运行", "变频运行")):
        score -= 0.2
        reasons.append("inline_state_heading_penalty")
    if "toc" not in signals and len(compact_heading) > 26 and any(token in compact_heading for token in ("如下", "尺寸", "型号")):
        score -= 0.18
        reasons.append("long_inline_heading_penalty")
    return score, reasons


class CaseLibraryService:
    def __init__(
        self,
        *,
        outline_library_path: str | Path | None = None,
        block_library_path: str | Path | None = None,
    ) -> None:
        settings = get_settings()
        self.outline_library_path = Path(outline_library_path or settings.case_library_outline_path)
        self.block_library_path = Path(block_library_path or settings.case_library_block_path)

    def retrieve_cases(
        self,
        *,
        query: str,
        top_k: int = 3,
        library_tracks: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        query_terms = _tokenize(query)
        for entry in self._load_outline_entries():
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            retrieval_text = self._build_outline_retrieval_text(entry)
            score, reasons = self._score_case(query_terms=query_terms, entry=entry, retrieval_text=retrieval_text)
            if score <= 0:
                continue
            candidates.append(
                {
                    "sample_id": entry.get("sample_id"),
                    "file_name": entry.get("file_name"),
                    "library_track": track,
                    "profile": entry.get("profile"),
                    "top_level_titles": entry.get("top_level_titles") or [],
                    "outline_tree": entry.get("outline_tree") or [],
                    "score": round(score, 4),
                    "reason": "; ".join(reasons),
                    "retrieval_text": retrieval_text,
                }
            )
        candidates.sort(
            key=lambda item: (
                float(item.get("score") or 0),
                len(item.get("top_level_titles") or []),
                str(item.get("file_name") or ""),
            ),
            reverse=True,
        )
        return candidates[:top_k]

    def retrieve_blocks(
        self,
        *,
        query: str,
        top_k: int = 8,
        sample_ids: set[str] | None = None,
        library_tracks: set[str] | None = None,
        section_title: str | None = None,
        section_ids: set[str] | None = None,
        section_path_prefixes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        query_terms = _tokenize(query)
        title_terms = _tokenize(section_title or "")
        context_terms = {term for term in query_terms if term in LIGHT_CONTEXT_TERMS}
        for hint in extract_taxonomy_hints(query, section_title or ""):
            normalized = hint.casefold()
            if normalized not in query_terms:
                query_terms.append(normalized)
            context_terms.update(_tokenize(normalized))
        for hint in _extract_detail_hints(query):
            if hint not in query_terms:
                query_terms.append(hint)
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title or query,
                "purpose": query,
                "expected_evidence_types": [],
                "asset_required": False,
            }
        )
        candidates: list[dict[str, Any]] = []
        for entry in self._load_block_entries():
            track = str(entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            if sample_ids and str(entry.get("sample_id") or "") not in sample_ids:
                continue
            section_id = str(entry.get("source_section_id") or "").strip()
            section_path = str(entry.get("section_path") or entry.get("heading_path") or "").strip()
            if section_ids and section_id not in section_ids:
                continue
            if section_path_prefixes and not any(section_path.startswith(prefix) for prefix in section_path_prefixes if prefix):
                continue
            score, reasons = self._score_block(
                query_terms=query_terms,
                title_terms=title_terms,
                context_terms=context_terms,
                entry=entry,
                section_title=section_title or "",
                target_taxonomy=target_taxonomy,
            )
            if score <= 0:
                continue
            candidates.append(
                {
                    **entry,
                    "score": round(score, 4),
                    "reason": "; ".join(reasons),
                }
            )
        candidates.sort(
            key=lambda item: (
                float(item.get("score") or 0),
                float(item.get("reuse_level") == "high"),
                int(item.get("token_count") or 0),
            ),
            reverse=True,
        )
        return candidates[:top_k]

    def retrieve_sections(
        self,
        *,
        query: str,
        top_k: int = 6,
        sample_ids: set[str] | None = None,
        library_tracks: set[str] | None = None,
        section_title: str | None = None,
    ) -> list[dict[str, Any]]:
        query_terms = _tokenize(query)
        title_terms = _tokenize(section_title or "")
        context_terms = {term for term in query_terms if term in LIGHT_CONTEXT_TERMS}
        for hint in extract_taxonomy_hints(query, section_title or ""):
            normalized = hint.casefold()
            if normalized not in query_terms:
                query_terms.append(normalized)
            context_terms.update(_tokenize(normalized))
        for hint in _extract_detail_hints(query):
            if hint not in query_terms:
                query_terms.append(hint)
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title or query,
                "purpose": query,
                "expected_evidence_types": [],
            }
        )

        candidates: list[dict[str, Any]] = []
        for outline_entry in self._load_outline_entries():
            track = str(outline_entry.get("library_track") or "pilot_main")
            if library_tracks and track not in library_tracks:
                continue
            sample_id = str(outline_entry.get("sample_id") or "").strip()
            if sample_ids and sample_id not in sample_ids:
                continue
            raw_sections = outline_entry.get("section_catalog") or outline_entry.get("flat_outline") or []
            sections = _flatten_section_entries(raw_sections)
            for section in sections:
                if not isinstance(section, dict):
                    continue
                score, reasons = self._score_section_candidate(
                    query_terms=query_terms,
                    title_terms=title_terms,
                    context_terms=context_terms,
                    section_title=section_title or "",
                    section=section,
                    target_taxonomy=target_taxonomy,
                )
                if score <= 0:
                    continue
                candidates.append(
                    {
                        "sample_id": sample_id,
                        "file_name": outline_entry.get("file_name"),
                        "library_track": track,
                        "section_id": section.get("section_id"),
                        "heading_path": section.get("heading_path") or section.get("section_path"),
                        "section_path": section.get("section_path") or section.get("heading_path"),
                        "source_heading": section.get("source_heading") or section.get("title"),
                        "normalized_heading": section.get("normalized_heading"),
                        "heading_aliases": section.get("heading_aliases") or [],
                        "level": section.get("level"),
                        "source_signals": section.get("source_signals") or [],
                        "score": round(score, 4),
                        "reason": "; ".join(reasons),
                    }
                )
        candidates.sort(
            key=lambda item: (
                float(item.get("score") or 0),
                int(item.get("level") or 0),
                len(str(item.get("heading_path") or "")),
            ),
            reverse=True,
        )
        return candidates[:top_k]

    def expand_related_blocks(
        self,
        *,
        seed_blocks: list[dict[str, Any]],
        section_title: str,
        top_k: int = 4,
        radius: int = 8,
    ) -> list[dict[str, Any]]:
        if not seed_blocks:
            return []
        target_taxonomy = infer_target_taxonomy(
            {
                "title": section_title,
                "purpose": section_title,
                "expected_evidence_types": [],
                "asset_required": False,
            }
        )
        seed_by_sample: dict[str, list[int]] = {}
        seed_signatures: set[tuple[str, int]] = set()
        for block in seed_blocks:
            sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
            chunk_index = block.get("chunk_index")
            if not sample_id or chunk_index is None:
                continue
            try:
                chunk_index_int = int(chunk_index)
            except (TypeError, ValueError):
                continue
            seed_signatures.add((sample_id, chunk_index_int))
            seed_by_sample.setdefault(sample_id, []).append(chunk_index_int)

        candidates: list[dict[str, Any]] = []
        for entry in self._load_block_entries():
            sample_id = str(entry.get("sample_id") or "").strip()
            if sample_id not in seed_by_sample:
                continue
            try:
                chunk_index = int(entry.get("chunk_index"))
            except (TypeError, ValueError):
                continue
            if (sample_id, chunk_index) in seed_signatures:
                continue
            nearest_distance = min(abs(chunk_index - anchor) for anchor in seed_by_sample[sample_id])
            if nearest_distance > radius:
                continue
            score, reasons = self._score_related_block(
                entry=entry,
                nearest_distance=nearest_distance,
                target_taxonomy=target_taxonomy,
            )
            if score <= 0:
                continue
            candidates.append({**entry, "score": round(score, 4), "reason": "; ".join(reasons)})

        candidates.sort(
            key=lambda item: (
                float(item.get("score") or 0),
                -abs(int(item.get("chunk_index") or 0)),
            ),
            reverse=True,
        )
        return candidates[:top_k]

    def _build_outline_retrieval_text(self, entry: dict[str, Any]) -> str:
        flat_outline = entry.get("flat_outline") or []
        heading_paths = [str(item.get("heading_path") or "") for item in flat_outline[:24]]
        parts = [
            str(entry.get("file_name") or ""),
            str(entry.get("profile") or ""),
            " ".join(str(item) for item in (entry.get("top_level_titles") or [])),
            " ".join(path for path in heading_paths if path),
        ]
        return "\n".join(part for part in parts if part).strip()

    def _score_case(
        self,
        *,
        query_terms: list[str],
        entry: dict[str, Any],
        retrieval_text: str,
    ) -> tuple[float, list[str]]:
        text_lower = retrieval_text.casefold()
        score = 0.0
        reasons: list[str] = []
        overlap_terms = [term for term in query_terms if term.casefold() in text_lower]
        if overlap_terms:
            score += min(0.7, len(overlap_terms) * 0.14)
            reasons.append(f"query_overlap={','.join(overlap_terms[:6])}")
        top_titles = [str(item) for item in (entry.get("top_level_titles") or [])]
        if any(any(term.casefold() in title.casefold() for term in query_terms) for title in top_titles):
            score += 0.18
            reasons.append("top_level_title_match")
        headings = entry.get("heading_count") or 0
        if int(headings) >= 20:
            score += 0.05
            reasons.append("rich_outline_structure")
        return score, reasons

    def _score_block(
        self,
        *,
        query_terms: list[str],
        title_terms: list[str],
        context_terms: set[str],
        entry: dict[str, Any],
        section_title: str,
        target_taxonomy: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        content = str(entry.get("content") or "")
        heading_path = str(entry.get("heading_path") or entry.get("section_path") or "")
        haystack = f"{heading_path}\n{content}".casefold()
        score = 0.0
        reasons: list[str] = []
        if heading_looks_like_document_title(heading_path):
            score -= 0.28
            reasons.append("document_title_penalty")
        detail_terms = [term for term in query_terms if term not in title_terms and term not in context_terms]
        context_overlap = [term for term in context_terms if term.casefold() in haystack]
        if context_overlap:
            score += min(0.12, len(context_overlap) * 0.03)
            reasons.append(f"context_overlap={','.join(context_overlap[:6])}")
        overlap_terms = [term for term in detail_terms if term.casefold() in haystack]
        if overlap_terms:
            score += min(0.48, len(overlap_terms) * 0.09)
            reasons.append(f"detail_overlap={','.join(overlap_terms[:6])}")
        elif detail_terms:
            score -= 0.04
            reasons.append("detail_mismatch_penalty")
        title_score, title_reasons = self._score_section_title_alignment(
            section_title=section_title,
            entry=entry,
            heading_path=heading_path,
        )
        score += title_score
        reasons.extend(title_reasons)
        noise_adjustment, noise_reasons = _section_heading_noise_adjustment(
            section_title=section_title,
            normalized_heading=str(entry.get("normalized_heading") or normalize_section_heading(heading_path)),
            signals={
                str(item)
                for item in (
                    entry.get("source_signals")
                    or entry.get("signals")
                    or []
                )
                if item
            },
        )
        score += noise_adjustment
        reasons.extend(noise_reasons)
        reuse_level = str(entry.get("reuse_level") or "")
        if reuse_level == "high":
            score += 0.08
            reasons.append("high_reuse_level")
        if not bool(entry.get("front_matter")):
            score += 0.04
        if str(entry.get("content_risk_level") or "") == "low":
            score += 0.05
            reasons.append("low_risk_block")
        section_type = str(entry.get("section_type") or "unknown")
        equipment_type = str(entry.get("equipment_type") or "generic")
        content_form = str(entry.get("content_form") or "narrative")
        if content_form == "page_furniture":
            score -= 0.25
            reasons.append("page_furniture_penalty")
        if target_taxonomy:
            target_section_type = str(target_taxonomy.get("section_type") or "unknown")
            target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic")
            preferred_forms = set(target_taxonomy.get("preferred_content_forms") or [])
            support_forms = set(target_taxonomy.get("support_content_forms") or support_content_forms(target_section_type))
            if target_section_type != "company_profile" and section_type == "company_profile":
                score -= 0.18
                reasons.append("company_profile_penalty")
            if target_section_type != "unknown" and section_type == target_section_type:
                score += 0.2
                reasons.append("section_type_match")
            elif section_type in related_section_types(target_section_type):
                score += 0.1
                reasons.append("related_section_type_match")
            if target_equipment_type != "generic" and equipment_type == target_equipment_type:
                score += 0.1
                reasons.append("equipment_type_match")
            elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
                score -= 0.1
                reasons.append("equipment_type_mismatch_penalty")
            if preferred_forms and content_form in preferred_forms:
                score += 0.08
                reasons.append("content_form_match")
            elif support_forms and content_form in support_forms:
                score += 0.04
                reasons.append("support_content_form_match")
            if target_section_type in {"overall_solution", "project_overview"} and content_form_is_table(content_form):
                score -= 0.14
                reasons.append("narrative_section_table_penalty")
            if target_section_type in {"overall_solution", "project_overview"} and content_form == "formula":
                score -= 0.12
                reasons.append("narrative_section_formula_penalty")
            heading_adjustment, heading_reasons = heading_focus_adjustment(
                target_section_type=target_section_type,
                heading_text=heading_path,
            )
            score += heading_adjustment
            reasons.extend(heading_reasons)
            heading_lower = heading_path.casefold()
            if target_section_type == "communication_interface":
                if any(token in heading_lower for token in ("上位机", "控制信号接口", "接口说明", "点表", "modbus", "rs485")):
                    score += 0.12
                    reasons.append("interface_heading_bonus")
                if any(token in heading_lower for token in ("性能要求", "整体要求", "技术要求")):
                    score -= 0.16
                    reasons.append("interface_heading_noise_penalty")
        return score, reasons

    def _score_section_title_alignment(
        self,
        *,
        section_title: str,
        entry: dict[str, Any],
        heading_path: str,
    ) -> tuple[float, list[str]]:
        normalized_section_title = normalize_section_heading(section_title)
        if not normalized_section_title:
            return 0.0, []
        reasons: list[str] = []
        score = 0.0
        normalized_heading = str(entry.get("normalized_heading") or normalize_section_heading(heading_path))
        compact_heading = normalized_heading.replace(" ", "")
        normalized_path = str(entry.get("normalized_section_path") or "")
        path_segments = [segment.strip() for segment in normalized_path.split(">") if segment.strip()]
        aliases = {
            str(item).strip()
            for item in (entry.get("heading_aliases") or build_heading_aliases(entry.get("source_heading") or heading_path))
            if str(item).strip()
        }
        title_aliases = set(build_heading_aliases(section_title))
        if normalized_heading and normalized_heading == normalized_section_title:
            score += 0.34
            reasons.append("normalized_section_title_match")
        elif normalized_section_title in path_segments:
            score += 0.22
            reasons.append("section_path_title_match")
        elif title_aliases & aliases:
            score += 0.22
            reasons.append("heading_alias_match")
        elif section_title and section_title.casefold() in heading_path.casefold():
            score += 0.2
            reasons.append("section_title_match")

        family_score = 0.0
        best_family = 0.0
        for segment in [heading_path, str(entry.get("source_heading") or ""), *path_segments]:
            best_family = max(best_family, heading_family_similarity(section_title, segment))
        if best_family >= 0.2:
            family_score = 0.12
        elif best_family > 0:
            family_score = 0.06
        if family_score:
            score += family_score
            reasons.append("heading_family_match")

        title_terms = [term for term in _tokenize(section_title) if term]
        title_overlap = [term for term in title_terms if term.casefold() in heading_path.casefold()]
        if title_overlap:
            score += min(0.12, len(title_overlap) * 0.04)
            reasons.append(f"title_overlap={','.join(title_overlap[:6])}")
        if any(token in normalized_section_title for token in ("系统", "方案")):
            if any(token in compact_heading for token in ("系统方案", "系统结构", "总体方案", "总体设计", "技术架构", "架构", "接口")):
                score += 0.12
                reasons.append("system_overview_heading_bonus")
            if any(
                token in compact_heading
                for token in ("散热", "冷却", "存储", "培训", "节电", "工频运行", "变频运行", "清单", "备品", "维护", "服务", "供方", "安装")
            ):
                score -= 0.14
                reasons.append("system_overview_heading_noise_penalty")
        return score, reasons

    def _score_section_candidate(
        self,
        *,
        query_terms: list[str],
        title_terms: list[str],
        context_terms: set[str],
        section_title: str,
        section: dict[str, Any],
        target_taxonomy: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        heading_path = str(section.get("heading_path") or section.get("section_path") or section.get("title") or "")
        normalized_heading = str(section.get("normalized_heading") or normalize_section_heading(heading_path))
        haystack = f"{heading_path}\n{normalized_heading}".casefold()
        detail_terms = [term for term in query_terms if term not in title_terms and term not in context_terms]
        score = 0.0
        reasons: list[str] = []

        title_score, title_reasons = self._score_section_title_alignment(
            section_title=section_title,
            entry={
                "normalized_heading": normalized_heading,
                "normalized_section_path": section.get("normalized_section_path"),
                "heading_aliases": section.get("heading_aliases") or [],
                "source_heading": section.get("source_heading") or section.get("title"),
            },
            heading_path=heading_path,
        )
        score += title_score
        reasons.extend(title_reasons)

        context_overlap = [term for term in context_terms if term.casefold() in haystack]
        if context_overlap:
            score += min(0.1, len(context_overlap) * 0.03)
            reasons.append(f"context_overlap={','.join(context_overlap[:6])}")
        detail_overlap = [term for term in detail_terms if term.casefold() in haystack]
        if detail_overlap:
            score += min(0.28, len(detail_overlap) * 0.08)
            reasons.append(f"detail_overlap={','.join(detail_overlap[:6])}")
        elif detail_terms:
            score -= 0.04
            reasons.append("detail_mismatch_penalty")

        signals = {str(item) for item in (section.get("source_signals") or []) if item}
        if {"toc", "parser_heading"} <= signals:
            score += 0.08
            reasons.append("toc_parser_confirmed")
        elif "toc" in signals:
            score += 0.05
            reasons.append("toc_confirmed")

        level = int(section.get("level") or 1)
        if detail_overlap and level >= 2:
            score += 0.04
            reasons.append("detail_section_specificity_bonus")
        if level == 1 and not detail_overlap:
            score += 0.03
            reasons.append("root_section_bonus")

        if target_taxonomy:
            section_taxonomy = infer_target_taxonomy(
                {
                    "title": str(section.get("source_heading") or section.get("title") or ""),
                    "purpose": heading_path,
                    "expected_evidence_types": [],
                }
            )
            target_section_type = str(target_taxonomy.get("section_type") or "unknown")
            section_type = str(section_taxonomy.get("section_type") or "unknown")
            if target_section_type != "unknown" and section_type == target_section_type:
                score += 0.14
                reasons.append("section_type_match")
            elif section_type in related_section_types(target_section_type):
                score += 0.08
                reasons.append("related_section_type_match")
            heading_adjustment, heading_reasons = heading_focus_adjustment(
                target_section_type=target_section_type,
                heading_text=heading_path,
            )
            score += heading_adjustment
            reasons.extend(heading_reasons)

        noise_adjustment, noise_reasons = _section_heading_noise_adjustment(
            section_title=section_title,
            normalized_heading=normalized_heading,
            signals=signals,
        )
        score += noise_adjustment
        reasons.extend(noise_reasons)

        if heading_looks_like_document_title(heading_path):
            score -= 0.22
            reasons.append("document_title_penalty")
        return score, reasons

    def _score_related_block(
        self,
        *,
        entry: dict[str, Any],
        nearest_distance: int,
        target_taxonomy: dict[str, Any],
    ) -> tuple[float, list[str]]:
        score = max(0.0, 0.38 - (nearest_distance * 0.04))
        reasons = [f"neighbor_distance={nearest_distance}"]
        if bool(entry.get("front_matter")):
            score -= 0.18
            reasons.append("front_matter_penalty")
        if heading_looks_like_document_title(str(entry.get("heading_path") or "")):
            score -= 0.22
            reasons.append("document_title_penalty")
        if str(entry.get("content_risk_level") or "") == "low":
            score += 0.05
            reasons.append("low_risk_block")
        section_type = str(entry.get("section_type") or "unknown")
        equipment_type = str(entry.get("equipment_type") or "generic")
        content_form = str(entry.get("content_form") or "narrative")
        target_section_type = str(target_taxonomy.get("section_type") or "unknown")
        target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic")
        preferred_forms = set(target_taxonomy.get("preferred_content_forms") or [])
        support_forms = set(target_taxonomy.get("support_content_forms") or support_content_forms(target_section_type))

        if target_section_type != "unknown" and section_type == target_section_type:
            score += 0.22
            reasons.append("section_type_match")
        elif section_type in related_section_types(target_section_type):
            score += 0.1
            reasons.append("related_section_type_match")
        elif section_type == "unknown":
            score -= 0.04
            reasons.append("unknown_section_type_penalty")
        else:
            score -= 0.08
            reasons.append("section_type_mismatch_penalty")

        if target_equipment_type != "generic" and equipment_type == target_equipment_type:
            score += 0.1
            reasons.append("equipment_type_match")
        elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
            score -= 0.06
            reasons.append("equipment_type_mismatch_penalty")

        if preferred_forms and content_form in preferred_forms:
            score += 0.08
            reasons.append("content_form_match")
        elif support_forms and content_form in support_forms:
            score += 0.04
            reasons.append("support_content_form_match")
        elif content_form == "page_furniture":
            score -= 0.25
            reasons.append("page_furniture_penalty")
        heading_adjustment, heading_reasons = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=str(entry.get("heading_path") or ""),
        )
        score += heading_adjustment
        reasons.extend(heading_reasons)
        heading_lower = str(entry.get("heading_path") or "").casefold()
        if target_section_type == "communication_interface":
            if any(token in heading_lower for token in ("上位机", "控制信号接口", "接口说明", "点表", "modbus", "rs485")):
                score += 0.12
                reasons.append("interface_heading_bonus")
            if any(token in heading_lower for token in ("性能要求", "整体要求", "技术要求")):
                score -= 0.16
                reasons.append("interface_heading_noise_penalty")
        return score, reasons

    def _load_outline_entries(self) -> list[dict[str, Any]]:
        if not self.outline_library_path.exists():
            return []
        payload = json.loads(self.outline_library_path.read_text(encoding="utf-8"))
        return [item for item in (payload.get("entries") or []) if isinstance(item, dict)]

    def _load_block_entries(self) -> list[dict[str, Any]]:
        if not self.block_library_path.exists():
            return []
        payload = json.loads(self.block_library_path.read_text(encoding="utf-8"))
        return [item for item in (payload.get("entries") or []) if isinstance(item, dict)]


def build_outline_examples(case_candidates: list[dict[str, Any]], *, max_cases: int = 3, max_titles: int = 10) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for candidate in case_candidates[:max_cases]:
        top_level_titles = _dedupe_keep_order(
            [str(item).strip() for item in (candidate.get("top_level_titles") or []) if str(item).strip()]
        )
        examples.append(
            {
                "file_name": candidate.get("file_name"),
                "score": candidate.get("score"),
                "top_level_titles": top_level_titles[:max_titles],
            }
        )
    return examples


def _flatten_section_entries(sections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        flattened.append(section)
        children = section.get("children") or []
        if children:
            flattened.extend(_flatten_section_entries(children))
    return flattened
