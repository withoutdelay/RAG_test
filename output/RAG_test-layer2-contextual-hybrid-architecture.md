# Layer 2 上下文化混合检索架构补丁

更新时间：2026-04-22

## 1. 本轮改造边界

本轮的 Layer 2 指向两条检索链路一起升级，而不是只改历史方案库：

1. `CaseLibraryService`
2. 在线 `Retriever` / ingestion / Qdrant 主路径

当前仓库已经具备一部分能力：

- 历史方案库已经有 contextual retrieval text
- 历史方案库已经有 sparse + semantic 的 RRF boost
- `reuse-first` 已经完成章节优先、块内二阶段选择

当前状态已经推进到：

- 在线 ingestion 已经把 `contextual_text / contextualized_block_text / semantic_retrieval_text` 写入 `Chunk.meta`，并以 `semantic_retrieval_text` 作为 embedding 主输入
- 在线 `Retriever` 已经采用 `Dense Shortlist -> Local Sparse Score -> Local Rerank -> Hybrid Final Sort`
- 历史方案库已经返回结构化 `reason_trace / score_breakdown`
- replay governance / threshold recommendation / advisory release gate 已经接通

当前仍然保留的边界：

- 在线主链仍然是 dense shortlist + 本地 sparse/rerank，尚未切到 Qdrant 原生 sparse vectors
- 在线主链与历史方案库虽然共享 `score_breakdown` 字段族，但 online 的 `hybrid_rrf` 目前语义上表示 shortlist hybrid fusion，而不是字面意义的 Qdrant RRF
- 真实 replay history 里仍缺带 structured retrieval metrics 的健康样本，所以 recommendation 还不能产出默认阈值

## 2. 目标主链路

### 2.1 在线文档主链路

`Chunking -> Contextual Text Build -> Dense Indexing -> Dense Shortlist -> Local Sparse Score -> Local Rerank -> Hybrid Final Sort`

实施原则：

- 不改数据库主模型，只把上下文化文本落到 `Chunk.meta`
- 不要求本轮切换成 Qdrant sparse vectors
- sparse 与 rerank 都先在 dense shortlist 上本地执行

### 2.2 历史方案库主链路

`Rule Score -> Sparse / Semantic RRF -> Rerank Window -> Final Sort`

实施原则：

- 保留现有标题优先、章节优先规则
- rerank 只在排序窗口内做，不破坏原有硬规则底座
- reason trace 必须能说明规则分、RRF 和 rerank 各自做了什么
- 返回结果除拼接后的 `reason` 外，还应提供结构化 `reason_trace` 与 `score_breakdown`，至少覆盖 `base / sparse / semantic / hybrid_rrf / rerank / hybrid_rerank / final`
- structured trace 不只停留在 retrieval return object；还要继续下沉到 `selected_sections`、`selected_blocks`、project replay snapshot / markdown proof-pack，形成可回放的诊断闭环
- validation / replay gate 应消费这组 structured trace：运行时以 `VAL109` 暴露“检索信号偏弱”，离线回放通过 `evaluate_project_snapshot.py` 的 retrieval 阈值参数做 proof-pack gate
- baseline replay comparison 还应单独统计 retrieval regression / improvement，避免质量分暂时稳定时掩盖检索侧的先行退化
- 阈值不应长期手写固定值；通过 `recommend_project_replay_thresholds.py` 基于健康 replay history 产出建议值，并在历史仍为旧格式时显式提示刷新快照
- replay history 刷新也应脚本化；通过 `refresh_project_replay_history.py` 发现 runtime 中可评估项目、批量重跑 snapshot，并在无候选项目时输出显式跳过原因而不是静默失败
- release gate / proof-pack 当前先以 advisory 形式接入 replay refresh 与 threshold recommendation 摘要，不直接改变 pass/fail；等 runtime 中有可刷新项目且历史阈值样本成形后，再决定是否升级为硬门槛
- 宿主 `quality smoke` 与 `release readiness` 也应固定校验这条 advisory 证据链已经接通，避免后续重构把 refresh / threshold 两份治理输出从 release gate / proof-pack / readiness 中静默丢失
- 当 runtime 暂时没有 `draft_version > 0` 的可评估项目时，需要保留离线补样通道；通过 `import_project_replay_history.py` 接受外部 snapshot/eval JSON 或目录，导入 canonical history 后再重跑 threshold recommendation，避免阈值建设长期卡死在运行时前提上
- `import_project_replay_history.py` 还应支持对已存在的 canonical history 做 `gate_thresholds` backfill；这样旧样本即使不重新连 runtime，也能补齐 `threshold_source` / `recommended_thresholds_available` 等元数据，供后续治理报告和阈值链路消费
- `recommend_project_replay_thresholds.py` 产出的 recommendation 还应能被 `evaluate_project_snapshot.py` 按需直接消费；通过 `--use-recommended-thresholds` 在显式阈值未提供时自动加载非空推荐值，避免人工抄写 gate 参数并保留显式参数优先级
- 同一套推荐阈值开关也要贯通到批量刷新和宿主入口：`refresh_project_replay_history.py` 与 `super-dev host release-gate/project-replay-gate` 应支持透传 `--use-recommended-thresholds` / `--recommended-thresholds-json`，这样历史样本一旦成形，执行入口无需再单独拼接阈值参数

