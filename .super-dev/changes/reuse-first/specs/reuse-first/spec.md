## ADDED Requirements

### Requirement: section-truth-layer

#### Scenario 1: 解析器提取结构提示信息
- GIVEN 文档经过 docling 解析并产出标准化 markdown
- WHEN 解析器提取结构提示信息
- THEN 结果中包含 `section_catalog`

#### Scenario 2: 构建 narrative/table/figure/formula 索引记录
- GIVEN 文档结构中存在可解析的章节路径
- WHEN 构建 narrative/table/figure/formula 索引记录
- THEN 每条记录都优先解析并写入 `source_section_id`

### Requirement: section-first-retrieval

#### Scenario 1: 系统执行 `retrieve_sections`
- GIVEN 目标章节标题与历史库中的叶子节标题存在 exact 或 alias 命中
- WHEN 系统执行 `retrieve_sections`
- THEN 标题命中与标题家族信号优先于正文局部词信号

#### Scenario 2: 系统执行 `retrieve_blocks`
- GIVEN `retrieve_sections` 已返回章节 shortlist
- WHEN 系统执行 `retrieve_blocks`
- THEN 默认只在 shortlisted section 内部做正文块选择

### Requirement: controlled-assembly-and-fallback

#### Scenario 1: 组装复用上下文
- GIVEN top section 命中明显领先且章节长度在预算内
- WHEN 组装复用上下文
- THEN 系统可直接选取完整源章节正文

#### Scenario 2: 系统评估整章直送风险
- GIVEN 存在 token 超预算、标题冲突或章节噪声升高
- WHEN 系统评估整章直送风险
- THEN 系统回退为 `section_pack`

### Requirement: auditable-trace-and-review

#### Scenario 1: 返回章节生成详情
- GIVEN 某章节使用 `reuse_first` 完成检索与装配
- WHEN 返回章节生成详情
- THEN 响应中包含 `selected_sections`、`selected_blocks`、`selection_reason`、`token_budget`

#### Scenario 2: 渲染章节详情
- GIVEN 前端打开章节调试/审阅面板
- WHEN 渲染章节详情
- THEN 界面展示 query intents、section candidates、selected sections、selected blocks
