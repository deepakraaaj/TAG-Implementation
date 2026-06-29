# App Onboarding SOP — Standard Operating Procedure

**Purpose:** Add a new Kritilabs application to the TAG chatbot in <1 hour  
**Target Users:** Developers, DevOps, Karthik  
**Prerequisites:** MySQL database + Java Spring backend with enums  
**Last Updated:** 2026-04-19

---

## Quick Start (5 Steps, ~60 min)

```bash
# Step 1: Prepare request file (2 min)
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant
cp scripts/generate_domain.request.json scripts/{app}_request.json
# Edit: set domain, app_name, db_url, include_tables, example_queries

# Step 2: Extract enums (5 min)
python scripts/extract_enums.py \
  --source-dir "/path/to/{app}/src/main/java" \
  --request-file scripts/{app}_request.json \
  --output-file scripts/extracted_enums.{app}.json \
  --db-url "mysql+pymysql://{user}:{pass}@{host}:{port}/{db}" \
  --merge

# Step 3: Generate domain (2 min)
python scripts/generate_domain.py \
  --config scripts/{app}_request.json \
  --db-url "mysql+pymysql://{user}:{pass}@{host}:{port}/{db}" \
  --force

# Step 4: Manual review (30 min)
# Edit: domains/{app}/developer_clarifications.json
#   - Verify enum mappings
#   - Add column descriptions
#   - Define workflows (INSERT/UPDATE/DELETE)

# Step 5: Register & test (20 min)
# Edit: config/apps.local.yaml
#   - Add app entry
# Add 20 test questions to scripts/vts_diagnostic_questions.json
# Run diagnostic
```

---

## Detailed Walkthrough

### Prerequisites Checklist

**Before you start, verify:**

- [ ] Access to the app's MySQL database
  ```bash
  mysql -h {host} -u {user} -p{pass} {database}
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}';
  # Should return table count > 5
  ```

- [ ] App has Java backend with enums
  ```bash
  find /path/to/app/src/main/java -name "*.java" | xargs grep "public enum" | wc -l
  # Should return > 5 enums
  ```

- [ ] Credentials ready: `{user}`, `{pass}`, `{host}`, `{port}`, `{database}`

- [ ] NL2SQL Assistant directory accessible
  ```bash
  cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant
  ls scripts/{generate_domain.py,extract_enums.py}
  ```

---

### Step 1: Create Request File (2 min)

**File:** `scripts/{app_name}_request.json`

**Command:**
```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant
cp scripts/generate_domain.request.json scripts/fms_request.json
```

**Edit the file with:**

```json
{
  "request": {
    "domain": "fms",
    "app_name": "fms",
    "description": "Fleet Management System — route planning, fuel tracking, driver assignments",
    "db_url": "mysql+aiomysql://user:pass@host:3306/FMS",
    "include_tables": [
      "trip",
      "vehicle",
      "driver",
      "route",
      "fuel_log",
      "maintenance_log",
      "expense",
      "location",
      "alert_cfg",
      "device_cfg"
    ],
    "metadata_hints": {
      "scope": "Fleet operations — trips, vehicles, drivers, routes, fuel/maintenance tracking",
      "example_queries": [
        "Show me all active trips for driver John",
        "How much fuel did vehicle ABC-123 consume this month?",
        "What's the total maintenance cost for all vehicles in Q1?",
        "List all vehicles due for maintenance in the next 7 days"
      ]
    },
    "clarification_hints": {
      "enum_values": {},
      "column_descriptions": {}
    }
  }
}
```

**Key Fields to Customize:**

