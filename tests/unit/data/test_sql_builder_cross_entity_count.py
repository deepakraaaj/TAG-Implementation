"""Tests for cross-entity negation count queries.

Verifies that queries like "how many facilities don't have tasks today"
route to the correct anti-join SQL instead of blindly counting tasks.
"""
import asyncio
from unittest.mock import patch
from typing import Any, Dict, List, Tuple

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


class _FakeCatalog:
    @staticmethod
    def table_names():
        return {"task_transaction", "facility", "user", "asset"}

    @staticmethod
    def important_columns(table):
        if table == "facility":
            return {"id", "name", "code", "company_id", "parent_id", "is_active"}
        if table == "task_transaction":
            return {"scheduled_date", "status", "company_id", "assigned_user_id", "facility_id"}
        return {"id", "name", "company_id"}

    @staticmethod
    def table_meta(table):
        if table == "facility":
            return {
                "important_columns": {"id": {}, "name": {}, "code": {}, "company_id": {}},
                "tenant_scope": {"column": "company_id", "metadata_key": "company_id"},
            }
        if table == "task_transaction":
            return {
                "important_columns": {"scheduled_date": {}, "status": {}, "company_id": {}},
                "tenant_scope": {"column": "company_id", "metadata_key": "company_id"},
            }
        return {}

    @staticmethod
    def aliases(table):
        if table == "task_transaction":
            return ["task", "tasks", "work order", "work orders", "job", "jobs"]
        if table == "facility":
            return ["facility", "facilities", "site", "sites"]
        if table == "user":
            return ["user", "users", "person", "people", "employee", "employees"]
        return []

    @staticmethod
    def get_query_template(table, key):
        templates = {
            "facility": {
                "no_task_transaction_today": (
                    "SELECT COUNT(*) AS facilities_without_tasks_today "
                    "FROM facility f WHERE f.company_id = {company_id} "
                    "AND f.id NOT IN ("
                    "SELECT DISTINCT tt.facility_id FROM task_transaction tt "
                    "WHERE DATE(tt.scheduled_date) = CURDATE());"
                ),
                "no_task_transaction": (
                    "SELECT COUNT(*) AS facilities_without_tasks "
                    "FROM facility f WHERE f.company_id = {company_id} "
                    "AND f.id NOT IN ("
                    "SELECT DISTINCT tt.facility_id FROM task_transaction tt);"
                ),
                "list_no_task_transaction_today": (
                    "SELECT f.id, f.name, f.code "
                    "FROM facility f WHERE f.company_id = {company_id} "
                    "AND f.id NOT IN ("
                    "SELECT DISTINCT tt.facility_id FROM task_transaction tt "
                    "WHERE DATE(tt.scheduled_date) = CURDATE());"
                ),
                "list_no_task_transaction": (
                    "SELECT f.id, f.name, f.code "
                    "FROM facility f WHERE f.company_id = {company_id} "
                    "AND f.id NOT IN ("
                    "SELECT DISTINCT tt.facility_id FROM task_transaction tt);"
                ),
                "count": "SELECT COUNT(*) AS total_facilities FROM facility WHERE company_id = {company_id};",
            },
            "user": {
                "no_task_transaction_today": (
                    "SELECT COUNT(*) AS users_without_tasks_today "
                    "FROM user u WHERE u.company_id = {company_id} "
                    "AND u.id NOT IN ("
                    "SELECT DISTINCT tt.assigned_user_id FROM task_transaction tt "
                    "WHERE DATE(tt.scheduled_date) = CURDATE());"
                ),
                "list_no_task_transaction_today": (
                    "SELECT u.id, u.first_name, u.last_name, u.email_id "
                    "FROM user u WHERE u.company_id = {company_id} "
                    "AND u.id NOT IN ("
                    "SELECT DISTINCT tt.assigned_user_id FROM task_transaction tt "
                    "WHERE DATE(tt.scheduled_date) = CURDATE());"
                ),
            },
            "task_transaction": {
                "count": "SELECT COUNT(*) AS total_tasks FROM task_transaction tt JOIN facility f ON tt.facility_id = f.id WHERE f.company_id = {company_id};",
            },
        }
        return (templates.get(table) or {}).get(key)


class _FakeBuilder:
    def __init__(self):
        self.catalog = _FakeCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return "task_transaction"

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    @staticmethod
    def build_count_from_filters(table, filters, company_id):
        if table == "task_transaction":
            return "SELECT COUNT(*) AS total_tasks FROM task_transaction WHERE company_id = 1;", ""
        return "", "unknown table"


