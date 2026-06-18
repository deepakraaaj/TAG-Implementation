"""Admin dashboard API.

Aggregates everything an operator needs to monitor and inspect the chatbot
across all configured applications:

* system + per-app status (health, DB reachability, domain load, cache, LLM)
* Prometheus metrics parsed into JSON
* per-app DB column metadata and the "when to use what" explanations
* the prompt material (templates, special queries, few-shot examples, knowledge)
* recent request traces (route / intent / SQL / timings / errors)
* a live test console that runs a query through the real pipeline
* edit endpoints that write metadata / prompt edits back to the domain configs

All routes require the static ``ADMIN_API_TOKEN`` (sent as ``Authorization:
Bearer <token>`` or ``X-Admin-Token``). When no token is configured the whole
surface fails closed.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from app.config import get_settings
from app.core.dependencies import get_container
from app.domains.registry import DomainRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> bool:
    settings = get_settings()
    expected = str(getattr(settings, "ADMIN_API_TOKEN", "") or "").strip()
    if not expected or not bool(getattr(settings, "ADMIN_DASHBOARD_ENABLED", True)):
        raise HTTPException(status_code=503, detail="Admin dashboard is disabled.")

    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    elif x_admin_token:
        presented = x_admin_token.strip()

    if presented != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token.")
    return True


def _container(req: Request) -> Any:
    return getattr(req.app.state, "container", None) or get_container()


# ---------------------------------------------------------------------------
# Domain file helpers
# ---------------------------------------------------------------------------

_MANUAL_FILES = (
    "semantics.json",
    "glossary.json",
    "sql_builder.json",
    "few_shot_examples.json",
    "domain_knowledge.json",
    "entity_behavior.json",
)

# Files the admin UI is permitted to edit, mapped to a path relative to the
# domain directory. Keeps writes inside the domain sandbox.
_EDITABLE_FILES = {
    "developer_clarifications": "developer_clarifications.json",
    "semantics": "manual/semantics.json",
    "glossary": "manual/glossary.json",
    "sql_builder": "manual/sql_builder.json",
    "few_shot_examples": "manual/few_shot_examples.json",
    "domain_knowledge": "manual/domain_knowledge.json",
}


def _domain_dir(domain_name: str) -> Path:
    root = DomainRegistry._domains_root()
    return root / str(domain_name or "").strip()


def _read_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read %s: %s", path, exc)
    return None


def _write_json_with_backup(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + f".bak-{int(time.time())}")
        try:
            shutil.copy2(path, backup)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not back up %s: %s", path, exc)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _app_lookup(container: Any) -> Dict[str, Any]:
    registry = getattr(container, "app_registry", None)
    if registry is None or not callable(getattr(registry, "enabled", None)) or not registry.enabled():
        return {}
    return {app_id: cfg for app_id, cfg in registry.list_apps()}


def _require_app(container: Any, app_id: str):
    apps = _app_lookup(container)
    cfg = apps.get(app_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown app_id: {app_id}")
    return cfg


def _app_metadata(cfg: Any) -> Dict[str, Any]:
    """Build a metadata dict suitable for driving the pipeline for this app."""
    meta = dict(getattr(cfg, "default_metadata", {}) or {})
    meta.setdefault("db_connection_string", getattr(cfg, "database_url", ""))
    meta.setdefault("domain_name", getattr(cfg, "domain_name", "") or "")
    if getattr(cfg, "allowed_tables", None):
        meta.setdefault("allowed_tables", list(cfg.allowed_tables))
    if getattr(cfg, "protected_tables", None):
        meta.setdefault("protected_tables", list(cfg.protected_tables))
    meta.setdefault("allow_mutations", bool(getattr(cfg, "allow_mutations", False)))
    meta.setdefault("require_select_where", bool(getattr(cfg, "require_select_where", True)))
    return meta


# ---------------------------------------------------------------------------
# Status / overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def overview(req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    settings = get_settings()

    readiness: Dict[str, Any] = {}
    snapshot = getattr(container, "readiness_snapshot", None)
    if callable(snapshot):
        try:
            readiness = await snapshot()
        except Exception as exc:  # pragma: no cover - defensive
            readiness = {"status": "error", "detail": str(exc)}

    db_snapshot = {}
    if callable(getattr(container, "app_database_snapshot", None)):
        try:
            db_snapshot = container.app_database_snapshot()
        except Exception as exc:
            db_snapshot = {"enabled": False, "error": str(exc)}

    domain_snapshot = {}
    if callable(getattr(container, "domain_registry_snapshot", None)):
        try:
            domain_snapshot = container.domain_registry_snapshot()
        except Exception as exc:
            domain_snapshot = {"enabled": False, "error": str(exc)}

    trace_store = getattr(container, "trace_store", None)
    trace_stats = trace_store.stats() if trace_store is not None else {}

    apps = _app_lookup(container)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "env": settings.APP_ENV,
        "app_count": len(apps),
        "readiness": readiness,
        "databases": db_snapshot,
        "domains": domain_snapshot,
        "traces": trace_stats,
    }


@router.get("/apps")
async def list_apps(req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    db_snapshot = {}
    if callable(getattr(container, "app_database_snapshot", None)):
        try:
            db_snapshot = container.app_database_snapshot().get("apps", {})
        except Exception:
            db_snapshot = {}

    apps: List[Dict[str, Any]] = []
    for app_id, cfg in _app_lookup(container).items():
        db_info = db_snapshot.get(app_id, {}) if isinstance(db_snapshot, dict) else {}
        apps.append(
            {
                "app_id": app_id,
                "display_name": getattr(cfg, "display_name", None) or app_id,
                "description": getattr(cfg, "description", "") or "",
                "domain_name": getattr(cfg, "domain_name", "") or app_id,
                "allow_mutations": bool(getattr(cfg, "allow_mutations", False)),
                "require_select_where": bool(getattr(cfg, "require_select_where", True)),
                "allowed_tables": list(getattr(cfg, "allowed_tables", []) or []),
                "protected_tables": list(getattr(cfg, "protected_tables", []) or []),
                "db_target": db_info.get("target"),
                "db_reachable": db_info.get("reachable"),
            }
        )
    apps.sort(key=lambda a: a["app_id"])
    return {"apps": apps}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def metrics(req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    metrics_service = getattr(container, "metrics_service", None)
    if metrics_service is None:
        return {"families": []}
    try:
        raw = metrics_service.get_metrics()
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception as exc:
        return {"families": [], "error": str(exc)}

    families: List[Dict[str, Any]] = []
    try:
        from prometheus_client.parser import text_string_to_metric_families

        for family in text_string_to_metric_families(text):
            samples = [
                {"name": s.name, "labels": dict(s.labels), "value": s.value}
                for s in family.samples
            ]
            if samples:
                families.append(
                    {
                        "name": family.name,
                        "type": family.type,
                        "documentation": family.documentation,
                        "samples": samples,
                    }
                )
    except Exception as exc:  # pragma: no cover - defensive
        return {"families": [], "raw": text, "error": str(exc)}
    return {"families": families}


# ---------------------------------------------------------------------------
# Per-app metadata (column descriptions + "when to use what")
# ---------------------------------------------------------------------------

@router.get("/apps/{app_id}/metadata")
async def app_metadata(app_id: str, req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    cfg = _require_app(container, app_id)
    domain_name = getattr(cfg, "domain_name", "") or app_id
    ddir = _domain_dir(domain_name)

    developer = _read_json(ddir / "developer_clarifications.json") or {}
    semantics = _read_json(ddir / "manual" / "semantics.json") or {}
    glossary = _read_json(ddir / "manual" / "glossary.json") or {}

    # Manifest tables/columns via the domain registry (best-effort).
    tables: Dict[str, Any] = {}
    try:
        with DomainRegistry.use_domain(domain_name):
            domain = DomainRegistry.get_current_domain()
            manifest = domain.manifest if isinstance(domain.manifest, dict) else {}
            raw_tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
            for tname, tinfo in raw_tables.items():
                cols = tinfo.get("columns") if isinstance(tinfo, dict) else None
                tables[tname] = {
                    "columns": list(cols.keys()) if isinstance(cols, dict) else (cols or []),
                }
    except Exception as exc:
        logger.debug("manifest unavailable for %s: %s", domain_name, exc)

    return {
        "app_id": app_id,
        "domain_name": domain_name,
        "column_descriptions": developer.get("column_descriptions", {}),
        "column_overrides": developer.get("column_overrides", {}),
        "enum_values": developer.get("enum_values", {}),
        "business_terms": developer.get("extra_business_terms", {}),
        "include_tables": developer.get("include_tables", []),
        "exclude_tables": developer.get("exclude_tables", []),
        "column_logic": semantics.get("column_logic", {}),
        "join_hints": semantics.get("join_hints", {}),
        "glossary": glossary,
        "manifest_tables": tables,
    }


# ---------------------------------------------------------------------------
# Per-app prompts
# ---------------------------------------------------------------------------

_ROUTER_PROMPT_TEMPLATE = """\
Classify the user message and, if it is a data request, extract its query intent in the same step.
Return only JSON with keys:
route: SQL|CHAT|REPORT
operation: select|insert|update|delete
table: db table name or empty string
filters: object
fields: object
Use SQL for data lookups/mutations, REPORT for report/export requests, CHAT otherwise.
[+ recent conversation, last table context, and field-extraction guidance when present]
User: <query>"""

_INTENT_PROMPT_TEMPLATE = """\
Return ONLY JSON with keys:
operation: select|insert|update|delete
table: db table name or empty string
filters: object
fields: object
[+ current/last-table context, recent conversation, field-extraction guidance]
User query: <query>"""


@router.get("/apps/{app_id}/prompts")
async def app_prompts(app_id: str, req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    cfg = _require_app(container, app_id)
    domain_name = getattr(cfg, "domain_name", "") or app_id
    ddir = _domain_dir(domain_name)

    sql_builder = _read_json(ddir / "manual" / "sql_builder.json") or {}
    few_shot = _read_json(ddir / "manual" / "few_shot_examples.json") or []
    knowledge = _read_json(ddir / "manual" / "domain_knowledge.json") or {}

    return {
        "app_id": app_id,
        "domain_name": domain_name,
        "templates": {
            "router_and_intent": _ROUTER_PROMPT_TEMPLATE,
            "intent_fallback": _INTENT_PROMPT_TEMPLATE,
        },
        "special_queries": sql_builder.get("special_queries", {}),
        "few_shot_examples": few_shot,
        "domain_knowledge": knowledge,
    }


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

@router.get("/traces")
async def list_traces(
    req: Request,
    app_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100),
    _auth: bool = Depends(require_admin),
) -> Dict[str, Any]:
    store = getattr(_container(req), "trace_store", None)
    if store is None:
        return {"traces": [], "stats": {}}
    return {"traces": store.list(limit=limit, app_id=app_id), "stats": store.stats()}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    store = getattr(_container(req), "trace_store", None)
    item = store.get(trace_id) if store is not None else None
    if item is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return item


@router.post("/traces/clear")
async def clear_traces(req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    store = getattr(_container(req), "trace_store", None)
    if store is not None:
        store.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Live console: run a query through the real pipeline
# ---------------------------------------------------------------------------

@router.post("/apps/{app_id}/console")
async def console(
    app_id: str,
    req: Request,
    payload: Dict[str, Any] = Body(...),
    _auth: bool = Depends(require_admin),
) -> Dict[str, Any]:
    container = _container(req)
    cfg = _require_app(container, app_id)
    query = str(payload.get("query", "") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    workflow = container.get_workflow() if callable(getattr(container, "get_workflow", None)) else None
    if workflow is None:
        raise HTTPException(status_code=503, detail="Workflow is not ready")

    from langchain_core.messages import HumanMessage

    meta = _app_metadata(cfg)
    if isinstance(payload.get("metadata"), dict):
        meta.update(payload["metadata"])

    domain_name = getattr(cfg, "domain_name", "") or app_id
    started = time.perf_counter()
    state = {"messages": [HumanMessage(content=query)], "metadata": meta}
    try:
        with DomainRegistry.use_domain(domain_name):
            final = await workflow.ainvoke(state)
    except Exception as exc:
        logger.exception("Console run failed for app=%s", app_id)
        return {"app_id": app_id, "query": query, "error": str(exc)}

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    messages = final.get("messages") or []
    answer = str(messages[-1].content) if messages else ""
    result = {
        "app_id": app_id,
        "query": query,
        "route": final.get("route"),
        "intent": final.get("intent"),
        "sql_query": final.get("sql_query"),
        "row_count": final.get("row_count"),
        "rows_preview": final.get("rows_preview"),
        "answer": answer,
        "error": final.get("error"),
        "elapsed_ms": elapsed_ms,
        "token_usage": final.get("token_usage"),
    }

    store = getattr(container, "trace_store", None)
    if store is not None:
        store.record(
            {
                "app_id": app_id,
                "source": "console",
                "query": query,
                "route": final.get("route"),
                "sql_query": final.get("sql_query"),
                "row_count": final.get("row_count"),
                "error": final.get("error"),
                "elapsed_ms": elapsed_ms,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Editing metadata / prompt files
# ---------------------------------------------------------------------------

@router.get("/apps/{app_id}/files/{file_key}")
async def get_file(app_id: str, file_key: str, req: Request, _auth: bool = Depends(require_admin)) -> Dict[str, Any]:
    container = _container(req)
    cfg = _require_app(container, app_id)
    rel = _EDITABLE_FILES.get(file_key)
    if rel is None:
        raise HTTPException(status_code=400, detail=f"Unknown or non-editable file: {file_key}")
    domain_name = getattr(cfg, "domain_name", "") or app_id
    path = _domain_dir(domain_name) / rel
    return {
        "app_id": app_id,
        "file_key": file_key,
        "relative_path": rel,
        "exists": path.exists(),
        "content": _read_json(path),
    }


@router.put("/apps/{app_id}/files/{file_key}")
async def put_file(
    app_id: str,
    file_key: str,
    req: Request,
    payload: Dict[str, Any] = Body(...),
    _auth: bool = Depends(require_admin),
) -> Dict[str, Any]:
    container = _container(req)
    cfg = _require_app(container, app_id)
    rel = _EDITABLE_FILES.get(file_key)
    if rel is None:
        raise HTTPException(status_code=400, detail=f"Unknown or non-editable file: {file_key}")
    if "content" not in payload:
        raise HTTPException(status_code=400, detail="Body must contain a 'content' field.")

    content = payload["content"]
    # Accept either a parsed object or a raw JSON string.
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    domain_name = getattr(cfg, "domain_name", "") or app_id
    path = _domain_dir(domain_name) / rel
    try:
        _write_json_with_backup(path, content)
    except Exception as exc:
        logger.exception("Failed to write %s", path)
        raise HTTPException(status_code=500, detail=f"Write failed: {exc}")

    # Drop cached domain config so the next request reloads the edited file.
    try:
        DomainRegistry.reset_cache(domain_name)
    except Exception:
        logger.debug("Could not reset domain cache; edit applies on restart")

    return {"ok": True, "app_id": app_id, "file_key": file_key, "relative_path": rel}


# ---------------------------------------------------------------------------
# AI config assistant
# ---------------------------------------------------------------------------

# Human-readable shape of each editable file, fed to the model so it knows
# exactly where a given piece of guidance belongs.
_FILE_SCHEMAS = {
    "developer_clarifications": (
        "Object. Keys: column_descriptions {table:{column:description}}, "
        "column_overrides {table:{date_columns:[...],priority_column,status_column}}, "
        "enum_values {column:{code:label}}, extra_business_terms {term:meaning}, "
        "include_tables [..], exclude_tables [..]."
    ),
    "semantics": (
        "Object. Keys: join_hints {name:\"a.x = b.y\"}, "
        "column_logic {column:\"when/how to use this column\"}."
    ),
    "glossary": "Object mapping a synonym (lowercase word/phrase) to its canonical entity/table name.",
    "sql_builder": (
        "Object with key special_queries: a list of {id, description, example_queries:[..], "
        "all_patterns:[regex], any_patterns:[regex], required_tables:[..], tenant_table, "
        "tenant_required, tenant_required_message}."
    ),
    "few_shot_examples": (
        "List of {question, sql, intent:{operation:select|insert|update|delete, table}}. "
        "SQL must only reference tables/columns that exist in the manifest."
    ),
    "domain_knowledge": (
        "Object. Keys: scope (string), primary_entities [..], "
        "critical_join_rules {name:\"JOIN guidance\"}, plus any free-form guidance keys."
    ),
}


def _manifest_columns(domain_name: str) -> Dict[str, List[str]]:
    """Best-effort {table: [columns]} from the live domain manifest."""
    tables: Dict[str, List[str]] = {}
    try:
        with DomainRegistry.use_domain(domain_name):
            domain = DomainRegistry.get_current_domain()
            manifest = domain.manifest if isinstance(domain.manifest, dict) else {}
            raw_tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
            for tname, tinfo in raw_tables.items():
                cols = tinfo.get("columns") if isinstance(tinfo, dict) else None
                tables[tname] = list(cols.keys()) if isinstance(cols, dict) else (list(cols) if cols else [])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("manifest unavailable for %s: %s", domain_name, exc)
    return tables


def _deep_merge(current: Any, patch: Any) -> Any:
    """Additive merge: dicts merge key-by-key, lists append unseen items, scalars are replaced."""
    if isinstance(current, dict) and isinstance(patch, dict):
        out = dict(current)
        for k, v in patch.items():
            out[k] = _deep_merge(current.get(k), v) if k in current else v
        return out
    if isinstance(current, list) and isinstance(patch, list):
        out = list(current)
        for item in patch:
            if item not in out:
                out.append(item)
        return out
    return patch


def _validate_columns(file_key: str, patch: Any, manifest: Dict[str, List[str]]) -> List[str]:
    """Flag (do not block) column keys that don't exist in the live manifest."""
    warnings: List[str] = []
    if not manifest:
        return warnings
    known = {t: set(cols) for t, cols in manifest.items()}
    if file_key == "developer_clarifications" and isinstance(patch, dict):
        for section in ("column_descriptions", "column_overrides"):
            block = patch.get(section)
            if isinstance(block, dict):
                for table, cols in block.items():
                    if table not in known:
                        warnings.append(f"{section}: unknown table '{table}'")
                        continue
                    if section == "column_descriptions" and isinstance(cols, dict):
                        for col in cols:
                            if col not in known[table]:
                                warnings.append(f"column_descriptions.{table}: unknown column '{col}'")
    return warnings


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


