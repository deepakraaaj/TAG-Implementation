-- =============================================================================
-- FITS Transaction Seed Data — Real IDs from 192.168.15.112/fits_dev_march_9
-- Date: 2026-04-20 (today)
-- company_id  : 56942686  (Kritilabs REMP)
-- Facilities  : 218 FLI_Third Floor_Ladies_Restroom
--               317 SM_G floor_Pantry
--               318 SM_G floor_Gents Rest Room
--               333 SM_Second Floor_Ladies Rest room
--               340 SM_Second Floor_Work Area
--               346 Ele unit_G Floor_Outer Area
-- Assets      : 37 Water Dispenser | 38 Workstation | 40 Milk Boiler
--               74 Machine_6 | 75 Machine_2 | 76 Machine_3
-- Task descs  : 58 Rest room Cleaning | 64 Tea & Coffee Prep
--               68 Work Area-Cleaning | 69 Outer Area Cleaning | 36 Remove garbage
-- Sched tasks : 397-400 Remove Garbage | 401-402 Daily Tea | 403-404 Dailly Maintenance
-- Checklist   : 16 Sweep | 17 Mop | 18 Wiping | 19 Refill Water
--               59 Toilet cleaning | 60 Mirror cleaning | 61 Washbasin cleaning
--               62 Working desk cleaning | 63 work area sweeping
-- Tag IDs     : E2004707CB60602367910111 (asset 76)
--               E20047027F70602312D2010A (asset 75)
--               E2004711B970602306720113 (asset 74)
-- ID ranges   : scheduler_task_log 1–5  (table was empty)
--               task_transaction    7198–7203
--               check_list_txn      11330–11347
--               maintenance_txn     19–24
--               tag_scan_log        42–51
-- User        : 11784578  (existing active user)
-- =============================================================================


-- ============================================================
-- TABLE: scheduler_task_log  (5 rows — table was empty, starting from 1)
-- ============================================================
INSERT INTO scheduler_task_log (
    id, name,
    scheduled_date_time, actual_date_time,
    date_created, date_updated,
    created_by, updated_by,
    company_id, scheduler_task_id,
    task_description, is_open
) VALUES

(1, 'Restroom Cleaning — Ladies (Third Floor)',
 '2026-04-20 08:00:00', '2026-04-20 08:15:00',
 '2026-04-20 07:45:00', '2026-04-20 08:15:00',
 '11784578', '11784578',
 56942686, 403,
 'Sweep, mop, toilet clean, mirror wipe — FLI Third Floor Ladies Restroom', 0),

(2, 'Pantry Setup & Tea Preparation',
 '2026-04-20 08:30:00', '2026-04-20 08:50:00',
 '2026-04-20 07:45:00', '2026-04-20 08:50:00',
 '11784578', '11784578',
 56942686, 401,
 'Refill water dispenser, prepare tea and snacks, clean pantry area', 0),

(3, 'Work Area Morning Clean',
 '2026-04-20 09:00:00', '2026-04-20 09:25:00',
 '2026-04-20 07:45:00', '2026-04-20 09:25:00',
 '11784578', '11784578',
 56942686, 404,
 'Sweep and mop work area, wipe desks, clean workstations', 0),

(4, 'Garbage Removal — Ground Floor',
 '2026-04-20 09:30:00', '2026-04-20 09:45:00',
 '2026-04-20 07:45:00', '2026-04-20 09:45:00',
 '11784578', '11784578',
 56942686, 397,
 'Collect and remove garbage from all bins on ground floor', 0),

(5, 'Gents Restroom Cleaning — G Floor',
 '2026-04-20 10:00:00', NULL,
 '2026-04-20 07:45:00', '2026-04-20 10:00:00',
 '11784578', NULL,
 56942686, 398,
 'Sweep, mop, toilet clean, mirror wipe — SM G Floor Gents Restroom', 1);


-- ============================================================
-- TABLE: task_transaction  (6 rows)
-- status: 0=open/pending, 1=in-progress, 2=completed
-- assigned_user_id: real user IDs from company 56942686
-- ============================================================
INSERT INTO task_transaction (
    id, company_id, asset_id, facility_id, location_level_id,
    task_description_id, schedule_id, scheduler_task_details_id,
    scheduled_date, adhoc,
    status, assigned_user_id, is_active,
    date_created, created_by
) VALUES
-- schedule_id=NULL (no scheduler linked for this company)
-- scheduler_task_details_id=NULL (no scheduler_task_details records exist)

-- Restroom cleaning — completed
(7198, 56942686, NULL, 218,  NULL, 58, NULL, NULL, '2026-04-20 08:00:00', 0,
 2, 11784841, 1, '2026-04-20 07:45:00', 11784578),

