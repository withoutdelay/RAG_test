from __future__ import annotations

import re
from typing import Any

from app.services.domain.synonyms import text_contains_domain_term
from app.services.parsing.formula_candidates import has_garbled_formula_text, is_formula_like_text


COMMERCIAL_MANUAL_ONLY_TERMS = (
    "商务",
    "报价",
    "合同",
    "法务",
    "授权",
    "保密",
    "资料提供",
    "提交资料",
    "交付资料",
    "随机资料",
    "文档清单",
    "交付文档",
    "资料归档",
    "操作维护手册",
    "测试报告",
    "合格证",
    "提交节点",
)
_COMMERCIAL_MANUAL_EXACT_COMPOUND_TERMS = ("同步资料", "启动资料", "启动同步资料")
_CONDITIONAL_COMMERCIAL_MANUAL_TERMS = ("技术资料", "设计图纸")
_COMMERCIAL_MANUAL_CONTEXT_TERMS = (
    "提交",
    "交付",
    "随机",
    "提供",
    "归档",
    "审查",
    "文档",
    "资料",
    "手册",
    "报告",
    "证书",
)
_TECHNICAL_LIST_ONLY_TERMS = ("供货清单", "设备清单", "配置清单", "物料清单", "bom")
_SUPPLY_SCOPE_TERMS = ("供货范围", "供货内容", "供货界面", "供货边界")
_STRONG_DELIVERY_TERMS = (
    "资料提供",
    "提交资料",
    "交付资料",
    "文档清单",
    "交付文档",
    "资料归档",
    "操作维护手册",
    "测试报告",
    "合格证",
    "提交节点",
)

_SECTION_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "commercial_manual_only",
        COMMERCIAL_MANUAL_ONLY_TERMS,
    ),
    ("company_profile", ("公司简介", "企业简介", "企业介绍", "公司概况")),
    ("bom_or_supply_list", ("供货清单", "设备清单", "主要设备清单", "配置清单", "物料清单", "bom")),
    ("supply_scope", ("供货范围", "供货内容", "供货界面")),
    ("site_conditions", ("工况条件", "工厂设计环境", "环境条件", "现场环境", "现场条件")),
    ("main_circuit_scheme", ("主回路", "一次系统", "一次接线", "主接线", "旁路切换", "电气原理")),
    (
        "communication_interface",
        (
            "通信接口",
            "通讯接口",
            "控制接口",
            "控制信号接口",
            "上位机接口",
            "上位机",
            "点表",
            "自动化通信",
            "dcs接口",
            "plc接口",
            "rs485",
            "iec 61850",
            "modbus",
            "profibus",
            "profinet",
        ),
    ),
    ("protection_interlock", ("保护", "联锁", "闭锁", "报警", "trip")),
    ("control_logic", ("控制策略", "控制逻辑", "运行功能", "启停逻辑", "切换逻辑", "操作流程")),
    ("cabinet_layout", ("柜体", "盘柜", "机柜", "设备布置", "现场布置", "外形尺寸", "柜体结构")),
    ("installation_conditions", ("安装条件", "安装要求", "配套要求", "施工条件", "土建条件", "基础要求")),
    ("commissioning_acceptance", ("调试", "试验", "测试", "验收", "联调", "开车")),
    ("service_support", ("售后", "培训", "维保", "质保", "备件", "巡检")),
    ("motor_spec", ("电机技术", "高压电机", "同步电机", "异步电机", "电机参数")),
    ("vfd_spec", ("变频器技术", "高压变频", "变频装置", "vfd", "lci")),
    ("starter_spec", ("软起动", "软启动", "液阻", "水电阻", "固态软起", "自耦变")),
    ("transformer_spec", ("变压器技术", "整流变压器", "变压器参数", "变压器")),
    ("overall_solution", ("总体方案", "整体方案", "系统总体", "总体设计", "总体说明")),
    ("project_overview", ("项目概述", "项目背景", "建设目标", "项目简介", "编制说明")),
    ("design_basis", ("设计依据", "编制依据", "技术要求", "标准规范", "执行标准")),
)

