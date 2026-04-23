# Reuse-First 检索重构架构设计

更新时间：2026-04-21

## 0. Layer 2 补充说明

2026-04-20 起，Layer 2 完整版实现的架构补丁单独记录在 `output/RAG_test-layer2-contextual-hybrid-architecture.md`。

该补丁不替换本文件中的 `reuse-first` 章节优先原则，而是在其上补齐：

- 在线 ingestion 的上下文化文本
- 在线 `Retriever` 的 dense + sparse + rerank 主路径
- 历史方案库的 rerank stage 与 hybrid trace

2026-04-21 起，Layer 2 的执行约束也并入该补丁文档：真实样本只用于发现问题和回归验证，运行时代码不得滑向文档名级特判；`Step 2a` 达到停止条件后必须切回 `Step 2b/2c`。

2026-04-20 起，Layer 3 视觉检索支路的架构补丁单独记录在 `output/RAG_test-layer3-visual-retrieval-architecture.md`。

2026-04-20 起，Layer 4 AI Wiki 知识编译层的架构补丁单独记录在 `output/RAG_test-layer4-ai-wiki-architecture.md`。

## 1. 架构目标

把当前以 `chunk/block` 为主的复用检索架构，重构为以 `section` 为主、`block` 为辅的层级检索架构。

目标链路：

`Document Shortlist -> Section Shortlist -> Block Selection -> Asset Backfill -> Prompt Assembly -> Generation`

---

## 2. 设计原则

### 2.1 章节是真值层

每份历史方案必须先形成稳定的 `section_catalog`，后续所有 block、asset、citation 都挂到 `source_section_id` 上。

### 2.2 标题优先，不是正文优先

在“找类似章节”任务里：

- 章节标题
- 标题别名
- 标题家族
- 目录邻接关系

优先级必须高于行业词、产品词和正文局部词。

### 2.3 块检索不取消，但降级

块检索继续存在，但职责变成：

- 在已命中的章节内挑最有价值的正文段
- 辅助表格/图/公式资产挂接
- 提供细粒度引用和可追溯性

### 2.4 长上下文是受控能力，不是默认能力

整章直送 LLM 只在高置信且 token 可控时开启。

---

## 3. 当前代码基础与改造范围

本轮主要改造范围：

- [backend/app/services/parsing/section_catalog.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/section_catalog.py)
- [backend/app/services/parsing/case_library.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/case_library.py)
- [backend/app/services/vectorstore/chunker.py](/Volumes/thunder/code/RAG_test/backend/app/services/vectorstore/chunker.py)
- [backend/app/services/retrieval/case_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/retrieval/case_service.py)
- [backend/app/services/retrieval/service.py](/Volumes/thunder/code/RAG_test/backend/app/services/retrieval/service.py)
- [backend/app/services/composition/section_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/composition/section_service.py)

---

## 4. 目标数据模型

### 4.1 `section_catalog`

每个历史文档输出结构化章节目录，建议字段如下：

```json
{
  "section_id": "3.2.1",
  "source_heading": "2.1 高压变频器选型",
  "normalized_heading": "高压变频器选型",
  "heading_aliases": ["高压变频器选型", "变频器选型"],
  "heading_family": ["系统及方案介绍", "系统方案", "高压变频器选型"],
  "parent_id": "3.2",
  "level": 3,
  "section_path": "第三章 系统及方案介绍 > 二、系统方案 > 2.1 高压变频器选型",
  "normalized_section_path": "系统及方案介绍 > 系统方案 > 高压变频器选型",
  "page_span": [14, 16],
  "content_span": {
    "chunk_start": 26,
    "chunk_end": 34
  },
  "source_signals": ["toc", "body_heading", "parser_heading"],
  "audit_flags": []
}
```

当前代码已有一部分字段，本轮需补齐：

- `heading_family`
- `page_span`
- `content_span`
- 更稳定的 `source_signals`

### 4.2 `section_index`

新增章节级检索文本，建议每条记录包含：

- `document_title`
- `section_path`
- `normalized_heading`
- `heading_aliases`
- `section_summary`
- `detail_keywords`
- `section_type`
- `equipment_type`

推荐生成一个 `section_retrieval_text`：

