# Reuse-First RAG 技术路线

> 目的：将当前 `summary-first` 路线正式降级为 `baseline / fallback`，明确后续主路线改为 `reuse-first`。  
> 适用范围：面向售前方案生成主链路，不替代现有 `PDF -> 清洗 -> 资产保留 -> 安全入库 -> 审计` 数据底座。  
> 结论：对于“客户历史方案 80% 可复用”的场景，主路线应从“摘要后生成”切换为“检索可复用块 -> 受控改写 -> 组装成稿”。

---

## 1. 背景与决策

基于真实样本验证，当前路线的核心问题已经比较明确：

- 现有系统更擅长“生成一份像样的新稿”
- 但不擅长“最大化复用旧方案中的干技术内容”
- 结果是高层结构可读，但技术章节容易出现：
  - 文字变多
  - 信息密度下降
  - 技术表述被泛化
  - 图、表、公式没有进入正文装配链路

这说明当前路线并非完全不可用，但不应继续承担主生成模式。

正式决策如下：

1. 当前 `summary-first` 路线保留，但定位调整为 `baseline / fallback`
2. 后续主路线切换为 `reuse-first`
3. `reuse-first` 的核心目标不是“写得更像样”，而是“复用得更准确、更省人工”

---

## 2. 当前路线的重新定位

### 2.1 当前路线定义

当前路线可定义为：

`检索摘要 -> LLM 生成新章节 -> 人工修订`

它的优点：

- 对冷启动友好
- 章节结构容易稳定
- 在方案概述、需求分析、实施排期、售后服务等章节上容易快速成稿
- 在样本库很少时仍能工作

它的缺点：

- 容易把原方案中的干货技术文本压缩成低密度摘要
- 再由 LLM 重新展开，导致废话变多
- 技术章节的“复用价值”无法充分兑现
- 图、表、公式只能作为弱提示，无法自然进入正文

### 2.2 正式降级为 Baseline

从现在开始，当前路线的定位应固定为：

- `baseline`
- `fallback`
- `冷启动草稿器`
- `低复用章节生成器`

不再把它作为最终主生成模式。

### 2.3 Baseline 的适用场景

当前路线仍然适合：

- 样本库较少的项目
- 首次冷启动
- 缺少高相似旧方案时
- 章节本身更偏总结和组织表达时
- 作为 `reuse-first` 失败时的兜底输出

---

## 3. Reuse-First 的核心定义

### 3.1 一句话定义

`先找最像的旧内容，再做最小必要改写。`

### 3.2 主流程定义

目标流程应改为：

`Requirement Structuring -> Candidate Outline Generation -> Sales Engineer Outline Review -> Candidate Retrieval -> Reusable Block Selection -> Controlled Rewrite -> Asset Recommendation / Placeholder Assembly -> Validation -> Review -> Export`

其中：

- `Candidate Outline Generation` 只负责提出高层结构候选，不直接进入章节生成
- `Sales Engineer Outline Review` 是正式人工确认断点，确认后的目录才允许进入后续复用链路
- `Candidate Retrieval` 不只检索摘要，而是检索原始可复用块
- `Reusable Block Selection` 是一级能力，不再是隐含步骤
- `Controlled Rewrite` 只对必要字段改写，不鼓励自由发挥
- `Asset Recommendation` 与正文装配并行，而不是事后补充

### 3.3 目标产物

`reuse-first` 的理想产物不是：

- 完全复制旧方案
- 也不是完全重新写一份新方案

而是：

- 通用技术段高复用
- 项目特异段受控改写
- 参数类内容有结构化约束
- 图、表、公式以“推荐资产 / 占位符 / 待替换点”形式参与装配

---

## 4. 总体架构

### 4.1 数据底座保持不变

现有以下能力继续保留并作为主底座：

- PDF 清洗
- 安全入库
- 图、表、公式资产保留
- table asset 隔离
- asset retrieval
- requirement / evidence / outline / validation 主流程

也就是说，`reuse-first` 不是推翻当前工程，而是在当前底座上重构生成主线。

### 4.2 新的主生成架构

新的主生成架构建议拆为 6 层：

