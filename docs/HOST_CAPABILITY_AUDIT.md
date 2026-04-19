# Host Capability Audit

## 官方依据

- Codex CLI / Codex desktop 作为主宿主，负责代码实现、预览确认和治理证据落盘。
- 宿主需要支持文件编辑、命令执行、预览访问和持续会话恢复。

## 宿主接入结论

- 当前实现已经在独立 worktree 中完成文档、前端、后端和质量闭环。
- 预览确认通过 IAB 完成，核心页面为 `/projects/:id/solution`。
- 交付证据统一写入 `output/` 与 `.super-dev/review-state/`。

## 推荐审计命令

- `super-dev integrate smoke`
- `super-dev review docs`
- `super-dev release proof-pack`
