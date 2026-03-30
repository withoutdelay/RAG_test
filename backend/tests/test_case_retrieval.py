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
        self.assertIn("section_title_match", results[0]["reason"])
        self.assertIn("equipment_type_match", results[0]["reason"])

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


if __name__ == "__main__":
    unittest.main()
