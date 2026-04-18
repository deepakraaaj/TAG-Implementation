# TAG Production Readiness Plan

**Date:** 2026-04-16  
**Baseline:** 20% pass rate on 20-question VTS diagnostic (`diagnostics/results.jsonl`)  
**Goal:** Any database → production-grade natural-language CRUD assistant, zero metadata hand-holding required

---

## The One-Line Goal

> Drop in a DB URL, describe your domain in plain English, and get a chatbot that reads **and writes** your data correctly, safely, and reliably — for any team member, on day one.

---

## Current State Snapshot

| Dimension | Status | Evidence |
|-----------|--------|----------|
| Pass rate (VTS) | 20% (4/20) | [evidence](evidence/vts-diagnostic-report-final.txt) |
| CRUD | Blocked (`allow_mutations: false`) | `config/apps.local.yaml` |
| Tenant isolation | Broken — company_id omitted in 6/20 queries | Q01, Q05, Q10, Q11, Q14 |
| Soft-delete | Broken — `is_active` never filtered in joins | Q02, Q10, Q11, Q14 |
| Business glossary | Not reaching entity-detection in 3/20 queries | Q17, Q18, Q20 |
| Onboarding | Manual, multi-step, no validation gate | `scripts/onboard_domain.py` |
| Guardrails (intermediate/verify/validate) | Spec written, not implemented | `docs/dev/llm-guardrails/SPEC.md` |
| Observability | Health + Prometheus scaffolding only | `/health`, `/metrics` |
| Multi-DB onboarding | Works but no automated quality gate | `Makefile: onboard-domain` |

---

## Architecture Reference

```
User message
    │
    ▼
POST /chat  (FastAPI · app/api/v1/endpoints/chat.py)
    │
    ▼
ChatService  (app/services/chat/service.py · 3100 lines)
    │
    ├─ Redis session + idempotency cache
    ├─ DomainRegistry  (app/domains/registry.py)
    │       └─ app/domains/<domain>/
    │               ├─ generated/          ← OpenMetaData output
    │               └─ manual/             ← hand-tuned overrides
    │
    ▼
LangGraph orchestration  (app/assistant/orchestration/graph.py)
    │
    ├─ Intent / entity detection
    ├─ SQL builder
    ├─ SQL validator  (app/services/data/sql_validator.py)
    └─ SQL executor → response
```

**Failure insertion points found:**
- Entity detection runs before semantic alias resolution → FM7 failures
- SQL builder never injects implicit `company_id` / `is_active` → FM4 failures
- Enum mapping not applied when entity detection fails → FM5 failures
- `user_id` from request body leaks into `WHERE created_by` → hidden FM4

---

## Phases

| Phase | Theme | Target pass rate | Effort |
|-------|-------|-----------------|--------|
| **0 — Stop the bleeding** | Fix the 7 diagnostic failures on existing VTS domain | 70%+ | 2–3 days |
| **1 — CRUD** | Safe, audited INSERT / UPDATE / DELETE | Full CRUD working | 3–5 days |
| **2 — Any-DB onboarding** | Drop in URL → working assistant in < 10 min | < 10 min per domain | 1 week |
| **3 — Production hardening** | Security, resilience, observability | SLA-ready | 1 week |
| **4 — Guardrails** | Intermediate contract, verifier, validator | LLM claims verified | 2 weeks |

---

## Phase 0 — Fix the 7 Diagnostic Failures

### P0-1  Tenant filter missing (FM4 ×6) — highest impact

**Root cause:** No implicit filter directive forces `company_id` onto every SQL query.

**Files to change:**

`app/domains/<domain>/manual/sql_builder.json` — add:
```json
{
  "implicit_filters": {
    "company_id": "{company_id}"
  },
  "default_row_filters": {
    "is_active": 1
  },
  "metadata_key_blocklist": [
    "user_id", "created_by", "session_id", "trace_id",
    "idempotency_key", "domain_name", "app_id"
  ]
}
```

`app/domains/<domain>/manual/domain_knowledge.json` — add under `data_model_notes`:
```json
"tenant_isolation": "Every query MUST include WHERE <primary_table>.company_id = {company_id}. Never omit this.",
"soft_delete": "Rows with is_active = 0 are soft-deleted. Always add is_active = 1 to WHERE unless the user explicitly asks for inactive records."
```

