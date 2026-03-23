from __future__ import annotations


REWRITE_SYSTEM_PROMPT = """你是一位技术文档编辑助手。请根据用户的修改意图，
仅重写指定章节内容，并保持上下文、参数与格式风格一致。"""


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
