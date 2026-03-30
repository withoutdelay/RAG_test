import unittest

from app.services.parsing.case_library import (
    build_outline_library_entry,
    build_reusable_block_entries,
    extract_outline_tree,
    summarize_case_library,
)


class CaseLibraryTests(unittest.TestCase):
    def test_extract_outline_tree_preserves_hierarchy(self) -> None:
        markdown = "\n".join(
            [
                "# 总标题",
                "## 1 项目概述",
                "### 1.1 项目背景",
                "## 2 技术方案",
                "### 2.1 系统架构",
            ]
        )
        nodes = extract_outline_tree(markdown)

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].title, "总标题")
        self.assertEqual(nodes[0].children[0].heading_path, "总标题 > 1 项目概述")
        self.assertEqual(nodes[0].children[1].children[0].heading_path, "总标题 > 2 技术方案 > 2.1 系统架构")

    def test_build_outline_library_entry_collects_top_level_titles(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="# 总标题\n\n## 1 项目概述\n\n## 2 技术方案\n",
        )

        self.assertEqual(entry["sample_id"], "s1")
        self.assertEqual(entry["heading_count"], 3)
        self.assertEqual(entry["top_level_titles"], ["总标题"])

    def test_build_reusable_block_entries_skips_front_matter_and_short_chunks(self) -> None:
        markdown = "\n\n".join(
            [
                "# 文档标题",
                "项目名称：测试项目",
                "## 1 技术方案",
                (
                    "本系统采用高压固态软起动装置，具备联锁、报警、状态监测和就地控制功能。"
                    "系统预留与 DCS 的硬接点和通讯接口，适用于连续运行工况。"
                    "装置采用模块化功率单元设计，支持就地/远程切换、故障记录、历史趋势查询和联锁闭锁。"
                    "控制柜内预留维护空间和扩展接线端子，便于后续调试、扩容和现场检修。"
                ),
                "## 2 供货范围",
                (
                    "| 序号 | 设备 | 型号 | 数量 | 备注 |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| 1 | 变压器 | ZT-01 | 1 | 含温控附件 |\n"
                    "| 2 | 控制柜 | CK-02 | 2 | 含扩展端子 |\n"
                    "| 3 | 旁路柜 | PL-03 | 1 | 含联锁回路 |\n"
                ),
            ]
        )
        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
        )

        tech_block = next(block for block in blocks if block["heading_path"] == "1 技术方案")
        supply_block = next(block for block in blocks if block["content_form"] == "bom_table")
        self.assertEqual(tech_block["equipment_type"], "soft_starter")
        self.assertEqual(tech_block["content_form"], "narrative")
        self.assertEqual(supply_block["section_type"], "supply_scope")
        self.assertEqual(supply_block["content_form"], "bom_table")
        self.assertTrue(all(not block["front_matter"] for block in blocks))

    def test_summarize_case_library_counts_tracks(self) -> None:
        summary = summarize_case_library(
            outline_entries=[
                {"library_track": "pilot_main"},
                {"library_track": "holdout_eval"},
            ],
            block_entries=[
                {"library_track": "pilot_main"},
                {"library_track": "pilot_main"},
            ],
        )

        self.assertEqual(summary["outline_document_count"], 2)
        self.assertEqual(summary["reusable_block_count"], 2)
        self.assertEqual(summary["track_summary"]["pilot_main"]["blocks"], 2)


if __name__ == "__main__":
    unittest.main()
