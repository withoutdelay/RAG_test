# Reuse-First 检索重构 UI/UX 规范

更新时间：2026-04-02

本轮重构以检索与生成质量为主，但后续实现前，调试与审阅界面的设计规范必须先冻结。

---

## 1. UI 定位

这是一个“检索与生成工作台”，不是聊天产品。

因此界面形态必须避免：

- Claude / ChatGPT 式侧边聊天壳
- 单列对话流主导交互
- 只展示最终成稿、不展示来源链路

界面应服务于：

- 章节命中判断
- 历史章节对比
- 证据与资产审阅
- 生成结果追溯

---

## 2. 冻结的设计系统

### 2.1 图标库

固定使用：

- `lucide-react`

禁止使用 emoji 作为功能图标或状态图标。

### 2.2 组件生态

固定使用：

- `shadcn/ui`
- Tailwind CSS v4
- `sonner` 作为 toast

### 2.3 字体系统

为了与现有仓库保持一致，本轮先冻结为：

- 主字体：`Inter`
- 等宽字体：现有 `--font-mono` / 系统等宽栈

后续如果要升级字体系统，必须先更新本文件再实施。

### 2.4 Design Tokens

沿用当前 neutral 基底，不切换到紫粉 AI 模板。

主色语义：

- Primary: 深墨色 / 工程蓝灰
- Success: 冷绿色
- Warning: 琥珀黄
- Danger: 工程红
- Info: 钢蓝色

视觉要求：

- 高信息密度
- 低装饰噪声
- 明确状态层级

---

## 3. 页面骨架

后续前端实现必须遵循以下骨架：

### 3.1 顶部阶段条

固定展示：

`Requirement -> Evidence -> Section Retrieval -> Assembly -> Generate -> Validate -> Export`

### 3.2 三栏主工作台

#### 左栏：章节与候选列表

展示：

- 当前目标章节列表
- 每章的检索模式
- top1/top2 命中强度
- 是否触发 `full-section mode`

#### 中栏：章节对比与生成主体

展示：

- 目标章节说明
- 命中的历史章节卡片
- 组装后的 reuse pack
- 最终生成正文

#### 右栏：来源与风险面板

展示：

- `selected_sections`
- `selected_blocks`
- `recommended_assets`
- `selection_reason`
- `token_budget`
- 风险提示

---

## 4. 关键交互规范

### 4.1 章节候选卡片

每张卡片必须显示：

- 历史文档名
- 章节路径
- 章节得分
- 主要命中原因
- source signals

状态样式：

- 强命中：深色边框 + success tag
- 可疑命中：warning tag
- 噪声命中：muted + 明显降级样式

### 4.2 Reuse Pack 检视区

不能只展示“生成前摘要”。

必须能区分：

- 章节摘要
- 正文块
- 表格块
- 图/公式资产

每条素材都要挂：

- 来源文档
- 来源章节
- 用途标签

### 4.3 Token Budget 与模式切换提示

当系统从 `full-section mode` 回退到 `section-pack mode` 时，必须显式提示原因，例如：

- token 超预算
- 标题冲突过高
- 章节噪声过多

不能静默降级。

### 4.4 结果追溯

生成后的章节视图里，必须能看到：

- 本章使用了哪些历史章节
- 哪些是正文块
- 哪些是资产补充
- 为什么这样选

---

## 5. 视觉风格要求

### 5.1 风格关键词

- Engineering desk
- Structured
- Auditable
- Dense but readable

### 5.2 不允许的风格

- 大面积渐变英雄区
- 空泛卡片墙
- 过度圆润的消费级 AI 模板
- 聊天气泡主导布局

### 5.3 表达重点

这个界面要传达的是：

- “系统在帮你找最像的历史章节”
- “系统给出了证据，不是替你拍板”

而不是：

- “AI 已经帮你写好了，直接发吧”

---

## 6. 后续实现必须具备的页面

### 6.1 Section Retrieval Review

用于审查每个目标章节命中了哪些历史章节。

### 6.2 Reuse Assembly Review

用于审查被选中的正文块与资产。

### 6.3 Generation Trace View

用于查看本章最终成稿与来源材料的映射。

---

## 7. UI/UX 结论

后续前端如果重做，必须围绕“章节检索工作台”展开，而不是退回聊天式交互。

冻结结论如下：

1. 图标库：`lucide-react`
2. 组件生态：`shadcn/ui` + Tailwind v4
3. 字体：保持当前 `Inter` 主字体
4. 页面骨架：顶部阶段条 + 三栏工作台
5. 核心表达：可追溯、可审阅、可比较，而不是黑盒生成
