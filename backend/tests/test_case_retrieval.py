import json
import tempfile
import unittest
from pathlib import Path

from app.services.retrieval.case_service import CaseLibraryService, build_outline_examples


class CaseRetrievalTests(unittest.TestCase):
    def _write_library(self, *, outline_entries: list[dict], block_entries: list[dict]) -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        outline_path = root / "outline_library.json"
        block_path = root / "block_library.json"
        outline_path.write_text(json.dumps({"entries": outline_entries}, ensure_ascii=False), encoding="utf-8")
        block_path.write_text(json.dumps({"entries": block_entries}, ensure_ascii=False), encoding="utf-8")
        return outline_path, block_path, temp_dir

    def test_retrieve_cases_prefers_matching_outline_titles(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "library_track": "pilot_main",
                    "profile": "mixed_engineering_pdf",
                    "top_level_titles": ["LCI 变频软起系统方案", "变压器技术规范", "运行保护与联锁设计"],
                    "flat_outline": [{"heading_path": "LCI 变频软起系统方案 > 启动和同步过程描述"}],
                },
                {
                    "sample_id": "case-b",
                    "file_name": "机场巡检方案.docx",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "top_level_titles": ["巡检方案", "维保周期", "备件清单"],
                    "flat_outline": [{"heading_path": "巡检方案 > 设备巡检周期"}],
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_cases(query="高浓磨机 LCI 软起 变压器", top_k=2, library_tracks={"pilot_main"})
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-a")
        self.assertIn("query_overlap", results[0]["reason"])

    def test_retrieve_cases_uses_secondary_family_signals_for_hybrid_documents(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-hybrid",
                    "file_name": "10KV-高压固态及变频软起动技术方案.docx",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "family_code": "hv_solid_state_starter",
                    "secondary_family_codes": ["hv_vfd_multilevel"],
                    "material_type": "proposal_sample",
                    "product_line": "hv_softstart",
                    "solution_family": "高压固态软起动",
                    "tags": ["hv_solid_state", "hv_vfd_candidate"],
                    "key_equipment": ["高压固态软起柜", "高压变频软起装置"],
                    "top_level_titles": ["系统方案", "供货范围"],
                    "flat_outline": [{"heading_path": "系统方案 > 主回路说明"}],
                },
                {
                    "sample_id": "case-b",
                    "file_name": "机场巡检方案.docx",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "top_level_titles": ["巡检方案", "维保周期"],
                    "flat_outline": [{"heading_path": "巡检方案 > 设备巡检周期"}],
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_cases(query="高压变频 一拖二 变频软起", top_k=2, library_tracks={"pilot_main"})
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-hybrid")
        self.assertIn("secondary_family_signal_match", results[0]["reason"])

    def test_retrieve_cases_can_be_scoped_to_family_codes_without_sample_ids(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-lci",
                    "file_name": "鼓风机LCI方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "lci_sync_drive",
                    "top_level_titles": ["系统方案", "接口说明"],
                    "flat_outline": [{"heading_path": "系统方案 > 主回路说明"}],
                },
                {
                    "sample_id": "case-vfd",
                    "file_name": "高压变频方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "hv_vfd_multilevel",
                    "top_level_titles": ["系统方案", "接口说明"],
                    "flat_outline": [{"heading_path": "系统方案 > 主回路说明"}],
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_cases(
                query="系统方案 接口说明",
                top_k=2,
                family_codes={"lci_sync_drive"},
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sample_id"], "case-lci")
        self.assertEqual(results[0]["family_code"], "lci_sync_drive")

    def test_retrieve_blocks_can_be_scoped_to_case_ids(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "LCI 变频软起系统方案",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "LCI 变频软起系统采用晶闸管整流逆变结构，支持同步切换和旁路投切。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "机场巡检方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "巡检方案",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "service_support",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 90,
                    "content": "巡检方案包含月检、季检和年度检修计划。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="LCI 软起 同步切换",
                top_k=4,
                sample_ids={"case-a"},
                section_title="LCI 变频软起系统方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sample_id"], "case-a")
        self.assertIn("normalized_section_title_match", results[0]["reason"])
        self.assertIn("equipment_type_match", results[0]["reason"])

    def test_retrieve_blocks_uses_family_signals_for_hybrid_case(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-hybrid",
                    "file_name": "10KV-高压固态及变频软起动技术方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "hv_solid_state_starter",
                    "secondary_family_codes": ["hv_vfd_multilevel"],
                    "product_line": "hv_softstart",
                    "solution_family": "高压固态软起动",
                    "tags": ["hv_solid_state", "hv_vfd_candidate"],
                    "key_equipment": ["高压固态软起柜", "高压变频软起装置"],
                    "heading_path": "2.2 主回路方案说明",
                    "section_path": "第二章 系统方案 > 2.2 主回路方案说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "主回路采用晶闸管旁路切换结构，并支持一拖二扩展。",
                }
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="高压变频 一拖二 变频软起",
                top_k=2,
                sample_ids={"case-hybrid"},
                section_title="主回路系统方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-hybrid")
        self.assertIn("secondary_family_signal_match", results[0]["reason"])

    def test_retrieve_blocks_can_be_scoped_to_family_codes_without_sample_ids(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-lci",
                    "file_name": "鼓风机LCI方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "lci_sync_drive",
                    "heading_path": "2.4 控制接口说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "励磁系统与主控PLC通过硬接线和串行通讯接口联动。",
                },
                {
                    "sample_id": "case-vfd",
                    "file_name": "高压变频方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "hv_vfd_multilevel",
                    "heading_path": "2.4 控制接口说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "变频器与上位机通过Modbus TCP和RS485接口通讯。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="控制接口 通讯 说明",
                top_k=2,
                family_codes={"lci_sync_drive"},
                section_title="控制接口与通讯方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sample_id"], "case-lci")
        self.assertEqual(results[0]["family_code"], "lci_sync_drive")

    def test_retrieve_blocks_can_be_scoped_to_document_names_when_sample_ids_differ(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a-runtime-id",
                    "file_name": "宝山钢铁股份有限公司三鼓风LCI改造方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "LCI 变频软起系统方案",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "LCI 主系统包含主驱动、整流变压器和励磁控制柜。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "机场巡检方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "巡检方案",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "service_support",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 90,
                    "content": "巡检方案包含月检、季检和年度检修计划。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="LCI 软起 整流变压器 励磁控制柜",
                top_k=4,
                sample_ids={"manifest-material-key"},
                document_names={"宝山钢铁股份有限公司三鼓风LCI改造方案.docx"},
                section_title="项目概述",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file_name"], "宝山钢铁股份有限公司三鼓风LCI改造方案.docx")

    def test_build_outline_examples_compacts_top_level_titles(self) -> None:
        examples = build_outline_examples(
            [
                {
                    "file_name": "案例A.pdf",
                    "score": 0.82,
                    "top_level_titles": ["LCI 变频软起系统方案", "变压器技术规范", "运行保护与联锁设计"],
                }
            ]
        )
        self.assertEqual(examples[0]["file_name"], "案例A.pdf")
        self.assertEqual(examples[0]["top_level_titles"][0], "LCI 变频软起系统方案")

    def test_expand_related_blocks_pulls_neighboring_chunks_from_same_case(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "library_track": "pilot_main",
                    "chunk_index": 10,
                    "heading_path": "2.2 主回路方案说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "主回路采用整流变压器加逆变链路结构。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "library_track": "pilot_main",
                    "chunk_index": 12,
                    "heading_path": "2.3 主回路保护与隔离",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "输出侧设置隔离开关和旁路接触器，满足检修隔离要求。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "高浓磨机LCI方案.pdf",
                    "library_track": "pilot_main",
                    "chunk_index": 28,
                    "heading_path": "5. 公司简介",
                    "reuse_level": "low",
                    "content_risk_level": "medium",
                    "front_matter": False,
                    "section_type": "company_profile",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 80,
                    "content": "公司主营业务介绍。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            neighbors = service.expand_related_blocks(
                seed_blocks=[
                    {
                        "sample_id": "case-a",
                        "chunk_index": 10,
                        "section_type": "main_circuit_scheme",
                        "equipment_type": "lci",
                        "content_form": "narrative",
                    }
                ],
                section_title="主回路系统方案",
                top_k=3,
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0]["chunk_index"], 12)
        self.assertIn("neighbor_distance", neighbors[0]["reason"])

    def test_retrieve_blocks_penalizes_generic_meaning_blocks_for_main_circuit(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "2.2 高压变频器主回路方案说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "main_circuit_scheme",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "主回路采用一拖一输入输出隔离方案，含旁路切换和检修隔离。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "1.2 变频调速的必要性和意义",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "protection_interlock",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 140,
                    "content": "变频改造可提升节能效果和运行经济性。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="鼓风机 高压电机 变频软起动 主回路 旁路切换",
                top_k=2,
                sample_ids={"case-a"},
                section_title="主回路系统方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "2.2 高压变频器主回路方案说明")
        self.assertIn("heading_noise_penalty", results[1]["reason"])

    def test_retrieve_sections_penalizes_inline_state_and_generic_summary_headings(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "1",
                            "title": "第三章 系统及方案介绍",
                            "source_heading": "第三章 系统及方案介绍",
                            "normalized_heading": "系统及方案介绍",
                            "heading_aliases": ["系统及方案介绍"],
                            "level": 1,
                            "section_path": "第三章 系统及方案介绍",
                            "heading_path": "第三章 系统及方案介绍",
                            "normalized_section_path": "系统及方案介绍",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [
                                {
                                    "section_id": "1.1",
                                    "title": "二、系统方案",
                                    "source_heading": "二、系统方案",
                                    "normalized_heading": "系统方案",
                                    "heading_aliases": ["系统方案"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 二、系统方案",
                                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案",
                                    "normalized_section_path": "系统及方案介绍 > 系统方案",
                                    "source_signals": ["toc", "parser_heading"],
                                    "children": [],
                                },
                                {
                                    "section_id": "1.2",
                                    "title": "方案综述",
                                    "source_heading": "方案综述",
                                    "normalized_heading": "方案综述",
                                    "heading_aliases": ["方案综述"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 方案综述",
                                    "heading_path": "第三章 系统及方案介绍 > 方案综述",
                                    "normalized_section_path": "系统及方案介绍 > 方案综述",
                                    "source_signals": ["markdown_heading"],
                                    "children": [],
                                },
                                {
                                    "section_id": "1.3",
                                    "title": "1#环冷风机在工频运行时：",
                                    "source_heading": "1#环冷风机在工频运行时：",
                                    "normalized_heading": "1#环冷风机在工频运行时",
                                    "heading_aliases": ["1#环冷风机在工频运行时"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 1#环冷风机在工频运行时：",
                                    "heading_path": "第三章 系统及方案介绍 > 1#环冷风机在工频运行时：",
                                    "normalized_section_path": "系统及方案介绍 > 1#环冷风机在工频运行时",
                                    "source_signals": ["markdown_heading"],
                                    "children": [],
                                },
                            ],
                        }
                    ],
                }
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_sections(
                query="系统总体架构 模块划分 接口关系 冷风机",
                top_k=3,
                sample_ids={"case-a"},
                section_title="系统及方案介绍",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "第三章 系统及方案介绍 > 二、系统方案")
        generic_summary = next(item for item in results if item["heading_path"].endswith("方案综述"))
        self.assertIn("generic_summary_heading_penalty", generic_summary["reason"])
        self.assertFalse(any("工频运行时" in item["heading_path"] for item in results))

    def test_retrieve_blocks_prefers_interface_heading_over_performance_requirements(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "风机变频方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "2.4 控制信号接口说明",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "interface_table",
                    "token_count": 130,
                    "content": "DCS至变频器的DI/DO/AI/AO点表及Modbus/RS485接口说明。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "风机变频方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "4. 变频器性能要求",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "vfd_spec",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 140,
                    "content": "变频装置应具备4~20mA隔离接口和必要的控制保护功能。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="DCS PLC Modbus RS485 DI DO AI AO",
                top_k=2,
                sample_ids={"case-a"},
                section_title="控制接口与通讯方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "2.4 控制信号接口说明")
        self.assertIn("interface_heading_bonus", results[0]["reason"])
        self.assertNotEqual(results[-1]["heading_path"], "4. 变频器性能要求")

    def test_retrieve_blocks_uses_section_contextual_text_for_detail_match(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "风机方案A.docx",
                    "library_track": "pilot_main",
                    "heading_path": "4. 技术架构",
                    "section_path": "第四章 技术架构",
                    "section_summary": "系统支持 Modbus、RS485 和 PLC 接口协同。",
                    "contextualized_block_text": "风机方案A 技术架构 Modbus RS485 PLC 接口协同 控制边界",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "系统采用分层结构设计，满足可靠性要求。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "风机方案A.docx",
                    "library_track": "pilot_main",
                    "heading_path": "4. 技术架构",
                    "section_path": "第四章 技术架构",
                    "section_summary": "系统采用模块化设计。",
                    "contextualized_block_text": "风机方案A 技术架构 模块化设计",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 108,
                    "content": "系统采用分层结构设计，满足可靠性要求。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="Modbus RS485 PLC 接口",
                top_k=2,
                sample_ids={"case-a"},
                section_title="控制接口与通讯方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertIn("section_context_match", results[0]["reason"])
        self.assertEqual(results[0]["contextualized_block_text"], "风机方案A 技术架构 Modbus RS485 PLC 接口协同 控制边界")

    def test_retrieve_sections_can_be_scoped_to_family_codes_without_sample_ids(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-lci",
                    "file_name": "鼓风机LCI方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "lci_sync_drive",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "控制接口说明",
                            "source_heading": "控制接口说明",
                            "normalized_heading": "控制接口说明",
                            "heading_aliases": ["控制接口说明", "接口说明"],
                            "level": 1,
                            "section_path": "第四章 控制接口说明",
                            "heading_path": "第四章 控制接口说明",
                            "normalized_section_path": "控制接口说明",
                            "section_summary": "励磁系统与主控PLC的联动接口说明。",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        }
                    ],
                },
                {
                    "sample_id": "case-vfd",
                    "file_name": "高压变频方案.docx",
                    "library_track": "pilot_main",
                    "family_code": "hv_vfd_multilevel",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "控制接口说明",
                            "source_heading": "控制接口说明",
                            "normalized_heading": "控制接口说明",
                            "heading_aliases": ["控制接口说明", "接口说明"],
                            "level": 1,
                            "section_path": "第四章 控制接口说明",
                            "heading_path": "第四章 控制接口说明",
                            "normalized_section_path": "控制接口说明",
                            "section_summary": "变频器与上位机的通讯接口说明。",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        }
                    ],
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_sections(
                query="控制接口 说明",
                top_k=2,
                family_codes={"lci_sync_drive"},
                section_title="控制接口与通讯方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sample_id"], "case-lci")
        self.assertEqual(results[0]["family_code"], "lci_sync_drive")

    def test_retrieve_sections_prefers_specific_subsection_when_detail_intent_is_present(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "3",
                            "title": "第三章 系统及方案介绍",
                            "source_heading": "第三章 系统及方案介绍",
                            "normalized_heading": "系统及方案介绍",
                            "heading_aliases": ["系统及方案介绍"],
                            "level": 1,
                            "section_path": "第三章 系统及方案介绍",
                            "heading_path": "第三章 系统及方案介绍",
                            "normalized_section_path": "系统及方案介绍",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [
                                {
                                    "section_id": "3.2",
                                    "title": "二、系统方案",
                                    "source_heading": "二、系统方案",
                                    "normalized_heading": "系统方案",
                                    "heading_aliases": ["系统方案"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 二、系统方案",
                                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案",
                                    "normalized_section_path": "系统及方案介绍 > 系统方案",
                                    "source_signals": ["toc", "parser_heading"],
                                    "children": [],
                                },
                                {
                                    "section_id": "3.2.4",
                                    "title": "2.4 控制信号接口说明",
                                    "source_heading": "2.4 控制信号接口说明",
                                    "normalized_heading": "控制信号接口说明",
                                    "heading_aliases": ["控制信号接口说明", "接口说明"],
                                    "level": 3,
                                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                                    "normalized_section_path": "系统及方案介绍 > 系统方案 > 控制信号接口说明",
                                    "source_signals": ["toc", "parser_heading"],
                                    "children": [],
                                },
                            ],
                        }
                    ],
                }
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_sections(
                query="系统总体架构 模块划分 接口关系",
                top_k=3,
                sample_ids={"case-a"},
                library_tracks={"pilot_main"},
                section_title="系统及方案介绍",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["section_id"], "3.2.4")

    def test_retrieve_sections_uses_section_summary_for_detail_rerank(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "方案A.docx",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "技术架构",
                            "source_heading": "技术架构",
                            "normalized_heading": "技术架构",
                            "heading_aliases": ["技术架构", "系统架构"],
                            "heading_family": ["技术架构"],
                            "level": 1,
                            "section_path": "第四章 技术架构",
                            "heading_path": "第四章 技术架构",
                            "normalized_section_path": "技术架构",
                            "section_summary": "系统采用站控层、网络层与装置层分层设计，支持 IEC 61850 通讯接口。",
                            "section_retrieval_text": "方案A 技术架构 IEC 61850 通讯接口 站控层 网络层",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        }
                    ],
                },
                {
                    "sample_id": "case-b",
                    "file_name": "方案B.docx",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "技术架构",
                            "source_heading": "技术架构",
                            "normalized_heading": "技术架构",
                            "heading_aliases": ["技术架构", "系统架构"],
                            "heading_family": ["技术架构"],
                            "level": 1,
                            "section_path": "第四章 技术架构",
                            "heading_path": "第四章 技术架构",
                            "normalized_section_path": "技术架构",
                            "section_summary": "系统采用模块化部署，满足一般控制要求。",
                            "section_retrieval_text": "方案B 技术架构 模块化部署 控制要求",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        }
                    ],
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_sections(
                query="技术架构 IEC 61850 接口",
                top_k=2,
                sample_ids={"case-a", "case-b"},
                library_tracks={"pilot_main"},
                section_title="技术架构",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-a")
        self.assertIn("section_summary_match", results[0]["reason"])

    def test_retrieve_blocks_can_be_scoped_to_section_prefixes(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "source_section_id": "3.2.4",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                    "normalized_heading": "控制信号接口说明",
                    "source_heading": "2.4 控制信号接口说明",
                    "heading_aliases": ["控制信号接口说明", "接口说明"],
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "DCS 至变频器提供 DI/DO、AI/AO 和 Modbus/RS485 接口。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "source_section_id": "3.3.1",
                    "section_path": "第三章 系统及方案介绍 > 三、施工方案 > 3.1 电机改造工程",
                    "heading_path": "第三章 系统及方案介绍 > 三、施工方案 > 3.1 电机改造工程",
                    "normalized_heading": "电机改造工程",
                    "source_heading": "3.1 电机改造工程",
                    "heading_aliases": ["电机改造工程"],
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "motor_spec",
                    "equipment_type": "motor",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "实施过程中拆除原异步电机并完成安装校准。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="DCS PLC Modbus RS485 DI DO AI AO",
                top_k=4,
                sample_ids={"case-a"},
                section_title="系统及方案介绍",
                section_path_prefixes={"第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明"},
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_section_id"], "3.2.4")

    def test_retrieve_blocks_prefers_parent_title_matched_narrative_over_parameter_table(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "乌海建龙方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型",
                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型",
                    "source_heading": "2.1 高压变频器选型",
                    "normalized_heading": "高压变频器选型",
                    "normalized_section_path": "系统及方案介绍 > 系统方案 > 高压变频器选型",
                    "heading_aliases": ["高压变频器选型"],
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 150,
                    "content": "本系统采用高压变频器一拖一方案，说明整体系统架构、模块划分以及控制接口边界。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "乌海建龙方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "1、1#环冷风机",
                    "section_path": "第三章 系统及方案介绍 > 一、电机配置及参数 > 1、1#环冷风机",
                    "source_heading": "1、1#环冷风机",
                    "normalized_heading": "1#环冷风机",
                    "normalized_section_path": "系统及方案介绍 > 电机配置及参数 > 1#环冷风机",
                    "heading_aliases": ["1#环冷风机"],
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "vfd_spec",
                    "equipment_type": "fan_blower",
                    "content_form": "parameter_table",
                    "token_count": 160,
                    "content": "| 额定电压 | 10kV | 额定功率 | 710kW | 额定电流 | 52.28A |",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="系统总体架构 模块划分 接口关系 冷风机 钢铁 风机",
                top_k=2,
                sample_ids={"case-a"},
                section_title="系统及方案介绍",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "2.1 高压变频器选型")
        self.assertIn("section_path_title_match", results[0]["reason"])
        self.assertIn("narrative_section_table_penalty", results[1]["reason"])


if __name__ == "__main__":
    unittest.main()
