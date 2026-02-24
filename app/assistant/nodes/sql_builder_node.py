from typing import Dict, Any, List, Tuple
import asyncio
import re
import logging

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp
from sqlalchemy import text

logger = logging.getLogger(__name__)


class _NullCatalog:
    @staticmethod
    def important_columns(_table: str) -> set[str]:
        return set()

    @staticmethod
    def table_names() -> set[str]:
        return set()

    @staticmethod
    def create_enabled(_table: str) -> bool:
        return False

    @staticmethod
    def required_create_fields(_table: str) -> List[str]:
        return []


class _NullSQLBuilder:
    def __init__(self):
        self.catalog = _NullCatalog()

    @staticmethod
    def parse_kv_pairs(_text: str) -> Dict[str, str]:
        return {}

    @staticmethod
    def resolve_table(_query: str, _intent: Dict[str, Any]) -> str:
        return ""

    async def build_select(self, _query: str, _table: str, _tenant_value: Any) -> str:
        return ""

    @staticmethod
    def build_select_from_filters(_table: str, _filters: Dict[str, Any], _tenant_value: Any) -> Tuple[str, str]:
        return "", "SQL builder is not configured."

    @staticmethod
    def build_insert(
        _table: str,
        _fields: Dict[str, Any],
        _tenant_value: Any,
        actor_user_id: Any = None,
    ) -> Tuple[str, str]:
        return "", "SQL builder is not configured."

    @staticmethod
    def build_update(
        _table: str,
        _fields: Dict[str, Any],
        _tenant_value: Any,
        actor_user_id: Any = None,
    ) -> Tuple[str, str]:
        return "", "SQL builder is not configured."