(7199, 56942686, NULL, 333,  NULL, 58, NULL, NULL, '2026-04-20 08:00:00', 0,
 2, 11784788, 1, '2026-04-20 07:45:00', 11784578),

-- Pantry / tea prep — completed
(7200, 56942686, 37,   317,  NULL, 64, NULL, NULL, '2026-04-20 08:30:00', 0,
 2, 11784703, 1, '2026-04-20 07:45:00', 11784578),

-- Work area cleaning — completed
(7201, 56942686, NULL, 340,  NULL, 68, NULL, NULL, '2026-04-20 09:00:00', 0,
 2, 11784840, 1, '2026-04-20 07:45:00', 11784578),

-- Outer area / garbage — completed
(7202, 56942686, NULL, 346,  NULL, 69, NULL, NULL, '2026-04-20 09:30:00', 0,
 2, 11784847, 1, '2026-04-20 07:45:00', 11784578),

-- Gents restroom — in progress
(7203, 56942686, NULL, 318,  NULL, 58, NULL, NULL, '2026-04-20 10:00:00', 0,
 1, 11784841, 1, '2026-04-20 07:45:00', 11784578);


-- ============================================================
-- TABLE: check_list_transaction  (18 rows)
-- ============================================================
INSERT INTO check_list_transaction (
    id, company_id, task_transaction_id, check_list_master_id,
    status, is_active,
    date_created, date_updated,
    created_by, updated_by, remarks
) VALUES

-- Task 7198: FLI Ladies Restroom — all done
(11330, 56942686, 7198, 59, 1, 1, '2026-04-20 08:15:00', '2026-04-20 08:15:00', '11784578', '11784578', 'Toilets cleaned and sanitized'),
(11331, 56942686, 7198, 60, 1, 1, '2026-04-20 08:16:00', '2026-04-20 08:16:00', '11784578', '11784578', 'Mirrors wiped, no streaks'),
(11332, 56942686, 7198, 61, 1, 1, '2026-04-20 08:17:00', '2026-04-20 08:17:00', '11784578', '11784578', 'Washbasins cleaned'),
(11333, 56942686, 7198, 16, 1, 1, '2026-04-20 08:18:00', '2026-04-20 08:18:00', '11784578', '11784578', 'Floor swept'),

-- Task 7199: SM Second Floor Ladies Restroom — all done
(11334, 56942686, 7199, 59, 1, 1, '2026-04-20 08:20:00', '2026-04-20 08:20:00', '11784578', '11784578', 'Cleaned and sanitized'),
(11335, 56942686, 7199, 60, 1, 1, '2026-04-20 08:21:00', '2026-04-20 08:21:00', '11784578', '11784578', 'Mirror cleaned'),
(11336, 56942686, 7199, 17, 0, 1, '2026-04-20 08:22:00', '2026-04-20 08:22:00', '11784578', '11784578', 'Mopping pending — wet floor sign placed'),

-- Task 7200: Pantry — water refill failed (dispenser empty)
(11337, 56942686, 7200, 19, 0, 1, '2026-04-20 08:50:00', '2026-04-20 08:50:00', '11784578', '11784578', 'Water can empty — requisition raised for refill'),
(11338, 56942686, 7200, 20, 1, 1, '2026-04-20 08:51:00', '2026-04-20 08:51:00', '11784578', '11784578', 'Tea and snacks distributed to all floors'),
(11339, 56942686, 7200, 18, 1, 1, '2026-04-20 08:52:00', '2026-04-20 08:52:00', '11784578', '11784578', 'Pantry counter wiped clean'),

-- Task 7201: Work Area Second Floor — done
(11340, 56942686, 7201, 63, 1, 1, '2026-04-20 09:25:00', '2026-04-20 09:25:00', '11784578', '11784578', 'Work area swept'),
(11341, 56942686, 7201, 17, 1, 1, '2026-04-20 09:26:00', '2026-04-20 09:26:00', '11784578', '11784578', 'Mopped — floor dry'),
(11342, 56942686, 7201, 62, 1, 1, '2026-04-20 09:27:00', '2026-04-20 09:27:00', '11784578', '11784578', 'All desks wiped'),

-- Task 7202: Outer Area garbage removal — done
(11343, 56942686, 7202, 16, 1, 1, '2026-04-20 09:45:00', '2026-04-20 09:45:00', '11784578', '11784578', 'Outer area swept'),
(11344, 56942686, 7202, 18, 1, 1, '2026-04-20 09:46:00', '2026-04-20 09:46:00', '11784578', '11784578', 'Surface wiped'),

