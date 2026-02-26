# Follow-Up Hardening Plan

Date: 2026-02-24
Owner: Backend Platform

## Goal
Increase production confidence with measurable SLOs, safer rollout mechanics, and DB-backed end-to-end validation.

## Track A: SLOs + Alerts
1. Define baseline SLOs:
- Availability: >= 99.9%
- p95 `/chat` latency: <= 2.5s (read path), <= 4.0s (write path)
- Stream terminal envelope success: 100%
- Timeout rate: < 1%
2. Add metrics emission for:
- request count by status
- stage latency buckets from `stage_timings_ms`
- idempotency replay rate
- mutation authorization deny rate
3. Configure alerts:
- high error rate
- high timeout rate
- p95 latency breach
- anomaly in mutation denials

Deliverables:
- alert rules in monitoring stack
- runbook with mitigation steps

## Track B: Canary + Rollback Automation
1. Define canary strategy:
- 5% -> 25% -> 100% rollout stages
2. Automate health gates between stages:
- SLO threshold checks
- stream contract checks
3. Add rollback action:
- one-command rollback to previous stable revision

Deliverables:
- deployment pipeline gate config
- rollback SOP + command examples

## Track C: DB-Backed E2E Regression Suite
1. Create deterministic staging fixture dataset.
2. Add end-to-end tests for top prompt groups:
- task status queries
- summary follow-up queries
- pagination/load-more
- mutation flow authorization
- prompt injection rejection
3. Assert full response contract:
- token/error/result ordering
- trace_id presence
- stage_timings_ms presence

Deliverables:
- `tests/e2e/` suite
- CI stage for nightly + pre-release runs

## Suggested Execution Order (2 Weeks)
1. Week 1: Track A (SLOs/alerts) + baseline dashboards
2. Week 1 end: Track B canary gates (manual rollback first)
3. Week 2: Track C DB-backed e2e + nightly CI
4. Week 2 end: make canary rollback fully automated

## Success Criteria
- Alerting catches failures before user reports
- Canary gates prevent bad rollout from reaching 100%
- E2E suite blocks regressions in core chat flows and safety policies