class _NullIntentDetector:
    @staticmethod
    async def detect_intent(_query: str, _metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    @staticmethod
    def fallback_intent(_query: str) -> Dict[str, Any]:
        return {}


class _NullResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _NullConnection:
    def execute(self, *_args, **_kwargs):
        return _NullResult()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class _NullEngine:
    @staticmethod
    def connect():
        return _NullConnection()


class _NullSchema:
    @staticmethod
    def get_engine_for_url(_db_url: str):
        return _NullEngine()


class SQLBuilderNode:
    _domain_provider = None
    _kv_parser = None
    _sql_builder_factory = None
    _intent_detector_factory = None
    _schema_factory = None

    def __init__(
        self,
        sql_builder: Any | None = None,
        intent_detector: Any | None = None,
        schema: Any | None = None,
        domain_provider=None,
        kv_parser=None,
    ):
        self.sql_builder = sql_builder if sql_builder is not None else self._new_sql_builder()
        self.intent_detector = intent_detector if intent_detector is not None else self._new_intent_detector()
        self.schema = schema if schema is not None else self._new_schema()
        if domain_provider is not None:
            self.__class__.set_domain_provider(domain_provider)
        if kv_parser is not None:
            self.__class__.set_kv_parser(kv_parser)

    @property
    def builder(self):
        return self.sql_builder

    @builder.setter
    def builder(self, value):
        self.sql_builder = value

    @classmethod
    def set_domain_provider(cls, provider) -> None:
        cls._domain_provider = provider

    @classmethod
    def set_kv_parser(cls, parser) -> None:
        cls._kv_parser = parser

    @classmethod
    def configure_adapters(
        cls,
        *,
        sql_builder_factory=None,
        intent_detector_factory=None,
        schema_factory=None,
        domain_provider=None,
        kv_parser=None,
    ) -> None:
        if sql_builder_factory is not None:
            cls._sql_builder_factory = sql_builder_factory
        if intent_detector_factory is not None:
            cls._intent_detector_factory = intent_detector_factory
        if schema_factory is not None:
            cls._schema_factory = schema_factory
        if domain_provider is not None:
            cls._domain_provider = domain_provider
        if kv_parser is not None:
            cls._kv_parser = kv_parser

    @classmethod
    def _new_sql_builder(cls):
        factory = cls._sql_builder_factory
        if callable(factory):
            return factory()
        return _NullSQLBuilder()

    @classmethod
    def _new_intent_detector(cls):
        factory = cls._intent_detector_factory
        if callable(factory):
            return factory()
        return _NullIntentDetector()

    @classmethod
    def _new_schema(cls):
        factory = cls._schema_factory
        if callable(factory):
            return factory()
        return _NullSchema()

    @staticmethod
    def _default_kv_parser(text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not text:
            return out
        for pattern in [
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s+is\s+([^,;]+)",
        ]:
            for key, value in re.findall(pattern, text, flags=re.IGNORECASE):
                out[str(key).strip()] = str(value).strip().strip("'\"")
        return out

    @classmethod
    def _parse_kv_pairs(cls, query: str) -> Dict[str, Any]:
        parser = cls._kv_parser
        if callable(parser):
            try:
                payload = parser(query)
                return dict(payload) if isinstance(payload, dict) else {}
            except Exception:
                return {}
        return cls._default_kv_parser(query)

    @staticmethod
    def _default_domain_provider():
        try:
            from app.domains.registry import DomainRegistry

            return DomainRegistry.get_current_domain()
        except Exception:
            return None

    @classmethod
    def _current_domain(cls):
        provider = cls._domain_provider
        if callable(provider):
            try:
                return provider()
            except Exception:
                pass
        return cls._default_domain_provider()

    @classmethod
    def _domain_dict(cls, getter_name: str, *args: Any) -> Dict[str, Any]:
        domain = cls._current_domain()
        getter = getattr(domain, getter_name, None)
        if callable(getter):
            try:
                payload = getter(*args)
                return payload if isinstance(payload, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _entity_behavior_config(cls) -> Dict[str, Any]:
        return cls._domain_dict("get_entity_behavior_config")

    @classmethod
    def _primary_table(cls) -> str:
        cfg = cls._entity_behavior_config()
        table = str(cfg.get("primary_table", "")).strip()
        if table:
            return table
        domain = cls._current_domain()
        manifest = dict(getattr(domain, "manifest", {}) or {})
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        if isinstance(tables, dict) and tables:
            return str(next(iter(tables.keys())))
        return "entity"

    @classmethod
    def _primary_keywords(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        keywords = [str(item).strip().lower() for item in (cfg.get("primary_keywords") or []) if str(item).strip()]
        return keywords or ["record", "records", "item", "items", "entry", "entries"]

    @classmethod
    def _primary_filter_keys(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        keys = {str(item).strip().lower() for item in (cfg.get("primary_filter_keys") or []) if str(item).strip()}
        if keys:
            return keys
        return {
            "date",
            "status",
            "priority",
            "user_id",
            "assigned_to",
            "assignee",
            "location_name",
            "location_id",
        }

    @classmethod
    def _user_filter_keys(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        keys = {str(item).strip().lower() for item in (cfg.get("user_filter_keys") or []) if str(item).strip()}
        return keys or {"user_id", "assignee", "user", "assigned_to"}

    @classmethod
    def _self_aliases(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        aliases = {str(item).strip().lower() for item in (cfg.get("self_aliases") or []) if str(item).strip()}
        return aliases or {"my", "mine", "myself", "me"}

    @classmethod
    def _all_users_aliases(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        aliases = [str(item).strip().lower() for item in (cfg.get("all_users_aliases") or []) if str(item).strip()]
        return aliases or ["all users", "all assignees", "for everyone", "everyone"]

    @classmethod
    def _default_entity_prompt(cls) -> str:
        cfg = cls._entity_behavior_config()
        msg = str(cfg.get("default_entity_prompt", "")).strip()
        return msg or "Please mention a table or entity."

    @classmethod
    def _filter_context_prompt(cls) -> str:
        cfg = cls._entity_behavior_config()
        msg = str(cfg.get("filter_context_prompt", "")).strip()
        return (
            msg
            or "I need context for that filter input. Please start with a table/entity and then apply filters."
        )

    @classmethod
    def _intent_mode(cls) -> str:
        cfg = cls._entity_behavior_config()
        mode = str(cfg.get("intent_mode", "")).strip().lower()
        if mode in {"llm", "heuristic", "auto"}:
            return mode
        return "auto"

    @classmethod
    def _primary_label(cls) -> str:
        cfg = cls._entity_behavior_config()
        label = str(cfg.get("primary_label", "")).strip()
        if label:
            return label
        return cls._primary_table().replace("_", " ")

    @classmethod
    def _date_filter_keys(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        keys = [str(item).strip() for item in (cfg.get("date_filter_keys") or []) if str(item).strip()]
        return keys or ["date"]

    @classmethod
    def _date_filter_key(cls) -> str:
        keys = cls._date_filter_keys()
        return keys[0] if keys else "scheduled_date"

    @classmethod
    def _status_filter_key(cls) -> str:
        cfg = cls._entity_behavior_config()
        key = str(cfg.get("status_filter_key", "")).strip()
        return key or "status"

    @classmethod
    def _priority_filter_key(cls) -> str:
        cfg = cls._entity_behavior_config()
        key = str(cfg.get("priority_filter_key", "")).strip()
        return key or "priority"

    @classmethod
    def _date_phrase_map(cls) -> Dict[str, str]:
        cfg = cls._entity_behavior_config()
        payload = cfg.get("date_phrase_map")
        if isinstance(payload, dict) and payload:
            out: Dict[str, str] = {}
            for phrase, value in payload.items():
                p = str(phrase or "").strip().lower()
                v = str(value or "").strip()
                if p and v:
                    out[p] = v
            if out:
                return out
        return {"today": "today", "yesterday": "yesterday"}

    @classmethod
    def _status_phrase_map(cls) -> Dict[str, str]:
        cfg = cls._entity_behavior_config()
        payload = cfg.get("status_phrase_map")
        if isinstance(payload, dict) and payload:
            out: Dict[str, str] = {}
            for phrase, value in payload.items():
                p = str(phrase or "").strip().lower()
                v = str(value or "").strip()
                if p and v:
                    out[p] = v
            if out:
                return out
        return {
            "pending": "Pending",
            "in progress": "In Progress",
            "in_progress": "In Progress",
            "completed": "Completed",
            "overdue": "Overdue",
            "over due": "Overdue",
        }

    @classmethod
    def _primary_menu_filters(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        keys = [str(item).strip() for item in (cfg.get("primary_menu_filters") or []) if str(item).strip()]
        if keys:
            return keys
        return [
            cls._date_filter_key(),
            cls._status_filter_key(),
            cls._user_id_filter_key(),
            cls._priority_filter_key(),
        ]

    @classmethod
    def _primary_menu_options(cls) -> List[Dict[str, str]]:
        cfg = cls._entity_behavior_config()
        configured = cfg.get("primary_menu_options")
        if isinstance(configured, list):
            normalized: List[Dict[str, str]] = []
            for item in configured:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "")).strip()
                value = str(item.get("value", "")).strip()
                if label and value:
                    normalized.append({"label": label, "value": value})
            if normalized:
                return normalized

        date_key = cls._date_filter_key()
        status_key = cls._status_filter_key()
        priority_key = cls._priority_filter_key()
        user_name_key = cls._user_name_filter_key()
        return [
            {"label": "Today", "value": f"{date_key}=today, {user_name_key}=current_user"},
            {"label": "Yesterday", "value": f"{date_key}=yesterday"},
            {"label": "Pick a date (YYYY-MM-DD)", "value": f"{date_key}="},
            {"label": "Different user / assignee", "value": f"{user_name_key}="},
            {"label": "Status", "value": f"{status_key}="},
            {"label": "Priority", "value": f"{priority_key}="},
        ]

    @classmethod
    def _primary_date_range_terms(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        terms = [str(item).strip().lower() for item in (cfg.get("date_range_terms") or []) if str(item).strip()]
        if terms:
            return terms
        return ["yesterday", "last week", "this week", "month", "range", "between"]

    @classmethod
    def _user_lookup_config(cls) -> Dict[str, Any]:
        return cls._domain_dict("get_user_lookup_config")

    @classmethod
    def _location_lookup_config(cls) -> Dict[str, Any]:
        return cls._domain_dict("get_config_section", "location_lookup")

    @classmethod
    def _select_workflow_config(cls) -> Dict[str, Any]:
        return cls._domain_dict("get_config_section", "select_workflow")

    @classmethod
    def _select_workflow_id(cls) -> str:
        cfg = cls._select_workflow_config()
        workflow_id = str(cfg.get("workflow_id", "")).strip()
        return workflow_id or "select_filters"

    @classmethod
    def _select_workflow_state(cls) -> str:
        cfg = cls._select_workflow_config()
        state = str(cfg.get("state", "")).strip()
        return state or "collect_filters"

    @classmethod
    def _select_workflow_mode(cls) -> str:
        cfg = cls._select_workflow_config()
        mode = str(cfg.get("mode", "")).strip().lower()
        return mode or "menu"

    @classmethod
    def _select_workflow_next_field(cls) -> str:
        cfg = cls._select_workflow_config()
        next_field = str(cfg.get("next_field", "")).strip()
        return next_field or "filters"

    @classmethod
    def _select_workflow_operation(cls) -> str:
        cfg = cls._select_workflow_config()
        operation = str(cfg.get("operation", "")).strip().lower()
        return operation or "select"

    @staticmethod
    def _safe_ident(name: str) -> str:
        candidate = str(name or "").strip()
        return candidate if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate) else ""

    def _table_meta(self, table: str) -> Dict[str, Any]:
        catalog = getattr(self.sql_builder, "catalog", None)
        resolver = getattr(catalog, "table_meta", None)
        if not callable(resolver):
            return {}
        try:
            payload = resolver(table)
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _tenant_scope(self, table: str) -> Dict[str, str]:
        allowed = self.sql_builder.catalog.important_columns(table) or set()
        meta = self._table_meta(table)
        payload = meta.get("tenant_scope") if isinstance(meta.get("tenant_scope"), dict) else {}
        configured_column = self._safe_ident(str(payload.get("column", "")).strip())
        if configured_column and configured_column not in allowed:
            configured_column = ""
        inferred_column = ""
        if not configured_column:
            for candidate in ("company_id", "tenant_id", "organization_id", "org_id", "account_id", "customer_id"):
                if candidate in allowed:
                    inferred_column = candidate
                    break
        column = configured_column or inferred_column
        metadata_key = self._safe_ident(str(payload.get("metadata_key", "")).strip()) or column
        return {"column": column, "metadata_key": metadata_key}

    def _tenant_columns(self, table: str) -> set[str]:
        column = str(self._tenant_scope(table).get("column", "")).strip().lower()
        return {column} if column else set()

    def _tenant_value(self, table: str, metadata: Dict[str, Any]) -> Any:
        meta = dict(metadata or {})
        scope = self._tenant_scope(table)
        candidates = [
            str(scope.get("metadata_key", "")).strip(),
            str(scope.get("column", "")).strip(),
            "company_id",
        ]
        for key in candidates:
            if not key:
                continue
            value = meta.get(key)
            if value is None:
                continue
            if str(value).strip() == "":
                continue
            return value
        return None

    def _system_columns(self, table: str) -> set[str]:
        excluded = {"id", "created_by", "updated_by", "date_created", "date_updated"}
        excluded.update(self._tenant_columns(table))
        return excluded

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value if value is not None else default)
        except Exception:
            parsed = default
        return max(minimum, min(parsed, maximum))

    @staticmethod
    def _metadata_value(metadata: Dict[str, Any], *keys: str) -> Any:
        meta = dict(metadata or {})
        for key in keys:
            if not key:
                continue
            value = meta.get(key)
            if value is None:
                continue
            if str(value).strip() == "":
                continue
            return value
        return None

    @staticmethod
    def _dedupe_text(values: List[str]) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for value in values:
            text_value = str(value or "").strip()
            if not text_value:
                continue
            lowered = text_value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(text_value)
        return out

    def _db_engine(self, metadata: Dict[str, Any]):
        return self.schema.get_engine_for_url((metadata or {}).get("db_connection_string"))

    def _lookup_tenant_value(self, metadata: Dict[str, Any], metadata_key: str, tenant_column: str) -> Any:
        return self._metadata_value(metadata, metadata_key, tenant_column, "company_id")

    def _query_rows(self, metadata: Dict[str, Any], query_sql: str, params: Dict[str, Any] | None = None):
        with self._db_engine(metadata).connect() as conn:
            return conn.execute(text(query_sql), params or {}).mappings().all()

    @classmethod
    def _location_filter_keys(cls) -> List[str]:
        cfg = cls._location_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("filter_keys") or []) if str(item).strip()]
        if configured:
            return configured
        return ["location_name", "location", "site"]

    @classmethod
    def _location_name_filter_key(cls) -> str:
        cfg = cls._location_lookup_config()
        key = str(cfg.get("canonical_filter_key", "")).strip()
        if key:
            return key
        keys = cls._location_filter_keys()
        return keys[0] if keys else "location_name"

    @classmethod
    def _location_id_filter_keys(cls) -> List[str]:
        cfg = cls._location_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("id_filter_keys") or []) if str(item).strip()]
        if configured:
            return configured
        fallback: List[str] = []
        canonical = cls._location_name_filter_key()
        if canonical.endswith("_name"):
            fallback.append(canonical.replace("_name", "_id"))
        fallback.extend(["location_id", "site_id"])
        return list(dict.fromkeys([x for x in fallback if x]))

    @classmethod
    def _user_lookup_filter_keys(cls) -> List[str]:
        cfg = cls._user_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("filter_keys") or []) if str(item).strip()]
        if configured:
            return configured
        return ["assigned_to", "assignee", "user"]

    @classmethod
    def _user_name_filter_key(cls) -> str:
        cfg = cls._user_lookup_config()
        key = str(cfg.get("canonical_filter_key", "")).strip()
        if key:
            return key
        for key in cls._user_lookup_filter_keys():
            if not key.endswith("_id"):
                return key
        return "assignee"

    @classmethod
    def _user_id_filter_key(cls) -> str:
        cfg = cls._user_lookup_config()
        key = str(cfg.get("id_filter_key", "")).strip()
        if key:
            return key
        for key in cls._user_filter_keys():
            if key.endswith("_id"):
                return key
        return "user_id"

    def _catalog_table_names(self) -> set[str]:
        catalog = getattr(self.sql_builder, "catalog", None)
        resolver = getattr(catalog, "table_names", None)
        if not callable(resolver):
            return set()
        try:
            return {str(item).strip() for item in (resolver() or []) if str(item).strip()}
        except Exception:
            return set()

    def _canonical_table_name(self, candidate: Any) -> str:
        name = str(candidate or "").strip()
        if not name:
            return ""

        table_names = self._catalog_table_names()
        if not table_names:
            return ""
        if name in table_names:
            return name

        lowered = name.lower()
        lowered_map = {str(tbl).lower(): str(tbl) for tbl in table_names}
        if lowered in lowered_map:
            return lowered_map[lowered]

        catalog = getattr(self.sql_builder, "catalog", None)
        aliases_getter = getattr(catalog, "aliases", None)
        if callable(aliases_getter):
            for table_name in table_names:
                try:
                    aliases = [str(a).strip().lower() for a in (aliases_getter(table_name) or []) if str(a).strip()]
                except Exception:
                    aliases = []
                if lowered in aliases:
                    return str(table_name)

        if lowered.endswith("s"):
            singular = lowered[:-1].strip()
            if singular in lowered_map:
                return lowered_map[singular]

        return ""

    @staticmethod
    def _looks_like_direct_operation_query(query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        return bool(
            re.match(
                r"^(show|list|get|find|create|add|new|insert|update|change|modify|edit|delete|remove)\b",
                text_query,
            )
        )

    @classmethod
    def _should_skip_llm_intent(cls, query: str, current_intent: Dict[str, Any]) -> bool:
        intent = dict(current_intent or {})
        if str(intent.get("table", "")).strip():
            return True
        if str(intent.get("operation", "")).strip():
            return True
        if cls._parse_kv_pairs(query):
            return True
        if cls._is_pure_filter_query(query):
            return True
        if cls._looks_like_direct_operation_query(query):
            return True
        # Very short inputs don't benefit from LLM roundtrip.
        if len(str(query or "").strip()) <= 20:
            return True
        return False

    @staticmethod
    def _looks_like_sql_statement(query: str) -> bool:
        text_query = str(query or "").strip()
        if not text_query:
            return False
        # Guard against natural language like "Update task status" being treated as SQL.
        if re.match(r"^UPDATE\b", text_query, flags=re.IGNORECASE):
            if not re.search(r"\bSET\b", text_query, flags=re.IGNORECASE):
                return False
        if re.match(r"^INSERT\b", text_query, flags=re.IGNORECASE):
            if not re.search(r"\bINTO\b", text_query, flags=re.IGNORECASE):
                return False
        if re.match(r"^SELECT\b", text_query, flags=re.IGNORECASE):
            if not re.search(r"\bFROM\b", text_query, flags=re.IGNORECASE):
                return False
        return bool(re.match(r"^(SELECT|INSERT|UPDATE)\b", text_query, flags=re.IGNORECASE))

    @staticmethod
    def _is_placeholder_filter_value(value: Any) -> bool:
        text = str(value or "").strip().lower()
        return text in {"", "null", "none", "undefined", "n/a", "na"}

    def _extract_forced_table_from_query(self, query: str) -> str:
        text_query = str(query or "").strip()
        match = re.match(r"^\s*show\s+([A-Za-z_][A-Za-z0-9_]*)\b", text_query, flags=re.IGNORECASE)
        if not match:
            return ""
        candidate = str(match.group(1) or "").strip()
        return self._canonical_table_name(candidate)

    def _query_mentions_explicit_table(self, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        for table_name in self._catalog_table_names():
            t = str(table_name or "").strip().lower()
            if t and re.search(rf"\b{re.escape(t)}\b", text_query):
                return True
        return False

    def _is_explicit_list_request(self, query: str, resolved_table: str = "") -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        if not re.match(r"^(list|show|get|find)\b", text_query):
            return False
        if str(resolved_table or "").strip():
            return True
        return self._query_mentions_explicit_table(query)

    @staticmethod
    def _is_pure_filter_query(query: str) -> bool:
        text_query = str(query or "").strip()
        if not text_query:
            return False
        if re.fullmatch(
            r"\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^,;]+(\s*[,;]\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^,;]+)*\s*",
            text_query,
            flags=re.IGNORECASE,
        ):
            return True
        lowered = text_query.lower()
        common_terms = {"today", "yesterday", "pending", "completed", "in progress", "overdue"}
        common_terms.update(set(SQLBuilderNode._date_phrase_map().keys()))
        common_terms.update(set(SQLBuilderNode._status_phrase_map().keys()))
        if lowered in common_terms:
            return True
        return False

    @classmethod
    def _looks_like_task_intent(cls, query: str, filters: Dict[str, Any]) -> bool:
        text_query = str(query or "").strip().lower()
        for keyword in cls._primary_keywords():
            escaped = re.escape(keyword).replace(r"\ ", r"\s+")
            if re.search(rf"\b{escaped}\b", text_query):
                return True

        task_filter_keys = cls._primary_filter_keys()
        lowered_keys = {str(k or "").strip().lower() for k in (filters or {}).keys()}
        return bool(lowered_keys & task_filter_keys)

    @classmethod
    def _mentions_explicit_nonself_user(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        if cls._requests_all_users(text_query):
            return True
        user_alias_patterns = [
            re.escape(str(alias).strip().replace("_", " "))
            for alias in cls._user_lookup_filter_keys()
            if str(alias).strip()
        ]
        alias_group = "|".join(sorted(set(user_alias_patterns), key=len, reverse=True))
        if not alias_group:
            return False
        match = re.search(rf"\b({alias_group})\s+([a-zA-Z0-9_]+)\b", text_query, flags=re.IGNORECASE)
        if not match:
            return False
        value = str(match.group(2) or "").strip().lower()
        return value not in cls._self_aliases()

    @classmethod
    def _requests_self_tasks(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        aliases = sorted(cls._self_aliases(), key=len, reverse=True)
        return any(re.search(rf"\b{re.escape(alias)}\b", text_query) for alias in aliases)

    @classmethod
    def _requests_all_users(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        for phrase in cls._all_users_aliases():
            escaped = re.escape(phrase).replace(r"\ ", r"\s+")
            if re.search(rf"\b{escaped}\b", text_query):
                return True
        # Backward-compatible generic patterns.
        if re.search(r"\ball\s+user(s)?\b", text_query):
            return True
        if re.search(r"\ball\s+assignee(s)?\b", text_query):
            return True
        return False

    @classmethod
    def _has_task_autorun_context(cls, filters: Dict[str, Any]) -> bool:
        normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in (filters or {}).items()}
        if not normalized:
            return False
        user_filter_keys = cls._user_filter_keys()
        has_user = any(normalized.get(key) for key in user_filter_keys)
        date_keys = {str(k).strip().lower() for k in cls._date_filter_keys()}
        has_date = any(normalized.get(key) for key in date_keys)
        location_filter_keys = {str(k).strip().lower() for k in cls._location_filter_keys()}
        location_id_keys = {str(k).strip().lower() for k in cls._location_id_filter_keys()}
        has_facility = any(normalized.get(key) for key in (location_filter_keys | location_id_keys))
        has_status = bool(normalized.get(cls._status_filter_key().lower()))
        has_priority = bool(normalized.get(cls._priority_filter_key().lower()))
        # Consider task query specific enough when date is present plus at least one strong narrowing filter.
        return has_date and (has_user or has_facility or has_status or has_priority)

    @staticmethod
    def _is_unfiltered_select(sql: str) -> bool:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return False
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return False
        if not isinstance(parsed, exp.Select):
            return False
        return parsed.args.get("where") is None

    @staticmethod
    def _select_where_columns(sql: str) -> set[str]:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return set()
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return set()
        if not isinstance(parsed, exp.Select):
            return set()
        where_expr = parsed.args.get("where")
        if where_expr is None:
            return set()
        cols = set()
        for col in where_expr.find_all(exp.Column):
            name = str(col.name or "").strip().lower()
            if name:
                cols.add(name)
        return cols

    @classmethod
    def _normalized_user_filters(cls, intent_filters: Dict, query: str) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        user_name_key = cls._user_name_filter_key()
        date_key = cls._date_filter_key()
        status_key = cls._status_filter_key()
        if isinstance(intent_filters, dict):
            for k, v in intent_filters.items():
                key = str(k or "").strip()
                value = str(v or "").strip()
                if (
                    key
                    and value
                    and not SQLBuilderNode._is_placeholder_filter_value(value)
                    and value.lower() != key.lower()
                ):
                    normalized[key] = value
        for k, v in cls._parse_kv_pairs(query).items():
            key = str(k or "").strip()
            value = str(v or "").strip()
            if (
                key
                and value
                and not SQLBuilderNode._is_placeholder_filter_value(value)
                and value.lower() != key.lower()
            ):
                normalized[key] = value
        lowered = str(query or "").lower()
        for phrase, value in cls._date_phrase_map().items():
            if phrase and phrase in lowered:
                normalized.setdefault(date_key, value)
        for phrase, value in cls._status_phrase_map().items():
            if phrase and phrase in lowered:
                normalized.setdefault(status_key, value)

        task_for_match = None
        for keyword in cls._primary_keywords():
            escaped = re.escape(keyword).replace(r"\ ", r"\s+")
            task_for_match = re.search(
                rf"\b{escaped}\b\s+for\s+([a-zA-Z][a-zA-Z0-9_ ]{{0,40}})",
                query,
                re.IGNORECASE,
            )
            if task_for_match:
                break
        if task_for_match:
            candidate = str(task_for_match.group(1) or "").strip()
            location_terms = [str(k).strip().replace("_", " ") for k in cls._location_filter_keys() if str(k).strip()]
            date_terms = [str(k).strip() for k in cls._date_phrase_map().keys()]
            status_terms = [str(k).strip() for k in cls._status_phrase_map().keys()]
            split_terms = (
                ["status", cls._priority_filter_key().replace("_", " "), "for all users?", "for everyone", "everyone"]
                + date_terms
                + status_terms
                + location_terms
            )
            split_pattern = "|".join(sorted({re.escape(term) for term in split_terms if term}, key=len, reverse=True))
            candidate = re.split(
                rf"\b({split_pattern})\b" if split_pattern else r"\b(status|priority)\b",
                candidate,
                flags=re.IGNORECASE,
            )[0].strip()
            looks_like_person = bool(re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", candidate))
            excluded_keywords = {str(item).strip().lower() for item in cls._primary_keywords()}
            if candidate and looks_like_person and candidate.lower() not in (excluded_keywords | {status_key.lower()}):
                normalized.setdefault(user_name_key, candidate)
        
        # Regex extraction for common user patterns
        user_alias_patterns = [re.escape(str(a).strip().replace("_", " ")) for a in cls._user_lookup_filter_keys() if str(a).strip()]
        user_alias_group = "|".join(sorted(set(user_alias_patterns), key=len, reverse=True))
        match = re.search(rf"\b({user_alias_group})\s+([a-zA-Z0-9_]+)", query, re.IGNORECASE) if user_alias_group else None
        if match:
             val = match.group(2).strip()
             excluded = {str(item).strip().lower() for item in cls._primary_keywords()}
             date_terms = {str(k).strip().lower() for k in cls._date_phrase_map().keys()}
             if val.lower() not in (excluded | {"me", "my", "assets"} | date_terms):
                  normalized[user_name_key] = val
        if cls._requests_all_users(lowered):
            removable = cls._user_filter_keys() | {user_name_key, cls._user_id_filter_key()} | set(cls._user_lookup_filter_keys())
            for key in removable:
                normalized.pop(key, None)
        return normalized

    def _generate_dynamic_filter_options(self, table: str) -> list[Dict[str, str]]:
        """Generate filter options with a simple config-first strategy."""
        try:
            # Primary entity: use explicit domain menu options.
            if table == self._primary_table():
                configured = self._primary_menu_options()
                if configured:
                    return configured[:6]

            columns = sorted(self.sql_builder.catalog.important_columns(table))
            if not columns:
                return [{"label": "Type your filters manually", "value": ""}]

            system_columns = {str(c).strip().lower() for c in self._system_columns(table)}
            options: list[Dict[str, str]] = []
            for col in columns:
                key = str(col or "").strip()
                if not key:
                    continue
                if key.lower() in system_columns:
                    continue
                label = key.replace("_", " ").strip().title()
                options.append({"label": label, "value": f"{key}="})
                if len(options) >= 6:
                    break

            # Add simple date shortcuts when table has date-like fields.
            if any("date" in str(c).lower() or "time" in str(c).lower() for c in columns):
                date_key = self._date_filter_key()
                options = [
                    {"label": "Today", "value": f"{date_key}=today"},
                    {"label": "Yesterday", "value": f"{date_key}=yesterday"},
                ] + options

            return options[:6] if options else [{"label": "Type your filters manually", "value": ""}]
        except Exception as e:
            logger.error(f"Failed to generate dynamic filters for {table}: {e}", exc_info=True)
            return [{"label": "Type your filters manually", "value": ""}]

    def _sanitize_prefilled_filters(self, table: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(filters or {})
        cleaned: Dict[str, Any] = {}

        for key, value in raw.items():
            k = str(key or "").strip()
            if not k:
                continue
            if self._is_placeholder_filter_value(value):
                continue
            text_value = str(value or "").strip()
            if not text_value:
                continue
            if text_value.lower() == k.lower():
                continue
            # Drop noisy meta-like inferences from intent detector.
            if k.lower() in {"field", "task_assigned"}:
                continue
            cleaned[k] = value

        # Normalize date aliases to configured primary date key.
        date_key = self._date_filter_key()
        if date_key and date_key not in cleaned:
            for alias in ("date", "due_date"):
                if alias in cleaned:
                    cleaned[date_key] = cleaned[alias]
                    break
        for alias in ("date", "due_date"):
            if alias != date_key:
                cleaned.pop(alias, None)

        # When assignee is already inferred, plain name often duplicates it.
        user_related = set(self._user_lookup_filter_keys()) | {self._user_name_filter_key(), self._user_id_filter_key()}
        if any(k in cleaned for k in user_related):
            cleaned.pop("name", None)

        # Keep only known/allowed fields for the target table plus supported aliases.
        allowed = {str(c).strip() for c in self.sql_builder.catalog.important_columns(table)}
        aliases = set(self._user_lookup_filter_keys())
        aliases.add(self._user_name_filter_key())
        aliases.add(self._user_id_filter_key())
        aliases.update(self._location_filter_keys())
        aliases.update(self._location_id_filter_keys())
        aliases.discard("")
        return {k: v for k, v in cleaned.items() if k in allowed or k in aliases}

    @staticmethod
    def _compact_label_options(options: List[Tuple[str, str]], limit: int = 6) -> List[Dict[str, str]]:
        return [{"label": label, "value": value} for label, value in options[:limit]]

    def _lookup_facility_candidates(self, value: str, metadata: Dict[str, Any]) -> List[str]:
        query_value = str(value or "").strip()
        if not query_value:
            return []
        cfg = self._location_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip()) or "location"
        name_column = self._safe_ident(str(cfg.get("name_column", "")).strip()) or "name"
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        search_limit = self._coerce_int(cfg.get("search_limit"), 12, 1, 200)
        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)

        # Single deterministic query only (no fuzzy second-pass scan).
        search_where: List[str] = [f"LOWER(`{name_column}`) LIKE :q"]
        search_params: Dict[str, Any] = {"q": f"%{query_value.lower()}%"}
        if tenant_column and tenant_value is not None:
            search_where.append(f"`{tenant_column}` = :tenant_value")
            search_params["tenant_value"] = tenant_value
        rows = self._query_rows(
            metadata,
            f"SELECT `{name_column}` AS name FROM `{table}` "
            f"WHERE {' AND '.join(search_where)} "
            f"ORDER BY `{name_column}` LIMIT {search_limit}",
            search_params,
        )
        return self._dedupe_text([str(r.get("name", "")).strip() for r in rows])

    def _lookup_user_candidates(self, value: str, metadata: Dict[str, Any]) -> List[Tuple[str, str]]:
        query_value = str(value or "").strip()
        if not query_value:
            return []
        cfg = self._user_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip()) or "user"
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip()) or "id"
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip()) or "first_name"
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip()) or "last_name"
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        search_limit = self._coerce_int(cfg.get("search_limit"), 12, 1, 200)
        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)
        query_lower = query_value.lower()
        name_filter_key = self._user_name_filter_key()

        search_where: List[str] = [f"(LOWER(`{first_column}`) LIKE :q OR LOWER(`{last_column}`) LIKE :q)"]
        search_params: Dict[str, Any] = {"q": f"%{query_lower}%"}
        if tenant_column and tenant_value is not None:
            search_where.append(f"`{tenant_column}` = :tenant_value")
            search_params["tenant_value"] = tenant_value

        order_parts = [f"`{first_column}`", f"`{last_column}`"]
        if active_column:
            order_parts.insert(0, f"`{active_column}` DESC")
        order_by = ", ".join(order_parts)
        rows = self._query_rows(
            metadata,
            f"SELECT `{id_column}` AS id, `{first_column}` AS first_name, `{last_column}` AS last_name "
            f"FROM `{table}` WHERE {' AND '.join(search_where)} "
            f"ORDER BY {order_by} LIMIT {search_limit}",
            search_params,
        )
        names = self._dedupe_text(
            [
                f"{str(r.get('first_name', '')).strip()} {str(r.get('last_name', '')).strip()}".strip()
                for r in rows
            ]
        )
        return [(name, f"{name_filter_key}={name}") for name in names]

    def _resolve_user_id_by_name(self, value: str, metadata: Dict[str, Any]) -> str:
        name = str(value or "").strip()
        if not name:
            return ""
        cfg = self._user_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip()) or "user"
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip()) or "id"
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip()) or "first_name"
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip()) or "last_name"
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column

        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)
        where_parts = [
            f"LOWER(TRIM(CONCAT(COALESCE(`{first_column}`,''), ' ', COALESCE(`{last_column}`,'')))) = LOWER(:n)",
            f"LOWER(`{first_column}`) = LOWER(:n)",
        ]
        params: Dict[str, Any] = {"n": name}
        extra_filters: List[str] = []
        if tenant_column and tenant_value is not None:
            extra_filters.append(f"`{tenant_column}` = :tenant_value")
            params["tenant_value"] = tenant_value

        order_parts: List[str] = []
        if active_column:
            order_parts.append(f"`{active_column}` DESC")
        order_parts.append(f"`{id_column}` ASC")
        order_by = ", ".join(order_parts)
        combined_where = f"({' OR '.join(where_parts)})"
        if extra_filters:
            combined_where += f" AND {' AND '.join(extra_filters)}"
        rows = self._query_rows(
            metadata,
            f"SELECT `{id_column}` AS id FROM `{table}` "
            f"WHERE {combined_where} "
            f"ORDER BY {order_by} LIMIT 1",
            params,
        )
        if not rows:
            return ""
        return str(rows[0].get("id") or "").strip()

    def _fallback_facility_options(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        cfg = self._location_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip()) or "location"
        name_column = self._safe_ident(str(cfg.get("name_column", "")).strip()) or "name"
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        fallback_limit = self._coerce_int(cfg.get("fallback_limit"), 6, 1, 50)
        location_name_key = self._location_name_filter_key()

        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)

        where_clause = ""
        params: Dict[str, Any] = {}
        if tenant_column and tenant_value is not None:
            where_clause = f" WHERE `{tenant_column}` = :tenant_value"
            params["tenant_value"] = tenant_value
        rows = self._query_rows(
            metadata,
            f"SELECT `{name_column}` AS name FROM `{table}`"
            f"{where_clause} ORDER BY `{name_column}` LIMIT {fallback_limit}",
            params,
        )
        names = self._dedupe_text([str(r.get("name", "")).strip() for r in rows])
        return self._compact_label_options([(n, f"{location_name_key}={n}") for n in names])

    def _fallback_user_options(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        cfg = self._user_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip()) or "user"
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip()) or "id"
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip()) or "first_name"
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip()) or "last_name"
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        fallback_limit = self._coerce_int(cfg.get("fallback_limit"), 6, 1, 100)
        name_filter_key = self._user_name_filter_key()

        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)

        where_clause = ""
        params: Dict[str, Any] = {}
        if tenant_column and tenant_value is not None:
            where_clause = f" WHERE `{tenant_column}` = :tenant_value"
            params["tenant_value"] = tenant_value
        order_parts = [f"`{first_column}`", f"`{last_column}`"]
        if active_column:
            order_parts.insert(0, f"`{active_column}` DESC")
        order_by = ", ".join(order_parts)
        rows = self._query_rows(
            metadata,
            f"SELECT `{id_column}` AS id, `{first_column}` AS first_name, `{last_column}` AS last_name "
            f"FROM `{table}`{where_clause} ORDER BY {order_by} LIMIT {fallback_limit}",
            params,
        )
        names = self._dedupe_text(
            [
                f"{str(r.get('first_name', '')).strip()} {str(r.get('last_name', '')).strip()}".strip()
                for r in rows
            ]
        )
        return self._compact_label_options([(name, f"{name_filter_key}={name}") for name in names])

    def _build_disambiguation_prompt(
        self,
        table: str,
        explicit_filters: Dict[str, Any],
        target_field: str,
        options: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        count = len(options or [])
        if count <= 1:
            message = f"I found a close match for `{target_field}`. Please confirm this option."
        else:
            message = f"I found multiple matches for `{target_field}`. Please pick one option."
        candidate_filters = self._candidate_filters(table)
        payload = self._filter_prompt_payload(
            table,
            candidate_filters or [target_field],
            prefilled_filters=self._sanitize_prefilled_filters(table, explicit_filters),
            options_override=options,
        )
        payload_ui = payload.get("ui") or {}
        payload_ui["title"] = f"Choose {target_field}"
        payload["ui"] = payload_ui
        return {
            "sql_query": "SKIP",
            "error": None,
            "pending_select": {"table": table},
            "workflow_payload": payload,
            "messages": [AIMessage(content=message)],
        }

    def _maybe_disambiguate_filters(
        self,
        table: str,
        explicit_filters: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any] | None]:
        filters = dict(explicit_filters or {})
        location_name_key = self._location_name_filter_key()
        location_filter_keys = [k for k in self._location_filter_keys() if str(filters.get(k, "")).strip()]
        if location_filter_keys:
            location_key = location_filter_keys[0]
            location_value = str(filters.get(location_key, "")).strip()
            candidates = self._lookup_facility_candidates(location_value, metadata)
            if candidates:
                exact = [x for x in candidates if x.lower() == location_value.lower()]
                if exact:
                    filters[location_name_key] = exact[0]
                elif len(candidates) == 1:
                    filters[location_name_key] = candidates[0]
                else:
                    options = self._compact_label_options([(name, f"{location_name_key}={name}") for name in candidates])
                    return filters, self._build_disambiguation_prompt(table, filters, location_name_key, options)
            else:
                options = self._fallback_facility_options(metadata)
                if options:
                    return filters, self._build_disambiguation_prompt(table, filters, location_name_key, options)
            for alias in self._location_filter_keys():
                if alias != location_name_key:
                    filters.pop(alias, None)

        user_name_key = self._user_name_filter_key()
        user_id_key = self._user_id_filter_key()
        user_aliases = self._user_lookup_filter_keys()
        user_keys = [k for k in user_aliases if str(filters.get(k, "")).strip()]
        if user_keys and not str(filters.get(user_id_key, "")).strip():
            user_key = user_keys[0]
            user_value = str(filters.get(user_key, "")).strip()
            user_lower = user_value.lower()

            if user_lower in {"me", "my", "mine", "myself", "self", "current_user"}:
                actor_user_id = str((metadata or {}).get("user_id") or "").strip()
                if actor_user_id:
                    filters[user_id_key] = actor_user_id
                resolved_name = str((metadata or {}).get("user_name") or "").strip()
                if resolved_name:
                    filters[user_name_key] = resolved_name
                for alias in user_aliases:
                    if alias not in {user_name_key, user_id_key}:
                        filters.pop(alias, None)
                return filters, None

            ignored_terms = {""}
            ignored_terms.update({str(k).strip().lower() for k in self._primary_keywords()})
            ignored_terms.update({str(k).strip().lower() for k in self._date_phrase_map().keys()})
            ignored_terms.update({str(k).strip().lower() for k in self._status_phrase_map().keys()})
            ignored_terms.update({"all", "everyone"})
            if user_lower in ignored_terms:
                for alias in user_aliases:
                    if alias != user_name_key:
                        filters.pop(alias, None)
                return filters, None

            candidates = self._lookup_user_candidates(user_value, metadata)
            if candidates:
                exact = [c for c in candidates if str(c[0] or "").strip().lower() == user_value.lower()]
                chosen = exact[0] if exact else None
                if chosen is None and len(candidates) == 1:
                    chosen = candidates[0]

                if chosen is not None:
                    val_expr = str(chosen[1]).strip()
                    if "=" in val_expr:
                        key, value = val_expr.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key and value:
                            filters[key] = value
                            if key == user_name_key:
                                resolved_id = self._resolve_user_id_by_name(value, metadata)
                                if resolved_id:
                                    filters[user_id_key] = resolved_id
                else:
                    options = self._compact_label_options(candidates)
                    return filters, self._build_disambiguation_prompt(table, filters, user_name_key, options)
            else:
                if len(user_value) >= 2:
                    options = self._fallback_user_options(metadata)
                    if options:
                        return filters, self._build_disambiguation_prompt(table, filters, user_name_key, options)
            if str(filters.get(user_name_key, "")).strip() and not str(filters.get(user_id_key, "")).strip():
                resolved_id = self._resolve_user_id_by_name(str(filters.get(user_name_key, "")).strip(), metadata)
                if resolved_id:
                    filters[user_id_key] = resolved_id
            # Only drop aliases after we have a concrete user id or resolved display name.
            if str(filters.get(user_id_key, "")).strip() or str(filters.get(user_name_key, "")).strip():
                for alias in user_aliases:
                    if alias not in {user_name_key, user_id_key}:
                        filters.pop(alias, None)
        elif str(filters.get(user_name_key, "")).strip() and not str(filters.get(user_id_key, "")).strip():
            resolved_id = self._resolve_user_id_by_name(str(filters.get(user_name_key, "")).strip(), metadata)
            if resolved_id:
                filters[user_id_key] = resolved_id

        return filters, None

    @staticmethod
    def _filter_options_excluding_prefilled(
        options: list[Dict[str, str]],
        prefilled_filters: Dict[str, Any] | None = None,
    ) -> list[Dict[str, str]]:
        prefilled = {str(k or "").strip().lower() for k in (prefilled_filters or {}).keys() if str(k or "").strip()}
        if not prefilled:
            return list(options or [])

        filtered: list[Dict[str, str]] = []
        for opt in options or []:
            value = str((opt or {}).get("value", "")).strip()
            if "=" in value:
                field = str(value.split("=", 1)[0]).strip().lower()
                if field and field in prefilled:
                    continue
            filtered.append(opt)
        return filtered

    def _candidate_filters(self, table: str, limit: int = 6) -> List[str]:
        max_items = max(1, int(limit or 1))
        return [
            c
            for c in sorted(self.sql_builder.catalog.important_columns(table))
            if c not in self._system_columns(table)
        ][:max_items]

    def _skip_with_filter_prompt(
        self,
        table: str,
        suggested_fields: List[str] | None = None,
        prefilled_filters: Dict[str, Any] | None = None,
        options_override: list[Dict[str, str]] | None = None,
        message: str = "",
    ) -> Dict[str, Any]:
        fields = suggested_fields if suggested_fields is not None else self._candidate_filters(table)
        return {
            "sql_query": "SKIP",
            "error": None,
            "pending_select": {"table": table},
            "workflow_payload": self._filter_prompt_payload(
                table,
                fields,
                prefilled_filters=prefilled_filters,
                options_override=options_override,
            ),
            "messages": [AIMessage(content=message)],
        }

    def _fallback_intent(self, query: str) -> Dict[str, Any]:
        detector = self.intent_detector
        for method_name in ("fallback_intent", "_fallback_intent"):
            resolver = getattr(detector, method_name, None)
            if callable(resolver):
                try:
                    payload = resolver(query)
                    return payload if isinstance(payload, dict) else {}
                except Exception:
                    continue
        return {}

    def _filter_prompt_payload(
        self,
        table: str,
        suggested_fields: list[str],
        prefilled_filters: Dict[str, Any] | None = None,
        options_override: list[Dict[str, str]] | None = None,
    ) -> Dict:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        if not fields:
            fields = ["id", "name", "date_created"]
        
        # Generate dynamic options based on table schema
        dynamic_options = options_override or self._generate_dynamic_filter_options(table)
        
        # Generate example based on first option
        example = "id=123, name=example"
        if dynamic_options:
            first_val = dynamic_options[0]["value"]
            if "=" in first_val:
                example = f"{first_val}, {fields[0] if fields else 'id'}=value"
        
        return {
            "workflow_id": self._select_workflow_id(),
            "state": self._select_workflow_state(),
            "completed": False,
            "mode": self._select_workflow_mode(),
            "next_field": self._select_workflow_next_field(),
            "collected_data": {
                "operation": self._select_workflow_operation(),
                "table": table,
                "required_fields": ["filters"],
                "collected_fields": dict(prefilled_filters or {}),
            },
            "ui": {
                "type": "menu",
                "title": f"Add filters for {table}",
                "options": dynamic_options,
                "suggested_fields": fields[:6],
                "example": example,
            },
        }

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = dict(state.get("metadata") or {})
        actor_user_id = metadata.get("user_id") or metadata.get("userId")

        # If user already supplied SQL, pass it through untouched.
        # Validation and safety checks happen in sql_validate_node.
        if self._looks_like_sql_statement(query):
            return {"sql_query": query.strip()}

        intent = dict(state.get("intent") or {})
        intent_mode = self._intent_mode()
        skip_llm_intent = intent_mode == "heuristic" or (
            intent_mode == "auto" and self._should_skip_llm_intent(query, intent)
        )

        if skip_llm_intent:
            detected_intent = self._fallback_intent(query)
            logger.info("Intent detection mode=heuristic (LLM skipped): %s", detected_intent)
        else:
            try:
                detected_intent = await asyncio.wait_for(
                    self.intent_detector.detect_intent(query, metadata),
                    timeout=4.0,
                )
                logger.info("Detected intent via LLM: %s", detected_intent)
            except Exception as exc:
                logger.warning("Intent detection LLM timeout/failure, falling back: %s", exc)
                detected_intent = self._fallback_intent(query)

        # Merge detected intent with existing intent (detected takes priority)
        if detected_intent.get("table"):
            intent["table"] = detected_intent["table"]
        if detected_intent.get("operation"):
            intent["operation"] = detected_intent["operation"]
        if detected_intent.get("filters"):
            # Merge filters from intent detection
            existing_filters = intent.get("filters", {})
            if isinstance(existing_filters, dict):
                for filter_obj in detected_intent["filters"]:
                    field = filter_obj.get("field")
                    value = filter_obj.get("value")
                    if (
                        field
                        and value
                        and not self._is_placeholder_filter_value(value)
                        and str(value).strip().lower() != str(field).strip().lower()
                    ):
                        existing_filters[field] = value
                intent["filters"] = existing_filters
        
        forced_table = self._extract_forced_table_from_query(query)
        intent_table = self._canonical_table_name(intent.get("table"))
        prefilters = self._normalized_user_filters(intent.get("filters"), query)
        pending_table = str(metadata.get("pending_select_table", "") or "").strip()
        table_names = self._catalog_table_names()
        pure_filter_query = self._is_pure_filter_query(query)

        operation = str(intent.get("operation", "select") or "select").lower()

        # Strict mode for filter-only input: use pending context table or ask for explicit table.
        if pure_filter_query and not forced_table and not self._query_mentions_explicit_table(query):
            if pending_table and pending_table in table_names:
                table = pending_table
            else:
                return {
                    "sql_query": "SKIP",
                    "error": None,
                    "messages": [
                        AIMessage(
                            content=self._filter_context_prompt()
                        )
                    ],
                }
        else:
            # Use forced table first (for pending-select followups), then detected/resolved.
            table = forced_table or intent_table or self.sql_builder.resolve_table(query, intent)
        table = self._canonical_table_name(table) or str(table or "").strip()
        if (
            not forced_table
            and operation == "select"
            and self._looks_like_task_intent(query, prefilters)
            and not self._query_mentions_explicit_table(query)
        ):
            table = self._primary_table()
        if not table:
            return {
                "sql_query": "SKIP",
                "messages": [AIMessage(content=self._default_entity_prompt())],
            }
        tenant_value = self._tenant_value(table, metadata)

        fields = {}
        if isinstance(intent.get("fields"), dict):
            fields.update(intent.get("fields"))
        kv_pairs = self.sql_builder.parse_kv_pairs(query)
        fields.update(kv_pairs)
        explicit_filters = prefilters
        explicit_filters, disambiguation_result = self._maybe_disambiguate_filters(table, explicit_filters, metadata)
        if disambiguation_result is not None:
            return disambiguation_result

        is_task_status = table == self._primary_table() and operation == "select"
        user_filter_keys = self._user_filter_keys()
        user_name_key = self._user_name_filter_key()
        user_id_key = self._user_id_filter_key()
        date_key = self._date_filter_key()
        
        # Only default to current user if NO user filter interpretation was found
        if (
            is_task_status
            and actor_user_id
            and not any(k in explicit_filters for k in (user_filter_keys | {user_name_key, user_id_key}))
            and not self._mentions_explicit_nonself_user(query)
            and not self._requests_all_users(query)
            and self._requests_self_tasks(query)
        ):
            # Default to current user's primary-entity records unless caller specified another user.
            explicit_filters[user_id_key] = actor_user_id

        # For primary-entity status views, assignee-only filters without an explicit date
        # become too broad; default to today unless user asked for another date.
        if (
            is_task_status
            and any(k in explicit_filters for k in (user_filter_keys | {user_name_key, user_id_key}))
            and not any(str(explicit_filters.get(k, "")).strip() for k in self._date_filter_keys())
        ):
            lowered_query = str(query or "").lower()
            range_terms = [re.escape(term) for term in self._primary_date_range_terms() if str(term).strip()]
            range_pattern = r"\b(" + "|".join(range_terms) + r")\b" if range_terms else ""
            if not range_pattern or not re.search(range_pattern, lowered_query):
                explicit_filters[date_key] = "today"

        display_filters = self._sanitize_prefilled_filters(table, explicit_filters)

        # For natural-language primary-entity requests with no inferred filters, show options menu.
        # Structured or inferred filters should continue to SQL execution.
        if is_task_status and not kv_pairs and not explicit_filters and not self._has_task_autorun_context(explicit_filters):
            candidate_filters = self._primary_menu_filters()
            behavior_cfg = self._entity_behavior_config()
            today_label = str(behavior_cfg.get("task_menu_today_label", "")).strip() or f"Today ({self._primary_label()})"
            today_value = (
                str(behavior_cfg.get("task_menu_today_value", "")).strip()
                or f"{date_key}=today, {user_name_key}=current_user"
            )
            user_option_value = f"{user_name_key}="
            task_options = self._primary_menu_options()
            if task_options:
                task_options = [dict(x) for x in task_options]
                task_options[0] = {"label": today_label, "value": today_value}
            # If user/assignee already supplied in query, don't ask "Different user" again.
            if any(k in display_filters for k in (user_filter_keys | {user_name_key, user_id_key})):
                task_options = [
                    opt
                    for opt in task_options
                    if str(opt.get("value", "")).strip() != user_option_value
                    and not str(opt.get("value", "")).strip().startswith(f"{user_name_key}=")
                ]
            task_options = self._filter_options_excluding_prefilled(task_options, display_filters)
            return self._skip_with_filter_prompt(
                table,
                candidate_filters,
                prefilled_filters=display_filters,
                options_override=task_options,
            )

        # Guard against tenant-only filters which are not useful business filters.
        if operation == "select" and explicit_filters:
            tenant_columns = {str(c).strip().lower() for c in self._tenant_columns(table)}
            non_tenant_explicit = {
                str(k).strip().lower()
                for k in explicit_filters.keys()
                if str(k).strip() and str(k).strip().lower() not in tenant_columns
            }
            if not non_tenant_explicit:
                return self._skip_with_filter_prompt(table, self._candidate_filters(table))

        explicit_list_request = self._is_explicit_list_request(query, str(table))

        # Generic understanding flow for other SELECT queries:
        # keep inferred filters and ask only for remaining helpful filters.
        if operation == "select" and not is_task_status and not kv_pairs and not explicit_list_request:
            generic_options = self._generate_dynamic_filter_options(table)
            generic_options = self._filter_options_excluding_prefilled(generic_options, display_filters)
            return self._skip_with_filter_prompt(
                table,
                self._candidate_filters(table),
                prefilled_filters=display_filters,
                options_override=generic_options,
            )

        if operation == "insert":
            if not self.sql_builder.catalog.create_enabled(table):
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=f"Create operation is not configured for `{table}`.")],
                }

            required = self.sql_builder.catalog.required_create_fields(table)
            if required:
                missing = [f for f in required if not str(fields.get(f, "")).strip()]
                if missing:
                    return {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=f"Missing required fields for insert: {', '.join(missing)}")],
                    }
            sql, err = self.sql_builder.build_insert(table, fields, tenant_value, actor_user_id=actor_user_id)
            if err:
                return {"sql_query": "SKIP", "messages": [AIMessage(content=err)]}
            return {"sql_query": sql}

        if operation == "update":
            sql, err = self.sql_builder.build_update(table, fields, tenant_value, actor_user_id=actor_user_id)
            if err:
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=err + " Use e.g. id=123, status=Completed.")],
                }
            return {"sql_query": sql}

        if not explicit_filters and not explicit_list_request:
            return self._skip_with_filter_prompt(table, self._candidate_filters(table))

        if explicit_list_request and not explicit_filters:
            sql = await self.sql_builder.build_select(query, table, tenant_value)
            select_err = ""
        else:
            sql, select_err = self.sql_builder.build_select_from_filters(table, explicit_filters, tenant_value)
        if select_err:
            return self._skip_with_filter_prompt(table, sorted(self.sql_builder.catalog.important_columns(table)))
        
        # Allow unfiltered queries but add LIMIT to prevent large result sets
        if self._is_unfiltered_select(sql):
            # Add LIMIT 100 to unfiltered queries
            if "LIMIT" not in sql.upper():
                sql = sql.rstrip(";") + " LIMIT 100;"

        where_cols = self._select_where_columns(sql)
        table_cols = self.sql_builder.catalog.important_columns(table)
        tenant_columns = {str(c).strip().lower() for c in self._tenant_columns(table)}
        requires_tenant_scope = bool(tenant_value) and bool(tenant_columns) and bool({c.lower() for c in table_cols} & tenant_columns)
        if requires_tenant_scope and tenant_columns.isdisjoint(where_cols) and not explicit_list_request:
            return self._skip_with_filter_prompt(table, self._candidate_filters(table, limit=5))

        non_tenant_filters = {c for c in where_cols if c not in tenant_columns}
        if requires_tenant_scope and not non_tenant_filters and not explicit_list_request:
            return self._skip_with_filter_prompt(table, self._candidate_filters(table, limit=5))
        return {"sql_query": sql}
