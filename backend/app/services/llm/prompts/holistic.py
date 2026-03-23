from __future__ import annotations


HOLISTIC_SYSTEM_PROMPT = """你是一位技术文档终审编辑。请将各章节草稿融合为一份完整、
连贯、术语一致的 Markdown 终稿。

要求：
1. 全文参数与术语保持一致
2. 章节之间补齐必要的过渡
3. 保持技术方案的正式语气
"""


def build_holistic_prompts(*, global_params: dict, all_sections_markdown: str) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    system_prompt = f"{HOLISTIC_SYSTEM_PROMPT}\n\n全局参数：\n{params_formatted}"
    user_prompt = f"请融合以下章节草稿，输出最终 Markdown：\n\n{all_sections_markdown}"
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
