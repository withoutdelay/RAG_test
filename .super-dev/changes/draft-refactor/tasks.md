# Draft Page Refactor — Task List

## Phase 1: MarkdownArticle 排版增强
- [x] Task 1.1: 增强 MarkdownArticle.tsx 标题层级样式 (H1/H2/H3/H4+)
- [x] Task 1.2: 增强段落间距、列表缩进、引用块样式

## Phase 2: 组件提取
- [ ] Task 2.1: 从 SectionBlock.tsx 提取 sectionBlockUtils.ts (工具函数)
- [ ] Task 2.2: 从 SectionBlock.tsx 提取 AssetPreviewCard.tsx
- [ ] Task 2.3: 新建 MaterialDetailDialog.tsx (单项详情浮窗)
- [ ] Task 2.4: 新建 AllCandidatesDialog.tsx (全部候选分类浮窗)
- [ ] Task 2.5: 新建 RetrievalTracePanel.tsx (可折叠追踪面板)

## Phase 3: 右侧面板重构
- [ ] Task 3.1: 重构 SectionBlock.tsx 右侧面板 — Citations 区块 (max 3 + 查看全部)
- [ ] Task 3.2: 重构 SectionBlock.tsx 右侧面板 — Assets 区块 (max 3 + 查看全部)
- [ ] Task 3.3: 重构 SectionBlock.tsx 右侧面板 — Assumptions + Retrieval Trace 折叠
- [ ] Task 3.4: 整合测试，确保编辑、保存、重生成功能正常

## Phase 4: Build & Verify
- [ ] Task 4.1: 运行 build，修复编译错误
- [ ] Task 4.2: 浏览器运行时验证
