from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from super_dev.host_commands import (
    load_default_project_id,
    run_project_replay_gate,
    run_quality_smoke,
    run_release_gate,
)


class HostCommandsTests(unittest.TestCase):
    def test_load_default_project_id_reads_snapshot_payload(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "project": {
                                "id": "project-123",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_default_project_id(report_path), "project-123")

    def test_run_quality_smoke_executes_pytest_style_functions(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            smoke_path = Path(tmp_dir) / "test_quality_smoke.py"
            smoke_path.write_text(
                "\n".join(
                    [
                        "STATE = []",
                        "",
                        "def test_alpha():",
                        "    STATE.append('alpha')",
                        "",
                        "def test_beta():",
                        "    STATE.append('beta')",
                    ]
                ),
                encoding="utf-8",
            )

            ran = run_quality_smoke(smoke_path)

            self.assertEqual(ran, ["test_alpha", "test_beta"])

    def test_run_project_replay_gate_passes_recommended_threshold_flags(self) -> None:
        with patch("super_dev.host_commands.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            code = run_project_replay_gate(
                project_id="project-123",
                repo_root=Path("/tmp/repo"),
                use_recommended_thresholds=True,
                recommended_thresholds_json="/tmp/thresholds.json",
            )

        self.assertEqual(code, 0)
        command = mock_run.call_args.args[0]
        self.assertIn("--use-recommended-thresholds", command)
        self.assertIn("--recommended-thresholds-json", command)
        self.assertIn("/tmp/thresholds.json", command)

    def test_run_release_gate_writes_pass_report(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_report = output_dir / "RAG_test-project-replay-eval.json"
            replay_report.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-20T14:11:45+00:00",
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                                "current_draft_version": 3,
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            },
                            "baseline_path": "/tmp/baseline.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                return_value=["test_alpha", "test_beta"],
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=replay_report,
                )

            self.assertEqual(code, 0)
            report_path = output_dir / "RAG_test-release-gate.json"
            markdown_path = output_dir / "RAG_test-release-gate.md"
            self.assertTrue(report_path.exists())
            self.assertTrue(markdown_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["summary"]["passed_step_count"], 2)
            self.assertEqual(report["project_replay_summary"]["regression_count"], 0)
            self.assertEqual(report["quality_smoke_tests"], ["test_alpha", "test_beta"])

    def test_run_release_gate_writes_failed_report_when_smoke_fails(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_report = output_dir / "RAG_test-project-replay-eval.json"
            replay_report.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                side_effect=AssertionError("smoke failed"),
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=replay_report,
                )

            self.assertEqual(code, 2)
            report = json.loads((output_dir / "RAG_test-release-gate.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["failure_reason"], "smoke failed")
            self.assertEqual(report["steps"][-1]["status"], "failed")

    def test_run_release_gate_refreshes_governance_artifacts_when_present(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_report = output_dir / "RAG_test-project-replay-eval.json"
            replay_report.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-20T14:11:45+00:00",
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                                "current_draft_version": 3,
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            },
                            "baseline_path": "/tmp/baseline.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-proof-pack.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "status": "ready",
                        "ready_count": 1,
                        "total_count": 1,
                        "completion_percent": 100,
                        "summary": {
                            "executive_summary": "base",
                            "blocking_count": 0,
                            "key_artifact_count": 1,
                            "next_actions": ["base"],
                        },
                        "blocking_artifacts": [],
                        "key_artifacts": [
                            {
                                "name": "Docs Confirmation",
                                "status": "ready",
                                "summary": "ok",
                                "path": "/tmp/docs.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-release-readiness.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "score": 100,
                        "passed": True,
                        "threshold": 85,
                        "failed_checks": [],
                        "checks": [
                            {
                                "name": "Version Alignment",
                                "passed": True,
                                "detail": "ok",
                                "severity": "low",
                                "recommendation": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                return_value=["test_alpha", "test_beta"],
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=replay_report,
                )

            self.assertEqual(code, 0)
            proof_pack = json.loads((output_dir / "RAG_test-proof-pack.json").read_text(encoding="utf-8"))
            readiness = json.loads((output_dir / "RAG_test-release-readiness.json").read_text(encoding="utf-8"))
            artifact_names = [item["name"] for item in proof_pack["key_artifacts"]]
            check_names = [item["name"] for item in readiness["checks"]]
            self.assertIn("Release Gate", artifact_names)
            self.assertIn("Governance: Release Gate", check_names)
            self.assertIn("Governance: Project Replay Evaluation", check_names)
            self.assertTrue((output_dir / "RAG_test-proof-pack.md").exists())
            self.assertTrue((output_dir / "RAG_test-proof-pack-summary.md").exists())
            self.assertTrue((output_dir / "RAG_test-release-readiness.md").exists())

    def test_run_release_gate_embeds_replay_governance_advisory_when_reports_exist(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_report = output_dir / "RAG_test-project-replay-eval.json"
            replay_report.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-20T14:11:45+00:00",
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                                "current_draft_version": 3,
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            },
                            "baseline_path": "/tmp/baseline.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-import.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T07:49:50+00:00",
                        "summary": {
                            "input_files": 1,
                            "loaded_snapshots": 1,
                            "imported_snapshots": 0,
                            "structured_imported_snapshots": 0,
                            "legacy_imported_snapshots": 0,
                            "skipped_snapshots": 1,
                            "invalid_inputs": 0,
                        },
                        "skipped": [
                            {
                                "source_path": "/tmp/replay.json",
                                "project_id": "project-123",
                                "reason": "history_snapshot_exists",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-refresh.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T06:27:17+00:00",
                        "summary": {
                            "discovered_projects": 1,
                            "eligible_candidates": 0,
                            "refreshed_projects": 0,
                            "failed_projects": 0,
                            "skipped_projects": 1,
                        },
                        "skipped": [
                            {
                                "project_id": "project-123",
                                "reason": "current_draft_version=0",
                            }
                        ],
                        "threshold_refresh": {
                            "exit_code": 0,
                            "json_path": str(output_dir / "RAG_test-project-replay-thresholds.json"),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-thresholds.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "total_snapshots": 9,
                            "eligible_healthy_snapshots": 9,
                            "trace_metric_samples": 0,
                            "retrieval_final_metric_samples": 0,
                            "retrieval_semantic_metric_samples": 0,
                            "retrieval_rerank_metric_samples": 0,
                        },
                        "recommendations": {
                            "min_trace_sections": None,
                            "min_avg_retrieval_final": None,
                            "min_avg_retrieval_semantic": None,
                            "min_avg_retrieval_rerank": None,
                        },
                        "notes": [
                            "9 healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics"
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-layer4-ai-wiki-prior-eval.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "total_sections": 6,
                            "sections_with_case_candidates": 6,
                            "sections_with_equipment_target": 6,
                            "baseline": {
                                "top1_section_type_match": 5,
                                "top1_equipment_type_match": 3,
                            },
                            "with_prior": {
                                "top1_section_type_match": 5,
                                "top1_equipment_type_match": 3,
                                "prior_hit_sections": 6,
                                "prior_hit_block_count": 16,
                                "total_prior_boost": 1.72,
                            },
                            "deltas": {
                                "section_match_rank_unchanged": 5,
                                "equipment_match_rank_unchanged": 4,
                                "top1_changed": 1,
                            },
                            "evaluation_mode": "fast_heuristic_only",
                            "docx_fast_extract_enabled": True,
                            "skipped_documents": 0,
                        },
                        "records": [],
                        "skipped_documents": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                return_value=["test_alpha"],
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=replay_report,
                )

            self.assertEqual(code, 0)
            report = json.loads((output_dir / "RAG_test-release-gate.json").read_text(encoding="utf-8"))
            self.assertEqual(report["project_replay_import_summary"]["input_files"], 1)
            self.assertEqual(report["project_replay_refresh_summary"]["skipped_projects"], 1)
            self.assertEqual(report["project_replay_threshold_summary"]["trace_metric_samples"], 0)
            self.assertFalse(report["project_replay_threshold_summary"]["recommendations_available"])
            self.assertEqual(report["knowledge_wiki_prior_summary"]["total_sections"], 6)
            self.assertTrue(report["knowledge_wiki_prior_summary"]["stable_for_release"])
            markdown = (output_dir / "RAG_test-release-gate.md").read_text(encoding="utf-8")
            self.assertIn("Replay Governance Advisory", markdown)
            self.assertIn("AI Wiki Prior Advisory", markdown)
            self.assertIn("history_snapshot_exists", markdown)
            self.assertIn("current_draft_version=0", markdown)
            self.assertIn("legacy retrieval telemetry", markdown)
            self.assertIn("holdout eval produced no regression signal", markdown)

    def test_run_release_gate_adds_replay_governance_artifacts_to_proof_pack(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_report = output_dir / "RAG_test-project-replay-eval.json"
            replay_report.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-20T14:11:45+00:00",
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                                "current_draft_version": 3,
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            },
                            "baseline_path": "/tmp/baseline.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-proof-pack.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "status": "ready",
                        "ready_count": 1,
                        "total_count": 1,
                        "completion_percent": 100,
                        "summary": {
                            "executive_summary": "base",
                            "blocking_count": 0,
                            "key_artifact_count": 1,
                            "next_actions": ["base"],
                        },
                        "blocking_artifacts": [],
                        "key_artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-refresh.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "discovered_projects": 1,
                            "eligible_candidates": 0,
                            "refreshed_projects": 0,
                            "failed_projects": 0,
                            "skipped_projects": 1,
                        },
                        "skipped": [{"project_id": "project-123", "reason": "current_draft_version=0"}],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-import.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "input_files": 1,
                            "loaded_snapshots": 1,
                            "imported_snapshots": 0,
                            "structured_imported_snapshots": 0,
                            "legacy_imported_snapshots": 0,
                            "skipped_snapshots": 1,
                            "invalid_inputs": 0,
                        },
                        "skipped": [{"project_id": "project-123", "reason": "history_snapshot_exists"}],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-thresholds.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "eligible_healthy_snapshots": 9,
                            "trace_metric_samples": 0,
                        },
                        "recommendations": {
                            "min_trace_sections": None,
                        },
                        "notes": ["legacy retrieval telemetry"],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-layer4-ai-wiki-prior-eval.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "total_sections": 6,
                            "sections_with_case_candidates": 6,
                            "sections_with_equipment_target": 6,
                            "baseline": {
                                "top1_section_type_match": 5,
                                "top1_equipment_type_match": 3,
                            },
                            "with_prior": {
                                "top1_section_type_match": 5,
                                "top1_equipment_type_match": 3,
                                "prior_hit_sections": 6,
                                "prior_hit_block_count": 16,
                                "total_prior_boost": 1.72,
                            },
                            "deltas": {
                                "section_match_rank_unchanged": 5,
                                "equipment_match_rank_unchanged": 4,
                                "top1_changed": 1,
                            },
                            "evaluation_mode": "fast_heuristic_only",
                            "docx_fast_extract_enabled": True,
                            "skipped_documents": 0,
                        },
                        "records": [],
                        "skipped_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-release-readiness.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "score": 100,
                        "passed": True,
                        "threshold": 85,
                        "failed_checks": [],
                        "checks": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                return_value=["test_alpha"],
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=replay_report,
                )

            self.assertEqual(code, 0)
            proof_pack = json.loads((output_dir / "RAG_test-proof-pack.json").read_text(encoding="utf-8"))
            artifact_names = [item["name"] for item in proof_pack["key_artifacts"]]
            self.assertIn("AI Wiki Prior Evaluation", artifact_names)
            self.assertIn("Project Replay Import", artifact_names)
            self.assertIn("Project Replay Refresh", artifact_names)
            self.assertIn("Project Replay Threshold Recommendation", artifact_names)
            readiness = json.loads((output_dir / "RAG_test-release-readiness.json").read_text(encoding="utf-8"))
            readiness_checks = {item["name"]: item for item in readiness["checks"]}
            self.assertIn("Governance: AI Wiki Prior Evaluation", readiness_checks)
            self.assertIn("Governance: Project Replay Import", readiness_checks)
            self.assertIn("Governance: Project Replay Refresh", readiness_checks)
            self.assertIn("Governance: Project Replay Threshold Recommendation", readiness_checks)
            self.assertIn("sections=6", readiness_checks["Governance: AI Wiki Prior Evaluation"]["detail"])
            self.assertIn("evaluation=fast_heuristic_only", readiness_checks["Governance: AI Wiki Prior Evaluation"]["detail"])
            self.assertIn("history_snapshot_exists", readiness_checks["Governance: Project Replay Import"]["detail"])
            self.assertIn("current_draft_version=0", readiness_checks["Governance: Project Replay Refresh"]["detail"])
            self.assertIn("flags_ready=False", readiness_checks["Governance: Project Replay Threshold Recommendation"]["detail"])

    def test_refresh_governance_artifacts_updates_proof_pack_after_readiness_refresh(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            output_dir = repo_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "RAG_test-proof-pack.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "status": "attention",
                        "ready_count": 1,
                        "total_count": 2,
                        "completion_percent": 50,
                        "summary": {
                            "executive_summary": "base",
                            "blocking_count": 1,
                            "key_artifact_count": 1,
                            "next_actions": ["base"],
                        },
                        "blocking_artifacts": [],
                        "key_artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-release-readiness.json").write_text(
                json.dumps(
                    {
                        "project_name": "RAG_test",
                        "generated_at": "2026-04-20T05:00:00+00:00",
                        "score": 95,
                        "passed": False,
                        "threshold": 85,
                        "failed_checks": ["old"],
                        "checks": [],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "RAG_test-project-replay-eval.json").write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-20T14:11:45+00:00",
                            "project": {
                                "id": "project-123",
                                "name": "Demo Project",
                                "current_draft_version": 3,
                            },
                            "summary": {
                                "health": "healthy",
                                "total_sections": 7,
                                "status_counts": {"generated": 7},
                                "quality_counts": {"passed": 7},
                            },
                        },
                        "comparison": {
                            "summary": {
                                "regression_count": 0,
                                "changed_section_count": 0,
                            },
                            "baseline_path": "/tmp/baseline.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("super_dev.host_commands.run_project_replay_gate", return_value=0), patch(
                "super_dev.host_commands.run_quality_smoke",
                return_value=["test_alpha"],
            ):
                code = run_release_gate(
                    project_id="project-123",
                    repo_root=repo_root,
                    report_path=output_dir / "RAG_test-project-replay-eval.json",
                    skip_project_replay=True,
                )

            self.assertEqual(code, 0)
            proof_pack = json.loads((output_dir / "RAG_test-proof-pack.json").read_text(encoding="utf-8"))
            release_readiness_artifact = next(
                item for item in proof_pack["key_artifacts"] if item["name"] == "Release Readiness"
            )
            self.assertEqual(release_readiness_artifact["status"], "ready")
            self.assertIn("passed=True", release_readiness_artifact["summary"])


if __name__ == "__main__":
    unittest.main()
