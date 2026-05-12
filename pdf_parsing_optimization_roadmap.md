# PDF 解析痛点与后续优化路线图

> 目的：固化当前真实工程 PDF 验收中已经确认的核心痛点、约束结论和后续高价值优化方向，避免多轮开发后重复试错或遗忘关键背景。  
> 范围：仅关注 `PDF -> 清洗 -> 图资产保留 -> 公式/上下标处理 -> 审计` 这条链路，不覆盖生成质量本身。

---

## 1. 当前基线

当前仓库中已经具备的能力：

- `Docling` 强制模式解析 PDF
- PDF markdown 清洗
- 图资产提取、落库和上下文定位
- PDF 审计脚本
- 文本层公式候选识别与 LaTeX hint
- 本地 `pix2tex` 作为可选公式 OCR 增强层

相关实现位置：

- [backend/app/services/parsing/docling_parser.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/docling_parser.py)
- [backend/app/services/parsing/markdown_cleaner.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/markdown_cleaner.py)
- [backend/app/services/parsing/formula_candidates.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/formula_candidates.py)
- [backend/app/services/parsing/formula_regions.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/formula_regions.py)
- [backend/app/services/parsing/formula_ocr.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/formula_ocr.py)
- [backend/app/services/parsing/pdf_audit.py](/Volumes/thunder/code/RAG_test/backend/app/services/parsing/pdf_audit.py)
- [backend/scripts/pdf_parse_audit.py](/Volumes/thunder/code/RAG_test/backend/scripts/pdf_parse_audit.py)

当前验收命令：

```bash
make pdf-audit PDF='/绝对路径/文件.pdf'
make pdf-audit PDF='/绝对路径/文件.pdf' FORMULA_OCR_BACKEND=pix2tex FORMULA_OCR_MAX_ASSETS=2 FORMULA_OCR_MAX_REGIONS_PER_ASSET=3
```

---

## 2. 已验证现状与痛点

以下结论来自真实样本：

- 样本文件：[sample-pulp-mill-lci-solution.pdf](/Volumes/thunder/code/RAG_test/sample-pulp-mill-lci-solution.pdf)
- 最新审计报告：
  - [上电某造纸企业高浓磨机项目成套方案VerA.audit.md](/Volumes/thunder/code/RAG_test/backend/data/pdf_audits/上电某造纸企业高浓磨机项目成套方案VerA.audit.md)
  - [上电某造纸企业高浓磨机项目成套方案VerA.audit.json](/Volumes/thunder/code/RAG_test/backend/data/pdf_audits/上电某造纸企业高浓磨机项目成套方案VerA.audit.json)

### 2.1 Docling 文本提取“可用但不可靠到可直接信任”

- 纯文本和普通技术说明段落大体可提取
- 复杂公式、上下标、特殊符号、混排行仍会损伤
- 当前真实样本中仍存在 `garbled_formula_count = 1`

### 2.2 图不能再丢，但“保住图”不等于“理解图”

- 当前样本可提取 `87` 个图片资产
- 其中 `9` 个被筛为更像工程图/波形图/示意图的大图资产
- 已能保存：
  - 页码
  - bbox
  - heading
  - 前后文
  - source_ref

结论：

- 图资产保留已经达标
- 图语义理解仍然远未达标

### 2.3 文本层公式 hint 有价值，但不能替代 OCR

- 对文本层上标/下标可生成基础 LaTeX hint
- 例如 `TH₁ = V² -> TH_{1} = V^{2}`
- 这适合做辅助标记，不适合当最终标准化公式结果

### 2.4 `pix2tex` 可运行，但在电气工程图上容易 hallucinate

已经验证过两种模式：

1. 整图 OCR
- 有时会返回看似“成功”的 LaTeX
- 但其中大量结果是把原理图/波形图错误解释成复杂公式
- 不能直接采信

2. 局部裁剪 OCR
- 比整图 OCR 更诚实
- 当前真实样本中 `2` 次尝试、`0` 次可自动采信成功、`2` 次都被标为 `manual_review`
- 说明裁剪后暴露出了真实质量边界：当前本地 OCR 仍不足以可靠恢复这类图中的公式/参数

结论：

