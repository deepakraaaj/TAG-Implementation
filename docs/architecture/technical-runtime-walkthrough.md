# TAG Assistant Technical Runtime Walkthrough

Date: 2026-03-12
Owner: Backend Platform

## Purpose
This document explains how the current TAG backend works at runtime, using the code that is active today.

It is intentionally implementation-grounded:

- it describes the real FastAPI, container, graph, Redis, DB, and domain wiring
- it explains what happens before the graph, inside the graph, and after the graph returns
- it calls out current design realities, not just the intended future architecture

## System Summary
TAG is a FastAPI backend that turns a chat-style request into one of three runtime paths:

- `CHAT`: conversational help and non-SQL responses
- `SQL`: routed data access, validation, execution, and summarization
- `REPORT`: execution of predefined report templates

The main technical shape is:

1. FastAPI accepts the request and normalizes metadata.
2. `ChatService` performs deterministic pre-graph handling such as idempotency replay, flow continuation, pagination, and cached response lookup.
3. A compiled LangGraph workflow handles route selection and the active node path.
4. `ChatService` converts graph output back into the stable stream contract and persists session state.

## Main Runtime Components

### API Layer
- `app/main.py`
- `app/api/v1/router.py`
- `app/api/v1/endpoints/chat.py`
- `app/api/v1/endpoints/health.py`
- `app/api/v1/endpoints/metrics.py`

FastAPI exposes:

