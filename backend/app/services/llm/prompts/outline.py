from __future__ import annotations

import json


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
1. 大纲必须包含项目概述、需求分析、技术架构、硬件配置清单、实施排期、售后服务
2. 输出必须严格为 JSON，并满足给定 Schema
3. 每个章节必须包含 title、description、keywords
4. 如果输入材料不充分，也要给出稳健、可执行的大纲
"""


def build_outline_prompts(
    *,
    project_name: str,
    instructions: str,
    global_params: dict,
    rfp_context: str = "",
) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    system_prompt = (
        f"{PLANNER_SYSTEM_PROMPT}\n\n"
        f"项目名称：{project_name}\n"
        f"全局参数：\n{params_formatted}\n\n"
        f"输出 JSON Schema：\n{json.dumps(PLANNER_OUTLINE_SCHEMA, ensure_ascii=False, indent=2)}"
    )
    user_prompt = (
        f"用户指令：{instructions}\n\n"
        f"RFP 摘要：\n{rfp_context or '暂无 RFP 摘要，请基于项目参数生成标准技术方案大纲。'}\n\n"
        "请返回最终大纲 JSON。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