`document_title + section_path + aliases + short summary + key terms`

### 4.3 `block_index`

块索引继续保留，但必须带完整章节上下文：

- `source_section_id`
- `section_path`
- `normalized_heading`
- `section_type`
- `content_form`
- `block_text`

同时新增 `contextualized_block_text`：

`document_title + section_path + block_text`

这样即使保留 chunk，也不再是“脱离章节身份的裸 chunk”。

---

## 5. Ingestion / Indexing 方案

### 5.1 Step 1：构建章节真值层

沿用当前 `section_catalog` 思路，但补齐以下能力：

1. TOC 与正文标题对齐
2. 正文标题提升为结构节点
3. 章节边界回挂到 chunk 范围
4. 输出 `page_span` 和 `content_span`
5. 同层编号标题不得互相串成父子，例如 `3.1 / 3.2 / 3.3` 必须优先保持 sibling
6. 对 `3.1 供货商应按照...` 这类“带编号但本质是整句条款正文”的 parser heading，在 catalog 层折叠掉，不作为稳定检索章节
7. 对 `1. 每一种控制板卡需要提供至少一块备件；`、`开关柜具备调试及上电条件`、`1. 卖方将为客户提供...` 这类清单条款 / 状态句 / 服务承诺句，不得误提升为 root section；它们属于正文证据，不属于稳定章节锚点
8. 目录页不能只支持 dotted TOC，还要支持列表型 TOC（如 `目录 Index` 下的 `1. 工厂设计环境 ... 3`）；目录尾随页码只进入 `page_span`，不能进入最终章节标题，否则会和正文标题长出两套平行根节点
9. 标题展示文本需要做轻量归一：清除 `tab` / 重复空格等版式噪声，但不能把 `第三章 系统及方案介绍` 这种合法空格压没
10. 对 `图5率单元结构示意图` 这类图题、`13. 6 IE/BA0IEFT` 这类代码串标题、`吴德朝` 这类作者姓名，不得进入章节树；它们可以保留在正文证据里，但不能作为 section anchor
11. `normalized_heading` / `heading_aliases` 允许比展示标题更激进地归一，例如把 `维 护`、`单 元`、`售 后` 这类 OCR 断空格折叠回正常词形，以提升章节匹配和别名召回稳定性
12. 对 `3 ． 功率单 元 原理` 这类“编号与标点之间夹杂空格”的标题，`normalized_heading` 仍需正确剥离编号前缀，得到 `功率单元原理`，不能残留前导 `．`

### 5.2 Step 2：块挂接到章节

现有 `source_section_id` 继续保留，但不再只用于展示。

后续要求：

- 所有 narrative/table/figure/formula block 必须挂到某个 section
- 无法挂接时打 `audit_flag`

同时补一条 parse gate：

- `legacy_doc_placeholder` 或 `fallback + binary format(pdf/docx/...)` 的历史方案文档，统一标记为 `parse_insufficient`
- `parse_insufficient` 文档不写入历史方案 chunks / raw_document / figure_assets / projection cache
- 历史库刷新与 case library 直构建只消费通过 parse gate 的文档
- 重解析如果把原本可用文档打回 `parse_insufficient`，需要同步把旧的 case library / AI Wiki / visual cache 结果清退

### 5.3 Step 3：章节摘要化

为每个 section 生成轻量 `section_summary`，用于：

- 章节索引
- rerank
- 低 token 预算下的 `section-pack mode`

同时，`section_retrieval_text` 需要比 `section_summary` 更干净：

