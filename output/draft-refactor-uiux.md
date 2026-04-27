# Draft Page Refactor — UI/UX Design Specification

## 1. Design System Locks

### 图标库
- **Lucide React** (v0.577) — 已安装，不引入新图标库
- 关键图标使用：
  - `FileText` — 文稿相关
  - `Eye` — 查看/预览
  - `ExternalLink` — 新窗口
  - `Layers3` — 分段
  - `GalleryVerticalEnd` — 图表集合
  - `Target` — 追踪/定位
  - `ChevronDown` / `ChevronRight` — 折叠展开
  - `Info` — 信息提示
  - `Search` — 检索追踪
  - `X` — 关闭

### 字体系统
- **标题字体**: `var(--font-heading)` → 系统 sans-serif (与 shadcn 一致)
- **正文字体**: `var(--font-sans)` → Geist Sans (Next.js 16 默认)
- **代码字体**: `var(--font-mono)` → Geist Mono

### Design Token System
继承现有 shadcn tokens，不引入新 token：

```
// 颜色（使用 Tailwind 内建 slate 色系）
heading-primary:     slate-900
heading-secondary:   slate-800
heading-tertiary:    slate-700
body-text:           slate-700
body-muted:          slate-500
decoration-line:     blue-500 (H2 左侧装饰线)
accent-surface:      blue-50 (blockquote 背景)

// 间距
heading-1-mt:        2.5em
heading-2-mt:        2em
heading-3-mt:        1.5em
paragraph-gap:       1.25em
line-height-body:    1.85
```

### 组件生态
- **shadcn/ui v4**: Button, Badge, Card, Dialog, Tabs, TabsList, TabsTrigger, TabsContent, Input, Textarea, Progress
- 不引入新的第三方 UI 组件
- 新增组件均为 project-level 组件，放在 `components/editor/` 下

## 2. 客户稿视图排版规范 (MarkdownArticle Enhanced)

### 2.1 标题层级视觉规范

#### H1 — 一级标题
```
字号: text-[1.875rem] (30px)
字重: font-bold
颜色: text-slate-900
上间距: mt-10 (first-child mt-0)
下间距: mb-4
附加: 底部 1px 分隔线 (border-b border-slate-200 pb-3)
```

#### H2 — 二级标题
```
字号: text-2xl (24px)
字重: font-semibold
颜色: text-slate-800
上间距: mt-8
下间距: mb-3
附加: 左侧 3px 蓝色装饰线 (border-l-[3px] border-blue-500 pl-4)
```

#### H3 — 三级标题
```
字号: text-xl (20px)
字重: font-semibold
颜色: text-slate-700
上间距: mt-6
下间距: mb-2
```

#### H4+ — 四级及以下
```
字号: text-lg (18px)
字重: font-medium
颜色: text-slate-700
上间距: mt-5
下间距: mb-2
```

### 2.2 正文排版规范

```
字号: text-base (16px)
行高: leading-[1.85]
颜色: text-slate-700
段间距: [&_p+p]:mt-5
```

### 2.3 列表排版规范

```
有序列表: list-decimal
无序列表: list-disc
缩进: pl-6
项间距: [&_li]:my-2
嵌套: 自动继承缩进
```

### 2.4 引用块规范

```
背景: bg-blue-50/60
左边框: border-l-4 border-blue-400
内边距: px-5 py-4
圆角: rounded-r-xl
字色: text-slate-700
```

### 2.5 表格规范
保持现有样式不变（已有圆角 + 边框 + 头部背景）。

## 3. 右侧面板重构 — 有限卡片 + 查看全部

### 3.1 面板整体布局

