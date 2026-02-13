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
- `app/assistant/services/router_service.py`: `SQL` vs `CHAT` classification.
- `app/assistant/services/intent_service.py`: operation and table understanding.
- `app/assistant/services/manifest_catalog.py`: manifest access and table metadata.
- `app/assistant/services/sql_builder_service.py`: SQL building and mutation form payloads.
- `app/assistant/nodes/*`: orchestration nodes.
- `app/assistant/orchestration/graph.py`: final graph wiring.

## Mutation UX
For missing required fields on `INSERT/UPDATE`, v2 returns `workflow_payload` with a form definition.
This keeps insert/update menu-driven while avoiding old workflow-engine complexity.

## Legacy Archive
- Full legacy snapshot: `archived/system_v1_clean/`
