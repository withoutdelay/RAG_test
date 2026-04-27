# Smoke Checklist

## Core Availability

- [ ] Health endpoint returns 200
- [ ] Frontend entry page renders without blocking errors
- [ ] Core API auth flow (login/token refresh) works

## Business Critical Flows

- [ ] Primary create/read/update flow passes
- [ ] One payment/order workflow passes (if applicable)
- [ ] One admin workflow passes

## Reliability and Security

- [ ] Error budget burn within expected range
- [ ] No new critical security findings
- [ ] Alerting rules and dashboards are active
