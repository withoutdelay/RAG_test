# 下一轮迭代计划：知识治理、图资产稳定与证据筛选

> 创建日期：2026-05-05
> 基于：`output/next-iteration-optimization-plan.md` 完成度复盘、真实试用问题、客户 PK 风险评估
> 当前结论：上一轮 Layer 0-4 主线已阶段性完成；下一轮不再继续堆叠检索规则，而是把领域知识、图资产和证据选择变成可审计、可评估、可回滚的产品能力。

---

## 1. 目标

本轮目标是提升 MVP 在真实客户 PK 中的稳定性和泛化能力，重点解决三类问题：

1. 图资产召回必须稳定。
2. Wiki / taxonomy 从代码硬编码迁移到可审计数据层。
3. 生成前增加 LLM 证据筛选 / 章节证据重排，避免相似度误召回直接污染正文。

成功标准不是“新增更多规则”，而是：

- 领域词、章节类型、设备类型、资产类型可审计、可版本化、可回滚。
- Wiki 产物区分草稿和已发布，只有通过质量评分或人工审核的内容进入生成链路。
- 章节生成前能对候选证据做二次筛选，降低“技术章节召回交付资料 / 培训 / 负载数据”等跨章节污染。
- 图资产能提供多个候选并解释命中原因，错误图不会静默进入正文。

---

## 2. 非目标

- 不引入 GraphRAG 作为主链路。
- 不把 LLM 放到每次底层召回打分里做实时大规模重排。
- 不继续在 `section_service.py` / `block_taxonomy.py` 里堆业务词补丁。
- 不要求一次性覆盖完整电气行业；先覆盖当前真实方案库和客户演示场景。

---

## 3. 当前基线

上一轮已完成：

- Layer 0 同义词/术语扩展：已有 `backend/app/services/domain/synonyms.py`，检索与 taxonomy 已接入。
- Layer 1 章节真值层：`section_catalog`、`source_section_id`、`contextual_text`、`semantic_retrieval_text` 已落地。
- Layer 2 Contextual Hybrid Retrieval：dense + sparse + rerank trace 主链已落地。
- Layer 3 视觉检索支路：textual / visual / structural 融合已落地，真视觉后端路线已保留。
- Layer 4 AI Wiki：编译、prompt 注入、质量回扫、retrieval prior、holdout eval 已落地。

当前不足：

- Wiki 产物质量未完全可信，术语表尤其弱。
- taxonomy 和同义词仍大量硬编码在 Python 中。
- 图资产解析和视觉角色判断不稳定。
- 证据候选进入章节生成前缺少强语义筛选。
- 历史方案库和 Wiki 缺少面向用户的知识审计入口。

---

## 4. 执行顺序

### Phase 1：固化当前基线并建立数据化 taxonomy

#### 1.1 建立数据目录

新增：

- `backend/data/domain_taxonomy/synonyms.json`
- `backend/data/domain_taxonomy/section_types.json`
- `backend/data/domain_taxonomy/equipment_types.json`
- `backend/data/domain_taxonomy/asset_roles.json`
- `backend/data/domain_taxonomy/retrieval_policies.json`
- `backend/data/domain_taxonomy/forbidden_phrases.json`

#### 1.2 迁移硬编码知识

从以下位置迁移领域知识到 JSON：

- `backend/app/services/domain/synonyms.py`
- `backend/app/services/vectorstore/block_taxonomy.py`
- `backend/app/services/knowledge/wiki_compiler.py`
- `backend/app/services/retrieval/asset_service.py`
- `backend/app/services/composition/section_service.py`

Python 代码保留机制，不保留大段领域词表。

目标调用形态：

```python
taxonomy = taxonomy_registry.infer(text)
policy = retrieval_policy_registry.resolve(taxonomy)
```

而不是在流程代码中继续写大量 `if keyword in text`。

#### 1.3 加载与回退策略

- JSON 加载失败时使用最小内置保守默认值。
- 配置文件 schema 校验失败时启动失败，避免静默使用坏配置。
- 单元测试覆盖每类 taxonomy 文件的 schema、加载、推断和回退。

---

### Phase 2：published_wiki / draft_wiki 双层结构

#### 2.1 目录结构

新增 Wiki 分层：

- `backend/data/knowledge_wiki/published/`
- `backend/data/knowledge_wiki/draft/`
- `backend/data/knowledge_wiki/rejected/`
- `backend/data/knowledge_wiki/audit_log.jsonl`

当前生成链路只能读取 `published`。

`draft` 用于存放自动编译和 LLM 归纳出的候选知识。

#### 2.2 Wiki item 数据模型

每条知识必须包含：

- `item_id`
- `item_type`: `product_family | section_template | term_alias | asset_type_rule`
- `canonical_name`
- `aliases`
- `summary`
- `source_documents`
- `evidence`
- `quality_score`
- `quality_flags`
- `status`: `draft | review_required | auto_approved | published | rejected | quarantined`
- `created_by`: `compiler | llm_compiler | human`
- `updated_at`

