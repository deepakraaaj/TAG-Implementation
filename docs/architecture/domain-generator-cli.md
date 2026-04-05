# Domain Onboarding And Generator CLI

Date: 2026-03-12
Owner: Backend Platform

## Purpose
Provide offline onboarding commands that inspect a live database schema, triage likely-noise tables, ask targeted clarification questions, and scaffold a new domain package for TAG.

This is the current implementation of the planned domain onboarding workflow:

- deterministic schema introspection first
- optional developer clarification interview for uncertain semantics
- table relevance triage before package generation
- clarification questions for low-confidence inferences
- onboarding tooling lives under `tools/domain_onboarding/`, not the runtime `app/` tree
- generated artifacts written into `generated/`
- root-level runtime stubs created for current report and module loading paths
- human review supported through a generated `review_report.json`

## Separation Rule

The onboarding sub-agent is intentionally separate from the application runtime.

- tooling code lives in `tools/domain_onboarding/`
- CLI entrypoints live in `scripts/`
- generated domain files are written into `app/domains/<domain>/`

This keeps domain onboarding logic out of the production runtime while still letting the application consume the generated artifacts.

## Preferred Workflow

1. Run the onboarding CLI first.
2. Review the included/excluded tables and clarification questions.
3. Re-run with explicit overrides if needed.
4. Write the generated domain package once the output looks right.

If you want the shortest single-command path, use guided generation:

```bash
.venv/bin/python scripts/generate_domain.py --domain <domain_name> --guided --db-url "<db_url>" --force
```

Guided mode performs schema triage, lets you adjust included/excluded tables, asks a short app-context interview including "what is this application for?", and then writes the domain package plus reports.

## Onboarding Command

```bash
.venv/bin/python scripts/onboard_domain.py --domain <domain_name>
```

Optional arguments:

- `--db-url`: override `DATABASE_URL`
- `--description`: custom generated domain description
- `--metadata-file`: optional JSON file with project vocabulary, examples, and workflow hints
- `--include-table`: force-include a table the heuristics would exclude
- `--exclude-table`: force-exclude a table
- `--primary-table`: explicit primary business table
- `--user-table`: explicit people/user table
- `--location-table`: explicit facility/location table
- `--report-file`: write the onboarding report without generating the domain package
- `--write`: write the generated domain package and onboarding report
- `--output-root`: target folder for generated domains
- `--force`: overwrite known generated files in an existing domain folder

Example:

```bash
.venv/bin/python scripts/onboard_domain.py \
  --domain warehouse_ops \
  --db-url "mysql+aiomysql://user:pass@host:3306/app_db" \
  --metadata-file design/warehouse_domain_hints.json \
  --exclude-table audit_log \
  --primary-table task_transaction \
  --user-table person \
  --location-table facility \
  --write \
  --output-root app/domains \
  --force
```

The onboarding CLI prints:

- sanitized database target
- included tables
- excluded tables
- primary table candidate
- targeted clarification questions with recommended answers

When `--write` is set, it also writes `onboarding_report.json` beside the generated domain package.

## Generator Command

```bash
.venv/bin/python scripts/generate_domain.py --domain <domain_name>
```

Optional arguments:

- `--config-file`: optional JSON file that stores the generator request plus run status
- `--generate-config`: write a reusable JSON config file and exit
- `--db-url`: override `DATABASE_URL`
- `--description`: custom generated domain description
- `--output-root`: target folder for generated domains
- `--metadata-file`: optional JSON file with project vocabulary, examples, and workflow hints
- `--include-table`: force-include a table in generation
- `--exclude-table`: exclude a table from generation
- `--clarification-file`: optional JSON file with previously approved developer clarifications
- `--developer-clarifications`: ask the developer targeted semantic questions before writing files
- `--simple`: use the lightest workflow, prefer `DATABASE_URL_DOCKER` when available, and keep questions focused on table meaning/labels
- `--guided`: run schema triage plus a short guided interview before writing files
- `--force`: overwrite known generated files in an existing domain folder

Example:

```bash
.venv/bin/python scripts/generate_domain.py \
  --domain warehouse_ops \
  --db-url "mysql+aiomysql://user:pass@host:3306/app_db" \
  --metadata-file design/warehouse_domain_hints.json \
  --output-root app/domains \
  --force
```

Simplest path when `DATABASE_URL_DOCKER` is already set:

```bash
.venv/bin/python scripts/generate_domain.py --domain warehouse_ops --simple --force
```

Guided path with review + write in one run:

```bash
.venv/bin/python scripts/generate_domain.py \
  --domain warehouse_ops \
  --db-url "mysql+aiomysql://user:pass@host:3306/app_db" \
  --guided \
  --force
```

JSON config workflow:

```bash
.venv/bin/python scripts/generate_domain.py
```

The script now auto-loads the checked-in template at:

`scripts/generate_domain.request.json`

Edit that file with your:

- database URL
- app name
- domain name
- business scope
- example queries
- optional table roles / enum hints

You can also paste the same JSON into ChatGPT. The template now contains a `_chatgpt_prompt` block that tells ChatGPT to:

- ask short onboarding questions one at a time
- fill the same JSON structure
- return the completed JSON only at the end

