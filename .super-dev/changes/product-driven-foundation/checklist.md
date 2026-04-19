# Checklist

## Before Merge
- [x] `proposal / tasks / spec / plan` 已与本次实现范围对齐
- [x] 关键 MUST 场景已有自动化覆盖（catalog、solution API、真实回归 helper）
- [x] 风险、回滚和人工干预策略已写入 plan

## Release Readiness
- [x] `python3 -m compileall backend/app backend/scripts backend/tests` 已通过
- [x] 真实文档回归脚本已通过，报告输出到 `output/product-driven-real-proposal-regression.md`
- [x] 前端 Solution 工作台已完成主机内预览确认
- [ ] Super Dev CLI 通用 quality gate 与仓库命名/契约仍需进一步对齐
- [ ] 发布说明、宿主使用手册与产品审查文档尚未补齐
