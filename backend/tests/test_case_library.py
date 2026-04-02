import unittest

from app.services.parsing.case_library import (
    build_outline_library_entry,
    build_reusable_block_entries,
    extract_outline_tree,
    summarize_case_library,
)
from app.services.parsing.section_catalog import build_section_catalog, normalize_section_heading


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
        self.assertEqual(entry["heading_count"], 2)
        self.assertEqual(entry["document_title"], "总标题")
        self.assertEqual(entry["top_level_titles"], ["1 项目概述", "2 技术方案"])

    def test_build_section_catalog_merges_toc_and_body_headings(self) -> None:
        markdown = "\n".join(
            [
                "# 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
                "",
                "## 目录",
                "",
                "| 第三章 系统及方案介绍 ................................ 9 |",
                "| 一、电机配置及参数 ................................ 9 |",
                "| 二、系统方案 .................................... 14 |",
                "| 2.1 高压变频器选型 .............................. 14 |",
                "",
                "## 一、电机配置及参数",
                "",
                "说明文字",
                "",
                "## 二、系统方案",
                "",
                "## 2.1 高压变频器选型",
                "",
                "具体选型内容",
            ]
        )

        catalog = build_section_catalog(markdown)
        top_sections = catalog["sections"]

        self.assertEqual(top_sections[0]["title"], "第三章 系统及方案介绍")
        self.assertEqual(top_sections[0]["children"][1]["title"], "二、系统方案")
        self.assertEqual(
            top_sections[0]["children"][1]["children"][0]["section_path"],
            "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型",
        )
        self.assertIn("toc", top_sections[0]["source_signals"])
        self.assertIn("toc", top_sections[0]["children"][1]["source_signals"])

    def test_build_section_catalog_can_use_parser_heading_hints_from_non_markdown_source(self) -> None:
        markdown = "# 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案\n\n正文被解析成普通段落，没有稳定 markdown heading。"
        catalog = build_section_catalog(
            markdown,
            structure_hints={
                "heading_hints": [
                    {"text": "方案名称", "parser_level": 3, "item_type": "TextItem", "page_no": 1},
                    {"text": "第三章 系统及方案介绍 9", "parser_level": 1, "item_type": "TextItem", "page_no": 9},
                    {"text": "二、系统方案", "parser_level": 3, "item_type": "SectionHeaderItem", "page_no": 14},
                    {"text": "2.1 高压变频器选型", "parser_level": 3, "item_type": "SectionHeaderItem", "page_no": 14},
                ]
            },
        )

        self.assertEqual(catalog["sections"][0]["title"], "第三章 系统及方案介绍")
        self.assertEqual(catalog["sections"][0]["children"][0]["title"], "二、系统方案")
        self.assertEqual(catalog["sections"][0]["children"][0]["children"][0]["title"], "2.1 高压变频器选型")
        self.assertIn("parser_heading", catalog["sections"][0]["source_signals"])

    def test_build_outline_library_entry_can_use_parser_hints_without_markdown_outline(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="# 文档标题\n\n纯文本内容。",
            structure_hints={
                "heading_hints": [
                    {"text": "第一章 项目概述 3", "parser_level": 1, "item_type": "TextItem", "page_no": 3},
                    {"text": "一、项目背景", "parser_level": 2, "item_type": "SectionHeaderItem", "page_no": 3},
                    {"text": "二、技术方案", "parser_level": 2, "item_type": "SectionHeaderItem", "page_no": 5},
                ]
            },
        )

        self.assertEqual(entry["top_level_titles"], ["第一章 项目概述"])
        self.assertEqual(entry["heading_count"], 3)

    def test_parser_heading_hints_do_not_promote_long_plain_paragraphs_to_sections(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="# 文档标题\n\n纯文本内容。",
            structure_hints={
                "heading_hints": [
                    {"text": "方案综述", "parser_level": 1, "item_type": "TextItem", "page_no": 2},
                    {
                        "text": "本系统采用模块化高压变频装置，能够满足现场连续运行要求，并兼顾接口联锁与检修隔离。",
                        "parser_level": 1,
                        "item_type": "TextItem",
                        "page_no": 2,
                    },
                ]
            },
        )

        self.assertEqual(entry["top_level_titles"], ["方案综述"])
        self.assertEqual(entry["heading_count"], 1)

    def test_build_section_catalog_prunes_duplicate_and_front_matter_roots(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.pdf", "file_format": "pdf", "library_track": "pilot_main", "profile": "mixed_engineering_pdf"},
            markdown="\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 一、基本条件",
                    "",
                    "正文",
                    "",
                    "## 2024 年 3 月",
                    "",
                    "## 吴德朝",
                    "",
                    "## 一、 基本条件",
                ]
            ),
            structure_hints={
                "heading_hints": [
                    {"text": "一、基本条件", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "2024 年 3 月", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "吴德朝", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                ]
            },
        )

        self.assertEqual(entry["top_level_titles"], ["一、基本条件"])

    def test_build_section_catalog_does_not_treat_numeric_codes_as_heading_ordinals(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 1379NEH2",
                    "",
                    "## 2ARNSAM 1M:",
                    "",
                    "## 1 技术方案",
                ]
            )
        )

        self.assertEqual([section["title"] for section in catalog["sections"]], ["1 技术方案"])

    def test_build_section_catalog_collapses_noisy_inner_heading_and_reparents_numbered_children(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
                    "",
                    "## 第三章 系统及方案介绍",
                    "",
                    "### 三、变频现场施工方案",
                    "",
                    "#### 3.1 变频节能配套工程",
                    "",
                    "### 四、散热方案",
                    "",
                    "### 2#环冷风机在变频运行时：",
                    "",
                    "#### 3.1.1 变频器室基建工程",
                    "",
                    "#### 4.1 散热方案的介绍",
                    "",
                    "### 项目节电预估计算：",
                ]
            )
        )

        chapter = catalog["sections"][0]
        child_titles = [child["title"] for child in chapter["children"]]
        self.assertEqual(child_titles, ["三、变频现场施工方案", "四、散热方案"])
        self.assertEqual(
            [child["title"] for child in chapter["children"][0]["children"]],
            ["3.1 变频节能配套工程"],
        )
        self.assertEqual(
            [child["title"] for child in chapter["children"][0]["children"][0]["children"]],
            ["3.1.1 变频器室基建工程"],
        )
        self.assertEqual(
            [child["title"] for child in chapter["children"][1]["children"]],
            ["4.1 散热方案的介绍"],
        )

    def test_build_section_catalog_collapses_nested_project_title_and_long_inline_heading(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
                    "",
                    "## 第三章 系统及方案介绍",
                    "",
                    "### 乌海建龙钢铁环冷风机永磁电机变频节能改造技术方案",
                    "",
                    "### 3.2 柜体安装",
                    "",
                    "#### 3.2.3.1 高压变频器规格型号及结构尺寸如下：",
                    "",
                    "#### 3.2.3.2 变频器维护空间要求",
                ]
            )
        )

        chapter = catalog["sections"][0]
        child_titles = [child["title"] for child in chapter["children"]]
        self.assertEqual(child_titles, ["3.2 柜体安装"])
        self.assertEqual(
            [child["title"] for child in chapter["children"][0]["children"]],
            ["3.2.3.2 变频器维护空间要求"],
        )

    def test_build_section_catalog_keeps_same_depth_decimal_sections_as_siblings(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 第三章 系统及方案介绍",
                    "",
                    "### 三、变频现场施工方案",
                    "",
                    "#### 3.1 变频节能配套工程",
                    "",
                    "##### 3.1.1 变频器室基建工程",
                    "",
                    "##### 3.1.2 电机改造工程",
                    "",
                    "##### 3.1.3 变频器安装工程",
                ]
            )
        )

        section = catalog["sections"][0]["children"][0]["children"][0]
        self.assertEqual(
            [child["title"] for child in section["children"]],
            ["3.1.1 变频器室基建工程", "3.1.2 电机改造工程", "3.1.3 变频器安装工程"],
        )

    def test_build_section_catalog_prefers_toc_confirmed_parent_over_same_ordinal_body_heading(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 目录",
                    "",
                    "| 第三章 系统及方案介绍 ........................ 9 |",
                    "| 四、散热方案 .............................. 22 |",
                    "",
                    "## 第三章 系统及方案介绍",
                    "",
                    "### 四、散热方案",
                    "",
                    "### 4 ． IGBT 驱 动 原理",
                    "",
                    "#### 4.1 散热方案的介绍",
                    "",
                    "#### 4.2 空水冷冷却方案",
                ]
            )
        )

        chapter = catalog["sections"][0]
        scatter = next(child for child in chapter["children"] if child["title"] == "四、散热方案")
        self.assertEqual(
            [child["title"] for child in scatter["children"]],
            ["4.1 散热方案的介绍", "4.2 空水冷冷却方案"],
        )

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

    def test_build_reusable_block_entries_assigns_catalog_section_path(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 目录",
                "",
                "| 第三章 系统及方案介绍 ................................ 9 |",
                "| 二、系统方案 .................................... 14 |",
                "| 2.1 高压变频器选型 .............................. 14 |",
                "",
                "## 二、系统方案",
                "",
                "## 2.1 高压变频器选型",
                "",
                (
                    "高压变频器采用一拖一配置，支持冷风机高压电机调速运行，并预留上位机接口。"
                    "系统方案说明模块划分、控制边界和通讯接口关系，便于后续工程实施与联调。"
                    "装置采用模块化设计，兼顾现场改造范围和检修隔离要求。"
                ),
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
        )

        block = next(block for block in blocks if normalize_section_heading(block["heading_path"]).endswith("高压变频器选型"))
        self.assertEqual(block["source_section_id"], "1.1.1")
        self.assertEqual(block["heading_path"], "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型")
        self.assertEqual(block["normalized_heading"], "高压变频器选型")

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