```
Right Panel (380px)
+-------------------------------------+
| Citations (5)               [badge] |  <-- 区块标题 + 总数
+-------------------------------------+
| [card] 总体方案                      |  <-- 卡片 1（可点击 -> 详情浮窗）
|   reuse_block · 0.87                |
+-------------------------------------+
| [card] 系统架构设计                   |  <-- 卡片 2
|   evidence · 0.82                   |
+-------------------------------------+
| [card] 供电方案                      |  <-- 卡片 3
|   reuse_block · 0.79                |
+-------------------------------------+
| [查看全部 5 个候选材料 ->]            |  <-- 超过 3 个时显示
+-------------------------------------+
|                                     |
| Tables & Figures (4)        [badge] |
+-------------------------------------+
| [card] 系统一次回路图                 |  <-- 最多 3 个
|   figure · 0.91                     |
+-------------------------------------+
| [card] 设备参数表                    |
|   table · 0.85                      |
+-------------------------------------+
| [card] 拓扑示意图                    |
|   figure · 0.78                     |
+-------------------------------------+
| [查看全部 4 个候选材料 ->]            |
+-------------------------------------+
|                                     |
| Assumptions (1)             [badge] |
+-------------------------------------+
| 假设电压等级为10kV                   |
+-------------------------------------+
|                                     |
| [查看检索追踪]              [toggle] |  <-- 默认折叠
+-------------------------------------+

(wireframe 中的图标用文字代替，实际实现使用 Lucide 图标)
```

### 3.2 候选材料卡片规范（面板内）

```
+--------------------------------------+
| [icon] Title text...                 |
|   [Badge: type]  Score: 0.87         |
|   Source document / heading path     |
+--------------------------------------+

高度: ~72px (py-3 px-3)
背景: bg-white hover:bg-slate-50
边框: border border-slate-200
圆角: rounded-xl
间距: 卡片之间 gap-2
交互: cursor-pointer, hover 高亮, 点击打开 MaterialDetailDialog
```

### 3.3 已选中状态
- 边框: `border-blue-300`
- 背景: `bg-blue-50`
- 左侧装饰: `border-l-2 border-blue-500`

### 3.4 "查看全部"按钮规范
```
样式: text-sm text-blue-600 hover:text-blue-800 font-medium
图标: ChevronRight (lucide)
位置: 在最后一个卡片下方，右对齐
交互: 点击打开 AllCandidatesDialog
```

## 4. 单项详情浮窗 (MaterialDetailDialog)

### 4.1 尺寸规范

```
宽度: max-w-5xl (1024px), min-w-[600px]
高度: max-h-[85vh]
圆角: rounded-2xl
阴影: shadow-2xl
背景: bg-white
遮罩: bg-black/50 backdrop-blur-sm
```

### 4.2 浮窗内部布局

```
+-- Header -----------------------------------------+
| [icon] Title                                [x]   |
| Source Document / Heading Path                     |
| [type badge] [score badge] [status badge]         |
+---------------------------------------------------+

+-- Body (scrollable) ------------------------------+
|                                                    |
|  -- 内容预览 -----------------------------------  |
|  | (MarkdownArticle rendered content)            | |
|  | 或者图片预览 (对于 visual assets)             | |
|  ------------------------------------------------ |
|                                                    |
|  -- 详细信息 -----------------------------------  |
|  | 来源文档: xxx.pdf                             | |
|  | 章节路径: 总体方案 > 系统架构                  | |
|  | 页码: Page 12                                 | |
|  | 视觉角色: system_diagram                      | |
|  ------------------------------------------------ |
|                                                    |
|  -- 检索分数 -----------------------------------  |
|  | +-------+-------+-------+-------+             | |
|  | |text   |visual |struct |final  |             | |
|  | | 0.72  | 0.65  | 0.43  | 0.87 |             | |
|  | +-------+-------+-------+-------+             | |
|  | 命中原因: xxx                                 | |
|  | 视觉通道: xxx                                 | |
|  ------------------------------------------------ |
|                                                    |
+---------------------------------------------------+

+-- Footer -----------------------------------------+
|              [选中/取消]  [重写]  [插入正文]        |
+---------------------------------------------------+
```

### 4.3 详情分区样式规范

每个分区使用统一的容器：
```
背景: bg-slate-50/70
边框: border border-slate-200
圆角: rounded-xl
内边距: p-4
标题: text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3
```

分数卡片使用网格：
```
grid grid-cols-2 sm:grid-cols-4 gap-3
每个卡片: rounded-lg bg-white border border-slate-200 p-3
数值: text-lg font-bold text-slate-900
标签: text-[11px] uppercase text-slate-500
```

## 5. 全部候选材料浮窗 (AllCandidatesDialog)

