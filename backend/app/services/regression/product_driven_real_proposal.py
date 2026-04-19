from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RealProposalRegressionFixture:
    fixture_id: str
    title: str
    project_name: str
    product_line: str
    industry: str
    description: str
    expected_case_titles: tuple[str, ...]
    expected_primary_series: str
    expected_support_series: tuple[str, ...]
    expected_protocol: str
    expected_voltage: str
    local_fixture_paths: tuple[Path, ...]


def build_lci_real_proposal_fixture(repo_root: Path) -> RealProposalRegressionFixture:
    fixture_dir = repo_root / "private_samples" / "real_proposals"
    return RealProposalRegressionFixture(
        fixture_id="lci_blower_real_proposals",
        title="LCI 鼓风机真实方案回归",
        project_name="某钢铁集团高炉鼓风机电机及 LCI 变频软起动系统改造项目（回归）",
        product_line="lci",
        industry="钢铁",
        description=(
            "10kV 4500kW 同步电机高炉鼓风机场景，要求采用 LCI 变频软起动，"
            "保留工频旁路切换，并通过 Profibus-DP 接入 DCS。"
        ),
        expected_case_titles=(
            "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
            "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
            "上电湛江中纸高浓磨机项目成套方案VerA.pdf",
        ),
        expected_primary_series="lci_sync_drive",
        expected_support_series=("rectifier_transformer", "excitation_cabinet", "bypass_cabinet"),
        expected_protocol="Profibus-DP",
        expected_voltage="10kV",
        local_fixture_paths=(
            fixture_dir / "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
            fixture_dir / "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx",
            fixture_dir / "上电湛江中纸高浓磨机项目成套方案VerA.pdf",
        ),
    )


def collect_evidence_titles(evidence_bundle: dict[str, Any]) -> list[str]:
    title_sources = collect_evidence_title_sources(evidence_bundle)
    titles: list[str] = []
    for source_titles in title_sources.values():
        titles.extend(source_titles)

    deduped: list[str] = []
    seen: set[str] = set()
    for title in titles:
        if title in seen:
            continue
        seen.add(title)
        deduped.append(title)
    return deduped


def collect_evidence_title_sources(evidence_bundle: dict[str, Any]) -> dict[str, list[str]]:
    content = evidence_bundle.get("content") if isinstance(evidence_bundle.get("content"), dict) else {}
    title_sources: dict[str, list[str]] = {
        "results": [],
        "fallback_results": [],
        "case_candidates": [],
    }

    for item in content.get("results") or []:
        title = str(item.get("source_title") or "").strip()
        if title:
            title_sources["results"].append(title)

    for item in content.get("fallback_results") or []:
        title = str(item.get("source_title") or "").strip()
        if title:
            title_sources["fallback_results"].append(title)

    for item in content.get("case_candidates") or []:
        title = str(item.get("file_name") or "").strip()
        if title:
            title_sources["case_candidates"].append(title)

    return {key: _dedupe_titles(value) for key, value in title_sources.items()}


