from collections import Counter
import json
import tempfile
import unittest
from pathlib import Path

from app.services.domain.term_lexicon import build_corpus_term_lexicon, expand_terms_with_lexicon
from app.services.retrieval.case_service import CaseLibraryService, build_outline_examples


class FakeSemanticScorer:
    def __init__(self, *, scores: dict[tuple[str, str], float] | None = None) -> None:
        self._scores = scores or {}

    @property
    def available(self) -> bool:
        return True

    def score(self, *, query: str, text: str) -> float:
        return float(self._scores.get((query.strip(), text.strip()), 0.0))


class FakeReranker:
    def __init__(self, *, scores: dict[tuple[str, str], float] | None = None) -> None:
        self._scores = scores or {}

    @property
    def available(self) -> bool:
        return True

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        normalized_query = query.strip()
        return [float(self._scores.get((normalized_query, text.strip()), 0.0)) for text in texts]


class CountingCaseLibraryService(CaseLibraryService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.outline_load_count = 0
        self.block_load_count = 0

    def _load_outline_payload(self) -> dict[str, object]:
        self.outline_load_count += 1
        return super()._load_outline_payload()

    def _load_block_payload(self) -> dict[str, object]:
        self.block_load_count += 1
        return super()._load_block_payload()


class CaseRetrievalTests(unittest.TestCase):
    def _write_library(
        self,
        *,
        outline_entries: list[dict],
        block_entries: list[dict],
        outline_term_lexicon: dict[str, list[str] | tuple[str, ...]] | None = None,
        block_term_lexicon: dict[str, list[str] | tuple[str, ...]] | None = None,
    ) -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        outline_path = root / "outline_library.json"
        block_path = root / "block_library.json"
        outline_payload = {"entries": outline_entries}
        block_payload = {"entries": block_entries}
        if outline_term_lexicon is not None:
            outline_payload["term_lexicon"] = outline_term_lexicon
        if block_term_lexicon is not None:
            block_payload["term_lexicon"] = block_term_lexicon
        outline_path.write_text(json.dumps(outline_payload, ensure_ascii=False), encoding="utf-8")
        block_path.write_text(json.dumps(block_payload, ensure_ascii=False), encoding="utf-8")
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

    def test_retrieve_cases_adds_hybrid_rrf_boost_for_dual_hit_outline(self) -> None:
        semantic_primary = "第四章 变频驱动总体设计 系统拓扑 控制分层 驱动边界"
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "案例A.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "top_level_titles": ["变频驱动总体设计"],
                    "flat_outline": [{"heading_path": "第四章 变频驱动总体设计 > 系统拓扑"}],
                    "semantic_retrieval_text": semantic_primary,
                },
                {
                    "sample_id": "case-b",
                    "file_name": "案例B.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "top_level_titles": ["系统说明"],
                    "flat_outline": [{"heading_path": "第四章 系统说明"}],
                    "semantic_retrieval_text": "第四章 系统说明 通用介绍",
                },
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        ("系统架构说明", semantic_primary): 0.92,
                    }
                ),
            )
            results = service.retrieve_cases(query="系统架构说明", top_k=2, library_tracks={"pilot_main"})
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-a")
        self.assertIn("hybrid_rrf_boost=", results[0]["reason"])
        self.assertIn("hybrid_rrf_sources=sparse,semantic", results[0]["reason"])
        self.assertTrue(any(item.startswith("hybrid_rrf_boost=") for item in results[0]["reason_trace"]))
        self.assertGreater(results[0]["score_breakdown"]["hybrid_rrf"], 0.0)
        self.assertGreater(results[0]["score_breakdown"]["semantic"], 0.0)
        self.assertEqual(results[0]["score_breakdown"]["final"], results[0]["score"])

    def test_hybrid_sparse_score_prefers_shorter_denser_text(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(outline_entries=[], block_entries=[])
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            concise_score = service._hybrid_sparse_score(
                query_text="系统架构说明",
                query_terms=["系统架构说明", "系统", "架构"],
                candidate_text="系统架构说明 控制分层 驱动边界 模块关系",
                candidate_term_counts=Counter(
                    {
                        "系统架构说明": 1,
                        "系统": 1,
                        "架构": 1,
                        "控制分层": 1,
                        "驱动边界": 1,
                        "模块关系": 1,
                    }
                ),
                document_frequencies={"系统架构说明": 2, "系统": 2, "架构": 2},
                corpus_size=2,
                avg_doc_length=10.5,
            )
            verbose_score = service._hybrid_sparse_score(
                query_text="系统架构说明",
                query_terms=["系统架构说明", "系统", "架构"],
                candidate_text=(
                    "系统架构说明 控制分层 驱动边界 模块关系 "
                    "项目说明 通用介绍 组织边界 交付范围 培训安排 会议纪要 归档要求 运行记录"
                ),
                candidate_term_counts=Counter(
                    {
                        "系统架构说明": 1,
                        "系统": 1,
                        "架构": 1,
                        "控制分层": 1,
                        "驱动边界": 1,
                        "模块关系": 1,
                        "项目说明": 1,
                        "通用介绍": 1,
                        "组织边界": 1,
                        "交付范围": 1,
                        "培训安排": 1,
                        "会议纪要": 1,
                        "归档要求": 1,
                        "运行记录": 1,
                    }
                ),
                document_frequencies={"系统架构说明": 2, "系统": 2, "架构": 2},
                corpus_size=2,
                avg_doc_length=10.5,
            )
        finally:
            temp_dir.cleanup()

        self.assertGreater(concise_score, verbose_score)

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

    def test_retrieve_blocks_matches_plc_dcs_synonyms(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "风机接口方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "7. 控制接口与通讯方案",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "dcs_plc_interface",
                    "content_form": "narrative",
                    "token_count": 130,
                    "content": "PLC 与 DCS 之间通过 Modbus/RS485 通讯接口交换命令、状态和报警信息。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "风机接口方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "12. 售后服务",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "service_support",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 90,
                    "content": "项目提供培训、维保和巡检服务。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="可编程逻辑控制器 分布式控制系统 通信接口",
                top_k=2,
                sample_ids={"case-a"},
                section_title="控制接口与通信方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "7. 控制接口与通讯方案")
        self.assertIn("section_type_match", results[0]["reason"])

    def test_corpus_term_lexicon_extracts_parenthetical_aliases(self) -> None:
        lexicon = build_corpus_term_lexicon(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "section_catalog": [
                        {
                            "title": "上位监控系统（SCADA）架构",
                            "source_heading": "1 上位监控系统（SCADA）架构",
                            "heading_path": "1 上位监控系统（SCADA）架构",
                            "children": [],
                        }
                    ],
                }
            ]
        )

        expanded = expand_terms_with_lexicon(["scada"], lexicon)
        self.assertIn("上位监控系统", expanded)
        self.assertIn("上位监控", expanded)

    def test_corpus_term_lexicon_extracts_defined_aliases(self) -> None:
        lexicon = build_corpus_term_lexicon(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "section_catalog": [
                        {
                            "title": "监控系统定义",
                            "source_heading": "1 监控系统定义",
                            "heading_path": "1 监控系统定义",
                            "section_summary": "上位监控系统简称SCADA，负责趋势监视、历史归档与报警汇总。",
                            "children": [],
                        }
                    ],
                }
            ]
        )

        expanded = expand_terms_with_lexicon(["scada"], lexicon)
        self.assertIn("上位监控系统", expanded)
        self.assertIn("上位监控", expanded)

    def test_corpus_term_lexicon_rejects_parameter_unit_and_contact_noise(self) -> None:
        lexicon = build_corpus_term_lexicon(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "section_catalog": [
                        {
                            "title": "技术参数",
                            "source_heading": "1 技术参数",
                            "heading_path": "1 技术参数",
                            "section_summary": "设计标准：相关国家标准(GB)。配用电机功率(kW)。额定起动时间(Tq)。",
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "heading_path": "四、产品认证",
                    "content": "(Tel) + 027-66992377\n(Postcode) + 430264",
                }
            ],
        )

        self.assertNotIn("gb", lexicon)
        self.assertNotIn("kw", lexicon)
        self.assertNotIn("tq", lexicon)
        self.assertNotIn("postcode", lexicon)

    def test_corpus_term_lexicon_rejects_sentence_like_alias_spans(self) -> None:
        lexicon = build_corpus_term_lexicon(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "section_catalog": [
                        {
                            "title": "控制逻辑",
                            "source_heading": "1 控制逻辑",
                            "heading_path": "1 控制逻辑",
                            "section_summary": "同步电机的启动和同步由变频器(SFC)控制。变频装置采用数字控制器(MCU)。",
                            "children": [],
                        }
                    ],
                }
            ]
        )

        self.assertNotIn("sfc", lexicon)
        self.assertNotIn("mcu", lexicon)

    def test_retrieve_blocks_uses_corpus_mined_aliases(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "section_catalog": [
                        {
                            "section_id": "1",
                            "title": "上位监控系统（SCADA）架构",
                            "source_heading": "1 上位监控系统（SCADA）架构",
                            "heading_path": "1 上位监控系统（SCADA）架构",
                            "section_path": "1 上位监控系统（SCADA）架构",
                            "normalized_heading": "上位监控系统SCADA架构",
                            "heading_aliases": ["上位监控系统", "SCADA"],
                            "section_summary": "SCADA 架构用于监视、历史归档与数据采集。",
                            "source_signals": ["markdown_heading"],
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "2. 上位监控系统历史归档",
                    "section_path": "2. 上位监控系统历史归档",
                    "source_section_id": "2",
                    "source_heading": "2. 上位监控系统历史归档",
                    "normalized_heading": "上位监控系统历史归档",
                    "heading_aliases": ["上位监控系统历史归档"],
                    "section_summary": "上位监控系统提供双机热备、历史归档与趋势查询。",
                    "contextualized_block_text": "上位监控系统提供双机热备、历史归档与趋势查询。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "上位监控系统提供双机热备、历史归档与趋势查询。",
                }
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="SCADA 历史归档",
                top_k=2,
                sample_ids={"case-a"},
                section_title="SCADA 系统方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "2. 上位监控系统历史归档")
        self.assertIn("上位监控系统", results[0]["reason"])

    def test_retrieve_blocks_uses_defined_aliases(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "section_catalog": [
                        {
                            "section_id": "1",
                            "title": "监控系统定义",
                            "source_heading": "1 监控系统定义",
                            "heading_path": "1 监控系统定义",
                            "section_path": "1 监控系统定义",
                            "normalized_heading": "监控系统定义",
                            "heading_aliases": ["监控系统定义"],
                            "section_summary": "上位监控系统简称SCADA，负责趋势监视、历史归档与报警汇总。",
                            "source_signals": ["markdown_heading"],
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "2. 上位监控系统趋势查询",
                    "section_path": "2. 上位监控系统趋势查询",
                    "source_section_id": "2",
                    "source_heading": "2. 上位监控系统趋势查询",
                    "normalized_heading": "上位监控系统趋势查询",
                    "heading_aliases": ["上位监控系统趋势查询"],
                    "section_summary": "上位监控系统支持趋势查询、历史归档与报警联动。",
                    "contextualized_block_text": "上位监控系统支持趋势查询、历史归档与报警联动。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "上位监控系统支持趋势查询、历史归档与报警联动。",
                }
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="SCADA 趋势查询",
                top_k=2,
                sample_ids={"case-a"},
                section_title="SCADA 监控方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "2. 上位监控系统趋势查询")
        self.assertIn("上位监控系统", results[0]["reason"])

    def test_retrieve_blocks_prefers_lci_startup_anchor_over_generic_vfd_blocks(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "通用高压变频方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "高压变频器随机备品配件及工具清单",
                    "section_path": "高压变频器随机备品配件及工具清单",
                    "source_heading": "高压变频器随机备品配件及工具清单",
                    "section_summary": "高压变频器备件、工具与随机资料说明。",
                    "section_retrieval_text": "高压变频器 备件清单 工具清单 变频器资料",
                    "contextualized_block_text": "高压变频器备件、工具与随机资料说明。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "vfd_spec",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "项目提供高压变频器随机备品、配件和工具清单。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_summary": "启动和同步过程描述包含励磁建立、加速和并网同步过程。",
                    "section_retrieval_text": (
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "contextualized_block_text": (
                        "LCI 系统方案。启动和同步过程描述包括励磁建立、加速、并网同步，"
                        "并给出 LCI Start-up Characteristic 和 Start curve by SFC。"
                    ),
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 180,
                    "content": "启动和同步过程描述包括建立励磁、升速到 95% 额定转速并完成同步切换。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-a", "case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-b")
        self.assertIn("section_anchor", results[0]["reason"])

    def test_retrieve_blocks_prefers_direct_subheading_over_contextual_parent_anchor(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution",
                    "section_path": "3 系统方案 System Solution",
                    "source_heading": "3 系统方案 System Solution",
                    "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
                    "section_retrieval_text": (
                        "3 系统方案 System Solution "
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 180,
                    "content": "系统方案总览说明启动、同步和启动特性。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "section_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
                    "section_summary": "LCI 启动特性包括励磁建立、加速过程和启动曲线。",
                    "section_retrieval_text": (
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "contextualized_block_text": "LCI 启动曲线给出启动特性与并网前转速变化。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 150,
                    "content": "变频启动曲线展示 SFC 启动特性、升速过程和同步前的关键节点。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["heading_path"],
            "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC",
        )
        self.assertIn("section_anchor_title_match", results[0]["reason"])
        parent_results = [item for item in results[1:] if item["heading_path"] == "3 系统方案 System Solution"]
        if parent_results:
            self.assertIn("contextual_anchor_only_penalty", parent_results[0]["reason"])
            self.assertIn("broad_heading_without_heading_intent_penalty", parent_results[0]["reason"])

    def test_retrieve_blocks_demotes_broad_parent_when_specific_descendant_is_already_hit(self) -> None:
        parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明启动、同步和启动特性。",
        }
        child_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "并网前通过 Synchro-tact 完成同步条件判定与切换。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 160,
            "content": "LCI 启动后通过同步装置完成与电网的同步切换。",
        }
        sibling_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "5 控制选项与同步判据",
            "section_path": "5 控制选项与同步判据",
            "source_heading": "5 控制选项与同步判据",
            "section_summary": "同步判据说明同期检测、切换条件和控制逻辑选项。",
            "section_retrieval_text": "控制选项 同步判据 同期检测 切换条件",
            "contextualized_block_text": "同步判据用于说明切换窗口和控制逻辑。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "同步判据用于说明切换窗口、同期检测和控制逻辑。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[parent_entry, child_entry, sibling_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(parent_entry)): 0.95,
                        (semantic_query, _candidate_text(child_entry)): 0.92,
                        (semantic_query, _candidate_text(sibling_entry)): 0.82,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[2]["source_heading"], "3 系统方案 System Solution")
        self.assertIn("ancestor_specific_hit_penalty", results[2]["reason"])
        self.assertIn("broad_parent_competitive_specificity_penalty", results[2]["reason"])

    def test_retrieve_blocks_caps_broad_parent_even_without_other_specific_competitors(self) -> None:
        parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明启动、同步和启动特性。",
        }
        child_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "并网前通过 Synchro-tact 完成同步条件判定与切换。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 160,
            "content": "LCI 启动后通过同步装置完成与电网的同步切换。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[parent_entry, child_entry, support_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(parent_entry)): 0.95,
                        (semantic_query, _candidate_text(child_entry)): 0.92,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        broad_parent_results = [item for item in results[2:] if item["source_heading"] == "3 系统方案 System Solution"]
        if broad_parent_results:
            self.assertIn("broad_parent_competitive_specificity_penalty", broad_parent_results[0]["reason"])
            self.assertIn("broad_parent_competitive_score_cap", broad_parent_results[0]["reason"])
            self.assertLessEqual(float(broad_parent_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_prioritizes_query_intent_over_section_title_bias(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.1 负载数据 Load data"
                    ),
                    "section_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.1 负载数据 Load data"
                    ),
                    "source_heading": "3.3.1 负载数据 Load data",
                    "section_summary": "LCI 启动特性基于负载数据与加速曲线进行计算。",
                    "section_retrieval_text": (
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                        "3.3.1 负载数据 Load data"
                    ),
                    "contextualized_block_text": "启动特性计算依赖负载数据、转动惯量与转矩。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 140,
                    "content": "负载数据用于计算 LCI 启动特性与启动曲线。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": (
                        "3 系统方案 System Solution > "
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
                    ),
                    "section_path": (
                        "3 系统方案 System Solution > "
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
                    ),
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
                    "section_retrieval_text": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "contextualized_block_text": "并网前通过 Synchro-tact 完成同步条件判定与切换。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 160,
                    "content": "LCI 启动后通过同步装置完成与电网的同步切换。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["heading_path"],
            "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertIn("query_intent_heading_overlap", results[0]["reason"])
        if len(results) > 1:
            self.assertIn("query_intent_mismatch_penalty", results[1]["reason"])
        else:
            self.assertEqual(len(results), 1)

    def test_retrieve_blocks_sync_query_does_not_get_overridden_by_curve_subsection(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "section_path": (
                        "3 系统方案 System Solution > "
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
                    "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
                    "section_retrieval_text": (
                        "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                        "3.3.2 变频启动曲线 Start curve by SFC"
                    ),
                    "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": (
                        "3 系统方案 System Solution > "
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
                    ),
                    "section_path": (
                        "3 系统方案 System Solution > "
                        "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
                    ),
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
                    "section_retrieval_text": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "contextualized_block_text": "并网前通过 Synchro-tact 完成同步条件判定与切换。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 160,
                    "content": "LCI 启动后通过同步装置完成与电网的同步切换。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["heading_path"],
            "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        figure_results = [
            item
            for item in results[1:]
            if item["heading_path"] == "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC"
        ]
        if figure_results:
            self.assertIn("section_title_without_query_intent_penalty", figure_results[0]["reason"])
            self.assertIn("non_requested_figure_section_penalty", figure_results[0]["reason"])

    def test_retrieve_blocks_penalizes_cross_document_noise_missing_lci_intent(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "通用高压变频方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "第四章 GBT-MV高压变频器综述 > 2 自动跟踪启动",
                    "section_path": "第四章 GBT-MV高压变频器综述 > 2 自动跟踪启动",
                    "source_heading": "2 自动跟踪启动",
                    "section_summary": "高压变频器支持自动跟踪启动，并可在不同工况下完成升速。",
                    "section_retrieval_text": "高压变频器 自动跟踪启动 变频器 升速 同步电机",
                    "contextualized_block_text": "高压变频器支持自动跟踪启动，并可在不同工况下完成升速。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "vfd_spec",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 140,
                    "content": "高压变频器支持自动跟踪启动，并可在不同工况下完成升速和切换。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
                    "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
                    "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 150,
                    "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-a", "case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["sample_id"], "case-b")
        self.assertIn("equipment_intent_overlap", results[0]["reason"])
        self.assertIn("missing_equipment_intent_penalty", results[1]["reason"])
        self.assertIn("explicit_equipment_mismatch_penalty", results[1]["reason"])

    def test_retrieve_blocks_allows_lci_technical_query_to_match_vfd_spec_without_equipment_penalty(self) -> None:
        wrong_leaf_curve_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        technical_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "source_heading": "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_summary": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器遵循的标准和证书 Converter Standard and Certification",
            "contextualized_block_text": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 125,
            "content": "Converter standard and certification, IEC compliance and type test records.",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[wrong_leaf_curve_entry, technical_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频器配置\nLCI 标准和证书"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(technical_entry)): 0.86,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 标准和证书",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频器配置",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
        )
        self.assertIn("equipment_intent_heading_overlap", results[0]["reason"])
        self.assertIn("equipment_type_match", results[0]["reason"])
        self.assertNotIn("missing_equipment_intent_penalty", results[0]["reason"])
        self.assertNotIn("explicit_equipment_mismatch_penalty", results[0]["reason"])
        self.assertNotIn("equipment_type_mismatch_penalty", results[0]["reason"])

    def test_retrieve_blocks_prefers_single_line_subsection_for_single_line_query(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
                    "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
                    "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
                    "section_summary": "LCI 单线图给出变压器、断路器和主回路连接关系。",
                    "section_retrieval_text": "LCI SFC 变频软起系统单线图 Single line Diagram",
                    "contextualized_block_text": "LCI 单线图给出变压器、断路器和主回路连接关系。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 130,
                    "content": "LCI 单线图给出变压器、断路器和主回路连接关系。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC",
                    "section_path": "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC",
                    "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
                    "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
                    "section_retrieval_text": "LCI SFC 变频启动特性 Start curve by SFC",
                    "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "启动曲线给出加速平台与并网前转速变化。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 单线图",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.1 变频软起系统单线图 Single line Diagram")
        self.assertIn("query_intent_heading_overlap", results[0]["reason"])
        mismatch_results = [
            item
            for item in results[1:]
            if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"
        ]
        if mismatch_results:
            self.assertIn("strict_query_intent_mismatch", mismatch_results[0]["reason"])

    def test_retrieve_blocks_penalizes_non_requested_support_sections(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "2 供货范围 Scopes of supply",
                    "section_path": "2 供货范围 Scopes of supply",
                    "source_heading": "2 供货范围 Scopes of supply",
                    "section_summary": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                    "section_retrieval_text": "LCI 供货范围 控制板卡 辅助设备",
                    "contextualized_block_text": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "supply_scope",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
                    "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
                    "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 150,
                    "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.2 启动和同步过程描述 Description of Start-up and Synchronization")
        support_results = [item for item in results[1:] if item["source_heading"] == "2 供货范围 Scopes of supply"]
        if support_results:
            self.assertIn("non_requested_support_section_penalty", support_results[0]["reason"])
        else:
            self.assertEqual(len(results), 1)

    def test_retrieve_blocks_dedupes_duplicate_non_requested_support_sections(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "2 供货范围 Scopes of supply",
                    "section_path": "2 供货范围 Scopes of supply",
                    "source_heading": "2 供货范围 Scopes of supply",
                    "source_section_id": "2",
                    "section_summary": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                    "section_retrieval_text": "LCI 供货范围 控制板卡 辅助设备",
                    "contextualized_block_text": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "supply_scope",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "2 供货范围 Scopes of supply",
                    "section_path": "2 供货范围 Scopes of supply",
                    "source_heading": "2 供货范围 Scopes of supply",
                    "source_section_id": "2",
                    "section_summary": "LCI 供货范围还包含备件、随机工具和板卡。",
                    "section_retrieval_text": "LCI 供货范围 备件 随机工具 板卡",
                    "contextualized_block_text": "LCI 供货范围还包含备件、随机工具和板卡。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "supply_scope",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "LCI 供货范围还包含备件、随机工具和板卡。",
                },
                {
                    "sample_id": "case-b",
                    "file_name": "三鼓风LCI方案.docx",
                    "library_track": "pilot_main",
                    "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
                    "source_section_id": "3.2",
                    "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
                    "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
                    "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                    "reuse_level": "medium",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "lci",
                    "content_form": "narrative",
                    "token_count": 150,
                    "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
                },
            ],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        entries = json.loads(block_path.read_text(encoding="utf-8"))["entries"]
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(entries[0])): 1.02,
                        (semantic_query, _candidate_text(entries[1])): 0.98,
                        (semantic_query, _candidate_text(entries[2])): 0.82,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=4,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.2 启动和同步过程描述 Description of Start-up and Synchronization")
        self.assertEqual(sum(item["source_heading"] == "2 供货范围 Scopes of supply" for item in results), 1)
        self.assertEqual(len(results), 2)

    def test_retrieve_blocks_filters_non_positive_tail_after_penalties(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "source_section_id": "3.1",
            "section_summary": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "section_retrieval_text": "LCI SFC 变频软起系统单线图 Single line Diagram",
            "contextualized_block_text": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "LCI 单线图给出变压器、断路器和主回路连接关系。",
        }
        negative_tail_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "source_heading": "4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "source_section_id": "4.1.4",
            "section_summary": "变频器外形图说明柜体尺寸和安装边界。",
            "section_retrieval_text": "变频器外形图 柜体尺寸 安装边界",
            "contextualized_block_text": "变频器外形图说明柜体尺寸和安装边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "vfd",
            "content_form": "narrative",
            "token_count": 120,
            "content": "变频器外形图说明柜体尺寸和安装边界。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, negative_tail_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 单线图"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(negative_tail_entry)): 0.37,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 单线图",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_heading"], "3.1 变频软起系统单线图 Single line Diagram")
        self.assertGreater(results[0]["score"], 0)

    def test_retrieve_blocks_filters_low_positive_specific_tail_noise_only_below_threshold(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_section_id": "3.2",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        high_tail_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC",
            "section_path": "3 系统方案 System Solution > 3.3 LCI 变频启动特性 LCI Start-up Characteristic > 3.3.2 变频启动曲线 Start curve by SFC",
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "source_section_id": "3.3.2",
            "section_summary": "LCI 启动曲线给出加速平台、转速变化和并网前关键节点。",
            "section_retrieval_text": "3.3.2 变频启动曲线 Start curve by SFC",
            "contextualized_block_text": "启动曲线说明升速过程和同步前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频启动曲线展示 SFC 启动特性、升速过程和同步前的关键节点。",
        }
        low_tail_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "9 售后服务",
            "section_path": "9 售后服务",
            "source_heading": "9 售后服务",
            "source_section_id": "9",
            "section_summary": "项目提供培训、维保和巡检服务。",
            "section_retrieval_text": "售后服务 培训 维保 巡检",
            "contextualized_block_text": "项目提供培训、维保和巡检服务。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "service_support",
            "equipment_type": "generic",
            "content_form": "narrative",
            "token_count": 90,
            "content": "项目提供培训、维保和巡检服务。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, high_tail_entry, low_tail_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(high_tail_entry)): 0.81,
                        (semantic_query, _candidate_text(low_tail_entry)): 0.41,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=5,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.2 启动和同步过程描述 Description of Start-up and Synchronization")
        self.assertTrue(any(item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC" for item in results))
        self.assertFalse(any(item["source_heading"] == "9 售后服务" for item in results))

    def test_retrieve_blocks_demotes_tail_figure_noise_after_specific_figure_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "section_summary": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "section_retrieval_text": "LCI SFC 变频软起系统单线图 Single line Diagram",
            "contextualized_block_text": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "LCI 单线图给出变压器、断路器和主回路连接关系。",
        }
        figure_noise_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "1. **总布置图 General Arrangement Drawing**",
            "section_path": "1. **总布置图 General Arrangement Drawing**",
            "source_heading": "1. **总布置图 General Arrangement Drawing**",
            "section_summary": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "section_retrieval_text": "LCI 总布置图 柜体位置 主回路连接 电缆走向",
            "contextualized_block_text": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "总布置图说明柜体位置、主回路连接和电缆走向。",
        }
        neutral_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "5 控制选项与同步判据",
            "section_path": "5 控制选项与同步判据",
            "source_heading": "5 控制选项与同步判据",
            "section_summary": "控制选项说明保护策略、联锁和同步判据。",
            "section_retrieval_text": "控制选项 保护策略 联锁 同步判据",
            "contextualized_block_text": "控制选项说明保护策略、联锁和同步判据。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 110,
            "content": "控制选项说明保护策略、联锁和同步判据。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, figure_noise_entry, neutral_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 单线图"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.80,
                        (semantic_query, _candidate_text(figure_noise_entry)): 0.78,
                        (semantic_query, _candidate_text(neutral_entry)): 0.77,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 单线图",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.1 变频软起系统单线图 Single line Diagram")
        self.assertTrue(all(item["source_heading"] != "1. **总布置图 General Arrangement Drawing**" for item in results[:2]))
        figure_noise_results = [item for item in results if item["source_heading"] == "1. **总布置图 General Arrangement Drawing**"]
        if figure_noise_results:
            self.assertIn("specific_hit_tail_noise_penalty", figure_noise_results[0]["reason"])

    def test_retrieve_blocks_demotes_support_and_figure_tail_noise_after_specific_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_noise_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含励磁柜、控制板卡和辅助设备。",
        }
        figure_noise_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "1. **总布置图 General Arrangement Drawing**",
            "section_path": "1. **总布置图 General Arrangement Drawing**",
            "source_heading": "1. **总布置图 General Arrangement Drawing**",
            "section_summary": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "section_retrieval_text": "LCI 总布置图 柜体位置 主回路连接 电缆走向",
            "contextualized_block_text": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "总布置图说明柜体位置、主回路连接和电缆走向。",
        }
        neutral_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "source_heading": "4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_summary": "变频器配置说明保护、选项和运行参数边界。",
            "section_retrieval_text": "变频器配置 保护 选项 运行参数边界",
            "contextualized_block_text": "变频器配置说明保护、选项和运行参数边界。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频器配置说明保护、选项和运行参数边界。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_noise_entry, figure_noise_entry, neutral_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_noise_entry)): 0.80,
                        (semantic_query, _candidate_text(figure_noise_entry)): 0.80,
                        (semantic_query, _candidate_text(neutral_entry)): 0.79,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=4,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        technical_results = [
            item for item in results[1:] if item["source_heading"] == "4.1.2 变频器已配置的选项 Converter Selected Options"
        ]
        if technical_results:
            self.assertEqual(results[1]["source_heading"], "4.1.2 变频器已配置的选项 Converter Selected Options")
        else:
            self.assertEqual(len(results), 1)
        for item in results[1:]:
            self.assertIn("specific_hit_tail_noise_penalty", item["reason"])

    def test_retrieve_blocks_caps_nonrequested_support_tail_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明启动、同步与启动特性相关内容。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, broad_parent_entry, support_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(broad_parent_entry)): 0.79,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.2 启动和同步过程描述 Description of Start-up and Synchronization")
        support_results = [item for item in results[1:] if item["source_heading"] == "2 供货范围 Scopes of supply"]
        self.assertEqual(len(support_results), 1)
        self.assertIn("direct_hit_support_tail_score_cap", support_results[0]["reason"])
        self.assertLessEqual(float(support_results[0]["score"]), 0.18)

    def test_retrieve_blocks_caps_fragment_support_tail_below_supply_scope_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        supply_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        spare_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "6 备件清单 Spare Parts > 1. 每一种控制板卡需要提供至少一块备件；",
            "section_path": "6 备件清单 Spare Parts > 1. 每一种控制板卡需要提供至少一块备件；",
            "source_heading": "1. 每一种控制板卡需要提供至少一块备件；",
            "section_summary": "LCI 同步电机控制板卡每一种都需要提供至少一块备件，用于后续维护支持。",
            "section_retrieval_text": "LCI 同步电机 控制板卡 备件清单 备件 维护支持",
            "contextualized_block_text": "LCI 同步电机控制板卡每一种都需要提供至少一块备件，用于维护和后续更换。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "bom_or_supply_list",
            "equipment_type": "switchgear",
            "content_form": "narrative",
            "token_count": 80,
            "content": "LCI 同步电机控制板卡每一种都需要提供至少一块备件，用于维护和后续更换。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, supply_entry, spare_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(supply_entry)): 1.02,
                        (semantic_query, _candidate_text(spare_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        supply_results = [item for item in results[1:] if item["source_heading"] == "2 供货范围 Scopes of supply"]
        self.assertEqual(len(supply_results), 1)
        self.assertIn("direct_hit_support_tail_score_cap", supply_results[0]["reason"])
        self.assertLessEqual(float(supply_results[0]["score"]), 0.18)

        spare_results = [item for item in results[1:] if item["source_heading"] == "1. 每一种控制板卡需要提供至少一块备件；"]
        if spare_results:
            self.assertIn("equipment_type_mismatch_penalty", spare_results[0]["reason"])
            self.assertIn("direct_hit_support_tail_score_cap", spare_results[0]["reason"])
            self.assertLessEqual(float(spare_results[0]["score"]), 0.10)
            self.assertLess(float(spare_results[0]["score"]), float(supply_results[0]["score"]))
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_caps_nonrequested_document_delivery_tail_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        doc_delivery_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "7 启动同步资料提供及数量",
            "section_path": "7 启动同步资料提供及数量",
            "source_heading": "7 启动同步资料提供及数量",
            "section_summary": "卖方应提交启动同步图纸、技术资料和随机文件。",
            "section_retrieval_text": "LCI 启动同步资料提供及数量 提交资料 技术资料 交付文档",
            "contextualized_block_text": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "commercial_manual_only",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 110,
            "content": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, doc_delivery_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(doc_delivery_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "7 启动同步资料提供及数量")
        self.assertIn("non_requested_support_section_penalty", results[1]["reason"])
        self.assertIn("specific_hit_tail_noise_penalty", results[1]["reason"])
        self.assertIn("query_intent_heading_overlap", results[1]["reason"])
        self.assertIn("direct_hit_support_tail_score_cap", results[1]["reason"])
        self.assertLessEqual(float(results[1]["score"]), 0.28)

    def test_retrieve_blocks_filters_generic_support_tail_when_title_aligned_wrong_leaf_exists(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertFalse(any(item["source_heading"] == "2 供货范围 Scopes of supply" for item in results))
        wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if wrong_leaf_results:
            self.assertIn("title_aligned_wrong_leaf_penalty", wrong_leaf_results[0]["reason"])

    def test_retrieve_blocks_keeps_supply_scope_when_query_explicitly_requests_it(self) -> None:
        supply_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[supply_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 供货范围"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(supply_entry)): 0.82,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 供货范围",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 供货范围",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "2 供货范围 Scopes of supply")
        self.assertNotIn("non_requested_support_section_penalty", results[0]["reason"])
        wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if wrong_leaf_results:
            self.assertLess(results.index(results[0]), results.index(wrong_leaf_results[0]))

    def test_retrieve_blocks_keeps_document_delivery_when_query_explicitly_requests_it(self) -> None:
        doc_delivery_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "7 启动同步资料提供及数量",
            "section_path": "7 启动同步资料提供及数量",
            "source_heading": "7 启动同步资料提供及数量",
            "section_summary": "卖方应提交启动同步图纸、技术资料和随机文件。",
            "section_retrieval_text": "LCI 启动同步资料提供及数量 提交资料 技术资料 交付文档",
            "contextualized_block_text": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "commercial_manual_only",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 110,
            "content": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[doc_delivery_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 启动同步资料"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(doc_delivery_entry)): 0.82,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步资料",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 启动同步资料提供及数量",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "7 启动同步资料提供及数量")
        self.assertNotIn("non_requested_support_section_penalty", results[0]["reason"])
        wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if wrong_leaf_results:
            self.assertLess(results.index(results[0]), results.index(wrong_leaf_results[0]))

    def test_retrieve_blocks_gates_technical_load_data_for_document_delivery_query(self) -> None:
        doc_delivery_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "7 提交资料",
            "section_path": "7 提交资料",
            "source_heading": "7 提交资料",
            "section_summary": "卖方应提交设计图纸、操作维护手册、测试报告和合格证。",
            "section_retrieval_text": "提交资料 交付资料 随机资料 技术资料 文档清单 操作维护手册 测试报告 合格证",
            "contextualized_block_text": "提交资料包括设备配置、主要技术数据表、电气接线图、操作维护手册、出厂试验报告和合格证。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "commercial_manual_only",
            "equipment_type": "generic",
            "content_form": "bom_table",
            "token_count": 130,
            "content": "| 序号 | 说明 | 提供时间 |\n| 1 | 操作维护手册 | 随机资料 |\n| 2 | 出厂试验报告 | 随机资料 |",
        }
        load_data_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 > 3.3 LCI 变频启动特性 > 3.3.1 负载数据 Load data",
            "section_path": "3 系统方案 > 3.3 LCI 变频启动特性 > 3.3.1 负载数据 Load data",
            "source_heading": "3.3.1 负载数据 Load data",
            "section_summary": "风机启动特性基于转动惯量、起动阻力矩和静阻力矩。",
            "section_retrieval_text": "LCI SFC 变频启动特性 负载数据 Load data 转动惯量 起动阻力矩",
            "contextualized_block_text": "转动惯量 J=18695 kg.m2，起动阻力矩 57000 N.m。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "starter_spec",
            "equipment_type": "lci",
            "content_form": "parameter_table",
            "token_count": 120,
            "content": "转动惯量：J=18695 kg.m2。起动阻力矩：空载 57000 N.m。静阻力矩：23500 N.m。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[doc_delivery_entry, load_data_entry],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                section_scope_semantic_enabled=False,
                section_scope_rerank_enabled=False,
            )
            results = service.retrieve_blocks(
                query=(
                    "项目交付资料与文档清单 交付文档 技术图纸 操作手册 测试报告 资料归档 "
                    "table parameter LCI 变频软起方案族 宝山钢铁股份有限公司三鼓风LCI改造方案.docx 控制柜 PLC"
                ),
                top_k=4,
                sample_ids={"case-b"},
                section_title="项目交付资料与文档清单",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "7 提交资料")
        self.assertFalse(any(item["source_heading"] == "3.3.1 负载数据 Load data" for item in results))

    def test_retrieve_sections_prefers_delivery_section_for_polluted_document_query(self) -> None:
        outline_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "section_catalog": [
                {
                    "section_id": "3.3",
                    "title": "3.3 LCI 变频启动特性",
                    "source_heading": "3.3 LCI 变频启动特性",
                    "section_path": "3 系统方案 > 3.3 LCI 变频启动特性",
                    "heading_path": "3 系统方案 > 3.3 LCI 变频启动特性",
                    "section_summary": "风机启动特性、负载数据和启动曲线。",
                    "section_retrieval_text": "LCI SFC 软起 变频启动 负载数据 启动曲线",
                    "level": 2,
                    "source_signals": ["toc", "parser_heading"],
                },
                {
                    "section_id": "7",
                    "title": "7 提交资料",
                    "source_heading": "7 提交资料",
                    "section_path": "7 提交资料",
                    "heading_path": "7 提交资料",
                    "section_summary": "提交设计图纸、操作维护手册、测试报告、合格证和随机资料。",
                    "section_retrieval_text": "提交资料 交付资料 文档清单 技术资料 操作维护手册 测试报告 合格证 随机资料",
                    "level": 1,
                    "source_signals": ["toc", "parser_heading"],
                },
            ],
        }
        outline_path, block_path, temp_dir = self._write_library(outline_entries=[outline_entry], block_entries=[])
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                section_scope_semantic_enabled=False,
                section_scope_rerank_enabled=False,
            )
            results = service.retrieve_sections(
                query=(
                    "项目交付资料与文档清单 交付文档 技术图纸 操作手册 测试报告 资料归档 "
                    "table parameter LCI 变频软起方案族 宝山钢铁股份有限公司三鼓风LCI改造方案.docx 控制柜 PLC"
                ),
                top_k=4,
                sample_ids={"case-b"},
                section_title="项目交付资料与文档清单",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["section_id"], "7")
        self.assertFalse(any(item["section_id"] == "3.3" for item in results))

    def test_retrieve_blocks_multi_query_regression_matrix_for_lci_bundle(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        wrong_leaf_curve_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        supply_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        doc_delivery_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "7 启动同步资料提供及数量",
            "section_path": "7 启动同步资料提供及数量",
            "source_heading": "7 启动同步资料提供及数量",
            "section_summary": "卖方应提交启动同步图纸、技术资料和随机文件。",
            "section_retrieval_text": "LCI 启动同步资料提供及数量 提交资料 技术资料 交付文档",
            "contextualized_block_text": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "commercial_manual_only",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 110,
            "content": "卖方应提交启动同步图纸、技术资料和随机文件，供设计联络和审查使用。",
        }
        single_line_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "section_summary": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "section_retrieval_text": "LCI SFC 变频软起系统单线图 Single line Diagram",
            "contextualized_block_text": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "LCI 单线图给出变压器、断路器和主回路连接关系。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明启动、同步和启动特性。",
        }
        technical_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "source_heading": "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_summary": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器遵循的标准和证书 Converter Standard and Certification",
            "contextualized_block_text": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 125,
            "content": "Converter standard and certification, IEC compliance and type test records.",
        }
        selected_options_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "source_heading": "4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_summary": "变频器配置说明保护、选项和运行参数边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器已配置的选项 Converter Selected Options",
            "contextualized_block_text": "变频器配置说明保护、选项和运行参数边界。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 130,
            "content": "Degree of protection IP31. Cabinet color RAL7032. Converter selected options.",
        }
        outline_drawing_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "source_heading": "4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "section_summary": "变频器外形图说明柜体尺寸和安装边界。",
            "section_retrieval_text": "变频器外形图 柜体尺寸 安装边界 变频器配置",
            "contextualized_block_text": "变频器外形图说明柜体尺寸和安装边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "narrative",
            "token_count": 120,
            "content": "变频器外形图说明柜体尺寸和安装边界。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                primary_entry,
                wrong_leaf_curve_entry,
                supply_entry,
                doc_delivery_entry,
                single_line_entry,
                broad_parent_entry,
                technical_entry,
                selected_options_entry,
                outline_drawing_entry,
            ],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        queries = [
            {
                "query": "LCI 启动同步",
                "section_title": "LCI 变频启动特性",
                "top_k": 5,
                "expected_first": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            },
            {
                "query": "LCI 供货范围",
                "section_title": "LCI 供货范围",
                "top_k": 5,
                "expected_first": "2 供货范围 Scopes of supply",
                "forbid_reason": "non_requested_support_section_penalty",
            },
            {
                "query": "LCI 启动同步资料",
                "section_title": "LCI 启动同步资料提供及数量",
                "top_k": 5,
                "expected_first": "7 启动同步资料提供及数量",
                "forbid_reason": "non_requested_support_section_penalty",
            },
            {
                "query": "LCI 单线图",
                "section_title": "LCI 变频启动特性",
                "top_k": 5,
                "expected_first": "3.1 变频软起系统单线图 Single line Diagram",
            },
            {
                "query": "LCI 启动曲线",
                "section_title": "LCI 变频启动特性",
                "top_k": 5,
                "expected_first": "3.3.2 变频启动曲线 Start curve by SFC",
            },
            {
                "query": "LCI 标准和证书",
                "section_title": "LCI 变频器配置",
                "top_k": 5,
                "expected_first": "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            },
            {
                "query": "LCI 已配置的选项",
                "section_title": "LCI 变频器配置",
                "top_k": 5,
                "expected_first": "4.1.2 变频器已配置的选项 Converter Selected Options",
            },
            {
                "query": "LCI 外形图",
                "section_title": "LCI 变频器配置",
                "top_k": 5,
                "expected_first": "4.1.4 变频器外形图 Converter Typical Outline Drawing",
            },
        ]

        semantic_scores: dict[tuple[str, str], float] = {}
        entries = [
            primary_entry,
            wrong_leaf_curve_entry,
            supply_entry,
            doc_delivery_entry,
            single_line_entry,
            broad_parent_entry,
            technical_entry,
            selected_options_entry,
            outline_drawing_entry,
        ]
        score_matrix = {
            ("LCI 变频启动特性\nLCI 启动同步", primary_entry["source_heading"]): 0.82,
            ("LCI 变频启动特性\nLCI 启动同步", wrong_leaf_curve_entry["source_heading"]): 1.05,
            ("LCI 变频启动特性\nLCI 启动同步", supply_entry["source_heading"]): 1.02,
            ("LCI 变频启动特性\nLCI 启动同步", doc_delivery_entry["source_heading"]): 0.96,
            ("LCI 变频启动特性\nLCI 启动同步", single_line_entry["source_heading"]): 1.05,
            ("LCI 变频启动特性\nLCI 启动同步", broad_parent_entry["source_heading"]): 0.90,
            ("LCI 变频启动特性\nLCI 启动同步", technical_entry["source_heading"]): 1.05,
            ("LCI 变频启动特性\nLCI 启动同步", selected_options_entry["source_heading"]): 1.05,
            ("LCI 变频启动特性\nLCI 启动同步", outline_drawing_entry["source_heading"]): 1.05,
            ("LCI 供货范围", supply_entry["source_heading"]): 0.82,
            ("LCI 供货范围", wrong_leaf_curve_entry["source_heading"]): 1.02,
            ("LCI 启动同步资料", doc_delivery_entry["source_heading"]): 0.82,
            ("LCI 启动同步资料", wrong_leaf_curve_entry["source_heading"]): 1.02,
            ("LCI 变频启动特性\nLCI 单线图", single_line_entry["source_heading"]): 0.84,
            ("LCI 变频启动特性\nLCI 单线图", wrong_leaf_curve_entry["source_heading"]): 0.78,
            ("LCI 变频启动特性\nLCI 单线图", broad_parent_entry["source_heading"]): 0.79,
            ("LCI 变频启动特性\nLCI 启动曲线", wrong_leaf_curve_entry["source_heading"]): 0.84,
            ("LCI 变频启动特性\nLCI 启动曲线", primary_entry["source_heading"]): 0.80,
            ("LCI 变频启动特性\nLCI 启动曲线", single_line_entry["source_heading"]): 0.78,
            ("LCI 变频器配置\nLCI 标准和证书", technical_entry["source_heading"]): 0.86,
            ("LCI 变频器配置\nLCI 标准和证书", selected_options_entry["source_heading"]): 0.74,
            ("LCI 变频器配置\nLCI 已配置的选项", selected_options_entry["source_heading"]): 0.86,
            ("LCI 变频器配置\nLCI 已配置的选项", technical_entry["source_heading"]): 0.74,
            ("LCI 变频器配置\nLCI 外形图", outline_drawing_entry["source_heading"]): 0.86,
            ("LCI 变频器配置\nLCI 外形图", single_line_entry["source_heading"]): 0.72,
        }
        for (semantic_query, heading), score in score_matrix.items():
            entry = next(item for item in entries if item["source_heading"] == heading)
            semantic_scores[(semantic_query, _candidate_text(entry))] = score

        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores=semantic_scores),
            )
            for scenario in queries:
                with self.subTest(query=scenario["query"]):
                    results = service.retrieve_blocks(
                        query=scenario["query"],
                        top_k=scenario["top_k"],
                        sample_ids={"case-b"},
                        section_title=scenario["section_title"],
                    )
                    if "expected_first" in scenario:
                        self.assertTrue(results)
                        self.assertEqual(results[0]["source_heading"], scenario["expected_first"])
                    if "forbid_reason" in scenario:
                        self.assertTrue(results)
                        self.assertNotIn(scenario["forbid_reason"], results[0]["reason"])
        finally:
            temp_dir.cleanup()

    def test_retrieve_blocks_caps_pure_figure_sidecar_below_support_tail_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        figure_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "1. **总布置图 General Arrangement Drawing**",
            "section_path": "1. **总布置图 General Arrangement Drawing**",
            "source_heading": "1. **总布置图 General Arrangement Drawing**",
            "section_summary": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "section_retrieval_text": "LCI 总布置图 柜体位置 主回路连接 电缆走向",
            "contextualized_block_text": "总布置图说明柜体位置、主回路连接和电缆走向。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "总布置图说明柜体位置、主回路连接和电缆走向。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, figure_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(figure_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        self.assertIn("direct_hit_support_tail_score_cap", results[1]["reason"])
        figure_results = [item for item in results[2:] if item["source_heading"] == "1. **总布置图 General Arrangement Drawing**"]
        if figure_results:
            self.assertIn("direct_hit_context_sidecar_score_cap", figure_results[0]["reason"])
            self.assertLessEqual(float(figure_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_caps_pure_technical_sidecar_below_support_tail_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        technical_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "source_heading": "4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_summary": "变频器配置说明保护、选项和运行参数边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器已配置的选项 Converter Selected Options",
            "contextualized_block_text": "变频器配置说明保护、选项和运行参数边界。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 130,
            "content": "Degree of protection IP31. Cabinet color RAL7032. Converter selected options.",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, technical_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(technical_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        technical_results = [
            item for item in results[2:] if item["source_heading"] == "4.1.2 变频器已配置的选项 Converter Selected Options"
        ]
        if technical_results:
            self.assertIn("technical_detail_sidecar_penalty", technical_results[0]["reason"])
            self.assertIn("direct_hit_context_sidecar_score_cap", technical_results[0]["reason"])
            self.assertLessEqual(float(technical_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_caps_non_reuse_technical_sidecar_below_support_tail_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        technical_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "source_heading": "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification",
            "section_summary": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器遵循的标准和证书 Converter Standard and Certification",
            "contextualized_block_text": "变频器标准和证书说明设备遵循的 IEC 标准、型式试验和认证边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 125,
            "content": "Converter standard and certification, IEC compliance and type test records.",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, technical_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(technical_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        technical_results = [
            item
            for item in results[2:]
            if item["source_heading"] == "4.1.3 变频器遵循的标准和证书 Converter Standard and Certification"
        ]
        if technical_results:
            self.assertIn("technical_detail_sidecar_penalty", technical_results[0]["reason"])
            self.assertIn("direct_hit_context_sidecar_score_cap", technical_results[0]["reason"])
            self.assertLessEqual(float(technical_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_filters_technical_figure_sidecar_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        technical_figure_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "source_heading": "4.1.4 变频器外形图 Converter Typical Outline Drawing",
            "section_summary": "变频器外形图说明柜体尺寸和安装边界。",
            "section_retrieval_text": "变频器外形图 柜体尺寸 安装边界 变频器配置",
            "contextualized_block_text": "变频器外形图说明柜体尺寸和安装边界。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "narrative",
            "token_count": 120,
            "content": "变频器外形图说明柜体尺寸和安装边界。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, technical_figure_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(technical_figure_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        technical_figure_results = [
            item for item in results[2:] if item["source_heading"] == "4.1.4 变频器外形图 Converter Typical Outline Drawing"
        ]
        if technical_figure_results:
            self.assertIn("technical_detail_sidecar_penalty", technical_figure_results[0]["reason"])
            self.assertIn("direct_hit_context_sidecar_score_cap", technical_figure_results[0]["reason"])
            self.assertLessEqual(float(technical_figure_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_filters_context_only_figure_wrong_leaf_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        support_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "2 供货范围 Scopes of supply",
            "section_path": "2 供货范围 Scopes of supply",
            "source_heading": "2 供货范围 Scopes of supply",
            "section_summary": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "section_retrieval_text": "LCI 供货范围 同步电机 控制板卡 辅助设备",
            "contextualized_block_text": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "supply_scope",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 供货范围包含同步电机接口柜、控制板卡和辅助设备。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "section_summary": "LCI 单线图展示主回路、变压器和柜体接口。",
            "section_retrieval_text": "LCI 单线图 Single line Diagram 变频软起系统",
            "contextualized_block_text": "单线图展示 LCI 主回路、隔离开关和变压器接口。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "switchgear",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 单线图给出主回路、变压器和并网接口。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, support_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(support_entry)): 1.02,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 1.05,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[1]["source_heading"], "2 供货范围 Scopes of supply")
        wrong_leaf_results = [item for item in results[2:] if item["source_heading"] == "3.1 变频软起系统单线图 Single line Diagram"]
        if wrong_leaf_results:
            self.assertIn("direct_hit_context_sidecar_score_cap", wrong_leaf_results[0]["reason"])
            self.assertLessEqual(float(wrong_leaf_results[0]["score"]), 0.10)
        else:
            self.assertEqual(len(results), 2)

    def test_retrieve_blocks_demotes_title_aligned_wrong_leaf_after_specific_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        neutral_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "source_heading": "4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_summary": "变频器配置说明保护、选项和运行参数边界。",
            "section_retrieval_text": "变频器配置 保护 选项 运行参数边界",
            "contextualized_block_text": "变频器配置说明保护、选项和运行参数边界。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频器配置说明保护、选项和运行参数边界。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, wrong_leaf_entry, neutral_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 0.81,
                        (semantic_query, _candidate_text(neutral_entry)): 0.79,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        technical_results = [
            item for item in results[1:] if item["source_heading"] == "4.1.2 变频器已配置的选项 Converter Selected Options"
        ]
        wrong_leaf_results = [item for item in results[2:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if not wrong_leaf_results:
            wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if technical_results:
            self.assertEqual(results[1]["source_heading"], "4.1.2 变频器已配置的选项 Converter Selected Options")
        if wrong_leaf_results:
            self.assertIn("specific_hit_tail_noise_penalty", wrong_leaf_results[0]["reason"])
            self.assertIn("context_only_sidecar_penalty", wrong_leaf_results[0]["reason"])
            if technical_results:
                self.assertLess(results.index(technical_results[0]), results.index(wrong_leaf_results[0]))

    def test_retrieve_blocks_demotes_wrong_sibling_leaf_below_broad_parent_after_single_line_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "section_summary": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "section_retrieval_text": "LCI SFC 变频软起系统单线图 Single line Diagram",
            "contextualized_block_text": "LCI 单线图给出变压器、断路器和主回路连接关系。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "LCI 单线图给出变压器、断路器和主回路连接关系。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.1 变频软起系统单线图 Single line Diagram "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总单线图、启动和同步等内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明单线图、启动、同步和启动特性。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "风机启动曲线如下，给出加速平台与并网前转速变化。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线给出加速平台与并网前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "风机启动曲线如下，给出加速平台与并网前转速变化。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, broad_parent_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 单线图"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.80,
                        (semantic_query, _candidate_text(broad_parent_entry)): 0.79,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 0.82,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 单线图",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.1 变频软起系统单线图 Single line Diagram")
        broad_parent_results = [item for item in results[1:] if item["source_heading"] == "3 系统方案 System Solution"]
        wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if broad_parent_results:
            self.assertIn("ancestor_specific_hit_penalty", broad_parent_results[0]["reason"])
        if wrong_leaf_results:
            self.assertIn("title_aligned_wrong_leaf_penalty", wrong_leaf_results[0]["reason"])
        if broad_parent_results and wrong_leaf_results:
            self.assertLess(results.index(broad_parent_results[0]), results.index(wrong_leaf_results[0]))

    def test_retrieve_blocks_demotes_broad_parent_below_clean_secondary_descendant_after_curve_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "LCI 启动曲线给出加速平台、转速变化和并网前关键节点。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线描述励磁建立后的升速曲线和同步前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频启动曲线展示 SFC 启动特性、升速过程和同步前的关键节点。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、负载数据以及启动曲线等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.3.1 负载数据 Load data "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总负载数据、启动曲线与同步过程。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明负载数据、启动曲线和同步过程。",
        }
        secondary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.1 负载数据 Load data"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.1 负载数据 Load data"
            ),
            "source_heading": "3.3.1 负载数据 Load data",
            "section_summary": "负载数据用于计算 LCI 启动曲线和启动过程中的转矩需求。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.1 负载数据 Load data"
            ),
            "contextualized_block_text": "负载数据给出转动惯量、负载转矩，并支撑启动曲线计算。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 140,
            "content": "负载数据用于计算 LCI 启动曲线与加速过程中的转矩和功率。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, broad_parent_entry, secondary_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动曲线"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.84,
                        (semantic_query, _candidate_text(broad_parent_entry)): 0.88,
                        (semantic_query, _candidate_text(secondary_entry)): 0.74,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动曲线",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.3.2 变频启动曲线 Start curve by SFC")
        self.assertEqual(results[1]["source_heading"], "3.3.1 负载数据 Load data")
        broad_parent_results = [item for item in results[2:] if item["source_heading"] == "3 系统方案 System Solution"]
        if broad_parent_results:
            self.assertIn("broad_parent_direct_leaf_redundancy_penalty", broad_parent_results[0]["reason"])
            self.assertIn("broad_parent_secondary_context_penalty", broad_parent_results[0]["reason"])

    def test_retrieve_blocks_demotes_broad_parent_below_sidecar_after_direct_single_line_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "section_path": "3 系统方案 System Solution > 3.1 变频软起系统单线图 Single line Diagram",
            "source_heading": "3.1 变频软起系统单线图 Single line Diagram",
            "section_summary": "LCI 单线图展示主回路、变压器和柜体接口。",
            "section_retrieval_text": "3.1 变频软起系统单线图 Single line Diagram",
            "contextualized_block_text": "单线图展示 LCI 主回路、隔离开关和变压器接口。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 120,
            "content": "LCI 单线图给出主回路、变压器和并网接口。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总单线图、启动曲线与同步过程。",
            "section_retrieval_text": "3 系统方案 System Solution 3.1 单线图 3.3.2 启动曲线",
            "contextualized_block_text": "LCI 系统方案总览，汇总单线图、启动曲线与同步过程。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明单线图、启动曲线和同步过程。",
        }
        sidecar_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "1. **总布置图 General Arrangement Drawing**",
            "section_path": "1. **总布置图 General Arrangement Drawing**",
            "source_heading": "1. **总布置图 General Arrangement Drawing**",
            "section_summary": "LCI 总布置图展示设备排布和柜列接口位置。",
            "section_retrieval_text": "总布置图 General Arrangement Drawing LCI",
            "contextualized_block_text": "总布置图展示 LCI 柜列、变压器和现场接口位置。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 100,
            "content": "LCI 总布置图展示设备排布和接口位置。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, broad_parent_entry, sidecar_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 单线图"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.84,
                        (semantic_query, _candidate_text(broad_parent_entry)): 0.88,
                        (semantic_query, _candidate_text(sidecar_entry)): 0.92,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 单线图",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.1 变频软起系统单线图 Single line Diagram")
        self.assertEqual(results[1]["source_heading"], "1. **总布置图 General Arrangement Drawing**")
        broad_parent_results = [item for item in results[2:] if item["source_heading"] == "3 系统方案 System Solution"]
        if broad_parent_results:
            self.assertIn("broad_parent_direct_leaf_redundancy_penalty", broad_parent_results[0]["reason"])

    def test_retrieve_blocks_demotes_detached_fragment_below_structured_block_after_curve_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "LCI 启动曲线给出加速平台、转速变化和并网前关键节点。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线描述励磁建立后的升速曲线和同步前转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频启动曲线展示 SFC 启动特性、升速过程和同步前的关键节点。",
        }
        structured_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization"
            ),
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "并网前通过 Synchro-tact 完成同步条件判定与切换。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 160,
            "content": "LCI 启动后通过同步装置完成与电网的同步切换。",
        }
        fragment_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "Total starting up time is 130 s , including:",
            "section_path": "Total starting up time is 130 s , including:",
            "source_heading": "Total starting up time is 130 s , including:",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "starter_spec",
            "equipment_type": "soft_starter",
            "content_form": "narrative",
            "token_count": 45,
            "content": (
                "LCI 启动曲线 total starting up time is 130 s, including the pure acceleration time "
                "specified in the torque-speed start curve and synchronization preparation."
            ),
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, structured_entry, fragment_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动曲线"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.84,
                        (semantic_query, _candidate_text(structured_entry)): 0.42,
                        (semantic_query, _candidate_text(fragment_entry)): 0.88,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动曲线",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["source_heading"], "3.3.2 变频启动曲线 Start curve by SFC")
        self.assertEqual(
            results[1]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        self.assertEqual(results[2]["source_heading"], "Total starting up time is 130 s , including:")
        self.assertIn("detached_fragment_sidecar_penalty", results[2]["reason"])

    def test_retrieve_blocks_demotes_technical_detail_sidecar_after_specific_process_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        broad_parent_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution",
            "section_path": "3 系统方案 System Solution",
            "source_heading": "3 系统方案 System Solution",
            "section_summary": "系统方案章节汇总启动和同步、单线图以及启动特性等内容。",
            "section_retrieval_text": (
                "3 系统方案 System Solution "
                "3.2 启动和同步过程描述 Description of Start-up and Synchronization "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic"
            ),
            "contextualized_block_text": "LCI 系统方案总览，汇总启动、同步与启动特性相关内容。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 180,
            "content": "系统方案总览说明启动、同步和启动特性。",
        }
        technical_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_path": "4 变频器技术数据 Component Technical Data > 4.1 变频器配置 Converter Configuration > 4.1.2 变频器已配置的选项 Converter Selected Options",
            "source_heading": "4.1.2 变频器已配置的选项 Converter Selected Options",
            "section_summary": "变频器配置说明保护、选项和运行参数边界。",
            "section_retrieval_text": "变频器技术数据 Component Technical Data 变频器配置 Converter Configuration 变频器已配置的选项 Converter Selected Options",
            "contextualized_block_text": "变频器配置说明保护、选项和运行参数边界。",
            "reuse_level": "high",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "vfd_spec",
            "equipment_type": "vfd",
            "content_form": "formula",
            "token_count": 130,
            "content": "Degree of protection IP31. Cabinet color RAL7032. Converter selected options.",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, broad_parent_entry, technical_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.80,
                        (semantic_query, _candidate_text(broad_parent_entry)): 0.86,
                        (semantic_query, _candidate_text(technical_entry)): 0.90,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=3,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        technical_results = [item for item in results if item["source_heading"] == "4.1.2 变频器已配置的选项 Converter Selected Options"]
        if technical_results:
            self.assertIn("technical_detail_sidecar_penalty", technical_results[0]["reason"])
            self.assertIn("context_only_sidecar_penalty", technical_results[0]["reason"])
            self.assertNotEqual(results[0]["source_heading"], "4.1.2 变频器已配置的选项 Converter Selected Options")
        else:
            self.assertEqual(len(results), 1)

    def test_retrieve_blocks_caps_context_only_wrong_leaf_after_direct_hit(self) -> None:
        primary_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_path": "3 系统方案 System Solution > 3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "source_heading": "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
            "section_summary": "LCI 启动过程包括励磁建立、升速和并网同步。",
            "section_retrieval_text": "LCI SFC 启动和同步过程描述 Description of Start-up and Synchronization",
            "contextualized_block_text": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 150,
            "content": "LCI 系统通过 SFC 完成启动、升速和并网同步。",
        }
        wrong_leaf_entry = {
            "sample_id": "case-b",
            "file_name": "三鼓风LCI方案.docx",
            "library_track": "pilot_main",
            "heading_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "section_path": (
                "3 系统方案 System Solution > "
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic > "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "source_heading": "3.3.2 变频启动曲线 Start curve by SFC",
            "section_summary": "启动曲线给出加速平台、转速变化和关键节点。",
            "section_retrieval_text": (
                "3.3 LCI 变频启动特性 LCI Start-up Characteristic "
                "3.3.2 变频启动曲线 Start curve by SFC"
            ),
            "contextualized_block_text": "启动曲线描述励磁建立后的升速曲线和转速变化。",
            "reuse_level": "medium",
            "content_risk_level": "low",
            "front_matter": False,
            "section_type": "overall_solution",
            "equipment_type": "lci",
            "content_form": "narrative",
            "token_count": 130,
            "content": "变频启动曲线展示 SFC 启动特性、升速过程和关键节点。",
        }
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[primary_entry, wrong_leaf_entry],
        )

        def _candidate_text(entry: dict[str, object]) -> str:
            return "\n".join(
                part
                for part in (
                    str(entry.get("heading_path") or entry.get("section_path") or ""),
                    str(entry.get("section_summary") or ""),
                    str(entry.get("contextualized_block_text") or ""),
                    str(entry.get("content") or "")[:1200],
                )
                if part
            ).strip()

        semantic_query = "LCI 变频启动特性\nLCI 启动同步"
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (semantic_query, _candidate_text(primary_entry)): 0.82,
                        (semantic_query, _candidate_text(wrong_leaf_entry)): 1.02,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="LCI 启动同步",
                top_k=2,
                sample_ids={"case-b"},
                section_title="LCI 变频启动特性",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            results[0]["source_heading"],
            "3.2 启动和同步过程描述 Description of Start-up and Synchronization",
        )
        wrong_leaf_results = [item for item in results[1:] if item["source_heading"] == "3.3.2 变频启动曲线 Start curve by SFC"]
        if wrong_leaf_results:
            self.assertIn("specific_hit_tail_noise_penalty", wrong_leaf_results[0]["reason"])
            self.assertIn("context_only_sidecar_penalty", wrong_leaf_results[0]["reason"])
            self.assertIn("direct_hit_context_sidecar_score_cap", wrong_leaf_results[0]["reason"])
            self.assertLessEqual(float(wrong_leaf_results[0]["score"]), 0.20)
        else:
            self.assertEqual(len(results), 1)

    def test_case_library_service_caches_library_and_term_lexicon(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "top_level_titles": ["上位监控系统（SCADA）架构"],
                    "flat_outline": [{"heading_path": "上位监控系统（SCADA）架构 > 趋势查询"}],
                }
            ],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "2. 上位监控系统趋势查询",
                    "section_path": "2. 上位监控系统趋势查询",
                    "source_section_id": "2",
                    "source_heading": "2. 上位监控系统趋势查询",
                    "normalized_heading": "上位监控系统趋势查询",
                    "heading_aliases": ["上位监控系统趋势查询"],
                    "section_summary": "上位监控系统支持趋势查询、历史归档与报警联动。",
                    "contextualized_block_text": "上位监控系统支持趋势查询、历史归档与报警联动。",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "上位监控系统支持趋势查询、历史归档与报警联动。",
                }
            ],
        )
        try:
            service = CountingCaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            service.retrieve_blocks(query="SCADA 趋势查询", top_k=2, sample_ids={"case-a"}, section_title="SCADA 监控方案")
            service.retrieve_sections(query="SCADA 趋势查询", top_k=2, sample_ids={"case-a"}, section_title="SCADA 监控方案")
        finally:
            temp_dir.cleanup()

        self.assertEqual(service.outline_load_count, 1)
        self.assertEqual(service.block_load_count, 1)

    def test_retrieve_sections_uses_materialized_term_lexicon_without_loading_blocks(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "监控系统方案.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "section_catalog": [
                        {
                            "section_id": "1",
                            "title": "上位监控系统架构",
                            "source_heading": "1 上位监控系统架构",
                            "heading_path": "1 上位监控系统架构",
                            "section_path": "1 上位监控系统架构",
                            "normalized_heading": "上位监控系统架构",
                            "heading_aliases": ["上位监控系统", "系统架构"],
                            "section_summary": "上位监控系统支持趋势监视、历史归档与报警联动。",
                            "section_retrieval_text": "上位监控系统支持趋势监视、历史归档与报警联动。",
                            "source_signals": ["markdown_heading"],
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[],
            outline_term_lexicon={
                "scada": ["scada", "上位监控系统", "上位监控"],
                "上位监控系统": ["scada", "上位监控系统", "上位监控"],
            },
        )
        try:
            service = CountingCaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
            )
            results = service.retrieve_sections(
                query="SCADA 历史归档",
                top_k=2,
                sample_ids={"case-a"},
                section_title="SCADA 系统架构",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "1 上位监控系统架构")
        self.assertEqual(service.outline_load_count, 1)
        self.assertEqual(service.block_load_count, 0)

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

    def test_retrieve_blocks_prefers_persisted_semantic_retrieval_text_when_present(self) -> None:
        semantic_primary = "风机方案A 技术架构 Modbus RS485 PLC 接口协同 控制边界"
        semantic_secondary = "风机方案A 技术架构 模块化设计"
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "风机方案A.docx",
                    "library_track": "pilot_main",
                    "heading_path": "4. 技术架构",
                    "section_path": "第四章 技术架构",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "系统采用分层结构设计，满足可靠性要求。",
                    "semantic_retrieval_text": semantic_primary,
                },
                {
                    "sample_id": "case-a",
                    "file_name": "风机方案A.docx",
                    "library_track": "pilot_main",
                    "heading_path": "4. 技术架构",
                    "section_path": "第四章 技术架构",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 108,
                    "content": "系统采用分层结构设计，满足可靠性要求。",
                    "semantic_retrieval_text": semantic_secondary,
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        ("控制接口与通讯方案\nModbus RS485 PLC 接口", semantic_primary): 0.86,
                        ("控制接口与通讯方案\nModbus RS485 PLC 接口", semantic_secondary): 0.0,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="Modbus RS485 PLC 接口",
                top_k=2,
                sample_ids={"case-a"},
                section_title="控制接口与通讯方案",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["semantic_retrieval_text"], semantic_primary)
        self.assertIn("semantic_high_confidence", results[0]["reason"])

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

    def test_retrieve_section_blocks_returns_whole_source_section_in_source_order(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "source_section_id": "3.2",
                    "section_path": "第三章 总体方案 > 3.2 高压变频系统总体方案 > 3.2.2 控制接口",
                    "heading_path": "第三章 总体方案 > 3.2 高压变频系统总体方案 > 3.2.2 控制接口",
                    "chunk_index": 12,
                    "content": "第二个来源块。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "source_section_id": "3.2",
                    "section_path": "第三章 总体方案 > 3.2 高压变频系统总体方案",
                    "heading_path": "第三章 总体方案 > 3.2 高压变频系统总体方案",
                    "chunk_index": 11,
                    "content": "第一个来源块。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "source_section_id": "4.1",
                    "section_path": "第四章 培训计划",
                    "heading_path": "第四章 培训计划",
                    "chunk_index": 13,
                    "content": "不应进入 3.2。",
                },
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_section_blocks(
                sample_id="case-a",
                file_name="环冷风机方案.docx",
                section_id="3.2",
                section_path="第三章 总体方案 > 3.2 高压变频系统总体方案",
                top_k=10,
                base_score=0.88,
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual([item["content"] for item in results], ["第一个来源块。", "第二个来源块。"])
        self.assertEqual(results[0]["score"], 0.88)
        self.assertIn("full_section_source_block", results[0]["reason"])

    def test_retrieve_blocks_auto_scopes_to_specific_section_candidates(self) -> None:
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
                            "section_summary": "系统及方案介绍总览。",
                            "section_retrieval_text": "系统及方案介绍 总览",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [
                                {
                                    "section_id": "3.2.4",
                                    "title": "2.4 控制信号接口说明",
                                    "source_heading": "2.4 控制信号接口说明",
                                    "normalized_heading": "控制信号接口说明",
                                    "heading_aliases": ["控制信号接口说明", "接口说明"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                                    "heading_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                                    "normalized_section_path": "系统及方案介绍 > 系统方案 > 控制信号接口说明",
                                    "section_summary": "DCS 至变频器提供 DI/DO、AI/AO 和 Modbus/RS485 接口。",
                                    "section_retrieval_text": "控制信号接口说明 DCS PLC Modbus RS485 DI DO AI AO",
                                    "source_signals": ["toc", "parser_heading"],
                                    "children": [],
                                },
                                {
                                    "section_id": "3.3.1",
                                    "title": "3.1 电机改造工程",
                                    "source_heading": "3.1 电机改造工程",
                                    "normalized_heading": "电机改造工程",
                                    "heading_aliases": ["电机改造工程"],
                                    "level": 2,
                                    "section_path": "第三章 系统及方案介绍 > 三、施工方案 > 3.1 电机改造工程",
                                    "heading_path": "第三章 系统及方案介绍 > 三、施工方案 > 3.1 电机改造工程",
                                    "normalized_section_path": "系统及方案介绍 > 施工方案 > 电机改造工程",
                                    "section_summary": "实施过程中拆除原异步电机并完成安装校准。",
                                    "section_retrieval_text": "电机改造工程 安装校准 施工方案",
                                    "source_signals": ["toc", "parser_heading"],
                                    "children": [],
                                },
                            ],
                        }
                    ],
                }
            ],
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
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_section_id"], "3.2.4")

    def test_retrieve_blocks_falls_back_to_unscoped_when_auto_scope_yields_no_blocks(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "3.2.4",
                            "title": "2.4 控制信号接口说明",
                            "source_heading": "2.4 控制信号接口说明",
                            "normalized_heading": "控制信号接口说明",
                            "heading_aliases": ["控制信号接口说明", "接口说明"],
                            "level": 2,
                            "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                            "heading_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.4 控制信号接口说明",
                            "normalized_section_path": "系统及方案介绍 > 系统方案 > 控制信号接口说明",
                            "section_summary": "DCS 至变频器提供 DI/DO、AI/AO 和 Modbus/RS485 接口。",
                            "section_retrieval_text": "控制信号接口说明 DCS PLC Modbus RS485 DI DO AI AO",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "环冷风机方案.docx",
                    "library_track": "pilot_main",
                    "section_path": "第四章 其他章节",
                    "heading_path": "第四章 其他章节",
                    "normalized_heading": "其他章节",
                    "source_heading": "其他章节",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "communication_interface",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 120,
                    "content": "DCS 至变频器提供 DI/DO、AI/AO 和 Modbus/RS485 接口。",
                }
            ],
        )
        try:
            service = CaseLibraryService(outline_library_path=outline_path, block_library_path=block_path)
            results = service.retrieve_blocks(
                query="DCS PLC Modbus RS485 DI DO AI AO",
                top_k=4,
                sample_ids={"case-a"},
                section_title="系统及方案介绍",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["heading_path"], "第四章 其他章节")

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

    def test_retrieve_sections_can_use_semantic_match_when_lexical_signal_is_weak(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "案例A.pdf",
                    "library_track": "pilot_main",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "变频驱动总体设计",
                            "source_heading": "变频驱动总体设计",
                            "normalized_heading": "变频驱动总体设计",
                            "heading_aliases": ["变频驱动总体设计"],
                            "level": 1,
                            "section_path": "第四章 变频驱动总体设计",
                            "heading_path": "第四章 变频驱动总体设计",
                            "normalized_section_path": "变频驱动总体设计",
                            "section_summary": "章节说明系统拓扑、控制分层和驱动边界。",
                            "section_retrieval_text": "变频驱动总体设计 系统拓扑 控制分层 驱动边界",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        },
                        {
                            "section_id": "4.2",
                            "title": "系统说明",
                            "source_heading": "系统说明",
                            "normalized_heading": "系统说明",
                            "heading_aliases": ["系统说明"],
                            "level": 1,
                            "section_path": "第四章 系统说明",
                            "heading_path": "第四章 系统说明",
                            "normalized_section_path": "系统说明",
                            "section_summary": "通用系统介绍。",
                            "section_retrieval_text": "系统说明 通用介绍",
                            "source_signals": ["toc", "parser_heading"],
                            "children": [],
                        },
                    ],
                }
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (
                            "系统架构说明\n系统架构说明",
                            "第四章 变频驱动总体设计\n变频驱动总体设计\n章节说明系统拓扑、控制分层和驱动边界。\n变频驱动总体设计 系统拓扑 控制分层 驱动边界",
                        ): 0.92,
                    }
                ),
                section_scope_semantic_enabled=True,
            )
            results = service.retrieve_sections(
                query="系统架构说明",
                top_k=2,
                sample_ids={"case-a"},
                section_title="系统架构说明",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["section_id"], "4.1")
        self.assertIn("semantic_match=", results[0]["reason"])
        self.assertIn("hybrid_rrf_boost=", results[0]["reason"])
        self.assertIn("hybrid_rrf_sources=sparse,semantic", results[0]["reason"])

    def test_retrieve_blocks_can_use_semantic_match_for_hybrid_rerank(self) -> None:
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[],
            block_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "案例A.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "4. 系统拓扑说明",
                    "section_summary": "描述控制分层、驱动边界和模块关系。",
                    "contextualized_block_text": "系统拓扑说明 控制分层 驱动边界 模块关系",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "overall_solution",
                    "equipment_type": "vfd",
                    "content_form": "narrative",
                    "token_count": 110,
                    "content": "该部分详细说明系统的分层架构和模块边界。",
                },
                {
                    "sample_id": "case-a",
                    "file_name": "案例A.pdf",
                    "library_track": "pilot_main",
                    "heading_path": "8. 培训计划",
                    "section_summary": "系统培训安排。",
                    "contextualized_block_text": "系统培训计划 培训安排",
                    "reuse_level": "high",
                    "content_risk_level": "low",
                    "front_matter": False,
                    "section_type": "service_support",
                    "equipment_type": "generic",
                    "content_form": "narrative",
                    "token_count": 90,
                    "content": "提供培训与售后服务。",
                },
            ],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(
                    scores={
                        (
                            "系统架构说明\n系统架构说明",
                            "4. 系统拓扑说明\n描述控制分层、驱动边界和模块关系。\n系统拓扑说明 控制分层 驱动边界 模块关系\n该部分详细说明系统的分层架构和模块边界。",
                        ): 0.88,
                    }
                ),
            )
            results = service.retrieve_blocks(
                query="系统架构说明",
                top_k=2,
                sample_ids={"case-a"},
                section_title="系统架构说明",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["heading_path"], "4. 系统拓扑说明")
        self.assertIn("semantic_match=", results[0]["reason"])
        self.assertIn("hybrid_rrf_boost=", results[0]["reason"])
        self.assertIn("hybrid_rrf_sources=sparse,semantic", results[0]["reason"])
        self.assertTrue(any(item.startswith("semantic_match=") for item in results[0]["reason_trace"]))
        self.assertGreater(results[0]["score_breakdown"]["hybrid_rrf"], 0.0)
        self.assertGreater(results[0]["score_breakdown"]["semantic"], 0.0)

    def test_retrieve_sections_adds_hybrid_rerank_boost(self) -> None:
        section_a_text = "4.1 总体说明\n控制分层 驱动边界 接口约束"
        section_b_text = "4.2 总体说明\n项目背景 组织安排 交付范围"
        outline_path, block_path, temp_dir = self._write_library(
            outline_entries=[
                {
                    "sample_id": "case-a",
                    "file_name": "案例A.pdf",
                    "library_track": "pilot_main",
                    "profile": "text_digital",
                    "section_catalog": [
                        {
                            "section_id": "4.1",
                            "title": "4.1 总体说明",
                            "source_heading": "4.1 总体说明",
                            "normalized_heading": "总体说明",
                            "heading_aliases": ["总体说明"],
                            "heading_path": "4.1 总体说明",
                            "section_path": "第四章 系统方案 > 4.1 总体说明",
                            "level": 2,
                            "semantic_retrieval_text": section_a_text,
                        },
                        {
                            "section_id": "4.2",
                            "title": "4.2 总体说明",
                            "source_heading": "4.2 总体说明",
                            "normalized_heading": "总体说明",
                            "heading_aliases": ["总体说明"],
                            "heading_path": "4.2 总体说明",
                            "section_path": "第四章 系统方案 > 4.2 总体说明",
                            "level": 2,
                            "semantic_retrieval_text": section_b_text,
                        },
                    ],
                }
            ],
            block_entries=[],
        )
        try:
            service = CaseLibraryService(
                outline_library_path=outline_path,
                block_library_path=block_path,
                semantic_scorer=FakeSemanticScorer(scores={}),
                reranker=FakeReranker(
                    scores={
                        ("总体说明\n系统架构说明", section_a_text): 0.91,
                        ("总体说明\n系统架构说明", section_b_text): 0.15,
                    }
                ),
                section_scope_rerank_enabled=True,
            )
            results = service.retrieve_sections(
                query="系统架构说明",
                top_k=2,
                sample_ids={"case-a"},
                section_title="总体说明",
            )
        finally:
            temp_dir.cleanup()

        self.assertEqual(results[0]["section_id"], "4.1")
        self.assertIn("hybrid_rerank_score=", results[0]["reason"])
        self.assertIn("hybrid_rerank_boost=", results[0]["reason"])
        self.assertTrue(any(item.startswith("hybrid_rerank_score=") for item in results[0]["reason_trace"]))
        self.assertGreater(results[0]["score_breakdown"]["rerank"], 0.0)
        self.assertGreater(results[0]["score_breakdown"]["hybrid_rerank"], 0.0)


if __name__ == "__main__":
    unittest.main()
