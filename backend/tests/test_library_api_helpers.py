from __future__ import annotations

import unittest

from app.api.library import _material_base_metadata, _route_for_entry


class LibraryApiHelperTest(unittest.TestCase):
    def test_route_for_entry_respects_uploaded_material_route(self) -> None:
        route = _route_for_entry(
            {
                "sample_id": "uploaded-1",
                "route": "main_indexed",
                "phase_b_track": "uploaded",
                "ingestion_recommendation": "uploaded_historical_material",
            },
            {"materials": {}},
        )

        self.assertEqual(route, "main_indexed")

    def test_material_base_metadata_preserves_uploaded_source_kind(self) -> None:
        metadata = _material_base_metadata(
            {
                "sample_id": "uploaded-1",
                "source_kind": "uploaded_document",
                "file_path": "data/uploads/example.docx",
            },
            "main_indexed",
        )

        self.assertEqual(metadata["source_kind"], "uploaded_document")


if __name__ == "__main__":
    unittest.main()
