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
5. 只返回 JSON
"""


def build_section_quality_prompts(
    *,
    section: dict[str, Any],
    outline_title: str,
    global_params: dict[str, Any],
    content_md: str,
    recommended_assets: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    section_title = str(section.get("title") or "未命名章节").strip() or "未命名章节"
    purpose = str(section.get("purpose") or section.get("description") or "").strip()
    keywords = [str(item).strip() for item in (section.get("keywords") or []) if str(item).strip()]
    assets = recommended_assets or []

    system_prompt = (
        f"{SECTION_QUALITY_SYSTEM_PROMPT}\n\n"
        f"方案标题：{outline_title}\n"
        f"章节标题：{section_title}\n"
        f"章节目的：{purpose or '暂无'}\n"
        f"全局参数：\n{_format_global_params(global_params)}"
    )
    user_prompt = (
        f"章节标题：{section_title}\n"
        f"章节目标：{purpose or '围绕该章节主题给出正式客户稿。'}\n"
        f"章节关键词：{', '.join(keywords) or '暂无'}\n"
        f"推荐资产：\n{_format_assets(assets)}\n\n"
        "请审查以下 Markdown 章节是否已经达到客户稿门槛，并输出结构化评审结果。\n"
        "如果不通过，rewrite_instruction 要能直接用于重写，明确指出要保留技术密度、统一标题风格、删除内部提示并保持 [[ASSET:...]] 占位。\n\n"
        f"<section_markdown>\n{content_md}\n</section_markdown>"
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
        reason = str(item.get("reason") or "").strip()
        line = f"- {label}"
        if asset_type:
            line += f" ({asset_type})"
        if reason:
            line += f"；{reason}"
        lines.append(line)
    return "\n".join(lines)
