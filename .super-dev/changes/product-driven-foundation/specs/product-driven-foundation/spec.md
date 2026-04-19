# Product Driven Foundation

## Summary
为现有方案生成链路新增“产品知识驱动的方案设计层”，让系统可以基于项目字段、需求卡和产品目录生成可持久化、可编辑、可确认的 `solution_snapshot`，并将其作为大纲、章节和校验环节的统一事实源。本次范围覆盖后端 artifact 建模、产品目录服务、前端 Solution 工作台、Catalog Explorer/方案对比交互，以及真实方案文档回归门禁。

## ADDED Requirements

### Requirement: versioned-solution-snapshot
SHALL 为每个项目持久化版本化的 `solution_snapshot`，保存方案摘要、选型清单、接口计划、约束、候选评分、确认状态以及 `source_catalog_version`。

#### Scenario 1: Initial design snapshot
- GIVEN 项目已存在并且有最新 `RequirementCard`
- WHEN 用户调用 `design-solution` 生成首版方案
- THEN 系统生成新的 `solution_snapshot` 版本，并保存所选产品、接口计划、约束与产品目录版本

#### Scenario 2: Editable snapshot lifecycle
- GIVEN 项目已有 `solution_snapshot`
- WHEN 用户执行 update / confirm 等操作
- THEN 系统保留版本化历史，并让最新快照作为项目级方案事实源

### Requirement: catalog-driven-solution-recommendation
SHALL 使用 PostgreSQL 产品目录而非静态候选库完成主驱动/配套产品推荐，并输出标准配置、约束与候选评分。

#### Scenario 1: Catalog-based shortlist
- GIVEN `RequirementCard` 提供产品线、行业、电压、接口等关键信息
- WHEN `SolutionService` 设计方案
- THEN 系统从产品目录选择主系列与配套系列，并返回 `candidate_scores`、标准配置和选择理由

#### Scenario 2: Catalog version traceability
- GIVEN 产品目录完成 seed 或发布
- WHEN 方案被重新设计或人工同步目录项
- THEN 生成的 `solution_snapshot` 必须绑定当前 `source_catalog_version`

### Requirement: solution-workbench-and-manual-adjustment
SHALL 提供项目级 Solution 工作台，支持 Catalog Explorer、方案对比和细粒度人工调整。

#### Scenario 1: Compare and inspect
- GIVEN 项目存在多个方案版本
- WHEN 用户进入 Solution 页面
- THEN 页面展示最新方案、历史版本对比、Catalog Explorer 和候选产品约束信息

#### Scenario 2: Manual product intervention
- GIVEN Catalog Explorer 中存在候选产品或已选配套产品
- WHEN 用户将候选系列设为主驱动、同步目录项或移除配套产品
- THEN 页面立即重算选型、IO 分配和相关摘要，并可再次保存为新快照

### Requirement: downstream-consistency
SHALL 将 `solution_snapshot` 接入大纲生成、章节草拟和校验服务，避免正文与选型事实脱节。

#### Scenario 1: Outline and section generation
- GIVEN 项目已有确认后的 `solution_snapshot`
- WHEN 系统生成大纲和章节草稿
- THEN 输出内容必须带入产品上下文、接口方案、推荐图表与供货范围信息

#### Scenario 2: Validation against solution facts
- GIVEN 章节草稿引用了产品参数、接口或供货范围
- WHEN `ValidationService` 执行质量校验
- THEN 系统检测并提示产品参数不一致、接口协议不匹配和供货范围缺漏

### Requirement: real-proposal-regression-gate
SHALL 提供基于真实方案文档的端到端回归脚本与门禁，验证 LCI 鼓风机场景在产品驱动链路下的可用性。

#### Scenario 1: Real fixture regression
- GIVEN 工作区存在受信的真实方案样本文件
- WHEN 运行 `backend/scripts/run_product_driven_real_regression.py`
- THEN 系统创建临时项目、依次执行 requirement/evidence/design-solution，并输出门禁报告

#### Scenario 2: Direct fixture hit acceptance
- GIVEN 检索分数较低但 evidence 结果直接命中了受信的真实样本文件
- WHEN 回归 helper 评估 `evidence_quality_sufficient`
- THEN 门禁允许以 `fixture_direct_hit` 作为通过原因，同时继续输出原始 `quality_score` 供诊断

## Notes
- 用户故事：作为方案工程师，我希望在项目内先确认产品组合，再生成大纲和正文，以便减少旧方案误导并保留人工判断权
- 性能：`design-solution` 与回归脚本在现有开发环境下保持交互式可用，目标是秒级方案生成与分钟级真实回归
- 可靠性：方案快照、目录版本、历史版本与回归报告都需要可追溯
- 安全性：本次变更不新增外部公开接口鉴权模型，但需要避免不可信样本直接改变方案事实

## Acceptance Checklist
- [x] AC1: 项目内可生成、查看、更新并确认 `solution_snapshot`
- [x] AC2: Catalog Explorer、方案对比与人工调整在预览中可用
- [x] AC3: Outline / Section / Validation 已消费 `solution_snapshot`
- [x] AC4: 真实 LCI 样本回归通过 `8/8` 门禁
- [x] AC5: 关键后端单测与 compileall 已通过

## Out of Scope
- 自动生成完整的产品主数据维护后台
- 面向所有产品线的深度规则引擎与商业报价引擎
- 完整的 CI/CD、覆盖率平台和生产观测平台建设
