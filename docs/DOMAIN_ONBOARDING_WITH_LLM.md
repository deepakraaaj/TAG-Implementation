# Domain Onboarding with LLM Enhancement

## Overview

The enhanced domain generator now supports LLM-powered metadata enrichment while **ensuring NO sensitive data is sent to the LLM**. The LLM only receives schema metadata (table names, column names, column types) — never data values, sample records, or any potentially sensitive information.

## Key Safety Features

### What Gets Sent to LLM (Safe)
- **Table names** (e.g., "maintenance_tasks", "users")
- **Column names** (e.g., "task_id", "assigned_user_id", "status")
- **Column types** (e.g., "UUID", "VARCHAR(255)", "TIMESTAMP")
- **Nullable flags** (e.g., "NOT NULL" constraints)

### What NEVER Gets Sent (Protected)
- ❌ Actual data/rows from the database
- ❌ Default values with sensitive information
- ❌ Sample queries with real records
- ❌ Database passwords, credentials, or connection strings
- ❌ Business logic that might reveal sensitive patterns

## Usage

### 1. JSON-Driven Run

Create or edit a reusable request file:

```bash
python scripts/onboard_domain.py --generate-config
```

This writes `scripts/onboard_domain.request.json`. Fill the `request` block once, then run:

```bash
python scripts/onboard_domain.py --config-file scripts/onboard_domain.request.json
```

If `scripts/onboard_domain.request.json` already exists, the script auto-detects it, so this also works:

```bash
python scripts/onboard_domain.py
```

A checked-in example lives at `scripts/onboard_domain.request.example.json`.

Example request file:

```json
{
  "request": {
    "domain": "my_app_domain",
    "db_url": "postgresql://user:pass@localhost/mydb",
    "description": "Operations and work-order system for my app",
    "write": true,
    "enable_llm_enhancement": true,
    "primary_table": "work_item",
    "user_table": "user",
    "location_table": "facility",
    "include_tables": ["work_item", "user", "facility"],
    "exclude_tables": ["audit_log"],
    "metadata_hints": {
      "scope": "Work orders, assignees, sites, and scheduling",
      "example_queries": [
        "Show open work orders",
        "Show work orders for Ravi",
        "List active sites"
      ],
      "business_terms": {
        "ticket": "work_item",
        "site": "facility"
      }
    }
  }
}
```

### 2. Simplest Local Run

Use the `Makefile` wrapper instead of typing the full Python command:

```bash
make onboard-domain DOMAIN=my_app_domain
```

With LLM enhancement:

```bash
make onboard-domain DOMAIN=my_app_domain LLM=1
```

With a JSON request file:

```bash
make onboard-domain CONFIG_FILE=scripts/onboard_domain.request.json
```

Optional overrides:

```bash
make onboard-domain DOMAIN=my_app_domain DB_URL="postgresql://user:pass@localhost/mydb"
make onboard-domain DOMAIN=my_app_domain PRIMARY_TABLE=maintenance_task USER_TABLE=user LOCATION_TABLE=facility
make onboard-domain DOMAIN=my_app_domain FORCE=1
make onboard-domain-config CONFIG_FILE=scripts/onboard_domain.request.json
```

Notes:

- `DB_URL` is optional. If omitted, the script uses `DATABASE_URL` from `.env`.
- `WRITE=1` is the default in the Makefile, so it writes files immediately.
- `FORCE=1` overwrites an existing generated package.

### 3. Basic Domain Generation (without LLM enhancement)

```bash
python scripts/onboard_domain.py \
  --domain my_app_domain \
  --db-url "postgresql://user:pass@localhost/mydb" \
  --write
```

### 4. Domain Generation WITH LLM Enhancement

```bash
python scripts/onboard_domain.py \
  --domain my_app_domain \
  --db-url "postgresql://user:pass@localhost/mydb" \
  --enable-llm-enhancement \
  --write
```

### 5. Options

```
--config-file PATH               Load onboarding request from JSON
--generate-config                Write JSON onboarding template and exit
--domain NAME                    Domain name (required)
--db-url URL                     Database connection (optional, defaults to DATABASE_URL env)
--description TEXT               Domain description override
--metadata-file PATH             JSON file with business hints
--include-table NAME             Force-include specific table (repeatable)
--exclude-table NAME             Force-exclude specific table (repeatable)
--primary-table NAME             Override primary business table
--user-table NAME                Override user/people table
--location-table NAME            Override facility/location table
--output-root PATH               Where to write domain package (default: app/domains)
--report-file PATH               Write onboarding report JSON
--write                          Actually write generated files
--force                          Overwrite existing files
--enable-llm-enhancement         Use LLM for metadata enrichment (NEW)
```

