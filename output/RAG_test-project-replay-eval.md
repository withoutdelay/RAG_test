# Project Replay Evaluation

- Project: `某钢铁集团高炉鼓风机 LCI 软起动改造项目`
- Project ID: `359acbab-0acd-4c19-aa31-7f6d50512a12`
- Draft Version: `3`
- Project Status: `EXPORTED`
- Snapshot Generated At (UTC): `2026-04-20T14:28:02.542025+00:00`
- Source: `http://127.0.0.1:8000/api/v1`

## Snapshot Summary

- Health: `healthy`
- Total sections: `7`
- Generated sections: `7`
- Passed sections: `7`
- Average quality score: `0.96`
- Open blocking review tasks: `0`
- Open advisory review tasks: `1`
- Fallback sections: `none`
- AI Wiki prior hit sections: `1, 2, 3, 4, 5, 8, 14`

## Section Matrix

| Section | Title | Status | Quality | Score | Path | Refinement | Issues |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 项目概述与建设目标 | generated | passed | 0.96 | llm_write | unknown | TITLE_REFINEMENT,TERM_CONSISTENCY,SCOPE_PRECISION |
| 2 | 工厂设计环境与边界条件 | generated | passed | 0.96 | extractive_reuse_deterministic | deterministic_assembled | WORDING_BASIS_INTERNALIZED,HEADING_NAMING_CONSISTENCY,TERM_MATCHING |
| 3 | 供电系统条件与负载参数 | generated | passed | 0.96 | extractive_reuse_deterministic | deterministic_assembled | TITLE_SCOPE_WEAK,HEADING_GENERIC,CONTENT_EXPECTATION_GAP |
| 4 | 总体方案与系统组成 | generated | passed | 0.96 | extractive_reuse_llm_finalize | rewrite_applied | TITLE_DUPLICATION,TERM_CONSISTENCY,TECHNICAL_RIGOR |
| 5 | 高炉鼓风机 LCI 软起动主回路方案 | generated | passed | 0.96 | extractive_reuse_llm_finalize | rewrite_applied | TITLE_DUPLICATION,TITLE_SCOPE_MISMATCH,GOAL_COVERAGE_GAP |
| 8 | LCI 变频器技术配置 | generated | passed | 0.96 | extractive_reuse_llm_finalize | rewrite_applied | TITLE_HIERARCHY_INCONSISTENT,SECTION_GOAL_PARTIAL_COVERAGE,TECHNICAL_WORDING_GENERALIZED |
| 14 | 供货范围与配套清单 | generated | passed | 0.96 | extractive_reuse_llm_finalize | rewrite_applied | TITLE_STYLE_MINOR,CONTENT_GRANULARITY_MINOR,TONE_FORMALITY_MINOR |

## Runtime Artifacts

- Validation status: `passed`
- Latest export status: `succeeded`
- Latest export file: `某钢铁集团高炉鼓风机-LCI-软起动改造项目-draft-v3.md`

## Comparison

- Baseline snapshot: `/Volumes/thunder/code/RAG_test/output/project-replay-history/359acbab-0acd-4c19-aa31-7f6d50512a12-v3-20260420142601.json`
- Baseline generated at: `2026-04-20T14:26:01.981806+00:00`
- Changed sections: `0`
- Regressions: `0`
- Improvements: `0`
- Average quality score delta: `0.0`
