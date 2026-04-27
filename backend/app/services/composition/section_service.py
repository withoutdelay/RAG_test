from __future__ import annotations

import asyncio
import base64
from collections import Counter
import json
import mimetypes
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session_factory
from app.config import get_settings
from app.models.chunk import Chunk
from app.models.evidence_bundle import EvidenceBundle
from app.models.figure_asset import FigureAsset
from app.models.job import Job
from app.models.project import Project
from app.models.proposal_outline import ProposalOutline
from app.models.requirement_card import RequirementCard
from app.models.section_draft import SectionDraft
from app.services.agents.executor import ExecutorAgent
from app.services.agents.state import WorkflowState
from app.services.composition.outline_service import outline_is_approved
from app.services.composition.section_quality import SectionQualityGateService
from app.services.domain.synonyms import expand_domain_terms, extract_domain_terms
from app.services.evidence_binding import resolve_outline_evidence_bundle
from app.services.knowledge import KnowledgeWikiContextProvider
from app.services.llm.client import LLMInputImage, LLMRequest, TaskType
from app.services.composition.semantic_rules import get_semantic_rules
from app.services.retrieval import AssetRetrievalService
from app.services.retrieval.case_service import CaseLibraryService
from app.services.validation.service import flatten_outline_sections
from app.services.vectorstore.block_taxonomy import (
    content_form_is_table,
    extract_taxonomy_hints,
    heading_family_similarity,
    heading_focus_adjustment,
    heading_looks_like_document_title,
    infer_target_taxonomy,
    related_section_types,
    support_content_forms,
)
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError
from app.utils.object_storage import get_object_storage

DEFAULT_REUSE_LIMIT = 5
CHILD_AWARE_REUSE_PROMPT_LIMIT = 12
CHILD_AWARE_REUSE_BLOCKS_PER_SUBSECTION = 2
CHILD_AWARE_CONTEXT_MAX_LINES = 24
REUSE_CANDIDATE_MULTIPLIER = 3
REUSE_MIN_CANDIDATES = 6
FULL_SECTION_MIN_SCORE = 0.72
FULL_SECTION_MIN_LEAD = 0.08
FULL_SECTION_PREFER_MIN_SCORE = 0.66
FULL_SECTION_PREFER_MIN_LEAD = 0.03
FULL_SECTION_MAX_SOURCE_TOKENS = 1800
EVIDENCE_JUDGE_DETERMINISTIC_CROSS_SECTION_MAX_SCORE = 0.62
EVIDENCE_JUDGE_DETERMINISTIC_MISMATCH_MAX_SCORE = 0.35
REUSE_TRACE_SECTION_LIMIT = 4
REUSE_TRACE_BLOCK_LIMIT = 6
INTER_SECTION_TOPIC_LIMIT = 8
REUSE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]{2,}")
TECHNICAL_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}\d*|\d+(?:\.\d+)+|[\u4e00-\u9fff]{2,}")
SECTION_TITLE_PREFIX_PATTERN = re.compile(r"^\s*(?:第[\d一二三四五六七八九十百]+[章节篇部]\s*|[\d一二三四五六七八九十百]+(?:\.\d+)*[、.\-]?\s*)")
REUSE_STOPWORDS = {
    "项目",
    "方案",
    "系统",
    "技术",
    "章节",
    "当前",
    "相关",
    "说明",
    "用于",
    "以及",
    "进行",
}
FIGURE_ASSET_HINTS = (
    "图",
    "示意",
    "单线",
    "接线",
    "原理",
    "波形",
    "曲线",
    "布局",
    "布置",
    "外形",
    "拓扑",
    "流程",
    "结构",
    "主回路",
    "总体方案",
    "架构",
)
TABLE_ASSET_HINTS = (
    "表",
    "参数",
    "性能",
    "数据",
    "清单",
    "配置",
    "供货",
    "范围",
    "点表",
    "规格",
    "容量",
    "型号",
    "数量",
    "尺寸",
)
FORMULA_ASSET_HINTS = ("公式", "equation", "推导", "算式")
SECTION_ASSET_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "overall_solution": ("系统示意图", "总体架构图", "单线图", "主回路图"),
    "main_circuit_scheme": ("主回路图", "单线图", "一次接线图", "原理图"),
    "communication_interface": ("接口示意图", "通信拓扑图", "控制逻辑图", "点表"),
    "control_logic": ("控制逻辑图", "联锁逻辑图", "信号流程图", "运行流程图"),
    "protection_interlock": ("联锁关系图", "保护关系图", "控制逻辑图", "信号流程图"),
    "cabinet_layout": ("柜体外形图", "设备布置图", "柜内结构图", "接线示意图"),
    "installation_conditions": ("安装布置图", "基础图", "进出线布置图", "设备外形图"),
    "vfd_spec": ("系统示意图", "单线图", "结构图", "外形图"),
    "starter_spec": ("系统示意图", "启动曲线", "单线图", "结构图"),
    "motor_spec": ("系统示意图", "启动曲线", "负载曲线", "外形图"),
    "transformer_spec": ("原理图", "绕组示意图", "电压波形图", "参数表"),
    "bom_or_supply_list": ("供货清单", "配置表", "参数表"),
    "supply_scope": ("供货清单", "配置表", "参数表"),
}
EXTRACTIVE_SECTION_CLASSES = {"architecture", "configuration", "implementation", "custom"}
EXTRACTIVE_SECTION_TYPES = {
    "overall_solution",
    "design_basis",
    "site_conditions",
    "supply_scope",
    "bom_or_supply_list",
    "motor_spec",
    "vfd_spec",
    "starter_spec",
    "transformer_spec",
    "main_circuit_scheme",
    "control_logic",
    "communication_interface",
    "protection_interlock",
    "cabinet_layout",
    "installation_conditions",
}
SECTION_TEMPLATE_HEADINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "main_circuit_scheme": {
        "主回路结构与运行切换": ("主回路", "主接线", "一次接线", "旁路", "切换", "隔离", "结构"),
        "设备选型与容量配置": ("选型", "配置", "容量", "功率单元", "整流变压器", "器件"),
        "关键技术参数": ("参数", "技术数据", "规格", "额定", "性能"),
        "保护与联锁条件": ("保护", "联锁", "闭锁", "报警"),
    },
    "communication_interface": {
        "通信架构与接口方式": ("通信", "通讯", "接口", "dcs", "plc", "modbus", "profibus", "profinet"),
        "接口与信号清单": ("信号", "点表", "ai", "ao", "di", "do", "清单"),
        "联锁与调试约束": ("联锁", "调试", "试验", "投运"),
    },
    "bom_or_supply_list": {
        "主要设备及供货范围": ("供货", "范围", "清单", "设备"),
        "关键参数与配置说明": ("参数", "规格", "配置", "说明"),
    },
    "supply_scope": {
        "主要设备及供货范围": ("供货", "范围", "清单", "设备"),
        "关键参数与配置说明": ("参数", "规格", "配置", "说明"),
    },
}
EXTRACTIVE_SECTION_OPENINGS = {
    "main_circuit_scheme": "本项目主回路按照安全隔离、旁路切换和连续运行要求进行配置，具体结构如下。",
    "communication_interface": "本项目控制系统接口按照上位机协同、信号闭环和调试可实施的原则进行配置，具体如下。",
    "supply_scope": "以下内容用于说明本项目主要设备供货边界和系统组成，最终以双方确认的供货清单为准。",
    "bom_or_supply_list": "以下内容用于说明本项目主要设备供货边界和系统组成，最终以双方确认的供货清单为准。",
}
EXTRACTIVE_TABLE_LEADS: dict[str, dict[str, str]] = {
    "main_circuit_scheme": {
        "设备选型与容量配置": "主要设备配置如下表所示。",
        "关键技术参数": "主要技术参数如下表所示。",
    },
    "communication_interface": {
        "接口与信号清单": "建议接口与信号清单如下表所示。",
    },
    "supply_scope": {
        "主要设备及供货范围": "主要设备供货范围如下表所示。",
        "关键参数与配置说明": "关键参数与配置说明如下表所示。",
    },
    "bom_or_supply_list": {
        "主要设备及供货范围": "主要设备供货范围如下表所示。",
        "关键参数与配置说明": "关键参数与配置说明如下表所示。",
    },
}
GENERIC_REUSE_HEADINGS = {
    "产品简介",
    "技术方案",
    "总体方案",
    "总体说明",
    "项目概述",
    "系统方案",
    "文件清单",
}
INTERNAL_REUSE_HEADING_PATTERNS = (
    re.compile(r"^(建议插入图表|建议图表|建议参考资产|推荐资产|可用参考资料|可用复用包|替换与禁用约束|参考摘要)$", re.IGNORECASE),
    re.compile(r"^(图表建议|插图建议|图表清单)$", re.IGNORECASE),
)
LATIN_ENUM_REUSE_HEADING_PATTERN = re.compile(r"^[A-Z]\.\s*.+$")
GENERIC_LABEL_REUSE_PATTERN = re.compile(r"^(概述|说明|补充说明|其他|附加说明)$")
BILINGUAL_GENERIC_REUSE_HEADING_PATTERN = re.compile(
    r"^(系统方案|总体方案|总体说明|项目概述|文件清单)\s+[A-Za-z][A-Za-z\s\-/]*$",
    re.IGNORECASE,
)
ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:[A-Z]+:([^\]]+)\]\]")
TABLE_SOURCE_REF_PATTERN = re.compile(r"#/tables/(\d+)")
BANNED_TERM_PATTERNS = (
    re.compile(r"(?:项目名称|买方|卖方|客户|用户)\s*[:：]\s*([^\n]{2,80})"),
)
STANDARD_REPLACE_FIELDS = (
    "project_name",
    "customer_name",
    "buyer_name",
    "seller_name",
    "location",
    "factory_name",
    "production_line_name",
    "voltage_level",
    "power_rating",
    "quantity",
    "delivery_scope",
)
SECTION_OUTPUT_NOISE_PATTERNS = (
    re.compile(r"^\s*本节基于.+草拟.*$", re.IGNORECASE),
    re.compile(r"^\s*目标章节标题[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*章节关键词[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*参考摘要[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*匹配原因[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*可参考章节[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*可用参考资料[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*(建议参考资产|推荐资产|可用复用包|替换与禁用约束)[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*(项目需求|对标资料|禁用表述)\s*$", re.IGNORECASE),
    re.compile(r"^\s*#{2,6}\s*(项目已确认的设计输入|对标资料提取.+|对标资料中的.+|禁用表述)\s*$", re.IGNORECASE),
    re.compile(r"^\s*这些资产仅供参考.*$", re.IGNORECASE),
    re.compile(r"^\s*只输出最终客户可阅读的 Markdown 正文.*$", re.IGNORECASE),
    re.compile(r"^\s*请撰写章节.+$", re.IGNORECASE),
)
STORAGE_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
INTERNAL_REUSE_SUMMARY_LINE_PATTERNS = (
    re.compile(r"^\s*匹配原因[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*可参考章节[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*命中原因[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*query_overlap\s*=.*$", re.IGNORECASE),
    re.compile(r"^\s*禁用表述\s*$", re.IGNORECASE),
    re.compile(r"^\s*项目需求\s*$", re.IGNORECASE),
    re.compile(r"^\s*对标资料\s*$", re.IGNORECASE),
)
PROMPT_XML_TAG_LINE_PATTERN = re.compile(r"^\s*</?[a-z_]+>\s*$", re.IGNORECASE)
TITLE_STATUS_MARKER_PATTERN = re.compile(r"\s*[（(]\s*(?:TBD|TODO|待定|待确认|待补充)\s*[)）]\s*", re.IGNORECASE)
INTERNAL_GUIDANCE_BULLET_PATTERN = re.compile(r"^\s*[-*]\s*(命中原因|推荐原因|使用方式|来源|章节|摘要)[:：].*$", re.IGNORECASE)
INTERNAL_GUIDANCE_HEADING_PATTERN = re.compile(
    r"^#{2,6}\s*(建议插入图表|建议图表|建议参考资产|推荐资产|可用参考资料|可用复用包|替换与禁用约束|参考摘要|图表建议|插图建议|图表清单)\s*$",
    re.IGNORECASE,
)
REWRITE_LEAKAGE_TOKENS = (
    "章节标题:",
    "章节目的:",
    "章节类型:",
    "当前关键参数:",
    "必须替换字段:",
    "禁止沿用词:",
    "已根据要求完成重写",
    "QWEN模型",
)
MAIN_CIRCUIT_FOCUS_TOKENS = ("主回路", "主接线", "一次接线", "一次系统", "单线图", "变压器", "旁路", "隔离", "母排", "电缆", "绝缘", "短路", "温升", "谐波")
MAIN_CIRCUIT_TOPOLOGY_TOKENS = (
    "单线图",
    "single line diagram",
    "进线",
    "断路器",
    "icb",
    "ocb",
    "rcb",
    "输入变压器",
    "输出变压器",
    "同步电机",
    "并网",
    "工频",
    "旁路",
    "同步装置",
    "synchrotact",
    "切换",
    "加速",
)
MAIN_CIRCUIT_NOISE_TOKENS = ("控制", "监控", "辅助设备", "油站", "冷却器", "励磁柜", "启动时间", "同步过程", "运行方式", "dcs", "认证", "证书", "测试", "试验")
MAIN_CIRCUIT_HARD_NOISE_TOKENS = (
    "fieldbus",
    "profibus",
    "modbus",
    "rs485",
    "i/o",
    "digital input",
    "routine test",
    "type test",
    "tests and certificates",
    "standard and certification",
    "packing",
    "transportation",
    "冷却系统",
    "润滑",
    "轴承",
    "保护功能",
)
MAIN_CIRCUIT_LINE_KEEP_TOKENS = (
    "进线",
    "断路器",
    "icb",
    "ocb",
    "rcb",
    "变频变压器",
    "输入变压器",
    "输出变压器",
    "同步装置",
    "synchrotact",
    "励磁装置",
    "切换",
    "工频运行",
    "并网",
    "同步电机",
    "主回路",
)
MAIN_CIRCUIT_LINE_DROP_TOKENS = (
    "description of start and sychronization",
    "lci start-up characteristic",
    "load data",
    "component technical data",
    "converter configuration",
    "converter system overview",
    "start curve by sfc",
    "变频启动曲线",
    "启动曲线",
    "负载数据",
    "飞轮力矩",
    "起动阻力矩",
    "静阻力矩",
    "总启动时间",
    "纯加速时间",
    "建立磁场",
    "连续启动3次",
    "连续启动 3 次",
    "gd2",
)
PROTECTION_FOCUS_TOKENS = ("控制", "监控", "监视", "联锁", "保护", "告警", "报警", "跳闸", "顺控", "故障", "信号接口", "plc", "dcs")
PROTECTION_NOISE_TOKENS = (
    "主回路",
    "一次接线",
    "一次图",
    "一次方案",
    "单线图",
    "功率单元",
    "结构示意",
    "波形",
    "谐波",
    "启动曲线",
    "转矩曲线",
    "外形尺寸",
    "版本",
    "页码",
    "备品备件",
    "备件",
    "spare",
    "售后",
    "服务",
    "额定数据",
    "rated data",
)
SEMANTIC_RULES = get_semantic_rules()
CONTROL_LOGIC_FOCUS_TOKENS = SEMANTIC_RULES.tokens("control_logic.focus")
CONTROL_LOGIC_NOISE_TOKENS = SEMANTIC_RULES.tokens("control_logic.noise")
PARAMETER_SUMMARY_FOCUS_TOKENS = SEMANTIC_RULES.tokens("parameter_summary.focus")
PARAMETER_SUMMARY_NOISE_TOKENS = SEMANTIC_RULES.tokens("parameter_summary.noise")
DESIGN_BASIS_FOCUS_TOKENS = SEMANTIC_RULES.tokens("design_basis.focus")
DESIGN_BASIS_NOISE_TOKENS = SEMANTIC_RULES.tokens("design_basis.noise")
VFD_SPEC_FOCUS_TOKENS = SEMANTIC_RULES.tokens("vfd_spec.focus")
VFD_SPEC_NOISE_TOKENS = SEMANTIC_RULES.tokens("vfd_spec.noise")
VFD_LCI_ALLOWED_SIGNAL_TOKENS = SEMANTIC_RULES.tokens("vfd_lci.allowed_signal")
VFD_LCI_SPECIFIC_TOKENS = SEMANTIC_RULES.tokens("vfd_lci.specific")
MOTOR_INTERFACE_FOCUS_TOKENS = SEMANTIC_RULES.tokens("motor_interface.focus")
MOTOR_INTERFACE_NOISE_TOKENS = SEMANTIC_RULES.tokens("motor_interface.noise")
SUPPLY_SCOPE_FOCUS_TOKENS = SEMANTIC_RULES.tokens("supply_scope.focus")
SUPPLY_SCOPE_NOISE_TOKENS = SEMANTIC_RULES.tokens("supply_scope.noise")
INSTALLATION_FOCUS_TOKENS = SEMANTIC_RULES.tokens("installation.focus")
INSTALLATION_NOISE_TOKENS = SEMANTIC_RULES.tokens("installation.noise")
SPARE_PARTS_FOCUS_TOKENS = SEMANTIC_RULES.tokens("spare_parts.focus")
SPARE_PARTS_NOISE_TOKENS = SEMANTIC_RULES.tokens("spare_parts.noise")
TECHNICAL_SCHEME_SECTION_TYPES = SEMANTIC_RULES.section_types("technical_scheme")
TECHNICAL_REUSE_HARD_NOISE_SECTION_TYPES = SEMANTIC_RULES.section_types("technical_reuse.hard_noise")
TECHNICAL_REUSE_HARD_NOISE_TOKENS = SEMANTIC_RULES.tokens("technical_reuse.hard_noise")
OVERALL_SOLUTION_FOCUS_TOKENS = SEMANTIC_RULES.tokens("overall_solution.focus")
OVERALL_SOLUTION_CORE_SECTION_TYPES = SEMANTIC_RULES.section_types("overall_solution.core")
LOW_VALUE_ASSET_TITLE_PATTERN = re.compile(r"^[0-9.\s,，:：+\-*/%()（）]+$")
EVIDENCE_JUDGE_DECISIONS = {"core", "support", "noise"}
EVIDENCE_JUDGE_KEEP_DECISIONS = {"core", "support"}
EVIDENCE_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["core", "support", "noise"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "decision", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "items"],
    "additionalProperties": False,
}
TABLE_PLACEHOLDER_REFERENCE_ONLY_SECTION_TYPES = {
    "bom_or_supply_list",
    "site_conditions",
    "supply_scope",
    "transformer_spec",
    "vfd_spec",
    "motor_spec",
}
SUPPLY_SCOPE_REQUIRED_ITEM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LCI/SFC 变频软起动装置", ("lci", "sfc", "变频软起", "软起动", "软启动")),
    ("输入变压器", ("输入变压器", "进线变压器")),
    ("输出变压器", ("输出变压器", "出线变压器")),
    ("励磁控制盘", ("励磁控制盘", "励磁柜", "励磁控制柜", "励磁调节")),
)
INVALID_ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:([A-Z_]+):([^\]]+)\]\]")
TITLE_ONLY_ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:(?![A-Z_]+:)([^\]]+)\]\]")
MIN_RECOMMENDED_ASSET_SCORE = 0.12
MIN_RECOMMENDED_FIGURE_SCORE = 0.16
MIN_RECOMMENDED_TABLE_SCORE = 0.10
ASSET_RECOMMENDATION_LIMIT = 3
ASSET_CANDIDATE_LIMIT = 10
RUNTIME_ASSET_RERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "visual_relevance": {"type": "number"},
                    "confidence": {"type": "number"},
                    "visual_role": {"type": "string"},
                    "should_recommend": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["asset_id", "visual_relevance", "confidence", "visual_role", "should_recommend", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}
SYSTEM_DIAGRAM_INTENT_TOKENS = ("系统架构", "系统方案", "系统示意", "单线图", "主回路", "一次接线", "接线图", "拓扑")
SYSTEM_DIAGRAM_FOCUS_TOKENS = (
    "系统架构",
    "系统方案",
    "系统示意",
    "单线图",
    "主回路",
    "一次接线",
    "接线图",
    "拓扑",
    "结构图",
)
SYSTEM_DIAGRAM_NOISE_TOKENS = (
    "负载数据",
    "启动曲线",
    "曲线",
    "波形",
    "load data",
    "start curve",
    "外形",
    "尺寸",
    "封面",
    "技术协议",
)
CURVE_FIGURE_INTENT_TOKENS = ("启动曲线", "启动特性", "曲线", "波形", "特性曲线", "负载曲线", "waveform", "curve")
LAYOUT_FIGURE_INTENT_TOKENS = ("布置", "布局", "外形", "尺寸", "安装", "基础", "通道")
SPEC_CURVE_OR_LOAD_NOISE_TOKENS = SEMANTIC_RULES.tokens("spec.curve_or_load_noise")
TRANSFORMER_SPEC_FOCUS_TOKENS = SEMANTIC_RULES.tokens("transformer_spec.focus")
TRANSFORMER_SPEC_CONCRETE_TOKENS = SEMANTIC_RULES.tokens("transformer_spec.concrete")
MOTOR_SPEC_FOCUS_TOKENS = SEMANTIC_RULES.tokens("motor_spec.focus")
MOTOR_SPEC_CONCRETE_TOKENS = SEMANTIC_RULES.tokens("motor_spec.concrete")
SPEC_PARAMETER_SIGNAL_TOKENS = SEMANTIC_RULES.tokens("spec.parameter_signal")
DEFAULT_PARAMETER_KEYS = SEMANTIC_RULES.tokens("parameter_keys.default")
PARAMETER_KEY_GROUPS_BY_SECTION_TYPE = {
    "motor_spec": SEMANTIC_RULES.tokens("parameter_keys.motor_spec"),
    "starter_spec": SEMANTIC_RULES.tokens("parameter_keys.starter_spec"),
    "transformer_spec": SEMANTIC_RULES.tokens("parameter_keys.transformer_spec"),
    "vfd_spec": SEMANTIC_RULES.tokens("parameter_keys.vfd_spec"),
}
PROTECTION_LCI_SCENARIO_NOISE_TOKENS = (
    "高浓磨机",
    "磨机",
    "lci",
    "sfc",
    "变频软起",
    "同步电机",
    "励磁柜",
    "励磁系统",
    "油站",
    "冷却器",
)
SUBSTATION_AUTOMATION_SECTION_TOKENS = ("变电站", "综合自动化", "站控层", "间隔层", "网络层", "iec 61850", "远方通信", "调度")
SUBSTATION_AUTOMATION_FOCUS_TOKENS = ("变电站", "综合自动化", "站控层", "间隔层", "网络层", "iec 61850", "goose", "mms", "调度")
SUBSTATION_AUTOMATION_OFF_SCOPE_TYPES = {
    "main_circuit_scheme",
    "motor_spec",
    "protection_interlock",
    "starter_spec",
    "transformer_spec",
    "vfd_spec",
}


def build_section_context(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any] | None = None,
    limit: int = 3,
    preferred_evidence_ids: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=limit,
        preferred_evidence_ids=preferred_evidence_ids,
    )

    context_lines = []
    citations: list[dict[str, Any]] = []
    for item in selected:
        heading_path = item.get("heading_path") or []
        heading_text = " > ".join(str(segment) for segment in heading_path if segment)
        raw_excerpt = _strip_internal_reuse_summary_lines(str(item.get("raw_content") or item.get("summary") or "")).strip()[:600]
        if not raw_excerpt:
            continue
        context_lines.append(f"- {item.get('source_title')} {heading_text}: {raw_excerpt}".strip())
        citations.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": item.get("relevance_score"),
                "type": item.get("type"),
                "excerpt": raw_excerpt,
            }
        )
    return "\n".join(context_lines), citations


def build_reuse_citations(reusable_blocks: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for index, block in enumerate(reusable_blocks[:limit], start=1):
        heading_path = [str(item) for item in (block.get("heading_path") or []) if str(item).strip()]
        signature = (str(block.get("source_title") or ""), tuple(heading_path))
        if signature in seen:
            continue
        seen.add(signature)
        citations.append(
            {
                "evidence_id": block.get("block_id") or f"reuse_{index:03d}",
                "source_doc_id": block.get("source_doc_id"),
                "source_title": block.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": block.get("selection_score") or block.get("reusability_score"),
                "type": block.get("block_type") or "section",
                "excerpt": _build_citation_excerpt(str(block.get("content_md") or "")),
            }
        )
    return citations


def _parameter_keys_for_section_type(target_section_type: str) -> tuple[str, ...]:
    configured_keys = PARAMETER_KEY_GROUPS_BY_SECTION_TYPE.get(str(target_section_type or "").lower())
    if configured_keys:
        return configured_keys
    return DEFAULT_PARAMETER_KEYS


def _is_empty_parameter_value(value: Any) -> bool:
    return value in (None, "", [], {})


def _format_parameter_candidate_rows(values: dict[str, Any]) -> str:
    rows = ["| 参数 | 值 |", "| --- | --- |"]
    for key, value in values.items():
        rows.append(f"| {key} | {value} |")
    return "\n".join(rows)


def _parameter_field_label(key: str) -> str:
    labels = SEMANTIC_RULES.metadata.get("parameter_field_labels")
    if isinstance(labels, dict):
        label = str(labels.get(key) or "").strip()
        if label:
            return label
    return str(key or "").strip()


def _format_parameter_snapshot_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _format_markdown_table_cell(value: Any) -> str:
    text = _format_parameter_snapshot_value(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _build_parameter_snapshot_section_content(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    reuse_pack: dict[str, Any],
    retrieval_mode: str,
    selected_sections: list[dict[str, Any]] | None = None,
    selected_blocks: list[dict[str, Any]] | None = None,
    knowledge_wiki_prior_summary: dict[str, Any] | None = None,
    selection_reason: dict[str, Any] | None = None,
    token_budget: dict[str, Any] | None = None,
    preceding_context_chars: int = 0,
    knowledge_wiki_context_chars: int = 0,
) -> tuple[str, dict[str, Any]] | None:
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type not in PARAMETER_KEY_GROUPS_BY_SECTION_TYPE:
        return None
    if not (
        bool(section.get("parameter_sensitive"))
        or target_section_type in {"motor_spec", "starter_spec", "transformer_spec", "vfd_spec"}
    ):
        return None
    if reuse_pack.get("reusable_blocks") or reuse_pack.get("recommended_assets") or reuse_pack.get("asset_candidates"):
        return None
    if reuse_pack.get("required_asset_placeholders"):
        return None

    parameter_candidates = reuse_pack.get("parameter_candidates")
    evidence_candidates = []
    if isinstance(parameter_candidates, dict):
        evidence_candidates = list(parameter_candidates.get("evidence") or [])
    preferred_keys = _parameter_keys_for_section_type(target_section_type)
    rows: list[tuple[str, Any]] = []
    seen_keys: set[str] = set()
    for key in preferred_keys:
        if key in seen_keys:
            continue
        value = global_params.get(key)
        if _is_empty_parameter_value(value):
            continue
        formatted_value = _format_parameter_snapshot_value(value)
        if not formatted_value:
            continue
        seen_keys.add(key)
        rows.append((key, value))

    min_row_count = 3 if target_section_type == "transformer_spec" else 4
    if len(rows) < min_row_count and not evidence_candidates:
        return None
    if len(rows) < min_row_count:
        return None

    title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
    lines = [f"## {title}", "", "| 参数 | 当前值 |", "| --- | --- |"]
    for key, value in rows:
        lines.append(f"| {_format_markdown_table_cell(_parameter_field_label(key))} | {_format_markdown_table_cell(value)} |")
    lines.extend(
        [
            "",
            "未确认的绝缘、温升、冷却、安装、试验、保护定值及接口资料，以最终技术协议、订货资料和厂家接口资料为准。",
            "",
        ]
    )
    details = {
        "effective_path": "parameter_snapshot_deterministic",
        "retrieval_mode": retrieval_mode,
        "target_section_type": target_section_type,
        "selected_sections": selected_sections or [],
        "selected_blocks": selected_blocks or [],
        "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary or {},
        "selection_reason": selection_reason or {},
        "token_budget": token_budget or {},
        "parameter_snapshot": {
            "source": "requirement_key_parameters",
            "parameter_count": len(rows),
            "parameter_keys": [key for key, _ in rows],
            "evidence_candidate_count": len(evidence_candidates),
        },
        "preceding_context_chars": preceding_context_chars,
        "knowledge_wiki_context_chars": knowledge_wiki_context_chars,
    }
    return "\n".join(lines), details


def _can_short_circuit_parameter_snapshot_retrieval(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
) -> bool:
    target_taxonomy = infer_target_taxonomy(section)
    asset_types = build_section_asset_types(section)
    if _section_needs_figure_asset(section) and not _should_skip_optional_asset_search(
        section=section,
        target_taxonomy=target_taxonomy,
        asset_types=asset_types,
    ):
        return False
    probe = _build_parameter_snapshot_section_content(
        section=section,
        global_params=global_params,
        reuse_pack={
            "reusable_blocks": [],
            "recommended_assets": [],
            "asset_candidates": [],
            "required_asset_placeholders": [],
            "parameter_candidates": {"evidence": []},
        },
        retrieval_mode="parameter_snapshot_short_circuit",
    )
    return probe is not None


def _split_source_excerpt_blocks(source_excerpt: str, *, max_blocks: int = 80) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for raw_line in str(source_excerpt or "").splitlines():
        line = raw_line.rstrip()
        heading_match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            if current_lines:
                blocks.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = heading_match.group(1).strip()
            current_lines = [line]
            if len(blocks) >= max_blocks:
                break
            continue
        if current_lines or line.strip():
            current_lines.append(line)
    if current_lines and len(blocks) < max_blocks:
        blocks.append((current_heading, "\n".join(current_lines).strip()))
    return [(heading, body) for heading, body in blocks if body]


def _parameter_focus_tokens_for_section(target_section_type: str) -> tuple[str, ...]:
    normalized = str(target_section_type or "").lower()
    if normalized == "transformer_spec":
        return TRANSFORMER_SPEC_FOCUS_TOKENS + TRANSFORMER_SPEC_CONCRETE_TOKENS
    if normalized == "motor_spec":
        return MOTOR_SPEC_FOCUS_TOKENS + MOTOR_SPEC_CONCRETE_TOKENS
    return PARAMETER_SUMMARY_FOCUS_TOKENS + SPEC_PARAMETER_SIGNAL_TOKENS


def _collect_parameter_evidence_candidates(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    limit: int = 4,
) -> list[dict[str, Any]]:
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if not (
        bool(section.get("parameter_sensitive"))
        or target_section_type in {"motor_spec", "starter_spec", "transformer_spec", "vfd_spec"}
    ):
        return []

    candidates: list[dict[str, Any]] = []
    preferred_keys = _parameter_keys_for_section_type(target_section_type)
    selected_params = {
        key: global_params.get(key)
        for key in preferred_keys
        if not _is_empty_parameter_value(global_params.get(key))
    }
    if selected_params:
        candidates.append(
            {
                "evidence_id": f"param:{target_section_type}:requirement_key_parameters",
                "type": "parameter",
                "source": "requirement_key_parameters",
                "source_title": "需求卡关键参数",
                "source_doc_id": None,
                "heading_path": [str(section.get("title") or "参数证据")],
                "content_md": _format_parameter_candidate_rows(selected_params),
                "score": 1.0 if len(selected_params) >= 2 else 0.82,
                "reason_trace": ["requirement_key_parameters", f"matched_keys={len(selected_params)}"],
            }
        )
    if len(selected_params) >= 2:
        return candidates[:limit]

    source_excerpt = str(global_params.get("_source_excerpt") or "").strip()
    if source_excerpt:
        focus_tokens = _parameter_focus_tokens_for_section(target_section_type)
        for index, (heading_text, body) in enumerate(_split_source_excerpt_blocks(source_excerpt), start=1):
            candidate_text = f"{heading_text}\n{body}"
            focus_hits = _token_hit_count_in_text(candidate_text, focus_tokens)
            parameter_hits = _token_hit_count_in_text(candidate_text, SPEC_PARAMETER_SIGNAL_TOKENS)
            if focus_hits < 2 and parameter_hits < 2:
                continue
            if _should_skip_reuse_scenario_noise(
                section=section,
                global_params=global_params,
                target_section_type=target_section_type,
                candidate_section_type=target_section_type,
                heading_text=heading_text,
                content_text=body,
            ):
                continue
            candidates.append(
                {
                    "evidence_id": f"param:{target_section_type}:source_excerpt:{index}",
                    "type": "parameter",
                    "source": "requirement_source_excerpt",
                    "source_title": "需求原文参数摘录",
                    "source_doc_id": None,
                    "heading_path": [heading_text] if heading_text else [str(section.get("title") or "参数摘录")],
                    "content_md": body[:1600],
                    "score": min(0.96, 0.62 + focus_hits * 0.04 + parameter_hits * 0.03),
                    "reason_trace": [
                        "requirement_source_excerpt",
                        f"focus_hits={focus_hits}",
                        f"parameter_hits={parameter_hits}",
                    ],
                }
            )

    results = (evidence_bundle.content or {}).get("results") or []
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    for item in results:
        if not isinstance(item, dict) or _is_case_fallback_evidence_item(item):
            continue
        raw_content = _strip_internal_reuse_summary_lines(str(item.get("raw_content") or item.get("summary") or ""))
        if not raw_content:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        content_form = str(item.get("content_form") or metadata.get("content_form") or "").lower()
        result_type = str(item.get("type") or item.get("source_chunk_type") or "").lower()
        if result_type not in {"parameter", "table"} and not content_form_is_table(content_form):
            continue
        heading_path = item.get("heading_path") or []
        heading_text = " > ".join(str(segment).strip() for segment in heading_path if str(segment).strip())
        candidate_section_type = str(metadata.get("section_type") or item.get("section_type") or "unknown").lower()
        if _should_skip_reuse_scenario_noise(
            section=section,
            global_params=global_params,
            target_section_type=target_section_type,
            candidate_section_type=candidate_section_type,
            heading_text=heading_text,
            content_text=raw_content,
        ):
            continue
        focus_hits = _token_hit_count_in_text(
            f"{heading_text}\n{raw_content}",
            _parameter_focus_tokens_for_section(target_section_type),
        )
        if focus_hits < 2:
            continue
        score, reasons, _ = _score_reuse_candidate(
            section=section,
            item=item,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
        )
        if score < 0.58:
            continue
        candidates.append(
            {
                "evidence_id": str(item.get("evidence_id") or item.get("source_chunk_id") or f"param:evidence:{len(candidates) + 1}"),
                "type": "parameter",
                "source": "evidence_bundle",
                "source_title": item.get("source_title"),
                "source_doc_id": item.get("source_doc_id"),
                "heading_path": heading_path,
                "content_md": raw_content[:1600],
                "score": round(score, 4),
                "reason_trace": [*reasons, f"focus_hits={focus_hits}"],
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in sorted(candidates, key=lambda item: float(item.get("score") or 0), reverse=True):
        signature = (
            str(candidate.get("source") or ""),
            str(candidate.get("source_title") or ""),
            " > ".join(str(item) for item in (candidate.get("heading_path") or [])),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(candidate)
        if len(deduped) >= limit:
            break
    return deduped


def _merge_parameter_evidence_context(
    *,
    context: str,
    citations: list[dict[str, Any]],
    parameter_evidence_candidates: list[dict[str, Any]],
    limit: int = 2,
) -> tuple[str, list[dict[str, Any]]]:
    if not parameter_evidence_candidates:
        return context, citations
    context_lines = [context.strip()] if context.strip() else []
    merged_citations = list(citations)
    seen_ids = {str(item.get("evidence_id") or "") for item in merged_citations if str(item.get("evidence_id") or "")}
    for candidate in parameter_evidence_candidates[:limit]:
        content = str(candidate.get("content_md") or "").strip()
        if not content:
            continue
        heading_path = [str(item) for item in (candidate.get("heading_path") or []) if str(item).strip()]
        heading_text = " > ".join(heading_path)
        context_lines.append(f"- {candidate.get('source_title') or '参数证据'} {heading_text}: {content[:900]}".strip())
        evidence_id = str(candidate.get("evidence_id") or f"param:{len(merged_citations) + 1}")
        if evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        merged_citations.append(
            {
                "evidence_id": evidence_id,
                "source_doc_id": candidate.get("source_doc_id"),
                "source_title": candidate.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": candidate.get("score"),
                "type": candidate.get("type") or "parameter",
                "excerpt": content[:600],
            }
        )
    return "\n".join(line for line in context_lines if line).strip(), merged_citations


def _merge_parameter_evidence_candidates(child_results: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for result in child_results:
        for candidate in result.get("parameter_evidence_candidates") or []:
            evidence_id = str(candidate.get("evidence_id") or "")
            if evidence_id and evidence_id in seen_ids:
                continue
            if evidence_id:
                seen_ids.add(evidence_id)
            candidates.append(candidate)
    candidates.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return candidates[:limit]


def _build_context_from_reusable_blocks(
    reusable_blocks: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    context_lines: list[str] = []
    citations: list[dict[str, Any]] = []
    for block in reusable_blocks[:limit]:
        heading_path = [str(item) for item in (block.get("heading_path") or []) if str(item).strip()]
        heading_text = " > ".join(heading_path)
        raw_excerpt = _strip_internal_reuse_summary_lines(str(block.get("content_md") or "")).strip()[:600]
        if not raw_excerpt:
            continue
        context_lines.append(f"- {block.get('source_title')} {heading_text}: {raw_excerpt}".strip())
        citations.append(
            {
                "evidence_id": block.get("block_id"),
                "source_doc_id": block.get("source_doc_id"),
                "source_title": block.get("source_title"),
                "heading_path": heading_path,
                "relevance_score": block.get("selection_score") or block.get("reusability_score"),
                "type": block.get("block_type") or "section",
                "excerpt": raw_excerpt,
            }
        )
    return "\n".join(context_lines), citations


def _sync_context_after_evidence_judge(
    *,
    context: str,
    citations: list[dict[str, Any]],
    reusable_blocks: list[dict[str, Any]],
    evidence_judge_trace: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    if str(evidence_judge_trace.get("status") or "").lower() != "applied":
        return context, citations
    input_count = int(evidence_judge_trace.get("input_count") or 0)
    kept_count = int(evidence_judge_trace.get("kept_count") or len(reusable_blocks))
    if input_count <= kept_count:
        return context, citations
    return _build_context_from_reusable_blocks(reusable_blocks)


def prioritize_recommended_assets(recommended_assets: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    if not recommended_assets:
        return []

    def _priority(asset: dict[str, Any]) -> tuple[float, float]:
        visual_role = str(asset.get("visual_role") or "").lower()
        asset_type = str(asset.get("asset_type") or "").lower()
        score = float(asset.get("score") or 0)
        bonus = 0.0
        if visual_role == "engineering_figure":
            bonus += 0.35
        elif visual_role == "formula_candidate":
            bonus += 0.25
        elif asset_type == "table":
            bonus += 0.22
        elif visual_role == "illustration":
            bonus += 0.05
        if visual_role == "page_furniture":
            bonus -= 0.55
        retrieval_quality = asset.get("metadata", {}).get("retrieval_quality") if isinstance(asset.get("metadata"), dict) else {}
        if isinstance(retrieval_quality, dict):
            if retrieval_quality.get("low_information"):
                bonus -= 0.65
            elif retrieval_quality.get("partial_fragment"):
                bonus -= 0.22
            if retrieval_quality.get("complete_diagram"):
                bonus += 0.08
        return (score + bonus, score)

    prioritized = sorted(recommended_assets, key=_priority, reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, str, str]] = set()
    for item in prioritized:
        signature = (
            str(item.get("document_name") or "").strip(),
            str(item.get("heading_path") or "").strip(),
            str(item.get("title") or "").strip(),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(item)
    non_furniture = [item for item in prioritized if str(item.get("visual_role") or "").lower() != "page_furniture"]
    if deduped:
        non_furniture = [item for item in deduped if str(item.get("visual_role") or "").lower() != "page_furniture"]
        if non_furniture:
            return non_furniture[:limit]
        return deduped[:limit]
    if non_furniture:
        return non_furniture[:limit]
    return prioritized[:limit]


def filter_recommended_assets_for_section(
    recommended_assets: list[dict[str, Any]],
    *,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    if not recommended_assets:
        return []
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    parameter_summary = _is_parameter_summary_section(section=section, target_taxonomy=target_taxonomy)
    installation_section = target_section_type == "installation_conditions"
    spare_parts_section = _is_spare_parts_section(section=section, target_taxonomy=target_taxonomy)
    parameter_text = _section_asset_signal_text(section).casefold()
    needs_dimension_assets = any(token in parameter_text for token in ("尺寸", "外形", "柜体", "布置"))
    technical_noise_tokens = (
        "认证",
        "检验",
        "检测",
        "报告",
        "证书",
        "试验",
        "test report",
        "report number",
        "certificate",
        "certification",
    )
    filtered: list[dict[str, Any]] = []
    for asset in recommended_assets:
        visual_role = str(asset.get("visual_role") or "").lower()
        asset_type = str(asset.get("asset_type") or "").lower()
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        retrieval_quality = metadata.get("retrieval_quality") if isinstance(metadata.get("retrieval_quality"), dict) else {}
        if not _recommended_asset_score_passes(asset=asset, section=section, target_taxonomy=target_taxonomy):
            continue
        if retrieval_quality.get("low_information"):
            continue
        if retrieval_quality.get("low_confidence_summary") and not retrieval_quality.get("complete_diagram"):
            continue
        if retrieval_quality.get("partial_fragment") and not retrieval_quality.get("complete_diagram"):
            continue
        if str(asset.get("title") or "").strip() in {"目录", "目 录"}:
            continue
        if str(metadata.get("label") or "").strip() == "document_index":
            continue
        heading_text = " ".join(
            str(part)
            for part in (
                asset.get("heading_path"),
                asset.get("title"),
                asset.get("display_title"),
                asset.get("caption"),
                asset.get("preview_text"),
                metadata.get("source_heading"),
                metadata.get("semantic_summary"),
            )
            if part
        )
        asset_section_type = str(metadata.get("section_type") or "unknown").lower()
        figure_intents = _infer_section_figure_intents(section)
        if asset_type == "figure" and figure_intents:
            if not any(
                _figure_asset_matches_section_intent(
                    intent=figure_intent,
                    heading_text=heading_text,
                    content_text=str(asset.get("preview_text") or ""),
                    metadata=metadata,
                )
                for figure_intent in figure_intents
            ):
                continue
        if target_section_type in TECHNICAL_SCHEME_SECTION_TYPES:
            if asset_section_type in TECHNICAL_REUSE_HARD_NOISE_SECTION_TYPES:
                continue
            if _text_contains_any_token(heading_text, TECHNICAL_REUSE_HARD_NOISE_TOKENS):
                continue
        if target_section_type == "overall_solution":
            focus_hits = _focus_token_hit_count(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
                focus_tokens=OVERALL_SOLUTION_FOCUS_TOKENS,
            )
            title_text = str(asset.get("title") or asset.get("display_title") or "").strip()
            source_section_id = str(asset.get("source_section_id") or metadata.get("source_section_id") or "").strip()
            low_value_unknown_figure = (
                asset_type == "figure"
                and LOW_VALUE_ASSET_TITLE_PATTERN.fullmatch(title_text)
                and asset_section_type == "unknown"
                and not source_section_id
            )
            generic_unanchored_figure = (
                asset_type == "figure"
                and title_text in {"参考图", "图", "图片", "figure", "image"}
                and not source_section_id
            )
            if low_value_unknown_figure:
                continue
            if generic_unanchored_figure:
                continue
            if asset_type == "figure" and visual_role in {"asset_fragment", "text_fragment", "page_furniture"}:
                continue
            if asset_type == "figure" and visual_role in {"illustration", "layout_drawing", "product_photo"} and focus_hits < 2:
                continue
            if asset_section_type in {
                "communication_interface",
                "control_logic",
                "protection_interlock",
                "supply_scope",
                "bom_or_supply_list",
                "installation_conditions",
                "cabinet_layout",
            } and focus_hits < 2:
                continue
        if target_section_type in EXTRACTIVE_SECTION_TYPES and visual_role == "page_furniture":
            continue
        if target_section_type in EXTRACTIVE_SECTION_TYPES and _text_contains_any_token(heading_text, technical_noise_tokens):
            continue
        if target_section_type == "main_circuit_scheme":
            focus_match, noise_match = _main_circuit_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"communication_interface", "protection_interlock"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "vfd_spec":
            focus_match, noise_match = _vfd_spec_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"protection_interlock", "cabinet_layout", "service_support", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "motor_spec":
            focus_match, noise_match = _motor_interface_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"cabinet_layout", "supply_scope", "service_support"} and not focus_match:
                continue
            if asset_type == "table" and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "protection_interlock":
            focus_match, noise_match = _protection_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            lowered_heading = heading_text.casefold()
            if any(token in lowered_heading for token in ("外形尺寸", "front view", "rear view", "side view", "尺寸图")):
                continue
            if not focus_match:
                continue
            if asset_type == "table" and not focus_match:
                continue
            if asset_section_type == "cabinet_layout":
                continue
            if asset_section_type in {"main_circuit_scheme", "vfd_spec", "transformer_spec"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "control_logic":
            focus_match, noise_match = _control_logic_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"cabinet_layout", "commissioning_acceptance", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif parameter_summary:
            focus_match, noise_match = _parameter_summary_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_type != "table":
                continue
            if asset_section_type == "cabinet_layout" and not needs_dimension_assets:
                continue
            if asset_section_type in {"control_logic", "communication_interface", "protection_interlock"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type in {"supply_scope", "bom_or_supply_list"}:
            focus_match, noise_match = _supply_scope_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            lowered_heading = heading_text.casefold()
            if asset_section_type == "service_support":
                continue
            if asset_type == "table" and asset_section_type not in {"supply_scope", "bom_or_supply_list"} and not focus_match:
                continue
            if any(token in lowered_heading for token in ("售后", "培训", "维保", "巡检")) and not focus_match:
                continue
            if any(token in lowered_heading for token in ("备件", "spare")) and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif installation_section:
            focus_match, noise_match = _focus_match_from_tokens(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
                focus_tokens=INSTALLATION_FOCUS_TOKENS,
                noise_tokens=INSTALLATION_NOISE_TOKENS,
            )
            if asset_section_type in {"overall_solution", "main_circuit_scheme", "starter_spec", "control_logic"} and (
                noise_match or not focus_match
            ):
                continue
            if noise_match and not focus_match:
                continue
            if not focus_match:
                continue
        elif spare_parts_section:
            focus_match, noise_match = _focus_match_from_tokens(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
                focus_tokens=SPARE_PARTS_FOCUS_TOKENS,
                noise_tokens=SPARE_PARTS_NOISE_TOKENS,
            )
            if asset_type != "table":
                continue
            if asset_section_type in {"site_conditions", "design_basis"}:
                continue
            if noise_match and not focus_match:
                continue
            if not focus_match:
                continue
        filtered.append(asset)
    if filtered:
        return filtered
    if installation_section or spare_parts_section:
        return []
    if target_section_type in EXTRACTIVE_SECTION_TYPES:
        return []
    return [
        asset
        for asset in recommended_assets
        if _recommended_asset_score_passes(asset=asset, section=section, target_taxonomy=target_taxonomy)
    ]


def _infer_section_figure_intents(section: dict[str, Any]) -> set[str]:
    text = _section_asset_signal_text(section).casefold()
    intents: set[str] = set()
    if _text_contains_any_token(text, CURVE_FIGURE_INTENT_TOKENS):
        intents.add("curve")
    if _text_contains_any_token(text, LAYOUT_FIGURE_INTENT_TOKENS):
        intents.add("layout")
    if _text_contains_any_token(text, SYSTEM_DIAGRAM_INTENT_TOKENS):
        intents.add("system_diagram")
    return intents


def _figure_asset_matches_section_intent(
    *,
    intent: str,
    heading_text: str,
    content_text: str,
    metadata: dict[str, Any],
) -> bool:
    combined = f"{heading_text} {content_text}".casefold()
    if intent == "curve":
        return _text_contains_any_token(combined, CURVE_FIGURE_INTENT_TOKENS)
    if intent == "layout":
        return _text_contains_any_token(combined, LAYOUT_FIGURE_INTENT_TOKENS)
    if intent != "system_diagram":
        return True

    has_focus = _text_contains_any_token(combined, SYSTEM_DIAGRAM_FOCUS_TOKENS)
    has_noise = _text_contains_any_token(combined, SYSTEM_DIAGRAM_NOISE_TOKENS)
    if has_focus:
        return True
    if has_noise:
        return False
    content_form = str(metadata.get("content_form") or "").strip().lower()
    if content_form == "formula":
        return False
    source_section_id = str(metadata.get("source_section_id") or "").strip()
    title = str(metadata.get("display_title") or metadata.get("raw_title") or "").strip()
    return bool(source_section_id and title and title not in {"参考图", "图", "图片", "figure", "image"})


def _recommended_asset_score_passes(
    *,
    asset: dict[str, Any],
    section: dict[str, Any],
    target_taxonomy: dict[str, Any],
) -> bool:
    score = _recommended_asset_score_value(asset)
    if score is None:
        return True
    asset_type = str(asset.get("asset_type") or "").lower()
    expected_types = set(_effective_section_evidence_types(section))
    minimum = MIN_RECOMMENDED_ASSET_SCORE
    if asset_type == "figure" and expected_types.intersection({"figure", "diagram"}):
        minimum = MIN_RECOMMENDED_FIGURE_SCORE
    elif asset_type == "table":
        minimum = MIN_RECOMMENDED_TABLE_SCORE
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type in {"main_circuit_scheme", "overall_solution"} and asset_type == "figure":
        minimum = max(minimum, 0.18)
    return score >= minimum


def _recommended_asset_score_value(asset: dict[str, Any]) -> float | None:
    raw_score = asset.get("score")
    if raw_score is None:
        score_breakdown = asset.get("score_breakdown")
        if not isinstance(score_breakdown, dict):
            metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
            score_breakdown = metadata.get("retrieval_score_breakdown") if isinstance(metadata.get("retrieval_score_breakdown"), dict) else {}
        raw_score = score_breakdown.get("final") if isinstance(score_breakdown, dict) else None
    if raw_score is None:
        return None
    try:
        return float(raw_score)
    except (TypeError, ValueError):
        return None


def _build_runtime_asset_gate_candidate(asset: dict[str, Any]) -> dict[str, Any]:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    quality = metadata.get("retrieval_quality") if isinstance(metadata.get("retrieval_quality"), dict) else {}
    summary = metadata.get("semantic_summary") if isinstance(metadata.get("semantic_summary"), dict) else {}
    return {
        "asset_id": str(asset.get("asset_id") or ""),
        "asset_type": str(asset.get("asset_type") or ""),
        "visual_role": str(asset.get("visual_role") or ""),
        "title": str(asset.get("display_title") or asset.get("title") or ""),
        "document_name": str(asset.get("document_name") or ""),
        "heading_path": str(asset.get("heading_path") or ""),
        "caption": str(asset.get("caption") or ""),
        "preview_text": str(asset.get("preview_text") or "")[:500],
        "score": _recommended_asset_score_value(asset) or 0.0,
        "review_required": bool(asset.get("review_required")),
        "quality_flags": quality,
        "semantic_summary": {
            "summary": str(summary.get("summary") or "")[:300],
            "diagram_type": str(summary.get("diagram_type") or ""),
            "confidence": summary.get("confidence"),
            "review_required": summary.get("review_required"),
            "review_notes": str(summary.get("review_notes") or "")[:220],
        }
        if summary
        else {},
    }


def _build_runtime_asset_gate_system_prompt() -> str:
    return (
        "你是技术方案运行时图资产门控器。请基于图片本体、章节目标、标题和检索摘要，判断每张候选图是否适合作为当前章节的自动推荐资产。\n"
        "原则：图片视觉本体优先，标题、章节路径和邻近文字只是弱证据；当视觉内容与标题冲突时，以视觉内容为准。\n"
        "不要把产品照片、机柜实拍、展台照片、房间布置、安装尺寸、文字截图、页眉页脚、局部箭头或裁剪碎片判为主接线图、拓扑图、控制原理图或系统示意图。\n"
        "只有图片本体清楚呈现电气连接关系、系统结构、控制逻辑、启动/同步过程、主回路或曲线波形时，才应作为对应技术章节的自动推荐。\n"
        "如果图片有参考价值但章节不匹配，should_recommend=false，reason 中说明更适合的用途。只返回 JSON。"
    )


def _build_runtime_asset_gate_user_prompt(
    *,
    section: dict[str, Any],
    query: str,
    candidates: list[dict[str, Any]],
    image_asset_ids: list[str],
) -> str:
    payload = {
        "section": {
            "title": section.get("title"),
            "description": section.get("description"),
            "keywords": section.get("keywords") or [],
            "taxonomy": infer_target_taxonomy(section),
            "expected_evidence_types": _effective_section_evidence_types(section),
        },
        "retrieval_query": query,
        "image_asset_ids_in_order": image_asset_ids,
        "candidates": candidates,
        "decision_rules": [
            "visual_relevance 取 0 到 1，表示图片本体与当前章节技术目标的匹配度。",
            "confidence 取 0 到 1，表示你对视觉判断的把握。",
            "should_recommend 只在图片本体与章节目标明确匹配且可直接作为自动推荐时为 true。",
            "visual_role 使用真实视觉类型，例如 engineering_figure、layout_drawing、product_photo、text_fragment、asset_fragment、page_furniture。",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=_json_prompt_default)


def build_generation_sections(sections: list[dict[str, Any]], *, granularity: str = "top_level") -> list[dict[str, Any]]:
    if str(granularity or "top_level").lower() == "all_nodes":
        return flatten_outline_sections(sections or [])
    generated: list[dict[str, Any]] = []
    for index, section in enumerate(sections or [], start=1):
        if not isinstance(section, dict):
            continue
        generated.append(_build_major_generation_section(section, fallback_id=str(index)))
    return generated


def _build_major_generation_section(section: dict[str, Any], *, fallback_id: str) -> dict[str, Any]:
    current = dict(section)
    children = current.get("children") if isinstance(current.get("children"), list) else []
    current["children"] = children
    child_sections = flatten_outline_sections(children)
    if not child_sections:
        return current

    original_title = str(current.get("title") or "").strip()
    child_outline_text = _format_child_outline_for_generation(children)
    current["child_outline_text"] = child_outline_text
    current["generation_unit"] = "top_level"
    current["child_aware_retrieval"] = True
    current["generation_child_count"] = len(child_sections)

    if _is_numeric_only_heading(original_title):
        current["title"] = _derive_major_section_title(current, child_sections, fallback_id=fallback_id)

    current["keywords"] = _merge_string_lists(
        current.get("keywords") or [],
        [str(item.get("title") or "") for item in child_sections],
        [str(item.get("section_id") or "") for item in child_sections],
    )
    current["expected_evidence_types"] = _merge_string_lists(
        current.get("expected_evidence_types") or [],
        *[(item.get("expected_evidence_types") or []) for item in child_sections],
    )
    current["asset_required"] = bool(current.get("asset_required")) or any(bool(item.get("asset_required")) for item in child_sections)
    current["parameter_sensitive"] = bool(current.get("parameter_sensitive")) or any(
        bool(item.get("parameter_sensitive")) for item in child_sections
    )
    current["needs_human_review"] = bool(current.get("needs_human_review")) or any(
        bool(item.get("needs_human_review")) for item in child_sections
    )
    if str(current.get("section_class") or "custom") == "custom":
        promoted_class = next(
            (
                str(item.get("section_class") or "").strip()
                for item in child_sections
                if str(item.get("section_class") or "").strip()
                and str(item.get("section_class") or "").strip() != "custom"
            ),
            "",
        )
        if promoted_class:
            current["section_class"] = promoted_class
    if any(str(item.get("generation_mode") or "") == "reuse_first" for item in child_sections):
        current["generation_mode"] = "reuse_first"
    purpose = str(current.get("purpose") or current.get("description") or "").strip()
    current["purpose"] = (
        f"{purpose}\n请按以下内部小节结构生成本章正文：\n{child_outline_text}"
        if purpose
        else f"请按以下内部小节结构生成本章正文：\n{child_outline_text}"
    )
    return current


def _format_child_outline_for_generation(children: list[dict[str, Any]], *, depth: int = 1) -> str:
    lines: list[str] = []
    for child in children or []:
        if not isinstance(child, dict):
            continue
        title = str(child.get("title") or child.get("section_id") or "").strip()
        if title:
            lines.append(f"{'  ' * (depth - 1)}- {title}")
        grand_children = child.get("children") if isinstance(child.get("children"), list) else []
        if grand_children:
            lines.append(_format_child_outline_for_generation(grand_children, depth=depth + 1))
    return "\n".join(line for line in lines if line).strip()


def _is_numeric_only_heading(title: str) -> bool:
    text = str(title or "").strip()
    return bool(text and re.fullmatch(r"(?:第)?[\d一二三四五六七八九十百]+(?:[章节])?", text))


def _derive_major_section_title(section: dict[str, Any], child_sections: list[dict[str, Any]], *, fallback_id: str) -> str:
    section_id = str(section.get("section_id") or fallback_id).strip()
    first_child_title = str((child_sections[0] if child_sections else {}).get("title") or "").strip()
    first_child_title = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", first_child_title).strip()
    if first_child_title:
        return f"{section_id} {first_child_title}".strip()
    return str(section.get("title") or section_id or fallback_id)


def _merge_string_lists(*groups: Any) -> list[str]:
    merged: list[str] = []
    for group in groups:
        if isinstance(group, str):
            candidates = [group]
        elif isinstance(group, (list, tuple, set)):
            candidates = list(group)
        else:
            continue
        for item in candidates:
            text = str(item or "").strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def _extract_runtime_asset_gate_decisions(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        decisions[asset_id] = item
    return decisions


def _json_prompt_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _apply_runtime_asset_gate_decision(
    *,
    asset: dict[str, Any],
    decision: dict[str, Any],
    model_used: str,
) -> dict[str, Any]:
    updated = dict(asset)
    metadata = dict(updated.get("metadata") or {})
    score_breakdown = dict(updated.get("score_breakdown") or metadata.get("retrieval_score_breakdown") or {})
    relevance = _clamped_float(decision.get("visual_relevance"), default=0.0)
    confidence = _clamped_float(decision.get("confidence"), default=0.0)
    should_recommend = bool(decision.get("should_recommend"))
    visual_role = str(decision.get("visual_role") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    gate_meta = {
        "status": "reviewed",
        "model": model_used,
        "visual_relevance": relevance,
        "confidence": confidence,
        "visual_role": visual_role,
        "should_recommend": should_recommend,
        "reason": reason,
    }
    metadata["runtime_vision_gate"] = gate_meta
    score_breakdown["runtime_vision_relevance"] = round(relevance, 4)
    score_breakdown["runtime_vision_confidence"] = round(confidence, 4)
    score_breakdown["runtime_vision_should_recommend"] = "true" if should_recommend else "false"
    updated["metadata"] = metadata
    updated["score_breakdown"] = score_breakdown
    if visual_role and confidence >= 0.72:
        updated["visual_role"] = visual_role
    if reason:
        existing_reason = str(updated.get("reason") or "").strip()
        updated["reason"] = f"{existing_reason}；视觉门控：{reason}" if existing_reason else f"视觉门控：{reason}"
    base_score = _recommended_asset_score_value(updated) or 0.0
    if should_recommend:
        score_delta = max(-0.08, min(0.14, (relevance - 0.5) * 0.22))
    elif confidence >= 0.65:
        score_delta = -0.35
        updated["review_required"] = True
    else:
        score_delta = -0.12
    updated["score"] = round(max(0.0, base_score + score_delta), 4)
    return updated


def _runtime_asset_gate_allows_recommendation(asset: dict[str, Any]) -> bool:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    gate = metadata.get("runtime_vision_gate") if isinstance(metadata.get("runtime_vision_gate"), dict) else {}
    if not gate or gate.get("status") != "reviewed":
        return True
    confidence = _clamped_float(gate.get("confidence"), default=0.0)
    relevance = _clamped_float(gate.get("visual_relevance"), default=0.0)
    if confidence < 0.6:
        return True
    return bool(gate.get("should_recommend")) and relevance >= 0.5


def _runtime_asset_gate_should_review(*, asset: dict[str, Any], section: dict[str, Any]) -> bool:
    figure_intents = _infer_section_figure_intents(section)
    if "system_diagram" in figure_intents:
        return True
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    quality = metadata.get("retrieval_quality") if isinstance(metadata.get("retrieval_quality"), dict) else {}
    summary = metadata.get("semantic_summary") if isinstance(metadata.get("semantic_summary"), dict) else {}
    visual_role = str(asset.get("visual_role") or "").lower()
    if bool(asset.get("review_required")):
        return True
    if visual_role in {"illustration", "layout_drawing", "product_photo", "asset_fragment", "text_fragment", "page_furniture"}:
        return True
    if quality.get("low_information") or quality.get("partial_fragment") or quality.get("low_confidence_summary"):
        return True
    if summary:
        if str(summary.get("status") or "") == "failed":
            return True
        if bool(summary.get("review_required")):
            return True
        if _clamped_float(summary.get("confidence"), default=1.0) < 0.62:
            return True
    score = _recommended_asset_score_value(asset)
    if score is not None and score < 0.28:
        return True
    combined = " ".join(
        str(item)
        for item in (
            asset.get("title"),
            asset.get("display_title"),
            asset.get("heading_path"),
            asset.get("caption"),
            asset.get("preview_text"),
        )
        if item
    ).casefold()
    return _text_contains_any_token(
        combined,
        ("产品照片", "实拍", "现场照片", "photo", "外观", "布置", "尺寸", "页眉", "页脚", "logo", "局部", "碎片"),
    )


def _clamped_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def tighten_recommended_assets_for_reuse(
    recommended_assets: list[dict[str, Any]],
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
    limit: int = ASSET_RECOMMENDATION_LIMIT,
) -> list[dict[str, Any]]:
    if not recommended_assets or not reusable_blocks:
        return recommended_assets[:limit]
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type not in EXTRACTIVE_SECTION_TYPES:
        return recommended_assets[:limit]
    preferred_limit = min(len(recommended_assets), limit)

    anchor_document_names = {
        str(block.get("source_title") or "").strip()
        for block in list(reusable_blocks)[:3]
        if str(block.get("source_title") or "").strip()
    }
    anchor_headings = [
        " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        for block in list(reusable_blocks)[:3]
        if block.get("heading_path")
    ]
    strong_matches: list[dict[str, Any]] = []
    contextual_matches: list[dict[str, Any]] = []
    related_types = related_section_types(target_section_type)

    def _dedupe_assets(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for asset in group:
                signature = str(
                    asset.get("asset_id")
                    or f"{asset.get('document_name')}|{asset.get('heading_path')}|{asset.get('title')}"
                )
                if signature in seen:
                    continue
                seen.add(signature)
                merged.append(asset)
        return merged

    for asset in recommended_assets:
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        asset_section_type = str(metadata.get("section_type") or "unknown").lower()
        document_name = str(asset.get("document_name") or "").strip()
        heading_text = str(asset.get("heading_path") or "").strip()
        if heading_looks_like_document_title(str(asset.get("title") or heading_text)):
            continue
        doc_match = bool(anchor_document_names) and document_name in anchor_document_names
        family_bonus = max(
            (heading_family_similarity(anchor_heading, heading_text) for anchor_heading in anchor_headings),
            default=0.0,
        )

        if doc_match and family_bonus >= 0.18:
            strong_matches.append(asset)
            continue
        if doc_match and (
            family_bonus >= 0.18
            or (asset_section_type == target_section_type and family_bonus >= 0.12)
            or (asset_section_type in related_types and family_bonus >= 0.08)
        ):
            contextual_matches.append(asset)
            continue

    if strong_matches:
        return _dedupe_assets([strong_matches, contextual_matches, recommended_assets])[:preferred_limit]
    if contextual_matches:
        return _dedupe_assets([contextual_matches, recommended_assets])[:preferred_limit]
    return recommended_assets[:preferred_limit]


def sanitize_generated_section_content(*, content_md: str, section_title: str, section_purpose: str = "") -> str:
    section_title = clean_customer_facing_section_title(section_title)
    lines = str(content_md or "").splitlines()
    cleaned: list[str] = []
    skip_blank_after_internal_heading = False
    for line in lines:
        stripped = line.strip()
        if PROMPT_XML_TAG_LINE_PATTERN.match(stripped):
            continue
        if INTERNAL_GUIDANCE_HEADING_PATTERN.match(stripped):
            skip_blank_after_internal_heading = True
            continue
        if skip_blank_after_internal_heading and not stripped:
            continue
        if skip_blank_after_internal_heading and INTERNAL_GUIDANCE_BULLET_PATTERN.match(stripped):
            continue
        skip_blank_after_internal_heading = False
        if any(pattern.match(line) for pattern in SECTION_OUTPUT_NOISE_PATTERNS):
            continue
        cleaned.append(line)

    text = STORAGE_CONTROL_CHAR_PATTERN.sub("", "\n".join(cleaned)).strip()
    text = _strip_leading_section_purpose(text=text, section_purpose=section_purpose)
    text = _strip_leading_section_goal_or_overview(text=text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return f"## {section_title}\n"
    if not text.lstrip().startswith("#"):
        return f"## {section_title}\n\n{text}\n"
    return text.rstrip() + "\n"


def clean_customer_facing_section_title(section_title: str) -> str:
    cleaned = TITLE_STATUS_MARKER_PATTERN.sub("", str(section_title or "")).strip()
    return re.sub(r"\s{2,}", " ", cleaned) or str(section_title or "").strip() or "未命名章节"


def _section_with_customer_facing_title(section: dict[str, Any]) -> dict[str, Any]:
    cleaned_title = clean_customer_facing_section_title(str(section.get("title") or ""))
    if cleaned_title == str(section.get("title") or ""):
        return section
    updated = dict(section)
    updated["title"] = cleaned_title
    return updated


def _strip_leading_section_purpose(*, text: str, section_purpose: str) -> str:
    normalized_purpose = str(section_purpose or "").strip()
    if not normalized_purpose:
        return text
    paragraphs = [paragraph for paragraph in str(text or "").split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return text
    body_index = 1 if paragraphs[0].lstrip().startswith("#") else 0
    if body_index >= len(paragraphs):
        return text

    def _normalize(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold().strip("。；;:：")

    if _normalize(paragraphs[body_index]) != _normalize(normalized_purpose):
        return text
    paragraphs.pop(body_index)
    return "\n\n".join(paragraphs).strip()


def _strip_leading_section_goal_or_overview(*, text: str) -> str:
    paragraphs = [paragraph for paragraph in str(text or "").split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return text
    body_index = 1 if paragraphs[0].lstrip().startswith("#") else 0
    if body_index >= len(paragraphs):
        return text
    candidate = paragraphs[body_index].strip()
    if _looks_like_leading_section_goal_or_overview(candidate):
        paragraphs.pop(body_index)
        return "\n\n".join(paragraphs).strip()
    return text


def _looks_like_leading_section_goal_or_overview(paragraph: str) -> bool:
    normalized = re.sub(r"\s+", "", str(paragraph or "").strip())
    if not normalized or len(normalized) > 240:
        return False
    if normalized.startswith(("#", "|", "-", "*", ">", "[[ASSET:")):
        return False
    if not re.match(r"^(?:本章|本节|本章节|本小节|本部分|本章节内容|本节内容)", normalized):
        return False
    return bool(
        re.search(
            r"(?:旨在|目标是|目的在于|围绕|针对|聚焦|概要|概述|"
            r"主要(?:介绍|说明|阐述|概述|梳理|围绕|覆盖|明确)|"
            r"将(?:介绍|说明|阐述|概述|梳理|围绕|展开|明确)|"
            r"用于(?:介绍|说明|阐述|概述|明确)|"
            r"(?:进行|展开)(?:说明|阐述|介绍|梳理)|"
            r"明确.+(?:要求|指标|边界|参数|内容)|"
            r"确保.+(?:满足|符合).+要求)",
            normalized,
        )
    )


def _strip_internal_reuse_summary_lines(content_md: str) -> str:
    lines = str(content_md or "").splitlines()
    cleaned = [
        line
        for line in lines
        if not any(pattern.match(line) for pattern in INTERNAL_REUSE_SUMMARY_LINE_PATTERNS)
    ]
    return "\n".join(cleaned).strip()


def _text_contains_any_token(text: str, tokens: tuple[str, ...] | set[str]) -> bool:
    haystack = str(text or "").casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    return any(
        token.casefold() in haystack or token.casefold().replace(" ", "") in compact_haystack
        for token in tokens
    )


def _focus_match_from_tokens(
    *,
    heading_text: str,
    content_text: str,
    focus_tokens: tuple[str, ...],
    noise_tokens: tuple[str, ...],
) -> tuple[bool, bool]:
    haystack = f"{heading_text}\n{content_text}"
    return _text_contains_any_token(haystack, focus_tokens), _text_contains_any_token(haystack, noise_tokens)


def _token_hit_count_in_text(text: str, tokens: tuple[str, ...] | set[str]) -> int:
    haystack = str(text or "").casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    return sum(
        1
        for token in tokens
        if token.casefold() in haystack or token.casefold().replace(" ", "") in compact_haystack
    )


def _should_skip_technical_cross_chapter_noise(
    *,
    target_section_type: str,
    candidate_section_type: str,
    heading_text: str,
    content_text: str,
) -> bool:
    normalized_target = str(target_section_type or "unknown").lower()
    if normalized_target not in TECHNICAL_SCHEME_SECTION_TYPES:
        return False
    normalized_candidate = str(candidate_section_type or "unknown").lower()
    candidate_text = f"{heading_text}\n{content_text}"
    if normalized_candidate in TECHNICAL_REUSE_HARD_NOISE_SECTION_TYPES:
        return True
    if _text_contains_any_token(candidate_text, TECHNICAL_REUSE_HARD_NOISE_TOKENS):
        return True
    if normalized_target != "overall_solution":
        return False

    focus_hits = _token_hit_count_in_text(candidate_text, OVERALL_SOLUTION_FOCUS_TOKENS)
    if normalized_candidate in OVERALL_SOLUTION_CORE_SECTION_TYPES:
        return focus_hits == 0
    if normalized_candidate in {
        "communication_interface",
        "control_logic",
        "protection_interlock",
        "supply_scope",
        "bom_or_supply_list",
        "installation_conditions",
        "cabinet_layout",
    }:
        return focus_hits < 2
    return normalized_candidate == "unknown" and focus_hits == 0


def _is_substation_automation_section(section: dict[str, Any]) -> bool:
    signal = _section_asset_signal_text(section)
    return _text_contains_any_token(signal, SUBSTATION_AUTOMATION_SECTION_TOKENS)


def _should_skip_substation_automation_scenario_noise(
    *,
    section: dict[str, Any],
    candidate_section_type: str,
    heading_text: str,
    content_text: str,
) -> bool:
    if not _is_substation_automation_section(section):
        return False
    candidate_text = f"{heading_text}\n{content_text}"
    if _text_contains_any_token(candidate_text, SUBSTATION_AUTOMATION_FOCUS_TOKENS):
        return False
    normalized_candidate_type = str(candidate_section_type or "unknown").lower()
    if normalized_candidate_type in SUBSTATION_AUTOMATION_OFF_SCOPE_TYPES:
        return True
    return _text_contains_any_token(candidate_text, PROTECTION_LCI_SCENARIO_NOISE_TOKENS)


def _should_skip_generic_vfd_lci_specific_noise(
    *,
    section: dict[str, Any],
    heading_text: str,
    content_text: str,
) -> bool:
    section_signal = _section_asset_signal_text(section)
    if _text_contains_any_token(section_signal, VFD_LCI_ALLOWED_SIGNAL_TOKENS):
        return False
    return _text_contains_any_token(f"{heading_text}\n{content_text}", VFD_LCI_SPECIFIC_TOKENS)


def _should_skip_spec_curve_or_load_noise(
    *,
    target_section_type: str,
    heading_text: str,
    content_text: str,
) -> bool:
    normalized_target = str(target_section_type or "unknown").lower()
    if normalized_target not in {"transformer_spec", "motor_spec"}:
        return False
    candidate_text = f"{heading_text}\n{content_text}"
    if not _text_contains_any_token(candidate_text, SPEC_CURVE_OR_LOAD_NOISE_TOKENS):
        return False
    focus_tokens = TRANSFORMER_SPEC_FOCUS_TOKENS if normalized_target == "transformer_spec" else MOTOR_SPEC_FOCUS_TOKENS
    return _token_hit_count_in_text(candidate_text, focus_tokens) < 2


def _should_skip_spec_target_mismatch_noise(
    *,
    target_section_type: str,
    candidate_section_type: str,
    heading_text: str,
    content_text: str,
) -> bool:
    normalized_target = str(target_section_type or "unknown").lower()
    if normalized_target not in {"transformer_spec", "motor_spec"}:
        return False
    candidate_text = f"{heading_text}\n{content_text}"
    focus_tokens = TRANSFORMER_SPEC_FOCUS_TOKENS if normalized_target == "transformer_spec" else MOTOR_SPEC_FOCUS_TOKENS
    concrete_tokens = (
        TRANSFORMER_SPEC_CONCRETE_TOKENS
        if normalized_target == "transformer_spec"
        else MOTOR_SPEC_CONCRETE_TOKENS
    )
    focus_hits = _token_hit_count_in_text(candidate_text, focus_tokens)
    concrete_hits = _token_hit_count_in_text(candidate_text, concrete_tokens)
    normalized_candidate = str(candidate_section_type or "unknown").lower()
    if normalized_candidate == normalized_target:
        return focus_hits == 0 or concrete_hits == 0
    if not _text_contains_any_token(candidate_text, SPEC_PARAMETER_SIGNAL_TOKENS):
        return True
    return concrete_hits < 2


def _should_skip_reuse_scenario_noise(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    target_section_type: str,
    candidate_section_type: str,
    heading_text: str,
    content_text: str,
) -> bool:
    if _should_skip_substation_automation_scenario_noise(
        section=section,
        candidate_section_type=candidate_section_type,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    if target_section_type == "vfd_spec" and _should_skip_generic_vfd_lci_specific_noise(
        section=section,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    if _should_skip_spec_curve_or_load_noise(
        target_section_type=target_section_type,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    if _should_skip_spec_target_mismatch_noise(
        target_section_type=target_section_type,
        candidate_section_type=candidate_section_type,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    if target_section_type == "protection_interlock" and _should_skip_protection_scenario_noise(
        section=section,
        global_params=global_params,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    if _should_skip_technical_cross_chapter_noise(
        target_section_type=target_section_type,
        candidate_section_type=candidate_section_type,
        heading_text=heading_text,
        content_text=content_text,
    ):
        return True
    return False


def _should_skip_protection_scenario_noise(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    heading_text: str,
    content_text: str,
) -> bool:
    product_line = str(global_params.get("product_line") or "").strip().lower()
    if product_line in {"", "generic", "lci"}:
        return False
    section_signal = _section_asset_signal_text(section).casefold()
    if any(token.casefold() in section_signal for token in PROTECTION_LCI_SCENARIO_NOISE_TOKENS):
        return False
    haystack = f"{heading_text}\n{content_text}".casefold()
    mismatch_hits = {
        token for token in PROTECTION_LCI_SCENARIO_NOISE_TOKENS if token.casefold() in haystack
    }
    return len(mismatch_hits) >= 2


def _derive_project_draft_status(drafts: list[SectionDraft]) -> str:
    statuses = {str(getattr(draft, "status", "") or "").lower() for draft in drafts}
    if statuses & {"review_required", "rejected", "manual_required"}:
        return "REVIEW_REQUIRED"
    if statuses:
        return "DRAFT_READY"
    return "OUTLINE_APPROVED"


def _make_generation_metric(
    *,
    section_id: str,
    draft_status: str,
    generation_details: dict[str, Any],
    quality_gate_result: dict[str, Any],
) -> dict[str, Any]:
    knowledge_wiki_prior_summary = (
        generation_details.get("knowledge_wiki_prior_summary")
        if isinstance(generation_details.get("knowledge_wiki_prior_summary"), dict)
        else {}
    )
    return {
        "section_id": section_id,
        "draft_status": str(draft_status or ""),
        "effective_path": str(generation_details.get("effective_path") or "unknown"),
        "refinement_status": str(generation_details.get("refinement_status") or "not_applicable"),
        "refinement_error": str(generation_details.get("refinement_error") or "").strip(),
        "quality_gate_status": str(quality_gate_result.get("status") or "skipped"),
        "knowledge_wiki_prior_hit_block_count": int(knowledge_wiki_prior_summary.get("prior_hit_block_count") or 0),
        "knowledge_wiki_prior_total_boost": float(knowledge_wiki_prior_summary.get("total_prior_boost") or 0),
    }


def _compute_next_section_draft_version(current_draft_version: int | None, existing_max_draft_version: int | None) -> int:
    return max(int(current_draft_version or 0), int(existing_max_draft_version or 0)) + 1


async def _ensure_available_section_draft_version(
    *,
    session: AsyncSession,
    project_id: UUID,
    requested_version: int,
) -> int:
    existing_max = await session.scalar(
        select(func.max(SectionDraft.draft_version)).where(SectionDraft.project_id == project_id)
    )
    return max(int(requested_version or 0), int(existing_max or 0) + 1)


def _build_generation_summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        return {
            "total_sections": 0,
            "effective_paths": {},
            "refinement_statuses": {},
            "quality_gate_statuses": {},
            "fallback_rate": 0.0,
            "refinement_error_count": 0,
            "knowledge_wiki_prior_sections": 0,
            "knowledge_wiki_prior_block_count": 0,
            "knowledge_wiki_prior_total_boost": 0.0,
        }

    total = len(metrics)
    effective_paths = Counter(str(item.get("effective_path") or "unknown") for item in metrics)
    refinement_statuses = Counter(str(item.get("refinement_status") or "not_applicable") for item in metrics)
    quality_gate_statuses = Counter(str(item.get("quality_gate_status") or "skipped") for item in metrics)
    fallback_count = sum(1 for item in metrics if str(item.get("refinement_status") or "").startswith("fallback_"))
    refinement_error_count = sum(1 for item in metrics if str(item.get("refinement_error") or "").strip())
    knowledge_wiki_prior_sections = sum(
        1 for item in metrics if int(item.get("knowledge_wiki_prior_hit_block_count") or 0) > 0
    )
    knowledge_wiki_prior_block_count = sum(int(item.get("knowledge_wiki_prior_hit_block_count") or 0) for item in metrics)
    knowledge_wiki_prior_total_boost = round(
        sum(float(item.get("knowledge_wiki_prior_total_boost") or 0) for item in metrics),
        4,
    )
    return {
        "total_sections": total,
        "effective_paths": dict(sorted(effective_paths.items())),
        "refinement_statuses": dict(sorted(refinement_statuses.items())),
        "quality_gate_statuses": dict(sorted(quality_gate_statuses.items())),
        "fallback_rate": round(fallback_count / max(total, 1), 4),
        "refinement_error_count": refinement_error_count,
        "knowledge_wiki_prior_sections": knowledge_wiki_prior_sections,
        "knowledge_wiki_prior_block_count": knowledge_wiki_prior_block_count,
        "knowledge_wiki_prior_total_boost": knowledge_wiki_prior_total_boost,
    }


def _build_evidence_retrieval_trace(
    *,
    citations: list[dict[str, Any]],
    preferred_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    return {
        "selected_count": len(citations),
        "preferred_evidence_ids": sorted(str(item) for item in (preferred_evidence_ids or set()) if str(item).strip()),
        "selected_items": [
            {
                "evidence_id": item.get("evidence_id"),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "heading_path": item.get("heading_path"),
                "type": item.get("type"),
                "relevance_score": item.get("relevance_score"),
            }
            for item in citations[:REUSE_TRACE_BLOCK_LIMIT]
        ],
    }


def _build_asset_retrieval_trace(
    *,
    query: str,
    asset_types: list[str] | None,
    skipped_optional_search: bool,
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]] | None = None,
    search_trace: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_candidates = list(asset_candidates or [])
    return {
        "query": str(query or ""),
        "asset_types": list(asset_types or []),
        "skipped_optional_search": bool(skipped_optional_search),
        "search_trace": dict(search_trace or {}),
        "diagnostics": dict(diagnostics or {}),
        "selected_count": len(recommended_assets),
        "candidate_count": len(normalized_candidates),
        "selected_assets": [
            {
                "asset_id": item.get("asset_id"),
                "asset_type": item.get("asset_type"),
                "visual_role": item.get("visual_role"),
                "display_title": item.get("display_title") or item.get("title"),
                "document_name": item.get("document_name"),
                "heading_path": item.get("heading_path"),
                "score": item.get("score"),
                "reason": item.get("reason"),
                "reason_trace": item.get("reason_trace") or [],
                "score_breakdown": item.get("score_breakdown")
                or (item.get("metadata") or {}).get("retrieval_score_breakdown")
                or {},
                "visual_backend": item.get("score_breakdown", {}).get("visual_backend")
                if isinstance(item.get("score_breakdown"), dict)
                else (item.get("metadata") or {}).get("visual_backend"),
                "visual_source": item.get("score_breakdown", {}).get("visual_source")
                if isinstance(item.get("score_breakdown"), dict)
                else (item.get("metadata") or {}).get("visual_source"),
            }
            for item in recommended_assets[:REUSE_TRACE_BLOCK_LIMIT]
        ],
        "asset_candidates": [
            {
                "asset_id": item.get("asset_id"),
                "asset_type": item.get("asset_type"),
                "visual_role": item.get("visual_role"),
                "display_title": item.get("display_title") or item.get("title"),
                "document_name": item.get("document_name"),
                "heading_path": item.get("heading_path"),
                "score": item.get("score"),
                "reason": item.get("reason"),
                "reason_trace": item.get("reason_trace") or [],
                "score_breakdown": item.get("score_breakdown")
                or (item.get("metadata") or {}).get("retrieval_score_breakdown")
                or {},
                "visual_backend": item.get("score_breakdown", {}).get("visual_backend")
                if isinstance(item.get("score_breakdown"), dict)
                else (item.get("metadata") or {}).get("visual_backend"),
                "visual_source": item.get("score_breakdown", {}).get("visual_source")
                if isinstance(item.get("score_breakdown"), dict)
                else (item.get("metadata") or {}).get("visual_source"),
            }
            for item in normalized_candidates[:ASSET_CANDIDATE_LIMIT]
        ],
    }


def _build_composition_retrieval_trace(
    *,
    evidence_trace: dict[str, Any],
    asset_trace: dict[str, Any],
    reuse_pack: dict[str, Any],
    generation_details: dict[str, Any],
) -> dict[str, Any]:
    reuse_trace = reuse_pack.get("retrieval_trace") if isinstance(reuse_pack.get("retrieval_trace"), dict) else {}
    return {
        "pipeline": "composition_main",
        "layers": {
            "evidence": {
                "role": "需求/证据层",
                **evidence_trace,
            },
            "reuse": {
                "role": "历史方案复用层",
                "query": reuse_trace.get("query"),
                "query_intents": reuse_trace.get("query_intents") or {},
                "child_aware": bool(reuse_trace.get("child_aware")),
                "knowledge_wiki_terms": reuse_trace.get("knowledge_wiki_terms") or [],
                "knowledge_wiki_product_cards": reuse_trace.get("knowledge_wiki_product_cards") or [],
                "knowledge_wiki_module_cards": reuse_trace.get("knowledge_wiki_module_cards") or [],
                "knowledge_wiki_source_documents": reuse_trace.get("knowledge_wiki_source_documents") or [],
                "knowledge_wiki_resolved_sample_ids": reuse_trace.get("knowledge_wiki_resolved_sample_ids") or [],
                "section_candidates": reuse_trace.get("section_candidates") or [],
                "scoped_sections": reuse_trace.get("scoped_sections") or [],
                "subsection_retrieval": reuse_trace.get("subsection_retrieval") or [],
                "retrieval_mode": generation_details.get("retrieval_mode"),
                "selected_sections": generation_details.get("selected_sections") or [],
                "selected_blocks": generation_details.get("selected_blocks") or [],
                "knowledge_wiki_prior_summary": generation_details.get("knowledge_wiki_prior_summary") or {},
                "selection_reason": generation_details.get("selection_reason") or {},
                "token_budget": generation_details.get("token_budget") or {},
            },
            "assets": {
                "role": "图表/公式层",
                **asset_trace,
            },
        },
    }


def _new_inter_section_state(
    *,
    task_id: str,
    outline_title: str,
    global_params: dict[str, Any],
) -> WorkflowState:
    return WorkflowState(
        task_id=task_id,
        project_name=str(outline_title or "技术方案"),
        global_params=global_params if isinstance(global_params, dict) else {},
        outline=None,
    )


def _normalize_inter_section_topic(title: str) -> str:
    normalized = SECTION_TITLE_PREFIX_PATTERN.sub("", str(title or "")).strip()
    return normalized or str(title or "未命名章节").strip() or "未命名章节"


def _should_record_inter_section_context(*, draft_status: str, content_md: str) -> bool:
    status = str(draft_status or "").strip().lower()
    if status in {"manual_required", "rejected"}:
        return False
    return bool(str(content_md or "").strip())


def _record_inter_section_context(
    *,
    state: WorkflowState,
    covered_topics: dict[int, str],
    section_index: int,
    section_title: str,
    draft_status: str,
    content_md: str,
) -> None:
    if not _should_record_inter_section_context(draft_status=draft_status, content_md=content_md):
        return
    title = str(section_title or f"章节 {section_index + 1}").strip() or f"章节 {section_index + 1}"
    state.record_section_summary(section_index, title, str(content_md or ""))
    covered_topics[section_index] = _normalize_inter_section_topic(title)


def _build_preceding_context(
    *,
    state: WorkflowState,
    covered_topics: dict[int, str],
    current_index: int,
) -> str:
    base_context = str(state.build_preceding_context(current_index) or "").strip()
    prior_topics = [topic for idx, topic in sorted(covered_topics.items()) if idx < current_index and topic]
    if not prior_topics:
        return base_context
    topics = prior_topics[:INTER_SECTION_TOPIC_LIMIT]
    suffix = "等" if len(prior_topics) > INTER_SECTION_TOPIC_LIMIT else ""
    covered_topics_text = f"已覆盖主题：{'、'.join(topics)}{suffix}"
    if not base_context:
        return covered_topics_text
    return f"{base_context}\n{covered_topics_text}"


def _build_preceding_context_from_existing_drafts(
    *,
    task_id: str,
    outline_title: str,
    global_params: dict[str, Any],
    sections: list[dict[str, Any]],
    current_section_id: str,
    existing_drafts: list[SectionDraft],
) -> str:
    current_section_id = str(current_section_id or "").strip()
    if not current_section_id:
        return ""
    current_index = next(
        (idx for idx, item in enumerate(sections) if str(item.get("section_id") or "").strip() == current_section_id),
        -1,
    )
    if current_index <= 0:
        return ""

    state = _new_inter_section_state(
        task_id=task_id,
        outline_title=outline_title,
        global_params=global_params,
    )
    covered_topics: dict[int, str] = {}
    drafts_by_id = {str(draft.section_id or "").strip(): draft for draft in existing_drafts}
    for index, section in enumerate(sections):
        if index >= current_index:
            break
        draft = drafts_by_id.get(str(section.get("section_id") or "").strip())
        if not draft:
            continue
        _record_inter_section_context(
            state=state,
            covered_topics=covered_topics,
            section_index=index,
            section_title=str(section.get("title") or getattr(draft, "title", "") or ""),
            draft_status=str(getattr(draft, "status", "") or ""),
            content_md=str(getattr(draft, "content_md", "") or ""),
    )
    return _build_preceding_context(state=state, covered_topics=covered_topics, current_index=current_index)


def _build_fast_parallel_section_context(*, sections: list[dict[str, Any]], current_index: int) -> str:
    current_section = sections[current_index] if 0 <= current_index < len(sections) else {}
    previous_titles = [
        _normalize_inter_section_topic(str(item.get("title") or ""))
        for item in sections[max(0, current_index - 3):current_index]
    ]
    next_titles = [
        _normalize_inter_section_topic(str(item.get("title") or ""))
        for item in sections[current_index + 1:current_index + 4]
    ]
    lines = [
        "并发快速成稿上下文：本轮优先保证各章节方向正确、证据复用准确，后续允许人工复核。",
        f"当前章节：{current_section.get('section_id') or current_index + 1} {current_section.get('title') or '未命名章节'}",
    ]
    if previous_titles:
        lines.append(f"前序章节主题：{'、'.join(title for title in previous_titles if title)}")
    if next_titles:
        lines.append(f"后续章节主题：{'、'.join(title for title in next_titles if title)}")
    lines.append("请避免展开前后章节的主体内容，只保留本章节必要边界说明。")
    return "\n".join(line for line in lines if line.strip())


def _dedupe_parallel_section_paragraphs(
    *,
    contents: list[str],
    min_chars: int = 90,
    similarity_threshold: float = 0.96,
) -> tuple[list[str], dict[str, Any]]:
    seen: list[str] = []
    deduped_contents: list[str] = []
    removed_count = 0

    for content in contents:
        kept_blocks: list[str] = []
        for block in re.split(r"\n{2,}", str(content or "").strip()):
            normalized = re.sub(r"\s+", "", block)
            if len(normalized) >= min_chars and any(
                normalized == prior or SequenceMatcher(None, normalized, prior).ratio() >= similarity_threshold
                for prior in seen
            ):
                removed_count += 1
                continue
            if normalized:
                seen.append(normalized)
            kept_blocks.append(block)
        deduped_contents.append("\n\n".join(kept_blocks).strip())

    return deduped_contents, {
        "mode": "deterministic_adjacent_dedupe",
        "removed_duplicate_paragraphs": removed_count,
    }


def _merge_prompt_context(*segments: str) -> str:
    normalized_segments = [str(segment or "").strip() for segment in segments if str(segment or "").strip()]
    return "\n\n".join(normalized_segments)


def should_use_extractive_reuse(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> bool:
    if str(section.get("generation_mode") or "baseline") != "reuse_first":
        return False
    reusable_blocks = reuse_pack.get("reusable_blocks") or []
    if not reusable_blocks:
        return False
    if len(reusable_blocks) >= 2:
        return True
    section_class = str(section.get("section_class") or "").lower()
    if section_class in EXTRACTIVE_SECTION_CLASSES:
        return True
    if bool(section.get("parameter_sensitive")) or bool(section.get("asset_required")):
        return True
    block_section_types = {
        str((block.get("metadata") or {}).get("section_type") or "").lower()
        for block in reusable_blocks
    }
    return bool(block_section_types & EXTRACTIVE_SECTION_TYPES)


def build_extractive_reuse_section_content(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    global_params: dict[str, Any],
    knowledge_wiki_context: str = "",
) -> str:
    title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
    query_terms = _build_reuse_query_terms(section=section, global_params=global_params)
    target_taxonomy = infer_target_taxonomy(section)
    candidate_blocks = _filter_reuse_blocks_for_assembly(
        reusable_blocks=list(reuse_pack.get("reusable_blocks") or []),
        target_taxonomy=target_taxonomy,
        section=section,
    )
    candidate_blocks = _order_assembly_blocks(candidate_blocks=candidate_blocks, target_taxonomy=target_taxonomy)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if _is_site_conditions_section(section=section, target_taxonomy=target_taxonomy):
        return _build_site_conditions_reuse_section_content(
            title=title,
            section=section,
            candidate_blocks=candidate_blocks,
        )
    if _is_power_condition_section(section=section, target_taxonomy=target_taxonomy):
        return _build_power_condition_reuse_section_content(
            title=title,
            section=section,
            candidate_blocks=candidate_blocks,
            global_params=global_params,
            knowledge_wiki_context=knowledge_wiki_context,
        )
    if target_section_type in {"bom_or_supply_list", "supply_scope"}:
        return _build_supply_scope_reuse_section_content(
            section=section,
            title=title,
            candidate_blocks=candidate_blocks,
            reuse_pack=reuse_pack,
        )
    if _is_parameter_summary_section(section=section, target_taxonomy=target_taxonomy):
        return _build_parameter_summary_reuse_section_content(
            title=title,
            candidate_blocks=candidate_blocks,
        )
    lines = [f"## {title}", ""]
    seen_paragraphs: set[str] = set()
    used_subheadings: set[str] = set()
    total_chars = 0

    for block in candidate_blocks:
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        if not body:
            continue
        content_form = str((block.get("metadata") or {}).get("content_form") or "narrative").lower()
        if target_section_type == "overall_solution":
            paragraphs = _extract_overall_solution_reuse_paragraphs(body)
        elif target_section_type == "main_circuit_scheme" and content_form not in {"parameter_table", "bom_table"}:
            paragraphs = _extract_main_circuit_reuse_paragraphs(body)
        else:
            paragraphs = _extract_reuse_paragraphs(body)
        if target_section_type == "overall_solution":
            ranked_paragraphs = paragraphs
        else:
            ranked_paragraphs = sorted(
                paragraphs,
                key=lambda paragraph: _score_reuse_paragraph(
                    paragraph=paragraph,
                    query_terms=query_terms,
                    target_taxonomy=target_taxonomy,
                ),
                reverse=True,
            )
        selected_paragraphs: list[str] = []
        for paragraph in ranked_paragraphs:
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if len(normalized) < 24 or normalized in seen_paragraphs:
                continue
            selected_paragraphs.append(_normalize_technical_spacing(paragraph.strip()))
            seen_paragraphs.add(normalized)
            total_chars += len(paragraph)
            if target_section_type == "overall_solution":
                max_paragraphs = 8 if len(candidate_blocks) <= 4 else 5
                char_limit = 6500
            else:
                max_paragraphs = 3 if len(candidate_blocks) <= 2 else 2
                char_limit = 4200
            if len(selected_paragraphs) >= max_paragraphs or total_chars >= char_limit:
                break
        if not selected_paragraphs:
            continue
        subheading = _derive_reuse_subheading(block=block, section_title=title, target_taxonomy=target_taxonomy)
        if subheading and subheading not in used_subheadings:
            lines.append(f"### {subheading}")
            lines.append("")
            used_subheadings.add(subheading)
        lines.extend(selected_paragraphs)
        lines.append("")
        if total_chars >= (6500 if target_section_type == "overall_solution" else 4200):
            break

    if len(lines) <= 2:
        fallback_text = str(section.get("purpose") or "请基于历史方案复用块补充本章节内容。").strip()
        lines.extend([fallback_text, ""])

    return "\n".join(lines).rstrip() + "\n"


def build_llm_write_fallback_section_content(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
    purpose = str(section.get("purpose") or section.get("description") or "").strip()
    project_name = str(global_params.get("project_name") or "").strip()
    keywords = [str(item).strip() for item in (section.get("keywords") or []) if str(item).strip()]
    lines = [f"## {title}", ""]
    if purpose:
        lines.extend([purpose, ""])
    if project_name:
        lines.extend([f"本章节需结合 `{project_name}` 的真实需求、现场条件和最终设备资料进一步补全。", ""])
    if keywords:
        lines.extend(["### 待补充技术要点", ""])
        for keyword in keywords[:6]:
            lines.append(f"- {keyword}")
        lines.append("")
    lines.extend(
        [
            "### 当前状态",
            "",
            "本章节自动生成未能完成，已保留章节目标和待补充要点，需重新生成或人工补充后再交付客户。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def polish_extractive_reuse_section_content(*, section: dict[str, Any], content_md: str) -> str:
    section_title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
    polished = sanitize_generated_section_content(
        content_md=content_md,
        section_title=section_title,
        section_purpose=str(section.get("purpose") or section.get("description") or ""),
    )
    target_section_type = str(infer_target_taxonomy(section).get("section_type") or "unknown").lower()
    opening_sentence = EXTRACTIVE_SECTION_OPENINGS.get(target_section_type)
    if opening_sentence and opening_sentence not in polished:
        polished = re.sub(
            r"^(## [^\n]+\n\n)(?=(### |\||\[\[ASSET:|- \[\[ASSET:))",
            lambda match: f"{match.group(1)}{opening_sentence}\n\n",
            polished,
            count=1,
        )
    for label, lead_sentence in (EXTRACTIVE_TABLE_LEADS.get(target_section_type) or {}).items():
        if lead_sentence in polished:
            continue
        polished = re.sub(
            rf"(### {re.escape(label)}\n\n)(?=\|)",
            lambda match: f"{match.group(1)}{lead_sentence}\n\n",
            polished,
            count=1,
        )
    return polished.rstrip() + "\n"


def _build_supply_scope_reuse_section_content(
    *,
    section: dict[str, Any],
    title: str,
    candidate_blocks: list[dict[str, Any]],
    reuse_pack: dict[str, Any],
) -> str:
    lines = [
        f"## {title}",
        "",
        f"本章节用于说明{str(section.get('project_name') or '本项目')}中软起动系统相关设备的供货边界、随机资料和接口分工。最终供货内容以双方确认的供货清单、技术协议和合同文件为准。",
        "",
        "### 主设备供货清单",
        "",
        "结合现有对标资料，当前可按下表理解主设备供货范围：",
        "",
    ]
    note = _extract_supply_scope_note(candidate_blocks)
    primary_table = _select_primary_supply_scope_table(candidate_blocks)
    if primary_table:
        primary_table = _build_canonical_supply_scope_table(table_md=primary_table, section=section)
        lines.extend([primary_table, ""])
    if note:
        lines.extend([note, ""])
    lines.extend(
        [
            "如项目范围后续明确包含电机本体、励磁装置或其他成套附件，则应在最终供货清单中单独列项，不在本章节默认扩展。",
            "",
            "### 随机资料与随机附件",
            "",
            "随设备交付的资料和附件通常包括：",
            "",
            "- 装箱清单、合格证明和出厂试验资料；",
            "- 安装、接线、调试和维护说明文件；",
            "- 设备外形、布置、接口及端子相关资料；",
            "- 随机专用工具和首批备品备件清单；",
            "- 与通信接口、联锁配合和运行维护相关的技术文件。",
            "",
            "### 监测元件与软件范围",
            "",
            "本项目如涉及装置内部必要的测温、测流、状态检测等监测元件，原则上随主设备成套配置，不再单独拆分列项；如需独立列示，则在最终供货清单或技术文件中明确。",
            "",
            "与装置运行相关的软件、参数文件、数据点表、通信接口定义及联锁配合内容，作为主设备技术文件的一部分交付，并在项目确认文件中固定版本边界。",
            "",
            "### 供货边界与接口分工",
            "",
            "本次装置本体供货范围之外的现场实施内容，通常包括以下部分：",
            "",
            "- 设备之间以及设备至现场系统接口的动力电缆和控制电缆；",
            "- 电缆终端、安装附件及现场敷设材料；",
            "- 设备基础、支架、桥架、接地及土建安装配合内容；",
            "- 与既有电机、开关设备、上位控制系统及现场仪表的外部接口实施工作。",
            "",
            "因此，本章节既用于说明主设备和随机资料的交付范围，也用于明确现场接口和实施责任边界，避免对供货范围产生歧义。",
            "",
            "### 说明事项",
            "",
            "1. 表内数量和范围用于当前方案阶段的交付边界表达，最终以确认清单为准。",
            "2. 未在本表中单独列项但属于设备内部集成功能的检测器件、标准附件和基础软件，按主设备成套供货理解。",
            "3. 需要在现场实施阶段完成的外部电缆、安装材料和土建配合内容，不视为装置本体供货范围。",
            "",
        ]
    )

    content = "\n".join(lines).rstrip() + "\n"
    return ensure_required_asset_placeholders(content_md=content, reuse_pack=reuse_pack)


def _build_site_conditions_reuse_section_content(
    *,
    title: str,
    section: dict[str, Any],
    candidate_blocks: list[dict[str, Any]],
) -> str:
    summary = _extract_site_condition_summary(candidate_blocks)
    project_name = str(section.get("project_name") or "本项目")
    lines = [
        f"## {title}",
        "",
        f"本章节用于明确{project_name}的环境边界、安装条件、公用工程配套条件及实施接口要求，作为 LCI 软起动系统、配套变压器及相关电气接口设计的统一边界。",
        "",
        "### 环境与安装边界",
        "",
        "根据现有项目需求和对标资料，本项目安装环境按工业厂房内布置条件执行，主要边界如下：",
        "",
        "| 项目 | 设计边界 |",
        "| --- | --- |",
        f"| 安装场所 | {summary['install_location']} |",
        f"| 海拔 | {summary['altitude']} |",
        f"| 环境温度 | {summary['ambient_temp']} |",
        f"| 大气环境 | {summary['hazardous_area']} |",
        f"| 基本要求 | {summary['basic_requirements']} |",
        "",
        "上述边界用于约束设备选型、绝缘配合、布置方式及防护等级要求；若现场存在持续凝露、强腐蚀性介质、导电粉尘或高温辐射，应在接口联络文件中同步调整防护与降额边界。",
        "",
        "### 公用工程与配套条件",
        "",
        "现场配套条件应满足以下要求：",
        "",
        "- 安装区域应提供稳定的低压辅助电源和控制电源，并在接口文件中明确来源、电压等级和容量边界；",
        "- 装置安装区域应具备持续通风散热条件，满足电力电子设备和配套变压器长期运行要求；",
        "- 桥架、接地干线、电缆通道及端子分界应与既有系统布置保持一致，避免二次改造冲突；",
        "- 设备室应满足照明、检修通道、吊装空间及日常维护的基本条件。",
        "",
        "### 安装与实施接口边界",
        "",
        "安装实施阶段应重点落实以下接口条件：",
        "",
        "- LCI 软起动装置、配套变压器及相关附件具备独立安装和检修空间；",
        "- 高压主回路、低压辅助电源、控制信号和接地系统具备清晰的接口分界；",
        "- 运输通道、门洞尺寸、吊装点位和基础承载条件满足设备就位要求；",
        "- 电缆进出线方向、桥架衔接和端子分界与既有系统布置协调一致；",
        "- 与既有高压开关设备、电机回路及土建条件的衔接边界应在联络阶段固化。",
        "",
        "### 运输与储存要求",
        "",
        "为保证设备在制造、到货和现场就位过程中的完整性，运输与储存阶段应满足以下要求：",
        "",
        "- 运输过程中采取防雨、防潮、防冲击和防倾覆措施；",
        "- 储存区域保持干燥、通风，避免腐蚀性气体和持续凝露影响；",
        "- 长期存放时对电子元件、绝缘件和接插件进行防潮保护并定期检查；",
        "- 设备到货后按照包装标识完成开箱检查、分类存放和条件保护。",
        "",
        "### 联络阶段需固化的现场条件",
        "",
        "| 确认项 | 固化目的 |",
        "| --- | --- |",
        "| 设备室净尺寸、门洞尺寸及运输吊装路径 | 固化设备拆包、运输和就位方案 |",
        "| 柜列布置、检修通道及散热组织方式 | 固化设备布置和长期运行条件 |",
        "| 桥架走向、进出线方向及端子分界点 | 固化电缆接口与施工界面 |",
        "| 与既有高压开关设备、电机回路及土建条件的衔接边界 | 固化改造范围和实施责任 |",
        "| 特殊环境附加防护要求 | 固化防护等级、防腐及降额措施 |",
        "",
        "上述条件固化后，可形成最终安装布置、接口分工和实施边界文件。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _build_power_condition_reuse_section_content(
    *,
    title: str,
    section: dict[str, Any],
    candidate_blocks: list[dict[str, Any]],
    global_params: dict[str, Any],
    knowledge_wiki_context: str = "",
) -> str:
    project_name = str(global_params.get("project_name") or "本项目")
    voltage_level = _extract_power_condition_voltage(global_params=global_params, candidate_blocks=candidate_blocks)
    frequency = _extract_power_condition_frequency(candidate_blocks=candidate_blocks)
    application_object = _infer_power_condition_application(section=section, global_params=global_params)
    reference_text = _collect_power_condition_reference_text(
        candidate_blocks=candidate_blocks,
        knowledge_wiki_context=knowledge_wiki_context,
    )
    supply_voltage_range = _extract_power_condition_supply_window(reference_text)
    frequency_range = _extract_power_condition_frequency_window(reference_text)
    grounding_mode = _extract_power_condition_grounding(reference_text)
    short_circuit_boundary = _extract_power_condition_short_circuit(reference_text)
    voltage_dip_boundary = _extract_power_condition_voltage_dip(reference_text)
    control_power_boundary = _extract_power_condition_control_power(reference_text)
    motor_reference_rows = _extract_power_condition_motor_reference_rows(reference_text)
    lines = [
        f"## {title}",
        "",
        f"本章节用于归纳{project_name}当前已确认的供电边界、负载属性和启动约束，并把已取得的项目输入、对标资料参考值和待最终锁定参数分开表达，作为 LCI 软起动系统选型和控制设计的依据。",
        "",
        "### 项目已确认的设计输入",
        "",
        "结合项目需求和当前已确认边界，本项目已落实的基础输入如下：",
        "",
        "| 项目 | 当前边界 | 来源 |",
        "| --- | --- | --- |",
        f"| 应用对象 | {application_object} | 项目需求 |",
        f"| 系统电压等级 | {voltage_level} | 项目需求 / 全局参数 |",
        f"| 系统频率 | {frequency} | 项目需求 / 对标资料 |",
        "| 启动方式 | LCI 变频软起动 | 项目需求 |",
        "| 运行方式 | 启动完成后切换至工频运行 | 项目需求 |",
        "| 控制配合对象 | 励磁系统、同步装置及一次开关设备 | 现有系统边界 |",
        "",
    ]
    reference_rows: list[tuple[str, str, str]] = []
    if supply_voltage_range:
        reference_rows.append(("电网电压允许范围", supply_voltage_range, "用于校核绝缘配合和变压器匹配"))
    if frequency_range:
        reference_rows.append(("频率边界", frequency_range, "用于校核同步切换与控制整定"))
    if grounding_mode:
        reference_rows.append(("接地方式", grounding_mode, "用于校核保护与绝缘边界"))
    if short_circuit_boundary:
        reference_rows.append(("短路能力", short_circuit_boundary, "用于校核主回路耐受与保护整定"))
    if voltage_dip_boundary:
        reference_rows.append(("启动期间电压波动", voltage_dip_boundary, "用于评估母线扰动适应能力"))
    if control_power_boundary:
        reference_rows.append(("辅助 / 控制电源", control_power_boundary, "用于落实辅机与控制回路供电条件"))
    if reference_rows:
        lines.extend(
            [
                "### 对标资料提取的供电与系统参考边界",
                "",
                "以下参数来自已导入历史方案资料，可作为当前方案阶段的参考边界，最终项目值以业主确认资料为准：",
                "",
                "| 项目 | 参考值 | 用途 |",
                "| --- | --- | --- |",
            ]
        )
        for label, value, usage in reference_rows:
            lines.append(f"| {label} | {value} | {usage} |")
        lines.append("")

    if motor_reference_rows:
        lines.extend(
            [
                "### 对标资料中的电机参数线索",
                "",
                "已导入资料中可直接识别的电机参数如下，可用于装置预选型和接口校核：",
                "",
                "| 参数项 | 参考值 | 使用方式 |",
                "| --- | --- | --- |",
            ]
        )
        for label, value, usage in motor_reference_rows:
            lines.append(f"| {label} | {value} | {usage} |")
        lines.append("")

    lines.extend(
        [
            "### 负载特性与启动约束",
            "",
            "高炉鼓风机属于大惯量、连续运行类关键机组，启动过程应重点控制母线冲击、加速平稳性和同步切换可靠性。LCI 软起动系统应满足以下基本约束：",
            "",
            "- 启动阶段对 10kV 母线的电压扰动可控，避免对既有系统造成明显冲击；",
            "- 从静止到接近额定转速的加速过程保持连续转矩输出，兼顾风机和联轴器机械应力控制；",
            "- 与励磁系统、同期装置及一次开关设备之间的动作边界清晰，确保切换条件明确、反馈完整；",
            "- 启动完成后可靠切换至工频运行，软起动系统退出启动控制状态。",
            "",
            "### 对装置选型的直接约束",
            "",
            "- 电网短路容量、接地方式和允许电压波动范围将直接影响整流变压器阻抗匹配、主回路器件耐受能力及保护定值协调；",
            "- 同步电机额定功率、额定电流、额定转速和等效惯量将直接决定装置容量、热容量和加速曲线整定边界；",
            "- 允许启动时间、允许启动次数及最低可接受起动转矩将直接影响功率器件热设计和控制限值设置；",
            "- 与励磁系统、上位控制系统及一次开关设备的接口分工将直接影响联锁逻辑和切换时序设计。",
            "",
            "### 需在联络阶段锁定的项目参数",
            "",
            "| 参数项 | 当前状态 / 来源 | 用途 |",
            "| --- | --- | --- |",
            "| 10kV 母线短路容量及接地方式 | 以业主电气系统资料和现场核实结果为准 | 固化主回路选型、保护定值和绝缘校核边界 |",
            "| 电机额定功率、额定电流、额定转速及等效惯量 | 以电机铭牌、试验资料和负载曲线为准 | 固化装置容量、加速能力和热容量校核 |",
            "| 风机静阻转矩、负载转矩曲线及最低可接受起动转矩 | 需结合工艺和机组运行条件确认 | 固化起动转矩储备和加速斜率设置 |",
            "| 允许启动时间、启动次数及连续再启动要求 | 需由业主运行条件确认 | 固化控制限值、热保护和运行策略 |",
            "| 励磁系统接口、同步切换判据及断路器动作边界 | 需在联络阶段与现地系统共同确认 | 固化接口信号、动作时序和联锁逻辑 |",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _collect_power_condition_reference_text(
    *,
    candidate_blocks: list[dict[str, Any]],
    knowledge_wiki_context: str,
) -> str:
    parts: list[str] = []
    for block in candidate_blocks[:8]:
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        if body:
            parts.append(body)
    context = str(knowledge_wiki_context or "").strip()
    if context:
        parts.append(context)
    return _normalize_technical_spacing("\n".join(parts))


def _extract_first_power_condition_match(reference_text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, reference_text, re.IGNORECASE)
        if match:
            return _normalize_technical_spacing(str(match.group(1) or "").strip("：: ;；,，"))
    return ""


def _extract_power_condition_supply_window(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (
            r"(?:电压|额定电压|系统电压等级)\s*[:：]?\s*([0-9.]+\s*kV\s*[+±][^,\n;；]{1,18})",
            r"(?:电压|额定电压|系统电压等级)\s*[:：]?\s*([0-9.]+\s*kV\s*~\s*[0-9.]+\s*kV)",
            r"(?:电压|额定电压|系统电压等级)[^\n]{0,16}([0-9.]+\s*kV)",
        ),
    )


def _extract_power_condition_frequency_window(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (
            r"(?:频率|额定频率|系统额定频率)\s*[:：]?\s*([0-9.]+\s*Hz\s*[±+][^,\n;；]{1,18})",
            r"(?:频率|额定频率|系统额定频率)[^\n]{0,16}([0-9.]+\s*Hz)",
        ),
    )


def _extract_power_condition_grounding(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (r"(?:接地系统|接地方式)\s*[:：]?\s*([^\n|,，;；]{2,32})",),
    )


def _extract_power_condition_short_circuit(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (
            r"(?:短路容量)\s*[:：]?\s*([^\n|]{0,36}(?:MVA|mva)[^\n|]{0,12})",
            r"(?:短路电流)\s*[:：]?\s*([^\n|]{0,36}(?:kA|ka)[^\n|]{0,12})",
        ),
    )


def _extract_power_condition_voltage_dip(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (
            r"(?:电压波动)\s*[:：]?\s*([^\n|]{0,40})",
            r"(?:压降)\s*[:：]?\s*([^\n|]{0,24})",
        ),
    )


def _extract_power_condition_control_power(reference_text: str) -> str:
    return _extract_first_power_condition_match(
        reference_text,
        (
            r"(?:二次控制电源|辅助电源)\s*[:：]?\s*([A-Z]*\s*[0-9/]+\s*V[^\n|;；]{0,18})",
            r"([A-Z]*\s*220V\s*/\s*380V[^\n|;；]{0,18})",
        ),
    )


def _extract_power_condition_motor_reference_rows(reference_text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    candidates = (
        ("额定功率", _extract_first_power_condition_match(reference_text, (r"(?:额定功率)\s*[:：]?\s*([0-9./]+\s*(?:kW|MW))",))),
        ("额定电流", _extract_first_power_condition_match(reference_text, (r"(?:额定电流)\s*[:：]?\s*([0-9.]+\s*A)",))),
        ("额定转速", _extract_first_power_condition_match(reference_text, (r"(?:额定转速|工作转速)\s*[:：]?\s*([0-9.]+\s*(?:r/min|rpm))",))),
    )
    for label, value in candidates:
        if not value:
            continue
        usage = {
            "额定功率": "用于预估装置容量和主回路电流等级",
            "额定电流": "用于校核功率单元和变压器电流边界",
            "额定转速": "用于校核加速区间和切换条件",
        }[label]
        rows.append((label, value, usage))
    return rows


def _use_deterministic_reuse_builder(
    *,
    section: dict[str, Any],
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    return _is_site_conditions_section(section=section, target_taxonomy=taxonomy) or _is_power_condition_section(
        section=section,
        target_taxonomy=taxonomy,
    )


def _extract_supply_scope_note(candidate_blocks: list[dict[str, Any]]) -> str:
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        if str(metadata.get("content_form") or "").lower() != "narrative":
            continue
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        if not body:
            continue
        paragraphs = _extract_reuse_paragraphs(body)
        for paragraph in paragraphs:
            lowered = paragraph.casefold()
            if not any(token in lowered for token in ("供货", "scope", "备注", "remark", "乙方提供", "不在", "不含")):
                continue
            cleaned = _clean_supply_scope_note(paragraph)
            if cleaned:
                return cleaned
    return ""


def _clean_supply_scope_note(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            continue
        line = re.sub(r"^\*+|\*+$", "", line).strip()
        if not line or line.startswith("<!--"):
            continue
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", line))
        ascii_word_count = len(re.findall(r"[A-Za-z]{2,}", line))
        if cjk_count == 0 and ascii_word_count >= 3:
            continue
        line = re.sub(r"\bABB\b", "本次供货", line, flags=re.IGNORECASE)
        line = re.sub(r"本次供货\s*仅提供", "本次供货仅提供", line)
        line = re.sub(r"供货范围表内的设备，该套变频系统的如下部分不在\s*本次供货\s*的供货范围内", "供货范围表内的设备，以下内容不在本次供货范围内", line)
        line = _normalize_technical_spacing(line)
        if line not in cleaned:
            cleaned.append(line)
    return "\n".join(cleaned[:6]).strip()


def _extract_site_condition_summary(candidate_blocks: list[dict[str, Any]]) -> dict[str, str]:
    combined_text = "\n".join(
        _normalize_reuse_block_body(str(block.get("content_md") or ""))
        for block in candidate_blocks[:6]
        if str(((block.get("metadata") or {}).get("content_form") or "")).lower() in {"narrative", "parameter_table", "interface_table"}
    )
    combined_text = _normalize_technical_spacing(combined_text)
    install_location = "LCI 软起动装置及配套变压器按户内电气室或鼓风机配套站房布置"
    if any(token in combined_text.casefold() for token in ("outdoor", "户外")):
        install_location = "LCI 软起动装置及配套设备按户外或半户外条件布置，详细防护等级需进一步确认"

    altitude = "小于 1000 m"
    altitude_match = re.search(r"(?:海拔|Altitude)[^\n|:：]*([<≤]?\s*\d{3,4}\s*m)", combined_text, re.IGNORECASE)
    if altitude_match:
        altitude = altitude_match.group(1).replace("<", "小于 ").replace("≤", "不大于 ").strip()

    ambient_temp = "最高 40°C，最低 -10°C"
    max_match = re.search(r"(?:最高|Max\.?|MAX\.?)[^\d\-+]*([\-+]?\d{1,3})\s*°?\s*C", combined_text, re.IGNORECASE)
    min_match = re.search(r"(?:最低|Min\.?|MIN\.?)[^\d\-+]*([\-+]?\d{1,3})\s*°?\s*C", combined_text, re.IGNORECASE)
    if max_match and min_match:
        ambient_temp = f"最高 {max_match.group(1)}°C，最低 {min_match.group(1)}°C"

    hazardous_area = "非爆炸危险区域，不按防爆场所设计"
    if any(token in combined_text.casefold() for token in ("hazardous", "防爆")) and "non hazardous" not in combined_text.casefold():
        hazardous_area = "危险区域等级需结合现场防爆分区进一步确认"

    return {
        "install_location": install_location,
        "altitude": altitude,
        "ambient_temp": ambient_temp,
        "hazardous_area": hazardous_area,
        "basic_requirements": "满足通风散热、检修通道、吊装运输及安全接地条件",
    }


def _extract_power_condition_voltage(*, global_params: dict[str, Any], candidate_blocks: list[dict[str, Any]]) -> str:
    voltage_level = str(global_params.get("voltage_level") or "").strip()
    if voltage_level:
        return voltage_level
    combined_text = "\n".join(
        _normalize_reuse_block_body(str(block.get("content_md") or ""))
        for block in candidate_blocks[:6]
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*kV", combined_text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}kV"
    return "10kV"


def _extract_power_condition_frequency(*, candidate_blocks: list[dict[str, Any]]) -> str:
    combined_text = "\n".join(
        _normalize_reuse_block_body(str(block.get("content_md") or ""))
        for block in candidate_blocks[:6]
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*Hz", combined_text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}Hz"
    return "50Hz"


def _infer_power_condition_application(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    signal_text = " ".join(
        [
            str(section.get("title") or ""),
            str(section.get("purpose") or ""),
            str(global_params.get("business_objective") or ""),
            str(global_params.get("project_name") or ""),
        ]
    ).casefold()
    if "鼓风机" in signal_text and "同步电机" in signal_text:
        return "高炉鼓风机高压同步电机"
    if "同步电机" in signal_text:
        return "高压同步电机"
    return "高压电机软起动对象"


def _build_canonical_supply_scope_table(*, table_md: str, section: dict[str, Any]) -> str:
    rows = _extract_canonical_supply_scope_rows(table_md=table_md)
    required_items = _infer_supply_scope_required_items(section)
    existing_text = "\n".join(row["device_name"] for row in rows).casefold()
    for item in required_items:
        if _supply_scope_item_present(item=item, existing_text=existing_text):
            continue
        rows.append(_default_supply_scope_row(item))
        existing_text += f"\n{item}".casefold()
    if not rows:
        rows = [_default_supply_scope_row(item) for item in ("LCI 软起动装置", "输入变压器", "输出变压器", "专用工具", "备品备件")]
    lines = [
        "| 序号 | 设备名称 | 主要内容 | 数量 | 单位 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {row['device_name']} | {row['description']} | {row['quantity']} | {row['unit']} | {row['remark']} |"
        )
    return "\n".join(lines)


def _extract_canonical_supply_scope_rows(*, table_md: str) -> list[dict[str, str]]:
    table_lines = [line.rstrip() for line in str(table_md or "").splitlines() if "|" in line]
    if len(table_lines) < 3:
        return []
    data_lines = table_lines[2:]
    rows: list[dict[str, str]] = []
    seen_items: set[str] = set()
    for line in data_lines:
        cells = _split_markdown_table_row(line)
        if len(cells) < 2:
            continue
        joined = " ".join(cells)
        canonical = _match_supply_scope_item(joined)
        if canonical is None:
            continue
        item, description, default_unit, remark = canonical
        if item in seen_items:
            continue
        quantity = _extract_supply_scope_quantity(cells)
        unit = _extract_supply_scope_unit(cells, default=default_unit)
        rows.append(
            {
                "device_name": item,
                "description": description,
                "quantity": quantity,
                "unit": unit,
                "remark": remark,
            }
        )
        seen_items.add(item)
    return rows


def _match_supply_scope_item(text: str) -> tuple[str, str, str, str] | None:
    lowered = str(text or "").casefold()
    if any(token in lowered for token in ("lci", "sfc", "converter", "软起", "变频器")):
        return ("LCI 软起动装置", "含主功率单元、控制单元及启动回路成套功能", "套", "用于同步电机受控启动和切换")
    if any(token in lowered for token in ("input transformer", "输入变压器", "进线变压器")):
        return ("输入变压器", "与 LCI 启动回路配套", "台", "额定参数以最终确认资料为准")
    if any(token in lowered for token in ("output transformer", "输出变压器", "出线变压器")):
        return ("输出变压器", "与电机侧启动回路配套", "台", "与主回路方案同步定型")
    if any(token in lowered for token in ("special tools", "特殊工具", "专用工具")):
        return ("专用工具", "晶闸管更换工具等随机专用工具", "套", "随主设备成套供货")
    if any(token in lowered for token in ("spare", "备品备件", "备件")):
        return ("备品备件", "首批随机备品备件", "批", "具体明细在最终备件清单中明确")
    return None


def _default_supply_scope_row(item: str) -> dict[str, str]:
    matched = _match_supply_scope_item(item) or (item, "待技术确认", _supply_scope_unit_for_item(item), "待技术确认")
    return {
        "device_name": matched[0],
        "description": matched[1],
        "quantity": "1",
        "unit": matched[2],
        "remark": matched[3],
    }


def _extract_supply_scope_quantity(cells: list[str]) -> str:
    for cell in cells:
        match = re.search(r"\b(\d+(?:\.\d+)?)\b", cell)
        if match:
            return match.group(1)
    return "1"


def _extract_supply_scope_unit(cells: list[str], *, default: str) -> str:
    for cell in cells:
        lowered = cell.casefold()
        if any(token in lowered for token in ("台", "pcs")):
            return "台"
        if any(token in lowered for token in ("套", "set")):
            return "套"
        if any(token in lowered for token in ("批", "batch")):
            return "批"
    return default


def _select_primary_supply_scope_table(candidate_blocks: list[dict[str, Any]]) -> str:
    table_candidates: list[tuple[float, str]] = []
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        content_form = str(metadata.get("content_form") or "").lower()
        if content_form not in {"bom_table", "parameter_table"}:
            continue
        table_text = _normalize_markdown_table_text(str(block.get("content_md") or ""))
        if not table_text:
            continue
        score = float(block.get("selection_score") or 0)
        if content_form == "bom_table":
            score += 0.18
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", table_text))
        english_words = len(re.findall(r"[A-Za-z]{2,}", table_text))
        score += min(chinese_chars / 200.0, 0.3)
        score -= min(english_words / 120.0, 0.12)
        table_candidates.append((score, table_text))
    if not table_candidates:
        return ""
    table_candidates.sort(key=lambda item: item[0], reverse=True)
    return table_candidates[0][1]


def _normalize_markdown_table_text(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    table_lines: list[str] = []
    for line in lines:
        if "|" not in line:
            continue
        normalized = _normalize_technical_spacing(line)
        table_lines.append(normalized)
    if len(table_lines) < 2:
        return ""
    return "\n".join(table_lines)


def _ensure_supply_scope_required_items(*, table_md: str, section: dict[str, Any]) -> str:
    required_items = _infer_supply_scope_required_items(section)
    if not required_items:
        return table_md
    table_lines = [line.rstrip() for line in str(table_md or "").splitlines() if "|" in line]
    if len(table_lines) < 2:
        return table_md
    headers = _split_markdown_table_row(table_lines[0])
    if not headers:
        return table_md
    existing_text = "\n".join(table_lines).casefold()
    next_index = _next_supply_scope_row_index(table_lines[2:])
    appended: list[str] = []
    for item in required_items:
        if _supply_scope_item_present(item=item, existing_text=existing_text):
            continue
        cells = _build_supply_scope_row_cells(headers=headers, item=item, row_index=next_index)
        appended.append(f"| {' | '.join(cells)} |")
        existing_text += f"\n{item}".casefold()
        next_index += 1
    if not appended:
        return table_md
    return "\n".join([*table_lines, *appended])


def _infer_supply_scope_required_items(section: dict[str, Any]) -> list[str]:
    section_text = " ".join(
        [
            str(section.get("title") or ""),
            str(section.get("purpose") or ""),
            " ".join(str(item) for item in (section.get("keywords") or [])),
        ]
    ).casefold()
    required: list[str] = []
    for item, triggers in SUPPLY_SCOPE_REQUIRED_ITEM_RULES:
        if any(trigger.casefold() in section_text for trigger in triggers) and item not in required:
            required.append(item)
    return required


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = str(line or "").strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _next_supply_scope_row_index(data_lines: list[str]) -> int:
    indexes: list[int] = []
    for line in data_lines:
        cells = _split_markdown_table_row(line)
        if not cells:
            continue
        match = re.search(r"\d+", cells[0])
        if match:
            indexes.append(int(match.group(0)))
    return (max(indexes) + 1) if indexes else 1


def _supply_scope_item_present(*, item: str, existing_text: str) -> bool:
    aliases = {
        "LCI/SFC 变频软起动装置": ("lci", "sfc", "变频器", "变频软起", "软起动装置", "软启动装置"),
        "输入变压器": ("输入变压器", "进线变压器"),
        "输出变压器": ("输出变压器", "出线变压器"),
        "励磁控制盘": ("励磁控制盘", "励磁控制柜", "励磁柜", "励磁调节柜"),
    }.get(item, (item,))
    return any(alias.casefold() in existing_text for alias in aliases)


def _build_supply_scope_row_cells(*, headers: list[str], item: str, row_index: int) -> list[str]:
    cells: list[str] = []
    item_written = False
    for header in headers:
        header_norm = re.sub(r"\s+", "", header).casefold()
        if any(token in header_norm for token in ("序号", "序", "no.", "no", "index")):
            cells.append(str(row_index))
        elif any(token in header_norm for token in ("设备", "名称", "component", "item", "description")):
            cells.append(item)
            item_written = True
        elif any(token in header_norm for token in ("型号", "规格", "model", "type")):
            cells.append("待技术确认")
        elif any(token in header_norm for token in ("制造", "供应", "厂家", "品牌", "manufacturer", "supplier")):
            cells.append("待确认")
        elif any(token in header_norm for token in ("数量", "qty", "number")):
            cells.append("1")
        elif any(token in header_norm for token in ("单位", "unit")):
            cells.append(_supply_scope_unit_for_item(item))
        elif any(token in header_norm for token in ("备注", "边界", "范围", "remark", "scope")):
            cells.append("待技术确认")
        else:
            cells.append("待技术确认")
    if not item_written and cells:
        for index, header in enumerate(headers):
            header_norm = re.sub(r"\s+", "", header).casefold()
            if not any(token in header_norm for token in ("序号", "序", "no.", "no", "index")):
                cells[index] = item
                break
    return cells


def _supply_scope_unit_for_item(item: str) -> str:
    if "变压器" in item:
        return "台"
    return "套"


def _build_parameter_summary_reuse_section_content(
    *,
    title: str,
    candidate_blocks: list[dict[str, Any]],
) -> str:
    lines = [f"## {title}", ""]
    intro = "以下主要技术参数和性能指标依据现有相近方案资料整理，未明确项建议在技术确认阶段进一步确认。"
    lines.extend([intro, ""])

    primary_parameter_block = _select_parameter_narrative_block(
        candidate_blocks=candidate_blocks,
        preferred_terms=("技术数据", "技术参数", "变频器", "型号", "额定电流"),
    )
    primary_parameter_bullets = _extract_parameter_summary_bullets(
        block=primary_parameter_block,
        preferred_terms=("型号", "额定", "电流", "晶闸管", "冷却", "温度", "功率"),
        limit=8,
    )
    if primary_parameter_bullets:
        lines.extend(["### 主要装置技术参数", "", *primary_parameter_bullets, ""])

    performance_block = _select_parameter_narrative_block(
        candidate_blocks=candidate_blocks,
        preferred_terms=("启动", "同步", "升速", "工频", "循环"),
    )
    performance_bullets = _extract_parameter_summary_bullets(
        block=performance_block,
        preferred_terms=("启动", "同步", "加速", "工频", "循环", "切换"),
        limit=8,
    )
    if performance_bullets:
        lines.extend(["### 启动性能与运行能力", "", *performance_bullets, ""])

    transformer_table = _select_parameter_table(
        candidate_blocks=candidate_blocks,
        preferred_terms=("输入", "输出", "变压器", "阻抗", "绕组"),
    )
    if transformer_table:
        lines.extend(["### 输入/输出变压器技术参数", "", "主要技术参数如下表所示。", "", transformer_table, ""])

    grid_table = _select_parameter_table(
        candidate_blocks=candidate_blocks,
        preferred_terms=("电网", "短路容量", "频率", "电压"),
    )
    if grid_table:
        lines.extend(["### 电网适应性及边界条件", "", "电网侧边界条件如下表所示。", "", grid_table, ""])

    lines.extend(
        [
            "### 待技术确认项",
            "",
            "- 系统效率、综合功率因数、防护等级及电磁兼容限值等未在现有资料中完整给出，建议在技术确认阶段明确。",
            "- 控制柜及辅助设备详细规格应结合最终接口边界和现场条件进一步确认。",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _select_parameter_narrative_block(
    *,
    candidate_blocks: list[dict[str, Any]],
    preferred_terms: tuple[str, ...],
) -> dict[str, Any] | None:
    scored: list[tuple[float, dict[str, Any]]] = []
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        if str(metadata.get("content_form") or "narrative").lower() != "narrative":
            continue
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
        focus_match, noise_match = _parameter_summary_focus_flags(heading_text=heading_text, content_text=body)
        score = float(block.get("selection_score") or 0)
        if focus_match:
            score += 0.18
        if noise_match and not focus_match:
            score -= 0.25
        if any(term.casefold() in f"{heading_text}\n{body}".casefold() for term in preferred_terms):
            score += 0.16
        if score >= 0.55:
            scored.append((score, block))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _select_parameter_table(
    *,
    candidate_blocks: list[dict[str, Any]],
    preferred_terms: tuple[str, ...],
) -> str:
    scored: list[tuple[float, str]] = []
    for block in candidate_blocks:
        metadata = block.get("metadata") or {}
        if str(metadata.get("content_form") or "").lower() != "parameter_table":
            continue
        table_text = _normalize_markdown_table_text(str(block.get("content_md") or ""))
        if not table_text:
            continue
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        score = float(block.get("selection_score") or 0)
        if any(term.casefold() in f"{heading_text}\n{table_text}".casefold() for term in preferred_terms):
            score += 0.2
        if "外形尺寸" in heading_text and "尺寸" not in " ".join(preferred_terms):
            score -= 0.3
        scored.append((score, table_text))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _extract_parameter_summary_bullets(
    *,
    block: dict[str, Any] | None,
    preferred_terms: tuple[str, ...],
    limit: int,
) -> list[str]:
    if not block:
        return []
    body = _normalize_reuse_block_body(str(block.get("content_md") or ""))
    bullets: list[str] = []
    seen: set[str] = set()
    for raw_line in body.splitlines():
        line = str(raw_line or "").strip().lstrip("").strip()
        if not line:
            continue
        if "|" in line or line.startswith("#"):
            continue
        lowered = line.casefold()
        if lowered in {"版本", "页码"} or "dayu electric" in lowered:
            continue
        if re.fullmatch(r"[0-9.,%'’+\- ]+", line):
            continue
        signal_match = any(term.casefold() in lowered for term in preferred_terms) or bool(re.search(r"\d", line))
        if not signal_match:
            continue
        if any(token in lowered for token in ("版本", "页码", "side view", "minimal clearance")):
            continue
        normalized = _normalize_technical_spacing(re.sub(r"\s{2,}", " ", line))
        if len(normalized) < 6:
            continue
        bullet = normalized if normalized.startswith("-") else f"- {normalized}"
        dedupe_key = bullet.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        bullets.append(bullet)
        if len(bullets) >= limit:
            break
    return bullets


def build_reuse_refinement_context(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    global_params: dict[str, Any],
) -> str:
    replace_fields = ", ".join(reuse_pack.get("must_replace_fields") or []) or "无"
    banned_terms = ", ".join(str(item) for item in (reuse_pack.get("banned_terms") or [])) or "无"
    placeholder_text = ", ".join(
        str(item.get("placeholder") or "")
        for item in (reuse_pack.get("required_asset_placeholders") or [])
        if str(item.get("placeholder") or "")
    ) or "无"
    key_params = ", ".join(
        f"{key}={value}"
        for key, value in global_params.items()
        if key in {"project_name", "product_line", "industry", "voltage_level", "power_rating", "quantity"}
        and value not in (None, "", [], {})
    ) or "无"
    return "\n".join(
        [
            f"章节标题: {section.get('title') or ''}",
            f"章节目的: {section.get('purpose') or ''}",
            f"章节类型: {section.get('section_class') or ''}",
            f"当前关键参数: {key_params}",
            f"必须替换字段: {replace_fields}",
            f"禁止沿用词: {banned_terms}",
            f"必须保留资产占位符: {placeholder_text}",
        ]
    )


def build_reuse_refinement_instruction(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> str:
    lines = [
        "请将给定章节草稿整理成客户可阅读的正式技术章节。",
        "优先保留现有技术细节、设备构成、接口逻辑和参数表达，不要压缩信息密度。",
        "保持现有章节标题、三级小标题、表格和列表结构，非必要不要改写结构。",
        "改写后正文长度原则上不低于原稿的 80%，不要把技术段压缩成一句结论。",
        "只做必要的统一、去重和替换，不要自由扩写背景，不要补充空泛套话。",
        "删除旧客户、旧项目和样板来源痕迹，严格使用当前项目字段。",
    ]
    if reuse_pack.get("required_asset_placeholders"):
        lines.append("若章节需要图表引用，请保留已有 [[ASSET:...]] 占位符，并放在合适位置。")
    if bool(section.get("parameter_sensitive")):
        lines.append("参数相关表述必须保持谨慎，不得虚构未确认参数。")
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))


def resolve_reuse_refinement_content(
    *,
    assembled_content: str,
    rewritten_content: str | None,
    section_title: str,
) -> tuple[str, str, str | None]:
    assembled = sanitize_generated_section_content(content_md=assembled_content, section_title=section_title)
    if not rewritten_content:
        return assembled, "fallback_assembled", "rewrite_missing"
    rewritten = sanitize_generated_section_content(content_md=rewritten_content, section_title=section_title)
    assembled_body = _strip_section_heading(assembled)
    rewritten_body = _strip_section_heading(rewritten)
    if not rewritten_body.strip():
        return assembled, "fallback_assembled", "rewrite_empty"
    if any(token in rewritten_body for token in REWRITE_LEAKAGE_TOKENS):
        return assembled, "fallback_assembled", "rewrite_leakage"
    if len(rewritten_body) < max(260, int(len(assembled_body) * 0.68)):
        return assembled, "fallback_assembled", "rewrite_too_thin"
    if _technical_density(rewritten_body) < (_technical_density(assembled_body) * 0.72):
        return assembled, "fallback_assembled", "rewrite_low_density"
    if rewritten_body.count("\n\n") + 1 < max(2, (assembled_body.count("\n\n") + 1) // 2):
        return assembled, "fallback_assembled", "rewrite_structure_collapse"
    return rewritten, "rewrite_applied", None


def select_preferred_reuse_content(
    *,
    assembled_content: str,
    rewritten_content: str | None,
    section_title: str,
) -> str:
    content, _, _ = resolve_reuse_refinement_content(
        assembled_content=assembled_content,
        rewritten_content=rewritten_content,
        section_title=section_title,
    )
    return content


def _strip_section_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _technical_density(text: str) -> float:
    body = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", text)
    tokens = TECHNICAL_TOKEN_PATTERN.findall(body)
    return len(tokens) / max(len(body), 1)


def _normalize_reuse_block_body(text: str) -> str:
    lines = str(text or "").splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if stripped.startswith("#"):
            continue
        if stripped.lower().startswith("来源:"):
            continue
        cleaned.append(line.rstrip())
    body = "\n".join(cleaned)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _extract_terminal_heading_label(heading_text: str) -> str:
    parts = [segment.strip() for segment in str(heading_text or "").split(">") if segment.strip()]
    label = parts[-1] if parts else str(heading_text or "").strip()
    label = re.sub(r"^[一二三四五六七八九十0-9.\-、\s]+", "", label).strip()
    return label


def _heading_should_be_excluded_from_customer_reuse(heading_text: str) -> bool:
    label = _extract_terminal_heading_label(heading_text)
    if not label:
        return False
    if any(pattern.match(label) for pattern in INTERNAL_REUSE_HEADING_PATTERNS):
        return True
    if LATIN_ENUM_REUSE_HEADING_PATTERN.match(label):
        return True
    if BILINGUAL_GENERIC_REUSE_HEADING_PATTERN.match(label):
        return True
    if GENERIC_LABEL_REUSE_PATTERN.match(label):
        return True
    if label in GENERIC_REUSE_HEADINGS:
        return True
    return False


def _extract_reuse_paragraphs(text: str) -> list[str]:
    paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", text) if segment.strip()]
    if not paragraphs and text.strip():
        return [text.strip()]
    return paragraphs


def _extract_overall_solution_reuse_paragraphs(text: str) -> list[str]:
    paragraphs = _extract_reuse_paragraphs(text)
    extracted: list[str] = []
    bold_labels: list[str] = []
    seen_labels: set[str] = set()
    for match in re.finditer(r"\*\*([^*\n]{1,48})\*\*", text):
        label = _normalize_technical_spacing(match.group(1).strip())
        normalized = re.sub(r"\s+", " ", label).casefold()
        if not label or normalized in seen_labels:
            continue
        if any(token in normalized for token in ("风机的总启动时间", "total starting")):
            continue
        seen_labels.add(normalized)
        bold_labels.append(label)
    schematic_labels = [
        label
        for label in bold_labels
        if not any(token in label.casefold() for token in ("启动时间", "starting", "加速时间"))
    ]
    if len(schematic_labels) >= 4:
        extracted.append(f"单线拓扑包含：{'、'.join(schematic_labels[:16])}。")

    for paragraph in paragraphs:
        normalized = _normalize_technical_spacing(paragraph.strip())
        if not normalized:
            continue
        lowered = normalized.casefold()
        if normalized == "<!-- image -->" or lowered.startswith("<!-- image"):
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", normalized):
            continue
        if re.match(r"^#{1,6}\s*", normalized):
            heading_label = re.sub(r"^#{1,6}\s*", "", normalized).strip()
            if not any(
                token in heading_label.casefold()
                for token in ("单线图", "single line", "启动和同步", "synchronization", "lci", "启动特性", "start-up")
            ):
                continue
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
        ascii_word_count = len(re.findall(r"[A-Za-z]{2,}", normalized))
        if cjk_count == 0 and ascii_word_count < 3 and len(normalized) < 24:
            continue
        extracted.append(normalized)
    return extracted or paragraphs


def _extract_main_circuit_reuse_paragraphs(text: str) -> list[str]:
    paragraphs = _extract_reuse_paragraphs(text)
    refined: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        segments = [paragraph]
        if len(paragraph) >= 160 and ("\n" in paragraph or "•" in paragraph):
            segments = [
                item.strip()
                for item in re.split(r"\n+|\t*•\t*|\s+•\s+", paragraph)
                if item.strip()
            ]
        kept_lines: list[str] = []
        for raw_segment in segments:
            line = _normalize_technical_spacing(raw_segment.strip("• \t"))
            if not line:
                continue
            lowered = line.casefold()
            if any(token in lowered for token in MAIN_CIRCUIT_LINE_DROP_TOKENS):
                continue
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", line))
            ascii_word_count = len(re.findall(r"[A-Za-z]{2,}", line))
            if cjk_count == 0 and ascii_word_count >= 3:
                continue
            topology_hits = _focus_token_hit_count(
                heading_text="",
                content_text=line,
                focus_tokens=MAIN_CIRCUIT_TOPOLOGY_TOKENS,
            )
            if topology_hits <= 0 and not any(token.casefold() in lowered for token in MAIN_CIRCUIT_LINE_KEEP_TOKENS):
                continue
            normalized = re.sub(r"\s+", " ", line).strip()
            if normalized in seen:
                continue
            kept_lines.append(line)
            seen.add(normalized)
        if kept_lines:
            refined.append("\n".join(kept_lines))
    return refined


def _filter_reuse_blocks_for_assembly(
    *,
    reusable_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
    section: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not reusable_blocks:
        return []
    top_score = max(float(block.get("selection_score") or 0) for block in reusable_blocks)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    parameter_summary = _is_parameter_summary_section(section=section, target_taxonomy=target_taxonomy)
    installation_section = target_section_type == "installation_conditions"
    spare_parts_section = _is_spare_parts_section(section=section, target_taxonomy=target_taxonomy)
    filtered: list[dict[str, Any]] = []
    scenario_guard_skipped = False
    for block in reusable_blocks:
        score = float(block.get("selection_score") or 0)
        metadata = block.get("metadata") or {}
        section_type = str(metadata.get("section_type") or "unknown").lower()
        content_form = str(metadata.get("content_form") or "narrative").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        original_content_text = str(block.get("content_md") or "")
        content_text = _strip_internal_reuse_summary_lines(original_content_text)
        if original_content_text.strip() and not content_text:
            continue
        if score < max(0.38, top_score * 0.5):
            continue
        if _heading_should_be_excluded_from_customer_reuse(heading_text):
            if not (
                target_section_type == "overall_solution"
                and section_type == "overall_solution"
                and score >= max(0.55, top_score * 0.5)
            ):
                continue
        if heading_looks_like_document_title(heading_text) and score < top_score * 0.9:
            continue
        if section and _should_skip_reuse_scenario_noise(
            section=section,
            global_params={},
            target_section_type=target_section_type,
            candidate_section_type=section_type,
            heading_text=heading_text,
            content_text=content_text,
        ):
            scenario_guard_skipped = True
            continue
        if target_section_type == "main_circuit_scheme":
            focus_match, noise_match = _main_circuit_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            topology_hits = _focus_token_hit_count(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=MAIN_CIRCUIT_TOPOLOGY_TOKENS,
            )
            hard_noise_hits = _focus_token_hit_count(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=MAIN_CIRCUIT_HARD_NOISE_TOKENS,
            )
            if content_form == "figure":
                continue
            if heading_looks_like_document_title(heading_text) and topology_hits < 3:
                continue
            if section_type in {"protection_interlock", "control_logic", "communication_interface"} and topology_hits < 2:
                continue
            if section_type in {"vfd_spec", "transformer_spec", "motor_spec", "supply_scope"} and topology_hits < 2:
                continue
            if hard_noise_hits >= 2 and topology_hits < 3:
                continue
            if content_form == "narrative" and noise_match and topology_hits < 2:
                continue
            if content_form == "narrative" and not focus_match and topology_hits < 2:
                continue
        elif target_section_type == "design_basis":
            focus_match, noise_match = _design_basis_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form in {"figure", "formula"}:
                continue
            if section_type in {"control_logic", "protection_interlock", "main_circuit_scheme", "service_support"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "vfd_spec":
            focus_match, noise_match = _vfd_spec_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form == "formula":
                continue
            if section_type in {"protection_interlock", "cabinet_layout", "service_support", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "motor_spec":
            focus_match, noise_match = _motor_interface_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if section_type in {"cabinet_layout", "supply_scope", "service_support"} and not focus_match:
                continue
            if section_type == "protection_interlock" and not focus_match:
                continue
            if content_form == "narrative" and not focus_match and section_type not in {"motor_spec", "protection_interlock"}:
                continue
            if content_form == "bom_table" and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "protection_interlock":
            focus_match, noise_match = _protection_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            focus_hits = _focus_token_hit_count(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=PROTECTION_FOCUS_TOKENS,
            )
            if content_form in {"formula", "figure"}:
                continue
            if section_type in {"main_circuit_scheme", "transformer_spec"} and not focus_match:
                continue
            if section_type == "unknown" and focus_hits < 2:
                continue
            if content_form == "narrative" and focus_hits < 2 and section_type not in {"protection_interlock", "communication_interface", "control_logic"}:
                continue
            if focus_hits < 2 and any(token in content_text for token in ("同期", "同步", "励磁")):
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "control_logic":
            focus_match, noise_match = _control_logic_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form in {"formula", "bom_table"}:
                continue
            if section_type in {"cabinet_layout", "commissioning_acceptance", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif parameter_summary:
            focus_match, noise_match = _parameter_summary_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form == "formula":
                continue
            if section_type in {"control_logic", "communication_interface", "protection_interlock"}:
                continue
            if content_form == "narrative" and score < max(0.52, top_score * 0.62) and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif installation_section:
            focus_match, noise_match = _focus_match_from_tokens(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=INSTALLATION_FOCUS_TOKENS,
                noise_tokens=INSTALLATION_NOISE_TOKENS,
            )
            if section_type in {"overall_solution", "main_circuit_scheme", "starter_spec", "control_logic"} and (
                noise_match or not focus_match
            ):
                continue
            if noise_match and not focus_match:
                continue
            if not focus_match:
                continue
        elif spare_parts_section:
            focus_match, noise_match = _focus_match_from_tokens(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=SPARE_PARTS_FOCUS_TOKENS,
                noise_tokens=SPARE_PARTS_NOISE_TOKENS,
            )
            heading_focus = _text_contains_any_token(heading_text, SPARE_PARTS_FOCUS_TOKENS)
            if content_form not in {"bom_table", "parameter_table", "narrative"}:
                continue
            if section_type in {"site_conditions", "design_basis"}:
                continue
            if noise_match and not focus_match:
                continue
            if content_form == "narrative" and not heading_focus:
                continue
            if not focus_match:
                continue
        if target_section_type not in {"unknown", "overall_solution"} and not spare_parts_section:
            if section_type == "unknown" and score < top_score * 0.7:
                continue
            if section_type not in {target_section_type, *related_section_types(target_section_type)} and score < top_score * 0.78:
                continue
        if target_section_type in {"communication_interface", "bom_or_supply_list", "supply_scope"} and content_form == "formula" and score < top_score * 0.92:
            continue
        if target_section_type in {"bom_or_supply_list", "supply_scope"}:
            focus_match, noise_match = _supply_scope_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if section_type == "service_support":
                continue
            if noise_match and not focus_match:
                continue
        if (
            target_section_type in {"bom_or_supply_list", "supply_scope"}
            and any(token in heading_text.casefold() for token in ("启动", "同步过程", "运行过程", "控制逻辑"))
            and score < top_score * 0.98
        ):
            continue
        if (
            target_section_type == "communication_interface"
            and any(token in heading_text for token in ("性能要求", "整体要求", "技术要求"))
            and score < top_score * 0.95
        ):
            continue
        normalized_block = dict(block)
        normalized_block["content_md"] = content_text
        filtered.append(normalized_block)
    if not filtered and (
        target_section_type in {"protection_interlock", "control_logic"}
        or scenario_guard_skipped
        or installation_section
        or spare_parts_section
    ):
        return []
    filtered = filtered or reusable_blocks[:2]
    if installation_section or spare_parts_section:
        return filtered
    return _augment_with_support_blocks(
        filtered=filtered,
        reusable_blocks=reusable_blocks,
        target_taxonomy=target_taxonomy,
        section=section,
    )


def _normalize_technical_spacing(text: str) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"(?<=\b[A-Z])\s+(?=[A-Z0-9]\b)", "", normalized)
    normalized = re.sub(r"(?<=\d)\s+(?=[A-Za-z])", "", normalized)
    normalized = re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "", normalized)
    for _ in range(4):
        normalized = re.sub(r"(?<![A-Za-z0-9])([A-Z])\s+([A-Z]\d*)(?![A-Za-z0-9])", r"\1\2", normalized)
        normalized = re.sub(r"(?<![A-Za-z0-9])([A-Z])\s+([A-Z]{2,})(?![A-Za-z0-9])", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z])\s+([A-Z](?:\d+)?)\b", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z]{2,})\s+([A-Z]{1,3})\b", r"\1\2", normalized)
        normalized = re.sub(r"\b([A-Z]{1,4})\s+(\d{1,2})\b", r"\1\2", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.strip()


def _main_circuit_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=MAIN_CIRCUIT_FOCUS_TOKENS,
        noise_tokens=MAIN_CIRCUIT_NOISE_TOKENS,
    )


def _protection_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=PROTECTION_FOCUS_TOKENS,
        noise_tokens=PROTECTION_NOISE_TOKENS,
    )


def _control_logic_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=CONTROL_LOGIC_FOCUS_TOKENS,
        noise_tokens=CONTROL_LOGIC_NOISE_TOKENS,
    )


def _parameter_summary_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=PARAMETER_SUMMARY_FOCUS_TOKENS,
        noise_tokens=PARAMETER_SUMMARY_NOISE_TOKENS,
    )


def _design_basis_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=DESIGN_BASIS_FOCUS_TOKENS,
        noise_tokens=DESIGN_BASIS_NOISE_TOKENS,
    )


def _vfd_spec_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=VFD_SPEC_FOCUS_TOKENS,
        noise_tokens=VFD_SPEC_NOISE_TOKENS,
    )


def _motor_interface_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=MOTOR_INTERFACE_FOCUS_TOKENS,
        noise_tokens=MOTOR_INTERFACE_NOISE_TOKENS,
    )


def _supply_scope_focus_flags(*, heading_text: str, content_text: str) -> tuple[bool, bool]:
    return _focus_flags(
        heading_text=heading_text,
        content_text=content_text,
        focus_tokens=SUPPLY_SCOPE_FOCUS_TOKENS,
        noise_tokens=SUPPLY_SCOPE_NOISE_TOKENS,
    )


def _focus_flags(
    *,
    heading_text: str,
    content_text: str,
    focus_tokens: tuple[str, ...],
    noise_tokens: tuple[str, ...],
) -> tuple[bool, bool]:
    haystack = f"{heading_text}\n{content_text}".casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    focus_match = any(
        token.casefold() in haystack or token.casefold().replace(" ", "") in compact_haystack
        for token in focus_tokens
    )
    noise_match = any(
        token.casefold() in haystack or token.casefold().replace(" ", "") in compact_haystack
        for token in noise_tokens
    )
    return focus_match, noise_match


def _focus_token_hit_count(*, heading_text: str, content_text: str, focus_tokens: tuple[str, ...]) -> int:
    haystack = f"{heading_text}\n{content_text}".casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    return sum(
        1
        for token in focus_tokens
        if token.casefold() in haystack or token.casefold().replace(" ", "") in compact_haystack
    )


def _is_parameter_summary_section(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    section = section or {}
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    text = _section_asset_signal_text(section).casefold()
    if _is_power_condition_section(section=section, target_taxonomy=taxonomy):
        return True
    explicit_parameter_title = any(token in text for token in ("技术参数", "性能指标", "技术数据", "额定容量"))
    if explicit_parameter_title:
        return True
    if bool(section.get("parameter_sensitive")) and any(token in text for token in ("参数", "性能", "容量", "电压", "电流")):
        return True
    return str(taxonomy.get("section_type") or "unknown").lower() in {"motor_spec", "vfd_spec", "starter_spec", "transformer_spec"} and bool(section.get("parameter_sensitive"))


def _is_site_conditions_section(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    section = section or {}
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    text = _section_asset_signal_text(section).casefold()
    section_type = str(taxonomy.get("section_type") or "unknown").lower()
    if section_type == "site_conditions":
        return True
    return any(token in text for token in ("工厂设计环境", "环境与边界条件", "安装条件", "运输储存", "现场边界"))


def _is_power_condition_section(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    section = section or {}
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    text = _section_asset_signal_text(section).casefold()
    section_type = str(taxonomy.get("section_type") or "unknown").lower()
    if any(token in text for token in ("供电系统条件", "负载参数", "电网条件", "短路容量", "启动约束")):
        return True
    return section_type in {"motor_spec", "starter_spec"} and any(
        token in text
        for token in (
            "供电条件",
            "电源条件",
            "负载条件",
            "负载数据",
            "电网边界",
            "短路容量",
            "额定功率",
            "额定电流",
        )
    )


def _is_spare_parts_section(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    section = section or {}
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    text = _section_asset_signal_text(section).casefold()
    section_type = str(taxonomy.get("section_type") or "unknown").lower()
    return section_type not in {"site_conditions", "design_basis"} and _text_contains_any_token(text, SPARE_PARTS_FOCUS_TOKENS)


def _requires_strict_reuse_evidence(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    taxonomy = target_taxonomy or infer_target_taxonomy(section or {})
    target_section_type = str(taxonomy.get("section_type") or "unknown").lower()
    return target_section_type == "installation_conditions" or _is_spare_parts_section(
        section=section,
        target_taxonomy=taxonomy,
    )


def _should_mark_generated_without_evidence_review_required(
    *,
    section: dict[str, Any] | None,
    target_taxonomy: dict[str, Any] | None = None,
) -> bool:
    section = section or {}
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    target_section_type = str(taxonomy.get("section_type") or "unknown").lower()
    if target_section_type in {"motor_spec", "transformer_spec", "vfd_spec", "starter_spec"}:
        return True
    return bool(section.get("parameter_sensitive"))


def _normalize_invalid_asset_placeholders(
    *,
    content_md: str,
    recommended_assets: list[dict[str, Any]],
) -> str:
    valid_ids = {
        str(asset.get("asset_id") or "").strip()
        for asset in recommended_assets
        if str(asset.get("asset_id") or "").strip()
    }
    asset_matches = _build_recommended_asset_reference_matches(recommended_assets)

    def _format_placeholder(asset: dict[str, Any], *, fallback_type: str | None = None) -> str:
        asset_id = str(asset.get("asset_id") or "").strip()
        asset_type = str(asset.get("asset_type") or fallback_type or "FIGURE").strip().upper()
        if not asset_type:
            asset_type = "FIGURE"
        return f"[[ASSET:{asset_type}:{asset_id}]]"

    def _replace(match: re.Match[str]) -> str:
        placeholder_type = str(match.group(1) or "").strip().upper()
        asset_ref = str(match.group(2) or "").strip()
        if asset_ref in valid_ids:
            return match.group(0)
        matched_asset = _find_recommended_asset_by_reference(asset_ref, asset_matches)
        if matched_asset:
            return _format_placeholder(matched_asset, fallback_type=placeholder_type)
        label = re.sub(r"\s+", " ", asset_ref).strip("[]【】")
        if not label:
            return "待补充确认"
        return f"待根据《{label}》进一步确认"

    def _replace_title_only(match: re.Match[str]) -> str:
        asset_ref = str(match.group(1) or "").strip()
        matched_asset = _find_recommended_asset_by_reference(asset_ref, asset_matches)
        if matched_asset:
            return _format_placeholder(matched_asset)
        label = re.sub(r"\s+", " ", asset_ref).strip("[]【】")
        if not label:
            return "待补充确认"
        return f"待根据《{label}》进一步确认"

    normalized = INVALID_ASSET_PLACEHOLDER_PATTERN.sub(_replace, str(content_md or ""))
    return TITLE_ONLY_ASSET_PLACEHOLDER_PATTERN.sub(_replace_title_only, normalized)


def _remove_mismatched_asset_placeholders(
    *,
    content_md: str,
    recommended_assets: list[dict[str, Any]],
) -> str:
    if "[[ASSET:" not in str(content_md or ""):
        return content_md
    asset_lookup = _build_asset_lookup(recommended_assets)
    if not asset_lookup:
        return content_md
    lines = str(content_md or "").splitlines()

    def _nearest_heading(index: int) -> str:
        for cursor in range(index - 1, -1, -1):
            stripped = lines[cursor].strip()
            if stripped.startswith("#### "):
                return stripped[5:].strip()
            if stripped.startswith("### "):
                return stripped[4:].strip()
            if stripped.startswith("## "):
                return stripped[3:].strip()
        return ""

    cleaned: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        matches = list(ASSET_PLACEHOLDER_PATTERN.finditer(line))
        if not matches:
            cleaned.append(line)
            continue
        heading_text = _nearest_heading(index)
        remove_line = False
        normalized_line = line
        for match in matches:
            asset_id = str(match.group(1) or "").strip()
            asset = asset_lookup.get(asset_id)
            if not asset or not heading_text:
                continue
            score = _score_asset_heading_match(
                heading_text=heading_text,
                anchor_texts=_collect_asset_anchor_texts(asset),
            )
            if score >= 0.62:
                continue
            if stripped == match.group(0) or stripped.startswith(f"- {match.group(0)}"):
                remove_line = True
                break
            normalized_line = normalized_line.replace(match.group(0), "")
        if remove_line:
            continue
        cleaned.append(normalized_line)
    return _remove_empty_asset_reference_sections(cleaned).rstrip() + "\n"


def _remove_empty_asset_reference_sections(lines: list[str]) -> str:
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped in {"### 相关图表", "#### 相关图表"}:
            cursor = index + 1
            body: list[str] = []
            while cursor < len(lines) and not lines[cursor].strip().startswith(("### ", "#### ")):
                body.append(lines[cursor])
                cursor += 1
            meaningful = [line for line in body if line.strip()]
            if not meaningful:
                index = cursor
                continue
            cleaned.append(lines[index])
            cleaned.extend(body)
            index = cursor
            continue
        cleaned.append(lines[index])
        index += 1
    return "\n".join(cleaned)


def _build_recommended_asset_reference_matches(recommended_assets: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    matches: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for asset in recommended_assets or []:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            continue
        for text in _collect_asset_reference_texts(asset):
            normalized = _normalize_asset_reference_label(text)
            if not normalized:
                continue
            key = (asset_id, normalized)
            if key in seen:
                continue
            seen.add(key)
            matches.append((normalized, asset))
    return matches


def _collect_asset_reference_texts(asset: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    semantic_summary = metadata.get("semantic_summary") if isinstance(metadata.get("semantic_summary"), dict) else {}
    for value in (
        asset.get("display_title"),
        asset.get("title"),
        asset.get("caption"),
        asset.get("heading_path"),
        metadata.get("display_title"),
        metadata.get("raw_title"),
        metadata.get("caption"),
        metadata.get("heading_path"),
        metadata.get("source_heading"),
        semantic_summary.get("title_hint"),
    ):
        if isinstance(value, str) and value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    return texts


def _normalize_asset_reference_label(text: str) -> str:
    return _normalize_asset_anchor_text(text)


def _find_recommended_asset_by_reference(
    asset_ref: str,
    asset_matches: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    normalized_ref = _normalize_asset_reference_label(asset_ref)
    if not normalized_ref:
        return None
    for normalized_asset, asset in asset_matches:
        if normalized_ref == normalized_asset:
            return asset
    for normalized_asset, asset in asset_matches:
        if len(normalized_ref) >= 4 and len(normalized_asset) >= 4 and (
            normalized_ref in normalized_asset or normalized_asset in normalized_ref
        ):
            return asset
    best_asset: dict[str, Any] | None = None
    best_score = 0.0
    for normalized_asset, asset in asset_matches:
        score = SequenceMatcher(None, normalized_ref, normalized_asset).ratio()
        if score > best_score:
            best_score = score
            best_asset = asset
    if best_score >= 0.86:
        return best_asset
    return None


def _augment_with_support_blocks(
    *,
    filtered: list[dict[str, Any]],
    reusable_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
    section: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not filtered:
        return []
    anchor = filtered[0]
    anchor_sample_id = str(anchor.get("sample_id") or anchor.get("source_doc_id") or "")
    try:
        anchor_chunk_index = int(anchor.get("chunk_index"))
    except (TypeError, ValueError):
        anchor_chunk_index = None
    related_types = {
        str(item).lower()
        for item in (
            {str(target_taxonomy.get("section_type") or "").lower()}
            | {str(item).lower() for item in related_section_types(target_taxonomy.get("section_type") or "")}
        )
        if item
    }
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    parameter_summary = _is_parameter_summary_section(section=section, target_taxonomy=target_taxonomy)
    target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic").lower()
    support_forms = {
        str(item).lower()
        for item in (
            target_taxonomy.get("support_content_forms")
            or support_content_forms(target_section_type)
        )
        if item
    }
    anchor_heading = " > ".join(str(item).strip() for item in (anchor.get("heading_path") or []) if str(item).strip())
    selected_signatures = {
        (str(item.get("sample_id") or item.get("source_doc_id") or ""), item.get("chunk_index"))
        for item in filtered
    }
    support_candidates: list[tuple[float, dict[str, Any]]] = []
    for block in reusable_blocks:
        signature = (str(block.get("sample_id") or block.get("source_doc_id") or ""), block.get("chunk_index"))
        if signature in selected_signatures:
            continue
        if anchor_sample_id and signature[0] != anchor_sample_id:
            continue
        metadata = block.get("metadata") or {}
        section_type = str(metadata.get("section_type") or "unknown").lower()
        equipment_type = str(metadata.get("equipment_type") or "generic").lower()
        content_form = str(metadata.get("content_form") or "narrative").lower()
        if content_form in {"formula", "page_furniture", "certificate"}:
            continue
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        original_content_text = str(block.get("content_md") or "")
        content_text = _strip_internal_reuse_summary_lines(original_content_text)
        if original_content_text.strip() and not content_text:
            continue
        if _heading_should_be_excluded_from_customer_reuse(heading_text):
            continue
        if heading_looks_like_document_title(heading_text):
            continue
        if _should_skip_reuse_scenario_noise(
            section=section or {},
            global_params={},
            target_section_type=target_section_type,
            candidate_section_type=section_type,
            heading_text=heading_text,
            content_text=content_text,
        ):
            continue
        if target_section_type == "main_circuit_scheme":
            focus_match, noise_match = _main_circuit_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form == "figure":
                continue
            if section_type in {"protection_interlock", "control_logic", "communication_interface"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "design_basis":
            focus_match, noise_match = _design_basis_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form in {"figure", "formula"}:
                continue
            if section_type in {"control_logic", "protection_interlock", "main_circuit_scheme", "service_support"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "vfd_spec":
            focus_match, noise_match = _vfd_spec_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form == "formula":
                continue
            if section_type in {"protection_interlock", "cabinet_layout", "service_support", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "motor_spec":
            focus_match, noise_match = _motor_interface_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if section_type in {"cabinet_layout", "supply_scope", "service_support"} and not focus_match:
                continue
            if section_type == "protection_interlock" and not focus_match:
                continue
            if content_form == "narrative" and not focus_match and section_type not in {"motor_spec", "protection_interlock"}:
                continue
            if content_form == "bom_table" and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "protection_interlock":
            focus_match, noise_match = _protection_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            focus_hits = _focus_token_hit_count(
                heading_text=heading_text,
                content_text=content_text,
                focus_tokens=PROTECTION_FOCUS_TOKENS,
            )
            if content_form in {"formula", "figure"}:
                continue
            if section_type in {"main_circuit_scheme", "transformer_spec"} and not focus_match:
                continue
            if section_type == "unknown" and focus_hits < 2:
                continue
            if content_form == "narrative" and focus_hits < 2 and section_type not in {"protection_interlock", "communication_interface", "control_logic"}:
                continue
            if focus_hits < 2 and any(token in content_text for token in ("同期", "同步", "励磁")):
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "control_logic":
            focus_match, noise_match = _control_logic_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form in {"formula", "bom_table"}:
                continue
            if section_type in {"cabinet_layout", "commissioning_acceptance", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif parameter_summary:
            focus_match, noise_match = _parameter_summary_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if content_form == "formula":
                continue
            if section_type in {"control_logic", "communication_interface", "protection_interlock"}:
                continue
            if noise_match and not focus_match:
                continue
        try:
            chunk_index = int(block.get("chunk_index"))
        except (TypeError, ValueError):
            chunk_index = None
        distance = abs(chunk_index - anchor_chunk_index) if chunk_index is not None and anchor_chunk_index is not None else 99
        if distance > 12:
            continue
        score = float(block.get("selection_score") or 0)
        bonus = 0.0
        if section_type in related_types:
            bonus += 0.18
        if target_section_type != "unknown" and section_type == target_section_type:
            bonus += 0.12
        if target_equipment_type != "generic" and equipment_type == target_equipment_type:
            bonus += 0.08
        elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
            bonus -= 0.12
        if support_forms and content_form in support_forms:
            bonus += 0.08
        if content_form in {"bom_table", "parameter_table"}:
            bonus += 0.12
        elif content_form == "narrative":
            bonus += 0.06
        if target_section_type in {"bom_or_supply_list", "supply_scope"}:
            focus_match, noise_match = _supply_scope_focus_flags(
                heading_text=heading_text,
                content_text=content_text,
            )
            if section_type == "service_support":
                continue
            if noise_match and not focus_match:
                continue
            if content_form == "narrative" and not any(
                token in heading_text.casefold()
                for token in ("供货", "范围", "清单", "设备", "备件", "spare", "scope", "remark", "备注")
            ):
                continue
        heading_bonus = heading_family_similarity(anchor_heading, heading_text)
        if heading_bonus:
            bonus += heading_bonus
        focus_adjustment, _ = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=heading_text,
        )
        bonus += focus_adjustment
        if distance <= 3:
            bonus += 0.1
        elif distance <= 6:
            bonus += 0.05
        support_score = score + bonus
        if support_score < 0.45:
            continue
        normalized_block = dict(block)
        normalized_block["content_md"] = content_text
        support_candidates.append((support_score, normalized_block))

    support_candidates.sort(key=lambda item: item[0], reverse=True)
    augmented = list(filtered)
    for _, block in support_candidates[:2]:
        augmented.append(block)
    return augmented


def _score_reuse_paragraph(
    *,
    paragraph: str,
    query_terms: list[str],
    target_taxonomy: dict[str, Any],
) -> float:
    terms = set(_tokenize_reuse_text(paragraph[:1200]))
    overlap = len(set(query_terms) & terms)
    score = overlap * 1.5
    text = paragraph.strip()
    score += min(len(text) / 900.0, 1.2)
    if "：" in text or ":" in text:
        score += 0.15
    if any(token in text for token in ("主回路", "接口", "联锁", "保护", "控制", "柜", "变频器", "电机")):
        score += 0.3
    preferred_forms = {
        str(item).lower()
        for item in (target_taxonomy.get("preferred_content_forms") or [])
        if item
    }
    if preferred_forms and "table" in preferred_forms and "|" in text:
        score += 0.35
    return score


def _derive_reuse_subheading(
    *,
    block: dict[str, Any],
    section_title: str,
    target_taxonomy: dict[str, Any] | None = None,
) -> str | None:
    heading_path = [str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()]
    metadata = block.get("metadata") or {}
    target_section_type = str((target_taxonomy or {}).get("section_type") or "").lower()
    template_label = _derive_template_subheading(
        target_section_type=target_section_type,
        metadata=metadata,
        heading_path=heading_path,
        section_title=section_title,
    )
    if template_label:
        return template_label
    if not heading_path:
        return None
    label = re.sub(r"^[一二三四五六七八九十0-9.\-、\s]+", "", heading_path[-1]).strip()
    if not label:
        return None
    if label in GENERIC_REUSE_HEADINGS:
        return None
    if label == section_title or label in section_title or section_title in label:
        return None
    return label


def _derive_template_subheading(
    *,
    target_section_type: str,
    metadata: dict[str, Any],
    heading_path: list[str],
    section_title: str,
) -> str | None:
    section_type = str(metadata.get("section_type") or "").lower()
    if target_section_type and section_type not in {"", "unknown", target_section_type} and section_type not in related_section_types(target_section_type):
        return None
    heading_text = " > ".join(heading_path)
    normalized_heading = heading_text.casefold()
    content_form = str(metadata.get("content_form") or "narrative").lower()
    template = SECTION_TEMPLATE_HEADINGS.get(target_section_type)
    if not template:
        return None
    for label, keywords in template.items():
        if any(keyword.casefold() in normalized_heading for keyword in keywords):
            if label == section_title:
                return None
            return label
    if content_form == "parameter_table":
        return "关键技术参数"
    if content_form == "bom_table" and target_section_type in {"main_circuit_scheme", "bom_or_supply_list", "supply_scope"}:
        return "设备选型与容量配置" if target_section_type == "main_circuit_scheme" else "主要设备及供货范围"
    if content_form == "interface_table" and target_section_type == "communication_interface":
        return "接口与信号清单"
    if target_section_type == "main_circuit_scheme":
        return "主回路结构与运行切换"
    if target_section_type == "communication_interface":
        return "通信架构与接口方式"
    return None


def _order_assembly_blocks(
    *,
    candidate_blocks: list[dict[str, Any]],
    target_taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidate_blocks:
        return []
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()

    def _rank(block: dict[str, Any]) -> tuple[int, float]:
        metadata = block.get("metadata") or {}
        content_form = str(metadata.get("content_form") or "narrative").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()).casefold()
        block_signal_text = f"{heading_text} {str(block.get('content_md') or '')[:900].casefold()}"
        selection_score = float(block.get("selection_score") or 0)
        priority = 5
        if target_section_type == "overall_solution":
            if content_form in {"formula", "parameter_table"} or any(
                token in block_signal_text for token in ("负载数据", "启动曲线", "load data", "start curve")
            ):
                priority = 4
            elif any(token in block_signal_text for token in ("变频软起系统单线图", "single line", "单线图", "主接线", "主回路", "拓扑")):
                priority = 0
            elif any(token in block_signal_text for token in ("启动和同步", "description of start", "同步过程", "synchronization")):
                priority = 1
            elif any(token in block_signal_text for token in ("lci 变频启动特性", "start-up characteristic", "启动特性")):
                priority = 2
            elif content_form == "narrative":
                priority = 3
        elif target_section_type == "main_circuit_scheme":
            if any(token in heading_text for token in ("主回路", "主接线", "一次接线", "旁路", "切换", "隔离")):
                priority = 0
            elif any(token in heading_text for token in ("选型", "配置", "容量", "器件")) or content_form == "bom_table":
                priority = 1
            elif any(token in heading_text for token in ("参数", "技术数据", "规格", "额定")) or content_form == "parameter_table":
                priority = 2
            elif any(token in heading_text for token in ("保护", "联锁", "闭锁", "报警")):
                priority = 3
        elif target_section_type == "communication_interface":
            if any(token in heading_text for token in ("通信", "通讯", "接口", "上位机", "dcs", "plc", "modbus", "profibus", "profinet", "rs485")):
                priority = 0
            elif content_form == "interface_table" or any(token in heading_text for token in ("点表", "信号", "ai", "ao", "di", "do")):
                priority = 1
            elif any(token in heading_text for token in ("联锁", "调试", "试验")):
                priority = 2
            elif any(token in heading_text for token in ("性能要求", "整体要求", "技术要求")):
                priority = 4
        elif target_section_type in {"bom_or_supply_list", "supply_scope"}:
            if content_form == "bom_table" or any(token in heading_text for token in ("清单", "供货", "设备")):
                priority = 0
            elif content_form == "parameter_table" or any(token in heading_text for token in ("参数", "规格", "配置")):
                priority = 1
        return (priority, -selection_score)

    return sorted(candidate_blocks, key=_rank)


def _append_unique_text(target: list[str], value: str) -> None:
    normalized = str(value or "").strip()
    if normalized and normalized not in target:
        target.append(normalized)


def _section_asset_signal_text(section: dict[str, Any]) -> str:
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or section.get("description") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or "").strip(),
        str(section.get("child_outline_text") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def _effective_section_evidence_types(section: dict[str, Any]) -> list[str]:
    raw_types = [str(item).strip().lower() for item in (section.get("expected_evidence_types") or []) if str(item).strip()]
    effective: list[str] = []
    for item in raw_types:
        _append_unique_text(effective, item)

    text = _section_asset_signal_text(section).casefold()
    target_taxonomy = infer_target_taxonomy(section)
    support_forms = {
        str(item).lower()
        for item in (target_taxonomy.get("support_content_forms") or [])
        if str(item).strip()
    }
    taxonomy_section = str(target_taxonomy.get("section_type") or "unknown").lower()
    explicit_figure = bool({"figure", "diagram"} & set(raw_types))
    table_hint = any(token in text for token in TABLE_ASSET_HINTS)
    figure_hint = any(token in text for token in FIGURE_ASSET_HINTS)
    formula_hint = any(token in text for token in FORMULA_ASSET_HINTS)
    prefer_table_only = table_hint and not figure_hint and not explicit_figure
    if taxonomy_section in {"vfd_spec", "starter_spec"} and bool(section.get("asset_required")):
        prefer_table_only = False

    if (
        {"table", "parameter"} & set(raw_types)
        or table_hint
        or (bool(section.get("parameter_sensitive")) and support_forms & {"parameter_table", "bom_table", "interface_table", "protection_table"})
    ):
        _append_unique_text(effective, "table")
        _append_unique_text(effective, "parameter")
    control_or_interface_with_parameters = (
        taxonomy_section in {"protection_interlock", "control_logic", "communication_interface"}
        and (bool({"table", "parameter"} & set(effective)) or bool(section.get("parameter_sensitive")))
    )
    if explicit_figure or figure_hint or control_or_interface_with_parameters or ("figure" in support_forms and not prefer_table_only):
        _append_unique_text(effective, "figure")
    if {"formula", "equation"} & set(raw_types) or formula_hint:
        _append_unique_text(effective, "formula")
    return effective


def _derive_section_asset_query_hints(section: dict[str, Any]) -> list[str]:
    effective_types = _effective_section_evidence_types(section)
    inferred_section = {**section, "expected_evidence_types": effective_types}
    target_taxonomy = infer_target_taxonomy(inferred_section)
    taxonomy_section = str(target_taxonomy.get("section_type") or "unknown").lower()
    hints: list[str] = []
    if _is_spare_parts_section(section=inferred_section, target_taxonomy=target_taxonomy):
        for item in ("备品备件清单", "随机备件表", "spare parts list"):
            _append_unique_text(hints, item)
    for item in SECTION_ASSET_QUERY_HINTS.get(taxonomy_section, ()):
        _append_unique_text(hints, item)
    if "figure" in effective_types:
        _append_unique_text(hints, "工程示意图")
    if "table" in effective_types or "parameter" in effective_types:
        _append_unique_text(hints, "技术参数表")
    return hints[:6]


def _build_asset_search_context(
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reusable_blocks = list(reusable_blocks or [])
    effective_types = _effective_section_evidence_types(section)
    anchor_document_names: list[str] = []
    anchor_heading_paths: list[str] = []
    anchor_sample_ids: list[str] = []
    anchor_source_section_ids: list[str] = []
    anchor_image_document_names: list[str] = []
    anchor_image_sample_ids: list[str] = []
    anchor_image_source_section_ids: list[str] = []
    for block in reusable_blocks[:3]:
        source_title = str(block.get("source_title") or "").strip()
        if source_title and source_title not in anchor_document_names:
            anchor_document_names.append(source_title)
        sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
        if sample_id and sample_id not in anchor_sample_ids:
            anchor_sample_ids.append(sample_id)
        source_section_id = str(block.get("source_section_id") or "").strip()
        if source_section_id and source_section_id not in anchor_source_section_ids:
            anchor_source_section_ids.append(source_section_id)
        heading_text = " > ".join(
            str(item).strip()
            for item in (block.get("heading_path") or [])
            if str(item).strip()
        )
        if heading_text and heading_text not in anchor_heading_paths:
            anchor_heading_paths.append(heading_text)
        if _block_has_asset_reference(block):
            if source_title and source_title not in anchor_image_document_names:
                anchor_image_document_names.append(source_title)
            if sample_id and sample_id not in anchor_image_sample_ids:
                anchor_image_sample_ids.append(sample_id)
            if source_section_id and source_section_id not in anchor_image_source_section_ids:
                anchor_image_source_section_ids.append(source_section_id)
    keywords: list[str] = []
    for item in section.get("keywords") or []:
        _append_unique_text(keywords, str(item))
    for item in _derive_section_asset_query_hints(section):
        _append_unique_text(keywords, item)
    return {
        "section_title": str(section.get("title") or ""),
        "purpose": str(section.get("purpose") or section.get("description") or ""),
        "section_class": str(section.get("section_class") or ""),
        "expected_evidence_types": effective_types,
        "keywords": keywords,
        "anchor_document_names": anchor_document_names,
        "anchor_heading_paths": anchor_heading_paths,
        "anchor_sample_ids": anchor_sample_ids,
        "anchor_source_section_ids": anchor_source_section_ids,
        "anchor_image_document_names": anchor_image_document_names,
        "anchor_image_sample_ids": anchor_image_sample_ids,
        "anchor_image_source_section_ids": anchor_image_source_section_ids,
    }


def section_outline_to_executor_payload(section: dict[str, Any]) -> dict[str, Any]:
    title = clean_customer_facing_section_title(str(section.get("title") or ""))
    return {
        "title": title,
        "description": section.get("purpose", ""),
        "keywords": [title, *[str(item) for item in section.get("expected_evidence_types") or []]],
        "section_class": section.get("section_class"),
        "reuse_level": section.get("reuse_level"),
        "generation_mode": section.get("generation_mode"),
        "asset_required": bool(section.get("asset_required")),
    }


def build_section_global_params(requirement_content: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(requirement_content, dict):
        return {}

    merged: dict[str, Any] = {}
    key_parameters = requirement_content.get("key_parameters")
    if isinstance(key_parameters, dict):
        merged.update(key_parameters)

    for field_name in ("project_name", "industry", "product_line", "business_objective"):
        value = requirement_content.get(field_name)
        if value not in (None, "", [], {}):
            merged.setdefault(field_name, value)
    source_excerpt = str(requirement_content.get("source_excerpt") or "").strip()
    if source_excerpt:
        merged["_source_excerpt"] = source_excerpt
    return merged


def build_section_asset_query(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    effective_types = _effective_section_evidence_types(section)
    query_hints = _derive_section_asset_query_hints(section)
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
        " ".join(effective_types),
        " ".join(query_hints),
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_reuse_query_intents(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    extra_terms: list[str] | None = None,
) -> dict[str, Any]:
    title_parts = [
        str(section.get("title") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
    ]
    detail_parts = [
        str(section.get("purpose") or "").strip(),
        " ".join(str(item) for item in (section.get("expected_evidence_types") or []) if item),
        str(section.get("section_class") or "").strip(),
        " ".join(str(item).strip() for item in (extra_terms or []) if str(item).strip()),
    ]
    context_parts = [
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("industry") or "").strip(),
    ]
    title_text = " ".join(part for part in title_parts if part).strip()
    detail_text = " ".join(part for part in detail_parts if part).strip()
    context_text = " ".join(part for part in context_parts if part).strip()
    return {
        "title_text": title_text,
        "detail_text": detail_text,
        "context_text": context_text,
        "title_terms": _tokenize_reuse_text(title_text),
        "detail_terms": _tokenize_reuse_text(detail_text),
        "context_terms": _tokenize_reuse_text(context_text),
        "knowledge_terms": _tokenize_reuse_text(" ".join(str(item).strip() for item in (extra_terms or []) if str(item).strip())),
    }


def build_section_reuse_query(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    extra_terms: list[str] | None = None,
) -> str:
    intents = build_section_reuse_query_intents(
        section=section,
        global_params=global_params,
        extra_terms=extra_terms,
    )
    parts = [
        intents["title_text"],
        intents["detail_text"],
        intents["context_text"],
    ]
    return " ".join(part for part in parts if part).strip()


def build_section_asset_types(section: dict[str, Any]) -> list[str] | None:
    target_taxonomy = infer_target_taxonomy(section)
    if _is_spare_parts_section(section=section, target_taxonomy=target_taxonomy):
        return ["table"]
    expected_types = set(_effective_section_evidence_types(section))
    asset_types: list[str] = []
    if {"table", "parameter"} & expected_types:
        asset_types.append("table")
    if {"figure", "diagram"} & expected_types:
        asset_types.append("figure")
    if {"formula", "equation"} & expected_types:
        asset_types.append("formula_candidate")
    return asset_types or None


def _section_needs_figure_asset(section: dict[str, Any]) -> bool:
    return "figure" in set(build_section_asset_types(section) or [])


def _section_is_figure_dominant(section: dict[str, Any], *, target_taxonomy: dict[str, Any] | None = None) -> bool:
    if not _section_needs_figure_asset(section):
        return False
    taxonomy = target_taxonomy or infer_target_taxonomy(section)
    content_form = str(taxonomy.get("content_form") or "").lower()
    if content_form == "figure":
        return True
    text = _section_asset_signal_text(section).casefold()
    if any(token in text for token in FIGURE_ASSET_HINTS):
        table_hits = sum(1 for token in TABLE_ASSET_HINTS if token in text)
        figure_hits = sum(1 for token in FIGURE_ASSET_HINTS if token in text)
        return figure_hits >= table_hits
    return str(taxonomy.get("section_type") or "").lower() in {"overall_solution", "main_circuit_scheme"}


def _asset_context_has_anchors(context: dict[str, Any]) -> bool:
    return any(
        bool(context.get(key))
        for key in (
            "anchor_document_names",
            "anchor_heading_paths",
            "anchor_sample_ids",
            "anchor_source_section_ids",
            "anchor_image_document_names",
            "anchor_image_sample_ids",
            "anchor_image_source_section_ids",
        )
    )


def _relax_asset_search_context(context: dict[str, Any]) -> dict[str, Any]:
    relaxed = dict(context)
    for key in (
        "anchor_document_names",
        "anchor_heading_paths",
        "anchor_sample_ids",
        "anchor_source_section_ids",
        "anchor_image_document_names",
        "anchor_image_sample_ids",
        "anchor_image_source_section_ids",
    ):
        relaxed[key] = []
    return relaxed


def _merge_recommended_assets(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for asset in group:
            signature = str(
                asset.get("asset_id")
                or f"{asset.get('document_name')}|{asset.get('heading_path')}|{asset.get('title')}"
            )
            if signature in seen:
                continue
            seen.add(signature)
            merged.append(asset)
    return merged


def _should_skip_optional_asset_search(
    *,
    section: dict[str, Any],
    target_taxonomy: dict[str, Any],
    asset_types: list[str] | None,
) -> bool:
    if bool(section.get("asset_required")):
        return False
    raw_expected_types = {
        str(item).strip().lower()
        for item in (section.get("expected_evidence_types") or [])
        if str(item).strip()
    }
    if raw_expected_types & {"figure", "diagram", "table", "parameter", "formula", "equation"}:
        return False
    if bool(section.get("parameter_sensitive")):
        return False
    return True


def _select_evidence_items(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any] | None = None,
    limit: int,
    preferred_evidence_ids: set[str] | None = None,
    extra_query_terms: list[str] | None = None,
    knowledge_retrieval_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    expected_types = {str(item) for item in (section.get("expected_evidence_types") or [])}
    results = (evidence_bundle.content or {}).get("results") or []
    query_terms = _build_reuse_query_terms(
        section=section,
        global_params=global_params or {},
        extra_terms=extra_query_terms,
    )
    target_taxonomy = infer_target_taxonomy(section)

    candidates: list[tuple[float, dict[str, Any]]] = []
    fallback_candidates: list[tuple[float, dict[str, Any]]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if _is_case_fallback_evidence_item(result):
            continue
        raw_content = str(result.get("raw_content") or result.get("summary") or "")
        heading_path = result.get("heading_path") or []
        heading_text = " > ".join(str(segment).strip() for segment in heading_path if str(segment).strip())
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        candidate_section_type = str(metadata.get("section_type") or result.get("section_type") or "unknown").lower()
        if _should_skip_reuse_scenario_noise(
            section=section,
            global_params=global_params or {},
            target_section_type=str(target_taxonomy.get("section_type") or "unknown").lower(),
            candidate_section_type=candidate_section_type,
            heading_text=heading_text,
            content_text=raw_content,
        ):
            continue
        score, _, _ = _score_reuse_candidate(
            section=section,
            item=result,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
        result_type = str(result.get("type") or "")
        target_pool = candidates if (not expected_types or result_type in expected_types) else fallback_candidates
        target_pool.append((score, result))

    ranked = candidates or fallback_candidates
    ranked.sort(
        key=lambda item: (
            float(item[0]),
            float(item[1].get("reusability_score") or item[1].get("relevance_score") or 0),
        ),
        reverse=True,
    )
    prioritized = [item for _, item in ranked]
    if preferred_evidence_ids:
        prioritized.sort(
            key=lambda item: (
                not _matches_preferred_citation(item, preferred_evidence_ids),
                -float(item.get("reusability_score") or item.get("relevance_score") or 0),
            )
        )
    return prioritized[:limit]


def _is_case_fallback_evidence_item(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        str(item.get("type") or "").lower() == "case_summary"
        or str(item.get("source_chunk_type") or "").upper() == "CASE_SUMMARY"
        or str(metadata.get("fallback_source") or "").lower() == "case_library"
    )


def _build_citation_excerpt(text: str, *, limit: int = 220) -> str:
    normalized = _normalize_reuse_block_body(text)
    normalized = re.sub(r"<!--.*?-->", " ", normalized, flags=re.DOTALL)
    normalized = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _normalize_preferred_citation_ids(preferred_citation_ids: list[str] | None) -> set[str]:
    return {
        str(item).strip()
        for item in (preferred_citation_ids or [])
        if str(item).strip()
    }


def _citation_identity_values(item: dict[str, Any]) -> set[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    values = {
        str(item.get("evidence_id") or "").strip(),
        str(item.get("block_id") or "").strip(),
        str(item.get("source_doc_id") or "").strip(),
        str(item.get("sample_id") or "").strip(),
        str(item.get("source_title") or "").strip(),
        str(metadata.get("sample_id") or "").strip(),
        str(metadata.get("document_name") or "").strip(),
    }
    return {value for value in values if value}


def _matches_preferred_citation(item: dict[str, Any], preferred_citation_ids: set[str]) -> bool:
    if not preferred_citation_ids:
        return False
    return bool(_citation_identity_values(item) & preferred_citation_ids)


def prioritize_reusable_blocks_for_citations(
    reusable_blocks: list[dict[str, Any]],
    *,
    preferred_citation_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    preferred_ids = _normalize_preferred_citation_ids(preferred_citation_ids)
    if not reusable_blocks or not preferred_ids:
        return reusable_blocks

    primary: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    remainder: list[dict[str, Any]] = []
    anchor_sources: set[str] = set()

    for block in reusable_blocks:
        if _matches_preferred_citation(block, preferred_ids):
            primary.append(block)
            anchor_sources.update(_citation_identity_values(block))

    if not primary:
        return reusable_blocks

    primary_ids = {id(block) for block in primary}
    for block in reusable_blocks:
        if id(block) in primary_ids:
            continue
        if _citation_identity_values(block) & anchor_sources:
            secondary.append(block)
        else:
            remainder.append(block)
    return [*primary, *secondary, *remainder]


def _collect_replace_fields(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    lowered = raw_content.lower()
    replace_fields = [field for field in STANDARD_REPLACE_FIELDS if field in global_params and global_params.get(field)]
    if any(token in raw_content for token in ["买方", "卖方", "客户", "项目名称"]):
        replace_fields.extend(["customer_name", "buyer_name", "seller_name", "project_name"])
    if any(token in raw_content for token in ["110kV", "35kV", "6kV", "10kV", "电压"]):
        replace_fields.append("voltage_level")
    if any(token in lowered for token in ["kw", "mw", "mva", "数量", "台"]):
        replace_fields.extend(["power_rating", "quantity"])
    deduped: list[str] = []
    for field in replace_fields:
        if field not in deduped:
            deduped.append(field)
    return deduped


def _extract_banned_terms(*, raw_content: str, global_params: dict[str, Any]) -> list[str]:
    current_project_name = str(global_params.get("project_name") or "").strip()
    terms: list[str] = []
    for pattern in BANNED_TERM_PATTERNS:
        for match in pattern.finditer(raw_content):
            candidate = re.sub(r"\s+", " ", str(match.group(1)).strip())
            candidate = candidate.strip(" :：;；,.，。()（）[]【】")
            if len(candidate) < 3:
                continue
            if current_project_name and candidate == current_project_name:
                continue
            if candidate not in terms:
                terms.append(candidate)
    return terms


def _build_required_asset_placeholders(
    recommended_assets: list[dict[str, Any]],
    *,
    asset_required: bool,
    target_taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not asset_required:
        return []
    target_section_type = str((target_taxonomy or {}).get("section_type") or "").lower()
    placeholders: list[dict[str, Any]] = []
    for asset in recommended_assets[:3]:
        asset_type = str(asset.get("asset_type") or "").lower()
        if asset_type not in {"figure", "table", "formula_candidate"}:
            continue
        if asset_type == "table" and target_section_type in TABLE_PLACEHOLDER_REFERENCE_ONLY_SECTION_TYPES:
            # Table-heavy chapters should materialize tables into Markdown or use them as references.
            # Leaving raw TABLE placeholders in customer draft blocks export as unconfirmed assets.
            continue
        asset_id = asset.get("asset_id")
        if not asset_id:
            continue
        normalized_type = "FORMULA" if asset_type == "formula_candidate" else asset_type.upper()
        placeholders.append(
            {
                "placeholder": f"[[ASSET:{normalized_type}:{asset_id}]]",
                "title": asset.get("display_title") or asset.get("title") or asset.get("caption") or "参考资产",
                "asset_type": asset_type,
            }
        )
    return placeholders


def _block_has_asset_reference(block: dict[str, Any]) -> bool:
    text = str(block.get("content_md") or "")
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    return bool(
        "<!-- image" in text.lower()
        or "[[ASSET:" in text
        or metadata.get("needs_asset_lookup")
    )


def _build_missing_asset_diagnostics(
    *,
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if recommended_assets:
        return []
    diagnostics: list[dict[str, Any]] = []
    for block in reusable_blocks[:3]:
        if not _block_has_asset_reference(block):
            continue
        diagnostics.append(
            {
                "code": "source_section_asset_missing",
                "source_title": block.get("source_title"),
                "sample_id": block.get("sample_id") or block.get("source_doc_id"),
                "source_section_id": block.get("source_section_id"),
                "section_path": block.get("section_path"),
                "heading_path": block.get("heading_path") or [],
                "message": "复用块包含图片引用，但同源章节未找到可展示 figure asset。",
            }
        )
    return diagnostics


def build_reusable_blocks(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    case_library_matches: list[dict[str, Any]] | None = None,
    limit: int = DEFAULT_REUSE_LIMIT,
    extra_query_terms: list[str] | None = None,
    knowledge_retrieval_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    blocks = _build_evidence_reusable_blocks(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=limit,
        extra_query_terms=extra_query_terms,
        knowledge_retrieval_bundle=knowledge_retrieval_bundle,
    )
    blocks.extend(
        _build_case_library_reusable_blocks(
            section=section,
            global_params=global_params,
            case_library_matches=case_library_matches or [],
            limit=limit,
            extra_query_terms=extra_query_terms,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
    )
    deduped = _dedupe_reusable_blocks(blocks)
    deduped.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    return deduped[:limit]


def _build_evidence_reusable_blocks(
    *,
    section: dict[str, Any],
    evidence_bundle: EvidenceBundle,
    global_params: dict[str, Any],
    limit: int,
    extra_query_terms: list[str] | None = None,
    knowledge_retrieval_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    selected = _select_evidence_items(
        section=section,
        evidence_bundle=evidence_bundle,
        global_params=global_params,
        limit=max(limit * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
        extra_query_terms=extra_query_terms,
        knowledge_retrieval_bundle=knowledge_retrieval_bundle,
    )
    blocks: list[dict[str, Any]] = []
    query_terms = _build_reuse_query_terms(
        section=section,
        global_params=global_params,
        extra_terms=extra_query_terms,
    )
    for item in selected:
        raw_content = _strip_internal_reuse_summary_lines(str(item.get("raw_content") or item.get("summary") or ""))
        if not raw_content:
            continue
        heading_path = item.get("heading_path") or []
        heading_text = " > ".join(str(segment).strip() for segment in heading_path if str(segment).strip())
        block_type = str(item.get("source_chunk_type") or item.get("type") or "section").lower()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        candidate_section_type = str(metadata.get("section_type") or item.get("section_type") or "unknown").lower()
        if _should_skip_reuse_scenario_noise(
            section=section,
            global_params=global_params,
            target_section_type=target_section_type,
            candidate_section_type=candidate_section_type,
            heading_text=heading_text,
            content_text=raw_content,
        ):
            continue
        selection_score, selection_reasons, selection_score_breakdown = _score_reuse_candidate(
            section=section,
            item=item,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
        blocks.append(
            {
                "block_id": str(item.get("evidence_id") or item.get("source_chunk_id") or ""),
                "sample_id": metadata.get("sample_id"),
                "source_doc_id": item.get("source_doc_id"),
                "source_title": item.get("source_title"),
                "source_section_id": item.get("source_section_id") or metadata.get("source_section_id"),
                "section_path": " > ".join(str(segment) for segment in heading_path if segment) if heading_path else "",
                "source_heading": heading_path[-1] if heading_path else "",
                "heading_path": heading_path,
                "content_md": raw_content,
                "block_type": block_type,
                "reusability_score": float(item.get("reusability_score") or item.get("relevance_score") or 0),
                "retrieval_reason": str(item.get("retrieval_reason") or item.get("reason") or item.get("recommended_use") or ""),
                "retrieval_reason_trace": (
                    [str(reason) for reason in (item.get("reason_trace") or []) if str(reason or "").strip()]
                    or ([str(item.get("recommended_use") or "")] if str(item.get("recommended_use") or "").strip() else [])
                ),
                "retrieval_score_breakdown": _extract_retrieval_score_breakdown(item),
                "selection_score": selection_score,
                "selection_reasons": selection_reasons,
                "selection_score_breakdown": selection_score_breakdown,
                "customer_specificity_score": 0.8 if metadata.get("front_matter") else 0.25,
                "asset_dependency_level": "high" if metadata.get("needs_asset_lookup") else "low",
                "must_replace_fields": _collect_replace_fields(raw_content=raw_content, global_params=global_params),
                "banned_terms": _extract_banned_terms(raw_content=raw_content, global_params=global_params),
                "must_not_copy_spans": [],
                "metadata": metadata,
            }
        )
    blocks.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    return blocks[:limit]


def _build_case_library_reusable_blocks(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    case_library_matches: list[dict[str, Any]],
    limit: int,
    extra_query_terms: list[str] | None = None,
    knowledge_retrieval_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    query_terms = _build_reuse_query_terms(
        section=section,
        global_params=global_params,
        extra_terms=extra_query_terms,
    )
    blocks: list[dict[str, Any]] = []
    for item in case_library_matches[: max(limit * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES)]:
        raw_content = _strip_internal_reuse_summary_lines(str(item.get("content") or ""))
        if not raw_content:
            continue
        heading_path = _normalize_case_heading_path(item.get("heading_path"))
        heading_text = " > ".join(str(segment).strip() for segment in heading_path if str(segment).strip())
        metadata = {
            "front_matter": bool(item.get("front_matter")),
            "needs_asset_lookup": bool(item.get("needs_asset_lookup")),
            "section_type": item.get("section_type") or "unknown",
            "equipment_type": item.get("equipment_type") or "generic",
            "content_form": item.get("content_form") or "narrative",
            "sample_id": item.get("sample_id"),
            "chunk_index": item.get("chunk_index"),
            "subchunk_index": item.get("subchunk_index"),
            "page_no": item.get("page_no"),
            "source_section_id": item.get("source_section_id"),
            "section_path": item.get("section_path"),
            "source_heading": item.get("source_heading"),
            "document_name": item.get("file_name"),
        }
        if _should_skip_reuse_scenario_noise(
            section=section,
            global_params=global_params,
            target_section_type=target_section_type,
            candidate_section_type=str(metadata["section_type"] or "unknown"),
            heading_text=heading_text,
            content_text=raw_content,
        ):
            continue
        score_input = {
            "type": str(item.get("chunk_type") or "section").lower(),
            "source_chunk_type": item.get("chunk_type"),
            "reusability_score": float(item.get("score") or 0),
            "metadata": metadata,
            "section_type": metadata["section_type"],
            "equipment_type": metadata["equipment_type"],
            "content_form": metadata["content_form"],
        }
        selection_score, selection_reasons, selection_score_breakdown = _score_reuse_candidate(
            section=section,
            item=score_input,
            raw_content=raw_content,
            heading_path=heading_path,
            query_terms=query_terms,
            target_taxonomy=target_taxonomy,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
        chunk_index = item.get("chunk_index")
        subchunk_index = item.get("subchunk_index")
        block_index = str(chunk_index if chunk_index is not None else "")
        if subchunk_index is not None:
            block_index = f"{block_index}:{subchunk_index}" if block_index else str(subchunk_index)
        blocks.append(
            {
                "block_id": f"case:{item.get('sample_id')}:{block_index}",
                "sample_id": item.get("sample_id"),
                "chunk_index": item.get("chunk_index"),
                "subchunk_index": item.get("subchunk_index"),
                "page_no": item.get("page_no"),
                "source_doc_id": item.get("sample_id"),
                "source_title": item.get("file_name"),
                "source_section_id": item.get("source_section_id"),
                "section_path": item.get("section_path") or " > ".join(heading_path),
                "source_heading": item.get("source_heading") or (heading_path[-1] if heading_path else ""),
                "normalized_heading": item.get("normalized_heading"),
                "heading_path": heading_path,
                "content_md": raw_content,
                "block_type": str(item.get("chunk_type") or "section").lower(),
                "reusability_score": float(item.get("score") or 0),
                "retrieval_reason": str(item.get("reason") or ""),
                "retrieval_reason_trace": [str(reason) for reason in (item.get("reason_trace") or []) if str(reason or "").strip()],
                "retrieval_score_breakdown": _extract_retrieval_score_breakdown(item),
                "selection_score": selection_score,
                "selection_reasons": selection_reasons,
                "selection_score_breakdown": selection_score_breakdown,
                "customer_specificity_score": 0.8 if metadata.get("front_matter") else 0.25,
                "asset_dependency_level": "high" if metadata.get("needs_asset_lookup") else "low",
                "must_replace_fields": _collect_replace_fields(raw_content=raw_content, global_params=global_params),
                "banned_terms": _extract_banned_terms(raw_content=raw_content, global_params=global_params),
                "must_not_copy_spans": [],
                "metadata": metadata,
            }
        )
    return blocks


def _normalize_case_heading_path(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [segment.strip() for segment in str(value or "").split(">") if segment.strip()]


def _reuse_block_signature(block: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    return (
        str(block.get("source_title") or ""),
        tuple(str(item) for item in (block.get("heading_path") or [])),
        str(block.get("content_md") or "")[:160],
    )


def _dedupe_reusable_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
    for block in blocks:
        signature = _reuse_block_signature(block)
        current = deduped.get(signature)
        if current is None or float(block.get("selection_score") or 0) > float(current.get("selection_score") or 0):
            deduped[signature] = block
    return list(deduped.values())


def build_reuse_pack(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
    asset_candidates: list[dict[str, Any]] | None = None,
    parameter_evidence_candidates: list[dict[str, Any]] | None = None,
    retrieval_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_taxonomy = infer_target_taxonomy(section)
    serializable_target_taxonomy = _json_safe_value(target_taxonomy)
    risk_flags: list[str] = []
    if bool(section.get("parameter_sensitive")):
        risk_flags.append("parameter_sensitive")
    if bool(section.get("asset_required")):
        risk_flags.append("asset_required")
    if bool(section.get("needs_human_review")):
        risk_flags.append("human_review_required")
    must_replace_fields: list[str] = []
    banned_terms: list[str] = []
    for block in reusable_blocks:
        for field_name in block.get("must_replace_fields") or []:
            if field_name not in must_replace_fields:
                must_replace_fields.append(field_name)
        for term in block.get("banned_terms") or []:
            if term not in banned_terms:
                banned_terms.append(term)
    required_asset_placeholders = _build_required_asset_placeholders(
        recommended_assets,
        asset_required=bool(section.get("asset_required")),
        target_taxonomy=target_taxonomy,
    )
    missing_asset_diagnostics = _build_missing_asset_diagnostics(
        reusable_blocks=reusable_blocks,
        recommended_assets=recommended_assets,
    )

    return {
        "section_title": str(section.get("title") or ""),
        "section_purpose": str(section.get("purpose") or ""),
        "generation_mode": str(section.get("generation_mode") or "baseline"),
        "reuse_level": str(section.get("reuse_level") or "medium"),
        "target_taxonomy": serializable_target_taxonomy,
        "reusable_blocks": reusable_blocks,
        "recommended_assets": recommended_assets,
        "asset_candidates": list(asset_candidates or []),
        "must_replace_fields": must_replace_fields,
        "banned_terms": banned_terms,
        "replacement_hints": {
            field_name: global_params.get(field_name)
            for field_name in must_replace_fields
            if global_params.get(field_name) not in (None, "", [], {})
        },
        "required_asset_placeholders": required_asset_placeholders,
        "missing_asset_diagnostics": missing_asset_diagnostics,
        "parameter_candidates": {
            "project": {
                key: value
                for key, value in global_params.items()
                if not key.startswith("_")
                and key in {"project_name", "product_line", "industry", "business_objective", "voltage_level", "power_rating", "quantity"}
            },
            "evidence": list(parameter_evidence_candidates or []),
        },
        "do_not_reuse_signals": [
            "customer_specific_fields",
            "outdated_schedule",
            "unconfirmed_parameters",
        ],
        "risk_flags": risk_flags,
        "retrieval_trace": retrieval_trace or {},
    }


def _merge_evidence_judge_trace(retrieval_trace: dict[str, Any] | None, evidence_judge_trace: dict[str, Any]) -> dict[str, Any]:
    merged = dict(retrieval_trace or {})
    if evidence_judge_trace:
        merged["evidence_judge"] = evidence_judge_trace
    return merged


def _reuse_prompt_block_limit(section: dict[str, Any]) -> int:
    if bool(section.get("child_aware_retrieval")):
        return CHILD_AWARE_REUSE_PROMPT_LIMIT
    return DEFAULT_REUSE_LIMIT


def _should_use_child_aware_retrieval(section: dict[str, Any]) -> bool:
    children = section.get("children") if isinstance(section.get("children"), list) else []
    return bool(children) and str(section.get("generation_unit") or "").lower() == "top_level"


def _retrieval_section_label(section: dict[str, Any]) -> str:
    title = str(section.get("title") or "").strip()
    section_id = str(section.get("section_id") or "").strip()
    if section_id and title and not title.startswith(section_id):
        return f"{section_id} {title}".strip()
    return title or section_id or "未命名小节"


def _strip_retrieval_heading_number(title: str) -> str:
    stripped = SECTION_TITLE_PREFIX_PATTERN.sub("", str(title or "")).strip()
    return stripped or str(title or "").strip()


def _sanitize_retrieval_keywords(keywords: Any) -> list[str]:
    sanitized: list[str] = []
    for item in keywords if isinstance(keywords, (list, tuple, set)) else []:
        text = _strip_retrieval_heading_number(str(item or ""))
        if not text or re.fullmatch(r"\d+(?:\.\d+)*", text):
            continue
        if text not in sanitized:
            sanitized.append(text)
    return sanitized


def _build_child_retrieval_sections(section: dict[str, Any]) -> list[dict[str, Any]]:
    if not _should_use_child_aware_retrieval(section):
        return [section]

    children = section.get("children") if isinstance(section.get("children"), list) else []
    parent_id = str(section.get("section_id") or "").strip()
    parent_title = str(section.get("title") or parent_id or "").strip()
    inherited_expected_types = list(section.get("expected_evidence_types") or [])
    inherited_generation_mode = str(section.get("generation_mode") or "baseline")
    inherited_section_class = str(section.get("section_class") or "custom")
    retrieval_sections: list[dict[str, Any]] = []

    def _walk(nodes: list[dict[str, Any]], ancestors: list[str]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or node.get("section_id") or "").strip()
            node_children = node.get("children") if isinstance(node.get("children"), list) else []
            next_ancestors = [*ancestors, title] if title else list(ancestors)
            if node_children:
                _walk(node_children, next_ancestors)
                continue

            current = dict(node)
            current["children"] = []
            semantic_title = _strip_retrieval_heading_number(title)
            if semantic_title:
                current["title"] = semantic_title
            current["retrieval_unit"] = "child_section"
            current["retrieval_parent_section_id"] = parent_id
            current["retrieval_parent_title"] = parent_title
            current["retrieval_heading_path"] = [item for item in [parent_title, *ancestors, title] if item]
            current["child_aware_retrieval_subsection"] = True
            if not current.get("generation_mode"):
                current["generation_mode"] = inherited_generation_mode
            if not current.get("section_class"):
                current["section_class"] = inherited_section_class
            if not current.get("expected_evidence_types") and inherited_expected_types:
                current["expected_evidence_types"] = inherited_expected_types
            current["asset_required"] = bool(current.get("asset_required")) or bool(section.get("asset_required"))
            current["parameter_sensitive"] = bool(current.get("parameter_sensitive")) or bool(section.get("parameter_sensitive"))
            current["needs_human_review"] = bool(current.get("needs_human_review")) or bool(section.get("needs_human_review"))
            current["keywords"] = _merge_string_lists(
                _sanitize_retrieval_keywords(current.get("keywords") or []),
                semantic_title,
            )
            purpose = str(current.get("purpose") or current.get("description") or "").strip()
            path_hint = " > ".join(item for item in [parent_title, *ancestors] if item)
            hint = f"所属大章节：{path_hint}" if path_hint else f"所属大章节：{parent_title}"
            current["purpose"] = f"{purpose}\n{hint}".strip() if purpose else hint
            retrieval_sections.append(current)

    _walk(children, [])
    return retrieval_sections or [section]


def _annotate_retrieval_subsection_block(block: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    label = _retrieval_section_label(section)
    current = dict(block)
    current["retrieval_subsection_id"] = str(section.get("section_id") or "")
    current["retrieval_subsection_title"] = label
    metadata = dict(current.get("metadata") if isinstance(current.get("metadata"), dict) else {})
    metadata["retrieval_subsection_id"] = current["retrieval_subsection_id"]
    metadata["retrieval_subsection_title"] = label
    current["metadata"] = metadata
    reasons = [str(item) for item in (current.get("selection_reasons") or []) if str(item).strip()]
    marker = f"child_section:{label}"
    if marker not in reasons:
        reasons.append(marker)
    current["selection_reasons"] = reasons
    return current


def _annotate_retrieval_subsection_asset(asset: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    label = _retrieval_section_label(section)
    current = dict(asset)
    current["retrieval_subsection_id"] = str(section.get("section_id") or "")
    current["retrieval_subsection_title"] = label
    metadata = dict(current.get("metadata") if isinstance(current.get("metadata"), dict) else {})
    metadata["retrieval_subsection_id"] = current["retrieval_subsection_id"]
    metadata["retrieval_subsection_title"] = label
    current["metadata"] = metadata
    reason_trace = [str(item) for item in (current.get("reason_trace") or []) if str(item).strip()]
    marker = f"child_section:{label}"
    if marker not in reason_trace:
        reason_trace.append(marker)
    current["reason_trace"] = reason_trace
    return current


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, tuple[str, ...], str], dict[str, Any]] = {}
    for citation in citations:
        heading_path = tuple(str(item) for item in (citation.get("heading_path") or []) if str(item).strip())
        signature = (
            str(citation.get("evidence_id") or ""),
            str(citation.get("source_doc_id") or citation.get("source_title") or ""),
            heading_path,
            str(citation.get("excerpt") or "")[:120],
        )
        current = deduped.get(signature)
        if current is None or float(citation.get("relevance_score") or 0) > float(current.get("relevance_score") or 0):
            deduped[signature] = citation
    return list(deduped.values())


def _merge_child_contexts(child_results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for result in child_results:
        subsection = result.get("retrieval_section") if isinstance(result.get("retrieval_section"), dict) else {}
        label = _retrieval_section_label(subsection)
        context = str(result.get("context") or "").strip()
        if not context:
            continue
        for line in context.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            lines.append(f"【{label}】 {normalized}")
            if len(lines) >= CHILD_AWARE_CONTEXT_MAX_LINES:
                return "\n".join(lines)
    return "\n".join(lines)


def _merge_child_reusable_blocks(child_results: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: list[list[dict[str, Any]]] = []
    for result in child_results:
        subsection = result.get("retrieval_section") if isinstance(result.get("retrieval_section"), dict) else {}
        blocks = [
            _annotate_retrieval_subsection_block(block, subsection)
            for block in (result.get("reusable_blocks") or [])
            if isinstance(block, dict)
        ]
        blocks.sort(
            key=lambda item: (
                float(item.get("selection_score") or 0),
                float(item.get("reusability_score") or 0),
            ),
            reverse=True,
        )
        grouped.append(blocks)

    deduped_global = _dedupe_reusable_blocks([block for group in grouped for block in group])
    deduped_global.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()

    def _add(block: dict[str, Any]) -> None:
        signature = _reuse_block_signature(block)
        if signature in seen:
            return
        seen.add(signature)
        selected.append(block)

    for group in grouped:
        added_from_group = 0
        for block in group:
            before = len(selected)
            _add(block)
            if len(selected) > before:
                added_from_group += 1
            if added_from_group >= CHILD_AWARE_REUSE_BLOCKS_PER_SUBSECTION:
                break
            if len(selected) >= limit:
                return selected

    for block in deduped_global:
        _add(block)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _merge_child_case_retrieval_trace(child_results: list[dict[str, Any]]) -> dict[str, Any]:
    section_candidates: list[dict[str, Any]] = []
    scoped_sections: list[dict[str, Any]] = []
    knowledge_terms: list[str] = []
    product_cards: list[str] = []
    module_cards: list[str] = []
    source_documents: list[str] = []
    resolved_sample_ids: list[str] = []
    subsection_retrieval: list[dict[str, Any]] = []
    queries: list[str] = []

    for result in child_results:
        subsection = result.get("retrieval_section") if isinstance(result.get("retrieval_section"), dict) else {}
        trace = result.get("case_trace") if isinstance(result.get("case_trace"), dict) else {}
        query = str(trace.get("query") or "").strip()
        if query:
            queries.append(query)
        child_section_candidates = [item for item in (trace.get("section_candidates") or []) if isinstance(item, dict)]
        child_scoped_sections = [item for item in (trace.get("scoped_sections") or []) if isinstance(item, dict)]
        section_candidates.extend(child_section_candidates)
        scoped_sections.extend(child_scoped_sections)
        knowledge_terms = _merge_string_lists(knowledge_terms, trace.get("knowledge_wiki_terms") or [])
        product_cards = _merge_string_lists(product_cards, trace.get("knowledge_wiki_product_cards") or [])
        module_cards = _merge_string_lists(module_cards, trace.get("knowledge_wiki_module_cards") or [])
        source_documents = _merge_string_lists(source_documents, trace.get("knowledge_wiki_source_documents") or [])
        resolved_sample_ids = _merge_string_lists(resolved_sample_ids, trace.get("knowledge_wiki_resolved_sample_ids") or [])
        subsection_retrieval.append(
            {
                "section_id": str(subsection.get("section_id") or ""),
                "title": _retrieval_section_label(subsection),
                "query": query,
                "match_count": len(result.get("case_matches") or []),
                "reusable_block_count": len(result.get("reusable_blocks") or []),
                "section_candidates": child_section_candidates[:REUSE_TRACE_SECTION_LIMIT],
                "scoped_sections": child_scoped_sections[:REUSE_TRACE_SECTION_LIMIT],
            }
        )

    section_candidates = _dedupe_section_candidates_for_strategy(section_candidates)[: max(REUSE_TRACE_SECTION_LIMIT, 12)]
    scoped_sections = _dedupe_section_candidates_for_strategy(scoped_sections)[: max(REUSE_TRACE_SECTION_LIMIT, 12)]
    return {
        "child_aware": True,
        "query": " | ".join(queries[:6]),
        "query_intents": {"mode": "child_section_merged", "subsection_count": len(child_results)},
        "knowledge_wiki_terms": knowledge_terms,
        "knowledge_wiki_product_cards": product_cards,
        "knowledge_wiki_module_cards": module_cards,
        "knowledge_wiki_source_documents": source_documents,
        "knowledge_wiki_resolved_sample_ids": resolved_sample_ids,
        "section_candidates": section_candidates,
        "scoped_sections": scoped_sections,
        "subsection_retrieval": subsection_retrieval,
        "full_section_match_count": sum(
            int((result.get("case_trace") or {}).get("full_section_match_count") or 0)
            for result in child_results
            if isinstance(result.get("case_trace"), dict)
        ),
    }


def _merge_child_evidence_trace(citations: list[dict[str, Any]], child_results: list[dict[str, Any]]) -> dict[str, Any]:
    trace = _build_evidence_retrieval_trace(citations=citations)
    trace["child_aware"] = True
    trace["subsection_evidence"] = [
        {
            "section_id": str((result.get("retrieval_section") or {}).get("section_id") or ""),
            "title": _retrieval_section_label(result.get("retrieval_section") or {}),
            "selected_count": len(result.get("citations") or []),
            "selected_items": (result.get("evidence_trace") or {}).get("selected_items") or [],
        }
        for result in child_results
    ]
    return trace


def _merge_child_evidence_judge_traces(child_results: list[dict[str, Any]]) -> dict[str, Any]:
    traces = [
        {
            "section_id": str((result.get("retrieval_section") or {}).get("section_id") or ""),
            "title": _retrieval_section_label(result.get("retrieval_section") or {}),
            "trace": result.get("evidence_judge_trace") or {},
        }
        for result in child_results
    ]
    statuses = Counter(
        str(item["trace"].get("status") or "unknown")
        for item in traces
        if isinstance(item.get("trace"), dict)
    )
    return {
        "child_aware": True,
        "status": "merged",
        "status_counts": dict(statuses),
        "subsections": traces,
        "input_count": sum(
            int((item["trace"] if isinstance(item.get("trace"), dict) else {}).get("input_count") or 0)
            for item in traces
        ),
        "kept_count": sum(
            int((item["trace"] if isinstance(item.get("trace"), dict) else {}).get("kept_count") or 0)
            for item in traces
        ),
        "dropped_count": sum(
            int((item["trace"] if isinstance(item.get("trace"), dict) else {}).get("dropped_count") or 0)
            for item in traces
        ),
    }


def _merge_child_assets(
    child_results: list[dict[str, Any]],
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    recommended_groups: list[list[dict[str, Any]]] = []
    candidate_groups: list[list[dict[str, Any]]] = []
    subsection_assets: list[dict[str, Any]] = []
    for result in child_results:
        subsection = result.get("retrieval_section") if isinstance(result.get("retrieval_section"), dict) else {}
        recommended = [
            _annotate_retrieval_subsection_asset(asset, subsection)
            for asset in (result.get("recommended_assets") or [])
            if isinstance(asset, dict)
        ]
        candidates = [
            _annotate_retrieval_subsection_asset(asset, subsection)
            for asset in (result.get("asset_candidates") or [])
            if isinstance(asset, dict)
        ]
        recommended_groups.append(recommended)
        candidate_groups.append(candidates)
        asset_trace = result.get("asset_trace") if isinstance(result.get("asset_trace"), dict) else {}
        subsection_assets.append(
            {
                "section_id": str(subsection.get("section_id") or ""),
                "title": _retrieval_section_label(subsection),
                "selected_count": len(recommended),
                "candidate_count": len(candidates),
                "selected_assets": asset_trace.get("selected_assets") or [],
                "asset_candidates": asset_trace.get("asset_candidates") or [],
                "diagnostics": asset_trace.get("diagnostics") or {},
            }
        )

    recommended_assets = _merge_recommended_assets(*recommended_groups)
    recommended_assets = prioritize_recommended_assets(recommended_assets, limit=ASSET_RECOMMENDATION_LIMIT)
    recommended_assets = tighten_recommended_assets_for_reuse(
        recommended_assets,
        section=section,
        reusable_blocks=reusable_blocks,
        limit=ASSET_RECOMMENDATION_LIMIT,
    )
    asset_candidates = _merge_recommended_assets(*candidate_groups)
    asset_candidates = prioritize_recommended_assets(asset_candidates, limit=ASSET_CANDIDATE_LIMIT)
    asset_candidates = tighten_recommended_assets_for_reuse(
        asset_candidates,
        section=section,
        reusable_blocks=reusable_blocks,
        limit=ASSET_CANDIDATE_LIMIT,
    )
    asset_trace = _build_asset_retrieval_trace(
        query=build_section_asset_query(section=section, global_params=global_params),
        asset_types=build_section_asset_types(section),
        skipped_optional_search=False,
        recommended_assets=recommended_assets,
        asset_candidates=asset_candidates,
        search_trace={
            "child_aware": True,
            "subsection_assets": subsection_assets,
        },
        diagnostics={"child_aware": True, "subsection_count": len(child_results)},
    )
    asset_trace["child_aware"] = True
    return recommended_assets, asset_candidates, asset_trace


def _normalize_source_document_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().casefold())


def _collect_knowledge_wiki_source_documents(bundle: dict[str, Any]) -> set[str]:
    source_documents: set[str] = set()
    for card_key in ("product_cards", "module_cards"):
        cards = [card for card in (bundle.get(card_key) or []) if isinstance(card, dict)]
        if not cards:
            continue
        for item in cards[0].get("source_documents") or []:
            text = str(item or "").strip()
            if text:
                source_documents.add(text)
                return source_documents
    return source_documents


def _item_matches_source_document_prior(item: dict[str, Any], preferred_documents: set[str]) -> bool:
    normalized_preferred = {_normalize_source_document_name(doc) for doc in preferred_documents if str(doc or "").strip()}
    if not normalized_preferred:
        return False
    candidates = [
        item.get("file_name"),
        item.get("source_title"),
        item.get("document_name"),
    ]
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    candidates.extend([metadata.get("document_name"), metadata.get("file_name")])
    return any(_normalize_source_document_name(candidate) in normalized_preferred for candidate in candidates if candidate)


def _apply_knowledge_source_document_prior(
    items: list[dict[str, Any]],
    *,
    preferred_documents: set[str],
    boost: float = 0.24,
) -> list[dict[str, Any]]:
    if not items or not preferred_documents:
        return items
    boosted: list[dict[str, Any]] = []
    for item in items:
        current = dict(item)
        if not _item_matches_source_document_prior(current, preferred_documents):
            boosted.append(current)
            continue
        current["score"] = round(float(current.get("score") or 0) + boost, 4)
        reasons = [str(reason) for reason in (current.get("reason_trace") or []) if str(reason or "").strip()]
        if "knowledge_wiki_source_document_prior" not in reasons:
            reasons.append("knowledge_wiki_source_document_prior")
        current["reason_trace"] = reasons
        reason_text = str(current.get("reason") or "")
        if "knowledge_wiki_source_document_prior" not in reason_text:
            current["reason"] = "; ".join(part for part in [reason_text, "knowledge_wiki_source_document_prior"] if part)
        breakdown = dict(current.get("score_breakdown") if isinstance(current.get("score_breakdown"), dict) else {})
        breakdown["knowledge_wiki_source_document_prior"] = boost
        try:
            breakdown["final"] = float(current["score"])
        except (TypeError, ValueError):
            pass
        current["score_breakdown"] = breakdown
        boosted.append(current)
    boosted.sort(key=lambda candidate: float(candidate.get("score") or 0), reverse=True)
    return boosted


def _is_evidence_judge_target(*, section: dict[str, Any], reusable_blocks: list[dict[str, Any]], mode: str) -> tuple[bool, str]:
    normalized_mode = str(mode or "off").lower()
    if normalized_mode == "off":
        return False, "disabled"
    if str(section.get("generation_mode") or "baseline") != "reuse_first":
        return False, "not_reuse_first"
    if not reusable_blocks:
        return False, "no_reusable_blocks"

    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if normalized_mode == "strict":
        return True, "strict_mode"
    if target_section_type not in TECHNICAL_SCHEME_SECTION_TYPES:
        return False, "non_technical_section"
    if target_section_type == "overall_solution":
        return True, "overall_solution_risk"

    related_types = related_section_types(target_section_type)
    for block in reusable_blocks:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        candidate_section_type = str(metadata.get("section_type") or "unknown").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        content_text = str(block.get("content_md") or "")
        if candidate_section_type in TECHNICAL_REUSE_HARD_NOISE_SECTION_TYPES:
            return True, "hard_noise_section_type"
        if _text_contains_any_token(f"{heading_text}\n{content_text}", TECHNICAL_REUSE_HARD_NOISE_TOKENS):
            return True, "hard_noise_terms"
        if candidate_section_type not in {"unknown", target_section_type, *related_types}:
            return True, "cross_section_candidates"
    return len(reusable_blocks) > DEFAULT_REUSE_LIMIT, "large_candidate_set" if len(reusable_blocks) > DEFAULT_REUSE_LIMIT else "low_risk"


def _deterministic_prefilter_evidence_judge_candidates(
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not reusable_blocks:
        return reusable_blocks, {
            "status": "skipped",
            "reason": "no_reusable_blocks",
            "input_count": 0,
            "kept_count": 0,
            "dropped_count": 0,
        }

    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    related_types = related_section_types(target_section_type)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for index, block in enumerate(reusable_blocks, start=1):
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        candidate_section_type = str(metadata.get("section_type") or "unknown").lower()
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        content_text = str(block.get("content_md") or "")
        block_text = f"{heading_text}\n{content_text}"
        try:
            selection_score = float(block.get("selection_score") or block.get("score") or 0.0)
        except (TypeError, ValueError):
            selection_score = 0.0
        selection_reasons = {
            str(item).strip()
            for item in (block.get("selection_reasons") or [])
            if str(item).strip()
        }

        reason = ""
        if candidate_section_type in TECHNICAL_REUSE_HARD_NOISE_SECTION_TYPES:
            reason = "hard_noise_section_type"
        elif _text_contains_any_token(block_text, TECHNICAL_REUSE_HARD_NOISE_TOKENS):
            reason = "hard_noise_terms"
        elif (
            target_section_type in TECHNICAL_SCHEME_SECTION_TYPES
            and candidate_section_type not in {"unknown", target_section_type, *related_types}
            and selection_score < EVIDENCE_JUDGE_DETERMINISTIC_CROSS_SECTION_MAX_SCORE
        ):
            reason = "cross_section_low_score"
        elif (
            target_section_type in TECHNICAL_SCHEME_SECTION_TYPES
            and selection_score < EVIDENCE_JUDGE_DETERMINISTIC_MISMATCH_MAX_SCORE
            and selection_reasons.intersection(
                {
                    "keyword_mismatch_penalty",
                    "equipment_type_mismatch_penalty",
                    "unknown_section_type_penalty",
                }
            )
        ):
            reason = "low_score_mismatch"

        if not reason:
            kept.append(block)
            continue

        dropped.append(
            {
                "candidate_id": f"d{index:02d}",
                "block_id": str(block.get("block_id") or ""),
                "source_title": str(block.get("source_title") or ""),
                "heading_path": heading_text,
                "section_type": candidate_section_type,
                "selection_score": round(selection_score, 4),
                "decision": "noise",
                "confidence": 0.8,
                "reason": reason,
            }
        )

    return kept, {
        "status": "applied" if dropped else "skipped",
        "reason": "deterministic_noise_filter" if dropped else "no_deterministic_noise",
        "input_count": len(reusable_blocks),
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "dropped": dropped[:8],
    }


def _build_evidence_judge_candidates(reusable_blocks: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, block in enumerate(reusable_blocks[:limit], start=1):
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        heading_text = " > ".join(str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip())
        candidates.append(
            {
                "candidate_id": f"c{index:02d}",
                "block_id": str(block.get("block_id") or ""),
                "source_title": str(block.get("source_title") or ""),
                "source_heading": str(block.get("source_heading") or ""),
                "heading_path": heading_text,
                "section_path": str(block.get("section_path") or ""),
                "section_type": str(metadata.get("section_type") or "unknown"),
                "equipment_type": str(metadata.get("equipment_type") or "generic"),
                "content_form": str(metadata.get("content_form") or "narrative"),
                "selection_score": round(float(block.get("selection_score") or 0), 4),
                "retrieval_reason_trace": [str(item) for item in (block.get("retrieval_reason_trace") or []) if str(item).strip()][:5],
                "excerpt": _build_citation_excerpt(str(block.get("content_md") or ""), limit=700),
            }
        )
    return candidates


def _build_evidence_judge_prompts(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    target_taxonomy = infer_target_taxonomy(section)
    key_params = {
        key: value
        for key, value in global_params.items()
        if key in {"project_name", "industry", "product_line", "business_objective", "voltage_level", "power_rating", "quantity"}
        and value not in (None, "", [], {})
    }
    system_prompt = (
        "你是售前技术方案的证据裁判。你的任务是在写作前筛选候选复用块，只判断证据是否适合当前章节，"
        "不要改写正文，不要补充新事实。必须输出 JSON。"
    )
    user_prompt = "\n".join(
        [
            "<section_request>",
            f"title: {section.get('title') or ''}",
            f"purpose: {section.get('purpose') or section.get('description') or ''}",
            f"section_class: {section.get('section_class') or ''}",
            f"target_section_type: {target_taxonomy.get('section_type') or 'unknown'}",
            f"target_equipment_type: {target_taxonomy.get('equipment_type') or 'generic'}",
            f"expected_evidence_types: {', '.join(str(item) for item in (section.get('expected_evidence_types') or []))}",
            f"current_project_params: {json.dumps(key_params, ensure_ascii=False)}",
            "</section_request>",
            "",
            "<judge_rules>",
            "decision=core: 可直接支撑本章节主体技术内容。",
            "decision=support: 与本章节有关，但只能作为补充边界、参数或接口说明。",
            "decision=noise: 不应进入本章节写作证据，尤其是培训、售后、维保、备品备件、建设经营、运营模式、实施进度、商务合同、公司简介、旧项目专属内容。",
            "如果标题/正文与当前章节目标冲突，以当前章节目标为准；不要因为都包含“系统/方案/项目”就判为相关。",
            "技术总体方案应优先保留系统架构、主回路、一次接线、拓扑、变频器、功率单元、旁路、隔离、冷却和柜体布置证据。",
            "</judge_rules>",
            "",
            "<candidates_json>",
            json.dumps(candidates, ensure_ascii=False, indent=2),
            "</candidates_json>",
        ]
    )
    return system_prompt, user_prompt


def _parse_evidence_judge_decisions(content: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(str(content or "{}"))
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    decisions: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_items, list):
        return decisions
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        decision = str(item.get("decision") or "").strip().lower()
        if not candidate_id or decision not in EVIDENCE_JUDGE_DECISIONS:
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        decisions[candidate_id] = {
            "decision": decision,
            "confidence": confidence,
            "reason": str(item.get("reason") or "").strip(),
        }
    return decisions


def _apply_evidence_judge_decisions(
    *,
    reusable_blocks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates or not decisions:
        return reusable_blocks, {
            "status": "fallback_no_valid_decisions",
            "input_count": len(reusable_blocks),
            "kept_count": len(reusable_blocks),
            "dropped_count": 0,
        }
    candidate_id_by_index = [str(item.get("candidate_id") or "") for item in candidates]
    judged_count = sum(1 for candidate_id in candidate_id_by_index if candidate_id in decisions)
    kept_blocks: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    candidate_limit = len(candidates)
    for index, block in enumerate(reusable_blocks):
        if index >= candidate_limit:
            kept_blocks.append(block)
            continue
        candidate = candidates[index]
        candidate_id = str(candidate.get("candidate_id") or "")
        decision = decisions.get(candidate_id)
        if not decision:
            kept_blocks.append(block)
            continue
        normalized_decision = str(decision.get("decision") or "noise")
        confidence = float(decision.get("confidence") or 0.0)
        annotated_block = dict(block)
        annotated_block["evidence_judge"] = {
            "candidate_id": candidate_id,
            "decision": normalized_decision,
            "confidence": round(confidence, 4),
            "reason": str(decision.get("reason") or ""),
        }
        if normalized_decision in EVIDENCE_JUDGE_KEEP_DECISIONS and confidence >= 0.45:
            kept_blocks.append(annotated_block)
        else:
            dropped.append(
                {
                    "candidate_id": candidate_id,
                    "block_id": candidate.get("block_id"),
                    "source_title": candidate.get("source_title"),
                    "heading_path": candidate.get("heading_path"),
                    "decision": normalized_decision,
                    "confidence": round(confidence, 4),
                    "reason": str(decision.get("reason") or ""),
                }
            )
    return kept_blocks, {
        "status": "applied",
        "input_count": len(reusable_blocks),
        "candidate_count": len(candidates),
        "judged_count": judged_count,
        "kept_count": len(kept_blocks),
        "dropped_count": len(dropped),
        "dropped": dropped[:8],
    }


def render_reuse_pack_context(reuse_pack: dict[str, Any]) -> str:
    blocks = reuse_pack.get("reusable_blocks") or []
    if not blocks:
        return ""

    sections: list[str] = []
    for index, block in enumerate(blocks, start=1):
        heading_path = block.get("heading_path") or []
        path_text = " > ".join(str(item) for item in heading_path if item)
        replace_fields = ", ".join(block.get("must_replace_fields") or []) or "无"
        sections.append(
            "\n".join(
                [
                    f"[复用块 {index}]",
                    f"来源: {block.get('source_title') or '未知来源'}",
                    f"位置: {path_text or '未标注章节'}",
                    f"类型: {block.get('block_type')}",
                    f"选择评分: {block.get('selection_score')}",
                    f"复用评分: {block.get('reusability_score')}",
                    f"必须替换字段: {replace_fields}",
                    "正文:",
                    str(block.get("content_md") or ""),
                ]
            )
        )
    return "\n\n".join(sections)


def build_manual_only_section_content(*, section: dict[str, Any], reuse_pack: dict[str, Any]) -> str:
    title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
    lines = [
        f"## {title}",
        "",
        "> 本章节已标记为人工编写，系统暂不自动生成最终客户正文。",
        "",
    ]
    blocks = reuse_pack.get("reusable_blocks") or []
    if blocks:
        lines.extend(["### 可参考复用材料", ""])
        for block in blocks[:3]:
            heading_path = " > ".join(str(item) for item in (block.get("heading_path") or []) if item)
            lines.append(
                f"- {block.get('source_title') or '未知来源'}"
                f"{f' / {heading_path}' if heading_path else ''}"
                f" / 复用评分 {block.get('reusability_score')}"
            )
        lines.append("")
    assets = reuse_pack.get("recommended_assets") or []
    if assets:
        lines.extend(["### 建议插入资产", ""])
        placeholders = reuse_pack.get("required_asset_placeholders") or []
        if placeholders:
            for item in placeholders[:3]:
                lines.append(f"- {item.get('placeholder')} {item.get('title')}")
        else:
            for asset in assets[:3]:
                asset_type = str(asset.get("asset_type") or "asset").upper()
                asset_id = asset.get("asset_id")
                title_text = asset.get("display_title") or asset.get("title") or asset.get("caption") or "参考资产"
                lines.append(f"- [[ASSET:{asset_type}:{asset_id}]] {title_text}")
        lines.append("")
    lines.extend(
        [
            "### 编写提示",
            "",
            "- 优先基于上述复用材料和资产进行人工整理。",
            "- 涉及客户信息、参数和供货边界时，必须按当前项目重新确认。",
        ]
    )
    return "\n".join(lines)


def ensure_required_asset_placeholders(*, content_md: str, reuse_pack: dict[str, Any]) -> str:
    placeholders = _filter_required_asset_placeholders_for_section(
        placeholders=list(reuse_pack.get("required_asset_placeholders") or []),
        reuse_pack=reuse_pack,
    )
    if not placeholders:
        return content_md
    missing = [
        item
        for item in placeholders
        if str(item.get("placeholder") or "") and str(item.get("placeholder")) not in content_md
    ]
    if not missing:
        return content_md

    content_with_inline_assets = _inline_missing_asset_placeholders(
        content_md=content_md,
        missing_placeholders=missing,
        reuse_pack=reuse_pack,
    )
    remaining = [
        item
        for item in placeholders
        if str(item.get("placeholder") or "") and str(item.get("placeholder")) not in content_with_inline_assets
    ]
    if not remaining:
        return content_with_inline_assets

    appendix_lines = ["", "### 相关图表", ""]
    for item in remaining:
        appendix_lines.append(f"- {item.get('placeholder')} {item.get('title') or '参考资产'}")
    return content_with_inline_assets.rstrip() + "\n" + "\n".join(appendix_lines).rstrip() + "\n"


def _filter_required_asset_placeholders_for_section(
    *,
    placeholders: list[dict[str, Any]],
    reuse_pack: dict[str, Any],
) -> list[dict[str, Any]]:
    target_section_type = str(((reuse_pack.get("target_taxonomy") or {}) or {}).get("section_type") or "").lower()
    if target_section_type not in TABLE_PLACEHOLDER_REFERENCE_ONLY_SECTION_TYPES:
        return placeholders
    filtered: list[dict[str, Any]] = []
    for item in placeholders:
        asset_type = str(item.get("asset_type") or "").lower()
        placeholder = str(item.get("placeholder") or "").strip().upper()
        if asset_type == "table" or placeholder.startswith("[[ASSET:TABLE:"):
            continue
        filtered.append(item)
    return filtered


def _inline_missing_asset_placeholders(
    *,
    content_md: str,
    missing_placeholders: list[dict[str, Any]],
    reuse_pack: dict[str, Any],
) -> str:
    lines = content_md.rstrip().splitlines()
    if not lines:
        return content_md
    asset_lookup = _build_asset_lookup(reuse_pack.get("recommended_assets") or [])
    for item in missing_placeholders:
        placeholder = str(item.get("placeholder") or "").strip()
        if not placeholder or placeholder in "\n".join(lines):
            continue
        asset_id = _extract_asset_id_from_placeholder(placeholder)
        asset = asset_lookup.get(asset_id)
        heading_index = _find_best_asset_anchor_heading(lines=lines, asset=item if asset is None else asset)
        if heading_index is None:
            continue
        insert_at = _find_asset_insertion_index(lines=lines, heading_index=heading_index)
        snippet = [placeholder, ""]
        lines[insert_at:insert_at] = snippet
    return "\n".join(lines).rstrip() + "\n"


def _build_asset_lookup(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "").strip()
        if asset_id:
            lookup[asset_id] = asset
    return lookup


def _extract_asset_id_from_placeholder(placeholder: str) -> str:
    match = ASSET_PLACEHOLDER_PATTERN.search(str(placeholder or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _find_best_asset_anchor_heading(*, lines: list[str], asset: dict[str, Any]) -> int | None:
    headings = [
        (index, line.strip()[4:].strip())
        for index, line in enumerate(lines)
        if line.strip().startswith("### ")
    ]
    if not headings:
        return None
    anchor_texts = _collect_asset_anchor_texts(asset)
    best_index: int | None = None
    best_score = 0.0
    for heading_index, heading_text in headings:
        score = _score_asset_heading_match(heading_text=heading_text, anchor_texts=anchor_texts)
        if score > best_score:
            best_score = score
            best_index = heading_index
    if best_score < 0.62:
        return None
    return best_index


def _collect_asset_anchor_texts(asset: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    semantic_summary = metadata.get("semantic_summary") if isinstance(metadata.get("semantic_summary"), dict) else {}
    for value in (
        asset.get("display_title"),
        asset.get("title"),
        asset.get("heading_path"),
        metadata.get("display_title"),
        metadata.get("raw_title"),
        metadata.get("heading_path"),
        semantic_summary.get("title_hint"),
    ):
        if isinstance(value, str) and value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    applicable_sections = semantic_summary.get("applicable_sections") if isinstance(semantic_summary.get("applicable_sections"), list) else []
    for value in applicable_sections:
        if isinstance(value, str) and value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    return texts


def _score_asset_heading_match(*, heading_text: str, anchor_texts: list[str]) -> float:
    heading_norm = _normalize_asset_anchor_text(heading_text)
    if not heading_norm:
        return 0.0
    best = 0.0
    for anchor_text in anchor_texts:
        anchor_norm = _normalize_asset_anchor_text(anchor_text)
        if not anchor_norm:
            continue
        score = SequenceMatcher(None, heading_norm, anchor_norm).ratio()
        if heading_norm == anchor_norm:
            score += 0.8
        elif heading_norm in anchor_norm or anchor_norm in heading_norm:
            score += 0.45
        best = max(best, score)
    return best


def _normalize_asset_anchor_text(text: str) -> str:
    normalized = _extract_terminal_heading_label(text)
    normalized = normalized.casefold()
    normalized = re.sub(r"[()（）【】\[\]《》·:：,，/\\\-\s]+", "", normalized)
    for token in ("系统方案", "方案", "系统图", "总图", "示意图", "框图", "原理图", "图"):
        normalized = normalized.replace(token, "")
    return normalized.strip()


def _find_asset_insertion_index(*, lines: list[str], heading_index: int) -> int:
    cursor = heading_index + 1
    seen_body = False
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if cursor > heading_index + 1 and stripped.startswith("### "):
            return cursor
        if stripped.startswith("[[ASSET:"):
            cursor += 1
            continue
        if stripped:
            seen_body = True
        elif seen_body:
            return cursor + 1
        cursor += 1
    return len(lines)


def _build_reuse_query_terms(
    *,
    section: dict[str, Any],
    global_params: dict[str, Any],
    extra_terms: list[str] | None = None,
) -> list[str]:
    intents = build_section_reuse_query_intents(
        section=section,
        global_params=global_params,
        extra_terms=extra_terms,
    )
    tokens: list[str] = []
    weighted_parts = [
        intents["title_text"],
        intents["title_text"],
        intents["detail_text"],
        intents["context_text"],
        " ".join(str(item).strip() for item in (extra_terms or []) if str(item).strip()),
    ]
    for part in weighted_parts:
        for token in _tokenize_reuse_text(part):
            if token not in tokens:
                tokens.append(token)
    for hint in extract_taxonomy_hints(*weighted_parts):
        normalized = hint.casefold()
        if normalized not in tokens:
            tokens.append(normalized)
    return tokens


def _tokenize_reuse_text(text: str) -> list[str]:
    tokens: list[str] = []
    raw_text = str(text or "")
    for match in REUSE_TOKEN_PATTERN.findall(str(text or "")):
        token = match.strip().lower()
        for expanded in expand_domain_terms([token]):
            if len(expanded) < 2 or expanded in REUSE_STOPWORDS or expanded in tokens:
                continue
            tokens.append(expanded)
    for term in extract_domain_terms(raw_text):
        if len(term) < 2 or term in REUSE_STOPWORDS or term in tokens:
            continue
        tokens.append(term)
    return tokens


def _score_reuse_candidate(
    *,
    section: dict[str, Any],
    item: dict[str, Any],
    raw_content: str,
    heading_path: list[Any],
    query_terms: list[str],
    target_taxonomy: dict[str, Any] | None = None,
    knowledge_retrieval_bundle: dict[str, Any] | None = None,
) -> tuple[float, list[str], dict[str, float]]:
    base_score = float(item.get("reusability_score") or item.get("relevance_score") or 0)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    result_type = str(item.get("type") or item.get("source_chunk_type") or "").lower()
    heading_text = " ".join(str(segment) for segment in heading_path if segment)
    heading_terms = set(_tokenize_reuse_text(heading_text))
    content_terms = set(_tokenize_reuse_text(raw_content[:1200]))
    query_set = set(query_terms)
    overlap_count = len(query_set & (heading_terms | content_terms))
    overlap_ratio = (overlap_count / len(query_set)) if query_set else 0.0
    score = base_score
    reasons: list[str] = []

    if overlap_ratio:
        bonus = min(0.22, overlap_ratio * 0.22)
        score += bonus
        reasons.append(f"keyword_overlap={overlap_count}")
    elif query_set:
        score -= 0.12
        reasons.append("keyword_mismatch_penalty")
    if query_set and heading_terms and (query_set & heading_terms):
        score += 0.12
        reasons.append("heading_match")

    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    if result_type and result_type in expected_types:
        score += 0.06
        reasons.append("expected_type")

    section_class = str(section.get("section_class") or "").lower()
    if section_class and any(section_class in token for token in heading_terms | content_terms):
        score += 0.08
        reasons.append("section_class_match")

    if metadata.get("front_matter"):
        score -= 0.18
        reasons.append("front_matter_penalty")
    content_form = str(
        item.get("content_form")
        or metadata.get("content_form")
        or "narrative"
    ).lower()
    section_type = str(
        item.get("section_type")
        or metadata.get("section_type")
        or "unknown"
    ).lower()
    equipment_type = str(
        item.get("equipment_type")
        or metadata.get("equipment_type")
        or "generic"
    ).lower()
    if content_form == "page_furniture":
        score -= 0.25
        reasons.append("page_furniture_penalty")
    if metadata.get("needs_asset_lookup") and not bool(section.get("asset_required")):
        score -= 0.08
        reasons.append("asset_dependency_penalty")

    customer_specificity = str(section.get("customer_specificity") or "medium").lower()
    if customer_specificity in {"medium", "high"} and _looks_customer_specific(raw_content):
        penalty = 0.08 if customer_specificity == "medium" else 0.12
        score -= penalty
        reasons.append("customer_specific_penalty")

    if bool(section.get("parameter_sensitive")) and result_type not in {"parameter", "table"}:
        score -= 0.04
        reasons.append("parameter_type_penalty")
    if bool(section.get("parameter_sensitive")) and not content_form_is_table(content_form):
        score -= 0.06
        reasons.append("parameter_content_form_penalty")

    if target_taxonomy:
        target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
        target_equipment_type = str(target_taxonomy.get("equipment_type") or "generic").lower()
        preferred_content_forms = {
            str(item).lower()
            for item in (target_taxonomy.get("preferred_content_forms") or [])
            if item
        }
        support_forms = {
            str(item).lower()
            for item in (
                target_taxonomy.get("support_content_forms")
                or support_content_forms(target_section_type)
            )
            if item
        }
        if target_section_type != "company_profile" and section_type == "company_profile":
            score -= 0.18
            reasons.append("company_profile_penalty")
        if target_section_type != "unknown" and section_type == target_section_type:
            score += 0.2
            reasons.append("section_type_match")
        elif section_type in related_section_types(target_section_type):
            score += 0.1
            reasons.append("related_section_type_match")
        elif target_section_type not in {"unknown", "overall_solution"}:
            if section_type == "unknown":
                score -= 0.05
                reasons.append("unknown_section_type_penalty")
            else:
                score -= 0.12
                reasons.append("section_type_mismatch_penalty")
        if target_equipment_type != "generic" and equipment_type == target_equipment_type:
            score += 0.1
            reasons.append("equipment_type_match")
        elif target_equipment_type != "generic" and equipment_type not in {"generic", target_equipment_type}:
            score -= 0.12
            reasons.append("equipment_type_mismatch_penalty")
        if preferred_content_forms and content_form in preferred_content_forms:
            score += 0.08
            reasons.append("content_form_match")
        elif support_forms and content_form in support_forms:
            score += 0.04
            reasons.append("support_content_form_match")
        elif preferred_content_forms and content_form in {"formula", "certificate"}:
            score -= 0.12
            reasons.append("content_form_mismatch_penalty")
        heading_adjustment, heading_reasons = heading_focus_adjustment(
            target_section_type=target_section_type,
            heading_text=" > ".join(str(item).strip() for item in heading_path if str(item).strip()),
        )
        score += heading_adjustment
        reasons.extend(heading_reasons)

    knowledge_prior_score, knowledge_prior_reasons, knowledge_prior_breakdown = _score_knowledge_wiki_retrieval_priors(
        knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        heading_text=heading_text,
        raw_content=raw_content,
        heading_terms=heading_terms,
        content_terms=content_terms,
        section_type=section_type,
        equipment_type=equipment_type,
        query_target_section_type=target_section_type if target_taxonomy else "",
        query_target_equipment_type=target_equipment_type if target_taxonomy else "",
    )
    score += knowledge_prior_score
    reasons.extend(knowledge_prior_reasons)

    normalized_score = round(min(max(score, 0.0), 1.2), 4)
    score_breakdown = {
        "base_score": round(base_score, 4),
        "knowledge_wiki_prior_total": round(knowledge_prior_score, 4),
        **knowledge_prior_breakdown,
        "final_score": normalized_score,
    }
    return normalized_score, reasons, score_breakdown


def _score_knowledge_wiki_retrieval_priors(
    *,
    knowledge_retrieval_bundle: dict[str, Any] | None,
    heading_text: str,
    raw_content: str,
    heading_terms: set[str],
    content_terms: set[str],
    section_type: str,
    equipment_type: str,
    query_target_section_type: str,
    query_target_equipment_type: str,
) -> tuple[float, list[str], dict[str, float]]:
    if not isinstance(knowledge_retrieval_bundle, dict):
        return 0.0, [], {}

    candidate_terms = set(heading_terms) | set(content_terms)
    if not candidate_terms:
        return 0.0, [], {}
    if not _knowledge_prior_candidate_is_structurally_relevant(
        query_target_section_type=query_target_section_type,
        query_target_equipment_type=query_target_equipment_type,
        candidate_section_type=section_type,
        candidate_equipment_type=equipment_type,
    ):
        return 0.0, [], {}

    score = 0.0
    reasons: list[str] = []
    breakdown: dict[str, float] = {}
    product_cards = [
        item
        for item in (knowledge_retrieval_bundle.get("product_cards") or [])
        if isinstance(item, dict)
    ]
    module_cards = [
        item
        for item in (knowledge_retrieval_bundle.get("module_cards") or [])
        if isinstance(item, dict)
    ]

    if _candidate_matches_knowledge_card(
        candidate_text=f"{heading_text}\n{raw_content}",
        candidate_terms=candidate_terms,
        cards=product_cards,
        text_fields=("title", "aliases", "representative_titles", "representative_headings"),
    ):
        score += 0.03
        reasons.append("knowledge_wiki_product_match")
        breakdown["knowledge_wiki_product_match"] = 0.03
    if _candidate_matches_knowledge_card(
        candidate_text=f"{heading_text}\n{raw_content}",
        candidate_terms=candidate_terms,
        cards=module_cards,
        text_fields=("title", "aliases", "top_headings"),
    ):
        score += 0.04
        reasons.append("knowledge_wiki_module_match")
        breakdown["knowledge_wiki_module_match"] = 0.04
    if _knowledge_cards_match_section_type(cards=product_cards, section_type=section_type):
        score += 0.03
        reasons.append("knowledge_wiki_product_section_prior")
        breakdown["knowledge_wiki_product_section_prior"] = 0.03
    if _knowledge_cards_match_equipment_type(cards=product_cards, equipment_type=equipment_type):
        score += 0.03
        reasons.append("knowledge_wiki_product_equipment_prior")
        breakdown["knowledge_wiki_product_equipment_prior"] = 0.03

    return score, reasons, breakdown


def _candidate_matches_knowledge_card(
    *,
    candidate_text: str,
    candidate_terms: set[str],
    cards: list[dict[str, Any]],
    text_fields: tuple[str, ...],
) -> bool:
    normalized_candidate_text = _normalize_knowledge_card_match_text(candidate_text)
    for card in cards:
        values: list[str] = []
        for field in text_fields:
            raw_value = card.get(field)
            if isinstance(raw_value, list):
                values.extend(str(item).strip() for item in raw_value if str(item).strip())
            else:
                text = str(raw_value or "").strip()
                if text:
                    values.append(text)
        for value in values:
            normalized_value = _normalize_knowledge_card_match_text(value)
            if normalized_value and normalized_value in normalized_candidate_text:
                return True
        card_terms = {
            token
            for token in _tokenize_reuse_text(" ".join(values))
            if token
        }
        if card_terms & candidate_terms:
            return True
    return False


def _knowledge_prior_candidate_is_structurally_relevant(
    *,
    query_target_section_type: str,
    query_target_equipment_type: str,
    candidate_section_type: str,
    candidate_equipment_type: str,
) -> bool:
    normalized_query_section_type = str(query_target_section_type or "").strip().lower()
    normalized_query_equipment_type = str(query_target_equipment_type or "").strip().lower()
    normalized_candidate_section_type = str(candidate_section_type or "").strip().lower()
    normalized_candidate_equipment_type = str(candidate_equipment_type or "").strip().lower()

    section_type_guard = normalized_query_section_type not in {"", "unknown", "overall_solution"}
    equipment_type_guard = normalized_query_equipment_type not in {"", "unknown", "generic"}

    if not section_type_guard and not equipment_type_guard:
        return True
    if section_type_guard and (
        normalized_candidate_section_type == normalized_query_section_type
        or normalized_candidate_section_type in related_section_types(normalized_query_section_type)
    ):
        return True
    if equipment_type_guard and normalized_candidate_equipment_type == normalized_query_equipment_type:
        return True
    return False


def _normalize_knowledge_card_match_text(text: str) -> str:
    normalized = str(text or "").casefold()
    normalized = re.sub(r"[()（）【】\[\]《》·:：,，/\\\-\s]+", "", normalized)
    return normalized.strip()


def _knowledge_cards_match_section_type(*, cards: list[dict[str, Any]], section_type: str) -> bool:
    normalized_section_type = str(section_type or "").strip().lower()
    if not normalized_section_type or normalized_section_type == "unknown":
        return False
    for card in cards:
        for item in (card.get("top_section_types") or []):
            if not isinstance(item, dict):
                continue
            if str(item.get("section_type") or "").strip().lower() == normalized_section_type:
                return True
    return False


def _knowledge_cards_match_equipment_type(*, cards: list[dict[str, Any]], equipment_type: str) -> bool:
    normalized_equipment_type = str(equipment_type or "").strip().lower()
    if not normalized_equipment_type or normalized_equipment_type == "generic":
        return False
    for card in cards:
        for item in (card.get("top_equipment_types") or []):
            if not isinstance(item, dict):
                continue
            if str(item.get("equipment_type") or "").strip().lower() == normalized_equipment_type:
                return True
    return False


def _looks_customer_specific(content: str) -> bool:
    text = str(content or "")
    return any(token in text for token in ("买方", "卖方", "客户", "项目名称", "用户"))


def _select_section_scope_candidates(section_candidates: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    if not section_candidates:
        return []
    candidates = [item for item in section_candidates if str(item.get("section_path") or item.get("heading_path") or "").strip()]
    if not candidates:
        return []
    selected: list[dict[str, Any]] = []
    selected_paths: list[str] = []

    anchor_candidates = [
        item
        for item in candidates
        if int(item.get("level") or 1) <= 2
        and any(
            token in str(item.get("reason") or "")
            for token in (
                "normalized_section_title_match",
                "section_path_title_match",
                "heading_alias_match",
                "section_title_match",
            )
        )
    ]
    if anchor_candidates:
        anchor = sorted(
            anchor_candidates,
            key=lambda item: (
                float(item.get("score") or 0),
                -int(item.get("level") or 0),
                len(str(item.get("section_path") or item.get("heading_path") or "")),
            ),
            reverse=True,
        )[0]
        path = str(anchor.get("section_path") or anchor.get("heading_path") or "").strip()
        if path:
            selected.append(anchor)
            selected_paths.append(path)

    specific_candidates = [item for item in candidates if int(item.get("level") or 1) >= 2]
    scoped = specific_candidates or candidates
    scoped = sorted(
        scoped,
        key=lambda item: (
            float(item.get("score") or 0),
            int(item.get("level") or 0),
            len(str(item.get("section_path") or item.get("heading_path") or "")),
        ),
        reverse=True,
    )
    for item in scoped:
        path = str(item.get("section_path") or item.get("heading_path") or "").strip()
        if not path:
            continue
        if path in selected_paths:
            continue
        if any(existing.startswith(f"{path} >") or existing == path for existing in selected_paths):
            continue
        selected.append(item)
        selected_paths.append(path)
        if len(selected) >= limit:
            break
    return selected or candidates[:limit]


def _serialize_section_candidate(section_candidate: dict[str, Any]) -> dict[str, Any]:
    score_breakdown = section_candidate.get("score_breakdown") if isinstance(section_candidate.get("score_breakdown"), dict) else {}
    normalized_breakdown: dict[str, float] = {}
    for key, value in score_breakdown.items():
        try:
            normalized_breakdown[str(key)] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return {
        "sample_id": str(section_candidate.get("sample_id") or ""),
        "file_name": str(section_candidate.get("file_name") or ""),
        "section_id": str(section_candidate.get("section_id") or ""),
        "section_path": str(section_candidate.get("section_path") or section_candidate.get("heading_path") or ""),
        "source_heading": str(section_candidate.get("source_heading") or ""),
        "level": int(section_candidate.get("level") or 0),
        "score": float(section_candidate.get("score") or 0),
        "reason": str(section_candidate.get("reason") or ""),
        "reason_trace": [str(item) for item in (section_candidate.get("reason_trace") or []) if str(item or "").strip()],
        "score_breakdown": normalized_breakdown,
    }


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(_json_safe_value(item) for item in value)
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    return value


def _parse_table_source_ref_index(source_ref: Any) -> int | None:
    match = TABLE_SOURCE_REF_PATTERN.search(str(source_ref or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _looks_like_markdown_table(text: Any) -> bool:
    normalized = str(text or "")
    return "|" in normalized and bool(re.search(r"\n\|?[-: ]+\|[-|: ]+", normalized))


def _weak_asset_preview_text(asset: dict[str, Any]) -> bool:
    preview = str(asset.get("preview_text") or "").strip()
    if not preview:
        return True
    candidates = {
        str(asset.get("title") or "").strip(),
        str(asset.get("display_title") or "").strip(),
        str(asset.get("heading_path") or "").strip(),
    }
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    candidates.update(
        str(value or "").strip()
        for value in (
            metadata.get("raw_title"),
            metadata.get("display_title"),
            metadata.get("heading_path"),
            metadata.get("source_heading"),
        )
    )
    return preview in {item for item in candidates if item}


def _compact_table_markdown_preview(markdown: str, *, limit: int = 1600) -> str:
    normalized = str(markdown or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _enrich_table_asset_from_source_chunks(asset: dict[str, Any], table_chunks: list[Any]) -> bool:
    if str(asset.get("asset_type") or "").lower() != "table":
        return False
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    if _looks_like_markdown_table(metadata.get("raw_table_markdown")):
        return False
    source_ref = asset.get("source_ref") or metadata.get("source_ref")
    table_index = _parse_table_source_ref_index(source_ref)
    if table_index is None or table_index < 0 or table_index >= len(table_chunks):
        return False
    chunk = table_chunks[table_index]
    content = str(getattr(chunk, "content", "") or "").strip()
    if not _looks_like_markdown_table(content):
        return False
    enriched_metadata = dict(metadata)
    enriched_metadata["raw_table_markdown"] = content
    enriched_metadata["raw_table_chunk_id"] = str(getattr(chunk, "id", "") or "")
    enriched_metadata["raw_table_chunk_index"] = getattr(chunk, "chunk_index", None)
    enriched_metadata["raw_table_source"] = "source_ref_table_chunk"
    asset["metadata"] = _json_safe_value(enriched_metadata)
    if _weak_asset_preview_text(asset):
        asset["preview_text"] = _compact_table_markdown_preview(content)
    return True


async def _enrich_table_assets_with_source_content(
    *,
    session: AsyncSession,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    document_ids: set[UUID] = set()
    for asset in assets:
        if str(asset.get("asset_type") or "").lower() != "table":
            continue
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        if _looks_like_markdown_table(metadata.get("raw_table_markdown")):
            continue
        document_id = asset.get("document_id") or metadata.get("legacy_document_id")
        if not document_id:
            continue
        try:
            document_ids.add(UUID(str(document_id)))
        except (TypeError, ValueError):
            continue
    if not document_ids:
        return assets

    table_chunks_by_document: dict[UUID, list[Chunk]] = {}
    for document_id in document_ids:
        rows = (
            await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id, Chunk.chunk_type == "TABLE")
                .order_by(Chunk.chunk_index.asc())
            )
        ).scalars().all()
        table_chunks_by_document[document_id] = list(rows)

    for asset in assets:
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        document_id_value = asset.get("document_id") or metadata.get("legacy_document_id")
        try:
            document_id = UUID(str(document_id_value))
        except (TypeError, ValueError):
            continue
        _enrich_table_asset_from_source_chunks(asset, table_chunks_by_document.get(document_id, []))
    return assets


def _estimate_material_tokens(
    *,
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    max_source_tokens: int | None = None,
) -> dict[str, Any]:
    effective_max_source_tokens = int(max_source_tokens or get_settings().section_reuse_full_section_max_tokens)
    section_material_tokens = sum(
        max(1, len(str(block.get("content_md") or "")) // 4)
        for block in blocks
    )
    asset_tokens = sum(
        max(
            1,
            len(
                " ".join(
                    str(item)
                    for item in (
                        asset.get("title"),
                        asset.get("caption"),
                        asset.get("heading_path"),
                        asset.get("preview_text"),
                    )
                    if item
                )
            )
            // 4,
        )
        for asset in assets[:3]
    )
    return {
        "section_material_tokens": section_material_tokens,
        "asset_tokens": asset_tokens,
        "max_source_tokens": effective_max_source_tokens,
        "within_budget": section_material_tokens <= effective_max_source_tokens,
    }


def _build_selected_block_trace(blocks: list[dict[str, Any]], *, limit: int = REUSE_TRACE_BLOCK_LIMIT) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for block in blocks[:limit]:
        heading_path = [str(item).strip() for item in (block.get("heading_path") or []) if str(item).strip()]
        trace.append(
            {
                "block_id": str(block.get("block_id") or ""),
                "source_title": str(block.get("source_title") or ""),
                "source_section_id": str(block.get("source_section_id") or ""),
                "section_path": str(block.get("section_path") or " > ".join(heading_path)),
                "heading_path": heading_path,
                "selection_score": float(block.get("selection_score") or 0),
                "selection_reasons": list(block.get("selection_reasons") or []),
                "retrieval_reason": str(block.get("retrieval_reason") or ""),
                "retrieval_reason_trace": [str(item) for item in (block.get("retrieval_reason_trace") or []) if str(item).strip()],
                "retrieval_score_breakdown": _extract_retrieval_score_breakdown(block),
                "selection_score_breakdown": _extract_selection_score_breakdown(block),
            }
        )
    return trace


def _extract_retrieval_score_breakdown(block: dict[str, Any]) -> dict[str, float]:
    breakdown = (
        block.get("retrieval_score_breakdown")
        if isinstance(block.get("retrieval_score_breakdown"), dict)
        else block.get("score_breakdown")
        if isinstance(block.get("score_breakdown"), dict)
        else (
            (block.get("metadata") or {}).get("hybrid_score_breakdown")
            if isinstance(block.get("metadata"), dict) and isinstance((block.get("metadata") or {}).get("hybrid_score_breakdown"), dict)
            else {}
        )
    )
    normalized: dict[str, float] = {}
    for key, value in breakdown.items():
        try:
            normalized[str(key)] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return normalized


def _extract_selection_score_breakdown(block: dict[str, Any]) -> dict[str, float]:
    breakdown = block.get("selection_score_breakdown") if isinstance(block.get("selection_score_breakdown"), dict) else {}
    normalized: dict[str, float] = {}
    for key, value in breakdown.items():
        try:
            normalized[key] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return normalized


def _reuse_block_source_order_key(block: dict[str, Any]) -> tuple[int, int, int, float]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}

    def _int_value(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return (
        _int_value(block.get("page_no") or metadata.get("page_no"), 0),
        _int_value(block.get("chunk_index") or metadata.get("chunk_index"), 0),
        _int_value(block.get("subchunk_index") or metadata.get("subchunk_index"), 0),
        -float(block.get("selection_score") or 0),
    )


def _build_knowledge_wiki_prior_summary(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    reason_hits: Counter[str] = Counter()
    prior_hit_block_count = 0
    total_prior_boost = 0.0
    max_prior_boost = 0.0
    for block in blocks:
        breakdown = _extract_selection_score_breakdown(block)
        prior_total = float(breakdown.get("knowledge_wiki_prior_total") or 0)
        if prior_total <= 0:
            continue
        prior_hit_block_count += 1
        total_prior_boost += prior_total
        max_prior_boost = max(max_prior_boost, prior_total)
        for key in (
            "knowledge_wiki_product_match",
            "knowledge_wiki_module_match",
            "knowledge_wiki_product_section_prior",
            "knowledge_wiki_product_equipment_prior",
        ):
            if float(breakdown.get(key) or 0) > 0:
                reason_hits[key] += 1
    selected_block_count = len(blocks)
    return {
        "selected_block_count": selected_block_count,
        "prior_hit_block_count": prior_hit_block_count,
        "prior_hit_ratio": round(prior_hit_block_count / max(selected_block_count, 1), 4) if selected_block_count else 0.0,
        "total_prior_boost": round(total_prior_boost, 4),
        "max_prior_boost": round(max_prior_boost, 4),
        "reason_hits": dict(sorted(reason_hits.items())),
    }


def _match_blocks_to_selected_section(
    *,
    reusable_blocks: list[dict[str, Any]],
    selected_section: dict[str, Any],
) -> list[dict[str, Any]]:
    target_sample_id = str(selected_section.get("sample_id") or "").strip()
    target_file_name = str(selected_section.get("file_name") or "").strip()
    target_section_id = str(selected_section.get("section_id") or "").strip()
    target_section_path = str(selected_section.get("section_path") or "").strip()
    target_source_heading = str(selected_section.get("source_heading") or "").strip()
    normalized_target_heading = _normalize_asset_anchor_text(target_source_heading or target_section_path)
    matched: list[dict[str, Any]] = []
    for block in reusable_blocks:
        block_sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
        block_file_name = str(block.get("source_title") or "").strip()
        block_section_id = str(block.get("source_section_id") or "").strip()
        block_section_path = str(block.get("section_path") or " > ".join(str(item) for item in (block.get("heading_path") or []) if item)).strip()
        block_content = str(block.get("content_md") or "")
        if target_sample_id and block_sample_id and block_sample_id != target_sample_id:
            continue
        if target_file_name and block_file_name and block_file_name != target_file_name:
            continue
        section_id_matched = bool(
            target_section_id
            and block_section_id
            and (
                block_section_id == target_section_id
                or block_section_id.startswith(f"{target_section_id}.")
            )
        )
        section_path_matched = bool(
            target_section_path
            and block_section_path
            and (
                block_section_path == target_section_path
                or block_section_path.startswith(f"{target_section_path} >")
            )
        )
        if section_id_matched or section_path_matched:
            matched.append(block)
            continue
        ancestor_section_block = bool(
            target_section_id
            and block_section_id
            and target_section_id.startswith(f"{block_section_id}.")
        )
        ancestor_path_block = bool(
            target_section_path
            and block_section_path
            and target_section_path.startswith(f"{block_section_path} >")
        )
        if ancestor_section_block or ancestor_path_block:
            normalized_content = _normalize_asset_anchor_text(block_content)
            if normalized_target_heading and normalized_target_heading in normalized_content:
                matched.append(block)
    return matched


def _section_candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(candidate.get("sample_id") or "").strip(),
        str(candidate.get("file_name") or "").strip(),
        str(candidate.get("section_id") or "").strip(),
        str(candidate.get("section_path") or candidate.get("heading_path") or "").strip(),
    )


def _same_source_section_family(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    left_sample, left_file, left_id, left_path = _section_candidate_key(left)
    right_sample, right_file, right_id, right_path = _section_candidate_key(right)
    if left_sample and right_sample and left_sample != right_sample:
        return False
    if left_file and right_file and left_file != right_file:
        return False
    if left_id and right_id and (
        left_id == right_id
        or left_id.startswith(f"{right_id}.")
        or right_id.startswith(f"{left_id}.")
    ):
        return True
    if left_path and right_path and (
        left_path == right_path
        or left_path.startswith(f"{right_path} >")
        or right_path.startswith(f"{left_path} >")
    ):
        return True
    return False


def _dedupe_section_candidates_for_strategy(section_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for candidate in section_candidates:
        key = _section_candidate_key(candidate)
        current = deduped.get(key)
        if current is None or float(candidate.get("score") or 0) > float(current.get("score") or 0):
            deduped[key] = candidate
    return sorted(deduped.values(), key=lambda item: float(item.get("score") or 0), reverse=True)


def _build_reuse_selection_reason(
    *,
    section: dict[str, Any],
    retrieval_mode: str,
    context_mode: str,
    full_section_budget_enabled: bool,
    min_score_threshold: float,
    min_lead_threshold: float,
    top_section: dict[str, Any] | None,
    runner_up: dict[str, Any] | None,
    reusable_blocks: list[dict[str, Any]],
    full_section_blocks: list[dict[str, Any]],
    full_section_budget: dict[str, Any],
) -> dict[str, Any]:
    top_score = float((top_section or {}).get("score") or 0)
    runner_up_score = float((runner_up or {}).get("score") or 0)
    lead_score = top_score - runner_up_score
    reasons: list[str] = []
    generation_mode = str(section.get("generation_mode") or "baseline")

    if generation_mode != "reuse_first":
        reasons.append("generation_mode_not_reuse_first")
    reasons.append(f"context_mode={context_mode}")
    reasons.append(f"full_section_budget_enabled={bool(full_section_budget_enabled)}")
    if not reusable_blocks:
        reasons.append("no_reusable_blocks")
    if top_section:
        reasons.append(f"top_section_score={top_score:.4f}")
        reasons.append(f"top_section_id={str(top_section.get('section_id') or '').strip() or 'unknown'}")
    else:
        reasons.append("no_section_candidate")
    if runner_up:
        reasons.append(f"runner_up_score={runner_up_score:.4f}")
        reasons.append(f"lead_score={lead_score:.4f}")
    elif top_section:
        reasons.append("single_section_candidate")
    if full_section_blocks:
        reasons.append(f"full_section_block_count={len(full_section_blocks)}")
    elif generation_mode == "reuse_first" and reusable_blocks:
        reasons.append("no_top_section_block_alignment")
    if full_section_budget:
        reasons.append(f"full_section_within_budget={bool(full_section_budget.get('within_budget'))}")

    if retrieval_mode == "baseline_fallback":
        reasons.append("selected_baseline_fallback")
    elif retrieval_mode == "full_section":
        reasons.append("selected_full_section")
    elif retrieval_mode == "section_pack":
        if context_mode == "section_pack":
            reasons.append("section_pack_forced_by_context_mode")
        if not top_section:
            reasons.append("section_pack_due_to_missing_top_section")
        elif top_score < min_score_threshold:
            reasons.append("section_pack_due_to_low_top_section_score")
        elif lead_score < min_lead_threshold:
            reasons.append("section_pack_due_to_low_section_lead")
        elif not full_section_blocks:
            reasons.append("section_pack_due_to_missing_aligned_blocks")
        elif full_section_budget_enabled and not bool(full_section_budget.get("within_budget")):
            reasons.append("section_pack_due_to_token_budget")
        else:
            reasons.append("selected_section_pack")

    return {
        "mode": retrieval_mode,
        "top_section_id": str((top_section or {}).get("section_id") or "").strip(),
        "top_section_score": round(top_score, 4),
        "runner_up_score": round(runner_up_score, 4),
        "lead_score": round(lead_score, 4),
        "context_mode": context_mode,
        "min_score_threshold": round(min_score_threshold, 4),
        "min_lead_threshold": round(min_lead_threshold, 4),
        "full_section_block_count": len(full_section_blocks),
        "full_section_budget_enabled": bool(full_section_budget_enabled),
        "full_section_within_budget": bool(full_section_budget.get("within_budget")),
        "reasons": reasons,
    }


def resolve_reuse_generation_strategy(
    *,
    section: dict[str, Any],
    reuse_pack: dict[str, Any],
    reusable_blocks: list[dict[str, Any]],
    recommended_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    context_mode = str(settings.section_reuse_context_mode or "prefer_full_section").lower()
    if context_mode not in {"section_pack", "auto", "prefer_full_section"}:
        context_mode = "prefer_full_section"
    if bool(section.get("child_aware_retrieval")):
        context_mode = "section_pack"
    prompt_block_limit = _reuse_prompt_block_limit(section)
    full_section_budget_enabled = bool(settings.section_reuse_full_section_budget_enabled)
    full_section_max_tokens = int(settings.section_reuse_full_section_max_tokens or FULL_SECTION_MAX_SOURCE_TOKENS)
    if context_mode == "prefer_full_section":
        min_score_threshold = FULL_SECTION_PREFER_MIN_SCORE
        min_lead_threshold = FULL_SECTION_PREFER_MIN_LEAD
    else:
        min_score_threshold = FULL_SECTION_MIN_SCORE
        min_lead_threshold = FULL_SECTION_MIN_LEAD
    retrieval_trace = reuse_pack.get("retrieval_trace") if isinstance(reuse_pack.get("retrieval_trace"), dict) else {}
    section_candidates = [
        item
        for item in (retrieval_trace.get("section_candidates") or [])
        if isinstance(item, dict)
    ]
    section_candidates = _dedupe_section_candidates_for_strategy(section_candidates)
    scoped_sections = [
        item
        for item in (retrieval_trace.get("scoped_sections") or [])
        if isinstance(item, dict)
    ]
    scoped_sections = _dedupe_section_candidates_for_strategy(scoped_sections)
    selected_sections = scoped_sections[:REUSE_TRACE_SECTION_LIMIT] or section_candidates[:REUSE_TRACE_SECTION_LIMIT]
    if str(section.get("generation_mode") or "baseline") != "reuse_first" or not reusable_blocks:
        prompt_blocks = reusable_blocks[:prompt_block_limit]
        return {
            "retrieval_mode": "baseline_fallback",
            "context_mode": context_mode,
            "prompt_blocks": prompt_blocks,
            "selected_sections": selected_sections,
            "selected_blocks": _build_selected_block_trace(prompt_blocks),
            "knowledge_wiki_prior_summary": _build_knowledge_wiki_prior_summary(prompt_blocks),
            "token_budget": _estimate_material_tokens(
                blocks=prompt_blocks,
                assets=recommended_assets,
                max_source_tokens=full_section_max_tokens,
            ),
        }

    top_section = section_candidates[0] if section_candidates else (selected_sections[0] if selected_sections else None)
    runner_up = next(
        (candidate for candidate in section_candidates[1:] if not _same_source_section_family(top_section, candidate)),
        None,
    )
    top_score = float((top_section or {}).get("score") or 0)
    lead_score = top_score - float((runner_up or {}).get("score") or 0)
    full_section_blocks = (
        _match_blocks_to_selected_section(
            reusable_blocks=reusable_blocks,
            selected_section=top_section,
        )
        if top_section
        else []
    )
    if context_mode == "prefer_full_section":
        full_section_blocks = sorted(full_section_blocks, key=_reuse_block_source_order_key)
    else:
        full_section_blocks = sorted(
            full_section_blocks,
            key=lambda item: (
                float(item.get("selection_score") or 0),
                float(item.get("reusability_score") or 0),
            ),
            reverse=True,
        )
    full_section_budget = _estimate_material_tokens(
        blocks=full_section_blocks,
        assets=recommended_assets,
        max_source_tokens=full_section_max_tokens,
    )
    full_section_budget["budget_enabled"] = full_section_budget_enabled
    full_section_allowed_by_budget = (
        bool(full_section_budget.get("within_budget"))
        if full_section_budget_enabled
        else True
    )
    if (
        context_mode != "section_pack"
        and top_section
        and top_score >= min_score_threshold
        and lead_score >= min_lead_threshold
        and full_section_blocks
        and full_section_allowed_by_budget
    ):
        prompt_blocks = full_section_blocks
        retrieval_mode = "full_section"
        token_budget = full_section_budget
    else:
        prompt_blocks = reusable_blocks[:prompt_block_limit]
        retrieval_mode = "section_pack"
        token_budget = _estimate_material_tokens(
            blocks=prompt_blocks,
            assets=recommended_assets,
            max_source_tokens=full_section_max_tokens,
        )
    selection_reason = _build_reuse_selection_reason(
        section=section,
        retrieval_mode=retrieval_mode,
        context_mode=context_mode,
        full_section_budget_enabled=full_section_budget_enabled,
        min_score_threshold=min_score_threshold,
        min_lead_threshold=min_lead_threshold,
        top_section=top_section,
        runner_up=runner_up,
        reusable_blocks=reusable_blocks,
        full_section_blocks=full_section_blocks,
        full_section_budget=full_section_budget,
    )
    return {
        "retrieval_mode": retrieval_mode,
        "context_mode": context_mode,
        "prompt_blocks": prompt_blocks,
        "selected_sections": selected_sections,
        "selected_blocks": _build_selected_block_trace(prompt_blocks),
        "knowledge_wiki_prior_summary": _build_knowledge_wiki_prior_summary(prompt_blocks),
        "token_budget": token_budget,
        "selection_reason": selection_reason,
    }


class SectionDraftService:
    def __init__(
        self,
        *,
        executor: ExecutorAgent | None = None,
        asset_retriever: AssetRetrievalService | None = None,
        case_library: CaseLibraryService | None = None,
        quality_gate: SectionQualityGateService | None = None,
        knowledge_wiki: KnowledgeWikiContextProvider | None = None,
    ) -> None:
        self.settings = get_settings()
        self.executor = executor or ExecutorAgent()
        self._asset_retriever = asset_retriever
        self.knowledge_wiki = knowledge_wiki or KnowledgeWikiContextProvider()
        self.case_library = case_library or CaseLibraryService()
        self.quality_gate = quality_gate or SectionQualityGateService(
            executor=self.executor,
            knowledge_wiki=self.knowledge_wiki,
        )
        self.section_generation_concurrency = max(1, int(self.settings.section_generation_concurrency or 1))
        self.section_generation_quality_gate = str(self.settings.section_generation_quality_gate or "full").lower()
        self.section_generation_fast_coherence_pass = bool(self.settings.section_generation_fast_coherence_pass)
        self.section_generation_granularity = str(self.settings.section_generation_granularity or "top_level").lower()
        self.section_reuse_context_mode = str(self.settings.section_reuse_context_mode or "prefer_full_section").lower()
        self.section_reuse_candidate_limit = max(DEFAULT_REUSE_LIMIT, int(self.settings.section_reuse_candidate_limit or DEFAULT_REUSE_LIMIT))
        self.section_reuse_full_section_block_limit = max(
            self.section_reuse_candidate_limit,
            int(self.settings.section_reuse_full_section_block_limit or self.section_reuse_candidate_limit),
        )
        self.evidence_judge_mode = str(self.settings.evidence_judge_mode or "off").lower()
        self.evidence_judge_max_candidates = max(1, int(self.settings.evidence_judge_max_candidates or 10))

    @property
    def asset_retriever(self) -> AssetRetrievalService:
        if self._asset_retriever is None:
            self._asset_retriever = AssetRetrievalService()
        return self._asset_retriever

    def _should_use_parallel_generation(self, sections: list[dict[str, Any]]) -> bool:
        return self.section_generation_concurrency > 1 and len(sections) > 1

    async def _filter_reusable_blocks_with_evidence_judge(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        global_params: dict[str, Any],
        reusable_blocks: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        should_run, reason = _is_evidence_judge_target(
            section=section,
            reusable_blocks=reusable_blocks,
            mode=self.evidence_judge_mode,
        )
        base_trace = {
            "mode": self.evidence_judge_mode,
            "status": "skipped",
            "reason": reason,
            "input_count": len(reusable_blocks),
        }
        if not should_run:
            return reusable_blocks, base_trace
        original_input_count = len(reusable_blocks)
        deterministic_blocks, deterministic_trace = _deterministic_prefilter_evidence_judge_candidates(
            section=section,
            reusable_blocks=reusable_blocks,
        )
        deterministic_dropped = list(deterministic_trace.get("dropped") or [])
        if deterministic_trace.get("dropped_count"):
            reusable_blocks = deterministic_blocks
            if not reusable_blocks:
                return [], {
                    **base_trace,
                    "status": "applied",
                    "reason": reason,
                    "input_count": original_input_count,
                    "candidate_count": 0,
                    "judged_count": 0,
                    "kept_count": 0,
                    "dropped_count": int(deterministic_trace.get("dropped_count") or 0),
                    "dropped": deterministic_dropped[:8],
                    "deterministic_prefilter": deterministic_trace,
                }
        candidates = _build_evidence_judge_candidates(
            reusable_blocks,
            limit=min(self.evidence_judge_max_candidates, len(reusable_blocks)),
        )
        if not candidates:
            return reusable_blocks, {**base_trace, "reason": "no_candidates"}
        system_prompt, user_prompt = _build_evidence_judge_prompts(
            section=section,
            global_params=global_params,
            candidates=candidates,
        )
        try:
            response = await self.executor.llm_client.invoke(
                LLMRequest(
                    task_type=TaskType.EVIDENCE_JUDGE,
                    session_id=f"{task_id}-evidence-judge",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.0,
                    max_tokens=900,
                    json_schema=EVIDENCE_JUDGE_SCHEMA,
                    metadata={
                        "section": section,
                        "target_taxonomy": infer_target_taxonomy(section),
                        "candidates": candidates,
                    },
                )
            )
            decisions = _parse_evidence_judge_decisions(response.content)
            filtered_blocks, trace = _apply_evidence_judge_decisions(
                reusable_blocks=reusable_blocks,
                candidates=candidates,
                decisions=decisions,
            )
            if trace.get("status") == "applied":
                trace["model_used"] = response.model_used
            if deterministic_trace.get("dropped_count"):
                trace = {
                    **trace,
                    "input_count": original_input_count,
                    "kept_count": len(filtered_blocks),
                    "dropped_count": int(trace.get("dropped_count") or 0)
                    + int(deterministic_trace.get("dropped_count") or 0),
                    "dropped": [*deterministic_dropped, *list(trace.get("dropped") or [])][:8],
                    "deterministic_prefilter": deterministic_trace,
                }
            return filtered_blocks, {
                **base_trace,
                **trace,
                "reason": reason,
            }
        except Exception as exc:  # noqa: BLE001
            return reusable_blocks, {
                **base_trace,
                "status": "fallback_error",
                "error": str(exc),
                "kept_count": len(reusable_blocks),
                "dropped_count": 0,
            }

    async def _prepare_single_section_retrieval_payload(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        index: int,
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
        task_id: str,
        run_evidence_judge: bool = True,
        run_runtime_vision_gate: bool = True,
    ) -> dict[str, Any]:
        if _can_short_circuit_parameter_snapshot_retrieval(section=section, global_params=global_params):
            parameter_evidence_candidates = _collect_parameter_evidence_candidates(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            context, citations = _merge_parameter_evidence_context(
                context="",
                citations=[],
                parameter_evidence_candidates=parameter_evidence_candidates,
            )
            evidence_judge_trace = {
                "mode": self.evidence_judge_mode,
                "status": "skipped",
                "reason": "parameter_snapshot_short_circuit",
                "input_count": 0,
                "kept_count": 0,
                "dropped_count": 0,
            }
            asset_trace = _build_asset_retrieval_trace(
                query=build_section_asset_query(section=section, global_params=global_params),
                asset_types=build_section_asset_types(section),
                skipped_optional_search=True,
                recommended_assets=[],
                asset_candidates=[],
                diagnostics={"skipped_reason": "parameter_snapshot_short_circuit"},
            )
            return {
                "retrieval_section": section,
                "index": index,
                "context": context,
                "citations": citations,
                "evidence_trace": _build_evidence_retrieval_trace(citations=citations),
                "case_matches": [],
                "case_trace": {
                    "query": "",
                    "query_intents": {},
                    "section_candidates": [],
                    "scoped_sections": [],
                    "parameter_snapshot_short_circuit": True,
                    "timings_ms": {"total": 0},
                },
                "reusable_blocks": [],
                "parameter_evidence_candidates": parameter_evidence_candidates,
                "evidence_judge_trace": evidence_judge_trace,
                "recommended_assets": [],
                "asset_trace": asset_trace,
                "asset_candidates": [],
            }
        knowledge_retrieval_bundle = self._build_knowledge_wiki_retrieval_bundle(
            section=section,
            global_params=global_params,
        )
        knowledge_wiki_terms = list(knowledge_retrieval_bundle.get("query_expansion_terms") or [])
        context, citations = build_section_context(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        case_library_result = self._retrieve_case_library_matches(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        reusable_blocks = build_reusable_blocks(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
            case_library_matches=case_library_result.get("matches") or [],
            extra_query_terms=knowledge_wiki_terms,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
            limit=self.section_reuse_full_section_block_limit,
        )
        reusable_blocks = self._expand_reusable_blocks_from_neighbors(
            section=section,
            reusable_blocks=reusable_blocks,
            global_params=global_params,
            limit=self.section_reuse_full_section_block_limit,
            extra_query_terms=knowledge_wiki_terms,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
        if run_evidence_judge:
            reusable_blocks, evidence_judge_trace = await self._filter_reusable_blocks_with_evidence_judge(
                task_id=task_id,
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
            )
        else:
            evidence_judge_trace = {
                "mode": self.evidence_judge_mode,
                "status": "skipped",
                "reason": "deferred_to_parent_section",
                "input_count": len(reusable_blocks),
                "kept_count": len(reusable_blocks),
                "dropped_count": 0,
            }
        context, citations = _sync_context_after_evidence_judge(
            context=context,
            citations=citations,
            reusable_blocks=reusable_blocks,
            evidence_judge_trace=evidence_judge_trace,
        )
        parameter_evidence_candidates = _collect_parameter_evidence_candidates(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        context, citations = _merge_parameter_evidence_context(
            context=context,
            citations=citations,
            parameter_evidence_candidates=parameter_evidence_candidates,
        )
        evidence_trace = _build_evidence_retrieval_trace(citations=citations)
        recommended_assets, asset_trace, asset_candidates = await self._search_recommended_assets(
            session=session,
            project_id=project_id,
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
            run_runtime_vision_gate=run_runtime_vision_gate,
        )
        return {
            "retrieval_section": section,
            "index": index,
            "context": context,
            "citations": citations,
            "evidence_trace": evidence_trace,
            "case_matches": case_library_result.get("matches") or [],
            "case_trace": case_library_result.get("trace") or {},
            "reusable_blocks": reusable_blocks,
            "parameter_evidence_candidates": parameter_evidence_candidates,
            "evidence_judge_trace": evidence_judge_trace,
            "recommended_assets": recommended_assets,
            "asset_trace": asset_trace,
            "asset_candidates": asset_candidates,
        }

    async def _prepare_retrieval_payload(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        index: int,
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        retrieval_sections = _build_child_retrieval_sections(section)
        if len(retrieval_sections) <= 1:
            payload = await self._prepare_single_section_retrieval_payload(
                session=session,
                project_id=project_id,
                section=section,
                index=index,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                task_id=f"{project_id}-{section.get('section_id') or index + 1}",
            )
            reuse_pack = build_reuse_pack(
                section=section,
                global_params=global_params,
                reusable_blocks=payload["reusable_blocks"],
                recommended_assets=payload["recommended_assets"],
                asset_candidates=payload["asset_candidates"],
                parameter_evidence_candidates=payload["parameter_evidence_candidates"],
                retrieval_trace=_merge_evidence_judge_trace(payload["case_trace"], payload["evidence_judge_trace"]),
            )
            return {
                "context": payload["context"],
                "citations": payload["citations"],
                "evidence_trace": payload["evidence_trace"],
                "asset_trace": payload["asset_trace"],
                "recommended_assets": payload["recommended_assets"],
                "asset_candidates": payload["asset_candidates"],
                "reusable_blocks": payload["reusable_blocks"],
                "reuse_pack": reuse_pack,
            }

        child_results: list[dict[str, Any]] = []
        for child_index, retrieval_section in enumerate(retrieval_sections, start=1):
            child_results.append(
                await self._prepare_single_section_retrieval_payload(
                    session=session,
                    project_id=project_id,
                    section=retrieval_section,
                    index=child_index,
                    evidence_bundle=evidence_bundle,
                    global_params=global_params,
                    task_id=f"{project_id}-{section.get('section_id') or index + 1}-{retrieval_section.get('section_id') or child_index}",
                    run_evidence_judge=False,
                    run_runtime_vision_gate=False,
                )
            )

        reusable_blocks = _merge_child_reusable_blocks(
            child_results,
            limit=self.section_reuse_full_section_block_limit,
        )
        child_aware_section = dict(section)
        child_aware_section["child_aware_retrieval"] = True
        reusable_blocks, parent_evidence_judge_trace = await self._filter_reusable_blocks_with_evidence_judge(
            task_id=f"{project_id}-{section.get('section_id') or index + 1}-merged",
            section=child_aware_section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
        )
        recommended_assets, asset_candidates, asset_trace = _merge_child_assets(
            child_results,
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
        )
        if _section_needs_figure_asset(section) and asset_candidates:
            query = build_section_asset_query(section=section, global_params=global_params)
            asset_candidates, vision_gate_trace = await self._vision_gate_asset_candidates(
                session=session,
                assets=asset_candidates,
                section=section,
                query=query,
            )
            diagnostics = dict(asset_trace.get("diagnostics") if isinstance(asset_trace.get("diagnostics"), dict) else {})
            if vision_gate_trace:
                diagnostics["runtime_vision_gate"] = vision_gate_trace
            if vision_gate_trace.get("status") == "reviewed":
                gated_recommendations = [
                    asset
                    for asset in asset_candidates
                    if _runtime_asset_gate_allows_recommendation(asset)
                ]
                if gated_recommendations:
                    recommended_assets = prioritize_recommended_assets(
                        gated_recommendations,
                        limit=ASSET_RECOMMENDATION_LIMIT,
                    )
                    recommended_assets = tighten_recommended_assets_for_reuse(
                        recommended_assets,
                        section=section,
                        reusable_blocks=reusable_blocks,
                        limit=ASSET_RECOMMENDATION_LIMIT,
                    )
                else:
                    recommended_assets = []
                    diagnostics["runtime_vision_gate_suppressed_recommendations"] = True
            if not any(str(asset.get("asset_type") or "").lower() == "figure" for asset in recommended_assets):
                diagnostics["missing_figure_asset"] = True
                diagnostics.setdefault("missing_figure_reason", "no_usable_figure_candidate_after_parent_gate")
            asset_trace = _build_asset_retrieval_trace(
                query=query,
                asset_types=build_section_asset_types(section),
                skipped_optional_search=False,
                recommended_assets=recommended_assets,
                asset_candidates=asset_candidates,
                search_trace=asset_trace.get("search_trace") if isinstance(asset_trace.get("search_trace"), dict) else {},
                diagnostics=diagnostics,
            )
            asset_trace["child_aware"] = True
        citations = _dedupe_citations([citation for result in child_results for citation in (result.get("citations") or [])])
        context = _merge_child_contexts(child_results)
        case_trace = _merge_child_case_retrieval_trace(child_results)
        evidence_judge_trace = _merge_child_evidence_judge_traces(child_results)
        evidence_judge_trace["parent_trace"] = parent_evidence_judge_trace
        evidence_judge_trace["status"] = parent_evidence_judge_trace.get("status") or evidence_judge_trace.get("status")
        evidence_judge_trace["reason"] = parent_evidence_judge_trace.get("reason") or evidence_judge_trace.get("reason")
        evidence_judge_trace["kept_count"] = parent_evidence_judge_trace.get("kept_count", evidence_judge_trace.get("kept_count"))
        evidence_judge_trace["dropped_count"] = parent_evidence_judge_trace.get(
            "dropped_count",
            evidence_judge_trace.get("dropped_count"),
        )
        context, citations = _sync_context_after_evidence_judge(
            context=context,
            citations=citations,
            reusable_blocks=reusable_blocks,
            evidence_judge_trace=evidence_judge_trace,
        )
        parameter_evidence_candidates = _merge_parameter_evidence_candidates(child_results)
        context, citations = _merge_parameter_evidence_context(
            context=context,
            citations=citations,
            parameter_evidence_candidates=parameter_evidence_candidates,
        )
        evidence_trace = _merge_child_evidence_trace(citations, child_results)
        reuse_pack = build_reuse_pack(
            section=child_aware_section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
            asset_candidates=asset_candidates,
            parameter_evidence_candidates=parameter_evidence_candidates,
            retrieval_trace=_merge_evidence_judge_trace(case_trace, evidence_judge_trace),
        )
        return {
            "context": context,
            "citations": citations,
            "evidence_trace": evidence_trace,
            "asset_trace": asset_trace,
            "recommended_assets": recommended_assets,
            "asset_candidates": asset_candidates,
            "reusable_blocks": reusable_blocks,
            "parameter_evidence_candidates": parameter_evidence_candidates,
            "reuse_pack": reuse_pack,
        }

    async def _prepare_section_generation_item(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        sections: list[dict[str, Any]],
        index: int,
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        payload = await self._prepare_retrieval_payload(
            session=session,
            project_id=project_id,
            section=section,
            index=index,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
        )
        return {
            "index": index,
            "section": section,
            "generation_mode": str(section.get("generation_mode") or "baseline"),
            "context": payload["context"],
            "citations": payload["citations"],
            "evidence_trace": payload["evidence_trace"],
            "asset_trace": payload["asset_trace"],
            "recommended_assets": payload["recommended_assets"],
            "asset_candidates": payload["asset_candidates"],
            "reusable_blocks": payload["reusable_blocks"],
            "reuse_pack": payload["reuse_pack"],
            "preceding_context": _build_fast_parallel_section_context(sections=sections, current_index=index),
        }

    async def _generate_prepared_section_item(
        self,
        *,
        job: Job,
        outline: ProposalOutline,
        outline_title: str,
        global_params: dict[str, Any],
        item: dict[str, Any],
    ) -> dict[str, Any]:
        section = item["section"]
        content_md, draft_status, citations, generation_details = await self._generate_section_content(
            task_id=f"{job.id}-{section.get('section_id') or item['index'] + 1}",
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            retrieved_context=item["context"],
            citations=item["citations"],
            recommended_assets=item["recommended_assets"],
            reusable_blocks=item["reusable_blocks"],
            reuse_pack=item["reuse_pack"],
            preceding_context=item["preceding_context"],
        )
        generation_details = {
            **generation_details,
            "parallel_generation": True,
            "retrieval_trace": _build_composition_retrieval_trace(
                evidence_trace=item["evidence_trace"],
                asset_trace=item["asset_trace"],
                reuse_pack=item["reuse_pack"],
                generation_details=generation_details,
            ),
        }
        quality_gate_result: dict[str, Any] = {}
        if draft_status == "generated":
            if self.section_generation_quality_gate == "skip":
                draft_status = "review_required"
                quality_gate_result = {
                    "status": "skipped_fast_mode",
                    "summary": "MVP 快速生成模式跳过逐章 LLM 质量修复，保留人工复核状态。",
                    "rewrite_attempted": False,
                    "rewrite_applied": False,
                }
            else:
                content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                    task_id=str(job.id),
                    section=_section_with_customer_facing_title(section),
                    outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                    global_params=global_params,
                    content_md=content_md,
                    recommended_assets=item["recommended_assets"],
                    allow_rewrite=True,
                )
        return {
            **item,
            "content_md": content_md,
            "draft_status": draft_status,
            "citations": citations,
            "generation_details": generation_details,
            "quality_gate_result": quality_gate_result,
        }

    async def _generate_sections_parallel(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        job: Job,
        outline: ProposalOutline,
        sections: list[dict[str, Any]],
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
        outline_title: str,
        draft_version: int,
        job_id: UUID | None,
    ) -> tuple[list[SectionDraft], list[dict[str, Any]]]:
        total_sections = len(sections)
        prepare_semaphore = asyncio.Semaphore(self.section_generation_concurrency)

        async def _prepare_item(index: int, section: dict[str, Any]) -> dict[str, Any]:
            async with prepare_semaphore:
                async with get_session_factory()() as prepare_session:
                    return await self._prepare_section_generation_item(
                        session=prepare_session,
                        project_id=project_id,
                        section=section,
                        sections=sections,
                        index=index,
                        evidence_bundle=evidence_bundle,
                        global_params=global_params,
                    )

        prepare_tasks = [
            asyncio.create_task(_prepare_item(index, section))
            for index, section in enumerate(sections)
        ]
        prepared_by_index: dict[int, dict[str, Any]] = {}
        try:
            for completed_task in asyncio.as_completed(prepare_tasks):
                item = await completed_task
                index = int(item["index"])
                prepared_by_index[index] = item
                section = item["section"]
                job.output_ref = {
                    "progress": {
                        "stage": "preparing_parallel",
                        "completed_sections": 0,
                        "prepared_sections": len(prepared_by_index),
                        "total_sections": total_sections,
                        "granularity": self.section_generation_granularity,
                        "concurrency": self.section_generation_concurrency,
                        "quality_gate": self.section_generation_quality_gate,
                        "current_section_index": index + 1,
                        "current_section_id": str(section.get("section_id") or ""),
                        "current_section_title": str(section.get("title") or "未命名章节"),
                    }
                }
                if job_id is not None:
                    await session.commit()
        except Exception:
            for task in prepare_tasks:
                task.cancel()
            raise
        prepared_items = [prepared_by_index[index] for index in range(total_sections)]

        semaphore = asyncio.Semaphore(self.section_generation_concurrency)

        async def _run_item(item: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._generate_prepared_section_item(
                    job=job,
                    outline=outline,
                    outline_title=outline_title,
                    global_params=global_params,
                    item=item,
                )

        tasks = [asyncio.create_task(_run_item(item)) for item in prepared_items]
        results_by_index: dict[int, dict[str, Any]] = {}
        try:
            for completed_task in asyncio.as_completed(tasks):
                result = await completed_task
                index = int(result["index"])
                results_by_index[index] = result
                completed_count = len(results_by_index)
                section = result["section"]
                job.output_ref = {
                    "progress": {
                        "stage": "generating_parallel",
                        "completed_sections": completed_count,
                        "total_sections": total_sections,
                        "granularity": self.section_generation_granularity,
                        "concurrency": self.section_generation_concurrency,
                        "quality_gate": self.section_generation_quality_gate,
                        "current_section_index": index + 1,
                        "current_section_id": str(section.get("section_id") or ""),
                        "current_section_title": str(section.get("title") or "未命名章节"),
                    }
                }
                if job_id is not None:
                    await session.commit()
        except Exception:
            for task in tasks:
                task.cancel()
            raise

        ordered_results = [results_by_index[index] for index in range(total_sections)]
        if self.section_generation_fast_coherence_pass:
            deduped_contents, coherence_summary = _dedupe_parallel_section_paragraphs(
                contents=[str(item.get("content_md") or "") for item in ordered_results],
            )
            for result, content_md in zip(ordered_results, deduped_contents, strict=True):
                result["content_md"] = content_md
                result["generation_details"] = {
                    **(result.get("generation_details") or {}),
                    "coherence_pass": coherence_summary,
                }

        generated_drafts: list[SectionDraft] = []
        generation_metrics: list[dict[str, Any]] = []
        draft_version = await _ensure_available_section_draft_version(
            session=session,
            project_id=project_id,
            requested_version=draft_version,
        )
        for result in ordered_results:
            section = result["section"]
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=clean_customer_facing_section_title(str(section.get("title") or "未命名章节")),
                content_md=str(result.get("content_md") or ""),
                citation_refs=result.get("citations") or [],
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status=str(result.get("draft_status") or "review_required"),
                validator_result={
                    "recommended_assets": result.get("recommended_assets") or [],
                    "asset_candidates": result.get("asset_candidates") or [],
                    "generation_mode": result.get("generation_mode") or "baseline",
                    "reuse_pack": result.get("reuse_pack") or {},
                    "generation_details": result.get("generation_details") or {},
                    "quality_gate": result.get("quality_gate_result") or {},
                },
            )
            session.add(draft)
            generated_drafts.append(draft)
            generation_metrics.append(
                _make_generation_metric(
                    section_id=str(section.get("section_id") or ""),
                    draft_status=draft.status,
                    generation_details=result.get("generation_details") or {},
                    quality_gate_result=result.get("quality_gate_result") or {},
                )
            )
        return generated_drafts, generation_metrics

    async def generate_sections(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> tuple[Job, list[SectionDraft]]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before generating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)

        sections = build_generation_sections(
            ((outline.outline_json or {}).get("sections") or []),
            granularity=self.section_generation_granularity,
        )
        if not sections:
            raise ArtifactValidationError("Outline has no sections")

        if job_id is None:
            job = Job(
                project_id=project_id,
                job_type="generate",
                status="running",
                trace_id=uuid.uuid4().hex,
                input_ref={
                    "project_id": str(project_id),
                    "outline_id": str(outline.id),
                    "section_generation_granularity": self.section_generation_granularity,
                },
                started_at=datetime.now(timezone.utc),
            )
            session.add(job)
            await session.flush()
        else:
            job = await session.get(Job, job_id)
            if not job:
                raise ArtifactNotFoundError("Generate job not found")
            if job.project_id != project_id or job.job_type != "generate":
                raise ArtifactValidationError("Generate job does not belong to this project")
            job.status = "running"
            job.error_code = None
            job.started_at = datetime.now(timezone.utc)
            job.completed_at = None
            job.input_ref = {
                "project_id": str(project_id),
                "outline_id": str(outline.id),
                "section_generation_granularity": self.section_generation_granularity,
            }
            job.output_ref = {
                "progress": {
                    "stage": "starting",
                    "completed_sections": 0,
                    "total_sections": len(sections),
                    "granularity": self.section_generation_granularity,
                }
            }
            await session.commit()

        existing_max_draft_version = await session.scalar(
            select(func.max(SectionDraft.draft_version)).where(SectionDraft.project_id == project_id)
        )
        draft_version = _compute_next_section_draft_version(
            current_draft_version=project.current_draft_version,
            existing_max_draft_version=existing_max_draft_version,
        )
        generated_drafts: list[SectionDraft] = []
        generation_metrics: list[dict[str, Any]] = []
        global_params = build_section_global_params(requirement_card.content)
        outline_title = (outline.outline_json or {}).get("title", "技术方案")
        inter_section_state = _new_inter_section_state(
            task_id=str(job.id),
            outline_title=outline_title,
            global_params=global_params,
        )
        covered_topics: dict[int, str] = {}
        parallel_generation_enabled = self._should_use_parallel_generation(sections)

        if parallel_generation_enabled:
            generated_drafts, generation_metrics = await self._generate_sections_parallel(
                session=session,
                project_id=project_id,
                job=job,
                outline=outline,
                sections=sections,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                outline_title=outline_title,
                draft_version=draft_version,
                job_id=job_id,
            )
            if generated_drafts:
                draft_version = int(generated_drafts[0].draft_version or draft_version)

        if not parallel_generation_enabled:
            draft_version = await _ensure_available_section_draft_version(
                session=session,
                project_id=project_id,
                requested_version=draft_version,
            )
        for index, section in enumerate([] if parallel_generation_enabled else sections):
            section_title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
            job.output_ref = {
                "progress": {
                    "stage": "generating",
                    "completed_sections": len(generated_drafts),
                    "total_sections": len(sections),
                    "granularity": self.section_generation_granularity,
                    "current_section_index": index + 1,
                    "current_section_id": str(section.get("section_id") or ""),
                    "current_section_title": section_title,
                }
            }
            if job_id is not None:
                await session.commit()
            generation_mode = str(section.get("generation_mode") or "baseline")
            preceding_context = _build_preceding_context(
                state=inter_section_state,
                covered_topics=covered_topics,
                current_index=index,
            )
            retrieval_payload = await self._prepare_retrieval_payload(
                session=session,
                project_id=project_id,
                section=section,
                index=index,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            context = retrieval_payload["context"]
            citations = retrieval_payload["citations"]
            evidence_trace = retrieval_payload["evidence_trace"]
            asset_trace = retrieval_payload["asset_trace"]
            recommended_assets = retrieval_payload["recommended_assets"]
            asset_candidates = retrieval_payload["asset_candidates"]
            reusable_blocks = retrieval_payload["reusable_blocks"]
            reuse_pack = retrieval_payload["reuse_pack"]
            content_md, draft_status, citations, generation_details = await self._generate_section_content(
                task_id=str(job.id),
                section=section,
                outline_title=outline_title,
                global_params=global_params,
                retrieved_context=context,
                citations=citations,
                recommended_assets=recommended_assets,
                reusable_blocks=reusable_blocks,
                reuse_pack=reuse_pack,
                preceding_context=preceding_context,
            )
            generation_details = {
                **generation_details,
                "retrieval_trace": _build_composition_retrieval_trace(
                    evidence_trace=evidence_trace,
                    asset_trace=asset_trace,
                    reuse_pack=reuse_pack,
                    generation_details=generation_details,
                ),
            }
            quality_gate_result: dict[str, Any] = {}
            if draft_status == "generated":
                if self.section_generation_quality_gate == "skip":
                    draft_status = "review_required"
                    quality_gate_result = {
                        "status": "skipped_fast_mode",
                        "summary": "MVP 快速生成模式跳过逐章 LLM 质量修复，保留人工复核状态。",
                        "rewrite_attempted": False,
                        "rewrite_applied": False,
                    }
                else:
                    job.output_ref = {
                        "progress": {
                            "stage": "quality_gate",
                            "completed_sections": len(generated_drafts),
                            "total_sections": len(sections),
                            "current_section_index": index + 1,
                            "current_section_id": str(section.get("section_id") or ""),
                            "current_section_title": section_title,
                        }
                    }
                    if job_id is not None:
                        await session.commit()
                    content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                        task_id=str(job.id),
                        section=_section_with_customer_facing_title(section),
                        outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                        global_params=global_params,
                        content_md=content_md,
                        recommended_assets=recommended_assets,
                        allow_rewrite=True,
                    )
            _record_inter_section_context(
                state=inter_section_state,
                covered_topics=covered_topics,
                section_index=index,
                section_title=clean_customer_facing_section_title(str(section.get("title") or "未命名章节")),
                draft_status=draft_status,
                content_md=content_md,
            )
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=clean_customer_facing_section_title(str(section.get("title") or "未命名章节")),
                content_md=content_md,
                citation_refs=citations,
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status=draft_status,
                validator_result={
                    "recommended_assets": recommended_assets,
                    "asset_candidates": asset_candidates,
                    "generation_mode": generation_mode,
                    "reuse_pack": reuse_pack,
                    "generation_details": generation_details,
                    "quality_gate": quality_gate_result,
                },
            )
            session.add(draft)
            generated_drafts.append(draft)
            generation_metrics.append(
                _make_generation_metric(
                    section_id=str(section.get("section_id") or ""),
                    draft_status=draft_status,
                    generation_details=generation_details,
                    quality_gate_result=quality_gate_result,
                )
            )
            if job_id is not None:
                job.output_ref = {
                    "progress": {
                        "stage": "section_completed",
                        "completed_sections": len(generated_drafts),
                        "total_sections": len(sections),
                        "current_section_index": index + 1,
                        "current_section_id": str(section.get("section_id") or ""),
                        "current_section_title": section_title,
                    }
                }
                await session.commit()

        project.current_draft_version = draft_version
        project.status = _derive_project_draft_status(generated_drafts)
        job.status = "succeeded"
        generation_summary = _build_generation_summary(generation_metrics)
        generation_summary["parallel_generation"] = parallel_generation_enabled
        generation_summary["section_generation_concurrency"] = (
            self.section_generation_concurrency if parallel_generation_enabled else 1
        )
        generation_summary["section_generation_quality_gate"] = self.section_generation_quality_gate
        generation_summary["section_generation_granularity"] = self.section_generation_granularity
        generation_summary["fast_coherence_pass"] = bool(
            parallel_generation_enabled and self.section_generation_fast_coherence_pass
        )
        job.output_ref = {
            "draft_version": draft_version,
            "section_count": len(generated_drafts),
            "generation_summary": generation_summary,
        }
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        for draft in generated_drafts:
            await session.refresh(draft)
        await session.refresh(job)
        return job, generated_drafts

    async def create_generate_sections_job(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None = None,
    ) -> Job:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before generating sections")

        sections = build_generation_sections(
            ((outline.outline_json or {}).get("sections") or []),
            granularity=self.section_generation_granularity,
        )
        if not sections:
            raise ArtifactValidationError("Outline has no sections")

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="queued",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "outline_id": str(outline.id),
                "section_count": len(sections),
                "section_generation_granularity": self.section_generation_granularity,
                "section_generation_concurrency": self.section_generation_concurrency,
                "section_generation_quality_gate": self.section_generation_quality_gate,
                "evidence_judge_mode": self.evidence_judge_mode,
            },
            output_ref={
                "progress": {
                    "stage": "queued",
                    "completed_sections": 0,
                    "total_sections": len(sections),
                    "granularity": self.section_generation_granularity,
                }
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def list_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int | None = None,
    ) -> list[SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        target_draft_version = draft_version or int(project.current_draft_version or 0)
        if target_draft_version <= 0:
            return []

        drafts = await self._load_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=target_draft_version,
        )
        if not drafts:
            return []

        return self._sort_drafts_by_outline(
            drafts=drafts,
            outline=await self._resolve_outline(session=session, project_id=project_id, outline_id=project.current_outline_id),
        )

    async def create_regenerate_section_job(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        outline_id: UUID | None = None,
        preferred_citation_ids: list[str] | None = None,
    ) -> Job:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before regenerating sections")
        section = self._find_section(outline=outline, section_id=section_id)
        await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )

        section_title = clean_customer_facing_section_title(str(section.get("title") or ""))
        job = Job(
            project_id=project_id,
            job_type="generate_section",
            status="queued",
            trace_id=uuid.uuid4().hex,
            input_ref={
                "project_id": str(project_id),
                "outline_id": str(outline.id),
                "section_id": section_id,
                "section_title": section_title,
                "draft_version": project.current_draft_version,
                "preferred_citation_ids": sorted(_normalize_preferred_citation_ids(preferred_citation_ids)),
                "section_generation_quality_gate": self.section_generation_quality_gate,
                "evidence_judge_mode": self.evidence_judge_mode,
            },
            output_ref={
                "progress": {
                    "stage": "queued",
                    "completed_sections": 0,
                    "total_sections": 1,
                    "current_section_id": section_id,
                    "current_section_title": section_title,
                }
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def regenerate_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        outline_id: UUID | None = None,
        preferred_citation_ids: list[str] | None = None,
        job_id: UUID | None = None,
    ) -> tuple[Job, SectionDraft]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before regenerating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)
        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        section = self._find_section(outline=outline, section_id=section_id)
        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )

        generation_mode = str(section.get("generation_mode") or "baseline")
        normalized_preferred_citation_ids = _normalize_preferred_citation_ids(preferred_citation_ids)
        global_params = build_section_global_params(requirement_card.content)
        outline_title = (outline.outline_json or {}).get("title", "技术方案")
        section_title = clean_customer_facing_section_title(str(section.get("title") or ""))
        job_started_monotonic = time.perf_counter()

        if job_id is None:
            job = Job(
                project_id=project_id,
                job_type="generate_section",
                status="running",
                trace_id=uuid.uuid4().hex,
                input_ref={
                    "project_id": str(project_id),
                    "outline_id": str(outline.id),
                    "section_id": section_id,
                    "section_title": section_title,
                    "draft_version": project.current_draft_version,
                    "preferred_citation_ids": sorted(normalized_preferred_citation_ids),
                    "section_generation_quality_gate": self.section_generation_quality_gate,
                    "evidence_judge_mode": self.evidence_judge_mode,
                },
                output_ref={
                    "progress": {
                        "stage": "starting",
                        "completed_sections": 0,
                        "total_sections": 1,
                        "current_section_id": section_id,
                        "current_section_title": section_title,
                    }
                },
                started_at=datetime.now(timezone.utc),
            )
            session.add(job)
            await session.flush()
        else:
            job = await session.get(Job, job_id)
            if not job:
                raise ArtifactNotFoundError("Generate job not found")
            if job.project_id != project_id or job.job_type not in {"generate", "generate_section"}:
                raise ArtifactValidationError("Generate job does not belong to this project")
            job.status = "running"
            job.error_code = None
            job.started_at = datetime.now(timezone.utc)
            job.completed_at = None
            job.input_ref = {
                "project_id": str(project_id),
                "outline_id": str(outline.id),
                "section_id": section_id,
                "section_title": section_title,
                "draft_version": project.current_draft_version,
                "preferred_citation_ids": sorted(normalized_preferred_citation_ids),
                "section_generation_quality_gate": self.section_generation_quality_gate,
                "evidence_judge_mode": self.evidence_judge_mode,
            }
            job.output_ref = {
                "progress": {
                    "stage": "starting",
                    "completed_sections": 0,
                    "total_sections": 1,
                    "current_section_id": section_id,
                    "current_section_title": section_title,
                }
            }
            await session.commit()

        async def _update_job_progress(stage: str, **extra: Any) -> None:
            completed_sections = int(extra.pop("completed_sections", 0))
            elapsed_ms = int((time.perf_counter() - job_started_monotonic) * 1000)
            job.output_ref = {
                **(job.output_ref or {}),
                "progress": {
                    "stage": stage,
                    "completed_sections": completed_sections,
                    "total_sections": 1,
                    "current_section_id": section_id,
                    "current_section_title": section_title,
                    "elapsed_ms": elapsed_ms,
                    **extra,
                },
            }
            await session.commit()

        await _update_job_progress("retrieving_evidence")
        if _can_short_circuit_parameter_snapshot_retrieval(section=section, global_params=global_params):
            preceding_context = ""
            context = ""
            citations = []
            reusable_blocks = []
            await _update_job_progress("collecting_parameter_evidence", reusable_block_count=0)
            parameter_evidence_candidates = _collect_parameter_evidence_candidates(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            context, citations = _merge_parameter_evidence_context(
                context=context,
                citations=citations,
                parameter_evidence_candidates=parameter_evidence_candidates,
            )
            evidence_trace = _build_evidence_retrieval_trace(
                citations=citations,
                preferred_evidence_ids=normalized_preferred_citation_ids,
            )
            evidence_judge_trace = {
                "mode": self.evidence_judge_mode,
                "status": "skipped",
                "reason": "parameter_snapshot_short_circuit",
                "input_count": 0,
                "kept_count": 0,
                "dropped_count": 0,
            }
            case_library_result = {
                "matches": [],
                "trace": {
                    "query": "",
                    "query_intents": {},
                    "section_candidates": [],
                    "scoped_sections": [],
                    "parameter_snapshot_short_circuit": True,
                    "timings_ms": {"total": 0},
                },
            }
            recommended_assets = []
            asset_candidates = []
            asset_trace = _build_asset_retrieval_trace(
                query=build_section_asset_query(section=section, global_params=global_params),
                asset_types=build_section_asset_types(section),
                skipped_optional_search=True,
                recommended_assets=[],
                asset_candidates=[],
                diagnostics={"skipped_reason": "parameter_snapshot_short_circuit"},
            )
        else:
            knowledge_retrieval_bundle = self._build_knowledge_wiki_retrieval_bundle(
                section=section,
                global_params=global_params,
            )
            knowledge_wiki_terms = list(knowledge_retrieval_bundle.get("query_expansion_terms") or [])
            existing_drafts = await self._load_section_drafts(
                session=session,
                project_id=project_id,
                draft_version=project.current_draft_version,
            )
            preceding_context = _build_preceding_context_from_existing_drafts(
                task_id=str(job.id),
                outline_title=outline_title,
                global_params=global_params,
                sections=sections,
                current_section_id=section_id,
                existing_drafts=existing_drafts,
            )
            await _update_job_progress("retrieving_context")
            context, citations = build_section_context(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                preferred_evidence_ids=normalized_preferred_citation_ids,
            )
            await _update_job_progress("retrieving_case_library")
            case_library_result = self._retrieve_case_library_matches(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            await _update_job_progress("building_reuse_blocks")
            reusable_blocks = build_reusable_blocks(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
                case_library_matches=case_library_result.get("matches") or [],
                extra_query_terms=knowledge_wiki_terms,
                knowledge_retrieval_bundle=knowledge_retrieval_bundle,
                limit=self.section_reuse_full_section_block_limit,
            )
            reusable_blocks = prioritize_reusable_blocks_for_citations(
                reusable_blocks,
                preferred_citation_ids=preferred_citation_ids,
            )
            reusable_blocks = self._expand_reusable_blocks_from_neighbors(
                section=section,
                reusable_blocks=reusable_blocks,
                global_params=global_params,
                limit=self.section_reuse_full_section_block_limit,
                extra_query_terms=knowledge_wiki_terms,
                knowledge_retrieval_bundle=knowledge_retrieval_bundle,
            )
            reusable_blocks = prioritize_reusable_blocks_for_citations(
                reusable_blocks,
                preferred_citation_ids=preferred_citation_ids,
            )
            await _update_job_progress("judging_evidence", reusable_block_count=len(reusable_blocks))
            reusable_blocks, evidence_judge_trace = await self._filter_reusable_blocks_with_evidence_judge(
                task_id=f"{job.id}-{section_id}",
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
            )
            context, citations = _sync_context_after_evidence_judge(
                context=context,
                citations=citations,
                reusable_blocks=reusable_blocks,
                evidence_judge_trace=evidence_judge_trace,
            )
            await _update_job_progress(
                "collecting_parameter_evidence",
                reusable_block_count=len(reusable_blocks),
                evidence_judge_status=evidence_judge_trace.get("status"),
            )
            parameter_evidence_candidates = _collect_parameter_evidence_candidates(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            context, citations = _merge_parameter_evidence_context(
                context=context,
                citations=citations,
                parameter_evidence_candidates=parameter_evidence_candidates,
            )
            evidence_trace = _build_evidence_retrieval_trace(
                citations=citations,
                preferred_evidence_ids=normalized_preferred_citation_ids,
            )
            await _update_job_progress(
                "searching_assets",
                reusable_block_count=len(reusable_blocks),
                parameter_evidence_count=len(parameter_evidence_candidates),
            )
            recommended_assets, asset_trace, asset_candidates = await self._search_recommended_assets(
                session=session,
                project_id=project_id,
                section=section,
                global_params=global_params,
                reusable_blocks=reusable_blocks,
            )
        reuse_pack = build_reuse_pack(
            section=section,
            global_params=global_params,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
            asset_candidates=asset_candidates,
            parameter_evidence_candidates=parameter_evidence_candidates,
            retrieval_trace=_merge_evidence_judge_trace(case_library_result.get("trace"), evidence_judge_trace),
        )
        await _update_job_progress(
            "generating_section",
            reusable_block_count=len(reusable_blocks),
            asset_candidate_count=len(asset_candidates),
            recommended_asset_count=len(recommended_assets),
        )
        content_md, draft_status, citations, generation_details = await self._generate_section_content(
            task_id=str(job.id),
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            retrieved_context=context,
            citations=citations,
            recommended_assets=recommended_assets,
            reusable_blocks=reusable_blocks,
            reuse_pack=reuse_pack,
            preceding_context=preceding_context,
        )
        generation_details = {
            **generation_details,
            "retrieval_trace": _build_composition_retrieval_trace(
                evidence_trace=evidence_trace,
                asset_trace=asset_trace,
                reuse_pack=reuse_pack,
                generation_details=generation_details,
            ),
        }
        quality_gate_result: dict[str, Any] = {}
        if draft_status == "generated":
            if self.section_generation_quality_gate == "skip":
                quality_gate_result = {
                    "status": "skipped",
                    "summary": "section generation quality gate disabled by configuration",
                    "issues": [],
                }
            else:
                await _update_job_progress("quality_gate")
                content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                    task_id=str(job.id),
                    section=_section_with_customer_facing_title(section),
                    outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                    global_params=global_params,
                    content_md=content_md,
                    recommended_assets=recommended_assets,
                    allow_rewrite=True,
                )
        draft.title = clean_customer_facing_section_title(str(section.get("title") or draft.title))
        draft.content_md = content_md
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params
        draft.status = draft_status
        draft.validator_result = {
            "recommended_assets": recommended_assets,
            "asset_candidates": asset_candidates,
            "generation_mode": generation_mode,
            "reuse_pack": reuse_pack,
            "quality_gate": quality_gate_result,
            "generation_details": {
                **generation_details,
                "selected_citation_ids": sorted(normalized_preferred_citation_ids),
            },
        }
        project.status = await self._compute_project_draft_status(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            fallback_drafts=[draft],
        )

        job.status = "succeeded"
        job.output_ref = {
            "draft_version": project.current_draft_version,
            "section_id": section_id,
            "draft_id": str(draft.id),
            "resource_id": str(draft.id),
            "progress": {
                "stage": "completed",
                "completed_sections": 1,
                "total_sections": 1,
                "current_section_id": section_id,
                "current_section_title": section_title,
                "elapsed_ms": int((time.perf_counter() - job_started_monotonic) * 1000),
            },
            "generation_summary": _build_generation_summary(
                [
                    _make_generation_metric(
                        section_id=section_id,
                        draft_status=draft_status,
                        generation_details=generation_details,
                        quality_gate_result=quality_gate_result,
                    )
                ]
            ),
        }
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)
        await session.refresh(draft)
        return job, draft

    async def update_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        content_md: str,
        citation_refs: list | None = None,
        assumptions: list | None = None,
    ) -> SectionDraft:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")
        if not project.current_draft_version:
            raise ArtifactValidationError("No draft version exists for this project")

        draft = await self._get_current_section_draft(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            section_id=section_id,
        )
        draft.content_md = content_md
        if citation_refs is not None:
            draft.citation_refs = citation_refs
        if assumptions is not None:
            draft.assumptions = assumptions
        draft.status = "edited"
        current_result = draft.validator_result if isinstance(draft.validator_result, dict) else {}
        draft.validator_result = {
            "recommended_assets": current_result.get("recommended_assets", []),
            "asset_candidates": current_result.get("asset_candidates", []),
            "generation_mode": current_result.get("generation_mode", "baseline"),
            "reuse_pack": current_result.get("reuse_pack", {}),
            "quality_gate": {
                "status": "stale",
                "summary": "manual edit pending validation",
                "issues": [],
            },
        }
        project.status = await self._compute_project_draft_status(
            session=session,
            project_id=project_id,
            draft_version=project.current_draft_version,
            fallback_drafts=[draft],
        )
        await session.commit()
        await session.refresh(draft)
        return draft

    async def _search_recommended_assets(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section: dict[str, Any],
        global_params: dict[str, Any],
        reusable_blocks: list[dict[str, Any]] | None = None,
        run_runtime_vision_gate: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        query = build_section_asset_query(section=section, global_params=global_params)
        asset_types = build_section_asset_types(section)
        if not query:
            trace = _build_asset_retrieval_trace(
                query="",
                asset_types=asset_types,
                skipped_optional_search=True,
                recommended_assets=[],
            )
            return [], trace, []
        target_taxonomy = infer_target_taxonomy(section)
        skipped_optional_search = _should_skip_optional_asset_search(
            section=section,
            target_taxonomy=target_taxonomy,
            asset_types=asset_types,
        )
        if skipped_optional_search:
            trace = _build_asset_retrieval_trace(
                query=query,
                asset_types=asset_types,
                skipped_optional_search=True,
                recommended_assets=[],
            )
            return [], trace, []
        search_context = _build_asset_search_context(section=section, reusable_blocks=reusable_blocks)
        trace_details: dict[str, Any] = {}
        diagnostics: dict[str, Any] = {}
        target_taxonomy = infer_target_taxonomy(section)
        needs_figure = _section_needs_figure_asset(section)
        figure_dominant = _section_is_figure_dominant(section, target_taxonomy=target_taxonomy)
        figure_assets: list[dict[str, Any]] = []
        figure_candidates: list[dict[str, Any]] = []

        if needs_figure:
            figure_response = await self.asset_retriever.search_project_assets(
                session=session,
                project_id=project_id,
                query=query,
                top_k=12,
                asset_types=["figure"],
                doc_types=["historical_proposal"],
                section_context=search_context,
                include_global_historical=True,
            )
            trace_details["figure_first"] = (
                figure_response.search_trace.model_dump(mode="json") if figure_response.search_trace is not None else {}
            )
            figure_assets = await self._post_process_recommended_assets(
                session=session,
                assets=[item.model_dump(mode="json") for item in figure_response.results],
                section=section,
                reusable_blocks=reusable_blocks,
            )
            figure_candidates = await self._post_process_recommended_assets(
                session=session,
                assets=[item.model_dump(mode="json") for item in figure_response.results],
                section=section,
                reusable_blocks=reusable_blocks,
                limit=ASSET_CANDIDATE_LIMIT,
            )
            if not figure_assets and _asset_context_has_anchors(search_context):
                relaxed_context = _relax_asset_search_context(search_context)
                relaxed_response = await self.asset_retriever.search_project_assets(
                    session=session,
                    project_id=project_id,
                    query=query,
                    top_k=12,
                    asset_types=["figure"],
                    doc_types=["historical_proposal"],
                    section_context=relaxed_context,
                    include_global_historical=True,
                )
                trace_details["figure_relaxed"] = (
                    relaxed_response.search_trace.model_dump(mode="json") if relaxed_response.search_trace is not None else {}
                )
                relaxed_assets = await self._post_process_recommended_assets(
                    session=session,
                    assets=[item.model_dump(mode="json") for item in relaxed_response.results],
                    section=section,
                    reusable_blocks=reusable_blocks,
                )
                relaxed_candidates = await self._post_process_recommended_assets(
                    session=session,
                    assets=[item.model_dump(mode="json") for item in relaxed_response.results],
                    section=section,
                    reusable_blocks=reusable_blocks,
                    limit=ASSET_CANDIDATE_LIMIT,
                )
                if relaxed_assets:
                    diagnostics["figure_anchor_relaxed"] = True
                    figure_assets = relaxed_assets
                    figure_candidates = relaxed_candidates

        regular_assets: list[dict[str, Any]] = []
        regular_candidates: list[dict[str, Any]] = []
        regular_response_trace: dict[str, Any] = {}
        if not figure_dominant:
            response = await self.asset_retriever.search_project_assets(
                session=session,
                project_id=project_id,
                query=query,
                top_k=12,
                asset_types=asset_types,
                doc_types=["historical_proposal"],
                section_context=search_context,
                include_global_historical=True,
            )
            regular_response_trace = response.search_trace.model_dump(mode="json") if response.search_trace is not None else {}
            regular_assets = await self._post_process_recommended_assets(
                session=session,
                assets=[item.model_dump(mode="json") for item in response.results],
                section=section,
                reusable_blocks=reusable_blocks,
            )
            regular_candidates = await self._post_process_recommended_assets(
                session=session,
                assets=[item.model_dump(mode="json") for item in response.results],
                section=section,
                reusable_blocks=reusable_blocks,
                limit=ASSET_CANDIDATE_LIMIT,
            )
        elif not figure_assets:
            diagnostics["missing_required_figure_asset"] = True
            diagnostics["missing_required_figure_reason"] = "figure_dominant_section_has_no_usable_figure_candidate"

        if figure_dominant:
            assets = figure_assets
            asset_candidates = figure_candidates
        else:
            assets = _merge_recommended_assets(figure_assets, regular_assets)
            assets = prioritize_recommended_assets(assets)
            assets = tighten_recommended_assets_for_reuse(
                assets,
                section=section,
                reusable_blocks=reusable_blocks,
            )
            asset_candidates = _merge_recommended_assets(figure_candidates, regular_candidates)
            asset_candidates = prioritize_recommended_assets(asset_candidates, limit=ASSET_CANDIDATE_LIMIT)
            asset_candidates = tighten_recommended_assets_for_reuse(
                asset_candidates,
                section=section,
                reusable_blocks=reusable_blocks,
                limit=ASSET_CANDIDATE_LIMIT,
            )
        if run_runtime_vision_gate and needs_figure and asset_candidates:
            asset_candidates, vision_gate_trace = await self._vision_gate_asset_candidates(
                session=session,
                assets=asset_candidates,
                section=section,
                query=query,
            )
            if vision_gate_trace:
                diagnostics["runtime_vision_gate"] = vision_gate_trace
            if vision_gate_trace.get("status") == "reviewed":
                gated_recommendations = [
                    asset
                    for asset in asset_candidates
                    if _runtime_asset_gate_allows_recommendation(asset)
                ]
                if gated_recommendations:
                    assets = prioritize_recommended_assets(gated_recommendations, limit=ASSET_RECOMMENDATION_LIMIT)
                    assets = tighten_recommended_assets_for_reuse(
                        assets,
                        section=section,
                        reusable_blocks=reusable_blocks,
                        limit=ASSET_RECOMMENDATION_LIMIT,
                    )
                else:
                    assets = []
                    diagnostics["runtime_vision_gate_suppressed_recommendations"] = True
        if needs_figure and not any(str(asset.get("asset_type") or "").lower() == "figure" for asset in assets):
            diagnostics["missing_figure_asset"] = True
            diagnostics.setdefault("missing_figure_reason", "no_usable_figure_candidate_after_filtering")
        trace = _build_asset_retrieval_trace(
            query=query,
            asset_types=asset_types,
            skipped_optional_search=False,
            recommended_assets=assets,
            asset_candidates=asset_candidates,
            search_trace={
                "regular": regular_response_trace,
                **trace_details,
            },
            diagnostics=diagnostics,
        )
        return assets, trace, asset_candidates

    async def _post_process_recommended_assets(
        self,
        *,
        session: AsyncSession,
        assets: list[dict[str, Any]],
        section: dict[str, Any],
        reusable_blocks: list[dict[str, Any]] | None,
        limit: int = ASSET_RECOMMENDATION_LIMIT,
    ) -> list[dict[str, Any]]:
        assets = await _enrich_table_assets_with_source_content(session=session, assets=assets)
        assets = filter_recommended_assets_for_section(assets, section=section)
        assets = prioritize_recommended_assets(assets, limit=limit)
        return tighten_recommended_assets_for_reuse(
            assets,
            section=section,
            reusable_blocks=reusable_blocks,
            limit=limit,
        )

    async def _vision_gate_asset_candidates(
        self,
        *,
        session: AsyncSession,
        assets: list[dict[str, Any]],
        section: dict[str, Any],
        query: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not assets:
            return assets, {}
        if not self.settings.runtime_asset_vision_gate_enabled:
            return assets, {"status": "disabled"}
        if not (self.settings.vision_llm_api_key and self.settings.vision_llm_base_url):
            return assets, {"status": "skipped", "reason": "vision_llm_not_configured"}

        max_assets = max(1, int(self.settings.runtime_asset_vision_gate_max_assets or 1))
        figure_candidates = [
            asset
            for asset in assets
            if str(asset.get("asset_type") or "").lower() == "figure"
        ]
        if not figure_candidates:
            return assets, {"status": "skipped", "reason": "no_figure_candidates"}
        review_candidates = [
            asset
            for asset in figure_candidates
            if _runtime_asset_gate_should_review(asset=asset, section=section)
        ][:max_assets]
        if not review_candidates:
            return assets, {"status": "skipped", "reason": "low_risk_figure_candidates"}

        input_images: list[LLMInputImage] = []
        prompt_candidates: list[dict[str, Any]] = []
        image_asset_ids: list[str] = []
        storage = get_object_storage()
        max_image_bytes = max(0, int(self.settings.runtime_asset_vision_gate_max_image_bytes or 0))
        detail = str(self.settings.runtime_asset_vision_gate_image_detail or "auto")

        for asset in review_candidates:
            asset_id = str(asset.get("asset_id") or "").strip()
            if not asset_id:
                continue
            try:
                db_asset = await session.get(FigureAsset, UUID(asset_id))
            except (TypeError, ValueError):
                continue
            if not db_asset:
                continue
            try:
                materialized = storage.materialize(db_asset.asset_uri)
                try:
                    image_bytes = materialized.path.read_bytes()
                    if max_image_bytes and len(image_bytes) > max_image_bytes:
                        continue
                    mime_type = mimetypes.guess_type(materialized.path.name)[0] or "image/png"
                    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
                finally:
                    materialized.cleanup()
            except Exception:
                continue
            input_images.append(LLMInputImage(image_url=data_url, detail=detail))
            image_asset_ids.append(asset_id)
            prompt_candidates.append(_build_runtime_asset_gate_candidate(asset))

        if not input_images:
            return assets, {"status": "skipped", "reason": "no_materialized_candidate_images"}

        request = LLMRequest(
            task_type=TaskType.ASSET_RERANK,
            system_prompt=_build_runtime_asset_gate_system_prompt(),
            user_prompt=_build_runtime_asset_gate_user_prompt(
                section=section,
                query=query,
                candidates=prompt_candidates,
                image_asset_ids=image_asset_ids,
            ),
            temperature=0.0,
            max_tokens=1800,
            json_schema=RUNTIME_ASSET_RERANK_SCHEMA,
            input_images=input_images,
            metadata={"candidates": prompt_candidates},
        )
        try:
            timeout_seconds = max(1.0, float(self.settings.runtime_asset_vision_gate_timeout_seconds or 1.0))
            async with asyncio.timeout(timeout_seconds):
                response = await self.executor.llm_client.invoke(request)
            payload = json.loads(response.content)
        except Exception as exc:
            return assets, {"status": "failed", "error": str(exc)}

        decisions = _extract_runtime_asset_gate_decisions(payload)
        if not decisions:
            return assets, {"status": "empty", "model": response.model_used, "image_count": len(input_images)}

        updated_assets: list[dict[str, Any]] = []
        reviewed_count = 0
        suppressed_count = 0
        for asset in assets:
            asset_id = str(asset.get("asset_id") or "").strip()
            decision = decisions.get(asset_id)
            if not decision:
                updated_assets.append(asset)
                continue
            reviewed_count += 1
            updated_asset = _apply_runtime_asset_gate_decision(asset=asset, decision=decision, model_used=response.model_used)
            if not _runtime_asset_gate_allows_recommendation(updated_asset):
                suppressed_count += 1
            updated_assets.append(updated_asset)

        updated_assets = prioritize_recommended_assets(updated_assets, limit=len(updated_assets))
        return updated_assets, {
            "status": "reviewed",
            "model": response.model_used,
            "image_count": len(input_images),
            "reviewed_count": reviewed_count,
            "suppressed_count": suppressed_count,
        }

    def _retrieve_case_library_matches(
        self,
        *,
        section: dict[str, Any],
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        timings: dict[str, int] = {}

        def _mark_timing(stage: str, stage_started_at: float) -> None:
            timings[stage] = int((time.perf_counter() - stage_started_at) * 1000)

        content = evidence_bundle.content if isinstance(evidence_bundle.content, dict) else {}
        case_candidates = content.get("case_candidates") or []
        sample_ids = {
            str(item.get("sample_id") or "").strip()
            for item in case_candidates
            if str(item.get("sample_id") or "").strip()
        }
        library_tracks = {
            str(item.get("library_track") or "").strip()
            for item in case_candidates
            if str(item.get("library_track") or "").strip()
        }
        knowledge_retrieval_bundle = self._build_knowledge_wiki_retrieval_bundle(
            section=section,
            global_params=global_params,
        )
        knowledge_wiki_terms = list(knowledge_retrieval_bundle.get("query_expansion_terms") or [])
        knowledge_source_documents = _collect_knowledge_wiki_source_documents(knowledge_retrieval_bundle)
        resolved_sample_ids: set[str] = set()
        resolve_sample_ids = getattr(self.case_library, "resolve_sample_ids_by_file_names", None)
        if callable(resolve_sample_ids) and knowledge_source_documents:
            stage_started_at = time.perf_counter()
            resolved_sample_ids = set(
                resolve_sample_ids(
                    knowledge_source_documents,
                    library_tracks=library_tracks or None,
                )
            )
            _mark_timing("resolve_sample_ids", stage_started_at)
            sample_ids.update(resolved_sample_ids)
        if not sample_ids:
            return {
                "matches": [],
                "trace": {
                    "query": "",
                    "query_intents": {},
                    "section_candidates": [],
                    "scoped_sections": [],
                    "knowledge_wiki_source_documents": sorted(knowledge_source_documents),
                    "knowledge_wiki_resolved_sample_ids": sorted(resolved_sample_ids),
                    "timings_ms": {**timings, "total": int((time.perf_counter() - started_at) * 1000)},
                },
            }
        query = build_section_reuse_query(
            section=section,
            global_params=global_params,
            extra_terms=knowledge_wiki_terms,
        )
        query_intents = build_section_reuse_query_intents(
            section=section,
            global_params=global_params,
            extra_terms=knowledge_wiki_terms,
        )
        stage_started_at = time.perf_counter()
        section_candidates = self.case_library.retrieve_sections(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=12 if knowledge_source_documents else 4,
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
        )
        _mark_timing("retrieve_sections", stage_started_at)
        section_candidates = _apply_knowledge_source_document_prior(
            section_candidates,
            preferred_documents=knowledge_source_documents,
        )
        stage_started_at = time.perf_counter()
        scoped_sections = _select_section_scope_candidates(section_candidates, limit=4)
        _mark_timing("select_section_scope", stage_started_at)
        section_ids = {
            str(item.get("section_id") or "").strip()
            for item in scoped_sections
            if str(item.get("section_id") or "").strip()
        }
        section_path_prefixes = {
            str(item.get("section_path") or item.get("heading_path") or "").strip()
            for item in scoped_sections
            if str(item.get("section_path") or item.get("heading_path") or "").strip()
        }
        full_section_matches: list[dict[str, Any]] = []
        if (
            self.section_reuse_context_mode != "section_pack"
            and scoped_sections
            and hasattr(self.case_library, "retrieve_section_blocks")
        ):
            top_section = scoped_sections[0]
            stage_started_at = time.perf_counter()
            full_section_matches = self.case_library.retrieve_section_blocks(
                sample_id=str(top_section.get("sample_id") or "").strip(),
                file_name=str(top_section.get("file_name") or "").strip(),
                section_id=str(top_section.get("section_id") or "").strip(),
                section_path=str(top_section.get("section_path") or top_section.get("heading_path") or "").strip(),
                library_tracks=library_tracks or None,
                top_k=self.section_reuse_full_section_block_limit,
                base_score=float(top_section.get("score") or 0.75),
            )
            _mark_timing("retrieve_section_blocks", stage_started_at)
        stage_started_at = time.perf_counter()
        base_matches = self.case_library.retrieve_blocks(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=max(
                self.section_reuse_candidate_limit * REUSE_CANDIDATE_MULTIPLIER,
                REUSE_MIN_CANDIDATES,
                24 if knowledge_source_documents else 0,
            ),
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
            section_ids=section_ids or None,
            section_path_prefixes=section_path_prefixes or None,
        )
        _mark_timing("retrieve_blocks_scoped", stage_started_at)
        base_matches = _apply_knowledge_source_document_prior(
            base_matches,
            preferred_documents=knowledge_source_documents,
        )
        if not base_matches:
            stage_started_at = time.perf_counter()
            base_matches = self.case_library.retrieve_blocks(
                query=query,
                section_title=str(section.get("title") or ""),
                top_k=max(
                    self.section_reuse_candidate_limit * REUSE_CANDIDATE_MULTIPLIER,
                    REUSE_MIN_CANDIDATES,
                    24 if knowledge_source_documents else 0,
                ),
                sample_ids=sample_ids,
                library_tracks=library_tracks or None,
            )
            _mark_timing("retrieve_blocks_fallback", stage_started_at)
            base_matches = _apply_knowledge_source_document_prior(
                base_matches,
                preferred_documents=knowledge_source_documents,
            )
        stage_started_at = time.perf_counter()
        neighbor_matches = self.case_library.expand_related_blocks(
            seed_blocks=base_matches[: max(self.section_reuse_candidate_limit, 3)],
            section_title=str(section.get("title") or ""),
            top_k=4,
        )
        _mark_timing("expand_related_blocks", stage_started_at)
        return {
            "matches": [*full_section_matches, *base_matches, *neighbor_matches],
            "trace": {
                "query": query,
                "query_intents": query_intents,
                "knowledge_wiki_terms": knowledge_wiki_terms,
                "knowledge_wiki_product_cards": [
                    str(item.get("title") or item.get("product_family") or "").strip()
                    for item in (knowledge_retrieval_bundle.get("product_cards") or [])
                    if isinstance(item, dict) and str(item.get("title") or item.get("product_family") or "").strip()
                ],
                "knowledge_wiki_module_cards": [
                    str(item.get("title") or item.get("module_key") or "").strip()
                    for item in (knowledge_retrieval_bundle.get("module_cards") or [])
                    if isinstance(item, dict) and str(item.get("title") or item.get("module_key") or "").strip()
                ],
                "knowledge_wiki_source_documents": sorted(knowledge_source_documents),
                "knowledge_wiki_resolved_sample_ids": sorted(resolved_sample_ids),
                "section_candidates": [
                    _serialize_section_candidate(item)
                    for item in section_candidates[:REUSE_TRACE_SECTION_LIMIT]
                ],
                "scoped_sections": [
                    _serialize_section_candidate(item)
                    for item in scoped_sections[:REUSE_TRACE_SECTION_LIMIT]
                ],
                "full_section_match_count": len(full_section_matches),
                "timings_ms": {**timings, "total": int((time.perf_counter() - started_at) * 1000)},
            },
        }

    def _expand_reusable_blocks_from_neighbors(
        self,
        *,
        section: dict[str, Any],
        reusable_blocks: list[dict[str, Any]],
        global_params: dict[str, Any],
        limit: int,
        extra_query_terms: list[str] | None = None,
        knowledge_retrieval_bundle: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not reusable_blocks:
            return []
        target_taxonomy = infer_target_taxonomy(section)
        target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
        top_score = max(float(item.get("selection_score") or 0) for item in reusable_blocks)
        seed_blocks = [
            item
            for item in reusable_blocks
            if float(item.get("selection_score") or 0) >= max(0.55, top_score * 0.7)
            or str((item.get("metadata") or {}).get("section_type") or "").lower() == target_section_type
        ]
        if not seed_blocks:
            seed_blocks = reusable_blocks[:1]
        neighbor_matches = self.case_library.expand_related_blocks(
            seed_blocks=seed_blocks[:2],
            section_title=str(section.get("title") or ""),
            top_k=4,
        )
        if not neighbor_matches:
            return reusable_blocks[:limit]
        expanded = list(reusable_blocks)
        expanded.extend(
            _build_case_library_reusable_blocks(
                section=section,
                global_params=global_params,
                case_library_matches=neighbor_matches,
                limit=max(limit, len(neighbor_matches)),
                extra_query_terms=extra_query_terms,
                knowledge_retrieval_bundle=knowledge_retrieval_bundle,
            )
        )
        deduped = _dedupe_reusable_blocks(expanded)
        deduped.sort(
            key=lambda item: (
                float(item.get("selection_score") or 0),
                float(item.get("reusability_score") or 0),
            ),
            reverse=True,
        )
        expanded_limit = max(limit, len(reusable_blocks) + len(neighbor_matches))
        return deduped[:expanded_limit]

    def _build_knowledge_wiki_retrieval_bundle(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
        collect_bundle = getattr(self.knowledge_wiki, "collect_retrieval_prior_bundle", None)
        if callable(collect_bundle):
            try:
                result = collect_bundle(section=section, global_params=global_params)
            except Exception:  # noqa: BLE001
                result = {}
            if isinstance(result, dict):
                return {
                    "query_expansion_terms": list(result.get("query_expansion_terms") or []),
                    "glossary_entries": list(result.get("glossary_entries") or []),
                    "product_cards": list(result.get("product_cards") or []),
                    "module_cards": list(result.get("module_cards") or []),
                }
        return {
            "query_expansion_terms": self.knowledge_wiki.collect_query_expansion_terms(
                section=section,
                global_params=global_params,
            ),
            "glossary_entries": [],
            "product_cards": [],
            "module_cards": [],
        }

    def _build_knowledge_wiki_query_terms(
        self,
        *,
        section: dict[str, Any],
        global_params: dict[str, Any],
    ) -> list[str]:
        return list(
            self._build_knowledge_wiki_retrieval_bundle(
                section=section,
                global_params=global_params,
            ).get("query_expansion_terms")
            or []
        )

    async def _generate_section_content(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        outline_title: str,
        global_params: dict[str, Any],
        retrieved_context: str,
        citations: list[dict[str, Any]],
        recommended_assets: list[dict[str, Any]],
        reusable_blocks: list[dict[str, Any]],
        reuse_pack: dict[str, Any],
        preceding_context: str = "",
    ) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
        generation_mode = str(section.get("generation_mode") or "baseline")
        effective_citations = citations
        assembly_blocks = reusable_blocks
        effective_reuse_pack = reuse_pack
        reuse_retrieval_trace = reuse_pack.get("retrieval_trace") if isinstance(reuse_pack.get("retrieval_trace"), dict) else {}
        early_retrieval_mode = (
            "parameter_snapshot_short_circuit"
            if reuse_retrieval_trace.get("parameter_snapshot_short_circuit")
            else "baseline_fallback"
        )
        if generation_mode != "manual_only":
            early_parameter_snapshot = _build_parameter_snapshot_section_content(
                section=section,
                global_params=global_params,
                reuse_pack=effective_reuse_pack,
                retrieval_mode=early_retrieval_mode,
                preceding_context_chars=len(preceding_context),
                knowledge_wiki_context_chars=0,
            )
            if early_parameter_snapshot is not None:
                content_md, generation_details = early_parameter_snapshot
                return (
                    content_md,
                    "generated",
                    effective_citations,
                    generation_details,
                )
        knowledge_wiki_context = self.knowledge_wiki.build_section_context(
            section=section,
            global_params=global_params,
        )
        normalized_preceding_context = _merge_prompt_context(preceding_context, knowledge_wiki_context)
        reuse_strategy = resolve_reuse_generation_strategy(
            section=section,
            reuse_pack=reuse_pack,
            reusable_blocks=reusable_blocks,
            recommended_assets=recommended_assets,
        )
        retrieval_mode = str(reuse_strategy.get("retrieval_mode") or "baseline_fallback")
        selected_sections = list(reuse_strategy.get("selected_sections") or [])
        selected_blocks = list(reuse_strategy.get("selected_blocks") or [])
        knowledge_wiki_prior_summary = dict(reuse_strategy.get("knowledge_wiki_prior_summary") or {})
        selection_reason = dict(reuse_strategy.get("selection_reason") or {})
        token_budget = dict(reuse_strategy.get("token_budget") or {})
        target_taxonomy = infer_target_taxonomy(section)
        strict_reuse_evidence = _requires_strict_reuse_evidence(
            section=section,
            target_taxonomy=target_taxonomy,
        )
        if generation_mode == "reuse_first" and reusable_blocks:
            retrieved_context = ""
            prompt_blocks = list(reuse_strategy.get("prompt_blocks") or reusable_blocks)
            filtered_prompt_blocks = _filter_reuse_blocks_for_assembly(
                reusable_blocks=prompt_blocks,
                target_taxonomy=target_taxonomy,
                section=section,
            )
            if retrieval_mode == "full_section" and not strict_reuse_evidence:
                assembly_blocks = filtered_prompt_blocks or prompt_blocks
            elif retrieval_mode == "full_section":
                assembly_blocks = filtered_prompt_blocks
            else:
                assembly_blocks = filtered_prompt_blocks
            if retrieval_mode == "full_section" and not assembly_blocks and not strict_reuse_evidence:
                assembly_blocks = prompt_blocks
            selected_blocks = _build_selected_block_trace(assembly_blocks)
            knowledge_wiki_prior_summary = _build_knowledge_wiki_prior_summary(assembly_blocks)
            token_budget = _estimate_material_tokens(blocks=assembly_blocks, assets=recommended_assets)
            effective_citations = build_reuse_citations(assembly_blocks)
            effective_reuse_pack = dict(reuse_pack)
            effective_reuse_pack["reusable_blocks"] = assembly_blocks
            effective_reuse_pack["retrieval_mode"] = retrieval_mode

        if generation_mode == "reuse_first" and strict_reuse_evidence and not assembly_blocks:
            content_md = build_llm_write_fallback_section_content(section=section, global_params=global_params)
            return (
                content_md,
                "review_required",
                effective_citations,
                {
                    "effective_path": "strict_reuse_no_evidence",
                    "retrieval_mode": retrieval_mode,
                    "selected_sections": selected_sections,
                    "selected_blocks": selected_blocks,
                    "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary,
                    "selection_reason": selection_reason,
                    "token_budget": token_budget,
                    "preceding_context_chars": len(normalized_preceding_context),
                    "knowledge_wiki_context_chars": len(knowledge_wiki_context),
                },
            )

        if generation_mode == "manual_only":
            return (
                build_manual_only_section_content(section=section, reuse_pack=reuse_pack),
                "manual_required",
                effective_citations,
                {
                    "effective_path": "manual_only",
                    "retrieval_mode": retrieval_mode,
                    "selected_sections": selected_sections,
                    "selected_blocks": selected_blocks,
                    "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary,
                    "selection_reason": selection_reason,
                    "token_budget": token_budget,
                    "preceding_context_chars": len(normalized_preceding_context),
                    "knowledge_wiki_context_chars": len(knowledge_wiki_context),
                },
            )

        section_title = clean_customer_facing_section_title(str(section.get("title") or "未命名章节"))
        customer_section = _section_with_customer_facing_title(section)
        if should_use_extractive_reuse(section=section, reuse_pack=effective_reuse_pack):
            assembly_reuse_pack = dict(effective_reuse_pack)
            assembly_reuse_pack["reusable_blocks"] = assembly_blocks
            assembly_reuse_pack["retrieval_mode"] = retrieval_mode
            target_taxonomy = infer_target_taxonomy(section)
            deterministic_reuse_builder = _use_deterministic_reuse_builder(
                section=section,
                target_taxonomy=target_taxonomy,
            )
            assembled_content = build_extractive_reuse_section_content(
                section=section,
                reuse_pack=assembly_reuse_pack,
                global_params=global_params,
                knowledge_wiki_context=knowledge_wiki_context,
            )
            if not deterministic_reuse_builder:
                assembled_content = ensure_required_asset_placeholders(
                    content_md=assembled_content,
                    reuse_pack=assembly_reuse_pack,
                )
            assembled_content = polish_extractive_reuse_section_content(
                section=section,
                content_md=assembled_content,
            )
            finalized_content: str | None = None
            refinement_status = "fallback_assembled"
            refinement_fallback_reason: str | None = "finalize_missing"
            refinement_error: str | None = None
            if deterministic_reuse_builder:
                content_md = assembled_content
                refinement_status = "deterministic_assembled"
                refinement_fallback_reason = "deterministic_section_builder"
                effective_path = "extractive_reuse_deterministic"
            else:
                try:
                    llm_reuse_pack = dict(assembly_reuse_pack)
                    llm_reuse_pack["reusable_blocks"] = (
                        assembly_blocks
                        if retrieval_mode == "full_section"
                        else assembly_blocks[:3]
                    )
                    llm_reuse_pack["retrieval_mode"] = retrieval_mode
                    response = await self.executor.write_section(
                        task_id=f"{task_id}-finalize",
                        section=section_outline_to_executor_payload(customer_section),
                        global_params=global_params,
                        retrieved_context="",
                        outline_title=outline_title,
                        recommended_assets=recommended_assets,
                        reuse_pack=llm_reuse_pack,
                        assembled_draft=assembled_content,
                        preceding_context=normalized_preceding_context,
                    )
                    finalized_content = response.content
                    finalized_content = sanitize_generated_section_content(
                        content_md=finalized_content,
                        section_title=section_title,
                        section_purpose=str(section.get("purpose") or section.get("description") or ""),
                    )
                    finalized_content = _normalize_invalid_asset_placeholders(
                        content_md=finalized_content,
                        recommended_assets=recommended_assets,
                    )
                    finalized_content = _remove_mismatched_asset_placeholders(
                        content_md=finalized_content,
                        recommended_assets=recommended_assets,
                    )
                    _, refinement_status, refinement_fallback_reason = resolve_reuse_refinement_content(
                        assembled_content=assembled_content,
                        rewritten_content=finalized_content,
                        section_title=section_title,
                    )
                except Exception as exc:  # noqa: BLE001
                    refinement_error = str(exc)
                    refinement_fallback_reason = "finalize_error"
                content_md, refinement_status, selection_fallback_reason = resolve_reuse_refinement_content(
                    assembled_content=assembled_content,
                    rewritten_content=finalized_content,
                    section_title=section_title,
                )
                refinement_fallback_reason = refinement_fallback_reason or selection_fallback_reason
                content_md = _normalize_invalid_asset_placeholders(
                    content_md=content_md,
                    recommended_assets=recommended_assets,
                )
                content_md = _remove_mismatched_asset_placeholders(
                    content_md=content_md,
                    recommended_assets=recommended_assets,
                )
                content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=assembly_reuse_pack)
                content_md = _remove_mismatched_asset_placeholders(
                    content_md=content_md,
                    recommended_assets=recommended_assets,
                )
                content_md = polish_extractive_reuse_section_content(section=section, content_md=content_md)
                effective_path = (
                    "extractive_reuse_llm_finalize"
                    if finalized_content is not None and refinement_error is None
                    else "extractive_reuse"
                )
            return (
                content_md,
                "generated",
                effective_citations,
                {
                    "effective_path": effective_path,
                    "retrieval_mode": retrieval_mode,
                    "selected_sections": selected_sections,
                    "selected_blocks": selected_blocks,
                    "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary,
                    "selection_reason": selection_reason,
                    "token_budget": token_budget,
                    "refinement_status": refinement_status,
                    "refinement_fallback_reason": refinement_fallback_reason,
                    "refinement_error": refinement_error,
                    "assembled_block_count": len(assembly_blocks),
                    "preceding_context_chars": len(normalized_preceding_context),
                    "knowledge_wiki_context_chars": len(knowledge_wiki_context),
                },
            )

        parameter_snapshot = _build_parameter_snapshot_section_content(
            section=section,
            global_params=global_params,
            reuse_pack=effective_reuse_pack,
            retrieval_mode=retrieval_mode,
            selected_sections=selected_sections,
            selected_blocks=selected_blocks,
            knowledge_wiki_prior_summary=knowledge_wiki_prior_summary,
            selection_reason=selection_reason,
            token_budget=token_budget,
            preceding_context_chars=len(normalized_preceding_context),
            knowledge_wiki_context_chars=len(knowledge_wiki_context),
        )
        if parameter_snapshot is not None:
            content_md, generation_details = parameter_snapshot
            return (
                content_md,
                "generated",
                effective_citations,
                generation_details,
            )

        write_error: str | None = None
        try:
            response = await self.executor.write_section(
                task_id=task_id,
                section=section_outline_to_executor_payload(customer_section),
                global_params=global_params,
                retrieved_context=retrieved_context,
                outline_title=outline_title,
                recommended_assets=recommended_assets,
                reuse_pack=effective_reuse_pack,
                preceding_context=normalized_preceding_context,
            )
            raw_content = response.content
            draft_status = "generated"
        except Exception as exc:  # noqa: BLE001
            write_error = str(exc)
            raw_content = build_llm_write_fallback_section_content(section=section, global_params=global_params)
            draft_status = "review_required"
        generated_without_reuse_evidence = (
            write_error is None
            and generation_mode == "reuse_first"
            and not assembly_blocks
            and not effective_citations
            and _should_mark_generated_without_evidence_review_required(
                section=section,
                target_taxonomy=target_taxonomy,
            )
        )
        if generated_without_reuse_evidence:
            draft_status = "review_required"
        content_md = sanitize_generated_section_content(
            content_md=raw_content,
            section_title=section_title,
            section_purpose=str(section.get("purpose") or section.get("description") or ""),
        )
        content_md = _normalize_invalid_asset_placeholders(
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        content_md = _remove_mismatched_asset_placeholders(
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=reuse_pack)
        content_md = _remove_mismatched_asset_placeholders(
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        return (
            content_md,
            draft_status,
            effective_citations,
            {
                "effective_path": "llm_write" if write_error is None else "llm_write_fallback",
                "retrieval_mode": retrieval_mode,
                "selected_sections": selected_sections,
                "selected_blocks": selected_blocks,
                "knowledge_wiki_prior_summary": knowledge_wiki_prior_summary,
                "selection_reason": selection_reason,
                "token_budget": token_budget,
                "write_error": write_error,
                "review_required_reason": "generated_without_reuse_evidence" if generated_without_reuse_evidence else None,
                "preceding_context_chars": len(normalized_preceding_context),
                "knowledge_wiki_context_chars": len(knowledge_wiki_context),
            },
        )

    async def _resolve_outline(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None,
    ) -> ProposalOutline:
        if outline_id is not None:
            outline = await session.get(ProposalOutline, outline_id)
            if not outline or outline.project_id != project_id:
                raise ArtifactNotFoundError("Outline not found")
            return outline
        result = await session.scalars(
            select(ProposalOutline)
            .where(ProposalOutline.project_id == project_id)
            .order_by(ProposalOutline.version.desc(), ProposalOutline.created_at.desc())
            .limit(1)
        )
        outline = result.first()
        if not outline:
            raise ArtifactNotFoundError("Outline not found")
        return outline

    async def _resolve_requirement_card(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> RequirementCard:
        if outline.requirement_card_id is None:
            raise ArtifactValidationError("Outline is not bound to a requirement card")
        card = await session.get(RequirementCard, outline.requirement_card_id)
        if not card:
            raise ArtifactNotFoundError("Requirement card not found")
        return card

    async def _resolve_evidence_bundle(
        self,
        *,
        session: AsyncSession,
        outline: ProposalOutline,
    ) -> EvidenceBundle:
        return await resolve_outline_evidence_bundle(session=session, outline=outline)

    async def _get_current_section_draft(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
        section_id: str,
    ) -> SectionDraft:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
                SectionDraft.section_id == section_id,
            )
            .limit(1)
        )
        draft = result.first()
        if not draft:
            raise ArtifactNotFoundError("Section draft not found")
        return draft

    async def _load_section_drafts(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
    ) -> list[SectionDraft]:
        result = await session.scalars(
            select(SectionDraft)
            .where(
                SectionDraft.project_id == project_id,
                SectionDraft.draft_version == draft_version,
            )
            .order_by(SectionDraft.section_id.asc())
        )
        return list(result.all())

    async def _compute_project_draft_status(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        draft_version: int,
        fallback_drafts: list[SectionDraft] | None = None,
    ) -> str:
        drafts = await self._load_section_drafts(
            session=session,
            project_id=project_id,
            draft_version=draft_version,
        )
        if not drafts:
            drafts = fallback_drafts or []
        return _derive_project_draft_status(drafts)

    def _find_section(self, *, outline: ProposalOutline, section_id: str) -> dict[str, Any]:
        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        for section in sections:
            if str(section.get("section_id")) == section_id:
                return section
        raise ArtifactNotFoundError("Section not found in outline")

    def _sort_drafts_by_outline(self, *, drafts: list[SectionDraft], outline: ProposalOutline) -> list[SectionDraft]:
        ordered_sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        order_map = {
            str(section.get("section_id") or ""): index
            for index, section in enumerate(ordered_sections)
        }
        return sorted(
            drafts,
            key=lambda draft: (order_map.get(draft.section_id, len(order_map)), draft.section_id),
        )
