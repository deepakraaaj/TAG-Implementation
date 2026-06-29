"""Verification tests for the signed-JWT auth path (release blockers #1-#3).

These mint real tokens with the per-tenant contracts the host services use and
assert the secure behaviour now holds: signature + expiry are enforced, tenant
is bound to a verified claim, and roles come only from the token.
"""

import base64
import time

import jwt
import pytest

from app.apps.registry import AppConfig, AppAuthConfig, AppRegistry
from app.security.jwt_auth import (
    AuthError,
    JwtVerifier,
    extract_bearer_token,
    primary_role,
)

# --- Tenant contracts mirroring the real host services ------------------------

VTS_SECRET_RAW = b"vts-super-secret-signing-key-at-least-64-bytes-long-for-hs512-xxxxxx"
# VTS stores its secret base64-encoded (jjwt Decoders.BASE64.decode).
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
                login_from=["VTSDMS", "VTSAPP", "ALSVTS"],
                auth=AppAuthConfig(
                    enforce=True,
                    secret_env="VTS_JWT_SECRET",
                    secret_encoding="base64",
                    algorithms=["HS512", "HS256"],
                    tenant_claim="loginFrom",
                    user_id_claim="userId",
                    company_id_claim="companyId",
                    company_name_claim="companyName",
                    roles_claim="authorities",
                    roles_format="csv",
                    claim_value_encoding="base64",
                ),
            ),
            "remp": AppConfig(
                display_name="REMP",
                database_url="mysql://h/remp",
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
            # An app with no auth block -> not enforced (legacy/demo).
            "demo": AppConfig(display_name="Demo", database_url="mysql://h/demo"),
        },
        default_app_id="demo",
    )


