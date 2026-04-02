# 章节真值层与标题优先检索迭代方案

> 目标：解决“原始方案目录章节抽取不稳”与“类似章节检索时标题权重不足”这两个上游问题。  
> 适用范围：面向 `reuse-first` 主路线的数据底座与章节级检索层，不覆盖前端交互细节。  
> 设计原则：先保证每份原始方案的章节目录可被稳定、可审计地还原，再让章节检索从“混合关键词召回”升级为“标题优先、细节补充、分层重排”。

---

## 1. 当前问题判断

这次“`系统及方案介绍` 没有稳定命中同名历史章节”的现象，不是孤例，而是当前链路结构性不足的集中体现。

当前主要断点有 3 个：

1. `case_library` 仍然主要依赖 markdown `#` 标题建 outline。
2. `docling` 解析出来的正文内章节标记，没有被稳定提升成真正的 `heading_path`。
3. 章节检索里虽然有 `section_title_match`，但只是一个较轻的加分项，容易被行业、设备、参数词覆盖。

从现状代码看：

- [case_library.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/case_library.py) 的 `extract_outline_tree` 只识别 markdown heading。
- [docling_parser.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/docling_parser.py) 当前主要跟踪 `SectionHeaderItem`，像“第三章 系统及方案介绍”这类正文内章节标记，容易停留在 chunk 正文中，而不是进入结构层。
- [section_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/composition/section_service.py) 的 `_build_reuse_query_terms` 会把章节标题、purpose、行业、产品线等全部混在一组 query token 中。
- [case_service.py](/Volumes/thunder/code/RAG_test/backend/app/services/retrieval/case_service.py) 里 `section_title_match` 当前只是较小加分，因此“冷风机 / 钢铁 / 风机”这类项目词，可能让参数表或设备清单排到叙述型章节前面。

这意味着：

- 现在不是“没有检索到相关文档”，而是“没有先把文档里的章节结构提准”。
- 只要章节结构真值不稳，后续再怎么调 prompt、调 UI、调改单段文本，都会反复撞到同类问题。

---

## 2. 目标与非目标

### 2.1 本轮目标

本轮要把系统推进到下面这个状态：

1. 每份原始方案都能产出一份可审计的 `section_catalog`，尽量真实还原原文目录结构。
2. `outline_library` 和 `block_library` 不再只依赖 markdown `#` 标题，而是基于 `section_catalog` 重建。
3. 章节检索改成“标题优先”的分层召回，不再让项目词轻易压过同名章节。
4. 如果用户在章节描述里补了更细的要点，这些细节要能作为二级甚至一级排序信号。
5. 章节型正文、参数表、图表资产的使用边界要更清楚，避免“叙述章节被设备参数表顶上来”。

### 2.2 非目标

本轮不追求：

- 一次性把所有 PDF / DOCX 的视觉排版 100% 恢复；
- 一次性做完所有前端交互重构；
- 用 prompt 优化替代章节结构修复。

---

## 3. 核心设计原则

### 3.1 章节真值优先

每份原始方案都应先形成“章节真值层”，后续 outline、检索、block 装配、asset 推荐都引用同一份结构真值，而不是各自再猜一遍标题。

### 3.2 标题是一级检索锚点

在“找类似章节”这个任务里，章节标题不是普通关键词，而是一级锚点。

优先级原则应为：

1. 同名 / 近义标题命中
2. 标题家族命中
3. 用户补充的细节要点命中
4. 行业、产品线、设备类型命中
5. 正文全文语义相似

### 3.3 用户细节可在特定场景下超过标题

如果用户对章节写了更具体的要求，例如：

- “描述系统总体架构、模块划分与接口关系”
- “要体现上位机通讯、控制柜和风机侧边界”
- “要写改造范围，不要展开设备参数”

那么这些细节应形成独立的 `detail_intent`，在同名标题候选之间起决定性作用；在某些场景下，甚至可以超过“仅仅同名”的弱匹配章节。

### 3.4 先选章节，再选块，再选资产

正确顺序不应是“直接从所有 chunk 里混合召回”，而应是：

`先找最可能的源章节 -> 再在该章节内挑正文块 -> 再补同章节的图表资产`

---

