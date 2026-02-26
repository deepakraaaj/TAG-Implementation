# Assistant Architecture

## Goal
Minimal, maintainable query pipeline:
- user query -> intent routing
- SQL-safe generation (`SELECT`, `INSERT`, `UPDATE`)
- SQL validation
- DB execution
- final response

## Runtime Entry
- `app/core/lifespan.py` uses `app.assistant.orchestration.graph.create_graph`.

## Package Layout
- `app/assistant/state.py`: graph state contract.
- `app/assistant/engine/router/router_service.py`: `SQL` vs `CHAT` classification.
- `app/assistant/engine/intent/intent_service.py`: operation and table understanding.
- `app/assistant/engine/metadata/manifest_catalog.py`: manifest access and table metadata.
- `app/assistant/engine/sql/sql_builder_service.py`: SQL building.
- `app/assistant/nodes/core/*` and `app/assistant/nodes/sql/*`: orchestration nodes.
- `app/assistant/orchestration/graph.py`: final graph wiring.

## Write UX
For missing required fields on `INSERT/UPDATE`, v2 returns a direct validation message so users can retry with explicit key-value inputs.

## Legacy Archive
- Full legacy snapshot: `archived/system_v1_clean/`
