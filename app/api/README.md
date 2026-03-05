# API Module Layout

## Structure
- `v1/router.py`: versioned router aggregator for all v1 endpoints.
- `v1/endpoints/chat.py`: chat/query/session endpoint with two response modes.
- `v1/endpoints/health.py`: liveness/readiness endpoints.
- `v1/endpoints/metrics.py`: Prometheus metrics endpoint.

## Runtime Endpoints
- `POST /session/start`: creates new session id.
- `POST /chat` and `POST /query`: same handler.
- Default `/chat` mode: NDJSON streaming (`application/x-ndjson`).
- Optional debug mode: `/chat?stream=false` returns buffered terminal JSON (`application/json`).
- `GET /health`: readiness snapshot payload with check breakdown.
- `GET /health/live`: liveness probe.
- `GET /health/ready`: readiness probe (`200` or `503`).
- `GET /metrics`: Prometheus scrape output.

## Compatibility
- `v1/api.py` is kept as a shim so old imports of `api_router` continue to work.
