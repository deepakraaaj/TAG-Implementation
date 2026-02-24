# Release Notes

## 2026-02-24 (Role-Based Mutation Authorization Hardening)

### Added
- Added centralized mutation authorization settings:
  - `MUTATION_ALLOWED_ROLES` (default: `admin,superadmin`)
  - `MUTATION_REQUIRE_EXPLICIT_PERMISSION` (default: `true`)

### Changed
- Updated SQL validation policy to enforce deny-by-default mutation behavior (`INSERT`/`UPDATE`):
  - mutation is allowed only when `allow_mutations=true` is explicitly provided, and
  - caller role is in the configured allowed-role list.
- Updated chat request handling to propagate `user_role` into metadata so mutation policy has consistent role context in validator stage.

### Fixed
- Prevented non-privileged or implicitly authorized mutation attempts from passing SQL validation.

### Tests
- Extended mutation policy unit coverage to validate:
  - explicit permission requirement,
  - allowed-role enforcement,
  - role extraction from alternate metadata keys.

### Application Impact
- **Safer write operations**: mutation SQL is no longer permitted by default, reducing accidental or unauthorized data changes.
- **Clearer authorization semantics**: both role and explicit permission are now required for mutation paths, aligning behavior with least-privilege expectations.

### Validation
- `pytest -q tests/unit/test_sql_validate_node_mutation_policy.py tests/unit/test_sql_validator_mutation_guards.py tests/unit/test_prompt_golden_regression.py tests/unit/test_chat_service_timeouts.py tests/unit/test_chat_service_stream_completion.py`
- Result: `16 passed, 1 warning`

## 2026-02-24 (Reliability, Safety, and Observability Hardening)

### Added
- Added execution tracker document for the optimization/hardening rollout:
  - `docs/optimization_execution_tracker.md`
- Added idempotency support for chat retries via `idempotency_key` in request schema and chat service replay cache.
- Added stage latency instrumentation on terminal chat results (`stage_timings_ms`) for key stages and total response time.
- Added end-to-end `trace_id` propagation from API boundary to terminal stream result payloads.
- Added Makefile quality gate targets:
  - `test-pytest` (`pytest -q`)
  - `quality-gate` (full pytest + targeted chat stream smoke tests)

### Changed
- Simplified duplicated streaming response logic in `ChatService` by centralizing token/error/result emitters.
- Simplified duplicate pending-select merge/persist logic into shared helpers.
- Added explicit timeout guards for:
  - workflow execution,
  - YAML flow startup/continuation,
  - load-more SQL execution.
- Centralized pagination hard limits/default behavior in chat service (`_bounded_page_limit`) and applied consistently.
- Added process-level cache for schema manifest loading in `SchemaManifestService`.
- Hardened SQL safety validation:
  - blocked protected system schema/table access (`information_schema`, `mysql`, `performance_schema`, `sys`),
  - enforced `UPDATE` requires `WHERE`,
  - tightened statement-type and mutation policy enforcement with metadata override support.

### Fixed
- Ensured streaming error paths always emit a terminal `type=result` envelope.
- Ensured endpoint-level stream failure fallback emits a structured terminal result.
- Ensured idempotent replay responses still include fresh stage timings and valid trace IDs.

### Tests
- Added/updated tests for:
  - stream completion and endpoint stream contract,
  - timeout terminal behavior,
  - idempotency replay behavior,
  - pagination limit cap behavior,
  - schema manifest cache behavior,
  - SQL mutation/system-table guardrails,
  - mutation policy parsing in SQL validate node,
  - prompt-injection golden regression coverage.

### Application Impact
- **Higher reliability**: timeout and stream-fallback handling now consistently return terminal result envelopes, reducing client-side hanging/error ambiguity.
- **Safer query execution**: stricter SQL guardrails reduce risk of unsafe/privileged queries and accidental broad mutations.
- **Better retry behavior**: idempotency prevents duplicate backend work and duplicate side effects for repeated client retries.
- **Better observability**: trace IDs and stage timings improve root-cause analysis and performance troubleshooting.
- **Lower maintenance cost**: deduplicated chat flow logic reduces branch complexity and regression surface.

