# Host Usage Guide

宿主内执行建议：

- 对新需求先走 `super-dev start --idea`
- 进入流程后的第一反馈必须声明当前阶段是 `research`
- 文档确认、预览确认、质量返工都需要写回 `.super-dev/review-state/`
- 支持 Slash 入口 `/super-dev`
- 支持消息前缀 `super-dev:`

成功标志：

- 三文档、Spec/Tasks、前端预览、后端回归和交付证据都留在仓库里
- 用户可以在宿主内直接访问预览并继续推进

## Smoke Checklist

Smoke 验收至少覆盖：

- Solution 工作台可打开
- Catalog Explorer 与 Snapshot Compare 可见
- 真实方案回归脚本仍然通过

失败恢复：

- 若 quality gate 因命名或契约文件缺失误判，先补充正式 artifact，再重新执行治理命令
- 若预览地址存在宿主代理问题，记录真实可访问 URL 和运行截图/快照证据
