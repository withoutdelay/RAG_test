# 临沂 holdout 第 3 章系统方案偏差根因分析

## 结论

临沂验证文档的第 3 章与历史库命中的宝山 LCI 方案第 3 章高度一致。两者都围绕：

- `3.1 变频软起系统单线图`
- `3.2 启动和同步过程描述`
- `3.3 LCI 变频启动特性`

当前生成结果偏差的根因不是历史库没有命中，也不是内容长度不足，而是命中后的证据分配和块选择策略失真：正确的宝山第 3 章已经进入 trace，但第 3 章最终只保留了 `3.3.1 负载数据`，而 `3.1 单线图` 和 `3.2 启动同步过程` 被后续的“系统布置与安装要求”章节拿走了。

## 原文对比

历史库文档：

- 文件：`宝山钢铁股份有限公司三鼓风LCI改造方案.docx`
- chunk：`14`
- 标题：`3 系统方案 System Solution`
- 内容包含单线图、ICB/OCB/RCB/LCI/SM/MCP/EXU、SFC 启动同步过程、95% 额定转速、RCB 合闸、LCI 电流归零、工频运行、LCI 启动特性。

验证文档：

- 文件：`临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx`
- chunk：`24`
- 标题：`3 系统方案 System Solution`
- 内容包含 1 套 LCI 软启动系统驱动 2 台同步电机、达到同步转速后 LCI 退出、电机挂网运行、单线图、SFC 启动同步过程。

因此，用户判断“两个系统方案章节高度相似”是成立的。

## 生成链路观察

第 3 章 `系统方案`：

- `selected_sections[0]` 正确命中宝山 `3. 系统方案 SYSTEM SOLUTION`
- `top_section_score = 1.3673`
- `semantic = 0.8939`
- `full_section_block_count = 3`
- `full_section_within_budget = true`
- 但最终 `retrieval_mode = section_pack`
- 原因是 `lead_score = 0.0057 < min_lead_threshold = 0.03`
- 最终 selected block 只有 `3.3.1 负载数据`

第 6 章 `系统布置与安装要求`：

- 同样命中宝山 `3. 系统方案 SYSTEM SOLUTION`
- selected blocks 包含 `3.1 变频软起系统单线图` 和 `3.2 启动和同步过程描述`
- 最终输出反而写出了第 3 章本该出现的系统方案核心内容

第 4 章 `启动与同步过程描述`：

- `selected_sections[0]` 正确命中宝山 `3.2 启动和同步过程描述`
- 但 `full_section_block_count = 0`
- selected block 被 `3.3.1 负载数据` 抢占
- 进入 deterministic builder 后生成了“设计输入/对标资料/禁用表述”等非交付正文残留

## 根因

### 1. section candidate 没有按同源章节去重

第 3 章 top section 与 runner-up 分数非常接近，触发 `section_pack_due_to_low_section_lead`。但这类低 lead 很可能来自同一个源章节或同一父子章节的重复候选，不代表检索不确定。

应该先按 `sample_id + section_id/source_section_id + section_path` 做 canonical dedupe，再计算 lead。

### 2. full-section 证据模式被错误降级

当前配置本来偏向 `prefer_full_section`，而第 3 章满足：

- 分数高
- 语义匹配高
- full section 有 block
- token budget 足够

但因为 lead 被重复候选压低，整章复用没有启用。对于系统方案这种强结构章节，应该优先保留完整父章节，而不是切成零散 block 再排序。

### 3. block 选择过度偏向叶子块

`3.3.1 负载数据` 是叶子块，关键词多、包含 LCI/启动/参数，容易拿高分；但它不是“系统方案”的主干内容。当前 block selection 没有强制：

- overall/system solution 先选父章节叙述块
- 再选单线图/启动同步/启动特性
- 最后才选负载数据、启动曲线等子项

结果是主方案被 leaf block 挤掉。

### 4. 章节之间没有 evidence allocation

同一份宝山第 3 章证据被第 3、4、6 章重复竞争。系统把最关键的 `3.1/3.2` 分给了第 6 章，导致第 3 章空心化。

需要在单次 draft 内做 evidence allocation：当一个源章节与目标第 3 章高度匹配时，宽泛章节如“系统布置与安装要求”不能优先消费它。

