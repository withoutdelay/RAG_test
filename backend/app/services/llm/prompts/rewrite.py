from __future__ import annotations


REWRITE_SYSTEM_PROMPT = """你是一位技术文档编辑助手。请根据用户的修改意图，
仅重写指定章节内容，并保持上下文、参数与格式风格一致。

规则：
1. 优先保留原文中的技术信息密度、条目结构和参数表达
2. 仅做必要的统一、替换和轻量润色，不要自行扩写背景或空泛总结
3. 不得保留旧客户、旧项目、样板来源或任务提示语
4. 若正文中已包含图表占位符 [[ASSET:...]]，必须保留
"""


def build_rewrite_prompts(
    *,
    section_context: str,
    selected_text: str,
    user_instruction: str,
    global_params: dict,
) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    system_prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n全局参数：\n{params_formatted}"
    user_prompt = (
        f"章节上下文：\n{section_context}\n\n"
        f"用户选中文本：\n{selected_text}\n\n"
        f"修改指令：\n{user_instruction}\n\n"
        "请输出重写后的完整章节内容。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
