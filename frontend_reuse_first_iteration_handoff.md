# Frontend Reuse-First Iteration Handoff

本文档用于交接当前项目的前端迭代开发，适用于：

- `antigravity`
- `claudecode`

目标不是“重做前端”，而是在现有 V2 UI 基础上，把 `reuse-first` 路线真正可用地接起来。

---

## 1. 先说清楚产品流程

当前设计 **不是** 一次性自动生成最终方案。

正确流程必须固定为：

`Requirement Card -> Evidence Bundle -> Candidate Outline -> 用户确认/修改 -> Approved Outline -> 逐章节生成 -> 用户逐章节审核/修订 -> Validation -> Review Tasks -> Export`

这条流程里有两个必须保留的人为断点：

1. **大纲确认断点**
   - 系统先生成候选大纲
   - 用户必须先修改/确认大纲
   - 只有 `approved outline` 才允许进入章节生成

2. **章节审核断点**
   - 系统按已确认大纲逐章节生成
   - 每章都可能需要用户编辑、再生或确认
   - Validation 和 Review Tasks 只是辅助，不替代人工判断

前端必须把这两个断点做得很清楚，不能让用户误以为“点一次就会自动出最终稿”。

---

## 2. 本轮交接目标

本轮前端迭代只追求 5 个目标：

1. 打通 `Candidate Outline -> Approve Outline -> Generate Sections` 的主路径
2. 在大纲层展示并保留 `reuse-first` 路由元数据
3. 在章节页展示每章生成策略、复用信息和 `recommended_assets`
4. 在 Validation 页正确展示 `reuse-first` 风险与 Review Tasks
5. 把整条链路做成一个能稳定演示、能让用户理解“人机协同生成”的产品体验

---

## 3. 当前实现现状

### 3.1 后端已经具备

当前后端已经支持：

- Requirement Card / Evidence Bundle / Outline / Section Draft / Validation / Export 全链路 API
- 大纲审批接口
- 审批前禁止生成章节
- 按章节 `generation_mode` 生成内容
- 基于案例块生成 `reuse-first` 草稿
- 章节返回 `recommended_assets`
- Validation 检查参数冲突、占位符、客户痕迹、复用过近、参数替换不完整等问题

补充说明：

- 真实方案样板当前是通过离线 `sample manifest -> case library` 流程接入
- 前端本轮 **不需要** 处理真实样板建库流程
- 前端只需要正确消费后端已经暴露的运行结果

### 3.2 当前前端已经有的基础

当前前端已具备这些页面骨架：

- 项目列表 / 项目详情
- 文档页
- Requirement 页
- Evidence 页
- Outline 页
- Editor 页
- Validation 页
- Export 页

但 `reuse-first` 关键交互还没完全接好。

### 3.3 当前前端最明显的缺口

1. Outline 页还没有 `Approve Outline`
2. Outline 类型与树节点没有完整保留 `reuse-first` 元数据
3. Section 页没有展示 `recommended_assets`
4. Section 页没有清晰展示 `baseline / reuse_first / manual_only`
5. Validation 页还没有针对 `reuse-first` 新语义做更清楚的分组与解释

---

## 4. 默认开发边界

### 4.1 对 antigravity

`antigravity` 默认只允许修改：

- `frontend/**`
- `frontend/Dockerfile`（仅在前端启动被阻塞时）

禁止修改：

- `backend/**`
- `gateway/**`
- `backend/alembic/**`
- 根目录 Python / API / 数据设计文档

### 4.2 对 claudecode

`claudecode` 默认也应先按“前端优先、后端不动”执行。

除非本轮任务被明确授权，否则也不要主动改：

- `backend/**`
- `gateway/**`
- 数据库 schema
- API 返回结构

### 4.3 通用禁止事项

- 不新增后端接口
- 不修改现有 API 字段语义
- 不为了 UI 方便去改数据库或 Python 逻辑
- 不把 legacy `/api/v1/generation/*` 做成主路径
- 不把“自动成稿”包装成当前产品真实能力

如果前端实现被契约阻塞：

- 记录阻塞点
- 在交付说明里列出来
- 不要自行扩改后端

---

## 5. 关键产品原则

前端设计必须遵守以下原则：

1. **让用户知道当前在哪个阶段**
   - 候选大纲
   - 已审批大纲
   - 章节生成中
   - 待审核
   - 可导出

