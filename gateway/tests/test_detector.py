from __future__ import annotations

import unittest
from unittest.mock import patch

from app.detector import EntityDetector


class DetectorTests(unittest.TestCase):
    def test_detects_core_entities_in_chinese_text(self) -> None:
        detector = EntityDetector(enable_presidio=False)
        text = "甲方上海电气集团，联系人张三，合同金额500万元，身份证号310101199001011234，联系电话13800138000"

        entities = detector.detect(
            text,
            entity_types=["COMPANY", "PERSON", "AMOUNT", "ID_CARD", "PHONE"],
        )

        payload = {(entity.entity_type, entity.original) for entity in entities}
        self.assertIn(("COMPANY", "上海电气集团"), payload)
        self.assertIn(("PERSON", "张三"), payload)
        self.assertIn(("AMOUNT", "500万元"), payload)
        self.assertIn(("ID_CARD", "310101199001011234"), payload)
        self.assertIn(("PHONE", "13800138000"), payload)

    def test_company_detection_trims_context_prefixes(self) -> None:
        detector = EntityDetector(enable_presidio=False)
        cases = {
            "本合同由上海电气集团签订。": "上海电气集团",
            "请联系上海电气集团项目组。": "上海电气集团",
            "项目由国家电网公司负责。": "国家电网公司",
        }

        for text, expected_company in cases.items():
            with self.subTest(text=text):
                entities = detector.detect(text, entity_types=["COMPANY"])
                self.assertTrue(any(entity.original == expected_company for entity in entities))
                self.assertFalse(any(entity.original.startswith(("由", "请联系", "项目由")) for entity in entities))

    def test_detector_falls_back_cleanly_when_presidio_sdk_is_unavailable(self) -> None:
        with patch("app.detector.get_presidio_sdk", return_value=None):
            detector = EntityDetector(enable_presidio=True)
            entities = detector.detect("甲方上海电气集团，联系人张三。", entity_types=["COMPANY", "PERSON"])

        payload = {(entity.entity_type, entity.original) for entity in entities}
        self.assertIn(("COMPANY", "上海电气集团"), payload)
        self.assertIn(("PERSON", "张三"), payload)


if __name__ == "__main__":
    unittest.main()
