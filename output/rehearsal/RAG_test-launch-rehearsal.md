# Launch Rehearsal

- Project: `RAG_test`
- Platform: `web`
- Frontend: `next.js`
- Backend: `fastapi`
- CI/CD: `github`

## Objectives

1. Validate deployment workflow end-to-end in a pre-production environment.
2. Verify rollback path within target recovery time.
3. Confirm observability and alert thresholds after release.

## Pre-flight Gate

- [ ] Red-team report passed (no critical blockers)
- [ ] Quality gate score >= 80
- [ ] Spec task execution report has no unresolved blockers
- [ ] Database migration dry-run passed
- [ ] CI pipeline green on release commit

## Rehearsal Timeline

1. Freeze release candidate commit.
2. Deploy to staging using same workflow as production.
3. Run smoke checklist and synthetic transactions.
4. Trigger rollback simulation.
5. Re-deploy and verify recovery.
6. Record outcomes and decision log.