2. **让用户知道系统输出是什么性质**
   - 候选
   - 推荐
   - 待确认
   - 可直接复用
   - 仅参考

3. **让用户知道哪些内容是系统生成、哪些是人工确认的**
   - 大纲是否已审批
   - 章节是否已编辑
   - 资产是否 `review_required`

4. **让用户知道不同章节走的生成策略**
   - `baseline`
   - `reuse_first`
   - `manual_only`

5. **让用户知道这不是黑盒**
   - 有引用
   - 有推荐资产
   - 有校验结果
   - 有 review tasks

---

## 6. 本轮迭代计划

建议严格按下面顺序推进。

### Iteration 1: Outline 审批断点

目标：

- 把“大纲生成后必须审批”这件事在 UI 上做实

必须完成：

- Outline 页展示：
  - `outline_status`
  - `generation_strategy`
  - 是否需要审批
- 增加两个明确动作：
  - `Save Outline`
  - `Approve Outline`
- 用户点击 `Approve Outline` 时：
  - 如果有未保存修改，先提交完整 `outline_json`
  - 再调用审批接口
- 若大纲仍是 `candidate`，章节页和生成按钮应明确提示“需先审批”

验收标准：

- 用户能明确区分 `candidate` 与 `approved`
- 审批前误点生成章节时，前端能友好展示后端返回的阻断信息
- 审批完成后，流程自然进入章节生成

### Iteration 2: Outline 元数据透传与展示

目标：

- 不丢 `reuse-first` 路由信息

必须完成：

- 前端类型补齐这些字段：
  - `section_class`
  - `reuse_level`
  - `generation_mode`
  - `asset_required`
  - `parameter_sensitive`
  - `customer_specificity`
  - `outline_status`
  - `approved_by_user`
  - `approved_at`
  - `reviewer_notes`
- OutlineTree 在编辑 `title / purpose / children` 时，未编辑字段必须透传保留
- 每个章节节点至少展示：
  - `generation_mode`
  - `reuse_level`
  - `asset_required`
  - `parameter_sensitive`
  - `needs_human_review`

验收标准：

- `PATCH /outlines/{id}` 后，额外字段不丢失
- 用户能在大纲页看出哪些章节适合复用、哪些章节偏人工

### Iteration 3: Section Drafts 与 Recommended Assets

目标：

- 让章节页真正体现 `reuse-first`

必须完成：

- Section 列表 / 编辑页展示每章：
  - `status`
  - `generation_mode`
  - `reuse_level`
  - `asset_required`
- 新增 `recommended_assets` 展示区
- 每个资产卡片至少展示：
  - `asset_type`
  - `title`
  - `document_name`
  - `page_no`
  - `heading_path`
  - `reason`
  - `preview_text`
  - `review_required`
- 对 `review_required=true` 做明显视觉标记
- 对 `manual_only` 章节做清晰提示，避免用户误以为系统会自动写好

加分项：

- 若正文中出现 `[[ASSET:...]]` 占位，可联动高亮对应资产卡片
- 按 `generation_mode` 使用不同色彩或图标

验收标准：

- 用户能看懂该章为什么是 `reuse_first`
- 用户能看懂该章有哪些推荐资产、这些资产为什么相关

### Iteration 4: Validation 与 Review Tasks

目标：

- 把“系统校验”和“人工收口”连接起来

必须完成：

- Validation 页更清楚展示：
  - `errors`
  - `warnings`
  - `review_tasks`
- 对以下码值做更清楚的 UI 呈现：
  - `VAL008`
  - `VAL009`
  - `VAL104`
  - `VAL105`
  - `VAL106`
- 建议按章节或任务归因分组显示
- 高亮 `P0`
- Review Task 的处理动作要清楚区分：
  - resolve
  - reject

验收标准：

- 用户能快速判断是否还能继续导出
- 用户能知道是“参数问题、过拟合问题、资产问题”还是“普通提醒”

### Iteration 5: 流程收口与体验抛光

目标：

- 让整条人机协同链路可演示、可理解、可稳定复现

建议完成：

- 项目级状态提示更清楚
- 页面间跳转更顺
- 空态 / 加载态 / 错误态统一
- 页面文案明确区分：
  - 自动生成
  - 推荐参考
  - 待人工确认
  - 可导出