## 4. 目标架构

### 4.1 新增 `section_catalog` 作为章节真值层

每份已解析文档，都应额外产出一份结构化章节目录，例如：

```json
{
  "document_id": "...",
  "sections": [
    {
      "section_id": "sec-003",
      "source_heading": "第三章 系统及方案介绍",
      "normalized_heading": "系统及方案介绍",
      "heading_family": ["系统及方案介绍", "系统方案介绍", "总体方案介绍"],
      "heading_level": 1,
      "ordinal": "第三章",
      "parent_id": null,
      "page_span": [6, 9],
      "content_span": {"chunk_start": 26, "chunk_end": 34},
      "source_signals": ["toc", "body_heading", "docling_header"],
      "audit_flags": [],
      "children": ["sec-003-1", "sec-003-2"]
    }
  ]
}
```

建议字段至少包括：

- `section_id`
- `source_heading`
- `normalized_heading`
- `heading_family`
- `heading_level`
- `ordinal`
- `parent_id`
- `page_span`
- `content_span`
- `source_signals`
- `audit_flags`

### 4.2 章节抽取改为多信号融合，而不是只看 markdown `#`

章节抽取建议拆成 5 步：

#### Step A：目录页 / TOC 抽取

先识别文档是否存在目录页，并把目录中的章节标题、顺序、页码抽出来。

目标：

- 建立原始目录骨架
- 为后续正文章节对齐提供锚点

#### Step B：正文章节标记修复

针对正文中的章节标记做专门识别，例如：

- `第一章 / 第二章 / 第三章`
- `第一节 / 1.1 / 1.2 / 2、`
- `一、二、三、`
- 行首强标题、编号标题、居中标题

这一步要把“正文里的章节名”提升成结构层，而不是继续埋在 chunk 内容里。

#### Step C：标题规范化与标题家族构建

对标题做统一清洗：

- 去掉编号前缀：`第三章`、`2.1`、`一、`
- 统一全半角和空格
- 去掉明显页码、文件名、目录噪声

同时构建 `heading_family`，例如：

- `系统及方案介绍`
- `系统方案介绍`
- `总体方案介绍`
- `方案介绍`

### 4.3 章节边界重建

目录标题被识别出来后，还要把正文 chunk 边界重新挂到这些章节上，而不是继续沿用“chunk 自己的 heading_path”作为唯一真值。

建议规则：

- 按正文中最近一次有效章节标记切分 section span
- 如果 TOC 页码存在，优先用页码对齐 section 起点
- 如果正文没有明确下一个标题，允许延续到下一强标题或文档末尾
- 对明显异常的 section 记 `audit_flags`，例如：
  - `missing_body_anchor`
  - `toc_only`
  - `body_only`
  - `suspicious_short_section`

### 4.4 `case_library` 与 `historical corpus` 全量改挂 `section_catalog`

后续所有 reusable block、asset card、outline entry，都应该显式挂到 `source_section_id` 上。

建议新增或替换的运行时字段：

- `source_section_id`
- `source_heading`
- `normalized_heading`
- `heading_family`
- `section_level`
- `section_order`
- `section_path`

这样做的直接收益：

- chunk 就算标题抽坏，只要 section span 对了，也还能回到正确章节；
- 表、图也可以天然挂在所属章节下，不再孤立漂浮；
- 章节级检索和块级检索能共享同一套真值层。

---

## 5. 标题优先的章节检索方案

### 5.1 总体流程

章节检索建议改为 4 层：

1. `Document Shortlist`
2. `Section Shortlist`
3. `Block Selection`
4. `Asset Backfill`

### 5.2 第一层：Document Shortlist

先用以下信号筛一批候选历史方案：

- 行业
- 产品线
- 设备类型
- 历史目录中是否出现过目标标题或标题家族

这里的关键变化是：

不只是“按项目词找文档”，还要加入“这份文档的目录里有没有类似章节”。

### 5.3 第二层：Section Shortlist

这是本轮最关键的一层。  
目标是：先锁定“最像的章节”，再去拿块。

建议评分拆成两组：

#### A. 标题主信号

- `exact_title_match`
- `normalized_title_match`
- `heading_family_match`
- `outline_neighbor_match`

