from __future__ import annotations


REWRITE_SYSTEM_PROMPT = """你是一位技术文档编辑助手。请根据用户的修改意图，
仅重写指定章节内容，并保持上下文、参数与格式风格一致。

规则：
1. 优先保留原文中的技术信息密度、条目结构和参数表达
2. 仅做必要的统一、替换和轻量润色，不要自行扩写背景或空泛总结
3. 不得保留旧客户、旧项目、样板来源或任务提示语
4. 若正文中已包含图表占位符 [[ASSET:...]]，必须保留
5. 保留原有章节标题、三级小标题、表格和列表结构，非必要不要改写结构
6. 改写后正文长度原则上不低于原稿的 80%，不要把技术段压缩成一句结论
7. 章节上下文中的字段标签仅供理解，不得直接复述到输出正文
8. 如果上下文提示某图表、照片、布局图、文字截图或资产占位符存在待确认/低质量/证据类型错配，只能保守处理，不得把它改写成确定的工程拓扑、主接线或控制原理图
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
        "以下上下文仅供理解，不得直接复述字段标签或说明语。\n"
        f"<section_context>\n{section_context}\n</section_context>\n\n"
        "以下为待精修的章节 Markdown，请保留章节标题、子标题、表格、列表和 [[ASSET:...]] 占位符，只做必要改写。\n"
        f"<draft_markdown>\n{selected_text}\n</draft_markdown>\n\n"
        f"改写要求：\n{user_instruction}\n\n"
        "请只输出最终客户可阅读的 Markdown 章节，不要附加解释、说明或完成提示。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())
