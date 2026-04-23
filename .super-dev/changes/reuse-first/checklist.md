# Checklist

## Before Merge
- [x] 规范与任务一致
- [x] 所有 MUST 场景均有测试
- [x] 风险与回滚策略已确认
- [x] `tasks.md` 1-10 项实现已完成
- [x] 后端无数据库回归测试通过（199/199）
- [x] 前端 lint 与 build 通过
- [ ] 依赖 PostgreSQL 的 API 集成用例待在可用数据库环境复跑

## Release Readiness
- [x] Super Dev 全局质量门禁通过
- [ ] 观测指标与告警已配置
- [x] 发布说明已准备（`output/reuse-first-delivery-report.md`）

## Validation Evidence

- [x] `cd backend && ../.venv/bin/python -m unittest tests.test_document_api_helpers tests.test_parsing tests.test_build_case_library tests.test_case_library tests.test_case_retrieval tests.test_composition_helpers`
- [x] `cd frontend && npm run lint -- src/components/editor/SectionBlock.tsx src/lib/types.ts`
- [x] `cd frontend && npm run build`
- [x] `cd /Volumes/thunder/code/RAG_test && pytest -q --maxfail=1`
- [ ] `cd backend && ../.venv/bin/python -m unittest tests.test_api_phase2.Phase2ApiTests.test_document_upload_persists_figure_assets`

## Open Governance Gaps

- [ ] `super-dev product-audit` 仍为 `revision_required`（16/100）
- [ ] `super-dev quality -t redteam` 当前为 56/100，尚未过阈值 70
- [ ] `super-dev release proof-pack` 当前为 21/26，仍缺 delivery manifest 与 rehearsal
- [ ] `super-dev release readiness` 仍要求补齐通用治理文档、宿主运行说明、ignore rules 与效能度量
