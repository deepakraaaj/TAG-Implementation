# TAG Backend

Runtime-focused implementation guide for the current TAG assistant backend.

When backend environment settings change and you need the container to pick them up:

```bash
docker compose up -d --build --force-recreate tag_backend
```

Last validated against this repository on **2026-03-05**.

## What This README Covers

- The **actual runtime path** currently wired in production code.
- How modules couple at the **platform layer** (graph, cache, DB, and LLM layers) while domain behavior is externalized.
- Which files are **active**, and which are present but **low-use/optional**.

## Runtime Snapshot

- Framework: `FastAPI` (`app/main.py`)
- Assistant orchestration: `LangGraph` (`app/assistant/orchestration/graph.py`)
- Cache/state: `Redis` via `app/services/platform/cache.py`
- DB access: synchronous SQLAlchemy engines from `app/services/data/schema_service.py`
- Report/audit DB access: synchronous gateway `app/services/db_service.py`
- Domain model: `DomainRegistry` + manifest under `app/domains/<domain>/`
- Default domain: `maintenance` (`DOMAIN=maintenance`)

## API Surface

### Endpoints

- `POST /session/start`
  - Returns a new `session_id` UUID.
- `POST /chat` and `POST /query`
  - Same handler.
  - Default: **streaming NDJSON** (`application/x-ndjson`).
  - Debug/widget option: `?stream=false` returns single JSON terminal payload (`application/json`).
- `GET /health`
  - Readiness snapshot (`status`, `ready`, `checks`) with HTTP `200`.
- `GET /health/live`
  - Liveness probe (`alive=true`) with HTTP `200`.
- `GET /health/ready`
  - Readiness probe with HTTP `200/503` based on required checks.
- `GET /metrics`
  - Prometheus scrape endpoint.

### Chat Request Contract

Body (`app/schemas/chat.py`):

```json
{
  "session_id": "string",
  "message": "string",
  "user_id": "optional string",
  "user_role": "optional string, default user",
  "idempotency_key": "optional string",
  "metadata": {}
}
```

Headers:

- `x-user-context`: optional base64 JSON (user/company context injection).
- `x-trace-id`: optional trace id; propagated to terminal result payload.
- `x-response-format`: optional output format (`json` default, `toon` for SQL preview TOON encoding).

Metadata flags:

- `metadata.token_minimization`: optional (`true` default). Enables TOON prompt compaction + heuristic LLM skips for lower token usage.

Streaming event types:

- `{"type":"token","content":"..."}`
- `{"type":"error","message":"..."}`
- `{"type":"result", ...}` (always terminal)

When `stream=false`, the endpoint buffers the stream server-side and returns only the terminal
`{"type":"result", ...}` payload as a normal JSON response.

SQL responses now include token comparison metadata for preview rows:

- `sql.rows_preview_token_count_with_toon`: estimated tokens for TOON preview
- `sql.rows_preview_token_count_without_toon`: estimated tokens for JSON preview
- `sql.rows_preview_token_summary`: human-readable comparison summary

Terminal payload `token_usage` also includes LLM prompt estimates:

- `token_usage.prompt_tokens_est_with_toon`
- `token_usage.prompt_tokens_est_without_toon`
- `token_usage.prompt_tokens_est_saved`

Human-readable token comparison is now sent in payload (not in `message`):

- `token_details.llm_prompt_token_summary`
- `sql.rows_preview_token_summary`

When `x-response-format: toon` (or `metadata.response_format="toon"`), SQL responses additionally include:

- `sql.rows_preview_toon`: TOON-encoded representation of `rows_preview`
- `sql.rows_preview_encoding`: `"toon"`

## End-to-End Request Lifecycle

### 1) Startup

`app/core/lifespan.py`:

1. Creates `ServiceContainer` and validates runtime config (`Settings.validate_runtime()`).
2. Verifies primary DB reachability (and report DB path, when configured).
3. Connects Redis cache (`cache.connect()`).
4. Compiles graph (`create_graph()`), stores compiled workflow in container/app state.

### 2) API Entry

`app/api/v1/endpoints/chat.py`:

1. Normalizes metadata and trace id.
2. Decodes `x-user-context` (if present).
3. Optionally resolves `user_name` from DB using `UserService`.
4. Executes `ChatService.generate_chat_stream(...)`.
5. Returns:
   - NDJSON stream by default
   - buffered JSON terminal payload when `stream=false`

