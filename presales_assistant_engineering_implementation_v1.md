# 售前方案助手（工程落地版）实施规格书 v1.0

> 文档类型：Implementation Spec / Engineering Playbook  
> 适用范围：一期 MVP，仅覆盖 **单一产品线 / 单一方案家族 / 单一模板体系** 的“售前方案助手”  
> 面向读者：后端工程师、AI 应用工程师、前端工程师、产品负责人、试点业务负责人、信息安全/IT  
> 文档目标：让团队可以据此拆分任务、开始编码、组织测试、推进试点

---

## 0. 阅读说明

本文档只回答四类问题：

1. **做什么**：一期系统必须完成哪些业务动作，明确不做什么；
2. **怎么做**：服务拆分、数据模型、API、异步任务、校验逻辑如何设计；
3. **怎么验收**：要看哪些指标，哪些错误不能放过；
4. **怎么上线**：数据准备、试点边界、部署与运维要满足什么条件。

本文档不做以下事情：

- 不做供应商宣传材料；
- 不承诺“全自动替代售前专家”；
- 不承诺“自动生成最终可发客户的定稿”；
- 不把某个模型/向量库/图谱当成银弹；
- 不在未经验证的数据基础上给出绝对 ROI 承诺。

---

## 1. 产品定义与成功标准

## 1.1 问题定义

售前人员面对新客户需求时，通常会遇到四个连续问题：

1. 输入不规范：客户需求来自邮件、纪要、招标文件、附件、微信转述，格式混乱；
2. 知识不集中：历史方案、图表、参数表、模板散落在多个文件中；
3. 初稿产出慢：高质量方案高度依赖少数资深方案工程师；
4. 质量不稳定：章节缺失、参数前后不一致、引用不清、图表误用。

一期系统的任务不是替代专家，而是把“从接收需求到产出首版可审草稿”的时间和认知负担显著降下来。

## 1.2 一期交付物

系统对用户交付的是一个 **Draft Package**，包含：

- 结构化需求卡（Requirement Card）
- 阻塞项与澄清问题列表
- 相似案例与证据包（Evidence Bundle）
- 方案大纲（Outline）
- 分章节草稿（Section Drafts）
- 校验报告（Validation Report）
- 待人工确认项（Review Tasks）
- 可编辑导出文件（Markdown 或 DOCX，至少一种）

## 1.3 一期成功的定义

以下指标用于试点验收。数值是 **初始门槛**，必须在试点数据上校准后再固化：

### A. 业务指标

- 首版草稿准备时间较现状下降 **40% 以上**
- 专家从零起草转为“审阅+修改”的项目占比达到 **60% 以上**
- 试点用户愿意在真实项目中持续使用，而不是只用于演示

### B. 质量指标

- 需求卡必填字段完整率 ≥ **85%**
- Top-5 检索结果中“业务可参考”命中率 ≥ **70%**
- 技术性章节引用覆盖率 ≥ **95%**
- 程序化阻塞错误漏检率 = **0**（针对规则已覆盖的错误类型）
- 导出草稿中不存在未消解占位符、空章节、失效引用

### C. 工程指标

- 解析和索引流程可重试、可追踪
- 每次生成都能回溯所用输入、证据、模型版本、规则版本
- 整体流程支持异步执行和断点恢复

## 1.4 一期非目标

以下内容不在一期承诺范围内：

- 自动生成最终签发版对外方案
- 自动报价、成本核算、商业承诺
- 自动生成 CAD、电气原理图、一次接线图
- 一期同时覆盖合同、标书、售后、法务
- 跨多产品线统一知识底座
- 在缺少关键参数时仍强行给出完整技术结论

---

## 2. 第一性原理与关键架构决策

以下决策是本文档的约束前提，不建议在一期推翻。

## 2.1 决策 D1：需求必须先结构化，后检索，最后生成

原因：原始需求高度非结构化，直接把整包输入扔给模型会导致召回泛化、生成失焦。  
落地要求：

- 系统必须先生成 Requirement Card；
- 检索必须基于 Requirement Card 的字段和 facets，而不是仅基于长文本；
- 若 P0 阻塞项未解决，流程必须暂停在澄清阶段。

## 2.2 决策 D2：采用“结构化中间产物链”，禁止 one-shot 生成长文档

标准产物链如下：

`Raw Inputs -> Requirement Card -> Clarification List -> Evidence Bundle -> Outline -> Section Drafts -> Validation Report -> Export`

任何一步失败都应允许重试或人工接管。

## 2.3 决策 D3：一期采用“混合检索 + 重排”，不把 GraphRAG 作为主路径

原因：

- 一期核心瓶颈通常不在“缺图谱”，而在“需求建模、文档清洗、切分和验收”；
- 如果同质资料规模仍是几十到几百份文档，先用简单但可控的架构更划算。

落地要求：

- 先做 metadata filter + BM25/keyword + vector recall + rerank；
- GraphRAG 只作为二期备选，不进入一期关键路径。

## 2.4 决策 D4：复杂图纸以“引用+人工确认”为主，不做自动重绘

系统可做：

- 图题、图注、页码、上下文抽取
- 图表资产检索
- 在草稿中插入引用建议与占位

系统不可做：

- 让 LLM 凭上下文重画复杂工程图
- 未经确认直接替换客户级图纸

## 2.5 决策 D5：模型负责起草，程序负责校验，人负责签字

分工如下：

- **模型**：抽取、检索辅助、总结、起草、语义审查
- **程序**：字段校验、参数一致性、引用校验、状态机、权限审计
- **人**：业务澄清、关键技术判断、图表确认、最终导出

## 2.6 决策 D6：所有外部模型接入必须走统一 LLM Adapter