### 5. outline 写出 3 个小节会有帮助，但不是根治

如果 outline 里明确加入：

- `3.1 变频软起系统单线图`
- `3.2 启动和同步过程描述`
- `3.3 LCI 变频启动特性`

生成效果大概率会更稳定，因为 prompt 目标更清晰，LLM 更不容易只写负载数据。

但这不是最高优先级修复。因为当前 retrieval 已经找到了这些内容，真正的问题是系统没有把这些命中的内容稳定分配给正确章节。

## 建议修复优先级

1. 对 section candidates 做 canonical dedupe，再计算 top lead。
2. 对 `overall_solution/system_scheme` 类章节启用 source-section atomic reuse：高置信命中父章节时整章复用，并保留子标题顺序。
3. 对父章节下的 child blocks 做 bundle 排序：`3.1/3.2/3.3` 必须优先于 `3.3.1` 这类叶子参数块。
4. 在同一次 draft 内增加 evidence allocation，避免第 6 章这类宽泛章节抢走第 3 章的核心系统方案材料。
5. 对 deterministic builder 输出做残留清理，禁止“项目需求/对标资料/禁用表述”等内部提示性内容进入正文。
6. 增加 section-to-asset compatibility guard：备件章节只能引用备品备件、供货清单、BOM 或明确同源的备件表；环境、供电条件、设计输入类表格不得作为备件资产引用。
7. 上传解析进入后台 job：DOCX 上传接口先保存文件并返回 job，解析、chunk、入库和资产抽取在后台队列执行，前端轮询解析状态，避免客户端 300 秒超时但后端继续写库的不可观测状态。

## 验收标准

重跑临沂 holdout 后，第 3 章应至少覆盖：

- 1 套 LCI 软起系统驱动 2 台同步电机；
- 单线图或主回路拓扑中的 ICB/OCB/RCB/LCI/SM 等关键设备；
- SFC 控制启动和同步；
- 加速至约 95% 额定转速；
- 同步装置释放并进行速度/电压调整；
- RCB 合闸后 LCI 电流降为零并退出；
- 电机转入 10kV 电网工频运行；
- LCI 启动特性、建磁时间、同步时间、连续启动能力按证据保留。

同时需要验证：

- 第 4 章不再出现“项目需求 / 对标资料 / 禁用表述”等内部清洗或诊断残留。
- 第 7 章备件章节不再引用环境条件、供电条件、设计输入类表格资产。
- 上传临沂 holdout DOCX 时客户端不会等待完整解析；接口快速返回 job，解析状态可轮询，失败时能看到明确错误。

## 本轮实现状态

已实现：

- section candidate 在生成策略阶段先按同源章节去重，同源父子章节不再压低 full-section lead。
- child section 可匹配包含该 child heading 的父章节 block，避免 `3.2 启动和同步过程描述` 因父 chunk 承载而 full-section block count 为 0。
- `overall_solution` 组装时按 `单线图 -> 启动同步 -> LCI启动特性 -> 负载/曲线` 排序，降低叶子参数块抢占主方案的概率。
- `启动与同步过程描述` 不再被误判为供电/负载参数 deterministic builder，避免写入“项目需求 / 对标资料 / 禁用表述”等内部残留。
- `installation_conditions` 会过滤没有安装/布置焦点的系统方案、单线图、启动同步证据，避免第 6 章抢走第 3 章材料。
- 备件章节增加 table asset 兼容性过滤，环境/供电/设计输入表格不会进入备件章节推荐资产。
- 项目文档上传改为后台 `document_parse` job：上传接口保存文件和 document 后快速返回 `job_id` / `next_poll`，解析、chunk、入库和资产抽取在后台队列执行。

已验证：

- `backend/tests/test_composition_helpers.py`
- `backend/tests/test_document_api_helpers.py`
- `frontend` Documents 页面 lint
- `section_service.py` / `documents.py` / `document.py` 语法编译

待验证：

- 连接可用测试数据库后重跑 `backend/tests/test_api_phase2.py`。
- 用真实临沂 holdout 重跑生成，检查第 3/4/6/7 章实际输出。
