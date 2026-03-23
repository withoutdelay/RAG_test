from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.config import get_settings
from app.services.gateway_client import GatewayClient


gateway_app = FastAPI()


@gateway_app.post("/mask")
async def fake_mask(payload: dict) -> dict:
    return {
        "code": 200,
        "message": "success",
        "data": {
            "masked_text": payload["text"].replace("上海电气集团", "[Company_A]"),
            "entity_count": 1,
            "entities_detected": [
                {
                    "original": "上海电气集团",
                    "placeholder": "[Company_A]",
                    "type": "COMPANY",
                    "start": 2,
                    "end": 8,
                }
            ],
        },
    }


@gateway_app.post("/restore")
async def fake_restore(payload: dict) -> dict:
    return {
        "code": 200,
        "message": "success",
        "data": {
            "restored_text": payload["text"].replace("[Company_A]", "上海电气集团"),
            "restored_count": 1,
        },
    }


class GatewayClientTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()

    def test_gateway_client_roundtrip_against_asgi_transport(self) -> None:
        transport = httpx.ASGITransport(app=gateway_app)
        client = GatewayClient(base_url="http://gateway.test", transport=transport)

        masked = asyncio.run(
            client.mask_text(
                session_id="backend-session",
                text="甲方上海电气集团需要新的实施方案。",
                entity_types=["COMPANY"],
            )
        )
        restored = asyncio.run(
            client.restore_text(
                session_id="backend-session",
                text="根据分析，[Company_A]需要新的实施方案。",
            )
        )

        self.assertEqual(masked.masked_text, "甲方[Company_A]需要新的实施方案。")
        self.assertEqual(masked.entity_count, 1)
        self.assertEqual(masked.entities_detected[0].placeholder, "[Company_A]")
        self.assertEqual(restored.restored_text, "根据分析，上海电气集团需要新的实施方案。")
        self.assertEqual(restored.restored_count, 1)

    def test_gateway_client_returns_passthrough_when_masking_disabled(self) -> None:
        with patch.dict(os.environ, {"GATEWAY_MASKING_ENABLED": "false"}, clear=False):
            get_settings.cache_clear()
            client = GatewayClient(base_url="http://gateway.test")

            masked = asyncio.run(
                client.mask_text(
                    session_id="disabled-session",
                    text="原始文本",
                    entity_types=["COMPANY"],
                )
            )
            restored = asyncio.run(
                client.restore_text(
                    session_id="disabled-session",
                    text="[Company_A]",
                )
            )

        get_settings.cache_clear()
        self.assertEqual(masked.masked_text, "原始文本")
        self.assertEqual(masked.entity_count, 0)
        self.assertEqual(restored.restored_text, "[Company_A]")
        self.assertEqual(restored.restored_count, 0)


if __name__ == "__main__":
    unittest.main()
