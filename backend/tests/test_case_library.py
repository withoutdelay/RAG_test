import unittest

from app.services.parsing.case_library import (
    _build_section_summary,
    _postprocess_section_summary,
    _normalize_section_summary_text,
    _resolve_chunk_section_anchor,
    _should_skip_unmatched_chunk,
    build_outline_library_entry,
    build_reusable_block_entries,
    extract_outline_tree,
    summarize_case_library,
)
from app.services.parsing.case_library_text import build_section_retrieval_text
from app.services.parsing.section_catalog import build_section_catalog, normalize_section_heading
from app.services.vectorstore.chunker import ChunkPayload, Chunker


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

    def test_build_outline_library_entry_enriches_section_spans_and_summary(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 文档标题",
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
                    "本节说明电机配置和核心参数边界。",
                    "",
                    "## 二、系统方案",
                    "",
                    "## 2.1 高压变频器选型",
                    "",
                    "高压变频器采用一拖一配置，支持接口联动和模块化扩展。",
                ]
            ),
        )

        chapter = entry["section_catalog"][0]
        subsection = chapter["children"][1]["children"][0]

        self.assertEqual(chapter["page_span"], [9, 14])
        self.assertEqual(chapter["content_span"]["chunk_start"], 3)
        self.assertEqual(chapter["content_span"]["chunk_end"], 4)
        self.assertEqual(chapter["heading_family"], ["系统及方案介绍"])
        self.assertIn("电机配置", chapter["section_summary"])
        self.assertEqual(subsection["page_span"], [14, 14])
        self.assertEqual(subsection["content_span"]["chunk_start"], 4)
        self.assertEqual(subsection["content_span"]["chunk_end"], 4)
        self.assertIn("高压变频器采用一拖一配置", subsection["section_summary"])
        self.assertEqual(subsection["section_type"], "vfd_spec")
        self.assertIn("文档标题", subsection["section_retrieval_text"])

    def test_build_outline_library_entry_enriches_section_retrieval_text_with_terms(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 风机变频改造方案",
                    "",
                    "## 2 技术架构",
                    "",
                    "### 2.1 通讯接口",
                    "",
                    (
                        "系统预留 Modbus RS485 接口，并与 PLC、DCS 建立通讯链路。"
                        "控制站与上位机之间支持状态监测、联锁反馈和远程启停。"
                        "接口定义覆盖开关量、模拟量和通讯寄存器映射。"
                    ),
                ]
            ),
        )

        section = entry["section_catalog"][0]["children"][0]
        self.assertEqual(section["section_type"], "communication_interface")
        self.assertIn("通信接口", section["taxonomy_hints"])
        self.assertIn("plc", section["domain_terms"])
        self.assertIn("通信接口", section["section_retrieval_text"])
        self.assertIn("dcs", section["section_retrieval_text"].casefold())
        self.assertEqual(section["contextual_text"], section["section_retrieval_text"])
        self.assertEqual(section["semantic_retrieval_text"], section["section_retrieval_text"])
        self.assertIn("风机变频改造方案", entry["semantic_retrieval_text"])
        self.assertIn("2.1 通讯接口", entry["semantic_retrieval_text"])

    def test_build_outline_library_entry_prefers_direct_section_summary_over_child_chunks(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 1 供货范围",
                    "",
                    "本节说明供货边界与安装责任划分。",
                    "",
                    "### 1.1 明细",
                    "",
                    "子节详细列出接口点位和参数映射。",
                ]
            ),
        )

        section = entry["section_catalog"][0]

        self.assertEqual(section["section_summary"], "本节说明供货边界与安装责任划分。")

    def test_build_section_retrieval_text_normalizes_child_titles_and_strips_image_noise(self) -> None:
        text = build_section_retrieval_text(
            section={
                "section_path": "第三章 系统及方案介绍 > 3.2 柜体安装",
                "normalized_section_path": "系统及方案介绍 > 柜体安装",
                "heading_aliases": ["3.2 柜体安装"],
                "heading_family": ["系统及方案介绍"],
                "taxonomy_hints": ["installation_requirements"],
                "domain_terms": ["变频器", "柜体"],
                "children": [
                    {
                        "title": "3.2.3.2 变频器 维 护 空间要求",
                        "normalized_heading": "变频器维护空间要求",
                    },
                    {
                        "title": "3.2.3.4 变频器安装 其他 要求",
                        "normalized_heading": "变频器安装其他要求",
                    },
                ],
            },
            document_title="乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
            file_name="uhai.docx",
            section_summary="图10 安装图 <!-- image --> 为了进行换气及维护空间，柜体前后应留有检修通道。",
        )

        self.assertIn("变频器维护空间要求", text)
        self.assertIn("变频器安装其他要求", text)
        self.assertNotIn("变频器 维 护 空间要求", text)
        self.assertNotIn("变频器安装 其他 要求", text)
        self.assertNotIn("图10 安装图", text)
        self.assertNotIn("<!-- image -->", text)
        self.assertIn("为了进行换气及维护空间", text)

    def test_build_section_retrieval_text_normalizes_section_path_spacing(self) -> None:
        text = build_section_retrieval_text(
            section={
                "section_path": (
                    "第三章 系统及方案介绍 > 三、变频现场施工方案 > 3.2变频器安装储运要求 > "
                    "3.2.3 柜体安装 > 3.2.3.2 变频器 维 护 空间要求"
                ),
                "normalized_section_path": "系统及方案介绍 > 变频现场施工方案 > 变频器安装储运要求 > 柜体安装 > 变频器维护空间要求",
                "heading_aliases": ["变频器维护空间要求"],
                "heading_family": ["系统及方案介绍", "变频现场施工方案"],
                "taxonomy_hints": ["installation_requirements"],
                "domain_terms": ["变频器"],
                "children": [],
            },
            document_title="乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
            file_name="uhai.docx",
            section_summary="为了进行换气及维护空间，应确保柜体前后留有检修通道。",
        )

        self.assertIn("第三章 系统及方案介绍", text)
        self.assertIn("3.2.3.2 变频器维护空间要求", text)
        self.assertNotIn("3.2.3.2 变频器 维 护 空间要求", text)

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

    def test_build_section_catalog_merges_list_style_toc_and_body_headings_without_duplicates(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "#### 目录 Index",
                "",
                "1. 工厂设计环境 PLANT DESIGN DATA\t3",
                "    - 1.1. 自然环境ENVIRONMENT\t3",
                "    - 1.2. 供电条件SUPPLY NETWORK\t3",
                "2. 供货范围SCOPES OF SUPPLY\t4",
                "",
                "### 1 工厂设计环境 Plant design data",
                "",
                "#### 1.1 自然环境 Environment",
                "",
                "#### 1.2 供电条件 Supply Network",
                "",
                "### 2 供货范围 Scopes of supply",
            ]
        )

        catalog = build_section_catalog(markdown)

        self.assertEqual(
            [section["title"] for section in catalog["sections"]],
            ["1. 工厂设计环境 PLANT DESIGN DATA", "2. 供货范围SCOPES OF SUPPLY"],
        )
        self.assertEqual(
            [child["title"] for child in catalog["sections"][0]["children"]],
            ["1.1. 自然环境ENVIRONMENT", "1.2. 供电条件SUPPLY NETWORK"],
        )
        self.assertIn("toc", catalog["sections"][0]["source_signals"])
        self.assertIn("markdown_heading", catalog["sections"][0]["source_signals"])

    def test_build_section_catalog_sanitizes_toc_heading_internal_whitespace(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "#### 目录 Index",
                "",
                "9\t调试\t15",
                "10\t售后服务\t16",
                "",
                "### 9 调试",
                "",
                "### 10 售后服务",
            ]
        )

        catalog = build_section_catalog(markdown)

        self.assertEqual(
            [section["title"] for section in catalog["sections"]],
            ["9 调试", "10 售后服务"],
        )

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

    def test_build_outline_library_entry_drops_sentence_like_clause_heading_from_parent_summary(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 3. 变频器整体要求",
                    "",
                    "3.1 供货商应按照电机启动要求、本技术规格书和相关的适用标准，提供一套完整的变频启动装置。",
                    "",
                    "3.2 供货商应根据压缩机启动运行特点和与之配套的电动机参数选择合适的变频装置，确保系统稳定运行。",
                    "",
                    "4.22 变频器预留必要的Modbus/RS485通讯接口。",
                ]
            ),
        )

        section = entry["section_catalog"][0]
        self.assertEqual(section["title"], "3. 变频器整体要求")
        self.assertNotIn("供货商应按照电机启动要求", str(section.get("section_summary") or ""))
        self.assertNotIn("供货商应根据压缩机启动运行特点", str(section.get("section_summary") or ""))
        self.assertNotIn("供货商应按照电机启动要求", str(section.get("section_retrieval_text") or ""))
        self.assertNotIn("供货商应根据压缩机启动运行特点", str(section.get("section_retrieval_text") or ""))

    def test_build_outline_library_entry_prunes_environment_parameter_clause_roots(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 文档标题",
                    "",
                    "图中绿色虚线框内为本项目的供货范围",
                    "",
                    "- 0.1. 绕线式水电阻使用环境条件",
                    "",
                    "装置在下列环境条件下能正常工作：",
                    "",
                    "1. 环境温度-20℃～45℃；",
                    "2. 海拔高度不大于2000m；",
                    "3. 空气相对湿度不超过90%；",
                    "4. 地面倾斜度不大于5°；",
                    "5. 污染等级：工业3级；",
                    "",
                    "- 0.1. 主供货范围",
                    "",
                    "1. 主供货清单",
                ]
            ),
            structure_hints={
                "heading_hints": [
                    {"text": "图中绿色虚线框内为本项目的供货范围", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "绕线式水电阻使用环境条件", "parser_level": 2, "item_type": "ListItem", "page_no": 1},
                    {"text": "装置在下列环境条件下能正常工作：", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "主供货清单", "parser_level": 2, "item_type": "ListItem", "page_no": 1},
                ]
            },
        )

        self.assertNotIn("图中绿色虚线框内为本项目的供货范围", entry["top_level_titles"])
        self.assertNotIn("装置在下列环境条件下能正常工作：", entry["top_level_titles"])
        self.assertNotIn("1. 环境温度-20℃～45℃；", entry["top_level_titles"])
        self.assertNotIn("2. 海拔高度不大于2000m；", entry["top_level_titles"])
        self.assertIn("1. 主供货清单", entry["top_level_titles"])

    def test_build_section_catalog_keeps_metric_group_heading_but_prunes_metric_clause_roots(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "1. 水电阻起动柜技术要求",
                    "    - 1.1. 主要技术指标要求",
                    "",
                    "1. 配用电机功率：1000KW/10kV；",
                    "2. 主回路额定电流：按电机额定转子电流设计；",
                    "3. 起动电流Iq： Iq≤1.3Ie；",
                    "",
                    "2. 控制联络接口类型及数量",
                ]
            ),
            structure_hints={
                "heading_hints": [
                    {"text": "水电阻起动柜技术要求", "parser_level": 2, "item_type": "ListItem", "page_no": 1},
                    {"text": "主要技术指标要求", "parser_level": 3, "item_type": "ListItem", "page_no": 1},
                    {"text": "控制联络接口类型及数量", "parser_level": 2, "item_type": "ListItem", "page_no": 1},
                ]
            },
        )

        self.assertEqual([section["title"] for section in catalog["sections"]], ["1. 水电阻起动柜技术要求", "2. 控制联络接口类型及数量"])
        self.assertEqual(
            [child["title"] for child in catalog["sections"][0]["children"]],
            ["1.1. 主要技术指标要求"],
        )

    def test_build_section_catalog_prunes_short_numbered_requirement_clause_and_keeps_real_siblings(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 5 备品备件清单",
                    "",
                    "### 5.1.1 调试备件（投标人负责）",
                    "",
                    "1. 每一种控制板卡需要提供至少一块备件；",
                    "2. 晶闸管及触发板等数量较多的板卡，不少于三块。",
                    "",
                    "### 5.1.2 生产备件（备品备件表）",
                ]
            )
        )

        self.assertEqual([section["title"] for section in catalog["sections"]], ["5 备品备件清单"])
        self.assertEqual(
            [child["title"] for child in catalog["sections"][0]["children"]],
            ["5.1.1 调试备件（投标人负责）", "5.1.2 生产备件（备品备件表）"],
        )

    def test_build_section_catalog_prunes_parser_list_item_status_clause_root(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 8 调试",
                    "",
                    "说明文字",
                    "",
                    "## 9 售后服务",
                ]
            ),
            structure_hints={
                "heading_hints": [
                    {"text": "8 调试", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "开关柜具备调试及上电条件", "parser_level": 2, "item_type": "ListItem", "page_no": 1},
                    {"text": "9 售后服务", "parser_level": 1, "item_type": "TextItem", "page_no": 2},
                ]
            },
        )

        self.assertEqual([section["title"] for section in catalog["sections"]], ["8 调试", "9 售后服务"])

    def test_build_section_catalog_prunes_long_service_clause_from_body_and_parser(self) -> None:
        service_clause = (
            "1. 卖方将为客户提供按合同规定的质保期内的免费保修服务。"
            "在质保期内，如因卖方原因造成的对合同范围的设备的故障，我方负责免费维修或更换故障元器件，将为业主提供免费的质保服务。"
            "2.卖方提供 24 小时技术服务热线，为客户提供免费咨询服务。热线支持电话：010-58217688 。"
        )
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 9 售后服务",
                    "",
                    service_clause,
                    "",
                    "## 10 培训",
                ]
            ),
            structure_hints={
                "heading_hints": [
                    {"text": "9 售后服务", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": service_clause, "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "10 培训", "parser_level": 1, "item_type": "TextItem", "page_no": 2},
                ]
            },
        )

        self.assertEqual([section["title"] for section in catalog["sections"]], ["9 售后服务", "10 培训"])

    def test_build_section_catalog_prunes_embedded_figure_caption_heading(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "### 4.1.4 变频器外形图 Converter Typical Outline Drawing",
                    "",
                    "1. **总布置图 General Arrangement Drawing**",
                    "",
                    "### 5 备品备件清单",
                ]
            )
        )

        self.assertEqual(
            [section["title"] for section in catalog["sections"]],
            ["4.1.4 变频器外形图 Converter Typical Outline Drawing", "5 备品备件清单"],
        )

    def test_build_section_catalog_prunes_figure_caption_and_code_like_inner_noise(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 第四章 GBT-MV高压变频器综述",
                    "",
                    "### 一、功能概述",
                    "",
                    "### 图5率单元结构示意图",
                    "",
                    "### 13. 6 IE/BA0IEFT",
                    "",
                    "### 五、售后服务措施",
                ]
            )
        )

        self.assertEqual(
            [child["title"] for child in catalog["sections"][0]["children"]],
            ["一、功能概述", "五、售后服务措施"],
        )

    def test_build_section_catalog_prunes_short_person_name_inner_noise(self) -> None:
        catalog = build_section_catalog(
            "\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 第四章 GBT-MV高压变频器综述",
                    "",
                    "### 二、特点介绍",
                    "",
                    "### 吴德朝",
                    "",
                    "### 四、产品认证",
                ]
            )
        )

        self.assertEqual(
            [child["title"] for child in catalog["sections"][0]["children"]],
            ["二、特点介绍", "四、产品认证"],
        )

    def test_normalize_section_summary_text_drops_bulleted_sentence_like_clause_heading(self) -> None:
        normalized = _normalize_section_summary_text(
            "- 3.1 供货商应按照电机启动要求、本技术规格书和相关的适用标准，提供一套完整的变频启动装置。"
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_drops_page_header_prefixed_clause_heading(self) -> None:
        normalized = _normalize_section_summary_text(
            "· 10 SHAANXI QINFENG GASES &amp; TECHNOLOGY CO., LTD. - 3.4 变频装置采用多级模块串联,交直交、高高方式,每相采用功率单元串联形成中压输出。"
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_drops_inline_company_header_noise(self) -> None:
        normalized = _normalize_section_summary_text(
            "SHAANXI QINFENG GASES &amp; TECHNOLOGY CO., LTD. 3.4 变频装置采用多级模块串联,交直交、高高方式,每相采用功率单元串联形成中压输出。"
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_drops_standalone_company_header_line(self) -> None:
        normalized = _normalize_section_summary_text(
            "SHAANXI QINFENG GASES &amp; TECHNOLOGY CO., LTD."
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_heading_collapses_split_cjk_tokens_for_retrieval(self) -> None:
        self.assertEqual(
            normalize_section_heading("3.2.3.2 变频器 维 护 空间要求"),
            "变频器维护空间要求",
        )
        self.assertEqual(
            normalize_section_heading("3 ． 功率单 元 原理"),
            "功率单元原理",
        )
        self.assertEqual(
            normalize_section_heading("2、建 立 完 善 的 售 后 服务网络 体系"),
            "建立完善的售后服务网络体系",
        )

    def test_normalize_section_summary_text_collapses_split_cjk_tokens(self) -> None:
        normalized = _normalize_section_summary_text(
            "功率单 元 主要由三相 桥式 整 流 器、电容器组组成。"
        )

        self.assertEqual(normalized, "功率单元主要由三相桥式整流器、电容器组组成。")

    def test_normalize_section_summary_text_strips_inline_figure_caption_and_image_marker(self) -> None:
        normalized = _normalize_section_summary_text(
            "为了进行换气及维护空间， 应确保以下尺寸： 图10 安装图 <!-- image --> 注：装置正面离墙距离不得小于 1600mm。"
        )

        self.assertNotIn("图10 安装图", normalized)
        self.assertNotIn("<!-- image -->", normalized)
        self.assertIn("为了进行换气及维护空间", normalized)
        self.assertIn("装置正面离墙距离不得小于 1600mm", normalized)

    def test_normalize_section_summary_text_strips_leading_figure_caption_line(self) -> None:
        normalized = _normalize_section_summary_text(
            "图5率单元结构示意图\n<!-- image -->\n注意：功率单元的旁路功能属于选配项目。"
        )

        self.assertEqual(normalized, "注意：功率单元的旁路功能属于选配项目。")

    def test_normalize_section_summary_text_strips_leading_ocr_noise_prefix(self) -> None:
        normalized = _normalize_section_summary_text(
            "300 600 2740( еПН) 现场登记电机配置及负载参数如下： 电机： 73 260 it 11 350000 A FE 4952 2.0 TEHE"
        )

        self.assertIn("现场登记电机配置及负载参数如下", normalized)
        self.assertNotIn("еПН", normalized)
        self.assertNotIn("300 600 2740", normalized)

    def test_normalize_section_summary_text_strips_trailing_ocr_noise_suffix(self) -> None:
        normalized = _normalize_section_summary_text(
            "可以根据需要设定两种种制动方式。 励磁制动时要求产生一个稳定的制动转矩。 14 19IRE3 4 NI # 6"
        )

        self.assertIn("励磁制动时要求产生一个稳定的制动转矩", normalized)
        self.assertNotIn("19IRE3", normalized)
        self.assertNotIn("# 6", normalized)

    def test_normalize_section_summary_text_strips_short_trailing_ocr_id(self) -> None:
        normalized = _normalize_section_summary_text(
            "可以根据需要设定两种种制动方式。 励磁制动时要求产生一个稳定的制动转矩。 14 19IRE3"
        )

        self.assertIn("励磁制动时要求产生一个稳定的制动转矩", normalized)
        self.assertNotIn("19IRE3", normalized)

    def test_normalize_section_summary_text_drops_ascii_report_noise_line(self) -> None:
        normalized = _normalize_section_summary_text(
            "TEST REPORT Report No. Client Address Sample 3 91 Model Type Serial ND 74: H W/1 Tested by (Tel) + 027-66992377 (Postcode) + 430264"
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_strips_noisy_english_prefix_before_chinese_clause(self) -> None:
        normalized = _normalize_section_summary_text(
            "Line Converter Machine L d I d SCC U L  u L I L f L STN exTN 防护等级 Sound pressure level ≤80 dB (A) @ nominal conditions"
        )

        self.assertTrue(normalized.startswith("防护等级"))
        self.assertNotIn("Line Converter Machine", normalized)

    def test_normalize_section_summary_text_strips_noisy_infix_between_chinese_clauses(self) -> None:
        normalized = _normalize_section_summary_text(
            "现场登记电机配置及负载参数如下： DAYU ELECTRIC 73 260 it 11 350000 A FE 4952 2.0 TEHE 8/110563 HANS 风机、泵、压缩机类负载在我国当前的工农业生产中起到很大作用。"
        )

        self.assertNotIn("DAYU ELECTRIC", normalized)
        self.assertIn("现场登记电机配置及负载参数如下", normalized)
        self.assertIn("风机、泵、压缩机类负载", normalized)

    def test_normalize_section_summary_text_drops_low_signal_label_summary(self) -> None:
        normalized = _normalize_section_summary_text("图示说明")

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_drops_short_dotted_ascii_code(self) -> None:
        normalized = _normalize_section_summary_text("T.FE")

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_preserves_model_token_and_drops_broken_prefix(self) -> None:
        normalized = _normalize_section_summary_text(
            "....cal GBT-MV 系列高压变频器采用SPWM控制技术，实现对高压电动机的变频调速控制。"
        )

        self.assertTrue(normalized.startswith("GBT-MV 系列高压变频器采用SPWM控制技术"))
        self.assertNotIn("....cal", normalized)

    def test_normalize_section_summary_text_strips_trailing_visual_reference_with_page_number(self) -> None:
        normalized = _normalize_section_summary_text(
            "GBT-MV 系列高压变频器继承了家族系列产品专业可靠的设计，C 型变频器外形如下图所示：2050"
        )

        self.assertIn("GBT-MV 系列高压变频器继承了家族系列产品专业可靠的设计", normalized)
        self.assertNotIn("如下图所示", normalized)
        self.assertNotIn("2050", normalized)

    def test_normalize_section_summary_text_drops_short_caption_like_page_label(self) -> None:
        normalized = _normalize_section_summary_text("C 型变频器 ： 2050")

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_strips_trailing_caption_fragment_from_long_summary(self) -> None:
        normalized = _normalize_section_summary_text(
            "GBT-MV 系列高压变频器继承了家族系列产品专业可靠的设计，具备高可靠性。 C 型变频器 ： 2050"
        )

        self.assertIn("GBT-MV 系列高压变频器继承了家族系列产品专业可靠的设计", normalized)
        self.assertNotIn("C 型变频器", normalized)
        self.assertNotIn("2050", normalized)

    def test_normalize_section_summary_text_strips_orphan_trailing_colon_without_dropping_valid_cue(self) -> None:
        self.assertEqual(_normalize_section_summary_text("变频器具备高可靠性："), "变频器具备高可靠性")
        self.assertEqual(_normalize_section_summary_text("风机启动曲线如下："), "风机启动曲线如下：")

    def test_normalize_section_summary_text_strips_numeric_prefix_and_trailing_field_label(self) -> None:
        normalized = _normalize_section_summary_text("300 600 现场登记电机配置及负载参数如下： 电机：")

        self.assertEqual(normalized, "现场登记电机配置及负载参数如下：")

    def test_normalize_section_summary_text_strips_mid_sentence_numeric_ocr_fragment(self) -> None:
        normalized = _normalize_section_summary_text(
            "这种人为增加管阻的调节方式虽然满足了生产生活所需的对流量 41597: 的控制，但是浪费了大量的电能。"
        )

        self.assertIn("这种人为增加管阻的调节方式虽然满足了生产生活所需的对流量的控制", normalized)
        self.assertNotIn("41597", normalized)

    def test_normalize_section_summary_text_drops_standalone_numeric_ocr_line_between_clauses(self) -> None:
        normalized = _normalize_section_summary_text(
            "这种人为增加管阻的调节方式虽然满足了生产生活所需的对流量\n\n41597:882\n\nSЛE7ВRI\n\nBNE7883\n\n28101LW +8ms 136.87\n\nO.HAKE\n\n<!-- image -->\n\n的控制，但是浪费了大量的电能。"
        )

        self.assertIn("这种人为增加管阻的调节方式虽然满足了生产生活所需的对流量的控制", normalized)
        self.assertNotIn("41597", normalized)
        self.assertNotIn("BNE7883", normalized)
        self.assertNotIn("Balan", normalized)

    def test_normalize_section_summary_text_summarizes_tab_delimited_bilingual_property_block(self) -> None:
        normalized = _normalize_section_summary_text(
            "\n".join(
                [
                    "Degree of protection\tIP 31",
                    "防护等级",
                    "Sound pressure level\t≤80 dB (A) @ nominal conditions",
                    "噪音等级",
                    "Cabinet color\tRAL7032",
                    "柜体颜色\t浅灰色",
                    "Door interlocking\tElectromechanical door interlocking system",
                    "柜门联锁\t机电联锁保护",
                    "Air inlet\tFront",
                    "空气进口\t正面",
                    "Air outlet\tTop",
                    "空气出口\t顶部",
                    "Fieldbus adapter\tProfibus-DP",
                    "现场总线适配器",
                ]
            )
        )

        self.assertIn("防护等级 IP 31", normalized)
        self.assertIn("噪音等级 ≤80 dB (A)", normalized)
        self.assertNotIn("@ nominal conditions", normalized)
        self.assertIn("柜体颜色 浅灰色", normalized)
        self.assertIn("柜门联锁 机电联锁保护", normalized)
        self.assertIn("空气进口 正面", normalized)
        self.assertIn("现场总线适配器 Profibus-DP", normalized)
        self.assertNotIn("Degree of protection", normalized)
        self.assertNotIn("Air inlet", normalized)

    def test_normalize_section_summary_text_collapses_split_latin_sequences(self) -> None:
        normalized = _normalize_section_summary_text(
            "GBT-MV 系列高压变频器继承了E m e r son 家族系列产品优良传统，使弱电信号（TT L 电平）能够驱动高压回路中的器件，由手动刀闸Q S1、Q S2完成隔离。"
        )

        self.assertIn("Emerson", normalized)
        self.assertIn("TTL 电平", normalized)
        self.assertIn("QS1", normalized)
        self.assertIn("QS2", normalized)
        self.assertNotIn("E m e r son", normalized)
        self.assertNotIn("TT L", normalized)
        self.assertNotIn("Q S1", normalized)

    def test_normalize_section_summary_text_drops_short_diagram_labels_after_sentence(self) -> None:
        normalized = _normalize_section_summary_text(
            "单套变频驱动系统的单线图如下所示。\n\n10 kV 母线\n\n利旧\n\nto VFD\n\n同步电机的启动和同步由变频器(SFC)控制."
        )

        self.assertIn("单套变频驱动系统的单线图如下所示", normalized)
        self.assertIn("同步电机的启动和同步由变频器(SFC)控制", normalized)
        self.assertNotIn("10 kV 母线", normalized)
        self.assertNotIn("利旧", normalized)
        self.assertNotIn("to VFD", normalized)

    def test_normalize_section_summary_text_drops_dense_field_name_chain(self) -> None:
        normalized = _normalize_section_summary_text(
            "压缩机额定功率压缩机最大工况轴功率压缩机额定工作转速压缩机额定力矩压缩机空载阻力矩压缩机启动阻力矩压缩机转动惯量 图1:空压机阻力矩曲线图2:增压机阻力矩曲线"
        )

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_drops_single_line_table_header_fragment(self) -> None:
        normalized = _normalize_section_summary_text("| 标准 | 标题 | 标准 标题 powerdrivesystems - Interfacedefini t ion")

        self.assertEqual(normalized, "")

    def test_normalize_section_summary_text_strips_leading_pipe_table_noise_prefix(self) -> None:
        normalized = _normalize_section_summary_text(
            "| | powerdrivesystems - Interfacedefini t ion | 对拟制的开发计划和研制任务书严格评审，形成指导性文件。"
        )

        self.assertEqual(normalized, "对拟制的开发计划和研制任务书严格评审，形成指导性文件。")

    def test_normalize_section_summary_text_strips_leading_field_header_prefix(self) -> None:
        normalized = _normalize_section_summary_text(
            "序号 设备名称 型号规格 数量 外形尺寸 W*D*H（mm） 备注 一次、二次回路以水电阻软启动柜和电容柜输入、输出电缆连接点为界。"
        )

        self.assertEqual(normalized, "一次、二次回路以水电阻软启动柜和电容柜输入、输出电缆连接点为界。")

    def test_normalize_section_summary_text_normalizes_chinese_punctuation_spacing(self) -> None:
        normalized = _normalize_section_summary_text(
            "主要从事各种装置研发 、设计、制造业务 ，10000kW 级产品市场占有率全国第一 。 产品广泛应用于冶金 、 石化 、 矿山 。"
        )

        self.assertEqual(
            normalized,
            "主要从事各种装置研发、设计、制造业务，10000kW 级产品市场占有率全国第一。产品广泛应用于冶金、石化、矿山。",
        )

    def test_normalize_section_summary_text_drops_header_stub_fragments(self) -> None:
        self.assertEqual(_normalize_section_summary_text("变频器尺寸 （长 * 高 * 深.mm） |"), "")
        self.assertEqual(_normalize_section_summary_text("备注 尺寸暂定"), "")
        self.assertEqual(
            _normalize_section_summary_text("尺寸暂定 一次、二次回路以水电阻软启动柜和电容柜输入、输出电缆连接点为界。"),
            "一次、二次回路以水电阻软启动柜和电容柜输入、输出电缆连接点为界。",
        )

    def test_normalize_section_summary_text_strips_inline_figure_stub(self) -> None:
        normalized = _normalize_section_summary_text("电动机停止描述：给QF1分闸。图… 装置在下列环境条件下能正常工作。")

        self.assertEqual(normalized, "电动机停止描述：给QF1分闸。装置在下列环境条件下能正常工作。")

    def test_normalize_section_summary_text_splits_inline_label_and_ordinal_list_boundary(self) -> None:
        normalized = _normalize_section_summary_text(
            "电动机启停过程见下面一次方案图电动机启动描述：启动前确认QF2断开。装置在下列环境条件下能正常工作 2. 海拔高度不大于2000m；"
        )

        self.assertIn("见下面一次方案图。电动机启动描述：", normalized)
        self.assertIn("装置在下列环境条件下能正常工作：2. 海拔高度不大于2000m", normalized)

    def test_normalize_section_summary_text_compacts_dense_ordinal_spec_list(self) -> None:
        normalized = _normalize_section_summary_text(
            "2. 主回路额定电流：按电机额定转子电流设计； 4. 起动转矩Tq：全程＞100%额定转矩； 7. 可连续起动次数：4～6次； 8. 起动控制时间：1～99s现场可调整； 9. 二次控制电源：用户提供 1.2.6. 电极极板要求采用防腐蚀的合金板材。"
        )

        self.assertTrue(normalized.startswith("主回路额定电流：按电机额定转子电流设计；起动转矩Tq：全程＞100%额定转矩"))
        self.assertNotIn("2. 主回路额定电流", normalized)
        self.assertTrue(normalized.endswith("…"))

    def test_normalize_section_summary_text_compacts_ordinals_before_truncation(self) -> None:
        normalized = _normalize_section_summary_text(
            "\n".join(
                [
                    "2. 主回路额定电流：按电机额定转子电流设计；",
                    "4. 起动转矩Tq：全程＞100%额定转矩；",
                    "7. 可连续起动次数：4～6次；",
                    "8. 起动控制时间：1～99s现场可调整；",
                    "9. 二次控制电源：用户提供AC220V/380V，10A；",
                ]
            )
        )

        self.assertIn("二次控制电源：用户提供AC220V/380V，10A", normalized)
        self.assertNotIn("二次控制电源：用户提供…", normalized)

    def test_normalize_section_summary_text_strips_markdown_header_prefix_and_compacts_ordinals(self) -> None:
        normalized = _normalize_section_summary_text(
            "序号** **接口名称** **类型** **数量** **来处** **去处** 0.0.6. 进出线方式：采用电缆下进下出，柜体基础下应设有电缆线沟。 0.0.8. 防护等级：不低于IP30。"
        )

        self.assertEqual(normalized, "进出线方式：采用电缆下进下出，柜体基础下应设有电缆线沟；防护等级：不低于IP30。")

    def test_build_section_summary_inserts_boundary_between_snippets(self) -> None:
        summary, _has_image_marker = _build_section_summary(
            source_heading="1. 水电阻起动柜技术要求",
            chunk_texts=[
                "\n".join(
                    [
                        "2. 主回路额定电流：按电机额定转子电流设计；",
                        "4. 起动转矩Tq：全程＞100%额定转矩；",
                        "7. 可连续起动次数：4～6次；",
                        "8. 起动控制时间：1～99s现场可调整；",
                        "9. 二次控制电源：用户提供AC220V/380V，10A；",
                    ]
                ),
                "\n".join(
                    [
                        "1.2.6. 电极极板要求采用防腐蚀的合金板材，电极使用受命不低于15年。",
                        "1.2.7. 无谐波污染。水电阻特性为纯阻性，无谐波，无电压畸变，对电网无污染。",
                        "1.2.8. 设有液位监测与显示，温度监测与显示。",
                        "1.2.9. 采用PLC对相关信号及动作进行控制，接线简单，控制灵活，故障少，维护方便。",
                    ]
                ),
            ],
        )

        self.assertIn("二次控制电源：用户提供AC220V/380V，10A；电极极板要求采用防腐蚀的合金板材", summary)
        self.assertNotIn("10A 电极极板要求", summary)

    def test_normalize_section_summary_text_strips_leading_interface_signal_prefix(self) -> None:
        normalized = _postprocess_section_summary(
            "高压开关柜 DO 水电阻柜 DI 进出线方式：采用电缆下进下出，柜体基础下应设有电缆线沟；防护等级：不低于IP30。"
        )

        self.assertEqual(normalized, "进出线方式：采用电缆下进下出，柜体基础下应设有电缆线沟；防护等级：不低于IP30。")

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

    def test_build_section_catalog_keeps_parser_decimal_siblings_flat_without_explicit_parent(self) -> None:
        catalog = build_section_catalog(
            "# 文档标题\n\n普通正文。",
            structure_hints={
                "heading_hints": [
                    {"text": "12. 说明", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {"text": "3.1 主回路结构", "parser_level": 3, "item_type": "SectionHeaderItem", "page_no": 2},
                    {"text": "3.2 隔离切换逻辑", "parser_level": 3, "item_type": "SectionHeaderItem", "page_no": 2},
                    {"text": "3.3 保护联锁", "parser_level": 3, "item_type": "SectionHeaderItem", "page_no": 2},
                ]
            },
        )

        chapter = catalog["sections"][0]
        self.assertEqual(chapter["title"], "12. 说明")
        self.assertEqual(
            [child["title"] for child in chapter["children"]],
            ["3.1 主回路结构", "3.2 隔离切换逻辑", "3.3 保护联锁"],
        )

    def test_build_section_catalog_collapses_sentence_like_parser_clause_headings(self) -> None:
        catalog = build_section_catalog(
            "# 文档标题\n\n普通正文。",
            structure_hints={
                "heading_hints": [
                    {"text": "12. 说明", "parser_level": 1, "item_type": "TextItem", "page_no": 1},
                    {
                        "text": "3.1 供货商应按照电机启动要求、本技术规格书和相关的适用标准，提供一套完整的变频启动装置。",
                        "parser_level": 3,
                        "item_type": "SectionHeaderItem",
                        "page_no": 2,
                    },
                    {
                        "text": "3.2 供货商应根据压缩机启动运行特点和与之配套的电动机参数选择合适的变频装置，确保系统稳定运行。",
                        "parser_level": 3,
                        "item_type": "SectionHeaderItem",
                        "page_no": 2,
                    },
                    {
                        "text": "4.22 变频器预留必要的Modbus/RS485通讯接口。",
                        "parser_level": 3,
                        "item_type": "SectionHeaderItem",
                        "page_no": 3,
                    },
                ]
            },
        )

        chapter = catalog["sections"][0]
        self.assertEqual(
            [child["title"] for child in chapter["children"]],
            ["4.22 变频器预留必要的Modbus/RS485通讯接口。"],
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

        block = next(block for block in blocks if "调速运行" in block["content"])
        self.assertEqual(block["source_section_id"], "1.1.1")
        self.assertEqual(block["heading_path"], "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型")
        self.assertEqual(block["normalized_heading"], "高压变频器选型")
        self.assertIn("## 2.1 高压变频器选型", block["content"])
        self.assertIn("文档标题", block["contextual_text"])
        self.assertIn("高压变频器选型", block["contextual_text"])
        self.assertGreater(len(block["semantic_retrieval_text"]), len(block["contextual_text"]))
        self.assertIn("文档标题", block["semantic_retrieval_text"])
        self.assertIn("vfd_spec", block["semantic_retrieval_text"])
        self.assertIn("## 2.1 高压变频器选型", block["semantic_retrieval_text"])
        self.assertIn("兼顾现场改造范围和检修隔离要求", block["semantic_retrieval_text"])
        self.assertEqual(block["contextualized_block_text"], block["semantic_retrieval_text"])

    def test_build_reusable_block_entries_can_fallback_to_parent_section_by_ordinal(self) -> None:
        markdown = "\n".join(
            [
                "# 乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案",
                "",
                "## 第三章 系统及方案介绍",
                "",
                "### 3.2 柜体安装",
                "",
                "#### 3.2.3.1 高压变频器规格型号及结构尺寸如下：",
                "",
                (
                    "高压变频器采用高可靠功率单元和旁路切换结构，柜体内预留维护空间。"
                    "设备接口覆盖状态采集、故障报警、联锁闭锁和远程启停控制。"
                    "该部分用于说明规格尺寸、接线边界和现场安装约束。"
                ),
                "",
                "#### 3.2.3.2 变频器维护空间要求",
                "",
                "维护空间需满足前后检修距离和柜门开启条件。",
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=4),
        )

        block = next(block for block in blocks if "规格尺寸、接线边界" in block["content"])
        self.assertEqual(block["source_section_id"], "1.1")
        self.assertEqual(block["section_anchor_source"], "ordinal_fallback")
        self.assertIn("3.2 柜体安装", block["section_path"])
        self.assertIn("3.2.3.1 高压变频器规格型号及结构尺寸如下", block["contextualized_block_text"])

    def test_resolve_chunk_section_anchor_can_contextually_carry_forward_label_heading(self) -> None:
        current_section = {
            "section_id": "3.11",
            "source_heading": "五、实施规划",
            "content_span": {"chunk_start": 58, "chunk_end": 66},
        }
        chunk = ChunkPayload(
            chunk_index=60,
            chunk_type="TABLE",
            content="| 序号 | 设备 |\n| --- | --- |\n| 1 | 变频器 |",
            token_count=40,
            heading_path="高压变频器 随 机备 品 配件及工 具 清单",
            metadata={},
        )

        index, section, anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=[current_section],
            current_section_index=0,
            current_section=current_section,
        )

        self.assertEqual(index, 0)
        self.assertEqual(section, current_section)
        self.assertEqual(anchor_source, "contextual_carry_forward")

    def test_resolve_chunk_section_anchor_does_not_carry_forward_garbled_heading(self) -> None:
        current_section = {
            "section_id": "3.11",
            "source_heading": "五、实施规划",
            "content_span": {"chunk_start": 58, "chunk_end": 66},
        }
        chunk = ChunkPayload(
            chunk_index=60,
            chunk_type="TABLE",
            content="| A | B |\n| --- | --- |\n| 1 | 2 |",
            token_count=24,
            heading_path="21 BEHEND",
            metadata={},
        )

        index, section, anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=[current_section],
            current_section_index=0,
            current_section=current_section,
        )

        self.assertEqual(index, -1)
        self.assertIsNone(section)
        self.assertEqual(anchor_source, "unmatched")

    def test_resolve_chunk_section_anchor_carries_forward_equipment_instance_label_heading(self) -> None:
        current_section = {
            "section_id": "3.10",
            "source_heading": "2、2#环冷风机",
            "content_span": {"chunk_start": 35, "chunk_end": 36},
        }
        chunk = ChunkPayload(
            chunk_index=38,
            chunk_type="PLAIN",
            content="## 2#环冷风机在变频运行时：\n\n估算2#环冷风机变频改造后年节约的耗电量。",
            token_count=32,
            heading_path="2#环冷风机在变频运行时：",
            metadata={},
        )

        index, section, anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=[current_section],
            current_section_index=0,
            current_section=current_section,
        )

        self.assertEqual(index, 0)
        self.assertEqual(section, current_section)
        self.assertEqual(anchor_source, "contextual_carry_forward")

    def test_resolve_chunk_section_anchor_carries_forward_numeric_enumerated_label_heading(self) -> None:
        current_section = {
            "section_id": "4.18",
            "source_heading": "二、大禹电气产品试验条件介绍",
            "content_span": {"chunk_start": 93, "chunk_end": 114},
        }
        chunk = ChunkPayload(
            chunk_index=96,
            chunk_type="PLAIN",
            content="## 2：四 象 限 回 馈 对拖试验台\n\n试验台说明。",
            token_count=24,
            heading_path="2：四 象 限 回 馈 对拖试验台",
            metadata={},
        )

        index, section, anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=[current_section],
            current_section_index=0,
            current_section=current_section,
        )

        self.assertEqual(index, 0)
        self.assertEqual(section, current_section)
        self.assertEqual(anchor_source, "contextual_carry_forward")

    def test_resolve_chunk_section_anchor_uses_active_until_for_late_label_heading(self) -> None:
        current_section = {
            "section_id": "4.20",
            "source_heading": "1、大禹电气 科 技 股份 有 限公司产品质 量 保证 体系",
            "content_span": {"chunk_start": 98, "chunk_end": 101},
        }
        chunk = ChunkPayload(
            chunk_index=110,
            chunk_type="TABLE",
            content="| 标准 | 说明 |\n| --- | --- |\n| DLT994-2006 | 火电厂风机水泵用高压变频器 |",
            token_count=32,
            heading_path="E 其他 相关 标准 （行业 标准 ）",
            metadata={},
        )

        index, section, anchor_source = _resolve_chunk_section_anchor(
            chunk=chunk,
            flat_sections=[current_section],
            current_section_index=0,
            current_section=current_section,
            current_section_active_until=107,
        )

        self.assertEqual(index, 0)
        self.assertEqual(section, current_section)
        self.assertEqual(anchor_source, "contextual_carry_forward")

    def test_should_skip_unmatched_chunk_for_garbled_heading(self) -> None:
        chunk = ChunkPayload(
            chunk_index=123,
            chunk_type="TABLE",
            content="| (A) | (A) | (A) |\n| --- | --- | --- |\n| 169.6 | 172.1 | 165.2 |",
            token_count=32,
            heading_path="21 BEHEND",
            metadata={},
        )

        self.assertTrue(_should_skip_unmatched_chunk(chunk))

    def test_should_not_skip_unmatched_chunk_for_meaningful_formula_label(self) -> None:
        chunk = ChunkPayload(
            chunk_index=38,
            chunk_type="PLAIN",
            content="## 2#环冷风机在变频运行时：\n\n估算2#环冷风机变频改造后年节约的耗电量。",
            token_count=32,
            heading_path="2#环冷风机在变频运行时：",
            metadata={},
        )

        self.assertFalse(_should_skip_unmatched_chunk(chunk))

    def test_build_reusable_block_entries_keeps_full_table_as_single_block(self) -> None:
        rows = "\n".join(f"| {index} | 设备{index} | 型号{index} | {index} |" for index in range(1, 13))
        markdown = "\n\n".join(
            [
                "# 文档标题",
                "## 2 供货范围",
                "| 序号 | 设备 | 型号 | 数量 |\n| --- | --- | --- | --- |\n" + rows,
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=4),
        )

        supply_blocks = [block for block in blocks if block["content_form"] == "bom_table"]
        self.assertEqual(len(supply_blocks), 1)
        self.assertIn("| 1 | 设备1 | 型号1 | 1 |", supply_blocks[0]["content"])
        self.assertIn("| 12 | 设备12 | 型号12 | 12 |", supply_blocks[0]["content"])

    def test_build_reusable_block_entries_splits_nested_subsections_into_finer_blocks(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 3 系统方案 System Solution",
                "",
                "### 3.1 变频软起系统单线图 Single line Diagram",
                "",
                (
                    "单线图说明文字，包含 ICB、RCB 和变压器配置，便于电气接线与设备布置。"
                    "该部分同时说明主回路、隔离关系和柜内布置边界，用于指导现场安装和联调。"
                ),
                "",
                "### 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                "",
                "启动和同步过程描述包括励磁建立、升速到 95% 额定转速并完成同步切换。",
                "",
                "### 3.3 LCI 变频启动特性 LCI Start-up Characteristic",
                "",
                "#### 3.3.2 变频启动曲线 Start curve by SFC",
                "",
                (
                    "风机启动曲线如下，给出加速阶段和同步阶段时序，便于核对启动过程。"
                    "曲线中标明励磁建立、升速平台和同期切换节点，可用于校验启动特性是否满足设计要求。"
                ),
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=1),
        )

        normalized_heading_paths = [normalize_section_heading(str(block["heading_path"])) for block in blocks]
        self.assertTrue(any("变频软起系统单线图" in heading for heading in normalized_heading_paths))
        self.assertTrue(any("启动和同步过程描述" in heading for heading in normalized_heading_paths))
        self.assertTrue(any("LCI 变频启动特性" in heading and "变频启动曲线" in heading for heading in normalized_heading_paths))
        self.assertEqual(len({int(block["subchunk_index"]) for block in blocks}), len(blocks))

    def test_build_reusable_block_entries_materializes_image_backed_leaf_section_summary(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 3 系统方案 System Solution",
                "",
                "### 3.3 LCI 变频启动特性 LCI Start-up Characteristic",
                "",
                "#### 3.3.2 变频启动曲线 Start curve by SFC",
                "",
                "风机启动曲线如下： <!-- image -->",
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=1),
        )

        curve_blocks = [block for block in blocks if "变频启动曲线" in str(block.get("heading_path") or "")]
        self.assertEqual(len(curve_blocks), 1)
        self.assertEqual(curve_blocks[0]["section_anchor_source"], "section_summary_fallback")
        self.assertTrue(curve_blocks[0].get("synthetic_section_summary"))
        self.assertIn("风机启动曲线如下", curve_blocks[0]["content"])

    def test_build_outline_library_entry_strips_image_marker_from_section_summary_but_keeps_output_clean(self) -> None:
        entry = build_outline_library_entry(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main", "profile": "text_digital"},
            markdown="\n".join(
                [
                    "# 文档标题",
                    "",
                    "## 3 系统方案 System Solution",
                    "",
                    "### 3.3 LCI 变频启动特性 LCI Start-up Characteristic",
                    "",
                    "#### 3.3.2 变频启动曲线 Start curve by SFC",
                    "",
                    "风机启动曲线如下： <!-- image -->",
                ]
            ),
        )

        section = entry["section_catalog"][0]["children"][0]["children"][0]
        self.assertEqual(section["section_summary"], "风机启动曲线如下：")
        self.assertNotIn("<!-- image -->", section["section_summary"])
        self.assertNotIn("_section_summary_has_image_marker", section)

    def test_build_reusable_block_entries_skips_unmatched_sentence_fragment_heading_chunks(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 3 系统方案 System Solution",
                "",
                "### 3.1 变频软起系统单线图 Single line Diagram",
                "",
                "单线图说明主回路配置、隔离关系和设备布置边界。",
                "",
                "### 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                "",
                "启动和同步过程描述包括励磁建立和并网同步。",
                "",
                "### 3.3 LCI 变频启动特性 LCI Start-up Characteristic",
                "",
                "#### 3.3.1 负载数据 Load data",
                "",
                "负载数据用于计算启动曲线。",
                "",
                "#### Total starting up time is 130 s , including:",
                "",
                "LCI 启动曲线 total starting up time is 130 s, including pure acceleration time and synchronization preparation.",
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=1),
        )

        self.assertFalse(any(block["heading_path"] == "Total starting up time is 130 s , including:" for block in blocks))
        self.assertFalse(any(block["section_anchor_source"] == "unmatched" for block in blocks))

    def test_build_reusable_block_entries_skips_toc_like_page_index_chunks(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 3. 系统方案 SYSTEM SOLUTION\t5",
                "",
                "- 3.1. 变频软起系统单线图 SINGLE LINE DIAGRAM\t5",
                "- 3.2. 启动和同步过程描述 DESCRIPTION OF START AND SYNCHRONIZATION\t6",
                "- 3.3. LCI 变频启动特性 LCI START-UP CHARACTERISTIC\t7",
                "",
                "## 4 控制方案",
                "",
                (
                    "控制方案说明主控柜、接口联锁和远程启停边界。"
                    "系统支持 PLC/DCS 状态采集、故障反馈和联锁闭锁，"
                    "并保留后续扩展所需的通讯接口和接线余量。"
                ),
            ]
        )

        blocks = build_reusable_block_entries(
            sample_entry={"sample_id": "s1", "file_name": "demo.docx", "file_format": "docx", "library_track": "pilot_main"},
            markdown=markdown,
            chunker=Chunker(max_heading_depth_to_split=1),
        )

        self.assertFalse(any("SYSTEM SOLUTION\t5" in str(block["heading_path"]) for block in blocks))
        control_block = next(block for block in blocks if block["heading_path"] == "4 控制方案")
        self.assertIn("控制方案说明主控柜", control_block["content"])

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
