from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.product_constraint import ProductConstraint
from app.models.product_series import ProductSeries
from app.models.product_standard_config import ProductStandardConfig
from app.services.catalog import ProductCatalogService
from app.services.solution import SolutionService


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
            series_map={"lci_sync_drive": self.lci_series, "bypass_cabinet": self.bypass_series},
            source_catalog_version="seed-20260419-v1",
        )

        self.assertEqual(payload["source_catalog_version"], "seed-20260419-v1")
        self.assertEqual(payload["selected_products"][0]["series_code"], "lci_sync_drive")
        self.assertEqual(payload["selected_products"][0]["config"], "旁路配置")
        self.assertTrue(any(item["series_code"] == "bypass_cabinet" for item in payload["selected_products"]))
        self.assertEqual(payload["interface_plan"]["dcs_protocol"], "Profibus-DP")
        self.assertEqual(payload["selection_reason"]["source_mode"], "catalog_plus_requirement_card")


if __name__ == "__main__":
    unittest.main()