- `POST /session/start`
- `POST /chat`
- `POST /query`
- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`

`/chat` and `/query` share the same handler and same runtime path.

### Composition Root
- `app/core/lifespan.py`
- `app/core/dependencies/service_container.py`

The application does not assemble services ad hoc per request. A single `ServiceContainer` creates shared infrastructure, LLM clients, graph nodes, and the compiled workflow during startup.

### Core Orchestration
- `app/services/chat/service.py`
- `app/assistant/orchestration/graph.py`
- `app/assistant/state.py`

`ChatService` is the central runtime controller. LangGraph handles route-specific node sequencing, but `ChatService` still owns a large amount of deterministic orchestration around that graph.

### Domain Layer
- `app/domains/registry.py`
- `app/assistant/engine/metadata/manifest_catalog.py`
- `app/domains/<domain>/...`

The active domain defines table aliases, query templates, flow bindings, enum mappings, response messages, and report templates. Runtime behavior is increasingly domain-config-driven.

### Data and State Infrastructure
- `app/services/platform/cache.py`
- `app/services/chat/history_store.py`
- `app/services/data/schema_service.py`
- `app/services/data/sql_validator.py`
- `app/services/db_service.py`

Redis stores chat/session state. SQLAlchemy-backed services provide schema inspection and SQL execution. The report stack uses a separate synchronous DB gateway.

### Guardrails and Response Control
- `app/services/guardrails/intermediate_service.py`
- `app/services/guardrails/evidence_service.py`
- `app/services/guardrails/verifier_service.py`
- `app/services/guardrails/validator_service.py`
- `app/assistant/nodes/core/guardrail_node.py`

The graph now includes an intermediate frame and guardrail pass for `CHAT` and `SQL` terminal messages. The `REPORT` route currently bypasses that guardrail node.

## Startup Sequence

### 1. Process Boot
`app/main.py` creates the FastAPI app, enables permissive CORS, and attaches the v1 router.

### 2. Lifespan Startup
`app/core/lifespan.py`:

1. creates or reuses the singleton `ServiceContainer`
2. calls `container.startup()`
3. stores the container on `app.state.container`
4. stores the compiled workflow in a module-global `workflow` variable for backward compatibility

### 3. Container Initialization
`ServiceContainer` constructs:

- settings and runtime validation
- Redis cache client
- metrics service
- schema service
- TOON/token utilities
- domain-backed manifest catalog
- prompt injection detector
- guardrail services
- multiple `ChatOpenAI` clients with different temperatures for chat, routing, intent, SQL building, and response intelligence
- graph nodes
- `ChatService`
- optional report stack

### 4. Readiness Checks
During `startup()` the container:

- validates runtime settings
- verifies primary DB reachability
- verifies reporting DB path when the reporting stack is available
- connects Redis
- compiles the LangGraph workflow

If required checks fail, startup fails instead of letting the service run partially initialized.

## Request Contract

### Body
`app/schemas/chat.py`

```json
{
  "session_id": "string",
  "message": "string",
  "user_id": "optional string",
  "user_role": "optional string",
  "idempotency_key": "optional string",
  "metadata": {}
}
```

### Headers
- `x-user-context`: optional base64 JSON user/company context
- `x-trace-id`: optional trace identifier
- `x-response-format`: optional output format, including `toon`

### Response Modes
- default: NDJSON stream
- `?stream=false`: buffered JSON terminal payload

The stream contract is:

- `{"type":"token","content":"..."}`
- `{"type":"error","message":"..."}`
- `{"type":"result", ...}`

Important implementation detail: the current backend does not live-stream model tokens. It emits one `token` event containing the final message text, then the terminal `result` event.

## API Entry Behavior
`app/api/v1/endpoints/chat.py` performs request normalization before `ChatService` runs:

1. ensures `metadata` exists
2. assigns `trace_id`
3. applies `x-response-format`
4. decodes `x-user-context`
5. sanitizes `user_id`, `company_id`, `user_role`, `user_name`
6. fetches `user_name` from the database when absent or obviously invalid
7. records pre-stream timing metadata
8. calls `ChatService.generate_chat_stream(...)`

This layer is intentionally light. It prepares metadata and leaves orchestration to `ChatService`.

## `ChatService` as the Main Runtime Controller
`app/services/chat/service.py` is the most important runtime file in the repository.

It is responsible for:

- session startup
- Redis-backed history and per-session state
- idempotency replay
- deterministic pre-graph features
- graph invocation
- terminal response formatting
- chat response caching
- stream emission
- final history persistence
- stage timing and metrics

This means the graph is not the whole system. The graph handles the route-specific computational path, but `ChatService` still decides whether the request should even reach the graph.

## Redis State Model
`ChatService` and `ChatHistoryStore` use Redis keys generated from hashed prefixes.

Current state buckets:

- `history`: prior user and assistant turns
- `flow_state`: active YAML flow session data
- `pending_select`: unresolved filter-disambiguation or update-selection state
- `last_select`: last executed `SELECT` state for summary and pagination
- idempotency cache: terminal payload replay
- chat response cache: non-flow, non-report response reuse

`ChatHistoryStore` appends history transactionally when Redis supports it, then falls back to a load-append-save path.

## End-to-End Request Lifecycle

### 1. Resolve Workflow and Request Metadata
`ChatService.generate_chat_stream()` resolves the compiled workflow, normalizes `trace_id`, injects `session_id`, and folds request-level identity into metadata.

### 2. Idempotency Replay
If an `idempotency_key` is present, `ChatService` checks Redis before doing any new work.

On a hit it:

- restores any pending-select state
- emits the cached message
- returns the stored terminal payload
- records replay metrics

### 3. Load Session State
The service loads:

- history
- active flow state
- pending select state
- last select state

It also derives a short recent-conversation summary and places it into metadata so routing, intent, chat prompts, and guardrails can use it.

### 4. Deterministic Pre-Graph Paths
Before LangGraph is invoked, `ChatService` checks for several special cases.

#### Summary over the last `SELECT`
If the user asks for a summary and `last_select` exists, the service runs a direct summary SQL path without re-entering the graph.

#### `load more` pagination
If the request is a pagination follow-up and `last_select` is present, the service re-runs the previous base SQL with adjusted `LIMIT/OFFSET`, merges rows into stored state, and returns updated preview results.

#### Pending filter or update selection
If the prior SQL path produced a filter prompt or update disambiguation, the next user message is rewritten into a more explicit SQL-style request or used to continue the selection flow.

#### Active YAML flow continuation
If a YAML flow is active, the request is routed directly to `FlowEngine` and bypasses the graph.

#### Navigation shortcut
Short UI navigation prompts such as opening known pages are resolved before the graph and returned as navigation payloads.

#### YAML flow startup
When a message matches a domain flow binding, `ChatService` starts the flow before invoking the graph.

### 5. Response Cache Lookup
If the request is not inside an active flow, `ChatService` checks a Redis chat-response cache keyed by:

- message
- sanitized metadata
- recent history window

On a hit it restores state, emits the cached result, and skips graph execution.

### 6. Graph Invocation
Only after the above paths are exhausted does `ChatService` invoke:

```text
workflow.ainvoke({
  "messages": prior_messages + [HumanMessage(content=request.message)],
  "metadata": request.metadata,
  "retry_count": 0
})
```

### 7. Graph Result Normalization
After the graph returns, `ChatService`:

- extracts final message, SQL, errors, workflow payload, report payload
- persists pending-select state
- persists `last_select` state for later summary/load-more behavior
- caches eligible terminal payloads
- emits final stream events
- appends history
- stores the idempotent terminal payload

## LangGraph Topology
`app/assistant/orchestration/graph.py`

```text
route
  -> intermediate
     -> CHAT   -> chat       -> guardrail -> END
     -> REPORT -> report                 -> END
     -> SQL    -> intent -> sql_build
                             -> SKIP message -> guardrail -> END
                             -> sql_validate
                                -> error   -> respond -> guardrail -> END
                                -> execute -> respond -> guardrail -> END
