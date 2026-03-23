from __future__ import annotations


EXECUTOR_SYSTEM_PROMPT = """你是一位专业的技术文档撰写专家。请根据给定章节目标与参考资料，
输出结构完整、语言正式、参数一致的 Markdown 章节内容。

规则：
1. 不要编造不存在的产品型号或参数
2. 如果参考资料不足，优先输出稳健的通用表述
3. 章节标题与正文需要层级清晰
4. 内容必须与全局参数保持一致
"""


def build_section_prompts(
    *,
    section: dict,
    global_params: dict,
    retrieved_context: str,
    outline_title: str,
) -> tuple[str, str]:
    params_formatted = _format_global_params(global_params)
    system_prompt = (
        f"{EXECUTOR_SYSTEM_PROMPT}\n\n"
        f"方案标题：{outline_title}\n"
        f"当前章节：{section.get('title', '未命名章节')}\n"
        f"章节描述：{section.get('description', '')}\n"
        f"全局参数：\n{params_formatted}"
    )
    user_prompt = (
        f"请撰写章节《{section.get('title', '未命名章节')}》。\n"
        f"关键词：{', '.join(section.get('keywords', [])) or '暂无'}\n\n"
        f"参考资料：\n{retrieved_context or '暂无检索资料，请输出稳健的标准章节草稿。'}"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