@router.post("/apps/{app_id}/config/assist")
async def config_assist(
    app_id: str,
    req: Request,
    payload: Dict[str, Any] = Body(...),
    _auth: bool = Depends(require_admin),
) -> Dict[str, Any]:
    """Turn a plain-English requirement into a proposed (merged) config patch.

    Does NOT write anything. Returns current + proposed content per affected
    file so the dashboard can show a diff and ask for confirmation.
    """
    container = _container(req)
    cfg = _require_app(container, app_id)
    instruction = str(payload.get("instruction", "") or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")

    if not callable(getattr(container, "new_llm", None)):
        raise HTTPException(status_code=503, detail="LLM client is not available")

    domain_name = getattr(cfg, "domain_name", "") or app_id
    ddir = _domain_dir(domain_name)
    current: Dict[str, Any] = {
        key: (_read_json(ddir / rel) or ({} if key != "few_shot_examples" else []))
        for key, rel in _EDITABLE_FILES.items()
    }
    manifest = _manifest_columns(domain_name)

    schema_lines = "\n".join(f"- {k}: {v}" for k, v in _FILE_SCHEMAS.items())
    manifest_str = json.dumps(manifest, ensure_ascii=False)[:8000]
    system = (
        "You configure a natural-language-to-SQL chatbot by editing JSON config files.\n"
        "You are given the live database schema and the current config. From the operator's\n"
        "request, produce ONLY the additions/changes needed.\n\n"
        "Editable files and their shapes:\n" + schema_lines + "\n\n"
        "Rules:\n"
        "- Output a single JSON object mapping file_key -> a PARTIAL patch for that file.\n"
        "- Include ONLY files you actually change. A patch is merged additively into the\n"
        "  existing file (dict keys merged, list items appended), so send only new/changed parts.\n"
        "- Only reference tables/columns that exist in the schema. Never invent columns.\n"
        "- Return JSON only. No prose, no markdown fences."
    )
    user = (
        f"DATABASE SCHEMA (table -> columns):\n{manifest_str}\n\n"
        f"CURRENT CONFIG:\n{json.dumps(current, ensure_ascii=False)[:12000]}\n\n"
        f"OPERATOR REQUEST:\n{instruction}"
    )

    from langchain_core.messages import HumanMessage, SystemMessage

    llm = container.new_llm(0.0)
    try:
        resp = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    except Exception as exc:
        logger.exception("Config assist LLM call failed for app=%s", app_id)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    raw = _strip_json_fence(str(getattr(resp, "content", "") or ""))
    try:
        patch_map = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Model did not return valid JSON: {exc}")
    if not isinstance(patch_map, dict):
        raise HTTPException(status_code=502, detail="Model output was not a JSON object")

    changes: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for file_key, patch in patch_map.items():
        rel = _EDITABLE_FILES.get(file_key)
        if rel is None:
            warnings.append(f"Ignored unknown file '{file_key}'")
            continue
        warnings.extend(_validate_columns(file_key, patch, manifest))
        proposed = _deep_merge(current.get(file_key), patch)
        changes.append(
            {
                "file_key": file_key,
                "relative_path": rel,
                "current": current.get(file_key),
                "patch": patch,
                "proposed": proposed,
            }
        )

    return {
        "app_id": app_id,
        "domain_name": domain_name,
        "instruction": instruction,
        "changes": changes,
        "warnings": warnings,
    }
