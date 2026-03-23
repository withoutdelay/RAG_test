from __future__ import annotations

import unittest

from app import models  # noqa: F401
from app.db import Base


class V2SchemaMetadataTests(unittest.TestCase):
    def test_v2_tables_are_registered_in_metadata(self) -> None:
        expected_tables = {
            "raw_documents",
            "parsed_blocks",
            "figure_assets",
            "knowledge_chunks",
            "requirement_cards",
            "evidence_bundles",
            "proposal_outlines",
            "section_drafts",
            "review_tasks",
            "jobs",
            "audit_logs",
        }
        self.assertTrue(expected_tables.issubset(set(Base.metadata.tables.keys())))

    def test_projects_table_has_v2_tracking_columns(self) -> None:
        columns = Base.metadata.tables["projects"].c
        for column_name in [
            "owner_user_id",
            "product_line",
            "status",
            "current_requirement_card_id",
            "current_outline_id",
            "current_draft_version",
        ]:
            self.assertIn(column_name, columns)


if __name__ == "__main__":
    unittest.main()
