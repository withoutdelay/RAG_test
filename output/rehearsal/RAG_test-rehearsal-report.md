# Launch Rehearsal Report

- Project: `RAG_test`
- Generated at (UTC): 2026-04-20T05:09:27.613643+00:00
- Score: 100/100
- Threshold: 80
- Passed: yes
- Failed checks: 0

## Checks

| Check | Result | Severity | Detail |
|:---|:---:|:---:|:---|
| Redteam Report | PASS | low | RAG_test-redteam.json, score=70/70, critical=0 |
| Quality Gate | PASS | medium | quality score=82 |
| Pipeline Metrics | PASS | low | success_rate=100 |
| Delivery Manifest | PASS | low | status=ready |
| Rehearsal Documents | PASS | low | launch/rollback/smoke docs ready |
| Migration Files | PASS | low | 2 migration files |
| CI/CD Files | PASS | medium | 2 files found |
| Governance Status | PASS | low | report=governance-report-20260420.md; validation=PASSED; knowledge_coverage=unknown |
| DNS Reachability | PASS | low | RAG_test.example.com -> 198.18.0.160 |
| SSL Certificate | PASS | low | 无法连接 RAG_test.example.com:443，跳过 SSL 检查 (placeholder domain) |
| Port Conflicts | PASS | low | 端口已被占用: 5432, 6379, 6333 (确保部署环境端口可用) |
| Rollback Readiness | PASS | low | rollback-playbook.md found; DB rollback SQL: 20260420_release_hardening_rollback.sql; CI/CD rollback step in cd.yml |
| Capacity Estimation | PASS | low | tier=large; 219 files/53043 lines; cpu=1000m-2000m; memory=512Mi-1Gi; replicas=3-5; langs=[Python=47624L, TypeScript=5401L, SQL=13L, JavaScript=5L]; db=detected |
| Release Risk Assessment | PASS | low | risk_level=MINIMAL; risk_score=0/100; passed=13/13; critical_fail=0; high_fail=0; medium_fail=0; recommendation=可以发布：风险极低 |

## Governance Status

- report=governance-report-20260420.md
- validation=PASSED
- knowledge_coverage=unknown
