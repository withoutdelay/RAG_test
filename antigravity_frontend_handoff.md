# Antigravity Frontend Handoff

本文档是给 antigravity 的前端对接清单。

目标很明确：

- 只实现前端
- 不修改后端契约
- 不修改数据库
- 不扩展网关
- 以前端可用、好看、易演示为第一目标

## 1. 硬约束

antigravity 这次 **只允许完成前端部分**。

允许修改的范围：

- `frontend/**`
- 若前端启动方式必须调整，可修改 `frontend/Dockerfile`

禁止修改的范围：

- `backend/**`
- `gateway/**`
- `backend/alembic/**`
- `docker-compose.yml`
- 根目录 API / spec / migration 文档

禁止做的事情：

- 不新增后端接口
- 不修改现有后端返回结构
- 不修改数据库 schema
- 不把 legacy `/api/v1/generation/*` 做成新主路径
- 不为了 UI 方便去改 Python 代码

如果发现前端实现被后端契约阻塞：

- 记录阻塞点
- 在交付说明里列出
- 不要自行修改后端

## 2. 当前项目状态

当前前端基本还是空白，`frontend/` 里只有一个占位 Dockerfile。

后端 V2 主链路已经可用，主路径如下：

`Project -> Documents -> Requirement Card -> Evidence Bundle -> Outline -> Section Drafts -> Validation -> Review Tasks -> Export`

前端应该围绕这条 V2 主线实现。

不要围绕这些 legacy 接口设计主页面：

- `/api/v1/generation/*`
- `/api/v1/review/*`

## 3. 前端实现目标

交付一个可演示的 Web UI，让用户可以跑通以下流程：

1. 创建项目
2. 上传资料
3. 查看和编辑 Requirement Card
4. 触发证据检索并查看 Evidence Bundle
5. 生成和编辑 Proposal Outline
6. 生成、查看、编辑 Section Drafts
7. 触发 Validation 并处理 Review Tasks
8. 导出 Markdown 结果

重点是：

- UI 质量高
- 页面流转清晰
- 能稳定对接现有 API
- 不依赖后端再改一轮

## 4. 推荐信息架构

建议以项目工作台为核心。

推荐路由：

- `/projects`
- `/projects/new`
- `/projects/:projectId`

推荐在项目详情页使用 6 个主区域：

- `Overview`
- `Documents`
- `Requirement`
- `Evidence`
- `Outline & Drafts`
- `Validation & Export`

如果 antigravity 想做更完整体验，可以把项目详情做成左侧步骤导航：

- 项目概览
- 资料上传
- 需求卡
- 证据包
- 大纲
- 章节草稿
- 校验与审核
- 导出结果

## 5. API 基础约定

后端基础前缀：

- `/api/v1`

健康检查：

- `GET /health`

所有 API 都使用统一包裹结构：

```json
{
  "code": 200,
  "message": "success",
  "data": {}
}
```

前端应统一解包 `data`。

## 6. V2 必接接口

### 6.1 Projects

- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `PUT /api/v1/projects/{project_id}`
- `DELETE /api/v1/projects/{project_id}`

重要字段：

- `id`
- `name`
- `product_line`
- `industry`
- `description`
- `status`
- `current_requirement_card_id`
- `current_outline_id`
- `current_draft_version`

项目状态枚举：

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

### 6.2 Documents

- `POST /api/v1/projects/{project_id}/documents/upload`
- `GET /api/v1/projects/{project_id}/documents`
- `GET /api/v1/documents/{document_id}`
- `GET /api/v1/documents/{document_id}/chunks`
- `POST /api/v1/documents/{document_id}/reparse`
- `DELETE /api/v1/documents/{document_id}`

上传说明：

- `multipart/form-data`
- 字段：
  - `file`
  - `doc_type`
  - `metadata`

建议前端支持：

- 上传进度态
- parse 成功/失败态
- 已上传文档列表
- chunk 预览抽屉或面板

### 6.3 Requirement Card

- `POST /api/v1/projects/{project_id}/extract-requirement`
- `GET /api/v1/projects/{project_id}/requirement-card/latest`
- `PATCH /api/v1/projects/{project_id}/requirement-card/{card_id}`
- `POST /api/v1/projects/{project_id}/clarifications/{item_id}/resolve`

Requirement Card 关键字段：

- `content`
- `missing_items`
- `blocking_items`
- `source_refs`
- `confirmed_by_user`

UI 要点：

- `content` 不要只做 JSON 文本框，尽量做成结构化表单
- `missing_items` / `blocking_items` 要可视化
- `P0` 阻塞项要高亮
- 用户解决 clarification 后，页面应立即刷新 latest card

### 6.4 Evidence Bundle

- `POST /api/v1/projects/{project_id}/retrieve-evidence`
- `GET /api/v1/projects/{project_id}/evidence-bundles/latest`

UI 要点：

- 展示 query、quality_score、results
- 每条 evidence 至少展示：
  - `evidence_id`
  - `source_title`
  - `heading_path`
  - `summary`
  - `relevance_score`

### 6.5 Outline

- `POST /api/v1/projects/{project_id}/generate-outline`
- `GET /api/v1/projects/{project_id}/outlines/latest`
- `PATCH /api/v1/projects/{project_id}/outlines/{outline_id}`

Outline 数据结构核心字段：

- `title`
- `sections[]`

每个 section 节点字段：

- `section_id`
- `title`
- `purpose`
- `mandatory`
- `expected_evidence_types`
- `needs_human_review`
- `children`

UI 要点：

