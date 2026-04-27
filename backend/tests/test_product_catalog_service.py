from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.models.product_compatibility import ProductCompatibility
from app.models.product_constraint import ProductConstraint
from app.models.product_interface import ProductInterface
from app.models.product_material import ProductMaterial
from app.models.product_model import ProductModel
from app.models.product_series import ProductSeries
from app.models.product_standard_config import ProductStandardConfig
from app.services.catalog import DEFAULT_PRODUCT_INTERFACES, DEFAULT_PRODUCT_MODELS, ProductCatalogService
from app.services.solution import SolutionService
from app.services.v2_errors import ArtifactValidationError


def _make_series(
    *,
    code: str,
    family: str,
    series_name: str,
    applicable_motors: list[str],
    applicable_loads: list[str],
    communication_protocols: list[str],
    voltage_levels: list[str],
    min_power_kw: float,
    max_power_kw: float,
    standard_configs: list[ProductStandardConfig],
    constraints: list[ProductConstraint] | None = None,
    topology: str | None = None,
) -> ProductSeries:
    series = ProductSeries(
        catalog_version="seed-20260419-v1",
        is_published=True,
        role_type="primary" if code in {"lci_sync_drive", "hv_vfd_multilevel"} else "support",
        family=family,
        series_name=series_name,
        code=code,
        vendor="标准产品",
        description=None,
        voltage_levels=voltage_levels,
        min_power_kw=min_power_kw,
        max_power_kw=max_power_kw,
        topology=topology,
        applicable_motors=applicable_motors,
        applicable_loads=applicable_loads,
        communication_protocols=communication_protocols,
        io_allocation={"DI": 4, "DO": 2},
        protection_features=[],
        preferred_scenarios=[],
        default_chapters=["总体方案", "供货范围与配置清单"],
    )
    series.standard_configs = standard_configs
    series.constraints = constraints or []
    return series


class ProductCatalogServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_service = ProductCatalogService()
        self.solution_service = SolutionService(product_catalog=self.catalog_service)

        self.bypass_config = ProductStandardConfig(
            config_name="旁路配置",
            components=[
                {"role": "旁路柜", "series_code": "bypass_cabinet", "quantity": 1, "config": "旁路配置"},
            ],
            applicable_scenarios=["工频旁路运行"],
            description="带旁路切换的完整主驱动配置。",
        )
        self.standard_config = ProductStandardConfig(
            config_name="标准配置",
            components=[],
            applicable_scenarios=["一般调速"],
            description="标准配置。",
        )
        self.lci_series = _make_series(
            code="lci_sync_drive",
            family="同步电机软起动",
            series_name="LCI 同步电机变频软起动系统",
            applicable_motors=["同步电机"],
            applicable_loads=["鼓风机", "压缩机"],
            communication_protocols=["Profibus-DP", "Modbus TCP"],
            voltage_levels=["6kV", "10kV"],
            min_power_kw=2000,
            max_power_kw=20000,
            standard_configs=[self.standard_config, self.bypass_config],
            constraints=[
                ProductConstraint(
                    constraint_type="electrical",
                    condition="同步电机",
                    action="必须配套励磁控制柜。",
                    severity="blocking",
                )
            ],
            topology="负载换流型",
        )
        self.vfd_series = _make_series(
            code="hv_vfd_multilevel",
            family="高压变频",
            series_name="高压变频器多电平驱动系统",
            applicable_motors=["异步电机", "同步电机"],
            applicable_loads=["风机", "泵", "输送机"],
            communication_protocols=["Modbus TCP", "Profinet"],
            voltage_levels=["3.3kV", "6kV", "10kV"],
            min_power_kw=200,
            max_power_kw=8000,
            standard_configs=[self.standard_config],
            topology="单元串联多电平",
        )
        self.bypass_series = _make_series(
            code="bypass_cabinet",
            family="配套设备",
            series_name="旁路切换柜",
            applicable_motors=["同步电机", "异步电机"],
            applicable_loads=["鼓风机", "风机", "泵"],
            communication_protocols=["硬接点"],
            voltage_levels=["6kV", "10kV"],
            min_power_kw=500,
            max_power_kw=20000,
            standard_configs=[
                ProductStandardConfig(
                    config_name="旁路配置",
                    components=[],
                    applicable_scenarios=["工频旁路运行"],
                    description="旁路切换标准配置。",
                )
            ],
            topology="工频旁路",
        )
        self.rectifier_series = _make_series(
            code="rectifier_transformer",
            family="配套设备",
            series_name="整流变压器",
            applicable_motors=["同步电机"],
            applicable_loads=["鼓风机", "压缩机"],
            communication_protocols=[],
            voltage_levels=["6kV", "10kV"],
            min_power_kw=2000,
            max_power_kw=20000,
            standard_configs=[self.standard_config],
            topology="隔离降压",
        )
        self.excitation_series = _make_series(
            code="excitation_cabinet",
            family="配套设备",
            series_name="励磁控制柜",
            applicable_motors=["同步电机"],
            applicable_loads=["鼓风机", "压缩机"],
            communication_protocols=["硬接点"],
            voltage_levels=["6kV", "10kV"],
            min_power_kw=2000,
            max_power_kw=20000,
            standard_configs=[self.standard_config],
            topology="同步励磁",
        )
        self.lci_compatibility = ProductCompatibility(
            catalog_version="seed-20260419-v1",
            is_published=True,
            source_family_code="lci_sync_drive",
            target_family_code="support_equipment",
            relation_type="requires",
            condition="同步电机软起及主回路成套场景",
            description="LCI 主驱动通常需要整流变压器和励磁控制柜；若要求工频旁路，则追加旁路柜。",
            preferred_series_codes=["rectifier_transformer", "excitation_cabinet"],
            optional_series_codes=["bypass_cabinet"],
            sort_order=10,
        )
        self.vfd_compatibility = ProductCompatibility(
            catalog_version="seed-20260419-v1",
            is_published=True,
            source_family_code="hv_vfd_multilevel",
            target_family_code="support_equipment",
            relation_type="recommended",
            condition="要求工频旁路、检修不停机或改造保留原系统切换",
            description="高压变频主驱动在特定改造场景下推荐配置旁路切换柜。",
            preferred_series_codes=["bypass_cabinet"],
            optional_series_codes=[],
            sort_order=20,
        )
        self.lci_model = ProductModel(
            catalog_version="seed-20260419-v1",
            is_published=True,
            series_id=uuid4(),
            series_code="lci_sync_drive",
            model_number="GBT.LCI.SO-A0606-211N465",
            rated_voltage="10kV",
            rated_power_kw=4208,
            rated_current="277.5A",
            specs={"input_transformer_kva": 5458, "output_transformer_kva": 4807},
            source_material_key="vera-46268861",
        )
        self.lci_interface = ProductInterface(
            catalog_version="seed-20260419-v1",
            is_published=True,
            series_id=uuid4(),
            series_code="lci_sync_drive",
            interface_type="communication",
            protocol="Profibus-DP",
            signal_spec={"adapter": "fieldbus_adapter", "digital_input_voltage": "24VDC"},
            notes="现场总线适配器 Profibus-DP（暂定）",
            source_material_key="vera-46268861",
            sort_order=10,
        )
        self.lci_material = ProductMaterial(
            material_key="lci-bf49f75e",
            family_code="lci_sync_drive",
            material_type="proposal_sample",
            document_name="宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
            availability_status="available",
            assigned_track="pilot_main",
            notes="覆盖 LCI 成套方案、供货边界和接口配套。",
            details={
                "quality_tier": "high",
                "solution_family": "LCI / 同步电机变频软起动",
                "key_equipment": ["LCI变频启动装置", "整流变压器", "励磁控制柜"],
            },
        )

    def test_build_requirement_signals_extracts_catalog_inputs(self) -> None:
        project = SimpleNamespace(
            name="鼓风机同步电机改造",
            industry="冶金",
            product_line="lci",
            description="10kV 4500kW 同步电机，要求 Profibus-DP 并保留旁路切换。",
        )
        requirement_card = SimpleNamespace(
            content={
                "motor_type": "同步电机",
                "communication_protocol": "Profibus-DP",
                "notes": "1 台鼓风机驱动系统",
            }
        )

        signals = self.catalog_service.build_requirement_signals(project=project, requirement_card=requirement_card)

        self.assertEqual(signals.motor_type, "同步电机")
        self.assertEqual(signals.requested_protocol, "Profibus-DP")
        self.assertEqual(signals.voltage_value, 10.0)
        self.assertEqual(signals.power_value, 4500.0)
        self.assertEqual(signals.quantity, 1)
        self.assertTrue(signals.bypass_required)
        self.assertEqual(signals.product_line, "lci")
        self.assertIn("鼓风机", signals.application_tokens)

    def test_score_prefers_lci_for_sync_blower_bypass_case(self) -> None:
        project = SimpleNamespace(
            name="鼓风机同步电机改造",
            industry="冶金",
            product_line="lci",
            description="10kV 4500kW 同步电机，要求 Profibus-DP，并且需要旁路切换。",
        )
        requirement_card = SimpleNamespace(content={"motor_type": "同步电机"})
        signals = self.catalog_service.build_requirement_signals(project=project, requirement_card=requirement_card)

        lci_score, _ = self.catalog_service._score_primary_series(series=self.lci_series, signals=signals)
        vfd_score, _ = self.catalog_service._score_primary_series(series=self.vfd_series, signals=signals)

        self.assertGreater(lci_score, vfd_score)

    def test_select_standard_config_uses_bypass_variant(self) -> None:
        config = self.catalog_service._select_standard_config(series=self.lci_series, bypass_required=True)
        self.assertIsNotNone(config)
        self.assertEqual(config.config_name, "旁路配置")

    def test_solution_payload_uses_catalog_entries_and_version(self) -> None:
        project = SimpleNamespace(
            name="鼓风机同步电机改造",
            industry="冶金",
            product_line="lci",
            description="10kV 4500kW 同步电机，要求 Profibus-DP，并且需要旁路切换。",
        )
        requirement_card = SimpleNamespace(content={"motor_type": "同步电机"})
        signals = self.catalog_service.build_requirement_signals(project=project, requirement_card=requirement_card)
        candidates = [
            SimpleNamespace(
                series=self.lci_series,
                selected_config=self.bypass_config,
                score=1.24,
                reasons=["产品线 lci 与目录系列匹配", "支持同步电机场景"],
            )
        ]
        payload = self.solution_service._build_solution_payload(
            signals=signals,
            candidates=candidates,
            series_map={
                "lci_sync_drive": self.lci_series,
                "bypass_cabinet": self.bypass_series,
                "rectifier_transformer": self.rectifier_series,
                "excitation_cabinet": self.excitation_series,
            },
            source_catalog_version="seed-20260419-v1",
            compatibility_rules=[self.lci_compatibility],
            catalog_models_by_series={"lci_sync_drive": [self.lci_model]},
            catalog_interfaces_by_series={"lci_sync_drive": [self.lci_interface]},
            catalog_material_rows=[self.lci_material],
        )

        selected_codes = [item["series_code"] for item in payload["selected_products"]]
        self.assertEqual(payload["source_catalog_version"], "seed-20260419-v1")
        self.assertEqual(payload["selected_products"][0]["series_code"], "lci_sync_drive")
        self.assertEqual(payload["selected_products"][0]["config"], "旁路配置")
        self.assertEqual(payload["selected_products"][0]["model_number"], "GBT.LCI.SO-A0606-211N465")
        self.assertIn("bypass_cabinet", selected_codes)
        self.assertIn("rectifier_transformer", selected_codes)
        self.assertIn("excitation_cabinet", selected_codes)
        self.assertEqual(payload["interface_plan"]["dcs_protocol"], "Profibus-DP")
        self.assertEqual(payload["interface_plan"]["catalog_interface_entries"][0]["protocol"], "Profibus-DP")
        self.assertEqual(payload["selection_reason"]["source_mode"], "catalog_plus_requirement_card")
        self.assertTrue(any("产品族兼容规则" in item for item in payload["selection_reason"]["why_selected"]))
        self.assertEqual(payload["selection_reason"]["catalog_model_matches"][0]["model_number"], "GBT.LCI.SO-A0606-211N465")
        self.assertIn("vera-46268861", payload["selection_reason"]["catalog_source_material_keys"])
        self.assertIn("lci-bf49f75e", payload["selection_reason"]["catalog_source_material_keys"])
        self.assertEqual(
            payload["selection_reason"]["catalog_material_entries"][0]["document_name"],
            "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
        )
        self.assertTrue(
            any("产品资料库材料" in item for item in payload["selection_reason"]["why_selected"])
        )
        self.assertEqual(payload["selection_reason"]["compatibility_actions"][0]["relation_type"], "requires")
        self.assertIn("rectifier_transformer", payload["selection_reason"]["compatibility_actions"][0]["added_series_codes"])

    def test_default_seed_models_and_interfaces_do_not_expose_sample_source_material_keys(self) -> None:
        self.assertTrue(all(item.get("source_material_key") in (None, "") for item in DEFAULT_PRODUCT_MODELS))
        self.assertTrue(all(item.get("source_material_key") in (None, "") for item in DEFAULT_PRODUCT_INTERFACES))

    def test_catalog_material_entries_prefer_customer_material_over_private_sample_in_same_bucket(self) -> None:
        customer_manual = ProductMaterial(
            material_key="customer-manual-001",
            family_code="lci_sync_drive",
            material_type="product_manual",
            document_name="客户提供-LCI产品手册.pdf",
            availability_status="available",
            source_kind="customer_provided",
            tags=[],
            details={"quality_tier": "medium"},
        )
        sample_manual = ProductMaterial(
            material_key="sample-manual-001",
            family_code="lci_sync_drive",
            material_type="product_manual",
            document_name="历史样板-LCI产品手册.pdf",
            availability_status="available",
            source_kind="private_sample",
            tags=[],
            details={"quality_tier": "high"},
        )
        entries = self.solution_service._build_catalog_material_entries(
            primary_series=self.lci_series,
            component_series_rows=[],
            material_rows=[sample_manual, customer_manual],
            limit=4,
        )

        self.assertEqual([item["material_key"] for item in entries], ["customer-manual-001"])

    def test_solution_payload_applies_recommended_bypass_compatibility_when_bypass_required(self) -> None:
        project = SimpleNamespace(
            name="风机改造",
            industry="冶金",
            product_line="hv_vfd",
            description="10kV 3200kW 风机变频改造，要求工频旁路切换。",
        )
        requirement_card = SimpleNamespace(content={"motor_type": "异步电机"})
        signals = self.catalog_service.build_requirement_signals(project=project, requirement_card=requirement_card)
        candidates = [
            SimpleNamespace(
                series=self.vfd_series,
                selected_config=self.standard_config,
                score=1.11,
                reasons=["产品线 hv_vfd 与目录系列匹配", "适用负载覆盖风机"],
            )
        ]

        payload = self.solution_service._build_solution_payload(
            signals=signals,
            candidates=candidates,
            series_map={
                "hv_vfd_multilevel": self.vfd_series,
                "bypass_cabinet": self.bypass_series,
            },
            source_catalog_version="seed-20260419-v1",
            compatibility_rules=[self.vfd_compatibility],
        )

        self.assertTrue(any(item["series_code"] == "bypass_cabinet" for item in payload["selected_products"]))
        self.assertTrue(any("建议补齐 bypass_cabinet" in item for item in payload["selection_reason"]["why_selected"]))
        self.assertEqual(payload["selection_reason"]["compatibility_actions"][0]["relation_type"], "recommended")

    def test_solution_payload_does_not_apply_recommended_bypass_without_trigger(self) -> None:
        project = SimpleNamespace(
            name="风机改造",
            industry="冶金",
            product_line="hv_vfd",
            description="10kV 3200kW 风机变频改造。",
        )
        requirement_card = SimpleNamespace(content={"motor_type": "异步电机"})
        signals = self.catalog_service.build_requirement_signals(project=project, requirement_card=requirement_card)
        candidates = [
            SimpleNamespace(
                series=self.vfd_series,
                selected_config=self.standard_config,
                score=1.11,
                reasons=["产品线 hv_vfd 与目录系列匹配", "适用负载覆盖风机"],
            )
        ]

        payload = self.solution_service._build_solution_payload(
            signals=signals,
            candidates=candidates,
            series_map={
                "hv_vfd_multilevel": self.vfd_series,
                "bypass_cabinet": self.bypass_series,
            },
            source_catalog_version="seed-20260419-v1",
            compatibility_rules=[self.vfd_compatibility],
        )

        self.assertFalse(any(item["series_code"] == "bypass_cabinet" for item in payload["selected_products"]))
        self.assertFalse(payload["selection_reason"]["compatibility_actions"][0]["applies"])

    def test_build_material_record_infers_family_type_and_status(self) -> None:
        record = self.catalog_service._build_material_record(
            {
                "sample_id": "lci-bf49f75e",
                "file_name": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                "file_path": "/tmp/lci.docx",
                "file_format": "docx",
                "file_size_bytes": 1024,
                "assigned_track": "needs_review",
                "suggested_track": "needs_review",
                "document_type_hint": "unknown",
                "manual_notes": "需要后续补接口资料",
            }
        )

        self.assertEqual(record["family_code"], "lci_sync_drive")
        self.assertEqual(record["material_type"], "proposal_sample")
        self.assertEqual(record["availability_status"], "review_needed")
        self.assertIn("docx", record["tags"])

    def test_build_material_record_detects_manual_asset_types(self) -> None:
        record = self.catalog_service._build_material_record(
            {
                "material_key": "manual-001",
                "document_name": "高压变频产品手册.pdf",
                "source_path": "/tmp/manual.pdf",
                "file_format": "pdf",
            }
        )

        self.assertEqual(record["family_code"], "hv_vfd_multilevel")
        self.assertEqual(record["material_type"], "product_manual")
        self.assertEqual(record["availability_status"], "available")

    def test_build_material_record_respects_explicit_manifest_overrides(self) -> None:
        record = self.catalog_service._build_material_record(
            {
                "material_key": "sample-explicit",
                "document_name": "某项目技术协议.pdf",
                "family_code": "lci_sync_drive",
                "material_type": "product_manual",
                "availability_status": "available",
                "tags": ["curated", "lci"],
                "details": {"source_confidence": "llm_curated"},
                "solution_family": "LCI / 同步电机变频软起动",
                "manual_notes": "由当前会话补标。",
            }
        )

        self.assertEqual(record["family_code"], "lci_sync_drive")
        self.assertEqual(record["material_type"], "product_manual")
        self.assertEqual(record["availability_status"], "available")
        self.assertIn("curated", record["tags"])
        self.assertEqual(record["details"]["source_confidence"], "llm_curated")
        self.assertEqual(record["details"]["solution_family"], "LCI / 同步电机变频软起动")

    def test_build_material_record_prefers_document_name_over_file_name(self) -> None:
        record = self.catalog_service._build_material_record(
            {
                "material_key": "synthetic-lci-manual-001",
                "document_name": "LCI / 同步电机变频软起动系统 测试开发用产品手册",
                "file_name": "lci_sync_drive-product-manual-synthetic-test-only.md",
                "file_path": "/tmp/lci_sync_drive-product-manual-synthetic-test-only.md",
                "material_type": "product_manual",
            },
            source_kind="synthetic_test_only",
        )

        self.assertEqual(record["document_name"], "LCI / 同步电机变频软起动系统 测试开发用产品手册")
        self.assertEqual(record["source_path"], "/tmp/lci_sync_drive-product-manual-synthetic-test-only.md")
        self.assertEqual(record["source_kind"], "synthetic_test_only")

    def test_import_material_manifest_adds_rows_and_returns_family_counts(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(
                {
                    "entries": [
                        {
                            "sample_id": "sample-1",
                            "file_name": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                            "file_path": "/tmp/sample-1.docx",
                            "file_format": "docx",
                            "assigned_track": "needs_review",
                        },
                        {
                            "sample_id": "sample-2",
                            "file_name": "10KV-高压固态及变频软起动技术方案-2025.3-荣信.doc",
                            "file_path": "/tmp/sample-2.doc",
                            "file_format": "doc",
                            "assigned_track": "needs_review",
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
            )
            manifest_path = handle.name

        session = SimpleNamespace()
        session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = asyncio.run(
            self.catalog_service.import_material_manifest(
                session=session,
                manifest_path=manifest_path,
                replace_existing=False,
                source_kind="private_sample",
            )
        )

        self.assertEqual(result["imported_material_count"], 2)
        self.assertEqual(result["skipped_existing_count"], 0)
        self.assertEqual(result["family_counts"]["lci_sync_drive"], 1)
        self.assertEqual(result["family_counts"]["hv_solid_state_starter"], 1)
        self.assertEqual(session.add.call_count, 2)
        session.commit.assert_awaited_once()

    def test_import_material_manifest_rejects_duplicate_material_keys(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(
                {
                    "entries": [
                        {
                            "material_key": "dup-material",
                            "document_name": "LCI 产品手册 A.pdf",
                            "family_code": "lci_sync_drive",
                            "material_type": "product_manual",
                            "availability_status": "available",
                        },
                        {
                            "material_key": "dup-material",
                            "document_name": "LCI 产品手册 B.pdf",
                            "family_code": "lci_sync_drive",
                            "material_type": "product_manual",
                            "availability_status": "available",
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
            )
            manifest_path = handle.name

        session = SimpleNamespace()

        with self.assertRaises(ArtifactValidationError) as error:
            asyncio.run(
                self.catalog_service.import_material_manifest(
                    session=session,
                    manifest_path=manifest_path,
                    replace_existing=False,
                )
            )

        self.assertIn("duplicate material_key", str(error.exception))

    def test_preview_material_manifest_summarizes_gate_and_inference_state(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(
                {
                    "entries": [
                        {
                            "material_key": "manual-lci-001",
                            "document_name": "LCI 产品手册.pdf",
                            "family_code": "lci_sync_drive",
                            "material_type": "product_manual",
                            "availability_status": "available",
                        },
                        {
                            "material_key": "synthetic-vfd-rule-001",
                            "document_name": "高压变频 测试开发用规则.md",
                            "family_code": "hv_vfd_multilevel",
                            "material_type": "selection_rule",
                            "availability_status": "available",
                            "source_kind": "synthetic_test_only",
                            "source_path": "/tmp/non-existent-synthetic.md",
                        },
                        {
                            "material_key": "bom-lci-001",
                            "document_name": "LCI 标准配置清单.xlsx",
                            "notes": "通过文件名推断类型与产品族。",
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
            )
            manifest_path = handle.name

        existing_row = ProductMaterial(
            material_key="manual-lci-001",
            family_code="lci_sync_drive",
            material_type="product_manual",
            document_name="LCI 产品手册.pdf",
            availability_status="available",
            source_kind="private_sample",
            tags=[],
            details={},
        )
        session = SimpleNamespace()
        session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [existing_row]))

        result = asyncio.run(
            self.catalog_service.preview_material_manifest(
                session=session,
                manifest_path=manifest_path,
                replace_existing=False,
            )
        )

        self.assertFalse(result["import_blocked"])
        self.assertEqual(result["total_entry_count"], 3)
        self.assertEqual(result["unique_material_key_count"], 3)
        self.assertEqual(result["existing_material_count"], 1)
        self.assertEqual(result["new_material_count"], 2)
        self.assertEqual(result["would_import_count"], 2)
        self.assertEqual(result["would_skip_existing_count"], 1)
        self.assertEqual(result["gate_ready_material_count"], 2)
        self.assertEqual(result["non_synthetic_material_count"], 2)
        self.assertEqual(result["inferred_family_count"], 1)
        self.assertEqual(result["inferred_material_type_count"], 1)
        self.assertEqual(result["inferred_status_count"], 1)
        self.assertEqual(result["missing_source_path_count"], 2)
        self.assertEqual(result["missing_source_file_count"], 1)
        self.assertEqual(result["family_counts"]["lci_sync_drive"], 2)
        self.assertEqual(result["material_type_counts"]["product_manual"], 1)
        self.assertEqual(result["source_kind_counts"]["synthetic_test_only"], 1)
        self.assertEqual(result["gate_ready_family_material_counts"]["lci_sync_drive"]["product_manual"], 1)
        self.assertTrue(
            any(issue["issue_type"] == "non_gate_source_kind" for issue in result["issues"])
        )
        inferred_entry = next(item for item in result["preview_entries"] if item["material_key"] == "bom-lci-001")
        self.assertFalse(inferred_entry["explicit_family_code"])
        self.assertFalse(inferred_entry["explicit_material_type"])
        self.assertFalse(inferred_entry["explicit_availability_status"])
        self.assertTrue(any("缺少 source_path" in issue for issue in inferred_entry["issues"]))

    def test_get_material_readiness_marks_gate_failed_when_entry_materials_missing(self) -> None:
        available_rows = [
            ProductMaterial(
                material_key="manual-hvss-001",
                family_code="hv_solid_state_starter",
                material_type="product_manual",
                document_name="高压固态软起动装置手册.pdf",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="sample-lci-001",
                family_code="lci_sync_drive",
                material_type="proposal_sample",
                document_name="宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
        ]
        self.catalog_service.list_materials = AsyncMock(return_value=available_rows)
        self.catalog_service.get_active_catalog_version = AsyncMock(return_value="seed-20260419-v1")

        result = asyncio.run(self.catalog_service.get_material_readiness(session=object()))

        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["catalog_version"], "seed-20260419-v1")
        self.assertIn("core_product_manuals", result["missing_items"])
        self.assertIn("standard_bom", result["missing_items"])
        self.assertFalse(result["phase_allowances"][2]["allowed"])
        self.assertEqual(result["family_material_counts"]["hv_solid_state_starter"]["product_manual"], 1)
        self.assertEqual(result["family_material_counts"]["lci_sync_drive"]["proposal_sample"], 1)

    def test_get_material_readiness_marks_gate_passed_when_all_entry_materials_exist(self) -> None:
        available_rows = [
            ProductMaterial(
                material_key="manual-lci-001",
                family_code="lci_sync_drive",
                material_type="product_manual",
                document_name="LCI 产品手册.pdf",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="manual-vfd-001",
                family_code="hv_vfd_multilevel",
                material_type="product_manual",
                document_name="高压变频器样本册.pdf",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="bom-001",
                family_code="lci_sync_drive",
                material_type="standard_bom",
                document_name="LCI 标准 BOM.xlsx",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="interface-001",
                family_code="lci_sync_drive",
                material_type="interface_schedule",
                document_name="LCI 点表.xlsx",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="rule-001",
                family_code="hv_solid_state_starter",
                material_type="selection_rule",
                document_name="高压固态软起动选型规则.docx",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="alias-001",
                family_code=None,
                material_type="model_alias_map",
                document_name="型号术语映射表.xlsx",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
        ]
        self.catalog_service.list_materials = AsyncMock(return_value=available_rows)
        self.catalog_service.get_active_catalog_version = AsyncMock(return_value="seed-20260419-v1")

        result = asyncio.run(
            self.catalog_service.get_material_readiness(
                session=object(),
                target_family_codes=["lci_sync_drive", "hv_vfd_multilevel"],
            )
        )

        self.assertTrue(result["gate_passed"])
        self.assertEqual(result["required_core_manual_family_count"], 2)
        self.assertEqual(result["missing_items"], [])
        self.assertTrue(all(item["allowed"] for item in result["phase_allowances"][2:]))
        self.assertEqual(result["material_type_counts"]["product_manual"], 2)
        self.assertEqual(result["family_material_counts"]["unclassified"]["model_alias_map"], 1)

    def test_get_material_readiness_ignores_synthetic_test_only_materials_for_gate(self) -> None:
        available_rows = [
            ProductMaterial(
                material_key="manual-hvss-001",
                family_code="hv_solid_state_starter",
                material_type="product_manual",
                document_name="高压固态软起动装置手册.pdf",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="manual-vfd-synth-001",
                family_code="hv_vfd_multilevel",
                material_type="product_manual",
                document_name="高压变频器多电平驱动系统-测试开发用产品手册.md",
                availability_status="available",
                source_kind="synthetic_test_only",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="bom-synth-001",
                family_code="lci_sync_drive",
                material_type="standard_bom",
                document_name="LCI-测试开发用标准BOM.md",
                availability_status="available",
                source_kind="synthetic_test_only",
                tags=[],
                details={},
            ),
        ]
        self.catalog_service.list_materials = AsyncMock(return_value=available_rows)
        self.catalog_service.get_active_catalog_version = AsyncMock(return_value="seed-20260419-v1")

        result = asyncio.run(self.catalog_service.get_material_readiness(session=object()))

        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["available_material_count"], 1)
        self.assertEqual(result["material_type_counts"]["product_manual"], 1)
        self.assertNotIn("standard_bom", result["material_type_counts"])
        self.assertEqual(result["checklist"][0]["actual_count"], 1)
        self.assertTrue(result["checklist"][0]["passed"])
        self.assertNotIn("core_product_manuals", result["missing_items"])
        self.assertIn("standard_bom", result["missing_items"])

    def test_get_material_readiness_infers_scope_from_available_materials(self) -> None:
        available_rows = [
            ProductMaterial(
                material_key="manual-lci-001",
                family_code="lci_sync_drive",
                material_type="product_manual",
                document_name="LCI 产品手册.pdf",
                availability_status="available",
                source_kind="customer_provided",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="bom-support-001",
                family_code="support_equipment",
                material_type="standard_bom",
                document_name="配套设备标准BOM.xlsx",
                availability_status="available",
                source_kind="customer_provided",
                tags=[],
                details={},
            ),
            ProductMaterial(
                material_key="manual-vfd-001",
                family_code="hv_vfd_multilevel",
                material_type="product_manual",
                document_name="高压变频产品手册.pdf",
                availability_status="available",
                source_kind="private_sample",
                tags=[],
                details={},
            ),
        ]
        self.catalog_service.list_materials = AsyncMock(return_value=available_rows)
        self.catalog_service.get_active_catalog_version = AsyncMock(return_value="seed-20260419-v1")

        result = asyncio.run(self.catalog_service.get_material_readiness(session=object()))

        self.assertEqual(result["target_family_codes"], ["lci_sync_drive", "hv_vfd_multilevel"])

    def test_get_material_readiness_can_infer_scope_from_project_context(self) -> None:
        project_id = uuid4()
        project = SimpleNamespace(id=project_id, name="某项目", industry="冶金", product_line="lci", description="鼓风机软起改造")
        requirement_card = SimpleNamespace(id=uuid4(), content={"motor_type": "同步电机"})
        candidate = SimpleNamespace(series=SimpleNamespace(family_code="hv_vfd_multilevel", code="hv_vfd_multilevel"))

        session = SimpleNamespace(get=AsyncMock(return_value=project))
        self.catalog_service._resolve_latest_requirement_card = AsyncMock(return_value=requirement_card)
        self.catalog_service.shortlist_primary_products = AsyncMock(return_value=(SimpleNamespace(), [candidate]))
        self.catalog_service.list_materials = AsyncMock(return_value=[])
        self.catalog_service.get_active_catalog_version = AsyncMock(return_value="seed-20260419-v1")

        result = asyncio.run(self.catalog_service.get_material_readiness(session=session, project_id=project_id))

        self.assertEqual(result["target_family_codes"], ["hv_vfd_multilevel"])


class SolutionServiceDesignTests(unittest.IsolatedAsyncioTestCase):
    async def test_design_solution_loads_compatibility_rules_by_primary_family(self) -> None:
        project_id = uuid4()
        requirement_card_id = uuid4()
        candidate = SimpleNamespace(
            series=SimpleNamespace(
                catalog_version="seed-20260419-v1",
                family_code="lci_sync_drive",
                code="lci_primary",
            ),
            selected_config=None,
            score=1.0,
            reasons=["目录匹配"],
        )
        fake_catalog = SimpleNamespace(
            shortlist_primary_products=AsyncMock(
                return_value=(SimpleNamespace(matching_signals=[]), [candidate])
            ),
            get_series_map=AsyncMock(return_value={}),
            list_models=AsyncMock(return_value=[]),
            list_interfaces=AsyncMock(return_value=[]),
            list_materials=AsyncMock(return_value=[]),
            list_compatibility_rules=AsyncMock(return_value=[]),
        )
        service = SolutionService(product_catalog=fake_catalog)
        service._resolve_requirement_card = AsyncMock(return_value=SimpleNamespace(id=requirement_card_id))
        service._next_version = AsyncMock(return_value=1)
        service._build_solution_payload = lambda **_: {
            "solution_summary": "测试方案",
            "selected_products": [],
            "interface_plan": {},
            "key_constraints": [],
            "open_questions": [],
            "suggested_chapters": [],
            "selection_reason": {},
            "source_catalog_version": "seed-20260419-v1",
        }

        added: list[object] = []
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(id=project_id)),
            add=lambda row: added.append(row),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        await service.design_solution(session=session, project_id=project_id)

        fake_catalog.list_compatibility_rules.assert_awaited_once()
        _, kwargs = fake_catalog.list_compatibility_rules.await_args
        self.assertEqual(kwargs["source_family_code"], "lci_sync_drive")
        self.assertEqual(kwargs["catalog_version"], "seed-20260419-v1")
        self.assertEqual(len(added), 1)


if __name__ == "__main__":
    unittest.main()