-- Task 7203: Gents Restroom — in progress (2 pending)
(11345, 56942686, 7203, 59, 0, 1, '2026-04-20 10:05:00', '2026-04-20 10:05:00', '11784578', '11784578', 'In progress'),
(11346, 56942686, 7203, 60, 0, 1, '2026-04-20 10:05:00', '2026-04-20 10:05:00', '11784578', '11784578', 'Pending'),
(11347, 56942686, 7203, 61, 0, 1, '2026-04-20 10:05:00', '2026-04-20 10:05:00', '11784578', '11784578', 'Pending');


-- ============================================================
-- TABLE: maintenance_transaction  (6 rows)
-- ============================================================
INSERT INTO maintenance_transaction (
    id, facility_id, company_id,
    latitude, longitude,
    recording_mode, scheduler_task_id,
    file_path, transaction_type, date_created
) VALUES

(19, 218, 56942686, 13.082700, 80.270700,
 'MANUAL', 403,
 '/fits/2026/04/20/fli_restroom/cleaning_19.jpg', 'INSPECTION',
 '2026-04-20 08:15:00'),

(20, 333, 56942686, 13.082700, 80.270700,
 'SCAN', 403,
 '/fits/2026/04/20/sm_restroom/cleaning_20.jpg', 'INSPECTION',
 '2026-04-20 08:22:00'),

(21, 317, 56942686, 13.082700, 80.270700,
 'MANUAL', 401,
 '/fits/2026/04/20/pantry/tea_prep_21.jpg', 'PREVENTIVE',
 '2026-04-20 08:51:00'),

-- Corrective: water dispenser empty — raised for follow-up
(22, 317, 56942686, 13.082700, 80.270700,
 'MANUAL', 401,
 '/fits/2026/04/20/pantry/water_dispenser_empty_22.jpg', 'CORRECTIVE',
 '2026-04-20 08:52:00'),

(23, 340, 56942686, 13.082700, 80.270700,
 'SCAN', 404,
 '/fits/2026/04/20/work_area/cleaning_23.jpg', 'INSPECTION',
 '2026-04-20 09:27:00'),

(24, 346, 56942686, 13.082700, 80.270700,
 'MANUAL', 397,
 '/fits/2026/04/20/outer_area/garbage_24.jpg', 'INSPECTION',
 '2026-04-20 09:45:00');


-- ============================================================
-- TABLE: tag_scan_log  (10 rows — NFC/RFID scans during rounds)
-- ============================================================
INSERT INTO tag_scan_log (
    id, company_id, location_id, asset_id,
    tag_id, scanned_date, date_created, scanned_by
) VALUES

-- Morning round: technician scans machines before cleaning
(42, 56942686, NULL, 76, 'E2004707CB60602367910111', '2026-04-20 08:00:00', '2026-04-20 08:00:01', '11784578'),
(43, 56942686, NULL, 75, 'E20047027F70602312D2010A', '2026-04-20 08:02:00', '2026-04-20 08:02:01', '11784578'),
(44, 56942686, NULL, 74, 'E2004711B970602306720113', '2026-04-20 08:04:00', '2026-04-20 08:04:01', '11784578'),

-- Scan at pantry — water dispenser check
(45, 56942686, NULL, 37, 'E2004707CB60602367910111', '2026-04-20 08:45:00', '2026-04-20 08:45:01', '11784578'),

-- Work area scan — workstation asset check
(46, 56942686, NULL, 38, 'E20047027F70602312D2010A', '2026-04-20 09:05:00', '2026-04-20 09:05:01', '11784578'),

-- Milk boiler checked at pantry
(47, 56942686, NULL, 40, 'E2004711B970602306720113', '2026-04-20 09:10:00', '2026-04-20 09:10:01', '11784578'),

-- Re-scan of Machine_3 — supervisor verification
(48, 56942686, NULL, 76, 'E2004707CB60602367910111', '2026-04-20 09:30:00', '2026-04-20 09:30:01', '11784578'),

-- End-of-round scan: Machine_2 and Machine_6
(49, 56942686, NULL, 75, 'E20047027F70602312D2010A', '2026-04-20 10:00:00', '2026-04-20 10:00:01', '11784578'),
(50, 56942686, NULL, 74, 'E2004711B970602306720113', '2026-04-20 10:02:00', '2026-04-20 10:02:01', '11784578'),

-- Gents restroom entry scan (in-progress task 7203)
(51, 56942686, NULL, NULL, 'E2004707CB60602367910111', '2026-04-20 10:05:00', '2026-04-20 10:05:01', '11784578');
