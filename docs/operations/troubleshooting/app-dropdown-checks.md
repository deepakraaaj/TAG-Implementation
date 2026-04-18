# App And Company Dropdown Checks

If the demo console shows no apps or no companies, verify the backend configuration in this order.

## 1. App Registry

The backend needs:

```env
APPS_CONFIG_PATH=./config/apps.remote.yaml
DEFAULT_CHAT_APP_ID=vts
```

Then confirm the config file exists and uses env placeholders for database URLs.

## 2. Backend App List

Check that the backend can load the configured apps:

```bash
curl http://localhost:8012/api/v1/apps
```

If this is empty, fix the registry or the YAML before checking anything else.

## 3. Company Loading

Once the app list is available, verify company loading for a selected app:

```bash
curl http://localhost:8012/api/v1/apps/vts/companies
```

Or run the repo diagnostic script:

```bash
.venv/bin/python scripts/test_company_loading.py
```

## 4. Frontend Proxy

For local widget development, the demo entry should point to `/api` and Vite should proxy `/api/*` to the backend port. That avoids hardcoded backend URLs in local browser code.
