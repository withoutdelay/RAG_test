from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from app.config import get_settings
from app.services.vectorstore.block_taxonomy import (
    extract_taxonomy_hints,
    heading_focus_adjustment,
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
    ) -> list[dict[str, Any]]:
        query_terms = _tokenize(" ".join(part for part in [query, section_title or ""] if part))
        for hint in extract_taxonomy_hints(query, section_title or ""):
            normalized = hint.casefold()
            if normalized not in query_terms:
                query_terms.append(normalized)
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
            score, reasons = self._score_block(
                query_terms=query_terms,
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
        entry: dict[str, Any],
        section_title: str,
        target_taxonomy: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        content = str(entry.get("content") or "")
        heading_path = str(entry.get("heading_path") or "")
        haystack = f"{heading_path}\n{content}".casefold()
        score = 0.0
        reasons: list[str] = []
        if heading_looks_like_document_title(heading_path):
            score -= 0.28
            reasons.append("document_title_penalty")
        overlap_terms = [term for term in query_terms if term.casefold() in haystack]
        if overlap_terms:
            score += min(0.72, len(overlap_terms) * 0.12)
            reasons.append(f"query_overlap={','.join(overlap_terms[:6])}")
        if section_title and section_title.casefold() in heading_path.casefold():
            score += 0.16
            reasons.append("section_title_match")
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