class _FakeIntentDetector:
    async def detect_intent(self, _query, _metadata):
        return {"operation": "SELECT", "table": "task_transaction", "filters": []}

    async def detect_intent_with_usage(self, _query, _metadata):
        return {"operation": "SELECT", "table": "task_transaction", "filters": []}, {}


class _ContextAwareIntentDetector:
    def __init__(self):
        self.captured_context_table = ""

    async def detect_intent_with_usage(self, _query, _metadata, context_table=""):
        self.captured_context_table = str(context_table or "")
        return {"operation": "SELECT", "table": "facility", "filters": []}, {}


# ── Domain config stub (read by classmethod helpers) ──────────────────
_ENTITY_BEHAVIOR_CONFIG = {
    "primary_table": "task_transaction",
    "intent_mode": "auto",
    "primary_keywords": ["task", "tasks", "work order", "workorder", "job", "jobs"],
    "primary_filter_keys": ["scheduled_date", "status", "priority", "assigned_user_id"],
    "primary_label": "tasks",
    "date_filter_keys": ["scheduled_date"],
    "status_filter_key": "status",
    "priority_filter_key": "priority",
    "date_phrase_map": {"today": "today", "yesterday": "yesterday"},
    "count_request_patterns": ["\\bcount\\b", "\\bhow many\\b", "\\btotal\\b", "\\bnumber of\\b"],
    "cross_entity_negation": {
        "patterns": [
            "(?P<subject>\\w+)\\s+(?:that\\s+|which\\s+)?(?:don'?t|do\\s*n[o']?t|doesn'?t|haven'?t)\\s+have\\s+(?P<object>\\w+)",
            "(?P<subject>\\w+)\\s+without\\s+(?P<object>\\w+)",
            "(?P<subject>\\w+)\\s+(?:with|having)\\s+no\\s+(?P<object>\\w+)",
        ],
        "list_request_patterns": [
            "\\bwhat\\s+are\\s+they\\b",
            "\\bwho\\s+are\\s+they\\b",
            "\\bshow\\s+them\\b",
            "\\blist\\s+them\\b",
        ],
        "entity_mappings": {
            "facility__task_transaction": {
                "fk_column": "facility_id",
                "date_column": "scheduled_date",
                "template_today": "no_task_transaction_today",
                "template_yesterday": "no_task_transaction_yesterday",
                "template_generic": "no_task_transaction",
            },
            "user__task_transaction": {
                "fk_column": "assigned_user_id",
                "date_column": "scheduled_date",
                "template_today": "no_task_transaction_today",
                "template_yesterday": "no_task_transaction_yesterday",
                "template_generic": "no_task_transaction",
            }
        },
    },
}


def _msg(text: str):
    return type("M", (), {"content": text})()


def _make_node():
    node = SQLBuilderNode()
    node.sql_builder = _FakeBuilder()
    node.intent_detector = _FakeIntentDetector()
    return node


# ── Tests ─────────────────────────────────────────────────────────────


def test_facilities_dont_have_tasks_today():
    """'how many facilities don't have tasks today' should generate an anti-join SQL."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many facilities don't have tasks today")],
            "metadata": {"company_id": "1"},
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "NOT IN" in sql.upper(), f"Expected NOT IN in SQL, got: {sql}"
        assert "facility" in sql.lower(), f"Expected facility table in SQL, got: {sql}"
        assert "CURDATE()" in sql, f"Expected CURDATE() for today filter, got: {sql}"
        # Must NOT be the old broken task count
        assert "total_tasks" not in sql.lower(), f"Should NOT count tasks, got: {sql}"


def test_facilities_without_tasks():
    """'how many facilities without tasks' should use the generic negation template."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many facilities without tasks")],
            "metadata": {"company_id": "1"},
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "NOT IN" in sql.upper(), f"Expected NOT IN in SQL, got: {sql}"
        assert "facility" in sql.lower(), f"Expected facility table, got: {sql}"


def test_facilities_with_no_tasks():
    """'how many facilities with no tasks today' should generate an anti-join SQL."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many facilities with no tasks today")],
            "metadata": {"company_id": "1"},
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "NOT IN" in sql.upper(), f"Expected NOT IN in SQL, got: {sql}"
        assert "CURDATE()" in sql, f"Expected CURDATE() for today filter, got: {sql}"


def test_regular_count_still_works():
    """'how many tasks' should still use the normal task_transaction count path."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many tasks")],
            "metadata": {"company_id": "1"},
            "intent": {"operation": "select", "table": "task_transaction", "filters": {}},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "total_tasks" in sql.lower() or "count(*)" in sql.lower(), (
            f"Expected a task count SQL, got: {sql}"
        )