_EQUIPMENT_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lci", ("lci",)),
    ("vfd", ("变频", "vfd")),
    ("soft_starter", ("软起动", "软启动", "液阻", "水电阻", "固态软起", "自耦变")),
    ("motor", ("电机", "同步机", "异步机")),
    ("transformer", ("变压器", "整流变压器")),
    ("switchgear", ("开关柜", "高压柜", "配电柜")),
    ("cabinet", ("柜体", "控制柜", "功率柜", "旁路柜", "机柜")),
    ("dcs_plc_interface", ("dcs", "plc", "通讯接口", "通信接口", "控制接口", "上位机接口")),
    ("cooling_system", ("冷却", "水冷", "风冷", "冷却系统")),
    ("fan_blower", ("风机", "鼓风机", "环冷风机")),
    ("compressor", ("压缩机", "空压机")),
)

_BOM_TERMS = ("供货", "清单", "设备", "数量", "序号", "名称", "型号", "规格")
_INTERFACE_TERMS = ("接口", "通讯", "通信", "dcs", "plc", "di", "do", "ai", "ao", "点表", "rs485")
_PROTECTION_TERMS = ("保护", "联锁", "报警", "trip", "闭锁")
_PARAMETER_TERMS = ("参数", "规格", "额定", "性能", "技术数据", "技术要求")
_FIGURE_TERMS = ("波形", "波特图", "时序图", "点阵图", "电路图", "原理图", "接线图", "示意图", "电气图", "fft", "bode", "waveform", "circuit", "schematic", "diagram")
_CERTIFICATE_TERMS = ("证书", "合格证", "资质", "认证", "检测报告")
_OVERALL_SOLUTION_HEADING_TERMS = ("总体方案", "整体方案", "系统总体", "总体设计", "总体说明")
_SPECIALIZED_HEADING_TERMS = ("主回路", "接口", "通讯", "通信", "清单", "供货", "保护", "联锁")

_RELATED_SECTION_TYPES: dict[str, set[str]] = {
    "overall_solution": {
        "main_circuit_scheme",
        "control_logic",
        "communication_interface",
        "protection_interlock",
        "motor_spec",
        "vfd_spec",
        "starter_spec",
        "transformer_spec",
    },
    "supply_scope": {"bom_or_supply_list"},
    "bom_or_supply_list": {"supply_scope"},
    "main_circuit_scheme": {"vfd_spec", "starter_spec", "transformer_spec", "motor_spec", "control_logic", "protection_interlock"},
    "vfd_spec": {"main_circuit_scheme", "control_logic"},
    "starter_spec": {"main_circuit_scheme", "control_logic"},
    "motor_spec": {"protection_interlock"},
}

_SUPPORT_CONTENT_FORMS: dict[str, set[str]] = {
    "overall_solution": {"narrative", "figure", "parameter_table", "bom_table"},
    "main_circuit_scheme": {"narrative", "parameter_table", "bom_table", "figure"},
    "communication_interface": {"narrative", "interface_table", "parameter_table", "figure"},
    "control_logic": {"narrative", "interface_table", "parameter_table"},
    "protection_interlock": {"narrative", "protection_table", "parameter_table"},
    "bom_or_supply_list": {"bom_table", "parameter_table", "narrative"},
    "supply_scope": {"bom_table", "parameter_table", "narrative"},
    "vfd_spec": {"narrative", "parameter_table", "bom_table", "figure"},
    "starter_spec": {"narrative", "parameter_table", "bom_table", "figure"},
    "motor_spec": {"narrative", "parameter_table", "bom_table", "figure"},
}

