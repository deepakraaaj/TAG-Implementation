# Demo Questions — VTS & FITS

Prompts grouped by domain and intent. Each group lists read-only lookups first (safe for IOCL), then guided CRUD flows, then harder/edge cases to stress the bot.

---

## VTS (Vehicle Tracking System)

Domain: `vts` — trips, vehicles, routes, locations, drivers, alerts, device state.

### A. Read-only lookups (GPS / live state)
1. Where is vehicle `TN01AB1234` right now?
2. What was the last GPS/VTS update time for vehicle `TN01AB1234`?
3. Show the current status of trip `Fuel Run 15`.
4. Which vehicles are currently en route?
5. Which vehicles have not reported a GPS ping in the last 2 hours?

### B. Trip & route reporting
6. Show all active trips for company `IOCL` today.
7. List trips scheduled for tomorrow by route code.
8. Count trips created this week, grouped by status.
9. Show the last 10 trips for vehicle `TN01AB1234`.
10. Which routes had the most trips last month?
11. Show trips that reached destination but have no invoice received yet.

### C. Alerts & exceptions
12. Show overspeed alerts today.
13. List geofence breach alerts for the last 24 hours.
14. Which drivers had the most harsh-braking events this week?
15. Show idle-time exceptions for vehicles at terminal locations.

### D. Guided CRUD flows
16. Create a trip.  *(runs `create_trip.yaml` — asks name, vehicle, location, route, scheduled date)*
17. Create a trip named `Fuel Run 27` for vehicle `TN01AB1234` at terminal VTS location, route `RT-09`, scheduled `2026-04-22`.
18. Update trip status.  *(runs `update_trip_status.yaml`)*
19. Mark trip `Fuel Run 15` as **Reached**.
20. Mark trip `Fuel Run 12` as **Invoice Received**.
21. Cancel trip `Fuel Run 08`.

### E. Clarification / edge cases
22. Update the trip. *(should ask which trip)*
23. Create a trip for vehicle `XYZ9999`. *(vehicle not in lookup — should fail gracefully)*
24. Mark trip `Fuel Run 15` as **Delivered**. *(status not in allowed menu — should refuse/clarify)*
25. Where is the truck? *(ambiguous — should ask for vehicle number)*

---

## FITS (Facility Maintenance)

Domain: `fits_dev_march_9` — task_transaction, scheduler, check_list_transaction, asset, facility.

### A. Task inbox / queues
1. Show me all pending maintenance tasks.
2. How many tasks are overdue?
3. List critical-priority tasks for today.
4. Show tasks assigned to me.
5. Show tasks due this week by facility.
6. Which tasks have been in `In Progress` for more than 3 days?

### B. Facility & asset reporting
7. Which facility has the most open tasks?
8. What's the workload distribution across facilities?
9. Show completed tasks from last week.
10. List assets with the most maintenance history.
11. Show assets at facility `Plant-01` that have no scheduled maintenance.
12. Count checklists completed this month, grouped by facility.

### C. Compliance & checklist status
13. Show checklist compliance status for this week.
14. Which tasks have incomplete checklists past their scheduled date?
15. Generate a priority breakdown report.
16. Show closed tasks missing `closed_by` or remarks.

### D. Guided CRUD flows
17. Create a maintenance task. *(runs `create_task.yaml`)*
18. Create a daily inspection task for facility `1`, task template `5`, priority **High**, scheduled `2026-04-22`.
19. Assign task `TASK_ABC123` to user `3`. *(runs `assign_task.yaml`)*
20. Reassign task `TASK_XYZ789` to user `2` — reason: original assignee unavailable.
21. Update task status. *(runs `update_task_status.yaml`)*
22. Mark task `TASK_ABC123` as **Completed**, closed by user `5`, remarks "all checks passed".
23. Move task `TASK_XYZ789` to **In Progress**.
24. Create a weekly maintenance schedule. *(runs `create_schedule.yaml`)*
25. Create a schedule `Weekly Facility Maintenance` for facility `2`, template `10`, frequency weekly, start `2026-04-21 10:00`, assigned user `4`.
26. Update checklist items for task `TASK_ABC123` — all three items completed, inspection date `2026-04-19 14:30`.

### E. Clarification / edge cases
27. Create a task scheduled for `2026-01-01`. *(past date — should be rejected)*
28. Assign task `TASK_ABC123` to user `99`. *(inactive/nonexistent user — should fail)*
29. Reassign task `TASK_DONE01`. *(task already completed — should refuse)*
30. Mark task as done. *(ambiguous — should ask which task and require `closed_by`)*
31. Create schedule `Daily Equipment Inspection` for facility `1`. *(duplicate name — should reject)*

---

## How to use

- **Smoke test**: run questions 1–5 from each domain to confirm read path works.
- **Flow test**: run the guided CRUD items (VTS D, FITS D) — each should walk through prompts, confirm, then write.
- **Robustness test**: run the edge cases (VTS E, FITS E) — bot should clarify or abstain instead of inventing data.
- **IOCL demo**: stick to sections A–C only (read-only). Skip D and flag E as "governed by approval workflow".
