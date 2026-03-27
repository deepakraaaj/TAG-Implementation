# Domain Generator CLI

Date: 2026-03-12
Owner: Backend Platform

## Purpose
Provide an offline onboarding command that inspects a live database schema and scaffolds a new domain package for TAG.

This is the first implementation of the planned domain generator workflow:

- deterministic schema introspection first
- optional developer clarification interview for uncertain semantics
- generated artifacts written into `generated/`
- root-level runtime stubs created for current report and module loading paths
- human review supported through a generated `review_report.json`

## Command

```bash
python scripts/generate_domain.py --domain <domain_name>
```

Optional arguments:

- `--db-url`: override `DATABASE_URL`
- `--description`: custom generated domain description
- `--output-root`: target folder for generated domains
- `--metadata-file`: optional JSON file with project vocabulary, examples, and workflow hints
- `--clarification-file`: optional JSON file with previously approved developer clarifications
- `--developer-clarifications`: ask the developer targeted semantic questions before writing files
- `--simple`: use the lightest workflow, prefer `DATABASE_URL_DOCKER` when available, and keep questions focused on table meaning/labels
- `--force`: overwrite known generated files in an existing domain folder

Example:

```bash
python scripts/generate_domain.py \
  --domain warehouse_ops \
  --db-url "mysql+aiomysql://user:pass@host:3306/app_db" \
  --metadata-file design/warehouse_domain_hints.json \
  --output-root app/domains \
  --force
```

Simplest path when `DATABASE_URL_DOCKER` is already set:

```bash
python scripts/generate_domain.py --domain warehouse_ops --simple --force
```

## What It Generates
For a domain named `warehouse_ops`, the CLI writes:

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
The generator currently infers:

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
The generator is not meant to silently guess everything.

It emits `review_report.json` with:

- database target summary
- inferred primary/user/location tables
- confidence values
- metadata-hint application summary
- `needs_review` items for uncertain mappings
- suggested manual override files

The intended workflow is:

1. run the generator
2. inspect `review_report.json`
3. optionally rerun with `--developer-clarifications` to answer semantic questions such as:
   - which table is the main business entity?
   - which table represents people or assignees?
   - which columns define status, date filters, tenant scope, and key foreign keys?
   - what user-facing labels and aliases should TAG use?
4. review `developer_clarifications.json`
5. validate and enable the domain

If you want the lowest-effort path, use `--simple`. It automatically enables the developer interview, prefers `DATABASE_URL_DOCKER` when `--db-url` is omitted, and limits the second pass to table meaning, labels, and aliases instead of the full technical column review.

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
