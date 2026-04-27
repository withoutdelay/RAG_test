# 语义规则与证据调优计划

## 结论

当前代码中大量词表来自少量测试项目，覆盖面不足，不能继续作为主调优路径。后续调优分三层：

1. **稳定护栏**：空证据不生成、错位资产不插入、明显噪声块不进入正文。这类逻辑保留在代码中。
2. **外置规则**：章节焦点词、噪声词、章节类型组放入 `backend/app/services/composition/rules/semantic_rules.json`，通过审计和回归测试后合入。
3. **模型判别**：`EVIDENCE_JUDGE_MODE=auto|strict` 负责在候选 evidence 进入生成前判定 `core/support/noise`，避免靠词表做主要语义判断。

## 合理扩充方法

### 1. 从全量真实方案库离线挖掘

使用 `backend/scripts/mine_semantic_rule_candidates.py` 扫描已解析 Markdown、AI Wiki cache、case block library，统计章节标题、上下文、复用块中的候选术语。

输出只进入 `output/semantic-rule-candidates.json`，不自动写入线上规则。

### 2. 用 AI Wiki 编译结果补全

AI Wiki 已经沉淀了产品族、设备卡、模块卡、接口卡。候选词优先从这些结构化产物中生成，而不是从单个项目错例里直接补词。

### 3. 用 LLM 做半自动归类

对候选词执行离线 LLM 审核：

- 属于哪个 section intent；
- 是 focus、noise、synonym 还是 forbidden phrase；
- 是否过窄，只适用于某个项目；
- 是否需要进入 holdout regression。

LLM 只生成候选，不直接改线上规则。

### 4. 用回归评测决定是否合入

每次合入规则前至少跑：

- 临沂 holdout：第 3/4/6/7 章；
- 当前真实项目 replay；
- asset placeholder 检查；
- evidence noise rate 检查。

只有通过后才能从候选文件晋升到 `semantic_rules.json`。

## 当前执行约束

- 不再把新错例直接写成代码常量。
- 新词先进入候选审计文件。
- 可以保留少量代码级硬护栏，但不能把业务语义判断继续堆进 `section_service.py`。
- 真实泛化能力主要依赖 evidence judge、AI Wiki 编译层、回归评测集，而不是人工词表数量。
