"""
Security audit tests for the SQL guardrail layer (highest-risk area).

Target: app/services/data/sql_validator.py::SQLValidatorService

These tests assert the *security contract* the guardrail must enforce BEFORE a
query reaches the (currently read/write) execution connection in
app/assistant/nodes/sql/sql_execute_node.py.

Tests marked ``xfail`` document real gaps found during the audit. They are NOT
weakened guardrails -- they assert the secure behaviour we expect and fail
because the production code does not yet provide it. When the gap is closed the
test will XPASS and the marker should be removed.
"""

import pytest

from app.services.data.sql_validator import SQLValidatorService


# A representative per-tenant/role table allowlist.
ALLOWED = ["task_transaction", "users", "asset"]


def _validator(**kwargs):
    defaults = dict(allowed_tables=ALLOWED, allow_mutations=False)
    defaults.update(kwargs)
    return SQLValidatorService(**defaults)


# ---------------------------------------------------------------------------
# 1. DDL / DML rejection (must be blocked before execution)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE users",
        "DROP DATABASE tenant_a",
        "ALTER TABLE users ADD COLUMN x INT",
        "CREATE TABLE evil (id INT)",
        "TRUNCATE TABLE users",
        "DELETE FROM users WHERE id = 1",
    ],
)
def test_ddl_and_delete_are_rejected(sql):
    assert _validator().validate_sql(sql) is False


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users (id, name) VALUES (1, 'x')",
        "UPDATE users SET name = 'x' WHERE id = 1",
        "REPLACE INTO users (id) VALUES (1)",
    ],
)
def test_insert_update_rejected_when_mutations_disabled(sql):
    # allow_mutations defaults to False in production AppConfig.
    assert _validator(allow_mutations=False).validate_sql(sql) is False


def test_update_without_where_rejected_even_when_mutations_allowed():
    # An unbounded UPDATE must never be permitted regardless of policy.
    assert _validator(allow_mutations=True).validate_sql(
        "UPDATE users SET name = 'x'"
    ) is False


# ---------------------------------------------------------------------------
# 2. Statement chaining / stacking via ';'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM users WHERE id = 1; DROP TABLE users",
        "SELECT id FROM users WHERE id = 1; DELETE FROM users WHERE 1=1",
        "SELECT id FROM users WHERE id = 1; UPDATE users SET name='x' WHERE id=1",
    ],
)
def test_statement_chaining_is_rejected(sql):
    # sqlglot parses multiple statements as a Block, which is not an allowed
    # top-level type -> rejected. This is the defense-in-depth backstop.
    assert _validator().validate_sql(sql) is False


# ---------------------------------------------------------------------------
# 3. Table allowlist enforcement (out-of-scope tables)
# ---------------------------------------------------------------------------

def test_table_outside_allowlist_is_rejected():
    assert _validator().validate_sql(
        "SELECT * FROM secret_payroll WHERE id = 1"
    ) is False


def test_subquery_to_unlisted_table_is_rejected():
    assert _validator().validate_sql(
        "SELECT id FROM users WHERE id IN (SELECT id FROM secret_payroll)"
    ) is False


def test_join_to_unlisted_table_is_rejected():
    assert _validator().validate_sql(
        "SELECT u.id FROM users u JOIN secret_payroll p ON p.id = u.id WHERE u.id = 1"
    ) is False


def test_system_schema_tables_are_rejected():
    for sql in (
        "SELECT * FROM information_schema.tables WHERE table_schema = 'tag'",
        "SELECT * FROM mysql.user WHERE user = 'root'",
        "SELECT * FROM performance_schema.events_statements_current WHERE 1=1",
    ):
        assert _validator().validate_sql(sql) is False


# ---------------------------------------------------------------------------
# 4. Filtered-read enforcement
# ---------------------------------------------------------------------------

def test_select_without_where_is_rejected():
    assert _validator().validate_sql("SELECT * FROM users") is False


def test_select_with_where_is_accepted():
    assert _validator().validate_sql(
        "SELECT id FROM users WHERE id = 1"
    ) is True


# ---------------------------------------------------------------------------
# 5. KNOWN GAPS (xfail) -- documented, not weakened
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5b. Row-LIMIT enforcement (closed gap) -- enforce_row_limit + fetch cap
# ---------------------------------------------------------------------------

def test_unbounded_select_gets_limit_injected():
    capped = SQLValidatorService.enforce_row_limit(
        "SELECT id, name FROM users WHERE id > 0", 1000
    )
    assert "LIMIT 1000" in capped.upper()


def test_oversized_limit_is_clamped():
    capped = SQLValidatorService.enforce_row_limit(
        "SELECT id FROM users WHERE id > 0 LIMIT 100000", 1000
    )
    assert "LIMIT 1000" in capped.upper()
    assert "100000" not in capped


def test_small_limit_is_preserved():
    sql = "SELECT id FROM users WHERE id > 0 LIMIT 10"
    capped = SQLValidatorService.enforce_row_limit(sql, 1000)
    assert "LIMIT 10" in capped.upper()
    assert "1000" not in capped


def test_scalar_aggregate_is_not_limited():
    sql = "SELECT COUNT(*) FROM users WHERE id > 0"
    capped = SQLValidatorService.enforce_row_limit(sql, 1000)
    assert "LIMIT" not in capped.upper()


# ---------------------------------------------------------------------------
# 5c. Stacked / multi-statement rejection (closed gap)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM users WHERE id = 1; DROP TABLE users",
        "SELECT id FROM users WHERE id = 1; SELECT * FROM users WHERE id = 2",
    ],
)
def test_stacked_statements_are_rejected(sql):
    assert _validator().validate_sql(sql) is False


@pytest.mark.xfail(
    reason="GAP: when allowed_tables is None (the default in SQLValidateNode and "
    "whenever the app registry is disabled) there is NO table allowlist, so any "
    "table is readable. The guardrail should deny-by-default.",
)
def test_missing_allowlist_should_deny_by_default():
    no_allowlist = SQLValidatorService(allowed_tables=None, allow_mutations=False)
    assert no_allowlist.validate_sql(
        "SELECT * FROM any_table_at_all WHERE id = 1"
    ) is False


def test_validator_can_enforce_column_allowlist_when_supplied():
    # The MECHANISM for column-level enforcement exists: if validate_sql is
    # given a restricted column set, an out-of-set column is rejected.
    #
    # The RBAC GAP (see tests/unit/security/test_rbac_data_layer_security.py)
    # is that SQLValidateNode populates table_columns from the FULL live schema
    # (SchemaService.get_table_columns), never a role-filtered entitlement set,
    # so this lever is never used for access control in production.
    v = _validator()
    column_entitlements = {"users": {"id", "name"}}  # 'salary' intentionally excluded
    assert v.validate_sql(
        "SELECT u.salary FROM users u WHERE u.id = 1",
        table_columns=column_entitlements,
    ) is False
