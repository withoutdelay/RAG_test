from __future__ import annotations

from collections import Counter
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
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

DEFAULT_REUSE_LIMIT = 5
REUSE_CANDIDATE_MULTIPLIER = 3
REUSE_MIN_CANDIDATES = 6
FULL_SECTION_MIN_SCORE = 0.72
FULL_SECTION_MIN_LEAD = 0.08
FULL_SECTION_MAX_SOURCE_TOKENS = 1800
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
    re.compile(r"^\s*这些资产仅供参考.*$", re.IGNORECASE),
    re.compile(r"^\s*只输出最终客户可阅读的 Markdown 正文.*$", re.IGNORECASE),
    re.compile(r"^\s*请撰写章节.+$", re.IGNORECASE),
)
INTERNAL_REUSE_SUMMARY_LINE_PATTERNS = (
    re.compile(r"^\s*匹配原因[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*可参考章节[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*命中原因[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*query_overlap\s*=.*$", re.IGNORECASE),
)
PROMPT_XML_TAG_LINE_PATTERN = re.compile(r"^\s*</?[a-z_]+>\s*$", re.IGNORECASE)
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
CONTROL_LOGIC_FOCUS_TOKENS = ("运行模式", "控制策略", "调节模式", "启动", "升速", "并切换", "切换", "同期", "工频", "顺控", "控制边界")
CONTROL_LOGIC_NOISE_TOKENS = ("外形尺寸", "版本信息", "开关柜参数", "供货范围", "设备清单", "目录", "版本")
PARAMETER_SUMMARY_FOCUS_TOKENS = ("技术数据", "技术参数", "性能指标", "额定", "容量", "电流", "电压", "频率", "短路容量", "阻抗", "温升", "防护等级", "电磁兼容", "效率", "功率因数", "输入变压器", "输出变压器", "变频器")
PARAMETER_SUMMARY_NOISE_TOKENS = ("控制总图", "控制系统", "联锁保护", "外形尺寸", "最小间距", "side view", "版本", "页码", "启动曲线")
DESIGN_BASIS_FOCUS_TOKENS = ("设计依据", "标准", "规范", "边界", "条件", "环境", "电源条件", "短路容量", "海拔", "温度", "湿度", "安装", "基础", "接口边界")
DESIGN_BASIS_NOISE_TOKENS = ("启动时间", "同步时间", "纯加速", "去磁", "油流量", "润滑曲线", "波形", "曲线", "顺控", "plc", "工频切换")
VFD_SPEC_FOCUS_TOKENS = ("lci", "变频软起", "变频软起动", "变频装置", "晶闸管", "整流", "逆变", "工频切换", "同步切换", "启动回路", "输出变压器")
VFD_SPEC_NOISE_TOKENS = ("柜体尺寸", "外形尺寸", "控制总图", "油站", "润滑油", "冷却器", "售后", "备件", "供货范围")
VFD_LCI_ALLOWED_SIGNAL_TOKENS = ("lci", "sfc", "软起", "软启动", "软起动", "变频软起", "同步切换", "工频切换", "启动时间", "启动过程")
VFD_LCI_SPECIFIC_TOKENS = ("lci", "sfc", "变频软起", "软起动", "软启动", "晶闸管", "换相", "纯加速", "同步切换", "工频切换", "励磁柜")
MOTOR_INTERFACE_FOCUS_TOKENS = ("同步电机", "励磁", "转子", "定子", "测温", "测振", "轴承", "接口", "绝缘", "整流", "触发", "适配")
MOTOR_INTERFACE_NOISE_TOKENS = (
    "柜体结构",
    "外形尺寸",
    "开关柜参数",
    "供货范围",
    "售后",
    "培训",
    "维保",
    "油站",
    "润滑油",
    "冷却器",
)
SUPPLY_SCOPE_FOCUS_TOKENS = ("供货范围", "供货", "设备清单", "专用工具", "随机资料", "软件", "接口分工", "责任边界", "资料交付")
SUPPLY_SCOPE_NOISE_TOKENS = ("售后服务", "培训", "维保", "质保", "巡检", "波形", "启动曲线", "控制总图")
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
    parameter_text = _section_asset_signal_text(section).casefold()
    needs_dimension_assets = any(token in parameter_text for token in ("尺寸", "外形", "柜体", "布置"))
    technical_noise_tokens = ("认证", "检验", "检测", "报告", "证书", "试验")
    filtered: list[dict[str, Any]] = []
    for asset in recommended_assets:
        visual_role = str(asset.get("visual_role") or "").lower()
        asset_type = str(asset.get("asset_type") or "").lower()
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        retrieval_quality = metadata.get("retrieval_quality") if isinstance(metadata.get("retrieval_quality"), dict) else {}
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
                asset.get("caption"),
                asset.get("preview_text"),
            )
            if part
        )
        if target_section_type in EXTRACTIVE_SECTION_TYPES and visual_role == "page_furniture":
            continue
        if target_section_type in EXTRACTIVE_SECTION_TYPES and any(token in heading_text for token in technical_noise_tokens):
            continue
        if target_section_type == "main_circuit_scheme":
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
            focus_match, noise_match = _main_circuit_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"communication_interface", "protection_interlock"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "vfd_spec":
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
            focus_match, noise_match = _vfd_spec_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"protection_interlock", "cabinet_layout", "service_support", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif target_section_type == "motor_spec":
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
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
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
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
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
            focus_match, noise_match = _control_logic_focus_flags(
                heading_text=heading_text,
                content_text=str(asset.get("preview_text") or ""),
            )
            if asset_section_type in {"cabinet_layout", "commissioning_acceptance", "supply_scope"} and not focus_match:
                continue
            if noise_match and not focus_match:
                continue
        elif parameter_summary:
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
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
            asset_section_type = str(metadata.get("section_type") or "unknown").lower()
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
        filtered.append(asset)
    if filtered:
        return filtered
    if target_section_type in EXTRACTIVE_SECTION_TYPES:
        return []
    return recommended_assets


