from __future__ import annotations

from collections import Counter
from typing import Any

from app.services.vectorstore.block_taxonomy import related_section_types


FIGURE_QUERY_HINTS = ("图", "示意图", "单线图", "总布置图", "拓扑")
TABLE_QUERY_HINTS = ("表", "参数", "清单", "配置", "接口")
EQUIPMENT_TYPE_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "vfd": ("高压变频器", "变频器"),
    "lci": ("LCI", "变频软起"),
    "soft_starter": ("软起动", "软启动"),
    "motor": ("电机", "同步电机"),
    "transformer": ("变压器",),
    "switchgear": ("开关柜",),
    "cabinet": ("控制柜",),
    "dcs_plc_interface": ("DCS", "PLC"),
    "cooling_system": ("冷却系统",),
    "fan_blower": ("鼓风机", "风机"),
    "compressor": ("压缩机",),
}
PARAMETER_SENSITIVE_SECTION_TYPES = {
    "vfd_spec",
    "motor_spec",
    "starter_spec",
    "transformer_spec",
    "bom_or_supply_list",
}
SKIP_EVAL_SECTION_TYPES = {"unknown", "company_profile"}


def _append_unique_text(values: list[str], candidate: Any) -> None:
    text = str(candidate or "").strip()
    if not text or text in values:
        return
    values.append(text)


def _flatten_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in sections:
        flattened.append(section)
        flattened.extend(_flatten_sections(list(section.get("children") or [])))
    return flattened


def _clean_holdout_purpose_text(*, section: dict[str, Any], document_name: str, title: str) -> str:
    raw_text = str(
        section.get("section_summary")
        or section.get("section_retrieval_text")
        or section.get("contextual_text")
        or title
    ).strip()
    if not raw_text:
        return title

    title_variants = {
        str(title).strip(),
        str(section.get("source_heading") or "").strip(),
        str(section.get("normalized_heading") or "").strip(),
        *[str(item).strip() for item in (section.get("heading_aliases") or []) if str(item).strip()],
    }
    cleaned_lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line == str(document_name or "").strip():
            continue
        if ".docx" in line.lower() or ".pdf" in line.lower():
            continue
        if " > " in line:
            continue
        if line in title_variants:
            continue
        if len(line) < 8:
            continue
        if line not in cleaned_lines:
            cleaned_lines.append(line)
    if not cleaned_lines:
        hint_text = " ".join(str(item).strip() for item in (section.get("taxonomy_hints") or []) if str(item).strip())
        return hint_text or title
    return "\n".join(cleaned_lines[:2])


