from __future__ import annotations

from typing import Any


SECTION_QUALITY_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "severity": {"type": "string"},
                    "target": {"type": "string"},
                    "message": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
                "required": ["code", "severity", "target", "message", "suggested_fix"],
                "additionalProperties": False,
            },
        },
        "rewrite_instruction": {"type": "string"},
    },
    "required": ["pass", "score", "summary", "issues", "rewrite_instruction"],
    "additionalProperties": False,
}


SECTION_QUALITY_SYSTEM_PROMPT = """你是一位售前技术方案质量审查员。请对单个章节做严格但保守的质量评审。

你的目标：
1. 判断章节是否已经达到可直接给客户阅读的质量门槛
2. 重点审查标题层级、小标题命名、内容完整度、客户口径、技术一致性和内部提示语残留
3. 如果质量不足，给出可直接用于重写的一段修复指令

审查原则：
1. 对电气技术内容保持保守，不要编造额外参数或原理
2. 若章节存在“建议插入图表”“推荐资产”“可用参考资料”“A. 概述”这类内部或风格不一致标题，应判为不通过
3. 若标题过于空泛、重复、与章节目标不匹配，应指出
4. 若正文明显像素材拼贴、章节组织混乱、客户口径不稳定，也应指出
5. 输入会使用 XML 标签提供上下文。除 <section_markdown> 内的正文外，其他标签只用于参考，不得因为这些标签本身出现“推荐资产”“章节关键词”等字样就判定为内部提示语泄露
6. 如果正文把低质量、待审、产品照片、布局图、文字截图或碎片图描述成主接线图、拓扑图、控制原理图、系统示意图或参数表，应视为证据类型错配并指出
7. 不要仅凭资产标题判断证据类型；recommended_assets 中的 asset_type、visual_role、audit_status、quality_score 和 review_required 更重要
8. 若仅存在轻微标题优化、措辞凝练或结构收束建议，而不存在实质性内容缺口、技术矛盾、证据类型错配或明确内部提示语泄露，应判为通过，并把建议写入 issues
9. 只返回 JSON
"""


def build_section_quality_prompts(
    *,
    section: dict[str, Any],
    outline_title: str,
    global_params: dict[str, Any],
    content_md: str,
    recommended_assets: list[dict[str, Any]] | None = None,
    quality_constraints: str = "",
) -> tuple[str, str]:
    section_title = str(section.get("title") or "未命名章节").strip() or "未命名章节"
    purpose = str(section.get("purpose") or section.get("description") or "").strip()
    keywords = [str(item).strip() for item in (section.get("keywords") or []) if str(item).strip()]
    assets = recommended_assets or []

    system_prompt = (
        f"{SECTION_QUALITY_SYSTEM_PROMPT}\n\n"
        "<review_context>\n"
        f"<outline_title>{outline_title}</outline_title>\n"
        f"<section_title>{section_title}</section_title>\n"
        f"<section_purpose>{purpose or '暂无'}</section_purpose>\n"
        "<global_parameters>\n"
        f"{_format_global_params(global_params)}\n"
        "</global_parameters>\n"
        "</review_context>"
    )
    user_prompt = (
        "<quality_review_request>\n"
        f"{_xml_block('section_title', section_title)}"
        f"{_xml_block('section_goal', purpose or '围绕该章节主题给出正式客户稿。')}"
        f"{_xml_block('section_keywords', ', '.join(keywords) or '暂无')}"
        f"{_xml_block('recommended_assets', _format_assets(assets))}"
        f"{_xml_block('ai_wiki_review_constraints', quality_constraints or '暂无')}"
        "<review_contract>\n"
        "1. 只审查 <section_markdown> 内的正文是否达到客户稿门槛。\n"
        "2. 标签区中的 metadata / recommended_assets / keywords 仅供理解章节目标，不应被视为正文中的内部提示语。\n"
        "3. pass=false 仅用于存在明确内部提示语泄露、重大技术矛盾、关键内容缺失、表格损坏或结构失真等阻断问题的场景。\n"
        "4. 如果正文对推荐资产的证据类型作出明显错误描述，例如把照片、布置图、文字截图、碎片或待审资产写成工程拓扑/主接线/控制原理图，应判定为阻断问题。\n"
        "5. 如果只是建议优化标题、措辞、结构收束或客户语气，请保持 pass=true，并在 issues 中给出建议。\n"
        "6. 如果不通过，rewrite_instruction 要能直接用于重写，明确指出要保留技术密度、统一标题风格、删除内部提示、修正证据类型错配并保持 [[ASSET:...]] 占位。\n"
        "</review_contract>\n"
        f"{_xml_block('section_markdown', content_md)}"
        "</quality_review_request>"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict[str, Any]) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())


def _format_assets(assets: list[dict[str, Any]]) -> str:
    if not assets:
        return "- 暂无"
    lines: list[str] = []
    for item in assets[:5]:
        label = str(item.get("display_title") or item.get("title") or item.get("caption") or "参考资产").strip()
        asset_type = str(item.get("asset_type") or "").strip()
        visual_role = str(item.get("visual_role") or "").strip()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        audit_status = str(item.get("asset_audit_status") or metadata.get("asset_audit_status") or "").strip()
        quality_score = item.get("asset_quality_score") or metadata.get("asset_quality_score")
        review_required = bool(item.get("review_required"))
        reason = str(item.get("reason") or "").strip()
        line = f"- {label}"
        if asset_type:
            line += f" ({asset_type})"
        if visual_role:
            line += f"；visual_role={visual_role}"
        if audit_status:
            line += f"；audit_status={audit_status}"
        if quality_score is not None and quality_score != "":
            line += f"；quality_score={quality_score}"
        if review_required:
            line += "；review_required=true"
        if reason:
            line += f"；{reason}"
        lines.append(line)
    return "\n".join(lines)


def _xml_block(tag: str, content: str) -> str:
    normalized = str(content or "").strip()
    return f"<{tag}>\n{normalized or '暂无'}\n</{tag}>\n"
