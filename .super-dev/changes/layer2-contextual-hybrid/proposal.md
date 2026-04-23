# Proposal

## Layer 2 完整版上下文化混合检索提案

本轮目标不是再补一层零散规则，而是把仓库里已经存在的 `hybrid-lite` 能力收敛成一条完整、可复用、可审计的 Layer 2 检索链路。

重点包含四件事：

- 在线文档 ingestion 不再只对裸 `chunk.content` 做 embedding，而是写入并使用 `contextual_text` / `semantic_retrieval_text`
- 在线 `Retriever` 从“dense 直出”升级为“dense shortlist + local sparse + rerank”的主路径
- 历史方案库 `CaseLibraryService` 在现有 sparse + semantic RRF 之上补上 rerank stage
- 返回结果补齐 hybrid trace，能说明 dense / sparse / rerank 三路信号各自的贡献

约束：

- 不直接把 LLM 放到在线检索打分主链路里
- 不要求本轮就把 Qdrant 改造成原生 sparse vector schema
- 保留现有 `reuse-first` 标题优先、章节优先、块内二阶段选择的既有行为