def select_holdout_sections(sections: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for section in _flatten_sections(sections):
        title = str(section.get("title") or section.get("source_heading") or "").strip()
        if not title:
            continue
        section_type = str(section.get("section_type") or "unknown").strip().lower()
        if section_type in SKIP_EVAL_SECTION_TYPES:
            continue
        summary = str(section.get("section_summary") or section.get("section_retrieval_text") or "").strip()
        if len(summary) < 8:
            continue
        equipment_type = str(section.get("equipment_type") or "generic").strip().lower()
        level = int(section.get("level") or 0)
        is_leaf = not bool(section.get("children"))
        score = 0.0
        score += 2.0 if is_leaf else 0.0
        score += 1.5 if section_type not in {"overall_solution", "project_overview"} else 0.0
        score += 1.0 if equipment_type not in {"generic", "unknown", ""} else 0.0
        score += min(len(summary) / 180.0, 1.0)
        score += min(level * 0.15, 0.45)
        ranked.append((score, section))
    ranked.sort(
        key=lambda item: (
            float(item[0]),
            int(item[1].get("level") or 0),
            len(str(item[1].get("section_summary") or item[1].get("section_retrieval_text") or "")),
            str(item[1].get("section_path") or item[1].get("title") or ""),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    section_type_counts: Counter[str] = Counter()
    for per_type_limit in (1, 2, max(limit, 1)):
        for _, section in ranked:
            path = str(section.get("section_path") or section.get("title") or "").strip()
            section_type = str(section.get("section_type") or "unknown").strip().lower()
            if not path or path in seen_paths:
                continue
            if section_type_counts[section_type] >= per_type_limit:
                continue
            seen_paths.add(path)
            selected.append(section)
            section_type_counts[section_type] += 1
            if len(selected) >= max(limit, 1):
                return selected
    return selected


def build_holdout_query_section(*, section: dict[str, Any], document_name: str) -> dict[str, Any]:
    title = str(section.get("title") or section.get("source_heading") or "未命名章节").strip()
    purpose = _clean_holdout_purpose_text(section=section, document_name=document_name, title=title)
    section_type = str(section.get("section_type") or "unknown").strip().lower()
    equipment_type = str(section.get("equipment_type") or "generic").strip().lower()
    content_form = str(section.get("content_form") or "narrative").strip().lower()
    keywords: list[str] = []
    for value in section.get("domain_terms") or []:
        _append_unique_text(keywords, value)
    for value in section.get("taxonomy_hints") or []:
        _append_unique_text(keywords, value)
    for value in section.get("heading_aliases") or []:
        _append_unique_text(keywords, value)
    for value in EQUIPMENT_TYPE_QUERY_TERMS.get(equipment_type, ()):
        _append_unique_text(keywords, value)

    expected_evidence_types = ["section"]
    haystack = f"{title}\n{purpose}"
    if content_form == "table" or any(token in haystack for token in TABLE_QUERY_HINTS):
        _append_unique_text(expected_evidence_types, "table")
        _append_unique_text(expected_evidence_types, "parameter")
    if any(token in haystack for token in FIGURE_QUERY_HINTS):
        _append_unique_text(expected_evidence_types, "figure")

    return {
        "title": title,
        "purpose": purpose,
        "keywords": keywords[:10],
        "expected_evidence_types": expected_evidence_types,
        "section_class": str(section.get("section_class") or ""),
        "generation_mode": "reuse_first",
        "reuse_level": str(section.get("reuse_level") or "high"),
        "parameter_sensitive": bool(section_type in PARAMETER_SENSITIVE_SECTION_TYPES or content_form == "table"),
        "asset_required": "figure" in expected_evidence_types,
        "customer_specificity": str(section.get("customer_specificity") or "medium"),
        "target_section_type": section_type,
        "target_equipment_type": equipment_type,
        "source_document_name": document_name,
        "source_section_id": str(section.get("section_id") or "").strip(),
        "source_section_path": str(section.get("section_path") or title).strip(),
    }


def _extract_block_metadata(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    return {
        "section_type": str(block.get("section_type") or metadata.get("section_type") or "unknown").strip().lower(),
        "equipment_type": str(block.get("equipment_type") or metadata.get("equipment_type") or "generic").strip().lower(),
        "content_form": str(block.get("content_form") or metadata.get("content_form") or "narrative").strip().lower(),
    }


def _section_type_matches(expected: str, actual: str) -> bool:
    normalized_expected = str(expected or "").strip().lower()
    normalized_actual = str(actual or "").strip().lower()
    if not normalized_expected or normalized_expected == "unknown":
        return False
    if normalized_expected == normalized_actual:
        return True
    return normalized_actual in related_section_types(normalized_expected)


def _equipment_type_matches(expected: str, actual: str) -> bool:
    normalized_expected = str(expected or "").strip().lower()
    normalized_actual = str(actual or "").strip().lower()
    if not normalized_expected or normalized_expected in {"unknown", "generic"}:
        return False
    return normalized_expected == normalized_actual


def evaluate_block_ranking(*, query_section: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    target_section_type = str(query_section.get("target_section_type") or "unknown").strip().lower()
    target_equipment_type = str(query_section.get("target_equipment_type") or "generic").strip().lower()
    top_blocks = list(blocks[:3])
    top1 = top_blocks[0] if top_blocks else {}
    top1_meta = _extract_block_metadata(top1) if top1 else {}
    section_match_ranks: list[int] = []
    equipment_match_ranks: list[int] = []
    prior_hit_block_count = 0
    total_prior_boost = 0.0
    for index, block in enumerate(top_blocks, start=1):
        metadata = _extract_block_metadata(block)
        if _section_type_matches(target_section_type, metadata["section_type"]):
            section_match_ranks.append(index)
        if _equipment_type_matches(target_equipment_type, metadata["equipment_type"]):
            equipment_match_ranks.append(index)
        breakdown = block.get("selection_score_breakdown") if isinstance(block.get("selection_score_breakdown"), dict) else {}
        prior_total = float(breakdown.get("knowledge_wiki_prior_total") or 0)
        if prior_total > 0:
            prior_hit_block_count += 1
            total_prior_boost += prior_total
    top1_breakdown = top1.get("selection_score_breakdown") if isinstance(top1.get("selection_score_breakdown"), dict) else {}
    return {
        "target_section_type": target_section_type,
        "target_equipment_type": target_equipment_type,
        "top_block_count": len(top_blocks),
        "top1_source_title": str(top1.get("source_title") or ""),
        "top1_section_path": str(top1.get("section_path") or ""),
        "top1_score": round(float(top1.get("selection_score") or 0), 4),
        "top1_section_type": top1_meta.get("section_type"),
        "top1_equipment_type": top1_meta.get("equipment_type"),
        "top1_section_type_match": _section_type_matches(target_section_type, str(top1_meta.get("section_type") or "")),
        "top1_equipment_type_match": _equipment_type_matches(target_equipment_type, str(top1_meta.get("equipment_type") or "")),
        "top3_section_type_hit": bool(section_match_ranks),
        "top3_equipment_type_hit": bool(equipment_match_ranks),
        "first_section_type_match_rank": section_match_ranks[0] if section_match_ranks else None,
        "first_equipment_type_match_rank": equipment_match_ranks[0] if equipment_match_ranks else None,
        "prior_hit_block_count": prior_hit_block_count,
        "total_prior_boost": round(total_prior_boost, 4),
        "top1_prior_boost": round(float(top1_breakdown.get("knowledge_wiki_prior_total") or 0), 4),
    }


def summarize_eval_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "total_sections": len(records),
        "sections_with_case_candidates": 0,
        "sections_with_equipment_target": 0,
        "baseline": Counter(),
        "with_prior": Counter(),
        "deltas": Counter(),
    }
    for record in records:
        if int(record.get("case_candidate_count") or 0) > 0:
            summary["sections_with_case_candidates"] += 1
        baseline = record.get("baseline") if isinstance(record.get("baseline"), dict) else {}
        with_prior = record.get("with_prior") if isinstance(record.get("with_prior"), dict) else {}
        if baseline.get("top1_section_type_match"):
            summary["baseline"]["top1_section_type_match"] += 1
        if baseline.get("top3_section_type_hit"):
            summary["baseline"]["top3_section_type_hit"] += 1
        if with_prior.get("top1_section_type_match"):
            summary["with_prior"]["top1_section_type_match"] += 1
        if with_prior.get("top3_section_type_hit"):
            summary["with_prior"]["top3_section_type_hit"] += 1
        if int(with_prior.get("prior_hit_block_count") or 0) > 0:
            summary["with_prior"]["prior_hit_sections"] += 1
        summary["with_prior"]["prior_hit_block_count"] += int(with_prior.get("prior_hit_block_count") or 0)
        summary["with_prior"]["total_prior_boost_x10000"] += int(round(float(with_prior.get("total_prior_boost") or 0) * 10000))

        target_equipment_type = str(record.get("target_equipment_type") or "").strip().lower()
        if target_equipment_type and target_equipment_type not in {"generic", "unknown"}:
            summary["sections_with_equipment_target"] += 1
            if baseline.get("top1_equipment_type_match"):
                summary["baseline"]["top1_equipment_type_match"] += 1
            if baseline.get("top3_equipment_type_hit"):
                summary["baseline"]["top3_equipment_type_hit"] += 1
            if with_prior.get("top1_equipment_type_match"):
                summary["with_prior"]["top1_equipment_type_match"] += 1
            if with_prior.get("top3_equipment_type_hit"):
                summary["with_prior"]["top3_equipment_type_hit"] += 1

        baseline_section_rank = baseline.get("first_section_type_match_rank")
        with_prior_section_rank = with_prior.get("first_section_type_match_rank")
        if baseline_section_rank is None and with_prior_section_rank is not None:
            summary["deltas"]["section_match_rank_improved"] += 1
        elif baseline_section_rank is not None and with_prior_section_rank is None:
            summary["deltas"]["section_match_rank_worsened"] += 1
        elif baseline_section_rank is not None and with_prior_section_rank is not None:
            if with_prior_section_rank < baseline_section_rank:
                summary["deltas"]["section_match_rank_improved"] += 1
            elif with_prior_section_rank > baseline_section_rank:
                summary["deltas"]["section_match_rank_worsened"] += 1
            else:
                summary["deltas"]["section_match_rank_unchanged"] += 1

        baseline_equipment_rank = baseline.get("first_equipment_type_match_rank")
        with_prior_equipment_rank = with_prior.get("first_equipment_type_match_rank")
        if baseline_equipment_rank is None and with_prior_equipment_rank is not None:
            summary["deltas"]["equipment_match_rank_improved"] += 1
        elif baseline_equipment_rank is not None and with_prior_equipment_rank is None:
            summary["deltas"]["equipment_match_rank_worsened"] += 1
        elif baseline_equipment_rank is not None and with_prior_equipment_rank is not None:
            if with_prior_equipment_rank < baseline_equipment_rank:
                summary["deltas"]["equipment_match_rank_improved"] += 1
            elif with_prior_equipment_rank > baseline_equipment_rank:
                summary["deltas"]["equipment_match_rank_worsened"] += 1
            else:
                summary["deltas"]["equipment_match_rank_unchanged"] += 1

        if str(with_prior.get("top1_source_title") or "") != str(baseline.get("top1_source_title") or ""):
            summary["deltas"]["top1_changed"] += 1
        if not baseline.get("top1_section_type_match") and with_prior.get("top1_section_type_match"):
            summary["deltas"]["top1_section_match_gained"] += 1
        if baseline.get("top1_section_type_match") and not with_prior.get("top1_section_type_match"):
            summary["deltas"]["top1_section_match_lost"] += 1

    return {
        "total_sections": summary["total_sections"],
        "sections_with_case_candidates": summary["sections_with_case_candidates"],
        "sections_with_equipment_target": summary["sections_with_equipment_target"],
        "baseline": dict(summary["baseline"]),
        "with_prior": {
            **{key: value for key, value in summary["with_prior"].items() if key != "total_prior_boost_x10000"},
            "total_prior_boost": round(summary["with_prior"]["total_prior_boost_x10000"] / 10000.0, 4),
        },
        "deltas": dict(summary["deltas"]),
    }


def render_prior_eval_markdown(*, summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    evaluation_mode = str(summary.get("evaluation_mode") or "default").strip()
    lines = [
        "# Layer 4 AI Wiki Prior Holdout Eval",
        "",
        "更新时间：2026-04-20",
        "",
        "评测范围：固定 `pilot_main` case shortlist，不改线上主链路，只比较 reusable block 检索/重排阶段在 `without_prior` 与 `with_prior` 两种模式下的差异。",
        f"评测模式：`{evaluation_mode}`",
        "",
        "## 汇总",
        "",
        f"- 评测章节数：{summary.get('total_sections') or 0}",
        f"- 有 case shortlist 的章节数：{summary.get('sections_with_case_candidates') or 0}",
        f"- 有明确 equipment target 的章节数：{summary.get('sections_with_equipment_target') or 0}",
        f"- 跳过文档数：{summary.get('skipped_documents') or 0}",
        f"- `with_prior` 命中 prior 的章节数：{(summary.get('with_prior') or {}).get('prior_hit_sections', 0)}",
        f"- `with_prior` 命中 prior 的块数：{(summary.get('with_prior') or {}).get('prior_hit_block_count', 0)}",
        f"- `with_prior` 累计 prior boost：{(summary.get('with_prior') or {}).get('total_prior_boost', 0.0)}",
        "",
        "| Metric | without_prior | with_prior |",
        "| --- | ---: | ---: |",
        f"| Top1 section_type match | {(summary.get('baseline') or {}).get('top1_section_type_match', 0)} | {(summary.get('with_prior') or {}).get('top1_section_type_match', 0)} |",
        f"| Top3 section_type hit | {(summary.get('baseline') or {}).get('top3_section_type_hit', 0)} | {(summary.get('with_prior') or {}).get('top3_section_type_hit', 0)} |",
        f"| Top1 equipment_type match | {(summary.get('baseline') or {}).get('top1_equipment_type_match', 0)} | {(summary.get('with_prior') or {}).get('top1_equipment_type_match', 0)} |",
        f"| Top3 equipment_type hit | {(summary.get('baseline') or {}).get('top3_equipment_type_hit', 0)} | {(summary.get('with_prior') or {}).get('top3_equipment_type_hit', 0)} |",
        "",
        "## Delta",
        "",
        f"- section_type 首命中排名改善：{(summary.get('deltas') or {}).get('section_match_rank_improved', 0)}",
        f"- section_type 首命中排名变差：{(summary.get('deltas') or {}).get('section_match_rank_worsened', 0)}",
        f"- equipment_type 首命中排名改善：{(summary.get('deltas') or {}).get('equipment_match_rank_improved', 0)}",
        f"- equipment_type 首命中排名变差：{(summary.get('deltas') or {}).get('equipment_match_rank_worsened', 0)}",
        f"- Top1 候选发生变化：{(summary.get('deltas') or {}).get('top1_changed', 0)}",
        f"- Top1 section_type match gained：{(summary.get('deltas') or {}).get('top1_section_match_gained', 0)}",
        f"- Top1 section_type match lost：{(summary.get('deltas') or {}).get('top1_section_match_lost', 0)}",
        "",
        "## 明细",
        "",
    ]
    for record in records:
        lines.extend(
            [
                f"### {record.get('document_name') or '未命名文档'} / {record.get('section_path') or record.get('section_title') or '未命名章节'}",
                "",
                f"- target: `section_type={record.get('target_section_type') or 'unknown'}` / `equipment_type={record.get('target_equipment_type') or 'generic'}`",
                f"- case shortlist: {', '.join(str(item.get('file_name') or item.get('sample_id') or '') for item in (record.get('case_candidates') or [])) or 'none'}",
                f"- AI Wiki terms: {', '.join(record.get('knowledge_wiki_terms') or []) or 'none'}",
                f"- AI Wiki products: {', '.join(record.get('knowledge_wiki_product_cards') or []) or 'none'}",
                f"- AI Wiki modules: {', '.join(record.get('knowledge_wiki_module_cards') or []) or 'none'}",
                f"- without_prior: top1={record.get('baseline', {}).get('top1_source_title') or 'none'} / score={record.get('baseline', {}).get('top1_score')} / section_match={record.get('baseline', {}).get('top1_section_type_match')} / equipment_match={record.get('baseline', {}).get('top1_equipment_type_match')}",
                f"- with_prior: top1={record.get('with_prior', {}).get('top1_source_title') or 'none'} / score={record.get('with_prior', {}).get('top1_score')} / section_match={record.get('with_prior', {}).get('top1_section_type_match')} / equipment_match={record.get('with_prior', {}).get('top1_equipment_type_match')} / prior_boost={record.get('with_prior', {}).get('total_prior_boost')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