def _vts_token(roles="ROLE_ADMIN,ROLE_USER", exp_delta=300, login_from="VTSDMS"):
    payload = {
        "sub": "alice@vts.com",
        "loginFrom": login_from,
        "userId": _b64("42"),
        "companyId": _b64("56942673"),
        "companyName": _b64("Acme Fleet"),
        "authorities": _b64(roles),
        "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(payload, VTS_SECRET_RAW, algorithm="HS512")


def _fits_token(roles=("user",), exp_delta=300):
    payload = {
        "sub": "bob@fits.com",
        "loginFrom": "ALSISS",
        "appcode": "REMP",
        "userId": 7,
        "cid": 56942686,
        "roles": list(roles),
        "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(payload, FITS_SECRET_RAW, algorithm="HS256")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("VTS_JWT_SECRET", VTS_SECRET_ENV_VALUE)
    monkeypatch.setenv("FITS_JWT_SECRET", FITS_SECRET_ENV_VALUE)


# --- Bearer extraction --------------------------------------------------------

def test_extract_bearer_token_variants():
    assert extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert extract_bearer_token("bearer abc.def.ghi") == "abc.def.ghi"
    assert extract_bearer_token("abc.def.ghi") == "abc.def.ghi"  # bare JWT tolerated
    assert extract_bearer_token("") is None
    assert extract_bearer_token(None) is None


# --- VTS contract -------------------------------------------------------------

def test_vts_token_verifies_and_decodes_claims():
    verifier = JwtVerifier(_registry())
    identity = verifier.verify(_vts_token())
    assert identity.app_id == "vts"            # tenant bound to verified loginFrom
    assert identity.tenant == "VTSDMS"
    assert identity.user_id == "42"            # base64 claim values decoded
    assert identity.company_id == "56942673"
    assert identity.company_name == "Acme Fleet"
    assert "ROLE_ADMIN" in identity.roles
    assert primary_role(identity.roles) == "admin"


def test_fits_token_verifies_and_decodes_claims():
    verifier = JwtVerifier(_registry())
    identity = verifier.verify(_fits_token(roles=("ROLE_SUPER_ADMIN",)))
    assert identity.app_id == "remp"
    assert identity.tenant == "ALSISS"
    assert identity.company_id == "56942686"
    assert primary_role(identity.roles) == "superadmin"


def test_fits_shared_login_from_requires_signed_appcode():
    verifier = JwtVerifier(_registry())
    missing_appcode = jwt.encode(
        {
            "sub": "bob@fits.com",
            "loginFrom": "ALSISS",
            "userId": 7,
            "cid": 56942686,
            "roles": ["user"],
            "exp": int(time.time()) + 300,
        },
        FITS_SECRET_RAW,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verifier.verify(missing_appcode)


def test_fits_signed_appcode_must_match_configured_app():
    verifier = JwtVerifier(_registry())
    wrong_appcode = jwt.encode(
        {
            "sub": "bob@fits.com",
            "loginFrom": "ALSISS",
            "appcode": "unknown-app",
            "userId": 7,
            "cid": 56942686,
            "roles": ["user"],
            "exp": int(time.time()) + 300,
        },
        FITS_SECRET_RAW,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verifier.verify(wrong_appcode)


# --- The blockers, now closed -------------------------------------------------

def test_tampered_signature_is_rejected():
    verifier = JwtVerifier(_registry())
    token = _vts_token()
    head, payload, sig = token.split(".")
    forged = ".".join([head, payload, sig[:-3] + "AAA"])
    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_token_signed_with_wrong_secret_is_rejected():
    verifier = JwtVerifier(_registry())
    bad = jwt.encode(
        {"loginFrom": "VTSDMS", "userId": _b64("1"), "exp": int(time.time()) + 300},
        b"attacker-key-which-is-also-quite-long-enough-for-hs512-padding-xx",
        algorithm="HS512",
    )
    with pytest.raises(AuthError):
        verifier.verify(bad)


def test_expired_token_is_rejected():
    verifier = JwtVerifier(_registry())
    # Beyond the 30s configured leeway.
    with pytest.raises(AuthError):
        verifier.verify(_vts_token(exp_delta=-120))


def test_alg_none_is_rejected():
    verifier = JwtVerifier(_registry())
    unsigned = jwt.encode(
        {"loginFrom": "VTSDMS", "userId": _b64("1"), "exp": int(time.time()) + 300},
        key=None,
        algorithm="none",
    )
    with pytest.raises(AuthError):
        verifier.verify(unsigned)


def test_forged_role_does_not_stick():
    # Attacker mints a token with a privileged role but cannot sign it correctly.
    verifier = JwtVerifier(_registry())
    forged = jwt.encode(
        {
            "loginFrom": "VTSDMS",
            "userId": _b64("1"),
            "authorities": _b64("ROLE_SUPER_ADMIN"),
            "exp": int(time.time()) + 300,
        },
        b"not-the-real-vts-secret-but-long-enough-to-be-a-valid-hs512-keyyy",
        algorithm="HS512",
    )
    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_unknown_tenant_token_is_rejected():
    verifier = JwtVerifier(_registry())
    token = jwt.encode(
        {"loginFrom": "NOPE", "exp": int(time.time()) + 300},
        VTS_SECRET_RAW,
        algorithm="HS512",
    )
    with pytest.raises(AuthError):
        verifier.verify(token)


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("VTS_JWT_SECRET", raising=False)
    verifier = JwtVerifier(_registry())
    with pytest.raises(AuthError) as exc:
        verifier.verify(_vts_token())
    assert exc.value.status == 503  # misconfig, not a client error -> never fail open


def test_is_enforced_reflects_app_config():
    verifier = JwtVerifier(_registry())
    assert verifier.is_enforced("vts") is True
    assert verifier.is_enforced("remp") is True
    assert verifier.is_enforced("demo") is False
    assert verifier.is_enforced(None) is False


def test_primary_role_defaults_to_user():
    assert primary_role([]) == "user"
    assert primary_role(["ROLE_HELP_DESK"]) == "user"
    assert primary_role(["ROLE_ADMIN"]) == "admin"
    assert primary_role(["whatever", "ROLE_SUPER_ADMIN"]) == "superadmin"