1. `Requirement Layer`
2. `Outline Planning Layer`
3. `Retrieval Layer`
4. `Reuse Assembly Layer`
5. `Rewrite Layer`
6. `Validation & Review Layer`

#### Requirement Layer

负责生成更硬的项目上下文：

- 项目类型
- 行业
- 产品线
- 供电等级
- 关键设备类型
- 关键约束
- 已知关键参数
- 明确不可沿用的客户专属信息

#### Outline Planning Layer

负责生成和确认“高层结构主干”：

- LLM 先生成候选大纲
- 售前工程师对候选大纲做编辑和确认
- 只有确认后的大纲才进入章节级复用流程

这一层是 `reuse-first` 的必要断点，而不是可选增强。

原因：

- 高层结构本身就是售前经验的重要表达
- 如果章节骨架不对，后续复用和资产装配都会偏
- 目录先确认，才能提升章节级检索精度与复用精度

#### Retrieval Layer

负责分层检索：

- 文本块检索
- 表格资产检索
- 图资产检索
- 公式资产检索

检索目标不是“找一些背景说明”，而是“找最值得复用的源材料”。

#### Reuse Assembly Layer

负责把候选内容按章节组装成：

- `reusable_text_blocks`
- `recommended_assets`
- `parameter_candidates`
- `do_not_reuse_signals`

#### Rewrite Layer

LLM 不再“从摘要自由生成”，而是：

- 对已选复用块做最小必要改写
- 完成项目参数替换
- 清理旧客户痕迹
- 补足桥接语句和少量上下文

#### Validation & Review Layer

负责拦截：

- 参数串案
- 客户信息泄漏
- 图表引用错位
- 章节复用不当
- 相似度过高或改写不足

---

## 5. 章节分层策略

`reuse-first` 不能整份统一处理，必须按章节分层。

### 5.1 高复用章节

适合最大化复用旧方案内容：

- 技术架构
- 核心设备技术方案
- 控制保护与监测方案
- 安装环境与接口条件
- 质量与可靠性保障
- 售后服务中的标准承诺部分

策略：

- 优先使用高相似旧块
- LLM 只做最小改写
- 严格限制自由扩写

### 5.2 中复用章节

适合“复用主体骨架 + 轻改写”：

- 需求分析
- 硬件配置清单说明部分
- 实施排期

策略：

- 允许多来源拼装
- 保留原有技术步骤和组织结构
- 对项目差异做显式标注

### 5.3 低复用章节

更适合受控生成：

- 项目概述
- 客户背景
- 项目边界
- 针对本次项目的定制说明

策略：

- 可以继续调用 `baseline`
- 但必须受 requirement 和参数约束
- 避免直接复制旧项目背景

---

## 6. Reuse-First 的关键实现单元

### 6.0 大纲人工确认断点

在 `reuse-first` 路线中，不建议保留“LLM 生成大纲后直接开始章节写作”的全自动流程。

建议改为：

1. 系统生成 `outline candidate`
2. 售前工程师进行目录级编辑
3. 系统保存 `approved outline`
4. 章节检索与生成严格基于 `approved outline`

售前工程师在大纲层至少应能做这些动作：

- 改章节标题
- 调整章节顺序
- 删除不需要的章节
- 新增特定项目需要的章节
- 合并或拆分章节
- 标记章节是否高复用
- 标记章节是否需要图 / 表 / 公式
- 标记章节是否必须人工编写

### 6.0.1 大纲层建议新增字段

每个 section 在确认阶段建议显式维护以下字段：

- `section_class`
- `reuse_level`
- `expected_evidence_types`
- `asset_required`
- `parameter_sensitive`
- `customer_specificity`
- `generation_mode`

其中 `generation_mode` 建议支持：

- `baseline`
- `reuse_first`
- `manual_only`

这样章节生成阶段就不再是“一个 prompt 打天下”，而是基于人工确认后的章节类型做路由。

### 6.1 Reusable Block

应新增一层统一对象：

`Reusable Block`

最小字段建议：

