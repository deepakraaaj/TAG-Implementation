from __future__ import annotations

from contextvars import ContextVar, Token

_REQUEST_ID: ContextVar[str] = ContextVar("tag_request_id", default="-")


def get_request_id() -> str:
    return _REQUEST_ID.get()


def set_request_id(request_id: str) -> Token:
    normalized = str(request_id or "").strip() or "-"
    return _REQUEST_ID.set(normalized)


def reset_request_id(token: Token) -> None:
    _REQUEST_ID.reset(token)
