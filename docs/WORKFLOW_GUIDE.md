# Workflow Guide

当前工作流遵循：

`research -> docs -> docs_confirm -> spec -> frontend -> preview_confirm -> backend -> quality -> delivery`

关键命令：

- `super-dev review docs`：记录三文档确认状态
- `super-dev run --resume`：在当前仓库继续既有流程
- `super-dev review quality`：记录质量返工与通过状态

执行原则：

- 文档未确认前不进入实现
- UI 不满意时先改 `output/*-uiux.md`
- 质量问题先修复，再刷新 proof-pack 和 release readiness

