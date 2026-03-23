from __future__ import annotations

import asyncio
import unittest

from app.detector import EntityDetector
from app.mapping_store import InMemoryMappingStore
from app.masker import Masker
from app.restorer import Restorer
from app.streaming_restorer import StreamingRestorer


class RestorerTests(unittest.TestCase):
    def test_restore_replaces_known_placeholders(self) -> None:
        store = InMemoryMappingStore(ttl_seconds=3600)
        masker = Masker(detector=EntityDetector(enable_presidio=False), mapping_store=store)
        restorer = Restorer(mapping_store=store)
        session_id = "restore-session"

        asyncio.run(
            masker.mask_text(
                session_id=session_id,
                text="甲方上海电气集团，联系人张三，合同金额500万元。",
                entity_types=["COMPANY", "PERSON", "AMOUNT"],
            )
        )
        result = asyncio.run(
            restorer.restore_text(
                session_id=session_id,
                text="根据分析，[Company_A]与[Person_1]签订的[Amount_1]合同存在以下风险。",
            )
        )

        self.assertEqual(
            result.restored_text,
            "根据分析，上海电气集团与张三签订的500万元合同存在以下风险。",
        )
        self.assertEqual(result.restored_count, 3)

    def test_streaming_restorer_handles_split_placeholders(self) -> None:
        store = InMemoryMappingStore(ttl_seconds=3600)
        masker = Masker(detector=EntityDetector(enable_presidio=False), mapping_store=store)
        session_id = "stream-session"

        asyncio.run(
            masker.mask_text(
                session_id=session_id,
                text="甲方上海电气集团，联系人张三。",
                entity_types=["COMPANY", "PERSON"],
            )
        )

        async def run_test():
            restorer = StreamingRestorer(session_id=session_id, restorer=Restorer(mapping_store=store))
            first = await restorer.push("根据分析，[Comp")
            second = await restorer.push("any_A]与[Person_")
            third = await restorer.push("1]需要补充材料。")
            flushed = await restorer.flush()
            return first, second, third, flushed

        first, second, third, flushed = asyncio.run(run_test())

        self.assertEqual(first.restored_text, "根据分析，")
        self.assertEqual(second.restored_text, "上海电气集团与")
        self.assertEqual(third.restored_text, "张三需要补充材料。")
        self.assertEqual(flushed.restored_text, "")


if __name__ == "__main__":
    unittest.main()