### 3) ChatService Orchestration (Central Controller)

`app/services/chat/service.py` is the main orchestrator. It:

1. Loads session history and per-session state from Redis.
2. Handles idempotency replay (`idempotency_key`) before any graph call.
3. Handles special pre-graph paths:
   - active YAML flows
   - pending select-filter follow-ups
   - summary intent over last select
   - `load more` pagination over last select
4. Optionally serves cached chat responses.
5. Invokes compiled graph (`lifespan.workflow.ainvoke(...)`) with `messages + metadata`.
6. Emits streaming token/result envelopes and writes terminal payload to idempotency cache.

### 4) LangGraph Path

`app/assistant/orchestration/graph.py` wiring:

```text
route
 ├─ CHAT -> chat -> END
 ├─ REPORT -> report -> END
 └─ SQL    -> intent -> sql_build
                        ├─ sql_query == SKIP -> END
                        └─ sql_validate
                             ├─ error -> respond -> END
                             └─ sql_execute -> respond -> END
```

## Coupling Map

| Coupling | Modules | Why it is tightly coupled |
|---|---|---|
| Global workflow singleton | `lifespan.workflow` -> `ChatService` | `ChatService` depends on process-global compiled graph set only during startup. |
| Domain manifest as source of truth | `DomainRegistry` -> `ManifestCatalog` -> router/SQL builder/flow logic | Table aliases, query templates, flow bindings, enum mappings, and capabilities all come from domain files. |
| SQL builder + DB schema | `SQLBuilderNode` (adapter-injected) -> `SQLBuilderService` + `SchemaService` | Runtime depends on DB schema quality, but node wiring is now dependency-injected and domain access can be provided via adapter/provider. |
| Safety enforcement chain | `SQLBuilderNode` -> `SQLValidateNode` -> `SQLValidatorService` | Builder intentionally returns raw/generated SQL; validator is the mandatory gate for mutation and table safety. |
| Response semantics | `SQLExecuteNode` -> `DomainRegistry` enums -> `ResponseNode` | Execution serializes DB rows and maps enum ints to domain labels before final user-facing message generation. |
| Session behavior | `ChatService` -> Redis keys (`history`, `flow_state`, `pending_select`, `last_select`, idempotency) | Continuations, pagination, and retries all depend on redis-backed state contracts. |
| LLM dependencies | `RouterService`, `IntentService`, `IntentDetectionService`, `ChatNode`, `SQLBuilderService` | Multiple stages independently call LLM with shared env config; behavior quality depends on all of them. |
| YAML flow execution | `ChatService` -> `FlowEngine` -> `FlowRegistry` + `ManifestFlowPlugin` | Flow start/continue logic and DB write actions are split across chat service state + declarative YAML + plugin actions. |

## Repository Structure (Current)

```text
app/
  main.py                         # FastAPI app, CORS, router wiring
  core/
    lifespan.py                   # Redis connect + graph compile
    logging.py
  api/v1/
    router.py                     # Aggregates v1 endpoint routers
    endpoints/chat.py             # Chat entry point (stream + buffered JSON mode)
    endpoints/health.py
    endpoints/metrics.py
  services/
    chat/
      service.py                  # Main runtime orchestrator (active)
      history_store.py            # Session history persistence
    platform/
      cache.py                    # Redis singleton used by runtime
      cache_service.py            # Report cache service
    data/
      db_service.py               # sync DB gateway for report/audit stack
      schema_service.py           # SQLAlchemy engine + inspection
      sql_validator.py            # SQL guardrails
      user_service.py             # user_name resolution from DB
      schema_manifest_service.py  # schema/semantic manifest helper
    observability/
      metrics_service.py          # Prometheus metrics
      audit_service.py            # Audit logging support
    core/
      llm_retry_service.py
      token_usage_service.py
      toon_service.py
    interfaces/
      __init__.py                 # service protocols/contracts
  assistant/
    orchestration/graph.py
    state.py
    nodes/core/                   # route/chat/intent/response
    nodes/reporting/              # report execution node (active via REPORT route)
    nodes/sql/                    # sql_build/sql_validate/sql_execute
    engine/flow/                  # flow engine/registry/plugins
    engine/intent/                # intent services
    engine/sql/                   # SQL builder service
    engine/router/                # route classification service
    engine/metadata/              # manifest catalog
    engine/safety/                # prompt injection detector
    engine/response/              # response intelligence
    flows/create_schedule.yaml    # default flow definition
  domains/
    registry.py
    maintenance/
      domain.json                 # flow bindings, capabilities, summary buckets
      schema_manifest.json        # tables/aliases/templates/rules
      enums.py                    # enum mappings + labels
      fields.py                   # field labels/options/lookups
      rules.py                    # flow candidate + conditional field rules
      flows/create_schedule.yaml  # domain flow override
      reports.json                # report templates used by REPORT route
tests/unit/                       # Unit tests grouped by area (api/chat/assistant/data/domain/observability)
```