## What LLM Enhancement Generates

When enabled with `--enable-llm-enhancement`, the LLM generates:

### 1. Better Descriptions
```json
{
  "description": "Table tracking maintenance work orders assigned to technicians"
}
```

### 2. User-Friendly Aliases
```json
{
  "aliases": ["Work Order", "Task", "Maintenance Job", "Job"]
}
```

### 3. Example Queries
```json
{
  "example_queries": [
    "Show all pending work orders",
    "List tasks assigned to John",
    "How many jobs are in progress?"
  ]
}
```

### 4. Business Concepts
```json
{
  "business_concepts": ["Maintenance", "Asset Management", "Scheduling"]
}
```

## Architecture

### Flow
1. **Schema Introspection** → Discover tables and columns from live DB
2. **Safe Filtering** → Extract only schema structure (names, types)
3. **LLM Enrichment** (if enabled) → Send safe schema to LLM for metadata
4. **Merge Results** → Combine LLM output with heuristic-generated metadata
5. **Write Artifacts** → Generate domain package with enhanced metadata

### Code Changes

- **`tools/domain_onboarding/generator.py`**
  - Added LLM client support in `__init__`
  - New `_enhance_table_metadata_with_llm()` method (sends only schema, no data)
  - New `enhance_artifacts_with_llm()` method (async, enriches artifacts after generation)

- **`tools/domain_onboarding/onboarding.py`**
  - Added `llm_client` parameter to `__init__` and `analyze_snapshot()`
  - Added `enable_llm_enhancement` parameter to control LLM usage
  - LLM enhancement only runs if both `enable_llm_enhancement=True` and `llm_client` is available

- **`scripts/onboard_domain.py`**
  - Added `--enable-llm-enhancement` flag
  - Made `main()` async to support async LLM calls
  - Initializes AsyncOpenAI client from settings if enhancement enabled

## Environment Configuration

Ensure your `.env` has valid LLM settings:

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=your-api-key-here
DATABASE_URL=postgresql://user:pass@localhost/mydb
```

## Example Output

Generated domain with LLM enhancement:

```
app/domains/my_app_domain/
├── generated/
│   ├── domain.json                 (with LLM-enhanced descriptions)
│   ├── domain_knowledge.json       (with LLM examples and aliases)
│   ├── manifest/
│   │   └── tables.json             (enriched with LLM metadata)
│   ├── capabilities.json
│   └── workflows/
│       └── (auto-generated YAML flows)
├── domain.yaml
├── onboarding_report.json
└── README.md
```

## Safety Guarantees

✅ **Only schema structure** (table/column names and types) is sent to LLM
✅ **No database data** is ever sent
✅ **No credentials** are included in LLM prompts
✅ **LLM responses parsed safely** with fallback if parsing fails
✅ **Enhancement is optional** — can run without LLM if desired
✅ **Existing heuristic generation** still works as fallback

## Performance

- **Without LLM**: ~5-10 seconds (schema introspection + heuristic analysis)
- **With LLM**: ~15-30 seconds (adds 1-2 LLM API calls per table)

LLM calls are batched per table, not per row, so scalability is proportional to table count, not data volume.

## Troubleshooting

### "LLM access disabled in tests"
This is expected. LLM enhancement works in production/dev, not in test suites.

### No enhancement happening despite `--enable-llm-enhancement`
Ensure:
1. `LLM_BASE_URL` and `LLM_API_KEY` are set in `.env`
2. LLM service is reachable
3. Check logs for HTTP/API errors

### "No tables remain after filtering"
Either all tables are excluded by default heuristics, or your `--exclude-table` flags excluded everything. Use `--include-table` to force inclusion.

## Future Enhancements

- [ ] Batch LLM calls for all tables in one request
- [ ] Cache LLM responses per table schema
- [ ] Support custom LLM prompts for domain-specific metadata
- [ ] Generate conversational YAML flows with LLM
- [ ] Multi-language alias generation
