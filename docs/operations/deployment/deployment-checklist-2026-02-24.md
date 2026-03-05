# Deployment Checklist (2026-02-24 Hardening)

## Scope
Release scope includes reliability, safety, and observability hardening:
- stream terminal result contract
- timeout guards
- idempotency replay
- SQL mutation/system-table safety constraints
- role-based mutation authorization
- trace ID + stage timings in result payloads

## Pre-Deploy
1. Confirm target env has updated app config values:
- `MUTATION_ALLOWED_ROLES`
- `MUTATION_REQUIRE_EXPLICIT_PERMISSION`
- `QUERY_TIMEOUT_SECONDS`
- `MAX_PAGE_SIZE`
- `EXPORT_TEMP_DIR`
2. Verify DB user permissions match policy (least privilege; no unnecessary write grants).
3. Run quality gate:
- `make quality-gate`
4. Run DB-backed integration gate for release candidates:
- `RUN_MYSQL_E2E=1 pytest tests/e2e/mysql -q`
5. Confirm release notes reviewed:
- `RELEASE_NOTES.md`

## Deploy
1. Deploy backend image/artifacts to staging.
2. Run smoke tests in staging:
- `/health/live`
- `/health/ready`
- `/metrics`
- `/chat` normal query
- `/chat` error-path query (verify terminal `type=result`)
3. Confirm `/health/ready` reports `config=ok` and `database=ok` before traffic is shifted.
4. Validate mutation policy behavior:
- non-admin with `allow_mutations=true` should be denied
- allowed role with `allow_mutations=true` should pass validator path
5. Validate traceability:
- terminal response includes non-empty `trace_id`
- terminal response includes `stage_timings_ms`

## Production Rollout
1. Roll out with canary slice first (5-10% traffic).
2. Watch for 30 minutes:
- error-rate
- timeout-rate
- p95 latency
- mutation validation failure spikes
3. Expand to 100% if stable.

## Rollback Criteria
Rollback immediately if any of the following persists for >10 minutes:
- error-rate doubles from baseline
- timeout-rate > 2x baseline
- terminal result envelope missing in stream responses
- legitimate write operations blocked for admin workflows

## Post-Deploy Verification
1. Run quick chat regression prompts (top operational queries).
2. Confirm logs can trace a request end-to-end via `trace_id`.
3. Record deployment outcome and observed metrics in release log.
