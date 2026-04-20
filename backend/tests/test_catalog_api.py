from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.catalog import get_product_catalog_service
from app.api.router import api_router
from app.db import get_db_session


class _FakeCatalogService:
    async def list_series(
        self,
        *,
        session,
        published_only=True,
        family=None,
        family_code=None,
        search=None,
        catalog_version=None,
    ):
        return [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "catalog_version": catalog_version or "seed-20260419-v1",
                "is_published": published_only,
                "role_type": "primary",
                "family": family or "同步电机软起动",
                "family_code": family_code or "lci_sync_drive",
                "series_name": "LCI 同步电机变频软起动系统",
                "code": "lci_sync_drive",
                "vendor": "标准产品",
                "description": "测试目录项",
                "voltage_levels": ["6kV", "10kV"],
                "min_power_kw": 2000,
                "max_power_kw": 20000,
                "topology": "负载换流型",
                "applicable_motors": ["同步电机"],
                "applicable_loads": ["鼓风机"],
                "communication_protocols": ["Profibus-DP"],
                "io_allocation": {"DI": 16, "DO": 8},
                "protection_features": ["过流"],
                "preferred_scenarios": ["同步切换"],
                "default_chapters": ["总体方案"],
                "standard_configs": [
                    {
                        "id": "00000000-0000-0000-0000-000000000010",
                        "config_name": "标准配置",
                        "components": [],
                        "applicable_scenarios": ["一般场景"],
                        "description": "标准配置",
                    }
                ],
                "constraints": [
                    {
                        "id": "00000000-0000-0000-0000-000000000011",
                        "constraint_type": "control",
                        "condition": "同步电机",
                        "action": "必须配套励磁控制柜。",
                        "severity": "blocking",
                    }
                ],
            }
        ]

    async def list_families(
        self,
        *,
        session,
        published_only=True,
        search=None,
        catalog_version=None,
    ):
        return [
            {
                "id": "00000000-0000-0000-0000-000000000020",
                "catalog_version": catalog_version or "seed-20260419-v1",
                "is_published": published_only,
                "code": "lci_sync_drive",
                "name": "同步电机软起动",
                "display_name": "LCI / 同步电机变频软起动系统",
                "description": "测试产品族",
                "status": "active",
                "sort_order": 10,
                "parent_family_code": None,
                "aliases": [
                    {
                        "id": "00000000-0000-0000-0000-000000000021",
                        "alias": "LCI",
                        "alias_type": "business",
                        "source": "seed",
                        "sort_order": 0,
                    }
                ],
                "compatibilities": [
                    {
                        "id": "00000000-0000-0000-0000-000000000022",
                        "catalog_version": catalog_version or "seed-20260419-v1",
                        "is_published": published_only,
                        "source_family_code": "lci_sync_drive",
                        "target_family_code": "support_equipment",
                        "relation_type": "requires",
                        "condition": "同步电机软起及主回路成套场景",
                        "description": "LCI 主驱动需要配套设备。",
                        "preferred_series_codes": ["rectifier_transformer", "excitation_cabinet"],
                        "optional_series_codes": ["bypass_cabinet"],
                        "sort_order": 10,
                    }
                ],
                "series_count": 1,
            }
        ]

    async def list_materials(
        self,
        *,
        session,
        family_code=None,
        material_type=None,
        availability_status=None,
        source_kind=None,
        search=None,
    ):
        return [
            {
                "id": "00000000-0000-0000-0000-000000000030",
                "material_key": "lci-bf49f75e",
                "family_code": family_code or "lci_sync_drive",
                "material_type": material_type or "proposal_sample",
                "document_name": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                "source_path": "/Volumes/thunder/code/RAG_test/private_samples/real_proposals/宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                "source_kind": source_kind or "private_sample",
                "availability_status": availability_status or "review_needed",
                "file_format": "docx",
                "file_size_bytes": 12345,
                "assigned_track": "needs_review",
                "suggested_track": "needs_review",
                "priority_tier": None,
                "tags": ["docx", "needs_review"],
                "notes": "待人工补标签",
                "details": {"solution_family": "LCI"},
            }
        ]

    async def list_models(
        self,
        *,
        session,
        published_only=True,
        family_code=None,
        series_code=None,
        search=None,
        catalog_version=None,
    ):
        return [
            {
                "id": "00000000-0000-0000-0000-000000000040",
                "catalog_version": catalog_version or "seed-20260419-v1",
                "is_published": published_only,
                "series_id": "00000000-0000-0000-0000-000000000001",
                "series_code": series_code or "lci_sync_drive",
                "model_number": "GBT.LCI.SO-A0606-211N465",
                "rated_voltage": "10kV",
                "rated_power_kw": 4208,
                "rated_current": "277.5A",
                "specs": {"input_transformer_kva": 5458},
                "source_material_key": "vera-46268861",
            }
        ]

    async def list_interfaces(
        self,
        *,
        session,
        published_only=True,
        family_code=None,
        series_code=None,
        interface_type=None,
        protocol=None,
        search=None,
        catalog_version=None,
    ):
        return [
            {
                "id": "00000000-0000-0000-0000-000000000050",
                "catalog_version": catalog_version or "seed-20260419-v1",
                "is_published": published_only,
                "series_id": "00000000-0000-0000-0000-000000000001",
                "series_code": series_code or "lci_sync_drive",
                "interface_type": interface_type or "communication",
                "protocol": protocol or "Profibus-DP",
                "signal_spec": {"adapter": "fieldbus_adapter"},
                "notes": "现场总线适配器 Profibus-DP（暂定）",
                "source_material_key": "vera-46268861",
                "sort_order": 10,
            }
        ]

    async def list_versions(self, *, session):
        return [{"catalog_version": "seed-20260419-v1", "is_published": True, "series_count": 5}]

    async def import_default_catalog(
        self,
        *,
        session,
        catalog_version=None,
        publish=True,
        replace_existing=True,
        commit=True,
    ):
        return {
            "catalog_version": catalog_version or "seed-20260419-v1",
            "imported_series_count": 5,
            "imported_model_count": 5,
            "imported_interface_count": 9,
            "published": publish,
            "reused_existing": False,
        }

    async def publish_catalog_version(self, *, session, catalog_version, commit=True):
        return {
            "catalog_version": catalog_version,
            "published_series_count": 5,
            "published_model_count": 5,
            "published_interface_count": 9,
        }

    async def import_material_manifest(
        self,
        *,
        session,
        manifest_path,
        replace_existing=False,
        source_kind=None,
        commit=True,
    ):
        return {
            "manifest_path": manifest_path,
            "imported_material_count": 13,
            "skipped_existing_count": 0,
            "family_counts": {"lci_sync_drive": 1, "hv_solid_state_starter": 2},
        }

    async def get_material_readiness(self, *, session, target_family_codes=None):
        return {
            "catalog_version": "seed-20260419-v1",
            "target_family_codes": target_family_codes or [
                "lci_sync_drive",
                "hv_solid_state_starter",
                "hv_vfd_multilevel",
            ],
            "gate_passed": False,
            "available_material_count": 3,
            "required_core_manual_family_count": 2,
            "checklist": [
                {
                    "check_key": "core_product_manuals",
                    "label": "至少两个核心产品族具备产品手册",
                    "passed": False,
                    "required_count": 2,
                    "actual_count": 1,
                    "matched_family_codes": ["hv_solid_state_starter"],
                    "matched_material_keys": ["manual-hvss-001"],
                    "matched_document_names": ["高压固态软起动装置手册.pdf"],
                    "missing_detail": "当前仅有 1 个核心产品族具备产品手册，还差 1 个。",
                }
            ],
            "missing_items": ["core_product_manuals"],
            "phase_allowances": [
                {
                    "phase": "phase_0a",
                    "label": "Phase 0A / 开发基线分流",
                    "allowed": True,
                    "reason": "允许继续用 mock 默认推进基线、链路和挂载能力。",
                },
                {
                    "phase": "phase_1",
                    "label": "Phase 1 / 产品知识驱动的方案设计层",
                    "allowed": False,
                    "reason": "材料门槛未通过。",
                },
            ],
            "material_type_counts": {"product_manual": 1},
            "family_material_counts": {
                "hv_solid_state_starter": {"total": 1, "product_manual": 1},
                "lci_sync_drive": {"total": 0},
            },
        }


