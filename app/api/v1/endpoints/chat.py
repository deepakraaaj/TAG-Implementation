from contextlib import nullcontext
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Annotated, Any, Optional
import json
import logging
import base64
import asyncio
import uuid
import time

from app.core.dependencies import get_container
from app.domains.registry import DomainRegistry
from app.schemas.chat import ChatRequest

router = APIRouter()
logger = logging.getLogger(__name__)


class _ChatServiceProxy:
    def __init__(self) -> None:
        self._target = None

    def _resolve(self):
        if self._target is None:
            self._target = get_container().chat_service
        return self._target

    async def start_session(self):
        return await self._resolve().start_session()

    async def generate_chat_stream(self, request):
        async for chunk in self._resolve().generate_chat_stream(request):
            yield chunk

    def _build_final_response(self, *args, **kwargs):
        return self._resolve()._build_final_response(*args, **kwargs)


class _UserServiceProxy:
    def __init__(self) -> None:
        self._target = None

    def _resolve(self):
        if self._target is None:
            self._target = get_container().user_service
        return self._target

    def get_user_info(self, user_id, db_url=None):
        return self._resolve().get_user_info(user_id, db_url=db_url)


# Backward-compatible module globals used by unit tests for monkeypatching.
chat_service: Any = _ChatServiceProxy()
user_service: Any = _UserServiceProxy()


def _resolve_services(req: Optional[Request]) -> tuple[Any, Any]:
    container = _resolve_container(req)
    if container is not None:
        return container.chat_service, container.user_service
    return chat_service, user_service


def _resolve_container(req: Optional[Request]) -> Any:
    app = getattr(req, "app", None)
    container = getattr(getattr(app, "state", None), "container", None)
    return container


# Token payload fields whose *values* arrive Base64-encoded (camelCase, minted
# by the host app). Plaintext fields like ``loginFrom``/``service`` are left as-is.
_TOKEN_ENCODED_FIELDS = ("companyId", "userId", "companyName", "companyType")