- 必须支持树形大纲展示
- 最好支持树形编辑
- `mandatory` 和 `needs_human_review` 要有清晰标签

### 6.6 Section Drafts

- `POST /api/v1/projects/{project_id}/generate-sections`
- `GET /api/v1/projects/{project_id}/sections`
- `POST /api/v1/projects/{project_id}/sections/{section_id}/regenerate`
- `PATCH /api/v1/projects/{project_id}/sections/{section_id}`

`GET /sections` 支持可选查询参数：

- `draft_version`

Section Draft 关键字段：

- `section_id`
- `title`
- `content_md`
- `citation_refs`
- `assumptions`
- `global_param_snapshot`
- `status`
- `validator_result`

UI 要点：

- 页面刷新后必须通过 `GET /sections` 恢复当前章节列表
- 建议左侧章节树，右侧 Markdown 编辑区
- 引用和 assumptions 最好单独做边栏或卡片区
- `regenerate` 动作用按钮触发
- 手工编辑后保存走 `PATCH`

### 6.7 Validation / Review Tasks

- `POST /api/v1/projects/{project_id}/validate`
- `GET /api/v1/projects/{project_id}/validation/latest`
- `GET /api/v1/projects/{project_id}/review-tasks`
- `POST /api/v1/projects/{project_id}/review-tasks/{task_id}/resolve`

Validation Report 关键字段：

- `status`
- `errors`
- `warnings`
- `review_tasks_created`

Review Task 关键字段：

- `task_type`
- `blocking_level`
- `payload`
- `status`

当前 review task 主要类型：

- `param_conflict`
- `figure_confirm`
- `content_review`
- `final_review`

UI 要点：

- P0 和 P1 明确区分
- review task 要能逐条处理
- `param_conflict` 需要输入 resolution value
- 处理完成后要重新拉取 validation 和 project detail

### 6.8 Export

- `POST /api/v1/projects/{project_id}/export`
- `GET /api/v1/projects/{project_id}/exports/latest`

当前只支持：

- `format = "markdown"`

Export 关键字段：

- `file_name`
- `file_type`
- `content_md`
- `snapshot`
- `status`

UI 要点：

- 支持查看导出的 Markdown
- 支持复制内容
- 如果要做下载，可基于 `content_md` 前端生成下载文件

### 6.9 Job Polling

- `GET /api/v1/jobs/{job_id}`

说明：

- 当前后端 job 记录已经存在
- 但多数任务实际上是“同步完成后立即返回 succeeded”
- 所以前端可以保留轮询层，但不要依赖复杂长轮询体验

## 7. 建议页面清单

### 7.1 项目列表页

应包含：

- 项目列表
- 项目状态
- 创建项目入口
- 最近更新时间

### 7.2 项目创建页 / 弹窗

字段：

- `name`
- `product_line`
- `industry`
- `description`

### 7.3 项目工作台

建议顶部展示：

- 项目名称
- 产品线
- 当前状态
- 当前 draft version
- 核心 CTA

### 7.4 Documents 页面

应包含：

- 上传区
- 文档列表
- parse 状态
- chunks 预览

### 7.5 Requirement 页面

应包含：

- Requirement Card 表单视图
- missing / blocking 列表
- clarification resolve 区

### 7.6 Evidence 页面

应包含：

- evidence bundle 摘要
- quality score
- evidence cards 列表

### 7.7 Outline & Drafts 页面

应包含：

- 大纲树
- 章节切换
- 当前章节编辑器
- citations / assumptions / param snapshot
- regenerate / save 按钮

### 7.8 Validation & Export 页面

应包含：

- validation report
- errors / warnings
- review task 列表和处理入口
- export 按钮
- export markdown 预览

## 8. 视觉与交互要求

antigravity 的主要价值在 UI 质量，所以这里建议：

- 不要做成普通后台 CRUD 风格
- 不要只做表格堆砌
- 要强调“售前工作台”和“工件流转”
- 状态流、阻塞项、引用关系要有明确视觉层次

推荐视觉方向：

- 信息密度中高，但层次清晰
- 用卡片、分栏、时间线、状态标签表达流程
- 编辑器区域做得像专业工作台，而不是表单页

但要注意：

- 审核与阻塞状态必须一眼可见
- 不要为了视觉牺牲可读性
- 不要发明后端没有的数据

## 9. 技术交付约束

antigravity 可以自由选择前端技术方案，但必须满足：

- 所有实现都留在 `frontend/`
- 能配置后端 base URL
- 能本地跑起来
- 不要求后端额外改接口

推荐但不强制：

- TypeScript
- React
- 明确的 API client 封装
- Markdown 编辑/预览双栏

## 10. 明确不要做的内容

这次不要做：

- 后端接口改造
- OpenAPI 生成器改后端代码
- 新增数据库迁移
- GraphRAG UI
- legacy generation 页面深度建设
- 登录鉴权体系重构
- 多用户协作
- 队列监控后台

## 11. 联调优先级

建议联调顺序：

1. `Projects`
2. `Documents`
3. `Requirement`
4. `Evidence`
5. `Outline`
6. `Section Drafts`
7. `Validation / Review`
8. `Export`

## 12. 最终交付标准

antigravity 的交付应满足：

- 可以从 UI 创建项目并上传文档
- 可以完整跑通 V2 主链路
- 可以查看和编辑大纲、章节草稿
- 可以处理 review tasks
- 可以看到导出结果
- 全程不修改后端代码

如果有后端不足之处：

- 单独列成 `frontend_blockers` 或 `handoff_notes`
- 不要直接改后端

