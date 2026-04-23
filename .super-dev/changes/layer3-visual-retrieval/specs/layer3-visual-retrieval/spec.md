## ADDED Requirements

### Requirement: visual-retrieval-text-contract

#### Scenario 1: 资产卡片构建视觉检索文本
- GIVEN 图资产已具备 `semantic_summary`、`title_hint` 或 `diagram_type`
- WHEN 系统构建 asset card
- THEN asset card 中包含单独的 `visual_retrieval_text`

### Requirement: dual-branch-asset-ranking

#### Scenario 1: 视觉支路参与排序
- GIVEN 系统执行资产检索
- WHEN 某图资产的文字较少但视觉摘要完整
- THEN 视觉支路可为其提供独立加分

#### Scenario 2: 返回 score breakdown
- GIVEN 系统返回资产检索结果
- WHEN 前端或审阅链路读取 metadata
- THEN metadata 中包含 textual / visual / structural / penalty / final breakdown