_HEADING_FOCUS_TERMS: dict[str, tuple[str, ...]] = {
    "overall_solution": ("总体方案", "整体方案", "系统总体", "总体说明", "技术方案"),
    "main_circuit_scheme": ("主回路", "主接线", "一次接线", "一次系统", "旁路", "隔离", "电气原理", "选型", "技术参数"),
    "communication_interface": ("接口", "通信", "通讯", "dcs", "plc", "开关量", "模拟量", "点表", "上位机", "rs485"),
    "control_logic": ("控制策略", "控制逻辑", "运行功能", "启停逻辑", "切换逻辑"),
    "protection_interlock": ("保护", "联锁", "闭锁", "报警", "trip"),
    "bom_or_supply_list": ("供货清单", "设备清单", "配置清单", "物料清单", "供货范围"),
    "supply_scope": ("供货范围", "供货内容", "供货界面"),
    "vfd_spec": ("变频器", "变频装置", "技术参数", "选型"),
    "starter_spec": ("软起动", "软启动", "液阻", "水电阻", "固态软起", "自耦变"),
}

_HEADING_NOISE_TERMS: dict[str, tuple[str, ...]] = {
    "overall_solution": ("公司简介", "企业简介", "必要性", "意义", "培训", "维保", "售后"),
    "main_circuit_scheme": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程", "测试制度", "质量保证", "文档控制", "掉电不停机", "驱动原理"),
    "communication_interface": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程", "驱动原理", "性能要求", "整体要求"),
    "control_logic": ("公司简介", "企业简介", "必要性", "意义", "产品简介"),
    "protection_interlock": ("公司简介", "企业简介", "必要性", "意义", "产品简介"),
    "bom_or_supply_list": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程"),
    "supply_scope": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程"),
    "vfd_spec": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程"),
    "starter_spec": ("公司简介", "企业简介", "必要性", "意义", "产品简介", "改造工程"),
}

_HEADING_NUMBER_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)*)")
_FILE_LIKE_HEADING_PATTERN = re.compile(r"\.(?:doc|docx|pdf|ppt|pptx|xls|xlsx)\b", re.IGNORECASE)


def is_commercial_manual_section_text(*texts: str) -> bool:
    haystack = "\n".join(str(text or "") for text in texts).casefold()
    compact = re.sub(r"\s+", "", haystack)
    if not compact:
        return False
    has_supply_scope = any(term.casefold().replace(" ", "") in compact for term in _SUPPLY_SCOPE_TERMS)
    has_strong_delivery = any(term.casefold().replace(" ", "") in compact for term in _STRONG_DELIVERY_TERMS)
    if has_supply_scope and not has_strong_delivery:
        return False
    if any(term.casefold().replace(" ", "") in compact for term in COMMERCIAL_MANUAL_ONLY_TERMS):
        return True
    if any(term.casefold().replace(" ", "") in compact for term in _COMMERCIAL_MANUAL_EXACT_COMPOUND_TERMS):
        return True
    if any(term.casefold().replace(" ", "") in compact for term in _CONDITIONAL_COMMERCIAL_MANUAL_TERMS):
        return any(term.casefold().replace(" ", "") in compact for term in _COMMERCIAL_MANUAL_CONTEXT_TERMS)
    if any(term.casefold().replace(" ", "") in compact for term in _TECHNICAL_LIST_ONLY_TERMS):
        return False
    return False


def classify_block_taxonomy(
    *,
    content: str,
    heading_path: str | None,
    chunk_type: str,
    front_matter: bool = False,
    needs_asset_lookup: bool = False,
) -> dict[str, str]:
    heading_text = str(heading_path or "")
    content_text = str(content or "")[:2000]
    haystack = f"{heading_text}\n{content_text}".casefold()

    section_type = _match_weighted_rules(
        heading_text=heading_text,
        content_text=content_text,
        rules=_SECTION_TYPE_RULES,
        default="unknown",
    )
    if section_type not in {"supply_scope", "bom_or_supply_list"} and is_commercial_manual_section_text(
        heading_text,
        content_text,
    ):
        section_type = "commercial_manual_only"
    heading_lower = heading_text.casefold()
    if any(keyword.casefold() in heading_lower for keyword in _OVERALL_SOLUTION_HEADING_TERMS) and not any(
        keyword.casefold() in heading_lower for keyword in _SPECIALIZED_HEADING_TERMS
    ):
        section_type = "overall_solution"
    equipment_type = _match_weighted_rules(
        heading_text=heading_text,
        content_text=content_text,
        rules=_EQUIPMENT_TYPE_RULES,
        default="generic",
    )
    content_form = _classify_content_form(
        haystack=haystack,
        chunk_type=chunk_type,
        front_matter=front_matter,
        needs_asset_lookup=needs_asset_lookup,
        content_text=content_text,
        section_type=section_type,
    )

    heading_lower = heading_text.casefold()
    if "上位机" in heading_lower and "接口" in heading_lower:
        section_type = "communication_interface"
    elif "性能要求" in heading_lower:
        if "变频" in heading_lower:
            section_type = "vfd_spec"
        elif any(token in heading_lower for token in ("软起动", "软启动", "液阻", "水电阻", "固态软起", "自耦变")):
            section_type = "starter_spec"
        elif "电机" in heading_lower:
            section_type = "motor_spec"
        elif "变压器" in heading_lower:
            section_type = "transformer_spec"

    return {
        "section_type": section_type,
        "equipment_type": equipment_type,
        "content_form": content_form,
        "taxonomy_source": "heuristic",
    }


