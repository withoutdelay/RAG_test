from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from app.services.composition.section_quality import (
    SectionQualityGateService,
    _estimate_section_quality_max_tokens,
    analyze_section_content_quality,
    analyze_section_heading_quality,
)
from app.services.knowledge.wiki_context import KnowledgeWikiContextProvider
from app.services.llm.prompts.section_quality import build_section_quality_prompts


class _StubLLMClient:
    async def invoke(self, request):
        if "建议插入图表" in request.user_prompt or "### A. 概述" in request.user_prompt:
            payload = {
                "pass": False,
                "score": 0.61,
                "summary": "章节存在内部标题和风格不一致的小标题。",
                "issues": [
                    {
                        "code": "SQLLM01",
                        "severity": "high",
                        "target": "建议插入图表",
                        "message": "存在内部编辑标题。",
                        "suggested_fix": "删除内部标题，把资产自然融入正文。",
                    }
                ],
                "rewrite_instruction": "统一小标题风格，删除内部标题，并保留技术信息密度。",
            }
        else:
            payload = {
                "pass": True,
                "score": 0.92,
                "summary": "章节标题风格和客户口径均已达标。",
                "issues": [],
                "rewrite_instruction": "保持当前表达。",
            }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _StubExecutor:
    async def rewrite_section(self, *, task_id, section_context, selected_text, instruction, global_params):
        rewritten = selected_text.replace("### A. 概述", "### 系统组成与控制分工")
        rewritten = rewritten.replace("### 建议插入图表\n\n", "")
        return SimpleNamespace(content=rewritten)


class _CaptureExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def rewrite_section(self, *, task_id, section_context, selected_text, instruction, global_params):
        self.calls.append(
            {
                "task_id": task_id,
                "section_context": section_context,
                "instruction": instruction,
                "selected_text": selected_text,
            }
        )
        rewritten = selected_text.replace("我公司", "本方案")
        rewritten = rewritten.replace("VFD", "变频器")
        return SimpleNamespace(content=rewritten)


class _FailingLLMClient:
    async def invoke(self, request):
        raise RuntimeError("relay unavailable")


