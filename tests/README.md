# Test Suite Layout

## Structure
- `conftest.py`: shared test bootstrap/config.
- `unit/api/`: endpoint and contract tests.
- `unit/chat/`: chat service/session/history orchestration tests.
- `unit/assistant/`: assistant node/flow/response behavior tests.
- `unit/data/`: SQL builder/validator/execution and schema-cache tests.
- `unit/domain/`: domain registry/manifest behavior tests.
- `unit/observability/`: metrics/token/toon utility tests.

## Runners
- `pytest -q`
