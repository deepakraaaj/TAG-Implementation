# API Module Layout

## Structure
- `v1/router.py`: versioned router aggregator for all v1 endpoints.
- `v1/endpoints/chat.py`: chat/query/session streaming endpoints.
- `v1/endpoints/health.py`: health endpoint.
- `v1/endpoints/metrics.py`: Prometheus metrics endpoint.

## Compatibility
- `v1/api.py` is kept as a shim so old imports of `api_router` continue to work.