- `block_id`
- `document_id`
- `chunk_id`
- `section_class`
- `source_title`
- `heading_path`
- `content_md`
- `block_type`
- `parameter_density`
- `asset_dependency_level`
- `customer_specificity_score`
- `reusability_score`
- `must_replace_fields`
- `must_not_copy_spans`

说明：

- `Reusable Block` 可以来自 chunk，也可以来自后续重建后的 table / formula asset
- 它是“可复用候选”，不是“必然落稿内容”

### 6.2 Block 检索

每个章节应至少检索出：

- `top_k_same_section_blocks`
- `top_k_adjacent_section_blocks`
- `top_k_asset_cards`

排序不能只看 embedding。

建议综合：

- 向量相似度
- 产品线一致性
- 行业一致性
- 设备类型一致性
- 供电等级一致性
- 参数相近程度
- 章节类型匹配程度
- 客户专属性惩罚

### 6.3 Reuse Pack

每章在进入 LLM 前，不应只有“几条摘要”，而应形成：

`Reuse Pack`

包含：

- 本章目标
- 可复用源块列表
- 不可直接复用字段列表
- 需替换参数列表
- 推荐图/表/公式资产
- 风险标签

LLM 的输入应以 `Reuse Pack` 为核心，而不是以自由 prompt 为核心。

### 6.4 Controlled Rewrite

LLM 输出要求应改成：

- 保留可复用技术骨架
- 仅替换项目相关字段
- 仅补足必要衔接句
- 不主动泛化展开
- 不主动增加无来源的标准套话

换句话说：

`rewrite` 应该比 `generate` 权重大。

### 6.5 Asset-Aware Assembly

图、表、公式在 `reuse-first` 中不应继续停留在“弱提示”。

MVP 应至少支持：

- 推荐资产列表
- 章节内建议插入点
- 占位符输出

建议占位形式：

- `[[ASSET:FIGURE:<asset_id>]]`
- `[[ASSET:TABLE:<asset_id>]]`
- `[[ASSET:FORMULA:<asset_id>]]`

这样后续：

- 前端可渲染待替换卡片
- 导出前可人工确认
- 不会因为模型没写图而完全丢失资产引用

---

## 7. 如何避免过拟合

这是 `reuse-first` 的核心实施约束，必须在一开始就写死。

### 7.1 过拟合的定义

在本项目中，过拟合主要指：

- 过度沿用旧客户专属信息
- 过度沿用旧参数、旧边界、旧项目名称
- 章节结构和措辞几乎原封不动
- 把不属于当前项目的图、表、公式直接当成新项目内容

### 7.2 基础约束

必须执行以下约束：

1. 任何复用都必须经过 `project fit gate`
2. 任何参数复用都必须经过结构化比对
3. 任何客户标识都必须经过专门清洗
4. 高风险资产默认只能参考，不能自动承诺
5. 任何低匹配度旧块不得直接进入最终复用集

### 7.3 Project Fit Gate

只有以下条件足够接近时，旧块才允许进入高复用候选：

- 行业一致
- 产品线一致
- 核心设备类型一致
- 电压等级一致或兼容
- 工艺场景接近
- 章节类型一致

如果不满足：

- 可以降为“中复用候选”
- 或者只允许作为背景参考

### 7.4 参数替换约束

以下字段不能默认沿用：

- 项目名称
- 客户名称
- 买方 / 卖方
- 地名 / 工厂名 / 产线名
- 电压等级
- 功率 / 数量 / 型号
- 工期
- 控制接口边界
- 供货边界

所有这些字段必须经过：

- 抽取
- 标准化
- 比对
- 替换或显式标记待确认

### 7.5 复用比例分级

不同章节的复用比例必须受控：

- 高复用章节：允许高比例复用，但必须通过参数校验
- 中复用章节：允许骨架复用，不允许大段原样照搬
- 低复用章节：默认只允许参考，不允许原文大段复用

### 7.6 客户专属性惩罚

检索排序时，应降低以下块的优先级：

- 含旧客户名称
- 含旧买卖方
- 含旧地名或旧组织结构
- 含明显旧项目时间表
- 含仅适用于旧厂区的接口条件

### 7.7 资产过拟合防护