**Enforce in code:** The SQL builder (`app/services/chat/service.py` or equivalent SQL construction path) must apply `implicit_filters` from `sql_builder.json` as post-generation injection — not rely on the LLM to remember them.

---

### P0-2  Business glossary not reaching entity detection (FM7 ×3)

**Root cause:** Entity detection checks a hardcoded vocabulary (`trips`, `vehicles`, `locations`). `semantics.json` aliases are loaded separately and consulted only after entity detection passes — so queries using `truck`, `journey`, `halt events` never reach the alias map.

**Files to change:**

`app/domains/<domain>/manual/entity_behavior.json` — extend `primary_keywords`:
```json
{
  "primary_keywords": [
    "trip", "trips", "journey", "journeys", "shipment", "shipments",
    "vehicle", "vehicles", "truck", "trucks", "bus", "buses",
    "exception", "exceptions", "alert", "halt", "overspeed",
    "location", "locations", "checkpoint",
    "route", "routes",
    "gps", "tracking", "live tracking", "current position"
  ]
}
```

`app/domains/<domain>/manual/glossary.json` — add missing business terms:
```json
{
  "overdue trip": "A trip whose scheduled_date < CURDATE() and recent_state_id NOT IN (40, 50, 60, 70)",
  "active trip":  "A trip where is_active = 1 or status = 1",
  "completed trip": "A trip where recent_state_id IN (40, 50, 60)",
  "cancelled trip": "A trip where recent_state_id = 70",
  "en route":     "recent_state_id = 30",
  "reached":      "recent_state_id = 40",
  "gps data":     "vts_transaction table — most recent record per vehicle",
  "current position": "vts_transaction — latest latitude/longitude per vehicle_id"
}
```

**Code change:** Merge `semantics.json` alias keys into the entity-detection vocabulary before the detection gate runs. Both maps should be unified at domain load time in `DomainRegistry`.

---

### P0-3  Enum integer vs label string (FM5 ×1)

**Root cause:** `enums.py` defines `recent_state_id` values as 10/20/30/70, but the actual DB sample shows 1/2/3/5/6. The enum values need verification against the live DB, then the verified values need few-shot examples.

**Steps:**
1. Run `SELECT DISTINCT recent_state_id FROM trip ORDER BY 1` against the live DB and cross-check with `enums.py`.
2. If values diverge, correct `enums.py` ENUM_MAPPINGS.
3. Add a few-shot example for every enum label in `few_shot_examples.json`:
```json
[
  {
    "question": "Show trips that are en route",
    "intent": { "table": "trip", "filters": [{"field": "recent_state_id", "operator": "=", "value": 30}] }
  },
  {
    "question": "Show cancelled trips",
    "intent": { "table": "trip", "filters": [{"field": "recent_state_id", "operator": "=", "value": 70}] }
  }
]
```

---

### P0-4  Wrong table selected (FM1 ×2)

**Root cause:** "company" in the question overwhelmed "routes"; "GPS location" had no alias in `semantics.json`.

**Files:**

`app/domains/<domain>/manual/semantics.json` — add:
```json
{
  "gps location":      "vts_transaction",
  "last known position": "vts_transaction",
  "routes":            "route",
  "route list":        "route",
  "transporter":       "transporter_details"
}
```

`app/domains/<domain>/manual/few_shot_examples.json` — add negative example:
```json
{
  "question": "How many routes does the company have?",
  "intent": { "table": "route", "operation": "COUNT" },
  "_note": "Target table is route, not company. Company appears in context only."
}
```

---

### P0-5  Wrong column / hallucinated column (FM2 ×1)

**Root cause:** `trip.source_location_id` was hallucinated (real column: `trip.location_id`). The LLM invented a plausible-sounding name because no authoritative column list was injected.

**Fix:** Inject exact column lists into the prompt context via `domain_knowledge.json`:
```json
{
  "table_column_hints": {
    "trip": ["id", "vehicle_id", "route_id", "location_id", "company_id",
             "scheduled_date", "recent_state_id", "is_active",
             "name", "fan_invoice_number", "plant_code", "type",
             "vehicle_entry_date_time", "vehicle_dispatch_date_time"],
    "vehicle": ["id", "vehicle_number", "company_id", "transporter_id",
                "is_active", "category", "capacity", "is_vts_enabled"],
    "vts_exception": ["id", "vehicle_id", "trip_id", "company_id", "is_active",
                      "is_over_speed", "is_halt", "is_route_deviation",
                      "over_speed_count", "halt_count", "route_deviation_count"]
  }
}
```

