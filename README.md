# TAG Backend

Enterprise-clean backend for conversational database operations.

## Current Runtime
The active runtime is a single package:

- `app/assistant/`
  - `orchestration/graph.py`: LangGraph pipeline
  - `nodes/`: routing, intent, SQL build, validation, execution, response
  - `services/`: router/intent/manifest/sql builder logic
  - `state.py`: assistant state contract

Core API remains under `app/api/v1/`.

## Request Flow
1. User query
2. Route (`SQL` or `CHAT`)
3. SQL path: intent -> SQL build (`SELECT`, `INSERT`, `UPDATE`) -> validate -> execute -> response
4. Mutation requests return form-style payloads if required fields are missing

## Legacy Preservation
Previous implementation is archived at:

- `archived/system_v1_clean/`

## Run
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Tests
```bash
pytest -q
```
