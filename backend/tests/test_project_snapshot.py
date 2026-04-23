from __future__ import annotations

import unittest

from app.services.validation.project_snapshot import (
    build_project_replay_threshold_recommendation,
    build_project_snapshot,
    build_project_snapshot_history_stem,
    build_section_snapshot,
    collect_project_snapshot_gate_failures,
    compare_project_snapshots,
    render_project_replay_threshold_markdown,
    unwrap_project_snapshot_payload,
)


class ProjectSnapshotTests(unittest.TestCase):
    def test_unwrap_project_snapshot_payload_accepts_wrapped_and_raw_shapes(self) -> None:
        raw = {"generated_at": "2026-04-20T13:00:00Z", "project": {"id": "project-1"}}
        wrapped = {"snapshot": raw, "comparison": {"summary": {"changed_section_count": 0}}}

        self.assertEqual(unwrap_project_snapshot_payload(raw), raw)
        self.assertEqual(unwrap_project_snapshot_payload(wrapped), raw)

    def test_build_project_snapshot_history_stem_uses_project_and_timestamp(self) -> None:
        stem = build_project_snapshot_history_stem(
            {
                "generated_at": "2026-04-20T13:39:47.243195+00:00",
                "project": {
                    "id": "359acbab-0acd-4c19-aa31-7f6d50512a12",
                    "current_draft_version": 3,
                },
            }
        )

        self.assertEqual(stem, "359acbab-0acd-4c19-aa31-7f6d50512a12-v3-20260420133947")

    def test_build_section_snapshot_extracts_quality_and_generation_fields(self) -> None:
        snapshot = build_section_snapshot(
            {
                "section_id": "3",
                "title": "供电系统条件与负载参数",
                "status": "generated",
                "updated_at": "2026-04-20T13:30:42Z",
                "validator_result": {
                    "quality_gate": {
                        "status": "passed",
                        "score": 0.96,
                        "final_review": {
                            "issues": [
                                {"code": "CLIENT_TONE_SOFTEN"},
                                {"code": "SECTION_CLOSURE_WEAK"},
                            ]
                        },
                    },
                    "generation_details": {
                        "effective_path": "extractive_reuse_deterministic",
                        "retrieval_mode": "section_pack",
                        "refinement_status": "deterministic_assembled",
                        "assembled_block_count": 4,
                        "selected_blocks": [
                            {
                                "retrieval_score_breakdown": {
                                    "final": 0.92,
                                    "semantic": 0.81,
                                    "hybrid_rrf": 0.12,
                                    "rerank": 0.73,
                                }
                            },
                            {
                                "retrieval_score_breakdown": {
                                    "final": 0.88,
                                    "semantic": 0.77,
                                    "hybrid_rrf": 0.08,
                                    "rerank": 0.69,
                                }
                            },
                        ],
                        "knowledge_wiki_prior_summary": {
                            "prior_hit_block_count": 3,
                            "total_prior_boost": 0.42,
                        },
                    },
                },
            }
        )

        self.assertEqual(snapshot["section_id"], "3")
        self.assertEqual(snapshot["quality_status"], "passed")
        self.assertEqual(snapshot["quality_score"], 0.96)
        self.assertEqual(snapshot["effective_path"], "extractive_reuse_deterministic")
        self.assertEqual(snapshot["refinement_status"], "deterministic_assembled")
        self.assertEqual(snapshot["knowledge_wiki_prior_hit_block_count"], 3)
        self.assertEqual(snapshot["selected_block_count"], 2)
        self.assertEqual(snapshot["retrieval_trace_source"], "blocks")
        self.assertEqual(snapshot["retrieval_trace_count"], 2)
        self.assertEqual(snapshot["retrieval_avg_final_score"], 0.9)
        self.assertEqual(snapshot["retrieval_avg_semantic_score"], 0.79)
        self.assertEqual(snapshot["retrieval_avg_rerank_score"], 0.71)
        self.assertEqual(snapshot["quality_issue_codes"], ["CLIENT_TONE_SOFTEN", "SECTION_CLOSURE_WEAK"])

    def test_build_project_snapshot_summarizes_health_counts_and_priors(self) -> None:
        snapshot = build_project_snapshot(
            project_payload={
                "id": "project-1",
                "name": "测试项目",
                "status": "DRAFT_READY",
                "current_draft_version": 3,
                "industry": "钢铁",
                "product_line": "lci",
            },
            sections_payload=[
                {
                    "section_id": "1",
                    "title": "项目概述",
                    "status": "generated",
                    "validator_result": {
                        "quality_gate": {"status": "passed", "score": 0.94, "final_review": {"issues": []}},
                        "generation_details": {
                            "effective_path": "extractive_reuse_llm_finalize",
                            "refinement_status": "rewrite_applied",
                            "selected_blocks": [
                                {
                                    "retrieval_score_breakdown": {
                                        "final": 0.84,
                                        "semantic": 0.74,
                                        "hybrid_rrf": 0.09,
                                        "rerank": 0.66,
                                    }
                                }
                            ],
                            "knowledge_wiki_prior_summary": {"prior_hit_block_count": 1, "total_prior_boost": 0.13},
                        },
                    },
                },
                {
                    "section_id": "2",
                    "title": "现场边界",
                    "status": "generated",
                    "validator_result": {
                        "quality_gate": {"status": "passed", "score": 0.96, "final_review": {"issues": []}},
                        "generation_details": {
                            "effective_path": "extractive_reuse_deterministic",
                            "refinement_status": "deterministic_assembled",
                            "selected_blocks": [
                                {
                                    "retrieval_score_breakdown": {
                                        "final": 0.9,
                                        "semantic": 0.82,
                                        "hybrid_rrf": 0.1,
                                        "rerank": 0.72,
                                    }
                                },
                                {
                                    "retrieval_score_breakdown": {
                                        "final": 0.86,
                                        "semantic": 0.78,
                                        "hybrid_rrf": 0.08,
                                        "rerank": 0.68,
                                    }
                                },
                            ],
                            "knowledge_wiki_prior_summary": {"prior_hit_block_count": 0, "total_prior_boost": 0.0},
                        },
                    },
                },
            ],
            review_tasks_payload=[],
            validation_payload={"status": "passed"},
            export_payload={"status": "succeeded", "file_name": "demo.md"},
            source_label="http://127.0.0.1:8000/api/v1",
            generated_at="2026-04-20T13:32:30Z",
        )

        self.assertEqual(snapshot["summary"]["total_sections"], 2)
        self.assertEqual(snapshot["summary"]["status_counts"], {"generated": 2})
        self.assertEqual(snapshot["summary"]["quality_counts"], {"passed": 2})
        self.assertEqual(snapshot["summary"]["effective_paths"]["extractive_reuse_deterministic"], 1)
        self.assertEqual(snapshot["summary"]["prior_hit_sections"], ["1"])
        self.assertEqual(snapshot["summary"]["sections_with_retrieval_trace"], 2)
        self.assertEqual(snapshot["summary"]["retrieval_trace_sections"], ["1", "2"])
        self.assertEqual(snapshot["summary"]["total_selected_blocks"], 3)
        self.assertEqual(snapshot["summary"]["average_selected_block_count"], 1.5)
        self.assertEqual(snapshot["summary"]["average_retrieval_final_score"], 0.86)
        self.assertEqual(snapshot["summary"]["average_retrieval_semantic_score"], 0.77)
        self.assertEqual(snapshot["summary"]["average_retrieval_rerank_score"], 0.68)
        self.assertEqual(snapshot["summary"]["open_blocking_review_task_count"], 0)
        self.assertEqual(snapshot["summary"]["open_advisory_review_task_count"], 0)
        self.assertEqual(snapshot["summary"]["health"], "healthy")

    def test_compare_project_snapshots_flags_improvement_and_regression(self) -> None:
        baseline = {
            "generated_at": "2026-04-20T10:00:00Z",
            "summary": {
                "average_quality_score": 0.9,
                "average_retrieval_final_score": 0.74,
                "average_retrieval_semantic_score": 0.61,
                "average_retrieval_rerank_score": 0.52,
                "status_counts": {"generated": 1},
                "quality_counts": {"passed": 1},
            },
            "sections": [
                {
                    "section_id": "2",
                    "title": "现场边界",
                    "status": "review_required",
                    "quality_status": "review_required",
                    "quality_score": 0.9,
                    "effective_path": "extractive_reuse_llm_finalize",
                    "refinement_status": "rewrite_applied",
                    "retrieval_trace_count": 1,
                    "retrieval_avg_final_score": 0.71,
                    "retrieval_avg_semantic_score": 0.58,
                    "retrieval_avg_rerank_score": 0.49,
                },
                {
                    "section_id": "3",
                    "title": "供电条件",
                    "status": "generated",
                    "quality_status": "passed",
                    "quality_score": 0.95,
                    "effective_path": "extractive_reuse_deterministic",
                    "refinement_status": "deterministic_assembled",
                    "retrieval_trace_count": 2,
                    "retrieval_avg_final_score": 0.79,
                    "retrieval_avg_semantic_score": 0.66,
                    "retrieval_avg_rerank_score": 0.57,
                },
            ],
        }
        current = {
            "generated_at": "2026-04-20T13:00:00Z",
            "summary": {
                "average_quality_score": 0.93,
                "average_retrieval_final_score": 0.82,
                "average_retrieval_semantic_score": 0.7,
                "average_retrieval_rerank_score": 0.6,
                "status_counts": {"generated": 2},
                "quality_counts": {"passed": 1, "review_required": 1},
            },
            "sections": [
                {
                    "section_id": "2",
                    "title": "现场边界",
                    "status": "generated",
                    "quality_status": "passed",
                    "quality_score": 0.96,
                    "effective_path": "extractive_reuse_deterministic",
                    "refinement_status": "deterministic_assembled",
                    "retrieval_trace_count": 2,
                    "retrieval_avg_final_score": 0.82,
                    "retrieval_avg_semantic_score": 0.68,
                    "retrieval_avg_rerank_score": 0.61,
                },
                {
                    "section_id": "3",
                    "title": "供电条件",
                    "status": "review_required",
                    "quality_status": "review_required",
                    "quality_score": 0.88,
                    "effective_path": "extractive_reuse_llm_finalize",
                    "refinement_status": "rewrite_applied",
                    "retrieval_trace_count": 1,
                    "retrieval_avg_final_score": 0.7,
                    "retrieval_avg_semantic_score": 0.61,
                    "retrieval_avg_rerank_score": 0.52,
                },
            ],
        }

        comparison = compare_project_snapshots(
            baseline_snapshot=baseline,
            current_snapshot=current,
        )

        self.assertEqual(comparison["summary"]["changed_section_count"], 2)
        self.assertEqual(comparison["summary"]["regression_count"], 1)
        self.assertEqual(comparison["summary"]["improvement_count"], 1)
        self.assertEqual(comparison["summary"]["retrieval_regression_count"], 1)
        self.assertEqual(comparison["summary"]["retrieval_improvement_count"], 1)
        self.assertEqual(comparison["summary"]["average_quality_score_delta"], 0.03)
        self.assertEqual(comparison["summary"]["average_retrieval_final_score_delta"], 0.08)
        self.assertEqual(comparison["summary"]["average_retrieval_semantic_score_delta"], 0.09)
        self.assertEqual(comparison["summary"]["average_retrieval_rerank_score_delta"], 0.08)
        self.assertEqual(comparison["improvements"][0]["section_id"], "2")
        self.assertEqual(comparison["improvements"][0]["retrieval_trace_count_change"], (1, 2))
        self.assertEqual(comparison["improvements"][0]["retrieval_final_score_delta"], 0.11)
        self.assertEqual(comparison["regressions"][0]["section_id"], "3")
        self.assertEqual(comparison["regressions"][0]["retrieval_trace_count_change"], (2, 1))
        self.assertEqual(comparison["regressions"][0]["retrieval_final_score_delta"], -0.09)
        self.assertEqual(comparison["retrieval_improvements"][0]["section_id"], "2")
        self.assertEqual(comparison["retrieval_regressions"][0]["section_id"], "3")

    def test_collect_project_snapshot_gate_failures_requires_healthy_and_zero_regressions(self) -> None:
        snapshot = {
            "summary": {
                "health": "attention",
                "open_blocking_review_task_count": 1,
                "sections_with_retrieval_trace": 1,
                "average_retrieval_final_score": 0.41,
                "average_retrieval_semantic_score": 0.36,
                "average_retrieval_rerank_score": 0.39,
            }
        }
        comparison = {
            "summary": {
                "regression_count": 2,
                "retrieval_regression_count": 1,
            }
        }

        failures = collect_project_snapshot_gate_failures(
            snapshot=snapshot,
            comparison=comparison,
            require_healthy=True,
            require_zero_regressions=True,
            require_zero_retrieval_regressions=True,
            require_no_open_blocking=True,
            min_retrieval_trace_sections=2,
            min_average_retrieval_final_score=0.55,
            min_average_retrieval_semantic_score=0.45,
            min_average_retrieval_rerank_score=0.45,
        )

        self.assertEqual(
            failures,
            [
                "project replay snapshot health is attention",
                "project replay snapshot still has 1 open blocking review tasks",
                "project replay snapshot has 1 sections with structured retrieval trace, below required 2",
                "project replay snapshot average retrieval final score is 0.41, below required 0.55",
                "project replay snapshot average retrieval semantic score is 0.36, below required 0.45",
                "project replay snapshot average retrieval rerank score is 0.39, below required 0.45",
                "project replay comparison found 2 regressions",
                "project replay comparison found 1 retrieval regressions",
            ],
        )

    def test_build_project_replay_threshold_recommendation_recommends_from_healthy_history(self) -> None:
        snapshots = [
            {
                "generated_at": "2026-04-21T10:00:00Z",
                "project": {"id": "project-1"},
                "summary": {
                    "health": "healthy",
                    "total_sections": 7,
                    "sections_with_retrieval_trace": 5,
                    "total_selected_blocks": 12,
                    "average_retrieval_final_score": 0.81,
                    "average_retrieval_semantic_score": 0.71,
                    "average_retrieval_rerank_score": 0.66,
                },
            },
            {
                "generated_at": "2026-04-21T11:00:00Z",
                "project": {"id": "project-1"},
                "summary": {
                    "health": "healthy",
                    "total_sections": 7,
                    "sections_with_retrieval_trace": 6,
                    "total_selected_blocks": 14,
                    "average_retrieval_final_score": 0.84,
                    "average_retrieval_semantic_score": 0.73,
                    "average_retrieval_rerank_score": 0.69,
                },
            },
            {
                "generated_at": "2026-04-21T12:00:00Z",
                "project": {"id": "project-1"},
                "summary": {
                    "health": "healthy",
                    "total_sections": 7,
                    "sections_with_retrieval_trace": 4,
                    "total_selected_blocks": 10,
                    "average_retrieval_final_score": 0.78,
                    "average_retrieval_semantic_score": 0.69,
                    "average_retrieval_rerank_score": 0.63,
                },
            },
        ]

        recommendation = build_project_replay_threshold_recommendation(
            snapshots=snapshots,
            min_samples=3,
        )

        self.assertEqual(recommendation["summary"]["eligible_healthy_snapshots"], 3)
        self.assertEqual(recommendation["recommendations"]["min_trace_sections"], 4)
        self.assertEqual(recommendation["recommendations"]["min_avg_retrieval_final"], 0.772)
        self.assertEqual(recommendation["recommendations"]["min_avg_retrieval_semantic"], 0.678)
        self.assertEqual(recommendation["recommendations"]["min_avg_retrieval_rerank"], 0.622)
        markdown = render_project_replay_threshold_markdown(recommendation)
        self.assertIn("Project Replay Threshold Recommendation", markdown)
        self.assertIn("`--min-avg-retrieval-final`", markdown)

    def test_build_project_replay_threshold_recommendation_reports_insufficient_samples(self) -> None:
        recommendation = build_project_replay_threshold_recommendation(
            snapshots=[
                {
                    "generated_at": "2026-04-21T10:00:00Z",
                    "project": {"id": "project-1"},
                    "summary": {
                        "health": "healthy",
                        "total_sections": 7,
                    },
                    "sections": [
                        {"section_id": "1", "retrieval_mode": "section_pack"},
                        {"section_id": "2", "retrieval_mode": "full_section"},
                    ],
                }
            ],
            min_samples=3,
        )

        self.assertIsNone(recommendation["recommendations"]["min_trace_sections"])
        self.assertIsNone(recommendation["recommendations"]["min_avg_retrieval_final"])
        self.assertIn("insufficient healthy snapshot samples", recommendation["notes"][0])
        self.assertIn("legacy retrieval telemetry", recommendation["notes"][-1])
        self.assertEqual(
            recommendation["summary"]["retrieval_mode_counts"],
            {"full_section": 1, "section_pack": 1},
        )


if __name__ == "__main__":
    unittest.main()