---

### P0-6  Missing join examples (FM3 ×2)

`app/domains/<domain>/manual/few_shot_examples.json` — add:
```json
[
  {
    "question": "Which vehicles had route deviation exceptions?",
    "intent": {
      "table": "vts_exception",
      "joins": ["vehicle"],
      "filters": [{"field": "is_route_deviation", "operator": "=", "value": 1}],
      "columns": ["vehicle.vehicle_number", "vts_exception.route_deviation_count"]
    }
  },
  {
    "question": "Show trips along with their route codes",
    "intent": {
      "table": "trip",
      "joins": ["route"],
      "columns": ["trip.id", "trip.name", "route.route_code"],
      "join_condition": "trip.route_id = route.id"
    }
  }
]
```

---

### P0-7  Spurious `created_by` filter injection

**Root cause:** `user_id` from `ChatRequest.user_id` leaks into the LLM context and the model treats it as a data predicate.

**Code fix:** In the context-building step of `ChatService`, strip the keys listed in `metadata_key_blocklist` before passing metadata into the SQL construction prompt. This is a one-line addition to the context sanitiser that already exists for `_REDACTED_METADATA_TOKENS`.

---

### P0 Validation Gate

After all 7 fixes: re-run `python3 scripts/run_diagnostic.py` from the TAG-Implementation repo root.  
**Target:** ≥ 70% pass rate (14/20).  
If not reached, iterate on the specific failing questions before moving to Phase 1.

---

## Phase 1 — CRUD: Safe, Audited Mutations

### Why CRUD needs its own phase

SELECT failures are inconvenient. A wrong INSERT corrupts data. A wrong DELETE cannot be undone. CRUD must be added with explicit safeguards, not by flipping `allow_mutations: true` and hoping for the best.

### 1-1  Enable mutations in config

`config/apps.local.yaml` (per-app section):
```yaml
allow_mutations: true
require_select_where: true          # keep — prevents table-wipe UPDATEs
mutation_require_confirmation: true # new flag — UI must confirm before execute
allowed_mutations:
  - INSERT
  - UPDATE
  # DELETE intentionally excluded by default; add per-domain if needed
protected_tables:
  - flyway_schema_history
  - schema_version
  - company
  - api_key
```

### 1-2  Mutation guard in SQL validator

`app/services/data/sql_validator.py` — add three checks for every mutation:

| Check | What it enforces |
|-------|-----------------|
| **Tenant scope** | UPDATE/INSERT must include `company_id = {company_id}` or FK path to it |
| **Row limit** | UPDATE without WHERE clause or with `WHERE 1=1` → BLOCK |
| **Protected columns** | Never allow UPDATE on `id`, `company_id`, `date_created`, `created_by` |

### 1-3  Confirmation round-trip

For mutations, the API should return a `pending_mutation` event before execution:
```json
{
  "type": "pending_mutation",
  "sql": "UPDATE trip SET recent_state_id = 70 WHERE id = 23 AND company_id = 56942673",
  "affected_table": "trip",
  "estimated_rows": 1,
  "requires_confirmation": true,
  "confirm_token": "tok_abc123"
}
```
The client sends back `{"confirm": true, "confirm_token": "tok_abc123"}` to execute.  
This maps to the existing `pending_select` flow already scaffolded in `ChatResponse`.

### 1-4  Mutation audit log

Every executed mutation writes to an audit table (or append-only log file if no audit DB):
```
mutation_id, session_id, user_id, app_id, company_id, table_name,
operation, sql_executed, rows_affected, executed_at, trace_id
```
This is non-negotiable for production. Even a local SQLite append-only file is better than nothing.

### 1-5  Few-shot examples for mutations

