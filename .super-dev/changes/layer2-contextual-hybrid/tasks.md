# Tasks

## 1. Architecture

- [x] 1. 补齐 Layer 2 架构附录，明确 ingestion / retriever / case library 三条链路的上下文化文本与 hybrid score contract

## 2. Backend

- [x] 2. 提取共享 hybrid scoring / rerank 组件，避免在线检索与历史方案库各写一套公式
- [x] 3. 为 `CaseLibraryService` 增加 rerank stage，并把原因链补进 trace
- [x] 4. 为在线 ingestion 写入 `contextual_text`、`contextualized_block_text`、`semantic_retrieval_text`，并使用上下文化文本做 embedding
- [x] 5. 把 `Retriever` 改为 dense shortlist + local sparse + rerank 的混合排序主路径

## 3. Testing

- [x] 6. 补充单测，覆盖 chunk contextual text、case library rerank、在线 hybrid 排序

## 4. Verification

- [x] 7. 跑通 Layer 2 相关后端测试并记录结果
