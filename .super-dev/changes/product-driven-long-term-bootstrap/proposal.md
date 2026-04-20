# Product-Driven Long-Term Bootstrap 提案

## 背景描述

当前仓库已经完成产品驱动方案第一阶段，实现了 `solution_snapshot`、`Solution` 工作台、产品目录基础能力，以及与大纲 / 章节 / 校验链路的集成。系统具备继续进入长期路线图的基础，但长期路线图的后续阶段不再只是“补几个 API”，而是要把历史方案样本、产品目录、接口资料、约束规则和术语映射组织成稳定的产品真值层。

目前的真实方案样本已经足够支持长期路线图的启动，但还不足以支撑全产品线、全场景的全面铺开。因此，本次变更不直接实现完整 `SolutionDesignAgent / DiagramGenerator / 反馈回写`，而是先收敛到长期路线图的启动阶段：

`真实样本整理 -> 产品级材料接入 -> 核心产品族优先级 -> schema delta -> 下一轮实现`

## 本次交付范围

本次变更聚焦长期路线图启动阶段，只覆盖以下内容：

1. 冻结长期路线图启动计划
2. 冻结客户材料清单与最小交付门槛
3. 明确 `Phase 0A / Phase 0B` 的进入条件与退出条件
4. 为下一轮长期路线图实现准备正式 Spec / tasks / plan / checklist

## 技术方案摘要

1. **长期路线图分段进入**
   - 把长期路线图拆成可执行的两段：内部准备 `Phase 0A` 与客户材料接入 `Phase 0B`。
2. **材料先行，不盲目编码**
   - 在缺少产品手册、标准配置、点表和约束规则前，不直接做全量产品目录和全面 Agent 化实现。
3. **聚焦两个首批产品族**
   - 先以 `LCI / 同步电机软起动` 与 `高压固态 / 高压变频软起动` 作为长期路线图第一批目标。
4. **下一轮实现有正式治理入口**
   - 通过本次 change spec，把后续真实样本台账、术语映射、schema delta 和材料 intake 工作纳入正式变更管理。

## 预估交付清单

- `output/product-driven-long-term-execution-plan.md`
- `output/product-driven-client-material-request.md`
- `.super-dev/changes/product-driven-long-term-bootstrap/proposal.md`
- `.super-dev/changes/product-driven-long-term-bootstrap/tasks.md`
- `.super-dev/changes/product-driven-long-term-bootstrap/plan.md`
- `.super-dev/changes/product-driven-long-term-bootstrap/checklist.md`
- `.super-dev/changes/product-driven-long-term-bootstrap/specs/product-driven-long-term-bootstrap/spec.md`