JSON-driven runs are now non-interactive by default. If you run through a config file, the generator applies guided table recommendations automatically unless you explicitly set `request.interactive_prompts=true` or pass `--interactive-prompts`.

Then run:

```bash
.venv/bin/python scripts/generate_domain.py
```

If you want a second config file somewhere else, you can still use:

```bash
.venv/bin/python scripts/generate_domain.py \
  --domain warehouse_ops \
  --config-file app/domains/warehouse_ops/generation_request.json \
  --generate-config
```

The default template looks like:

```json
{
  "_chatgpt_prompt": ["..."],
  "version": 1,
  "status": {
    "generated": false,
    "template_generated": false,
    "state": "draft"
  },
  "request": {
    "domain": "warehouse_ops",
    "app_name": "Warehouse Ops Assistant",
    "db_url": "",
    "output_root": "app/domains",
    "guided": true,
    "interactive_prompts": false,
    "metadata_hints": {
      "scope": "warehouse operations for work orders, assignees, and facilities",
      "example_queries": ["show open work orders"]
    },
    "clarification_hints": {
      "enum_values": {},
      "column_descriptions": {}
    }
  }
}
```

The script updates the same JSON file with run status and result paths after each run. `status.generated=true` means a domain package was written successfully. CLI flags still work and override any overlapping values from `request`.

## What The Generator Writes
For a domain named `warehouse_ops`, the generator writes:

```text
app/domains/warehouse_ops/
  __init__.py
  enums.py
  fields.py
  rules.py
  reports.json
  review_report.json
  developer_clarifications.json
  flows/README.md
  manual/README.md
  generated/
    domain.json
    domain_knowledge.json
    entity_behavior.json
    user_lookup.json
    location_lookup.json
    select_workflow.json
    sql_builder.json
    manifest/
      tables.json
      query_templates.json
      table_resolution_rules.json
```

## Current Inference Behavior
The onboarding + generator flow currently infers:

- likely noise/system tables to exclude from onboarding by default
- likely primary business table
- likely user lookup table
- likely location lookup table
- tenant-scoping columns
- primary key and important columns
- starter aliases
- ClearTM-style domain knowledge scaffold
- workflow candidates for common create/update actions
- starter list query templates
- a starter status-summary report when a primary status column exists

When `--metadata-file` is supplied, the generator also merges:

- project-specific entity labels and aliases
- business vocabulary terms
- categorized example queries
- workflow hints and trigger phrases
- scope overrides for the generated domain knowledge

## HITL Review Model
The onboarding flow is not meant to silently guess everything.

It emits:

- onboarding CLI terminal questions for fast review
- `onboarding_report.json` when requested or written through onboarding
- `review_report.json` in the generated domain package
- `onboarding_report.json` in the generated domain package when `generate_domain.py --guided` is used

The reports include:

- database target summary
- inferred primary/user/location tables
- confidence values
- metadata-hint application summary
- `needs_review` items for uncertain mappings
- suggested manual override files

The intended workflow is:

1. run `onboard_domain.py`
2. inspect the suggested included/excluded tables and clarification questions
3. rerun with overrides if needed
4. write the domain package
5. inspect `review_report.json`
6. optionally rerun `generate_domain.py --developer-clarifications` to answer semantic questions such as:
   - what is this application mainly for?
   - which table is the main business entity?
   - which table represents people or assignees?
   - which columns define status, date filters, tenant scope, and key foreign keys?
   - what user-facing labels and aliases should TAG use?
7. review `developer_clarifications.json`
8. add corrections in `manual/`
9. validate and enable the domain

If you want the lowest-effort generator path, use `--simple`. It automatically enables the developer interview, prefers `DATABASE_URL_DOCKER` when `--db-url` is omitted, and limits the second pass to table meaning, labels, and aliases instead of the full technical column review.

## Current Constraints
- Report templates are still generated at the root `reports.json` path because the current report runtime reads that legacy location.
- Flow YAML is not generated automatically. The generator emits workflow candidates into `generated/domain_knowledge.json` and leaves reviewed YAML authoring to `flows/`.
- The generator is deterministic-first. Developer clarification mode asks targeted questions, but it does not use free-form LLM inference to guess the domain.

## Metadata File Shape
Example:

```json
{
  "scope": "warehouse operations including work orders, technicians, and zones",
  "business_terms": {
    "backlog": "Open work orders not yet started"
  },
  "entities": {
    "task_transaction": {
      "label": "work orders",
      "aliases": ["ticket", "tickets"],
      "description": "Operational work orders raised by the warehouse team",
      "example_queries": ["show open work orders", "count overdue tickets"]
    }
  },
  "categorized_examples": {
    "Work Orders": ["show open work orders"],
    "Actions": ["create work order"]
  },
  "workflows": [
    {
      "workflow_id": "create_work_order",
      "table": "task_transaction",
      "operation": "insert",
      "label": "Create work order",
      "trigger_phrases": ["create work order", "log ticket"],
      "required_fields": ["title", "assignee_id", "facility_id"],
      "reasoning": "Core warehouse intake action",
      "confidence": 95
    }
  ]
}
```

## Validation
Before writing files, the generator validates the generated config and manifest through `DomainRegistry.validate_domain_artifacts(...)`.

That means the scaffold is not just written to disk. It must also satisfy the current typed domain contract.
