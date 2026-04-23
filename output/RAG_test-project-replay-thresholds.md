# Project Replay Threshold Recommendation

## Summary

- Total snapshots: `9`
- Eligible healthy snapshots: `9`
- Trace metric samples: `0`
- Retrieval final metric samples: `0`
- Retrieval semantic metric samples: `0`
- Retrieval rerank metric samples: `0`
- Retrieval mode counts: `{'baseline_fallback': 9, 'full_section': 9, 'section_pack': 45}`

## Recommended Flags

- `--min-trace-sections`: `None`
- `--min-avg-retrieval-final`: `None`
- `--min-avg-retrieval-semantic`: `None`
- `--min-avg-retrieval-rerank`: `None`

## Basis

- Quantile: `0.2`
- Score safety margin: `0.02`
- Minimum samples: `3`

## Notes

- insufficient healthy snapshot samples for trace-section threshold: 0 < 3
- insufficient healthy snapshot samples for retrieval final score threshold: 0 < 3
- insufficient healthy snapshot samples for retrieval semantic score threshold: 0 < 3
- insufficient healthy snapshot samples for retrieval rerank score threshold: 0 < 3
- 9 healthy replay snapshots still use legacy retrieval telemetry; rerun evaluate_project_snapshot.py to refresh history with structured retrieval metrics
