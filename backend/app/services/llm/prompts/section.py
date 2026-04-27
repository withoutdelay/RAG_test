from __future__ import annotations

import re


EXECUTOR_SYSTEM_PROMPT = """你是一位专业的技术文档撰写专家。请根据给定章节目标与参考资料，
输出结构完整、语言正式、参数一致的 Markdown 章节内容。

规则：
1. 不要编造不存在的产品型号或参数
2. 如果参考资料不足，优先输出稳健的通用表述
3. 章节标题与正文需要层级清晰
4. 内容必须与全局参数保持一致
5. 内容必须面向客户外发口径，不得出现导出、review、draft、smoke、模型、prompt、联调测试等内部流程措辞
6. 若信息不足，请使用审慎的客户语言，但不要在每个小节重复“建议在深化设计阶段确认/补充”；多个未明确项应尽量收束到章节末尾统一归纳
7. 严禁在正文中出现“本节基于…草拟”“请撰写章节”“参考摘要”“生成模式”“复用包”“推荐资产”“关键词”等任务提示残留
8. 如果提供了前序章节摘要，请勿重复前序章节已说明的内容（尤其是项目背景和建设目标），直接进入本章节主题
9. 输入中会使用 XML 标签承载上下文。标签名仅用于分隔信息，严禁在输出中复述任何标签名、字段名或提示语
10. 推荐资产中的标题、来源章节和推荐原因只是弱证据，不能单独证明图片或表格内容；必须结合 asset_type、visual_role、audit_status、quality_score 和正文证据判断证据类型与是否可引用
11. 不得把产品照片、布局图、文字截图、碎片图或低质量待审资产描述成主接线图、拓扑图、控制原理图、系统示意图或参数表
12. 若资产质量状态为 rejected、review_pending、低质量分或视觉类型与章节目标不匹配，应优先不引用该资产；必须引用时只能用审慎措辞说明“需人工确认”
13. 章节标题后不要输出“本章/本节围绕……进行说明”“本章节针对……明确……”这类目标或概要导语；直接进入实质小节、技术结论、配置说明或参数表
14. 如需引用推荐资产，必须逐字使用输入中给出的完整占位符 [[ASSET:TYPE:asset_id]]；不得自行改写成 [[ASSET:标题]]、[[ASSET:TABLE:标题]] 或其他标题式引用
"""

REUSE_FIRST_SYSTEM_APPENDIX = """
当章节 generation_mode 为 reuse_first 时，请额外遵守：
1. 优先复用给定复用块中的技术骨架和信息顺序，不要把它们重新概括成空泛套话
2. 仅做当前项目所需的最小改写，重点替换项目、客户、参数和边界信息
3. 不要无依据扩写，不要为了完整性主动添加长篇背景铺垫
4. 如果复用块里已经有足够技术描述，应尽量保留其干货密度
5. 如果给定了禁止沿用词或必须替换字段，必须严格遵守，不得把旧客户或旧项目痕迹带入正文
6. 如果给定了资产占位符要求，必须在合适位置输出相应的 [[ASSET:...]] 占位
"""

REUSE_FINALIZE_SYSTEM_APPENDIX = """
当给定“已组装章节草稿”时，请额外遵守：
1. 必须将该组装稿视为主素材，在其基础上整理成正式客户稿，不要抛开组装稿重新泛化总结
2. 优先保留组装稿中的技术细节、信息顺序、三级小标题和 [[ASSET:...]] 占位符
3. 仅做必要的统一、去噪、术语修正和项目字段替换，不要随意删减技术信息
4. 如组装稿中已经存在图表占位符，应保留并放在对应技术段附近
"""

TITLE_PREFIX_PATTERN = re.compile(r"^\s*(?:第[\d一二三四五六七八九十百]+[章节篇部]\s*|[\d一二三四五六七八九十百]+(?:\.\d+)*[、.\-]?\s*)")


