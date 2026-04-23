## ADDED Requirements

### Requirement: compiled-knowledge-assets

#### Scenario 1: 从 case library 编译术语表
- GIVEN case library 中已存在 `term_lexicon`
- WHEN 系统执行知识编译
- THEN wiki 中生成稳定的术语表页面和结构化词表清单

#### Scenario 2: 从 case library 编译设备卡与接口卡
- GIVEN block library 中已存在 `equipment_type` 与 `section_type`
- WHEN 系统执行知识编译
- THEN wiki 中生成设备卡和接口卡，并附带 representative headings 与 source examples

### Requirement: wiki-index-and-log

#### Scenario 1: 生成 wiki index
- GIVEN 系统已编译多类知识页
- WHEN 输出 wiki
- THEN 目录中包含 `index.md` 和 `manifest.json`

#### Scenario 2: 记录编译日志
- GIVEN 系统执行一次知识编译
- WHEN 输出 wiki
- THEN 目录中包含 append-friendly `log.md`

### Requirement: compiled-knowledge-context-consumption

#### Scenario 1: 章节写作时注入 AI Wiki 编译知识
- GIVEN `backend/data/knowledge_wiki` 中已存在 glossary / section_templates / forbidden_phrases 等结构化产物
- WHEN `SectionDraftService` 生成或重生成章节
- THEN 系统应把与当前章节相关的术语别名、章节骨架和禁用表述拼接进写作上下文

#### Scenario 2: 缺失 AI Wiki 产物时平稳降级
- GIVEN 本地尚未生成 `knowledge_wiki` 产物，或产物不完整
- WHEN `SectionDraftService` 生成章节
- THEN 系统仍应继续走原有写作流程，而不是抛出运行时异常

### Requirement: compiled-knowledge-quality-review

#### Scenario 1: 质检阶段回扫禁用表述
- GIVEN 当前章节正文中出现 AI Wiki 禁用表述
- WHEN `SectionQualityGateService` 执行 review/review_and_repair
- THEN 系统应产出对应质量问题，并在 rewrite 上下文中提供替代表达

#### Scenario 2: 质检阶段回扫术语混用
- GIVEN 当前章节正文中对同一对象同时使用多个 glossary 别名
- WHEN `SectionQualityGateService` 执行质量审查
- THEN 系统应指出术语不统一，并给出推荐主称谓

### Requirement: compiled-knowledge-retrieval-expansion

#### Scenario 1: reuse retrieval 使用 glossary 做 query expansion
- GIVEN 当前章节命中了 AI Wiki glossary 中的术语组
- WHEN `SectionDraftService` 检索 case library reusable blocks
- THEN 系统应把 glossary 主称谓及别名作为附加 query terms 参与检索和复用打分

#### Scenario 2: reuse rerank 使用产品族卡和模块卡做轻量先验
- GIVEN 当前章节与某个产品族卡 / 模块卡存在显著匹配
- WHEN `SectionDraftService` 对 evidence blocks 和 case library blocks 做二次筛选与重排
- THEN 系统应把产品族卡 / 模块卡命中、章节类型一致性和设备类型一致性作为轻量加分特征参与排序

#### Scenario 3: trace 保留 AI Wiki retrieval prior 证据
- GIVEN 当前章节命中了 glossary / product_cards / module_cards
- WHEN `SectionDraftService` 输出 case library retrieval trace
- THEN trace 中应保留 `knowledge_wiki_terms`、`knowledge_wiki_product_cards` 和 `knowledge_wiki_module_cards`

#### Scenario 4: trace 暴露 AI Wiki prior score breakdown
- GIVEN 某个 reusable block 因 AI Wiki 产品族卡 / 模块卡先验获得额外加分
- WHEN `SectionDraftService` 输出 selected prompt blocks 与 generation summary
- THEN 系统应显式记录 prior total boost、分项 boost 和命中块数，便于后续权重校准

#### Scenario 5: holdout_eval 对比开关 prior
- GIVEN 当前仓库存在 `holdout_eval` 文档和 `pilot_main` case library
- WHEN 运维侧运行 Layer 4 prior eval 脚本
- THEN 系统应在固定 `pilot_main` shortlist 前提下，对比 `without_prior` 与 `with_prior` 两种模式的 reusable block 排序结果，并输出 markdown/json 报告

### Requirement: compiled-knowledge-ingestion-refresh

#### Scenario 1: 导入历史方案后自动刷新 case library 与 AI Wiki
- GIVEN 用户上传或重解析 `historical_proposal` 类型文档
- WHEN 文档完成解析并提交
- THEN 系统应在后台触发 case library 重建，并基于新的 case library 重新编译 AI Wiki

#### Scenario 2: 删除历史方案后移除对应编译产物
- GIVEN 某个 `historical_proposal` 文档已参与过 case library / AI Wiki 编译
- WHEN 用户删除该文档
- THEN 后续后台刷新应先移除旧的 uploaded_documents 条目，再按剩余历史方案重新编译，避免脏条目残留

#### Scenario 3: RFP 文档不污染历史方案知识层
- GIVEN 用户上传或重解析 `rfp` 类型文档
- WHEN 文档完成解析
- THEN 系统不应触发历史方案 case library / AI Wiki 刷新

#### Scenario 4: 用户可见历史方案知识层刷新状态
- GIVEN 系统已触发历史方案 case library / AI Wiki 后台刷新
- WHEN 前端或运维侧读取刷新状态
- THEN 系统应返回最近一次刷新任务的状态、时间戳和统计信息，便于界面展示和问题定位

### Requirement: compiled-knowledge-product-and-module-cards

#### Scenario 1: 从历史方案编译产品族卡
- GIVEN case library 中存在多份属于相近方案族的历史方案文档
- WHEN 系统执行 AI Wiki 编译
- THEN 系统应按方案族/产品族输出稳定的 `product_cards` 结构化资产和对应知识页

#### Scenario 2: 从 block library 编译模块卡
- GIVEN 历史方案 block 中反复出现功率单元、控制柜、变压器柜等模块实体
- WHEN 系统执行 AI Wiki 编译
- THEN 系统应输出 `module_cards` 结构化资产和对应模块知识页

#### Scenario 3: 章节写作消费产品族卡与模块卡
- GIVEN 当前章节查询文本与某个产品族卡或模块卡存在显著匹配
- WHEN `KnowledgeWikiContextProvider` 构建写作上下文
- THEN 系统应把对应的产品族卡 / 模块卡摘要注入提示上下文，帮助模型稳定用词和结构