证据字段必须至少包含：

- `sample_id`
- `raw_document_id`
- `source_section_id`
- `heading_path`
- `evidence_quote` 或 `asset_id`

没有来源证据的 Wiki item 不允许发布。

#### 2.3 发布规则

- `quality_score >= 0.82` 且无阻断 flag：可自动发布。
- `0.60 <= quality_score < 0.82`：进入人工待审。
- `< 0.60`：进入 rejected 或 quarantined。
- 与已发布知识冲突：必须人工审核。

---

### Phase 3：Wiki 审计 API 与前端页面

#### 3.1 API

新增：

- `GET /wiki/audit/summary`
- `GET /wiki/audit/items`
- `GET /wiki/audit/items/{item_id}`
- `POST /wiki/audit/items/{item_id}/approve`
- `POST /wiki/audit/items/{item_id}/reject`
- `POST /wiki/audit/items/{item_id}/edit`
- `POST /wiki/audit/items/{item_id}/merge`
- `POST /wiki/audit/rebuild`
- `GET /wiki/audit/diff`

#### 3.2 前端页面

新增页面：

- `/library/wiki`

页面能力：

- 查看产品族、章节模板、术语别名、图片类型审核候选。
- 按状态筛选：草稿、待审、已发布、已拒绝、隔离。
- 查看来源文档、来源章节、证据片段、命中次数、质量评分。
- 对比 draft 与 published diff。
- 执行批准、拒绝、编辑、合并、回滚。

#### 3.3 审计原则

用户不是从零造知识，而是审核系统自动生成的候选知识。

人工只处理：

- 低置信候选。
- 冲突候选。
- OCR 噪声。
- 跨章节污染。
- 高影响知识变更。

---

### Phase 4：LLM 离线编译任务

#### 4.1 处理范围

第一版只处理：

- 产品族
- 章节模板
- 术语别名
- 图片类型审核

不处理完整知识图谱。

#### 4.2 编译链路

历史方案上传或重解析后：

1. 解析原始文档。
2. 生成 case library projection。
3. 程序抽取候选知识。
4. LLM 离线归纳候选。
5. 程序做 schema 和证据校验。
6. 进入 `draft_wiki`。
7. 通过评分或人工审核后进入 `published_wiki`。

#### 4.3 LLM 输出约束

LLM 输出必须是 JSON schema。

每个结论必须带来源证据：

- 产品族必须说明来自哪些文档和章节。
- 章节模板必须说明来自哪些真实标题。
- 术语别名必须说明在哪些文档中出现。
- 图片类型审核必须说明图片本体判断、标题判断和上下文判断。

LLM 不得凭空新增未在历史库出现过的专业词。

无证据时只能输出：

```json
{
  "status": "insufficient_evidence"
}
```

#### 4.4 视觉审核

如果配置了视觉模型，图片类型审核优先使用视觉模型。

目标分类：

- `engineering_figure`
- `single_line_diagram`
- `main_circuit_topology`
- `layout_drawing`
- `cabinet_outline`
- `product_photo`
- `table_image`
- `text_fragment`
- `asset_fragment`
- `page_furniture`
- `unknown`

图片审核只进入 `draft_wiki` 或 asset metadata，不直接改变正文生成结果。

---

### Phase 5：Wiki 质量评分与发布门控

#### 5.1 评分维度

每个 Wiki item 计算：

- 来源完整性：是否有文档、章节、证据片段或资产 ID。
- 证据覆盖度：是否来自多个一致来源。
- OCR 噪声：是否包含乱码、断词、异常字符比例过高。
- 跨章节污染：技术模板是否混入商务、培训、交付资料等内容。
- 冲突检测：是否与 published item 的主叫法、别名、类型冲突。
- 泛词风险：是否只是“系统 / 方案 / 设备 / 柜”等低信息词。
- 复用价值：是否被多个检索/生成场景实际命中。

#### 5.2 阻断 flag

以下 flag 必须阻断自动发布：

- `missing_source_evidence`
- `cross_section_contamination`
- `ocr_noise_high`
- `conflicts_with_published`
- `over_generic_term`
- `asset_type_uncertain`
- `source_section_missing`

#### 5.3 进入生成链路的条件

只有满足以下条件的 item 可以进入生成链路：

- `status == published`
- 无阻断 flag
- schema 校验通过
- 证据来源仍然存在

---

### Phase 6：生成前 LLM 证据筛选 / 章节证据重排

#### 6.1 触发位置

在 hybrid retrieval / case library retrieval 之后，进入章节生成 prompt 之前增加 evidence selector。

输入：

- 当前章节标题、目的、taxonomy、outline 子结构。
- Top-N section candidates。
- Top-N reusable blocks。
- Top-N asset candidates。
- `published_wiki` 相关项。

输出：

- `selected_sections`
- `selected_blocks`
- `selected_assets`
- `rejected_candidates`
- `selection_reason`
- `risk_flags`

#### 6.2 LLM 筛选原则

LLM 只负责筛选和解释，不直接生成正文。

必须拒绝：

