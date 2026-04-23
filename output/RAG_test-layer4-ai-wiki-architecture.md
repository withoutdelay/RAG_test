# Layer 4 AI Wiki 知识编译层架构补丁

更新时间：2026-04-23

## 1. 定位

Layer 4 的目标不是让模型每次临时从原始文档里重新“发现知识”，而是把现有历史方案库持续编译成一层稳定的知识资产。

这和 Karpathy 提出的 LLM Wiki 思路一致：

- raw sources 保持不变
- 中间知识层持续积累
- 后续问答和写作优先消费中间层

但本项目的第一版不做通用 wiki，而做领域结构化变体：

- 术语表
- 产品族卡
- 模块卡
- 设备卡
- 接口卡
- 章节模板
- 禁用表述

## 2. 输入与输出

### 2.1 输入

- `backend/data/case_library/outline_library.json`
- `backend/data/case_library/block_library.json`

### 2.2 输出

- `backend/data/knowledge_wiki/index.md`
- `backend/data/knowledge_wiki/log.md`
- `backend/data/knowledge_wiki/manifest.json`
- `backend/data/knowledge_wiki/glossary.md`
- `backend/data/knowledge_wiki/products/*.md`
- `backend/data/knowledge_wiki/modules/*.md`
- `backend/data/knowledge_wiki/equipment/*.md`
- `backend/data/knowledge_wiki/interfaces/*.md`
- `backend/data/knowledge_wiki/templates/*.md`
- `backend/data/knowledge_wiki/policies/*.md`

## 3. 编译 contract

### 3.1 术语表

来源：

- case library materialized `term_lexicon`
- 已归一化的 heading / summary 别名关系

产物：

- 统一别名
- 推荐主叫法
- 典型出现位置

### 3.2 设备卡

来源：

- `equipment_type`
- 相关 block headings / section summaries / sample docs

产物：

- 设备定位
- 高频章节类型
- 代表性标题
- 示例来源文档

### 3.3 产品族卡

来源：

- outline library 中的 `document_title / top_level_titles / profile`
- 与该文档关联的 block library `equipment_type / section_type`

产物：

- 方案族/产品族标签
- 高频设备类型
- 高频章节类型
- 代表性方案标题
- 代表性来源文档

### 3.4 模块卡

来源：

- block library 中反复出现的模块/柜体/核心器件关键词
- 关联的章节标题、设备类型和代表性内容片段

产物：

- 模块名称与别名
- 高频设备类型
- 高频章节类型
- 典型章节标题
- 代表性内容片段

### 3.5 接口卡

来源：

- `section_type in {communication_interface, protection_interlock, control_logic}`

产物：

- 接口主题
- 典型信号/联锁上下文
- 代表性章节标题

### 3.6 章节模板

来源：

- 高频 `section_type`
- 高频 `normalized_heading`

产物：

- 常见标题骨架
- 代表性来源
- 推荐适用场景

### 3.7 禁用表述

来源：

- 现有写作规范
- 售前客户稿应避免的营销口径

产物：

- 禁用表达
- 替代表达原则

## 4. 为什么第一版先做本地文件 wiki

原因很直接：

- 当前规模小，没必要先上 DB schema 和复杂搜索
- markdown + json 既方便人读，也方便 LLM 和脚本消费
- 后续不论走产品库化，还是更完整的 wiki / search / graph，都能平滑演进

## 5. 后续演进

第一版完成后，下一步可以往两条线扩展：

1. 把设备卡/接口卡进一步结构化为产品目录实体
2. 把已确认的章节结果回写为“已验证知识页”

## 6. 主链路消费方式

编译层落地后，不能只停留在离线文件输出，还需要进入正式写作链路。

当前接入方式：

- `SectionDraftService` 在章节生成前读取 `backend/data/knowledge_wiki/*.json`
- 根据当前章节 `title / purpose / keywords / section_type` 匹配相关术语别名
- 匹配相关产品族卡和模块卡，补充方案族边界与关键部件知识
- 按 `section_type` 提取对应章节模板 guidance
- 注入禁用表述约束，压制“我公司 / 国内领先 / 绝对满足”一类营销口径
- 将上述内容与前序章节去重上下文一起送入 writer prompt