def infer_target_taxonomy(section: dict[str, Any]) -> dict[str, Any]:
    title = str(section.get("title") or "")
    purpose = str(section.get("purpose") or section.get("description") or "")
    keywords = " ".join(str(item) for item in (section.get("keywords") or []) if item)
    expected_types = {str(item).lower() for item in (section.get("expected_evidence_types") or []) if item}
    prefer_table = bool(expected_types & {"table", "parameter"})
    taxonomy = classify_block_taxonomy(
        content=" ".join(part for part in [purpose, keywords] if part),
        heading_path=title,
        chunk_type="TABLE" if prefer_table else "PLAIN",
        front_matter=False,
        needs_asset_lookup=bool(section.get("asset_required")),
    )
    explicit_section_type = str(section.get("target_section_type") or "").strip().lower()
    explicit_equipment_type = str(section.get("target_equipment_type") or "").strip().lower()
    if explicit_section_type:
        taxonomy["section_type"] = explicit_section_type
    if explicit_equipment_type:
        taxonomy["equipment_type"] = explicit_equipment_type
    hint_text = f"{title}\n{purpose}\n{keywords}".casefold()
    if prefer_table and taxonomy["section_type"] == "supply_scope":
        if any(keyword.casefold() in hint_text for keyword in ("清单", "设备", "bom", "物料")):
            taxonomy["section_type"] = "bom_or_supply_list"

    if taxonomy["section_type"] == "commercial_manual_only":
        preferred_content_forms = {"narrative", "bom_table"}
    else:
        preferred_content_forms = {"narrative"}
    if taxonomy["section_type"] in {"bom_or_supply_list", "supply_scope"}:
        preferred_content_forms.add("bom_table")
    if prefer_table and taxonomy["section_type"] != "commercial_manual_only":
        preferred_content_forms.add("parameter_table")
    if bool(expected_types & {"interface", "communication"}):
        preferred_content_forms.add("interface_table")
    if bool(expected_types & {"figure", "diagram"}):
        preferred_content_forms.add("figure")
    if bool(expected_types & {"formula", "equation"}):
        preferred_content_forms.add("formula")

    return {
        **taxonomy,
        "preferred_content_forms": preferred_content_forms,
        "support_content_forms": support_content_forms(taxonomy["section_type"]),
        "related_section_types": related_section_types(taxonomy["section_type"]),
    }


def extract_taxonomy_hints(*texts: str) -> list[str]:
    haystack = "\n".join(str(text or "") for text in texts)
    hints: list[str] = []
    for _, keywords in (*_SECTION_TYPE_RULES, *_EQUIPMENT_TYPE_RULES):
        for keyword in keywords:
            if text_contains_domain_term(haystack, keyword) and keyword not in hints:
                hints.append(keyword)
    return hints


def related_section_types(section_type: str) -> set[str]:
    return set(_RELATED_SECTION_TYPES.get(str(section_type or ""), set()))


def support_content_forms(section_type: str) -> set[str]:
    normalized = str(section_type or "").lower()
    return set(_SUPPORT_CONTENT_FORMS.get(normalized, {"narrative"}))