`app/domains/<domain>/manual/few_shot_examples.json` — add CRUD examples:
```json
[
  {
    "question": "Cancel trip 23",
    "intent": {
      "operation": "UPDATE",
      "table": "trip",
      "set": [{"field": "recent_state_id", "value": 70}],
      "filters": [{"field": "id", "operator": "=", "value": 23}]
    }
  },
  {
    "question": "Create a new trip for vehicle TN55AB1234 scheduled tomorrow",
    "intent": {
      "operation": "INSERT",
      "table": "trip",
      "values": {"vehicle_id": "<lookup vehicle_number=TN55AB1234>", "scheduled_date": "CURDATE() + 1"}
    }
  }
]
```

### 1-6  CRUD validation gate

Run a second diagnostic pass with 10 mutation-specific questions:
- 3 UPDATE (single row, filtered correctly)
- 3 INSERT (with required fields)
- 2 UPDATE (table-wipe attempt → must be BLOCKED)
- 2 DELETE (if enabled — must require confirmation)

**Target:** Blocked attempts always blocked. Permitted mutations always scoped to correct tenant.

---

## Phase 2 — Any-DB Onboarding Pipeline

### Goal

`give me a DB URL + 2 sentences` → working, tested assistant in under 10 minutes.

### 2-1  Single onboarding command

Today's onboarding requires multiple manual steps across two repos (OpenMetaData → TAG). Collapse it into one:

```bash
make onboard-domain \
  DOMAIN=my_app \
  DB_URL="mysql+aiomysql://user:pass@host/dbname" \
  DESCRIPTION="Order management system for logistics ops" \
  LLM=1 \
  FORCE=1
```

