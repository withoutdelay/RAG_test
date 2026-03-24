# Antigravity Reuse-First Frontend Handoff

本文档用于交接 `reuse-first` 路线下的前端增量改造。

这不是一次“重做前端”，而是在现有 V2 UI 基础上，补上新的大纲确认断点、章节路由信息和资产推荐展示。

## 1. 硬约束

antigravity **只允许完成前端部分**。

允许修改：

- `frontend/**`
- 如前端启动方式必须调整，可修改 `frontend/Dockerfile`

禁止修改：

- `backend/**`
- `gateway/**`
- `backend/alembic/**`
- 根目录所有 Python / 数据库 / API 文档

禁止做的事情：

- 不新增后端接口
- 不修改现有后端返回结构
- 不为了 UI 方便去改后端字段
- 不把 legacy `/api/v1/generation/*` 重新做成主路径

如果前端实现被后端契约阻塞：

- 记录阻塞点
- 在交付说明里列出来
- 不要自行修改后端

## 2. 本次改造目标

本次前端改造的目标只有 4 个：

1. 支持 `Candidate Outline -> 人工确认 -> Approved Outline`
2. 在大纲层展示并保留章节路由元数据
3. 在章节页展示 `recommended_assets`
4. 让用户明确知道哪些章节走 `baseline`、哪些走 `reuse_first`、哪些需要人工编写

## 3. 当前后端已具备的关键能力

当前后端已经支持：

- 生成带章节元数据的大纲
- 更新大纲
- 审批大纲
- 审批前禁止生成章节
- 章节生成后返回 `recommended_assets`
- `manual_only` 章节返回人工编写骨架
- 导出稿中渲染 `[[ASSET:...]]` 资产占位

这意味着前端不需要自己推导这些逻辑，只需要把后端返回的状态和字段正确展示出来。

## 4. 必须对接的新接口

### 4.1 Outline 审批

- `POST /api/v1/projects/{project_id}/outlines/{outline_id}/approve`

请求体：

```json
{
  "outline_json": { "...": "可选，通常传用户修改后的完整 outline_json" },
  "reviewer_notes": "可选",
  "approved_by_user": true
}
```

注意：

- 前端在审批前，应默认把当前编辑态的大纲完整提交
- 后端会把 `outline_json.outline_status` 从 `candidate` 更新为审批后的状态
- 审批前调用 `generate-sections` 会收到 `400`

### 4.2 章节生成

- `POST /api/v1/projects/{project_id}/generate-sections`
- `POST /api/v1/projects/{project_id}/sections/{section_id}/regenerate`
- `GET /api/v1/projects/{project_id}/sections`

`GET /sections` 返回的每条 section draft 里，新增前端应重点使用：

- `recommended_assets`
- `status`
- `validator_result`

其中 `validator_result` 至少可能包含：

- `generation_mode`
- `reuse_pack`
- `errors`
- `warnings`

## 5. 大纲页必须支持的新字段

大纲树里每个 `section` 节点，后端现在可能带这些字段：

- `section_id`
- `title`
- `purpose`
- `mandatory`
- `expected_evidence_types`
- `needs_human_review`
- `children`
- `section_class`
- `reuse_level`
- `generation_mode`
- `asset_required`
- `parameter_sensitive`
- `customer_specificity`

前端必须做到两件事：

1. 展示这些字段中的关键部分
2. 在用户编辑并保存大纲时，不要把这些字段丢掉

这是本次交接里最重要的一条。

建议保存策略：

- 以前端树节点为基准编辑 `title / purpose / children`
- 对未编辑的额外字段做透传保留
- `PATCH /outlines/{outline_id}` 时始终提交完整 `outline_json`

## 6. 推荐的大纲页交互

### 6.1 Candidate Outline 页面

建议展示：

- 大纲标题
- `outline_status`
- `generation_strategy`
- 审批要求提示

每个 section 节点建议有以下只读或半只读标签：

