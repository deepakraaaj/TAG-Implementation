# TAG Chatbot System — Complete Reference

**Owner:** Karthik @ Kritilabs (karthik.c@kritilabs.com)  
**Last Updated:** 2026-04-19  
**Goal:** NL CRUD over any Kritilabs app (VTS/FMS/PMS/HRMS/Cargo/ERPNext)  
**Status:** VTS production-ready phase (enums done, Phase 0 bugs identified)

---

## 1. Architecture Overview

```
User Query (natural language)
    ↓
POST /chat (FastAPI · app/api/v1/endpoints/chat.py)
    ↓
Intent Detection → SQL Builder → Safety Checks → SQL Execute → Response Gen
    ↓
Response (SQL, rows, natural-language summary)
```

**Tech Stack:**
- **Framework:** FastAPI + LangGraph (orchestration)
- **LLM:** Ollama (local) + qwen2.5-coder:7b (recommended)
- **DB:** MySQL 8.0+ (SQLAlchemy + INFORMATION_SCHEMA)
- **Semantic Retrieval:** ChromaDB + fastembed (BAAI/bge-small-en-v1.5)
- **Config:** YAML + JSON (domains, apps, developer hints)

**Current Hardware (Karthik's):**
- i7-13650HX, RTX 4060 Laptop 8GB VRAM, 24GB RAM, 512GB NVMe
- Runs qwen2.5-coder:7b (Q4 quantized) locally

---

## 2. Domain System

### 2.1 What is a Domain?

A **domain** = NL metadata + safety guardrails for ONE app's database.

**Location:** `./domains/{app_name}/`

```
domains/vts/
├── generated/                          # Auto-generated (regenerate often)
│   ├── context.json                   # Full schema + hints for RAG
│   ├── schema_hints.json              # Column descriptions, enum maps
│   ├── rules.yaml                     # Allowed tables/columns
│   └── ...
├── manual/                            # Hand-written overrides
│   └── developer_clarifications.json   # Enum maps, column descriptions, workflows
├── onboarding_report.json             # What was discovered during onboarding
└── review_report.json                 # Warnings (cross-table collisions, etc.)
```

### 2.2 Developer Clarifications (The Key File)

**Location:** `domains/{app}/developer_clarifications.json`

**Format:**
```json
{
  "enum_values": {
    "ignition_status": {
      "0": "Off",
      "1": "On"
    },
    "packet_type": {
      "0": "Ignition Off",
      "IF": "Ignition Off",
      "1": "Ignition On",
      "IN": "Ignition On"
    }
  },
  "column_descriptions": {
    "trip.created_at": "Timestamp when trip was created",
    "trip.status": "Current state of trip (0=Open, 1=Closed)"
  },
  "workflows": [
    {
      "name": "create_trip",
      "tables": ["trip"],
      "mutations": ["INSERT"]
    }
  ]
}
```

**Key Rules:**
- `enum_values`: Maps DB column name → `{raw_value: label}` dict
  - Raw value can be INT (as string) or STRING (for varchar/char columns)
  - Label is the business English name the LLM should use
- `column_descriptions`: Freeform hints about what each column means
- `workflows`: Define allowed INSERT/UPDATE/DELETE operations

---

## 3. Enum Extraction (Auto-Population)

### 3.1 The Problem

Enum columns in the DB (like `ignition_status` with values 0/1) need to be mapped to human-readable labels ("Off"/"On"). These mappings usually live in Java backend enums.

**Manual approach:** Hours of hunting through code + typos in IDs.  
**Auto approach:** `extract_enums.py` does it in 5 minutes.

### 3.2 How It Works

**Script:** `./scripts/extract_enums.py`

**4-Step Pipeline:**

1. **DB Introspection:** Query `INFORMATION_SCHEMA` for columns matching `*status`, `*type`, `*category`, `*state`, `*source`
   - Skip FK columns (`_id` suffix)
   - Skip TEXT columns in `*_master` tables (they ARE the labels)
   - Skip bitmask/dirty columns (hex values)

2. **Java Enum Parsing:** Recursive grep through source for `public enum ClassName { ... }`
   - Extract member identifiers, labels, and optional codes (3-arg constructor)
   - Example: `IGNITION_ON("Ignition On", "IN", 1)` → `{identifier: IGNITION_ON, label: "Ignition On", code: "IN", id: "1"}`

3. **Column ↔ Enum Matching:** Dual-scoring system
   - **Name Score:** Overlap between column name tokens and enum/class name tokens
     - Exclude generic tokens (`status`, `type`, `id`, `vts`, `transaction`, etc.)
     - Prevents false matches like `main_power_status` → `TransactionTripStatus`
   - **Value Score:** Overlap between DB values and enum values (int IDs, string codes, or label names)
     - For varchar columns: label-set matching (e.g., `ignition_status` stores 'Off'/'On' strings)
   - **Total Score:** Weighted combination
     - Thresholds: `min_name_score=50, min_value_score=75, min_total_score=130`

4. **JSON Output:** Two formats
   - **Flat:** `{column_name: {value: label}}` (legacy TAG format, loses cross-table collisions)
   - **Nested:** `{table: {column: {value: label}}}` (collision-safe, authoritative)

### 3.3 Running the Extractor

```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

# Extract without merging (preview only)
python scripts/extract_enums.py \
  --source-dir "/path/to/vts-api-service/src/main/java" \
  --request-file scripts/generate_domain.request.json \
  --output-file scripts/extracted_enums.vts.json \
  --db-url "mysql+pymysql://root:12345@localhost:3306/VTS"

# Extract with merge into request.json
python scripts/extract_enums.py \
  --source-dir "/path/to/vts-api-service/src/main/java" \
  --request-file scripts/generate_domain.request.json \
  --output-file scripts/extracted_enums.vts.json \
  --db-url "mysql+pymysql://root:12345@localhost:3306/VTS" \
  --merge
```

**Output:** `extracted_enums.vts.json`
```json
{
  "_summary": {
    "matched": 14,
    "needs_review": 3,
    "no_match": 0,
    "collision_count": 0
  },
  "enum_values": { ... },
  "enum_values_by_table": { ... },
  "_review": { ... }
}
```

### 3.4 VTS Status (As of 2026-04-19)

- **81 Java enum classes** found in vts-api-service
- **17 candidate enum columns** in DB
- **14 auto-matched** (82% accuracy): `ignition_status`, `packet_type`, `packet_status`, `alert_type`, `category`, `emergency_status`, `invoice_category`, `invoice_type`, `implementation_status`, `report_type`, `type`, `recent_state_id`, etc.
- **3 needs_review:**
  - `state_source` → likely `TripStateUpdateSource` (user to confirm)
  - `main_power_status` → no enum found (define as 0=Off, 1=On)
  - `network_type` → no enum (has 9 data misspellings of "airtel" in DB)

---

## 4. Domain Regeneration

**Script:** `./scripts/generate_domain.py`

Introspects a live database and generates all domain artifacts.

```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

python scripts/generate_domain.py \
  --config scripts/generate_domain.request.json \
  --db-url "mysql+pymysql://root:12345@localhost:3306/VTS" \
  --force
```

**Input:** `generate_domain.request.json`
```json
{
  "request": {
    "domain": "vts",
    "app_name": "vts",
    "db_url": "mysql+aiomysql://...",
    "include_tables": ["trip", "vehicle", "alert_cfg", ...],
    "metadata_hints": {
      "scope": "Vehicle tracking system",
      "example_queries": [
        "Show me all trips for vehicle ABCD06",
        "How many vehicles are active?"
      ]
    },
    "clarification_hints": {
      "enum_values": { ... },      # Populated by extract_enums.py
      "column_descriptions": { ... }
    }
  }
}
```

**Output:** 19 files in `domains/vts/`
- `context.json` — Full schema for RAG
- `schema_hints.json` — Column names, types, descriptions
- `rules.yaml` — Allowed tables/columns for safety
- `developer_clarifications.json` — Your manual hints (enums, descriptions, workflows)
- `onboarding_report.json` — What was auto-discovered
- `review_report.json` — Manual review flags (collisions, ambiguities)

---

## 5. VTS Database Setup

### 5.1 Credentials

**Current (Local):**
```
Host: localhost
User: root
Password: 12345
Port: 3306
Database: VTS
Connection String: mysql+pymysql://root:12345@localhost:3306/VTS
```

**Configuration File:** `./.env`
```bash
DATABASE_URL=mysql+aiomysql://root:12345@127.0.0.1:3306/VTS?charset=utf8mb4
```

### 5.2 Key Tables

| Table | Purpose | Rows | Tenant Filter |
|-------|---------|------|---|
| `trip` | Core trip records | ~10K | `company_id` |
| `vehicle` | Asset registry | ~500 | `company_id` |
| `location` | Geofences / sites | ~100 | `company_id` |
| `alert_cfg` | Event definitions | ~50 | None |
| `vts_transaction` | Event logs | ~1M | None (has `trip_id` FK) |
| `trip_status_master` | Enum labels (read-only) | 25 | None |
| `device_command_master` | Command definitions | ~10 | None |

### 5.3 Known Data Quality Issues

- **`vts_transaction.network_type`:** 9 misspellings of "airtel" (e.g., "airtell", "airtel ", " airtel")
  - Fix: Standardize in DB or add fuzzy matching in enum_values
- **`developer_clarifications.json` had wrong enum IDs:** e.g., `recent_state_id: {"10": "Created"}` when actual DB has `{1: "Created"}`
  - Fixed by auto-extractor (derives IDs from live DB, not manual entry)

---

## 6. Current Diagnostic & Pass Rate

**Test Questions:** 20 VTS questions in `scripts/vts_diagnostic_questions.json`

**Failure Modes (FM1-FM8):**
- FM1: Wrong table selected
- FM2: Right table, wrong/missing columns
- FM3: Wrong joins (duplicates, missing rows, cartesian)
- **FM4: Missing implicit filters (soft-delete `is_active`, tenant `company_id`)** ← 6/20 failures
- **FM5: Wrong enum interpretation (int vs label string)** ← FIXED by enum injection
- FM6: Wrong value interpretation (units, timezones, formats)
- FM7: Missing business-glossary term misread
- FM8: Metadata has it but LLM ignored it

**Current LLM:** qwen2.5:0.5b (0.5B params, 20% pass rate)  
**Recommended LLM:** qwen2.5-coder:7b (7B params, Q4 quantized, fits in 8GB VRAM)

**Setup:**
```bash
ollama pull qwen2.5-coder:7b
# Edit .env: LLM_MODEL=/path/to/qwen2.5-coder-7b.gguf or use model name
```

**Run Diagnostic (requires TAG API running):**
```bash
# Start TAG API (Docker or uvicorn)
python /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant/scripts/run_diagnostic.py \
  --url http://localhost:8012 \
  --out diagnostics/results_with_enums.jsonl \
  --report diagnostics/report_with_enums.txt
```

**Expected Improvement:** Enum injection fixes FM5 (wrong enum interpretation), moving some questions from fail to pass.

---

## 7. Onboarding a New App (SOP)

### 7.1 Prerequisites

- ✅ MySQL database (schema finalized)
- ✅ Java Spring backend with `public enum` classes in `constants/` folder
- ✅ DB credentials (host, user, password, port, database)

### 7.2 Steps

**Step 1: Create Request File**

```bash
cd .

# Copy template
cp scripts/generate_domain.request.json scripts/{app_name}_request.json
```

Edit to set:
```json
{
  "request": {
    "domain": "{app_name}",
    "app_name": "{app_name}",
    "db_url": "mysql+aiomysql://{user}:{pass}@{host}:{port}/{db}",
    "include_tables": ["table1", "table2", ...],
    "metadata_hints": {
      "scope": "Brief description",
      "example_queries": ["Q1", "Q2", "Q3"]
    }
  }
}
```

**Step 2: Extract Enums**

```bash
python scripts/extract_enums.py \
  --source-dir "/path/to/app/src/main/java" \
  --request-file scripts/{app_name}_request.json \
  --output-file scripts/extracted_enums.{app_name}.json \
  --db-url "mysql+pymysql://{user}:{pass}@{host}:{port}/{db}" \
  --merge
```

Review output: How many matched? Any flagged as needs_review?

**Step 3: Generate Domain**

```bash
python scripts/generate_domain.py \
  --config scripts/{app_name}_request.json \
  --db-url "mysql+pymysql://{user}:{pass}@{host}:{port}/{db}" \
  --force
```

Review artifacts in `domains/{app_name}/`

**Step 4: Manual Review**

Edit `domains/{app_name}/developer_clarifications.json`:
- Verify enum mappings (especially needs_review cases)
- Add column descriptions (if needed for business context)
- Define workflows (INSERT, UPDATE, DELETE operations)

**Step 5: Regenerate (If Manual Changes)**

```bash
python scripts/generate_domain.py \
  --config scripts/{app_name}_request.json \
  --db-url "mysql+pymysql://{user}:{pass}@{host}:{port}/{db}" \
  --force
```

**Step 6: Register App**

Edit `./config/apps.local.yaml`:
```yaml
apps:
  {app_name}:
    domain: {app_name}
    allow_mutations: false  # Start conservative
    tenant_id_column: company_id
```

**Step 7: Test**

Add 20 domain-specific test questions to `scripts/vts_diagnostic_questions.json` and run diagnostic.

### 7.3 Typical Timeline

| Task | Time |
|------|------|
| Enum extraction | 5 min |
| Domain generation | 2 min |
| Manual review (enum/column fixes) | 30 min (first time), 10 min (subsequent) |
| App registration | 2 min |
| Diagnostic run | 10 min |
| **Total** | **~1 hour** |

---

## 8. Known Phase 0 Bugs

**Status:** Identified but not yet fixed

| ID | Issue | Impact | Severity |
|----|-------|--------|----------|
| B1 | Tenant filter (`company_id`) missing in 6/20 queries | Data leak across tenants | **CRITICAL** |
| B2 | Soft-delete (`is_active`) not filtered in joins | Deleted records appear in results | **HIGH** |
| B3 | Enum FM5 (int vs string label) | Wrong enum interpretation | MEDIUM (Partially fixed by enum injection) |
| B4 | Business glossary not reaching entity detection in 3/20 queries | Columns with synonyms not found | MEDIUM |
| B5 | CRUD disabled (`allow_mutations: false`) | Read-only only | MEDIUM (by design for beta) |
| B6 | No intermediate/verify/validate guardrails | LLM can generate bad SQL | HIGH |

---

## 9. File Structure Reference

```
/home/deepakrajb/Desktop/ChatBot/
├── NL2SQL Assistant/                    # Main chatbot app
│   ├── app/
│   │   ├── main.py                       # FastAPI entry point
│   │   ├── api/v1/endpoints/chat.py      # POST /chat handler
│   │   ├── assistant/
│   │   │   ├── nodes/core/chat_node.py   # Query processing
│   │   │   └── engine/
│   │   │       ├── router/               # Intent detection
│   │   │       ├── sql/                  # SQL builder
│   │   │       └── response/             # Response generation
│   │   └── services/
│   │       ├── data/schema_service.py    # DB introspection
│   │       └── guardrails/               # Safety checks
│   ├── domains/
│   │   ├── vts/
│   │   │   ├── generated/
│   │   │   │   ├── context.json
│   │   │   │   └── schema_hints.json
│   │   │   ├── manual/
│   │   │   │   └── developer_clarifications.json
│   │   │   ├── onboarding_report.json
│   │   │   └── review_report.json
│   │   └── {other_apps}/
│   ├── scripts/
│   │   ├── extract_enums.py              # Auto-enum extractor
│   │   ├── generate_domain.py            # Domain generator
│   │   ├── benchmark_llm.py              # LLM latency tests
│   │   └── debug_chat.py                 # Manual chat testing
│   ├── tests/
│   │   ├── e2e/test_chat_and_reporting_e2e.py
│   │   └── unit/
│   ├── config/
│   │   └── apps.local.yaml               # App registry
│   ├── .env                              # Local config (DB, LLM)
│   ├── Makefile                          # make up, make test, etc.
│   └── Dockerfile
├── OpenMetaData/                         # Legacy (not used in VTS beta)
├── KLProjects/Frontend/VTS Project/
│   └── vts-api-service/
│       └── src/main/java/com/kritilabs/vts/
│           └── constants/                # 92 Java enum classes
├── scripts/vts_diagnostic_questions.json  # 20 VTS diagnostic questions
├── scripts/run_diagnostic.py              # Test runner
├── docs/operations/hardening/production-readiness-plan.md  # Phase 0, 1, 2 roadmap
└── docs/product/tag-assistant/chatbot-system.md            # This file
```

---

## 10. Environment Variables (.env)

**Critical for Local Dev:**
```bash
# Database
DATABASE_URL=mysql+aiomysql://root:12345@127.0.0.1:3306/VTS?charset=utf8mb4
DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/VTS?charset=utf8mb4

# LLM (Ollama local)
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen2.5:0.5b  # or qwen2.5-coder:7b
LLM_API_KEY=dummy

# Domain
DEFAULT_CHAT_APP_ID=vts
DOMAIN=vts
APPS_CONFIG_PATH=./config/apps.local.yaml

# Semantic Retrieval
SEMANTIC_RETRIEVAL_ENABLED=true
SEMANTIC_RETRIEVAL_PROVIDER=fastembed
SEMANTIC_RETRIEVAL_MODEL=BAAI/bge-small-en-v1.5

# Redis (optional, for caching)
REDIS_URL=redis://localhost:6384/0
```

---

## 11. Quick Commands

```bash
# Extract enums for VTS
cd .
python scripts/extract_enums.py \
  --source-dir "/path/to/vts-api-service/src/main/java" \
  --request-file scripts/generate_domain.request.json \
  --output-file scripts/extracted_enums.vts.json \
  --db-url "mysql+pymysql://root:12345@localhost:3306/VTS" \
  --merge

# Regenerate VTS domain
python scripts/generate_domain.py \
  --config scripts/generate_domain.request.json \
  --db-url "mysql+pymysql://root:12345@localhost:3306/VTS" \
  --force

# Start TAG API (requires deps)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8012

# Run diagnostic
python /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant/scripts/run_diagnostic.py \
  --url http://localhost:8012 \
  --out diagnostics/results.jsonl \
  --report diagnostics/report.txt

# Docker
cd .
make up        # Start containers
make down      # Stop containers
make logs      # View logs
make test      # Run pytest
```

---

## 12. Success Criteria & Next Steps

### Current State (2026-04-19)

✅ **Done:**
- VTS enums extracted and injected (12/12 columns)
- Domain regenerated with enum metadata
- Extract-enums script ready for FMS/PMS/HRMS/Cargo
- Infrastructure bugs (pymysql, JDBC params) fixed

⏳ **In Progress:**
- Enum impact on diagnostic (needs TAG API running)
- Phase 0 bug fixes (tenant filter, soft-delete)

📋 **Pending:**
- Regenerate diagnostic with updated LLM (qwen2.5-coder:7b)
- Write SOP markdown for future app onboarding
- Collect 15-20 real IOCL user questions (currently have 4)
- Verify data residency / cloud AI permission for IOCL
- Add column_descriptions (empty currently)

### Next: Which to prioritize?

**Option A** ✅ **DONE**
- Enum extraction + injection + domain regeneration

**Option B** — Write Onboarding SOP
```bash
# For future AIs onboarding FMS/PMS/HRMS/Cargo
# Single-command flow to generate domain in <10 min
```

**Option C** — Fix Phase 0 bugs
- Tenant filter (company_id) missing in 6/20 queries
- Soft-delete (is_active) not filtered

---

## 13. Common Pitfalls & Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| `allowPublicKeyRetrieval` error | JDBC params in MySQL URL | Use `_normalize_db_url()` to strip them |
| `mysqlconnector` not found | Wrong driver dialect | Swap to `pymysql` in `schema_service.py` |
| Database connection refused | Using remote IP, need localhost | Update `.env` `DATABASE_URL` |
| Enum not matched | Generic token overlap (e.g., "status") | Add to `_GENERIC_TOKENS` stoplist |
| Varchar enum not matching | Only checking int IDs, not label strings | Add label-set matching in `_value_score()` |
| Cross-table collision on `type` | Flat enum_values loses 2 of 3 matches | Use `enum_values_by_table` (nested) format |

---

## 14. References

- **docs/operations/hardening/production-readiness-plan.md** — Phase roadmap and known issues
- **scripts/vts_diagnostic_questions.json** — 20 VTS diagnostic test cases
- **scripts/extract_enums.py** — Enum auto-extraction source code
- **scripts/generate_domain.py** — Domain generation source code
- **app/main.py** — FastAPI app entry point
- **domains/vts/developer_clarifications.json** — VTS enum mappings

---

**End of Reference.** For questions, contact: karthik.c@kritilabs.com
