from __future__ import annotations

import unittest

from app.services.catalog.defaults import (
    DEFAULT_PRODUCT_CATALOG,
    DEFAULT_PRODUCT_COMPATIBILITY,
    DEFAULT_PRODUCT_FAMILIES,
    DEFAULT_PRODUCT_INTERFACES,
    DEFAULT_PRODUCT_MODELS,
)


class ProductCatalogDefaultsTests(unittest.TestCase):
    def test_default_series_reference_known_family_code(self) -> None:
        family_codes = {item["code"] for item in DEFAULT_PRODUCT_FAMILIES}

        self.assertIn("lci_sync_drive", family_codes)
        self.assertIn("hv_vfd_multilevel", family_codes)
        self.assertIn("support_equipment", family_codes)

        for item in DEFAULT_PRODUCT_CATALOG:
            self.assertIn(item["family_code"], family_codes)

    def test_phase0a_target_families_exist_in_default_family_seed(self) -> None:
        family_index = {item["code"]: item for item in DEFAULT_PRODUCT_FAMILIES}

        self.assertEqual(family_index["lci_sync_drive"]["status"], "active")
        self.assertEqual(family_index["hv_vfd_multilevel"]["status"], "active")
        self.assertEqual(family_index["hv_solid_state_starter"]["status"], "planned")
        self.assertIn("LCI", family_index["lci_sync_drive"]["aliases"])
        self.assertIn("高压固态", family_index["hv_solid_state_starter"]["aliases"])

    def test_default_compatibility_references_known_families_and_series(self) -> None:
        family_codes = {item["code"] for item in DEFAULT_PRODUCT_FAMILIES}
        series_codes = {item["code"] for item in DEFAULT_PRODUCT_CATALOG}

        self.assertGreaterEqual(len(DEFAULT_PRODUCT_COMPATIBILITY), 3)

        for item in DEFAULT_PRODUCT_COMPATIBILITY:
            self.assertIn(item["source_family_code"], family_codes)
            self.assertIn(item["target_family_code"], family_codes)
            for code in item["preferred_series_codes"]:
                self.assertIn(code, series_codes)
            for code in item["optional_series_codes"]:
                self.assertIn(code, series_codes)

    def test_default_models_reference_known_series_and_have_material_keys(self) -> None:
        series_codes = {item["code"] for item in DEFAULT_PRODUCT_CATALOG}

        self.assertGreaterEqual(len(DEFAULT_PRODUCT_MODELS), 5)

        for item in DEFAULT_PRODUCT_MODELS:
            self.assertIn(item["series_code"], series_codes)
            self.assertTrue(item["model_number"])
            self.assertIn("source_material_key", item)

    def test_default_interfaces_reference_known_series_and_sorted_slots(self) -> None:
        series_codes = {item["code"] for item in DEFAULT_PRODUCT_CATALOG}
        seen_slots: set[tuple[str, str, int]] = set()

        self.assertGreaterEqual(len(DEFAULT_PRODUCT_INTERFACES), 8)

        for item in DEFAULT_PRODUCT_INTERFACES:
            self.assertIn(item["series_code"], series_codes)
            slot = (item["series_code"], item["interface_type"], int(item["sort_order"]))
            self.assertNotIn(slot, seen_slots)
            seen_slots.add(slot)
            self.assertIn("signal_spec", item)


if __name__ == "__main__":
    unittest.main()
