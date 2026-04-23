# 售前 Copilot 下一阶段迭代优化方案

> 更新日期：2026-04-19
> 基于：当前 MVP 已完成状态 + ChatGPT Pro 外部 review + 独立代码分析
> 前置文档：`output/reuse-first-quality-next-plan.md`（本计划的前序迭代，已全部完成）

---

## 当前系统画像

当前 MVP 已经完成 `retrieve → outline → generate-sections → validate → export` 端到端链路，LCI Demo 结果：

| 指标 | 数值 |
|------|------|
| 章节数 | 8 |
| effective_path | 100% `extractive_reuse_llm_finalize` |
| fallback_rate | 0.0 |
| validation errors | 0 |
| validation warnings | 0 |
| 项目状态 | EXPORTED |
| 测试通过 | 247 |

**这不是一个需要推倒重来的系统，而是一个骨架正确、需要把底座补硬的系统。**

---

## ChatGPT Pro 的核心判断 vs 我的独立判断

### 一致的判断（两边都认为重要）

| 方向 | ChatGPT Pro 的措辞 | 我的验证 |
|------|-------------------|---------|
| 章节真值层不够硬 | parser fallback 时 `structure={}` | ✅ 正确。`docling_parser.py:153` fallback 路径确实输出空结构 |
| 检索层偏"规则重排" | 核心还是启发式打分 | ✅ 正确。`_score_reuse_candidate` 和 `CaseLibraryService` 全部是关键词/启发式，零语义 |
| section_catalog 是关键 | 设计稿已定义好 schema | ✅ 正确。`section_catalog.py` 和 `case_library.py` 已部分实现，`case_service.py:299` 已优先读取 |
| 不应押注 GraphRAG | 太贵且不是当前主线 | ✅ 同意。当前阶段用结构化规则+混合检索更实际 |
| 不应直接整合 RAG-Anything | 框架层竞争 | ✅ 同意。现有 composition 主链路已成熟，换框架成本不合理 |

### ChatGPT Pro 高估或遗漏的地方

| 问题 | 我的判断 |
|------|---------|
| **高估 parser fallback 严重性** | 当前 13 份真实文档全部通过 Docling 主路径入库，fallback 其实没被触发。真正问题不在"fallback 会不会跑"，而在"Docling 主路径输出的 heading 质量还不够稳"——这两个是不同问题 |
| **高估 LongRefiner 的实用价值** | 你的章节生成是"先组装 → 再 LLM finalize"，一个章节的 evidence pack 通常 3-8 个 block（约 3000-8000 字）。这不是 long-context RAG 场景。LongRefiner 解决的是"上下文太长需要压缩"，但你的瓶颈是"找不到/找错了"，不是"找到了太多" |
| **遗漏了同义词/术语扩展** | 这是当前系统最大的"零成本快速收益项"。`_tokenize_reuse_text` 做精确 token 匹配，"变频器"≠"VFD"，"通讯"≠"通信"，"PLC"≠"可编程逻辑控制器"。ChatGPT Pro 整篇没提这个 |
| **遗漏了 `block_taxonomy.py` 的领域知识瓶颈** | 19 种 section_type + 12 种 equipment_type 目前只覆盖高压变频器/软起动子领域。扩展到完整电气领域（配电/继保/电缆/仪表等）需要大量补充，但 ChatGPT Pro 没有意识到这个局限性 |
| **视觉检索优先级偏高** | ColPali/ColQwen 很有前景，但在当前 13 份文档的规模下，文本检索 + `semantic_summary` 已经能找对图。视觉检索应排在混合文本检索之后 |

---

## 推荐迭代路线图

### 总体优先级

```
Layer 0（速赢）: 同义词/术语扩展                    ← 半天
Layer 1（P0）  : 章节真值层强化                     ← 1-2 周
Layer 2（P0）  : Contextual Hybrid Retrieval        ← 2-3 周
Layer 3（P1）  : 视觉检索支路                       ← 1-2 周
Layer 4（P2）  : AI Wiki 知识编译层                  ← 持续
```

