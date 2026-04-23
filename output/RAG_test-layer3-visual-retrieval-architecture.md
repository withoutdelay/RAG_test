# Layer 3 视觉检索支路架构补丁

更新时间：2026-04-22

## 1. 目标

把当前资产检索从单一文本排序升级为双支路排序：

- textual branch
- visual branch

同时保留：

- taxonomy / anchor / expected type 等结构化信号
- noise / risk 等惩罚层

## 2. 为什么本轮不直接上重型视觉 embedding

当前仓库已经有：

- `semantic_summary`
- `title_hint`
- `diagram_type`
- `problem_solved`
- `key_components`
- `applicable_sections`

这些字段本质上已经是面向视觉内容的语义抽象。先把它们收进独立 `visual branch`，能够在不改变数据库和向量存储结构的前提下，明显改善“图里有、正文没说透”的检索场景。

因此本轮先做：

- `visual_retrieval_text`
- `visual_query`
- `visual_score`

后续再平滑替换为真实图像 embedding backend。

## 3. 评分 contract

最终资产排序拆成四层：

1. `textual`
   - retrieval_text embedding
   - keyword overlap
   - summary overlap
2. `visual`
   - visual_retrieval_text embedding
   - title_hint / diagram_type / key_components / applicable_sections overlap
   - complete_diagram / engineering_figure bonus
3. `structural`
   - expected type
   - taxonomy
   - anchor
4. `penalty`
   - risk
   - low information
   - partial fragment
   - wrong visual focus

返回结果时输出完整 score breakdown。

## 4. 文本 contract

### 4.1 retrieval_text

继续保留面向正文线索的文本：

- heading
- title
- caption
- context_before / context_after
- semantic_summary

### 4.2 visual_retrieval_text

新增面向图意的文本：

- visual_role
- display_title / title_hint / diagram_type
- summary / problem_solved / principle_summary
- key_components
- signals_or_loops
- applicable_sections
- retrieval_keywords

## 5. 后续升级位

本轮的 `visual_retrieval_text` 是未来 image embedding backend 的兼容层。

将来可以把 `visual_score` 的来源替换成：

- ColQwen / ColPali 图像 embedding
- OCR + layout graph
- 独立 Qdrant visual collection

但 API contract 与融合方式可以保持不变。

## 6. 真视觉后端路线记录

这条路线明确保留，后续实现时不需要重新梳理方向。

### 6.1 目标形态

- 新增独立 visual collection
- 资产入库时生成 image embedding
- 查询时生成 visual query embedding
- `AssetRetrievalService` 融合 textual / visual / structural / penalty 四层得分

### 6.2 依赖判断

真视觉后端通常需要第三方模型能力，但不一定需要外部 SaaS：

- 可选本地开源模型：ColQwen / ColPali / CLIP-like vision encoders
- 运行时依赖：`torch`、`transformers` 及对应模型权重
- 推荐硬件：可用 GPU；纯 CPU 仅适合很小规模离线构建

### 6.3 实施顺序

1. 资产入库阶段抽取稳定图片文件
2. 为资产生成 visual embedding
3. 落独立 visual collection
4. 查询阶段新增 visual query embedding
5. 在现有 `visual_score` contract 下替换语义代理层

### 6.4 与当前第一版的关系

当前第一版不是终点，而是接口稳定层。

- 现在：`semantic_summary` 代理视觉语义
- 后续：真实 image embedding 进入同一 `visual_score` 位置

这样升级时不用重写上层 API 和融合逻辑。

## 7. 当前已落地的真视觉接口层

截至当前仓库状态，Layer 3 已不再把“视觉语义”硬编码在 `asset_service.py` 内部，而是拆成独立 provider：

- `backend/app/services/retrieval/visual_backend.py`
- `backend/app/services/retrieval/asset_service.py`

### 7.1 当前 provider 形态

- `proxy`
  - 默认模式
  - 继续使用 `visual_retrieval_text` 走 text embedding
  - 保证本地和测试环境稳定
- `auto`
  - 优先尝试真视觉 backend
  - 失败时自动退回 `text-proxy`
- `clip`
  - 显式要求使用本地 CLIP 类 image-text 同空间编码
  - 若依赖或模型不可用，直接报错，不做静默降级

### 7.2 当前配置项

已新增配置：

- `VISUAL_EMBEDDING_BACKEND=proxy|auto|clip`
- `VISUAL_EMBEDDING_MODEL`
- `VISUAL_EMBEDDING_LOCAL_FILES_ONLY`
- `VISUAL_EMBEDDING_DEVICE=cpu|cuda|auto`
- `VISUAL_EMBEDDING_CACHE_PATH`

对应后端配置入口：

- `backend/app/config.py`

### 7.2.1 当前离线构建脚本

已新增：

- `backend/scripts/build_visual_asset_embeddings.py`

作用：

1. 从 `figure_assets` / `raw_documents` 读取真实资产
2. 离线生成 visual embedding cache
3. 输出仓库内可复用的 JSON cache

默认 cache 目标路径：

- `backend/data/visual_index/asset_embedding_cache.json`

本轮已用当前项目做过小样本 smoke，输出物示例：