## Domain-Driven Implementation Details

### `maintenance` domain assets currently loaded

- `schema_manifest.json`
  - `20` tables
  - `3` query template groups (`asset`, `task_transaction`, `user`)
  - `2` table resolution priority rules
- `domain.json`
  - Flow binding: `scheduler_task_details + insert -> create_schedule`
  - Summary spec used by `ChatService` for follow-up summary requests
- `reports.json`
  - `16` report definitions used by report routing/execution (`route=REPORT`)

### Domain portability hooks

The core runtime now reads these domain-level hooks from `domain.json` (and optional `rules.py` helpers):

- `assistant_prompt`
  - Chat persona/prompt template and suggested example queries.
- `intent_detection`
  - Domain-specific intent interpretation hints injected into intent-detection prompt.
- `entity_behavior`
  - Primary entity/table behavior (keywords, user-filter semantics, default prompts, date/status inference maps, and primary menu options).
  - Includes `intent_mode` (`auto|heuristic|llm`) to control intent-detection latency/accuracy tradeoff.
- `user_lookup`
  - User table/column mapping for resolving `user_name` from `user_id`, plus select-filter user disambiguation keys.
- `location_lookup`
  - Location/facility table mapping for select-filter disambiguation and fallback options.
- `select_workflow`
  - Select-filter workflow payload contract (`workflow_id`, `state`, `mode`, `next_field`, `operation`) consumed by `SQLBuilderNode` and `ChatService`.
- `response_messages`
  - Domain wording for security/no-record responses.
- `rules.py::format_no_records_message(context)` (optional)
  - Domain override for no-record messages in specialized SQL patterns.

The runtime also reads per-table SQL-builder metadata from `schema_manifest.json`:

- `tables.<table>.template_filter_aliases`
  - Maps friendly filter keys (for example `facility_name`) to template SQL expressions (for example `f.name`).
- `tables.<table>.default_select_columns`
  - Declares fallback SELECT columns for non-template filtered queries.
- `tables.<table>.tenant_scope`
  - Declares tenant column + template variable + metadata key (`column`, `template_var`, `metadata_key`) so core SQL building does not assume `company_id`.

## Domain Decoupling Status

Current state:

- Domain portability is now mostly folder-scoped: changing `app/domains/<domain>/` is sufficient for entity semantics, prompts, summary behavior, select-workflow contract, and user/location disambiguation.
- Startup is resilient to partial domain folders:
  - `DomainRegistry` deep-merges the active domain config/manifest over starter defaults.
  - Missing `enums.py`, `fields.py`, or `rules.py` in the active domain now fall back safely to starter/default stubs instead of crashing startup.
  - Enum label rendering in SQL results is domain-driven from enum mappings (not hardcoded status column names).
- Core no longer hardcodes:
  - select workflow id (`select_filters`) in request/response follow-up contracts
  - tenant filter column assumptions (`company_id`) in SQL builder paths
  - facility/user lookup table queries for filter disambiguation
  - flow payload operation default as always `insert`
  - primary entity date/status phrase inference and primary menu options (`entity_behavior.date_phrase_map`, `status_phrase_map`, `primary_menu_options`)

Still intentionally platform-coupled:

- LangGraph orchestration topology
- Redis-backed session/idempotency behavior
- SQL validator policy layer
- LLM provider wiring from environment settings

`SQLBuilderNode` decoupling specifics:

- Constructor now supports dependency injection for `sql_builder`, `intent_detector`, `schema`, `domain_provider`, and `kv_parser`.
- Domain access is lazy/provider-based (no direct top-level `DomainRegistry` import requirement in the node).
- Intent fallback is adapter-based (`fallback_intent` or `_fallback_intent`) to avoid hard private-method coupling.
- Default services are lazily constructed via adapter factories (`configure_adapters`) instead of hard top-level imports.
- The node now has null-object defaults, and real adapters are wired in composition root (`app/assistant/orchestration/graph.py`).

