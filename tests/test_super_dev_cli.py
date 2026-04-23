from __future__ import annotations

import unittest
from unittest.mock import patch

from super_dev import cli


class SuperDevCliTests(unittest.TestCase):
    def test_main_dispatches_release_gate_to_local_host_command(self) -> None:
        with patch("super_dev.cli.run_release_gate", return_value=0) as mocked:
            result = cli.main(["host", "release-gate", "--project-id", "project-1", "--skip-quality-smoke"])

        self.assertEqual(result, 0)
        mocked.assert_called_once_with(
            project_id="project-1",
            base_url="http://127.0.0.1:8000/api/v1",
            draft_version=0,
            output_path="",
            json_output_path="",
            history_dir="",
            skip_project_replay=False,
            skip_quality_smoke=True,
        )

    def test_main_dispatches_quality_smoke_to_local_host_command(self) -> None:
        with patch("super_dev.cli.run_quality_smoke", return_value=["test_alpha"]) as mocked:
            result = cli.main(["host", "quality-smoke"])

        self.assertEqual(result, 0)
        mocked.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