| Field | What to Do |
|-------|-----------|
| `domain` | Lowercase app name (fms, pms, hrms, cargo) |
| `app_name` | Same as domain |
| `description` | 1-2 sentence purpose |
| `db_url` | MySQL connection string (see [Database URL Format](#database-url-format)) |
| `include_tables` | List 8-15 core tables (skip logging tables, audit tables) |
| `example_queries` | 3-4 real user questions in natural language |

#### Database URL Format

```
mysql+aiomysql://{user}:{password}@{host}:{port}/{database}
```

**Examples:**
```
mysql+aiomysql://fms_user:pass123@localhost:3306/FMS
mysql+aiomysql://remote_192_168_15_49:12345@192.168.15.112:3306/FMS
```

**⚠️ Important:** Use `mysql+aiomysql://` in the request file (async driver).  
When running extract_enums.py or generate_domain.py, override with `--db-url "mysql+pymysql://..."` (sync driver).

**Verify:**
```bash
# Test the URL works
python -c "
from sqlalchemy import create_engine
url = 'mysql+pymysql://user:pass@host:3306/FMS'
engine = create_engine(url)
with engine.connect() as conn:
    result = conn.execute('SELECT 1')
    print('✓ Connection successful')
"
```

---

### Step 2: Extract Enums (5 min)

**Purpose:** Auto-discover enum mappings from Java source code + live DB

**Command:**
```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

python scripts/extract_enums.py \
  --source-dir "/home/dev/fms/backend/src/main/java" \
  --request-file scripts/fms_request.json \
  --output-file scripts/extracted_enums.fms.json \
  --db-url "mysql+pymysql://user:pass@localhost:3306/FMS" \
  --merge
```

**Expected Output:**
```
[1/4] DB schema: FMS
[2/4] Scanning 15 tables for enum-style columns…
      → 12 candidate columns
[3/4] Parsing Java enums under /home/dev/fms/backend/src/main/java…
      → 45 enum classes
[4/4] Matching columns ↔ enums…
      wrote scripts/extracted_enums.fms.json
      matched=10  needs_review=2  no_match=0
      merged enum_values into scripts/fms_request.json
```

**What to Look For:**

| Line | Meaning |
|------|---------|
| `→ 12 candidate columns` | Good (5-20 is healthy range) |
| `→ 45 enum classes` | Good (backend should have enums) |
| `matched=10` | Success rate: 10/12 = 83% |
| `needs_review=2` | Manual verification required (see Step 4) |
| `no_match=0` | No unmatched columns (ideal) |

**If Something's Wrong:**

| Problem | Cause | Fix |
|---------|-------|-----|
| `0 candidate columns` | No enum-style columns found in DB | Check `include_tables` list, may need to add more columns with type/status/category suffix |
| `0 enum classes` | No `public enum` in Java source | Verify path, may use different pattern (constants class, not enum) |
| `high no_match count (>3)` | Enum definitions don't match DB column names | Add to Step 4 manual review, may need fuzzy matching |
| Connection error | DB unreachable | Test DB URL with `mysql -h ... -u ... -p ...` |

**Review Generated Files:**

```bash
# See what was extracted
cat scripts/extracted_enums.fms.json | python -m json.tool | head -50

# Check what was merged into request file
grep -A 20 "enum_values" scripts/fms_request.json
```

---

### Step 3: Generate Domain (2 min)

**Purpose:** Introspect live DB + combine with enums to create domain artifacts

**Command:**
```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

python scripts/generate_domain.py \
  --config scripts/fms_request.json \
  --db-url "mysql+pymysql://user:pass@localhost:3306/FMS" \
  --force
```

**Expected Output:**
```
Guided onboarding summary
  Database target: mysql+pymysql://localhost:3306/FMS (password hidden)
  Included tables (15): trip, vehicle, driver, route, fuel_log, ...
  Excluded tables (8): audit_log, event_log, ...
  Primary table candidate: trip
  User table candidate: driver
  Location table candidate: location
  Review flags:
    - Ignore likely system or noise tables...? Recommended: yes
    - Please review user_lookup.id_filter_key...? Recommended: driver_id
    - Do the inferred workflow candidates match...? Recommended: review workflows
Auto mode: applying guided table recommendations from the JSON/non-interactive workflow.
Generated domain `fms`
Files written: 19
Needs review: 2
Review report: domains/fms/review_report.json
Onboarding report: domains/fms/onboarding_report.json
Developer clarifications: domains/fms/developer_clarifications.json
```

**What This Created:**

```
domains/fms/
├── generated/
│   ├── context.json              # Full schema for RAG
│   ├── schema_hints.json         # Column metadata
│   ├── rules.yaml                # Safety guardrails
│   └── ...
├── developer_clarifications.json # YOUR NEXT EDIT ←
├── onboarding_report.json        # Auto-discovered tables
└── review_report.json            # Warnings (collisions, ambiguities)
```

**Verify:**
```bash
# Check structure
ls -la domains/fms/
ls -la domains/fms/generated/

# Check enums made it in
python -c "
import json
d = json.loads(open('domains/fms/developer_clarifications.json').read())
print(f'Enums: {len(d.get(\"enum_values\", {}))} columns')
print(f'Descriptions: {len(d.get(\"column_descriptions\", {}))} hints')
"
```

---

### Step 4: Manual Review (30 min)

**File to Edit:** `domains/fms/developer_clarifications.json`

**Purpose:** Verify auto-extracted data + add business context

#### 4.1 Verify Enum Mappings

```bash
# Show what was extracted
python -c "
import json
d = json.loads(open('domains/fms/developer_clarifications.json').read())
for col, vals in d['enum_values'].items():
    print(f'{col}:')
    for k, v in list(vals.items())[:5]:
        print(f'  {k:10} → {v}')
    if len(vals) > 5:
        print(f'  ... ({len(vals)} total values)')
    print()
"
```

**Check Each:**
- Do values look correct? (e.g., 0=Off, 1=On for status)
- Any typos or garbage values? (e.g., "OfF" instead of "Off")
- Do IDs match the DB? (e.g., SELECT DISTINCT status FROM trip_status_master)

**If Wrong:**

**Case 1: Missing enum**
- Manually add to `enum_values`
- Example:
  ```json
  "trip_priority": {
    "0": "Low",
    "1": "Normal",
    "2": "High",
    "3": "Critical"
  }
  ```

**Case 2: Wrong mapping (e.g., IDs are 10, 20, 30 not 1, 2, 3)**
- Delete the wrong entry, run extract_enums.py again
- OR manually fix the IDs in developer_clarifications.json

**Case 3: Varchar column with string labels (e.g., ignition_status = 'Off'/'On' not 0/1)**
- Extract_enums.py should handle this (label-set matching)
- If not matched, manually add:
  ```json
  "ignition_status": {
    "Off": "Off",
    "On": "On"
  }
  ```

#### 4.2 Add Column Descriptions

```json
{
  "column_descriptions": {
    "trip.status": "Current state of trip (0=Open, 1=In-Progress, 2=Completed, 3=Cancelled)",
    "trip.vehicle_id": "FK to vehicle table; vehicle assigned to this trip",
    "trip.driver_id": "FK to driver table; primary driver for this trip",
    "trip.start_time": "Timestamp when trip departed from origin",
    "trip.end_time": "Timestamp when trip arrived at destination (null if not completed)",
    "trip.distance_km": "Total distance traveled in kilometers",
    "trip.fuel_consumed_liters": "Estimated fuel consumed during trip",
    "vehicle.registration_number": "License plate number (e.g., ABC-1234)",
    "vehicle.is_active": "0=Inactive/Decommissioned, 1=Active (soft-delete marker)",
    "driver.phone_number": "Mobile number; used for alerts and communication",
    "driver.is_verified": "1=Verified license, 0=Unverified or expired"
  }
}
```

**Guidelines:**
- One description per column
- Include enum values if not obvious
- Mention FKs (foreign keys)
- Note soft-delete columns (is_active, deleted_at, is_archived)
- Keep to 1-2 sentences

#### 4.3 Define Workflows (Optional for Beta)

```json
{
  "workflows": [
    {
      "name": "create_trip",
      "description": "Create a new trip record",
      "tables": ["trip"],
      "mutations": ["INSERT"],
      "required_columns": ["vehicle_id", "driver_id", "start_location_id", "end_location_id"],
      "examples": [
        "Create a trip from warehouse to customer site XYZ"
      ]
    },
    {
      "name": "complete_trip",
      "description": "Mark trip as completed with final data",
      "tables": ["trip"],
      "mutations": ["UPDATE"],
      "filter": "status != 2",
      "examples": [
        "Mark trip #12345 as completed with 150 km distance"
      ]
    }
  ]
}
```

**Keep workflows disabled for now** (`allow_mutations: false` in apps.local.yaml). Workflows are for Phase 2.

#### 4.4 Checklist

**Before Moving to Step 5, verify:**

- [ ] All extracted enums look correct (no garbage values)
- [ ] Any typos or wrong mappings fixed
- [ ] Added at least 5 important column descriptions
- [ ] No obvious omissions (e.g., status columns with no enum)

**Command to validate JSON:**
```bash
python -m json.tool domains/fms/developer_clarifications.json > /dev/null && echo "✓ Valid JSON"
```

---

### Step 5: Register App & Test (20 min)

#### 5.1 Register in App Registry

**File:** `config/apps.local.yaml`

**Add entry:**
```yaml
apps:
  fms:
    domain: fms
    description: "Fleet Management System"
    allow_mutations: false
    tenant_id_column: company_id
    example_query: "Show me all active trips"
```

**Field Explanations:**

| Field | Value | Notes |
|-------|-------|-------|
| `domain` | fms | Must match domain folder name |
| `description` | Short description | Shown in UI |
| `allow_mutations` | false | Keep false for beta (Phase 2: true) |
| `tenant_id_column` | company_id | The column used to filter by tenant (null if no multi-tenancy) |
| `example_query` | One user question | Shown as hint |

**Verify:**
```bash
# Check YAML syntax
python -c "
import yaml
with open('config/apps.local.yaml') as f:
    data = yaml.safe_load(f)
    print(f'✓ {len(data[\"apps\"])} apps registered')
    for app in data['apps']:
        print(f'  - {app}')
"
```

#### 5.2 Create Test Questions

**File:** `scripts/vts_diagnostic_questions.json` (add to existing 20-question VTS set)

**Add 20 FMS-specific questions:**

```json
[
  {
    "id": 101,
    "category": "lookup",
    "question": "Show me all trips for vehicle ABC-1234",
    "probe": "FM4 — company_id must appear in WHERE clause",
    "expected_tables": ["trip"],
    "required_patterns": ["trip", "ABC-1234"],
    "required_filter_patterns": ["company_id"],
    "forbidden_patterns": [],
    "failure_mode_if_no_required": "FM4",
    "notes": "Vehicle lookup. Must filter by company_id for multi-tenancy."
  },
  {
    "id": 102,
    "category": "aggregation",
    "question": "What's the total fuel consumed this month?",
    "probe": "FM6 — date range interpretation",
    "expected_tables": ["trip"],
    "required_patterns": ["fuel_consumed", "SUM|sum"],
    "required_filter_patterns": ["MONTH|month", "CURRENT"],
    "forbidden_patterns": [],
    "failure_mode_if_no_required": "FM6",
    "notes": "Aggregation. Must filter to current month only."
  },
  {
    "id": 103,
    "category": "enum",
    "question": "List all pending trips",
    "probe": "FM5 — enum interpretation (status=1 means In-Progress, not pending)",
    "expected_tables": ["trip"],
    "required_patterns": ["status", "1"],
    "required_filter_patterns": ["status"],
    "forbidden_patterns": [],
    "failure_mode_if_no_required": "FM5",
    "notes": "Enum interpretation. 'Pending' may mean status=0 or status=1 depending on business logic."
  }
]
```

**Guidelines for 20 Questions:**

| Category | Count | Example |
|----------|-------|---------|
| Simple Lookup | 4 | "Show trips for vehicle X" |
| Aggregation (SUM/COUNT) | 4 | "Total fuel this month" |
| Time-based Filtering | 4 | "Trips in last 7 days" |
| Enum Interpretation | 4 | "List active vehicles" |
| Join Queries | 4 | "Trips by driver with fuel cost" |

**Ensure variety in:**
- Single table vs. multi-table (joins)
- SELECT vs. aggregation
- Simple filters vs. complex WHERE clauses
- Date/time interpretation
- Enum value interpretation

#### 5.3 Run Diagnostic

**Prerequisite:** TAG API must be running on port 8012

**Start API:**
```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant
python -m uvicorn app.main:app --host 127.0.0.1 --port 8012 &
sleep 5
curl http://localhost:8012/health  # Verify it's up
```

**Run Diagnostic:**
```bash
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

python scripts/run_diagnostic.py \
  --url http://localhost:8012 \
  --out diagnostics/results_fms.jsonl \
  --report diagnostics/report_fms.txt
```

**Expected Output:**
```
Running 20 FMS diagnostic questions...
Progress: [████████████████████] 20/20
Results written to: diagnostics/results_fms.jsonl
Report written to: diagnostics/report_fms.txt

Summary:
  Total: 20
  Pass: 12 (60%)
  FM1: 0
  FM2: 2
  FM3: 0
  FM4: 2
  FM5: 2
  FM6: 2
  FM7: 0
  FM8: 0
  no_sql: 0
  api_error: 0
```

**Interpret Results:**

```bash
# Show detailed report
cat diagnostics/report_fms.txt

# Show failed questions
cat diagnostics/results_fms.jsonl | python -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    if r['status'] != 'pass':
        print(f\"Q{r['id']}: {r['failure_mode']}\")
        print(f\"  Query: {r['question']}\")
        print(f\"  Error: {r.get('error_message', 'N/A')}\")
        print()
"
```

**Good Pass Rate:** 60%+ for first-time onboarding  
**Acceptable:** 50%+ (known Phase 0 bugs: tenant filter, soft-delete)  
**Below 50%:** Review enum mappings and column descriptions

---

## Troubleshooting

### Extract Enums Fails

**Error: `Connection refused` or `Access denied`**
```
Fix: Verify DB URL and credentials
python -c "
from sqlalchemy import create_engine
url = 'mysql+pymysql://user:pass@host:3306/DB'
engine = create_engine(url)
engine.connect()
"
```

**Error: `0 enum classes`**
```
Fix: Verify Java source path
find /path -name "*.java" | xargs grep "public enum" | head -5
# Should find enums
```

**Error: `0 candidate columns`**
```
Fix: Your DB may not have typical enum columns (status, type, category)
Alternative: Manually add to developer_clarifications.json
```

### Generate Domain Fails

**Error: `Domain directory already exists`**
```
Fix: Use --force flag
python scripts/generate_domain.py ... --force
```

**Error: `DB connection timeout`**
```
Fix: Check network, firewall, DB is running
mysql -h {host} -u {user} -p{pass} {database} -e "SELECT 1"
```

### Diagnostic Shows Low Pass Rate (<40%)

**Common Causes & Fixes:**

| Failure Mode | Cause | Fix |
|--------------|-------|-----|
| FM4 (tenant filter) | company_id not in WHERE | Known Phase 0 bug; expected until fix deployed |
| FM2 (wrong columns) | LLM confused about schema | Add more column_descriptions |
| FM5 (enum interpret) | Wrong enum_values mapping | Re-check Step 4 enum mappings |
| FM6 (value interpret) | Date/time ambiguity | Add clarification_hints for date columns |
| no_sql (0 results) | Safety guardrails too strict | Check rules.yaml, may need to allow more tables |

---

## Success Criteria

### Minimum Viable Onboarding

✅ **Done when:**
- [ ] Request file created and DB connection works
- [ ] Enum extraction runs successfully (>70% matched)
- [ ] Domain generated (19 files written)
- [ ] Manual review complete (enum_values verified, descriptions added)
- [ ] App registered in apps.local.yaml
- [ ] 20 test questions added
- [ ] Diagnostic runs (no API errors)
- [ ] Pass rate ≥ 50%

**Time:** ~60 minutes  
**Next:** Deploy to production (Phase 2)

### Production Readiness Checklist

- [ ] Pass rate ≥ 70%
- [ ] All Phase 0 bugs fixed (tenant filter, soft-delete)
- [ ] 30+ real user questions tested
- [ ] Column_descriptions complete (100+ entries)
- [ ] Workflows defined for CRUD operations
- [ ] Data quality verified (no typos, duplicates, missing values)
- [ ] Performance tested (query latency <2s, throughput >10 QPS)
- [ ] Security review passed (no SQL injection vectors)

---

## Common Patterns by App Type

### FMS (Fleet Management System)

**Key Tables:** trip, vehicle, driver, route, fuel_log, maintenance_log  
**Tenant Filter:** company_id  
**Typical Enums:** trip_status (open/closed), vehicle_status (active/inactive), driver_status (verified/unverified)  
**Example Queries:**
```
Show me all trips for vehicle ABC-1234
How much fuel did we consume in Q1?
List drivers with incomplete safety training
Which vehicles are due for maintenance?
```

### PMS (Project Management System)

**Key Tables:** project, task, team_member, milestone, timesheet, deliverable  
**Tenant Filter:** company_id  
**Typical Enums:** task_status (open/in-progress/done), priority (low/normal/high), project_status  
**Example Queries:**
```
Show me all open tasks assigned to John
What's the deadline for project XYZ?
Which projects are behind schedule?
How many hours did the team log this month?
```

### HRMS (Human Resource Management System)

**Key Tables:** employee, department, payroll, leave_request, performance_review, attendance  
**Tenant Filter:** company_id  
**Typical Enums:** leave_type (sick/vacation/other), attendance_status (present/absent/half-day), employee_status (active/inactive)  
**Example Queries:**
```
Show me all employees in the Engineering department
How many leave days has John used this year?
List employees with pending performance reviews
What's the average salary by department?
```

### Cargo (Logistics/Shipment Tracking)

**Key Tables:** shipment, warehouse, delivery_agent, route, consignment, expense  
**Tenant Filter:** company_id  
**Typical Enums:** shipment_status (pending/in-transit/delivered), vehicle_type (van/truck/bike), delivery_status (scheduled/completed/failed)  
**Example Queries:**
```
Track shipment #SHP-123456
Show me all pending deliveries for zone Mumbai
Which deliveries failed today?
What's the average delivery time for the last week?
```

---

## Commands Quick Reference

```bash
# Navigate to project
cd /home/deepakrajb/Desktop/ChatBot/NL2SQL Assistant

# Extract enums
python scripts/extract_enums.py \
  --source-dir "/path/to/app/src/main/java" \
  --request-file scripts/{app}_request.json \
  --output-file scripts/extracted_enums.{app}.json \
  --db-url "mysql+pymysql://user:pass@host:3306/DB" \
  --merge

# Generate domain
python scripts/generate_domain.py \
  --config scripts/{app}_request.json \
  --db-url "mysql+pymysql://user:pass@host:3306/DB" \
  --force

# Validate JSON
python -m json.tool domains/{app}/developer_clarifications.json > /dev/null && echo "✓ Valid"

# Start TAG API
python -m uvicorn app.main:app --host 127.0.0.1 --port 8012

# Run diagnostic
python scripts/run_diagnostic.py \
  --url http://localhost:8012 \
  --out diagnostics/results_{app}.jsonl \
  --report diagnostics/report_{app}.txt

# View results
cat diagnostics/report_{app}.txt
```

---

## Post-Onboarding

### If Diagnostic Pass Rate < 50%

1. **Review enum mappings**
   ```bash
   cat domains/{app}/developer_clarifications.json | python -m json.tool | grep -A 10 enum_values
   ```

2. **Check if Phase 0 bugs are blocking** (FM4 tenant filter, FM5 enum interpretation)
   ```bash
   grep "FM4\|FM5" diagnostics/report_{app}.txt | wc -l
   ```

3. **Add more column descriptions**
   - Currently empty? Run a second review pass

4. **Increase test questions**
   - Only 20 questions may not cover all failure modes
   - Add 10 more variant questions

### If Diagnostic Pass Rate ≥ 70%

1. **Document the 30% of questions that still fail**
   ```bash
   cat diagnostics/report_{app}.txt | grep -E "^Q[0-9]+" | grep -v "pass"
   ```

2. **Plan Phase 1 work**
   - Fix identified Phase 0 bugs
   - Add soft-delete filtering
   - Improve enum handling

3. **Prepare for production**
   - Coordinate with app owner for real user testing
   - Load real company data (not test data)
   - Monitor performance (query latency, token usage)

---

## FAQ

**Q: Can I onboard multiple apps in parallel?**  
A: Yes. Each app has its own request file, domain folder, and test questions.

**Q: What if the app uses Hibernate @Entity instead of @Enum?**  
A: Extract_enums.py won't find them. Either refactor to use public enum, or manually populate enum_values in Step 4.

**Q: How do I handle multi-tenant apps with no company_id column?**  
A: Set `tenant_id_column: null` in apps.local.yaml. Ensure other tenant isolation exists (e.g., API-level filtering).

**Q: Can I update enums after onboarding?**  
A: Yes. Re-run extract_enums.py with --merge, then regenerate domain, then update apps.local.yaml if schemas changed.

**Q: What if the database has 200+ tables?**  
A: Use `include_tables` to select only 8-15 business-critical tables. Ignore logging, audit, internal tables.

**Q: How often should I regenerate the domain?**  
A: Every time the database schema changes significantly. For ongoing tweaks (enum values, descriptions), edit developer_clarifications.json directly.

---

## Support

**Issues during onboarding?**
- Check [Troubleshooting](#troubleshooting) section
- Review docs/product/tag-assistant/chatbot-system.md for architecture details
- Contact: karthik.c@kritilabs.com

**Onboarding checklist template:**
```markdown
- [ ] Request file created (generate_domain.request.json)
- [ ] Database connectivity verified
- [ ] Enum extraction done (check matched/needs_review/no_match)
- [ ] Domain generated (19 files)
- [ ] Manual review complete (enums, descriptions, workflows)
- [ ] App registered (apps.local.yaml)
- [ ] Test questions added (20 questions to scripts/vts_diagnostic_questions.json)
- [ ] Diagnostic run successful (pass rate ≥ 50%)
- [ ] Known issues documented
- [ ] Ready for production (pass rate ≥ 70%)
```

---

**Last Updated:** 2026-04-19  
**Next Review:** After first non-VTS app onboarding (FMS)