def _dedupe_titles(titles: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for title in titles:
        if title in seen:
            continue
        seen.add(title)
        deduped.append(title)
    return deduped


def evaluate_real_proposal_gate(
    *,
    fixture: RealProposalRegressionFixture,
    requirement_card: dict[str, Any],
    evidence_bundle: dict[str, Any],
    solution_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    title_sources = collect_evidence_title_sources(evidence_bundle)
    evidence_titles = collect_evidence_titles(evidence_bundle)
    quality_trace = (
        evidence_bundle.get("content", {}).get("quality_trace", {})
        if isinstance(evidence_bundle.get("content"), dict)
        else {}
    )
    quality_score = float(evidence_bundle.get("quality_score") or 0.0)
    solution_products = solution_snapshot.get("selected_products") or []
    primary_product = solution_products[0] if solution_products else {}
    selected_codes = {str(item.get("series_code") or "").strip() for item in solution_products}
    expected_hit_titles = [
        title
        for title in fixture.expected_case_titles
        if any(title in evidence_title for evidence_title in evidence_titles)
    ]
    direct_hit_titles = _dedupe_titles(
        [
            title
            for title in fixture.expected_case_titles
            if any(
                title in evidence_title
                for evidence_title in title_sources["results"] + title_sources["fallback_results"]
            )
        ]
    )
    candidate_only_hit_titles = [
        title
        for title in expected_hit_titles
        if title not in direct_hit_titles
    ]
    quality_gate_passed = quality_score >= 0.55 or bool(direct_hit_titles)
    if quality_score >= 0.55:
        quality_gate_reason = "score_threshold"
    elif direct_hit_titles:
        quality_gate_reason = "fixture_direct_hit"
    else:
        quality_gate_reason = "insufficient"

    gate_results = [
        {
            "gate": "requirement_ready",
            "passed": len(requirement_card.get("blocking_items") or []) == 0,
            "detail": f"blocking_items={len(requirement_card.get('blocking_items') or [])}",
        },
        {
            "gate": "evidence_hits_real_samples",
            "passed": bool(expected_hit_titles),
            "detail": (
                "direct_hits="
                + ("；".join(direct_hit_titles) if direct_hit_titles else "无")
                + ", candidate_only_hits="
                + ("；".join(candidate_only_hit_titles) if candidate_only_hit_titles else "无")
            ),
        },
        {
            "gate": "evidence_quality_sufficient",
            "passed": quality_gate_passed,
            "detail": (
                f"quality_score={quality_score:.2f}, "
                f"primary_results_source={quality_trace.get('primary_results_source') or 'unknown'}, "
                f"acceptance={quality_gate_reason}"
            ),
        },
        {
            "gate": "solution_bound_to_catalog",
            "passed": bool(solution_snapshot.get("source_catalog_version")),
            "detail": f"source_catalog_version={solution_snapshot.get('source_catalog_version') or 'missing'}",
        },
        {
            "gate": "primary_series_matches_fixture",
            "passed": str(primary_product.get("series_code") or "") == fixture.expected_primary_series,
            "detail": f"primary_series={primary_product.get('series_code') or 'missing'}",
        },
        {
            "gate": "support_scope_covers_fixture",
            "passed": all(code in selected_codes for code in fixture.expected_support_series),
            "detail": "selected_codes=" + ", ".join(sorted(code for code in selected_codes if code)),
        },
        {
            "gate": "protocol_matches_fixture",
            "passed": str(solution_snapshot.get("interface_plan", {}).get("dcs_protocol") or "") == fixture.expected_protocol,
            "detail": f"dcs_protocol={solution_snapshot.get('interface_plan', {}).get('dcs_protocol') or 'missing'}",
        },
        {
            "gate": "voltage_matches_fixture",
            "passed": fixture.expected_voltage in str(primary_product.get("rated_voltage") or ""),
            "detail": f"rated_voltage={primary_product.get('rated_voltage') or 'missing'}",
        },
    ]
    return gate_results


def build_real_proposal_regression_report(
    *,
    fixture: RealProposalRegressionFixture,
    requirement_card: dict[str, Any],
    evidence_bundle: dict[str, Any],
    solution_snapshot: dict[str, Any],
    gate_results: list[dict[str, Any]],
    project_id: str,
    deleted_after_run: bool,
) -> str:
    title_sources = collect_evidence_title_sources(evidence_bundle)
    evidence_titles = collect_evidence_titles(evidence_bundle)
    content = evidence_bundle.get("content") if isinstance(evidence_bundle.get("content"), dict) else {}
    quality_trace = content.get("quality_trace") or {}
    selected_products = solution_snapshot.get("selected_products") or []
    passed_count = sum(1 for gate in gate_results if gate.get("passed"))
    total_count = len(gate_results)
    direct_fixture_hits = [
        title
        for title in fixture.expected_case_titles
        if any(
            title in evidence_title
            for evidence_title in title_sources["results"] + title_sources["fallback_results"]
        )
    ]
    candidate_only_hits = [
        title
        for title in fixture.expected_case_titles
        if any(title in evidence_title for evidence_title in title_sources["case_candidates"])
        and title not in direct_fixture_hits
    ]

    lines = [
        "# Product-Driven Real Proposal Regression",
        "",
        f"- 场景：{fixture.title}",
        f"- fixture_id：`{fixture.fixture_id}`",
        f"- project_id：`{project_id}`",
        f"- run_cleanup：`{'deleted' if deleted_after_run else 'kept'}`",
        f"- 通过门禁：`{passed_count}/{total_count}`",
        "",
        "## Local Fixtures",
        "",
    ]
    lines.extend(f"- `{path}`" for path in fixture.local_fixture_paths)
    lines.extend(
        [
            "",
            "## Requirement",
            "",
            f"- product_line：`{requirement_card.get('content', {}).get('product_line') or 'missing'}`",
            f"- industry：`{requirement_card.get('content', {}).get('industry') or 'missing'}`",
            f"- blocking_items：`{len(requirement_card.get('blocking_items') or [])}`",
            "",
            "## Evidence",
            "",
            f"- quality_score：`{evidence_bundle.get('quality_score')}`",
            f"- retrieval_version：`{evidence_bundle.get('retrieval_version')}`",
            f"- primary_results_source：`{quality_trace.get('primary_results_source') or 'unknown'}`",
            f"- case_candidate_count：`{quality_trace.get('case_candidate_count') or 0}`",
            f"- direct_fixture_hits：`{'；'.join(direct_fixture_hits) if direct_fixture_hits else 'none'}`",
            f"- candidate_only_hits：`{'；'.join(candidate_only_hits) if candidate_only_hits else 'none'}`",
            "",
            "### Evidence Titles",
            "",
        ]
    )
    lines.extend(f"- {title}" for title in evidence_titles)
    lines.extend(
        [
            "",
            "## Solution",
            "",
            f"- solution_version：`{solution_snapshot.get('version')}`",
            f"- source_catalog_version：`{solution_snapshot.get('source_catalog_version') or 'missing'}`",
            f"- dcs_protocol：`{solution_snapshot.get('interface_plan', {}).get('dcs_protocol') or 'missing'}`",
            "",
            "### Selected Products",
            "",
        ]
    )
    lines.extend(
        f"- `{item.get('series_code')}` | {item.get('name')} | qty={item.get('quantity')} | {item.get('rated_voltage')}"
        for item in selected_products
    )
    lines.extend(
        [
            "",
            "## Gate Results",
            "",
            "| Gate | Status | Detail |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(
        f"| `{item.get('gate')}` | {'pass' if item.get('passed') else 'fail'} | {item.get('detail')} |"
        for item in gate_results
    )
    return "\n".join(lines) + "\n"