- `output/visual-embedding-cache-smoke.json`

### 7.2.2 当前自动刷新链路

离线 cache 已正式接入历史方案主刷新链路，但现在不再和方案库重编译绑成单一任务，而是拆成两个独立 pipeline：

- `case_library`
  - 负责 uploaded historical proposals 的方案库重编译
  - 同步产出 AI Wiki
- `visual_cache`
  - 负责 figure assets 的 visual embedding cache 重建

上传历史方案文档、重解析历史方案文档、删除历史方案文档时，上述两个 pipeline 会一起被请求，但状态独立推进、失败独立记录：

1. `case_library` 失败不会把 `visual_cache` 状态覆盖掉
2. `visual_cache` 失败不会把已成功完成的方案库 / AI Wiki 产物标成整体失败
3. 聚合状态会给出 `queued` / `running` / `succeeded` / `partial_failed` / `failed`

接入点位于：

- `backend/app/services/knowledge/library_refresh.py`

当前策略：

- 默认纳入 `figure` 资产
- 仅扫描 `raw_documents.doc_type == historical_proposal`
- 仅纳入 `parse_status == done` 的原始文档
- 允许 `auto/clip` 失败后保留 `text_proxy` fallback cache，保证视觉索引链路不会因单个视觉 backend 问题阻塞

### 7.2.3 当前复用缓存层

为避免历史方案刷新时每次都重新 materialize 文件并重跑 parser / asset review，仓库已新增 uploaded document projection cache：

- 上传 / 重解析文档时，直接写出 case library projection cache
- 后续刷新优先读取该 cache
- 只有 cache 缺失或损坏时，才退回 parser 路径并顺手回填 cache

当前缓存形态：

- 位置：`backend/data/knowledge_wiki/library_refresh_cache/<document_id>.json`
- 内容：`outline_entry` + `block_entries`

当前还提供独立预热脚本：

- `backend/scripts/warm_history_library_cache.py`

用途：

- 为现有历史文档一次性回填 projection cache
- 让后续刷新更快进入“直接复用已落库结果”的路径

### 7.3 当前主链可观察性

资产检索结果现在会显式回传：

- 顶层 `reason_trace`
- 顶层 `score_breakdown`
- 顶层 `search_trace`
- `metadata.visual_backend`
- `metadata.visual_source`
- `metadata.retrieval_score_breakdown.visual_backend`
- `metadata.retrieval_score_breakdown.visual_source`

也就是说，即使当前仍处于 `proxy` 模式，前端和调试链也能直接看出：

- 当前是否真的用了 image backend
- 是否发生了自动降级
- 视觉分支得分来自 `image` 还是 `text_proxy`

另外，cache 命中时现在会保持 source 语义一致：

- `image_cache` 会映射到 image query 分支
- `text_proxy_cache` 会映射到 text-proxy query 分支

这样不会再出现“命中 cache 以后反而拿错查询向量”的负向偏移。

同时，`search_trace` 现在还会显式暴露：

- 当前是否启用了 visual branch
- image / text-proxy 两个 visual collection 的命中数
- 是否退回 direct visual fallback
- 最终结果里的 source / branch 分布

### 7.3.1 当前独立 visual collection 形态

Layer 3 现在已经不再停留在“单次请求现算 visual embedding”，而是有独立 visual ANN collection：

- `presale_visual_assets_image`
- `presale_visual_assets_text_proxy`

两条 collection 的来源是同一份 visual embedding cache，但按 embedding 空间拆开：

- `image`
  - 真视觉 image embedding
- `text_proxy`
  - proxy / fallback text embedding

这样可以避免把不同维度、不同语义空间的向量硬塞进同一个 collection。

当前接入点：

- `backend/app/services/retrieval/visual_backend.py`
- `backend/scripts/build_visual_asset_embeddings.py`
- `backend/app/services/retrieval/asset_service.py`

当前策略：

1. visual cache 重建后会同步刷新独立 visual collection
2. 检索阶段先查对应 channel 的 visual collection
3. 若 collection 不可用或未命中，再回退到 direct visual fallback
4. 即使 visual collection 失效，文本检索路径也不会被拖垮

### 7.3.2 当前启用边界

视觉支路现在已经和规划对齐：

- 只在 `expected_evidence_types` / `asset_types` 包含 `figure` 时启用
- `table` / `parameter` / `formula_candidate` 检索默认走 `textual_only`

也就是说：

- 图章节会走 `textual + visual + structural`
- 表格章节不会被视觉支路误干扰

### 7.4 当前边界

这一版已经把 Layer 3 在当前仓库范围内收成可用闭环：

1. 独立 visual collection / ANN 索引：已完成
2. `AssetRetrievalService` visual branch：已完成
3. 评分融合与 figure-only 启用边界：已完成
4. refresh status / 前端调试可观察性：已完成

当前剩余的是后续增强项，而不是 Layer 3 主链阻塞项：

1. 更大规模资产集上的增量刷新与性能优化
2. 真视觉 backend 的 GPU 基线与运维文档
3. 将 CLIP 类 backend 再升级到 ColQwen / ColPali 一类重型视觉模型