建议权重上，标题类信号总体要占章节排序的主导地位。

可以采用类似下面的权重带：

- 同名标题：`0.35 ~ 0.45`
- 规范化标题命中：`0.18 ~ 0.25`
- 标题家族命中：`0.10 ~ 0.18`
- 上下级目录邻近命中：`0.06 ~ 0.12`

#### B. 细节补充信号

把用户在章节描述里写的内容单独拆出来，形成：

- `detail_intent`
- `required_points`
- `negative_points`

例如：

- `required_points`: 模块划分、接口关系、控制边界
- `negative_points`: 不要堆设备参数、不要写公司介绍

建议权重：

- 细节命中：`0.15 ~ 0.30`
- 负向约束冲突：`-0.12 ~ -0.25`

设计原则是：

- 没有细节时，标题优先；
- 有细节时，在同名章节之间，细节优先；
- 如果某个候选虽然不同名，但细节命中极强，也允许进入 Top K，但不应轻易压过高质量同名章节。

### 5.4 第三层：Block Selection

在选中章节后，再在该章节内做块选择：

- 叙述型章节优先正文 narrative block
- 参数型章节优先 table / spec block
- 接口型章节优先 interface / signal block

这一步要避免现在这种情况：

- 目标是“系统及方案介绍”
- 结果被 `1#风机参数表 / 2#风机参数表` 顶到前面

建议增加内容形态约束：

- 如果目标章节是叙述型章节，则对纯参数表、纯清单表给予明显惩罚
- 如果目标章节是清单 / 供货范围 / 参数章节，则反过来抬高表格块

### 5.5 第四层：Asset Backfill

图表资产不再独立平铺检索，而是优先从已选中的 `source_section_id` 下回填。

优先级建议：

1. 同章节资产
2. 同标题家族章节资产
3. 同文档相邻章节资产
4. 全库相关资产

---

## 6. 需要新增的能力模块

### 6.1 `section_catalog_builder`

职责：

- 融合 TOC、正文 heading、docling 标题信号
- 产出结构化章节目录
- 输出章节审计信息

### 6.2 `heading_normalizer`

职责：

- 编号剥离
- 标题清洗
- 标题家族归一
- 噪声标题拦截

### 6.3 `section_boundary_resolver`

职责：

- 给 chunk / asset 挂接 `source_section_id`
- 修复 heading 丢失但正文仍可对齐的 section span

### 6.4 `section_retriever`

职责：

- 做标题优先的 section shortlist
- 接收 `section_title + detail_intent + global_context`
- 输出“候选章节包”，而不是一锅混排 block

### 6.5 `section_audit_report`

职责：

- 输出每份文档的章节抽取质量
- 识别：
  - 目录页未对齐
  - 标题断裂
  - 噪声标题
  - section span 异常短

---

## 7. 分阶段实施方案

## Phase 0：评测集与审计基线

目标：

- 先固定一套真实回归基线，避免“感觉变好了”

实施内容：

1. 从 `pilot_main + holdout_eval` 中抽 8 到 12 份真实方案
2. 人工标注每份文档的：
   - 顶层目录
   - 关键二级章节
   - 同名 / 近义章节
3. 建立 20 到 30 个章节检索 query，包括：
   - 纯标题 query
   - 标题 + 细节 query
   - 负向约束 query

验收指标：

- `top_level_heading_recall`
- `key_section_recall`
- `section_retrieval_top1`
- `section_retrieval_top3`
- `table_noise_top3_rate`

优先级：`P0`

## Phase 1：章节真值层建设

目标：

- 让每份原始方案都能产出可信 `section_catalog`

实施内容：

1. 新增目录页抽取
2. 新增正文章节标记修复
3. 新增标题规范化与标题家族构建
4. 新增 section span 重建
5. 为 chunk / asset / parsed_block 显式挂 `source_section_id`

验收标准：

- `pilot_main` 中顶层标题召回率达到 `>= 95%`
- 关键二级章节召回率达到 `>= 85%`
- 顶层 `top_level_titles` 不再出现文件名、`6 Л` 这类明显噪声

优先级：`P0`

## Phase 2：Case Library 重建

目标：

