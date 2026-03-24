from __future__ import annotations


EXECUTOR_SYSTEM_PROMPT = """你是一位专业的技术文档撰写专家。请根据给定章节目标与参考资料，
输出结构完整、语言正式、参数一致的 Markdown 章节内容。

规则：
1. 不要编造不存在的产品型号或参数
2. 如果参考资料不足，优先输出稳健的通用表述
3. 章节标题与正文需要层级清晰
4. 内容必须与全局参数保持一致
5. 内容必须面向客户外发口径，不得出现导出、review、draft、smoke、模型、prompt、联调测试等内部流程措辞
6. 若信息不足，请使用“建议在深化设计阶段确认/补充”的客户语言，不要描述生成过程或内部工作流
"""

REUSE_FIRST_SYSTEM_APPENDIX = """
当章节 generation_mode 为 reuse_first 时，请额外遵守：
1. 优先复用给定复用块中的技术骨架和信息顺序，不要把它们重新概括成空泛套话
2. 仅做当前项目所需的最小改写，重点替换项目、客户、参数和边界信息
3. 不要无依据扩写，不要为了完整性主动添加长篇背景铺垫
4. 如果复用块里已经有足够技术描述，应尽量保留其干货密度
"""


def build_section_prompts(
    *,
    section: dict,
    global_params: dict,
    retrieved_context: str,
    outline_title: str,
    recommended_assets: list[dict] | None = None,
    reuse_pack: dict | None = None,
) -> tuple[str, str]:
    reuse_pack = reuse_pack or {}
    params_formatted = _format_global_params(global_params)
    section_title = str(section.get("title", "未命名章节"))
    section_guidance = _build_section_guidance(section_title)
    asset_guidance = _format_recommended_assets(recommended_assets or [])
    generation_mode = str(section.get("generation_mode") or reuse_pack.get("generation_mode") or "baseline")
    reuse_guidance = REUSE_FIRST_SYSTEM_APPENDIX if generation_mode == "reuse_first" else ""
    system_prompt = (
        f"{EXECUTOR_SYSTEM_PROMPT}\n\n"
        f"{reuse_guidance}\n"
        f"方案标题：{outline_title}\n"
        f"当前章节：{section_title}\n"
        f"章节描述：{section.get('description', '')}\n"
        f"全局参数：\n{params_formatted}\n\n"
        f"章节写作要求：\n{section_guidance}"
    )
    reuse_pack_text = _format_reuse_pack(reuse_pack)
    user_prompt = (
        f"请撰写章节《{section_title}》。\n"
        f"生成模式：{generation_mode}\n"
        f"关键词：{', '.join(section.get('keywords', [])) or '暂无'}\n\n"
        f"参考资料：\n{retrieved_context or '暂无检索资料，请输出稳健的客户版标准章节内容。'}\n\n"
        f"复用包：\n{reuse_pack_text}\n\n"
        f"建议参考资产：\n{asset_guidance}\n\n"
        "这些资产仅供参考，不代表已确认可直接外发；如引用，请用客户口径描述其作用，不要把未确认参数写成最终承诺。\n\n"
        "请直接输出客户可阅读的 Markdown 正文，不要解释写作过程。"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in global_params.items())


def _build_section_guidance(section_title: str) -> str:
    guides = {
        "项目概述": (
            "先写项目背景、建设目标和建设范围，再总结总体建设思路。"
            "项目背景应聚焦客户业务场景，不要提任何导出、评审、测试或系统验证过程。"
        ),
        "需求分析": (
            "请按业务需求、技术需求、实施需求和待确认事项组织内容。"
            "对未明确的信息，要写成待澄清项或深化设计输入，不要写成内部流程说明。"
        ),
        "技术架构": (
            "请突出站控层、间隔层、网络层三层架构、关键接口关系、IEC 61850 集成、可靠性和可扩展性。"
            "若提到图示，仅说明建议在详细设计中补充，不要伪造不存在的图。"
        ),
        "硬件配置清单": (
            "先列出已确认的设备型号和数量，再说明关键配置含义、待确认参数以及建议补充的配套设备范围。"
            "不要把建议补充项写成已确认供货清单，不要出现内部审核或图表确认流程描述。"
        ),
        "实施排期": (
            "请围绕勘察、设计、实施、调试、验收五个阶段展开，明确阶段目标、进入条件、主要输出和关键衔接。"
            "未给定实际工期时，不要编造具体日期，可写建议顺序、依赖关系、窗口期和关键风险。"
        ),
        "售后服务": (
            "请聚焦质保范围、故障响应、远程与现场支持、培训、备件和版本管理。"
            "避免描述内部文档审查、导出流程或研发验证，只保留客户真正关心的服务承诺。"
        ),
    }
    return guides.get(
        section_title,
        "请保持客户外发口径，围绕章节目标输出结构化、可执行、可审阅的技术方案内容。",
    )


def _format_recommended_assets(recommended_assets: list[dict]) -> str:
    if not recommended_assets:
        return "- 暂无推荐资产"

    lines: list[str] = []
    for item in recommended_assets[:5]:
        label = str(item.get("title") or item.get("caption") or item.get("asset_type") or "参考资产")
        page_no = item.get("page_no")
        heading = item.get("heading_path")
        usage_mode = item.get("usage_mode")
        reason = item.get("reason")
        parts = [label]
        if page_no:
            parts.append(f"页码 {page_no}")
        if heading:
            parts.append(f"章节 {heading}")
        if usage_mode:
            parts.append(f"使用方式 {usage_mode}")
        if reason:
            parts.append(f"推荐原因 {reason}")
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)


def _format_reuse_pack(reuse_pack: dict) -> str:
    blocks = reuse_pack.get("reusable_blocks") or []
    if not blocks:
        return "- 暂无可复用块，必要时再回退到常规写作。"

    lines = []
    for block in blocks[:3]:
        heading = " > ".join(str(item) for item in (block.get("heading_path") or []) if item)
        replace_fields = ", ".join(block.get("must_replace_fields") or []) or "无"
        lines.extend(
            [
                f"- 来源：{block.get('source_title') or '未知来源'}"
                + (f" / {heading}" if heading else ""),
                f"  复用评分：{block.get('reusability_score')}",
                f"  必须替换字段：{replace_fields}",
                "  可复用正文：",
                f"  {str(block.get('content_md') or '').replace(chr(10), chr(10) + '  ')}",
            ]
        )
    return "\n".join(lines)
