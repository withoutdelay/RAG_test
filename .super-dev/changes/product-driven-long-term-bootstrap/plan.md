# Plan: Product Driven Long Term Bootstrap

## Context

长期路线图已经具备启动条件，但当前还缺少稳定的产品级真值材料。继续直接编码 `SolutionDesignAgent`、`DiagramGenerator` 或反馈回写能力，会把不完整的产品知识固化进系统。因此，这一轮先做“长期路线图的进入治理”，把样本、材料、产品族优先级和 schema 演化边界锁定下来。

## Architecture Impact

- 模块边界
  - 不新增新的运行时业务模块
  - 本轮主要新增长期路线图治理文档与 change spec
  - 为后续 `product_families / product_models / product_interfaces / product_compatibility` 等模型扩展做前置定义
- 数据流
  - 真实方案样本继续作为历史经验源
  - 客户补充的产品手册、配置、点表和约束规则将成为后续产品真值层
  - 当前 seed catalog 仅作为长期路线图启动期的过渡事实源
- 兼容性
  - 不破坏当前已交付的 `solution_snapshot`、目录查询与 Solution 工作台
  - 不改变现有 API 路径和用户可见行为

## Risks & Mitigations

- 风险1: 真实样本标签不清，后续产品族归类混乱
  - 缓解: 先建立样本台账与统一标签规则，再决定首批产品族边界
- 风险2: 客户提供材料不完整，导致长期路线图反复返工
  - 缓解: 先冻结正式材料清单，明确 P0 / P1 / P2 分层与最小交付门槛
- 风险3: 过早推进全量 schema，造成模型和数据结构震荡
  - 缓解: 先形成 schema delta 清单，只在首批产品族范围内演化

## Rollout Strategy

- 阶段发布与回滚方案
  - 第 1 阶段：冻结长期路线图执行计划与客户材料清单
  - 第 2 阶段：默认使用 mock 推进样本台账、产品族标签、schema delta 和材料主链接入
  - 第 3 阶段：仅在 checkpoint 使用 live 验证 `项目概述 / 整体方案概述 / 总体方案`
  - 第 4 阶段：客户材料全部到位后，再创建下一轮实现 change spec
  - 回滚方式：如客户材料不足，则停留在当前产品驱动基础版本，不进入长期路线图实现
- 观测与告警策略
  - 通过 `output/product-driven-long-term-execution-plan.md` 追踪启动阶段边界
  - 通过 `output/product-driven-client-material-request.md` 追踪材料接入契约
  - 通过当前 change tasks 与 checklist 跟踪是否满足进入下一轮实现的前置条件
  - 如 Entry Gate 未通过，必须明确反馈“材料门槛未通过”，不能继续宣称进入 Phase 1/2/3
