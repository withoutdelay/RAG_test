from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.refresh_project_replay_history import (
    _run_evaluate_snapshot,
    render_project_replay_refresh_markdown,
    select_project_replay_candidates,
)


class ProjectReplayRefreshScriptTests(unittest.TestCase):
    def test_select_project_replay_candidates_filters_by_draft_version(self) -> None:
        candidates, skipped = select_project_replay_candidates(
            [
                {
                    "id": "project-created",
                    "name": "刚创建项目",
                    "status": "CREATED",
                    "current_draft_version": 0,
                },
                {
                    "id": "project-ready",
                    "name": "可回放项目",
                    "status": "EXPORTABLE",
                    "current_draft_version": 3,
                },
            ]
        )

        self.assertEqual([item["project_id"] for item in candidates], ["project-ready"])
        self.assertEqual(skipped[0]["project_id"], "project-created")
        self.assertEqual(skipped[0]["reason"], "current_draft_version=0")

    def test_select_project_replay_candidates_reports_missing_project_filter(self) -> None:
        candidates, skipped = select_project_replay_candidates(
            [
                {
                    "id": "project-ready",
                    "name": "可回放项目",
                    "status": "EXPORTABLE",
                    "current_draft_version": 2,
                }
            ],
            project_id_filter="missing-project",
        )

        self.assertEqual(candidates, [])
        self.assertEqual(skipped[0]["project_id"], "missing-project")
        self.assertEqual(skipped[0]["reason"], "project_not_found")

    def test_render_project_replay_refresh_markdown_includes_skips_and_refreshes(self) -> None:
        markdown = render_project_replay_refresh_markdown(
            {
                "base_url": "http://127.0.0.1:8000/api/v1",
                "project_id_filter": "",
                "generated_at": "2026-04-22T10:00:00Z",
                "summary": {
                    "discovered_projects": 2,
                    "eligible_candidates": 1,
                    "refreshed_projects": 1,
                    "failed_projects": 0,
                    "skipped_projects": 1,
                },
                "candidates": [
                    {
                        "project_id": "project-ready",
                        "status": "EXPORTABLE",
                        "draft_version": 3,
                    }
                ],
                "refreshed": [
                    {
                        "project_id": "project-ready",
                        "exit_code": 0,
                        "markdown_path": "/tmp/project-ready.md",
                        "json_path": "/tmp/project-ready.json",
                    }
                ],
                "failures": [],
                "skipped": [
                    {
                        "project_id": "project-created",
                        "reason": "current_draft_version=0",
                        "status": "CREATED",
                        "draft_version": 0,
                    }
                ],
                "threshold_refresh": {
                    "exit_code": 0,
                    "markdown_path": "/tmp/thresholds.md",
                    "json_path": "/tmp/thresholds.json",
                    "notes": "Recommendation summary: healthy=1",
                },
            }
        )

        self.assertIn("Project Replay Refresh", markdown)
        self.assertIn("project-ready", markdown)
        self.assertIn("current_draft_version=0", markdown)
        self.assertIn("Threshold Refresh", markdown)

    def test_run_evaluate_snapshot_passes_recommended_threshold_flags(self) -> None:
        with patch("scripts.refresh_project_replay_history.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""

            result = _run_evaluate_snapshot(
                project_id="project-ready",
                draft_version=3,
                base_url="http://127.0.0.1:8000/api/v1",
                history_dir=Path("/tmp/history"),
                output_dir=Path("/tmp/output"),
                use_recommended_thresholds=True,
                recommended_thresholds_json="/tmp/thresholds.json",
            )

        command = mock_run.call_args.args[0]
        self.assertIn("--use-recommended-thresholds", command)
        self.assertIn("--recommended-thresholds-json", command)
        self.assertIn("/tmp/thresholds.json", command)
        self.assertFalse(result["used_recommended_thresholds"])
        self.assertFalse(result["recommended_thresholds_available"])
        self.assertEqual(result["recommended_thresholds_json"], "/tmp/thresholds.json")


if __name__ == "__main__":
    unittest.main()
