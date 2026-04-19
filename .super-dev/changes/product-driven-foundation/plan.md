# Plan: Product Driven Foundation

## Context
当前方案链路在 `Requirement -> Evidence -> Outline/Section` 之间缺少稳定的产品知识层，导致选型、接口、供货范围和章节内容容易被历史文档牵着走。本次变更在现有 artifacts pipeline 中新增 `Solution` 设计层，把产品目录、方案快照和人工调整能力放到生成主链路前面，形成可确认、可追溯、可回灌到正文生成的产品驱动链路。

## Architecture Impact
- 模块边界
  - 后端新增 `solution_snapshot` 持久化模型与 `SolutionService`
  - 后端新增产品目录模型、seed、查询服务与 `catalog` API
  - 前端新增项目级 `Solution` 工作台，并接入项目导航
  - `OutlineService`、`SectionDraftService`、`ValidationService` 消费 `solution_snapshot`
- 数据流
  - `RequirementCard` 提供产品线、行业和约束输入
  - `EvidenceBundle` 继续提供历史方案证据，但不再直接决定产品清单
  - `ProductCatalogService` 产出候选产品、标准配置、约束和候选评分
  - `SolutionSnapshot` 作为下游大纲、章节、校验的统一事实源
- 兼容性
  - 维持现有 artifacts API 版本约定
  - 新增字段保持向后兼容，前端缺失字段时保持可读
  - 数据迁移通过 Alembic 增量方式交付，不破坏已有项目数据

## Risks & Mitigations
- 风险1: 产品目录规则过窄，导致真实方案推荐偏离 -> 缓解: 保存 `candidate_scores`、`source_catalog_version`，并提供 Catalog Explorer 人工干预入口
- 风险2: 方案快照和正文生成链路脱节 -> 缓解: 将 `solution_snapshot` 接入 outline、section、validation 三条主链路，并用针对性单测覆盖
- 风险3: 检索质量分数过低误伤真实回归 -> 缓解: 在真实样本回归门禁中区分直接命中样本与候选命中，并保留 `quality_score` 作为诊断信息
- 风险4: 预览通过但交付证据不足 -> 缓解: 补齐 tasks/spec/plan/checklist、真实回归报告与治理侧 review 状态

## Rollout Strategy
- 阶段发布与回滚方案
  - 第 1 阶段：落地 `solution_snapshot` 与 `SolutionService`，保证项目可生成首版方案
  - 第 2 阶段：接入 `OutlineService`、`SectionDraftService`、`ValidationService`
  - 第 3 阶段：替换静态候选库为 PostgreSQL 产品目录，开放前端人工调整
  - 第 4 阶段：以真实方案文档跑回归，验证核心场景
  - 回滚方式：下线 `design-solution` 入口与前端工作台，回退到旧的 evidence/reuse-first 链路；数据库迁移保留历史数据，不做 destructive rollback
- 观测与告警策略
  - 记录 `solution_snapshot.version`、`source_catalog_version`、候选评分与确认状态
  - 对真实回归输出 `output/product-driven-real-proposal-regression.md`
  - 在质量门禁中追踪 compileall、目标单测和真实回归脚本结果
