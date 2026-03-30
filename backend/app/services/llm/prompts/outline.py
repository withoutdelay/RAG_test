from __future__ import annotations

PLANNER_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "方案总标题"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "subsections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "keywords": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["index", "title", "description", "keywords"],
                        },
                    },
                },
                "required": ["index", "title", "description", "keywords"],
            },
        },
    },
    "required": ["title", "sections"],
}

PLANNER_SYSTEM_PROMPT = """你是一位资深的售前技术方案架构师。你的任务是根据客户需求，
生成一份专业、完整、结构清晰的技术方案大纲。

规则：
1. 输出必须严格为 JSON，并满足给定 Schema
2. 每个章节必须包含 title、description、keywords
3. 优先参考给定的历史方案目录结构样本，但不要机械照抄；应根据当前项目内容选择、删减、合并或重命名章节
4. 如果历史样本中的章节标题直接包含关键设备、系统名称、工艺段名称或控制对象，允许在当前大纲中沿用这种更具体的标题风格
5. 如果输入材料不充分，也要给出稳健、可执行的大纲，但不要为了完整性强行补固定模板章节
6. 大纲描述必须面向客户外发口径，不得出现导出、review、draft、smoke、模型、prompt、联调测试等内部流程词汇
"""


def build_outline_prompts(
    *,
    project_name: str,
    instructions: str,
    global_params: dict,
    rfp_context: str = "",
    outline_examples: list[dict] | None = None,
) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    examples_formatted = _format_outline_examples(outline_examples or [])
    system_prompt = (
        f"{PLANNER_SYSTEM_PROMPT}\n\n"
        f"项目名称：{project_name}\n"
        f"全局参数：\n{params_formatted}\n\n"
        "响应格式已由接口层约束为结构化 JSON，请只返回符合要求的最终结果。"
    )
    user_prompt = (
        f"用户指令：{instructions}\n\n"
        f"RFP 摘要：\n{rfp_context or '暂无 RFP 摘要，请基于项目参数生成标准技术方案大纲。'}\n\n"
        f"历史目录样本：\n{examples_formatted}\n\n"
        "请返回面向客户技术方案的最终大纲 JSON，避免任何内部研发、联调、评审或导出过程措辞。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())


def _format_outline_examples(outline_examples: list[dict]) -> str:
    if not outline_examples:
        return "- 暂无历史目录样本"
    lines: list[str] = []
    for item in outline_examples:
        titles = ", ".join(str(title) for title in (item.get("top_level_titles") or [])[:12])
        lines.append(
            f"- {item.get('file_name')}: {titles or '无可用目录标题'}"
        )
    return "\n".join(lines)
