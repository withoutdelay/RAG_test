from __future__ import annotations

import asyncio
import unittest

from app.detector import EntityDetector
from app.mapping_store import InMemoryMappingStore
from app.masker import Masker


class MaskerTests(unittest.TestCase):
    def test_masker_reuses_placeholders_within_same_session(self) -> None:
        store = InMemoryMappingStore(ttl_seconds=3600)
        masker = Masker(detector=EntityDetector(enable_presidio=False), mapping_store=store)
        session_id = "phase3-session"

        first_result = asyncio.run(
            masker.mask_text(
                session_id=session_id,
                text="甲方上海电气集团，联系人张三，合同金额500万元。上海电气集团负责验收。",
                entity_types=["COMPANY", "PERSON", "AMOUNT"],
            )
        )
        second_result = asyncio.run(
            masker.mask_text(
                session_id=session_id,
                text="联系人张三确认上海电气集团追加200万元预算。",
                entity_types=["COMPANY", "PERSON", "AMOUNT"],
            )
        )
        stored_mappings = asyncio.run(store.get_all(session_id))

        self.assertIn("[Company_A]", first_result.masked_text)
        self.assertEqual(first_result.masked_text.count("[Company_A]"), 2)
        self.assertIn("[Person_1]", first_result.masked_text)
        self.assertIn("[Amount_1]", first_result.masked_text)
        self.assertIn("[Company_A]", second_result.masked_text)
        self.assertIn("[Person_1]", second_result.masked_text)
        self.assertIn("[Amount_2]", second_result.masked_text)
        self.assertEqual(stored_mappings["[Company_A]"], "上海电气集团")
        self.assertEqual(stored_mappings["[Person_1]"], "张三")
        self.assertEqual(stored_mappings["[Amount_1]"], "500万元")
        self.assertEqual(stored_mappings["[Amount_2]"], "200万元")

    def test_concurrent_masking_allocates_distinct_placeholders(self) -> None:
        store = InMemoryMappingStore(ttl_seconds=3600)
        masker = Masker(detector=EntityDetector(enable_presidio=False), mapping_store=store)

        async def run_test():
            first_result, second_result = await asyncio.gather(
                masker.mask_text(
                    session_id="concurrent-session",
                    text="甲方上海电气集团要求验收。",
                    entity_types=["COMPANY"],
                ),
                masker.mask_text(
                    session_id="concurrent-session",
                    text="乙方国家电网公司负责交付。",
                    entity_types=["COMPANY"],
                ),
            )
            return first_result, second_result, await store.get_all("concurrent-session")

        first_result, second_result, stored_mappings = asyncio.run(run_test())

        self.assertEqual(len(stored_mappings), 2)
        self.assertIn("上海电气集团", stored_mappings.values())
        self.assertIn("国家电网公司", stored_mappings.values())
        self.assertNotEqual(first_result.masked_text, second_result.masked_text)
        self.assertTrue(any(placeholder in first_result.masked_text for placeholder in stored_mappings))
        self.assertTrue(any(placeholder in second_result.masked_text for placeholder in stored_mappings))


if __name__ == "__main__":
    unittest.main()
