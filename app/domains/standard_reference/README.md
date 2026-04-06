# Standard Reference Domain

This package is a richer, copyable reference than `app/domains/starter/`.
Use it when you want domain files that explain a database clearly to both the runtime and the humans maintaining it.

What this package shows:

- `generated/domain.json`: domain identity, assistant prompt, capabilities, and response rules
- `generated/domain_knowledge.json`: business scope, entities, sample questions, and workflow candidates
- `generated/entity_behavior.json`: how TAG interprets the main entity in natural language
- `generated/user_lookup.json` and `generated/location_lookup.json`: how assignee and site resolution works
- `generated/sql_builder.json`: SQL-builder guardrails, heuristics, and UI prompts
- `generated/manifest/tables.json`: table roles, joins, tenant scope, aliases, and important columns
- `generated/manifest/query_templates.json`: reliable starter queries for each key table
- `generated/manifest/table_resolution_rules.json`: how phrases map to tables
- `manual/glossary.json`: business-language to schema-language mapping
- `manual/semantics.json`: join hints and derived-column logic that the schema alone does not reveal
- `manual/few_shot_examples.json`: examples of how to translate user requests into DB intent
- `fields.py`, `enums.py`, and `rules.py`: UI-facing labels, enum mapping, and small domain hooks
- `reports.json`: predefined report definitions for the report route
- `flows/create_work_item.yaml`: example write flow for a basic insert action

How to adapt it:

1. Copy this folder to `app/domains/<your_domain>`.
2. Replace table names and aliases in `generated/manifest/tables.json`.
3. Update the business terms and examples in `generated/domain_knowledge.json`.
4. Review `manual/glossary.json` and `manual/semantics.json` with someone who knows the DB well.
5. Adjust `fields.py`, `enums.py`, and `reports.json` to match real columns and values.
6. Point `DOMAIN=<your_domain>` at the new package and restart the backend.

The intent is not to model one specific product. It is a standard pattern for explaining any operational relational database with tenant scope, people, locations, assets, and work records.
