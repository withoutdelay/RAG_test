# Asset Retrieval 设计稿

> 适用范围：对应 [pdf_parsing_development_plan.md](/Volumes/thunder/code/RAG_test/pdf_parsing_development_plan.md) 中的 `Phase 4：Asset Retrieval 与生成阶段引用增强`  
> 目标：让高风险图、表、公式虽然不自动进入主知识真值库，但可以被检索、被推荐、被引用，并供售前工程师人工复用。

---

## 1. 设计目标

当前系统已经能做到：

- 文本正文进入主向量库
- 高风险图、表、公式被保留下来
- 大表不再污染主检索
- 图表资产可回溯到页码、bbox、上下文

当前还做不到：

- 在生成某一章节时，自动找出最相关的图、表、公式
- 将这些资产以“推荐参考材料”的形式提供给售前工程师

本设计解决的就是这个缺口。

一句话定义：

`文本用于生成，资产用于推荐与人工复用。`

---

## 2. 业务定位

### 2.1 本阶段解决什么

本阶段不试图自动理解所有复杂图表，而是提供一条更稳妥的业务路径：

1. 高风险资产继续保留
2. 不进入主知识真值库
3. 单独建立检索能力
4. 在章节生成时推荐最相关资产
5. 由售前工程师决定是否复用、替换、改写

### 2.2 本阶段不解决什么

- 不自动把原理图、波形图、电路图写进最终稿
- 不自动把大表重建成结构化真值
- 不自动把公式 OCR 结果当作事实
- 不自动替换客户项目参数

---

## 3. 设计原则

1. 资产推荐优先于资产自动落稿  
   系统只负责找出“最值得看”的资产，不负责把它们直接写进最终正文。

2. 高风险资产永远不绕过人工关口  
   复杂图、复杂表、公式图块默认都是 `reference_only` 或 `manual_review_required`。

3. 不依赖脏表全文进入主向量库  
   检索依赖的是 `Asset Card`，而不是原始噪声文本。

4. 检索以“上下文 + 元数据 + 向量”混合完成  
   不能只靠纯文本 embedding。

5. 资产推荐结果必须可解释  
   每个推荐结果都要说明“为什么相关”。

---

## 4. 资产范围

### 4.1 资产类型

MVP 阶段统一归为 3 类：

- `figure`
- `table`
- `formula_candidate`

说明：

- 当前数据库里已经有 `figure_assets`
- `table` 可以继续复用现有 `FigureAsset.asset_type="table"`
- `formula_candidate` 在 MVP 可先不单独建表，先作为 `figure asset` 的一种派生类型，通过 `metadata.visual_role` / `metadata.formula_risk` 标识

### 4.2 资产来源

资产可来自以下路径：

1. `Docling` 抽取的图片资产
2. `Docling` 抽取的表格资产
3. 被安全入库规则拦下并保留的 `table asset`
4. PDF 审计阶段识别出的公式候选图块

---

## 5. MVP 推荐架构

### 5.1 推荐对象：Asset Card

MVP 不建议让生成逻辑直接面对原始 asset。

建议在 `figure_assets` 之上，派生出一层统一对象：

`Asset Card`

它不是原图/原表本身，而是“资产的可检索描述卡片”。

### 5.2 Asset Card 应包含的字段

基础标识：

- `asset_card_id`
- `asset_id`
- `project_id`
- `raw_document_id`
- `document_id`

资产类型：

- `asset_type`: `figure | table | formula_candidate`
- `visual_role`
- `risk_level`
- `reuse_mode`

定位信息：

- `page_no`
- `bbox`
- `heading_path`
- `source_ref`

检索描述字段：

- `title`
- `caption`
- `context_before`
- `context_after`
- `section_heading`
- `document_name`
- `doc_type`

控制字段：

- `reconstruction_status`
- `review_required`
- `preserve_in_vector_db`
- `recommended_usage_mode`

检索文本：

- `retrieval_text`

显示信息：

- `asset_uri`
- `thumbnail_uri`（可选）
- `preview_text`

### 5.3 retrieval_text 组成规则

`retrieval_text` 不应直接等于原始表格全文或图片 OCR 全文。

建议由以下字段拼接：

- `heading_path`
- `title`
- `caption`
- `context_before`
- `context_after`
- 资产类型标签
- 文档类型标签
- 风险标签

对 table asset 额外补充：

- 表格所属章节
- 表格主题短摘要
- `indexing_reasons`

但不直接拼接整张大表的 `raw_table_markdown`。

---

## 6. 数据模型建议

### 6.1 MVP 方案：不新增主表，先做派生索引

MVP 最稳妥的方式不是立刻加很多新表，而是：

1. 继续保留 `figure_assets`
2. 在服务层派生 `Asset Card`
3. 将 `Asset Card` 单独建立索引

这样优点是：

- 对现有 schema 改动小
- 与 `safe ingestion + table asset only` 方向兼容
- 后续如果需要，再把 `asset_cards` 正式落库

当前默认实施路线：

- `Phase 4` 先采用“服务层派生 `Asset Card` + 单独检索索引”
- 不在 MVP 首批实现中新增 `asset_cards` 表
- 只有在资产推荐价值被验证、且确实出现状态流需求后，再升级为正式落表

### 6.2 后续增强方案：新增 `asset_cards` 表

如果 MVP 证明有效，第二步再考虑新增 `asset_cards` 表，字段建议包括：

- `id`
- `project_id`
- `figure_asset_id`
- `asset_type`
- `retrieval_text`
- `risk_level`
- `recommended_usage_mode`
- `embedding_status`
- `metadata`

不建议一开始就为了这个改很多数据库结构。

---

## 7. 索引与检索方案

### 7.1 双索引

