# WORKFLOW_GUIDE

## 流程阶段

`research -> docs -> docs_confirm -> spec -> frontend -> preview_confirm -> backend -> quality -> delivery`

## 关键门禁

- 文档门禁：`super-dev review docs`
- 质量门禁：`super-dev review quality`

## 中断恢复

- `super-dev run --resume`
- `super-dev status`
- `super-dev next`

## 当前仓库实践

- 自然语言里的“继续 / 下一步 / 确认”默认沿当前 Super Dev 流程推进
- 文档确认前不直接跳过到实现
- 质量返工后重新跑 `super-dev review quality`