### 5.1 尺寸规范

```
宽度: max-w-6xl (1152px)
高度: max-h-[85vh]
圆角: rounded-2xl
阴影: shadow-2xl
背景: bg-white
遮罩: bg-black/50 backdrop-blur-sm
```

### 5.2 浮窗内部布局

```
+-- Header -----------------------------------------+
| [icon] 全部候选材料 (N)                       [x]  |
| Section: 章节标题                                  |
+---------------------------------------------------+

+-- Body (scrollable) ------------------------------+
|                                                    |
|  == Citations (M) ==============================  |
|  +--------+ +--------+ +--------+ +--------+     |
|  | card 1 | | card 2 | | card 3 | | card 4 |     |
|  |        | |        | |        | |        |     |
|  +--------+ +--------+ +--------+ +--------+     |
|  (每个卡片可点击 -> 展开详情)                       |
|                                                    |
|  == Recommended Assets (K) =====================  |
|  +--------+ +--------+ +--------+                 |
|  | asset1 | | asset2 | | asset3 |                 |
|  |        | |        | |        |                 |
|  +--------+ +--------+ +--------+                 |
|                                                    |
|  == More Candidates (J) ========================  |
|  +--------+ +--------+                            |
|  | cand1  | | cand2  |                            |
|  |        | |        |                            |
|  +--------+ +--------+                            |
|                                                    |
+---------------------------------------------------+

+-- Footer -----------------------------------------+
|                                          [关闭]    |
+---------------------------------------------------+
```

### 5.3 分类区块样式

```
区块标题: text-sm font-semibold text-slate-800, 带计数 Badge
区块背景: bg-slate-50/50
区块边框: border border-slate-200 rounded-xl
区块内边距: p-4
卡片布局: 垂直堆叠 (space-y-2), 每个卡片类似面板内的卡片规范
```

### 5.4 卡片展开行为
- 点击某个卡片后，该卡片在浮窗内展开显示完整详情（内联展开）
- 展开时显示完整的内容预览、图片、分数细节
- 再次点击或点击其他卡片时，当前卡片收起

## 6. 交互状态矩阵

| 交互 | 触发 | 效果 |
|------|------|------|
| hover 候选卡片 | 鼠标悬停 | bg-slate-50, 轻微 shadow |
| click 候选卡片（面板内） | 点击 | 打开 MaterialDetailDialog |
| click "查看全部" | 点击按钮 | 打开 AllCandidatesDialog |
| click 候选卡片（全部浮窗内） | 点击 | 内联展开详情 |
| 折叠/展开区块 | 点击区块标题 | 动画展开/收起列表 |
| 选中引用 | 浮窗内按钮 | 卡片显示蓝色选中态 |
| 关闭浮窗 | ESC / 点击遮罩 / X 按钮 | 平滑关闭 |
| 查看追踪 | 点击 "查看检索追踪" | 在面板内展开追踪详情 |

## 7. 页面骨架 (Updated)

```
+----------------------------------------------------------------+
| Section Drafts Header (不变)                                    |
| Draft Coverage Progress Bar (不变)                              |
+----------------------------------------------------------------+
| SectionBlock Card                                               |
| +-- Section Header (title + badges + action buttons) ---------+ |
| +-- Left Panel (flex-1) ------+-- Right Panel (380px) --------+ |
| | Tabs: 客户稿视图 | 分段 | 源码 | Citations (max 3 cards)      | |
| |                             | [查看全部 N 个]                 | |
| | [Enhanced MarkdownArticle]  | Tables & Figures (max 3 cards) | |
| |  - 清晰标题层级            | [查看全部 N 个]                 | |
| |  - 优化段落间距            | Assumptions (折叠)             | |
| |  - 装饰线标题              | Retrieval Trace (折叠)        | |
| |                             |                                | |
| +-----------------------------+--------------------------------+ |
+----------------------------------------------------------------+
```

## 8. Responsive Considerations

| 断点 | 行为 |
|------|------|
| xl+ (1280px+) | 双栏布局，左右并排 |
| < xl | 右侧面板移至下方 |
| 浮窗 < 768px | 浮窗全屏模式 |