- `section_summary` 本身不能携带 `<!-- image -->` 这类占位标记；图像型叶子节是否需要 synthetic fallback，应该由独立内部信号判断，而不是继续依赖脏摘要文本
- `section_summary` 需要额外剔除独立 OCR 码串行，例如 `41597:882`、`BNE7883`、`O.HAKE`、短英文字碎片等，避免它们在跨行拼接后污染正文语义
- 短 caption / 图页残片（例如 `C 型变频器：2050`）不能以独立摘要或长摘要尾巴的形式残留；清洗后若只剩孤立标点，也应继续裁掉
- 合法提示型尾冒号需要保留，例如 `风机启动曲线如下：`、`现场登记电机配置及负载参数如下：`；非提示型孤立尾冒号应清除
- 对 `Degree of protection\tIP 31 / 防护等级` 这类 tab 形式的双语属性表，不应把整块原表直接拼进摘要；应优先抽取中文属性项和对应值，生成短属性串
- `E m e r son`、`TT L`、`Q S1 / Q S2`、`S inusoidal PWM` 这类 Latin OCR 拉裂，需要在摘要层先折回正常词形
- 当摘要里已经出现句子级正文时，`10 kV 母线`、`利旧`、`to VFD` 这类短图签标签应从摘要中剔除，不能继续贴在正文前后
- `压缩机额定功率压缩机最大工况轴功率...` 这类“字段名连写链”不应被当作有效摘要；它们保留在检索正文即可，`section_summary` 应回落为 `None`
- `| 标准 | 标题 | ...` 这类 pipe 表头残片，无论是独立单行还是以 `| ... | 正文句子` 的前缀形式出现，都应在摘要层裁掉
- `序号 设备名称 型号规格 数量 ...` 这类字段表头前缀，如果后面已经进入正文句子，应剥掉前缀而保留正文；仅剩 `尺寸暂定 / 待定 / 预留` 这类占位短语时，摘要应回落为空
- 父节存在直辖正文时，`section_summary` 应优先从直辖 chunk 提取；只有直辖摘要清洗后为空或退化成表头残桩时，才回退到子树摘要
- 摘要层应做中文标点空格归一，例如 `研发 、设计、制造业务 ，...` 需要折回 `研发、设计、制造业务，...`
- `图…` 这类中途插入的图号残桩不应保留在摘要句面中
- `见下面一次方案图电动机启动描述：` 这类“图题/标签词直接黏到描述标签”的内联边界，需要在摘要层补回句号或冒号边界，避免标题词直接并入正文
- `装置在下列环境条件下能正常工作 2. 海拔...` 这类“引导语 + 枚举条目”之间缺失分隔符的摘要，应补成 `...工作：2. 海拔...`
- `高压开关柜 DO 水电阻柜 DI 进出线方式：...` 这类“接口对象 + DI/DO/AI/AO 标签串”如果只是前导接口映射前缀，应在摘要层剥掉，只保留后面的正文型规格描述
- 枚举型规格摘要需要先做条款压缩、再做长度截断；不能先在原文层截断再压缩，否则会把 `二次控制电源：用户提供AC220V/380V，10A` 这类取值条目截坏
- 子节标题在拼入检索文本前，需要先走 `normalized_heading` 级别的 OCR 断空格归一
- 紧邻 `<!-- image -->` 的图题碎片（如 `图10 安装图`）不能进入检索文本
- 路径串里的标题片段允许做轻量空格归一，以减少 `维 护 / 单 元 / 其 他` 这类 OCR 断词对检索的污染

### 5.4 Step 4：上下文化块文本

对 block embedding / BM25 文本追加：

- 文档标题
- 章节路径
- 标题别名

这一步对应 Anthropic 提到的 contextual retrieval 思路。

---

## 6. 查询与召回流程

### 6.1 Query Builder

输入不是只看标题，而是拆成三个层：

1. `title_intent`
   - 章节标题
   - 标题别名
2. `detail_intent`
   - purpose
   - 用户补充细节
3. `context_intent`
   - industry
   - product_line
   - equipment_type

其中：

- `title_intent` 权重最高
- `detail_intent` 第二
- `context_intent` 最低

### 6.2 Stage A：Document Shortlist

沿用当前 `case_candidates`，但增加一条硬信号：

- 历史方案目录中是否出现目标标题/标题家族

### 6.3 Stage B：Section Shortlist

这是主阶段，按 section 检索。

推荐评分结构：

```text
section_score =
  0.40 * title_exact_alias_score +
  0.20 * heading_family_score +
  0.15 * detail_intent_score +
  0.10 * outline_neighbor_score +
  0.10 * semantic_score +
  0.05 * context_score
```

必须是 hybrid：

- BM25 / exact
- embedding
- rule bonus / penalty
- rerank

### 6.4 Stage C：Block Selection

