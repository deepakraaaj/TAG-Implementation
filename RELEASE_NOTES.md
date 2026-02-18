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
