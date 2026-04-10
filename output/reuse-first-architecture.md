# Reuse-First 检索重构架构设计

更新时间：2026-04-10

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

### 5.2 Step 2：块挂接到章节

现有 `source_section_id` 继续保留，但不再只用于展示。

后续要求：

- 所有 narrative/table/figure/formula block 必须挂到某个 section
- 无法挂接时打 `audit_flag`

### 5.3 Step 3：章节摘要化

为每个 section 生成轻量 `section_summary`，用于：

- 章节索引
- rerank
- 低 token 预算下的 `section-pack mode`

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
