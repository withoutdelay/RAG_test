# 售前智能系统 MVP — 工程实现规格说明书 V2

> 本文档用于替代 [spec.md](/Volumes/thunder/code/RAG_test/spec.md) 的后续实施基线。  
> V2 不推翻现有解析、检索、脱敏和 LLM 底座，而是重定义一期主链路、数据模型和验收口径。

---

## 1. 文档目标

V2 的目标不是“直接自动生成终稿”，而是稳定产出一份：

- 可审查
- 可追溯
- 可校验
- 可导出

的方案草案包。

一期默认交付物包括：

- Requirement Card
- Clarification List
- Evidence Bundle
- Proposal Outline
- Section Drafts
- Validation Report
- Review Tasks
- 可编辑导出文件（Markdown 或 DOCX）

---

## 2. 范围与非目标

### 2.1 一期范围

- 单一产品线 / 单一模板族
- 单一主要用户角色：售前工程师
- 3-10 份高质量历史材料作为冷启动语料
- 支持文本输入、RFP 附件上传、历史案例检索、章节草案生成、人工审核、导出
- 全链路保留引用、版本和审计信息

### 2.2 一期非目标

- 不承诺完全替代人工完成最终对外交付
- 不把 GraphRAG 作为主路径
- 不做复杂图纸自动重绘
- 不做跨行业泛化能力承诺
- 不做重型工作流平台引入

---

## 3. 核心设计原则

### 3.1 结构化优先

所有长链路任务都必须先落到结构化工件，再进入下一步。禁止“RFP 长文本直接生成 40 页终稿”的主路径设计。

### 3.2 需求先于检索

检索必须基于 Requirement Card 的字段和 facets，而不是仅基于原始长文本。

### 3.3 证据先于生成

生成大纲和章节前，必须先固化一版 Evidence Bundle。技术性段落必须绑定可追溯证据。

### 3.4 程序校验优先于模型润色

参数冲突、引用失效、未消解占位符、缺失必填章节等问题必须由程序化规则兜底，不依赖模型自觉修正。

### 3.5 人工审批保留最终决策权

模型负责抽取、起草和辅助审查；程序负责规则校验和状态推进；人负责关键技术判断、图表确认、承诺边界和最终导出。

### 3.6 统一模型边界

所有外部模型都必须通过统一 LLM Adapter 调用，继续复用现有脱敏网关能力。

---

## 4. 一期主链路

```text
项目创建
  -> 输入需求 / 上传资料
  -> 提取 Requirement Card
  -> 生成 Clarification List
  -> 解决 P0 / P1 关键缺失
  -> 检索 Evidence Bundle
  -> 生成 Proposal Outline
  -> 按章节生成 Section Drafts
  -> 运行 Validation
  -> 生成 / 处理 Review Tasks
  -> 人工审核与修改
  -> 导出
```

### 4.1 状态机

项目状态统一使用以下枚举：

- `CREATED`
- `INPUT_READY`
- `REQUIREMENT_DRAFTED`
- `BLOCKED_FOR_CLARIFICATION`
- `EVIDENCE_READY`
- `OUTLINE_READY`
- `DRAFT_GENERATING`
- `DRAFT_READY`
- `REVIEW_REQUIRED`
- `EXPORTABLE`
- `EXPORTED`
- `FAILED`

### 4.2 导出阻塞条件

以下任一条件成立时，系统必须阻塞导出：

- 存在未解决的 P0 澄清项
- 缺少必填章节
- 存在参数冲突且未被人工接受
- 存在无来源的关键技术断言
- 存在未确认的复杂图纸替换
- 存在未消解占位符或导出模板变量

---

## 5. 系统架构

```text
[Web App / Block Editor]
        |
        v
[API Layer + Auth + Audit]
        |
        v
[Workflow Service / State Machine]
   |         |         |         |         |
   |         |         |         |         +--> [Export Service]
   |         |         |         +------------> [Validation Service]
   |         |         +----------------------> [Generation Service]
   |         +--------------------------------> [Retrieval Service]
   +------------------------------------------> [Requirement Service]

[Ingestion Workers] --> [Object Storage]
                       [PostgreSQL]
                       [Vector Index]
                       [Redis / Queue]
                       [Audit Logs]
```

### 5.1 模块职责

`Requirement Service`
- 提取 Requirement Card
- 生成 Clarification List
- 合并人工补充
- 维护 Global Parameter Registry

`Retrieval Service`
- metadata pre-filter
- hybrid recall
- rerank
- Evidence Bundle 构建
- 低置信度处理

`Generation Service`
- 生成 Outline
- 逐节生成 Section Draft
- 控制最小必要上下文
- 强制结构化输出

`Validation Service`
- 确定性程序校验
- 语义辅助审查
- 生成 Validation Report
- 创建 Review Tasks