class _StringIssueLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": True,
            "score": 0.91,
            "summary": "章节基本可用，但模型返回了字符串格式建议。",
            "issues": ["建议进一步统一标题命名风格。"],
            "rewrite_instruction": "保持当前内容，仅按需统一标题风格。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _MetadataOnlyLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.9,
            "summary": "章节存在可优化项。",
            "issues": [
                {
                    "code": "INTERNAL_METADATA_PRESENT",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "输入中包含推荐资产等上下文信息。",
                    "suggested_fix": "不要把内部提示信息写入正文。",
                },
                {
                    "code": "TITLE_STYLE_GENERIC",
                    "severity": "low",
                    "target": "控制方案",
                    "message": "标题可再凝练。",
                    "suggested_fix": "统一标题风格。",
                },
            ],
            "rewrite_instruction": "统一标题风格即可。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _MinorIssueLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节存在少量可优化项。",
            "issues": [
                {
                    "code": "TITLE_STYLE_MINOR",
                    "severity": "medium",
                    "target": "5.3 测温/振动与辅机接口",
                    "message": "标题风格可再收束。",
                    "suggested_fix": "统一标题命名风格。",
                },
                {
                    "code": "REPETITION_MINOR",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "未确认项提示语略有重复。",
                    "suggested_fix": "将未确认项集中到末尾统一归纳。",
                },
                {
                    "code": "CUSTOMER_TONE_MINOR",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "少量语句偏模板化。",
                    "suggested_fix": "强化项目改造场景限定语。",
                },
                {
                    "code": "CONTENT_BOUNDARY_GENERAL",
                    "severity": "medium",
                    "target": "励磁与转子回路接口",
                    "message": "接口边界还可更具体。",
                    "suggested_fix": "补充信号类别、控制方向和保护出口归属。",
                },
            ],
            "rewrite_instruction": "统一标题风格，并将未确认项集中到章节末尾。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _SoftIssueLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节基本达标，但仍有少量可优化项。",
            "issues": [
                {
                    "code": "TITLE_SCOPE_SOFT",
                    "severity": "medium",
                    "target": "项目背景",
                    "message": "个别小标题与章节总目标贴合度可继续增强。",
                    "suggested_fix": "将标题改得更贴近章节目标。",
                },
                {
                    "code": "CONTENT_REDUNDANCY",
                    "severity": "medium",
                    "target": "建设原则/实施边界",
                    "message": "存在一定信息重复。",
                    "suggested_fix": "合并重复表述并收束措辞。",
                },
                {
                    "code": "WORDING_GENERIC",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "部分表述略泛。",
                    "suggested_fix": "用更具体的技术表达替代泛化描述。",
                },
                {
                    "code": "NAMING_CONSISTENCY",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "局部命名风格可再统一。",
                    "suggested_fix": "统一对象命名和标题风格。",
                },
                {
                    "code": "CUSTOMER_TONE_SOFT",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "商务表达可更凝练。",
                    "suggested_fix": "收束措辞，提升客户稿口径的一致性。",
                },
            ],
            "rewrite_instruction": "统一标题和命名风格，减少重复表述，保持技术内容不变。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreSoftMediumLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体已达到客户可读门槛，仅存在少量可优化项。",
            "issues": [
                {
                    "code": "TITLE_GENERIC",
                    "severity": "medium",
                    "target": "4.8 方案说明",
                    "message": "末节标题较宽泛。",
                    "suggested_fix": "改为更明确的总结或边界类标题。",
                },
                {
                    "code": "SCOPE_BLEND",
                    "severity": "medium",
                    "target": "4.4 高压变频器接入与控制策略",
                    "message": "局部内容略超出综合自动化章节边界。",
                    "suggested_fix": "收束职责边界，明确控制主体。",
                },
                {
                    "code": "TECH_RISK_WORDING",
                    "severity": "medium",
                    "target": "4.4 高压变频器接入与控制策略",
                    "message": "个别控制主体表述可能引发理解歧义。",
                    "suggested_fix": "改为更稳妥的接口/联锁执行表述。",
                },
            ],
            "rewrite_instruction": "统一标题风格并收束职责边界。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreVfdSoftMediumLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体已具备客户可读性，仅剩少量口径和收束问题。",
            "issues": [
                {
                    "code": "TITLE_OVERLAP",
                    "severity": "medium",
                    "target": "章节小标题",
                    "message": "局部标题与上一级标题语义重叠。",
                    "suggested_fix": "压缩重复标题词汇。",
                },
                {
                    "code": "PARAMETER_COMMITMENT_RISK",
                    "severity": "medium",
                    "target": "参数描述",
                    "message": "个别参数性描述表述偏定值承诺。",
                    "suggested_fix": "改为推荐值、参考值或最终联调确认口径。",
                },
                {
                    "code": "CONTROL_SCOPE_GENERAL",
                    "severity": "medium",
                    "target": "控制分工",
                    "message": "控制分工边界还可更凝练。",
                    "suggested_fix": "收束控制职责边界。",
                },
                {
                    "code": "TECHNICAL_CAUTION",
                    "severity": "medium",
                    "target": "技术表述",
                    "message": "个别句式需更谨慎。",
                    "suggested_fix": "改为更稳妥的工程口径。",
                },
                {
                    "code": "CLOSING_SECTION_WEAK",
                    "severity": "medium",
                    "target": "收束段",
                    "message": "章节收束偏弱。",
                    "suggested_fix": "补一段边界说明或小结。",
                },
            ],
            "rewrite_instruction": "统一标题和参数措辞，收束章节结尾。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreControlSoftMediumLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节主体已经完整，仍有少量可收束项。",
            "issues": [
                {
                    "code": "WORDING_CUSTOMER_TONE",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "少量措辞还可更偏客户稿。",
                    "suggested_fix": "收束为正式方案口径。",
                },
                {
                    "code": "PARAMETER_PREMATURE_COMMITMENT",
                    "severity": "medium",
                    "target": "保护定值",
                    "message": "局部参数表述偏提前承诺。",
                    "suggested_fix": "改为后续联调确认。",
                },
                {
                    "code": "TITLE_STYLE_OPTIMIZATION",
                    "severity": "medium",
                    "target": "小标题",
                    "message": "小标题还可更紧凑。",
                    "suggested_fix": "统一标题风格。",
                },
                {
                    "code": "CONTENT_PRECISION",
                    "severity": "medium",
                    "target": "控制保护表述",
                    "message": "个别表述还可更精确。",
                    "suggested_fix": "补强执行主体和边界条件。",
                },
            ],
            "rewrite_instruction": "统一客户口径，收束参数承诺表达。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreVfdRefineLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体可用，仅剩范围和措辞层面的可优化项。",
            "issues": [
                {
                    "code": "TITLE_SCOPE_MISMATCH",
                    "severity": "medium",
                    "target": "章节标题",
                    "message": "标题与章节重心贴合度还可增强。",
                    "suggested_fix": "收束标题与内容重心。",
                },
                {
                    "code": "GOAL_COVERAGE_WEAK",
                    "severity": "medium",
                    "target": "章节目标",
                    "message": "章节目标与收束段呼应仍可加强。",
                    "suggested_fix": "补强目标闭环表达。",
                },
                {
                    "code": "PARAMETER_COMMITMENT_RISK",
                    "severity": "medium",
                    "target": "参数措辞",
                    "message": "局部参数措辞偏提前承诺。",
                    "suggested_fix": "改为参考或联调确认口径。",
                },
                {
                    "code": "CUSTOMER_TONE_REFINEMENT",
                    "severity": "medium",
                    "target": "章节整体",
                    "message": "客户口径还可收束。",
                    "suggested_fix": "统一为正式技术方案语气。",
                },
                {
                    "code": "TERMINOLOGY_CONSISTENCY",
                    "severity": "medium",
                    "target": "术语命名",
                    "message": "局部术语表达还可统一。",
                    "suggested_fix": "统一术语命名。",
                },
            ],
            "rewrite_instruction": "统一标题、术语和客户口径。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreControlRefineLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节主体已完整，仅剩少量细化问题。",
            "issues": [
                {
                    "code": "TITLE_SCOPE_REFINEMENT",
                    "severity": "medium",
                    "target": "章节标题",
                    "message": "标题范围仍可更聚焦。",
                    "suggested_fix": "收束标题与段落职责。",
                },
                {
                    "code": "TECH_APPLICABILITY_AMBIGUOUS",
                    "severity": "medium",
                    "target": "技术适用边界",
                    "message": "个别适用边界表达略泛。",
                    "suggested_fix": "补充适用条件或实施边界。",
                },
                {
                    "code": "TECH_TERM_CAUTION",
                    "severity": "medium",
                    "target": "技术措辞",
                    "message": "个别技术术语仍可更谨慎。",
                    "suggested_fix": "改为更稳妥的工程表述。",
                },
                {
                    "code": "CONSISTENCY_DETAIL_LEVEL",
                    "severity": "medium",
                    "target": "章节颗粒度",
                    "message": "局部细节颗粒度不完全一致。",
                    "suggested_fix": "统一章节细节层级。",
                },
                {
                    "code": "CLIENT_TONE_OPTIMIZATION",
                    "severity": "medium",
                    "target": "客户口径",
                    "message": "客户口径还可优化。",
                    "suggested_fix": "收束为正式客户稿语气。",
                },
            ],
            "rewrite_instruction": "统一标题范围、术语和客户口径。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreVfdRefine2LLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体已可用，仅剩标题、术语和客户口径层面的可优化项。",
            "issues": [
                {
                    "code": "TITLE_HIERARCHY_INCONSISTENT",
                    "severity": "medium",
                    "target": "标题层级",
                    "message": "局部标题层级感还可更统一。",
                    "suggested_fix": "统一标题层级和命名关系。",
                },
                {
                    "code": "TERM_INCONSISTENT",
                    "severity": "medium",
                    "target": "术语命名",
                    "message": "个别术语前后表达不完全一致。",
                    "suggested_fix": "统一术语命名。",
                },
                {
                    "code": "TECH_SCOPE_ALIGNMENT",
                    "severity": "medium",
                    "target": "技术范围",
                    "message": "技术范围与章节主题还可更贴合。",
                    "suggested_fix": "收束到配置章节主线。",
                },
                {
                    "code": "CUSTOMER_CLARITY",
                    "severity": "medium",
                    "target": "客户口径",
                    "message": "客户视角的收束还可更清晰。",
                    "suggested_fix": "强化客户可读性的结论表达。",
                },
                {
                    "code": "PARAMETER_SOURCE_RISK",
                    "severity": "medium",
                    "target": "参数来源",
                    "message": "个别参数来源说明仍可更谨慎。",
                    "suggested_fix": "明确为参考值或来源待确认。",
                },
            ],
            "rewrite_instruction": "统一标题、术语和参数来源口径。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreLciPostRewriteLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体结构完整，主要问题属于术语、承诺边界和章节收束层面的优化项，不构成阻断。",
            "issues": [
                {
                    "code": "TERM_CONSISTENCY",
                    "severity": "medium",
                    "target": "4.1-4.5 全章",
                    "message": "LCI/SFC、LCI装置和变频器等称谓交替出现。",
                    "suggested_fix": "统一术语口径。",
                },
                {
                    "code": "COMMITMENT_RISK",
                    "severity": "medium",
                    "target": "4.1 系统配置方案",
                    "message": "典型起动时间表述可能被理解为固定承诺。",
                    "suggested_fix": "补充参考值和现场条件确认口径。",
                },
                {
                    "code": "TECH_PRECISION",
                    "severity": "medium",
                    "target": "4.2.3 同步建立控制",
                    "message": "控制职责边界可更稳妥。",
                    "suggested_fix": "改为接口配合和同步判据口径。",
                },
                {
                    "code": "STYLE_REDUNDANCY",
                    "severity": "low",
                    "target": "4.4 装置级技术特点",
                    "message": "部分内容与前文重复。",
                    "suggested_fix": "压缩为项目相关特点。",
                },
                {
                    "code": "STRUCTURE_OVERLAP",
                    "severity": "low",
                    "target": "4.5 单线方案说明",
                    "message": "与前文存在一定重复。",
                    "suggested_fix": "聚焦单线图阅读重点。",
                },
                {
                    "code": "TITLE_TIGHTENING",
                    "severity": "low",
                    "target": "4.6 本章范围说明",
                    "message": "章末标题略显模板化。",
                    "suggested_fix": "改为本章小结或并入结尾段。",
                },
            ],
            "rewrite_instruction": "统一术语、补充参数边界并压缩重复内容。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreInterlockPostRewriteLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节整体可用，剩余问题属于标题命名、接口细化和逻辑衔接层面的优化项。",
            "issues": [
                {
                    "code": "TITLE_GENERIC",
                    "severity": "medium",
                    "target": "7.1 方案概述 / 7.8 实施说明",
                    "message": "个别小标题名称较泛。",
                    "suggested_fix": "改为功能型标题。",
                },
                {
                    "code": "CONTENT_ABSTRACT",
                    "severity": "medium",
                    "target": "7.4 接口章节",
                    "message": "接口部分仍以原则性表述为主。",
                    "suggested_fix": "补充硬接点/通讯边界和状态量/命令量分类。",
                },
                {
                    "code": "CONTROL_SCOPE_CLARITY",
                    "severity": "medium",
                    "target": "7.2 电机控制盘功能配置",
                    "message": "本地控制单元与电机控制盘关系可进一步统一。",
                    "suggested_fix": "统一术语并明确包含关系。",
                },
                {
                    "code": "CLIENT_TONE_REFINEMENT",
                    "severity": "medium",
                    "target": "7.6 联锁保护设计",
                    "message": "局部客户表达还可更稳妥。",
                    "suggested_fix": "收束为正式客户稿语气。",
                },
                {
                    "code": "STRUCTURE_TIGHTENING",
                    "severity": "medium",
                    "target": "全文",
                    "message": "章节之间的逻辑衔接可再增强。",
                    "suggested_fix": "强化控制架构、接口信号、动作逻辑和故障处理主线。",
                },
            ],
            "rewrite_instruction": "统一标题、术语和接口口径。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class _HighScoreCommissioningSoftMediumLLMClient:
    async def invoke(self, request):
        payload = {
            "pass": False,
            "score": 0.96,
            "summary": "章节已具备客户阅读条件，剩余问题属于服务口径和颗粒度优化。",
            "issues": [
                {
                    "code": "TONE_CONTRACT_HEAVY",
                    "severity": "medium",
                    "target": "验收与服务条款",
                    "message": "局部表达偏合同条款。",
                    "suggested_fix": "收束为技术方案口径。",
                },
                {
                    "code": "TITLE_SCOPE_SOFT",
                    "severity": "medium",
                    "target": "章节收尾",
                    "message": "标题范围可再贴合调试验收主题。",
                    "suggested_fix": "调整小标题。",
                },
                {
                    "code": "TRAINING_WORDING_RISK",
                    "severity": "medium",
                    "target": "培训内容",
                    "message": "培训承诺可再收束。",
                    "suggested_fix": "改为按交付资料和现场培训计划执行。",
                },
                {
                    "code": "DEBUG_TEST_GRANULARITY",
                    "severity": "medium",
                    "target": "调试内容",
                    "message": "两台电机切换验证颗粒度可再明确。",
                    "suggested_fix": "补充分别验证口径。",
                },
            ],
            "rewrite_instruction": "收束合同口径并补充调试颗粒度。",
        }
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


