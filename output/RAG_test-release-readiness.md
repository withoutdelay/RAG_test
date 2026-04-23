# Release Readiness Report

- Project: `RAG_test`
- Generated at (UTC): 2026-04-22T17:42:13.013079+00:00
- Score: 100/100
- Threshold: 85
- Passed: yes
- Failed checks: 0

## Checks

| Check | Result | Severity | Detail | Recommendation |
|:---|:---:|:---:|:---|:---|
| Version Alignment | PASS | low | versions=['2.3.0'] | 同步 pyproject、super_dev/__init__.py、README 与 README_EN 中的版本号。 |
| Documentation Coverage | PASS | low | all required docs and markers present | 补齐发布、宿主使用、Smoke 验收与恢复流程文档。 |
| Host Matrix Integrity | PASS | low | host targets, slash map, docs map and skill targets are aligned | 确保宿主目录、slash/skill 覆盖、官方文档映射保持一致。 |
| Host Coverage Depth | PASS | low | certified=2, compatible=8, stable=10, official_backed=18, total=21 | 继续提升关键宿主的稳定等级，并确保官方依据已核验。 |
| Runtime Boundary Rules | PASS | low | runtime host surfaces and review-state ignore rules are present | 明确忽略宿主运行时目录、review-state 与项目级宿主接入产物，避免本机生成文件混入仓库。 |
| Packaging Entrypoints | PASS | low | installer, entrypoint and pip/uv/update docs are present | 确保 pip/uv 安装、入口脚本和 update 命令文档一致。 |
| Release Change Spec | PASS | low | release change spec present | 将发布收尾任务沉淀到正式 change spec，避免口头跟踪。 |
| Spec Quality | PASS | low | change=reuse-first, score=100.0, level=excellent, blockers=0 | super-dev task run reuse-first |
| Scope Coverage | PASS | low | status=partial, coverage=0.0%, high_priority_gaps=0, missing=0, unknown=18 | 当前范围覆盖率未发现高优先级缺口。 |
| Delivery Closure | PASS | low | delivery closure evidence aligned | 先补齐 redteam / quality gate / task execution / product audit / ui contract / frontend runtime 证据，并确保它们指向同一轮交付。 |
| Governance: Report | PASS | medium | 治理报告已生成 (1 份) | - |
| Governance: Knowledge References | PASS | low | 知识引用报告 1 份, 知识缓存 0 份 | - |
| Governance: Performance Metrics | PASS | low | 效能度量文件 6 份 | - |
| Governance: ADR Records | PASS | low | ADR 决策记录 1 份 | - |
| Governance: Validation Results | PASS | low | 验证规则结果 2 份 | - |
| Governance: Project Replay Evaluation | PASS | medium | 项目回放评估 1 份, health=healthy, regressions=0 | 持续为同一项目版本保留历史 snapshot，并在发布前复跑一次对比。 |
| Governance: Release Gate | PASS | medium | 宿主侧 release gate 已通过, 1/2 steps passed | 将 release gate 纳入发布前固定动作，并保留最新 md/json 证据。 |
| Governance: Project Replay Refresh | PASS | low | 项目发现=1, 可刷新=0, 已刷新=0, 跳过=1, skip_reasons=current_draft_version=0: 1, recommendation_requested=True | 当 runtime 中出现 current_draft_version>0 的项目后，重跑 refresh_project_replay_history.py 刷新结构化 replay history。 |
| Governance: Project Replay Threshold Recommendation | PASS | low | healthy=9, trace_samples=0, final_samples=0, flags_ready=False, note=9 healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics | 当 replay history 已包含 structured retrieval metrics 时，将 recommendation 产出的阈值固化到 evaluate_project_snapshot.py 的默认 gate 参数。 |
| Governance: Project Replay Import | PASS | low | 输入=1, 载入=1, 导入=0, 回填=1, structured=0, skip_reasons=none, threshold_sources=recommended_unavailable: 1 | 当 runtime 没有可评估项目时，优先使用 import_project_replay_history.py 从外部 snapshot/eval JSON 补齐历史样本。 |
| Governance: AI Wiki Prior Evaluation | PASS | low | sections=16, prior_hit_sections=11, prior_hit_blocks=29, total_prior_boost=3.55, section_worsened=0, equipment_worsened=0, top1_lost=0, evaluation=fast_heuristic_only, note=holdout eval produced no regression signal; AI Wiki priors remain conservative | 重跑 evaluate_knowledge_wiki_priors.py，并根据 holdout delta 调整 AI Wiki prior boost 或收窄产品族/模块卡匹配范围。 |

## Governance Readiness

- Governance checks: 11/11 passed

| Check | Result | Detail |
|:---|:---:|:---|
| Governance: Report | PASS | 治理报告已生成 (1 份) |
| Governance: Knowledge References | PASS | 知识引用报告 1 份, 知识缓存 0 份 |
| Governance: Performance Metrics | PASS | 效能度量文件 6 份 |
| Governance: ADR Records | PASS | ADR 决策记录 1 份 |
| Governance: Validation Results | PASS | 验证规则结果 2 份 |
| Governance: Project Replay Evaluation | PASS | 项目回放评估 1 份, health=healthy, regressions=0 |
| Governance: Release Gate | PASS | 宿主侧 release gate 已通过, 1/2 steps passed |
| Governance: Project Replay Refresh | PASS | 项目发现=1, 可刷新=0, 已刷新=0, 跳过=1, skip_reasons=current_draft_version=0: 1, recommendation_requested=True |
| Governance: Project Replay Threshold Recommendation | PASS | healthy=9, trace_samples=0, final_samples=0, flags_ready=False, note=9 healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics |
| Governance: Project Replay Import | PASS | 输入=1, 载入=1, 导入=0, 回填=1, structured=0, skip_reasons=none, threshold_sources=recommended_unavailable: 1 |
| Governance: AI Wiki Prior Evaluation | PASS | sections=16, prior_hit_sections=11, prior_hit_blocks=29, total_prior_boost=3.55, section_worsened=0, equipment_worsened=0, top1_lost=0, evaluation=fast_heuristic_only, note=holdout eval produced no regression signal; AI Wiki priors remain conservative |
