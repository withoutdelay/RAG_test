# Draft Page Refactor — Research Report

## Date: 2026-04-27

## 1. Problem Statement

用户提出了两个核心问题：

### 问题 A — 客户稿视图排版混乱
- 标题和正文在渲染时没有视觉层次区分
- 所有内容混成一片，无法快速扫读
- 缺少自动排版逻辑，生成内容直接以 raw markdown 渲染

### 问题 B — 候选材料区域堆叠过长
- 右侧面板（360px 固定宽度）内放置了 Citations、Retrieval Trace、Tables & Figures、Assumptions 四个大区块
- 每个区块内又嵌套完整的卡片展开，导致纵向滚动极长
- 影响左侧客户稿的视觉焦点和阅读沉浸感

## 2. Current Architecture Analysis

### 文件结构
```
frontend/src/
├── app/projects/[id]/editor/page.tsx      # Draft 页面入口 (219 行)
├── components/editor/
│   ├── SectionBlock.tsx                    # 核心组件 (1599 行，巨型文件)
│   ├── MarkdownArticle.tsx                 # Markdown 渲染器 (29 行)
│   └── sectionBlockHelpers.ts             # 工具函数 (69 行)
```

### 技术栈
- Next.js 16 + React 19 + TypeScript
- TailwindCSS v4 + shadcn/ui v4
- Lucide React icons
- react-markdown + remark-gfm
- Zustand for state management

### 当前 MarkdownArticle 问题
- 仅使用 `prose prose-slate` 类做基础排版
- 没有针对标题层级做视觉强化（字号、颜色、间距差异不够大）
- 没有自动识别"首行可能是标题"的逻辑
- 表格有自定义样式，但段落和标题的层次感不足

### 当前右侧面板问题
- 所有内容在 `xl:grid-cols-[minmax(0,1fr)_360px]` 的 360px 列里全量展开
- Citations、Retrieval Trace、Tables & Figures、Assumptions 依次堆叠
- 每个 AssetPreviewCard 直接全量渲染（含图片预览、详情、分数细节）
- 没有折叠/展开或弹窗机制

## 3. Similar Product Research

### 3.1 Notion
- 清晰的 heading 层级：H1 大字号 + 粗体 + 大间距，H2/H3 递减
- 正文使用中等行高（1.6–1.7），段间有明确空白
- 侧边面板使用可折叠/展开模式
- 评论和引用通过 hover 弹出浮窗

### 3.2 Google Docs
- 标题有明显的字号和颜色差异
- 使用工具栏标记 heading level
- 右侧引用面板可折叠
- 建议面板使用 drawer 模式

### 3.3 Craft
- 极致的排版美学，类似杂志风格
- 标题使用大幅间距 + 装饰线
- 图表使用 lightbox 模式点击放大
- 附件使用紧凑卡片 + 点击展开详情

### 3.4 Coda / AFFiNE
- 文档区域占满宽度，附属信息通过 slide-over panel 展示
- 候选材料使用缩略行 → 点击弹出全屏或大浮窗

## 4. Design Direction Summary

### 排版优化方向
1. **增强标题视觉层级**：H1–H3 使用递减但差异明显的字号、权重、颜色
2. **增大段间距**：标题前后各增加额外间距
3. **正文排版优化**：设定合理的 line-height (1.7+)、max-width 限制、首段缩进等
4. **自动排版逻辑**：自动检测 markdown 中的首个标题并给予特殊样式

### 候选材料交互优化方向
1. **右侧面板变为紧凑摘要列表**：每个 citation/asset 只显示标题 + 类型 + 分数
2. **点击后弹出大浮窗（Dialog/Overlay）**：自动适配屏幕，显示完整详情
3. **详情浮窗内分类分行展示**：Citations / Assets / Trace 各自分 tab 或分区
4. **减少右侧面板的视觉负载**：让用户先专注于客户稿内容

## 5. Key Constraints
- 不改变后端 API 接口
- 不引入新的依赖包（利用已有的 shadcn Dialog、Tabs 等组件）
- 保持所有现有功能完整（编辑、保存、重生成等）
- 保持 lucide-react 作为图标库