- `pix2tex` 目前只适合作为“人工复核前的候选建议”
- 不适合直接作为自动清洗真值

### 2.5 复杂工程 PDF 当前必须保留人工复核关口

对于以下内容，当前系统不能自动保证保真：

- 原理图
- 接线图
- 波形图
- 谐波图
- 点阵图
- 电路设计图
- 图片中的公式
- 图片中的英文字母上下标

因此当前主张不变：

- 复杂图表和图中公式必须走 `review_required`
- 不应因为 OCR 有输出就取消人工确认

---

## 3. 当前核心痛点

### 3.1 公式与图像问题高度耦合

当前很多“公式问题”本质上不是字符替换问题，而是：

- 公式在图片里
- 公式混在原理图或波形图里
- 公式区域和普通图形边界不清晰

所以“全文字符修复”无法解决主要问题。

### 3.2 需要先做“定位”，再谈“识别”

在复杂工程 PDF 中，价值最高的不是立刻换更强 OCR，而是：

- 先更准确地找到公式区域
- 再把局部区域送去 OCR

错误区域输入给再强的 OCR，也会输出伪精确结果。

### 3.3 真实风险不是“没结果”，而是“假结果”

当前最危险的情况不是 OCR 失败，而是：

- OCR 返回了看起来很像 LaTeX 的结果
- 结果实际和原图无关
- 后续系统把它当真值继续传播

因此质量闸门必须保守，宁可判为 `manual_review`，也不要误报成功。

### 3.4 不同 PDF 类型需要分流

至少应区分：

- 文本型数字 PDF
- 图文混排工程 PDF
- 扫描型 PDF

这三类文档的最优解析路径不同，不能期待一个 parser 或一个 OCR 模型通吃。

---

## 4. 已锁定的实现原则

后续优化默认遵循以下原则：

1. 不允许静默回退到“二进制硬解码文本”来评估复杂 PDF 质量  
   评估复杂工程 PDF 时必须强制 `Docling` 或明确报错。

2. 图资产必须保留原始定位  
   任意图资产都要能回溯到页码、bbox、上下文和原始文件。

3. OCR 结果默认是候选，不是事实  
   除非后续引入强置信度机制，否则公式 OCR 结果只作为辅助信息。

4. parser 与 OCR 保持解耦  
   `Docling` 负责主解析，公式 OCR 是增强层，而不是强绑定替换。

5. 审计优先于自动使用  
   先让审计报告能暴露问题，再决定哪些结果可以接入主流程。

---

## 5. 后续高价值优化方向

按性价比排序，建议优先级如下。

### P0：高价值且应优先考虑

#### 5.1 文档类型分流

目标：

- 在解析前识别 PDF 属于文本型、图文混排型还是扫描型
- 为不同类型走不同策略

价值：

- 这是后续一切优化的前提
- 能避免把扫描件、原理图型 PDF 误交给文本 parser 直接进入主流程

建议实现：

- 基于 text density、image density、page object 特征做轻量分类
- 分类结果写入 `raw_documents` / 审计报告

#### 5.2 公式区域定位增强

目标：

- 让裁剪区域更接近真正的公式，而不是整张原理图中的任意横条

价值：

- 比直接更换 OCR backend 更划算
- 这是提升 `pix2tex` 成功率的关键前置条件

建议实现：

- 结合图像启发式和文本上下文
- 结合 `Docling` 的 caption / heading / 周边块
- 优先检测：
  - 显式包含“公式 / equation / latex”的区域
  - 公式样式短横带
  - 带大量 `=`, `^`, `_`, 希腊字母的局部图块

#### 5.3 审计报告增加“是否可自动采信”判定

目标：

- 不只显示 OCR 结果，还给出是否建议进入后续流程

价值：

- 避免误把伪公式结果带入生成链路

建议实现：

- 将 `succeeded` 再细分为：
  - `trusted_candidate`
  - `review_required_candidate`

#### 5.4 建立真实工程 PDF 基准集

目标：

- 用 5-10 份脱敏后的真实工程文档做固定回归

价值：

- 没有真实样本基准，就无法判断优化是否真的有效

建议内容：

- 文本型说明书
- 图文混排技术方案
- 扫描件
- 含大量波形图
- 含大量原理图
- 含大量表格图片

