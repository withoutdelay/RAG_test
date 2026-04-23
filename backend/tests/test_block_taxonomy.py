import unittest

from app.services.vectorstore.block_taxonomy import (
    classify_block_taxonomy,
    heading_family_similarity,
    heading_focus_adjustment,
    infer_target_taxonomy,
    support_content_forms,
)


class BlockTaxonomyTests(unittest.TestCase):
    def test_classify_overall_solution_vfd_narrative(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="本章节说明鼓风机高压电机及变频软起动系统总体方案、主回路配置与控制策略。",
            heading_path="三、鼓风机高压电机及变频软起动系统总体方案",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "overall_solution")
        self.assertEqual(taxonomy["equipment_type"], "vfd")
        self.assertEqual(taxonomy["content_form"], "narrative")

    def test_classify_bom_table(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="| 序号 | 设备名称 | 型号 | 数量 |\n| --- | --- | --- | --- |\n| 1 | 变频器 | ABB | 2 |",
            heading_path="十一、供货范围与主要设备清单",
            chunk_type="TABLE",
        )

        self.assertEqual(taxonomy["section_type"], "bom_or_supply_list")
        self.assertEqual(taxonomy["content_form"], "bom_table")

    def test_classify_document_delivery_heading_as_commercial_manual_only(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="卖方应提交设备配置图纸、接线图以及文字资料，供设计联络和后续审查使用。",
            heading_path="7 资料提供及数量",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "commercial_manual_only")
        self.assertEqual(taxonomy["content_form"], "narrative")

    def test_classify_interface_table_prefers_interface_form(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="| 序号 | 结点定义 | 技术要求 |\n| 1 | 变频器启动指令 | 干接点输入额定电压24Vdc |\n| 2 | DCS模拟量给定 | 4~20mA |",
            heading_path="2.4 控制信号接口说明",
            chunk_type="TABLE",
        )

        self.assertEqual(taxonomy["section_type"], "communication_interface")
        self.assertEqual(taxonomy["content_form"], "interface_table")

    def test_infer_target_taxonomy_marks_table_preference(self) -> None:
        taxonomy = infer_target_taxonomy(
            {
                "title": "十一、供货范围与主要设备清单",
                "purpose": "列出供货范围、主要设备、数量和规格。",
                "expected_evidence_types": ["table", "parameter"],
            }
        )

        self.assertEqual(taxonomy["section_type"], "bom_or_supply_list")
        self.assertIn("bom_table", taxonomy["preferred_content_forms"])
        self.assertIn("parameter_table", taxonomy["preferred_content_forms"])

    def test_infer_target_taxonomy_prefers_main_circuit_over_generic_solution(self) -> None:
        taxonomy = infer_target_taxonomy(
            {
                "title": "主回路系统方案",
                "purpose": "说明高压变频器主回路、旁路切换和一次接线方案。",
                "keywords": ["主回路", "旁路切换", "一次接线"],
                "expected_evidence_types": ["section", "figure"],
            }
        )

        self.assertEqual(taxonomy["section_type"], "main_circuit_scheme")
        self.assertIn("figure", taxonomy["preferred_content_forms"])

    def test_infer_target_taxonomy_honors_explicit_holdout_targets(self) -> None:
        taxonomy = infer_target_taxonomy(
            {
                "title": "2 供货范围 Scopes of supply",
                "purpose": "说明供货边界和随机资料。",
                "keywords": ["LCI", "供货范围", "控制柜"],
                "target_section_type": "supply_scope",
                "target_equipment_type": "lci",
            }
        )

        self.assertEqual(taxonomy["section_type"], "supply_scope")
        self.assertEqual(taxonomy["equipment_type"], "lci")

    def test_heading_match_beats_protection_terms_for_interface_sections(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="变频器可提供给DCS的报警、故障和保护信号，并支持开关量、模拟量和通讯接口。",
            heading_path="5. 变频启动装置与上位机的接口",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "communication_interface")
        self.assertEqual(taxonomy["equipment_type"], "vfd")

    def test_heading_match_beats_generic_vfd_terms_for_main_circuit(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="主回路采用一拖一输入输出隔离方案，含变压器柜、功率柜和输出电抗器柜。",
            heading_path="2.2 高压变频器主回路方案说明",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "main_circuit_scheme")
        self.assertEqual(taxonomy["equipment_type"], "vfd")

    def test_interface_narrative_is_not_misclassified_as_formula(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="变频装置能够为DCS系统提供4~20mA电流信号、4~20mA频率信号及开关量信号，同时预留Modbus/RS485通讯接口。",
            heading_path="2.4 控制信号接口说明",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "communication_interface")
        self.assertEqual(taxonomy["content_form"], "narrative")

    def test_performance_heading_routes_to_vfd_spec_not_interface(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="变频装置配置现场总线适配器模块，能够为DCS系统提供多种控制和监控信号。",
            heading_path="4. 变频器性能要求",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "vfd_spec")

    def test_classify_interface_with_full_chinese_controller_terms(self) -> None:
        taxonomy = classify_block_taxonomy(
            content="可编程逻辑控制器与分布式控制系统之间通过通信总线交换命令、状态和报警信号。",
            heading_path="8. 可编程逻辑控制器接口说明",
            chunk_type="PLAIN",
        )

        self.assertEqual(taxonomy["section_type"], "communication_interface")
        self.assertEqual(taxonomy["equipment_type"], "dcs_plc_interface")

    def test_support_content_forms_include_tables_for_main_circuit(self) -> None:
        forms = support_content_forms("main_circuit_scheme")
        self.assertIn("narrative", forms)
        self.assertIn("parameter_table", forms)
        self.assertIn("bom_table", forms)

    def test_heading_focus_adjustment_penalizes_necessary_and_meaning_for_main_circuit(self) -> None:
        score, reasons = heading_focus_adjustment(
            target_section_type="main_circuit_scheme",
            heading_text="1.2 变频调速的必要性和意义",
        )

        self.assertLess(score, 0)
        self.assertIn("heading_noise_penalty", reasons)

    def test_heading_family_similarity_prefers_same_major_family(self) -> None:
        self.assertGreater(heading_family_similarity("2.2 主回路方案说明", "2.3 高压变频器主要技术参数"), 0)
        self.assertEqual(heading_family_similarity("2.2 主回路方案说明", "3.1 电机改造工程"), 0.0)


if __name__ == "__main__":
    unittest.main()
