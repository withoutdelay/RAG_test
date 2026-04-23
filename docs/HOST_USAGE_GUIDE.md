# HOST_USAGE_GUIDE

本仓库默认宿主为 Codex CLI。

## 触发方式

- `super-dev start --idea "你的需求"`
- Codex CLI：`super-dev: 你的需求`
- 兼容宿主：`/super-dev "你的需求"`

## Smoke 流程

1. 打开仓库根目录。
2. 输入 `super-dev: 继续当前项目的需求`
3. 检查首条响应是否明确写出当前阶段是 `research`
4. 检查后续是否遵守文档确认门

## 本地质量/发布门禁

- `super-dev host release-gate`
- `super-dev host project-replay-gate`
- `super-dev host quality-smoke`

说明：

- `release-gate` 会顺序执行项目回放 gate 和质量 smoke。
- `project-replay-gate` 只执行项目回放 gate，并以非零退出码暴露回归。
- 如果已经生成过 `output/RAG_test-project-replay-eval.json`，可不传 `--project-id`；否则显式传入项目 UUID。
- `release-gate` 每次运行都会刷新 `output/RAG_test-release-gate.md` 和 `output/RAG_test-release-gate.json`。

## 成功标志

- 会话保持在 Super Dev 流程内
- 文档确认前不直接编码
- `super-dev review docs` 可记录当前文档确认状态

## 常见恢复动作

- `super-dev run --resume`
- `super-dev review docs`
- `super-dev review quality`