- 与章节目标不一致的证据。
- 技术章节中的培训、交付、商务资料。
- 备件章节中的环境/供电条件表。
- 主回路/拓扑章节中的产品照片。
- 只有匹配原因但没有正文证据的 case fallback。

#### 6.3 回退策略

- LLM 筛选失败时，回退到当前 deterministic rerank。
- 低置信筛选结果不阻断生成，但必须在 trace 中标红。
- 对高风险章节可以要求用户人工确认证据。

#### 6.4 可观察性

Draft 页面需要展示：

- 候选证据。
- 被选中的证据。
- 被拒绝的证据。
- 拒绝原因。
- 是否受 Wiki prior 影响。
- 是否使用 LLM selector。

---

### Phase 7：图资产稳定性专项

#### 7.1 同源绑定

图资产必须优先绑定：

- `sample_id`
- `raw_document_id`
- `source_section_id`
- `heading_path`
- `page_no`

缺失 `source_section_id` 的资产不能作为高置信图进入正文。

#### 7.2 多候选展示

每个需要图的章节至少返回多个候选：

- 首选图。
- 备选图。
- 被过滤图。
- missing-asset 诊断。

用户可以手动替换图资产。

#### 7.3 错图门控

质量门控必须识别：

- 小碎片。
- 页面装饰。
- 文字截图。
- 产品照片误当拓扑图。
- 布置图误当柜体外形图。
- 表格图误当系统图。

#### 7.4 验收指标

在 holdout 文档上统计：

- 需要图章节的 Top-3 命中率。
- 首图准确率。
- 错图进入正文率。
- missing-asset 可解释率。

---

### Phase 8：回归评测与 PK 准备

#### 8.1 固定评测集

至少包含：

- 临沂钢铁鼓风机电机及启动装置技术方案。
- 上电湛江中纸高浓磨机项目成套方案。
- 一个接近当前历史库的客户模拟需求。
- 一个偏离历史库的泛化模拟需求。

#### 8.2 分开评估 outline 与 draft

评估分两段：

1. Outline 生成质量。
2. 使用人工/基准 outline 覆盖后，评估 draft 质量。

避免 outline 错误掩盖 draft 链路能力。

#### 8.3 核心指标

- Outline 章节匹配度。
- 技术章节证据准确率。
- 图资产 Top-3 命中率。
- 错误证据进入正文率。
- 章节内废话比例。
- 内部残留词出现次数。
- Word 导出可编辑性。
- 完整生成耗时。

#### 8.4 目标阈值

- 完整 draft 生成硬上限：10 分钟。
- MVP 目标耗时：5 分钟内。
- 关键技术章节证据准确率：>= 80%。
- 需要图章节 Top-3 图命中率：>= 80%。
- 错图进入正文率：<= 10%。
- 内部残留词：0。

---

## 5. 建议分支

建议从当前固化点切出：

```text
codex/knowledge-governance-next-20260505
```

该分支专门用于下一轮：

- Wiki governance
- taxonomy data migration
- LLM offline compiler
- evidence selector
- asset stability

---

## 6. 验收测试计划

### 后端

- taxonomy JSON schema / load / fallback tests。
- Wiki compiler draft/published tests。
- Wiki audit API tests。
- LLM compiler schema validation tests。
- Evidence selector deterministic fallback tests。
- Asset gating tests。
- Existing retrieval regression tests。

### 前端

- `/library/wiki` 页面加载。
- 状态筛选。
- item approve/reject/edit/merge。
- draft vs published diff。
- Draft 页面展示 evidence selected/rejected trace。
- 图候选替换流程。

### 集成

- 上传历史方案后生成 draft Wiki。
- 审核通过后进入 published Wiki。
- 生成章节只读取 published Wiki。
- 未审核或 rejected Wiki item 不影响生成。
- holdout eval 输出 outline/draft/asset 三类指标。

---

## 7. 风险

| 风险 | 缓解 |
|------|------|
| LLM 编译产生幻觉词 | 强制证据引用，无证据不发布 |
| 人工审核成本高 | 只审核低置信和冲突项，高置信自动发布 |
| taxonomy JSON 迁移引入回归 | 保留最小内置默认值，并加回归测试 |
| evidence selector 增加延迟 | 只对 Top-N 候选做筛选，失败回退 deterministic rerank |
| 图资产仍受解析质量限制 | 增加云解析兜底和 missing-asset 诊断，不静默错配 |

---

## 8. 完成定义

本轮完成时，应满足：

- 当前硬编码领域词大部分迁移到 `backend/data/domain_taxonomy/*.json`。
- `published_wiki` / `draft_wiki` 双层结构可运行。
- `/library/wiki` 可以审计 Wiki 产物。
- LLM 离线编译能产出产品族、章节模板、术语别名、图片类型审核候选。
- Wiki item 有质量评分和发布门控。
- 章节生成前有 evidence selector，并在 trace 中可见。
- 图资产错误进入正文的概率显著下降。
- 至少两份 holdout 文档完成 outline / draft / asset 分项评测。