Example:

```python
node = SQLBuilderNode(
    sql_builder=my_sql_builder,
    intent_detector=my_intent_detector,
    schema=my_schema_service,
    domain_provider=lambda: my_domain_adapter,
    kv_parser=my_parse_kv_pairs_fn,
)
```

Or configure once globally:

```python
SQLBuilderNode.configure_adapters(
    sql_builder_factory=my_sql_builder_factory,
    intent_detector_factory=my_intent_factory,
    schema_factory=my_schema_factory,
    domain_provider=lambda: my_domain_adapter,
    kv_parser=my_parse_kv_pairs_fn,
)
```

### Starter domain template

A ready-to-copy starter domain is included at:

- `app/domains/starter/`

A richer reference package with `generated/` and `manual/` layers is also included at:

- `app/domains/standard_reference/`

It includes:

- `domain.json`
- `schema_manifest.json`
- `enums.py`
- `fields.py`
- `rules.py`
- `reports.json`
- `flows/create_work_item.yaml`

The richer `standard_reference` package adds:

- `generated/domain.json`
- `generated/domain_knowledge.json`
- `generated/entity_behavior.json`
- `generated/user_lookup.json`
- `generated/location_lookup.json`
- `generated/select_workflow.json`
- `generated/sql_builder.json`
- `generated/manifest/tables.json`
- `generated/manifest/query_templates.json`
- `generated/manifest/table_resolution_rules.json`
- `manual/glossary.json`
- `manual/semantics.json`
- `manual/few_shot_examples.json`
- `developer_clarifications.json`
- `review_report.json`

Use it for a new application:

1. Copy `app/domains/starter` to `app/domains/<your_domain>`.
2. Update `domain.json` and `schema_manifest.json` first.
3. Adjust `enums.py`, `fields.py`, and `rules.py` to your business model.
4. Set `DOMAIN=<your_domain>` in environment.
5. Restart the backend.

### Enum coupling

- Write-time: `SQLBuilderService` maps labels to enum ints (`DomainRegistry.get_enum_mapping`).
- Read-time: `SQLExecuteNode` maps enum ints to labels (`DomainRegistry.get_enum_label`).

## State and Cache Contracts

`ChatService` uses these Redis key families (hashed via `cache.generate_key(...)`):

| Key Family | Purpose | TTL |
|---|---|---|
| `history` | chat turns | 86400s |
| `flow_state` | active YAML flow progress | 3600s |
| `pending_select` | unresolved filter flow context | 1800s |
| `last_select` | pagination/summary base SQL | 1800s |
| `chat_idempotent` | terminal response replay | 3600s |
| `chat` | regular response cache | 3600s |

Important behavior:

- Cache is **best effort**. If Redis is unavailable, app still runs, but stateful features degrade.
- Idempotency replay returns a terminal response without invoking graph again.

## Safety and Policy Enforcement

- Prompt injection detection in `ChatNode` (SQL path relies on SQL validation policy layer).
- SQL validation (`SQLValidateNode` + `SQLValidatorService`):
  - only `SELECT`, `INSERT`, `UPDATE`
  - blocks `DROP`, `DELETE`, `ALTER`, `CREATE`
  - blocks system schemas (`information_schema`, `mysql`, `performance_schema`, `sys`)
  - requires `WHERE` for `SELECT` and `UPDATE`
  - mutation policy tied to role + explicit `allow_mutations`
- Mutation authorization settings:
  - `MUTATION_ALLOWED_ROLES` (default `admin,superadmin`)
  - `MUTATION_REQUIRE_EXPLICIT_PERMISSION` (default `true`)

## Local Development

### Prerequisites

- Python `3.10`
- Redis
- MySQL-compatible database
- Valid LLM endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`)

### Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8001
```

### Run with Docker

```bash
cp .env.example .env
docker compose up -d --build
```

This compose stack now starts:

- `tag_backend` on `http://localhost:${TAG_BACKEND_PORT:-8012}`
- `chatbot_demo` on `http://localhost:${CHATBOT_DEMO_PORT:-5174}`
- `redis` on `localhost:${REDIS_HOST_PORT:-6384}`

Notes:

