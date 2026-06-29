"""Endpoint-level enforcement for the verified-JWT path (release blockers #1-#3).

Proves the chat endpoint fails closed for auth-enforcing tenants and binds the
tenant + identity to the verified token, ignoring the client-controlled
x-app-id / x-user-context headers.
"""

import asyncio
import base64
import time
import types

import jwt
import pytest

from app.api.v1.endpoints import chat as chat_endpoint
from app.apps.registry import AppConfig, AppAuthConfig, AppRegistry
from app.schemas.chat import ChatRequest
from fastapi.responses import JSONResponse

VTS_SECRET_RAW = b"vts-super-secret-signing-key-at-least-64-bytes-long-for-hs512-xxxxxx"
VTS_SECRET_ENV_VALUE = base64.b64encode(VTS_SECRET_RAW).decode("ascii")
FITS_SECRET_RAW = b"YourSuperSecretKeyWhichIsAtLeast32BytesLong123456"
FITS_SECRET_ENV_VALUE = base64.b64encode(FITS_SECRET_RAW).decode("ascii")


def _b64(value: str) -> str:
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _registry() -> AppRegistry:
    return AppRegistry(
        apps={
            "vts": AppConfig(
                display_name="VTS",
                database_url="mysql://h/vts",
                login_from=["VTSDMS"],
                default_metadata={"company_id": 56942673},
                auth=AppAuthConfig(
                    enforce=True,
                    secret_env="VTS_JWT_SECRET",
                    secret_encoding="base64",
                    algorithms=["HS512"],
                    roles_claim="authorities",
                    roles_format="csv",
                    claim_value_encoding="base64",
                    company_id_claim="companyId",
                ),
            ),
            "remp": AppConfig(
                display_name="REMP",
                database_url="mysql://h/remp",
                default_metadata={"company_id": 56942686},
                auth=AppAuthConfig(
                    enforce=True,
                    secret_env="FITS_JWT_SECRET",
                    secret_encoding="base64",
                    algorithms=["HS256"],
                    app_claim="appcode",
                    tenant_claim="loginFrom",
                    user_id_claim="userId",
                    company_id_claim="cid",
                    roles_claim="roles",
                    roles_format="list",
                ),
            ),
        },
        default_app_id="remp",
    )


def _vts_token(roles="ROLE_ADMIN", exp_delta=300):
    return jwt.encode(
        {
            "sub": "alice@vts.com",
            "loginFrom": "VTSDMS",
            "userId": _b64("42"),
            "companyId": _b64("56942673"),
            "authorities": _b64(roles),
            "exp": int(time.time()) + exp_delta,
        },
        VTS_SECRET_RAW,
        algorithm="HS512",
    )


def _remp_token(roles=("ROLE_USER",), exp_delta=300):
    return jwt.encode(
        {
            "sub": "bob@fits.com",
            "loginFrom": "ALSISS",
            "appcode": "REMP",
            "userId": 7,
            "cid": 56942686,
            "roles": list(roles),
            "exp": int(time.time()) + exp_delta,
        },
        FITS_SECRET_RAW,
        algorithm="HS256",
    )


class _FakeUserService:
    def get_user_info(self, user_id, db_url=None):
        return {}

    def get_company_name(self, company_id, db_url=None):
        return ""


def _make_req(capture: dict):
    async def _stream(request):
        capture["user_id"] = request.user_id
        capture["user_role"] = request.user_role
        capture["metadata"] = dict(request.metadata or {})
        if False:  # pragma: no cover - generator with no yields
            yield b""

    chat_service = types.SimpleNamespace(generate_chat_stream=_stream)
    container = types.SimpleNamespace(
        app_registry=_registry(),
        chat_service=chat_service,
        user_service=_FakeUserService(),
        trace_store=None,
    )
    state = types.SimpleNamespace(container=container)
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app)


async def _run(req, request, **kwargs):
    return await chat_endpoint.query_tag(request, req=req, stream=True, **kwargs)


async def _drain(response):
    async for _chunk in response.body_iterator:
        pass


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("VTS_JWT_SECRET", VTS_SECRET_ENV_VALUE)
    monkeypatch.setenv("FITS_JWT_SECRET", FITS_SECRET_ENV_VALUE)


def test_enforced_app_without_token_is_rejected():
    capture: dict = {}
    req = _make_req(capture)
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    resp = asyncio.run(_run(req, request, x_app_id="vts"))
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 401
    assert "metadata" not in capture  # stream never reached


def test_production_without_token_is_rejected_even_without_requested_app(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    chat_endpoint.get_settings.cache_clear()
    capture: dict = {}
    req = _make_req(capture)
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    try:
        resp = asyncio.run(_run(req, request))
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        chat_endpoint.get_settings.cache_clear()
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 401
    assert "metadata" not in capture


def test_forged_token_is_rejected_with_401():
    capture: dict = {}
    req = _make_req(capture)
    forged = jwt.encode(
        {"loginFrom": "VTSDMS", "exp": int(time.time()) + 300},
        b"attacker-key-long-enough-for-hs512-padding-padding-padding-xxxxxx",
        algorithm="HS512",
    )
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    resp = asyncio.run(_run(req, request, authorization=f"Bearer {forged}"))
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 401


def test_valid_token_binds_tenant_and_ignores_x_app_id():
    capture: dict = {}
    req = _make_req(capture)
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    # Client tries to point at a different tenant via x-app-id; token wins.
    resp = asyncio.run(
        _run(req, request, authorization=f"Bearer {_vts_token()}", x_app_id="remp")
    )
    asyncio.run(_drain(resp))
    md = capture["metadata"]
    assert md["app_id"] == "vts"               # bound to verified loginFrom, not x-app-id
    assert capture["user_id"] == "42"
    assert capture["user_role"] == "admin"     # role from verified claim
    assert md["company_id"] == 56942673


def test_remp_token_binds_app_from_signed_appcode_not_shared_login_from():
    capture: dict = {}
    req = _make_req(capture)
    request = ChatRequest(session_id="s1", message="hi", metadata={})
    resp = asyncio.run(
        _run(req, request, authorization=f"Bearer {_remp_token()}", x_app_id="vts")
    )
    asyncio.run(_drain(resp))
    md = capture["metadata"]
    assert md["app_id"] == "remp"
    assert md["login_from"] == "ALSISS"
    assert capture["user_id"] == "7"
    assert md["company_id"] == 56942686
    assert md["db_connection_string"] == "mysql://h/remp"


def test_client_cannot_self_assert_guardrail_metadata_on_enforced_app():
    capture: dict = {}
    req = _make_req(capture)
    # Forged guardrail flags + role in the request body must be discarded.
    request = ChatRequest(
        session_id="s1",
        message="hi",
        user_role="superadmin",
        metadata={
            "allow_mutations": True,
            "require_select_where": False,
            "allowed_tables": ["secret_payroll"],
            "user_role": "superadmin",
        },
    )
    resp = asyncio.run(_run(req, request, authorization=f"Bearer {_vts_token(roles='ROLE_USER')}"))
    asyncio.run(_drain(resp))
    md = capture["metadata"]
    assert md["allow_mutations"] is False          # server-derived from app config
    assert md["require_select_where"] is True
    assert "secret_payroll" not in (md.get("allowed_tables") or [])
    assert capture["user_role"] == "user"          # forged superadmin discarded
