# TAG Optimization Execution Tracker

Last updated: 2026-02-24

## Step 1: Response Contract Hardening
- [x] Ensure streamed failures always emit a terminal `type=result` envelope.
- [x] Ensure workflow-missing and workflow-exception paths emit terminal result.
- [x] Remove duplicated terminal envelope construction in endpoint and centralize helper usage.
- [x] Add endpoint-level stream contract test.
- [x] Run targeted tests and record results.

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_endpoint_stream_contract.py`
- Result: `3 passed, 1 warning`

## Step 2: Collapse Duplicate Flow Paths
- [x] Audit duplicate pending-select merge/persist branches in chat service.
- [x] Refactor pending-select logic into shared helpers (`_merge_pending_select`, `_persist_pending_select_state`).
- [x] Add endpoint-level stream contract test for branch-safe terminal behavior.
- [x] Reduce broader duplicate execution branches in chat and flow engine (shared emitters + transition helper).

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_pagination.py tests/unit/test_chat_history_store.py tests/unit/test_chat_service_flow_bindings.py tests/unit/test_manifest_flow_plugin.py`
- Result: `12 passed, 1 warning`
- Note: `tests/unit/test_select_filter_guard.py` currently has unrelated existing failures tied to SQL builder behavior and intent fallback.

## Step 3: Reliability Guards
- [x] Add explicit timeout/retry budget policies for workflow/flow-engine/SQL boundaries.
- [x] Add idempotency key support for retried chat requests.
- [x] Add reliability policy tests for timeout terminal behavior.

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_chat_idempotency.py tests/unit/test_chat_service_timeouts.py tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_pagination.py tests/unit/test_chat_history_store.py tests/unit/test_chat_service_flow_bindings.py tests/unit/test_manifest_flow_plugin.py`
- Result: `15 passed, 1 warning`

## Step 4: Performance Tightening
- [x] Cache immutable schema/domain metadata.
- [x] Enforce SQL hard caps/default pagination in one place.
- [x] Add stage-latency instrumentation.

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_idempotency.py tests/unit/test_chat_service_timeouts.py tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_pagination.py tests/unit/test_chat_history_store.py tests/unit/test_chat_service_flow_bindings.py tests/unit/test_manifest_flow_plugin.py tests/unit/test_schema_manifest_service_cache.py`
- Result: `17 passed, 1 warning`

## Step 5: Safety + Correctness Gates
- [x] Strengthen allowlist and mutation constraints.
- [x] Add top-prompt golden regression suite.

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_sql_validator_mutation_guards.py tests/unit/test_sql_validate_node_mutation_policy.py tests/unit/test_prompt_golden_regression.py tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_idempotency.py tests/unit/test_chat_service_timeouts.py tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_pagination.py tests/unit/test_schema_manifest_service_cache.py`
- Result: `21 passed, 1 warning`

## Step 6: Observability + Release Safety
- [x] Enforce trace ID propagation end-to-end.
- [x] Add pre-merge quality gate command set in make targets.

Status: Completed on 2026-02-24.
Validation:
- `pytest -q tests/unit/test_chat_endpoint_stream_contract.py tests/unit/test_chat_service_stream_completion.py tests/unit/test_chat_idempotency.py tests/unit/test_chat_service_timeouts.py tests/unit/test_sql_validator_mutation_guards.py tests/unit/test_sql_validate_node_mutation_policy.py tests/unit/test_prompt_golden_regression.py tests/unit/test_schema_manifest_service_cache.py tests/unit/test_chat_service_pagination.py`
- Result: `22 passed, 1 warning`

## Notes
- This tracker is updated as each subtask is completed.
