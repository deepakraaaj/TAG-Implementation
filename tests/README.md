# Test Suite Layout

## Structure
- `conftest.py`: shared test bootstrap/config.
- `e2e/`: fixture-backed FastAPI + DB integration coverage.
- `e2e/mysql/`: opt-in MySQL-backed e2e coverage for real dialect validation.
- `unit/api/`: endpoint and contract tests.
- `unit/chat/`: chat service/session/history orchestration tests.
- `unit/assistant/`: assistant node/flow/response behavior tests.
- `unit/data/`: SQL builder/validator/execution and schema-cache tests.
- `unit/domain/`: domain registry/manifest behavior tests.
- `unit/observability/`: metrics/token/toon utility tests.

## Runners
- `pytest -q`
- `RUN_MYSQL_E2E=1 pytest tests/e2e/mysql -q`

## MySQL E2E
- Set `RUN_MYSQL_E2E=1` to enable the MySQL-backed suite.
- Optionally set `E2E_MYSQL_ADMIN_URL` to a MySQL admin URL with create/drop schema privileges.
- If `E2E_MYSQL_ADMIN_URL` is unset, the suite will try to autodetect a local `lightning_db` Docker container and use its published `3306` host port.
