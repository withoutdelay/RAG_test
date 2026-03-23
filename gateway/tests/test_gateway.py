from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.mapping_store import reset_mapping_store_state


class GatewayApiTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        reset_mapping_store_state()

    def test_mask_and_restore_roundtrip(self) -> None:
        with TestClient(app) as client:
            mask_response = client.post(
                "/mask",
                json={
                    "session_id": "api-session",
                    "text": "甲方上海电气集团，联系人张三，合同金额500万元。",
                    "entity_types": ["COMPANY", "PERSON", "AMOUNT"],
                },
            )
            self.assertEqual(mask_response.status_code, 200)
            masked_text = mask_response.json()["data"]["masked_text"]
            self.assertIn("[Company_A]", masked_text)
            self.assertIn("[Person_1]", masked_text)
            self.assertIn("[Amount_1]", masked_text)

            restore_response = client.post(
                "/restore",
                json={
                    "session_id": "api-session",
                    "text": "根据分析，[Company_A]与[Person_1]签订的[Amount_1]合同存在以下风险。",
                },
            )
            self.assertEqual(restore_response.status_code, 200)
            self.assertEqual(
                restore_response.json()["data"]["restored_text"],
                "根据分析，上海电气集团与张三签订的500万元合同存在以下风险。",
            )


if __name__ == "__main__":
    unittest.main()
