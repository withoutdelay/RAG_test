from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from app.services.parsing.document_sources import (
    build_direct_source_entry,
    is_library_ready_entry,
    iter_document_paths,
)


class DocumentSourceTests(unittest.TestCase):
    def test_iter_document_paths_recurses_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            first = root / "alpha.pdf"
            second = nested / "beta.DOCX"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")

            resolved = iter_document_paths([str(root), str(first)])

        self.assertEqual(resolved, [first.resolve(), second.resolve()])

    def test_iter_document_paths_rejects_unsupported_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("a", encoding="utf-8")
            with self.assertRaises(SystemExit):
                iter_document_paths([str(path)])

    def test_build_direct_source_entry_preserves_profile_and_applies_track_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "方案A.pdf"
            path.write_text("demo", encoding="utf-8")

            entry = build_direct_source_entry(
                file_path=path,
                parsed_metadata={
                    "document_profile": {
                        "name": "mixed_engineering_pdf",
                        "metrics": {"table_count": 4},
                    },
                    "ingestion_recommendation": "text_primary_with_asset_review",
                    "high_risk_content_flags": ["engineering_figures_present"],
                },
                library_track="holdout_eval",
                source="real_proposals",
            )

        self.assertEqual(entry["file_name"], "方案A.pdf")
        self.assertEqual(entry["detected_profile"], "mixed_engineering_pdf")
        self.assertEqual(entry["ingestion_recommendation"], "text_primary_with_asset_review")
        self.assertEqual(entry["metrics"], {"table_count": 4})
        self.assertEqual(entry["source"], "real_proposals")
        self.assertEqual(entry["phase_b_track"], "holdout_eval")
        self.assertEqual(entry["library_track"], "holdout_eval")
        self.assertEqual(entry["track"], "holdout_eval")
        self.assertEqual(entry["suggested_track"], "pilot_main")

    def test_is_library_ready_entry_only_accepts_recommended_profiles(self) -> None:
        self.assertTrue(is_library_ready_entry({"ingestion_recommendation": "main_vector_ready"}))
        self.assertTrue(is_library_ready_entry({"ingestion_recommendation": "text_primary_with_asset_review"}))
        self.assertFalse(is_library_ready_entry({"ingestion_recommendation": "conversion_required"}))
        self.assertFalse(is_library_ready_entry({"ingestion_recommendation": "asset_only_review"}))


if __name__ == "__main__":
    unittest.main()