生成阶段保留两套检索：

1. `text retrieval`
   - 面向正文 chunk
   - 进入主向量库

2. `asset retrieval`
   - 面向 `Asset Card`
   - 与正文索引分开

### 7.2 Asset Retrieval 的检索键

检索时不只看 embedding，还要带结构过滤：

- `project_id`
- `asset_type`
- `doc_type`
- `risk_level`
- `review_required`
- `reconstruction_status`

### 7.3 排序策略

建议采用：

`final_score = semantic_score + metadata_boost + section_match_boost - risk_penalty`

其中：

- `semantic_score`
  - 来自 `retrieval_text` 的向量相似度
- `metadata_boost`
  - 同章节
  - 同文档类型
  - 同领域标签
- `section_match_boost`
  - 目标章节标题与 `heading_path` / `caption` / `title` 高匹配
- `risk_penalty`
  - 高风险资产降低自动推荐排序，但不完全排除

### 7.4 风险控制

以下资产即使被检索到，也默认只能作为参考：

- `reconstruction_status=pending` 的 table asset
- `formula_candidate`
- `engineering_figure`
- `review_required=true`

---

## 8. 与生成阶段的集成

## 8.1 接入位置

优先接入 V2 主线中的：

- `generate-outline` 之后
- `generate-sections`
- `section regenerate`

最直接的接入点是章节生成前：

1. 先做文本检索
2. 再做资产检索
3. 将推荐资产作为辅助上下文传给生成逻辑

### 8.2 生成模型如何使用推荐资产

模型不应直接消费原图/原表的原始脏内容，而应该看到结构化提示：

```json
{
  "recommended_assets": [
    {
      "asset_id": "...",
      "asset_type": "table",
      "title": "电机参数表",
      "page_no": 12,
      "heading_path": "4.3 电机技术参数",
      "usage_mode": "reference_only",
      "reason": "与当前章节“硬件配置清单”高度相关，且为原方案中的关键参数表",
      "review_required": true
    }
  ]
}
```

模型可以：

- 在正文里提示“建议参考原始参数表”
- 生成占位性引用说明
- 输出给售前工程师待确认的建议

模型不可以：

- 默认把这张表的内容当作最终真值写进正文

### 8.3 推荐结果的输出形式

建议对每一章节返回：

- `draft_text`
- `citations`
- `recommended_assets`

其中 `recommended_assets` 是单独字段，不混进 citations。

---

## 9. API 设计建议

### 9.1 资产检索接口

建议新增：

- `POST /api/v1/projects/{project_id}/assets/search`

输入：

- `query`
- `asset_types`
- `top_k`
- `section_context`
- `doc_types`

输出：

- 推荐资产列表
- 每项包含：
  - `asset_id`
  - `asset_type`
  - `title`
  - `caption`
  - `page_no`
  - `heading_path`
  - `reason`
  - `usage_mode`
  - `review_required`

### 9.2 章节生成返回结构增强

建议在章节生成接口的响应里预留：

- `recommended_assets`

这样前端可以在章节编辑页直接展示“建议参考资产”侧栏。

### 9.3 后续可补接口

- `POST /api/v1/projects/{project_id}/asset-cards/rebuild`
- `GET /api/v1/documents/{document_id}/asset-cards`
- `POST /api/v1/assets/{asset_id}/mark-used`

这些可以放到增强阶段，不一定要首批实现。

---

## 10. 前端与人工复用方式

前端上不建议把推荐资产做成“自动插入文稿”，而应该做成：

### 10.1 章节侧栏

对每一章节展示：

- 推荐图
- 推荐表
- 推荐公式候选

每个卡片显示：

- 标题
- 缩略图或类型标签
- 页码
- 所属章节
- 推荐原因
- 风险标记

### 10.2 工程师可执行动作

售前工程师可以：

- 打开原资产
- 复制引用说明
- 标记“采用”
- 标记“忽略”
- 标记“替换为当前项目版本”

### 10.3 MVP 不做

- 不做自动替换客户参数
- 不做自动图表重绘
- 不做自动把推荐资产写进最终正文

---

## 11. 风险与误区

### 11.1 不能只靠上下文向量

很多工程图、参数表、波形图的上下文非常像。

所以不能只靠 embedding，需要结合：

- `asset_type`
- `heading_path`
- `doc_type`
- 风险标签
- 项目上下文

### 11.2 不要把推荐资产等同于最终内容

被推荐，只代表“值得参考”，不代表“可直接发客户”。

### 11.3 不能让高风险资产绕过 review

即便检索命中非常高，只要它是：

- 原理图
- 波形图
- 大表
- 公式候选

默认仍应标成 `reference_only`。

---

## 12. MVP 实施顺序

建议按下面顺序做：

1. 统一 `Asset Card` 生成逻辑
2. 为 `Asset Card` 建立单独索引
3. 提供资产检索接口
4. 在章节生成中接入 `recommended_assets`
5. 前端显示推荐资产侧栏
6. 再决定是否补“已采用/已忽略”状态流

---

## 13. 验收标准

MVP 通过标准：

1. 高风险图、表、公式不会进入主向量库污染正文检索
2. 章节生成时可返回最相关的 `recommended_assets`
3. 推荐结果对售前工程师可解释
4. 推荐结果能直接打开原始资产
5. 工程师可以在不依赖自动 OCR 成功的前提下复用原资产

---

## 14. 与现有计划的关系

本设计与现有路线完全兼容：

- 不推翻 `safe ingestion`
- 不推翻 `table asset only`
- 不推翻 `review_required`
- 不要求立即完成表格重建或公式真值恢复

它的定位是：

`把已经保住、但暂时不能自动理解的高风险资产，提前变成业务上可复用的参考材料。`
