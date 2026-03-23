# 售前智能系统 V1 -> V2 迁移方案

> 目标：在保留现有解析、检索、脱敏和 LLM 底座的前提下，将系统从 V1 的“生成任务中心”迁移到 V2 的“结构化工件中心”。  
> 基线分支：`codex/baseline-v1`  
> V2 目标文档：`spec_v2.md`、`项目开发计划_v2.md`

---

## 1. 迁移结论

本项目不建议“另起一套新系统”，也不建议“继续沿 V1 主线把代码堆完再回头重做”。

推荐策略是：

- 代码单轨迁移
- 数据模型加法迁移
- API 分阶段切换
- 旧主链路冻结为只读参考

一句话总结：

`保留底座，重构主线。`

---

## 2. 当前基线快照

当前仓库已完成 Git 冻结：

- 基线分支：`codex/baseline-v1`
- 基线提交：`4741c15`
- V2 文档提交：`11d9afd`

当前已存在并可复用的能力：

- 项目 CRUD
- 文档上传、解析、切块、向量入库
- 基础混合检索接口
- 脱敏网关
- 统一 LLM Client 与 provider 路由
- 大纲/章节生成与 review 点

当前主线的核心特征：

- 以 `generation_tasks` 为中心保存大纲、章节、终稿
- 以 `review_points` 表示审批断点
- API 以 `/generation/*` 为主
- 状态机围绕 `outlining / generating / section_review / finalizing`

这条主线可以验证技术底座，但不适合作为 V2 的长期架构。

---

## 3. 迁移原则

### 3.1 先加法，后替换

先新增 V2 工件表、服务和 API，再逐步把写流量切到新主线。不要一开始就直接删掉 V1 表和接口。

### 3.2 保持底层能力复用

以下能力默认不推倒重做：

- `backend/app/services/parsing/*`
- `backend/app/services/vectorstore/*`
- `backend/app/services/llm/*`
- `backend/app/services/gateway_client.py`
- `gateway/*`

### 3.3 以工件为边界重构

V2 的核心不是“换一个 prompt”，而是把长流程拆成一组可版本化工件：

- Requirement Card
- Evidence Bundle
- Proposal Outline
- Section Draft
- Validation Report
- Review Task

### 3.4 优先打通可追溯链路

任何迁移步骤都要优先保证：

- 数据来源可追溯
- 参数快照一致
- 引用关系可校验
- 导出可绑定明确版本

---

## 4. V1 与 V2 的对象映射

| V1 当前对象 | 现状 | V2 目标对象 | 迁移策略 |
|---|---|---|---|
| `projects` | 仅保存基础信息 | `projects` | 扩字段，不替换 |
| `documents` | 兼具项目输入和知识文档 | `raw_documents` | 新增表，旧表保留过渡 |
| `chunks` | 同时承担解析块和检索块角色 | `parsed_blocks` + `knowledge_chunks` | 拆表迁移 |
| `generation_tasks` | 集中保存 outline / sections / final_markdown | `requirement_cards`、`evidence_bundles`、`proposal_outlines`、`section_drafts`、`jobs` | 停止扩展，逐步退役 |
| `review_points` | 仅绑定生成任务章节 | `review_tasks` | 泛化替换 |
| `masking_audit_logs` | 只覆盖脱敏链路 | `audit_logs` | 保留并扩展 |

---

## 5. 数据库迁移方案

## 5.1 总体策略

数据库采用三步走：

1. 新增 V2 表与字段
2. 回填历史数据
3. 切换写入入口

在 V2 主链路未稳定前，不删除 V1 表。

## 5.2 `projects` 迁移

### 当前

- `name`
- `industry`
- `description`

### 目标

新增字段：

- `owner_user_id`
- `product_line`
- `status`
- `current_requirement_card_id`
- `current_outline_id`
- `current_draft_version`

### 迁移策略

- 通过新 Alembic revision 扩表
- 为现有项目补默认状态：`CREATED` 或 `INPUT_READY`
- `industry` 暂时保留，不急于删除

## 5.3 `documents` -> `raw_documents`

### 当前问题

当前 `documents` 同时承担：

- 项目输入附件
- 历史知识材料
- 解析状态跟踪

但 V2 需要更清晰的文档边界和版本属性。

### 目标

新增 `raw_documents` 表，至少包含：

- `project_id`
- `corpus_scope`
- `doc_type`
- `file_uri`
- `file_name`
- `checksum`
- `version_label`
- `parse_status`
- `confidentiality_level`

### 迁移策略

- 先新增 `raw_documents`
- 编写一次性 backfill，把 `documents` 同步进 `raw_documents`
- 新上传链路后续只写 `raw_documents`
- `documents` 在过渡期保留只读兼容

## 5.4 `chunks` -> `parsed_blocks` + `knowledge_chunks`

### 当前问题

当前 `chunks` 同时承担：

- 解析结果
- 检索单元
- Qdrant 载体

这会让“引用真实来源”和“检索派生文本”混在一起。

### 目标

新增：

