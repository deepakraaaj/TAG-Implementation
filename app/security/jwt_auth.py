"""Signed-JWT verification for incoming chat requests (release blockers #1-#3).

The host apps (VTS, FITS, ...) already mint signed HS-family JWTs. Identity and
tenant selection MUST be derived only from a verified token — never from the
client-controlled ``x-app-id`` / ``x-user-context`` headers.

Flow:
  1. The verifier decodes the token WITHOUT verifying only to read routing
     claims. Prefer a configured signed app claim such as ``appcode``; fall
     back to ``loginFrom`` for older tenant-specific tokens. This just selects
     *which* secret/algorithm to verify against; a wrong guess fails at step 2,
     so it grants no trust.
  2. ``verify`` loads that app's :class:`AppAuthConfig`, resolves the signing
     secret from the environment, and verifies signature + expiry (+ issuer /
     audience when configured) with the pinned algorithms. Only then are the
     identity claims read — and per-tenant decoded — into a
     :class:`VerifiedIdentity`.

Symmetric (HS*) secrets mean whoever holds a tenant's secret can also mint its
tokens; the secret therefore lives only in the backend environment, never in
config or the client.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import jwt

from app.apps.registry import AppAuthConfig, AppConfig, AppRegistry

logger = logging.getLogger(__name__)

# Hard allow-list of algorithms we will ever accept. This blocks "alg: none"
# and asymmetric-key-confusion downgrades regardless of per-app config.
_SUPPORTED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class AuthError(Exception):
    """Raised when a token is missing, malformed, unverifiable, or expired.

    Carries an HTTP-style ``status`` (401 by default) and a non-leaky public
    ``message`` safe to return to the caller.
    """

    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class VerifiedIdentity:
    """The trusted identity extracted from a verified token."""

    app_id: str
    tenant: str
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    user_name: Optional[str] = None
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)


def extract_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    """Pull the raw token out of an ``Authorization: Bearer <token>`` header."""
    raw = str(authorization_header or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    # Tolerate a bare token without the "Bearer " prefix.
    return raw if raw.count(".") >= 2 else None


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (binascii.Error, ValueError):
        return base64.b64decode(value + padding)


def _decode_claim_value(value: Any, encoding: Optional[str]) -> str:
    text = str(value if value is not None else "").strip().strip('"').strip("'").strip()
    if not text or not encoding:
        return text
    if encoding == "base64":
        if text.isdigit():
            return text
        try:
            return _b64decode(text).decode("utf-8").strip()
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return text
    return text


def _peek_unverified_claim(token: str, claim: str) -> str:
    """Read a single claim from the token payload WITHOUT verifying the signature.

    Used only to select which tenant secret to verify against. Never trusted.
    """
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return ""
    if not isinstance(unverified, dict):
        return ""
    value = unverified.get(claim) or unverified.get(_snake_alias(claim))
    return str(value or "").strip()


def _snake_alias(claim: str) -> str:
    # loginFrom -> login_from, so we tolerate either spelling in the token.
    out = []
    for ch in claim:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


class JwtVerifier:
    """Verifies signed tokens against per-app :class:`AppAuthConfig`."""

    def __init__(self, registry: AppRegistry) -> None:
        self._registry = registry

    def is_enforced(self, app_id: Optional[str]) -> bool:
        cfg = self._registry.resolve_optional(app_id)
        return bool(cfg and cfg.auth and cfg.auth.enforce)

    def _resolve_app(self, token: str) -> tuple[str, AppConfig]:
        # Prefer configured app identity claims. This handles central auth
        # tokens where loginFrom identifies the auth service (for example
        # ALSISS), while a signed appcode identifies REMP/FITS.
        for _configured_app_id, configured_cfg in self._registry.list_apps():
            auth = configured_cfg.auth
            app_claim = str(getattr(auth, "app_claim", "") or "").strip() if auth else ""
            if not app_claim:
                continue
            app_code = _peek_unverified_claim(token, app_claim)
            if not app_code:
                continue
            app_id = self._registry.resolve_alias(app_code)
            cfg = self._registry.resolve_optional(app_id) if app_id else None
            if app_id and cfg is not None:
                return app_id, cfg

        # Backward-compatible fallback for app-specific loginFrom values such
        # as VTSDMS.
        tenant = _peek_unverified_claim(token, "loginFrom")
        app_id = self._registry.resolve_alias(tenant) if tenant else None
        cfg = self._registry.resolve_optional(app_id) if app_id else None
        if not app_id or cfg is None:
            raise AuthError("Token does not identify a known tenant.")
        return app_id, cfg

    def _signing_key(self, auth: AppAuthConfig) -> bytes:
        secret = os.environ.get(str(auth.secret_env or "").strip(), "")
        if not secret:
            # Misconfiguration, not a client error: refuse rather than fail open.
            logger.error("Missing signing secret env %r for an enforced app.", auth.secret_env)
            raise AuthError("Authentication is not configured for this tenant.", status=503)
        if auth.secret_encoding == "base64":
            try:
                return _b64decode(secret)
            except (binascii.Error, ValueError) as exc:  # pragma: no cover - config error
                raise AuthError("Authentication is misconfigured for this tenant.", status=503) from exc
        return secret.encode("utf-8")

    def verify(self, token: Optional[str]) -> VerifiedIdentity:
        raw = str(token or "").strip()
        if not raw:
            raise AuthError("A signed authentication token is required.")

        app_id, cfg = self._resolve_app(raw)
        auth = cfg.auth
        if auth is None or not auth.enforce:
            # Should not happen — callers gate on is_enforced — but never verify
            # against an app that opted out.
            raise AuthError("Authentication is not enabled for this tenant.", status=503)

        algorithms = [a for a in (auth.algorithms or []) if a in _SUPPORTED_ALGORITHMS]
        if not algorithms:
            logger.error("App %s configures no supported JWT algorithm.", app_id)
            raise AuthError("Authentication is misconfigured for this tenant.", status=503)

        key = self._signing_key(auth)
        options = {"require": ["exp"], "verify_exp": True}
        decode_kwargs: dict[str, Any] = {
            "algorithms": algorithms,
            "leeway": max(0, int(auth.leeway_seconds or 0)),
            "options": options,
        }
        if auth.audience:
            decode_kwargs["audience"] = auth.audience
        if auth.issuer:
            decode_kwargs["issuer"] = auth.issuer

        try:
            claims = jwt.decode(raw, key, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Authentication token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            # Bad signature, alg mismatch, bad iss/aud, missing exp, etc.
            logger.warning("Rejected token for app %s: %s", app_id, type(exc).__name__)
            raise AuthError("Authentication token is invalid.") from exc

        self._validate_signed_app_claim(app_id, auth, claims)
        return self._identity_from_claims(app_id, auth, claims)

    def _validate_signed_app_claim(self, app_id: str, auth: AppAuthConfig, claims: dict[str, Any]) -> None:
        app_claim = str(getattr(auth, "app_claim", "") or "").strip()
        if not app_claim:
            return
        value = claims.get(app_claim)
        if value is None:
            value = claims.get(_snake_alias(app_claim))
        resolved = self._registry.resolve_alias(str(value or "").strip())
        if not resolved or resolved != app_id:
            logger.warning("Rejected token for app %s: signed app claim mismatch.", app_id)
            raise AuthError("Authentication token is invalid.")

    @staticmethod
    def _identity_from_claims(app_id: str, auth: AppAuthConfig, claims: dict[str, Any]) -> VerifiedIdentity:
        enc = auth.claim_value_encoding
        tenant = str(claims.get(auth.tenant_claim) or claims.get(_snake_alias(auth.tenant_claim)) or "").strip()

        def read(claim_name: Optional[str]) -> str:
            if not claim_name:
                return ""
            value = claims.get(claim_name)
            if value is None:
                value = claims.get(_snake_alias(claim_name))
            return _decode_claim_value(value, enc)

        user_id = read(auth.user_id_claim) or _decode_claim_value(claims.get("uid"), enc)
        company_id = read(auth.company_id_claim)
        company_name = read(auth.company_name_claim)
        user_name = read(auth.user_name_claim) if auth.user_name_claim != "sub" else str(claims.get("sub") or "").strip()

        roles = _extract_roles(claims.get(auth.roles_claim), auth.roles_format, enc)

        return VerifiedIdentity(
            app_id=app_id,
            tenant=tenant,
            user_id=user_id or None,
            company_id=company_id or None,
            company_name=company_name or None,
            user_name=user_name or None,
            roles=roles,
            claims=claims,
        )


def _extract_roles(value: Any, roles_format: str, encoding: Optional[str]) -> list[str]:
    if value is None:
        return []
    if roles_format == "csv":
        decoded = _decode_claim_value(value, encoding)
        return [part.strip() for part in decoded.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    decoded = _decode_claim_value(value, encoding)
    return [decoded] if decoded else []


# Roles vary by tenant (ROLE_SUPER_ADMIN, superadmin, ADMIN, ...). Normalize to
# the vocabulary the SQL mutation gate uses (see MUTATION_ALLOWED_ROLES).
_PRIVILEGE_ORDER = ("superadmin", "admin")


def primary_role(roles: list[str]) -> str:
    """Collapse a set of token roles to one value for the mutation gate.

    Returns the most privileged recognized role, else "user". Because this is
    derived only from verified claims, a client cannot self-assert it.
    """
    normalized = {_normalize_role(r) for r in roles}
    for role in _PRIVILEGE_ORDER:
        if role in normalized:
            return role
    return "user"


def _normalize_role(role: str) -> str:
    text = str(role or "").strip().lower()
    if text.startswith("role_"):
        text = text[len("role_"):]
    return text.replace("_", "").replace(" ", "")
