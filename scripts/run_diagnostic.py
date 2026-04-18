#!/usr/bin/env python3
"""
TAG Diagnostic Test Runner
20-question VTS domain assessment against POST /chat?stream=false

Usage:
    python3 scripts/run_diagnostic.py [--url http://localhost:8012] [--out diagnostics/results.jsonl] [--report diagnostics/diagnostic_report.txt]
"""

import argparse
import base64
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

# ── Configuration ──────────────────────────────────────────────────────────────
DEFAULT_URL = "http://localhost:8012"
DOMAIN = "vts"
COMPANY_ID = 56942673
APP_ID = "vts"
QUESTIONS_FILE = Path(__file__).parent / "vts_diagnostic_questions.json"
DEFAULT_OUT = "diagnostics/results.jsonl"
DEFAULT_REPORT = "diagnostics/diagnostic_report.txt"

# Failure mode labels
FM = {
    "FM1": "Wrong table selected",
    "FM2": "Right table, wrong/missing columns",
    "FM3": "Wrong joins (duplicates, missing rows, cartesian)",
    "FM4": "Missing implicit filters (soft-delete, tenant, test data)",
    "FM5": "Wrong enum interpretation (int vs label string)",
    "FM6": "Wrong value interpretation (units, timezones, formats)",
    "FM7": "Missing business-glossary term misread",
    "FM8": "Metadata has it but LLM ignored it",
    "pass": "Pass",
    "no_sql": "No SQL generated (non-SQL response)",
    "api_error": "API call failed",
}


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _make_user_context() -> str:
    payload = {"company_id": COMPANY_ID, "domain_name": DOMAIN}
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _post_chat(base_url: str, message: str, session_id: str, timeout: int = 60) -> dict:
    url = f"{base_url}/chat?stream=false"
    body = json.dumps({
        "session_id": session_id,
        "message": message,
        "user_id": "diagnostic_runner",
        "metadata": {
            "domain_name": DOMAIN,
            "company_id": COMPANY_ID,
            "app_id": APP_ID,
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-user-context": _make_user_context(),
            "x-app-id": APP_ID,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:500]}
    except Exception as e:
        return {"_exception": str(e)}


# ── SQL extraction ─────────────────────────────────────────────────────────────

def _extract_sql(response: dict) -> Optional[str]:
    """Pull the SQL string out of the API response, wherever it lives."""
    # Primary path: response.sql.query
    sql_obj = response.get("sql") or {}
    if isinstance(sql_obj, dict):
        q = sql_obj.get("query")
        if q:
            return str(q)

    # Fallback: scan the message text for a SQL block
    msg = response.get("message", "")
    code_block = re.search(r"```sql\s*(.*?)```", msg, re.DOTALL | re.IGNORECASE)
    if code_block:
        return code_block.group(1).strip()

    # Last resort: bare SELECT in message
    select_match = re.search(r"(SELECT\s+.+?;?)\s*$", msg, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()

    return None


# ── Classification engine ──────────────────────────────────────────────────────

def _match(pattern: str, sql: str) -> bool:
    """Case-insensitive regex match against the SQL string."""
    try:
        return bool(re.search(pattern, sql, re.IGNORECASE | re.DOTALL))
    except re.error:
        return pattern.lower() in sql.lower()


def _classify(q: dict, sql: Optional[str], response: dict) -> tuple[str, str, list[str]]:
    """
    Returns (mode_id, explanation, issues_list).
    mode_id is one of FM1..FM8, 'pass', 'no_sql', 'api_error'.
    """
    issues = []

    # API-level failure
    if "_exception" in response or "_http_error" in response:
        return "api_error", f"API call failed: {response}", []

    # No SQL at all
    if sql is None:
        # Check if it's a legitimate clarification / non-data response
        msg = response.get("message", "").lower()
        if any(kw in msg for kw in ("i need", "please specify", "could you clarify", "which entity")):
            return "no_sql", "Model asked for clarification instead of generating SQL", []
        return "no_sql", "No SQL in response; model may have answered in prose", []

    sql_normalized = sql.upper()

    # ── Check forbidden patterns first (high-signal failures) ─────────────────
    for fp in q.get("forbidden_patterns", []):
        if _match(fp, sql):
            mode = q.get("failure_mode_if_forbidden") or "FM5"
            issues.append(f"Forbidden pattern found: {fp}")
            return mode, "; ".join(issues), issues

    # ── Check required table(s) ────────────────────────────────────────────────
    expected_tables = q.get("expected_tables", [])
    missing_tables = [t for t in expected_tables if not _match(r'\b' + re.escape(t) + r'\b', sql)]
    if missing_tables:
        mode = q.get("failure_mode_if_no_required", "FM1")
        # Distinguish FM1 (completely wrong table) vs FM3 (right table, missing join partner)
        if len(missing_tables) == len(expected_tables):
            mode = "FM1"
            issues.append(f"Wrong table: expected one of {expected_tables}, got nothing matching")
        else:
            mode = "FM3"
            issues.append(f"Missing join partner table(s): {missing_tables}")
        return mode, "; ".join(issues), issues

    # ── Check required content patterns ───────────────────────────────────────
    for rp in q.get("required_patterns", []):
        if not _match(rp, sql):
            mode = q.get("failure_mode_if_no_required", "FM2")
            issues.append(f"Missing required pattern: {rp}")

    # ── Check required filter patterns ────────────────────────────────────────
    for fp in q.get("required_filter_patterns", []):
        if not _match(fp, sql):
            mode_hint = q.get("failure_mode_if_no_required", "FM4")
            issues.append(f"Missing required filter pattern: {fp}")
            # Distinguish tenant filter (FM4) vs enum (FM5) vs date (FM6)
            if re.search(r"company_id", fp, re.IGNORECASE):
                mode_hint = "FM4"
            elif re.search(r"CURDATE|NOW|CURRENT_DATE|today", fp, re.IGNORECASE):
                mode_hint = "FM6"
            elif re.search(r"\d{2,}", fp):
                mode_hint = "FM5"
            # Override mode hint from question if available
            if not issues or mode_hint:
                pass  # keep accumulating

    if issues:
        # Determine dominant failure mode from issues list
        mode = q.get("failure_mode_if_no_required", "FM2")
        # Tenant filter missing → FM4
        if any("company_id" in i for i in issues):
            mode = "FM4"
        # Dynamic date missing → FM6
        elif any("CURDATE" in i or "NOW" in i or "CURRENT_DATE" in i or "today" in i for i in issues):
            mode = "FM6"
        # Enum integer missing → FM5
        elif any(re.search(r"Missing required filter pattern: \d+", i) for i in issues):
            mode = "FM5"
        return mode, "; ".join(issues), issues

    # ── Extra heuristic checks ─────────────────────────────────────────────────
    extra_issues = []

    # FM3: Cartesian join detector — multiple FROM tables with no ON/WHERE join condition
    from_tables = re.findall(r'FROM\s+(\w+)', sql_normalized)
    join_tables = re.findall(r'JOIN\s+(\w+)', sql_normalized)
    all_tables = from_tables + join_tables
    has_join_condition = bool(re.search(r'\bON\b|\bUSING\b', sql_normalized))
    if len(all_tables) >= 2 and not has_join_condition and "WHERE" not in sql_normalized:
        extra_issues.append("Possible cartesian join: multiple tables with no ON/USING/WHERE")

    # FM4: Soft-delete leak — is_active column not filtered when table has it
    tables_with_is_active = {"vehicle", "trip", "vts_exception", "route", "location"}
    queried_tables = {t.lower() for t in all_tables}
    if queried_tables & tables_with_is_active:
        if not _match(r'is_active', sql):
            extra_issues.append("Soft-delete not filtered: is_active not in WHERE clause")

    if extra_issues:
        mode = "FM4" if any("Soft-delete" in i for i in extra_issues) else "FM3"
        return mode, "; ".join(extra_issues), extra_issues

    return "pass", "SQL matches all expected patterns", []


# ── Report generation ──────────────────────────────────────────────────────────

def _generate_report(results: list[dict]) -> str:
    total = len(results)
    passed = sum(1 for r in results if r["mode"] == "pass")
    pass_rate = passed / total * 100 if total else 0

    # Count by mode
    mode_counts: dict[str, int] = {}
    for r in results:
        mode_counts[r["mode"]] = mode_counts.get(r["mode"], 0) + 1

    # Top 3 failure modes (excluding pass, no_sql, api_error)
    failure_modes = {k: v for k, v in mode_counts.items() if k not in ("pass",)}
    top3 = sorted(failure_modes.items(), key=lambda x: -x[1])[:3]

    lines = [
        "=" * 70,
        "TAG DIAGNOSTIC REPORT — VTS Domain",
        "=" * 70,
        f"Total questions : {total}",
        f"Passed          : {passed}",
        f"Failed          : {total - passed}",
        f"Pass rate       : {pass_rate:.1f}%",
        "",
        "── Failure Mode Distribution ─────────────────────────────────────────",
    ]
    for mode, count in sorted(mode_counts.items()):
        label = FM.get(mode, mode)
        bar = "█" * count
        lines.append(f"  {mode:10s} {count:3d}  {bar}  {label}")

    lines += ["", "── Top 3 Failure Modes with Examples ────────────────────────────────"]
    for rank, (mode, count) in enumerate(top3, 1):
        label = FM.get(mode, mode)
        lines.append(f"\n  #{rank}  {mode} — {label}  (×{count})")
        # Find up to 2 examples
        examples = [r for r in results if r["mode"] == mode][:2]
        for ex in examples:
            lines.append('       Q{:02d}: "{}"'.format(ex['id'], ex['question']))
            lines.append("             Issue  : {}".format(ex['explanation']))
            sql_snip = (ex.get("sql") or "")[:120].replace("\n", " ")
            if sql_snip:
                suffix = "..." if len(ex.get("sql", "")) > 120 else ""
                lines.append("             SQL    : {}{}".format(sql_snip, suffix))

    lines += ["", "── Recommended Metadata-Layer Fixes ─────────────────────────────────"]

    fix_map = {
        "FM1": (
            "FM1 — Wrong table selected",
            [
                "Strengthen semantics.json mappings (e.g. 'current position'→vts_transaction).",
                "Add negative examples in few_shot_examples.json to show the wrong table being rejected.",
                "Add explicit primary_table hint in entity_behavior.json for ambiguous terms.",
            ]
        ),
        "FM2": (
            "FM2 — Right table, wrong/missing columns",
            [
                "Add required_columns hints to entity_behavior.json for each entity.",
                "In few_shot_examples.json, include the exact column names expected in the SQL SELECT list.",
                "Annotate columns with business_meaning in enum_dictionary.json so the LLM knows which to surface.",
            ]
        ),
        "FM3": (
            "FM3 — Wrong / missing joins",
            [
                "Ensure all join_hints in semantics.json are exhaustive (add missing FK pairs).",
                "Add join-path few_shot_examples for each multi-table query in few_shot_examples.json.",
                "Consider adding a relationship_map.json entry with join type (INNER/LEFT) and cardinality.",
            ]
        ),
        "FM4": (
            "FM4 — Missing implicit filters (tenant / soft-delete)",
            [
                "Add implicit_filters to sql_builder.json: company_id = {company_id} for every tenant-scoped table.",
                "Add is_active = 1 as a default_filter in entity_behavior.json for tables with soft-delete.",
                "Document these invariants in domain_knowledge.json data_model_notes so the LLM sees them in every prompt.",
            ]
        ),
        "FM5": (
            "FM5 — Wrong enum interpretation",
            [
                "Extend enums.py ENUM_MAPPINGS to cover all status columns (verify integer values against DB).",
                "Cross-check enum integer values in enums.py against actual sample_values in columns.json — they differ for recent_state_id (metadata says 10/20/30, DB shows 1/2/3).",
                "Add enum examples in few_shot_examples.json: show WHERE recent_state_id = 30, not = 'En Route'.",
            ]
        ),
        "FM6": (
            "FM6 — Wrong value interpretation (dates / units)",
            [
                "Add date_filter_keys and date_functions to sql_builder.json (e.g. CURDATE() for 'today').",
                "Add a few_shot example: 'trips scheduled today' → WHERE scheduled_date = CURDATE().",
                "Document timezone assumptions in domain_knowledge.json data_model_notes.",
            ]
        ),
        "FM7": (
            "FM7 — Business-glossary term misread",
            [
                "Expand glossary.json with missing terms: 'overdue', 'active trip', 'journey', 'shipment'.",
                "Map each business term to a precise SQL predicate in semantics.json column_logic.",
                "Add few_shot_examples for each glossary term showing the resolved SQL.",
            ]
        ),
        "FM8": (
            "FM8 — Metadata present but LLM ignored it",
            [
                "Promote join_hints and column_logic to the system prompt preamble so they appear before user query.",
                "Add 'must-use' annotations in semantics.json so the orchestration layer enforces hint injection.",
                "If hints are already injected, check that they are not truncated by token budget — reduce other context sections.",
            ]
        ),
    }

    active_modes = {r["mode"] for r in results if r["mode"] not in ("pass", "no_sql", "api_error")}
    for mode in sorted(active_modes):
        if mode in fix_map:
            title, fixes = fix_map[mode]
            lines.append(f"\n  {title}")
            for fix in fixes:
                lines.append(f"    • {fix}")

    lines += ["", "=" * 70]
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TAG VTS diagnostic runner")
    parser.add_argument("--url", default=DEFAULT_URL, help="TAG base URL")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output JSONL path")
    parser.add_argument("--report", default=DEFAULT_REPORT, help="Report output path")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_FILE.read_text())
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"TAG Diagnostic — {len(questions)} questions → {args.url}")
    print(f"Output: {out_path}")
    print("-" * 60)

    for q in questions:
        qid = q["id"]
        session_id = f"diag_{qid}_{uuid.uuid4().hex[:8]}"
        print(f"  Q{qid:02d} [{q['category']:18s}] {q['question'][:55]}…", end=" ", flush=True)

        t0 = time.time()
        response = _post_chat(args.url, q["question"], session_id)
        elapsed_ms = round((time.time() - t0) * 1000)

        sql = _extract_sql(response)
        mode, explanation, issues = _classify(q, sql, response)

        status_icon = "✓" if mode == "pass" else ("⚠" if mode in ("no_sql", "api_error") else "✗")
        print(f"{status_icon} {mode}  ({elapsed_ms}ms)")

        record = {
            "id": qid,
            "category": q["category"],
            "question": q["question"],
            "probe": q.get("probe", ""),
            "mode": mode,
            "mode_label": FM.get(mode, mode),
            "explanation": explanation,
            "issues": issues,
            "sql": sql,
            "response_message": response.get("message", "")[:300],
            "elapsed_ms": elapsed_ms,
            "session_id": session_id,
            "app_id": response.get("app_id", ""),
            "llm_model": response.get("llm_model", ""),
        }
        results.append(record)

        with out_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

        if qid < len(questions):
            time.sleep(args.delay)

    print("-" * 60)
    passed = sum(1 for r in results if r["mode"] == "pass")
    print(f"Done. {passed}/{len(results)} passed  ({passed/len(results)*100:.1f}%)")

    report_text = _generate_report(results)
    report_path.write_text(report_text)
    print(f"\nReport written to: {report_path}")
    print()
    print(report_text)


if __name__ == "__main__":
    main()
