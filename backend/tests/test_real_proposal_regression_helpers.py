from __future__ import annotations

from pathlib import Path
import unittest

from app.services.regression import (
    build_lci_real_proposal_fixture,
    collect_evidence_titles,
    collect_evidence_title_sources,
    evaluate_real_proposal_gate,
)


class RealProposalRegressionHelperTests(unittest.TestCase):
    def test_collect_evidence_titles_dedupes_results_and_cases(self) -> None:
        titles = collect_evidence_titles(
            {
                "content": {
                    "results": [{"source_title": "A.docx"}],
                    "fallback_results": [{"source_title": "B.pdf"}],
                    "case_candidates": [{"file_name": "A.docx"}, {"file_name": "C.docx"}],
                }
            }
        )
        self.assertEqual(titles, ["A.docx", "B.pdf", "C.docx"])

    def test_collect_evidence_title_sources_preserves_source_buckets(self) -> None:
        title_sources = collect_evidence_title_sources(
            {
                "content": {
                    "results": [{"source_title": "A.docx"}, {"source_title": "A.docx"}],
                    "fallback_results": [{"source_title": "B.pdf"}],
                    "case_candidates": [{"file_name": "C.docx"}],
                }
            }
        )
        self.assertEqual(
            title_sources,
            {
                "results": ["A.docx"],
                "fallback_results": ["B.pdf"],
                "case_candidates": ["C.docx"],
            },
        )

    def test_evaluate_real_proposal_gate_passes_for_matching_payload(self) -> None:
        fixture = build_lci_real_proposal_fixture(Path("/tmp/repo"))
        gates = evaluate_real_proposal_gate(
            fixture=fixture,
            requirement_card={"blocking_items": [], "content": {"product_line": "lci", "industry": "钢铁"}},
            evidence_bundle={
                "quality_score": 0.81,
                "retrieval_version": 1,
                "content": {
                    "quality_trace": {"primary_results_source": "case_fallback", "case_candidate_count": 3},
                    "results": [],
                    "fallback_results": [{"source_title": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx"}],
                    "case_candidates": [{"file_name": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx"}],
                },
            },
            solution_snapshot={
                "version": 1,
                "source_catalog_version": "seed-20260419-v1",
                "interface_plan": {"dcs_protocol": "Profibus-DP"},
                "selected_products": [
                    {"series_code": "lci_sync_drive", "rated_voltage": "10kV"},
                    {"series_code": "rectifier_transformer"},
                    {"series_code": "excitation_cabinet"},
                    {"series_code": "bypass_cabinet"},
                ],
            },
        )
        self.assertTrue(all(item["passed"] for item in gates))

    def test_evidence_quality_gate_accepts_direct_fixture_hit_even_when_score_is_low(self) -> None:
        fixture = build_lci_real_proposal_fixture(Path("/tmp/repo"))
        gates = evaluate_real_proposal_gate(
            fixture=fixture,
            requirement_card={"blocking_items": [], "content": {"product_line": "lci", "industry": "钢铁"}},
            evidence_bundle={
                "quality_score": 0.08,
                "retrieval_version": 1,
                "content": {
                    "quality_trace": {"primary_results_source": "retrieval_results", "case_candidate_count": 0},
                    "results": [{"source_title": "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx"}],
                    "fallback_results": [],
                    "case_candidates": [],
                },
            },
            solution_snapshot={
                "version": 1,
                "source_catalog_version": "seed-20260419-v1",
                "interface_plan": {"dcs_protocol": "Profibus-DP"},
                "selected_products": [
                    {"series_code": "lci_sync_drive", "rated_voltage": "10kV"},
                    {"series_code": "rectifier_transformer"},
                    {"series_code": "excitation_cabinet"},
                    {"series_code": "bypass_cabinet"},
                ],
            },
        )

        quality_gate = next(item for item in gates if item["gate"] == "evidence_quality_sufficient")
        self.assertTrue(quality_gate["passed"])
        self.assertIn("acceptance=fixture_direct_hit", quality_gate["detail"])

    def test_evidence_quality_gate_stays_blocking_for_candidate_only_hit(self) -> None:
        fixture = build_lci_real_proposal_fixture(Path("/tmp/repo"))
        gates = evaluate_real_proposal_gate(
            fixture=fixture,
            requirement_card={"blocking_items": [], "content": {"product_line": "lci", "industry": "钢铁"}},
            evidence_bundle={
                "quality_score": 0.08,
                "retrieval_version": 1,
                "content": {
                    "quality_trace": {"primary_results_source": "retrieval_results", "case_candidate_count": 1},
                    "results": [{"source_title": "某通用变频项目方案.docx"}],
                    "fallback_results": [],
                    "case_candidates": [{"file_name": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx"}],
                },
            },
            solution_snapshot={
                "version": 1,
                "source_catalog_version": "seed-20260419-v1",
                "interface_plan": {"dcs_protocol": "Profibus-DP"},
                "selected_products": [
                    {"series_code": "lci_sync_drive", "rated_voltage": "10kV"},
                    {"series_code": "rectifier_transformer"},
                    {"series_code": "excitation_cabinet"},
                    {"series_code": "bypass_cabinet"},
                ],
            },
        )

        hit_gate = next(item for item in gates if item["gate"] == "evidence_hits_real_samples")
        quality_gate = next(item for item in gates if item["gate"] == "evidence_quality_sufficient")
        self.assertTrue(hit_gate["passed"])
        self.assertIn("candidate_only_hits=宝山钢铁股份有限公司三鼓风LCI改造方案.docx", hit_gate["detail"])
        self.assertFalse(quality_gate["passed"])
        self.assertIn("acceptance=insufficient", quality_gate["detail"])


if __name__ == "__main__":
    unittest.main()