- `reuse_level`
- `generation_mode`
- `asset_required`
- `parameter_sensitive`
- `needs_human_review`

### 6.2 编辑动作

建议允许用户：

- 改标题
- 改 purpose
- 增删章节
- 调整顺序
- 编辑 children

如果前端本轮不想开放全部高级字段编辑，至少要做到：

- 展示这些字段
- 保存时不丢失这些字段

### 6.3 审批动作

页面应有明确的：

- `Save Outline`
- `Approve Outline`

用户点 `Approve Outline` 时：

- 若存在未保存变更，先提交当前完整 `outline_json`
- 再调用审批接口

## 7. 章节页必须支持的新展示

章节列表页或编辑页现在要明确显示每章的：

- 标题
- `status`
- `generation_mode`
- `reuse_level`
- `asset_required`

建议用标签展示：

- `baseline`
- `reuse_first`
- `manual_only`

## 8. Recommended Assets 侧栏

`GET /sections` 返回的每条 draft 里有 `recommended_assets`。

前端应在章节编辑页新增一个右侧或底部面板，展示每个推荐资产的：

- `asset_type`
- `title`
- `document_name`
- `page_no`
- `heading_path`
- `reason`
- `preview_text`
- `review_required`

建议交互：

- 点击资产卡片可展开详情
- 对 `review_required=true` 的资产做明显标记
- 若正文中存在 `[[ASSET:...]]` 占位，可在侧栏中高亮对应资产

注意：

- 本轮前端不需要自动把资产真正插成图片
- 只需要帮助售前工程师看到“建议放什么”和“为什么”

## 9. Validation 页要兼容的新语义

当前 validation 里与 `reuse-first` 相关的新码值包括：

- `VAL008`：旧项目/旧客户痕迹残留
- `VAL009`：关键参数未替换到当前项目
- `VAL104`：缺少资产占位
- `VAL105`：与历史块相似度过高，需确认是否过拟合
- `VAL106`：参数替换不完整

前端不需要理解这些码值背后的算法，但应在 UI 上把它们清晰展示出来，尤其是：

- `VAL008`
- `VAL009`
- `VAL105`
- `VAL106`

建议做法：

- 按章节分组显示
- 高亮 `P0`
- 对 `VAL105/VAL106` 解释成“需人工确认复用是否过近 / 参数是否补齐”

## 10. Export 页面改动

后端导出时会把正文里的 `[[ASSET:...]]` 渲染成引用块，所以前端在 Export 页面上不需要自己解析资产占位。

前端只需要：

- 保持预览 markdown 正常显示
- 不要在预览层过滤这些 blockquote 资产块

## 11. 推荐的最小交付范围

如果 antigravity 想控制开发范围，建议这次只交付：

1. 大纲页支持审批断点
2. 大纲页展示 section 路由标签
3. 章节页展示 `recommended_assets`
4. Validation 页兼容新码值展示

这 4 项做完，就已经能支撑 `reuse-first` 的第一轮联调。

## 12. 明确不做

本轮不要求 antigravity 完成：

- 真正的图/表拖拽插入
- 资产图库管理后台
- 章节 diff 审查器
- 复杂 block 级复用选择器
- 后端接口改造

## 13. 建议联调顺序

1. `GET /projects/{id}/outlines/latest`
2. `PATCH /projects/{id}/outlines/{outline_id}`
3. `POST /projects/{id}/outlines/{outline_id}/approve`
4. `POST /projects/{id}/generate-sections`
5. `GET /projects/{id}/sections`
6. `POST /projects/{id}/validate`
7. `GET /projects/{id}/validation/latest`
8. `GET /projects/{id}/review-tasks`
9. `POST /projects/{id}/export`

## 14. 交付标准

这轮前端改造完成的最低标准是：

- 大纲审批前后状态清晰
- 大纲编辑后保存不会丢 section 元数据
- 章节页能看到 `recommended_assets`
- 用户能区分 `baseline / reuse_first / manual_only`
- Validation 页能正确展示 `reuse-first` 相关问题