---

### Layer 0：同义词/术语扩展（速赢，半天）

> ChatGPT Pro **未提及**。这是我在上一轮 review 中独立发现的最大快速收益项。

#### 问题

当前 `_tokenize_reuse_text()` 和 `_build_reuse_query_terms()` 做精确 token 匹配：

```python
overlap_count = len(query_set & (heading_terms | content_terms))
```

"变频器"和"VFD"互相不认识，"通讯接口"和"通信接口"互相不认识。

#### 改动

| 文件 | 改动 |
|------|------|
| `backend/app/services/composition/section_service.py` | `_build_reuse_query_terms` 加同义词扩展 |
| `backend/app/services/retrieval/case_service.py` | `_tokenize` 加同义词扩展 |
| 新增: `backend/app/services/domain/synonyms.py` | 电气领域同义词表（30-50 组） |

```python
# 示例同义词表
DOMAIN_SYNONYMS = {
    "变频器": {"VFD", "变频柜", "变频装置", "频率转换器"},
    "PLC": {"可编程控制器", "可编程逻辑控制器"},
    "DCS": {"集散控制系统", "分布式控制系统"},
    "HMI": {"人机界面", "触摸屏", "操作面板"},
    "通讯": {"通信"},
    "制动单元": {"回馈单元", "制动电阻", "能耗制动"},
    "PT100": {"温度传感器", "热电阻"},
    "整体方案": {"总体设计", "总体方案", "系统方案"},
    "软起动": {"软启动", "软起"},
    "LCI": {"变频软起动", "晶闸管换相"},
    # ...
}
```

#### 验收

- "变频器系统方案" 查询能匹配到包含 "VFD 整体设计" 的文档块
- 不引入误匹配（同义词表人工审核）

#### 同时补充 `block_taxonomy.py`

趁这个机会，把当前 19 种 `section_type` 和 12 种 `equipment_type` 做一轮扫描，补充你已有文档中实际出现但未覆盖的类型。不需要一次做到"全电气领域"，只需覆盖你当前 13 份真实文档的实际章节分布。

---

### Layer 1：章节真值层强化（P0，1-2 周）

> ChatGPT Pro 和你自己的设计稿 `section_truth_and_heading_retrieval_plan.md` 对此判断一致。

#### 当前状态

- `section_catalog.py` 已存在，`build_section_catalog()` 已实现
- `case_library.py` 已优先读取 `section_catalog`（L299: `outline_entry.get("section_catalog") or outline_entry.get("flat_outline")`）
- 但 `docling_parser.py` 的输出**没有显式产出 section_catalog**
- 正文中的"第三章 / 1.2 / 一、"等章节标记仍然主要埋在 chunk 正文中

#### 应做

| Phase | 内容 | 文件 |
|-------|------|------|
| 1a | Docling 解析后自动产出 `section_catalog` | `docling_parser.py` |
| 1b | 对"第X章 / X.X / 一、"等正文章节标记做提升 | `section_catalog.py` |
| 1c | chunks 和 figure_assets 强制带 `source_section_id` | `docling_parser.py`, 入库链路 |
| 1d | `parser_backend_used=fallback` 的文档标记为 `parse_insufficient`，不进入 case library | `case_library.py` |
| 1e | case library 重建时只吃通过 parse gate 的文档 | 重建脚本 |

#### Docling vs MinerU A/B

ChatGPT Pro 建议做 A/B 对比。这是合理的，但**不需要是当前阻断项**。建议：

1. 先用 Docling 把当前 13 份文档的 `section_catalog` 跑出来并人工抽检
2. 如果 5 份以上文档的章节抽取质量不达标（顶层标题召回 < 90%），再引入 MinerU 做对比
3. 不要一开始就维护两套 parser

#### 验收

- `pilot_main` 顶层标题召回 >= 95%
- 关键二级章节召回 >= 85%
- case library 中每个 block 都可追溯到 `source_section_id`

---

### Layer 2：Contextual Hybrid Retrieval（P0，2-3 周）