这样 Layer 4 的职责就明确了：

- Layer 1/2/3 负责“找哪些原始材料”
- Layer 4 负责“给模型稳定术语、结构骨架和写作口径约束”

后续如果升级到更强版本，可以继续增加两类消费方式：

1. 在 retrieval/rerank 阶段把 AI Wiki 条目作为轻量先验特征
2. 在 final review 阶段对生成稿做术语一致性和禁用表述回扫

## 7. 质检链路消费方式

Layer 4 进入主写作链路后，第二个闭环是进入质检链路。

当前接入方式：

- `SectionQualityGateService` 支持显式注入 `KnowledgeWikiContextProvider`
- 规则层回扫两类问题：禁用表述、同义词混用
- LLM 质检 prompt 额外拿到 AI Wiki 质检约束，避免只靠规则层兜底
- rewrite 阶段也携带同样的术语和口径约束，保证修复动作和编译知识一致

这样 Layer 4 在运行期就形成双闭环：

1. 写前约束：提示模型优先使用稳定术语和章节骨架
2. 写后纠偏：回扫禁用表述和术语漂移，并在 rewrite 中纠正

## 8. 检索链路消费方式

在写前约束和写后纠偏之外，Layer 4 还开始进入 reuse retrieval 链路。

当前做法：

- 先根据当前章节匹配 glossary 中的相关术语组
- 同时匹配相关产品族卡和模块卡，形成 retrieval prior bundle
- 将主称谓和别名作为附加 query terms 注入 reuse retrieval
- evidence blocks 与 case library blocks 的二次打分额外吃到产品族卡 / 模块卡先验
- 先验特征当前包含：产品族命中、模块命中、产品族高频章节类型一致、产品族高频设备类型一致
- trace 中保留 `knowledge_wiki_terms`、`knowledge_wiki_product_cards` 和 `knowledge_wiki_module_cards`，方便观察 AI Wiki 对命中的影响
- selected prompt blocks 额外保留 `selection_score_breakdown`，其中显式记录 `knowledge_wiki_prior_total` 与各分项 boost
- 章节 generation summary 额外汇总 prior 命中块数、命中章节数与累计 boost，便于后续调权重和做 A/B 对比

为了避免只在在线 trace 里“局部观察”，当前还补了一条离线 holdout_eval 路径：

- 使用 `holdout_eval` 文档章节作为查询侧
- 固定当前 `pilot_main` case shortlist，不改线上主链路召回范围
- 比较 `without_prior` 与 `with_prior` 两种模式下 reusable block 的 Top1/Top3 命中表现
- 首轮评测优先使用 `--fast-heuristic-only`，关闭 dense semantic scorer / reranker，并让 DOCX 走 XML 轻解析，先判断 prior 是否真的改变排序
- 输出 markdown/json 报告，便于后续调节 0.03 / 0.04 这类 prior boost 权重

这一层仍然保持保守：

- 不直接改底层 case library 主排序器
- 不单独给 AI Wiki 建索引
- 先作为轻量 query expansion 先验，验证收益后再考虑更深集成

## 9. 导入链路消费方式

如果 AI Wiki 只靠离线脚本手工重编译，它就无法跟真实历史方案库持续同步，所以 Layer 4 还需要进入导入链路。

当前做法：

