# REMP Chatbot — What You Can Ask

REMP is a **facility-maintenance** assistant. It answers questions over 48
tables (tasks, assets, facilities, users, schedulers, checklists, maintenance)
and — when the write flag is on — performs guarded create/update/delete.

> **Write flag:** create/update/delete only work when
> `ENABLE_TASK_STATUS_WRITE=true`. Default is **off** (read-only). Delete also
> requires an explicit confirmation. See the bottom of this doc.

---

## 1. READ — ask anything about your data (always available)

### Tasks (`task_transaction`, 7k+ rows)
- "Show me all pending maintenance tasks"
- "How many tasks are overdue?"
- "List high priority tasks for today"
- "Show completed tasks from last week"
- "Which tasks are assigned to me?"
- "Task status summary" / "tasks grouped by status"
- "How many tasks are in progress?"

### Assets (`asset`, `asset_category`)
- "How many assets does facility ABC have?"
- "List all assets in facility 1"
- "Show assets by category"
- "Which assets need maintenance?"

### Facilities (`facility`, `facility_types`, `facility_user_mapping`)
- "Which facilities have the most open tasks?"
- "List all facilities"
- "How many facilities are there?"
- "Show facilities without any tasks today"

### Users (`user`, `facility_user_mapping`)
- "Which users are assigned to facility 1?"
- "How many active users?"
- "List supervisors"

### Schedulers & checklists (`scheduler`, `check_list_master`, `check_list_transaction`)
- "Show all maintenance schedules"
- "List pending check lists"
- "Which schedules are recurring?"
- "Show checklist completion status"

### Maintenance (`maintenance_transaction`, `maintenance_types`)
- "Show recent maintenance records"
- "How many maintenance jobs this month?"

**Read mechanics:** filtered queries are required (a WHERE clause), results are
capped at `SQL_MAX_LIMIT` (1000), tenant scoping by `company_id` is automatic,
and sensitive tables (`api_key`, `*password*`, tokens) are blocked.

---

## 2. CREATE — add records (flag on)

### New task — `create task` / `new task`
- "Create a daily inspection task for facility 1 on 2026-04-20, priority 2"
- Captures: `task_description_id`, `scheduled_date`, `facility_id`, `priority`, `remarks`

### New schedule — `create schedule` / `schedule maintenance`
- "Create a recurring weekly facility maintenance schedule"
- "Set recurring task: Daily Equipment Inspection"

---

## 3. UPDATE — change records (flag on)

### Task status — `update task status` / `mark task` / `complete task` / `close task`
- "Mark task TASK_ABC123 as completed"
- "Move task TASK_XYZ789 to in progress"
- "update task_transaction id=321, status=Completed"

### Assign / reassign — `assign task` / `reassign task` / `give task`
- "Assign task TASK_ABC123 to user 3"
- "Reassign overdue task TASK_XYZ789 to user 2"

### Checklist — `complete checklist` / `update checklist` / `mark checklist done`
- "Complete the compliance checklist for task ABC"

**Update mechanics:** an UPDATE without a WHERE is rejected; the task-status
fast-path only allows the `status` (and `updated_by`) columns on the task table.

---

## 4. DELETE — remove one record (flag on + confirmation)

Delete is **guarded**: one specific row, identified by `id`, and you must confirm.

- Step 1 — "delete task id=321"
  → bot replies: *"⚠️ This will permanently delete task #321. This cannot be
  undone. To proceed, resend: delete task_transaction id=321 confirm"*
- Step 2 — "delete task_transaction id=321 confirm"
  → bot replies: *"✓ Deleted 1 task."*

Blocked by design (will refuse): bulk deletes ("delete all tasks"), deletes
without an id, `DROP`/`TRUNCATE`, deleting the database.

---

## 5. Graceful replies

Every outcome gets a specific answer — never a blank or "no response":
- Success: "✓ Updated 1 task", "✓ Created 2 schedules", "✓ Deleted 1 task"
- Nothing matched: "No matching task was found to update, so nothing was changed"
- Not allowed / unclear: a specific reason + how to rephrase

---

## Enabling writes for a demo

```
# in .env
ENABLE_TASK_STATUS_WRITE=true   # default false = strictly read-only
```
Flip to `true`, restart, demo; set back to `false` to instantly revert. Every
write is recorded in the NL2SQL audit log (full SQL), so changes are traceable.