def build_section_prompts(
    *,
    section: dict,
    global_params: dict,
    retrieved_context: str,
    outline_title: str,
    recommended_assets: list[dict] | None = None,
    reuse_pack: dict | None = None,
    preceding_context: str = "",
) -> tuple[str, str]:
    reuse_pack = reuse_pack or {}
    params_formatted = _format_global_params(global_params)
    section_title = str(section.get("title", "未命名章节"))
    section_guidance = _build_section_guidance(section_title)
    section_outline = _format_section_outline(section)
    asset_guidance = _format_recommended_assets(recommended_assets or [])
    asset_candidate_guidance = _format_asset_candidates(reuse_pack.get("asset_candidates") or [])
    generation_mode = str(section.get("generation_mode") or reuse_pack.get("generation_mode") or "baseline")
    reuse_guidance = REUSE_FIRST_SYSTEM_APPENDIX if generation_mode == "reuse_first" else ""
    assembled_draft = str(reuse_pack.get("assembled_draft") or "").strip()
    finalize_guidance = REUSE_FINALIZE_SYSTEM_APPENDIX if assembled_draft else ""
    reference_material_text = retrieved_context or (
        "复用优先模式：请直接依据下方复用包中的正文块完成最小改写，不要复述素材标题、提示词或任务说明。"
        if reuse_pack.get("reusable_blocks")
        else "暂无检索资料，请输出稳健的客户版标准章节内容。"
    )
    system_prompt = (
        f"{EXECUTOR_SYSTEM_PROMPT}\n\n"
        f"{reuse_guidance}\n"
        f"{finalize_guidance}\n"
        "<authoring_context>\n"
        f"<outline_title>{outline_title}</outline_title>\n"
        f"<section_title>{section_title}</section_title>\n"
        f"<section_description>{section.get('description', '')}</section_description>\n"
        "<global_parameters>\n"
        f"{params_formatted}\n"
        "</global_parameters>\n"
        "<section_guidance>\n"
        f"{section_guidance}\n"
        "</section_guidance>\n"
        "</authoring_context>"
    )
    reuse_pack_text = _format_reuse_pack(reuse_pack)
    replacement_constraints = _format_replacement_constraints(reuse_pack)
    assembled_draft_text = _format_assembled_draft(assembled_draft)
    preceding_context_block = _xml_block("preceding_sections", preceding_context.strip()) if preceding_context.strip() else ""
    user_prompt = (
        "<section_request>\n"
        f"{preceding_context_block}"
        f"{_xml_block('section_title', section_title)}"
        f"{_xml_block('section_outline', section_outline)}"
        f"{_xml_block('section_keywords', ', '.join(section.get('keywords', [])) or '暂无')}"
        f"{_xml_block('reference_material', reference_material_text)}"
        f"{_xml_block('assembled_draft_bundle', assembled_draft_text)}"
        f"{_xml_block('reuse_pack', reuse_pack_text)}"
        f"{_xml_block('replacement_constraints', replacement_constraints)}"
        f"{_xml_block('recommended_assets', asset_guidance)}"
        f"{_xml_block('asset_candidates', asset_candidate_guidance)}"
        "<output_contract>\n"
        "1. 只输出最终客户可阅读的 Markdown 正文。\n"
        "2. 不要复述任务说明、XML 标签、字段标签或写作过程。\n"
        "3. 推荐资产是可自动引用的高置信资产；候选资产仅作备选证据，只有在其视觉类型、章节目标和来源内容明确匹配时才可引用。\n"
        "4. 若已提供 assembled_draft_bundle，必须以其为主素材整理成稿。\n"
        "5. 若 section_outline 提供了内部小节结构，必须按其顺序输出对应 Markdown 小标题，不要拆散或遗漏。\n"
        "</output_contract>\n"
        "</section_request>"
    )
    return system_prompt, user_prompt


def _format_global_params(global_params: dict) -> str:
    if not global_params:
        return "- 暂无"
    visible_items = [(key, value) for key, value in global_params.items() if not str(key).startswith("_")]
    if not visible_items:
        return "- 暂无"
    return "\n".join(f"- {key}: {value}" for key, value in visible_items)


def _format_section_outline(section: dict) -> str:
    child_outline = str(section.get("child_outline_text") or "").strip()
    if child_outline:
        return (
            "本次按大章节生成，请在当前章节正文中保留以下内部小节结构，并按顺序展开：\n"
            f"{child_outline}"
        )
    children = section.get("children") if isinstance(section.get("children"), list) else []
    if not children:
        return "- 无内部小节结构"
    return _format_children(children)


def _format_children(children: list[dict], *, depth: int = 1) -> str:
    lines: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        title = str(child.get("title") or child.get("section_id") or "").strip()
        if title:
            lines.append(f"{'  ' * (depth - 1)}- {title}")
        grand_children = child.get("children") if isinstance(child.get("children"), list) else []
        if grand_children:
            lines.append(_format_children(grand_children, depth=depth + 1))
    return "\n".join(line for line in lines if line).strip() or "- 无内部小节结构"


