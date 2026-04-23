# Release Gate Report

- Project: `某钢铁集团高炉鼓风机 LCI 软起动改造项目`
- Project ID: `359acbab-0acd-4c19-aa31-7f6d50512a12`
- Generated at (UTC): `2026-04-22T17:42:13.013079+00:00`
- Status: `passed`
- Passed: `yes`
- Step Summary: `1/2` passed

## Steps

| Step | Result | Detail | Path |
|:---|:---:|:---|:---|
| Project Replay Gate | SKIPPED | skipped by flag | /Volumes/thunder/code/RAG_test/output/RAG_test-project-replay-eval.json |
| Quality Smoke | PASSED | ran 5 smoke tests | /Volumes/thunder/code/RAG_test/tests/test_quality_smoke.py |

## Project Replay Summary

- Health: `healthy`
- Sections Generated: `7/7`
- Sections Passed: `7/7`
- Regressions: `0`
- Changed Sections: `0`
- Baseline Snapshot: `/Volumes/thunder/code/RAG_test/output/project-replay-history/359acbab-0acd-4c19-aa31-7f6d50512a12-v3-20260420142601.json`

## Replay Governance Advisory

- Replay Import: inputs=`1`, loaded=`1`, imported=`0`, backfilled=`1`, structured=`0`, skipped=`0`, invalid=`0`
- Import Threshold Sources: `recommended_unavailable: 1`
- Replay Refresh: discovered=`1`, eligible=`0`, refreshed=`0`, skipped=`1`, failed=`0`, recommendation_requested=`yes`
- Refresh Skip Reasons: `current_draft_version=0: 1`
- Refresh Triggered Threshold Recommendation: `exit_code=0`
- Threshold Recommendation: healthy=`9`, trace_samples=`0`, final_samples=`0`, semantic_samples=`0`, rerank_samples=`0`
- Threshold Flags Ready: `no`
- Advisory Note: `9 healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics`

## AI Wiki Prior Advisory

- Holdout Eval: sections=`16`, case_candidates=`16`, prior_hit_sections=`11`, prior_hit_blocks=`29`, total_prior_boost=`3.55`, evaluation=`fast_heuristic_only`
- Ranking Delta: section_improved=`0`, section_worsened=`0`, equipment_improved=`0`, equipment_worsened=`0`, top1_changed=`1`, top1_lost=`0`
- Advisory Note: `holdout eval produced no regression signal; AI Wiki priors remain conservative`

## Quality Smoke

- Ran `5` smoke tests
- `test_ai_wiki_prior_governance_artifacts_are_wired`
- `test_core_reuse_first_artifacts_exist`
- `test_project_replay_governance_artifacts_are_wired`
- `test_project_replay_regression_guard_is_healthy`
- `test_runtime_contract_artifacts_exist`
