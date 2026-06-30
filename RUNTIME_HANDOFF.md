# NL2SQL Assistant Runtime Handoff

DevOps owns Dockerfile, Compose, Helm, ECS task definitions, and deployment YAML.
This repository provides the backend source and runtime contract.

## Backend

- Application type: FastAPI
- Entrypoint: `app.main:app`
- Clone-and-run (HTTPS, bundled cert): `sudo ./.venv/bin/python -m app.main` → `https://localhost:443`
- Production run command (CLI, real cert): `uvicorn app.main:app --host 0.0.0.0 --port 443 --ssl-certfile <crt> --ssl-keyfile <key>`
- Default port: `443` (override with `APP_PORT`)
- Health check: `GET /health`

### TLS / HTTPS (in-process termination)

The app terminates TLS itself (no proxy) and ships with a **bundled self-signed
cert** so a fresh clone serves HTTPS on **443** with zero setup:

```bash
# Clone-and-run (uses bundled cert/kritilabs.cert + cert/kritilabs.pem on :443).
# Port 443 is privileged, so the process needs CAP_NET_BIND_SERVICE or root:
sudo ./.venv/bin/python -m app.main
# ...or grant the capability once, then run without sudo:
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f ./.venv/bin/python)"
./.venv/bin/python -m app.main
```

Defaults (in `app/config.py`), all overridable via env:
- `APP_PORT=443`
- `APP_SSL_CERTFILE=<repo>/cert/kritilabs.cert`
- `APP_SSL_KEYFILE=<repo>/cert/kritilabs.pem`

The `cert/kritilabs.*` files are committed self-signed dev certs (CN
`nl2sql.kritilabs.local`, SANs `localhost`/`127.0.0.1`, 10-yr validity). They are
for local/internal use only.

**Production:** the run command is the **uvicorn CLI**, which does NOT execute the
`if __name__ == "__main__"` block — so the `APP_*` defaults above are ignored and
you must pass real cert/port on the command line. Replace the self-signed cert
with a CA-issued one (or terminate TLS at the ingress and run plain HTTP):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 443 \
  --ssl-certfile /path/to/real.crt --ssl-keyfile /path/to/real.key
```

To disable in-process TLS (proxy/ingress terminates instead), set
`APP_SSL_CERTFILE=` and `APP_SSL_KEYFILE=` empty (the `python -m app.main` path)
or simply omit the `--ssl-*` flags (the uvicorn CLI path).

Notes for DevOps:
- Port 443 is privileged — the container/process needs `CAP_NET_BIND_SERVICE` or root.
- Self-signed certs are not trusted by default; clients must trust the cert (or use `--insecure`/disabled verification). For public-facing use, prefer a CA-issued cert or terminate TLS at the ingress instead. **Never commit a real private key** — replace the bundled key out-of-band.
- `app/main.py` reads `APP_HOST`, `APP_PORT`, `APP_SSL_CERTFILE`, `APP_SSL_KEYFILE` from settings only on the `python -m app.main` path — the CLI flags above are authoritative in production.
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