`Export Service`
- 读取明确版本快照
- 渲染 Markdown / DOCX
- 输出导出日志

`Workflow Service`
- 驱动状态机
- 编排异步任务
- 处理失败重试、人工接管和回退

---

## 6. 数据模型

### 6.1 核心实体

`projects`
- 保存项目基础信息、产品线、当前状态
- 持有 current requirement / outline / draft version 指针

`raw_documents`
- 保存原始输入和知识库文档
- 支持去重、分级、版本标签和解析状态

`parsed_blocks`
- 保存 heading / paragraph / table / figure_caption 等解析块
- 作为后续知识块和引用链路的底层来源

`figure_assets`
- 保存图纸、架构图、表格图片等资产
- 区分 `reference_only`、`review_required`、`reusable`

`knowledge_chunks`
- 保存检索使用的 chunk
- 必须标记 `source_of_truth`
- 必须标记 `derived_type`

`requirement_cards`
- 版本化保存需求卡
- 保存 missing_items、blocking_items、source_refs、confirmed_by_user

`evidence_bundles`
- 保存检索产物
- 绑定 requirement_card_id
- 保存 quality_score

`proposal_outlines`
- 保存结构化大纲树
- 绑定 requirement_card_id 和 evidence_bundle_id

`section_drafts`
- 保存章节正文、引用、假设、参数快照、校验结果

`review_tasks`
- 保存 clarification / param_conflict / figure_confirm / final_review 等待办

`jobs`
- 保存 parse / index / extract / retrieve / outline / generate / validate / export 等异步任务
- 必须包含 `trace_id`

`audit_logs`
- 记录查看、编辑、审批、导出、模型调用、证据使用

### 6.2 版本化要求

以下对象必须显式版本化：

- Requirement Card
- Evidence Bundle
- Outline
- Draft
- Ruleset
- Prompt Template
- Model Config

导出动作必须绑定到一组明确版本快照。

---

## 7. 检索设计

### 7.1 输入面

Requirement Card 至少拆成这些检索 facets：

- `industry`
- `scenario`
- `solution_type`
- `product_line`
- `key_parameters`
- `constraints`
- `required_sections`
- `disallowed_conditions`

### 7.2 检索流水线

Stage 1: Metadata Pre-filter
- 优先过滤 `product_line`、`template_family`、`confidentiality_level`、`industry`

Stage 2: Hybrid Recall
- BM25
- vector recall
- glossary / parameter exact match（可选）

Stage 3: Rerank
- cross-encoder 或轻量 LLM 重排

Stage 4: Bundle Builder
- case candidates
- section candidates
- figure candidates
- table candidates
- parameter candidates
- template candidates

### 7.3 检索失败处理

当 Evidence Bundle 质量分低于阈值时，系统必须至少执行以下之一：

- 提示用户补充需求
- 放宽过滤条件重检索
- 允许人工手选参考案例
- 阻止直接进入全文生成

### 7.4 引用规则

- 技术性段落必须有引用
- 引用只能指向 `source_of_truth = true`
- synthetic query 不能作为 citation source
- 图表引用必须包含 `asset_id` 与页码

---

## 8. 生成设计

### 8.1 生成产物

`Requirement Card`
- 必须为结构化 JSON

`Clarification List`
- 每项至少包含 `field_name`、`priority`、`reason`、`question`、`blocking`

`Outline`
- 每节至少包含 `section_id`、`title`、`purpose`、`mandatory`、`expected_evidence_types`、`needs_human_review`

`Section Draft`
- 每节至少包含 `content_md`、`citation_refs`、`assumptions`、`open_questions`、`used_global_params`

### 8.2 Outline 生成规则

- 必填章节不可删除
- 不相关章节不可乱加
- 每节必须声明“为什么存在”
- 需要图表或人工确认的章节，必须在大纲层提前标记

### 8.3 Section 生成规则

仅向模型注入最小必要上下文：

- 当前 section spec
- 当前 requirement card
- 当前章节相关证据子集
- 全局参数表
- 规则与禁用承诺表

禁止：

- 一次性灌入所有文档 / 所有检索结果
- 为了润色牺牲事实可追溯性
- 缺参时臆造具体数值

### 8.4 全局参数注册表

系统必须维护 Global Parameter Registry，至少保存：

- 参数名
- 当前值
- 单位
- 来源
- 置信度
- 是否已人工确认

章节生成和校验必须引用同一参数快照。

### 8.5 假设声明

允许在缺参条件下继续草拟时，必须显式输出：

- `assumption`
- `impact`
- `needs_confirmation`

假设不能藏在正文里。

---

## 9. 校验设计

### 9.1 校验分层

Layer 1: 确定性程序校验
- 必填字段缺失
- 必填章节缺失
- 参数单位冲突
- 参数值冲突
- 引用缺失 / 失效
- 未消解占位符
- 禁用承诺词
- 图表引用未确认
- 导出模板变量未替换