> ChatGPT Pro 的核心推荐。我同意这是收益最大的架构升级，但实现路径需要更具体。

#### 当前实现状态（2026-04-22）

- `Step 2a` 已完成：
  - 在线 ingestion 已写入 `contextual_text / contextualized_block_text / semantic_retrieval_text`
  - `semantic_retrieval_text` 已作为在线 embedding 主输入
  - 历史方案库也已对齐 contextual retrieval text contract
- `Step 2b` 已完成当前阶段目标：
  - 在线 `Retriever` 已从 dense-only 升级为 `Dense shortlist + local sparse shortlist + hybrid final sort`
  - 历史方案库已具备 sparse + semantic hybrid boost
  - 当前在线 sparse 仍是本地 shortlist 融合，而不是 Qdrant 原生 sparse vector；这不再阻塞 Layer 2 主线验收
- `Step 2c` 已完成当前阶段目标：
  - 在线与历史方案库都已接入 rerank
  - 两条链路都返回 `reason / reason_trace / score_breakdown`
  - structured trace 已下沉到 evidence bundle、section selection、validation、project replay snapshot 和 proof-pack advisory

当前仍保留的后续项：

- 若后续语料规模继续扩大，可把在线 sparse shortlist 从本地实现升级为 Qdrant 原生 sparse vectors
- replay threshold recommendation 还需要更多带 structured retrieval metrics 的 healthy snapshots 才能产出默认阈值

#### 当前问题回顾

- `CaseLibraryService` 全部是关键词匹配（`_tokenize` -> 词频命中）
- `_score_reuse_candidate` 是 20+ 维度手写评分器，但每个信号都是精确字符串匹配
- 没有语义理解能力：同义词不认识、意图不理解

#### 分步实施

**Step 2a：Contextual Chunk（入库阶段增强）**

Anthropic 的 Contextual Retrieval 核心思想：在入库时给每个 chunk 补一段"这个 chunk 在整篇文档里的位置和意义"的短上下文。

| 文件 | 改动 |
|------|------|
| `chunks` 表 | 新增 `contextual_text` 字段 |
| 入库链路 | 为每个 chunk 生成 contextual text（可用 LLM 或规则） |
| Qdrant 索引 | 用 `contextual_text + raw_content` 做 embedding |

对你的场景，contextual text 不一定需要 LLM 生成。用规则也能做到 80% 效果：

```python
contextual_text = f"文档《{doc_title}》中，章节《{section_heading}》的第{chunk_index}段。"
```

**Step 2b：BM25 + Dense Hybrid Search**

| 组件 | 当前 | 目标 |
|------|------|------|
| 文本检索 | 关键词命中计数 | Qdrant sparse vector (BM25) |
| 向量检索 | 已有 Qdrant dense | 保留，加 contextual embedding |
| 融合 | 无 | Reciprocal Rank Fusion (RRF) |

Qdrant 已有 Query API 支持 hybrid search。你的 Qdrant 基础设施已就位，只需升级查询方式。

**Step 2c：Lightweight Rerank**

在 hybrid retrieval 得到 top-30 候选后，加一层 cross-encoder rerank 压缩到 top-5~8。

| 选项 | 适用性 |
|------|--------|
| bge-reranker-v2-m3 | 中文支持好，本地可跑 |
| Cohere Rerank API | 更简单但依赖外部 |
| 你现有的 `_score_reuse_candidate` | 保留作为领域信号层 |

最终评分公式：

```python
final_score = (
    hybrid_retrieval_rrf_score * 0.3    # 语义+关键词粗召回
    + reranker_score * 0.3               # cross-encoder 语义精排
    + domain_score * 0.4                 # 你现有的 20+ 维度领域信号
)
```

**领域信号权重应该最高**，因为你的场景是结构化方案（taxonomy/equipment_type/content_form 这些信号通用 reranker 学不到）。

#### 不建议做的

- **LongRefiner**：你每章 evidence pack 约 3000-8000 字，不是 long-context 场景，加了反而多一层延迟
- **一次性替换所有检索为 embedding**：应分步做，先在 `CaseLibraryService.retrieve_sections()` 层加，再逐步替换 block 级

