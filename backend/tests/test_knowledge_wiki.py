from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.knowledge.library_refresh import (
    _build_uploaded_case_library_entries,
    _duration_seconds,
    _derive_aggregate_refresh_status,
    delete_uploaded_document_library_cache,
    get_uploaded_document_library_cache_path,
    LIBRARY_REFRESH_CACHE_SCHEMA_VERSION,
    read_uploaded_document_library_cache,
    get_case_library_refresh_status_path,
    read_case_library_refresh_status,
    write_uploaded_document_library_cache,
)
from app.services.knowledge.wiki_compiler import compile_knowledge_wiki
from app.services.knowledge.wiki_context import KnowledgeWikiContextProvider


class KnowledgeWikiCompilerTests(unittest.TestCase):
    def test_compile_knowledge_wiki_builds_glossary_product_module_and_index(self) -> None:
        bundle = compile_knowledge_wiki(
            outline_entries=[
                {
                    "sample_id": "sample-a",
                    "file_name": "案例A.pdf",
                    "document_title": "高压变频器系统方案",
                    "top_level_titles": ["系统方案", "主回路方案", "接口说明"],
                    "section_catalog": [
                        {
                            "title": "DCS/PLC 接口信号表",
                            "source_heading": "DCS/PLC 接口信号表",
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "sample_id": "sample-a",
                    "file_name": "案例A.pdf",
                    "equipment_type": "vfd",
                    "section_type": "communication_interface",
                    "heading_path": "DCS/PLC 接口信号表",
                    "section_summary": "说明 DCS、PLC、AI、AO、DI、DO 的接口边界。",
                    "content": "DCS 与变频器之间提供 DI/DO、AI/AO 和 Modbus 通讯接口，控制柜与功率单元分别完成控制与驱动。",
                }
            ],
            term_lexicon={
                "vfd": ["vfd", "变频器", "变频柜"],
                "dcs": ["dcs", "集散控制系统"],
            },
        )

        self.assertIn("glossary.md", bundle["pages"])
        self.assertIn("index.md", bundle["pages"])
        self.assertIn("products/vfd-system.md", bundle["pages"])
        self.assertIn("modules/power-cell.md", bundle["pages"])
        self.assertIn("变频器", bundle["pages"]["glossary.md"])
        self.assertIn("高压变频器方案族", bundle["pages"]["products/vfd-system.md"])
        self.assertIn("功率单元", bundle["pages"]["modules/power-cell.md"])
        self.assertEqual(bundle["manifest"]["categories"]["products"], 1)
        self.assertEqual(bundle["manifest"]["categories"]["modules"], 2)
        self.assertEqual(bundle["manifest"]["categories"]["equipment"], 1)
        self.assertEqual(bundle["manifest"]["categories"]["interfaces"], 1)
        self.assertEqual(len(bundle["structured_assets"]["product_cards"]), 1)
        self.assertEqual(len(bundle["structured_assets"]["module_cards"]), 2)

    def test_compile_knowledge_wiki_builds_equipment_and_template_pages(self) -> None:
        bundle = compile_knowledge_wiki(
            outline_entries=[
                {
                    "file_name": "案例A.pdf",
                    "section_catalog": [
                        {
                            "title": "4.1 LCI 变频软起系统方案",
                            "source_heading": "4.1 LCI 变频软起系统方案",
                            "children": [],
                        }
                    ],
                }
            ],
            block_entries=[
                {
                    "file_name": "案例A.pdf",
                    "equipment_type": "lci",
                    "section_type": "main_circuit_scheme",
                    "heading_path": "4.1 LCI 变频软起系统方案",
                    "section_summary": "说明主回路、同步切换和励磁边界。",
                    "content": "LCI、输入变压器、输出变压器和同步电机构成主回路。",
                },
                {
                    "file_name": "案例B.pdf",
                    "equipment_type": "lci",
                    "section_type": "main_circuit_scheme",
                    "heading_path": "输入输出变压器及主回路配置",
                    "section_summary": "说明旁路与切换路径。",
                    "content": "主回路采用输入输出隔离配置并保留旁路切换。",
                },
            ],
            term_lexicon={},
        )

        page_paths = set(bundle["pages"])
        self.assertIn("equipment/lci.md", page_paths)
        self.assertIn("templates/main-circuit-scheme.md", page_paths)
        self.assertIn("LCI 变频软起系统", bundle["pages"]["equipment/lci.md"])
        self.assertIn("主回路方案", bundle["pages"]["templates/main-circuit-scheme.md"])


class KnowledgeWikiContextProviderTests(unittest.TestCase):
    def test_build_section_context_injects_glossary_template_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "vfd",
                            "display_primary_term": "变频器",
                            "aliases": ["变频柜"],
                            "display_aliases": ["变频柜"],
                        },
                        {
                            "primary_term": "dcs",
                            "display_primary_term": "DCS",
                            "aliases": ["集散控制系统"],
                            "display_aliases": ["集散控制系统"],
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "product_family": "vfd_system",
                            "title": "高压变频器方案族",
                            "entry_count": 3,
                            "aliases": ["高压变频器", "VFD"],
                            "top_equipment_types": [
                                {"equipment_type": "vfd", "label": "高压变频器", "count": 3}
                            ],
                            "top_section_types": [
                                {"section_type": "main_circuit_scheme", "label": "主回路方案", "count": 2}
                            ],
                            "representative_titles": ["高压变频器系统方案"],
                            "representative_headings": ["主回路结构与切换逻辑"],
                            "representative_snippets": ["该方案由移相变压器、功率单元和控制柜组成。"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "module_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "module_key": "power-cell",
                            "title": "功率单元",
                            "aliases": ["功率模块", "H桥"],
                            "entry_count": 4,
                            "top_equipment_types": [
                                {"equipment_type": "vfd", "label": "高压变频器", "count": 4}
                            ],
                            "top_section_types": [
                                {"section_type": "main_circuit_scheme", "label": "主回路方案", "count": 3}
                            ],
                            "top_headings": ["主回路结构与切换逻辑"],
                            "representative_snippets": ["功率单元串联后形成输出级，每单元配置旁路能力。"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "equipment_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "equipment_type": "vfd",
                            "title": "高压变频器",
                            "entry_count": 5,
                            "top_section_types": [
                                {"section_type": "main_circuit_scheme", "label": "主回路方案", "count": 4}
                            ],
                            "top_headings": ["主回路配置"],
                            "representative_snippets": ["高压变频器配合移相整流变压器和功率单元串联运行。"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(
                json.dumps(
                    [
                        {
                            "section_type": "main_circuit_scheme",
                            "title": "主回路方案",
                            "guidance": ["说明主回路、旁路和隔离路径。", "说明输入输出变压器边界。"],
                            "common_headings": ["主回路结构与切换逻辑"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "forbidden_phrases.json").write_text(
                json.dumps(
                    [
                        {"phrase": "我公司", "preferred": "本方案 / 本系统 / 本装置"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            provider = KnowledgeWikiContextProvider(root)
            context = provider.build_section_context(
                section={
                    "title": "主回路系统方案",
                    "purpose": "说明高压变频器主回路、旁路和隔离方式。",
                    "keywords": ["主回路", "变频器", "旁路"],
                },
                global_params={"project_name": "测试项目"},
            )

        self.assertIn("AI Wiki 编译知识", context)
        self.assertIn("术语别名", context)
        self.assertIn("产品族知识卡", context)
        self.assertIn("模块知识卡", context)
        self.assertIn("变频器", context)
        self.assertIn("高压变频器方案族", context)
        self.assertIn("功率单元", context)
        self.assertIn("章节骨架（主回路方案）", context)
        self.assertIn("设备知识卡", context)
        self.assertIn("禁用表述", context)

    def test_build_section_context_gracefully_degrades_when_assets_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = KnowledgeWikiContextProvider(temp_dir)
            context = provider.build_section_context(
                section={"title": "控制系统方案", "keywords": ["控制系统"]},
                global_params={"project_name": "测试项目"},
            )

        self.assertEqual(context, "")

    def test_build_quality_review_context_emits_term_and_policy_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "vfd",
                            "display_primary_term": "变频器",
                            "aliases": ["VFD", "变频柜"],
                            "display_aliases": ["VFD", "变频柜"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "module_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(
                json.dumps(
                    [
                        {"phrase": "我公司", "preferred": "本方案 / 本系统 / 本装置"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            provider = KnowledgeWikiContextProvider(root)
            context = provider.build_quality_review_context(
                section={
                    "title": "VFD 主回路方案",
                    "purpose": "说明变频器主回路与旁路切换方式。",
                    "keywords": ["VFD", "变频器"],
                },
                global_params={"project_name": "测试项目"},
            )

        self.assertIn("AI Wiki 质检约束", context)
        self.assertIn("统一术语", context)
        self.assertIn("优先使用“变频器”", context)
        self.assertIn("禁用表述", context)

    def test_collect_query_expansion_terms_returns_primary_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "vfd",
                            "display_primary_term": "变频器",
                            "aliases": ["VFD", "变频柜"],
                            "display_aliases": ["VFD", "变频柜"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "module_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

            provider = KnowledgeWikiContextProvider(root)
            terms = provider.collect_query_expansion_terms(
                section={"title": "VFD 主回路方案", "keywords": ["VFD"]},
                global_params={"project_name": "测试项目"},
            )

        self.assertIn("变频器", terms)
        self.assertIn("VFD", terms)
        self.assertIn("变频柜", terms)

    def test_collect_retrieval_prior_bundle_returns_terms_and_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "vfd",
                            "display_primary_term": "变频器",
                            "aliases": ["VFD"],
                            "display_aliases": ["VFD"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "product_family": "vfd_system",
                            "title": "高压变频器方案族",
                            "aliases": ["高压变频器"],
                            "representative_titles": ["高压变频器系统方案"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "module_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "module_key": "power-cell",
                            "title": "功率单元",
                            "aliases": ["功率模块"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

            provider = KnowledgeWikiContextProvider(root)
            bundle = provider.collect_retrieval_prior_bundle(
                section={
                    "title": "主回路系统方案",
                    "purpose": "说明高压变频器主回路与功率单元配置。",
                    "keywords": ["高压变频器", "功率单元"],
                },
                global_params={"project_name": "测试项目"},
            )

        self.assertIn("变频器", bundle["query_expansion_terms"])
        self.assertIn("VFD", bundle["query_expansion_terms"])
        self.assertIn("高压变频器方案族", bundle["query_expansion_terms"])
        self.assertIn("高压变频器", bundle["query_expansion_terms"])
        self.assertIn("功率单元", bundle["query_expansion_terms"])
        self.assertEqual(bundle["product_cards"][0]["title"], "高压变频器方案族")
        self.assertEqual(bundle["module_cards"][0]["title"], "功率单元")

    def test_collect_retrieval_prior_bundle_keeps_supply_scope_query_expansion_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "lci",
                            "display_primary_term": "LCI",
                            "aliases": ["LCI 软起"],
                            "display_aliases": ["LCI 软起"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "product_family": "lci_system",
                            "title": "LCI 变频软起方案族",
                            "aliases": ["LCI", "LCI 软起"],
                            "top_equipment_types": [
                                {"equipment_type": "lci", "label": "LCI 变频软起系统", "count": 5}
                            ],
                            "top_section_types": [
                                {"section_type": "supply_scope", "label": "供货范围", "count": 3}
                            ],
                            "representative_titles": ["LCI 供货范围章节"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "module_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "module_key": "control-cabinet",
                            "title": "控制柜",
                            "aliases": ["控制单元"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

            provider = KnowledgeWikiContextProvider(root)
            bundle = provider.collect_retrieval_prior_bundle(
                section={
                    "title": "供货范围",
                    "purpose": "说明 LCI 软起装置、本地控制柜和随机资料的供货边界。",
                    "keywords": ["供货范围", "LCI", "控制柜"],
                    "target_section_type": "supply_scope",
                    "target_equipment_type": "lci",
                },
                global_params={"project_name": "测试项目"},
            )

        self.assertIn("LCI", bundle["query_expansion_terms"])
        self.assertIn("LCI 软起", bundle["query_expansion_terms"])
        self.assertNotIn("LCI 变频软起方案族", bundle["query_expansion_terms"])
        self.assertNotIn("控制柜", bundle["query_expansion_terms"])
        self.assertEqual(bundle["product_cards"][0]["title"], "LCI 变频软起方案族")
        self.assertEqual(bundle["module_cards"][0]["title"], "控制柜")

    def test_collect_retrieval_prior_bundle_disables_card_terms_for_document_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(
                json.dumps(
                    [
                        {
                            "primary_term": "lci",
                            "display_primary_term": "LCI",
                            "aliases": ["LCI 软起"],
                            "display_aliases": ["LCI 软起"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "product_cards.json").write_text(
                json.dumps([{"title": "LCI 变频软起方案族", "aliases": ["LCI"]}], ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "module_cards.json").write_text(
                json.dumps([{"title": "控制柜", "aliases": ["PLC柜"]}], ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

            provider = KnowledgeWikiContextProvider(root)
            bundle = provider.collect_retrieval_prior_bundle(
                section={
                    "title": "项目交付资料与文档清单",
                    "purpose": "列明设计图纸、操作维护手册、测试报告及合格证等交付文档。",
                    "keywords": ["交付文档", "技术图纸", "LCI"],
                },
                global_params={"project_name": "某钢铁厂 LCI 变频软起项目"},
            )

        self.assertEqual(bundle["query_expansion_terms"], [])
        self.assertEqual(bundle["product_cards"], [])
        self.assertEqual(bundle["module_cards"], [])

    def test_collect_retrieval_prior_bundle_filters_unanchored_product_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-04-20T00:00:00Z"}), encoding="utf-8")
            (root / "glossary.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "product_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "product_family": "maintenance_service",
                            "title": "运维巡检方案族",
                            "aliases": ["维保", "巡检"],
                            "top_equipment_types": [
                                {"equipment_type": "transformer", "label": "变压器", "count": 4}
                            ],
                            "top_section_types": [
                                {"section_type": "service_support", "label": "服务支持", "count": 24}
                            ],
                            "representative_titles": ["成套设备维保巡检方案"],
                            "representative_headings": ["设备巡检周期"],
                            "representative_snippets": ["该方案用于各配电站高低压设备维保及巡检。"],
                        },
                        {
                            "product_family": "lci_system",
                            "title": "LCI 变频软起方案族",
                            "aliases": ["LCI", "变频软起"],
                            "top_equipment_types": [
                                {"equipment_type": "lci", "label": "LCI 变频软起系统", "count": 5}
                            ],
                            "top_section_types": [
                                {"section_type": "vfd_spec", "label": "变频器规格", "count": 6}
                            ],
                            "representative_titles": ["三鼓风 LCI 改造方案"],
                            "representative_headings": ["启动和同步过程描述"],
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "module_cards.json").write_text(
                json.dumps(
                    [
                        {
                            "module_key": "transformer-cabinet",
                            "title": "变压器柜",
                            "aliases": ["变压器柜", "隔离变压器"],
                            "top_equipment_types": [
                                {"equipment_type": "transformer", "label": "变压器", "count": 1}
                            ],
                            "top_section_types": [
                                {"section_type": "transformer_spec", "label": "变压器参数", "count": 1}
                            ],
                            "top_headings": ["输出变压器技术规范"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "equipment_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "interface_cards.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "section_templates.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            (root / "forbidden_phrases.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

            provider = KnowledgeWikiContextProvider(root)
            bundle = provider.collect_retrieval_prior_bundle(
                section={
                    "title": "输出变压器技术规范",
                    "purpose": "说明输出变压器参数、绝缘等级和冷却方式。",
                    "keywords": ["变压器", "输出变压器", "技术规范"],
                    "expected_evidence_types": ["section", "table"],
                },
                global_params={"project_name": "测试项目"},
            )

        self.assertEqual(bundle["product_cards"], [])
        self.assertEqual(bundle["module_cards"][0]["title"], "变压器柜")


class KnowledgeLibraryRefreshStatusTests(unittest.TestCase):
    def test_duration_seconds_returns_elapsed_seconds(self) -> None:
        self.assertEqual(
            _duration_seconds(
                "2026-04-20T10:00:00+00:00",
                "2026-04-20T10:00:01.2349+00:00",
            ),
            1.235,
        )
        self.assertIsNone(_duration_seconds("invalid", "2026-04-20T10:00:01+00:00"))

    def test_read_case_library_refresh_status_defaults_to_idle_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = read_case_library_refresh_status(
                status_path=get_case_library_refresh_status_path(root=Path(temp_dir))
            )

        self.assertEqual(status["status"], "idle")
        self.assertFalse(status["pending"])
        self.assertEqual(status["stats"], {})
        self.assertEqual(status["pipelines"]["case_library"]["status"], "idle")
        self.assertIsNone(status["pipelines"]["case_library"]["duration_seconds"])
        self.assertEqual(status["pipelines"]["visual_cache"]["status"], "idle")
        self.assertIsNone(status["pipelines"]["visual_cache"]["duration_seconds"])

    def test_read_case_library_refresh_status_reads_existing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = get_case_library_refresh_status_path(root=Path(temp_dir))
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "last_success_at": "2026-04-20T10:00:00+00:00",
                        "pending": False,
                        "stats": {"uploaded_outline_documents": 3},
                        "pipelines": {
                            "case_library": {"status": "succeeded", "duration_seconds": 1.25},
                            "visual_cache": {"status": "running", "duration_seconds": None},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status = read_case_library_refresh_status(status_path=status_path)

        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["stats"]["uploaded_outline_documents"], 3)
        self.assertEqual(status["pipelines"]["case_library"]["status"], "succeeded")
        self.assertEqual(status["pipelines"]["case_library"]["duration_seconds"], 1.25)
        self.assertEqual(status["pipelines"]["visual_cache"]["status"], "running")
        self.assertIsNone(status["pipelines"]["visual_cache"]["duration_seconds"])

    def test_derive_aggregate_refresh_status_marks_partial_failure(self) -> None:
        payload = _derive_aggregate_refresh_status(
            {
                "stats": {},
                "pipelines": {
                    "case_library": {
                        "status": "succeeded",
                        "last_success_at": "2026-04-20T10:00:00+00:00",
                        "finished_at": "2026-04-20T10:00:00+00:00",
                    },
                    "visual_cache": {
                        "status": "failed",
                        "finished_at": "2026-04-20T10:02:00+00:00",
                        "error": "clip backend unavailable",
                    },
                },
            }
        )

        self.assertEqual(payload["status"], "partial_failed")
        self.assertIn("visual_cache", payload["error"])
        self.assertEqual(payload["last_success_at"], "2026-04-20T10:00:00+00:00")

    def test_write_uploaded_document_library_cache_round_trip(self) -> None:
        document = SimpleNamespace(
            id="doc-1",
            filename="案例A.md",
            file_type="md",
            meta={
                "raw_document_id": "raw-1",
                "document_profile": {"name": "vfd_spec"},
                "ingestion_recommendation": "main_vector_ready",
            },
        )
        parsed_document = SimpleNamespace(
            markdown=(
                "# 文档标题\n\n"
                "## 控制接口\n\n"
                "系统提供 DCS / PLC 控制接口、AI/AO、DI/DO、联锁闭锁与故障反馈信号，"
                "并对上位系统开放运行状态、告警状态、远程启停和参数监视能力。"
            ),
            structure={},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = write_uploaded_document_library_cache(
                document=document,
                parsed_document=parsed_document,
                root=Path(temp_dir),
            )
            loaded = read_uploaded_document_library_cache(document_id="doc-1", root=Path(temp_dir))

        self.assertEqual(payload["schema_version"], LIBRARY_REFRESH_CACHE_SCHEMA_VERSION)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["document_id"], "doc-1")
        self.assertEqual(loaded["raw_document_id"], "raw-1")
        self.assertEqual(loaded["outline_entry"]["file_name"], "案例A.md")
        self.assertGreaterEqual(len(loaded["block_entries"]), 1)

    def test_delete_uploaded_document_library_cache_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = get_uploaded_document_library_cache_path(document_id="doc-1", root=Path(temp_dir))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

            delete_uploaded_document_library_cache(document_id="doc-1", root=Path(temp_dir))

            self.assertFalse(path.exists())


class KnowledgeLibraryRefreshCacheUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_uploaded_case_library_entries_prefers_cache(self) -> None:
        document = SimpleNamespace(
            id="doc-1",
            filename="案例A.md",
            file_type="md",
            storage_path="/tmp/case-a.md",
            meta={
                "document_profile": {"name": "vfd_spec"},
                "ingestion_recommendation": "main_vector_ready",
            },
        )
        cached_payload = {
            "outline_entry": {"file_name": "案例A.md", "sample_id": "uploaded-doc-1"},
            "block_entries": [{"file_name": "案例A.md", "content": "系统提供 DCS / PLC 控制接口。"}],
        }

        class _FakeScalars:
            def __init__(self, items):
                self._items = items

            def all(self):
                return self._items

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def scalars(self, stmt):
                del stmt
                return _FakeScalars([document])

        with (
            patch("app.services.knowledge.library_refresh.get_session_factory", return_value=lambda: _FakeSession()),
            patch("app.services.knowledge.library_refresh.read_uploaded_document_library_cache", return_value=cached_payload),
            patch("app.services.knowledge.library_refresh.ParserService") as parser_cls,
            patch("app.services.knowledge.library_refresh.get_object_storage"),
        ):
            outline_entries, block_entries, meta = await _build_uploaded_case_library_entries()

        parser_cls.assert_not_called()
        parser_cls.return_value.parse_document.assert_not_called()
        self.assertEqual(len(outline_entries), 1)
        self.assertEqual(len(block_entries), 1)
        self.assertEqual(meta["cache_hit_uploaded_documents"], 1)
        self.assertEqual(meta["cache_miss_uploaded_documents"], 0)


if __name__ == "__main__":
    unittest.main()