- 让 outline 和 block 都建立在章节真值层上

实施内容：

1. `outline_library` 改为读取 `section_catalog`
2. `block_library` 中每个 block 挂接 `source_section_id`
3. 资产卡也挂接 `source_section_id`
4. 重建 `pilot_main` 历史库，并输出章节审计摘要

验收标准：

- case library 中能稳定看到“源标题 / 规范化标题 / 标题家族”
- 每个 reusable block 都可追溯到所属章节

优先级：`P0`

## Phase 3：标题优先的章节检索

目标：

- 把“先找章节”变成正式能力

实施内容：

1. 新增 `section_retriever`
2. 把 query 拆成：
   - `section_title`
   - `detail_intent`
   - `global_context`
3. 同名 / 规范化标题 / 标题家族加权显著抬高
4. 对叙述型章节加入参数表惩罚
5. 对接口、主回路、供货范围等已知强章节保留专门 heading bonus

验收标准：

- “系统及方案介绍”这类同名章节，在目标样本上 Top 3 召回达到 `>= 90%`
- 叙述型章节 Top 1 被纯参数表占据的比例降到 `< 10%`

优先级：`P0`

## Phase 4：章节内块与资产装配

目标：

- 让 block / asset 的复用顺序更符合工程师直觉

实施内容：

1. 优先从已选章节内抽 narrative block
2. 图表优先从同章节回填
3. 把“图表是支持证据还是正文必需组件”显式区分
4. 把表格占位从“裸 markdown”升级为“结构化 table asset 引用”

验收标准：

- 叙述章节默认不再先冒出参数表残片
- 供货范围、参数、配置类章节，表格命中率明显提升

优先级：`P1`

## Phase 5：线上回归与历史库回灌

目标：

- 让真实历史库和运行时索引切换到新结构

实施内容：

1. 回灌 `pilot_main`
2. 对 `holdout_eval` 做只读验证
3. 输出对比报告：
   - 旧版章节抽取
   - 新版章节抽取
   - 旧版检索排序
   - 新版检索排序

验收标准：

- 不出现明显回退
- 主回路 / 接口 / 供货范围 / 系统介绍这四类章节稳定变好

优先级：`P1`

---

## 8. 当前最值得先做的 3 个动作

如果只做最关键、最能立刻改变效果的动作，建议按下面顺序：

1. 先补 `section_catalog`
2. 再让 `case_library` 改挂 `section_catalog`
3. 最后把章节检索改成“标题主导 + 细节补充”

原因很简单：

- 没有章节真值层，标题优先检索没有可靠输入；
- 不先重建 case library，检索永远是在坏 heading 上打补丁；
- 不先把标题抬成一级信号，像“系统及方案介绍”这类章节会持续被设备词和参数表干扰。

---

## 9. 对当前失败案例的直接修复建议

针对“`宝钢冷风机部署方案` -> `系统及方案介绍` 没有稳定命中 `乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx` 同名大章节”这个问题，建议直接作为第一批回归样例。

这条 case 的目标是：

1. 在源文档中把“第三章 系统及方案介绍”提升成真实章节；
2. 让 `section_catalog` 中存在该章节；
3. 让章节检索把它排进 Top 3；
4. 让该章节内部的 narrative block 先于 `1# / 2# 风机参数表` 被选中；
5. 让相关图表作为同章节支持资产回填，而不是四散漂浮。

只要这条样例跑通，后续“接口说明”“系统方案”“供货范围”“主回路”等章节的质量也会一起受益。

---

## 10. 结论

这轮需求的正确主线不是继续调 prompt，而是：

`章节真值层 -> 标题优先检索 -> 章节内块选择 -> 资产回填`

其中最关键的判断是：

- 每个原始方案的目录章节，必须先被稳定提准；
- 检索类似章节时，章节标题必须是一级锚点；
- 用户补充的细节要点，应当作为强约束参与同名章节之间的排序；
- 参数表、清单表、图表资产应该在“章节选对之后”再进入装配，而不是一开始就和所有正文块混排竞争。

这条链路一旦打稳，`reuse-first` 后面的大多数章节质量问题，都会从“生成侧问题”收口成“检索与结构底座问题”，这才是能长期推进的路线。
