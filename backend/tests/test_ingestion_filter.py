import unittest

from app.services.vectorstore.chunker import ChunkPayload
from app.services.vectorstore.ingestion_filter import SafeIngestionFilter


class SafeIngestionFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = SafeIngestionFilter()

    def test_skips_front_matter_noise(self) -> None:
        payload = ChunkPayload(
            chunk_index=2,
            chunk_type="PLAIN",
            content="## 电机及软起动成套装置技术方案\n\n项目名称 : 买方 : 卖方 : 2024 年 6 月 9 日",
            token_count=12,
            heading_path="电机及软起动成套装置技术方案",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("front_matter_noise", decision.reasons)

    def test_skips_oversized_table(self) -> None:
        rows = "\n".join(f"| 参数{i} | 值{i} |" for i in range(40))
        payload = ChunkPayload(
            chunk_index=10,
            chunk_type="TABLE",
            content=f"| 名称 | 值 |\n|---|---|\n{rows}",
            token_count=500,
            heading_path="参数表",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("oversized_table", decision.reasons)

    def test_skips_garbled_formula_text(self) -> None:
        payload = ChunkPayload(
            chunk_index=18,
            chunk_type="PLAIN",
            content="4Ă TH₴₩÷",
            token_count=2,
            heading_path="系统功能描述",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("garbled_formula_text", decision.reasons)

    def test_allows_clean_explanatory_text(self) -> None:
        payload = ChunkPayload(
            chunk_index=30,
            chunk_type="PLAIN",
            content="系统采用一拖一变频软起方案，LCU 负责与 DCS 系统通信，并提供运行状态、报警和联锁控制。",
            token_count=32,
            heading_path="系统功能描述",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertTrue(decision.indexable)
        self.assertEqual(decision.reasons, ())


if __name__ == "__main__":
    unittest.main()
