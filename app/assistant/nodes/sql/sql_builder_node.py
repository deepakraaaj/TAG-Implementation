from typing import Dict, Any, List, Tuple
import asyncio
import inspect
import re
import logging
from difflib import SequenceMatcher

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp
from sqlalchemy import text
from app.config import get_settings
from app.services.core.token_usage_service import TokenUsageService

logger = logging.getLogger(__name__)
settings = get_settings()


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

    async def build_select_with_usage(
        self,
        _query: str,
        _table: str,
        _tenant_value: Any,
        metadata: Dict[str, Any] | None = None,
    ) -> Tuple[str, Dict[str, int]]:
        return "", TokenUsageService.empty()

    @staticmethod
    def build_select_from_filters(_table: str, _filters: Dict[str, Any], _tenant_value: Any) -> Tuple[str, str]:
        return "", "SQL builder is not configured."

    @staticmethod
    def build_count_from_filters(_table: str, _filters: Dict[str, Any], _tenant_value: Any) -> Tuple[str, str]:
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
    async def detect_intent(
        _query: str,
        _metadata: Dict[str, Any],
        context_table: str = "",
    ) -> Dict[str, Any]:
        _ = context_table
        return {}

    @staticmethod
    async def detect_intent_with_usage(
        _query: str,
        _metadata: Dict[str, Any],
        context_table: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        _ = context_table
        return {}, TokenUsageService.empty()

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
    def _sql_builder_config(cls) -> Dict[str, Any]:
        return cls._domain_dict("get_config_section", "sql_builder")

    @classmethod
    def _sql_builder_patterns_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("patterns")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_filter_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("filter_cleanup")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_heuristics_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("heuristics")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_name_matching_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_heuristics_config()
        payload = cfg.get("name_matching")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_ui_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("ui")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_messages_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("messages")
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _join_message_parts(*parts: Any) -> str:
        items = [str(part or "").strip() for part in parts if str(part or "").strip()]
        return " ".join(items)

    @classmethod
    def _sql_builder_tenant_config(cls) -> Dict[str, Any]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("tenant")
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _sql_builder_table_alias_overrides(cls) -> Dict[str, str]:
        cfg = cls._sql_builder_config()
        payload = cfg.get("table_alias_overrides")
        if not isinstance(payload, dict):
            return {}
        overrides: Dict[str, str] = {}
        for key, value in payload.items():
            source = str(key or "").strip().lower()
            target = str(value or "").strip().lower()
            if source and target:
                overrides[source] = target
        return overrides

    @staticmethod
    def _format_template(template: str, **values: Any) -> str:
        rendered = str(template or "")
        for key, value in values.items():
            rendered = rendered.replace(f"{{{key}}}", str(value))
        return rendered

    @classmethod
    def _list_request_patterns(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        neg_cfg = cfg.get("cross_entity_negation", {})
        patterns = neg_cfg.get("list_request_patterns")
        return [str(p).strip() for p in (patterns or []) if str(p).strip()]

    @classmethod
    def _explicit_list_request_patterns(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        patterns = cfg.get("explicit_list_request_patterns")
        return [str(p).strip() for p in (patterns or []) if str(p).strip()]

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
        return ""

    @classmethod
    def _primary_keywords(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        keywords = [str(item).strip().lower() for item in (cfg.get("primary_keywords") or []) if str(item).strip()]
        return keywords

    @classmethod
    def _primary_filter_keys(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        keys = {str(item).strip().lower() for item in (cfg.get("primary_filter_keys") or []) if str(item).strip()}
        return keys

    @classmethod
    def _user_filter_keys(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        keys = {str(item).strip().lower() for item in (cfg.get("user_filter_keys") or []) if str(item).strip()}
        return keys

    @classmethod
    def _self_aliases(cls) -> set[str]:
        cfg = cls._entity_behavior_config()
        aliases = {str(item).strip().lower() for item in (cfg.get("self_aliases") or []) if str(item).strip()}
        return aliases

    @classmethod
    def _all_users_aliases(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        aliases = [str(item).strip().lower() for item in (cfg.get("all_users_aliases") or []) if str(item).strip()]
        return aliases

    @classmethod
    def _default_entity_prompt(cls) -> str:
        cfg = cls._entity_behavior_config()
        msg = str(cfg.get("default_entity_prompt", "")).strip()
        return msg

    @classmethod
    def _filter_context_prompt(cls) -> str:
        cfg = cls._entity_behavior_config()
        msg = str(cfg.get("filter_context_prompt", "")).strip()
        return msg

    @classmethod
    def _intent_mode(cls) -> str:
        cfg = cls._entity_behavior_config()
        mode = str(cfg.get("intent_mode", "")).strip().lower()
        return mode if mode in {"llm", "heuristic", "auto"} else ""

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
        return keys

    @classmethod
    def _date_filter_key(cls) -> str:
        keys = cls._date_filter_keys()
        return keys[0] if keys else ""

    @classmethod
    def _status_filter_key(cls) -> str:
        cfg = cls._entity_behavior_config()
        key = str(cfg.get("status_filter_key", "")).strip()
        return key

    @classmethod
    def _priority_filter_key(cls) -> str:
        cfg = cls._entity_behavior_config()
        key = str(cfg.get("priority_filter_key", "")).strip()
        return key

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
            return out
        return {}

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
            return out
        return {}

    @classmethod
    def _primary_menu_filters(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        keys = [str(item).strip() for item in (cfg.get("primary_menu_filters") or []) if str(item).strip()]
        return keys

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
            return normalized
        return []

    @classmethod
    def _primary_date_range_terms(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        terms = [str(item).strip().lower() for item in (cfg.get("date_range_terms") or []) if str(item).strip()]
        return terms

    @classmethod
    def _self_default_date_value(cls) -> str:
        cfg = cls._entity_behavior_config()
        return str(cfg.get("self_default_date_value", "")).strip()

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
        return workflow_id

    @classmethod
    def _select_workflow_state(cls) -> str:
        cfg = cls._select_workflow_config()
        state = str(cfg.get("state", "")).strip()
        return state

    @classmethod
    def _select_workflow_mode(cls) -> str:
        cfg = cls._select_workflow_config()
        mode = str(cfg.get("mode", "")).strip().lower()
        return mode

    @classmethod
    def _select_workflow_next_field(cls) -> str:
        cfg = cls._select_workflow_config()
        next_field = str(cfg.get("next_field", "")).strip()
        return next_field

    @classmethod
    def _select_workflow_operation(cls) -> str:
        cfg = cls._select_workflow_config()
        operation = str(cfg.get("operation", "")).strip().lower()
        return operation

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
            tenant_cfg = self._sql_builder_tenant_config()
            for candidate in [str(item).strip() for item in (tenant_cfg.get("fallback_columns") or []) if str(item).strip()]:
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
        tenant_cfg = self._sql_builder_tenant_config()
        candidates = [str(scope.get("metadata_key", "")).strip(), str(scope.get("column", "")).strip()]
        candidates.extend(
            str(item).strip()
            for item in (tenant_cfg.get("metadata_fallback_keys") or [])
            if str(item).strip()
        )
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
        cfg = self._sql_builder_config()
        excluded = {
            str(item).strip().lower()
            for item in (cfg.get("system_columns") or [])
            if str(item).strip()
        }
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
    def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value if value is not None else default)
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

    @staticmethod
    def _normalize_person_name(value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[^a-z0-9\s]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def _is_strong_name_match(cls, query: str, candidate: str) -> bool:
        q = cls._normalize_person_name(query)
        c = cls._normalize_person_name(candidate)
        if not q or not c:
            return False
        match_cfg = cls._sql_builder_name_matching_config()
        substring_min_length = cls._coerce_int(match_cfg.get("substring_min_length"), 4, 1, 50)
        prefix_min_length = cls._coerce_int(match_cfg.get("prefix_min_length"), 3, 1, 50)
        meaningful_token_min_length = cls._coerce_int(
            match_cfg.get("meaningful_token_min_length"),
            2,
            1,
            20,
        )
        ratio_threshold = cls._coerce_float(match_cfg.get("ratio_threshold"), 0.90, 0.0, 1.0)
        max_length_delta = cls._coerce_int(match_cfg.get("max_length_delta"), 3, 0, 100)
        if q == c:
            return True
        if len(q) >= substring_min_length and (q in c or c in q):
            return True

        q_tokens = [t for t in q.split(" ") if t]
        c_tokens = [t for t in c.split(" ") if t]
        if not q_tokens or not c_tokens:
            return False

        first_q = q_tokens[0]
        if len(first_q) >= prefix_min_length and any(tok.startswith(first_q) for tok in c_tokens):
            return True

        meaningful_q = [t for t in q_tokens if len(t) >= meaningful_token_min_length]
        if len(meaningful_q) >= 2:
            if all(any(tok.startswith(qt) for tok in c_tokens) for qt in meaningful_q):
                return True

        ratio = SequenceMatcher(None, q, c).ratio()
        if (
            ratio >= ratio_threshold
            and q[0] == c[0]
            and abs(len(q) - len(c)) <= max_length_delta
        ):
            return True
        return False

    @classmethod
    def _name_similarity_score(cls, query: str, candidate: str) -> float:
        q = cls._normalize_person_name(query)
        c = cls._normalize_person_name(candidate)
        if not q or not c:
            return 0.0
        if q == c:
            return 1.0
        match_cfg = cls._sql_builder_name_matching_config()
        contains_score = cls._coerce_float(match_cfg.get("contains_score"), 0.96, 0.0, 1.0)
        prefix_score = cls._coerce_float(match_cfg.get("prefix_score"), 0.85, 0.0, 1.0)
        if q in c or c in q:
            return contains_score
        score = SequenceMatcher(None, q, c).ratio()
        q_tokens = [t for t in q.split(" ") if t]
        c_tokens = [t for t in c.split(" ") if t]
        if q_tokens and c_tokens:
            # Boost likely prefix-like matches (e.g., "mahalakshmi" vs "mahalakshmi priya")
            first_q = q_tokens[0]
            if any(tok.startswith(first_q) or first_q.startswith(tok) for tok in c_tokens):
                score = max(score, prefix_score)
        return float(score)

    @classmethod
    def _llm_skip_short_query_length(cls) -> int:
        cfg = cls._sql_builder_heuristics_config()
        return cls._coerce_int(cfg.get("llm_skip_short_query_length"), 20, 0, 500)

    @classmethod
    def _user_suggestion_candidate_pool_limit(cls) -> int:
        cfg = cls._sql_builder_heuristics_config()
        return cls._coerce_int(cfg.get("user_suggestion_candidate_pool_limit"), 50, 1, 500)

    @classmethod
    def _user_suggestion_min_score(cls) -> float:
        cfg = cls._sql_builder_heuristics_config()
        return cls._coerce_float(cfg.get("user_suggestion_min_score"), 0.75, 0.0, 1.0)

    @classmethod
    def _unfiltered_select_limit(cls) -> int:
        cfg = cls._sql_builder_heuristics_config()
        return cls._coerce_int(cfg.get("unfiltered_select_limit"), 100, 1, 10000)

    @classmethod
    def _apply_unfiltered_select_limit(cls, sql: str) -> str:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return ""
        if "LIMIT" in text_sql.upper():
            return text_sql
        limit_value = cls._unfiltered_select_limit()
        return text_sql.rstrip(";") + f" LIMIT {limit_value};"

    def _db_engine(self, metadata: Dict[str, Any]):
        return self.schema.get_engine_for_url((metadata or {}).get("db_connection_string"))

    def _lookup_tenant_value(self, metadata: Dict[str, Any], metadata_key: str, tenant_column: str) -> Any:
        tenant_cfg = self._sql_builder_tenant_config()
        fallback_keys = [
            str(item).strip()
            for item in (tenant_cfg.get("metadata_fallback_keys") or [])
            if str(item).strip()
        ]
        return self._metadata_value(metadata, metadata_key, tenant_column, *fallback_keys)

    def _query_rows(self, metadata: Dict[str, Any], query_sql: str, params: Dict[str, Any] | None = None):
        with self._db_engine(metadata).connect() as conn:
            return conn.execute(text(query_sql), params or {}).mappings().all()

    @classmethod
    def _location_filter_keys(cls) -> List[str]:
        cfg = cls._location_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("filter_keys") or []) if str(item).strip()]
        return configured

    @classmethod
    def _location_name_filter_key(cls) -> str:
        cfg = cls._location_lookup_config()
        key = str(cfg.get("canonical_filter_key", "")).strip()
        if key:
            return key
        keys = cls._location_filter_keys()
        return keys[0] if keys else ""

    @classmethod
    def _location_id_filter_keys(cls) -> List[str]:
        cfg = cls._location_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("id_filter_keys") or []) if str(item).strip()]
        if configured:
            return configured
        derived: List[str] = []
        canonical = cls._location_name_filter_key()
        if canonical.endswith("_name"):
            derived.append(canonical.replace("_name", "_id"))
        return list(dict.fromkeys([x for x in derived if x]))

    @classmethod
    def _user_lookup_filter_keys(cls) -> List[str]:
        cfg = cls._user_lookup_config()
        configured = [str(item).strip() for item in (cfg.get("filter_keys") or []) if str(item).strip()]
        return configured

    @classmethod
    def _user_name_filter_key(cls) -> str:
        cfg = cls._user_lookup_config()
        key = str(cfg.get("canonical_filter_key", "")).strip()
        if key:
            return key
        for key in cls._user_lookup_filter_keys():
            if not key.endswith("_id"):
                return key
        return ""

    @classmethod
    def _user_id_filter_key(cls) -> str:
        cfg = cls._user_lookup_config()
        key = str(cfg.get("id_filter_key", "")).strip()
        if key:
            return key
        for key in cls._user_filter_keys():
            if key.endswith("_id"):
                return key
        return ""

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

        overrides = self._sql_builder_table_alias_overrides()
        override_target = overrides.get(lowered, "")
        if override_target and override_target in lowered_map:
            return lowered_map[override_target]

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

        normalized_name = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
        if normalized_name:
            best_table = ""
            best_score = 0.0
            for table_name in table_names:
                labels = [str(table_name or "").strip().lower()]
                if callable(aliases_getter):
                    try:
                        labels.extend(
                            str(a).strip().lower()
                            for a in (aliases_getter(table_name) or [])
                            if str(a).strip()
                        )
                    except Exception:
                        pass
                for label in labels:
                    normalized_label = re.sub(r"[^a-z0-9]+", " ", str(label or "").strip().lower()).strip()
                    if not normalized_label:
                        continue
                    if normalized_name[0] != normalized_label[0]:
                        continue
                    if abs(len(normalized_name) - len(normalized_label)) > 2:
                        continue
                    score = SequenceMatcher(None, normalized_name, normalized_label).ratio()
                    if score > best_score:
                        best_score = score
                        best_table = str(table_name)
            if best_table and best_score >= 0.84:
                return best_table

        return ""

    def _table_hint_from_query(self, query: str) -> str:
        text_query = str(query or "").strip()
        if not text_query:
            return ""

        candidates: list[str] = []
        stripped = re.sub(r"[`\"']", " ", text_query)
        simplified = re.sub(r"\b(table|tables|entity|entities|records?|rows?|data)\b", " ", stripped, flags=re.IGNORECASE)
        simplified = re.sub(r"\s+", " ", simplified).strip()
        if simplified:
            candidates.append(simplified)

        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text_query):
            candidates.append(str(token).strip())

        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            resolved = self._canonical_table_name(candidate)
            if resolved:
                return resolved
        return ""

    @classmethod
    def _looks_like_direct_operation_query(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        patterns_cfg = cls._sql_builder_patterns_config()
        for pattern in [str(item).strip() for item in (patterns_cfg.get("direct_operation_patterns") or []) if str(item).strip()]:
            try:
                if re.match(pattern, text_query, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False

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
        if len(str(query or "").strip()) <= cls._llm_skip_short_query_length():
            return True
        return False

    @staticmethod
    def _is_destructive_query(text: Any) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        if "filter" in lowered:
            return False
        return bool(re.search(r"\b(delete|remove|drop|truncate|wipe|erase|purge|destroy)\b", lowered))

    @staticmethod
    def _is_non_delete_operation_query(text: Any) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        return bool(re.search(r"\b(show|list|get|find|view|select|create|add|new|insert|update|change|modify|edit|set|mark)\b", lowered))

    @classmethod
    def _has_recent_destructive_context(cls, metadata: Dict[str, Any]) -> bool:
        if not isinstance(metadata, dict):
            return False
        recent = metadata.get("_recent_conversation")
        if not isinstance(recent, list):
            return False

        user_messages: list[str] = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            if str(item.get("role", "")).strip().lower() != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                user_messages.append(content)

        for content in reversed(user_messages[-3:]):
            if cls._is_destructive_query(content):
                return True
            if cls._is_non_delete_operation_query(content):
                return False
        return False

    @classmethod
    def _unsupported_delete_message(cls, query: str, table: str = "") -> str:
        lowered = str(query or "").strip().lower()
        resolved_table = str(table or "").strip()
        if re.search(r"\b(db|database)\b", lowered):
            return "I can't delete the database. Destructive delete/drop requests are blocked."
        if re.search(r"\b(everything|all data|all records|all rows|entire database|whole database)\b", lowered):
            return "I can't delete everything or wipe all data. Destructive delete/drop requests are blocked."
        if resolved_table:
            return (
                f"I can't delete records from `{resolved_table}`. "
                "Destructive delete requests are blocked. I can help you review records or perform supported updates instead."
            )
        return "I can't perform delete/drop requests in this assistant. Destructive deletion is blocked."

    @classmethod
    def _looks_like_sql_statement(cls, query: str) -> bool:
        text_query = str(query or "").strip()
        if not text_query:
            return False
        patterns_cfg = cls._sql_builder_patterns_config()
        for item in patterns_cfg.get("sql_statement_guard_patterns") or []:
            if not isinstance(item, dict):
                continue
            start_pattern = str(item.get("start", "")).strip()
            required_pattern = str(item.get("required", "")).strip()
            if not start_pattern or not required_pattern:
                continue
            try:
                if re.match(start_pattern, text_query, flags=re.IGNORECASE):
                    return bool(re.search(required_pattern, text_query, flags=re.IGNORECASE))
            except re.error:
                continue
        passthrough_pattern = str(patterns_cfg.get("sql_statement_passthrough_pattern", "")).strip()
        if not passthrough_pattern:
            return False
        try:
            return bool(re.match(passthrough_pattern, text_query, flags=re.IGNORECASE))
        except re.error:
            return False

    @staticmethod
    def _supports_keyword_arg(func: Any, arg_name: str) -> bool:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        for param in signature.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return arg_name in signature.parameters

    @staticmethod
    def _is_placeholder_filter_value(value: Any) -> bool:
        text = str(value or "").strip().lower()
        cfg = SQLBuilderNode._sql_builder_config()
        placeholder_values = {
            str(item or "").strip().lower()
            for item in (cfg.get("placeholder_filter_values") or [])
        }
        return text in placeholder_values

    def _extract_forced_table_from_query(self, query: str) -> str:
        text_query = str(query or "").strip()
        patterns_cfg = self._sql_builder_patterns_config()
        for pattern in [str(item).strip() for item in (patterns_cfg.get("forced_table_patterns") or []) if str(item).strip()]:
            try:
                match = re.match(pattern, text_query, flags=re.IGNORECASE)
            except re.error:
                continue
            if not match:
                continue
            groups = match.groupdict() if hasattr(match, "groupdict") else {}
            candidate = str(groups.get("table", "") or (match.group(1) if match.groups() else "")).strip()
            if candidate:
                return self._canonical_table_name(candidate)
        return ""

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
        if str(resolved_table or "").strip() or self._query_mentions_explicit_table(query):
            for pattern in self._explicit_list_request_patterns():
                try:
                    if re.search(pattern, text_query, re.IGNORECASE):
                        return True
                except re.error:
                    continue
        
        # Pronoun/Configurable list request patterns (e.g. "what are they")
        # only valid if we have a context table.
        if str(resolved_table or "").strip():
            patterns = self._list_request_patterns()
            for pattern in patterns:
                try:
                    if re.search(pattern, text_query, re.IGNORECASE):
                        return True
                except re.error:
                    continue
        return False

    @classmethod
    def _is_pure_filter_query(cls, query: str) -> bool:
        text_query = str(query or "").strip()
        if not text_query:
            return False
        patterns_cfg = cls._sql_builder_patterns_config()
        for pattern in [str(item).strip() for item in (patterns_cfg.get("pure_filter_query_patterns") or []) if str(item).strip()]:
            try:
                if re.fullmatch(pattern, text_query, flags=re.IGNORECASE):
                    return True
            except re.error:
                continue
        lowered = text_query.lower()
        cfg = cls._sql_builder_config()
        common_terms = {
            str(item).strip().lower()
            for item in (cfg.get("pure_filter_terms") or [])
            if str(item).strip()
        }
        common_terms.update(set(cls._date_phrase_map().keys()))
        common_terms.update(set(cls._status_phrase_map().keys()))
        if lowered in common_terms:
            return True
        return False

    @classmethod
    def _count_request_patterns(cls) -> List[str]:
        cfg = cls._entity_behavior_config()
        patterns = [str(item).strip() for item in (cfg.get("count_request_patterns") or []) if str(item).strip()]
        return patterns

    @classmethod
    def _is_count_request(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        return any(re.search(pattern, text_query) for pattern in cls._count_request_patterns())

    # ── Cross-entity negation count detection ────────────────────────
    # All patterns and entity mappings are read from the domain config
    # (entity_behavior.cross_entity_negation) — nothing is hardcoded here.

    @classmethod
    def _cross_entity_negation_config(cls) -> Dict[str, Any]:
        """Read cross-entity negation config from domain entity_behavior."""
        cfg = cls._entity_behavior_config()
        return cfg.get("cross_entity_negation") or {}

    @classmethod
    def _cross_entity_negation_patterns(cls) -> List[str]:
        """Get negation regex patterns from domain config."""
        neg_cfg = cls._cross_entity_negation_config()
        patterns = [str(p).strip() for p in (neg_cfg.get("patterns") or []) if str(p).strip()]
        return patterns

    @classmethod
    def _matches_cross_entity_negation_pattern(cls, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        for pattern in cls._cross_entity_negation_patterns():
            try:
                if re.search(pattern, text_query, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False

    @classmethod
    def _cross_entity_mappings(cls) -> Dict[str, Dict[str, str]]:
        """Get entity pair mappings from domain config.

        Keys are 'subject__object' (e.g. 'facility__task_transaction').
        Values contain: fk_column, date_column, template_today, template_yesterday, template_generic.
        """
        neg_cfg = cls._cross_entity_negation_config()
        mappings = neg_cfg.get("entity_mappings")
        return dict(mappings) if isinstance(mappings, dict) else {}

    @classmethod
    def _cross_entity_date_scope_keys(cls) -> set[str]:
        date_scope_patterns = cls._cross_entity_negation_config().get("date_scope_patterns")
        if not isinstance(date_scope_patterns, dict):
            return set()
        return {
            str(scope or "").strip().lower()
            for scope in date_scope_patterns.keys()
            if str(scope or "").strip()
        }

    @classmethod
    def _negation_date_scope_from_query(cls, query: str) -> str:
        lowered_query = str(query or "").lower()
        neg_cfg = cls._cross_entity_negation_config()
        date_scope_patterns = neg_cfg.get("date_scope_patterns")
        if isinstance(date_scope_patterns, dict):
            for scope, patterns in date_scope_patterns.items():
                normalized_scope = str(scope or "").strip().lower()
                if not normalized_scope:
                    continue
                for pattern in patterns or []:
                    text_pattern = str(pattern).strip()
                    if not text_pattern:
                        continue
                    try:
                        if re.search(text_pattern, lowered_query, re.IGNORECASE):
                            return normalized_scope
                    except re.error:
                        continue
        return ""

    @staticmethod
    def _recent_user_messages(recent_conversation: Any) -> List[str]:
        if not isinstance(recent_conversation, list):
            return []
        messages: List[str] = []
        for item in reversed(recent_conversation):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                messages.append(content)
        return messages

    def _extract_cross_entity_negation_context(self, query: str) -> Dict[str, Any]:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return {}
        patterns = self._cross_entity_negation_patterns()
        if not patterns:
            return {}
        for pattern in patterns:
            try:
                match = re.search(pattern, text_query, re.IGNORECASE)
            except re.error:
                continue
            if not match:
                continue
            groups = match.groupdict() if hasattr(match, "groupdict") else {}
            subject_word = str(groups.get("subject", "")).strip()
            object_word = str(groups.get("object", "")).strip()
            if not subject_word or not object_word:
                continue
            subject_table = self._canonical_table_name(subject_word)
            object_table = self._canonical_table_name(object_word)
            if subject_table and object_table and subject_table != object_table:
                return {
                    "subject_table": subject_table,
                    "object_table": object_table,
                    "date_scope": self._negation_date_scope_from_query(text_query),
                }
        return {}

    def _normalize_negation_context(
        self,
        context: Dict[str, Any] | None,
        pending_table: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        context_subject = self._canonical_table_name(context.get("subject_table") or pending_table)
        context_object = self._canonical_table_name(context.get("object_table"))
        if not context_subject or not context_object or context_subject == context_object:
            return {}
        mapping_key = f"{context_subject}__{context_object}"
        if mapping_key not in self._cross_entity_mappings():
            return {}
        context_scope = str(context.get("date_scope", "")).strip().lower()
        if context_scope not in self._cross_entity_date_scope_keys():
            context_scope = ""
        return {
            "subject_table": context_subject,
            "object_table": context_object,
            "date_scope": context_scope,
        }

    def _infer_negation_context_from_recent_conversation(
        self,
        recent_conversation: Any,
        pending_table: str = "",
    ) -> Dict[str, Any]:
        pending_subject = self._canonical_table_name(pending_table)
        for user_message in self._recent_user_messages(recent_conversation):
            candidate = self._extract_cross_entity_negation_context(user_message)
            if not candidate:
                continue
            normalized = self._normalize_negation_context(candidate, pending_table=pending_table)
            if not normalized:
                continue
            if pending_subject and normalized.get("subject_table") != pending_subject:
                continue
            return normalized
        return {}

    def _is_cross_entity_negation_count(
        self,
        query: str,
        pending_table: str = "",
        pending_negation_context: Dict[str, Any] | None = None,
        recent_conversation: Any = None,
    ) -> Dict[str, Any]:
        """Detect cross-entity negation queries using domain config patterns.

        Returns:
            Dict with 'subject_table', 'object_table', and 'is_list_request' if detected,
            otherwise empty dict.
        """
        text_query = str(query or "").strip().lower()
        if not text_query:
            return {}

        query_context = self._extract_cross_entity_negation_context(text_query)
        list_context_table = str(query_context.get("subject_table") or pending_table or "").strip()

        # Must be either a count request OR a likely list request 
        is_count = self._is_count_request(text_query)
        is_list = self._is_explicit_list_request(text_query, resolved_table=list_context_table)
        
        if not (is_count or is_list):
            return {}

        if query_context:
            return {
                "subject_table": query_context.get("subject_table"),
                "object_table": query_context.get("object_table"),
                "is_list_request": is_list and not is_count,
                "date_scope": str(query_context.get("date_scope", "")).strip().lower(),
            }

        # Follow-up pronoun requests ("what are they") can reuse the last negation context.
        if is_list:
            normalized_context = self._normalize_negation_context(
                pending_negation_context,
                pending_table=pending_table,
            )
            if not normalized_context:
                normalized_context = self._infer_negation_context_from_recent_conversation(
                    recent_conversation,
                    pending_table=pending_table,
                )
            if normalized_context:
                return {
                    "subject_table": normalized_context.get("subject_table"),
                    "object_table": normalized_context.get("object_table"),
                    "is_list_request": True,
                    "date_scope": str(normalized_context.get("date_scope", "")).strip().lower(),
                }
        return {}

    def _build_cross_entity_negation_sql(
        self,
        subject_table: str,
        object_table: str,
        tenant_value: Any,
        query: str,
        is_list_request: bool = False,
        date_scope: str = "",
    ) -> Tuple[str, str]:
        """Build a NOT IN / anti-join query using templates from schema_manifest.json.

        First tries to find a dedicated template via the entity_mappings config.
        Falls back to dynamic SQL built from entity_mappings config fields.
        """
        lowered_query = str(query or "").lower()
        resolved_date_scope = self._negation_date_scope_from_query(lowered_query)
        if not resolved_date_scope:
            fallback_scope = str(date_scope or "").strip().lower()
            if fallback_scope in self._cross_entity_date_scope_keys():
                resolved_date_scope = fallback_scope
        resolved_list_request = bool(
            is_list_request or self._is_explicit_list_request(query, resolved_table=subject_table)
        )

        # Look up entity mapping from domain config
        mapping_key = f"{subject_table}__{object_table}"
        mappings = self._cross_entity_mappings()
        entity_map = mappings.get(mapping_key, {})

        # Determine template key from config mapping
        template_key = None
        prefix = "list_" if resolved_list_request else ""

        if resolved_date_scope:
            template_key = str(entity_map.get(f"template_{resolved_date_scope}", "")).strip() or None

        if not template_key:
            template_key = str(entity_map.get("template_generic", "")).strip() or None

        if template_key:
            template_key = prefix + template_key

        # Try dedicated template from schema_manifest.json query_templates
        catalog = getattr(self.sql_builder, "catalog", None)
        get_template = getattr(catalog, "get_query_template", None)

        if callable(get_template) and template_key:
            template = get_template(subject_table, template_key)
            if template:
                sql = str(template)
                tenant_context = self._tenant_template_context_for(subject_table, tenant_value)
                for k, v in tenant_context.items():
                    sql = sql.replace(f"{{{k}}}", str(v))
                return sql, ""

        # Dynamic fallback: build from entity_mappings config fields
        if not entity_map:
            return "", f"No cross-entity mapping configured for {subject_table} -> {object_table}."

        fk_column = str(entity_map.get("fk_column", "")).strip()
        date_column = str(entity_map.get("date_column", "")).strip()
        if not fk_column:
            return "", f"No foreign-key column configured for {subject_table} -> {object_table}."

        tenant_scope = self._tenant_scope(subject_table)
        tenant_column = str(tenant_scope.get("column", "")).strip()
        safe_tenant = str(tenant_value or "NULL")
        if isinstance(tenant_value, str) and not tenant_value.isdigit():
            safe_tenant = f"'{tenant_value}'"

        date_clause = ""
        if date_column and resolved_date_scope:
            sql_template = str((self._cross_entity_negation_config().get("date_scope_sql") or {}).get(resolved_date_scope, "")).strip()
            if sql_template:
                rendered_clause = self._format_template(sql_template, date_column=date_column)
                if rendered_clause:
                    date_clause = f" AND {rendered_clause}"

        tenant_where = f"st.{tenant_column} = {safe_tenant}" if tenant_column else "1=1"

        if resolved_list_request:
            # Robust listing alias: id + name (or whatever looks good)
            select_clause = "st.id, st.name" if "name" in self.sql_builder.catalog.important_columns(subject_table) else "st.*"
            sql = (
                f"SELECT {select_clause} "
                f"FROM {subject_table} st "
                f"WHERE {tenant_where} "
                f"AND st.id NOT IN ("
                f"SELECT DISTINCT ot.{fk_column} FROM {object_table} ot"
                f" WHERE 1=1{date_clause}"
                f");"
            )
        else:
            sql = (
                f"SELECT COUNT(*) AS {subject_table}s_without_{object_table}s "
                f"FROM {subject_table} st "
                f"WHERE {tenant_where} "
                f"AND st.id NOT IN ("
                f"SELECT DISTINCT ot.{fk_column} FROM {object_table} ot"
                f" WHERE 1=1{date_clause}"
                f");"
            )
        return sql, ""

    def _tenant_template_context_for(self, table: str, tenant_value: Any) -> Dict[str, str]:
        """Build template context for a given table's tenant scope."""
        scope = self._tenant_scope(table)
        safe_val = str(tenant_value) if tenant_value is not None else "NULL"
        context: Dict[str, str] = {}
        for key in ("column", "metadata_key"):
            col = str(scope.get(key, "")).strip()
            if col:
                context[col] = safe_val
        tenant_cfg = self._sql_builder_tenant_config()
        for key in [str(item).strip() for item in (tenant_cfg.get("template_context_keys") or []) if str(item).strip()]:
            context.setdefault(key, safe_val)
        return context

    def _build_count_from_filters(self, table: str, filters: Dict[str, Any], tenant_value: Any) -> Tuple[str, str]:
        count_builder = getattr(self.sql_builder, "build_count_from_filters", None)
        if callable(count_builder):
            return count_builder(table, filters, tenant_value)

        select_builder = getattr(self.sql_builder, "build_select_from_filters", None)
        if not callable(select_builder):
            message = str(self._sql_builder_messages_config().get("count_builder_not_supported", "")).strip()
            return "", message

        select_sql, select_err = select_builder(table, filters, tenant_value)
        if select_err:
            return "", select_err

        normalized = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*;?\s*$", "", select_sql, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+ORDER\s+BY\s+.+$", "", normalized, flags=re.IGNORECASE)
        return f"SELECT COUNT(*) AS total_count FROM ({normalized}) count_rows;", ""

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
        if cls._matches_cross_entity_negation_pattern(query):
            return False
        value = cls._extract_inline_user_reference(query)
        if not value:
            return False
        return value.lower() not in cls._self_aliases()

    @classmethod
    def _extract_inline_user_reference(cls, query: str) -> str:
        user_alias_patterns = [
            re.escape(str(alias).strip().replace("_", " "))
            for alias in cls._user_lookup_filter_keys()
            if str(alias).strip()
        ]
        alias_group = "|".join(sorted(set(user_alias_patterns), key=len, reverse=True))
        if not alias_group:
            return ""
        text_query = str(query or "").strip()
        match = re.search(
            rf"\b(?P<alias>{alias_group})\b\s+(?P<value>[a-zA-Z0-9_]+)\b",
            text_query,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        value = str(match.group("value") or "").strip()
        if not value:
            return ""
        if match.end("value") < len(text_query) and text_query[match.end("value")] in {"'", "’"}:
            return ""
        return value

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
    def _extract_iso_date_literal(cls, query: str) -> str:
        text_query = str(query or "").strip()
        if not text_query:
            return ""
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text_query)
        return str(match.group(1) or "").strip() if match else ""

    @classmethod
    def _strip_trailing_date_clause(cls, candidate: str) -> str:
        text = str(candidate or "").strip()
        if not text:
            return ""
        patterns_cfg = cls._sql_builder_patterns_config()
        trailing_clause_pattern = str(patterns_cfg.get("trailing_date_clause_pattern", "")).strip()
        if trailing_clause_pattern:
            try:
                text = re.sub(trailing_clause_pattern, "", text, flags=re.IGNORECASE).strip()
            except re.error:
                pass

        date_terms = [
            str(item).strip()
            for item in (
                list(cls._date_phrase_map().keys())
                + list(cls._status_phrase_map().keys())
                + list(cls._location_filter_keys())
            )
            if str(item).strip()
        ]
        if date_terms:
            split_pattern = "|".join(sorted({re.escape(term) for term in date_terms}, key=len, reverse=True))
            text = re.split(rf"\b({split_pattern})\b", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()

        return text.strip(" ,")

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

        iso_date = cls._extract_iso_date_literal(query)
        if iso_date:
            normalized.setdefault(date_key, iso_date)

        task_for_match = None
        patterns_cfg = cls._sql_builder_patterns_config()
        clause_patterns = [str(item).strip() for item in (patterns_cfg.get("task_for_clause_patterns") or []) if str(item).strip()]
        for keyword in cls._primary_keywords():
            keyword_pattern = re.escape(keyword).replace(r"\ ", r"\s+")
            for clause_pattern in clause_patterns:
                rendered_pattern = clause_pattern.replace("{keyword}", keyword_pattern)
                try:
                    task_for_match = re.search(rendered_pattern, query, re.IGNORECASE)
                except re.error:
                    continue
                if task_for_match:
                    break
            if task_for_match:
                break
        if task_for_match:
            candidate = cls._strip_trailing_date_clause(str(task_for_match.group(1) or "").strip())
            location_terms = [str(k).strip().replace("_", " ") for k in cls._location_filter_keys() if str(k).strip()]
            date_terms = [str(k).strip() for k in cls._date_phrase_map().keys()]
            status_terms = [str(k).strip() for k in cls._status_phrase_map().keys()]
            filter_cfg = cls._sql_builder_filter_config()
            extra_split_terms = [
                str(item).strip()
                for item in (filter_cfg.get("task_for_split_terms") or [])
                if str(item).strip()
            ]
            split_terms = (
                extra_split_terms
                + ([cls._priority_filter_key().replace("_", " ")] if cls._priority_filter_key() else [])
                + [phrase for phrase in cls._all_users_aliases() if str(phrase).strip()]
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
        inline_user = ""
        if not cls._matches_cross_entity_negation_pattern(query):
            inline_user = cls._extract_inline_user_reference(query)
        if inline_user:
            excluded = {str(item).strip().lower() for item in cls._primary_keywords()}
            date_terms = {str(k).strip().lower() for k in cls._date_phrase_map().keys()}
            filter_cfg = cls._sql_builder_filter_config()
            excluded_values = {
                str(item).strip().lower()
                for item in (filter_cfg.get("inline_user_excluded_values") or [])
                if str(item).strip()
            }
            if inline_user.lower() not in (excluded | cls._self_aliases() | excluded_values | date_terms):
                normalized[user_name_key] = inline_user
        if cls._requests_all_users(lowered):
            removable = cls._user_filter_keys() | {user_name_key, cls._user_id_filter_key()} | set(cls._user_lookup_filter_keys())
            for key in removable:
                normalized.pop(key, None)
        return normalized

    def _generate_dynamic_filter_options(self, table: str) -> list[Dict[str, str]]:
        """Generate filter options with a simple config-first strategy."""
        try:
            ui_cfg = self._sql_builder_ui_config()
            empty_option_payload = ui_cfg.get("empty_option") if isinstance(ui_cfg.get("empty_option"), dict) else {}
            empty_option = {
                "label": str(empty_option_payload.get("label", "")).strip(),
                "value": str(empty_option_payload.get("value", "")).strip(),
            }
            # Primary entity: use explicit domain menu options.
            if table == self._primary_table():
                configured = self._primary_menu_options()
                if configured:
                    return configured[:6]

            columns = sorted(self.sql_builder.catalog.important_columns(table))
            if not columns:
                return [empty_option] if empty_option["label"] else []

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
                date_options: list[Dict[str, str]] = []
                for item in ui_cfg.get("dynamic_date_options") or []:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("label", "")).strip()
                    value_template = str(item.get("value_template", "")).strip()
                    value = self._format_template(value_template, date_key=date_key)
                    if label and value:
                        date_options.append({"label": label, "value": value})
                options = date_options + options

            return options[:6] if options else ([empty_option] if empty_option["label"] else [])
        except Exception as e:
            logger.error(f"Failed to generate dynamic filters for {table}: {e}", exc_info=True)
            ui_cfg = self._sql_builder_ui_config()
            empty_option_payload = ui_cfg.get("empty_option") if isinstance(ui_cfg.get("empty_option"), dict) else {}
            label = str(empty_option_payload.get("label", "")).strip()
            value = str(empty_option_payload.get("value", "")).strip()
            return [{"label": label, "value": value}] if label else []

    def _sanitize_prefilled_filters(self, table: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(filters or {})
        cleaned: Dict[str, Any] = {}
        filter_cfg = self._sql_builder_filter_config()
        drop_keys = {
            str(item).strip().lower()
            for item in (filter_cfg.get("drop_keys") or [])
            if str(item).strip()
        }
        date_aliases = [str(item).strip() for item in (filter_cfg.get("date_aliases") or []) if str(item).strip()]
        duplicate_name_keys = [str(item).strip() for item in (filter_cfg.get("duplicate_name_keys") or []) if str(item).strip()]

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
            if k.lower() in drop_keys:
                continue
            cleaned[k] = value

        # Normalize date aliases to configured primary date key.
        date_key = self._date_filter_key()
        if date_key and date_key not in cleaned:
            for alias in date_aliases:
                if alias in cleaned:
                    cleaned[date_key] = cleaned[alias]
                    break
        for alias in date_aliases:
            if alias != date_key:
                cleaned.pop(alias, None)

        # When assignee is already inferred, plain name often duplicates it.
        user_related = set(self._user_lookup_filter_keys()) | {self._user_name_filter_key(), self._user_id_filter_key()}
        if any(k in cleaned for k in user_related):
            for duplicate_key in duplicate_name_keys:
                cleaned.pop(duplicate_key, None)

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

    def _normalize_lookup_value(self, column: str, value: Any) -> Any:
        normalizer = getattr(self.sql_builder, "_normalize_enum_value", None)
        if callable(normalizer):
            try:
                return normalizer(column, value)
            except Exception:
                pass
        return value

    @staticmethod
    def _normalize_date_label(value: Any) -> str:
        text_value = str(value or "").strip()
        if not text_value:
            return ""
        return text_value[:16].replace("T", " ")

    def _friendly_entity_name(self, table: str) -> str:
        if table == self._primary_table():
            return "task"
        catalog = getattr(self.sql_builder, "catalog", None)
        aliases_getter = getattr(catalog, "aliases", None)
        if callable(aliases_getter):
            try:
                aliases = [str(a or "").strip() for a in (aliases_getter(table) or []) if str(a or "").strip()]
            except Exception:
                aliases = []
            for alias in aliases:
                normalized = alias.replace("_", " ").strip()
                if not normalized:
                    continue
                if normalized.lower() == str(table or "").strip().lower():
                    continue
                if normalized.endswith("s") and len(normalized) > 3:
                    normalized = normalized[:-1]
                return normalized
        label = str(table or "").strip().replace("_", " ")
        if label.endswith("s") and len(label) > 3:
            return label[:-1]
        return label or "record"

    def _format_update_candidate_label(self, table: str, row: Dict[str, Any]) -> str:
        if table == self._primary_table():
            task_name = str(row.get("task_name") or row.get("task_id") or "Task").strip()
            facility_name = str(row.get("facility_name") or "").strip()
            status_value = row.get("status")
            domain = self._current_domain()
            if hasattr(domain, "get_enum_label"):
                try:
                    status_label = str(domain.get_enum_label("status", status_value) or "").strip()
                except Exception:
                    status_label = str(status_value or "").strip()
            else:
                status_label = str(status_value or "").strip()
            scheduled_label = self._normalize_date_label(row.get("scheduled_date"))
            parts = [task_name, facility_name, status_label, scheduled_label]
            return " | ".join(part for part in parts if part)

        title = str(row.get("title") or row.get("name") or self._friendly_entity_name(table).title()).strip()
        location = str(row.get("location_name") or row.get("facility_name") or "").strip()
        status_label = str(row.get("status") or "").strip()
        scheduled_label = self._normalize_date_label(row.get("scheduled_date"))
        parts = [title, location, status_label, scheduled_label]
        return " | ".join(part for part in parts if part)

    def _selection_filters_for_update(
        self,
        explicit_filters: Dict[str, Any],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        selection_filters: Dict[str, Any] = {}
        normalized_fields = {
            str(k or "").strip(): str(v or "").strip().lower()
            for k, v in (fields or {}).items()
            if str(k or "").strip() and str(v or "").strip()
        }
        for raw_key, raw_value in (explicit_filters or {}).items():
            key = str(raw_key or "").strip()
            value = str(raw_value or "").strip()
            if not key or not value:
                continue
            if key == "id":
                continue
            if normalized_fields.get(key, "") == value.lower():
                continue
            selection_filters[key] = raw_value
        return selection_filters

    def _lookup_primary_task_update_candidates(
        self,
        metadata: Dict[str, Any],
        selection_filters: Dict[str, Any],
        limit: int = 5,
    ) -> List[Dict[str, str]]:
        table = self._primary_table()
        tenant_scope = self._tenant_scope(table)
        tenant_column = str(tenant_scope.get("column", "")).strip()
        tenant_value = self._tenant_value(table, metadata)
        if tenant_column and tenant_value is None:
            return []

        base_filters = dict(selection_filters or {})
        attempts = [base_filters]
        if base_filters:
            attempts.append({})

        status_key = self._status_filter_key()
        date_key = self._date_filter_key()
        user_id_key = self._user_id_filter_key()
        priority_key = self._priority_filter_key()
        facility_keys = {
            key
            for key in [self._location_name_filter_key(), "facility_name", "facility", "site", "location"]
            if str(key or "").strip()
        }

        for attempt_filters in attempts:
            where_parts: List[str] = []
            params: Dict[str, Any] = {}
            if tenant_column and tenant_value is not None:
                where_parts.append(f"tt.`{tenant_column}` = :tenant_value")
                params["tenant_value"] = tenant_value

            status_value = str(attempt_filters.get(status_key, "")).strip()
            if status_value:
                where_parts.append("tt.`status` = :status_value")
                params["status_value"] = self._normalize_lookup_value(status_key, status_value)

            user_id_value = str(attempt_filters.get(user_id_key, "")).strip()
            if user_id_value:
                where_parts.append("tt.`assigned_user_id` = :assigned_user_id")
                params["assigned_user_id"] = user_id_value

            priority_value = str(attempt_filters.get(priority_key, "")).strip()
            if priority_value:
                where_parts.append("tt.`priority` = :priority_value")
                params["priority_value"] = self._normalize_lookup_value(priority_key, priority_value)

            date_value = str(attempt_filters.get(date_key, "")).strip().lower()
            if date_value == "today":
                where_parts.append("DATE(tt.`scheduled_date`) = CURDATE()")
            elif date_value == "yesterday":
                where_parts.append("DATE(tt.`scheduled_date`) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)")
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                where_parts.append("DATE(tt.`scheduled_date`) = :scheduled_date")
                params["scheduled_date"] = date_value

            facility_value = ""
            for facility_key in facility_keys:
                candidate = str(attempt_filters.get(facility_key, "")).strip()
                if candidate:
                    facility_value = candidate
                    break
            if facility_value:
                where_parts.append("LOWER(COALESCE(f.`name`, '')) LIKE :facility_name")
                params["facility_name"] = f"%{facility_value.lower()}%"

            sql = (
                "SELECT tt.`id` AS id, tt.`task_id` AS task_id, td.`name` AS task_name, "
                "tt.`status` AS status, tt.`scheduled_date` AS scheduled_date, "
                "f.`name` AS facility_name, "
                "TRIM(CONCAT(COALESCE(u.`first_name`, ''), ' ', COALESCE(u.`last_name`, ''))) AS assignee_name "
                "FROM `task_transaction` tt "
                "LEFT JOIN `task_description` td ON tt.`task_description_id` = td.`id` "
                "LEFT JOIN `facility` f ON tt.`facility_id` = f.`id` "
                "LEFT JOIN `user` u ON tt.`assigned_user_id` = u.`id` "
            )
            if where_parts:
                sql += "WHERE " + " AND ".join(where_parts) + " "
            sql += f"ORDER BY tt.`id` DESC LIMIT {max(1, int(limit or 1))}"

            try:
                rows = self._query_rows(metadata, sql, params)
            except Exception:
                rows = []
            if not rows:
                continue

            options: List[Dict[str, str]] = []
            for index, row in enumerate(rows, start=1):
                record_id = str((row or {}).get("id") or "").strip()
                if not record_id:
                    continue
                options.append(
                    {
                        "label": self._format_update_candidate_label(table, dict(row or {})),
                        "value": str(index),
                        "record_id": record_id,
                    }
                )
            if options:
                return options
        return []

    def _lookup_generic_update_candidates(
        self,
        table: str,
        metadata: Dict[str, Any],
        limit: int = 5,
    ) -> List[Dict[str, str]]:
        allowed = {str(col or "").strip() for col in self.sql_builder.catalog.important_columns(table)}
        tenant_scope = self._tenant_scope(table)
        tenant_column = str(tenant_scope.get("column", "")).strip()
        tenant_value = self._tenant_value(table, metadata)
        if tenant_column and tenant_value is None:
            return []

        if "title" not in allowed and "name" not in allowed:
            return []

        label_column = "title" if "title" in allowed else "name"
        status_column = "status" if "status" in allowed else ""
        scheduled_column = "scheduled_date" if "scheduled_date" in allowed else ""
        where_parts: List[str] = []
        params: Dict[str, Any] = {}
        if tenant_column and tenant_value is not None:
            where_parts.append(f"`{tenant_column}` = :tenant_value")
            params["tenant_value"] = tenant_value

        select_columns = [f"`id` AS id", f"`{label_column}` AS {label_column}"]
        if status_column:
            select_columns.append(f"`{status_column}` AS status")
        if scheduled_column:
            select_columns.append(f"`{scheduled_column}` AS scheduled_date")

        sql = f"SELECT {', '.join(select_columns)} FROM `{table}`"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += f" ORDER BY `id` DESC LIMIT {max(1, int(limit or 1))}"

        try:
            rows = self._query_rows(metadata, sql, params)
        except Exception:
            rows = []
        options: List[Dict[str, str]] = []
        for index, row in enumerate(rows, start=1):
            record_id = str((row or {}).get("id") or "").strip()
            if not record_id:
                continue
            options.append(
                {
                    "label": self._format_update_candidate_label(table, dict(row or {})),
                    "value": str(index),
                    "record_id": record_id,
                }
            )
        return options

    def _lookup_update_candidates(
        self,
        table: str,
        selection_filters: Dict[str, Any],
        metadata: Dict[str, Any],
        limit: int = 5,
    ) -> List[Dict[str, str]]:
        if table == self._primary_table():
            return self._lookup_primary_task_update_candidates(metadata, selection_filters, limit=limit)
        return self._lookup_generic_update_candidates(table, metadata, limit=limit)

    def _missing_update_change_message(self, table: str) -> str:
        entity = self._friendly_entity_name(table)
        return (
            f"Tell me which {entity} to update and what should change. "
            f"For example: mark the {entity} as completed."
        )

    def _missing_update_target_message(self, table: str) -> str:
        entity = self._friendly_entity_name(table)
        return (
            f"Tell me which {entity} to update. "
            f"You can mention its name, location, assignee, or pick it from the list below."
        )

    def _build_update_selection_prompt(
        self,
        table: str,
        fields: Dict[str, Any],
        selection_filters: Dict[str, Any],
        options: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        entity = self._friendly_entity_name(table)
        ui_options = [
            {"label": str((opt or {}).get("label", "")).strip(), "value": str((opt or {}).get("value", "")).strip()}
            for opt in (options or [])
            if str((opt or {}).get("label", "")).strip() and str((opt or {}).get("value", "")).strip()
        ]
        workflow_payload = {
            "workflow_id": self._select_workflow_id(),
            "state": "choose_update_target",
            "completed": False,
            "mode": self._select_workflow_mode() or "menu",
            "next_field": "target_record",
            "collected_data": {
                "operation": "update",
                "table": table,
                "required_fields": ["target_record"],
                "collected_fields": dict(selection_filters or {}),
            },
            "ui": {
                "type": self._select_workflow_mode() or "menu",
                "title": f"Choose the {entity} to update",
                "options": ui_options,
                "suggested_fields": ["name", "location", "assignee", "date"],
                "example": f"Pick a {entity} from the list",
            },
        }
        pending_select = {
            "table": table,
            "mode": "update_selection",
            "filters": dict(selection_filters or {}),
            "update_fields": {
                str(k or "").strip(): v
                for k, v in (fields or {}).items()
                if str(k or "").strip() and str(k or "").strip() != "id"
            },
            "selection_options": options,
            "workflow_payload": workflow_payload,
            "prompt_message": self._missing_update_target_message(table),
        }
        return {
            "sql_query": "SKIP",
            "error": None,
            "pending_select": pending_select,
            "workflow_payload": workflow_payload,
            "messages": [AIMessage(content=self._missing_update_target_message(table))],
        }

    def _lookup_facility_candidates(self, value: str, metadata: Dict[str, Any]) -> List[str]:
        query_value = str(value or "").strip()
        if not query_value:
            return []
        cfg = self._location_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip())
        name_column = self._safe_ident(str(cfg.get("name_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        search_limit = self._coerce_int(cfg.get("search_limit"), 1, 1, 200)
        if not table or not name_column:
            return []
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
        table = self._safe_ident(str(cfg.get("table", "")).strip())
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip())
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip())
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip())
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        search_limit = self._coerce_int(cfg.get("search_limit"), 1, 1, 200)
        if not table or not id_column or not first_column or not last_column:
            return []
        tenant_value = self._lookup_tenant_value(metadata, metadata_key, tenant_column)
        query_lower = query_value.lower()
        name_filter_key = self._user_name_filter_key()

        search_where: List[str] = [
            (
                f"(LOWER(`{first_column}`) LIKE :q "
                f"OR LOWER(`{last_column}`) LIKE :q "
                f"OR LOWER(TRIM(CONCAT(COALESCE(`{first_column}`,''), ' ', COALESCE(`{last_column}`,'')))) LIKE :q)"
            )
        ]
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
        table = self._safe_ident(str(cfg.get("table", "")).strip())
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip())
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip())
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip())
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        if not table or not id_column or not first_column or not last_column:
            return ""

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
        table = self._safe_ident(str(cfg.get("table", "")).strip())
        name_column = self._safe_ident(str(cfg.get("name_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        fallback_limit = self._coerce_int(cfg.get("fallback_limit"), 1, 1, 50)
        location_name_key = self._location_name_filter_key()
        if not table or not name_column or not location_name_key:
            return []

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

    def _fallback_user_options(
        self,
        metadata: Dict[str, Any],
        limit_override: int | None = None,
    ) -> List[Dict[str, str]]:
        cfg = self._user_lookup_config()
        table = self._safe_ident(str(cfg.get("table", "")).strip())
        id_column = self._safe_ident(str(cfg.get("id_column", "")).strip())
        first_column = self._safe_ident(str(cfg.get("first_name_column", "")).strip())
        last_column = self._safe_ident(str(cfg.get("last_name_column", "")).strip())
        active_column = self._safe_ident(str(cfg.get("active_column", "")).strip())
        tenant_column = self._safe_ident(str(cfg.get("tenant_column", "")).strip())
        metadata_key = self._safe_ident(str(cfg.get("metadata_key", "")).strip()) or tenant_column
        fallback_limit = self._coerce_int(limit_override, 1, 1, 200) if limit_override is not None else self._coerce_int(cfg.get("fallback_limit"), 1, 1, 100)
        name_filter_key = self._user_name_filter_key()
        if not table or not id_column or not first_column or not last_column or not name_filter_key:
            return []

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

    def _suggest_user_options(self, value: str, metadata: Dict[str, Any], limit: int = 6) -> List[Dict[str, str]]:
        base_options = self._fallback_user_options(
            metadata,
            limit_override=self._user_suggestion_candidate_pool_limit(),
        )
        scored: List[Tuple[float, Dict[str, str]]] = []
        min_score = self._user_suggestion_min_score()
        for opt in base_options:
            label = str((opt or {}).get("label", "")).strip()
            if not label:
                continue
            if not self._is_strong_name_match(value, label):
                continue
            score = self._name_similarity_score(value, label)
            if score < min_score:
                continue
            scored.append((score, dict(opt)))

        scored.sort(key=lambda item: (-item[0], str(item[1].get("label", "")).lower()))
        selected = [item[1] for item in scored[: max(1, int(limit or 1))]]
        return selected

    def _build_disambiguation_prompt(
        self,
        table: str,
        explicit_filters: Dict[str, Any],
        target_field: str,
        options: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        count = len(options or [])
        messages_cfg = self._sql_builder_messages_config()
        if count <= 1:
            message = self._format_template(
                str(messages_cfg.get("disambiguation_single_option", "")).strip(),
                target_field=target_field,
            )
        else:
            message = self._format_template(
                str(messages_cfg.get("disambiguation_multiple_options", "")).strip(),
                target_field=target_field,
            )
        candidate_filters = self._candidate_filters(table)
        payload = self._filter_prompt_payload(
            table,
            candidate_filters or [target_field],
            prefilled_filters=self._sanitize_prefilled_filters(table, explicit_filters),
            options_override=options,
        )
        payload_ui = payload.get("ui") or {}
        ui_cfg = self._sql_builder_ui_config()
        payload_ui["title"] = self._format_template(
            str(ui_cfg.get("disambiguation_title_template", "")).strip(),
            target_field=target_field,
        )
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
            filter_cfg = self._sql_builder_filter_config()
            self_reference_values = {
                str(item).strip().lower()
                for item in (filter_cfg.get("self_reference_filter_values") or [])
                if str(item).strip()
            }

            if user_lower in self_reference_values:
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
            ignored_terms.update(
                {
                    str(item).strip().lower()
                    for item in (filter_cfg.get("ignored_user_filter_values") or [])
                    if str(item).strip()
                }
            )
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
                    suggested = self._suggest_user_options(user_value, metadata, limit=6)
                    if suggested:
                        prompt_payload = self._build_disambiguation_prompt(table, filters, user_name_key, suggested)
                        messages_cfg = self._sql_builder_messages_config()
                        prompt_payload["messages"] = [
                            AIMessage(
                                content=self._format_template(
                                    str(messages_cfg.get("assignee_no_exact_match", "")).strip(),
                                    user_value=user_value,
                                )
                            )
                        ]
                        return filters, prompt_payload
                    messages_cfg = self._sql_builder_messages_config()
                    return filters, {
                        "sql_query": "SKIP",
                        "error": None,
                        "messages": [
                            AIMessage(
                                content=self._format_template(
                                    str(messages_cfg.get("assignee_no_match", "")).strip(),
                                    user_value=user_value,
                                )
                            )
                        ],
                    }
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

    @staticmethod
    def _intent_detection_timeout_seconds() -> float:
        try:
            return max(0.1, float(getattr(settings, "INTENT_DETECTION_TIMEOUT_SECONDS", 2.0) or 2.0))
        except Exception:
            return 2.0

    def _filter_prompt_payload(
        self,
        table: str,
        suggested_fields: list[str],
        prefilled_filters: Dict[str, Any] | None = None,
        options_override: list[Dict[str, str]] | None = None,
    ) -> Dict:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        ui_cfg = self._sql_builder_ui_config()
        if not fields:
            fields = [str(item).strip() for item in (ui_cfg.get("filter_prompt_default_fields") or []) if str(item).strip()]
        
        # Generate dynamic options based on table schema
        dynamic_options = options_override or self._generate_dynamic_filter_options(table)
        
        # Generate example based on first option
        example = str(ui_cfg.get("filter_prompt_example_default", "")).strip()
        if dynamic_options:
            first_val = dynamic_options[0]["value"]
            if "=" in first_val:
                suffix_field = fields[0] if fields else ""
                example = f"{first_val}, {suffix_field}=value" if suffix_field else first_val
        required_field = self._select_workflow_next_field()
        
        return {
            "workflow_id": self._select_workflow_id(),
            "state": self._select_workflow_state(),
            "completed": False,
            "mode": self._select_workflow_mode(),
            "next_field": required_field,
            "collected_data": {
                "operation": self._select_workflow_operation(),
                "table": table,
                "required_fields": [required_field] if required_field else [],
                "collected_fields": dict(prefilled_filters or {}),
            },
            "ui": {
                "type": self._select_workflow_mode(),
                "title": self._format_template(
                    str(ui_cfg.get("filter_prompt_title_template", "")).strip(),
                    table=table,
                ),
                "options": dynamic_options,
                "suggested_fields": fields[:6],
                "example": example,
            },
        }

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = dict(state.get("metadata") or {})
        pending_table = str(metadata.get("pending_select_table", "") or "").strip()
        pending_negation_context = metadata.get("pending_select_negation")
        if not isinstance(pending_negation_context, dict):
            pending_negation_context = {}
        actor_user_id = metadata.get("user_id") or metadata.get("userId")
        usage_accumulator = TokenUsageService.merge(state.get("token_usage"), {})

        def emit(payload: Dict[str, Any]) -> Dict[str, Any]:
            out = dict(payload or {})
            out["token_usage"] = usage_accumulator
            return out

        # If user already supplied SQL, pass it through untouched.
        # Validation and safety checks happen in sql_validate_node.
        if self._looks_like_sql_statement(query):
            return emit({"sql_query": query.strip()})

        intent = dict(state.get("intent") or {})
        intent_mode = self._intent_mode()
        skip_llm_intent = intent_mode == "heuristic" or (
            intent_mode == "auto" and self._should_skip_llm_intent(query, intent)
        )

        if skip_llm_intent:
            detected_intent = self._fallback_intent(query)
            usage_accumulator = TokenUsageService.merge(usage_accumulator, TokenUsageService.skipped_call())
            logger.info("Intent detection mode=heuristic (LLM skipped): %s", detected_intent)
        else:
            detection_timeout_seconds = self._intent_detection_timeout_seconds()
            try:
                detector_with_usage = getattr(self.intent_detector, "detect_intent_with_usage", None)
                if callable(detector_with_usage):
                    detector_kwargs: Dict[str, Any] = {}
                    if pending_table and self._supports_keyword_arg(detector_with_usage, "context_table"):
                        detector_kwargs["context_table"] = pending_table
                    detected_intent, detector_usage = await asyncio.wait_for(
                        detector_with_usage(query, metadata, **detector_kwargs),
                        timeout=detection_timeout_seconds,
                    )
                else:
                    detect_intent = getattr(self.intent_detector, "detect_intent", None)
                    if callable(detect_intent):
                        detector_kwargs = {}
                        if pending_table and self._supports_keyword_arg(detect_intent, "context_table"):
                            detector_kwargs["context_table"] = pending_table
                        detected_intent = await asyncio.wait_for(
                            detect_intent(query, metadata, **detector_kwargs),
                            timeout=detection_timeout_seconds,
                        )
                    else:
                        fallback_detect_intent = self.intent_detector.detect_intent
                        detector_kwargs = {}
                        if pending_table and self._supports_keyword_arg(fallback_detect_intent, "context_table"):
                            detector_kwargs["context_table"] = pending_table
                        detected_intent = await asyncio.wait_for(
                            fallback_detect_intent(query, metadata, **detector_kwargs),
                            timeout=detection_timeout_seconds,
                        )
                    detector_usage = TokenUsageService.empty()
                usage_accumulator = TokenUsageService.merge(usage_accumulator, detector_usage)
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
        table_names = self._catalog_table_names()
        pure_filter_query = self._is_pure_filter_query(query)

        default_operation = self._select_workflow_operation()
        operation = str(intent.get("operation", default_operation) or default_operation).lower()
        destructive_query = operation == "delete" or self._is_destructive_query(query)

        # Strict mode for filter-only input: use pending context table or ask for explicit table.
        if pure_filter_query and not forced_table and not self._query_mentions_explicit_table(query):
            if pending_table and pending_table in table_names:
                table = pending_table
            else:
                return emit(
                    {
                        "sql_query": "SKIP",
                        "error": None,
                        "messages": [
                            AIMessage(
                                content=self._filter_context_prompt()
                            )
                        ],
                    }
                )
        else:
            # Use forced table first (for pending-select followups), then detected/resolved.
            table = forced_table or intent_table or self.sql_builder.resolve_table(query, intent)

        table = self._canonical_table_name(table) or str(table or "").strip()
        if not table and (destructive_query or self._has_recent_destructive_context(metadata)):
            table = self._table_hint_from_query(query)

        if (
            operation != "delete"
            and not destructive_query
            and table
            and not self._is_non_delete_operation_query(query)
            and self._has_recent_destructive_context(metadata)
        ):
            operation = "delete"
            destructive_query = True

        # ── Cross-entity negation query (e.g. "facilities without tasks today") ──
        negation_info = self._is_cross_entity_negation_count(
            query,
            pending_table=pending_table,
            pending_negation_context=pending_negation_context,
            recent_conversation=metadata.get("_recent_conversation"),
        )
        if negation_info:
            subject_table = negation_info["subject_table"]
            object_table = negation_info["object_table"]
            is_list = negation_info.get("is_list_request", False)
            date_scope = str(negation_info.get("date_scope", "")).strip().lower()
            sql, err = self._build_cross_entity_negation_sql(
                subject_table,
                object_table,
                self._tenant_value(subject_table, metadata),
                query,
                is_list_request=is_list,
                date_scope=date_scope,
            )
            if not err:
                return emit(
                    {
                        "sql_query": sql,
                        "pending_select": {
                            "table": subject_table,
                            "negation": {
                                "subject_table": subject_table,
                                "object_table": object_table,
                                "date_scope": date_scope,
                            },
                        },
                    }
                )

        if (
            not forced_table
            and operation == "select"
            and self._looks_like_task_intent(query, prefilters)
            and not self._query_mentions_explicit_table(query)
            and not negation_info  # Don't hijack table for negation queries
        ):
            table = self._primary_table()
        if operation == "delete" or destructive_query:
            return emit(
                {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=self._unsupported_delete_message(query, table=table))],
                }
            )
        if not table:
            return emit(
                {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=self._default_entity_prompt())],
                }
            )
        tenant_value = self._tenant_value(table, metadata)

        fields = {}
        if isinstance(intent.get("fields"), dict):
            fields.update(intent.get("fields"))
        kv_pairs = self.sql_builder.parse_kv_pairs(query)
        fields.update(kv_pairs)
        explicit_filters = prefilters
        explicit_filters, disambiguation_result = self._maybe_disambiguate_filters(table, explicit_filters, metadata)
        if disambiguation_result is not None:
            return emit(disambiguation_result)

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

        # For primary-entity self views, assignee-only filters without an explicit date
        # default to today unless user asked for another date.
        # Do not force today for explicit named-assignee queries (e.g. "for Nirmala").
        if (
            is_task_status
            and any(k in explicit_filters for k in (user_filter_keys | {user_name_key, user_id_key}))
            and not any(str(explicit_filters.get(k, "")).strip() for k in self._date_filter_keys())
            and self._requests_self_tasks(query)
            and not self._mentions_explicit_nonself_user(query)
            and not self._requests_all_users(query)
        ):
            lowered_query = str(query or "").lower()
            range_terms = [re.escape(term) for term in self._primary_date_range_terms() if str(term).strip()]
            range_pattern = r"\b(" + "|".join(range_terms) + r")\b" if range_terms else ""
            if not range_pattern or not re.search(range_pattern, lowered_query):
                default_date_value = self._self_default_date_value()
                if default_date_value:
                    explicit_filters[date_key] = default_date_value

        if operation == "select" and self._is_count_request(query):
            sql, err = self._build_count_from_filters(table, explicit_filters, tenant_value)
            if err:
                messages_cfg = self._sql_builder_messages_config()
                return emit(
                    {
                        "sql_query": "SKIP",
                        "error": None,
                        "messages": [
                            AIMessage(
                                content=str(messages_cfg.get("count_query_error", "")).strip()
                            )
                        ],
                    }
                )
            return emit({"sql_query": sql})

        display_filters = self._sanitize_prefilled_filters(table, explicit_filters)

        # For natural-language primary-entity requests with no inferred filters, show options menu.
        # Structured or inferred filters should continue to SQL execution.
        if is_task_status and not kv_pairs and not explicit_filters and not self._has_task_autorun_context(explicit_filters):
            candidate_filters = self._primary_menu_filters()
            behavior_cfg = self._entity_behavior_config()
            today_label = str(behavior_cfg.get("task_menu_today_label", "")).strip()
            today_value = str(behavior_cfg.get("task_menu_today_value", "")).strip()
            user_option_value = f"{user_name_key}="
            task_options = self._primary_menu_options()
            if task_options:
                task_options = [dict(x) for x in task_options]
                if today_label and today_value:
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
            return emit(
                self._skip_with_filter_prompt(
                    table,
                    candidate_filters,
                    prefilled_filters=display_filters,
                    options_override=task_options,
                )
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
                return emit(self._skip_with_filter_prompt(table, self._candidate_filters(table)))

        explicit_list_request = self._is_explicit_list_request(query, str(table))

        # Generic understanding flow for other SELECT queries:
        # keep inferred filters and ask only for remaining helpful filters.
        if operation == "select" and not is_task_status and not kv_pairs and not explicit_list_request:
            generic_options = self._generate_dynamic_filter_options(table)
            generic_options = self._filter_options_excluding_prefilled(generic_options, display_filters)
            return emit(
                self._skip_with_filter_prompt(
                    table,
                    self._candidate_filters(table),
                    prefilled_filters=display_filters,
                    options_override=generic_options,
                )
            )

        if operation == "insert":
            if not self.sql_builder.catalog.create_enabled(table):
                messages_cfg = self._sql_builder_messages_config()
                return emit(
                    {
                        "sql_query": "SKIP",
                        "messages": [
                            AIMessage(
                                content=self._format_template(
                                    str(messages_cfg.get("create_not_configured", "")).strip(),
                                    table=table,
                                )
                            )
                        ],
                    }
                )

            required = self.sql_builder.catalog.required_create_fields(table)
            if required:
                missing = [f for f in required if not str(fields.get(f, "")).strip()]
                if missing:
                    messages_cfg = self._sql_builder_messages_config()
                    return emit(
                        {
                            "sql_query": "SKIP",
                            "messages": [
                                AIMessage(
                                    content=self._format_template(
                                        str(messages_cfg.get("missing_required_fields", "")).strip(),
                                        fields=", ".join(missing),
                                    )
                                )
                            ],
                        }
                    )
            sql, err = self.sql_builder.build_insert(table, fields, tenant_value, actor_user_id=actor_user_id)
            if err:
                return emit({"sql_query": "SKIP", "messages": [AIMessage(content=err)]})
            return emit({"sql_query": sql})

        if operation == "update":
            update_fields = {
                str(k or "").strip(): v
                for k, v in (fields or {}).items()
                if str(k or "").strip() and str(k or "").strip() != "id"
            }
            if not update_fields:
                return emit(
                    {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=self._missing_update_change_message(table))],
                    }
                )

            record_id = str((fields or {}).get("id") or "").strip()
            if not record_id:
                selection_filters = self._selection_filters_for_update(explicit_filters, update_fields)
                selection_options = self._lookup_update_candidates(
                    table,
                    selection_filters,
                    metadata,
                    limit=5,
                )
                if selection_options:
                    return emit(
                        self._build_update_selection_prompt(
                            table,
                            fields,
                            selection_filters,
                            selection_options,
                        )
                    )
                return emit(
                    {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=self._missing_update_target_message(table))],
                    }
                )

            sql, err = self.sql_builder.build_update(table, fields, tenant_value, actor_user_id=actor_user_id)
            if err:
                if "Update requires id=<record_id>" in str(err or ""):
                    message = self._missing_update_target_message(table)
                elif "Update requires at least one field to change" in str(err or ""):
                    message = self._missing_update_change_message(table)
                else:
                    messages_cfg = self._sql_builder_messages_config()
                    message = self._join_message_parts(
                        err,
                        messages_cfg.get("update_error_suffix", ""),
                    )
                return emit(
                    {
                        "sql_query": "SKIP",
                        "messages": [
                            AIMessage(
                                content=message
                            )
                        ],
                    }
                )
            return emit({"sql_query": sql})

        if not explicit_filters and not explicit_list_request:
            return emit(self._skip_with_filter_prompt(table, self._candidate_filters(table)))

        if explicit_list_request and not explicit_filters:
            builder_with_usage = getattr(self.sql_builder, "build_select_with_usage", None)
            if callable(builder_with_usage):
                sql, builder_usage = await builder_with_usage(query, table, tenant_value, metadata=metadata)
                usage_accumulator = TokenUsageService.merge(usage_accumulator, builder_usage)
            else:
                sql = await self.sql_builder.build_select(query, table, tenant_value)
            select_err = ""
        else:
            sql, select_err = self.sql_builder.build_select_from_filters(table, explicit_filters, tenant_value)
        if select_err:
            return emit(self._skip_with_filter_prompt(table, sorted(self.sql_builder.catalog.important_columns(table))))
        
        # Allow unfiltered queries but add LIMIT to prevent large result sets
        if self._is_unfiltered_select(sql):
            sql = self._apply_unfiltered_select_limit(sql)

        where_cols = self._select_where_columns(sql)
        table_cols = self.sql_builder.catalog.important_columns(table)
        tenant_columns = {str(c).strip().lower() for c in self._tenant_columns(table)}
        requires_tenant_scope = bool(tenant_value) and bool(tenant_columns) and bool({c.lower() for c in table_cols} & tenant_columns)
        if requires_tenant_scope and tenant_columns.isdisjoint(where_cols) and not explicit_list_request:
            return emit(self._skip_with_filter_prompt(table, self._candidate_filters(table, limit=5)))

        non_tenant_filters = {c for c in where_cols if c not in tenant_columns}
        if requires_tenant_scope and not non_tenant_filters and not explicit_list_request:
            return emit(self._skip_with_filter_prompt(table, self._candidate_filters(table, limit=5)))
        return emit({"sql_query": sql})