原因：降低供应商耦合，统一治理。  
落地要求：

- 前端和业务服务不得直接调用模型 API；
- 统一记录 model_id、temperature、max_tokens、prompt_version、response_id、cost_estimate；
- 支持按项目策略切换模型或禁用外部模型。

## 2.7 决策 D7：所有派生内容必须标注来源类型

来源类型必须至少区分：

- original：原始事实
- extracted：从原始内容抽取的结构化信息
- synthesized：模型推导/合成内容
- user_confirmed：人工确认后的内容

派生内容不得冒充原始事实。

---

## 3. 系统边界与角色分工

## 3.1 输入边界

系统允许接收：

- 纯文本需求描述
- PDF / DOCX / 图片附件
- 会议纪要、邮件整理稿
- 已有方案模板
- 历史方案与配套参数资料

系统不应直接依赖：

- 未授权的外部行业资料
- 无版本标识的旧模板
- 无法确认来源的第三方图纸

## 3.2 输出边界

系统可输出：

- 结构化需求卡
- 章节化草稿
- 证据引用
- 待确认项与风险提示
- 可编辑文档

系统不可输出：

- 未经人审的最终对外承诺稿
- 自动签发的技术承诺
- 无证据来源的关键断言

## 3.3 角色与责任

| 角色 | 输入责任 | 审核责任 | 结果责任 |
|---|---|---|---|
| 售前人员 | 录入需求、补充材料、回答澄清问题 | 初审需求卡与大纲 | 对业务表达负责 |
| 售前技术负责人 | 选择/确认关键案例、确认图表适用性 | 审核技术章节、参数、接口 | 对技术可行性负责 |
| 知识管理员 | 维护模板、资料、元数据、禁用项 | 维护知识库质量 | 对知识可用性负责 |
| 系统管理员/IT | 管理权限、部署、日志、模型接入策略 | 审核安全配置 | 对平台运行负责 |

---

## 4. 端到端流程与状态机

## 4.1 主流程

```text
项目创建
  -> 输入需求/上传资料
  -> 提取 Requirement Card
  -> 识别澄清问题
  -> 解决 P0/P1 关键缺失
  -> 检索 Evidence Bundle
  -> 生成 Outline
  -> 按章节生成 Section Drafts
  -> 运行 Validation
  -> 生成 Review Tasks
  -> 人工审核与修改
  -> 导出
```

## 4.2 状态定义

| 状态 | 说明 | 进入条件 | 退出条件 |
|---|---|---|---|
| `CREATED` | 项目已创建，尚无有效输入 | 创建项目 | 有文本或文档输入 |
| `INPUT_READY` | 输入已上传 | 至少一份有效输入 | 触发需求抽取 |
| `REQUIREMENT_DRAFTED` | 已生成需求卡草稿 | 抽取任务成功 | 人工确认或继续澄清 |
| `BLOCKED_FOR_CLARIFICATION` | 关键字段缺失 | 存在未解决 P0 项 | P0 全部解决或人工放行 |
| `EVIDENCE_READY` | 已产出证据包 | 检索完成 | 触发大纲生成 |
| `OUTLINE_READY` | 已产出大纲 | 大纲校验通过 | 触发章节生成 |
| `DRAFT_GENERATING` | 章节正在生成 | 已有大纲 | 所有章节完成或失败 |
| `DRAFT_READY` | 章节草稿已完成 | 所有章节完成 | 触发校验 |
| `REVIEW_REQUIRED` | 需要人工审阅 | 校验发现阻塞项或系统要求人审 | 审核完成 |
| `EXPORTABLE` | 可以导出 | 阻塞项清零且必审项已签核 | 触发导出 |
| `EXPORTED` | 已导出 | 导出成功 | 新版本编辑后可重新进入 REVIEW_REQUIRED |
| `FAILED` | 某异步任务失败 | 达到失败条件 | 人工重试或回退 |

## 4.3 阻塞条件

以下任一条件成立时，系统必须阻塞导出：

- P0 澄清项未解决
- 缺少必填章节
- 存在参数冲突且未被人工接受
- 存在无来源的关键技术断言
- 存在未确认的复杂图纸替换
- 存在未消解占位符或导出模板变量

---

## 5. 总体架构

## 5.1 逻辑架构

```text
[Web App / Block Editor]
        |
        v
[API Gateway + Auth]
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
                       [Cache / Queue]
                       [Audit Log Sink]
```

## 5.2 组件职责

### A. Web App / Block Editor

必须支持：

- 项目创建与权限控制
- 输入材料上传
- Requirement Card 可视化编辑
- 澄清问题答复
- 证据面板（案例、章节、图表）
- 大纲查看与调整
- 章节级编辑、重写、接受/驳回
- Review Tasks 面板
- 导出与历史版本查看

### B. API Gateway

职责：

- 鉴权与授权
- 请求校验
- 审计身份注入
- 限流与租户隔离
- 统一返回 trace_id / request_id

### C. Workflow Service

职责：

- 持有项目状态机
- 编排异步任务
- 维护任务依赖
- 执行失败重试、人工接管、回退

建议：

- MVP 可用数据库驱动状态机 + 队列；
- 不建议一期引入过重的工作流平台。

### D. Ingestion Workers

职责：

- 文档清洗
- 结构化解析
- chunk 生成
- 元数据抽取
- 图表资产入库
- embedding 与索引

### E. Requirement Service

职责：

- 抽取 Requirement Card
- 生成缺失项
- 合并人工补充
- 维护全局参数注册表

### F. Retrieval Service

职责：

- 执行多路召回
- metadata filter
- 混合检索与重排
- 产出 Evidence Bundle
- 计算检索置信度

### G. Generation Service

职责：

