"""
Security audit tests for RBAC enforced at the data layer (area #4).

Design intent: role-based access (allowed tables, column/row entitlements,
mutation rights) is enforced server-side at the data layer, not assumed from
the UI.

What exists: SQLValidateNode gates INSERT/UPDATE by role
(MUTATION_ALLOWED_ROLES) and requires explicit permission. That part works and
is asserted below.

GAPS (xfail / documented):
  * The role used for gating is the same client-asserted role from the unsigned
    x-user-context header (see test_auth_handshake_security.py) -- so the
    server-side check trusts a client-supplied value.
  * There is no column- or row-level entitlement filtering: SQLValidateNode
    feeds the validator the FULL live schema (SchemaService.get_table_columns),
    never a role-scoped column allowlist, so the column lever is never used for
    access control.
"""

import asyncio

import pytest

from app.assistant.nodes.sql.sql_validate_node import SQLValidateNode


def _node():
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"admin", "superadmin"}
    node.require_explicit_mutation_permission = True
    return node


# ---------------------------------------------------------------------------
# Mutation RBAC that DOES work (passing).
# ---------------------------------------------------------------------------

def test_non_privileged_role_cannot_mutate():
    node = _node()
    result = asyncio.run(
        node.run(
            {
                "sql_query": "UPDATE asset SET name='x' WHERE id=1;",
                "metadata": {"allow_mutations": True, "user_role": "user"},
            }
        )
    )
    assert result["error"] == "Mutation not allowed for current role/policy."


def test_privileged_role_may_mutate_with_explicit_permission():
    node = _node()
    result = asyncio.run(
        node.run(
            {
                "sql_query": "UPDATE asset SET name='x' WHERE id=1;",
                "metadata": {"allow_mutations": True, "user_role": "admin"},
            }
        )
    )
    assert result["error"] is None


def test_mutation_denied_without_explicit_permission_even_for_admin():
    node = _node()
    result = asyncio.run(
        node.run(
            {
                "sql_query": "INSERT INTO asset (name) VALUES ('x');",
                "metadata": {"user_role": "admin"},  # no allow_mutations flag
            }
        )
    )
    assert result["error"] == "Mutation not allowed for current role/policy."


# ---------------------------------------------------------------------------
# GAPS
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="GAP: mutation RBAC trusts the client-asserted role. Because the role "
    "originates from the unsigned x-user-context header, a caller can grant "
    "themselves a privileged role. The role used for gating must come from a "
    "verified token, not request metadata.",
)
def test_mutation_role_should_not_come_from_client_metadata():
    node = _node()
    # The caller self-asserts an admin role purely via request metadata.
    allowed = node._mutation_policy_override(
        {"allow_mutations": True, "user_role": "admin"}, is_mutation=True
    )
    # Secure expectation: a client-supplied role must NOT unlock mutations.
    assert allowed is False


@pytest.mark.xfail(
    reason="GAP: no role-scoped column entitlements. SQLValidateNode builds "
    "table_columns from the full live schema, so the validator's column check "
    "cannot enforce that a role only sees its permitted columns. Restricted "
    "columns (e.g. salary) are returned as long as the table is allowed.",
)
def test_schema_feed_is_role_scoped_for_column_rbac():
    node = _node()
    # There is no API on the node to supply a role-scoped column allowlist.
    assert hasattr(node, "role_column_entitlements")