- `parsed_blocks`
- `figure_assets`
- `knowledge_chunks`

### 迁移策略

- `parsed_blocks` 由解析器直接产出
- `knowledge_chunks` 由 chunker/indexer 从 `parsed_blocks` 派生
- 对已有 `chunks`：
  - 可回填为 `knowledge_chunks`
  - 若缺失块级结构信息，则不强行伪造 `parsed_blocks`
- V2 起引用系统必须优先引用 `parsed_blocks` 或其明确来源

## 5.5 `generation_tasks` 退役方案

### 当前问题

`generation_tasks` 里把以下内容塞在一起：

- outline
- global_params
- sections
- final_markdown

这不利于版本化、校验和多轮编辑。

### 目标替代

新增表：

- `requirement_cards`
- `evidence_bundles`
- `proposal_outlines`
- `section_drafts`
- `jobs`

### 迁移策略

- 不再给 `generation_tasks` 加新能力
- V2 新链路全部写新表
- 如需保留历史查看能力，可写一个只读转换层，把 V1 task 投影为 V2 视图
- 等 V2 稳定后，再评估是否下线 `generation_tasks`

## 5.6 `review_points` -> `review_tasks`

### 当前问题

`review_points` 仅支持“章节级审批断点”，表达能力不够。

### 目标

`review_tasks` 统一承载：

- clarification
- param_conflict
- figure_confirm
- content_review
- final_review

### 迁移策略

- 新建 `review_tasks`
- V2 校验和人工流程只写 `review_tasks`
- 若需要兼容旧页面，可把 `review_points` 映射为 `review_tasks` 的子集

## 5.7 审计体系

### 当前

- 只有 `masking_audit_logs`

### 目标

新增通用 `audit_logs`，记录：

- 查看
- 编辑
- 审核
- 导出
- 模型调用
- 证据使用

### 迁移策略

- 保留 `masking_audit_logs`
- 新增 `audit_logs`
- 不做表合并，避免把安全链路和业务链路一次性耦合

---

## 6. 服务层迁移方案

## 6.1 可直接保留的服务

以下模块优先保持不动，只做适配层接入：

- `backend/app/services/parsing/parser.py`
- `backend/app/services/parsing/docling_parser.py`
- `backend/app/services/parsing/table_parser.py`
- `backend/app/services/parsing/image_extractor.py`
- `backend/app/services/vectorstore/embedder.py`
- `backend/app/services/vectorstore/qdrant_client.py`
- `backend/app/services/llm/client.py`
- `backend/app/services/gateway_client.py`

## 6.2 需要改造的服务

### `Retriever`

当前只输出搜索结果列表。  
V2 应升级为：

- 支持 Requirement facets 输入
- 支持 metadata filter + hybrid recall + rerank
- 输出 Evidence Bundle
- 输出 bundle 质量分

### `WorkflowOrchestrator`

当前围绕：

- planner
- retriever
- executor
- holistic

V2 需要上移为项目级编排器，统一调度：

- Requirement Service
- Retrieval Service
- Generation Service
- Validation Service
- Export Service

### `GenerationService`

当前职责过大，需拆分：

- 保留一部分作为 `GenerationService`
- 抽出 `RequirementService`
- 抽出 `ValidationService`
- 抽出 `ExportService`
- 把状态推进收敛到 `WorkflowService`

## 6.3 新增服务建议

建议新增：

- `backend/app/services/requirement/service.py`
- `backend/app/services/retrieval/service.py`
- `backend/app/services/validation/service.py`
- `backend/app/services/export/service.py`
- `backend/app/services/workflow/service.py`

---

## 7. API 迁移方案

## 7.1 当前 API 分类

当前主 API：

- `/projects`
- `/documents`
- `/retrieval/search`
- `/generation/*`
- `/review/*`

## 7.2 V2 API 增量引入

优先新增以下 API，而不是先删旧接口：

- `POST /api/projects/{id}/extract-requirement`
- `GET /api/projects/{id}/requirement-card/latest`
- `PATCH /api/projects/{id}/requirement-card/{cardId}`
- `POST /api/projects/{id}/clarifications/{itemId}/resolve`
- `POST /api/projects/{id}/retrieve-evidence`
- `GET /api/projects/{id}/evidence-bundles/latest`
- `POST /api/projects/{id}/generate-outline`
- `GET /api/projects/{id}/outlines/latest`
- `POST /api/projects/{id}/generate-sections`
- `PATCH /api/projects/{id}/sections/{sectionId}`
- `POST /api/projects/{id}/validate`
- `GET /api/projects/{id}/validation/latest`
- `GET /api/projects/{id}/review-tasks`
- `POST /api/projects/{id}/review-tasks/{taskId}/resolve`
- `POST /api/projects/{id}/export`
- `GET /api/projects/{id}/exports/latest`
- `GET /api/jobs/{jobId}`

## 7.3 旧 API 处置

### 保留

- `/projects/*`
- `/documents/*`
- `/retrieval/search`

### 进入兼容模式