### P1：高价值，但可在 P0 后实施

#### 5.5 Shadow Parser A/B：Docling vs Marker/Surya

目标：

- 不替换主 parser，先做对照实验

价值：

- 帮助判断是否值得在后续引入更重的 OCR/布局栈

建议方式：

- 对同一批真实 PDF 同时跑：
  - `Docling`
  - `Marker + Surya`
- 对比：
  - heading 保真
  - 表格保真
  - 图资产保真
  - inline math 保真
  - 中文段落质量

#### 5.6 公式 OCR backend 的候选扩展

候选：

- `pix2tex`
- `Mathpix`
- `Marker/Surya latex OCR`

建议：

- 当前先不要直接接更多 backend 到主流程
- 应先做离线对照评估，再决定是否升级

#### 5.7 图中公式专用复核页

目标：

- 前端提供一个“图资产 + OCR 结果 + 原文上下文”的人工复核页

价值：

- 比盲目追求全自动更符合当前阶段收益

建议能力：

- 左侧原图
- 中间自动裁剪区域
- 右侧 OCR 候选 LaTeX
- 人工确认 / 放弃 / 手动录入

### P2：中长期方向

#### 5.8 扫描件专用 OCR 流程

适用场景：

- 低质量扫描 PDF
- 图片文字占绝大多数的资料

建议：

- 与文本型 PDF 分流，不要混在同一主链路

#### 5.9 图表语义化

目标：

- 不只是保图，还能结构化部分图表信息

适用对象：

- 趋势图
- 谐波图
- 参数图
- 外形尺寸图

当前判断：

- 这是高难度增强项，不建议在近期主线投入过多资源

---

## 6. 可执行 Backlog

### 6.1 立即做

适合在近期进入排期的事项：

1. 建立真实工程 PDF 基准集  
   产出：脱敏样本集、样本标签、固定回归命令、基线报告。

2. 文档类型分流  
   产出：`文本型 / 图文混排型 / 扫描型` 分类规则，以及写入审计报告的分类结果。

3. 公式区域定位增强  
   产出：更稳定的局部裁剪策略，减少“整张图被误送 OCR”的情况。

4. 审计可信度分级  
   产出：`trusted_candidate / review_required_candidate / reject` 之类的更细粒度判定。

### 6.2 合适时再做

价值高，但应建立在前一批工作完成后再推进：

1. Shadow Parser A/B：`Docling vs Marker/Surya`  
   前提：已有真实样本基准集，且审计指标稳定。

2. 公式 OCR backend 离线对照评估  
   前提：已确认当前 `pix2tex` 的主要失败模式和样本分布。

3. 图中公式专用复核页  
   前提：后端输出结构基本稳定，避免前端复核页频繁返工。

### 6.3 暂不做

当前阶段不建议进入实施排期：

1. 整图 `pix2tex` 扩覆盖  
   原因：当前实测已证明容易产生伪公式结果。

2. 直接把 OCR 结果写回主解析文本  
   原因：质量不够稳定，风险高于收益。

3. 未建立基准集前更换主 parser  
   原因：没有对照标准，容易引入新的不确定性。

4. 自动理解复杂原理图语义  
   原因：投入大、风险高、短期回报低。

---

## 7. 后续实施建议顺序

推荐顺序：

1. 真实 PDF 基准集
2. 文档类型分流
3. 公式区域定位增强
4. 审计可信度分级
5. Shadow Parser A/B
6. 前端图公式复核页
7. 再决定是否升级 OCR backend

一句话总结：

`先把问题看清，再决定换模型；先把复核链路做好，再决定追求自动化。`

---

## 8. 当前阶段结论

当前 PDF 数据清洗链路已经达到：

- 文本说明部分可用
- 图资产不丢
- 复杂公式和工程图风险可暴露
- 本地公式 OCR 可作为辅助尝试

但尚未达到：

- 图片中公式可自动可靠恢复
- 原理图/波形图可自动结构化理解
- 复杂工程 PDF 可无人工复核直接进入高质量生成

因此，当前阶段对复杂真实客户 PDF 的合理定位是：

`可做受控解析与审计，不应假设其已具备可全自动高保真清洗能力。`