def test_no_negation_config_skips_detection():
    """Without cross_entity_negation config, detection should be skipped gracefully."""
    node = _make_node()
    empty_config = {"primary_table": "task_transaction", "primary_keywords": ["task"]}
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=empty_config):
        info = node._is_cross_entity_negation_count("how many facilities without tasks")
        assert info == {}, f"Expected empty dict when no config, got: {info}"


def test_users_dont_have_tasks_today():
    """'how many users don't have tasks today' should generate an anti-join SQL on assigned_user_id."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many users don't have tasks today")],
            "metadata": {"company_id": "1"},
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        # The query should use the user negation template
        assert "NOT IN" in sql.upper(), f"Expected NOT IN in SQL, got: {sql}"
        assert "user" in sql.lower(), f"Expected user table in SQL, got: {sql}"
        assert "assigned_user_id" in sql.lower(), f"Expected assigned_user_id in SQL, got: {sql}"
        assert "CURDATE()" in sql, f"Expected CURDATE() for today filter, got: {sql}"
        assert "total_tasks" not in sql.lower(), f"Should NOT count tasks, got: {sql}"


def test_negation_count_emits_pending_select_context():
    """Negation result should persist table + negation context for next-turn list follow-ups."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("how many facilities don't have tasks today")],
            "metadata": {"company_id": "1"},
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        pending = result.get("pending_select") or {}
        assert pending.get("table") == "facility"
        negation = pending.get("negation") or {}
        assert negation.get("subject_table") == "facility"
        assert negation.get("object_table") == "task_transaction"
        assert negation.get("date_scope") == "today"


def test_followup_what_are_they_uses_pending_negation_context():
    """Follow-up list pronoun should reuse prior negation context and use list template SQL."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("what are they")],
            "metadata": {
                "company_id": "1",
                "pending_select_table": "facility",
                "pending_select_negation": {
                    "subject_table": "facility",
                    "object_table": "task_transaction",
                    "date_scope": "today",
                },
            },
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "SELECT f.id, f.name, f.code" in sql, f"Expected list negation template SQL, got: {sql}"
        assert "DATE(tt.scheduled_date) = CURDATE()" in sql, f"Expected 'today' scope in SQL, got: {sql}"


def test_followup_who_are_they_uses_pending_negation_context():
    """`who are they` should be handled as a list follow-up with pending negation context."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("who are they")],
            "metadata": {
                "company_id": "1",
                "pending_select_table": "user",
                "pending_select_negation": {
                    "subject_table": "user",
                    "object_table": "task_transaction",
                    "date_scope": "today",
                },
            },
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "SELECT u.id, u.first_name, u.last_name, u.email_id" in sql, (
            f"Expected user list negation template SQL, got: {sql}"
        )
        assert "DATE(tt.scheduled_date) = CURDATE()" in sql, f"Expected 'today' scope in SQL, got: {sql}"


def test_followup_who_are_they_uses_recent_conversation_when_negation_missing():
    """Follow-up should recover negation context from recent conversation when cache has only pending table."""
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("who are they")],
            "metadata": {
                "company_id": "1",
                "pending_select_table": "user",
                "_recent_conversation": [
                    {"role": "user", "content": "how many users don't have tasks today"},
                    {"role": "assistant", "content": "users without tasks today: 14"},
                ],
            },
            "intent": {},
        }
        result = asyncio.run(node.run(state))
        sql = result.get("sql_query", "")
        assert "SELECT u.id, u.first_name, u.last_name, u.email_id" in sql, (
            f"Expected user list negation template SQL, got: {sql}"
        )
        assert "DATE(tt.scheduled_date) = CURDATE()" in sql, f"Expected 'today' scope in SQL, got: {sql}"


def test_intent_detector_receives_pending_context_table():
    """LLM intent detector should receive pending table context for pronoun follow-ups."""
    node = SQLBuilderNode()
    node.sql_builder = _FakeBuilder()
    detector = _ContextAwareIntentDetector()
    node.intent_detector = detector
    cfg = dict(_ENTITY_BEHAVIOR_CONFIG)
    cfg["intent_mode"] = "llm"
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=cfg):
        state = {
            "messages": [_msg("how many facilities without tasks")],
            "metadata": {"company_id": "1", "pending_select_table": "facility"},
            "intent": {},
        }
        asyncio.run(node.run(state))
        assert detector.captured_context_table == "facility"
