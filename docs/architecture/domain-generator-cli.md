# Domain Generator CLI

Date: 2026-03-12
Owner: Backend Platform

## Purpose
Provide an offline onboarding command that inspects a live database schema and scaffolds a new domain package for TAG.

This is the first implementation of the planned domain generator workflow:

- deterministic schema introspection first
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
3. add corrections in `manual/`
4. validate and enable the domain

## Current Constraints
- Report templates are still generated at the root `reports.json` path because the current report runtime reads that legacy location.
- Flow YAML is not generated automatically. The generator emits workflow candidates into `generated/domain_knowledge.json` and leaves reviewed YAML authoring to `flows/`.
- The generator is deterministic-first and does not yet use LLM inference.

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