def tighten_recommended_assets_for_reuse(
    recommended_assets: list[dict[str, Any]],
    *,
    section: dict[str, Any],
    reusable_blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not recommended_assets or not reusable_blocks:
        return recommended_assets
    target_taxonomy = infer_target_taxonomy(section)
    target_section_type = str(target_taxonomy.get("section_type") or "unknown").lower()
    if target_section_type not in EXTRACTIVE_SECTION_TYPES:
        return recommended_assets
    preferred_limit = min(len(recommended_assets), 3)

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


def sanitize_generated_section_content(*, content_md: str, section_title: str) -> str:
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

    text = "\n".join(cleaned).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return f"## {section_title}\n"
    if not text.lstrip().startswith("#"):
        return f"## {section_title}\n\n{text}\n"
    return text.rstrip() + "\n"


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
    if target_section_type == "protection_interlock" and _should_skip_protection_scenario_noise(
        section=section,
        global_params=global_params,
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
    search_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "query": str(query or ""),
        "asset_types": list(asset_types or []),
        "skipped_optional_search": bool(skipped_optional_search),
        "search_trace": dict(search_trace or {}),
        "selected_count": len(recommended_assets),
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
                "knowledge_wiki_terms": reuse_trace.get("knowledge_wiki_terms") or [],
                "knowledge_wiki_product_cards": reuse_trace.get("knowledge_wiki_product_cards") or [],
                "knowledge_wiki_module_cards": reuse_trace.get("knowledge_wiki_module_cards") or [],
                "section_candidates": reuse_trace.get("section_candidates") or [],
                "scoped_sections": reuse_trace.get("scoped_sections") or [],
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
    title = str(section.get("title") or "未命名章节").strip() or "未命名章节"
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
        if target_section_type == "main_circuit_scheme" and content_form not in {"parameter_table", "bom_table"}:
            paragraphs = _extract_main_circuit_reuse_paragraphs(body)
        else:
            paragraphs = _extract_reuse_paragraphs(body)
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
            max_paragraphs = 3 if len(candidate_blocks) <= 2 else 2
            if len(selected_paragraphs) >= max_paragraphs or total_chars >= 4200:
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
        if total_chars >= 4200:
            break

    if len(lines) <= 2:
        fallback_text = str(section.get("purpose") or "请基于历史方案复用块补充本章节内容。").strip()
        lines.extend([fallback_text, ""])

    return "\n".join(lines).rstrip() + "\n"


def build_llm_write_fallback_section_content(*, section: dict[str, Any], global_params: dict[str, Any]) -> str:
    title = str(section.get("title") or "未命名章节").strip() or "未命名章节"
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
    section_title = str(section.get("title") or "未命名章节")
    polished = sanitize_generated_section_content(content_md=content_md, section_title=section_title)
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
        if target_section_type not in {"unknown", "overall_solution"}:
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
    if not filtered and (target_section_type in {"protection_interlock", "control_logic"} or scenario_guard_skipped):
        return []
    filtered = filtered or reusable_blocks[:2]
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
    return section_type in {"motor_spec", "starter_spec"} and any(token in text for token in ("供电", "负载", "同步电机", "启动"))


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

    def _replace(match: re.Match[str]) -> str:
        asset_ref = str(match.group(2) or "").strip()
        if asset_ref in valid_ids:
            return match.group(0)
        label = re.sub(r"\s+", " ", asset_ref).strip("[]【】")
        if not label:
            return "待补充确认"
        return f"待根据《{label}》进一步确认"

    return INVALID_ASSET_PLACEHOLDER_PATTERN.sub(_replace, str(content_md or ""))


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
        selection_score = float(block.get("selection_score") or 0)
        priority = 5
        if target_section_type == "main_circuit_scheme":
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
    taxonomy_section = str(infer_target_taxonomy(inferred_section).get("section_type") or "unknown").lower()
    hints: list[str] = []
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
    for block in reusable_blocks[:3]:
        source_title = str(block.get("source_title") or "").strip()
        if source_title and source_title not in anchor_document_names:
            anchor_document_names.append(source_title)
        heading_text = " > ".join(
            str(item).strip()
            for item in (block.get("heading_path") or [])
            if str(item).strip()
        )
        if heading_text and heading_text not in anchor_heading_paths:
            anchor_heading_paths.append(heading_text)
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
    }


def section_outline_to_executor_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": section.get("title"),
        "description": section.get("purpose", ""),
        "keywords": [section.get("title", ""), *[str(item) for item in section.get("expected_evidence_types") or []]],
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
    expected_types = set(_effective_section_evidence_types(section))
    asset_types: list[str] = []
    if {"table", "parameter"} & expected_types:
        asset_types.append("table")
    if {"figure", "diagram"} & expected_types:
        asset_types.append("figure")
    if {"formula", "equation"} & expected_types:
        asset_types.append("formula_candidate")
    return asset_types or None


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
        score, _, _ = _score_reuse_candidate(
            section=section,
            item=result,
            raw_content=str(result.get("raw_content") or result.get("summary") or ""),
            heading_path=result.get("heading_path") or [],
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
        blocks.append(
            {
                "block_id": f"case:{item.get('sample_id')}:{item.get('chunk_index')}",
                "sample_id": item.get("sample_id"),
                "chunk_index": item.get("chunk_index"),
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

    return {
        "section_title": str(section.get("title") or ""),
        "section_purpose": str(section.get("purpose") or ""),
        "generation_mode": str(section.get("generation_mode") or "baseline"),
        "reuse_level": str(section.get("reuse_level") or "medium"),
        "target_taxonomy": serializable_target_taxonomy,
        "reusable_blocks": reusable_blocks,
        "recommended_assets": recommended_assets,
        "must_replace_fields": must_replace_fields,
        "banned_terms": banned_terms,
        "replacement_hints": {
            field_name: global_params.get(field_name)
            for field_name in must_replace_fields
            if global_params.get(field_name) not in (None, "", [], {})
        },
        "required_asset_placeholders": required_asset_placeholders,
        "parameter_candidates": {
            key: value
            for key, value in global_params.items()
            if key in {"project_name", "product_line", "industry", "business_objective", "voltage_level", "power_rating", "quantity"}
        },
        "do_not_reuse_signals": [
            "customer_specific_fields",
            "outdated_schedule",
            "unconfirmed_parameters",
        ],
        "risk_flags": risk_flags,
        "retrieval_trace": retrieval_trace or {},
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
    title = str(section.get("title") or "未命名章节")
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


def _estimate_material_tokens(*, blocks: list[dict[str, Any]], assets: list[dict[str, Any]]) -> dict[str, Any]:
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
        "within_budget": section_material_tokens <= FULL_SECTION_MAX_SOURCE_TOKENS,
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
    matched: list[dict[str, Any]] = []
    for block in reusable_blocks:
        block_sample_id = str(block.get("sample_id") or block.get("source_doc_id") or "").strip()
        block_file_name = str(block.get("source_title") or "").strip()
        block_section_id = str(block.get("source_section_id") or "").strip()
        block_section_path = str(block.get("section_path") or " > ".join(str(item) for item in (block.get("heading_path") or []) if item)).strip()
        if target_sample_id and block_sample_id and block_sample_id != target_sample_id:
            continue
        if target_file_name and block_file_name and block_file_name != target_file_name:
            continue
        if target_section_id and block_section_id == target_section_id:
            matched.append(block)
            continue
        if target_section_path and block_section_path == target_section_path:
            matched.append(block)
    return matched


def _build_reuse_selection_reason(
    *,
    section: dict[str, Any],
    retrieval_mode: str,
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
        if not top_section:
            reasons.append("section_pack_due_to_missing_top_section")
        elif top_score < FULL_SECTION_MIN_SCORE:
            reasons.append("section_pack_due_to_low_top_section_score")
        elif lead_score < FULL_SECTION_MIN_LEAD:
            reasons.append("section_pack_due_to_low_section_lead")
        elif not full_section_blocks:
            reasons.append("section_pack_due_to_missing_aligned_blocks")
        elif not bool(full_section_budget.get("within_budget")):
            reasons.append("section_pack_due_to_token_budget")
        else:
            reasons.append("selected_section_pack")

    return {
        "mode": retrieval_mode,
        "top_section_id": str((top_section or {}).get("section_id") or "").strip(),
        "top_section_score": round(top_score, 4),
        "runner_up_score": round(runner_up_score, 4),
        "lead_score": round(lead_score, 4),
        "full_section_block_count": len(full_section_blocks),
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
    retrieval_trace = reuse_pack.get("retrieval_trace") if isinstance(reuse_pack.get("retrieval_trace"), dict) else {}
    section_candidates = [
        item
        for item in (retrieval_trace.get("section_candidates") or [])
        if isinstance(item, dict)
    ]
    scoped_sections = [
        item
        for item in (retrieval_trace.get("scoped_sections") or [])
        if isinstance(item, dict)
    ]
    selected_sections = scoped_sections[:REUSE_TRACE_SECTION_LIMIT] or section_candidates[:REUSE_TRACE_SECTION_LIMIT]
    if str(section.get("generation_mode") or "baseline") != "reuse_first" or not reusable_blocks:
        prompt_blocks = reusable_blocks[:DEFAULT_REUSE_LIMIT]
        return {
            "retrieval_mode": "baseline_fallback",
            "prompt_blocks": prompt_blocks,
            "selected_sections": selected_sections,
            "selected_blocks": _build_selected_block_trace(prompt_blocks),
            "knowledge_wiki_prior_summary": _build_knowledge_wiki_prior_summary(prompt_blocks),
            "token_budget": _estimate_material_tokens(blocks=prompt_blocks, assets=recommended_assets),
        }

    top_section = section_candidates[0] if section_candidates else (selected_sections[0] if selected_sections else None)
    runner_up = section_candidates[1] if len(section_candidates) > 1 else None
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
    full_section_blocks = sorted(
        full_section_blocks,
        key=lambda item: (
            float(item.get("selection_score") or 0),
            float(item.get("reusability_score") or 0),
        ),
        reverse=True,
    )
    full_section_budget = _estimate_material_tokens(blocks=full_section_blocks, assets=recommended_assets)
    if (
        top_section
        and top_score >= FULL_SECTION_MIN_SCORE
        and lead_score >= FULL_SECTION_MIN_LEAD
        and full_section_blocks
        and full_section_budget["within_budget"]
    ):
        prompt_blocks = full_section_blocks
        retrieval_mode = "full_section"
        token_budget = full_section_budget
    else:
        prompt_blocks = reusable_blocks[:DEFAULT_REUSE_LIMIT]
        retrieval_mode = "section_pack"
        token_budget = _estimate_material_tokens(blocks=prompt_blocks, assets=recommended_assets)
    selection_reason = _build_reuse_selection_reason(
        section=section,
        retrieval_mode=retrieval_mode,
        top_section=top_section,
        runner_up=runner_up,
        reusable_blocks=reusable_blocks,
        full_section_blocks=full_section_blocks,
        full_section_budget=full_section_budget,
    )
    return {
        "retrieval_mode": retrieval_mode,
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
        self.executor = executor or ExecutorAgent()
        self._asset_retriever = asset_retriever
        self.knowledge_wiki = knowledge_wiki or KnowledgeWikiContextProvider()
        self.case_library = case_library or CaseLibraryService()
        self.quality_gate = quality_gate or SectionQualityGateService(
            executor=self.executor,
            knowledge_wiki=self.knowledge_wiki,
        )

    @property
    def asset_retriever(self) -> AssetRetrievalService:
        if self._asset_retriever is None:
            self._asset_retriever = AssetRetrievalService()
        return self._asset_retriever

    async def generate_sections(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        outline_id: UUID | None = None,
    ) -> tuple[Job, list[SectionDraft]]:
        project = await session.get(Project, project_id)
        if not project:
            raise ArtifactNotFoundError("Project not found")

        outline = await self._resolve_outline(session=session, project_id=project_id, outline_id=outline_id)
        if not outline_is_approved(outline.outline_json):
            raise ArtifactValidationError("Outline must be approved before generating sections")
        requirement_card = await self._resolve_requirement_card(session=session, outline=outline)
        evidence_bundle = await self._resolve_evidence_bundle(session=session, outline=outline)

        sections = flatten_outline_sections(((outline.outline_json or {}).get("sections") or []))
        if not sections:
            raise ArtifactValidationError("Outline has no sections")

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "outline_id": str(outline.id)},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

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

        for index, section in enumerate(sections):
            generation_mode = str(section.get("generation_mode") or "baseline")
            knowledge_retrieval_bundle = self._build_knowledge_wiki_retrieval_bundle(
                section=section,
                global_params=global_params,
            )
            knowledge_wiki_terms = list(knowledge_retrieval_bundle.get("query_expansion_terms") or [])
            preceding_context = _build_preceding_context(
                state=inter_section_state,
                covered_topics=covered_topics,
                current_index=index,
            )
            context, citations = build_section_context(
                section=section,
                evidence_bundle=evidence_bundle,
                global_params=global_params,
            )
            evidence_trace = _build_evidence_retrieval_trace(citations=citations)
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
            )
            reusable_blocks = self._expand_reusable_blocks_from_neighbors(
                section=section,
                reusable_blocks=reusable_blocks,
                global_params=global_params,
                limit=DEFAULT_REUSE_LIMIT,
                extra_query_terms=knowledge_wiki_terms,
                knowledge_retrieval_bundle=knowledge_retrieval_bundle,
            )
            recommended_assets, asset_trace = await self._search_recommended_assets(
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
                retrieval_trace=case_library_result.get("trace"),
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
                content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                    task_id=str(job.id),
                    section=section,
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
                section_title=str(section.get("title") or "未命名章节"),
                draft_status=draft_status,
                content_md=content_md,
            )
            draft = SectionDraft(
                project_id=project_id,
                draft_version=draft_version,
                section_id=str(section.get("section_id")),
                title=str(section.get("title") or "未命名章节"),
                content_md=content_md,
                citation_refs=citations,
                assumptions=[],
                global_param_snapshot=global_params if isinstance(global_params, dict) else {},
                status=draft_status,
                validator_result={
                    "recommended_assets": recommended_assets,
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

        project.current_draft_version = draft_version
        project.status = _derive_project_draft_status(generated_drafts)
        job.status = "succeeded"
        job.output_ref = {
            "draft_version": draft_version,
            "section_count": len(generated_drafts),
            "generation_summary": _build_generation_summary(generation_metrics),
        }
        job.completed_at = datetime.now(timezone.utc)

        await session.commit()
        for draft in generated_drafts:
            await session.refresh(draft)
        await session.refresh(job)
        return job, generated_drafts

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

    async def regenerate_section(
        self,
        *,
        session: AsyncSession,
        project_id: UUID,
        section_id: str,
        outline_id: UUID | None = None,
        preferred_citation_ids: list[str] | None = None,
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

        job = Job(
            project_id=project_id,
            job_type="generate",
            status="running",
            trace_id=uuid.uuid4().hex,
            input_ref={"project_id": str(project_id), "section_id": section_id},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        await session.flush()

        generation_mode = str(section.get("generation_mode") or "baseline")
        normalized_preferred_citation_ids = _normalize_preferred_citation_ids(preferred_citation_ids)
        global_params = build_section_global_params(requirement_card.content)
        outline_title = (outline.outline_json or {}).get("title", "技术方案")
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
        context, citations = build_section_context(
            section=section,
            evidence_bundle=evidence_bundle,
            global_params=global_params,
            preferred_evidence_ids=normalized_preferred_citation_ids,
        )
        evidence_trace = _build_evidence_retrieval_trace(
            citations=citations,
            preferred_evidence_ids=normalized_preferred_citation_ids,
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
        )
        reusable_blocks = prioritize_reusable_blocks_for_citations(
            reusable_blocks,
            preferred_citation_ids=preferred_citation_ids,
        )
        reusable_blocks = self._expand_reusable_blocks_from_neighbors(
            section=section,
            reusable_blocks=reusable_blocks,
            global_params=global_params,
            limit=DEFAULT_REUSE_LIMIT,
            extra_query_terms=knowledge_wiki_terms,
            knowledge_retrieval_bundle=knowledge_retrieval_bundle,
        )
        reusable_blocks = prioritize_reusable_blocks_for_citations(
            reusable_blocks,
            preferred_citation_ids=preferred_citation_ids,
        )
        recommended_assets, asset_trace = await self._search_recommended_assets(
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
            retrieval_trace=case_library_result.get("trace"),
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
            content_md, draft_status, quality_gate_result = await self.quality_gate.review_and_repair(
                task_id=str(job.id),
                section=section,
                outline_title=(outline.outline_json or {}).get("title", "技术方案"),
                global_params=global_params,
                content_md=content_md,
                recommended_assets=recommended_assets,
                allow_rewrite=True,
            )
        draft.title = str(section.get("title") or draft.title)
        draft.content_md = content_md
        draft.citation_refs = citations
        draft.global_param_snapshot = global_params
        draft.status = draft_status
        draft.validator_result = {
            "recommended_assets": recommended_assets,
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
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        query = build_section_asset_query(section=section, global_params=global_params)
        asset_types = build_section_asset_types(section)
        if not query:
            return [], _build_asset_retrieval_trace(
                query="",
                asset_types=asset_types,
                skipped_optional_search=True,
                recommended_assets=[],
            )
        target_taxonomy = infer_target_taxonomy(section)
        skipped_optional_search = _should_skip_optional_asset_search(
            section=section,
            target_taxonomy=target_taxonomy,
            asset_types=asset_types,
        )
        if skipped_optional_search:
            return [], _build_asset_retrieval_trace(
                query=query,
                asset_types=asset_types,
                skipped_optional_search=True,
                recommended_assets=[],
            )
        response = await self.asset_retriever.search_project_assets(
            session=session,
            project_id=project_id,
            query=query,
            top_k=12,
            asset_types=asset_types,
            section_context=_build_asset_search_context(section=section, reusable_blocks=reusable_blocks),
            include_global_historical=True,
        )
        assets = [item.model_dump(mode="json") for item in response.results]
        assets = filter_recommended_assets_for_section(assets, section=section)
        assets = prioritize_recommended_assets(assets)
        assets = tighten_recommended_assets_for_reuse(
            assets,
            section=section,
            reusable_blocks=reusable_blocks,
        )
        return assets, _build_asset_retrieval_trace(
            query=query,
            asset_types=asset_types,
            skipped_optional_search=False,
            recommended_assets=assets,
            search_trace=response.search_trace.model_dump(mode="json") if response.search_trace is not None else None,
        )

    def _retrieve_case_library_matches(
        self,
        *,
        section: dict[str, Any],
        evidence_bundle: EvidenceBundle,
        global_params: dict[str, Any],
    ) -> dict[str, Any]:
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
        if not sample_ids:
            return {"matches": [], "trace": {"query": "", "query_intents": {}, "section_candidates": [], "scoped_sections": []}}
        knowledge_retrieval_bundle = self._build_knowledge_wiki_retrieval_bundle(
            section=section,
            global_params=global_params,
        )
        knowledge_wiki_terms = list(knowledge_retrieval_bundle.get("query_expansion_terms") or [])
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
        section_candidates = self.case_library.retrieve_sections(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=4,
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
        )
        scoped_sections = _select_section_scope_candidates(section_candidates, limit=4)
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
        base_matches = self.case_library.retrieve_blocks(
            query=query,
            section_title=str(section.get("title") or ""),
            top_k=max(DEFAULT_REUSE_LIMIT * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
            sample_ids=sample_ids,
            library_tracks=library_tracks or None,
            section_ids=section_ids or None,
            section_path_prefixes=section_path_prefixes or None,
        )
        if not base_matches:
            base_matches = self.case_library.retrieve_blocks(
                query=query,
                section_title=str(section.get("title") or ""),
                top_k=max(DEFAULT_REUSE_LIMIT * REUSE_CANDIDATE_MULTIPLIER, REUSE_MIN_CANDIDATES),
                sample_ids=sample_ids,
                library_tracks=library_tracks or None,
            )
        neighbor_matches = self.case_library.expand_related_blocks(
            seed_blocks=base_matches[: max(DEFAULT_REUSE_LIMIT, 3)],
            section_title=str(section.get("title") or ""),
            top_k=4,
        )
        return {
            "matches": [*base_matches, *neighbor_matches],
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
                "section_candidates": [
                    _serialize_section_candidate(item)
                    for item in section_candidates[:REUSE_TRACE_SECTION_LIMIT]
                ],
                "scoped_sections": [
                    _serialize_section_candidate(item)
                    for item in scoped_sections[:REUSE_TRACE_SECTION_LIMIT]
                ],
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
        if generation_mode == "reuse_first" and reusable_blocks:
            retrieved_context = ""
            prompt_blocks = list(reuse_strategy.get("prompt_blocks") or reusable_blocks)
            if retrieval_mode == "full_section":
                assembly_blocks = prompt_blocks
            else:
                assembly_blocks = _filter_reuse_blocks_for_assembly(
                    reusable_blocks=prompt_blocks,
                    target_taxonomy=infer_target_taxonomy(section),
                    section=section,
                )
            selected_blocks = _build_selected_block_trace(assembly_blocks)
            knowledge_wiki_prior_summary = _build_knowledge_wiki_prior_summary(assembly_blocks)
            token_budget = _estimate_material_tokens(blocks=assembly_blocks, assets=recommended_assets)
            effective_citations = build_reuse_citations(assembly_blocks)
            effective_reuse_pack = dict(reuse_pack)
            effective_reuse_pack["reusable_blocks"] = assembly_blocks

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

        section_title = str(section.get("title") or "未命名章节")
        if should_use_extractive_reuse(section=section, reuse_pack=effective_reuse_pack):
            assembly_reuse_pack = dict(effective_reuse_pack)
            assembly_reuse_pack["reusable_blocks"] = assembly_blocks
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
                    llm_reuse_pack["reusable_blocks"] = assembly_blocks[:3]
                    response = await self.executor.write_section(
                        task_id=f"{task_id}-finalize",
                        section=section_outline_to_executor_payload(section),
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
                    )
                    finalized_content = _normalize_invalid_asset_placeholders(
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
                content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=assembly_reuse_pack)
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

        write_error: str | None = None
        try:
            response = await self.executor.write_section(
                task_id=task_id,
                section=section_outline_to_executor_payload(section),
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
        content_md = sanitize_generated_section_content(
            content_md=raw_content,
            section_title=section_title,
        )
        content_md = _normalize_invalid_asset_placeholders(
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        content_md = ensure_required_asset_placeholders(content_md=content_md, reuse_pack=reuse_pack)
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