Layer 2: 模型辅助语义校验
- 章节是否偏离目标
- 技术结论是否与需求矛盾
- 是否存在未声明假设
- 章节是否重复 / 冗余

Layer 3: 人工审核
- 技术路线判断
- 图纸对外使用判断
- 参数是否可承诺
- 是否达到可发送门槛

### 9.2 推荐错误码

- `VAL001` Requirement Card 缺少 P0 字段
- `VAL002` 必填章节缺失
- `VAL003` 同一参数多值冲突
- `VAL004` 技术性段落无引用
- `VAL005` 引用目标不存在或失效
- `VAL006` 存在未确认复杂图表引用
- `VAL007` 存在未消解占位符
- `VAL101` 章节可能偏离目标
- `VAL102` 可能存在未声明假设
- `VAL103` 检索证据相关性偏低

### 9.3 校验输出

Validation Report 至少包含：

- `project_id`
- `draft_version`
- `status`
- `errors`
- `warnings`
- `review_tasks_created`

---

## 10. API 边界

### 10.1 项目与输入

- `POST /api/projects`
- `GET /api/projects/{id}`
- `POST /api/projects/{id}/inputs/text`
- `POST /api/projects/{id}/documents`
- `GET /api/projects/{id}/documents`

### 10.2 需求抽取与澄清

- `POST /api/projects/{id}/extract-requirement`
- `GET /api/projects/{id}/requirement-card/latest`
- `PATCH /api/projects/{id}/requirement-card/{cardId}`
- `POST /api/projects/{id}/clarifications/{itemId}/resolve`

### 10.3 检索与证据包

- `POST /api/projects/{id}/retrieve-evidence`
- `GET /api/projects/{id}/evidence-bundles/latest`
- `POST /api/projects/{id}/evidence-bundles/{bundleId}/pin`

### 10.4 大纲与章节生成

- `POST /api/projects/{id}/generate-outline`
- `GET /api/projects/{id}/outlines/latest`
- `PATCH /api/projects/{id}/outlines/{outlineId}`
- `POST /api/projects/{id}/generate-sections`
- `POST /api/projects/{id}/sections/{sectionId}/regenerate`
- `PATCH /api/projects/{id}/sections/{sectionId}`

### 10.5 校验、审核与导出

- `POST /api/projects/{id}/validate`
- `GET /api/projects/{id}/validation/latest`
- `GET /api/projects/{id}/review-tasks`
- `POST /api/projects/{id}/review-tasks/{taskId}/resolve`
- `POST /api/projects/{id}/export`
- `GET /api/projects/{id}/exports/latest`

### 10.6 作业查询

- `GET /api/jobs/{jobId}`

---

## 11. 异步任务设计

### 11.1 最小队列集合

- `parse_queue`
- `index_queue`
- `generation_queue`
- `validation_queue`
- `export_queue`

### 11.2 任务原则

- 幂等：同一输入重复提交不应生成重复对象
- 可重试：临时失败支持自动重试
- 可取消：长任务可被用户终止
- 可追踪：每个任务都必须带 `job_id`、`trace_id`、`project_id`

---

## 12. 复用与重构策略

以下现有实现直接保留：

- 文档上传、解析、切块和基础检索底座
- 脱敏网关
- 统一 LLM Adapter
- 基础审计与导出设施

以下现有实现需要改造成 V2 主线：

- 将以 `generation_task` 为中心的流程改为以 Requirement / Evidence / Outline / Draft 为中心
- 将 review 点扩展为更通用的 review tasks
- 将当前生成 API 改造成按工件推进的 API
- 将状态机从“outlining / generating / finalizing”升级为项目级状态机

以下内容暂不进入一期关键路径：

- GraphRAG
- 复杂图纸自动重绘
- 多行业模板泛化
- 过重的 DAG / workflow 平台

---

## 13. 一期验收标准

- 售前从输入到“可审草案包”时间缩短 ≥ 40%
- Requirement Card 的 P0/P1 字段完整度 ≥ 85%
- Top-5 Evidence Bundle 被人工判定为“有用”的比例 ≥ 70%
- 技术章节引用覆盖率 ≥ 95%
- 导出结果中不存在未消解占位符、空章节、失效引用
- 确定性校验 P0 错误未解决时，系统不得允许导出

---

## 14. 文档关系

- [spec.md](/Volumes/thunder/code/RAG_test/spec.md)：原始工程规格，保留为 V1 基线
- [项目开发计划.md](/Volumes/thunder/code/RAG_test/项目开发计划.md)：原始开发计划，保留为 V1 基线
- [presales_assistant_engineering_implementation_v1.md](/Volumes/thunder/code/RAG_test/presales_assistant_engineering_implementation_v1.md)：V2 的主要研究依据

后续新增实现应默认以本文件为准。
