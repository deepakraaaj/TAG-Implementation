# NL2SQL Assistant Runtime Handoff

DevOps owns Dockerfile, Compose, Helm, ECS task definitions, and deployment YAML.
This repository provides the backend source and runtime contract.

## Backend

- Application type: FastAPI
- Entrypoint: `app.main:app`
- Run command: `uvicorn app.main:app --host 0.0.0.0 --port 8001`
- Port: `8001`
- Health check: `GET /health`
- Environment template: `.env.example`
- Required external service: Redis, configured with `REDIS_URL`

## Runtime Files

Required at runtime:

- `app/`
- `domains/`
- `config/`
- `requirements.txt`
- environment variables based on `.env.example`

Not runtime artifacts:

- `.env`
- `.env.production`
- `.venv/`
- `__pycache__/`
- `.pytest_cache/`
- `output/`
- logs
- SQLite/database dump files

## Widget

The widget is hosted separately on S3/CDN. No widget container is required in this
backend deployment.

## Local Run

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```
