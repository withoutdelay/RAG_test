# Tasks

## 1. Planning

- [x] **1: . 补齐 `section_catalog` 数据结构，明确 `source_signals`、`content_span`、`page_span`、章节摘要与稳定路径字段**

## 2. Frontend

- [x] **2: . 重构历史方案索引构建流程，让所有 narrative/table/figure/formula block 都显式挂接 `source_section_id`**

## 3. Backend

- [x] **3: . 在章节与块索引中加入章节上下文化检索文本，避免继续使用脱离章节身份的裸 chunk**

## 4. Integration & Quality

- [x] **4: . 重构 `CaseLibraryService.retrieve_sections` 打分逻辑，提升标题 exact/alias/family 与 detail intent 权重**

## 5. Testing

- [x] **5: . 把 `CaseLibraryService.retrieve_blocks` 限定为“章节内二阶段选择”，而不是与章节候选并列竞争**

## 6. Documentation

- [x] **6: . 重构 `SectionCompositionService` 查询构造，拆分 `title_intent`、`detail_intent`、`context_intent`**

## 7. Other

- [x] **7: . 在 `SectionCompositionService` 中实现 `section-pack mode` 与 `full-section mode` 的运行时切换与 token budget 控制**

## 8. Other

- [x] **8: . 为 `reuse_first` 章节输出完整 trace，包括 `selected_sections`、`selected_blocks`、`selection_reason`、`token_budget`**

## 9. Other

- [x] **9: . 补充后端测试，覆盖章节目录构建、章节检索排序、章节内 block 选择、模式回退与 trace 输出**

## 10. Other

- [x] **10: . 最小化更新前端调试/审阅界面，使其可查看章节候选、装配包与生成 trace**