- 生成 Outline
- 逐节生成草稿
- 控制输入上下文窗口
- 强制结构化输出
- 管理 prompt / schema / model version

### H. Validation Service

职责：

- 程序化规则校验
- 语义辅助审查
- 生成错误码、阻塞项、警告项
- 计算导出前门槛

### I. Export Service

职责：

- 将最新审定版本装配为可编辑文档
- 保留标题层级、图表占位、引用清单
- 写入导出日志

---

## 6. 数据准备与知识接入规范

## 6.1 启动数据要求

### POC 最低门槛

- 5 份高质量历史成功方案
- 3 份可对应原始需求/RFP 的案例
- 1 份当前标准方案模板
- 1 份术语表或参数字典
- 至少 1 位业务专家参与标注/验收

### MVP 推荐门槛

- 20–50 份同一产品线历史方案
- 10–20 份“需求文档 ↔ 最终方案”可映射样本
- 1–3 套稳定模板
- 参数字典 / 产品资料 / 术语表
- 10–20 张可复用图表资产（有明确使用边界）
- 20–50 个真实需求样本作为评测集

### 直接判定不适合启动的情况

- 资料来自多个完全不同产品线，模板完全不统一
- 只有 PDF 定稿，没有任何原始需求或专家可补充上下文
- 历史方案质量本身很差，存在大量复制粘贴错误
- 业务方希望一期直接替代专家并自动对外承诺

## 6.2 接入流程

### 步骤 1：收集与归类

文档至少要分成：

- `case_solution`
- `case_requirement`
- `template`
- `parameter_reference`
- `figure_asset`
- `other_attachment`

### 步骤 2：清洗与去重

必须执行：

- checksum 去重
- 页眉页脚/页码/版权声明去除
- 模板版本识别
- 文档语言与编码归一
- 非正文噪声过滤（目录、修订记录等单独标记）

### 步骤 3：结构化解析

解析结果必须保留：

- heading path
- 原始页码
- block 顺序
- 表格结构
- 图题与图注
- 解析置信度
- 原始块 ID 与文档 ID 的映射

### 步骤 4：切块

切块规则：

1. 以章节/小节边界优先；
2. 超长小节再按语义段落细分；
3. 表格、图注、公式单独成块；
4. 不允许固定 token 滑窗替代一切；
5. 每块必须携带 heading_path、page_range、doc_type、product_line 等 metadata。

### 步骤 5：元数据抽取

建议最低字段：

- product_line
- scenario
- industry
- customer_type
- solution_type
- power_range / capacity_range / key_specs
- compliance_tags
- template_version
- confidentiality_level
- success_flag（若已知）

### 步骤 6：索引构建

一期建议至少维护 3 类索引：

- `case_index`：项目级摘要与元数据
- `section_index`：章节/段落级检索
- `asset_index`：图表/表格资产检索

## 6.3 合成问题（Synthetic Queries）的使用边界

在历史方案缺少对应原始需求时，可选地为章节生成 synthetic queries，用于提高召回。必须满足：

- 单独存储为派生字段；
- `derived_type = synthesized_query`
- 检索默认降权；
- 永远不能作为对外引用证据；
- 若原始需求存在，优先使用原始需求表述。

---

## 7. 数据模型与存储设计

## 7.1 存储选型（MVP）

- **PostgreSQL**：主业务库 + JSONB + 事务
- **pgvector**（或独立向量库）：向量检索
- **Redis**：队列、缓存、短期会话状态
- **Object Storage**：原始文件、图表资产、导出文件
- **审计日志存储**：可先入 PostgreSQL，后续再接集中日志系统

## 7.2 核心实体

### 7.2.1 `projects`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| name | text | 项目名 |
| owner_user_id | uuid | 项目负责人 |
| product_line | text | 一期必须固定 |
| status | text | 状态机状态 |
| current_requirement_card_id | uuid | 当前需求卡 |
| current_outline_id | uuid | 当前大纲 |
| current_draft_version | int | 当前草稿版本 |
| created_at / updated_at | timestamptz | 时间戳 |

索引：

- `(owner_user_id, created_at desc)`
- `(product_line, status)`

### 7.2.2 `raw_documents`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid nullable | 项目输入文档可挂项目 |
| corpus_scope | text | `project` / `knowledge_base` |
| doc_type | text | 见上文 |
| file_uri | text | 对象存储地址 |
| file_name | text | 原始文件名 |
| checksum | text | 去重 |
| version_label | text | 版本标识 |
| parse_status | text | `pending/running/success/failed` |
| confidentiality_level | text | 数据分级 |
| created_at | timestamptz | 时间戳 |

唯一约束建议：

- `(checksum, corpus_scope)` 组合去重

### 7.2.3 `parsed_blocks`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| raw_document_id | uuid | 来源文档 |
| block_type | text | `heading/paragraph/table/figure_caption/formula` |
| heading_path | text[] | 章节路径 |
| page_start / page_end | int | 页码范围 |
| order_in_doc | int | 顺序 |
| text | text | 正文 |
| parse_confidence | numeric | 解析置信度 |
| block_hash | text | 防重复 |
| metadata | jsonb | 补充字段 |

建议索引：

- `(raw_document_id, order_in_doc)`
- gin index on `heading_path`
- trigram / full-text on `text`

### 7.2.4 `figure_assets`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| raw_document_id | uuid | 来源文档 |
| page_no | int | 所在页 |
| asset_uri | text | 原图地址 |
| asset_type | text | `curve/topology/architecture/table_image/photo/other` |
| title | text | 图题 |
| caption | text | 图注 |
| reuse_mode | text | `reference_only/review_required/reusable` |
| parse_confidence | numeric | 解析置信度 |
| metadata | jsonb | 额外信息 |

