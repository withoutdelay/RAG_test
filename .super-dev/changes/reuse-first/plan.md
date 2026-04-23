# Plan: Reuse First

## Context

当前 `reuse_first` 主要问题是 chunk 过度主导章节候选，导致短查询容易命中词面相近但章节错误的内容。本次变更将主链路调整为：

`document shortlist -> section shortlist -> block selection -> asset backfill -> prompt assembly -> trace review`

目标是先把“最像的历史章节”找对，再把块、表格、图片等作为章节内部证据挂接进来。

## Architecture Impact

- 解析层：`docling_parser.py` 在结构提示中补齐 `section_catalog` 与 `document_title`
- 入库层：`documents.py` 在 chunk 与资产元数据中补写 `source_section_id`、`section_path`、`source_heading`
- 索引层：`case_library.py` 与构建脚本生成章节/块的上下文化检索文本
- 检索层：`case_service.py` 提升标题 exact/alias/family 权重，并强化父子节冗余压分
- 组装层：`section_service.py` 按 `title_intent` / `detail_intent` / `context_intent` 拆查询，输出 `selection_reason`
- 前端层：`SectionBlock.tsx` 展示章节候选、query intents、selection decision 与 trace

兼容性原则：

- 保持现有 `reuse_first` 接口不变，新增字段为向后兼容扩展
- 无高置信章节时回退到 `section-pack mode`，避免整章直送误用
- 无法解析章节锚点时保留空值，不造假 metadata

## Completed Workstreams

1. 章节真值层
   - docling 解析结构中输出 `section_catalog`
   - 章节路径与标题归一化能力进入解析真值层

2. 索引与挂接
   - narrative/table/figure 等内容显式挂接 `source_section_id`
   - preserved table metadata 继承章节锚点

3. 检索与装配
   - `retrieve_sections` 调整为标题优先的 hybrid 排序
   - `retrieve_blocks` 调整为“章节内二阶段选择”
   - `SectionCompositionService` 输出完整 trace 与 mode 决策

4. 审阅与验证
   - 前端调试面板补充章节候选和 decision 展示
   - 后端单测扩到章节目录、章节锚点、检索排序、回退与 trace 场景

## Risks & Mitigations

- 风险 1：章节标题泛化，父节仍可能挤占叶子节
  - 缓解：保留 exact/alias/family 强信号，并继续对宽父节做冗余压分

- 风险 2：章节锚点缺失导致资产无法稳定回挂
  - 缓解：通过 `section_catalog` 与 heading path 双重解析；解析失败时保留空值并在 trace 中暴露

- 风险 3：整章直送导致 token 预算超限
  - 缓解：默认 `section-pack mode`，仅在高置信且预算可控时启用 `full-section mode`

- 风险 4：数据库依赖的端到端测试在本地环境不可复跑
  - 缓解：先完成单元层和无数据库回归；数据库连通后补跑 `tests.test_api_phase2`

## Rollout Strategy

- 阶段 1：先上线解析、索引和检索逻辑，确保 trace 字段完整
- 阶段 2：用前端调试视图给人工审阅提供可见性
- 阶段 3：观察真实项目上的 top1/top3 命中与人工修改量，再决定是否扩大 `full-section mode` 触发面

回滚策略：

- 检索异常时，可在服务层退回已有 baseline / section-pack 行为
- 新增字段均为兼容扩展，不影响既有调用方反序列化

观测建议：

- 记录 `selection_reason.mode`
- 记录 top1 与 runner-up 分差
- 记录 `token_budget` 命中率与 fallback 触发次数