图、表、公式必须分开对待：

- 图：允许推荐，不允许自动当成新项目最终插图
- 大表：允许作为模板参考，不允许自动当成新项目最终参数表
- 公式：允许作为候选引用，不允许自动当成真值

### 7.8 相似度审查

最终稿应增加一层“过拟合审查”：

- 与来源块的文本相似度
- 旧客户字段残留检查
- 参数替换完成度检查
- 章节级来源多样性检查

如果章节：

- 相似度过高
- 但关键参数没有替换

则必须进入 review gate。

---

## 8. Baseline 与 Reuse-First 的协同关系

两条路线不应互斥，而应分工。

### 8.1 Baseline 负责什么

- 冷启动
- 低复用章节
- 候选不足时兜底
- 章节桥接和总结性表达

### 8.2 Reuse-First 负责什么

- 高复用技术章节
- 参数密集章节
- 图表密集章节
- 需要“像原方案但不是复制”的场景

### 8.3 路由策略

建议后续按章节路由：

- `项目概述` -> baseline 或低复用 rewrite
- `需求分析` -> 中复用
- `技术架构` -> 高复用
- `核心设备技术方案` -> 高复用
- `控制保护与监测方案` -> 高复用
- `安装环境与接口条件` -> 高复用
- `硬件配置清单` -> 表格/参数专门路线
- `实施排期` -> 中复用
- `质量与可靠性保障` -> 高复用
- `售后服务` -> 高复用或 baseline 混合

但在章节路由生效之前，必须先满足一个前置条件：

- 目录已经被售前工程师确认

也就是说，真正的路由顺序应是：

`候选大纲 -> 人工确认大纲 -> 章节级路由 -> 检索 / 复用 / 改写`

---

## 9. 实施路线

### Phase R1：章节分层与基线重定位

目标：

- 在系统中显式区分 `baseline` 与 `reuse-first`
- 为章节配置复用等级

产出：

- section class 规则
- baseline / reuse-first 路由表

### Phase R2：大纲确认断点与章节路由

目标：

- 把“目录人工确认”从可选操作升级为正式断点
- 为每个章节挂上生成模式和复用等级

产出：

- approved outline 数据结构
- section-level route config
- 目录确认后的章节元数据

### Phase R3：Reusable Block 建模与检索

目标：

- 从现有 chunk 和 asset 中派生 `Reusable Block`
- 建立章节级候选召回能力

产出：

- block scorer
- reuse pack builder

### Phase R4：受控改写链路

目标：

- 让 LLM 从“自由生成”切换到“受控改写”

产出：

- rewrite-first prompt
- 参数替换约束
- 旧客户痕迹清洗规则

### Phase R5：Asset-Aware Assembly

目标：

- 让图、表、公式正式进入成稿装配链路

产出：

- asset placeholders
- 插入点推荐
- review-aware asset rendering

### Phase R6：过拟合校验与评估

目标：

- 将“复用效果”和“过拟合风险”同时量化

产出：

- similarity check
- customer leakage check
- parameter replacement completeness check

---

## 10. 验收标准

`reuse-first` 路线的成功，不应只看“读起来像不像”，还要看下面几项：

1. 技术章节的信息密度明显高于 baseline
2. 售前人工修改工作量明显下降
3. 图、表、公式至少能以占位或推荐形式进入稿件
4. 旧客户字段残留率可控
5. 参数串案率可控
6. 高复用章节的有效复用比例显著提升

建议的第一阶段验收目标：

- 高复用章节中，至少 `60%` 的正文来自可追溯复用块或受控改写结果
- 图表资产至少能在应出现的章节中以占位符或推荐块出现
- 相较 baseline，技术章节人工删改量显著下降

---

## 11. 最终结论

当前路线不应被废弃，但应明确降级为：

- `baseline`
- `fallback`
- `冷启动与低复用章节生成器`

后续主路线应切换为：

`reuse-first + asset-aware + validation-constrained`

对本项目来说，真正需要优化的不是“让模型写得更流畅”，而是：

`让系统更擅长复用旧方案中的干技术内容，并在复用时不串案、不过拟合。`
