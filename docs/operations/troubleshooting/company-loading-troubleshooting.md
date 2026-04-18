# Company Loading Troubleshooting

This note is for cases where the app list loads but the company dropdown stays empty.

## Expected Flow

1. `GET /api/v1/apps` loads the configured app list from `AppRegistry`.
2. The frontend picks an `app_id`.
3. `GET /api/v1/apps/{app_id}/companies` uses that app's database URL.
4. The backend queries the `company` table and returns company options.

## Common Causes

- `APPS_CONFIG_PATH` points to the wrong file
- the YAML exists but `${ENV_VAR}` placeholders are not resolving
- the target app database is reachable, but the `company` table is missing or uses different column names
- the backend was restarted after `.env` changes with only `docker compose restart`, so the new env values were not recreated into the container

## Quick Checks

Verify app registry loading:

```bash
.venv/bin/pytest -q tests/unit/apps/test_app_registry.py
```

Verify company lookup across configured apps:

```bash
.venv/bin/python scripts/test_company_loading.py
```

If you changed `.env`, recreate the backend container instead of doing a plain restart:

```bash
docker compose up -d --force-recreate tag_backend
```
