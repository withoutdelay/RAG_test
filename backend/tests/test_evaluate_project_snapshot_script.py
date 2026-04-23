from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.evaluate_project_snapshot import (
    load_recommended_gate_thresholds,
    resolve_gate_thresholds,
)


class EvaluateProjectSnapshotScriptTests(unittest.TestCase):
    def test_load_recommended_gate_thresholds_ignores_null_values(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "thresholds.json"
            path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            "min_trace_sections": 4,
                            "min_avg_retrieval_final": 0.77,
                            "min_avg_retrieval_semantic": None,
                            "min_avg_retrieval_rerank": None,
                        }
                    }
                ),
                encoding="utf-8",
            )

            resolved = load_recommended_gate_thresholds(path)

            self.assertEqual(resolved["min_retrieval_trace_sections"], 4)
            self.assertEqual(resolved["min_average_retrieval_final_score"], 0.77)
            self.assertNotIn("min_average_retrieval_semantic_score", resolved)
            self.assertNotIn("min_average_retrieval_rerank_score", resolved)
            self.assertEqual(resolved["source_path"], str(path.resolve()))
            self.assertTrue(resolved["available"])
            self.assertEqual(
                resolved["available_threshold_keys"],
                ["min_retrieval_trace_sections", "min_average_retrieval_final_score"],
            )

    def test_resolve_gate_thresholds_uses_recommendations_when_enabled(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "thresholds.json"
            path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            "min_trace_sections": 4,
                            "min_avg_retrieval_final": 0.77,
                            "min_avg_retrieval_semantic": 0.68,
                            "min_avg_retrieval_rerank": 0.62,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                min_trace_sections=0,
                min_avg_retrieval_final=0.0,
                min_avg_retrieval_semantic=0.0,
                min_avg_retrieval_rerank=0.0,
                use_recommended_thresholds=True,
                recommended_thresholds_json=str(path),
            )

            resolved = resolve_gate_thresholds(args)

            self.assertEqual(resolved["min_retrieval_trace_sections"], 4)
            self.assertEqual(resolved["min_average_retrieval_final_score"], 0.77)
            self.assertEqual(resolved["min_average_retrieval_semantic_score"], 0.68)
            self.assertEqual(resolved["min_average_retrieval_rerank_score"], 0.62)
            self.assertTrue(resolved["used_recommended_thresholds"])
            self.assertEqual(resolved["recommended_thresholds_path"], str(path.resolve()))
            self.assertTrue(resolved["recommendation_requested"])
            self.assertTrue(resolved["recommended_thresholds_available"])
            self.assertEqual(resolved["threshold_source"], "recommended")

    def test_resolve_gate_thresholds_prefers_explicit_flags(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "thresholds.json"
            path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            "min_trace_sections": 4,
                            "min_avg_retrieval_final": 0.77,
                            "min_avg_retrieval_semantic": 0.68,
                            "min_avg_retrieval_rerank": 0.62,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                min_trace_sections=6,
                min_avg_retrieval_final=0.81,
                min_avg_retrieval_semantic=0.0,
                min_avg_retrieval_rerank=0.0,
                use_recommended_thresholds=True,
                recommended_thresholds_json=str(path),
            )

            resolved = resolve_gate_thresholds(args)

            self.assertEqual(resolved["min_retrieval_trace_sections"], 6)
            self.assertEqual(resolved["min_average_retrieval_final_score"], 0.81)
            self.assertEqual(resolved["min_average_retrieval_semantic_score"], 0.68)
            self.assertEqual(resolved["min_average_retrieval_rerank_score"], 0.62)
            self.assertTrue(resolved["used_recommended_thresholds"])
            self.assertEqual(resolved["threshold_source"], "mixed")

    def test_resolve_gate_thresholds_returns_explicit_only_when_recommendation_missing(self) -> None:
        args = argparse.Namespace(
            min_trace_sections=3,
            min_avg_retrieval_final=0.75,
            min_avg_retrieval_semantic=0.0,
            min_avg_retrieval_rerank=0.0,
            use_recommended_thresholds=True,
            recommended_thresholds_json="/tmp/missing-thresholds.json",
        )

        resolved = resolve_gate_thresholds(args)

        self.assertEqual(resolved["min_retrieval_trace_sections"], 3)
        self.assertEqual(resolved["min_average_retrieval_final_score"], 0.75)
        self.assertIsNone(resolved["min_average_retrieval_semantic_score"])
        self.assertFalse(resolved["used_recommended_thresholds"])
        self.assertEqual(resolved["recommended_thresholds_path"], "")
        self.assertTrue(resolved["recommendation_requested"])
        self.assertFalse(resolved["recommended_thresholds_available"])
        self.assertEqual(resolved["threshold_source"], "explicit")

    def test_resolve_gate_thresholds_marks_unavailable_when_request_has_no_values(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "thresholds.json"
            path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            "min_trace_sections": None,
                            "min_avg_retrieval_final": None,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                min_trace_sections=0,
                min_avg_retrieval_final=0.0,
                min_avg_retrieval_semantic=0.0,
                min_avg_retrieval_rerank=0.0,
                use_recommended_thresholds=True,
                recommended_thresholds_json=str(path),
            )

            resolved = resolve_gate_thresholds(args)

            self.assertFalse(resolved["used_recommended_thresholds"])
            self.assertFalse(resolved["recommended_thresholds_available"])
            self.assertEqual(resolved["recommended_thresholds_path"], str(path.resolve()))
            self.assertEqual(resolved["threshold_source"], "recommended_unavailable")


if __name__ == "__main__":
    unittest.main()
