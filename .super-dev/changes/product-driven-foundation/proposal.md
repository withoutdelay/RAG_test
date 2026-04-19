# Product-Driven Foundation 提案

## 背景描述

当前仓库已经具备完整的 artifacts pipeline，但在方案生成阶段仍然主要依赖历史文档复用。对于电气行业方案场景，这会带来几个直接问题：

- 新产品组合缺少稳定事实源
- 关键参数容易继承旧文档表述
- 图表与清单主要靠召回旧资料
- `product_line` 只是项目字段，还不是可推理的方案输入

因此，这次变更的目标不是继续只优化文档复用，而是在现有主链路前面补上一层“产品知识驱动的方案设计层”。

目标链路：

`Requirement -> Evidence -> Solution Design -> Outline -> Sections -> Validation -> Export`

## 本次交付范围

本次变更先实现第一条可演示的竖切：

1. 新增 `solution_snapshot` 作为项目级持久化 artifacts
2. 新增 `design-solution / latest / update / confirm` API
3. 新增基于项目信息与需求卡的首版方案推荐逻辑
4. 新增 `Solution` 工作台页面，并接入项目导航
5. 冻结产品知识驱动方向的执行计划，为后续 outline / section / validation 集成做铺垫

## 技术方案摘要

1. **持久化方案快照**
   - 新增 `solution_snapshots` 表和 ORM 模型，保存方案摘要、选型清单、接口计划、约束与确认状态。
2. **首版方案服务**
   - 新增 `SolutionService`，先基于项目字段和最新需求卡做规则驱动推荐，输出可编辑的方案快照。
3. **前端方案工作台**
   - 在项目页新增 `Solution` 阶段，使用三栏工作台展示需求、选型、图表预览和风险追溯。
4. **前端优先集成**
   - 先实现可操作的 `Solution` 页面，再继续把 `solution_snapshot` 接入 Outline / Section / Validation 主链路。

## 预估交付清单

- `backend/app/models/solution_snapshot.py`
- `backend/app/services/solution/service.py`
- `backend/app/api/artifacts.py`
- `backend/app/schemas/artifacts.py`
- `frontend/src/app/projects/[id]/solution/page.tsx`
- `frontend/src/app/projects/[id]/layout.tsx`
- `frontend/src/lib/types.ts`
- `output/product-driven-execution-plan.md`
- `.super-dev/changes/product-driven-foundation/tasks.md`
