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
1. 大纲必须包含项目概述、需求分析、技术架构、硬件配置清单、实施排期、售后服务
2. 输出必须严格为 JSON，并满足给定 Schema
3. 每个章节必须包含 title、description、keywords
4. 如果输入材料不充分，也要给出稳健、可执行的大纲
5. 大纲描述必须面向客户外发口径，不得出现导出、review、draft、smoke、模型、prompt、联调测试等内部流程词汇
6. “硬件配置清单”应强调已知配置、待确认项和建议补充范围；“实施排期”应强调阶段目标、前后依赖和交付输出；“售后服务”应强调服务承诺、响应机制和培训质保
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
        "响应格式已由接口层约束为结构化 JSON，请只返回符合要求的最终结果。"
    )
    user_prompt = (
        f"用户指令：{instructions}\n\n"
        f"RFP 摘要：\n{rfp_context or '暂无 RFP 摘要，请基于项目参数生成标准技术方案大纲。'}\n\n"
        "请返回面向客户技术方案的最终大纲 JSON，避免任何内部研发、联调、评审或导出过程措辞。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