#### 验收

- 用之前分析的失败案例回归："变频器系统方案"能匹配到"VFD 整体设计"
- "系统及方案介绍"查询 Top-3 召回 >= 90%（vs 当前依赖精确标题匹配）
- 叙述型章节 Top-1 被纯参数表占据的比例 < 10%

---

### Layer 3：视觉检索支路（P1，1-2 周）

> ChatGPT Pro 推荐。方向正确，但优先级应排在 Layer 2 之后。

#### 当前状态

- `AssetRetrievalService` 已有完善的文本检索 + 降权/过滤体系
- 697 个 figure_assets 已入库，145 个已有语义摘要
- 工程图检索质量在 LCI Demo 中已验证可用

#### 为什么排在 P1 而不是 P0

1. 当前 13 份文档的图资产检索，靠 `semantic_summary + heading_path + caption` 已基本够用
2. 真正漏掉的是"图里有但文字没说的"信息——这在当前规模下不是主要痛点
3. ColPali/ColQwen multivector 计算成本较高，文档库规模需要更大才有 ROI

#### 应做

| 步骤 | 内容 |
|------|------|
| 3a | 为每个 figure_asset 的原图页面生成 ColQwen2 embedding，存入 Qdrant 独立 collection |
| 3b | 在 `AssetRetrievalService` 中加 `visual_score` 支路 |
| 3c | 合并评分：`textual_score * 0.6 + visual_score * 0.3 + section_anchor * 0.1` |
| 3d | 只在 `expected_evidence_types` 包含 figure 的章节启用视觉支路 |

#### 验收

- 对无 caption/heading 但图中绘制了完整拓扑的工程图，视觉检索能把它排进 Top-5
- 不增加文本检索已经找对的图的排序回退

#### 当前实现状态（2026-04-22）

- `3a`：已完成到当前仓库可落地形态
  - visual embedding cache 现已同步到独立 Qdrant visual collections
  - 按 embedding 空间拆成 `image` / `text_proxy` 两条 collection，而不是混放
- `3b`：已完成
  - `AssetRetrievalService` 已有独立 `visual_score` 支路
  - 返回 `reason_trace / score_breakdown / search_trace`
- `3c`：已完成
  - 当前融合权重已经对齐为 `textual 0.6 + visual 0.3 + structural 0.1`
- `3d`：已完成
  - 视觉支路只在 `expected_evidence_types` 或显式 `asset_types` 包含 `figure` 时启用
  - 表格/参数章节默认走 `textual_only`

当前 Layer 3 主链可以收口。剩余项属于后续增强：

- 将 CLIP / proxy provider 再升级为 ColQwen / ColPali 等重型视觉 backend
- 对更大资产集做增量刷新和性能基线

---

### Layer 4：AI Wiki 知识编译层（P2，持续）

> ChatGPT Pro 推荐，引用了 Karpathy 的 gist。方向正确，但这是最长期的投入。

#### 核心思路

把客户真实文档**编译**成几类稳定的 wiki 页面：

| 页面类型 | 示例 | 作用 |
|---------|------|------|
| 产品卡 | "LCI 变频软起动系统" | 参数、适用场景、接口边界 |
| 模块卡 | "H 桥功率单元" | 技术规格、版本差异 |
| 接口卡 | "DCS/PLC 通讯接口" | 信号类型、协议、点表结构 |
| 设计依据页 | GB/T 15543-2008 电能质量 | 标准号、适用范围 |
| 术语表 | VFD=变频器=变频柜 | 同义词规范 |
| 章节模板 | "整体方案" 标准骨架 | 大纲生成参考 |
| 禁用表述 | "我们团队经验证明" | 客户稿不允许的内部口径 |

#### 实现路径

1. 先把 Layer 0 的同义词表扩展为结构化术语表
2. 从已有 13 份文档中提取产品卡和模块卡（可半自动）
3. 售前工程师确认后的好章节反向写回 wiki
4. 大纲生成先查 wiki，章节生成再回 raw docs