- Compose uses `config/apps.docker.yaml`, which expects `FITS_DATABASE_URL_DOCKER`, `VTS_DATABASE_URL_DOCKER`, and `IMS_DATABASE_URL_DOCKER` in `.env`.
- The demo widget proxies `/api` to the backend container, so the browser can use the dropdown chatbot without extra CORS or host setup.
- If your LLM is running on the same machine, set `LLM_BASE_URL` to a host-reachable URL before starting the stack.

### Run backend + widget + fits-ui together

```bash
./scripts/start-local-stack.sh
```

This script:

- Starts this repository's Docker stack in detached mode.
- Builds `chatbot-widget`, then serves it on `8081`.
- Runs `fits-ui` with `npm run dev` in the foreground.
- You can override the external app paths with `WIDGET_DIR=...`, `FITS_UI_DIR=...`, `WIDGET_PORT=...`, and `FITS_UI_PORT=...`.

### Stop backend + widget + fits-ui

```bash
./scripts/stop-local-stack.sh
```

This script:

- Stops `fits-ui` by port using `FITS_UI_PORT` (`5173` by default).
- Stops `chatbot-widget` by port using `WIDGET_PORT` (`8081` by default).
- Runs `docker compose down` for this repository.

Notes:

- Backend listens on `8001` in container.
- Docker compose maps host `${TAG_BACKEND_PORT:-8012} -> container 8001`.
- Demo widget maps host `${CHATBOT_DEMO_PORT:-5174} -> container 80`.

### Domain onboarding CLI

Use the onboarding CLI when you want TAG to inspect a live schema, ignore likely noise tables, and suggest clarification questions before generating a new domain package.

The onboarding sub-agent is kept out of the runtime app tree. The tooling lives in `tools/domain_onboarding/`, while the reviewed/generated domain files are written into `app/domains/`.

Preview the workflow:

```bash
.venv/bin/python scripts/onboard_domain.py --domain warehouse_ops
```

Write the generated package after review:

```bash
.venv/bin/python scripts/onboard_domain.py --domain warehouse_ops --write --force
```

See [domain-generator-cli.md](/home/deepakrajb/Desktop/MD/TAG-Implementation/docs/architecture/domain-generator-cli.md) for the full onboarding and generation workflow.

## Testing

### Quick run

```bash
pytest -q
```

### CI currently runs

```bash
pytest -q
```

Coverage focus in existing unit tests:

- stream completion/terminal envelopes
- timeout handling
- idempotency replay
- pagination helpers
- mutation guardrails
- select filter guardrails
- prompt injection golden cases
- flow binding + plugin behavior
- report route fallback/classification behavior
- health liveness/readiness endpoint behavior
- sqlite + optional mysql e2e coverage for chat/report flows

## Active vs Low-Use Modules

### Active in current runtime path

- `app/services/chat/service.py`
- `app/assistant/orchestration/graph.py` and nodes it wires
- `app/assistant/engine/*` used by wired nodes and flow engine
- `app/services/platform/cache.py`
- `app/services/platform/cache_service.py`
- `app/services/data/schema_service.py`, `app/services/data/sql_validator.py`
- `app/services/db_service.py`
- `app/services/observability/metrics_service.py`
- `app/services/observability/audit_service.py`
- `app/domains/maintenance/*`

### Present but low-use/optional

- `app/services/data/schema_manifest_service.py` (mostly support/testing use)
- `app/assistant/engine/legacy/*` (archived helpers)

## Chatbot Widget Integration

- Widget sends `POST /session/start` once per chat session and `POST /chat` for each message.
- Use `x-user-context` (base64 JSON) to pass `user_id`, `user_role`, `company_id`, and `user_name`.
- For production UX (token-by-token updates), keep default stream mode.
- For browser Network debugging or simpler widget clients, call `POST /chat?stream=false`.
- Full integration checklist and examples: `docs/operations/integration/chatbot-widget-integration.md`.

## Practical Extension Guidance

If you change behavior, update these together to avoid breaking coupling assumptions:

1. Update domain manifest/domain config first (`app/domains/<domain>/...`).
2. Update SQL build/validation logic (`app/assistant/nodes/sql/sql_builder_node.py`, `app/assistant/nodes/sql/sql_validate_node.py`).
3. Update response behavior (`app/assistant/nodes/core/response_node.py`) if result semantics change.
4. Update chat orchestration state handling (`services/chat/service.py`) for cache/flow/idempotency compatibility.
5. Add/adjust unit tests in `tests/unit/<area>/` for any path you touched.