### 7.2.5 `knowledge_chunks`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| raw_document_id | uuid | 来源文档 |
| source_block_ids | uuid[] | 来源块 |
| chunk_type | text | `case_summary/section/table/figure_caption/parameter/glossary/synth_query` |
| text | text | 检索文本 |
| summary | text | 摘要 |
| metadata | jsonb | 检索过滤字段 |
| embedding | vector | 向量 |
| source_of_truth | bool | 是否可作为引用事实 |
| derived_type | text | `original/extracted/synthesized` |

建议索引：

- ivfflat/hnsw on embedding
- gin on metadata
- `(chunk_type, source_of_truth)`

### 7.2.6 `requirement_cards`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| version | int | 版本 |
| schema_version | text | schema 版本 |
| content | jsonb | 完整 requirement card |
| missing_items | jsonb | 缺失项 |
| blocking_items | jsonb | P0/P1 |
| confidence | numeric | 抽取置信度 |
| source_refs | jsonb | 来源块 |
| confirmed_by_user | bool | 是否人工确认 |

### 7.2.7 `evidence_bundles`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| requirement_card_id | uuid | 输入需求卡 |
| retrieval_version | int | 检索版本 |
| content | jsonb | 案例、章节、资产集合 |
| quality_score | numeric | 置信度 |
| created_at | timestamptz | 时间戳 |

### 7.2.8 `proposal_outlines`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| version | int | 版本 |
| outline_json | jsonb | 大纲树 |
| requirement_card_id | uuid | 基于哪版需求卡 |
| evidence_bundle_id | uuid | 基于哪版证据包 |
| validator_status | text | 校验状态 |

### 7.2.9 `section_drafts`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| draft_version | int | 草稿版本 |
| section_id | text | 大纲节点 ID |
| title | text | 节标题 |
| content_md | text | Markdown 正文 |
| citation_refs | jsonb | 引用列表 |
| assumptions | jsonb | 假设项 |
| global_param_snapshot | jsonb | 全局参数快照 |
| status | text | `generated/edited/review_required/approved/rejected` |
| validator_result | jsonb | 校验结果 |

### 7.2.10 `review_tasks`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| task_type | text | `clarification/param_conflict/figure_confirm/final_review` |
| blocking_level | text | `P0/P1/P2` |
| payload | jsonb | 任务内容 |
| assignee_user_id | uuid | 处理人 |
| status | text | `open/in_progress/resolved/rejected` |

### 7.2.11 `jobs`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| job_type | text | `parse/index/extract/retrieve/outline/generate/validate/export` |
| status | text | `queued/running/succeeded/failed/cancelled` |
| input_ref | jsonb | 输入引用 |
| output_ref | jsonb | 输出引用 |
| retry_count | int | 重试次数 |
| error_code | text | 错误码 |
| trace_id | text | 链路 ID |

### 7.2.12 `audit_logs`

记录：

- 谁在什么时候看过/改过/导出了什么
- 使用了哪个模型版本
- 哪些文档被作为证据用过

## 7.3 版本化原则

必须版本化的对象：

- Requirement Card
- Evidence Bundle
- Outline
- Draft
- Ruleset
- Prompt Template
- Model Config

导出动作必须绑定到一组明确版本，避免“同名不同内容”的审计问题。

---

## 8. 检索设计

## 8.1 输入：Requirement Facets

Requirement Card 需要被拆成多个检索面：

- `industry`
- `scenario`
- `solution_type`
- `product_line`
- `key_parameters`
- `constraints`
- `required_sections`
- `disallowed_conditions`

## 8.2 检索流水线

### Stage 1：Metadata Pre-filter

优先使用强约束过滤，减少搜索空间：

- product_line
- template_family
- confidentiality_level
- industry
- key parameter range（若已知）

### Stage 2：Hybrid Recall

并行执行：

- keyword / BM25
- vector recall
- optional exact match on glossary/parameter dictionary

### Stage 3：Cross-encoder / LLM Rerank（可选）

在候选集较大时执行重排，输出统一评分。

### Stage 4：Bundle Builder

将结果按用途分组：

- case_candidates
- section_candidates
- figure_candidates
- table_candidates
- parameter_candidates
- template_candidates

## 8.3 检索输出格式

Evidence Bundle 中每条证据必须包含：

```json
{
  "evidence_id": "ev_001",
  "type": "section",
  "source_doc_id": "doc_xxx",
  "source_title": "某历史方案",
  "page_range": [12, 14],
  "heading_path": ["3 系统设计", "3.2 控制策略"],
  "summary": "描述了高压变频启动策略及适用条件",
  "relevance_score": 0.87,
  "recommended_use": "可用于“控制策略说明”章节",
  "risk_note": "仅适用于 6kV 场景"
}
```

## 8.4 低置信度处理

当 Evidence Bundle 质量分低于阈值时，系统必须执行以下至少一项：

- 提示用户补充需求
- 放宽过滤条件重检索
- 允许人工手选参考案例
- 阻止直接进入全文生成

## 8.5 引用规则

- 技术性段落必须有来源引用
- 引用只允许指向 `source_of_truth = true` 的对象
- synthetic query 不能作为 citation source
- 图表引用必须带 `asset_id` 与页码

---

## 9. 生成设计

## 9.1 生成产物链

### A. Requirement Card

必须结构化输出，禁止自由文本替代。

### B. Clarification List

每条澄清项字段：

- field_name
- priority
- reason
- question
- blocking(bool)

### C. Outline

必须结构化输出，至少包含：

- section_id
- title
- purpose
- mandatory
- expected_evidence_types
- needs_human_review
- children[]

### D. Section Draft

每节必须带：

- content_md
- citation_refs
- assumptions
- open_questions
- used_global_params

