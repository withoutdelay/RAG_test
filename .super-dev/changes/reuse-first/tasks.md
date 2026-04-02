# 任务清单

- [x] 1. (类型系统补齐) 更新 `frontend/src/lib/types.ts` 中的 `OutlineSection`, `OutlineModel` 与 `SectionModel`, `RecommendedAsset` 接口契约
- [x] 2. (Outline 审批断点) 大纲页新增 `Save Outline` 与 `Approve Outline` 按钮及对应的后端请求逻辑
- [x] 3. (Outline 审批断点) 未审批大纲（`candidate`）状态下，前端明确阻断“生成章节”相关的点击或跳转
- [x] 4. (Outline 元数据展示) Outline 树节点中补充渲染 `generation_mode`, `reuse_level` 等徽标标签（Badge）
- [x] 5. (Outline 元数据透传) 修复 `PATCH /outlines/{id}` 请求，确保不丢失未编辑的 `section_class`, `reuse_level`, `asset_required` 等路由核心参数
- [x] 6. (章节推荐资产) Section 页面/组件引入 `recommended_assets` 区块，并针对 `review_required: true` 做高亮与黄牌提示
- [x] 7. (章节分类标注) Section 页面为各个章节打上真实的生成策略标签，特别凸出 `manual_only` 章节防混淆
- [x] 8. (Validation 视图重构) 将 Validation 的日志基于 `Errors / Warnings / Review Tasks` 进行折叠/分组展现
- [x] 9. (Validation 问题归因) 对 `VAL008`, `VAL009` 等高频校验异常代码进行文案解释及视觉凸显（如红色高亮 P0 级）
- [x] 10. (Review Task 操作链路) 为每一个审核任务增加绿色的 `Resolve` 和灰色的 `Reject` 处理入口按钮
- [x] 11. (产品链路与文案自查) 清理各类文案（将“自动生成”等误导性词汇修正为“参考”、“待确认”），统一各个页面的 Loading态 / Error态 样式
