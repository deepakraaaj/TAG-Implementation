# app_v2

Clean, future-ready orchestration layer.

## Structure
- `core/`: shared state contracts.
- `services/`: LLM intent/router and SQL construction services.
- `nodes/`: graph nodes that orchestrate routing, intent, SQL, execution, and response.
- `orchestration/`: compiled LangGraph pipeline.

## Flow
1. User query
2. Route (`SQL` or `CHAT`)
3. SQL path: intent -> SQL build -> validate -> execute -> response
4. Mutation support: `INSERT`/`UPDATE` with form payload when required fields are missing.

## Notes
- Uses `app/services/schema_manifest.json` as source of table metadata.
- Old system is archived under `archived/system_v1/`.
