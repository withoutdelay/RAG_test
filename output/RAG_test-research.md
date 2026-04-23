# Reuse-First 检索重构研究报告

更新时间：2026-04-02

## 1. 本次讨论的核心问题

当前效果不理想，核心不是“模型不会写”，而是“给模型的材料组织方式不对”。

你提出的判断基本成立：

- 原始文档被切得过碎时，短查询很容易命中若干局部词相似但语义不完整的 chunk。
- 后续再把这些碎片拼成长上下文交给大模型，生成结果就容易出现：
  - 命中不准
  - 信息密度下降
  - 章节风格漂移
  - 章节标题对不上正文素材

但“直接按整章切分 + 仅按章节名检索 + 全量丢给模型”还不够稳，它比当前更好，但不是终态。

---

## 2. 当前代码现状判断

从仓库现状看，系统已经在朝“章节优先”方向演进，但还没真正把“章节”变成第一检索单元。

### 2.1 已有的正确方向

- [backend/app/services/parsing/section_catalog.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/section_catalog.py)
  - 已经能从目录、正文标题、解析器提示中构建 `section_catalog`
  - 已支持 `normalize_section_heading`、`heading_aliases`
- [backend/app/services/parsing/case_library.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/case_library.py)
  - 已给 block 挂接 `source_section_id`
  - 已尝试把 chunk 对齐到章节路径
- [backend/app/services/retrieval/case_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/retrieval/case_service.py)
  - 已存在 `retrieve_sections`
  - 已对标题匹配、标题家族、细节词、taxonomy 做组合打分
- [backend/app/services/composition/section_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/composition/section_service.py)
  - 运行时已经是“先选 section，再取 block”的近似两阶段流程

### 2.2 还没做完的关键缺口

- [backend/app/services/vectorstore/chunker.py](/Volumes/thunder/code/RAG_test/backend/app/services/vectorstore/chunker.py)
  - 仍以固定长度 chunk 为主，默认约 `1200 chars`
  - chunk 仍是主存储/主召回实体
- `_build_reuse_query_terms`
  - 会把章节标题、purpose、industry、product_line 等混在一起
  - 对短查询来说，章节标题权重仍不够“硬”
- `section_catalog`
  - 还没有稳定沉淀为独立检索索引
  - 缺少更强的 `page_span` / `content_span` / 章节摘要 / 章节上下文文本
- 生成阶段
  - 当前仍以 block/reuse pack 为主
  - 没有明确的“整章节材料直通模式”与 token budget 策略

结论：

- 现在不是完全没有“章节检索”
- 而是“章节只是在运行时被临时利用，chunk 仍然是第一公民”

---

## 3. 外部研究结论

### 3.1 Microsoft Azure AI Search 官方文档

官方文档明确给出两点：

- 文档并不总适合被单个向量表示，含多个子主题的文档通常要按更细粒度切分。
- 对结构化文档，按句子、段落、章节等可变长度切分，通常比纯固定长度切分更合适。
- 当任务需要保留完整段落或完整语义单元时，更大的 chunk 和保留结构的 variable chunking 会更好。
- 官方还建议在自定义组合方案里，把文档标题或结构上下文补回到 chunk，以避免上下文丢失。

这与当前问题高度一致：不是不能切，而是不能把“脱离章节身份的纯文本块”当成唯一检索对象。

来源：

- https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-chunk-documents
- https://learn.microsoft.com/en-ca/azure/search/cognitive-search-skill-textsplit

### 3.2 Anthropic Contextual Retrieval

Anthropic 官方研究指出，传统 RAG 的常见问题就是 chunk 脱离了原文上下文，导致检索失败。其建议是：

- 结合 embedding + BM25，而不是只靠 embedding
- 给 chunk 补充 chunk-specific context
- 加 rerank
- 当知识库足够小且 token 预算允许时，直接把更长材料放进 prompt 反而可能更简单

这对本项目的启示是：

- 你的“把更长材料给模型”这个方向没有问题
- 但前提是这些更长材料必须先经过正确的章节级筛选，而不是直接把若干大段盲目堆进 prompt

来源：

- https://www.anthropic.com/engineering/contextual-retrieval

### 3.3 RAPTOR

RAPTOR 的核心结论是：

- 只检索短连续 chunk，会丢失文档整体理解
- 更有效的方法是建立分层结构，在不同抽象层级上检索

这和我们现在要做的事情几乎同向：

- 第一层找“最像的章节”
- 第二层再找“章节里的最佳正文块/表格/图”
- 必要时再补一个章节摘要层

来源：

- https://proceedings.iclr.cc/paper_files/paper/2024/hash/8a2acd174940dbca361a6398a4f9df91-Abstract-Conference.html

---

## 4. 对你方案的判断

### 4.1 我赞同的部分

以下判断我认为是对的：

1. 章节应成为主检索锚点，而不是纯碎片 chunk。
2. 用户输入很短时，章节标题/章节别名/章节家族比正文局部词更重要。
3. 生成阶段应尽量看到更完整的源材料，而不是只看几段摘要。

### 4.2 我不建议直接照搬的部分

如果“只按章节名检索 top n 整章，然后全丢给模型”，会有 4 个风险：

1. 标题碰撞：
   - `系统方案`
   - `技术方案`
   - `项目概述`
   这类标题太泛，只按标题名容易召回错章。
2. 章节过长：
   - 整章可能混有表格、噪声、页眉页脚、非目标子节
   - 直接全塞 prompt，容易把模型注意力摊薄
3. 细节意图丢失：
   - 用户虽然输入短，但往往仍有隐含细节，例如“总体架构”与“接口说明”并不一样
4. 成本和稳定性：
   - top n 整章直塞，token 波动会很大
   - 一旦候选章过多，生成稳定性反而下降

---

## 5. 推荐结论

推荐采用：

`章节优先 + 块级补充 + 条件式长上下文生成`

具体说法：

1. 章节是第一检索单元
2. chunk 继续保留，但降级为章节内部的子证据单元
3. 生成时不是“永远只喂 chunk”，也不是“永远整章全喂”
4. 而是根据章节命中强度和 token 预算，在以下两种模式间切换：
   - `section-pack mode`：喂章节摘要 + 精选正文块 + 表格/图
   - `full-section mode`：当命中足够强且 token 可控时，直接喂 1 到 2 个完整章节材料

---

## 6. 本轮研究后的决策

本轮建议正式确认以下方向：

1. 当前固定 chunk 路线不再作为 `reuse-first` 主检索范式
2. 主检索范式改为 `section-first hierarchical retrieval`
3. “整章直接喂模型”保留，但只作为强命中下的受控模式，不作为默认模式
4. 章节检索必须采用 hybrid 方式：
   - 标题 exact / alias / family
   - BM25
   - embedding
   - rerank
5. 生成输入必须显式保留：
   - `source_section_id`
   - `heading_path`
   - `selection_reason`
   - `token_budget`

这比“继续微调 prompt”更值得优先做。