def heading_focus_adjustment(*, target_section_type: str, heading_text: str) -> tuple[float, list[str]]:
    normalized_type = str(target_section_type or "").lower()
    haystack = str(heading_text or "").casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    if not normalized_type or normalized_type == "unknown" or not haystack:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    if any(
        text_contains_domain_term(haystack, term) or term.casefold().replace(" ", "") in compact_haystack
        for term in _HEADING_FOCUS_TERMS.get(normalized_type, ())
    ):
        score += 0.14
        reasons.append("heading_focus_match")
    if any(
        text_contains_domain_term(haystack, term) or term.casefold().replace(" ", "") in compact_haystack
        for term in _HEADING_NOISE_TERMS.get(normalized_type, ())
    ):
        score -= 0.22
        reasons.append("heading_noise_penalty")
    return score, reasons


def extract_heading_family(heading_text: str) -> tuple[str, ...]:
    match = _HEADING_NUMBER_PATTERN.match(str(heading_text or ""))
    if not match:
        return ()
    return tuple(part for part in match.group(1).split(".") if part)


def heading_family_similarity(anchor_heading: str, candidate_heading: str) -> float:
    anchor_family = extract_heading_family(anchor_heading)
    candidate_family = extract_heading_family(candidate_heading)
    if not anchor_family or not candidate_family:
        return 0.0
    if anchor_family[0] != candidate_family[0]:
        return 0.0
    if len(anchor_family) >= 2 and len(candidate_family) >= 2 and anchor_family[:2] == candidate_family[:2]:
        return 0.2
    return 0.12


def heading_looks_like_document_title(heading_text: str) -> bool:
    text = str(heading_text or "").strip()
    if not text:
        return False
    if _FILE_LIKE_HEADING_PATTERN.search(text):
        return True
    lowered = text.casefold()
    return lowered in {"目录", "目 录", "contents"}


def content_form_is_table(content_form: str) -> bool:
    return content_form in {"parameter_table", "bom_table", "interface_table", "protection_table"}


def _match_weighted_rules(
    *,
    heading_text: str,
    content_text: str,
    rules: tuple[tuple[str, tuple[str, ...]], ...],
    default: str,
) -> str:
    best_label = default
    best_score = 0

    for label, keywords in rules:
        heading_match = any(text_contains_domain_term(heading_text, keyword) for keyword in keywords)
        content_match = any(text_contains_domain_term(content_text, keyword) for keyword in keywords)
        score = 0
        if heading_match:
            score += 5
        if content_match:
            score += 1
        if score > best_score:
            best_label = label
            best_score = score
    return best_label if best_score > 0 else default


def _classify_content_form(
    *,
    haystack: str,
    chunk_type: str,
    front_matter: bool,
    needs_asset_lookup: bool,
    content_text: str,
    section_type: str,
) -> str:
    if front_matter:
        return "page_furniture"
    if any(text_contains_domain_term(haystack, keyword) for keyword in _CERTIFICATE_TERMS):
        return "certificate"
    if str(chunk_type or "").upper() == "TABLE":
        if str(section_type or "").lower() == "communication_interface" and any(
            text_contains_domain_term(haystack, keyword) for keyword in _INTERFACE_TERMS
        ):
            return "interface_table"
        if any(text_contains_domain_term(haystack, keyword) for keyword in _BOM_TERMS):
            return "bom_table"
        if any(text_contains_domain_term(haystack, keyword) for keyword in _INTERFACE_TERMS):
            return "interface_table"
        if any(text_contains_domain_term(haystack, keyword) for keyword in _PROTECTION_TERMS):
            return "protection_table"
        return "parameter_table"
    if str(section_type or "").lower() == "communication_interface" and any(
        text_contains_domain_term(haystack, keyword) for keyword in _INTERFACE_TERMS
    ):
        return "narrative"
    if has_garbled_formula_text(content_text) or is_formula_like_text(content_text):
        return "formula"
    if needs_asset_lookup and any(text_contains_domain_term(haystack, keyword) for keyword in _FIGURE_TERMS):
        return "figure"
    return "narrative"
