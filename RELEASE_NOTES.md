# Release Notes

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