## 3. 文本 contract

每个在线 chunk 增加三段文本：

- `contextual_text`
  - 文档名
  - 行业/年份等上下文
  - section path / source heading
  - taxonomy 字段
  - chunk content
- `contextualized_block_text`
  - 面向块级复用的轻量上下文化文本
- `semantic_retrieval_text`
  - 面向 embedding / semantic / rerank 的主检索文本
- `semantic_retrieval_version`
  - 标记当前上下文化 contract 版本

约束：

- `semantic_retrieval_text` 是 embedding 主输入
- `contextual_text` 保留更完整上下文，便于稽核与 fallback
- 在线 chunk 文本现在额外显式带 `chunk_index` 段位标签，避免同节重复段落在语义上完全塌缩
- 返回结果时优先暴露 score breakdown，不直接泄露内部公式常量

## 4. Hybrid 评分 contract

在线 `Retriever` 最终得分由三路组成：

- dense score
- sparse BM25-style score
- rerank score

在线返回 contract 现已对齐为：

- `reason`
- `reason_trace`
- `score_breakdown`
  - `base`
  - `semantic`
  - `sparse`
  - `hybrid_rrf`
  - `rerank`
  - `hybrid_rerank`
  - `final`
- `search_trace`
  - `search_mode`
  - `dense_search_limit`
  - `dense_hit_count`
  - `candidate_count`
  - `ranked_count`
  - `returned_count`
  - `reranker_enabled`
  - `reranker_backend`

历史方案库最终得分由三层组成：

- 原始 rule score
- sparse / semantic RRF boost
- rerank boost

两条链路共享：

- 稀疏分规范化逻辑
- rerank 接口
- hybrid trace 结构

下游消费约束：

- evidence bundle 中的在线命中块需要保留 `retrieval_reason`、`reason_trace`、`retrieval_score_breakdown`
- `section_service` 优先消费在线 evidence item 自带的 structured trace，而不是只回退到 `recommended_use`
- validation / project snapshot 在读取 retrieval breakdown 时，对 `final <- hybrid`、`semantic <- dense` 保留兼容回退，避免旧快照失真

## 5. Rerank 实现策略

本轮不把在线 LLM 放进打分闭环。

rerank 采用双层策略：

1. 优先使用本地 cross-encoder（如果环境已具备）
2. 否则回退到稳定的 heuristic reranker

这样可以满足：

- 离线可运行
- 测试可稳定复现
- 后续可平滑升级为更强 reranker

## 6. 风险控制

- dense shortlist 数量放大，但最终返回仍受 `top_k` 限制
- rerank 只作用于 shortlist，避免全量代价失控
- case library 与 online retriever 共用 contract，但不强行做同一套候选来源

## 7. 执行约束

当前分支允许用真实方案库暴露 Layer 2 问题，但当前执行方式必须满足下面的边界：

- 当前 `section_summary` / `section_catalog` / `section_retrieval_text` 清洗，统一视为 `Layer 2 / Step 2a` 的入库质量加固，而不是独立长期主线
- 真实样本只用于发现问题、构造回归和验证泛化；运行时代码禁止按 `file_name`、客户名、项目名、`sample_id` 做分支或特判
- 新增规则必须落在通用 contract 上，例如章节真值、摘要清洗、上下文化文本、hybrid trace；不能把样本文档中的完整标题句或客户专有文本直接写成运行时白名单/黑名单
- 每次针对真实样本发现的问题，都必须至少补一条可复现的回归测试；如果问题无法抽象为跨文档模式，只记录到解析审计或待观察列表，不进入主链运行时逻辑
- `Step 2a` 的停止条件是：
  - 代表性真实样本中至少 5 份文档不存在明显章节树错挂、摘要前缀污染或跨段粘连问题
  - 全量回归持续通过
  - 运行时代码中不存在文档名级特判
- 一旦达到上述停止条件，后续优先级立即切回 `Step 2b` 和 `Step 2c`，继续推进 hybrid retrieval 与 rerank 主链，而不是无限细抠单个样本文档

## 8. 验收标准

- 新入库 chunk metadata 中存在上下文化检索文本
- 在线 `Retriever` 返回结果中存在顶层 `reason / reason_trace / score_breakdown / search_trace`
- 在线 evidence item 与 section selection trace 中存在 `retrieval_reason_trace / retrieval_score_breakdown`
- `CaseLibraryService` 返回结果 reason 中存在 rerank trace
- Layer 2 相关测试通过
