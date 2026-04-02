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

    def test_skips_low_signal_plain_fragment(self) -> None:
        payload = ChunkPayload(
            chunk_index=12,
            chunk_type="PLAIN",
            content="# 6 Л\n\nof",
            token_count=2,
            heading_path="6 Л",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("low_signal_plain_fragment", decision.reasons)
        self.assertFalse(decision.preserve_for_assets)

    def test_skips_numeric_table_fragment_without_preserving_asset(self) -> None:
        payload = ChunkPayload(
            chunk_index=20,
            chunk_type="TABLE",
            content="| 37.5 | 17.5 | 20.0 |\n|---|---|---|\n| 47.3 | 17.5 | 29.8 |\n| 43.5 | 17.5 | 26 |",
            token_count=18,
            heading_path="47.8 17.5",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("numeric_table_fragment", decision.reasons)
        self.assertFalse(decision.preserve_for_assets)

    def test_skips_garbled_table_and_preserves_it_for_asset_review(self) -> None:
        payload = ChunkPayload(
            chunk_index=16,
            chunk_type="TABLE",
            content=(
                "| 项目 | 数值 |\n"
                "|---|---|\n"
                "| E#77+H | 4Ă TH₴₩÷ |\n"
                "| ##M*F#H##* | COS Ф 0.95 (đk Hứ) |\n"
                "| 参数 | ##**@@@ |\n"
            ),
            token_count=24,
            heading_path="参数表",
            metadata={},
        )

        decision = self.filter.decide(payload)

        self.assertFalse(decision.indexable)
        self.assertIn("garbled_table_fragment", decision.reasons)
        self.assertTrue(decision.preserve_for_assets)

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