### Validation
- `pytest -q tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_idempotency.py tests/unit/test_chat_service_timeouts.py tests/unit/test_sql_validator_mutation_guards.py tests/unit/test_sql_validate_node_mutation_policy.py tests/unit/test_prompt_golden_regression.py tests/unit/test_schema_manifest_service_cache.py tests/unit/test_chat_service_pagination.py`
- Result: `22 passed, 1 warning`

## 2026-02-18

### Fixed
- Fixed task queries that used date-only equality on `DATETIME` columns (for example `scheduled_date='2026-02-18'`) by rewriting to an inclusive day range during SQL validation.
- Fixed raw SQL prompts getting corrupted by filter inference logic; SQL statements are now passed through directly from the builder stage.
- Improved MySQL engine URL compatibility by removing unsupported mysqlconnector query params (`allowPublicKeyRetrieval`, `useSSL`) when building sync inspection engines.

### Validation
- Added tests for datetime date-equality rewrite and raw SQL passthrough.
- Verified end-to-end `/chat` response now returns expected rows for Nirmala on `2026-02-18`.

## 2026-02-18 (Full Repository Sync)

### Added
- Added reporting and metrics API/backend scaffolding, including new endpoint/service/node modules.
- Added domain package content and migration assets.
- Added supporting services for audit, cache, export, and metrics workflows.

### Changed
- Updated assistant orchestration and SQL-related flows (`chat_node`, `sql_execute_node`, `flow_engine`, `router_service`, `sql_builder_service`, `chat_service`).
- Updated app/runtime configuration and deployment files (`app/config.py`, `app/main.py`, `docker-compose.yml`, `requirements.txt`, `.env.example`).
- Updated dashboard behavior in `test_dashboard/app.py` and expanded SQL builder helper tests.

### Notes
- This commit intentionally includes all pending local repository changes after the previous fix release.

## 2026-02-18 (Filter Parsing Strictness)

### Fixed
- Fixed mis-parsing of facility phrases like `for Ele unit ...` as assignee filters.
- Added explicit handling for `all user(s)` / `everyone` so chatbot does not force or inject `assigned_user_id` filters.
- Improved task-query autorun rules so `date + facility` runs directly without unnecessary user disambiguation prompts.
- Improved zero-row responses to include exact applied filters for strict parameter debugging.

### Validation
- Added/updated tests for all-users phrase handling and zero-record filter-aware responses.
- Verified live `/chat` query for `today ... facility ... all users` now executes directly and returns records.

## 2026-02-18 (Task Status UX + Query Behavior)

### Fixed
- Removed preview text from normal success messages (now concise: `Found X record(s).`).
- Added context-aware summary follow-ups (e.g., `how many tasks are complete`) using the last query result.
- Switched assignee interaction to name-based matching and display (no user ID prompts in chat flows).
- Fixed repeated assignee disambiguation loop after selecting an exact match.
- Added explicit handling for `tasks for me` / `assigned_to=current_user` using decoded user context.
- Prevented hidden `assigned_user_id` auto-injection in generic task-status flows unless self-intent is explicit.
- Hid ID-based filters (`*_id`, `company_id`) from user-facing `No records found` messages.
- Updated task status output to include `facility_name` and merged `assignee_name` (first+last), and removed `priority`.
- Ensured assignee-only task status filters default to `scheduled_date=today` to avoid broad historical results.
- Fixed `list assets` to execute directly instead of forcing filter menu.

### Added
- Added/updated unit tests for summary intent detection, response messaging, assignee disambiguation, and name-based filter parsing.

### Validation
- Verified live chat flows for: `tasks for me`, `tasks for Nirmala`, task status + summary follow-up, today/current-user option, and `list assets`.

## 2026-02-18 (ID-First Query Optimization + Friendly Empty States)

### Fixed
- Switched user-based task filtering to ID-first SQL execution (`assigned_user_id = ...`) for better performance and index usage.
- Resolved assignee names to user IDs before query execution where possible.
- Removed name-based `LIKE` filtering in task SQL execution paths when ID resolution is available.
- Updated empty-state messaging to be non-technical and human-readable.
- Added personalized empty-state format for self-task checks (e.g., `Vinothini, you don't have tasks today.`).

### Validation
- Added/updated tests for ID-based assignee resolution and zero-result messaging behavior.
- Verified live flows for `assigned_to=current_user` and `assignee=Nirmala` now execute with `assigned_user_id` predicates.