## 9.2 Outline 生成规则

输入：

- requirement_card
- template skeleton
- evidence bundle
- section policy

约束：

- 必填章节不可删除
- 不相关章节不可乱加
- 每节必须声明“为什么存在”
- 若某节需要图表或人工确认，应在大纲层就标记

## 9.3 Section 生成规则

生成时只给模型最小必要上下文：

- 当前 section spec
- 当前 requirement card
- 与本节相关的证据子集
- 全局参数表
- 规则与禁用承诺表

禁止做法：

- 把所有文档/所有检索结果一次性塞给模型
- 为了“文采”牺牲事实可追溯性
- 缺参时臆造具体数值

## 9.4 全局参数注册表

系统必须维护一个 **Global Parameter Registry**，在需求抽取、检索和各章节生成之间共享。  
至少包含：

- 参数名
- 当前值
- 单位
- 来源
- 置信度
- 是否已人工确认

章节生成和校验都必须引用同一快照，避免 A 章节写 5000kW，B 章节写 5200kW。

## 9.5 假设声明

若允许在缺参情况下继续草拟，必须显式输出：

- `assumption`
- `impact`
- `needs_confirmation`

假设不能藏在正文里。

## 9.6 模型选择策略

按任务分工，而不是单模型包打天下：

- 结构化抽取：优先稳定、受 schema 约束的模型
- 章节起草：优先长文本组织能力强的模型
- 视觉理解：只在确有图表需要时调用
- 语义校验：可用较小模型或规则优先

---

## 10. 校验设计（Validation Engine）

## 10.1 校验分层

### Layer 1：确定性程序校验（必须）

适用于：

- 必填字段缺失
- 必填章节缺失
- 参数单位冲突
- 参数值冲突
- 引用缺失/失效
- 未消解占位符
- 禁用承诺词
- 图表引用未确认
- 导出模板变量未替换

### Layer 2：模型辅助语义校验（可选但推荐）

适用于：

- 某段是否偏离章节意图
- 某技术结论是否与需求矛盾
- 某段是否存在未声明假设
- 某章节是否重复/冗余

### Layer 3：人工审核（强制）

以下事项必须由人最终判定：

- 技术路线是否对
- 图纸是否能对外使用
- 参数是否能承诺
- 是否达到可发送门槛

## 10.2 错误码规范（建议）

| 错误码 | 含义 | 阻塞级别 |
|---|---|---|
| `VAL001` | Requirement Card 缺少 P0 字段 | P0 |
| `VAL002` | 必填章节缺失 | P0 |
| `VAL003` | 同一参数多值冲突 | P0 |
| `VAL004` | 技术性段落无引用 | P0 |
| `VAL005` | 引用目标不存在或失效 | P0 |
| `VAL006` | 存在未确认复杂图表引用 | P0 |
| `VAL007` | 存在未消解占位符 | P0 |
| `VAL101` | 章节可能偏离目标 | P1 |
| `VAL102` | 可能存在未声明假设 | P1 |
| `VAL103` | 检索证据相关性偏低 | P1 |
| `VAL201` | 表达可优化 | P2 |

## 10.3 校验输出格式

```json
{
  "project_id": "proj_001",
  "draft_version": 3,
  "status": "blocked",
  "errors": [
    {
      "code": "VAL003",
      "level": "P0",
      "section_id": "3.2",
      "message": "总功率存在 5000kW / 5200kW 两种值",
      "suggested_action": "确认需求卡中的功率并回写全局参数表"
    }
  ],
  "warnings": [],
  "review_tasks_created": ["task_001", "task_002"]
}
```

---

## 11. API 设计

以下接口足够支撑 MVP。同步接口用于轻量操作，耗时任务统一异步化。

## 11.1 项目与输入

| Method | Path | 用途 | 同步/异步 |
|---|---|---|---|
| POST | `/api/projects` | 创建项目 | 同步 |
| GET | `/api/projects/{id}` | 获取项目详情 | 同步 |
| POST | `/api/projects/{id}/inputs/text` | 添加文本输入 | 同步 |
| POST | `/api/projects/{id}/documents` | 上传附件 | 同步 |
| GET | `/api/projects/{id}/documents` | 查询附件 | 同步 |

### 示例：创建项目

```http
POST /api/projects
Content-Type: application/json
```

```json
{
  "name": "华东某客户高压变频方案",
  "product_line": "high_voltage_vfd",
  "owner_user_id": "user_001"
}
```

## 11.2 需求抽取与澄清

| Method | Path | 用途 | 同步/异步 |
|---|---|---|---|
| POST | `/api/projects/{id}/extract-requirement` | 触发需求抽取 | 异步 |
| GET | `/api/projects/{id}/requirement-card/latest` | 获取最新需求卡 | 同步 |
| PATCH | `/api/projects/{id}/requirement-card/{cardId}` | 人工修改需求卡 | 同步 |
| POST | `/api/projects/{id}/clarifications/{itemId}/resolve` | 回答澄清项 | 同步 |

### 示例：需求抽取返回

```json
{
  "job_id": "job_ext_001",
  "status": "queued",
  "next_poll": "/api/jobs/job_ext_001"
}
```

## 11.3 检索与证据包

| Method | Path | 用途 | 同步/异步 |
|---|---|---|---|
| POST | `/api/projects/{id}/retrieve-evidence` | 触发检索 | 异步 |
| GET | `/api/projects/{id}/evidence-bundles/latest` | 获取最新证据包 | 同步 |
| POST | `/api/projects/{id}/evidence-bundles/{bundleId}/pin` | 固定某批证据供后续生成 | 同步 |

## 11.4 大纲与章节生成

