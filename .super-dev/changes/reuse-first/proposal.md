# Reuse-First 章节优先检索重构提案

## 背景描述

当前 `reuse-first` 主链路虽然已经具备 `section_catalog`、`source_section_id`、`retrieve_sections` 等基础能力，但运行时仍然以 chunk/block 为主检索与装配单位。实际效果上，这会导致：

- 原始章节语义被固定长度 chunk 打散
- 短查询更容易命中词面相近但章节不对的内容
- 组装给模型的上下文来自多个碎片，造成章节成稿密度下降和风格漂移

因此，本次重构不再把“prompt 微调”作为主抓手，而是正式把主链路调整为：

`section-first hierarchical retrieval`

即：

`Document Shortlist -> Section Shortlist -> Block Selection -> Asset Backfill -> Prompt Assembly -> Generation`

## 技术方案摘要

1. **章节真值层补齐**
   - 继续扩展 `section_catalog`，让每个章节具备稳定的 `section_path`、`source_signals`、`content_span`、`page_span` 和可检索摘要。
2. **章节级检索提升为主通路**
   - `CaseLibraryService` 先按章节标题、标题别名、标题家族和 detail intent 做 shortlist，再在 shortlisted sections 内挑 block。
3. **上下文化块索引**
   - 保留 block/chunk，但为其补齐章节上下文，不再把裸 chunk 当成第一检索实体。
4. **生成输入模式切换**
   - 默认使用 `section-pack mode`
   - 在高置信、低噪声、token 可控时开启 `full-section mode`
5. **运行时追溯与评估**
   - 为每个 `reuse_first` 章节输出 `selected_sections`、`selected_blocks`、`selection_reason`、`token_budget`
   - 建立 `section_retrieval_top1/top3` 与生成质量评估

## 预估交付清单

- `backend/app/services/parsing/section_catalog.py` 章节真值层补齐
- `backend/app/services/parsing/case_library.py` 章节挂接与索引构建增强
- `backend/app/services/retrieval/case_service.py` 章节优先检索主通路
- `backend/app/services/composition/section_service.py` 组装策略与 `section-pack/full-section` 模式切换
- `.super-dev/changes/reuse-first/tasks.md` 对应实施任务与验收项
