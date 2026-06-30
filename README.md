# NL2SQL Assistant

A domain-aware backend assistant that turns natural-language chat messages into
SQL-backed answers for business-operations databases. It exposes chat-style HTTP
endpoints that route a user's message to:

- conversational answers,
- SQL-backed query results,
- reports, and
- guided workflow actions.

The runtime is **multi-tenant**: one deployment serves several applications
(e.g. **REMP/FITS**, **VTS**), each pointed at its own database and governed by
its own guardrails and JWT auth contract. Tenant configuration lives in
[`config/apps.local.yaml`](config/apps.local.yaml) (local) and
[`config/apps.remote.yaml`](config/apps.remote.yaml) (deployed).

Built with **FastAPI** + **LangGraph/LangChain**, with Redis for caching and
ChromaDB for optional semantic retrieval. The LLM is OpenAI-compatible
(default: Cerebras `gpt-oss-120b`).

> For deeper docs see [`docs/README.md`](docs/README.md). For the
> deployment/runtime contract see [`RUNTIME_HANDOFF.md`](RUNTIME_HANDOFF.md).

---

## Prerequisites

- **Python 3.11+** (developed on 3.12)
- **Redis** running locally (default `redis://localhost:6384/0`)
- A reachable **database** for at least one tenant (MySQL or PostgreSQL)
- An **LLM API key** (OpenAI-compatible endpoint; default is Cerebras)

---

## Run locally

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd "NL2SQL Assistant"

# 2. Create the virtualenv and install dependencies
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 3. Create your .env from the template and fill in the CHANGEME values
cp .env.example .env
#    At minimum set: DOMAIN, a tenant DATABASE_URL (e.g. REMP_DATABASE_URL),
#    LLM_API_KEY, and REDIS_URL.

# 4. Run the app (HTTPS on :443 using the bundled self-signed cert)
sudo ./.venv/bin/python -m app.main
```

The app starts on **`https://localhost:443`** out of the box, terminating TLS
with the committed self-signed cert in [`cert/`](cert/)
(`kritilabs.cert` + `kritilabs.pem`). No extra setup needed for a fresh clone.

**Why `sudo`?** Port 443 is privileged. To run without `sudo`, grant the bind
capability to your venv's Python once:

```bash
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f ./.venv/bin/python)"
./.venv/bin/python -m app.main
```

**Self-signed cert note:** browsers and HTTP clients won't trust the bundled
cert by default — trust it locally, or pass `--insecure` / disable verification
during development. Replace it with a real CA-issued cert in production
(see [`RUNTIME_HANDOFF.md`](RUNTIME_HANDOFF.md)).

### Verify it's up

```bash
curl -k https://localhost:443/health
```

### Run on plain HTTP instead (no TLS)

Set the SSL paths empty and pick a non-privileged port in `.env`:

```bash
APP_PORT=8001
APP_SSL_CERTFILE=
APP_SSL_KEYFILE=
```

Then `./.venv/bin/python -m app.main` serves `http://localhost:8001`.

---

## Key configuration

Settings are read from environment variables (see [`.env.example`](.env.example)
for the full list). The most relevant for local runs:

| Variable | Purpose | Default |
|---|---|---|
| `APP_ENV` | `development` / `staging` / `production` | `development` |
| `APP_PORT` | HTTP(S) port | `443` |
| `APP_SSL_CERTFILE` / `APP_SSL_KEYFILE` | TLS cert/key (empty = plain HTTP) | bundled `cert/kritilabs.*` |
| `APPS_CONFIG_PATH` | Tenant registry file | `./config/apps.local.yaml` |
| `DOMAIN` | Default startup domain | — |
| `<TENANT>_DATABASE_URL` | Per-tenant DB URL (e.g. `REMP_DATABASE_URL`) | — |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | LLM connection | Cerebras `gpt-oss-120b` |
| `REDIS_URL` | Cache / session store | `redis://localhost:6384/0` |
| `FITS_JWT_SECRET` / `VTS_JWT_SECRET` | Per-tenant JWT signing secrets (server-only) | — |

> **Secrets** (`*_JWT_SECRET`, `LLM_API_KEY`, DB passwords) are read from the
> environment. The `.env` file is for local dev only; in deployed environments
> they are injected from the platform's secret store (e.g. AWS Secrets Manager /
> SSM Parameter Store). JWT signing secrets must match the value the host app's
> auth service signs tokens with — obtain them from that team, do not generate
> your own.

---

## Tests

```bash
./.venv/bin/python -m pytest
```

---

## Project layout

| Path | Contents |
|---|---|
| [`app/`](app/) | FastAPI application, API routes, services, security |
| [`config/`](config/) | Per-tenant app registry (`apps.*.yaml`) |
| [`domains/`](domains/) | Domain definitions / specs |
| [`cert/`](cert/) | Bundled self-signed TLS cert for local HTTPS |
| [`scripts/`](scripts/) | Onboarding, diagnostics, and data-seed utilities |
| [`tests/`](tests/) | Test suite |
| [`docs/`](docs/) | Architecture, operations, and product documentation |
