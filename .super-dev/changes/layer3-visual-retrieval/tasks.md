# Tasks

## 1. Architecture

- [x] 1. 补齐 Layer 3 架构附录，明确 textual branch / visual branch / structural signals / penalties 的融合 contract

## 2. Backend

- [x] 2. 为资产卡片补充 `visual_retrieval_text`
- [x] 3. 为 `AssetRetrievalService` 增加独立视觉 query 与视觉评分支路
- [x] 4. 在 asset search 结果 metadata 中返回 score breakdown，便于调参与审计

## 3. Testing

- [x] 5. 补充单测，覆盖视觉文本构造、视觉支路加分、结果 breakdown

## 4. Verification

- [x] 6. 跑通 Layer 3 相关检索测试