只在 shortlisted section 内选 block。

策略：

- narrative section 优先 narrative block
- parameter section 优先 parameter / table block
- 可补 1 到 2 个邻近 block
- 仍保留当前 `neighbor` 扩展逻辑，但必须在同章节或同章节族内优先

### 6.5 Stage D：Asset Backfill

图表资产不再全局盲搜，优先顺序：

1. 同源章节
2. 同文档相邻章节
3. 全局资产回退

---

## 7. Prompt Assembly 方案

### 7.1 `section-pack mode` 默认模式

默认向模型提供：

- 目标章节标题
- 章节 purpose
- top 2 到 4 个章节候选摘要
- 每个候选章的 top narrative blocks
- 必要表格/图/公式资产
- 替换字段与禁用词

适用场景：

- 命中尚可，但整章过长
- 标题有泛化冲突
- token 预算紧张

### 7.2 `full-section mode` 受控模式

满足以下条件时允许：

- top1 章节得分明显领先
- top1 / top2 标题冲突低
- 总 token 不超预算
- 章节噪声率低

此时直接提供：

- 1 到 2 个完整源章节正文
- 同章节关键表格/图
- 必须替换字段

### 7.3 防止长上下文失焦

即使进入 `full-section mode`，也不能简单乱拼。

必须：

1. 把最强命中章节放在 prompt 前部
2. 次强章节放后部，不放在中间冗长位置
3. 先给章节摘要，再给正文
4. 限制候选章数量，默认不超过 2

---

## 8. 运行时输出契约

每个 `reuse_first` 章节需要输出：

```json
{
  "retrieval_mode": "section_pack | full_section | baseline_fallback",
  "selected_sections": [
    {
      "section_id": "3.2.1",
      "section_path": "...",
      "score": 0.83,
      "reason": [
        "normalized_section_title_match",
        "heading_family_match",
        "detail_overlap=接口"
      ]
    }
  ],
  "selected_blocks": [
    {
      "block_id": "...",
      "source_section_id": "3.2.1"
    }
  ],
  "token_budget": {
    "section_material_tokens": 3200,
    "asset_tokens": 420,
    "within_budget": true
  }
}
```

---

## 9. 分阶段落地建议

### Phase 1

- 补齐 `section_catalog`
- 为 block 全量挂 `source_section_id`
- 新增 `section_summary`

### Phase 2

- 在 `CaseLibraryService` 中把 `retrieve_sections` 提升为主通路
- 把 block 检索彻底改成“章节内选择”

### Phase 3

- 在 `SectionCompositionService` 中加入 `section-pack` / `full-section` 模式切换
- 输出完整 trace

### Phase 4

- 建立评估集
- 统计 `top1/top3`
- 调整标题权重、detail 权重、token 门限

---

## 10. 架构结论

最终结论：

- 你的方向是对的，必须把“章节”提到 chunk 之上
- 但最佳实现不是“整章替代 chunk”
- 而是“章节做检索锚点，chunk 做章节内部证据，长上下文按条件开启”

这条路能最大化复用仓库里已经存在的 `section_catalog` / `source_section_id` / `retrieve_sections` 基础，而不是推倒重来。

---

## 11. 模型接入策略备注

当前阶段的模型接入策略固定如下：

- 开发阶段继续使用 `OpenAI-compatible` 接口作为统一接入层
- 生成、图资产审校、多模态摘要等能力优先复用当前 `OPENAI_BASE_URL` / `OPENAI_MODEL` 配置
- 不在当前开发阶段引入 `DashScope SDK` 作为主运行时依赖，避免本地开发与测试矩阵膨胀

生产阶段的预留策略如下：

- 若最终落地选择阿里千问体系，优先评估 `Qwen + DashScope SDK`
- `DashScope SDK` 重点用于 `OCR`、原始文件输入、视觉理解和图语义增强等阿里原生能力更完整的场景
- `OpenAI-compatible` 仍可作为兼容回退层，但不作为生产环境下 OCR / 视觉增强能力的首选实现

落地原则：

- 开发期先保证接口稳定和链路可调试
- 生产期再按真实成本、模型效果和运维约束决定是否切换到 `DashScope SDK`
