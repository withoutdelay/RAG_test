# Proposal

## Layer 3 视觉检索支路提案

本轮目标是在不引入重型多模态基础设施的前提下，把当前资产检索从“只有文本检索”升级为“文本支路 + 视觉支路”的双通道排序。

当前资产检索已经具备：

- 标题 / caption / heading / context 的文本检索
- `semantic_summary` 生成与质量筛选
- taxonomy / anchor / noise penalty 规则层

但还缺两个关键点：

- `semantic_summary`、`title_hint`、`diagram_type` 等视觉描述没有作为独立支路参与排序
- API 返回里没有清晰的 textual / visual / structural / penalty breakdown

本轮实现策略：

- 新增 `visual_retrieval_text`
- 新增 `visual_query`
- 在 `AssetRetrievalService` 内增加独立 `visual_score`
- 把结果 breakdown 写回 metadata

约束：

- 不要求本轮上 ColQwen / ColPali / 多向量 Qdrant collection
- 设计上保留未来替换为真实 image embedding backend 的接口位置
