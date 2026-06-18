# Multi-App Database Configuration

This repo supports selecting a database per app through the app registry instead of hardcoding a single tenant.

## Files

- `config/apps.remote.yaml`: maps each `app_id` to a display name, domain, and `database_url`
- `.env`: provides the concrete database URLs referenced by the YAML via `${ENV_VAR}` placeholders
- `app/apps/registry.py`: loads the YAML and expands placeholders from the configured env file plus process env

## Required Settings

```env
APPS_CONFIG_PATH=./config/apps.remote.yaml
DEFAULT_CHAT_APP_ID=vts

VTS_DATABASE_URL=mysql+aiomysql://...
IMS_DATABASE_URL=mysql+aiomysql://...
HZL_DATABASE_URL=mysql+aiomysql://...
CROWD_DATABASE_URL=mysql+aiomysql://...
FITS_DEV_HO_DATABASE_URL=mysql+aiomysql://...
REMP_DATABASE_URL=mysql+aiomysql://...
FITS_DEV_RAILWAY_DATABASE_URL=mysql+aiomysql://...
IOC_DEV_MARCH_9_DATABASE_URL=mysql+aiomysql://...
```

## Runtime Flow

1. The backend loads `Settings`.
2. `AppRegistry.from_settings()` reads `APPS_CONFIG_PATH`.
3. Placeholder values inside the YAML are expanded from the env file and process environment.
4. The selected `app_id` determines which database URL is used for reads and reports.

## Verification

Check registry loading:

```bash
.venv/bin/pytest -q tests/unit/apps/test_app_registry.py
```

Check remote connectivity for configured apps:

```bash
.venv/bin/python scripts/verify_remote_dbs.py
```

Check company loading against each configured app:

```bash
.venv/bin/python scripts/test_company_loading.py
```

## Notes

- Keep secrets in `.env`, not in committed docs or YAML.
- `config/apps.remote.yaml` should only reference env vars, never inline credentials.
- The optional `app/db/multi_tenant_manager.py` helper resolves its URLs through the same app registry, so it stays aligned with the YAML configuration.