- `documents/upload`、`documents/reparse`、`documents/delete` 在处理 `historical_proposal` 时，都会调度后台刷新
- 刷新任务先读取现有 case library，并过滤旧的 `source=uploaded_documents` 条目
- 再从数据库中拉取所有 `parse_status=done` 的历史方案文档，优先读取 `library_refresh_cache/<document_id>.json` 中的 projection cache，只有 miss 时才物化、轻解析并回填 cache
- 新的 case library 写回 `backend/data/case_library/*.json` 后，立刻重新编译 `backend/data/knowledge_wiki/*`
- 图资产侧同时独立重建 `backend/data/visual_index/asset_embedding_cache.json`
- `rfp` 文档只进入原有解析/索引链路，不参与历史方案知识层刷新
- 刷新过程同时写入 `backend/data/knowledge_wiki/refresh_status.json`，前端可通过只读状态接口展示运行中 / 成功 / 失败 / partial_failed 状态
- 状态文件显式区分 `case_library` 与 `visual_cache` 两条子流水线，并记录 `requested_at / started_at / finished_at / duration_seconds`
- `stats` 中额外落盘 `cache_hit_uploaded_documents / cache_miss_uploaded_documents`，用于判断当前回刷是否真正走到了 projection cache 命中路径

这样当前系统已经具备一条可运行的“真实历史方案 -> case library -> AI Wiki”自动增量回刷通道。

但这里有一个边界要明确：

- 现在的刷新是进程内后台任务，不是持久任务队列
- 如果服务在刷新过程中重启，本次刷新会中断，需要依赖下一次导入/重解析/删除再次触发

后续如果历史文档规模继续增大，建议升级为独立 job queue / worker，把刷新状态、失败重试和进度可视化也补齐。

## 10. 轻量刷新现状

截至 2026-04-20，导入链路已经补齐三层轻量化能力：

1. projection cache
   - 上传/重解析完成后，立即把 outline/block projection 写入 `backend/data/knowledge_wiki/library_refresh_cache/*.json`
   - 后续历史库刷新优先读取这层 cache，不再重复走完整解析链路
2. fallback backfill
   - 对 cache miss 的旧文档，刷新任务只走 `include_asset_enrichment=False` 的轻解析路径
   - 不再在 case library backfill 阶段等待图资产审校或视觉摘要
3. refresh observability
   - `refresh_status.json` 记录两条子流水线的独立耗时
   - 前端文档页直接展示 pipeline 状态、projection cache hit/miss 和 visual cache 规模

这意味着当前 AI Wiki 已经不是“每次全量重跑”的黑箱，而是一个可观察、可回刷、可逐步提速的知识编译层。

## 11. 当前实现状态（2026-04-23）

截至当前分支，Layer 4 在仓库范围内已经完成一轮收口，具体包括：

- 编译层
  - `compile_knowledge_wiki()` 持续从 case library 产出 glossary / product / module / equipment / interface / template / policy 页面与 `manifest.json`
- 运行时消费
  - `SectionDraftService` 在写前注入 AI Wiki 术语、章节骨架、产品族卡、模块卡和禁用口径
  - `SectionQualityGateService` 在写后回扫禁用表述与术语混用，并把同样约束带入 rewrite
  - reuse retrieval 额外引入 AI Wiki prior bundle，对 query expansion 和 block 二次打分提供轻量先验
- 评估闭环
  - `evaluate_knowledge_wiki_priors.py` 已形成固定 holdout_eval 路径，持续比较 `without_prior` 与 `with_prior`
  - 评测结果稳定落盘到 `output/RAG_test-layer4-ai-wiki-prior-eval.md/.json`
- 导入闭环
  - 历史方案上传 / 重解析 / 删除会自动触发 case library + AI Wiki + visual cache 回刷
  - projection cache / fallback backfill / refresh status 三层轻量化路径已接通
- 治理闭环
  - AI Wiki prior holdout eval 已进入 `release-gate` / `proof-pack` / `release-readiness` 的 governance advisory 链
  - 当前宿主侧会稳定暴露 `AI Wiki Prior Evaluation` artifact 与 `Governance: AI Wiki Prior Evaluation` check

因此，Layer 4 当前不再只是“离线脚本可运行”，而是已经形成：

1. 编译
2. 运行时消费
3. holdout 评估
4. 导入回刷
5. 交付治理

这五段闭环。

当前仍保留为后续增强项的内容：

- 把已确认的优质章节反向写回更结构化的 verified wiki page
- 为 AI Wiki prior 引入更细的权重分层与自动调参
- 当页面规模继续扩大后，再考虑独立索引或更强实体层