- `/generation/start`
- `/generation/{task_id}`
- `/generation/{task_id}/outline/confirm`
- `/generation/{task_id}/sections/{idx}/rewrite`
- `/generation/{task_id}/stream`
- `/review/*`

### 兼容策略

- 在 V2 UI 未切换前继续保留
- 标注为 legacy
- 不再承载新能力
- V2 试点稳定后再决定下线时间

---

## 8. 前端迁移方案

当前前端仍是占位状态，因此迁移成本相对可控。

V2 前端优先级应为：

1. Requirement Card 编辑页
2. Clarification 面板
3. Evidence Bundle 面板
4. Outline 页面
5. Section Block Editor
6. Validation Report / Review Tasks 面板
7. Export 页面

前端不要先围绕 legacy `/generation/*` 深度定制复杂交互，否则会再次加大返工。

---

## 9. 分阶段实施方案

## Phase A：Schema 加法迁移

目标：

- 新增 V2 表
- 扩展 `projects`
- 建立 `jobs`
- 建立 `audit_logs`

完成标准：

- Alembic 可顺利升级
- 不影响现有 V1 功能

## Phase B：Requirement / Evidence 主线落地

目标：

- Requirement Card
- Clarification
- Evidence Bundle

完成标准：

- 用户可以从项目输入产出 requirement card
- 未解决 P0 时无法进入后续主线
- 证据包可落库并版本化

## Phase C：Outline / Section Draft 迁移

目标：

- 大纲写入 `proposal_outlines`
- 章节写入 `section_drafts`
- 参数快照和引用关系落库

完成标准：

- 不再依赖 `generation_tasks.sections`
- 单节可重生成、可人工修改、可回溯

## Phase D：Validation / Review / Export 落地

目标：

- Validation Report
- Review Tasks
- 导出阻断
- Export 绑定版本快照

完成标准：

- P0 错误可准确阻断导出
- 导出文件可追溯到 requirement/evidence/outline/draft 版本

## Phase E：Legacy 收敛

目标：

- legacy generation/review API 标注只读或下线
- 停止写入 `generation_tasks`
- 停止写入 `review_points`

完成标准：

- 新 UI 和新后端主线全部走 V2 工件链路

---

## 10. 推荐落地顺序

建议按以下顺序开工：

1. Alembic：先扩 `projects`，再加 V2 核心表
2. Requirement Card schema 和 `RequirementService`
3. `RetrievalService` 输出 Evidence Bundle
4. `proposal_outlines` 和 `section_drafts`
5. `ValidationService`
6. `review_tasks`
7. `jobs` / 队列 / trace_id
8. 导出绑定版本快照

不建议的顺序：

- 先大改前端
- 先优化 prompt 细节
- 先继续扩 `generation_tasks`

---

## 11. 测试迁移方案

## 11.1 保留现有测试

以下测试可以继续保留，作为底座回归：

- parsing
- retrieval
- gateway
- llm client

## 11.2 新增测试类型

必须新增：

- requirement extraction service tests
- evidence bundle service tests
- validation engine tests
- review task lifecycle tests
- export snapshot tests
- project state machine tests
- Alembic migration tests

## 11.3 端到端回归

至少准备 5-10 个固定样例，覆盖：

- 输入到 requirement card
- requirement 到 evidence bundle
- evidence 到 outline
- outline 到 section drafts
- validation 到 review tasks
- export

---

## 12. 切换与回退策略

### 切换策略

- 先在 `main` 上完成 V2 文档与 schema
- 后续功能以 feature branch 增量合并
- 每个阶段都保证仓库可运行

### 回退策略

- 如 V2 某阶段不稳定，可随时回切 `codex/baseline-v1`
- 在 V2 完成前，legacy `/generation/*` 可继续作为技术兜底路径

### 分支建议

- `codex/v2-schema`
- `codex/v2-requirement`
- `codex/v2-evidence`
- `codex/v2-generation`
- `codex/v2-validation`
- `codex/v2-export`

---

## 13. 明确不做的事情

在 V2 主线完成前，不建议做以下事项：

- GraphRAG 主线化
- 复杂图纸自动重绘
- 多行业模板扩展
- 大规模前端美化
- 对 legacy generation 流继续做重投入

---

## 14. 首批可执行任务清单

第一批任务建议直接拆成：

1. 新增 Alembic migration：扩展 `projects` 并创建 V2 核心表
2. 新增 `RequirementService` 与 requirement card schema
3. 新增 `retrieve-evidence` API 与 Evidence Bundle 持久化
4. 设计 `proposal_outlines` / `section_drafts` ORM 与 schema
5. 新增 `ValidationService` 基础错误码与阻断规则
6. 新增 `review_tasks` API
7. 编写 V1 数据到 V2 的 backfill 脚本
8. 新增 migration / state machine / validation 测试

---

## 15. 最终目标

迁移完成后，系统主叙事应该从：

`启动生成任务 -> 等终稿`

变成：

`形成需求卡 -> 固化证据包 -> 生成可审草案 -> 校验阻断 -> 人工签核 -> 导出`

这才是 V2 架构真正要达成的工程形态。
