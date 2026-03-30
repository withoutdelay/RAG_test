import unittest
import tempfile
from pathlib import Path

from app.services.parsing.sample_manifest import (
    TRACK_NEEDS_REVIEW,
    TRACK_OCR_ASSET_ONLY,
    TRACK_PILOT_MAIN,
    apply_profile_to_manifest_entry,
    build_phase_b_plan,
    build_sample_manifest,
    build_sample_manifest_entry,
    build_sample_id,
    render_sample_manifest_markdown,
)


class SampleManifestTests(unittest.TestCase):
    def _touch_sample(self, suffix: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as handle:
            handle.write("sample")
            return handle.name

    def test_build_sample_id_falls_back_to_hash_for_non_ascii_filename(self) -> None:
        sample_id = build_sample_id("/tmp/上电湛江中纸高浓磨机项目成套方案VerA.pdf")
        self.assertTrue(sample_id.startswith("vera-") or sample_id.startswith("sample-"))

    def test_apply_profile_to_manifest_entry_updates_suggested_track(self) -> None:
        entry = build_sample_manifest_entry(file_path=__file__, assigned_track=TRACK_NEEDS_REVIEW)
        profiled_entry = apply_profile_to_manifest_entry(
            entry,
            profile_name="legacy_word_doc",
            ingestion_recommendation="conversion_required",
            high_risk_content_flags=["legacy_doc_requires_conversion"],
            metrics={"markdown_char_count": 120},
        )

        self.assertEqual(profiled_entry.document_type_hint, "legacy_word_doc")
        self.assertEqual(profiled_entry.suggested_track, TRACK_OCR_ASSET_ONLY)
        self.assertEqual(profiled_entry.detected_profile, "legacy_word_doc")
        self.assertEqual(profiled_entry.metrics["markdown_char_count"], 120)

    def test_build_manifest_summarizes_tracks_and_profiles(self) -> None:
        first = build_sample_manifest_entry(file_path=__file__, assigned_track=TRACK_PILOT_MAIN)
        second = build_sample_manifest_entry(file_path=__file__, assigned_track=TRACK_NEEDS_REVIEW)
        second = apply_profile_to_manifest_entry(
            second,
            profile_name="mixed_engineering_pdf",
            ingestion_recommendation="text_primary_with_asset_review",
            high_risk_content_flags=["engineering_figures_present"],
            metrics={"table_count": 3},
        )
        manifest = build_sample_manifest(entries=[first, second], root_paths=["/tmp/samples"])

        self.assertEqual(manifest["total_samples"], 2)
        self.assertEqual(manifest["summary"]["track_counts"]["pilot_main"], 1)
        self.assertEqual(manifest["summary"]["track_counts"]["needs_review"], 1)
        self.assertEqual(manifest["summary"]["profile_counts"]["mixed_engineering_pdf"], 1)
        self.assertTrue(all("phase_b_track" in entry for entry in manifest["entries"]))

    def test_render_sample_manifest_markdown_contains_entry_table(self) -> None:
        entry = build_sample_manifest_entry(file_path=__file__, assigned_track=TRACK_PILOT_MAIN)
        manifest = build_sample_manifest(entries=[entry], root_paths=["/tmp/samples"])

        rendered = render_sample_manifest_markdown(manifest)
        self.assertIn("# Sample Manifest Summary", rendered)
        self.assertIn(
            "| sample_id | file_name | assigned_track | phase_b_track | suggested_track | profile | recommendation | quality_tier |",
            rendered,
        )
        self.assertIn("pilot_main", rendered)
        self.assertIn("Track Glossary", rendered)

    def test_build_phase_b_plan_recommends_holdout_and_conversion_buckets(self) -> None:
        complex_path = self._touch_sample(".pdf")
        clean_path = self._touch_sample(".docx")
        review_path = self._touch_sample(".pdf")
        legacy_path = self._touch_sample(".doc")

        mixed = apply_profile_to_manifest_entry(
            build_sample_manifest_entry(file_path=complex_path, assigned_track=TRACK_NEEDS_REVIEW),
            profile_name="mixed_engineering_pdf",
            ingestion_recommendation="text_primary_with_asset_review",
            high_risk_content_flags=["engineering_figures_present"],
            metrics={"markdown_char_count": 32000, "table_count": 16, "image_count": 80},
        )
        text = apply_profile_to_manifest_entry(
            build_sample_manifest_entry(file_path=clean_path, assigned_track=TRACK_NEEDS_REVIEW),
            profile_name="text_digital",
            ingestion_recommendation="main_vector_ready",
            high_risk_content_flags=[],
            metrics={"markdown_char_count": 52000, "table_count": 6, "image_count": 2},
        )
        review = apply_profile_to_manifest_entry(
            build_sample_manifest_entry(file_path=review_path, assigned_track=TRACK_NEEDS_REVIEW),
            profile_name="mixed_engineering_pdf",
            ingestion_recommendation="text_primary_with_asset_review",
            high_risk_content_flags=["formula_like_content"],
            metrics={"markdown_char_count": 10000, "table_count": 14, "image_count": 20},
        )
        conversion = apply_profile_to_manifest_entry(
            build_sample_manifest_entry(file_path=legacy_path, assigned_track=TRACK_NEEDS_REVIEW),
            profile_name="legacy_word_doc",
            ingestion_recommendation="conversion_required",
            high_risk_content_flags=["legacy_doc_requires_conversion"],
            metrics={"markdown_char_count": 200},
        )

        try:
            plan = build_phase_b_plan([mixed, text, review, conversion])

            self.assertEqual(len(plan["holdout_eval"]), 2)
            self.assertEqual(plan["needs_review"][0]["file_name"], Path(review_path).name)
            self.assertEqual(plan["ocr_asset_only"][0]["file_name"], Path(legacy_path).name)
            manifest = build_sample_manifest(entries=[mixed, text, review, conversion])
            entry_tracks = {entry["file_name"]: entry["phase_b_track"] for entry in manifest["entries"]}
            self.assertEqual(entry_tracks[Path(review_path).name], "needs_review")
            self.assertEqual(entry_tracks[Path(legacy_path).name], "ocr_asset_only")
        finally:
            for path in (complex_path, clean_path, review_path, legacy_path):
                Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