#### 不建议先做的

- 不建议先上 qmd 等本地搜索引擎。你的 wiki 页面初期不会超过 200 页，Qdrant 或简单 JSON 就够
- 不建议先做 GraphRAG。结构化 wiki 页面 + 混合检索已经能解决 80% 的跨文档关联需求

#### 当前实现状态（2026-04-23）

- 编译层已落地：
  - `backend/data/knowledge_wiki/*` 持续产出 glossary / product / module / equipment / interface / template / policy 页面
- 运行时已接入三条主链：
  - 写前 prompt 约束
  - 写后质量回扫 / rewrite
  - reuse retrieval prior bundle
- 导入链路已具备自动回刷：
  - 历史方案上传 / 重解析 / 删除后，case library、AI Wiki、visual cache 会联动刷新
  - projection cache / fallback backfill / refresh_status 也已补齐
- 评估链已落地：
  - `evaluate_knowledge_wiki_priors.py` 使用 `holdout_eval` 文档固定比较 `without_prior` / `with_prior`
  - 输出 `output/RAG_test-layer4-ai-wiki-prior-eval.md/.json`
- 治理链已收口：
  - AI Wiki prior eval 已进入 `release-gate`、`proof-pack`、`release-readiness`
  - 宿主侧稳定产出 `AI Wiki Prior Evaluation` artifact 和 `Governance: AI Wiki Prior Evaluation` check

因此，按当前 repo scope，Layer 4 已完成这一阶段的闭环实现。后续保留项主要是：

- verified wiki page 的反写与持续人工确认
- prior boost 自动调参
- 页面规模继续扩大后的独立索引/实体层演进

---

## 完整优先级排序

| 层级 | 名称 | 改哪里 | 预期收益 | 耗时 | 依赖 |
|------|------|--------|---------|------|------|
| **L0** | 同义词/术语扩展 | `section_service.py`, `case_service.py`, 新增 `synonyms.py` | 立即解决"VFD!=变频器"类问题 | 0.5 天 | 无 |
| **L1** | 章节真值层 | `docling_parser.py`, `section_catalog.py`, `case_library.py` | 章节结构稳定，检索基座变硬 | 1-2 周 | 无 |
| **L2** | Contextual Hybrid Retrieval | Qdrant 索引, `case_service.py`, 新增 rerank 层 | 语义检索能力，同义词/意图理解 | 2-3 周 | L0 完成 |
| **L3** | 视觉检索支路 | `asset_service.py`, 新增 ColQwen collection | 找对"图里有字里没有"的信息 | 1-2 周 | L1 完成 |
| **L4** | AI Wiki | 新增 wiki 模块 | 长期知识积累，专家级稳定输出 | 持续 | L2 基本完成 |
| backlog | Selective GraphRAG | 新模块 | 实体-关系-约束推理 | — | L4 初步完成 |
| 不做 | LongRefiner | — | 当前章节 evidence 不够长，不适用 | — | — |
| 不做 | RAG-Anything 整合 | — | 框架竞争，侵入太大 | — | — |

---

## 风险与约束

| 风险 | 缓解 |
|------|------|
| Qdrant sparse vector 需要额外索引 | 先在 CaseLibrary 层做，不动 chunks 层 |
| Cross-encoder rerank 增加延迟 | 只对 top-30 做 rerank（约200ms），不影响总体时间 |
| ColQwen2 模型大，推理慢 | 只在入库时生成 embedding，检索时只算 query embedding |
| 同义词表维护成本 | 初期 30-50 组覆盖 90%，后续从 wiki 自动提取 |
| Parser A/B 测试耗时 | 不做阻断。先用 Docling，质量不达标再引入 MinerU |

---

## 一句话总结

> 下一阶段最该做的不是"让模型更像专家"，而是**把客户文档变成更像专家记忆系统的知识底座**。
> 四个关键词：**同义词 -> 结构真值 -> 混合检索 -> 知识编译**。