```

The state contract is defined in `app/assistant/state.py`.

Important fields:

- `messages`
- `metadata`
- `route`
- `intermediate_frame`
- `intent`
- `sql_query`
- `row_count`
- `rows_preview`
- `total_records`
- `error`
- `workflow_payload`
- `report_result`
- `token_usage`
- `evidence_bundle`
- `verification_report`
- `validation_report`

## What Each Graph Node Does

### `route`
`RouterNode` calls `RouterService.route_with_usage(...)`.

Routing is LLM-first with heuristic fallback. The router also coerces some decisions using context, for example:

- referential follow-ups stay in `SQL` when there is pending-select context
- over-eager `REPORT` classifications can be downgraded back to `SQL` or `CHAT`

### `intermediate`
`IntermediateNode` creates a compact frame describing:

- route
- intent label
- entities
- filters
- unknowns
- required evidence
- allowed actions
- token budget
- recent session summary
- question type

This frame is later reused by chat prompting and guardrail evaluation.

### `chat`
`ChatNode` handles general conversational responses.

Its behavior is:

1. run prompt injection detection
2. short-circuit help requests and off-topic handling
3. use the intermediate frame to build a compact prompt
4. ask for clarification immediately when the frame says the request has an unresolved referent
5. call the chat LLM

`ChatNode` also records estimated token savings by comparing the compact prompt to a legacy longer prompt.

### `intent`
`IntentNode` runs `IntentService.analyze_with_usage(...)`.

This produces a normalized intent object with:

- `operation`
- `table`
- `filters`
- `fields`

This is a lightweight intent layer used by the graph state and also by pre-graph flow startup.

### `sql_build`
`SQLBuilderNode` is the most behavior-heavy graph node.

It does more than just convert intent to SQL. It also handles:

- direct user-supplied SQL passthrough
- a second, more SQL-focused intent detection pass via `IntentDetectionService`
- forced or manifest-driven table resolution
- pending-select follow-up interpretation
- cross-entity negation patterns
- self/default filter inference
- dynamic disambiguation of users and facilities
- count-query generation
- filter prompt generation when required input is missing
- update and insert support

`sql_build` can return:

- a real SQL string
- `SKIP` plus a clarification message
- `SKIP` plus workflow payload for filter selection

Current design reality: this node still contains a large amount of runtime intelligence and domain-aware heuristics, so the system is not yet fully separated into tiny single-purpose services.

### `sql_validate`
`SQLValidateNode` applies the hard safety gate.

It:

- determines whether the SQL is a mutation
- enforces role/policy checks for mutations
- allows only tightly scoped task-status updates under special metadata conditions
- inspects live schema columns and column types
- rewrites `datetime_column = 'YYYY-MM-DD'` into a day-range filter when needed
- calls `SQLValidatorService.validate_sql(...)`

`SQLValidatorService` currently blocks:

- unsupported top-level statements
- `DROP`, `DELETE`, `ALTER`, `CREATE`
- unfiltered `SELECT`
- `UPDATE` without `WHERE`
- protected system tables
- duplicate table aliases
- hallucinated qualified columns when live schema is available

### `sql_execute`
`SQLExecuteNode` runs validated SQL synchronously through `SchemaService`.

It:

- gets the correct engine for the configured DB URL
- executes the query
- commits mutations
- serializes datetimes, dates, times, and decimals
- maps enum integers to domain labels
- extracts `_total_count` window metadata when present

It returns:

- `row_count`
- `rows_preview`
- `total_records`
- serialized SQL result

### `respond`
`ResponseNode` turns SQL output into a user-facing summary.

It generates friendly messages for:

- blocked or invalid SQL errors
- no-records cases
- count queries
- normal record listings
- inserts and updates

It also uses domain hooks for no-record messaging when available.

### `guardrail`
`GuardrailNode` runs after `chat` and after `respond`, and also after `sql_build` skip messages.

It performs four steps:

1. ensure an intermediate frame exists
2. assemble evidence from SQL output, session summary, user context, domain config, and runtime errors
3. verify whether the candidate response is supported
4. validate that the final text is safe to emit

Current checks include:

- unresolved referential questions become clarification
- causal claims without explicit cause evidence become abstention
- chat answers that imply data without SQL evidence are blocked
- SQL response numbers must match execution results
- internal artifacts, raw SQL, prompt leaks, and over-budget responses are rewritten

The `REPORT` route currently ends before this guardrail node.

## Two Intent Layers on the SQL Path
The current system has two distinct intent mechanisms:

- `IntentService`: used in the graph `intent` node and pre-graph flow startup
- `IntentDetectionService`: used inside `SQLBuilderNode` for SQL-focused interpretation with schema context and TOON compaction

This is technically important because the graph-level intent is not the only intent inference step. SQL generation can still refine or override the earlier interpretation.

## Domain System
`DomainRegistry` is the runtime source of truth for the active domain.

It:

- resolves the active domain from `DOMAIN`
- falls back to `starter`, then `maintenance`, when necessary
- loads legacy files plus `generated/` and `manual/` layers
- validates merged config with typed Pydantic models
- imports optional `enums.py`, `fields.py`, and `rules.py`

Merge behavior matters:

- fallback config is merged first
- active domain config is merged on top
- for manifest sections such as `tables`, `query_templates`, and `table_resolution_rules`, active-domain content replaces fallback content to avoid cross-domain leakage

Runtime consumers use the domain for:

- aliases and query templates
- response messages
- enum label translation
- field labels and lookup config
- flow candidacy and flow-field normalization
- report definitions

## YAML Flow Runtime
The flow system is a second orchestration path parallel to the graph.

Main files:

- `app/assistant/engine/flow/flow_registry.py`
- `app/assistant/engine/flow/flow_engine.py`
- `app/assistant/engine/flow/plugins/manifest_flow_plugin.py`

How it works:

1. `FlowRegistry` loads YAML files from `app/assistant/flows/` and the active domain flow folder.
2. `ChatService` decides whether to start or continue a flow.
3. `FlowEngine` manages flow state transitions across menu, input, confirmation, system, DB-write, and end states.
4. `ManifestFlowPlugin` provides generic lookup resolvers and row-creation actions.

Flows are useful for structured write actions because they can gather required fields step by step instead of relying on a single free-form prompt.

## Report Path
The report path is intentionally separate from the SQL-builder path.

Main files:

- `app/assistant/nodes/reporting/report_node.py`
- `app/assistant/engine/reporting/reporting_service.py`
- `app/services/db_service.py`
- `app/services/observability/audit_service.py`
- `app/services/platform/cache_service.py`

Behavior:

1. match the query to a predefined report from the active domain `reports.json`
2. enforce report access by role
3. extract pagination and simple filters
4. build report SQL from a template
5. run the query through `DBService`
6. cache results
7. emit audit logs and metrics

This path is template-driven, not generated by `SQLBuilderNode`.

## Token Optimization
Token minimization is already present in several places:

- `IntentService` skips LLM calls for simple queries
- `IntentDetectionService` can use TOON-encoded schema payloads
- `SQLBuilderNode` heuristically skips expensive intent detection in some cases
- `ChatNode` uses a compact frame-based prompt instead of a longer legacy prompt
- terminal payloads include prompt-token comparison metadata
- SQL preview data can be returned in TOON format

The current runtime goal is not just correctness. It is also to reduce repeated prompt context and unnecessary model calls.

## Safety Model
Current safety layers are stacked rather than delegated to one place.

### Request and Chat Safety
- prompt injection detector in `ChatNode`
- help/off-topic short-circuits
- guardrail verifier and validator for emitted `CHAT` and `SQL` messages

### SQL Safety
- mutation permission enforcement in `SQLValidateNode`
- statement-type blocking in `SQLValidatorService`
- protected table blocking
- schema-aware qualified-column validation
- unfiltered read and unsafe update rejection

### Report Safety
- access-level checks in `ReportNode`
- query-template execution instead of generated SQL
- optional audit logging

## Observability and Health

### Health
`/health` and `/health/ready` expose a readiness snapshot based on:

- container initialization
- config validity
- compiled workflow availability
- primary DB reachability
- reporting DB reachability
- Redis reachability

### Metrics
`MetricsService` publishes Prometheus counters, histograms, and gauges for:

- report execution counts, duration, cache hits/misses, active queries, result size
- chat request counts and per-stage latency
- idempotency replays
- denied mutations
- guardrail verifier outcomes
- validator failures
- abstain and clarify counts
- estimated token savings

### Terminal Payload Metadata
Chat terminal payloads can include:

- `trace_id`
- `token_usage`
- `token_details`
- `stage_timings_ms`
- SQL preview token comparison data

## Important Design Realities

### 1. `ChatService` is still the real control plane
Even though LangGraph exists, much of the product behavior lives in `ChatService`, not in graph nodes.

### 2. The SQL builder node remains large
`SQLBuilderNode` currently holds a significant share of domain-aware heuristic logic. The architecture is moving toward more config-driven services, but that split is not complete yet.

### 3. DB work is mostly synchronous
Schema inspection and SQL execution use synchronous SQLAlchemy engines inside an async app. The report path explicitly uses `asyncio.to_thread(...)`, while the main SQL path executes through synchronous engine calls inside the node.

### 4. Guardrails are partial, not global
The current guardrail pipeline protects chat and SQL terminal text, but not the report path.

### 5. Domain config is central to behavior
Aliases, enum mapping, flow hooks, response messaging, and reports all come from the domain package. The application is increasingly a domain runtime rather than a hard-coded assistant.

## Mental Model for Engineers
If you need to reason about a request, the most reliable sequence is:

1. API handler metadata normalization
2. `ChatService` deterministic pre-graph checks
3. LangGraph route and node path
4. `ChatService` post-graph state persistence and stream emission

If you skip step 2, you will miss a large amount of actual runtime behavior.