def _build_section_guidance(section_title: str) -> str:
    normalized_title = _normalize_section_title(section_title)
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
    if section_title in guides:
        return guides[section_title]
    if normalized_title in guides:
        return guides[normalized_title]
    if any(token in normalized_title for token in ("项目概述", "改造目标", "建设范围")):
        return (
            "请围绕项目背景、现状痛点、改造目标和建设范围组织内容。"
            "优先使用二级小节，不要再细分过多四级编号；每一部分都要落到本项目的高炉鼓风机、电机启动冲击、联锁改造和连续运行需求。"
        )
    if "设计依据" in normalized_title or "边界条件" in normalized_title:
        return (
            "请聚焦设计依据、适用标准、现场环境条件和接口/安装边界。"
            "不要混入启动时序、波形曲线、去磁逻辑或具体控制步骤；标准条款只写类别和适用边界，不要伪造编号。"
        )
    if "lci" in normalized_title.casefold() or "变频软起动装置方案" in normalized_title:
        return (
            "请重点说明 LCI 装置的系统组成、工作原理、启动过程、并网/工频切换和装置级技术特点。"
            "不要把 PLC 监控细节、柜体制造要求或安装条件展开成主内容，这些应留给后续专章。"
        )
    if "同步电机" in normalized_title and any(token in normalized_title for token in ("适配", "接口")):
        return (
            "请按“电机基础参数适配、励磁与转子回路接口、测温/振动与辅机接口、启动切换联锁边界、待确认关键点”组织内容，"
            "重点写同步电机与 LCI 系统之间的参数适配、信号类别、控制方向、保护出口归属和切换判据来源。"
            "小节标题尽量使用“适配要求 / 接口方案 / 联锁边界 / 待确认关键点”这类命名，不要出现松散并列式标题。"
            "不要混入柜体尺寸、开关柜通用参数、自然环境表或泛化供货清单，也不要把通用控制总论写成本章主内容。"
            "未明确项不要在各节反复重复“建议在深化设计阶段确认”，应集中在末尾统一归纳，并明确属于参数确认、接口清单确认、保护定值校核还是切换时序确认。"
        )
    if any(token in normalized_title for token in ("柜体结构", "布线", "制造要求")):
        return (
            "请按柜体结构、防护等级、板材与防腐、母排与端子排、二次配线、标识和制造工艺组织内容。"
            "不要带入主回路原理图解读、控制逻辑或无关图示说明。"
        )
    if any(token in normalized_title for token in ("安装布置", "基础条件", "安装条件")):
        return (
            "请聚焦设备布置原则、基础与底板、进出线与接地、检修通道和施工接口条件。"
            "重点说明空间、荷载、通道、桥架/电缆沟和接地要求，不要写控制原理或波形分析。"
        )
    if "供货范围" in normalized_title or "接口分工" in normalized_title:
        return (
            "请明确主设备供货范围、软件与随机资料、专用工具、接口分工和供方/需方边界。"
            "备品备件若资料不足，只能写推荐或待确认项，不要把售后服务内容混成供货范围。"
        )
    if any(token in normalized_title for token in ("可靠性", "安全性", "运维保障")):
        return (
            "请围绕启动可靠性、保护与联锁安全、故障监测、可维护性和运维保障组织内容。"
            "避免直接复述图表中的时长数字，除非其确有助于说明可靠性边界。"
        )
    if any(token in normalized_title for token in ("技术资料", "服务承诺", "资料提交")):
        return (
            "请聚焦技术文件交付范围、图纸资料、铭牌标识、使用维护资料和现场服务支持。"
            "服务承诺要写成交付和响应边界，不要混入设备原理说明或无关图表引用。"
        )
    return "请保持客户外发口径，围绕章节目标输出结构化、可执行、可审阅的技术方案内容。"


def _normalize_section_title(section_title: str) -> str:
    normalized = re.sub(TITLE_PREFIX_PATTERN, "", str(section_title or "")).strip()
    return re.sub(r"\s{2,}", " ", normalized)


def _format_recommended_assets(recommended_assets: list[dict]) -> str:
    if not recommended_assets:
        return "- 暂无推荐资产"

    lines: list[str] = []
    for item in recommended_assets[:5]:
        label = str(item.get("display_title") or item.get("title") or item.get("caption") or item.get("asset_type") or "参考资产")
        page_no = item.get("page_no")
        heading = item.get("heading_path")
        document_name = item.get("document_name")
        usage_mode = item.get("usage_mode")
        reason = item.get("reason")
        asset_type = item.get("asset_type")
        asset_id = item.get("asset_id")
        visual_role = item.get("visual_role")
        review_required = item.get("review_required")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        audit_status = item.get("asset_audit_status") or metadata.get("asset_audit_status")
        quality_score = item.get("asset_quality_score") or metadata.get("asset_quality_score")
        preview_text = str(item.get("preview_text") or "").strip()
        raw_table_markdown = str(metadata.get("raw_table_markdown") or "").strip()
        parts = [label]
        if asset_type and asset_id:
            parts.append(f"占位符 [[ASSET:{str(asset_type).upper()}:{asset_id}]]")
        if asset_type:
            parts.append(f"资产类型 {asset_type}")
        if visual_role:
            parts.append(f"视觉类型 {visual_role}")
        if audit_status:
            parts.append(f"审核状态 {audit_status}")
        if quality_score is not None and quality_score != "":
            parts.append(f"质量分 {quality_score}")
        if page_no:
            parts.append(f"页码 {page_no}")
        if document_name:
            parts.append(f"来源 {document_name}")
        if heading:
            parts.append(f"章节 {heading}")
        if preview_text and preview_text != label:
            parts.append(f"摘要 {preview_text[:120]}")
        if asset_type == "table" and raw_table_markdown and raw_table_markdown != preview_text:
            parts.append(f"表格内容 {raw_table_markdown[:500]}")
        if usage_mode:
            parts.append(f"使用方式 {usage_mode}")
        if review_required:
            parts.append("需人工确认")
        if reason:
            parts.append(f"推荐原因 {reason}")
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)


