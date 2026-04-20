# Product Driven Long Term Bootstrap

## Summary

为长期路线图建立正式的进入治理层，明确当前系统何时可以从“产品驱动基础阶段”进入“长期路线图实现阶段”。本次范围不直接交付新的产品设计 Agent 或图形生成模块，而是把真实方案样本、客户材料清单、首批目标产品族和 schema 演化边界固定为正式需求，作为后续实现的前置门。

## ADDED Requirements

### Requirement: long-term-roadmap-entry-gate
SHALL 为长期路线图定义明确的进入条件、边界和阶段拆分，避免在材料不足时盲目推进完整实现。

#### Scenario 1: Bootstrap entry
- GIVEN 当前仓库已经完成产品驱动基础阶段
- WHEN 团队评估是否进入长期路线图
- THEN 系统必须明确区分“可立即开始的启动阶段”和“依赖客户材料的后续实现阶段”

#### Scenario 2: Guard against premature expansion
- GIVEN 客户尚未提供产品级真值材料
- WHEN 团队准备推进完整长期路线图实现
- THEN 变更必须把范围收敛在 `Phase 0A / Phase 0B`，而不是直接展开全量实现

### Requirement: client-material-intake-contract
SHALL 为客户公司提供正式材料清单，说明长期路线图所需的最小材料包和推荐交付结构。

#### Scenario 1: Material request
- GIVEN 项目需要向客户收集产品级真值材料
- WHEN 团队输出材料请求说明
- THEN 文档必须明确 P0 / P1 / P2 三层材料、推荐格式和最小交付门槛

#### Scenario 2: Intake readiness
- GIVEN 客户按要求准备材料
- WHEN 团队评估是否进入下一轮长期路线图实现
- THEN 能够根据材料清单判断哪些进入条件已经满足，哪些仍然缺失

### Requirement: sample-inventory-and-family-priority
SHALL 把当前真实方案样本整理为长期路线图的输入基础，并明确首批目标产品族。

#### Scenario 1: Sample inventory
- GIVEN 当前已经存在真实方案与技术协议样本
- WHEN 团队进入长期路线图启动阶段
- THEN 必须建立样本台账，并能按产品族、电压等级、电机类型、负载类型和协议进行归类

#### Scenario 2: First target families
- GIVEN 当前样本覆盖多个方向
- WHEN 团队确定长期路线图首批目标
- THEN 必须明确至少 1 到 2 个核心产品族作为下一轮实现范围

### Requirement: schema-delta-definition
SHALL 定义长期路线图目标 schema 与当前基础实现之间的差距，作为后续数据建模演化依据。

#### Scenario 1: Delta identification
- GIVEN 当前系统已经有 `product_series / product_standard_configs / product_constraints`
- WHEN 团队准备进入下一轮实现
- THEN 必须明确长期路线图目标中的缺失实体、关系和字段边界

#### Scenario 2: Controlled evolution
- GIVEN 长期路线图目标 schema 比当前实现更完整
- WHEN 团队规划后续模型演进
- THEN 必须优先在首批目标产品族范围内推进，而不是一次性做全产品线建模

## Notes

- 用户故事：作为项目负责人，我希望在客户补齐产品级材料前先锁定进入条件和资料清单，这样后续长期路线图实现不会因为材料缺失而反复返工
- 可靠性：长期路线图必须基于真实方案样本和正式产品资料的组合，而不是只靠历史文档推断
- 安全性：客户材料仍属于敏感资料，后续 intake、整理和入库要继续遵守当前私有样本目录约束

## Acceptance Checklist

- [x] AC1: 长期路线图启动计划已落盘到 `output/product-driven-long-term-execution-plan.md`
- [x] AC2: 客户材料清单已落盘到 `output/product-driven-client-material-request.md`
- [x] AC3: 真实方案样本台账已建立
- [x] AC4: 首批目标产品族与 schema delta 已冻结
- [ ] AC5: 已满足进入下一轮长期路线图实现的材料门槛

## Out of Scope

- 本轮不直接实现独立 `SolutionDesignAgent`
- 本轮不直接实现独立 `DiagramGenerator`
- 本轮不直接实现反馈回写 API 和同义词自动抽取逻辑
