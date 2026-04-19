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
            "published": publish,
            "reused_existing": False,
        }

    async def publish_catalog_version(self, *, session, catalog_version, commit=True):
        return {"catalog_version": catalog_version, "published_series_count": 5}


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

            versions_response = client.get("/api/v1/catalog/versions")
            self.assertEqual(versions_response.status_code, 200)
            self.assertEqual(versions_response.json()["data"][0]["series_count"], 5)

            import_response = client.post(
                "/api/v1/catalog/import-defaults",
                json={"catalog_version": "seed-20260419-v2", "publish": True, "replace_existing": True},
            )
            self.assertEqual(import_response.status_code, 200)
            self.assertEqual(import_response.json()["data"]["catalog_version"], "seed-20260419-v2")

            publish_response = client.post("/api/v1/catalog/versions/seed-20260419-v2/publish")
            self.assertEqual(publish_response.status_code, 200)
            self.assertEqual(publish_response.json()["data"]["published_series_count"], 5)


if __name__ == "__main__":
    unittest.main()