This should do **all** of the following automatically:
1. Introspect the DB schema (tables, columns, FKs, enums, row counts)
2. Run LLM-assisted semantic enrichment (business names, descriptions, glossary)
3. Generate `generated/` artifacts (domain.json, enums.py, fields.py, sql_builder.json, entity_behavior.json)
4. Apply the P0 fixes as defaults (implicit filters, blocklist, column hints)
5. Register the new app in `config/apps.local.yaml`
6. Run the 20-question diagnostic automatically and print the pass rate
7. Warn (don't block) if pass rate < 60%

### 2-2  Onboarding config as first-class artifact

The `scripts/onboard_domain.request.json` pattern is good — extend it:

```json
{
  "domain": "my_app",
  "db_url": "...",
  "description": "...",
  "primary_table": "orders",
  "user_table": "user",
  "location_table": "location",
  "tenant_column": "company_id",
  "soft_delete_column": "is_active",
  "enum_tables": ["order_status_master", "trip_status_master"],
  "exclude_tables": ["flyway_schema_history", "schema_version"],
  "crud_enabled": false,
  "auto_confirm_mutations": false,
  "generate_diagnostic_questions": true
}
```

The `tenant_column` and `soft_delete_column` fields drive automatic implicit-filter generation — eliminating the P0 hand-fix for every new domain.

### 2-3  Domain quality score

After onboarding, generate a `onboarding_report.json` that scores the domain across five dimensions:

| Dimension | Checks |
|-----------|--------|
| Schema coverage | Tables with descriptions / total tables |
| Enum coverage | Enum columns with label maps / total status columns |
| Relationship coverage | FKs with join hints / total FKs |
| Glossary coverage | Business terms defined / inferred business terms |
| Diagnostic pass rate | Questions passed / 20 |

Print this as a table in the terminal. If any dimension < 60%, print actionable warnings.

### 2-4  Hot-reload without container restart

Currently, domain changes require `docker compose restart tag_backend`.

Add a `POST /semantic/reindex` endpoint (already scaffolded) that reloads `DomainRegistry` from disk without restart. Call this automatically at the end of `onboard_domain.py`.

---

## Phase 3 — Production Hardening

### 3-1  Security

| Risk | Current state | Fix |
|------|--------------|-----|
| SQL injection via user message | `sql_validator.py` partially handles this | Add parameterised query enforcement — never string-interpolate user values into SQL |
| Tenant data leak | `company_id` often missing | P0-1 implicit filter (mandatory, not optional) |
| Mutation without auth | No auth layer | Add `x-user-role` header check before any mutation — `viewer` role blocks INSERT/UPDATE/DELETE |
| Prompt injection | User can say "ignore instructions" | Model's `compact_reasoning` rules help; add explicit injection-attempt detector in ChatService |
| DB credential exposure | `.env` in git-tracked directory | Move to Docker secrets or environment injection; add `.env` to `.gitignore` if not already |
| LLM seeing raw credentials | DB URL passed in metadata | Strip DB URL from all LLM context; only inject `db_alias` |

### 3-2  Resilience

| Scenario | Current behaviour | Fix |
|----------|------------------|-----|
| DB unreachable | Health check fails; chat fails | Add retry (×3, exponential backoff) in `schema_service.py` |
| LLM timeout | Stream hangs | `stage_timings_ms` suggests 8s+ per call; set hard 15s timeout per LLM call |
| Redis down | Likely crashes ChatService | Add in-memory fallback session store with TTL |
| Large result set | Row preview truncated (TOON) | Already handled; verify LIMIT 100 is always enforced |
| Concurrent mutations | Race on same row | Add optimistic lock check: include `date_updated = <last_seen>` in UPDATE WHERE clause |

### 3-3  Observability

**Prometheus metrics to add** (beyond existing scaffolding):

```
tag_query_pass_rate{domain, category}          # from diagnostic runner
tag_sql_generated_total{domain, operation}     # SELECT / INSERT / UPDATE / DELETE
tag_implicit_filter_applied_total{domain}      # how often company_id was injected
tag_entity_detection_miss_total{domain}        # how often entity detection asked for clarification
tag_enum_lookup_hit_total{domain, table, col}  # enum map hits vs misses
tag_mutation_confirmed_total{domain}           # confirmed mutations
tag_mutation_blocked_total{domain, reason}     # blocked mutations and why
tag_llm_call_latency_seconds{domain, stage}    # per-stage LLM latency
```

**Structured logging:** Every SQL execution should emit a structured JSON log line:
```json
{
  "event": "sql_executed",
  "domain": "vts",
  "operation": "SELECT",
  "table": "trip",
  "filters_applied": ["company_id", "is_active"],
  "rows_returned": 12,
  "implicit_filters_injected": true,
  "trace_id": "...",
  "latency_ms": 340
}
```

### 3-4  Performance

| Bottleneck | Fix |
|-----------|-----|
| LLM latency (8–12s per query) | Add query-result cache in Redis: same message + same filters = cached SQL + result for 60s |
| Schema introspection on every request | Already cached; verify TTL is ≥ 5 min |
| Large domain manifests reloaded per request | Load once at startup; invalidate only on `/semantic/reindex` |
| N+1 LLM calls for compound queries | Single-pass SQL generation for joins; reduce LLM calls from 2 to 1 for most queries |

### 3-5  Deployment checklist

```
□ .env not committed to git
□ DB credentials in Docker secrets (not plain env vars in production)
□ Redis password set in production
□ CORS_ORIGINS restricted to actual frontend domains
□ RATE_LIMIT_PER_MINUTE tuned (current default is likely too high)
□ Health probe at /health/ready used by load balancer (not /health)
□ Log aggregation configured (stdout → ELK / CloudWatch / Loki)
□ Prometheus scraping /metrics every 15s
□ Backup of app/domains/ directory (all metadata artifacts)
□ allow_mutations=false by default; opt-in per domain
□ Docker image tagged with git SHA, not :latest
```

---

## Phase 4 — Guardrails (LLM Claim Verification)

This phase implements the design already specified in `docs/dev/llm-guardrails/SPEC.md`.

### 4-1  Intermediate contract builder

Before any final response, build:
```python
IntermediateFrame(
    route="sql",                      # or "chat", "report"
    intent="filter",
    entities=["trip"],
    filters={"company_id": 56942673, "recent_state_id": 30},
    unknowns=[],
    allowed_actions=["SELECT"],
    required_evidence=["trip.recent_state_id"],
    token_budget=2000,
)
```

This frame replaces the current ad-hoc intent parsing that lives scattered across `ChatService` (3100 lines). The frame becomes the single source of truth that all downstream steps read from.

### 4-2  Evidence assembler

After SQL execution, assemble:
```python
EvidenceBundle(
    sql_rowset=rows,                    # actual DB rows returned
    domain_config=manifest_snippet,     # only the relevant section
    runtime_state=session_summary,      # 1–3 sentence context window
    user_context={"company_id": ...},
)
```

### 4-3  Claim verifier

Before emitting the response, check:
- Every factual number in the response matches a row in `sql_rowset`
- Every table/column reference matches the manifest
- No claims about data not in `required_evidence`
- Response mode is in `allowed_actions`

### 4-4  Response validator

Block the response if:
- Any claim is unsupported by evidence
- Response contains a SQL snippet not previously validated
- Response reveals schema internals (table names, column names) without being asked
- Response is > 400 tokens for a factual lookup (signal of over-generation)

### 4-5  Impact

The guardrail layer will drive pass rate from ~70% (after Phase 0) toward 90%+ by catching the remaining cases where the LLM generates plausible-but-wrong SQL or prose.

---

## Success Criteria

| Milestone | Metric | Target |
|-----------|--------|--------|
| Phase 0 complete | Diagnostic pass rate | ≥ 70% |
| Phase 1 complete | Mutation attempts correctly blocked | 100% |
| Phase 1 complete | Permitted mutations tenant-scoped | 100% |
| Phase 2 complete | Time from DB URL to working assistant | < 10 min |
| Phase 2 complete | Domain quality score (all dimensions) | ≥ 70% |
| Phase 3 complete | p95 query latency | < 5s |
| Phase 3 complete | Tenant isolation test suite | 100% pass |
| Phase 4 complete | Diagnostic pass rate | ≥ 90% |
| Phase 4 complete | Unsupported-claim block rate | > 95% |

---

## Execution Order (Next Actions)

```
Week 1
  Day 1–2   Apply P0-1 through P0-7 (metadata fixes, VTS domain)
  Day 3     Re-run diagnostic, confirm ≥ 70%
  Day 4–5   Phase 1: mutation guard, confirmation flow, audit log

Week 2
  Day 1–3   Phase 2: single-command onboarding, domain quality score
  Day 4–5   Onboard a second domain end-to-end; verify quality score

Week 3
  Day 1–3   Phase 3: security fixes (auth header, SQL injection, credential handling)
  Day 4–5   Phase 3: observability (structured logs, Prometheus metrics, deployment checklist)

Week 4+
  Phase 4: guardrail pipeline (intermediate contract → verifier → validator)
  Milestone: 90% diagnostic pass rate across all registered domains
```

---

## Files Changed Per Phase

| Phase | Files |
|-------|-------|
| P0 metadata fixes | `app/domains/*/manual/sql_builder.json`, `entity_behavior.json`, `glossary.json`, `few_shot_examples.json`, `domain_knowledge.json`, `enums.py` |
| P0 code fix (blocklist) | `app/services/chat/service.py` (context sanitiser) |
| P1 CRUD config | `config/apps.local.yaml` |
| P1 mutation guard | `app/services/data/sql_validator.py` |
| P1 confirmation flow | `app/schemas/chat.py`, `app/api/v1/endpoints/chat.py` |
| P1 audit log | new: `app/services/data/mutation_audit.py` |
| P2 onboarding | `scripts/onboard_domain.py`, `Makefile` |
| P2 hot-reload | `app/api/v1/endpoints/semantic.py` (already scaffolded) |
| P3 auth | `app/api/v1/endpoints/chat.py` (header check) |
| P3 metrics | `app/services/observability/metrics_service.py` |
| P3 logging | `app/services/chat/service.py` (emit structured events) |
| P4 guardrails | new: `app/services/guardrails/` (intermediate, evidence, verifier, validator) |

---

## What NOT to do

- Do not tune the LLM prompt before fixing the metadata layer — 80% of failures are metadata, not LLM.
- Do not enable CRUD (`allow_mutations: true`) before the mutation guard and audit log are in place.
- Do not deploy to production before the tenant isolation test passes 100%.
- Do not remove `require_select_where: true` — it is a genuine safety rail, not a limitation.
- Do not merge Phase 4 guardrails into Phase 0 — they are architectural and will slow down the urgent fixes.

---

*This plan was authored after running a 20-question diagnostic against the VTS domain on 2026-04-16.  
Diagnostic artefacts: `diagnostics/results.jsonl`, `evidence/vts-diagnostic-report-final.txt`, `scripts/vts_diagnostic_questions.json`, `scripts/run_diagnostic.py`.*
