from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.domain.synonyms import expand_domain_term, text_contains_domain_term
from app.services.domain.taxonomy_registry import (
    TaxonomySchemaError,
    clear_domain_taxonomy_caches,
    get_asset_role_registry,
    get_forbidden_phrase_registry,
    get_retrieval_policy_registry,
    get_section_type_registry,
    get_synonym_registry,
    get_taxonomy_registry,
    load_domain_taxonomy_registries,
)


class DomainTaxonomyRegistryTests(unittest.TestCase):
    def test_domain_taxonomy_files_load_and_infer_main_circuit(self) -> None:
        registries = load_domain_taxonomy_registries()
        taxonomy_registry = registries["taxonomy"]
        retrieval_policies = registries["retrieval_policies"]

        taxonomy = taxonomy_registry.infer(
            "主回路采用一拖一输入输出隔离方案，含旁路切换和一次接线。",
            heading_path="2.2 高压变频器主回路方案说明",
        )

        self.assertEqual(taxonomy["section_type"], "main_circuit_scheme")
        self.assertEqual(taxonomy["equipment_type"], "vfd")
        policy = retrieval_policies.resolve(taxonomy)
        self.assertIs(policy["extractive"], True)
        self.assertIn("单线图", policy["asset_query_hints"])

    def test_synonym_registry_uses_external_alias_groups(self) -> None:
        self.assertIn("高压变频器", expand_domain_term("HV-VFD"))
        self.assertTrue(text_contains_domain_term("可编程逻辑控制器与分布式控制系统接口", "plc"))

    def test_asset_role_and_forbidden_phrase_registries_load_external_data(self) -> None:
        asset_roles = get_asset_role_registry()
        forbidden = get_forbidden_phrase_registry()

        self.assertIsNotNone(asset_roles.pattern("complete_diagram").search("一次接线单线图"))
        self.assertEqual(asset_roles.threshold("min_reusable_figure_dimension", 0), 80)
        self.assertTrue(any(item["phrase"] == "绝对满足" for item in forbidden.items))

    def test_missing_taxonomy_files_use_minimal_conservative_fallback(self) -> None:
        clear_domain_taxonomy_caches()
        with TemporaryDirectory() as tmp_dir:
            missing_dir = Path(tmp_dir) / "missing"

            registry = get_taxonomy_registry(str(missing_dir))

            self.assertTrue(registry.synonyms.contains("VFD 系统", "变频器"))
            taxonomy = registry.infer("供货清单包含设备、数量和型号。", heading_path="供货清单", chunk_type="TABLE")
            self.assertEqual(taxonomy["section_type"], "bom_or_supply_list")
            self.assertEqual(taxonomy["content_form"], "bom_table")
        clear_domain_taxonomy_caches()

    def test_json_decode_failure_falls_back_to_minimal_defaults(self) -> None:
        clear_domain_taxonomy_caches()
        with TemporaryDirectory() as tmp_dir:
            taxonomy_dir = Path(tmp_dir) / "taxonomy"
            taxonomy_dir.mkdir()
            (taxonomy_dir / "synonyms.json").write_text("{not json", encoding="utf-8")

            registry = get_synonym_registry(str(taxonomy_dir))

            self.assertTrue(registry.contains("VFD 系统", "变频器"))
        clear_domain_taxonomy_caches()

    def test_schema_failure_raises_to_block_startup(self) -> None:
        clear_domain_taxonomy_caches()
        with TemporaryDirectory() as tmp_dir:
            taxonomy_dir = Path(tmp_dir) / "taxonomy"
            taxonomy_dir.mkdir()
            (taxonomy_dir / "section_types.json").write_text(
                '{"schema_version": 1, "section_types": "bad"}',
                encoding="utf-8",
            )

            with self.assertRaises(TaxonomySchemaError):
                get_section_type_registry(str(taxonomy_dir))
        clear_domain_taxonomy_caches()

    def test_retrieval_policy_registry_resolves_section_policy(self) -> None:
        policies = get_retrieval_policy_registry()

        self.assertIn("主回路", policies.tokens("main_circuit.focus"))
        self.assertIn("main_circuit_scheme", policies.section_types("extractive_section_types"))
        self.assertEqual(policies.rules("supply_scope_required_items")[0][0], "LCI/SFC 变频软起动装置")


if __name__ == "__main__":
    unittest.main()