验收标准：

- 新用户第一次看也能理解整条工作流
- 演示时不会误导为“完全自动成稿”

---

## 7. 必接接口

### 7.1 Outline

- `POST /api/v1/projects/{project_id}/generate-outline`
- `GET /api/v1/projects/{project_id}/outlines/latest`
- `PATCH /api/v1/projects/{project_id}/outlines/{outline_id}`
- `POST /api/v1/projects/{project_id}/outlines/{outline_id}/approve`

审批请求体：

```json
{
  "outline_json": { "...": "通常提交当前前端完整编辑态" },
  "reviewer_notes": "可选",
  "approved_by_user": true
}
```

### 7.2 Sections

- `POST /api/v1/projects/{project_id}/generate-sections`
- `GET /api/v1/projects/{project_id}/sections`
- `POST /api/v1/projects/{project_id}/sections/{section_id}/regenerate`
- `PATCH /api/v1/projects/{project_id}/sections/{section_id}`

`GET /sections` 返回值里前端必须使用：

- `status`
- `recommended_assets`
- `validator_result`

`validator_result` 里至少可能有：

- `generation_mode`
- `reuse_pack`
- `generation_details`

### 7.3 Validation / Review / Export

- `POST /api/v1/projects/{project_id}/validate`
- `GET /api/v1/projects/{project_id}/validation/latest`
- `GET /api/v1/projects/{project_id}/review-tasks`
- `POST /api/v1/projects/{project_id}/review-tasks/{task_id}/resolve`
- `POST /api/v1/projects/{project_id}/export`
- `GET /api/v1/projects/{project_id}/exports/latest`

---

## 8. 当前前端实现时的注意点

### 8.1 Outline 保存不能只传精简字段

后端在更新大纲时会重新标准化整个 `outline_json`。

因此前端保存时不要只传：

- `title`
- `sections`

而是应尽量提交完整 `outline_json`，保住：

- `outline_status`
- `generation_strategy`
- `approval_required`
- `approved_by_user`
- `approved_at`
- `reviewer_notes`
- section 上所有路由字段

### 8.2 类型定义必须补齐

当前前端类型偏旧，容易导致：

- 字段丢失
- UI 不展示
- 保存时误删 metadata

因此建议先做一轮 `frontend/src/lib/types.ts` 的契约补齐，再开始 UI 迭代。

### 8.3 推荐资产是“参考”，不是“自动落稿”

前端文案不要写成：

- 已插入图片
- 已自动引用图表
- 已自动替换参数

更准确的表述应是：

- 推荐资产
- 建议插入
- 待人工确认
- 参考来源

### 8.4 manual_only 章节必须被显式标记

这类章节不要和普通生成章节混在一起展示，否则用户会误解系统能力边界。

---

## 9. 建议交付物

本轮最低交付物建议为：

1. 一版可用的 Outline 审批页
2. 一版可用的 Section Draft 页面，含 `recommended_assets`
3. 一版更清晰的 Validation / Review Tasks 页面
4. 一份交付说明，包含：
   - 已完成能力
   - 未完成能力
   - 已知阻塞点
   - 是否严格遵守“前端不改后端”

---

## 10. 建议自测清单

每轮提交前，至少人工走一遍下面流程：

1. 创建项目
2. 上传文档
3. 生成 Requirement Card
4. 检索 Evidence Bundle
5. 生成 Outline
6. 修改并保存 Outline
7. 审批 Outline
8. 生成全部章节
9. 查看某个 `reuse_first` 章节的推荐资产
10. 编辑某个章节并保存
11. 运行 Validation
12. 处理至少一个 Review Task
13. 进入 Export 页面确认链路闭环

---

## 11. 本轮不做

本轮明确不要求：

- 图/表拖拽插入编辑器
- 资产图库后台
- block 级可视化复用选择器
- 样板台账后台
- case library 在线管理界面
- 后端契约扩改

---

## 12. 最终判断

这轮前端开发的核心，不是“做一个更炫的页面”，而是：

`把 reuse-first 的人机协同工作流做清楚，让用户知道什么时候该确认大纲、什么时候该看推荐资产、什么时候该逐章审核。`

只要这条主线清楚，前端就已经在帮项目真正落地，而不是只做展示皮肤。
