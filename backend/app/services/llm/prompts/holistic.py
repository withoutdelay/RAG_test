from __future__ import annotations

import re

ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:[A-Z]+:[^\]]+\]\]")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


HOLISTIC_SYSTEM_PROMPT = """你是一位资深售前技术方案终审编辑。你的任务是将各章节草稿融合为一份\
完整、连贯、专业的 Markdown 终稿，使之达到可直接外发给客户审阅的质量。

<editing_contract>
1. 内容保真：保留所有有工程价值的型号、数量、电压等级、容量、接口、联锁、供货边界和待确认项。
2. 不压缩技术密度：每个章节正文长度原则上不低于原稿的 85%，只删除重复背景、空泛套话和内部残留。
3. 参数一致：若正文与全局参数冲突，统一为全局参数；若全局参数缺失，不得自行编造。
4. 术语一致：同一对象全文只使用一个主称谓，例如 LCI/SFC 变频软起动系统、DCS、PLC、同步电机。
5. 表格保真：Markdown 表格必须保留表头、分隔行和原有数据行；不得删除供货清单、参数表、接口表的有效行。
6. 资产保真：所有 [[ASSET:...]] 占位符必须原样保留，不得改写 asset_id，不得伪造新占位符。
7. 章节衔接：只在必要位置添加 1-2 句自然过渡，不要每章重复项目背景。
8. 客户口径：不得输出 prompt、模型、draft、review、smoke、生成模式、复用包、参考摘要、可参考章节等内部流程词。
9. 待确认边界：保留“待技术确认 / 以技术协议为准 / 以双方确认清单为准”等审慎表达，不得改成确定承诺。
10. 证据保守：不得在终审阶段把照片、布局图、文字截图、碎片图或待确认资产改写成确定的主接线图、拓扑图、控制原理图或系统示意图。
</editing_contract>

<output_contract>
只输出完整 Markdown 终稿，从一级标题 # 开始。
不要输出 XML 标签。
不要输出解释说明、审查报告、修改记录或 HTML 注释。
</output_contract>
"""


def build_holistic_prompts(*, global_params: dict, all_sections_markdown: str) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    param_checklist = _build_param_checklist(global_params)
    section_manifest = _build_section_manifest(all_sections_markdown)
    term_hints = _build_term_unification_hints(global_params=global_params, all_sections_markdown=all_sections_markdown)
    asset_inventory = _build_asset_placeholder_inventory(all_sections_markdown)
    system_prompt = (
        f"{HOLISTIC_SYSTEM_PROMPT}\n\n"
        "<global_params>\n"
        f"{params_formatted}\n"
        "</global_params>"
    )
    user_prompt = (
        "<holistic_finalize_request>\n"
        "<global_param_checklist>\n"
        f"{param_checklist}\n"
        "</global_param_checklist>\n\n"
        "<section_manifest>\n"
        f"{section_manifest}\n"
        "</section_manifest>\n\n"
        "<term_unification_hints>\n"
        f"{term_hints}\n"
        "</term_unification_hints>\n\n"
        "<asset_placeholder_inventory>\n"
        f"{asset_inventory}\n"
        "</asset_placeholder_inventory>\n\n"
        "<section_markdown>\n"
        f"{all_sections_markdown}\n"
        "</section_markdown>\n"
        "</holistic_finalize_request>"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())


def _build_param_checklist(global_params: dict) -> str:
    if not global_params:
        return "- 无需校验（无全局参数）"
    lines = []
    for key, value in global_params.items():
        lines.append(f"- [ ] {key} = {value}  →  请确认全文中该值的所有出现均一致")
    return "\n".join(lines)


def _build_section_manifest(all_sections_markdown: str) -> str:
    sections: list[dict[str, int | str]] = []
    current: dict[str, int | str] | None = None
    current_chars = 0
    for line in str(all_sections_markdown or "").splitlines():
        match = MARKDOWN_HEADING_PATTERN.match(line.strip())
        if match and len(match.group(1)) <= 2:
            if current:
                current["chars"] = current_chars
                sections.append(current)
            current = {"title": match.group(2).strip(), "chars": 0}
            current_chars = 0
            continue
        current_chars += len(line)
    if current:
        current["chars"] = current_chars
        sections.append(current)
    if not sections:
        return "- 未识别到章节标题，请保持原有章节顺序和内容密度。"
    lines = []
    for index, section in enumerate(sections, start=1):
        lines.append(f"- {index}. {section['title']}（正文约 {section['chars']} 字符）")
    return "\n".join(lines)


def _build_term_unification_hints(*, global_params: dict, all_sections_markdown: str) -> str:
    text = str(all_sections_markdown or "")
    hints: list[str] = []
    if any(token in text for token in ("LCI", "SFC", "软起动", "软启动")):
        hints.append("- 将 LCI、SFC、软起动、软启动相关表述统一到“LCI/SFC 变频软起动系统”，但保留原始型号中的英文缩写。")
    if any(token in text for token in ("DCS", "集散控制")):
        hints.append("- DCS 首次可写为“DCS（集散控制系统）”，后文统一使用“DCS”。")
    if any(token in text for token in ("PLC", "可编程逻辑")):
        hints.append("- PLC 首次可写为“PLC（可编程逻辑控制器）”，后文统一使用“PLC”。")
    voltage = global_params.get("voltage_level") if isinstance(global_params, dict) else None
    if voltage:
        hints.append(f"- 全文电压等级优先统一为“{voltage}”，历史案例中的不同电压仅可作为来源说明保留。")
    quantity = global_params.get("quantity") if isinstance(global_params, dict) else None
    if quantity:
        hints.append(f"- 系统数量、服务对象数量等涉及数量口径时，优先与全局参数 quantity={quantity} 保持一致。")
    return "\n".join(hints) if hints else "- 未检测到需要特别统一的高频术语，保持原章节术语即可。"


def _build_asset_placeholder_inventory(all_sections_markdown: str) -> str:
    placeholders = []
    seen: set[str] = set()
    for match in ASSET_PLACEHOLDER_PATTERN.finditer(str(all_sections_markdown or "")):
        placeholder = match.group(0)
        if placeholder in seen:
            continue
        seen.add(placeholder)
        placeholders.append(placeholder)
    if not placeholders:
        return "- 未检测到资产占位符。"
    return "\n".join(f"- {placeholder}" for placeholder in placeholders)
