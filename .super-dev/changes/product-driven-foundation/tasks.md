# 任务清单

- [x] 1. 冻结 `product-driven` 方向的 proposal、tasks 和 execution plan
- [x] 2. 新增 `solution_snapshot` 数据模型、迁移和后端读写接口
- [x] 3. 新增 `SolutionService` 首版推荐逻辑，生成可编辑的方案快照
- [x] 4. 新增项目级 `Solution` 工作台页面，并接入项目导航
- [x] 5. 把 `solution_snapshot` 接入 `OutlineService`，让建议章节和方案摘要进入大纲生成
- [x] 6. 把 `solution_snapshot` 接入 `SectionDraftService`，为不同章节类型注入产品上下文和图表
- [x] 7. 在 `ValidationService` 中增加产品参数一致性、接口能力和供货范围校验
- [x] 8. 用 PostgreSQL 产品目录替换当前首版静态候选库，并补齐导入与发布流程
- [x] 9. 引入 Catalog Explorer、方案对比和更细粒度的人工调整交互
- [x] 10. 基于真实方案文档补齐端到端回归测试和质量门禁