async def _fake_db_session():
    yield object()


class CatalogApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api/v1")
        self.app.dependency_overrides[get_product_catalog_service] = lambda: _FakeCatalogService()
        self.app.dependency_overrides[get_db_session] = _fake_db_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_catalog_routes(self) -> None:
        with TestClient(self.app) as client:
            list_response = client.get("/api/v1/catalog/series?published_only=true")
            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(list_response.json()["data"][0]["code"], "lci_sync_drive")
            self.assertEqual(list_response.json()["data"][0]["family_code"], "lci_sync_drive")

            families_response = client.get("/api/v1/catalog/families?published_only=true")
            self.assertEqual(families_response.status_code, 200)
            self.assertEqual(families_response.json()["data"][0]["code"], "lci_sync_drive")
            self.assertEqual(families_response.json()["data"][0]["aliases"][0]["alias"], "LCI")
            self.assertEqual(families_response.json()["data"][0]["compatibilities"][0]["relation_type"], "requires")

            materials_response = client.get("/api/v1/catalog/materials?family_code=lci_sync_drive")
            self.assertEqual(materials_response.status_code, 200)
            self.assertEqual(materials_response.json()["data"][0]["material_key"], "lci-bf49f75e")
            self.assertEqual(materials_response.json()["data"][0]["family_code"], "lci_sync_drive")

            readiness_response = client.get("/api/v1/catalog/material-readiness?family_code=lci_sync_drive")
            self.assertEqual(readiness_response.status_code, 200)
            self.assertFalse(readiness_response.json()["data"]["gate_passed"])
            self.assertEqual(readiness_response.json()["data"]["target_family_codes"], ["lci_sync_drive"])
            self.assertEqual(readiness_response.json()["data"]["checklist"][0]["check_key"], "core_product_manuals")

            models_response = client.get("/api/v1/catalog/models?family_code=lci_sync_drive")
            self.assertEqual(models_response.status_code, 200)
            self.assertEqual(models_response.json()["data"][0]["model_number"], "GBT.LCI.SO-A0606-211N465")
            self.assertEqual(models_response.json()["data"][0]["source_material_key"], "vera-46268861")

            interfaces_response = client.get("/api/v1/catalog/interfaces?series_code=lci_sync_drive&protocol=Profibus")
            self.assertEqual(interfaces_response.status_code, 200)
            self.assertEqual(interfaces_response.json()["data"][0]["interface_type"], "communication")
            self.assertEqual(interfaces_response.json()["data"][0]["protocol"], "Profibus")

            versions_response = client.get("/api/v1/catalog/versions")
            self.assertEqual(versions_response.status_code, 200)
            self.assertEqual(versions_response.json()["data"][0]["series_count"], 5)

            import_response = client.post(
                "/api/v1/catalog/import-defaults",
                json={"catalog_version": "seed-20260419-v2", "publish": True, "replace_existing": True},
            )
            self.assertEqual(import_response.status_code, 200)
            self.assertEqual(import_response.json()["data"]["catalog_version"], "seed-20260419-v2")
            self.assertEqual(import_response.json()["data"]["imported_model_count"], 5)
            self.assertEqual(import_response.json()["data"]["imported_interface_count"], 9)

            import_materials_response = client.post(
                "/api/v1/catalog/materials/import-manifest",
                json={
                    "manifest_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/product-driven-sample-manifest.json",
                    "replace_existing": False,
                },
            )
            self.assertEqual(import_materials_response.status_code, 200)
            self.assertEqual(import_materials_response.json()["data"]["imported_material_count"], 13)

            publish_response = client.post("/api/v1/catalog/versions/seed-20260419-v2/publish")
            self.assertEqual(publish_response.status_code, 200)
            self.assertEqual(publish_response.json()["data"]["published_series_count"], 5)
            self.assertEqual(publish_response.json()["data"]["published_model_count"], 5)
            self.assertEqual(publish_response.json()["data"]["published_interface_count"], 9)


if __name__ == "__main__":
    unittest.main()
