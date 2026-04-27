# Rollback Playbook

- Project: `RAG_test`
- CI/CD: `github`

## Trigger Conditions

- [ ] Error rate spike over agreed SLO threshold
- [ ] P95 latency regression over threshold
- [ ] Authentication or payment critical flow failure
- [ ] Data integrity check failed

## Rollback Procedure

1. Halt current rollout / traffic shift.
2. Restore previous stable image/tag.
3. Revert incompatible migrations (or apply compensating migration).
4. Re-run smoke checks on rolled-back version.
5. Communicate status and incident scope.

## Required Evidence

- Incident start/end timestamps
- Metrics snapshots before and after rollback
- Root-cause hypothesis
- Permanent fix owner and ETA