| Method | Path | 用途 | 同步/异步 |
|---|---|---|---|
| POST | `/api/projects/{id}/generate-outline` | 生成大纲 | 异步 |
| GET | `/api/projects/{id}/outlines/latest` | 获取最新大纲 | 同步 |
| PATCH | `/api/projects/{id}/outlines/{outlineId}` | 调整大纲 | 同步 |
| POST | `/api/projects/{id}/generate-sections` | 批量生成章节 | 异步 |
| POST | `/api/projects/{id}/sections/{sectionId}/regenerate` | 重生成单节 | 异步 |
| PATCH | `/api/projects/{id}/sections/{sectionId}` | 人工编辑单节 | 同步 |

### 示例：生成大纲请求

```json
{
  "requirement_card_id": "req_003",
  "evidence_bundle_id": "evb_002",
  "template_id": "tpl_default_v1"
}
```

### 示例：大纲返回体（节选）

```json
{
  "outline_id": "out_001",
  "version": 1,
  "sections": [
    {
      "section_id": "1",
      "title": "项目背景与需求理解",
      "purpose": "总结客户现状、问题与目标",
      "mandatory": true,
      "needs_human_review": false,
      "expected_evidence_types": ["requirement", "case_summary"]
    },
    {
      "section_id": "3",
      "title": "技术方案设计",
      "purpose": "给出控制策略、系统构成与参数说明",
      "mandatory": true,
      "needs_human_review": true,
      "expected_evidence_types": ["section", "parameter", "figure"]
    }
  ]
}
```

## 11.5 校验、审核与导出

| Method | Path | 用途 | 同步/异步 |
|---|---|---|---|
| POST | `/api/projects/{id}/validate` | 触发全量校验 | 异步 |
| GET | `/api/projects/{id}/validation/latest` | 获取最新校验报告 | 同步 |
| GET | `/api/projects/{id}/review-tasks` | 查看待办 | 同步 |
| POST | `/api/projects/{id}/review-tasks/{taskId}/resolve` | 完成待办 | 同步 |
| POST | `/api/projects/{id}/export` | 导出文档 | 异步 |
| GET | `/api/projects/{id}/exports/latest` | 获取导出结果 | 同步 |

## 11.6 作业查询

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/jobs/{jobId}` | 查询异步任务状态与错误信息 |

---

## 12. 异步任务与队列设计

## 12.1 队列划分

建议最少 5 类队列：

- `parse_queue`
- `index_queue`
- `generation_queue`
- `validation_queue`
- `export_queue`

## 12.2 任务设计原则

- 幂等：同一输入重复提交不应生成重复对象
- 可重试：临时失败可自动重试
- 可取消：草稿重生时，旧任务可取消
- 可追踪：任务必须带 `job_id`, `trace_id`, `project_id`

## 12.3 重试策略

建议：

- 临时网络/模型超时：最多重试 2 次
- 解析失败：允许人工标记并重新上传/重跑
- 规则校验失败：不自动重试，直接生成 review task
- 队列积压：前端显示“处理中”，并允许离线轮询

## 12.4 超时建议

| 任务 | 超时建议 |
|---|---|
| 需求抽取 | 60s |
| 检索 | 30s |
| 大纲生成 | 90s |
| 单章节生成 | 120s |
| 全量校验 | 60s |
| 导出 | 60s |

---

## 13. 前端与交互要求

## 13.1 页面结构

一期至少需要 5 个页面/工作区：

1. 项目列表
2. 项目工作台
3. Requirement Card 编辑页
4. 草稿编辑页（Block Editor）
5. 审核/待办页

## 13.2 项目工作台

必须展示：

- 当前状态
- 最近一次输入
- 最新需求卡状态
- 最新证据包质量
- 大纲版本
- 草稿版本
- 阻塞项计数
- 最近导出记录

## 13.3 Block Editor

每个章节块必须支持：

- 查看本节引用的证据
- 查看本节使用的全局参数
- 局部重写
- 标记为“需技术确认”
- 查看 diff
- 接受/驳回 AI 建议
- 人工直接编辑并留痕

## 13.4 Evidence Side Panel

必须能按以下维度查看证据：

- 案例
- 章节
- 参数表
- 图表
- 模板

并支持“固定为本项目参考证据”。

## 13.5 Review Tasks 面板

至少支持：

- 按 P0/P1/P2 过滤
- 指派处理人
- 写处理意见
- 解决后回写状态机

---

## 14. 安全、权限与部署

## 14.1 权限模型

建议最少角色：

- `viewer`
- `editor`
- `reviewer`
- `admin`

权限至少区分：

- 查看原始文档
- 编辑需求卡
- 触发生成
- 处理待办
- 导出
- 管理模板/知识库

## 14.2 数据分级

建议按项目或文档标注：

- `public`
- `internal`
- `sensitive`
- `restricted`

规则示例：

- `restricted` 项目不得默认调用外部模型
- `sensitive` 项目导出与访问需全量审计
- `public/internal` 可根据策略使用外部模型

## 14.3 外部模型使用边界

必须明确：

- 哪些项目允许出域
- 哪些字段必须脱敏
- 哪些模型可用于哪些任务
- 模型调用日志保存多久
- 供应商条款和审批是否已落实

工程上不要把“零数据留存”写死成默认事实；它必须来自具体产品配置与合同确认。

## 14.4 推荐部署拓扑（MVP）

```text
企业控制环境内：
- Web App
- API Gateway
- Workflow Service
- PostgreSQL / pgvector
- Redis
- Object Storage
- Audit / Logs