def _b64decode_segment(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_b64_value(value: object) -> str:
    """Best-effort Base64 decode of a single token field value.

    Returns the original text when the value is empty, already plaintext
    (e.g. a numeric id), or not valid Base64.
    """
    text = str(value or "").strip().strip('"').strip("'").strip()
    if not text or text.isdigit():
        return text
    try:
        decoded = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
        return decoded.decode("utf-8").strip()
    except Exception:
        return text


def _decode_user_context(raw_header: str) -> dict:
    token = str(raw_header or "").strip()
    if not token:
        return {}
    # A signed JWT arrives as header.payload.signature; decode the payload
    # segment. Otherwise treat the whole header as Base64-encoded JSON.
    segment = token.split(".")[1] if token.count(".") >= 2 else token
    decoded = _b64decode_segment(segment).decode("utf-8")
    data = json.loads(decoded)
    if not isinstance(data, dict):
        return {}
    # Decode the per-field Base64 values minted by the host app.
    for field in _TOKEN_ENCODED_FIELDS:
        if field in data:
            data[field] = _decode_b64_value(data[field])
    login_from = str(data.get("loginFrom") or data.get("login_from") or "").strip()
    if login_from:
        data["login_from"] = login_from
    return data


def _build_terminal_error_result(chat_service: Any, session_id: str, message: str, trace_id: str) -> dict:
    return chat_service._build_final_response(
        session_id,
        message,
        status="error",
        workflow_payload=None,
        sql_data=None,
        trace_id=trace_id,
    )


def _record_chat_trace(req, request, terminal_event, trace_id, started_at) -> None:
    """Push a compact record of the request into the admin trace ring buffer."""
    try:
        container = _resolve_container(req)
        store = getattr(container, "trace_store", None)
        if store is None:
            return
        meta = request.metadata or {}
        event = terminal_event if isinstance(terminal_event, dict) else {}
        sql = event.get("sql") if isinstance(event.get("sql"), dict) else {}
        status = str(event.get("status", "") or "").lower()
        row_count = sql.get("row_count")
        if row_count is None:
            row_count = sql.get("count")
        error = None
        if status not in ("ok", "success", ""):
            error = event.get("message") or status
        store.record(
            {
                "trace_id": trace_id,
                "app_id": str(meta.get("app_id") or meta.get("appId") or meta.get("domain_name") or ""),
                "source": "chat",
                "query": str(getattr(request, "message", "") or ""),
                "route": event.get("route") or sql.get("route"),
                "sql_query": sql.get("query"),
                "row_count": row_count,
                "status": status,
                "error": error,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
            }
        )
    except Exception:
        logger.debug("Failed to record chat trace", exc_info=True)


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _company_name_from_metadata(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    company_obj = metadata.get("company")
    company_obj_name = ""
    if isinstance(company_obj, dict):
        company_obj_name = _clean_text(company_obj.get("name"))
    for candidate in (
        metadata.get("company_name"),
        metadata.get("companyName"),
        company_obj_name,
    ):
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned
    return ""


def _has_usable_user_name(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    user_name = _clean_text(metadata.get("user_name"))
    if not user_name:
        return False
    lowered = user_name.casefold()
    if lowered in {"user", "unknown", "na", "n/a", "null", "none"}:
        return False
    company_name = _company_name_from_metadata(metadata)
    if company_name and lowered == company_name.casefold():
        return False
    return True


def _requested_app_id(
    metadata: dict,
    x_app_id: Optional[str] = None,
) -> str:
    if x_app_id and str(x_app_id).strip():
        return str(x_app_id).strip()
    if not isinstance(metadata, dict):
        return ""
    for key in ("app_id", "appId", "application_id", "applicationId", "source_name", "sourceName"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _apply_app_config(
    req: Optional[Request],
    metadata: dict,
    requested_app_id: str,
    login_from: str | None = None,
) -> tuple[str | None, Any | None]:
    container = _resolve_container(req)
    app_registry = getattr(container, "app_registry", None)
    if app_registry is None or not callable(getattr(app_registry, "enabled", None)) or not app_registry.enabled():
        return None, None

    # Resolve the target app tolerantly. The signed token's `loginFrom` is
    # authoritative and takes precedence; the explicit app id (X-App-Id /
    # widget `appId`) is only a fallback for tokens that omit loginFrom. Both
    # are run through alias/normalization matching so slugged or suffixed
    # variants still map to a real app instead of 400ing.
    resolved_request = None
    for candidate in (login_from, requested_app_id):
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        resolved_request = app_registry.resolve_alias(candidate)
        if resolved_request:
            break
        logger.warning("Could not resolve app from %r; trying next candidate / default.", candidate)

    app_id, app_config = app_registry.resolve_request(resolved_request)
    if app_id is None or app_config is None:
        return None, None

    for key, value in dict(app_config.default_metadata or {}).items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        existing = metadata.get(normalized_key)
        if existing is None or str(existing).strip() == "":
            metadata[normalized_key] = value

    metadata["app_id"] = app_id
    metadata["app_name"] = app_config.name or app_id
    metadata["app_display_name"] = app_config.display_name
    metadata["db_connection_string"] = app_config.database_url
    metadata["domain_name"] = app_config.domain_name or app_id
    metadata["allow_mutations"] = bool(app_config.allow_mutations)
    metadata["require_select_where"] = bool(app_config.require_select_where)
    metadata["allowed_tables"] = list(app_config.allowed_tables or [])
    metadata["protected_tables"] = list(app_config.protected_tables or [])
    return app_id, app_config


def _domain_context(domain_name: str | None):
    normalized = str(domain_name or "").strip()
    if not normalized:
        return nullcontext()
    return DomainRegistry.use_domain(normalized)


@router.post("/session/start")
async def start_session(
    req: Request = None,
    x_app_id: Annotated[Optional[str], Header()] = None,
):
    active_chat_service, _ = _resolve_services(req)
    session = await active_chat_service.start_session()
    metadata: dict[str, Any] = {}
    try:
        app_id, _app_config = _apply_app_config(req, metadata, str(x_app_id or "").strip())
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    payload = dict(session or {})
    if app_id:
        payload["app_id"] = app_id
    return payload

@router.post("/query")
@router.post("/chat")
async def query_tag(
    request: ChatRequest,
    req: Request = None,
    x_user_context: Annotated[Optional[str], Header()] = None,
    x_app_id: Annotated[Optional[str], Header()] = None,
    x_trace_id: Annotated[Optional[str], Header()] = None,
    x_response_format: Annotated[Optional[str], Header()] = None,
    stream: bool = True,
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    If user_name is missing or invalid, attempts to fetch it from DB.
    Set `stream=false` to return a single buffered JSON payload for easier
    inspection in browser developer tools.
    """
    active_chat_service, active_user_service = _resolve_services(req)

    if request.metadata is None:
        request.metadata = {}
    endpoint_started_at = time.perf_counter()
    trace_id = str(x_trace_id or request.metadata.get("trace_id") or uuid.uuid4().hex).strip()
    request.metadata["trace_id"] = trace_id
    if x_response_format is not None and str(x_response_format).strip():
        request.metadata["response_format"] = str(x_response_format).strip().lower()

    # Base64 Context Decoding
    if x_user_context:
        try:
            context_data = _decode_user_context(x_user_context)
            
            # Inject into request (sanitize embedded quotes from widget encoding)
            raw_uid = context_data.get("user_id") or context_data.get("userId")
            if raw_uid is not None:
                request.user_id = str(raw_uid).strip().strip('"').strip("'").strip()
            if "user_role" in context_data:
                request.user_role = str(context_data["user_role"]).strip().strip('"').strip("'").strip()
            elif "userRole" in context_data:
                request.user_role = str(context_data["userRole"]).strip().strip('"').strip("'").strip()
            if "user_name" in context_data:
                request.metadata["user_name"] = context_data["user_name"]
            if "company_name" in context_data:
                request.metadata["company_name"] = context_data["company_name"]
            company_id = (
                context_data.get("company_id")
                or context_data.get("companyId")
                or (context_data.get("company", {}) or {}).get("id")
            )
            if company_id is not None and str(company_id).strip():
                # Sanitize: strip embedded quotes (widget may double-quote values)
                clean_id = str(company_id).strip().strip('"').strip("'").strip()
                if clean_id.isdigit():
                    request.metadata["company_id"] = int(clean_id)
                else:
                    request.metadata["company_id"] = clean_id
                
            # Merge into metadata, but remove keys we've already sanitized
            for _key in ("company_id", "companyId", "user_id", "userId"):
                context_data.pop(_key, None)
            request.metadata.update(context_data)
            
        except Exception as e:
            logger.error(f"Failed to decode x-user-context: {e}")
            # We don't fail the request, just log and ignore invalid context

    try:
        requested_app_id = _requested_app_id(request.metadata, x_app_id=x_app_id)
        login_from = str(request.metadata.get("login_from") or "").strip()
        _apply_app_config(req, request.metadata, requested_app_id, login_from=login_from)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    resolved_user_id = _clean_text(
        request.user_id
        or request.metadata.get("user_id")
        or request.metadata.get("userId")
    )
    if resolved_user_id:
        request.user_id = resolved_user_id

    # Auto-Fetch User Name when missing or clearly invalid.
    if request.user_id and not _has_usable_user_name(request.metadata):
        request.metadata.pop("user_name", None)
        logger.info(f"User name missing/invalid for {request.user_id}, fetching from DB...")
        user_lookup_started_at = time.perf_counter()
        with _domain_context(request.metadata.get("domain_name")):
            user_info = active_user_service.get_user_info(
                request.user_id,
                db_url=request.metadata.get("db_connection_string"),
            )
        request.metadata["_user_lookup_ms"] = round((time.perf_counter() - user_lookup_started_at) * 1000, 2)
        resolved_name = _clean_text((user_info or {}).get("user_name"))
        if resolved_name:
            request.metadata["user_name"] = resolved_name
            logger.info(f"Resolved User Name: {resolved_name}")

    # Authoritative company name from the DB by company_id (overrides whatever
    # the client passed, which can be stale/cross-tenant). Falls back silently.
    company_id_for_lookup = _clean_text(
        request.metadata.get("company_id") or request.metadata.get("companyId")
    )
    if company_id_for_lookup:
        with _domain_context(request.metadata.get("domain_name")):
            db_company_name = active_user_service.get_company_name(
                company_id_for_lookup,
                db_url=request.metadata.get("db_connection_string"),
            )
        if db_company_name:
            request.metadata["company_name"] = db_company_name
            request.metadata.pop("companyName", None)

    async def safe_stream():
        try:
            with _domain_context(request.metadata.get("domain_name")):
                async for chunk in active_chat_service.generate_chat_stream(request):
                    yield chunk
        except asyncio.CancelledError:
            logger.info("Client disconnected during chat stream")
            return
        except Exception as exc:
            logger.exception("Unhandled streaming error: %s", exc)
            error_message = "Internal streaming error"
            yield json.dumps({"type": "error", "message": error_message}) + "\n"
            yield json.dumps(
                _build_terminal_error_result(
                    active_chat_service,
                    request.session_id,
                    error_message,
                    trace_id,
                )
            ) + "\n"

    request.metadata["_endpoint_pre_stream_ms"] = round((time.perf_counter() - endpoint_started_at) * 1000, 2)

    response_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
    }

    async def traced_stream():
        terminal: Optional[dict[str, Any]] = None
        async for chunk in safe_stream():
            payload = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            for line in payload.splitlines():
                line = line.strip()
                if not line or '"result"' not in line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if isinstance(parsed, dict) and parsed.get("type") == "result":
                    terminal = parsed
            yield chunk
        _record_chat_trace(req, request, terminal, trace_id, endpoint_started_at)

    if stream:
        return StreamingResponse(
            traced_stream(),
            media_type="application/x-ndjson",
            headers=response_headers,
        )

    terminal_event: Optional[dict[str, Any]] = None
    async for chunk in safe_stream():
        payload = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "result":
                terminal_event = parsed

    if terminal_event is None:
        terminal_event = _build_terminal_error_result(
            active_chat_service,
            request.session_id,
            "No terminal result produced",
            trace_id,
        )

    _record_chat_trace(req, request, terminal_event, trace_id, endpoint_started_at)
    return JSONResponse(content=terminal_event, headers=response_headers)
