# Domain Folder Analysis — How the Chatbot Learns About the DB

## Your Ideology vs What's Already Built

Your idea is: **"get the DB URL → access it → generate files so the chatbot knows the schema."**

**Good news — this is exactly what the system already does.** The `maintenance_cli` domain is the proof. Let me show you the full picture.

---

## How It Currently Works

```mermaid
graph LR
    A[DB URL] --> B[onboard_domain.py]
    B --> C[DomainOnboardingService]
    C --> D[Introspects live DB<br>via SQLAlchemy]
    D --> E[Generated Domain Files]
    E --> F[DomainRegistry loads at startup]
    F --> G[Chatbot knows the schema]
```

### The Pipeline

| Step | What Happens | File |
|------|-------------|------|
| 1. **You provide a DB URL** | `--db-url mysql://...` | [onboard_domain.py](../../scripts/onboard_domain.py) |
| 2. **DB introspection** | SQLAlchemy reflects all tables, columns, joins, FKs | [generator.py](../../tools/domain_onboarding/generator.py) (85KB) |
| 3. **Files generated** | `domain.json`, `schema_manifest.json`, `query_templates`, etc. | Written to `domains/{name}/generated/` |
| 4. **Registry loads** | At app startup, `DomainRegistry` reads & merges all domain files | [registry.py](../../app/domains/registry.py) |
| 5. **Chatbot uses it** | The SQL builder, intent service, etc. reference the registry | SQL path in graph |

### Example Command
```bash
python scripts/onboard_domain.py \
  --domain maintenance_cli \
  --db-url "mysql+pymysql://user:pass@host:3306/mydb" \
  --write --force
```

---

## What Gets Generated (maintenance_cli example)

The `maintenance_cli/generated/` folder was **auto-generated from a live DB**:

```
domains/maintenance_cli/
├── generated/                          ← AUTO-GENERATED from DB
│   ├── domain.json                     ← Bot config, prompts, intent rules
│   ├── domain_knowledge.json           ← Business terms, examples, workflows
│   ├── entity_behavior.json            ← Primary table, filters, status maps
│   ├── location_lookup.json            ← How to resolve facility names
│   ├── user_lookup.json                ← How to resolve user names
│   ├── select_workflow.json            ← Select/filter workflow config
│   ├── sql_builder.json                ← SQL generation hints
│   └── manifest/
│       ├── tables.json                 ← Full schema: 33 tables, all columns, joins
│       ├── query_templates.json        ← Pre-built SQL templates
│       └── table_resolution_rules.json ← How to pick the right table from NL
├── manual/                             ← HUMAN OVERRIDES (currently just README)
│   └── README.md
├── enums.py                            ← Status code mappings (empty/stub)
├── fields.py                           ← Field labels, dropdowns (empty/stub)
├── rules.py                            ← Business rules (empty/stub)
└── reports.json
```

### What Each Key File Teaches the Chatbot

| File | What the Chatbot Learns |
|------|------------------------|
| **`manifest/tables.json`** | Every table, every column, data types, joins (FK relationships), aliases (natural language names), tenant scopes |
| **`domain.json`** | Bot name, description, prompt templates, suggested queries, intent detection rules, status buckets |
| **`domain_knowledge.json`** | Business vocabulary, example queries users can ask, workflows (create/update actions) |
| **`query_templates.json`** | Pre-built SQL for common operations (count, list, etc.) with `{company_id}` placeholders |
| **`entity_behavior.json`** | Which table is "primary", which filters apply, date/status/user/priority keys |
| **`user_lookup.json` / `location_lookup.json`** | How to resolve "show tasks for John" → `assigned_user_id = <id>` |

---

## Is This Approach Effective? — Honest Assessment

### ✅ What's Strong

1. **Schema-aware SQL generation.** The chatbot doesn't guess column names — it reads them from `schema_manifest.json`. This is the right approach for avoiding hallucinated SQL.

2. **Layered merging (generated → manual).** The registry merges `generated/` files first, then applies `manual/` overrides. This means:
   - Auto-generated files capture the raw DB schema
   - Humans can override/refine without touching generated files
   - Re-running onboarding doesn't wipe manual customizations

3. **Config-driven, not hardcoded.** The chatbot behavior (which table to query, what filters exist, how to format responses) is driven by JSON config, not Python code. Adding a new domain doesn't require code changes.

4. **Pre-built query templates.** Common queries like "count tasks", "list facilities without tasks today" are pre-generated as SQL templates. This avoids LLM hallucination for well-known patterns.

5. **Tenant isolation baked in.** Every generated table has `tenant_scope` → `company_id`, so the SQL builder always injects `WHERE company_id = {company_id}`.

### ⚠️ What Could Be Better

1. **Column descriptions are generic.** The auto-generator produces descriptions like `"Status (INTEGER)"` or `"Name (VARCHAR(100))"`. Compare with the hand-tuned `maintenance` domain where descriptions say `"Task status (Pending, In Progress, Completed, etc.)"`. The generic descriptions give the LLM less context for understanding what values mean.

2. **Join discovery is incomplete.** The auto-generator finds FK-based joins. But some business joins (like `scheduled_ref_no` between `scheduler_task_details` and `scheduled_facility_meta_details`) are logical joins without FKs. These require manual addition.

3. **No sample data context.** The generator introspects the schema but doesn't sample actual data. Knowing that `status` contains values `{0, 1, 2, 3}` and what they map to requires manual `enums.py` work. Without this, the chatbot can't translate "show pending tasks" → `WHERE status = 0`.

4. **Generated query templates are heuristic.** The auto-generated templates are reasonable but formulaic. The hand-tuned `maintenance` domain has complex templates (multi-table JOINs with CONCAT for assignee names, cross-entity negation queries) that an auto-generator can't reliably produce.

5. **No relationship semantics.** The generator knows `task_transaction.assigned_user_id → user.id` but doesn't know that this means "tasks are assigned to users". This semantic meaning is in `domain.json` (hand-written) and drives how the chatbot interprets "my tasks" or "tasks for John".

---

## Bottom Line

> **Your ideology is sound and already implemented.** The `onboard_domain.py` script does exactly what you described: takes a DB URL, introspects the schema, and generates the domain files the chatbot needs.

**The auto-generated files get you ~60-70% of the way there.** They give the chatbot complete structural knowledge of the DB (tables, columns, joins, types).

**The remaining 30-40% requires human curation:**
- Business-meaningful column descriptions
- Enum value mappings (status codes → labels)
- Semantic relationships (which field means "assignee"?)
- Complex query templates (cross-table JOINs, negation queries)
- Lookup resolution (how to fuzzy-match "John" to a user record)

This is by design — the `generated/` vs `manual/` split exists specifically to support this workflow:
1. Run `onboard_domain.py --write` → get the structural baseline
2. Add manual overrides in `manual/` → refine the business semantics
3. Re-run onboarding safely → generated files update, manual files preserved
