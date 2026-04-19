from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.validation.service import (
    build_review_task_blueprints,
    collect_validation_findings,
    derive_validation_status,
)


class ValidationHelperTests(unittest.TestCase):
    def test_collect_validation_findings_returns_blockers_and_warnings(self) -> None:
        requirement_card = SimpleNamespace(
            content={"key_parameters": {"total_power": "5000kW"}},
            blocking_items=[],
        )
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.4200"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_001",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "type": "figure",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "1",
                        "title": "技术架构",
                        "mandatory": True,
                        "expected_evidence_types": ["figure", "parameter"],
                        "asset_required": True,
                        "needs_human_review": True,
                        "children": [],
                    },
                    {
                        "section_id": "2",
                        "title": "实施计划",
                        "mandatory": True,
                        "expected_evidence_types": ["section"],
                        "needs_human_review": False,
                        "children": [],
                    },
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="1",
                title="技术架构",
                content_md=(
                    "当前方案待确认系统拓扑，使用 [Company_A] 占位，并引用旧项目A 的拓扑描述，"
                    "补充了足够多的技术说明文字用于测试。\n\n[[ASSET:FIGURE:asset-001]]"
                ),
                citation_refs=[
                    {
                        "evidence_id": "ev_001",
                        "source_doc_id": "doc_1",
                        "source_title": "历史方案A",
                        "type": "figure",
                    }
                ],
                assumptions=[],
                global_param_snapshot={"total_power": "5200kW"},
                validator_result={
                    "recommended_assets": [{"asset_id": "asset-001", "asset_type": "figure", "risk_level": "high"}],
                    "reuse_pack": {"banned_terms": ["旧项目A"]},
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertEqual({item["code"] for item in errors}, {"VAL002", "VAL003", "VAL006", "VAL007", "VAL008"})
        self.assertEqual({item["code"] for item in warnings}, {"VAL101", "VAL102", "VAL103"})
        self.assertEqual({item["code"] for item in section_results["1"]["errors"]}, {"VAL006", "VAL007", "VAL008"})

    def test_collect_validation_findings_flags_similarity_and_parameter_replacement_risk(self) -> None:
        requirement_card = SimpleNamespace(
            content={"key_parameters": {"voltage_level": "10kV", "quantity": "3台"}},
            blocking_items=[],
        )
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.8800"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "4",
                        "title": "硬件配置清单",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "asset_required": False,
                        "parameter_sensitive": True,
                        "customer_specificity": "medium",
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        source_block = (
            "硬件配置清单如下：本方案配置高压变频器 2台，电压等级 6kV，采用站控层、间隔层和网络层的分层架构，"
            "配套原有控制柜和辅助系统，适用于历史项目的标准交付边界。"
        )
        section_drafts = [
            SimpleNamespace(
                section_id="4",
                title="硬件配置清单",
                content_md=source_block,
                citation_refs=[
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={"voltage_level": "10kV", "quantity": "3台"},
                validator_result={
                    "reuse_pack": {
                        "must_replace_fields": ["voltage_level", "quantity"],
                        "replacement_hints": {"voltage_level": "10kV", "quantity": "3台"},
                        "reusable_blocks": [
                            {
                                "block_id": "block-1",
                                "source_title": "历史方案B",
                                "content_md": source_block,
                            }
                        ],
                    }
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertIn("VAL009", {item["code"] for item in errors})
        self.assertIn("VAL105", {item["code"] for item in warnings})
        self.assertEqual(
            {item["code"] for item in section_results["4"]["errors"]},
            {"VAL009"},
        )
        self.assertEqual(
            {item["code"] for item in section_results["4"]["warnings"]},
            {"VAL105"},
        )

    def test_collect_validation_findings_accepts_reuse_block_citations(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_002",
                        "source_doc_id": "doc_2",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "3",
                        "title": "技术架构",
                        "mandatory": True,
                        "expected_evidence_types": ["section"],
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="3",
                title="技术架构",
                content_md="系统采用分层控制架构，配置上位机接口与联锁回路。",
                citation_refs=[
                    {
                        "evidence_id": "case:sample-b:9",
                        "source_doc_id": "sample-b",
                        "source_title": "历史方案B",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "reuse_pack": {
                        "reusable_blocks": [
                            {
                                "block_id": "case:sample-b:9",
                                "source_title": "历史方案B",
                                "content_md": "系统采用分层控制架构。",
                            }
                        ]
                    }
                },
            )
        ]

        errors, _, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL005", {item["code"] for item in errors})
        self.assertNotIn("VAL005", {item["code"] for item in section_results["3"]["errors"]})

    def test_collect_validation_findings_skips_val103_when_case_fallback_is_acceptable(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.6200"),
            content={
                "results": [
                    {
                        "evidence_id": "case_ev_001",
                        "source_doc_id": "sample-a",
                        "source_title": "历史方案A",
                        "type": "case_summary",
                    }
                ],
                "fallback_results": [
                    {
                        "evidence_id": "case_ev_001",
                        "source_doc_id": "sample-a",
                        "source_title": "历史方案A",
                        "type": "case_summary",
                        "relevance_score": 0.51,
                    }
                ],
                "quality_trace": {
                    "primary_results_source": "case_fallback",
                    "case_fallback_used": True,
                    "case_fallback_count": 1,
                    "case_fallback_top_score": 0.51,
                },
            },
        )
        outline = SimpleNamespace(outline_json={"title": "测试方案", "sections": []})

        errors, warnings, _ = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=[],
        )

        self.assertEqual(errors, [])
        self.assertNotIn("VAL103", {item["code"] for item in warnings})

    def test_collect_validation_findings_warns_precisely_for_weak_case_fallback(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.5800"),
            content={
                "results": [],
                "fallback_results": [
                    {
                        "evidence_id": "case_ev_001",
                        "source_doc_id": "sample-a",
                        "source_title": "历史方案A",
                        "type": "case_summary",
                        "relevance_score": 0.32,
                    }
                ],
                "quality_trace": {
                    "primary_results_source": "case_fallback",
                    "case_fallback_used": True,
                    "case_fallback_count": 1,
                    "case_fallback_top_score": 0.32,
                },
            },
        )
        outline = SimpleNamespace(outline_json={"title": "测试方案", "sections": []})

        _, warnings, _ = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=[],
        )

        warning = next(item for item in warnings if item["code"] == "VAL103")
        self.assertIn("案例级 fallback", warning["message"])
        self.assertEqual(warning["details"]["primary_results_source"], "case_fallback")

    def test_collect_validation_findings_blocks_internal_heading_and_quality_gate(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_010",
                        "source_doc_id": "doc_10",
                        "source_title": "历史方案C",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "3",
                        "title": "总体方案",
                        "mandatory": True,
                        "expected_evidence_types": ["section"],
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="3",
                title="总体方案",
                content_md="## 总体方案\n\n### A. 概述\n\n本节说明系统组成和控制边界。",
                citation_refs=[
                    {
                        "evidence_id": "ev_010",
                        "source_doc_id": "doc_10",
                        "source_title": "历史方案C",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "quality_gate": {
                        "status": "review_required",
                        "score": 0.63,
                        "summary": "章节小标题仍需整理。",
                        "issues": [{"code": "SQ002"}],
                    }
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertIn("VAL010", {item["code"] for item in errors})
        self.assertIn("VAL108", {item["code"] for item in errors})
        self.assertIn("VAL010", {item["code"] for item in section_results["3"]["errors"]})
        self.assertIn("VAL108", {item["code"] for item in section_results["3"]["errors"]})

    def test_collect_validation_findings_skips_table_confirmation_when_table_is_already_materialized(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(quality_score=Decimal("0.9000"), content={"results": []})
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "12",
                        "title": "供货范围",
                        "mandatory": True,
                        "expected_evidence_types": ["table"],
                        "asset_required": True,
                        "needs_human_review": True,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="12",
                title="供货范围",
                content_md=(
                    "| 序号 | 设备 |\n"
                    "| --- | --- |\n"
                    "| 1 | LCI 变频软起动装置 |\n\n"
                    "[[ASSET:TABLE:asset-table-1]]"
                ),
                citation_refs=[],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "recommended_assets": [
                        {"asset_id": "asset-table-1", "asset_type": "table", "risk_level": "high"}
                    ]
                },
            )
        ]

        errors, _, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL006", {item["code"] for item in errors})
        self.assertNotIn("VAL006", {item["code"] for item in section_results["12"]["errors"]})

    def test_collect_validation_findings_accepts_quantity_unit_pairs_as_replaced(self) -> None:
        requirement_card = SimpleNamespace(
            content={"key_parameters": {"quantity": "1套软起系统，服务2台同步电机", "voltage_level": "10kV"}},
            blocking_items=[],
        )
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_quantity",
                        "source_doc_id": "doc_quantity",
                        "source_title": "历史方案",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "3",
                        "title": "总体方案",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "asset_required": False,
                        "parameter_sensitive": True,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="3",
                title="总体方案",
                content_md=(
                    "本项目配置 1 套 LCI/SFC 变频软启动系统，服务 2 台 10kV 同步电机，"
                    "用于完成高炉鼓风机启动、同步切换及转工频运行，关键参数已按当前项目替换。"
                ),
                citation_refs=[
                    {
                        "evidence_id": "ev_quantity",
                        "source_doc_id": "doc_quantity",
                        "source_title": "历史方案",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={"quantity": "1套软起系统，服务2台同步电机", "voltage_level": "10kV"},
                validator_result={
                    "reuse_pack": {
                        "must_replace_fields": ["quantity", "voltage_level"],
                        "replacement_hints": {
                            "quantity": "1套软起系统，服务2台同步电机",
                            "voltage_level": "10kV",
                        },
                    }
                },
            )
        ]

        errors, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL009", {item["code"] for item in errors})
        self.assertNotIn("VAL106", {item["code"] for item in warnings})
        self.assertNotIn("VAL009", {item["code"] for item in section_results["3"]["errors"]})
        self.assertNotIn("VAL106", {item["code"] for item in section_results["3"]["warnings"]})

    def test_collect_validation_findings_skips_val104_when_table_asset_is_materialized(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_table",
                        "source_doc_id": "doc_table",
                        "source_title": "历史方案",
                        "type": "table",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "3",
                        "title": "供货范围",
                        "mandatory": True,
                        "expected_evidence_types": ["table"],
                        "asset_required": True,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="3",
                title="供货范围",
                content_md=(
                    "本章说明主要设备供货边界。\n\n"
                    "| 序号 | 设备 | 数量 |\n"
                    "|---|---|---:|\n"
                    "| 1 | LCI/SFC 变频软起动系统 | 1 套 |\n"
                    "| 2 | 同步电机接口 | 2 台 |\n"
                ),
                citation_refs=[
                    {
                        "evidence_id": "ev_table",
                        "source_doc_id": "doc_table",
                        "source_title": "历史方案",
                        "type": "table",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "recommended_assets": [
                        {"asset_id": "asset-table-1", "asset_type": "table", "risk_level": "high"}
                    ]
                },
            )
        ]

        _, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL104", {item["code"] for item in warnings})
        self.assertNotIn("VAL104", {item["code"] for item in section_results["3"]["warnings"]})

    def test_collect_validation_findings_flags_solution_snapshot_alignment_issues(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_solution_1",
                        "source_doc_id": "doc_solution_1",
                        "source_title": "历史方案A",
                        "type": "section",
                    },
                    {
                        "evidence_id": "ev_solution_2",
                        "source_doc_id": "doc_solution_2",
                        "source_title": "历史方案B",
                        "type": "table",
                    },
                    {
                        "evidence_id": "ev_solution_3",
                        "source_doc_id": "doc_solution_3",
                        "source_title": "历史方案C",
                        "type": "section",
                    },
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "1",
                        "title": "主要设备技术参数",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "parameter_sensitive": True,
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    },
                    {
                        "section_id": "2",
                        "title": "DCS 通讯接口方案",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "parameter"],
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    },
                    {
                        "section_id": "3",
                        "title": "供货范围与配置清单",
                        "mandatory": True,
                        "expected_evidence_types": ["table", "parameter"],
                        "parameter_sensitive": True,
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    },
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="1",
                title="主要设备技术参数",
                content_md="本章仅描述一般性技术原则，尚未落入当前项目的设备名称、电压等级和容量信息，但篇幅足够用于通过基础长度检查。",
                citation_refs=[
                    {
                        "evidence_id": "ev_solution_1",
                        "source_doc_id": "doc_solution_1",
                        "source_title": "历史方案A",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={"reuse_pack": {}},
            ),
            SimpleNamespace(
                section_id="2",
                title="DCS 通讯接口方案",
                content_md="本章仅说明系统具备远程监控接口和信号传输能力，但未落入协议类型、点数分配及具体接口边界，文本长度同样足够。",
                citation_refs=[
                    {
                        "evidence_id": "ev_solution_2",
                        "source_doc_id": "doc_solution_2",
                        "source_title": "历史方案B",
                        "type": "table",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={"reuse_pack": {}},
            ),
            SimpleNamespace(
                section_id="3",
                title="供货范围与配置清单",
                content_md="本章仅写入主驱动系统，不含旁路切换柜，也未采用表格列出供货清单，导致方案快照中的供货范围没有完整覆盖。",
                citation_refs=[
                    {
                        "evidence_id": "ev_solution_3",
                        "source_doc_id": "doc_solution_3",
                        "source_title": "历史方案C",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={"reuse_pack": {}},
            ),
        ]
        solution_snapshot = SimpleNamespace(
            selected_products=[
                {
                    "role": "主驱动",
                    "name": "LCI 同步电机变频软起动系统",
                    "rated_voltage": "10kV",
                    "rated_power_kw": 4500,
                    "quantity": 1,
                    "config": "旁路配置",
                },
                {
                    "role": "旁路柜",
                    "name": "旁路切换柜",
                    "rated_voltage": "10kV",
                    "rated_power_kw": 4500,
                    "quantity": 1,
                    "config": "旁路配置",
                },
            ],
            interface_plan={"dcs_protocol": "Profibus-DP", "io_allocation": {"DI": 16, "DO": 8, "AI": 4, "AO": 2}},
        )

        errors, _, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
            solution_snapshot=solution_snapshot,
        )

        self.assertIn("VAL011", {item["code"] for item in errors})
        self.assertIn("VAL012", {item["code"] for item in errors})
        self.assertIn("VAL013", {item["code"] for item in errors})
        self.assertIn("VAL011", {item["code"] for item in section_results["1"]["errors"]})
        self.assertIn("VAL012", {item["code"] for item in section_results["2"]["errors"]})
        self.assertIn("VAL013", {item["code"] for item in section_results["3"]["errors"]})

    def test_collect_validation_findings_keeps_val104_for_missing_figure_placeholder(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_figure",
                        "source_doc_id": "doc_figure",
                        "source_title": "历史方案",
                        "type": "figure",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "7",
                        "title": "接口联锁方案",
                        "mandatory": True,
                        "expected_evidence_types": ["figure"],
                        "asset_required": True,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="7",
                title="接口联锁方案",
                content_md="系统按主回路、控制回路和联锁回路组织接口说明，但尚未插入系统结构图引用。",
                citation_refs=[
                    {
                        "evidence_id": "ev_figure",
                        "source_doc_id": "doc_figure",
                        "source_title": "历史方案",
                        "type": "figure",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={
                    "recommended_assets": [
                        {"asset_id": "asset-figure-1", "asset_type": "figure", "risk_level": "medium"}
                    ]
                },
            )
        ]

        _, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertIn("VAL104", {item["code"] for item in warnings})
        self.assertIn("VAL104", {item["code"] for item in section_results["7"]["warnings"]})

    def test_collect_validation_findings_treats_declared_assumption_context_as_not_implicit(self) -> None:
        requirement_card = SimpleNamespace(content={"key_parameters": {}}, blocking_items=[])
        evidence_bundle = SimpleNamespace(
            quality_score=Decimal("0.9000"),
            content={
                "results": [
                    {
                        "evidence_id": "ev_assumption",
                        "source_doc_id": "doc_assumption",
                        "source_title": "历史方案",
                        "type": "section",
                    }
                ]
            },
        )
        outline = SimpleNamespace(
            outline_json={
                "title": "测试方案",
                "sections": [
                    {
                        "section_id": "2",
                        "title": "供电条件",
                        "mandatory": True,
                        "expected_evidence_types": ["section", "table"],
                        "asset_required": False,
                        "needs_human_review": False,
                        "children": [],
                    }
                ],
            }
        )
        section_drafts = [
            SimpleNamespace(
                section_id="2",
                title="供电条件",
                content_md=(
                    "本章说明供电条件边界。\n\n"
                    "### 待确认供电参数\n\n"
                    "| 项目 | 状态 |\n"
                    "|---|---|\n"
                    "| 输入变压器型号 | 待确认 |\n"
                    "注：表中待确认项目以最终技术协议及供货清单约定为准。"
                ),
                citation_refs=[
                    {
                        "evidence_id": "ev_assumption",
                        "source_doc_id": "doc_assumption",
                        "source_title": "历史方案",
                        "type": "section",
                    }
                ],
                assumptions=[],
                global_param_snapshot={},
                validator_result={},
            )
        ]

        _, warnings, section_results = collect_validation_findings(
            requirement_card=requirement_card,
            evidence_bundle=evidence_bundle,
            outline=outline,
            section_drafts=section_drafts,
        )

        self.assertNotIn("VAL102", {item["code"] for item in warnings})
        self.assertNotIn("VAL102", {item["code"] for item in section_results["2"]["warnings"]})

    def test_build_review_task_blueprints_creates_manual_tasks_without_final_review_by_default(self) -> None:
        outline = SimpleNamespace(id=uuid4(), outline_json={"title": "测试方案"})
        existing_open_task = SimpleNamespace(
            status="open",
            payload={"signature": "content_review:1:1:VAL101"},
        )

        blueprints = build_review_task_blueprints(
            errors=[
                {
                    "code": "VAL003",
                    "details": {"param_name": "total_power", "values": ["5000kW", "5200kW"]},
                    "message": "参数冲突",
                },
                {
                    "code": "VAL006",
                    "section_id": "1",
                    "section_title": "技术架构",
                    "message": "图表待确认",
                },
                {
                    "code": "VAL108",
                    "section_id": "3",
                    "section_title": "总体方案",
                    "message": "章节质量审查未通过",
                },
            ],
            warnings=[
                {
                    "code": "VAL101",
                    "section_id": "1",
                    "section_title": "技术架构",
                    "message": "章节可能偏离目标",
                },
                {
                    "code": "VAL105",
                    "section_id": "2",
                    "section_title": "硬件配置清单",
                    "message": "复用相似度过高",
                }
            ],
            outline=outline,
            draft_version=1,
            existing_tasks=[existing_open_task],
        )

        task_types = {item["task_type"] for item in blueprints}
        self.assertEqual(task_types, {"param_conflict", "figure_confirm", "content_review"})
        blocking_by_code = {item["payload"]["code"]: item["blocking_level"] for item in blueprints}
        self.assertEqual(blocking_by_code["VAL108"], "P0")
        self.assertEqual(blocking_by_code["VAL105"], "P1")

    def test_build_review_task_blueprints_can_opt_in_final_review(self) -> None:
        outline = SimpleNamespace(id=uuid4(), outline_json={"title": "测试方案"})

        blueprints = build_review_task_blueprints(
            errors=[],
            warnings=[],
            outline=outline,
            draft_version=1,
            existing_tasks=[],
            require_final_review=True,
        )

        self.assertEqual([item["task_type"] for item in blueprints], ["final_review"])

    def test_derive_validation_status_marks_review_and_passed(self) -> None:
        self.assertEqual(
            derive_validation_status(errors=[{"code": "VAL002"}], open_review_task_count=0),
            "blocked",
        )
        self.assertEqual(
            derive_validation_status(errors=[{"code": "VAL003"}], open_review_task_count=1),
            "review_required",
        )
        self.assertEqual(derive_validation_status(errors=[], open_review_task_count=0), "passed")


if __name__ == "__main__":
    unittest.main()
