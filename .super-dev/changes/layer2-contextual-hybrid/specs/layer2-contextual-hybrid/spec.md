## ADDED Requirements

### Requirement: contextualized-online-ingestion

#### Scenario 1: 在线文档写入上下文化检索文本
- GIVEN 文档被解析并切分为 chunks
- WHEN 系统执行 indexing
- THEN 每个可索引 chunk 都写入 `contextual_text`、`contextualized_block_text`、`semantic_retrieval_text`

#### Scenario 2: 在线向量索引使用上下文化文本
- GIVEN chunk 已生成 `semantic_retrieval_text`
- WHEN 系统执行 embedding
- THEN 向量索引使用上下文化文本而不是裸 `chunk.content`

### Requirement: hybrid-retriever-main-path

#### Scenario 1: 在线检索执行 hybrid 排序
- GIVEN Qdrant 已返回 dense shortlist
- WHEN 系统执行 `Retriever.search`
- THEN 系统在 shortlist 上继续计算 sparse score 与 rerank score，并输出最终 hybrid score

#### Scenario 2: 返回 hybrid trace
- GIVEN 某个 chunk 被返回为检索结果
- WHEN API 返回 `RetrievalResult`
- THEN 结果 metadata 中包含 dense / sparse / rerank / hybrid score breakdown

### Requirement: case-library-rerank-stage

#### Scenario 1: 历史方案库二阶段重排
- GIVEN `CaseLibraryService` 已完成规则分与 sparse/semantic RRF 融合
- WHEN 候选进入最终排序窗口
- THEN 系统继续执行 rerank，并对 top candidates 增加可解释的 rerank boost

#### Scenario 2: 历史方案库返回 rerank trace
- GIVEN 某个章节或块因 rerank 获得加分
- WHEN 结果被返回
- THEN `reason` 中包含 `hybrid_rerank_score` 与 `hybrid_rerank_boost`
