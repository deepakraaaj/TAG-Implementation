# Assistant Module Layout

## Runtime-Critical
- `state.py`: graph state contract.
- `orchestration/graph.py`: LangGraph topology + node wiring.
- `nodes/core/`: route/chat/intent/response runtime nodes.
- `nodes/sql/`: sql_build/sql_validate/sql_execute runtime nodes.
- `engine/flow/`: flow runtime (`flow_engine.py`, `flow_registry.py`, plugins).
- `engine/intent/`: intent services.
- `engine/router/`: router service.
- `engine/sql/`: SQL builder service.
- `engine/metadata/`: manifest catalog.
- `engine/safety/`: prompt-injection detector.
- `engine/response/`: response intelligence.
- `flows/`: default assistant flow YAML.

## Optional / Low-Use Paths
- `nodes/reporting/report_node.py`: report stack node used for explicit report intents.
- `engine/reporting/reporting_service.py`: report stack support service.
