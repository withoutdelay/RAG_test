from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.solution_snapshot import SolutionSnapshot


_INTERFACE_HINTS = ("接口", "通讯", "通信", "dcs", "plc", "自动化", "点表", "联锁信号")
_SUPPLY_HINTS = ("供货", "清单", "配置", "参数", "设备", "物料", "规格", "型号", "范围")
_ARCHITECTURE_HINTS = ("总体", "方案", "架构", "主回路", "系统", "装置", "拓扑")
_CONTROL_HINTS = ("控制", "联锁", "保护", "切换", "启动", "同步", "逻辑", "时序")
_DEMAND_HINTS = ("需求", "分析", "约束", "边界", "指标")
_IMPLEMENTATION_HINTS = ("实施", "排期", "进度", "里程碑", "调试", "验收", "投运")
_SERVICE_HINTS = ("售后", "服务", "培训", "维保", "质保", "巡检", "备件", "运维")

_CATALOG_CODE_LABELS = {
    "lci_sync_drive": "LCI 同步电机变频软起动系统",
    "support_equipment": "配套设备",
    "rectifier_transformer": "整流变压器",
    "excitation_cabinet": "励磁控制柜",
    "excitation_system": "励磁系统",
    "bypass_cabinet": "旁路柜",
    "bypass_unit": "旁路单元",
    "switchgear": "高压开关柜",
    "oil_station": "润滑油站",
    "cooler": "冷却系统",
    "lci": "LCI 主驱动",
    "fieldbus_adapter": "现场总线适配器",
    "oil_level": "油位",
    "oil_temperature": "油温",
    "supply_temperature": "供电温度",
    "excitation_build_wait_5s": "励磁建立等待 5s",
    "sync_switching": "同步切换",
}


async def get_preferred_solution_snapshot(
    *,
    session: AsyncSession,
    project_id: UUID,
) -> SolutionSnapshot | None:
    result = await session.scalars(
        select(SolutionSnapshot)
        .where(SolutionSnapshot.project_id == project_id)
        .order_by(
            SolutionSnapshot.version.desc(),
            SolutionSnapshot.confirmed_by_user.desc(),
            SolutionSnapshot.created_at.desc(),
        )
        .limit(1)
    )
    return result.first()


def _solution_summary_text(solution_snapshot: SolutionSnapshot | None) -> str:
    if solution_snapshot is None:
        return ""

    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    primary_name = str(primary.get("name") or "").strip()
    if not primary_name:
        return _normalize_solution_summary_line(str(getattr(solution_snapshot, "solution_summary", "") or "").strip())

    scenario_parts: list[str] = []
    rated_voltage = str(primary.get("rated_voltage") or "").strip()
    if rated_voltage:
        scenario_parts.append(rated_voltage)
    rated_power = _format_power(primary.get("rated_power_kw"))
    if rated_power:
        scenario_parts.append(rated_power)

    summary = f"本项目方案围绕 {primary_name} 组织主回路、接口与供货配置"
    if scenario_parts:
        summary += f"，适配 {' / '.join(scenario_parts)} 场景"
    config_name = str(primary.get("config") or "").strip()
    if config_name:
        summary += f"，当前按 {config_name} 组织设备成套"
    protocol = _interface_protocol(solution_snapshot)
    if protocol:
        summary += f"，控制接口按 {protocol} 规划"
    return _normalize_customer_sentence(_ensure_terminal_punctuation(summary))


