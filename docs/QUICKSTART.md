# QUICKSTART

适用对象：第一次在当前仓库中使用 Super Dev 工作流的人。

## 最短路径

1. 在仓库根目录打开 Codex CLI。
2. 输入 `super-dev start --idea "你的需求"`，或者直接输入 `super-dev: 你的需求`。
3. 进入流程后，当前阶段是 `research`。
4. 等三文档产出完成后，执行确认，再继续实现。

## 成功标志

- 首条响应明确说明当前阶段是 `research`
- 仓库中出现 `output/*-research.md`、`output/*-prd.md`、`output/*-architecture.md`、`output/*-uiux.md`
- 文档确认后出现 `.super-dev/changes/*`

## 失败恢复

- 当前会话中断时，执行 `super-dev run --resume`
- 或在当前宿主里直接继续说“继续 / 下一步 / 确认”
- 文档门禁确认使用 `super-dev review docs`

## 备注

- 当前仓库的默认进入方式仍推荐 `super-dev: 你的需求`
- `super-dev start --idea` 主要用于从空白想法启动统一入口
