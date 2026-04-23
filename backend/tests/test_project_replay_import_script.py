from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.import_project_replay_history import (
    discover_replay_import_inputs,
    import_project_replay_history,
    render_project_replay_import_markdown,
)


class ProjectReplayImportScriptTests(unittest.TestCase):
    def test_discover_replay_import_inputs_deduplicates_explicit_and_directory_sources(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            nested_dir = root / "incoming"
            nested_dir.mkdir(parents=True, exist_ok=True)
            explicit = nested_dir / "a.json"
            explicit.write_text("{}", encoding="utf-8")
            extra = nested_dir / "b.json"
            extra.write_text("{}", encoding="utf-8")

            paths = discover_replay_import_inputs(
                input_jsons=[str(explicit)],
                input_dirs=[str(nested_dir)],
            )

            self.assertEqual(
                [path.name for path in paths],
                ["a.json", "b.json"],
            )

    def test_import_project_replay_history_normalizes_eval_payload_and_skips_duplicates(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "incoming"
            input_dir.mkdir(parents=True, exist_ok=True)
            history_dir = root / "history"
            replay_eval = input_dir / "replay-eval.json"
            replay_eval.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-22T10:00:00Z",
                            "project": {
                                "id": "project-123",
                                "current_draft_version": 4,
                            },
                            "summary": {
                                "health": "healthy",
                                "sections_with_retrieval_trace": 5,
                                "average_retrieval_final_score": 0.82,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            first_report = import_project_replay_history(
                input_paths=[replay_eval],
                history_dir=history_dir,
            )
            second_report = import_project_replay_history(
                input_paths=[replay_eval],
                history_dir=history_dir,
            )

            self.assertEqual(first_report["summary"]["imported_snapshots"], 1)
            self.assertEqual(first_report["summary"]["backfilled_snapshots"], 0)
            self.assertEqual(first_report["summary"]["structured_imported_snapshots"], 1)
            self.assertEqual(second_report["summary"]["imported_snapshots"], 0)
            self.assertEqual(second_report["summary"]["skipped_snapshots"], 1)
            history_files = sorted(history_dir.glob("*.json"))
            self.assertEqual(len(history_files), 1)
            stored = json.loads(history_files[0].read_text(encoding="utf-8"))
            self.assertIn("snapshot", stored)
            self.assertEqual(stored["snapshot"]["project"]["id"], "project-123")

    def test_import_project_replay_history_backfills_existing_gate_thresholds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_dir = root / "incoming"
            input_dir.mkdir(parents=True, exist_ok=True)
            history_dir = root / "history"
            replay_eval = input_dir / "replay-eval.json"
            replay_eval.write_text(
                json.dumps(
                    {
                        "snapshot": {
                            "generated_at": "2026-04-22T10:00:00Z",
                            "project": {
                                "id": "project-123",
                                "current_draft_version": 4,
                            },
                            "summary": {
                                "health": "healthy",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            import_project_replay_history(
                input_paths=[replay_eval],
                history_dir=history_dir,
            )
            report = import_project_replay_history(
                input_paths=[replay_eval],
                history_dir=history_dir,
                gate_thresholds={
                    "recommendation_requested": True,
                    "recommended_thresholds_available": False,
                    "used_recommended_thresholds": False,
                    "recommended_thresholds_path": "/tmp/thresholds.json",
                    "threshold_source": "recommended_unavailable",
                },
                backfill_existing_gate_thresholds=True,
            )

            self.assertEqual(report["summary"]["imported_snapshots"], 0)
            self.assertEqual(report["summary"]["backfilled_snapshots"], 1)
            self.assertEqual(
                report["summary"]["threshold_source_counts"],
                {"recommended_unavailable": 1},
            )
            history_files = sorted(history_dir.glob("*.json"))
            stored = json.loads(history_files[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["gate_thresholds"]["threshold_source"], "recommended_unavailable")

    def test_render_project_replay_import_markdown_includes_threshold_refresh(self) -> None:
        markdown = render_project_replay_import_markdown(
            {
                "generated_at": "2026-04-22T10:00:00Z",
                "project_id_filter": "",
                "use_recommended_thresholds": True,
                "summary": {
                    "input_files": 2,
                    "loaded_snapshots": 1,
                    "imported_snapshots": 1,
                    "backfilled_snapshots": 1,
                    "structured_imported_snapshots": 1,
                    "legacy_imported_snapshots": 0,
                    "gate_threshold_annotations": 1,
                    "gate_threshold_backfills": 1,
                    "threshold_source_counts": {"recommended_unavailable": 2},
                    "skipped_snapshots": 1,
                    "invalid_inputs": 0,
                },
                "imported": [
                    {
                        "project_id": "project-123",
                        "draft_version": 4,
                        "health": "healthy",
                        "trace_sections": 5,
                        "average_retrieval_final_score": 0.82,
                        "has_structured_metrics": True,
                        "threshold_source": "recommended_unavailable",
                        "destination_path": "/tmp/history/project-123-v4.json",
                    }
                ],
                "backfilled": [
                    {
                        "project_id": "project-123",
                        "draft_version": 4,
                        "threshold_source": "recommended_unavailable",
                        "destination_path": "/tmp/history/project-123-v4.json",
                    }
                ],
                "skipped": [
                    {
                        "source_path": "/tmp/incoming/replay.json",
                        "project_id": "project-123",
                        "reason": "history_snapshot_exists",
                        "destination_path": "/tmp/history/project-123-v4.json",
                    }
                ],
                "invalid": [],
                "threshold_refresh": {
                    "exit_code": 0,
                    "markdown_path": "/tmp/thresholds.md",
                    "json_path": "/tmp/thresholds.json",
                    "notes": "Recommendation summary: healthy=1",
                },
            }
        )

        self.assertIn("Project Replay Import", markdown)
        self.assertIn("Structured metric imports", markdown)
        self.assertIn("Backfilled snapshots", markdown)
        self.assertIn("recommended_unavailable", markdown)
        self.assertIn("history_snapshot_exists", markdown)
        self.assertIn("Threshold Refresh", markdown)


if __name__ == "__main__":
    unittest.main()
