import unittest

from app.services.parsing.table_profile import build_table_profile


class TableProfileTests(unittest.TestCase):
    def test_build_table_profile_detects_parameter_matrix(self) -> None:
        markdown = (
            "| 参数 | 数值 |\n"
            "|---|---|\n"
            "| 电压 | 11000 V |\n"
            "| 频率 | 50 Hz |\n"
            "| 电流 | 220 A |\n"
            "| 功率 | 2500 kW |\n"
            "| 转速 | 1480 rpm |\n"
            "| 防护等级 | IP55 |\n"
            "| 数量 | 2 |\n"
            "| 裕量 | 15 % |\n"
            "| 温升 | 80 ℃ |\n"
            "| 重量 | 2400 kg |\n"
        )

        profile = build_table_profile(markdown)

        self.assertEqual(profile.profile_name, "parameter_matrix")
        self.assertEqual(profile.recommended_strategy, "table_reconstruction_candidate")
        self.assertGreaterEqual(profile.numeric_ratio, 0.3)
        self.assertGreaterEqual(profile.unit_hits, 4)
        self.assertIn("参数", profile.header_fields[0])

    def test_build_table_profile_detects_garbled_table(self) -> None:
        markdown = (
            "| 项目 | 数值 |\n"
            "|---|---|\n"
            "| E#77+H | 4Ă TH₴₩÷ |\n"
            "| ##M*F#H##* | COS Ф 0.95 (đk Hứ) |\n"
            "| 参数 | ##**@@@ |\n"
        )

        profile = build_table_profile(markdown)

        self.assertEqual(profile.profile_name, "garbled_table")
        self.assertEqual(profile.recommended_strategy, "ocr_reconstruction_priority")
        self.assertGreaterEqual(profile.garbled_ratio, 0.12)
        self.assertIn("garbled_cells", profile.risk_flags)


if __name__ == "__main__":
    unittest.main()