def _format_asset_candidates(asset_candidates: list[dict]) -> str:
    if not asset_candidates:
        return "- 暂无候选资产"
    formatted = _format_recommended_assets(asset_candidates[:8])
    return (
        "以下为备选图表资产池，不代表可直接引用。若推荐资产为空或明显不匹配，可从候选中选择最匹配者；"
        "若候选也不匹配，则不要输出资产占位符。\n"
        f"{formatted}"
    )


def _format_assembled_draft(assembled_draft: str) -> str:
    if not assembled_draft:
        return "暂无已组装章节草稿"
    return (
        "请将下面这份基于复用块拼装出的技术草稿整理成正式客户稿，保留技术密度、结构和 [[ASSET:...]] 占位符。\n"
        f"{_xml_block('assembled_draft', assembled_draft)}"
    )


def _format_reuse_pack(reuse_pack: dict) -> str:
    blocks = reuse_pack.get("reusable_blocks") or []
    parameter_candidates = _format_parameter_candidates(reuse_pack.get("parameter_candidates") or {})
    if not blocks:
        if parameter_candidates:
            return "- 暂无可复用正文块；以下为可用参数证据，生成时只能据此填写参数，不得扩展为未确认规格。\n" + parameter_candidates
        return "- 暂无可复用块，必要时再回退到常规写作。"

    lines = []
    retrieval_mode = str(reuse_pack.get("retrieval_mode") or "").lower()
    block_limit = len(blocks) if retrieval_mode == "full_section" else 5
    for block in blocks[:block_limit]:
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
    if parameter_candidates:
        lines.extend(["", "参数证据候选：", parameter_candidates])
    return "\n".join(lines)


def _format_parameter_candidates(parameter_candidates: dict) -> str:
    if not isinstance(parameter_candidates, dict):
        return ""
    lines: list[str] = []
    project_params = parameter_candidates.get("project") if isinstance(parameter_candidates.get("project"), dict) else {}
    if project_params:
        lines.append("- 当前项目参数：")
        for key, value in project_params.items():
            lines.append(f"  - {key}: {value}")
    evidence_items = parameter_candidates.get("evidence") if isinstance(parameter_candidates.get("evidence"), list) else []
    if evidence_items:
        lines.append("- 参数证据：")
        for item in evidence_items[:4]:
            if not isinstance(item, dict):
                continue
            heading = " > ".join(str(part) for part in (item.get("heading_path") or []) if part)
            lines.append(
                f"  - 来源：{item.get('source_title') or item.get('source') or '参数证据'}"
                + (f" / {heading}" if heading else "")
                + f" / score={item.get('score')}"
            )
            content = str(item.get("content_md") or "").strip()
            if content:
                lines.append(f"    {content[:1200].replace(chr(10), chr(10) + '    ')}")
    return "\n".join(lines).strip()


def _format_replacement_constraints(reuse_pack: dict) -> str:
    replace_fields = reuse_pack.get("must_replace_fields") or []
    replacement_hints = reuse_pack.get("replacement_hints") or {}
    banned_terms = reuse_pack.get("banned_terms") or []
    placeholders = reuse_pack.get("required_asset_placeholders") or []

    lines = [
        f"- 必须替换字段：{', '.join(replace_fields) if replace_fields else '无'}",
    ]
    if replacement_hints:
        lines.append("- 当前项目可用替换值：")
        for key, value in replacement_hints.items():
            lines.append(f"  - {key}: {value}")
    if banned_terms:
        lines.append(f"- 禁止沿用词：{', '.join(str(item) for item in banned_terms)}")
    else:
        lines.append("- 禁止沿用词：无")
    if placeholders:
        lines.append("- 必须包含的资产占位符：")
        for item in placeholders:
            lines.append(f"  - {item.get('placeholder')} {item.get('title') or ''}".rstrip())
    else:
        lines.append("- 必须包含的资产占位符：无")
    return "\n".join(lines)


def _xml_block(tag: str, content: str) -> str:
    normalized = str(content or "").strip()
    return f"<{tag}>\n{normalized or '暂无'}\n</{tag}>\n"