def _normalize_solution_summary_line(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = normalized.replace("推荐采用", "当前方案采用")
    normalized = normalized.replace("作为主驱动基线", "作为主驱动")
    normalized = normalized.replace("主驱动基线", "主驱动")
    normalized = normalized.replace("本轮默认采用", "当前按")
    normalized = normalized.replace("本轮按", "当前按")
    normalized = normalized.replace("基线", "")
    normalized = " ".join(normalized.split())
    normalized = normalized.replace(" ，", "，").replace(" 。", "。")
    return _normalize_customer_sentence(_ensure_terminal_punctuation(normalized))


def build_solution_outline_params(solution_snapshot: SolutionSnapshot | None) -> dict[str, Any]:
    if solution_snapshot is None:
        return {}

    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    selection_reason = _selection_reason(solution_snapshot)
    matching_signals = _string_list(selection_reason.get("matching_signals"))
    risk_flags = _string_list(selection_reason.get("risk_flags"))
    constraints = _string_list(getattr(solution_snapshot, "key_constraints", None))
    questions = _string_list(getattr(solution_snapshot, "open_questions", None))
    compatibility_summary = _format_compatibility_summary(solution_snapshot)
    model_matches = _catalog_model_matches(solution_snapshot)
    interface_entries = _catalog_interface_entries(solution_snapshot)
    params: dict[str, Any] = {
        "solution_snapshot_version": solution_snapshot.version,
        "solution_snapshot_status": "confirmed" if solution_snapshot.confirmed_by_user else "draft",
    }
    summary_text = _solution_summary_text(solution_snapshot)
    if summary_text:
        params["solution_summary"] = summary_text
    if primary.get("name"):
        params["primary_product"] = primary.get("name")
    if primary.get("family"):
        params["primary_product_family"] = primary.get("family")
    if primary.get("rated_voltage"):
        params["voltage_level"] = primary.get("rated_voltage")
    if primary.get("rated_power_kw") not in (None, ""):
        params["power_rating"] = f"{primary.get('rated_power_kw')}kW"
    protocol = _interface_protocol(solution_snapshot)
    if protocol:
        params["dcs_protocol"] = protocol
    if products:
        params["selected_products"] = "；".join(
            f"{item.get('role') or '设备'}:{item.get('name') or '未命名'} x{item.get('quantity') or 1}"
            for item in products
        )
        families = _selected_family_labels(products)
        if families:
            params["selected_product_families"] = " / ".join(families)
    if model_matches:
        params["selected_model_numbers"] = " / ".join(
            str(item.get("model_number") or "").strip()
            for item in model_matches
            if str(item.get("model_number") or "").strip()
        )
        primary_model = model_matches[0]
        if primary_model.get("model_number"):
            params["primary_model_number"] = primary_model.get("model_number")
        model_summary = _format_model_summary(solution_snapshot)
        if model_summary:
            params["catalog_model_summary"] = model_summary
    if interface_entries:
        interface_summary = _format_catalog_interface_summary(solution_snapshot)
        if interface_summary:
            params["catalog_interface_summary"] = interface_summary
    chapters = _string_list(solution_snapshot.suggested_chapters)
    if chapters:
        params["suggested_chapters"] = " / ".join(chapters)
    if matching_signals:
        params["matching_signals"] = " / ".join(matching_signals)
    if compatibility_summary:
        params["compatibility_summary"] = compatibility_summary
    if risk_flags:
        params["solution_risk_flags"] = " / ".join(risk_flags)
    if constraints:
        params["solution_constraints"] = " / ".join(constraints)
    if questions:
        params["solution_open_questions"] = " / ".join(questions)
    return params


def build_solution_writer_params(solution_snapshot: SolutionSnapshot | None) -> dict[str, Any]:
    if solution_snapshot is None:
        return {}

    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    selection_reason = _selection_reason(solution_snapshot)
    matching_signals = _string_list(selection_reason.get("matching_signals"))
    risk_flags = _string_list(selection_reason.get("risk_flags"))
    constraints = _string_list(getattr(solution_snapshot, "key_constraints", None))
    questions = _string_list(getattr(solution_snapshot, "open_questions", None))
    compatibility_summary = _format_compatibility_summary(solution_snapshot)
    model_matches = _catalog_model_matches(solution_snapshot)
    interface_entries = _catalog_interface_entries(solution_snapshot)
    params: dict[str, Any] = {
        "solution_snapshot_version": solution_snapshot.version,
    }
    summary_text = _solution_summary_text(solution_snapshot)
    if summary_text:
        params["solution_summary"] = summary_text
    if primary.get("name"):
        params["primary_product"] = primary.get("name")
    if primary.get("family"):
        params["primary_product_family"] = primary.get("family")
    if primary.get("rated_voltage"):
        params["voltage_level"] = primary.get("rated_voltage")
    if primary.get("rated_power_kw") not in (None, ""):
        params["power_rating"] = f"{primary.get('rated_power_kw')}kW"
    protocol = _interface_protocol(solution_snapshot)
    if protocol:
        params["dcs_protocol"] = protocol
    if products:
        params["selected_products"] = "；".join(
            f"{item.get('role') or '设备'}:{item.get('name') or '未命名'} x{item.get('quantity') or 1}"
            for item in products
        )
        families = _selected_family_labels(products)
        if families:
            params["selected_product_families"] = " / ".join(families)
    if model_matches:
        params["selected_model_numbers"] = " / ".join(
            str(item.get("model_number") or "").strip()
            for item in model_matches
            if str(item.get("model_number") or "").strip()
        )
        primary_model = model_matches[0]
        if primary_model.get("model_number"):
            params["primary_model_number"] = primary_model.get("model_number")
        model_summary = _format_model_summary(solution_snapshot)
        if model_summary:
            params["catalog_model_summary"] = model_summary
    if interface_entries:
        interface_summary = _format_catalog_interface_summary(solution_snapshot)
        if interface_summary:
            params["catalog_interface_summary"] = interface_summary
    if matching_signals:
        params["matching_signals"] = " / ".join(matching_signals)
    if compatibility_summary:
        params["compatibility_summary"] = compatibility_summary
    if risk_flags:
        params["solution_risk_flags"] = " / ".join(risk_flags)
    if constraints:
        params["solution_constraints"] = " / ".join(constraints)
    if questions:
        params["solution_open_questions"] = " / ".join(questions)
    return params


def render_solution_outline_context(solution_snapshot: SolutionSnapshot | None) -> str:
    if solution_snapshot is None:
        return ""

    products = _selected_products(solution_snapshot)
    chapters = _string_list(solution_snapshot.suggested_chapters)
    constraints = _string_list(solution_snapshot.key_constraints)
    questions = _string_list(solution_snapshot.open_questions)
    protocol = _interface_protocol(solution_snapshot)
    selection_reason = _selection_reason(solution_snapshot)
    matching_signals = _string_list(selection_reason.get("matching_signals"))
    risk_flags = _string_list(selection_reason.get("risk_flags"))
    compatibility_lines = _compatibility_lines(solution_snapshot)
    model_matches = _catalog_model_matches(solution_snapshot)
    interface_entries = _catalog_interface_entries(solution_snapshot)
    summary_text = _solution_summary_text(solution_snapshot)

    lines = [
        f"方案快照版本：v{solution_snapshot.version}" + ("（已确认）" if solution_snapshot.confirmed_by_user else "（待确认）"),
    ]
    if summary_text:
        lines.append(f"方案摘要：{summary_text}")
    if products:
        lines.append("当前设备配置：")
        lines.extend(
            f"- {item.get('role') or '设备'}：{item.get('name') or '未命名'}，"
            f"{item.get('rated_voltage') or '电压待确认'}，"
            f"{_format_power(item.get('rated_power_kw'))}，"
            f"数量 {item.get('quantity') or 1}，"
            f"配置 {item.get('config') or '标准配置'}"
            for item in products
        )
        families = _selected_family_labels(products)
        if families:
            lines.append(f"涉及产品族：{' / '.join(families)}")
    if model_matches:
        lines.append("目录型号映射：")
        lines.extend(
            f"- {item.get('series_name') or item.get('series_code')}：{item.get('model_number')}"
            + (f"，{item.get('rated_voltage')}" if item.get("rated_voltage") else "")
            + (f"，{item.get('rated_power_kw')}kW" if item.get("rated_power_kw") not in (None, "") else "")
            + (f"，来源 {item.get('source_material_key')}" if item.get("source_material_key") else "")
            for item in model_matches
        )
    if protocol:
        lines.append(f"接口协议：{protocol}")
    if interface_entries:
        lines.append("目录接口定义：")
        lines.extend(
            f"- {item.get('series_name') or item.get('series_code')} / {_interface_type_label(str(item.get('interface_type') or ''))}"
            + (f" / {item.get('protocol')}" if item.get("protocol") else "")
            + (f" / 来源 {item.get('source_material_key')}" if item.get("source_material_key") else "")
            for item in interface_entries
        )
    if matching_signals:
        lines.append("命中信号：")
        lines.extend(f"- {item}" for item in matching_signals)
    if compatibility_lines:
        lines.append("产品族兼容与配套规则：")
        lines.extend(compatibility_lines)
    if chapters:
        lines.append("建议章节：")
        lines.extend(f"- {chapter}" for chapter in chapters)
    if constraints:
        lines.append("关键约束：")
        lines.extend(f"- {item}" for item in constraints)
    if risk_flags:
        lines.append("风险提示：")
        lines.extend(f"- {item}" for item in risk_flags)
    if questions:
        lines.append("待确认项：")
        lines.extend(f"- {item}" for item in questions)
    return "\n".join(line for line in lines if line).strip()


def build_solution_section_context(
    *,
    section: dict[str, Any],
    solution_snapshot: SolutionSnapshot | None,
) -> str:
    if solution_snapshot is None:
        return ""

    products = _selected_products(solution_snapshot)
    constraints = _string_list(solution_snapshot.key_constraints)
    questions = _string_list(solution_snapshot.open_questions)
    compatibility_lines = _compatibility_lines(solution_snapshot)
    model_matches = _catalog_model_matches(solution_snapshot)
    interface_entries = _catalog_interface_entries(solution_snapshot)
    text = _section_signal_text(section)
    summary_text = _solution_summary_text(solution_snapshot)

    parts: list[str] = ["方案快照参考："]
    if summary_text:
        parts.append(f"- 当前方案要点：{summary_text}")
    if products and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        parts.extend(
            [
                "",
                "当前设备配置：",
                _render_product_table(products),
            ]
        )
    elif products:
        parts.extend(
            [
                "",
                "核心设备：",
                *[
                    f"- {item.get('role') or '设备'}：{item.get('name') or '未命名'}，"
                    f"配置 {item.get('config') or '标准配置'}，"
                    f"数量 {item.get('quantity') or 1}"
                    for item in products
                ],
            ]
        )

    if model_matches and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        parts.extend(
            [
                "",
                "目录型号映射：",
                _render_model_table(model_matches),
            ]
        )

    if _is_demand_section(section):
        demand_lines = _render_demand_analysis_lines(solution_snapshot)
        if demand_lines:
            parts.extend(["", "需求与约束：", *demand_lines])

    if _matches_any(text, _INTERFACE_HINTS):
        parts.extend(
            [
                "",
                "接口计划：",
                _render_interface_table(solution_snapshot),
            ]
        )
        if interface_entries:
            parts.extend(
                [
                    "",
                    "目录接口定义：",
                    _render_interface_registry_table(interface_entries),
                ]
            )
        notes = _interface_notes(solution_snapshot)
        if notes:
            parts.append(f"- 接口备注：{notes}")

    if (_matches_any(text, _CONTROL_HINTS) or _matches_any(text, _ARCHITECTURE_HINTS)) and constraints:
        parts.extend(["", "关键约束：", *[f"- {item}" for item in constraints]])

    if (
        compatibility_lines
        and (
            _matches_any(text, _CONTROL_HINTS)
            or _matches_any(text, _ARCHITECTURE_HINTS)
            or _matches_any(text, _SUPPLY_HINTS)
        )
    ):
        parts.extend(["", "产品族兼容与配套规则：", *compatibility_lines])

    if (_matches_any(text, _CONTROL_HINTS) or _matches_any(text, _SUPPLY_HINTS)) and questions:
        parts.extend(["", "待确认项：", *[f"- {item}" for item in questions]])

    if _matches_any(text, _IMPLEMENTATION_HINTS):
        implementation_lines = _render_implementation_plan_lines(solution_snapshot)
        if implementation_lines:
            parts.extend(["", "实施安排要点：", *implementation_lines])

    if _matches_any(text, _SERVICE_HINTS):
        service_lines = _render_service_support_lines(solution_snapshot)
        if service_lines:
            parts.extend(["", "服务保障要点：", *service_lines])

    return "\n".join(part for part in parts if part).strip()


def build_solution_section_citations(
    *,
    section: dict[str, Any],
    solution_snapshot: SolutionSnapshot | None,
) -> list[dict[str, Any]]:
    if solution_snapshot is None:
        return []
    excerpt = _solution_summary_text(solution_snapshot)
    if not excerpt:
        return []
    return [
        {
            "evidence_id": f"solution:{solution_snapshot.id}",
            "source_doc_id": str(solution_snapshot.id),
            "source_title": f"方案快照 v{solution_snapshot.version}",
            "heading_path": [str(section.get("title") or "方案快照参考")],
            "relevance_score": 1.0,
            "type": "solution_snapshot",
            "excerpt": excerpt[:220],
        }
    ]


def build_solution_reusable_blocks(
    *,
    section: dict[str, Any],
    solution_snapshot: SolutionSnapshot | None,
) -> list[dict[str, Any]]:
    if solution_snapshot is None:
        return []

    text = _section_signal_text(section)
    products = _selected_products(solution_snapshot)
    blocks: list[dict[str, Any]] = []

    if _should_include_snapshot_summary_block(section):
        summary_block = {
            "block_id": f"solution:{solution_snapshot.id}:summary",
            "source_doc_id": str(solution_snapshot.id),
            "source_title": f"方案快照 v{solution_snapshot.version}",
            "source_section_id": "summary",
            "section_path": "方案快照 > 方案要点",
            "source_heading": "方案要点",
            "heading_path": ["方案快照", "方案要点"],
            "content_md": _solution_summary_text(solution_snapshot),
            "block_type": "section",
            "reusability_score": 0.99,
            "selection_score": 1.25,
            "selection_reasons": ["confirmed_solution_snapshot"],
            "customer_specificity_score": 0.9,
            "asset_dependency_level": "low",
            "must_replace_fields": [],
            "banned_terms": [],
            "must_not_copy_spans": [],
            "metadata": {"source_type": "solution_snapshot", "front_matter": True},
        }
        if summary_block["content_md"]:
            blocks.append(summary_block)

    if _is_demand_section(section):
        demand_content = _render_demand_analysis_block(solution_snapshot)
        if demand_content:
            blocks.append(
                {
                    "block_id": f"solution:{solution_snapshot.id}:demand",
                    "source_doc_id": str(solution_snapshot.id),
                    "source_title": f"方案快照 v{solution_snapshot.version}",
                    "source_section_id": "demand",
                    "section_path": "方案快照 > 需求拆解",
                    "source_heading": "需求拆解",
                    "heading_path": ["方案快照", "需求拆解"],
                    "content_md": demand_content,
                    "block_type": "section",
                    "reusability_score": 0.99,
                    "selection_score": 1.33,
                    "selection_reasons": ["solution_requirement_summary"],
                    "customer_specificity_score": 0.95,
                    "asset_dependency_level": "low",
                    "must_replace_fields": [],
                    "banned_terms": [],
                    "must_not_copy_spans": [],
                    "metadata": {"source_type": "solution_snapshot", "content_form": "narrative"},
                }
            )

    if products and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        blocks.append(
            {
                "block_id": f"solution:{solution_snapshot.id}:products",
                "source_doc_id": str(solution_snapshot.id),
                "source_title": f"方案快照 v{solution_snapshot.version}",
                "source_section_id": "products",
                "section_path": "方案快照 > 设备配置清单",
                "source_heading": "设备配置清单",
                "heading_path": ["方案快照", "设备配置清单"],
                "content_md": _render_product_table(products),
                "block_type": "table",
                "reusability_score": 0.98,
                "selection_score": 1.32,
                "selection_reasons": ["solution_product_matrix"],
                "customer_specificity_score": 0.95,
                "asset_dependency_level": "low",
                "must_replace_fields": [],
                "banned_terms": [],
                "must_not_copy_spans": [],
                "metadata": {"source_type": "solution_snapshot", "content_form": "bom_table"},
            }
        )

    if _matches_any(text, _INTERFACE_HINTS):
        blocks.append(
            {
                "block_id": f"solution:{solution_snapshot.id}:interface",
                "source_doc_id": str(solution_snapshot.id),
                "source_title": f"方案快照 v{solution_snapshot.version}",
                "source_section_id": "interface",
                "section_path": "方案快照 > 接口计划",
                "source_heading": "接口计划",
                "heading_path": ["方案快照", "接口计划"],
                "content_md": _render_interface_table(solution_snapshot),
                "block_type": "table",
                "reusability_score": 0.98,
                "selection_score": 1.34,
                "selection_reasons": ["solution_interface_matrix"],
                "customer_specificity_score": 0.95,
                "asset_dependency_level": "low",
                "must_replace_fields": [],
                "banned_terms": [],
                "must_not_copy_spans": [],
                "metadata": {"source_type": "solution_snapshot", "content_form": "interface_table"},
            }
        )
        interface_entries = _catalog_interface_entries(solution_snapshot)
        if interface_entries:
            blocks.append(
                {
                    "block_id": f"solution:{solution_snapshot.id}:interface_registry",
                    "source_doc_id": str(solution_snapshot.id),
                    "source_title": f"方案快照 v{solution_snapshot.version}",
                    "source_section_id": "interface_registry",
                    "section_path": "方案快照 > 目录接口定义",
                    "source_heading": "目录接口定义",
                    "heading_path": ["方案快照", "目录接口定义"],
                    "content_md": _render_interface_registry_table(interface_entries),
                    "block_type": "table",
                    "reusability_score": 0.98,
                    "selection_score": 1.35,
                    "selection_reasons": ["solution_interface_registry"],
                    "customer_specificity_score": 0.95,
                    "asset_dependency_level": "low",
                    "must_replace_fields": [],
                    "banned_terms": [],
                    "must_not_copy_spans": [],
                    "metadata": {"source_type": "solution_snapshot", "content_form": "interface_registry"},
                }
            )

    model_matches = _catalog_model_matches(solution_snapshot)
    if model_matches and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        blocks.append(
            {
                "block_id": f"solution:{solution_snapshot.id}:models",
                "source_doc_id": str(solution_snapshot.id),
                "source_title": f"方案快照 v{solution_snapshot.version}",
                "source_section_id": "models",
                "section_path": "方案快照 > 目录型号映射",
                "source_heading": "目录型号映射",
                "heading_path": ["方案快照", "目录型号映射"],
                "content_md": _render_model_table(model_matches),
                "block_type": "table",
                "reusability_score": 0.98,
                "selection_score": 1.33,
                "selection_reasons": ["solution_model_registry"],
                "customer_specificity_score": 0.95,
                "asset_dependency_level": "low",
                "must_replace_fields": [],
                "banned_terms": [],
                "must_not_copy_spans": [],
                "metadata": {"source_type": "solution_snapshot", "content_form": "model_registry"},
            }
        )

    if _matches_any(text, _CONTROL_HINTS):
        constraints = _string_list(solution_snapshot.key_constraints)
        questions = _string_list(solution_snapshot.open_questions)
        risk_lines = []
        if constraints:
            risk_lines.extend(["关键约束：", *[f"- {item}" for item in constraints]])
        if questions:
            risk_lines.extend(["待确认项：", *[f"- {item}" for item in questions]])
        if risk_lines:
            blocks.append(
                {
                    "block_id": f"solution:{solution_snapshot.id}:constraints",
                    "source_doc_id": str(solution_snapshot.id),
                    "source_title": f"方案快照 v{solution_snapshot.version}",
                    "source_section_id": "constraints",
                    "section_path": "方案快照 > 约束与确认项",
                    "source_heading": "约束与确认项",
                    "heading_path": ["方案快照", "约束与确认项"],
                    "content_md": "\n".join(risk_lines),
                    "block_type": "section",
                    "reusability_score": 0.96,
                    "selection_score": 1.28,
                    "selection_reasons": ["solution_constraints"],
                    "customer_specificity_score": 0.95,
                    "asset_dependency_level": "low",
                    "must_replace_fields": [],
                    "banned_terms": [],
                    "must_not_copy_spans": [],
                    "metadata": {"source_type": "solution_snapshot"},
                }
            )

    compatibility_lines = _compatibility_lines(solution_snapshot)
    if compatibility_lines and (
        _matches_any(text, _CONTROL_HINTS)
        or _matches_any(text, _ARCHITECTURE_HINTS)
        or _matches_any(text, _SUPPLY_HINTS)
    ):
        blocks.append(
            {
                "block_id": f"solution:{solution_snapshot.id}:compatibility",
                "source_doc_id": str(solution_snapshot.id),
                "source_title": f"方案快照 v{solution_snapshot.version}",
                "source_section_id": "compatibility",
                "section_path": "方案快照 > 产品族兼容与配套规则",
                "source_heading": "产品族兼容与配套规则",
                "heading_path": ["方案快照", "产品族兼容与配套规则"],
                "content_md": "\n".join(["产品族兼容与配套规则：", *compatibility_lines]),
                "block_type": "section",
                "reusability_score": 0.96,
                "selection_score": 1.31,
                "selection_reasons": ["solution_compatibility_rules"],
                "customer_specificity_score": 0.95,
                "asset_dependency_level": "low",
                "must_replace_fields": [],
                "banned_terms": [],
                "must_not_copy_spans": [],
                "metadata": {"source_type": "solution_snapshot", "content_form": "compatibility_rules"},
            }
        )

    if _matches_any(text, _IMPLEMENTATION_HINTS):
        implementation_content = _render_implementation_plan_block(solution_snapshot)
        if implementation_content:
            blocks.append(
                {
                    "block_id": f"solution:{solution_snapshot.id}:implementation",
                    "source_doc_id": str(solution_snapshot.id),
                    "source_title": f"方案快照 v{solution_snapshot.version}",
                    "source_section_id": "implementation",
                    "section_path": "方案快照 > 实施里程碑与调试安排",
                    "source_heading": "实施里程碑与调试安排",
                    "heading_path": ["方案快照", "实施里程碑与调试安排"],
                    "content_md": implementation_content,
                    "block_type": "section",
                    "reusability_score": 0.97,
                    "selection_score": 1.29,
                    "selection_reasons": ["solution_implementation_plan"],
                    "customer_specificity_score": 0.9,
                    "asset_dependency_level": "low",
                    "must_replace_fields": [],
                    "banned_terms": [],
                    "must_not_copy_spans": [],
                    "metadata": {"source_type": "solution_snapshot", "content_form": "narrative"},
                }
            )

    if _matches_any(text, _SERVICE_HINTS):
        service_content = _render_service_support_block(solution_snapshot)
        if service_content:
            blocks.append(
                {
                    "block_id": f"solution:{solution_snapshot.id}:service",
                    "source_doc_id": str(solution_snapshot.id),
                    "source_title": f"方案快照 v{solution_snapshot.version}",
                    "source_section_id": "service",
                    "section_path": "方案快照 > 服务保障与培训要点",
                    "source_heading": "服务保障与培训要点",
                    "heading_path": ["方案快照", "服务保障与培训要点"],
                    "content_md": service_content,
                    "block_type": "section",
                    "reusability_score": 0.96,
                    "selection_score": 1.27,
                    "selection_reasons": ["solution_service_support"],
                    "customer_specificity_score": 0.78,
                    "asset_dependency_level": "low",
                    "must_replace_fields": [],
                    "banned_terms": [],
                    "must_not_copy_spans": [],
                    "metadata": {"source_type": "solution_snapshot", "content_form": "narrative"},
                }
            )

    return blocks


def _section_signal_text(section: dict[str, Any]) -> str:
    return " ".join(
        [
            str(section.get("title") or "").strip(),
            str(section.get("purpose") or section.get("description") or "").strip(),
            " ".join(str(item).strip() for item in (section.get("keywords") or []) if str(item).strip()),
        ]
    ).lower()


def _is_demand_section(section: dict[str, Any]) -> bool:
    title = str(section.get("title") or "").strip().lower()
    return _matches_any(title, _DEMAND_HINTS)


def _matches_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(hint.lower() in lowered for hint in hints)


def _selected_products(solution_snapshot: SolutionSnapshot | None) -> list[dict[str, Any]]:
    products = getattr(solution_snapshot, "selected_products", None)
    if not isinstance(products, list):
        return []
    return [item for item in products if isinstance(item, dict)]


def _selection_reason(solution_snapshot: SolutionSnapshot | None) -> dict[str, Any]:
    selection_reason = getattr(solution_snapshot, "selection_reason", None)
    if not isinstance(selection_reason, dict):
        return {}
    return selection_reason


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _selected_family_labels(products: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for item in products:
        label = str(item.get("family") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _compatibility_actions(solution_snapshot: SolutionSnapshot | None) -> list[dict[str, Any]]:
    actions = _selection_reason(solution_snapshot).get("compatibility_actions")
    if not isinstance(actions, list):
        return []
    return [item for item in actions if isinstance(item, dict)]


def _catalog_model_matches(solution_snapshot: SolutionSnapshot | None) -> list[dict[str, Any]]:
    matches = _selection_reason(solution_snapshot).get("catalog_model_matches")
    if not isinstance(matches, list):
        return []
    return [item for item in matches if isinstance(item, dict)]


def _catalog_interface_entries(solution_snapshot: SolutionSnapshot | None) -> list[dict[str, Any]]:
    interface_plan = getattr(solution_snapshot, "interface_plan", None)
    if not isinstance(interface_plan, dict):
        return []
    entries = interface_plan.get("catalog_interface_entries")
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _catalog_code_label(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered in _CATALOG_CODE_LABELS:
        return _CATALOG_CODE_LABELS[lowered]
    return normalized.replace("_", " ")


def _catalog_code_labels(values: list[str]) -> list[str]:
    return [_catalog_code_label(item) for item in values if _catalog_code_label(item)]


def _humanize_signal_summary_item(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "=" not in text:
        return _catalog_code_label(text)

    key, raw_value = [part.strip() for part in text.split("=", 1)]
    values = [item.strip() for item in raw_value.split(",") if item.strip()]

    if key == "adapter":
        adapter = _catalog_code_label(raw_value)
        return f"配置{adapter}" if adapter else ""
    if key == "coverage":
        labels = _catalog_code_labels(values)
        return f"接口范围覆盖 {'、'.join(labels)}" if labels else ""
    if key == "digital_input_voltage":
        return f"数字量输入电压 {raw_value}"
    if key == "analog_outputs":
        metrics: list[str] = []
        signal_type = ""
        for item in values:
            item_parts = item.split(" ", 1)
            if len(item_parts) == 2:
                signal_type = item_parts[0]
                metrics.append(_catalog_code_label(item_parts[1]))
            else:
                metrics.append(_catalog_code_label(item))
        metric_text = "、".join(metric for metric in metrics if metric)
        if metric_text and signal_type:
            return f"模拟量输出包括{metric_text}（{signal_type}）"
        if metric_text:
            return f"模拟量输出包括{metric_text}"
        return ""
    if key == "control_sequence":
        labels = _catalog_code_labels(values)
        return f"控制时序包含{'、'.join(labels)}" if labels else ""
    if key == "supporting_systems":
        labels = _catalog_code_labels(values)
        return f"需联动{'、'.join(labels)}" if labels else ""

    humanized_value = "、".join(_catalog_code_labels(values)) if values else _catalog_code_label(raw_value)
    return f"{_catalog_code_label(key)} {humanized_value}".strip()


def _humanize_signal_summary_items(values: list[str]) -> list[str]:
    return [item for item in (_humanize_signal_summary_item(value) for value in values) if item]


def _compatibility_lines(solution_snapshot: SolutionSnapshot | None) -> list[str]:
    lines: list[str] = []
    for action in _compatibility_actions(solution_snapshot):
        relation_type = str(action.get("relation_type") or "recommended").strip().lower()
        target_family = _catalog_code_label(action.get("target_family_code") or "unknown")
        phrase_prefix = {
            "requires": "必需配套",
            "recommended": "建议配套",
            "optional": "可选配套",
            "conflicts_with": "冲突关系",
        }.get(relation_type, relation_type or "配套规则")
        if target_family == "配套设备":
            phrase_prefix = {
                "requires": "",
                "recommended": "建议补充",
                "optional": "可选补充",
                "conflicts_with": "冲突关系",
            }.get(relation_type, phrase_prefix)
            target_family = "成套配套设备"
        applies = bool(action.get("applies"))
        preferred = _string_list(action.get("preferred_series_codes"))
        optional = _string_list(action.get("optional_series_codes"))
        covered = _string_list(action.get("covered_series_codes"))
        added = _string_list(action.get("added_series_codes"))
        missing = _string_list(action.get("missing_series_codes"))
        condition = str(action.get("condition") or "").strip()
        relation_label = f"{phrase_prefix}{target_family}" if phrase_prefix else target_family

        if not applies:
            line = f"- {relation_label}：当前场景未触发。"
            if condition:
                line = f"- {relation_label}：当前场景未触发（条件：{condition}）。"
            lines.append(line)
            continue

        details: list[str] = []
        if added:
            details.append(f"已补齐{'、'.join(_catalog_code_labels(added))}")
        if covered:
            details.append(f"已覆盖{'、'.join(_catalog_code_labels(covered))}")
        if missing:
            details.append(f"目录中尚缺{'、'.join(_catalog_code_labels(missing))}")
        if not details and preferred:
            details.append(f"优先配置{'、'.join(_catalog_code_labels(preferred))}")
        if optional:
            details.append(f"可选配置包括{'、'.join(_catalog_code_labels(optional))}")
        if condition:
            details.append(f"适用条件为{condition}")
        if details:
            lines.append(f"- {relation_label}：{'；'.join(details)}。")
        else:
            lines.append(f"- {relation_label}。")
    return lines


def _format_compatibility_summary(solution_snapshot: SolutionSnapshot | None) -> str:
    lines = [line.removeprefix("- ").strip() for line in _compatibility_lines(solution_snapshot)]
    return " / ".join(lines)


def _format_model_summary(solution_snapshot: SolutionSnapshot | None) -> str:
    lines = [
        f"{item.get('series_name') or item.get('series_code')}:{item.get('model_number')}"
        for item in _catalog_model_matches(solution_snapshot)
        if str(item.get("model_number") or "").strip()
    ]
    return " / ".join(lines)


def _format_catalog_interface_summary(solution_snapshot: SolutionSnapshot | None) -> str:
    lines: list[str] = []
    for item in _catalog_interface_entries(solution_snapshot):
        series_name = str(item.get("series_name") or item.get("series_code") or "").strip() or "主驱动系统"
        interface_type = _interface_type_label(str(item.get("interface_type") or ""))
        protocol = str(item.get("protocol") or "").strip()
        signal_summary = "；".join(_humanize_signal_summary_items(_string_list(item.get("signal_summary"))))
        subject = f"{series_name}的 {interface_type}" if interface_type.startswith("I/O") else f"{series_name}的{interface_type}"
        if protocol and signal_summary:
            lines.append(f"{subject}采用 {protocol}，{signal_summary}")
        elif protocol:
            lines.append(f"{subject}采用 {protocol}")
        elif signal_summary:
            lines.append(f"{subject}方面，{signal_summary}")
        else:
            lines.append(subject)
    return " / ".join(line for line in lines if line)


def _render_demand_analysis_block(solution_snapshot: SolutionSnapshot | None) -> str:
    lines = _render_demand_analysis_lines(solution_snapshot)
    return "\n".join(lines).strip()


def _render_demand_analysis_lines(solution_snapshot: SolutionSnapshot | None) -> list[str]:
    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    protocol = _interface_protocol(solution_snapshot)
    constraints = _string_list(getattr(solution_snapshot, "key_constraints", None))
    risk_flags = _string_list(_selection_reason(solution_snapshot).get("risk_flags"))
    questions = _string_list(getattr(solution_snapshot, "open_questions", None))

    lines: list[str] = []
    if primary:
        scenario_parts = [
            str(primary.get("rated_voltage") or "").strip(),
            _format_power(primary.get("rated_power_kw")) if primary.get("rated_power_kw") not in (None, "") else "",
        ]
        scenario_text = " / ".join(part for part in scenario_parts if part)
        line = f"主驱动场景为 {primary.get('name') or '当前主驱动方案'}"
        if scenario_text:
            line += f"，适配 {scenario_text}"
        if primary.get("config"):
            line += f"，当前按{primary.get('config')}组织供货与切换边界"
        _append_unique_line(lines, f"- {_normalize_customer_sentence(_ensure_terminal_punctuation(line))}")
    if protocol:
        _append_unique_line(
            lines,
            f"- 接口要求：控制系统需接入 {protocol}，并明确站点划分、点表边界和调试前提。",
        )
    for item in constraints[:4]:
        normalized = _normalize_requirement_line(item)
        if normalized:
            _append_unique_line(lines, f"- {normalized}")
    for item in risk_flags[:2]:
        normalized = _normalize_requirement_line(item, default_prefix="重点确认")
        if normalized:
            _append_unique_line(lines, f"- {normalized}")
    for item in questions[:2]:
        normalized = str(item or "").strip()
        if normalized:
            _append_unique_line(
                lines,
                f"- {_normalize_customer_sentence(f'待进一步确认：{_ensure_terminal_punctuation(normalized)}')}",
            )
    return [_normalize_customer_sentence(line) for line in lines[:6] if _normalize_customer_sentence(line)]


def _render_implementation_plan_block(solution_snapshot: SolutionSnapshot | None) -> str:
    return "\n".join(_render_implementation_plan_lines(solution_snapshot)).strip()


def _render_implementation_plan_lines(solution_snapshot: SolutionSnapshot | None) -> list[str]:
    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    protocol = _interface_protocol(solution_snapshot)
    constraints = _string_list(getattr(solution_snapshot, "key_constraints", None))
    questions = _string_list(getattr(solution_snapshot, "open_questions", None))
    product_names = [str(item.get("name") or "").strip() for item in products if str(item.get("name") or "").strip()]

    equipment_name = str(primary.get("name") or "主驱动系统").strip() or "主驱动系统"
    scope_text = "、".join(product_names[:3]) if product_names else equipment_name
    lines = [
        f"- 阶段一：完成 {equipment_name} 的方案确认与接口冻结，锁定主回路边界、配套范围和成套接口。",
        f"- 阶段二：围绕 {scope_text} 开展设备成套与出厂联检，核对型号、配置、随机资料和关键接口条件。",
        f"- 阶段三：现场安装阶段完成设备就位、一次二次接线及配套系统联接，形成联调前检查条件。",
        f"- 阶段四：单体调试与联动试运重点验证 {equipment_name} 的启动时序、保护联锁、切换逻辑与系统协同。",
        "- 阶段五：完成验收移交、资料交付和运维培训，形成投运后的持续服务边界。",
    ]
    if protocol:
        _append_unique_line(
            lines,
            f"- 调试前需完成 {protocol} 接口、点表边界及联锁条件确认，确保现场联调与投运窗口可执行。",
        )
    if constraints:
        _append_unique_line(
            lines,
            f"- {_normalize_customer_sentence(f'实施边界：{_ensure_terminal_punctuation(constraints[0])}')}",
        )
    if questions:
        _append_unique_line(
            lines,
            f"- {_normalize_customer_sentence(f'计划排定前需确认：{_ensure_terminal_punctuation(questions[0])}')}",
        )
    return [_normalize_customer_sentence(line) for line in lines[:8] if _normalize_customer_sentence(line)]


def _render_service_support_block(solution_snapshot: SolutionSnapshot | None) -> str:
    return "\n".join(_render_service_support_lines(solution_snapshot)).strip()


def _render_service_support_lines(solution_snapshot: SolutionSnapshot | None) -> list[str]:
    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    protocol = _interface_protocol(solution_snapshot)
    equipment_name = str(primary.get("name") or "当前系统").strip() or "当前系统"
    lines = [
        f"- 服务范围覆盖 {equipment_name} 的到货核验、安装指导、现场调试配合、试运行支持与投运初期问题处理。",
        f"- 现场服务重点围绕 {equipment_name} 的启停流程、联锁条件、切换逻辑、常见告警与运行状态检查展开。",
        f"- 培训与资料交付应覆盖 {equipment_name} 的系统组成、日常点检、故障诊断、操作维护和资料归档要求。",
        "- 质保、备件与到场响应边界应在双方确认文件中统一明确，以形成可执行的服务接口、升级路径和协同机制。",
    ]
    if protocol:
        _append_unique_line(lines, f"- 若系统接入 {protocol}，培训与服务资料中应同步覆盖接口诊断、点表核对、联调配合和异常定位要求。")
    return [_normalize_customer_sentence(line) for line in lines[:5] if _normalize_customer_sentence(line)]


def _should_include_snapshot_summary_block(section: dict[str, Any]) -> bool:
    section_class = str(section.get("section_class") or "").strip().lower()
    if section_class == "overview":
        return True
    return _is_demand_section(section)


def _format_power(value: Any) -> str:
    if value in (None, ""):
        return "功率待确认"
    return f"{value}kW"


def _interface_protocol(solution_snapshot: SolutionSnapshot | None) -> str:
    interface_plan = getattr(solution_snapshot, "interface_plan", None)
    if not isinstance(interface_plan, dict):
        return ""
    return str(interface_plan.get("dcs_protocol") or "").strip()


def _interface_notes(solution_snapshot: SolutionSnapshot | None) -> str:
    interface_plan = getattr(solution_snapshot, "interface_plan", None)
    if not isinstance(interface_plan, dict):
        return ""
    return str(interface_plan.get("notes") or "").strip()


def _normalize_requirement_line(text: str, *, default_prefix: str = "") -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("约束项："):
        return _normalize_customer_sentence(
            f"约束条件：{_ensure_terminal_punctuation(normalized.removeprefix('约束项：').strip())}"
        )
    if normalized.startswith("阻断项："):
        return _normalize_customer_sentence(
            f"必须满足：{_ensure_terminal_punctuation(normalized.removeprefix('阻断项：').strip())}"
        )
    if normalized.startswith("风险项："):
        return _normalize_customer_sentence(
            f"重点确认：{_ensure_terminal_punctuation(normalized.removeprefix('风险项：').strip())}"
        )
    if default_prefix:
        return _normalize_customer_sentence(f"{default_prefix}：{_ensure_terminal_punctuation(normalized)}")
    return _normalize_customer_sentence(_ensure_terminal_punctuation(normalized))


def _ensure_terminal_punctuation(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if normalized.endswith(("。", "！", "？", ".", "!", "?")):
        return normalized
    return f"{normalized}。"


def _normalize_customer_sentence(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    normalized = re.sub(r"\s*([，。；：！？])\s*", r"\1", normalized)
    normalized = re.sub(r"([（(])\s+", r"\1", normalized)
    normalized = re.sub(r"\s+([）)])", r"\1", normalized)
    normalized = re.sub(r"([“‘])\s+", r"\1", normalized)
    normalized = re.sub(r"\s+([”’])", r"\1", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Z])", " ", normalized)
    normalized = normalized.replace(" I/O", " I/O")
    return normalized


def _append_unique_line(lines: list[str], line: str) -> None:
    normalized = str(line or "").strip()
    if normalized and normalized not in lines:
        lines.append(normalized)


def _render_product_table(products: list[dict[str, Any]]) -> str:
    lines = [
        "| 角色 | 产品 | 电压等级 | 功率 | 数量 | 配置 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in products:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("role") or "设备"),
                    str(item.get("name") or "未命名"),
                    str(item.get("rated_voltage") or "待确认"),
                    _format_power(item.get("rated_power_kw")),
                    str(item.get("quantity") or 1),
                    str(item.get("config") or "标准配置"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_model_table(model_matches: list[dict[str, Any]]) -> str:
    lines = [
        "| 系列 | 型号 | 电压等级 | 功率 | 电流 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in model_matches:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("series_name") or item.get("series_code") or "未命名系列"),
                    str(item.get("model_number") or "待确认"),
                    str(item.get("rated_voltage") or "-"),
                    _format_power(item.get("rated_power_kw")),
                    str(item.get("rated_current") or "-"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_interface_table(solution_snapshot: SolutionSnapshot) -> str:
    interface_plan = solution_snapshot.interface_plan if isinstance(solution_snapshot.interface_plan, dict) else {}
    io_allocation = interface_plan.get("io_allocation") if isinstance(interface_plan.get("io_allocation"), dict) else {}
    return "\n".join(
        [
            "| 协议 | DI | DO | AI | AO |",
            "| --- | --- | --- | --- | --- |",
            "| "
            + " | ".join(
                [
                    str(interface_plan.get("dcs_protocol") or "待确认"),
                    str(io_allocation.get("DI") if io_allocation.get("DI") is not None else "-"),
                    str(io_allocation.get("DO") if io_allocation.get("DO") is not None else "-"),
                    str(io_allocation.get("AI") if io_allocation.get("AI") is not None else "-"),
                    str(io_allocation.get("AO") if io_allocation.get("AO") is not None else "-"),
                ]
            )
            + " |"
        ]
    )


def _render_interface_registry_table(interface_entries: list[dict[str, Any]]) -> str:
    lines = [
        "| 系列 | 接口类型 | 协议 | 接口要点 |",
        "| --- | --- | --- | --- |",
    ]
    for item in interface_entries:
        signal_summary = _humanize_signal_summary_items(_string_list(item.get("signal_summary")))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("series_name") or item.get("series_code") or "未命名系列"),
                    _interface_type_label(str(item.get("interface_type") or "")),
                    str(item.get("protocol") or "-"),
                    "；".join(signal_summary) if signal_summary else "-",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _interface_type_label(value: str) -> str:
    mapping = {
        "communication": "通讯接口",
        "io_signal": "I/O 信号",
        "power": "电源接口",
    }
    return mapping.get(str(value or "").strip(), str(value or "").strip() or "未知接口")
