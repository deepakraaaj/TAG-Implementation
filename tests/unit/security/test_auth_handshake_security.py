"""
Security audit tests for the auth handshake (release-blocking area #3).

Design intent: every request carries a signed JWT minted by the host app
(claims: tenant_id, user_id, roles; per-tenant signing key). The backend must
verify the signature, expiry, and issuer/audience, resolve the per-tenant key,
enforce an iframe origin allowlist, and reject any client self-asserted role.

AUDIT FINDING: none of this exists. Identity arrives in the ``x-user-context``
header as **unsigned, unverified Base64 JSON** (chat.py::_decode_user_context).
There is no JWT verification, no expiry, no issuer/audience check, no per-tenant
key, and no origin allowlist. ``user_role`` is whatever the client says it is.

The passing tests below pin the current (insecure) behaviour so the gap is
explicit and regression-visible. The ``xfail`` tests assert the secure
behaviour that must exist before release.
"""

import asyncio
import base64
import json

import pytest

from app.api.v1.endpoints import chat as chat_endpoint
from app.schemas.chat import ChatRequest


def _encode_context(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


async def _drain(response):
    async for _chunk in response.body_iterator:
        pass


# ---------------------------------------------------------------------------
# Current behaviour (passing) -- documents the absence of verification.
# ---------------------------------------------------------------------------

def test_user_context_is_decoded_without_any_signature_check():
    # Arbitrary, attacker-authored claims decode cleanly: no signature, no key.
    forged = _encode_context({"user_id": "999", "user_role": "superadmin"})
    decoded = chat_endpoint._decode_user_context(forged)
    assert decoded == {"user_id": "999", "user_role": "superadmin"}


def test_tampered_token_is_silently_ignored_not_rejected(monkeypatch):
    # A malformed token does not reject the request; it is logged and dropped,
    # and the request proceeds anonymously (fail-open).
    request = ChatRequest(session_id="s-tamper", message="hi", metadata={})

    async def _noop_stream(_request):
        if False:  # pragma: no cover
            yield b""

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _noop_stream)

    response = asyncio.run(
        chat_endpoint.query_tag(request, req=None, x_user_context="!!!not-base64!!!")
    )
    # No exception, request still served.
    asyncio.run(_drain(response))


# ---------------------------------------------------------------------------
# Secure expectations (xfail) -- must hold before production.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="GAP: no JWT/signature verification. A client-asserted role is "
    "accepted verbatim, so any caller can claim 'superadmin'. Roles MUST come "
    "from a verified token claim, never from the client.",
)
def test_forged_role_should_not_be_trusted(monkeypatch):
    captured = {}

    async def _capture_stream(request):
        captured["user_role"] = getattr(request, "user_role", None)
        if False:  # pragma: no cover
            yield b""

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)

    forged = _encode_context({"user_id": "1", "user_role": "superadmin"})
    request = ChatRequest(session_id="s-role", message="hi", metadata={})
    response = asyncio.run(
        chat_endpoint.query_tag(request, req=None, x_user_context=forged)
    )
    asyncio.run(_drain(response))

    # Secure expectation: a self-asserted privileged role must not stick.
    assert captured.get("user_role") != "superadmin"


@pytest.mark.xfail(
    reason="GAP: no signature verification primitive exists. _decode_user_context "
    "does not verify a signature/HMAC, so a token whose payload was altered after "
    "minting cannot be detected.",
)
def test_modified_payload_should_be_rejected():
    # An attacker flips user_role from 'user' to 'admin' in the encoded payload.
    tampered = _encode_context({"user_id": "1", "user_role": "admin"})
    decoded = chat_endpoint._decode_user_context(tampered)
    # Secure expectation: verification rejects unsigned/tampered tokens.
    assert decoded == {}


@pytest.mark.xfail(
    reason="GAP: no concept of token expiry. An 'exp' claim in the past is "
    "ignored and the identity is still honoured.",
)
def test_expired_token_should_be_rejected():
    expired = _encode_context({"user_id": "1", "exp": 0})  # 1970 -> long expired
    decoded = chat_endpoint._decode_user_context(expired)
    assert decoded == {}
