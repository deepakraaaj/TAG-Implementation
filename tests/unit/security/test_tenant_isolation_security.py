"""
Security audit tests for tenant isolation (release-blocking area #2).

Design intent (per the system spec): each tenant is a physically separate
database, routed per-request, and the tenant MUST be derived ONLY from a
validated JWT -- never from a client-controlled header or body field.

Findings encoded here:
  * Tenant routing keys off ``app_id`` which is taken from the client-controlled
    ``x-app-id`` header / request metadata (chat.py::_requested_app_id). There is
    no JWT, so nothing binds the caller to a tenant.
  * When the app registry is DISABLED, the server never overrides the safety
    metadata, so a client can self-assert ``allow_mutations`` /
    ``allowed_tables`` / ``require_select_where`` via the unsigned
    ``x-user-context`` header and weaken the guardrail.

Pure-routing facts are asserted as passing tests. The trust-boundary violations
are marked ``xfail`` -- they assert the secure behaviour and fail today.
"""

import asyncio
import base64
import json
from pathlib import Path

import pytest

import app.db.multi_tenant_manager as multi_tenant_manager
from app.api.v1.endpoints import chat as chat_endpoint
from app.apps.registry import AppConfig, AppRegistry
from app.schemas.chat import ChatRequest


class _Settings:
    def __init__(self, path: str) -> None:
        self.APPS_CONFIG_PATH = path
        self.DEFAULT_CHAT_APP_ID = "tenant_a"


def _write_two_tenant_config(path: Path) -> None:
    path.write_text(
        """
apps:
  tenant_a:
    display_name: Tenant A
    domain: tenant_a
    database_url: mysql+aiomysql://localhost:3306/tenant_a
  tenant_b:
    display_name: Tenant B
    domain: tenant_b
    database_url: mysql+aiomysql://localhost:3306/tenant_b
""".strip(),
        encoding="utf-8",
    )


def _encode_context(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


# ---------------------------------------------------------------------------
# Routing correctness (passing) -- each app_id maps to its own database.
# ---------------------------------------------------------------------------

def test_each_tenant_routes_to_its_own_database(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "apps.yaml"
    _write_two_tenant_config(config_path)
    monkeypatch.setattr(multi_tenant_manager, "get_settings", lambda: _Settings(str(config_path)))

    mgr = multi_tenant_manager.MultiTenantDatabaseManager
    assert mgr.get_database_url("tenant_a").endswith("/tenant_a")
    assert mgr.get_database_url("tenant_b").endswith("/tenant_b")


def test_unknown_tenant_is_not_silently_routed(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "apps.yaml"
    _write_two_tenant_config(config_path)
    monkeypatch.setattr(multi_tenant_manager, "get_settings", lambda: _Settings(str(config_path)))

    mgr = multi_tenant_manager.MultiTenantDatabaseManager
    assert mgr.get_database_url("tenant_zzz") is None
    with pytest.raises(ValueError):
        asyncio.run(mgr.get_connection("tenant_zzz"))


# ---------------------------------------------------------------------------
# Trust boundary: tenant must NOT be selectable by the client.
# ---------------------------------------------------------------------------

def test_client_controls_tenant_selection_via_app_id_header():
    """
    Documents the current (insecure) behaviour: the tenant/db is chosen purely
    from the client-supplied x-app-id header. Whoever sends the request picks
    the database. This test passes today precisely because the vulnerability
    exists; it will start failing once tenant is bound to a verified JWT.
    """
    registry = AppRegistry(
        apps={
            "tenant_a": AppConfig(display_name="A", database_url="mysql://h/tenant_a"),
            "tenant_b": AppConfig(display_name="B", database_url="mysql://h/tenant_b"),
        },
        default_app_id="tenant_a",
    )

    # A client that authenticated (conceptually) as tenant_a can simply ask for
    # tenant_b and the registry hands back tenant_b's database connection.
    app_id, config = registry.resolve_request("tenant_b")
    assert app_id == "tenant_b"
    assert config.database_url.endswith("/tenant_b")


@pytest.mark.xfail(
    reason="GAP: there is no JWT, so the tenant is taken from the client-controlled "
    "x-app-id header / metadata (chat.py::_requested_app_id). A request 'for' "
    "tenant_a can reach tenant_b simply by changing the header. Tenant MUST be "
    "derived from a verified token claim.",
)
def test_app_id_should_not_be_taken_from_client_header():
    requested = chat_endpoint._requested_app_id(metadata={}, x_app_id="tenant_b")
    # Secure expectation: a client-supplied header must NOT be able to select a
    # tenant on its own.
    assert requested != "tenant_b"


@pytest.mark.xfail(
    reason="GAP: when the app registry is disabled the server does not override "
    "guardrail metadata, so the unsigned x-user-context header can self-assert "
    "allow_mutations / require_select_where / allowed_tables and weaken the "
    "SQL guardrail.",
)
def test_client_cannot_self_assert_guardrail_metadata(monkeypatch):
    captured = {}

    async def _capture_stream(request):
        captured["metadata"] = dict(request.metadata or {})
        if False:  # pragma: no cover - generator with no yields
            yield b""

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)

    forged = _encode_context(
        {
            "user_id": "1",
            "allow_mutations": True,
            "require_select_where": False,
            "allowed_tables": ["users", "secret_payroll"],
        }
    )
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    response = asyncio.run(
        chat_endpoint.query_tag(request, req=None, x_user_context=forged)
    )
    asyncio.run(_drain(response))

    md = captured.get("metadata", {})
    # Secure expectation: client-supplied guardrail flags must be ignored.
    assert md.get("allow_mutations") is not True
    assert md.get("require_select_where") is not False
    assert "secret_payroll" not in (md.get("allowed_tables") or [])


async def _drain(response):
    async for _chunk in response.body_iterator:
        pass
