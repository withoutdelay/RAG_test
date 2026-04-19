from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.solution_snapshot import SolutionSnapshot


_INTERFACE_HINTS = ("接口", "通讯", "通信", "dcs", "plc", "自动化", "点表", "联锁信号")
_SUPPLY_HINTS = ("供货", "清单", "配置", "参数", "设备", "物料", "规格", "型号", "范围")
_ARCHITECTURE_HINTS = ("总体", "方案", "架构", "主回路", "系统", "装置", "拓扑")
_CONTROL_HINTS = ("控制", "联锁", "保护", "切换", "启动", "同步", "逻辑", "时序")


async def get_preferred_solution_snapshot(
    *,
    session: AsyncSession,
    project_id: UUID,
) -> SolutionSnapshot | None:
    result = await session.scalars(
        select(SolutionSnapshot)
        .where(SolutionSnapshot.project_id == project_id)
        .order_by(
            SolutionSnapshot.confirmed_by_user.desc(),
            SolutionSnapshot.version.desc(),
            SolutionSnapshot.created_at.desc(),
        )
        .limit(1)
    )
    return result.first()


def build_solution_outline_params(solution_snapshot: SolutionSnapshot | None) -> dict[str, Any]:
    if solution_snapshot is None:
        return {}

    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    params: dict[str, Any] = {
        "solution_snapshot_version": solution_snapshot.version,
        "solution_snapshot_status": "confirmed" if solution_snapshot.confirmed_by_user else "draft",
    }
    if solution_snapshot.solution_summary:
        params["solution_summary"] = str(solution_snapshot.solution_summary).strip()
    if primary.get("name"):
        params["primary_product"] = primary.get("name")
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
    chapters = _string_list(solution_snapshot.suggested_chapters)
    if chapters:
        params["suggested_chapters"] = " / ".join(chapters)
    return params


def build_solution_writer_params(solution_snapshot: SolutionSnapshot | None) -> dict[str, Any]:
    if solution_snapshot is None:
        return {}

    products = _selected_products(solution_snapshot)
    primary = products[0] if products else {}
    params: dict[str, Any] = {
        "solution_snapshot_version": solution_snapshot.version,
    }
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
    return params


def render_solution_outline_context(solution_snapshot: SolutionSnapshot | None) -> str:
    if solution_snapshot is None:
        return ""

    products = _selected_products(solution_snapshot)
    chapters = _string_list(solution_snapshot.suggested_chapters)
    constraints = _string_list(solution_snapshot.key_constraints)
    questions = _string_list(solution_snapshot.open_questions)
    protocol = _interface_protocol(solution_snapshot)

    lines = [
        f"方案快照版本：v{solution_snapshot.version}" + ("（已确认）" if solution_snapshot.confirmed_by_user else "（待确认）"),
        f"方案摘要：{str(solution_snapshot.solution_summary or '').strip()}",
    ]
    if products:
        lines.append("推荐设备：")
        lines.extend(
            f"- {item.get('role') or '设备'}：{item.get('name') or '未命名'}，"
            f"{item.get('rated_voltage') or '电压待确认'}，"
            f"{_format_power(item.get('rated_power_kw'))}，"
            f"数量 {item.get('quantity') or 1}，"
            f"配置 {item.get('config') or '标准配置'}"
            for item in products
        )
    if protocol:
        lines.append(f"接口协议：{protocol}")
    if chapters:
        lines.append("建议章节：")
        lines.extend(f"- {chapter}" for chapter in chapters)
    if constraints:
        lines.append("关键约束：")
        lines.extend(f"- {item}" for item in constraints)
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
    text = _section_signal_text(section)

    parts: list[str] = [
        "方案快照参考：",
        f"- 当前推荐方案：{str(solution_snapshot.solution_summary or '').strip()}",
    ]
    if products and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        parts.extend(
            [
                "",
                "推荐设备清单：",
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

    if _matches_any(text, _INTERFACE_HINTS):
        parts.extend(
            [
                "",
                "接口计划：",
                _render_interface_table(solution_snapshot),
            ]
        )
        notes = _interface_notes(solution_snapshot)
        if notes:
            parts.append(f"- 接口备注：{notes}")

    if (_matches_any(text, _CONTROL_HINTS) or _matches_any(text, _ARCHITECTURE_HINTS)) and constraints:
        parts.extend(["", "关键约束：", *[f"- {item}" for item in constraints]])

    if (_matches_any(text, _CONTROL_HINTS) or _matches_any(text, _SUPPLY_HINTS)) and questions:
        parts.extend(["", "待确认项：", *[f"- {item}" for item in questions]])

    return "\n".join(part for part in parts if part).strip()


def build_solution_section_citations(
    *,
    section: dict[str, Any],
    solution_snapshot: SolutionSnapshot | None,
) -> list[dict[str, Any]]:
    if solution_snapshot is None:
        return []
    excerpt = str(solution_snapshot.solution_summary or "").strip()
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

    summary_block = {
        "block_id": f"solution:{solution_snapshot.id}:summary",
        "source_doc_id": str(solution_snapshot.id),
        "source_title": f"方案快照 v{solution_snapshot.version}",
        "source_section_id": "summary",
        "section_path": "方案快照 > 方案摘要",
        "source_heading": "方案摘要",
        "heading_path": ["方案快照", "方案摘要"],
        "content_md": str(solution_snapshot.solution_summary or "").strip(),
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

    if products and (_matches_any(text, _ARCHITECTURE_HINTS) or _matches_any(text, _SUPPLY_HINTS)):
        blocks.append(
            {
                "block_id": f"solution:{solution_snapshot.id}:products",
                "source_doc_id": str(solution_snapshot.id),
                "source_title": f"方案快照 v{solution_snapshot.version}",
                "source_section_id": "products",
                "section_path": "方案快照 > 推荐设备清单",
                "source_heading": "推荐设备清单",
                "heading_path": ["方案快照", "推荐设备清单"],
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

    return blocks


def _section_signal_text(section: dict[str, Any]) -> str:
    return " ".join(
        [
            str(section.get("title") or "").strip(),
            str(section.get("purpose") or section.get("description") or "").strip(),
            " ".join(str(item).strip() for item in (section.get("keywords") or []) if str(item).strip()),
        ]
    ).lower()


def _matches_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(hint.lower() in lowered for hint in hints)


def _selected_products(solution_snapshot: SolutionSnapshot | None) -> list[dict[str, Any]]:
    products = getattr(solution_snapshot, "selected_products", None)
    if not isinstance(products, list):
        return []
    return [item for item in products if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