class SectionQualityGateTests(unittest.TestCase):
    def test_estimate_section_quality_max_tokens_scales_for_long_asset_heavy_sections(self) -> None:
        short_budget = _estimate_section_quality_max_tokens(content_md="## 章节\n\n正文。", recommended_assets=[])
        long_budget = _estimate_section_quality_max_tokens(
            content_md="## 章节\n\n" + ("变频器、输入变压器、输出变压器和同步切换控制逻辑。" * 180),
            recommended_assets=[{"asset_id": str(index), "asset_type": "figure"} for index in range(3)],
        )

        self.assertEqual(short_budget, 1400)
        self.assertGreater(long_budget, short_budget)
        self.assertLessEqual(long_budget, 2600)

    def test_analyze_section_heading_quality_flags_internal_and_latin_headings(self) -> None:
        issues = analyze_section_heading_quality(
            section_title="总体方案",
            content_md=(
                "## 总体方案\n\n"
                "### A. 概述\n\n"
                "正文。\n\n"
                "### 建议插入图表\n\n"
                "- [[ASSET:FIGURE:1]] 图1\n"
            ),
        )

        self.assertEqual({issue.code for issue in issues}, {"SQ001", "SQ002"})

    def test_analyze_section_content_quality_flags_fluff_internal_voice_and_duplicates(self) -> None:
        issues = analyze_section_content_quality(
            section={"title": "主回路系统方案", "section_class": "architecture"},
            content_md=(
                "## 主回路系统方案\n\n"
                "高压变频器主回路采用移相整流变压器配合功率单元串联结构，满足安全隔离和旁路切换要求。\n\n"
                "高压变频器主回路采用移相整流变压器配合功率单元串联结构，满足安全隔离和旁路切换要求。\n\n"
                "经过我们团队的深入研究，本系统具有先进性和可靠性。"
                "同时，输入侧设置隔离开关、快速熔断器和真空接触器，输出侧经旁路切换柜送至同步电机，"
                "并由本地控制单元与上位机系统完成联锁和状态交互。\n"
            ),
        )

        self.assertTrue({"SQ010", "SQ011", "SQ012"}.issubset({issue.code for issue in issues}))

    def test_analyze_section_content_quality_flags_short_technical_section(self) -> None:
        issues = analyze_section_content_quality(
            section={"title": "控制系统方案", "section_class": "architecture"},
            content_md="## 控制系统方案\n\n系统采用 PLC 控制。\n",
        )

        self.assertEqual({issue.code for issue in issues}, {"SQ013"})

    def test_analyze_section_content_quality_allows_customer_facing_commissioning_completion(self) -> None:
        issues = analyze_section_content_quality(
            section={"title": "项目实施计划", "section_class": "implementation"},
            content_md=(
                "## 项目实施计划\n\n"
                "系统调试完成后，应完成投运前功能确认、保护联锁核对和运行状态复核，"
                "确保设备满足现场投运条件并形成交付记录。\n"
            ),
        )

        self.assertNotIn("SQ011", {issue.code for issue in issues})

    def test_analyze_section_content_quality_flags_legacy_scenario_drift(self) -> None:
        issues = analyze_section_content_quality(
            section={
                "title": "4 110kV变电站综合自动化系统方案",
                "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构、监控策略和远方通信。",
                "keywords": ["综合自动化系统", "站控层", "间隔层", "网络层"],
            },
            content_md=(
                "## 4 110kV变电站综合自动化系统方案\n\n"
                "### LCI 变频软起系统方案\n\n"
                "磨机总启动时间约69s，LCI大约需要5s切换内部晶闸管换相模式，"
                "到达工频后电机与电网同步约30s。\n"
            ),
        )

        self.assertIn("SQ014", {issue.code for issue in issues})

    def test_analyze_section_content_quality_flags_technical_scope_drift_headings(self) -> None:
        issues = analyze_section_content_quality(
            section={
                "title": "第三章 高压变频系统总体方案",
                "purpose": "提供变频改造主接线拓扑与系统架构设计，说明功率单元、冷却方式和柜体布置。",
                "section_class": "architecture",
                "expected_evidence_types": ["section", "figure"],
            },
            content_md=(
                "## 第三章 高压变频系统总体方案\n\n"
                "### 主回路拓扑说明\n\n"
                "高压变频器主回路采用输入隔离、变频器、输出隔离和旁路回路组成。\n\n"
                "### 培训计划\n\n"
                "供方提供操作培训和售后服务。\n"
            ),
        )

        self.assertIn("SQ015", {issue.code for issue in issues})

    def test_analyze_section_content_quality_allows_training_headings_in_service_sections(self) -> None:
        issues = analyze_section_content_quality(
            section={
                "title": "8 调试、验收与运维服务",
                "purpose": "说明调试、验收、培训和运维服务安排。",
                "section_class": "implementation",
            },
            content_md=(
                "## 8 调试、验收与运维服务\n\n"
                "### 验收与培训\n\n"
                "调试完成后，双方按确认的验收项目进行检查，并形成问题闭环记录。培训内容围绕设备组成、"
                "操作流程和日常维护注意事项展开。\n"
            ),
        )

        self.assertNotIn("SQ015", {issue.code for issue in issues})

    def test_analyze_section_content_quality_allows_current_project_scenario_terms(self) -> None:
        issues = analyze_section_content_quality(
            section={
                "title": "7 电机控制盘、励磁与接口联锁方案",
                "purpose": "说明电机控制盘、励磁系统、DCS/PLC 接口、断路器反馈、联锁保护和故障诊断设计。",
                "keywords": ["电机控制盘", "励磁", "DCS", "PLC", "联锁", "断路器反馈"],
            },
            content_md=(
                "## 7 电机控制盘、励磁与接口联锁方案\n\n"
                "本项目按高炉鼓风机连续运行工况配置电机控制盘、励磁系统和DCS/PLC接口，"
                "用于同步电机变频起动、同步切换及工频运行期间的状态监视、故障告警和联锁保护。"
                "LCI/SFC变频软起动系统负责升速与同步切换控制，电机控制盘负责断路器反馈、"
                "励磁就绪、保护闭锁和上位系统接口信号的集中处理。"
            ),
            global_params={
                "project_name": "某钢铁集团高炉鼓风机电机及 LCI 变频软起动系统改造项目",
                "product_line": "lci",
                "industry": "钢铁",
            },
        )

        self.assertNotIn("SQ014", {issue.code for issue in issues})

    def test_review_rejects_high_score_content_with_legacy_scenario_drift(self) -> None:
        service = SectionQualityGateService(
            llm_client=_StubLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-scenario-drift",
                section={
                    "title": "4 110kV变电站综合自动化系统方案",
                    "purpose": "围绕站控层、间隔层和网络层阐述综合自动化系统架构、监控策略和远方通信。",
                    "keywords": ["综合自动化系统", "站控层", "间隔层", "网络层"],
                },
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 4 110kV变电站综合自动化系统方案\n\n"
                    "### LCI 变频软起系统方案\n\n"
                    "磨机总启动时间约69s，LCI大约需要5s切换内部晶闸管换相模式，"
                    "到达工频后电机与电网同步约30s。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertFalse(review.passed)
        self.assertIn("SQ014", {issue.code for issue in review.issues})

    def test_review_accepts_string_issues_from_llm(self) -> None:
        service = SectionQualityGateService(
            llm_client=_StringIssueLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-string-issue",
                section={"title": "控制接口方案", "purpose": "说明控制接口和联锁边界。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 控制接口方案\n\n"
                    "本节围绕控制接口、状态反馈和联锁保护边界进行说明。系统通过硬接线和通讯链路"
                    "完成启停指令、运行状态、故障报警和保护闭锁信号交互，并保留现场联调阶段对"
                    "信号点表、通讯规约和保护出口归属进行确认的空间。\n"
                ),
                recommended_assets=[],
            )
        )

        llm_issue = next(issue for issue in review.issues if issue.code == "SQLLM")
        self.assertEqual(llm_issue.severity, "low")
        self.assertIn("统一标题命名风格", llm_issue.message)

    def test_review_and_repair_rewrites_bad_headings_before_returning(self) -> None:
        service = SectionQualityGateService(
            llm_client=_StubLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-test",
                section={"title": "3 总体方案", "purpose": "说明总体控制和主回路方案。"},
                outline_title="测试项目技术方案",
                global_params={"voltage_level": "10kV"},
                content_md=(
                    "## 3 总体方案\n\n"
                    "### A. 概述\n\n"
                    "本节说明系统组成。\n\n"
                    "### 建议插入图表\n\n"
                    "- [[ASSET:FIGURE:1]] 主回路图\n"
                ),
                recommended_assets=[{"asset_id": "1", "asset_type": "figure", "title": "主回路图"}],
                allow_rewrite=True,
            )
        )

        self.assertEqual(status, "generated")
        self.assertNotIn("### A. 概述", content)
        self.assertNotIn("建议插入图表", content)
        self.assertIn("### 系统组成与控制分工", content)
        self.assertTrue(meta["rewrite_attempted"])
        self.assertTrue(meta["rewrite_applied"])
        self.assertEqual(meta["status"], "passed")

    def test_review_allows_rule_only_pass_when_llm_unavailable_and_content_clean(self) -> None:
        service = SectionQualityGateService(
            llm_client=_FailingLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-fallback",
                section={"title": "3 LCI变频软起总体方案", "purpose": "说明LCI变频软起总体控制和主回路方案。"},
                outline_title="测试项目技术方案",
                global_params={"voltage_level": "10kV"},
                content_md=(
                    "## 3 总体方案\n\n"
                    "### 系统组成与控制分工\n\n"
                    "本地控制单元 PLC 负责对主回路、油站和冷却系统进行监控，并与上位机系统完成接口联动。"
                    "系统通过硬接线与通讯总线协同实现启停指令、状态反馈、报警和故障闭锁，"
                    "同时对励磁回路、润滑油站和冷却器工作状态进行持续监视，"
                    "以保证同步电机在变频软起和工频运行两种工况下均具备稳定的控制边界和保护逻辑。\n"
                ),
                recommended_assets=[],
                allow_rewrite=True,
            )
        )

        self.assertEqual(status, "generated")
        self.assertEqual(meta["status"], "passed")
        self.assertFalse(meta["rewrite_applied"])
        self.assertIn("SQ999", {item["code"] for item in meta["issues"]})
        fallback_issue = next(item for item in meta["issues"] if item["code"] == "SQ999")
        self.assertEqual(fallback_issue["severity"], "low")

    def test_review_passes_when_only_metadata_and_style_advice_remain(self) -> None:
        service = SectionQualityGateService(
            llm_client=_MetadataOnlyLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-metadata-only",
                section={"title": "7 控制系统及联锁保护方案", "purpose": "说明控制架构、联锁边界与信号接口。"},
                outline_title="测试项目技术方案",
                global_params={"voltage_level": "10kV"},
                content_md=(
                    "## 7 控制系统及联锁保护方案\n\n"
                    "### 控制架构与联锁分工\n\n"
                    "本地控制单元 PLC 与 LCI PLC 分别承担设备状态监测、启停顺控、保护闭锁和上位系统接口功能，"
                    "并通过硬接线与通讯链路实现状态反馈、报警上传和关键联锁闭环。\n"
                ),
                recommended_assets=[{"asset_id": "1", "asset_type": "figure", "title": "控制系统总图"}],
                allow_rewrite=True,
            )
        )

        self.assertEqual(content.splitlines()[0], "## 7 控制系统及联锁保护方案")
        self.assertEqual(status, "generated")
        self.assertEqual(meta["status"], "passed")
        metadata_issue = next(item for item in meta["issues"] if item["code"] == "INTERNAL_METADATA_PRESENT")
        self.assertEqual(metadata_issue["severity"], "low")
        self.assertFalse(meta["rewrite_applied"])

    def test_review_passes_when_only_minor_style_tone_and_one_general_issue_remain(self) -> None:
        service = SectionQualityGateService(
            llm_client=_MinorIssueLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-minor-issues",
                section={"title": "5 高炉鼓风机同步电机适配与接口方案", "purpose": "说明同步电机参数适配、接口边界和联锁配合。"},
                outline_title="测试项目技术方案",
                global_params={"voltage_level": "10kV"},
                content_md=(
                    "## 5 高炉鼓风机同步电机适配与接口方案\n\n"
                    "### 5.2 励磁与转子回路接口\n\n"
                    "本节说明励磁投入、就绪反馈、闭锁信号和保护出口边界。\n\n"
                    "### 5.5 待确认关键点\n\n"
                    "相关未明确项纳入深化设计统一确认。\n"
                ),
                recommended_assets=[],
                allow_rewrite=True,
            )
        )

        self.assertEqual(content.splitlines()[0], "## 5 高炉鼓风机同步电机适配与接口方案")
        self.assertEqual(status, "generated")
        self.assertEqual(meta["status"], "passed")
        title_issue = next(item for item in meta["issues"] if item["code"] == "TITLE_STYLE_MINOR")
        repetition_issue = next(item for item in meta["issues"] if item["code"] == "REPETITION_MINOR")
        tone_issue = next(item for item in meta["issues"] if item["code"] == "CUSTOMER_TONE_MINOR")
        self.assertEqual(title_issue["severity"], "low")
        self.assertEqual(repetition_issue["severity"], "low")
        self.assertEqual(tone_issue["severity"], "low")
        self.assertFalse(meta["rewrite_applied"])

    def test_review_passes_when_only_soft_scope_redundancy_wording_and_naming_issues_remain(self) -> None:
        service = SectionQualityGateService(
            llm_client=_SoftIssueLLMClient(),
            executor=_StubExecutor(),
        )

        content, status, meta = self._run(
            service.review_and_repair(
                task_id="quality-soft-issues",
                section={"title": "1 项目概述与建设目标", "purpose": "说明项目背景、建设目标和实施边界。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "# 1 项目概述与建设目标\n\n"
                    "## 1.1 项目背景\n\n"
                    "本项目围绕变电站智能化改造与关键辅机驱动优化展开。\n\n"
                    "## 1.2 建设目标\n\n"
                    "建设目标包括提升监控能力、运行可靠性与接口集成水平。\n\n"
                    "## 1.3 实施边界\n\n"
                    "改造范围聚焦于监控、通信、联锁和特定辅机驱动回路，不延伸至未明确纳入范围的一次设备功能。\n"
                ),
                recommended_assets=[],
                allow_rewrite=True,
            )
        )

        self.assertEqual(content.splitlines()[0], "# 1 项目概述与建设目标")
        self.assertEqual(status, "generated")
        self.assertEqual(meta["status"], "passed")
        self.assertFalse(meta["rewrite_attempted"])
        self.assertFalse(meta["rewrite_applied"])

    def test_review_passes_when_high_score_only_has_soft_medium_architecture_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreSoftMediumLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-soft-medium",
                section={"title": "4 110kV变电站综合自动化系统方案", "purpose": "说明综自系统总体设计、监控策略和接口边界。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 4 110kV变电站综合自动化系统方案\n\n"
                    "### 4.1 系统总体方案\n\n"
                    "本章围绕站控层、间隔层和网络层展开说明，并给出监控管理、数据采集、告警联动和接口协同策略。"
                    "系统通过站控主机、操作员工作站、间隔层测控单元和网络交换设备构成统一的综合自动化平台，"
                    "实现站内一次设备状态监视、遥测遥信采集、事件顺序记录、告警管理及远方通信接口。"
                    "站控层侧重集中监视、操作授权、运行报表和历史数据管理，间隔层负责测控保护、状态采集与执行接口，"
                    "网络层承担站控层与间隔层之间以及与上级调度、集控平台之间的数据承载和通信隔离，"
                    "并满足改造条件下对既有监控回路、告警链路和接口边界的兼容要求。\n\n"
                    "### 4.4 高压变频器接入与控制策略\n\n"
                    "针对高压变频器及其辅助设备，综合自动化系统负责运行状态监视、告警联动、数据转发和授权后的命令接口管理，"
                    "具体启动、同步切换和励磁执行仍由变频器控制单元、PLC 及相关专用装置完成，以明确职责边界并保持系统接口清晰。"
                    "在接口实现上，系统重点接入运行方式、故障状态、关键联锁、远方许可和典型告警信息，"
                    "并通过标准化通信对象和授权控制流程将相关信息纳入变电站综合自动化平台统一展示与管理，"
                    "从而保证综自系统、专用控制装置和站内既有系统之间的职责划分清晰、接口链路可追踪、运行管理口径一致。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual([item.code for item in review.issues], ["TITLE_GENERIC", "SCOPE_BLEND", "TECH_RISK_WORDING"])

    def test_review_passes_when_high_score_only_has_soft_medium_vfd_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreVfdSoftMediumLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-vfd-soft-medium",
                section={"title": "6 HV-VFD高压变频器配置方案", "purpose": "说明高压变频器配置、控制分工和参数边界。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 6 HV-VFD高压变频器配置方案\n\n"
                    "### 6.1 系统配置原则\n\n"
                    "高压变频器配置围绕输入侧隔离、整流逆变单元、输出侧切换和辅助系统接口展开，"
                    "兼顾设备选型、运行监视、保护协同和调试实施边界。"
                    "方案阶段重点说明设备构成、主回路协同关系、就地与远方控制接口、告警采集和实施边界，"
                    "并对需要结合现场联调确认的定值、切换逻辑和保护配合保持审慎表达，"
                    "避免在售前阶段提前固化最终参数承诺。\n\n"
                    "### 6.3 控制分工与参数边界\n\n"
                    "针对运行控制、状态采集、告警联动和通信接口，方案明确上位监控系统、PLC、专用控制单元和现场执行回路之间的职责边界，"
                    "避免在方案阶段对最终定值、联调结果和现场整定值作出过早承诺。"
                    "对于启停顺控、保护闭锁、远方许可、故障旁路和关键状态反馈，章节采用职责分工加接口边界的表达方式，"
                    "既保留足够的工程可读性，也避免形成超出当前阶段的信息承诺。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TITLE_OVERLAP",
                "PARAMETER_COMMITMENT_RISK",
                "CONTROL_SCOPE_GENERAL",
                "TECHNICAL_CAUTION",
                "CLOSING_SECTION_WEAK",
            ],
        )

    def test_review_passes_when_high_score_only_has_soft_medium_control_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreControlSoftMediumLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-control-soft-medium",
                section={"title": "8 控制保护与系统可靠性设计", "purpose": "说明保护配置、联锁边界和可靠性设计原则。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 8 控制保护与系统可靠性设计\n\n"
                    "### 8.1 控制与保护总体原则\n\n"
                    "章节围绕控制链路、保护配置、联锁策略、告警管理和可靠性设计展开，强调执行主体、保护出口和故障闭锁边界的清晰划分。"
                    "同时结合主设备控制单元、PLC、上位监控和现场执行回路之间的协同关系，"
                    "说明故障闭锁、状态采集、告警分级和人工干预边界，确保方案阶段的控制责任和保护责任表述清晰。"
                    "在保护协同方面，章节进一步强调测点采集、保护出口、上送告警、人工复归和旁路条件之间的接口逻辑，"
                    "保证控制链路、保护链路和运维链路的工程边界一致。\n\n"
                    "### 8.4 可靠性与运维边界\n\n"
                    "方案阶段重点说明冗余设计、告警分级、故障记录、人工旁路条件和后续联调整定边界，"
                    "对需结合现场整定和联调确认的参数保持审慎表达，不在方案阶段提前固化。"
                    "对于保护定值、延时整定、闭锁门槛和恢复条件，仅说明设计原则、影响因素和联调确认机制，"
                    "不直接承诺最终执行值，以保证客户口径、工程边界和后续实施条件保持一致。"
                    "同时说明故障记录、冗余切换、人工干预、运维授权和后续联调确认之间的配合机制，"
                    "确保可靠性设计既满足工程可读性，又不在售前阶段越过实施与整定边界。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "WORDING_CUSTOMER_TONE",
                "PARAMETER_PREMATURE_COMMITMENT",
                "TITLE_STYLE_OPTIMIZATION",
                "CONTENT_PRECISION",
            ],
        )

    def test_review_passes_when_high_score_only_has_soft_medium_vfd_refine_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreVfdRefineLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-vfd-refine",
                section={"title": "6 HV-VFD高压变频器配置方案", "purpose": "说明配置边界、参数口径和客户交付表达。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 6 HV-VFD高压变频器配置方案\n\n"
                    "### 6.1 配置边界与系统协同\n\n"
                    "章节围绕设备构成、系统协同、参数表达和交付边界展开，说明主回路接口、控制职责、参数口径和实施边界。"
                    "对需要结合现场联调确认的内容保持审慎表述，避免形成超出当前阶段的信息承诺。"
                    "同时补充输入侧、输出侧、旁路切换、辅助系统监视和远方通信之间的接口关系，"
                    "保证高压变频器配置说明、控制分工说明和交付边界说明之间的层次清晰、颗粒度一致。\n\n"
                    "### 6.4 参数表达与客户交付口径\n\n"
                    "针对参数、接口和性能指标，章节采用推荐值、参考值与联调确认机制相结合的表达方式，"
                    "保证客户可读性、术语一致性和交付口径稳定，同时避免在售前阶段提前固化最终执行值。"
                    "对需要现场校核、联调确认和实施深化的内容，仅说明影响因素、接口对象和确认机制，"
                    "不把实施阶段才应形成的最终参数写成售前阶段的刚性承诺。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TITLE_SCOPE_MISMATCH",
                "GOAL_COVERAGE_WEAK",
                "PARAMETER_COMMITMENT_RISK",
                "CUSTOMER_TONE_REFINEMENT",
                "TERMINOLOGY_CONSISTENCY",
            ],
        )

    def test_review_passes_when_high_score_only_has_soft_medium_control_refine_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreControlRefineLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-control-refine",
                section={"title": "8 控制保护与系统可靠性设计", "purpose": "说明控制保护适用边界、术语口径和客户稿表达。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 8 控制保护与系统可靠性设计\n\n"
                    "### 8.1 控制保护设计原则\n\n"
                    "章节围绕控制职责、保护出口、联锁边界和可靠性设计展开，说明各类控制链路、保护链路和运维链路的适用条件与责任边界。"
                    "通过统一的章节颗粒度和工程口径，保证方案表达既具备客户可读性，也不过早跨越实施边界。"
                    "同时补充状态采集、告警分级、故障闭锁、人工复归、运维授权和记录追溯之间的协同关系，"
                    "使控制保护链路、可靠性链路和运维链路的适用边界表达保持一致。\n\n"
                    "### 8.5 适用边界与客户口径\n\n"
                    "对于需结合现场整定、联调验证和运维授权机制确认的内容，章节采用原则性说明加实施边界约束的方式表达，"
                    "避免在售前阶段将技术适用性、最终定值和客户交付边界混写，保持术语口径、细节层级和客户语气一致。"
                    "对于需在实施阶段结合现场条件确认的保护整定、延时逻辑和旁路条件，仅说明适用原则和确认流程，"
                    "不提前写成最终结论，以维持客户口径、工程边界和实施责任的一致性。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TITLE_SCOPE_REFINEMENT",
                "TECH_APPLICABILITY_AMBIGUOUS",
                "TECH_TERM_CAUTION",
                "CONSISTENCY_DETAIL_LEVEL",
                "CLIENT_TONE_OPTIMIZATION",
            ],
        )

    def test_review_passes_when_high_score_only_has_soft_medium_vfd_refine_second_wave_issues(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreVfdRefine2LLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-vfd-refine-second-wave",
                section={"title": "6 HV-VFD高压变频器配置方案", "purpose": "说明配置章节的标题层级、术语口径和参数来源表达。"},
                outline_title="测试项目技术方案",
                global_params={"project_name": "测试项目"},
                content_md=(
                    "## 6 HV-VFD高压变频器配置方案\n\n"
                    "### 6.1 配置结构与主线说明\n\n"
                    "章节围绕设备构成、控制边界、参数表达和交付边界展开，说明输入侧、输出侧、监视接口和实施边界之间的关系。"
                    "对需结合现场联调确认的参数和性能指标，采用参考值、来源说明和后续确认机制相结合的表达方式，"
                    "避免将实施阶段才应最终确认的内容提前写成售前承诺。"
                    "同时补充设备配置、监视接口、控制职责和交付边界之间的对应关系，使章节标题层级、术语命名和技术范围保持一致，"
                    "避免出现参数说明和交付说明彼此脱节的问题。\n\n"
                    "### 6.5 参数口径与客户说明\n\n"
                    "针对关键参数、接口条件和交付范围，章节采用统一术语和统一标题层级进行收束，"
                    "并补充参数来源、参考口径和确认路径，保证客户可读性、章节主线和参数表达边界保持一致。"
                    "对需要结合实施深化、联调确认和现场校核的内容，仅说明来源依据、确认机制和适用范围，"
                    "不将后续实施阶段形成的信息写成售前阶段的刚性承诺。\n"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TITLE_HIERARCHY_INCONSISTENT",
                "TERM_INCONSISTENT",
                "TECH_SCOPE_ALIGNMENT",
                "CUSTOMER_CLARITY",
                "PARAMETER_SOURCE_RISK",
            ],
        )

    def test_review_passes_when_high_score_lci_post_rewrite_only_has_non_blocking_advice(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreLciPostRewriteLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-lci-post-rewrite",
                section={"title": "4 LCI 变频软起动系统总体方案", "purpose": "说明LCI/SFC总体配置、起动同步和主回路切换关系。"},
                outline_title="高炉鼓风机电机及LCI变频软起动系统改造项目",
                global_params={"project_name": "高炉鼓风机电机及LCI变频软起动系统改造项目", "voltage_level": "10kV"},
                content_md=(
                    "## 4 LCI 变频软起动系统总体方案\n\n"
                    "### 4.1 系统配置方案\n\n"
                    "本项目采用1套LCI/SFC变频软起动系统服务2台10kV同步电机，"
                    "同一时刻仅执行1台电机的起动过程。电机完成同步切换后转入工频运行，"
                    "LCI/SFC变频软起动系统退出主功率回路，电机由电网持续供电运行。\n\n"
                    "[[ASSET:FIGURE:1]]\n\n"
                    "上述起动时间为类似工况下的典型参考，实际起动时长及连续起动能力需结合电机参数、"
                    "负载转矩、励磁配合及现场系统条件最终确定。\n\n"
                    "### 4.2 起动与同步切换过程\n\n"
                    "系统根据同步判据及与励磁调节装置的接口信号进行转速和励磁配合调整，以满足并网同步条件。"
                    "达到同步条件后，系统向运行断路器发出合闸命令，并在反馈确认后转入工频连续运行。"
                    "控制逻辑以断路器位置反馈、励磁就绪、保护闭锁、运行许可和故障返回信号为主要判断条件，"
                    "在起动、加速、同步、切换和退出各阶段分别确认主回路状态，避免软起回路与工频运行回路误并列。"
                    "DCS/PLC侧重点承担启停指令、状态显示、报警上送和必要的远方许可接口，"
                    "具体功率变换、同步判据和切换执行仍由LCI/SFC变频软起动系统及相关专用控制单元完成。"
                    "对现场尚需深化确认的保护定值、联锁延时、励磁给定和断路器动作时序，"
                    "本章仅说明总体原则和接口边界，最终以设计联络、设备参数和现场联调结果为准。\n\n"
                    "### 4.3 本章小结\n\n"
                    "本章重点说明LCI/SFC变频软起动系统的一拖二配置、同步切换边界和主回路接口关系。"
                ),
                recommended_assets=[{"asset_id": "1", "asset_type": "figure", "title": "LCI变频软起系统图"}],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TERM_CONSISTENCY",
                "COMMITMENT_RISK",
                "TECH_PRECISION",
                "STYLE_REDUNDANCY",
                "STRUCTURE_OVERLAP",
                "TITLE_TIGHTENING",
            ],
        )

    def test_review_passes_when_high_score_interlock_post_rewrite_only_has_non_blocking_advice(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreInterlockPostRewriteLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-interlock-post-rewrite",
                section={
                    "title": "7 电机控制盘、励磁与接口联锁方案",
                    "purpose": "说明电机控制盘、励磁系统、DCS/PLC 接口、断路器反馈、联锁保护和故障诊断设计。",
                    "keywords": ["电机控制盘", "励磁", "DCS", "PLC", "联锁", "断路器反馈"],
                },
                outline_title="高炉鼓风机电机及LCI变频软起动系统改造项目",
                global_params={
                    "project_name": "高炉鼓风机电机及LCI变频软起动系统改造项目",
                    "product_line": "lci",
                    "industry": "钢铁",
                },
                content_md=(
                    "## 7 电机控制盘、励磁与接口联锁方案\n\n"
                    "### 7.1 控制架构与职责边界\n\n"
                    "电机控制盘作为本地控制单元，负责LCI/SFC变频软起动系统、高压开关柜、低压配电柜、"
                    "励磁柜和辅助系统信号的集中监视与联锁处理，并与DCS/PLC完成启停、允许、报警和状态反馈接口。"
                    "LCI控制单元负责同步电机升速、同步切换和工频运行相关逻辑，电机控制盘负责接口归集、条件判断和保护闭锁。"
                    "两者通过硬接点和通信信号配合，形成控制架构、接口信号、动作逻辑和故障处理的闭环。\n\n"
                    "### 7.2 接口信号与联锁处理\n\n"
                    "接口信号按命令量、状态量、允许量和故障量分类管理。命令量包括起动、停机、复位和方式选择；"
                    "状态量包括LCI运行、断路器分合位、励磁就绪、辅助系统运行和工频运行反馈；"
                    "允许量包括起动允许、励磁允许、同步切换允许和远方操作许可；"
                    "故障量包括LCI故障、励磁故障、断路器异常、辅助系统异常和联锁保护动作。"
                    "系统根据上述信号完成起动禁止、起动中断、故障停机、报警上送和状态闭锁处理，"
                    "最终接口范围和I/O点表在设计联络及工程实施阶段确认。\n\n"
                    "[[ASSET:FIGURE:asset-001]]"
                ),
                recommended_assets=[{"asset_id": "asset-001", "asset_type": "figure", "title": "高浓磨机电控系统总图"}],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            [
                "TITLE_GENERIC",
                "CONTENT_ABSTRACT",
                "CONTROL_SCOPE_CLARITY",
                "CLIENT_TONE_REFINEMENT",
                "STRUCTURE_TIGHTENING",
            ],
        )

    def test_review_passes_when_high_score_commissioning_only_has_service_tone_advice(self) -> None:
        service = SectionQualityGateService(
            llm_client=_HighScoreCommissioningSoftMediumLLMClient(),
            executor=_StubExecutor(),
        )

        review = self._run(
            service.review(
                task_id="quality-high-score-commissioning",
                section={"title": "8 调试、验收与运维服务", "purpose": "说明调试、验收、培训和运维服务安排。"},
                outline_title="高炉鼓风机电机及LCI变频软起动系统改造项目",
                global_params={"project_name": "高炉鼓风机电机及LCI变频软起动系统改造项目"},
                content_md=(
                    "## 8 调试、验收与运维服务\n\n"
                    "### 8.1 现场调试服务\n\n"
                    "供方在现场条件具备后实施软起系统调试，验证控制电源、主回路辅助电源、"
                    "断路器反馈、励磁配合、DCS/PLC接口和联锁保护逻辑。两台同步电机对应的启动、"
                    "加速、同步切换及工频运行逻辑分别进行确认，保证系统满足投运前功能检查要求。\n\n"
                    "### 8.2 验收与培训\n\n"
                    "调试完成后，双方按确认的验收项目进行检查，并形成问题闭环记录。培训内容围绕设备组成、"
                    "操作流程、告警处理、维护检查和资料使用展开，具体计划按项目执行文件落实。"
                ),
                recommended_assets=[],
            )
        )

        self.assertTrue(review.passed)
        self.assertEqual(review.score, 0.96)
        self.assertEqual(
            [item.code for item in review.issues],
            ["TONE_CONTRACT_HEAVY", "TITLE_SCOPE_SOFT", "TRAINING_WORDING_RISK", "DEBUG_TEST_GRANULARITY"],
        )

    def test_build_section_quality_prompts_uses_xml_contract(self) -> None:
        system_prompt, user_prompt = build_section_quality_prompts(
            section={
                "title": "7 控制系统及联锁保护方案",
                "purpose": "说明控制架构、联锁边界与信号接口。",
                "keywords": ["控制系统", "联锁保护"],
            },
            outline_title="测试项目技术方案",
            global_params={"project_name": "测试项目", "voltage_level": "10kV"},
            content_md="## 控制系统方案\n\n### 控制架构\n\n正文。",
            recommended_assets=[
                {
                    "asset_type": "figure",
                    "visual_role": "product_photo",
                    "title": "控制系统总图",
                    "review_required": True,
                    "metadata": {"asset_audit_status": "review_pending", "asset_quality_score": 0.41},
                    "reason": "说明系统架构",
                }
            ],
        )

        self.assertIn("<review_context>", system_prompt)
        self.assertIn("<quality_review_request>", user_prompt)
        self.assertIn("<review_contract>", user_prompt)
        self.assertIn("<section_markdown>", user_prompt)
        self.assertIn("标签区中的 metadata", user_prompt)
        self.assertIn("证据类型错配", user_prompt)
        self.assertIn("visual_role=product_photo", user_prompt)
        self.assertIn("audit_status=review_pending", user_prompt)
        self.assertIn("quality_score=0.41", user_prompt)

    def test_build_section_quality_prompts_includes_ai_wiki_constraints_when_provided(self) -> None:
        _system_prompt, user_prompt = build_section_quality_prompts(
            section={"title": "主回路方案", "purpose": "说明主回路结构。", "keywords": ["主回路"]},
            outline_title="测试项目技术方案",
            global_params={"project_name": "测试项目"},
            content_md="## 主回路方案\n\n正文。",
            recommended_assets=[],
            quality_constraints="AI Wiki 质检约束\n- 优先使用“变频器”",
        )

        self.assertIn("<ai_wiki_review_constraints>", user_prompt)
        self.assertIn("优先使用“变频器”", user_prompt)

    def test_review_flags_ai_wiki_forbidden_phrases_and_term_mixing_when_provider_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._build_quality_wiki_provider(Path(temp_dir))
            service = SectionQualityGateService(
                llm_client=_StubLLMClient(),
                executor=_StubExecutor(),
                knowledge_wiki=provider,
            )

            review = self._run(
                service.review(
                    task_id="quality-ai-wiki-review",
                    section={"title": "VFD 主回路方案", "purpose": "说明变频器主回路结构。", "keywords": ["VFD", "变频器"]},
                    outline_title="测试项目技术方案",
                    global_params={"project_name": "测试项目"},
                    content_md=(
                        "## VFD 主回路方案\n\n"
                        "我公司推荐本次改造采用 VFD 方案，后续变频器主回路按旁路切换方式配置。"
                        "该 VFD 与变频器控制单元协同工作。\n"
                    ),
                    recommended_assets=[],
                )
            )

        self.assertFalse(review.passed)
        self.assertIn("KW001", {item.code for item in review.issues})
        self.assertIn("KW010", {item.code for item in review.issues})

    def test_review_and_repair_passes_ai_wiki_constraints_into_rewrite_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._build_quality_wiki_provider(Path(temp_dir))
            executor = _CaptureExecutor()
            service = SectionQualityGateService(
                llm_client=_StubLLMClient(),
                executor=executor,
                knowledge_wiki=provider,
            )

            content, status, meta = self._run(
                service.review_and_repair(
                    task_id="quality-ai-wiki-rewrite",
                    section={"title": "VFD 主回路方案", "purpose": "说明变频器主回路结构。", "keywords": ["VFD", "变频器"]},
                    outline_title="测试项目技术方案",
                    global_params={"project_name": "测试项目"},
                    content_md=(
                        "## VFD 主回路方案\n\n"
                        "我公司推荐本次改造采用 VFD 方案，后续变频器主回路按旁路切换方式配置。"
                        "该 VFD 与变频器控制单元协同工作。\n"
                    ),
                    recommended_assets=[],
                    allow_rewrite=True,
                )
            )

        self.assertEqual(status, "generated")
        self.assertEqual(meta["status"], "passed")
        self.assertTrue(meta["rewrite_attempted"])
        self.assertTrue(meta["rewrite_applied"])
        self.assertGreater(meta["knowledge_review_context_chars"], 0)
        self.assertEqual(len(executor.calls), 1)
        self.assertIn("AI Wiki 约束", executor.calls[0]["section_context"])
        self.assertIn("统一术语", executor.calls[0]["section_context"])
        self.assertIn("禁用表述", executor.calls[0]["section_context"])
        self.assertNotIn("我公司", content)
        self.assertNotIn(" VFD ", f" {content} ")

    def _build_quality_wiki_provider(self, root: Path) -> KnowledgeWikiContextProvider:
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
        return KnowledgeWikiContextProvider(root)

    def _run(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)