出域边界（可选）：
- LLM Adapter -> 外部模型服务
```

## 14.5 密钥与机密管理

- 所有模型密钥放 Secret Manager / 环境密钥系统
- 前端绝不直接持有供应商 API Key
- 导出文件采用带过期时间的受控下载链接
- 日志默认打码，不打印原始长文本和完整 prompt

---

## 15. 可观测性、运维与成本治理

## 15.1 需要的监控指标

### 系统层

- 接口 QPS
- 错误率
- 平均/分位耗时
- 队列积压
- 任务失败率

### 业务层

- 需求卡生成成功率
- 检索 bundle 低分率
- 大纲生成成功率
- 节点重生成率
- 导出成功率
- Review Task 类型分布

### 质量层

- P0 阻塞项数量
- 参数冲突数量
- 技术章节无引用数量
- 人工修改率

### 成本层

- 每项目模型调用次数
- 每项目 token / 请求成本
- 解析成本
- 缓存命中率

## 15.2 日志与追踪

每次关键操作都应记录：

- request_id / trace_id
- project_id
- user_id
- model_id / prompt_version / ruleset_version
- input_ref / output_ref
- duration_ms
- error_code

## 15.3 Prompt 与模型版本治理

必须可回放：

- 某次草稿用的是哪版 prompt
- 用的是哪版 schema
- 用的是哪个模型
- 该版结果通过了哪些校验

不允许“今天模型默默升级，结果风格变了，但没有记录”。

---

## 16. 测试与验收工程

## 16.1 测试分层

### 单元测试

覆盖：

- 文档清洗规则
- 切块函数
- 参数归一逻辑
- 校验规则
- API 参数校验

### 集成测试

覆盖：

- 文档上传到入库
- 需求抽取到写库
- 检索到 bundle 组装
- 大纲生成到章节生成
- 校验到 review task 创建
- 导出链路

### 端到端测试

准备 5–10 个固定样例，从输入到导出跑完整流程。

### 离线评测

使用标注好的历史案例，评测：

- 需求抽取
- 检索命中
- 章节相关性
- 引用覆盖率
- 参数一致性

### 用户验收测试（UAT）

由真实售前和技术负责人在试点项目中打分。

## 16.2 推荐评测集

- 20–50 个真实需求样本
- 10–20 个需求-方案配对样本
- 10 个故意注入冲突/缺字段的对抗样本
- 10 个复杂图纸引用样本

## 16.3 验收门槛（建议）

| 类别 | 指标 | 初始门槛 |
|---|---|---|
| 需求抽取 | 必填字段完整率 | ≥85% |
| 检索 | Top-5 可参考命中率 | ≥70% |
| 生成 | 技术章节引用覆盖率 | ≥95% |
| 校验 | 已覆盖规则类型漏检率 | 0 |
| 人效 | 首版草稿时间下降 | ≥40% |
| 稳定性 | 关键异步任务成功率 | ≥95% |

## 16.4 不上线条件

若出现以下任一情形，不应直接投产：

- 数据准备不足，无法构建稳定知识库
- 技术章节大量无引用
- 关键参数冲突频发
- 试点用户主观评价非常差
- 阻塞错误漏检
- 审计与权限未就绪

---

## 17. 实施路线图（8 周建议版）

> 以 1 名后端 / 1 名 AI 工程 / 0.5 名前端 / 1 名兼职产品与业务专家为参考。

## Week 1：业务收敛与数据盘点

交付：

- 一期场景确认
- Requirement Card schema v1
- 文档分类规则
- 试点模板确认
- 数据清单与不可用资料清单

## Week 2：文档入库与清洗

交付：

- 上传接口
- 对象存储接入
- 解析任务框架
- 清洗与去重规则
- parsed_blocks / figure_assets 入库

## Week 3：索引与检索底座

交付：

- knowledge_chunks 生成
- embedding 与索引
- metadata filter
- 基础混合检索 API
- evidence bundle v1

## Week 4：需求卡与澄清机制

交付：

- extract-requirement 异步任务
- requirement_card 编辑页
- clarification_items
- Global Parameter Registry v1

## Week 5：大纲生成与章节生成

交付：

- outline schema
- generate-outline
- generate-sections
- section_drafts 表与 block editor 初版

## Week 6：校验与审核流

交付：

- Validation Service v1
- review_tasks
- 阻塞条件控制
- 参数冲突与引用校验规则

## Week 7：导出、监控、回归测试

交付：

- export service
- 审计日志
- 核心监控指标
- e2e 测试与离线评测脚本

## Week 8：试点与验收

交付：

- 试点项目运行
- 缺陷清单
- 验收报告
- 二期 backlog

---

## 18. 资源配置建议

## 18.1 角色配置

- 后端工程师：接口、状态机、数据模型、任务队列、导出
- AI 应用工程师：解析、检索、prompt/schema、评测
- 前端工程师：工作台、Requirement Card、Block Editor、Review Tasks
- 产品/业务专家：定义字段、模板、验收规则
- IT/安全：部署、访问控制、模型边界审批

## 18.2 技术债优先级

一期可接受的技术债：

- 管理后台先简化
- 图表深度解析先保守
- Cross-encoder 重排可后置
- DOCX 导出可先于复杂版式

一期不应欠下的技术债：

- 没有审计
- 没有版本化
- 没有阻塞条件
- 没有评测集
- 没有人工审核闭环

---

## 19. 风险、开放问题与止损条件

## 19.1 核心风险

| 风险 | 表现 | 缓解 |
|---|---|---|
| 数据质量差 | 解析后大量噪声，模板混乱 | 先缩到单模板、单产品线 |
| 无需求-方案映射 | 系统只会“找相似文案” | 强制收集最小映射样本 |
| 用户预期过高 | 希望系统直接出定稿 | 立项时写清“副驾”边界 |
| 图表误用 | 复杂图被错误复用 | complex figure 一律 review required |
| 模型不稳定 | 风格飘、成本飘 | 统一 adapter + 版本锁定 + 缓存 |
| 无验收标准 | 好坏全靠感觉 | 先建评测集再上线 |

## 19.2 开放问题（上线前必须回答）

1. 一期到底服务哪一个产品线、哪类方案模板？
2. Requirement Card 的 P0 字段由谁拍板？
3. 哪些图表允许复用，哪些只能引用不可改？
4. 若检索低置信度，允许继续草拟还是必须补数？
5. 是否允许外部模型参与生产链路？边界是什么？
6. 最终导出的文档在哪个审批流程里继续流转？

## 19.3 止损条件

如果在 **Week 3 结束** 仍出现以下情况，应暂停扩 scope，先缩范围：

- 同一产品线内都拿不到 10 份可解析、可复用的高质量方案
- 业务方无法定义 P0 字段
- 模板本身频繁变化，没有稳定结构
- 没有业务验收人可持续参与

---

## 20. 建议的最小技术栈（供落地时参考）

> 目标是“够用、简单、可维护”，不是炫技。

- Backend: **Python + FastAPI**
- DB: **PostgreSQL**
- Vector: **pgvector**（先合并在 PG 内）
- Cache / Queue: **Redis**
- Object Storage: **MinIO / OSS / S3 兼容**
- Workers: **Celery / RQ**
- Parser: **独立解析服务（可接 Docling 等）**
- LLM Gateway: **统一适配层**
- Frontend: **React / Next.js + 块编辑器**
- Observability: **OpenTelemetry + 日志聚合**

当且仅当以下条件出现时，再考虑拆复杂组件：

- 文档规模明显增长
- 检索延迟成为瓶颈
- 多产品线隔离需求显著上升
- pgvector 在召回/性能上无法满足要求

---

## 附录 A：Requirement Card Schema（示例）

```json
{
  "schema_version": "v1",
  "project_id": "proj_001",
  "product_line": "high_voltage_vfd",
  "customer_industry": "steel",
  "application_scenario": "blower_startup_control",
  "business_goal": [
    "降低启动冲击",
    "提升能效",
    "满足现场联锁要求"
  ],
  "known_parameters": {
    "power_kw": {
      "value": 5000,
      "unit": "kW",
      "source": "user_input",
      "confidence": 0.93,
      "confirmed": false
    },
    "voltage_kv": {
      "value": 6,
      "unit": "kV",
      "source": "rfp_page_3",
      "confidence": 0.88,
      "confirmed": false
    }
  },
  "constraints": {
    "site_constraints": ["现场空间有限"],
    "integration_requirements": ["需要接入现有 PLC"],
    "compliance_requirements": []
  },
  "missing_items": [
    {
      "field_name": "ambient_temperature",
      "priority": "P1",
      "blocking": false,
      "reason": "影响设备选型和保护策略",
      "question": "请确认现场环境温度范围"
    }
  ],
  "blocking_items": [],
  "source_refs": [
    {"doc_id": "doc_001", "page": 3, "block_id": "blk_023"}
  ]
}
```

## 附录 B：Outline Schema（示例）

```json
{
  "outline_id": "out_001",
  "version": 1,
  "sections": [
    {
      "section_id": "1",
      "title": "项目背景与需求理解",
      "purpose": "说明客户场景、目标与约束",
      "mandatory": true,
      "needs_human_review": false,
      "expected_evidence_types": ["requirement", "case_summary"],
      "children": []
    },
    {
      "section_id": "2",
      "title": "方案总体设计",
      "purpose": "说明系统构成与技术路线",
      "mandatory": true,
      "needs_human_review": true,
      "expected_evidence_types": ["section", "figure", "parameter"],
      "children": [
        {
          "section_id": "2.1",
          "title": "系统架构",
          "purpose": "说明模块组成及接口关系",
          "mandatory": true,
          "needs_human_review": true,
          "expected_evidence_types": ["section", "figure"],
          "children": []
        }
      ]
    }
  ]
}
```

## 附录 C：Section Draft Schema（示例）

```json
{
  "section_id": "2.1",
  "title": "系统架构",
  "content_md": "### 系统架构\n\n本方案采用……",
  "citation_refs": [
    {
      "evidence_id": "ev_011",
      "source_doc_id": "doc_case_09",
      "page_range": [8, 10]
    }
  ],
  "assumptions": [
    {
      "text": "默认客户现场 PLC 支持标准 Modbus TCP 接口",
      "impact": "若不支持，接口适配方案需调整",
      "needs_confirmation": true
    }
  ],
  "used_global_params": [
    {"name": "power_kw", "value": 5000, "unit": "kW"}
  ]
}
```

## 附录 D：MVP 上线前检查清单

- [ ] 试点范围已收敛到单产品线
- [ ] Requirement Card schema 已由业务负责人签字确认
- [ ] 有至少 20 份同质历史方案可用
- [ ] 有至少 10 份需求-方案映射样本
- [ ] 评测集已准备
- [ ] 审计日志已接通
- [ ] 关键校验规则已覆盖
- [ ] 复杂图表策略已明确
- [ ] 外部模型边界已获审批
- [ ] 导出流程已接入业务审批链路

---

## 21. 结论

一个能落地的售前方案助手，一期的成败不取决于“模型有多聪明”，而取决于以下四件事是否做实：

1. **需求是否被结构化，而不是停留在聊天输入层；**
2. **知识是否被建成可检索、可追溯、可过滤的资产；**
3. **长文档是否被拆成可验证的中间步骤；**
4. **人审与校验是否被硬编码进流程，而不是事后补救。**

只要团队守住这四条，一期 MVP 就有现实落地机会；反之，即使模型再强，也很容易变成一个“能写很多字、但不能稳定交付”的演示系统。
