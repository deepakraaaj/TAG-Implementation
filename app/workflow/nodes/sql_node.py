import logging
import json
import os
import re
from typing import Dict, Any, List, Optional
import sqlglot
from langchain_core.messages import AIMessage
# Dynamic import for ChatOpenAI to support generic openai clients
from langchain_openai import ChatOpenAI

from app.workflow.state import AgentState
from app.config import get_settings
from app.services.schema_service import SchemaService
from app.services.cache_service import SemanticCache
from app.services.table_selector_service import TableSelectorService
from app.services.person_resolver_service import PersonResolverService
from app.services.query_refiner import QueryRefinerService
from app.services.schema_manifest_service import SchemaManifestService
from app.services.query_understanding_service import QueryUnderstandingService
from app.services.llm_retry_service import ainvoke_with_retry
from app.workflow.prompts import (
    SQL_GEN_PROMPT_TEMPLATE,
    TABLE_SELECTION_PROMPT_TEMPLATE
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _extract_sql_from_text(raw_text: str) -> str:
    """
    Try to recover SQL when model output is malformed JSON/truncated but still
    contains a SQL payload.
    """
    if not raw_text:
        return ""
    text = raw_text.strip()

    def is_valid_candidate(candidate: str) -> bool:
        candidate = candidate.strip()
        if not candidate or candidate.endswith(","):
            return False
        try:
            parsed = sqlglot.parse_one(candidate)
            if candidate.upper().startswith("SELECT"):
                return parsed is not None and " FROM " in f" {candidate.upper()} "
            return parsed is not None
        except Exception:
            return False

    # Direct SQL first line fallback.
    first = text.splitlines()[0].strip().upper()
    if first.startswith(("SELECT", "INSERT", "UPDATE")):
        candidate = text.strip()
        if is_valid_candidate(candidate):
            return candidate

    # JSON-like payload with "content": "SELECT ..."
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)', text, flags=re.DOTALL)
    if m:
        candidate = m.group(1).encode("utf-8").decode("unicode_escape").strip()
        if candidate.upper().startswith(("SELECT", "INSERT", "UPDATE")) and is_valid_candidate(candidate):
            return candidate

    return ""


def _manifest_create_clarification(query: str, intent_analysis: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    q = (query or "").strip().lower()
    if not q:
        return ""

    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    entities = [str(e).lower() for e in intent_analysis.get("entities", []) if str(e).strip()]
    create_signals = ["create", "add", "new", "insert"]
    has_create_signal = any(sig in q for sig in create_signals)
    has_slot_statement = bool(re.search(r"\bname\s+is\b", q) or re.search(r"\bis\s+the\s+\w+\s+name\b", q))
    if intent_type != "mutation" and not has_create_signal and not has_slot_statement:
        return ""

    tables = manifest.get("tables", {})
    if not isinstance(tables, dict):
        return ""

    for table_name, meta in tables.items():
        if not isinstance(meta, dict):
            continue
        create_meta = meta.get("operations", {}).get("create", {})
        if not isinstance(create_meta, dict) or not create_meta.get("enabled", False):
            continue

        aliases = [table_name.lower()]
        custom_aliases = meta.get("aliases", [])
        if isinstance(custom_aliases, list):
            aliases.extend([str(a).lower() for a in custom_aliases if str(a).strip()])
        if table_name.lower().endswith("s"):
            aliases.append(table_name.lower()[:-1])
        else:
            aliases.append(f"{table_name.lower()}s")
        aliases = list(dict.fromkeys(aliases))

        mentions_entity = any(a in q for a in aliases) or any(e in aliases for e in entities)
        if not mentions_entity:
            continue

        slot_markers = [str(m).lower() for m in create_meta.get("slot_markers", []) if str(m).strip()]
        has_quoted_value = bool(re.search(r"['\"].+['\"]", q))
        has_any_detail = any(m in q for m in slot_markers) or has_quoted_value

        required_fields = [str(f).lower() for f in create_meta.get("required_fields", []) if str(f).strip()]
        required_field_markers = create_meta.get("required_field_markers", {})
        missing_required: List[str] = []
        for field in required_fields:
            markers = required_field_markers.get(field, [])
            if not isinstance(markers, list):
                markers = []
            normalized_markers = [str(m).lower() for m in markers if str(m).strip()]
            if not normalized_markers:
                normalized_markers = [field.replace("_", " ")]

            if field == "name":
                present = has_quoted_value or any(m in q for m in normalized_markers)
            else:
                present = any(m in q for m in normalized_markers)
            if not present:
                missing_required.append(field)

        if missing_required and (len(q.split()) <= 20 or has_any_detail):
            return str(
                create_meta.get(
                    "clarification_message",
                    f"Please share details required to create {table_name}.",
                )
            ).strip()

    return ""


def _maybe_build_user_query_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    schema_manifest: SchemaManifestService,
) -> str:
    """
    Deterministic SQL for simple user count/list intents.
    This avoids unstable LLM joins for straightforward prompts like
    "how many users are there" or "list users".
    """
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip()
    if not q:
        return ""

    resolved_table: Optional[str] = schema_manifest.resolve_entity_table(q, intent_analysis)
    if resolved_table != "user":
        return ""

    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    template_kind = ""
    if intent_type == "aggregation":
        template_kind = "count"
    elif intent_type in {"listing", "lookup"}:
        template_kind = "list"
    else:
        return ""

    return schema_manifest.render_query_template("user", template_kind, company_id=company_id)


def _maybe_build_asset_query_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    schema_manifest: SchemaManifestService,
) -> str:
    """
    Deterministic SQL for simple asset count/list intents.
    """
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip()
    if not q:
        return ""

    resolved_table: Optional[str] = schema_manifest.resolve_entity_table(q, intent_analysis)
    if resolved_table != "asset":
        return ""

    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    template_kind = ""
    if intent_type == "aggregation":
        template_kind = "count"
    elif intent_type in {"listing", "lookup"}:
        template_kind = "list"
    else:
        return ""

    return schema_manifest.render_query_template("asset", template_kind, company_id=company_id)


def _safe_sql_str(value: str) -> str:
    return (value or "").replace("'", "''").strip()


def _extract_recent_days_window(query: str, filters: Dict[str, Any]) -> Optional[int]:
    candidates: List[str] = []
    if query:
        candidates.append(str(query))
    if isinstance(filters, dict):
        for key in ("scheduled_date", "date", "date_range", "time_range"):
            value = filters.get(key)
            if value:
                candidates.append(str(value))

    combined = " ".join(candidates).lower()
    if not combined:
        return None

    match = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", combined)
    if not match:
        return None
    try:
        days = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return days


def _collect_date_text(query: str, filters: Dict[str, Any]) -> str:
    chunks: List[str] = []
    if query:
        chunks.append(str(query))
    if isinstance(filters, dict):
        for key in ("scheduled_date", "date", "date_range", "time_range"):
            value = filters.get(key)
            if value:
                chunks.append(str(value))
    return " ".join(chunks).strip()


def _extract_explicit_date_range(query: str, filters: Dict[str, Any]) -> Optional[tuple[str, str]]:
    combined = _collect_date_text(query, filters).lower()
    if not combined:
        return None

    # Supports:
    # - from 2026-01-01 to 2026-01-31
    # - between 2026-01-01 and 2026-01-31
    patterns = [
        r"\bfrom\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\b",
        r"\bbetween\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            start_date = match.group(1)
            end_date = match.group(2)
            if start_date <= end_date:
                return start_date, end_date
    return None


def _extract_relative_period_clause(query: str, filters: Dict[str, Any]) -> str:
    combined = _collect_date_text(query, filters).lower()
    if not combined:
        return ""

    # Existing support: last/past N days.
    recent_days = _extract_recent_days_window(query, filters)
    if recent_days:
        return f"DATE(tt.scheduled_date) >= DATE_SUB(CURDATE(), INTERVAL {recent_days} DAY)"

    # last month
    if re.search(r"\blast\s+month\b", combined):
        return (
            "DATE(tt.scheduled_date) >= DATE_SUB(CURDATE(), INTERVAL 1 MONTH) "
            "AND DATE(tt.scheduled_date) <= CURDATE()"
        )

    # next week (next 7 days window)
    if re.search(r"\bnext\s+week\b", combined):
        return (
            "DATE(tt.scheduled_date) >= CURDATE() "
            "AND DATE(tt.scheduled_date) <= DATE_ADD(CURDATE(), INTERVAL 7 DAY)"
        )

    return ""


def _build_task_date_clause(query: str, filters: Dict[str, Any]) -> str:
    explicit = _extract_explicit_date_range(query, filters)
    if explicit:
        start_date, end_date = explicit
        return (
            f"DATE(tt.scheduled_date) >= '{start_date}' "
            f"AND DATE(tt.scheduled_date) <= '{end_date}'"
        )

    return _extract_relative_period_clause(query, filters)


def _merge_intent_with_understanding(
    intent_analysis: Dict[str, Any],
    query_understanding: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(intent_analysis or {})

    if str(merged.get("intent_type", "")).lower() in {"", "unknown"}:
        mapped_intent = str(query_understanding.get("intent", "unknown")).lower()
        if mapped_intent in {"listing", "aggregation", "lookup", "mutation"}:
            merged["intent_type"] = mapped_intent

    if not merged.get("entities") and query_understanding.get("entities"):
        merged["entities"] = list(query_understanding["entities"])

    return merged


def _extract_labeled_value(text: str, labels: List[str], stop_labels: List[str]) -> str:
    if not text:
        return ""
    pattern = r"|".join(re.escape(lbl) for lbl in labels if lbl)
    stop_pattern = r"|".join(re.escape(lbl) for lbl in stop_labels if lbl)
    if not pattern:
        return ""
    match = re.search(rf"\b(?:{pattern})\b\s*[:=-]?\s*(.+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    value = match.group(1).strip()
    if stop_pattern:
        stopper = re.search(rf"\s+\b(?:{stop_pattern})\b\s*[:=-]?", value, flags=re.IGNORECASE)
        if stopper:
            value = value[: stopper.start()].strip()
    return value.strip(" ,.;")


def _maybe_build_asset_create_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    schema_manifest: SchemaManifestService,
) -> str:
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip()
    if not q:
        return ""

    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    if intent_type != "mutation":
        return ""

    resolved_table: Optional[str] = schema_manifest.resolve_entity_table(q, intent_analysis)
    if resolved_table != "asset":
        return ""

    name_labels = ["asset name", "name", "named"]
    category_id_labels = ["category id", "asset category id", "category_id"]
    category_labels = ["asset category", "category", "type"]

    asset_name = _extract_labeled_value(q, name_labels, category_id_labels + category_labels)
    if not asset_name:
        named_match = re.search(r"\bnamed\b\s+(.+)$", q, flags=re.IGNORECASE)
        if named_match:
            asset_name = named_match.group(1).strip(" ,.;")
    if not asset_name:
        return ""

    category_id_raw = _extract_labeled_value(q, category_id_labels, name_labels + category_labels)
    category_id_match = re.search(r"\d+", category_id_raw) if category_id_raw else None
    if category_id_match:
        return schema_manifest.render_query_template(
            "asset",
            "create_by_category_id",
            asset_name=_safe_sql_str(asset_name),
            asset_category_id=int(category_id_match.group(0)),
            company_id=company_id,
        )

    category_name = _extract_labeled_value(q, category_labels, name_labels + category_id_labels)
    if not category_name:
        return ""

    return schema_manifest.render_query_template(
        "asset",
        "create_by_category_name",
        asset_name=_safe_sql_str(asset_name),
        asset_category_name=_safe_sql_str(category_name),
        company_id=company_id,
    )


def _maybe_build_task_aggregation_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    user_id: Any,
    user_role: Optional[str],
    schema_manifest: SchemaManifestService,
) -> str:
    """
    Deterministic SQL for 'how many' task aggregation queries.
    """
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip().lower()
    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    if intent_type != "aggregation":
        return ""

    task_aliases = ["task", "tasks", "work order", "work orders", "job", "jobs"]
    mentions_task = any(alias in q for alias in task_aliases)
    if not mentions_task:
        return ""

    # Build base query and joins.
    filters = intent_analysis.get("filter_dict", {})
    person = filters.get("person")
    include_user_join = bool(person) and user_role in {"admin", "super_admin"}

    from_clause = "FROM task_transaction tt JOIN facility f ON tt.facility_id = f.id"
    if include_user_join:
        from_clause += " LEFT JOIN user u ON tt.assigned_user_id = u.id"

    sql = f"SELECT COUNT(*) AS total_tasks {from_clause} WHERE f.company_id = {company_id}"

    # Apply Filters from intent analysis

    # 1. Status Filter
    status = filters.get("status")
    if status:
        sql += f" AND LOWER(tt.status) = '{_safe_sql_str(status).lower()}'"

    # 2. Priority Filter
    priority = filters.get("priority")
    if priority:
        sql += f" AND LOWER(tt.priority) = '{_safe_sql_str(priority).lower()}'"

    # 3. Date Filter (supports relative windows and explicit from/to ranges)
    date_clause = _build_task_date_clause(query, filters)
    if date_clause:
        sql += f" AND {date_clause}"

    # 3. Person/User Filter
    # If role is 'user', always restrict to self
    if user_role not in {"admin", "super_admin"}:
        sql += f" AND tt.assigned_user_id = {user_id}"
    else:
        # Admin can filter by other people
        if person:
            person_name = _safe_sql_str(str(person)).lower()
            sql += (
                " AND ("
                f"LOWER(u.first_name) LIKE '%{person_name}%' OR "
                f"LOWER(u.last_name) LIKE '%{person_name}%' OR "
                f"LOWER(CONCAT(u.first_name, ' ', u.last_name)) LIKE '%{person_name}%'"
                ")"
            )

    return sql + ";"


def _maybe_build_task_listing_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    user_id: Any,
    user_role: Optional[str],
    schema_manifest: SchemaManifestService,
) -> str:
    """
    Deterministic SQL for task listing/lookup queries like:
    - "pending tasks"
    - "show high priority tasks"
    - "tasks for nirmala last month"
    """
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip().lower()
    intent_type = str(intent_analysis.get("intent_type", "")).lower()
    if intent_type not in {"listing", "lookup"}:
        return ""

    task_aliases = ["task", "tasks", "work order", "work orders", "job", "jobs"]
    mentions_task = any(alias in q for alias in task_aliases)
    if not mentions_task:
        return ""

    filters = intent_analysis.get("filter_dict", {})
    person = filters.get("person")
    include_user_join = bool(person) and user_role in {"admin", "super_admin"}

    from_clause = (
        "FROM task_transaction tt "
        "JOIN task_description td ON tt.task_description_id = td.id "
        "JOIN facility f ON tt.facility_id = f.id"
    )
    if include_user_join:
        from_clause += " LEFT JOIN user u ON tt.assigned_user_id = u.id"

    sql = (
        "SELECT COUNT(*) OVER() AS _total_count, "
        "tt.id, tt.task_id, td.name AS task_name, tt.status, tt.priority, tt.scheduled_date "
        f"{from_clause} WHERE f.company_id = {company_id}"
    )

    status = filters.get("status")
    if status:
        sql += f" AND LOWER(tt.status) = '{_safe_sql_str(status).lower()}'"

    priority = filters.get("priority")
    if priority:
        sql += f" AND LOWER(tt.priority) = '{_safe_sql_str(priority).lower()}'"

    date_clause = _build_task_date_clause(query, filters)
    if date_clause:
        sql += f" AND {date_clause}"

    if user_role not in {"admin", "super_admin"}:
        sql += f" AND tt.assigned_user_id = {user_id}"
    elif person:
        person_name = _safe_sql_str(str(person)).lower()
        sql += (
            " AND ("
            f"LOWER(u.first_name) LIKE '%{person_name}%' OR "
            f"LOWER(u.last_name) LIKE '%{person_name}%' OR "
            f"LOWER(CONCAT(u.first_name, ' ', u.last_name)) LIKE '%{person_name}%'"
            ")"
        )

    sql += " ORDER BY tt.id DESC LIMIT 100;"
    return sql


def _maybe_build_task_status_sql(
    query: str,
    intent_analysis: Dict[str, Any],
    company_id: Any,
    user_id: Any,
    user_role: Optional[str],
    schema_manifest: SchemaManifestService,
) -> str:
    """
    Deterministic SQL for 'task status' style queries, tailored by user role.
    """
    if not company_id or not schema_manifest:
        return ""

    q = (query or "").strip().lower()
    if not q:
        return ""

    # Detect signals for task status
    # We look for "status" and "task" (or aliases) in the query
    status_signals = ["status", "task status", "how many tasks", "today's status", "today status", "update", "status update", "task update", "tasks update"]
    task_aliases = ["task", "tasks", "work order", "work orders", "job", "jobs"]
    
    mentions_task = any(alias in q for alias in task_aliases)
    mentions_status = any(sig in q for sig in status_signals)
    
    if not (mentions_task and mentions_status):
        return ""

    template_kind = ""
    if user_role in {"admin", "super_admin"}:
        template_kind = "today_status_admin"
    else:
        template_kind = "today_status_user"

    return schema_manifest.render_query_template("task_transaction", template_kind, company_id=company_id, user_id=user_id)


class GenerateSQLNode:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )
        self.schema_service = SchemaService()
        self.cache_service = SemanticCache()
        self.table_selector = TableSelectorService()
        self.person_resolver = PersonResolverService(self.llm, self.schema_service)
        self.query_refiner = QueryRefinerService()
        self.schema_manifest = SchemaManifestService()
        self.query_understanding = QueryUnderstandingService(self.schema_manifest)

    async def run(self, state: AgentState):
        """
        Generates SQL based on the user query and schema.
        """
        logger.info("Entering generate_sql_node")
        
        # Use rewritten query if available, otherwise fallback
        last_message = state.get("rewritten_query") or state["messages"][-1].content
        logger.info(f"Using Query for SQL Gen: {last_message}")
        intent_analysis = state.get("intent_analysis", {})
        query_understanding = state.get("query_understanding")
        if not query_understanding:
            query_understanding = await self.query_understanding.analyze(
                str(last_message), state.get("messages", [])
            )

        effective_intent_analysis = _merge_intent_with_understanding(intent_analysis, query_understanding)

        clarification = _manifest_create_clarification(
            last_message,
            effective_intent_analysis,
            self.schema_manifest.manifest,
        )
        if clarification:
            return {
                "sql_query": "SKIP",
                "messages": [
                    AIMessage(
                        content=clarification
                    )
                ],
                "retry_count": 0,
            }

        # Deterministic fast-path for simple user count/list asks.
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")
        user_fast_path_sql = _maybe_build_user_query_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            self.schema_manifest,
        )
        if user_fast_path_sql:
            logger.info("Using deterministic user SQL fast-path.")
            user_fast_path_sql = self.query_refiner.apply_ironclad_heuristics(user_fast_path_sql, {}, company_id)
            return {"sql_query": user_fast_path_sql, "retry_count": state.get("retry_count", 0) + 1}

        asset_fast_path_sql = _maybe_build_asset_query_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            self.schema_manifest,
        )
        if asset_fast_path_sql:
            logger.info("Using deterministic asset SQL fast-path.")
            asset_fast_path_sql = self.query_refiner.apply_ironclad_heuristics(asset_fast_path_sql, {}, company_id)
            return {"sql_query": asset_fast_path_sql, "retry_count": state.get("retry_count", 0) + 1}

        asset_create_sql = _maybe_build_asset_create_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            self.schema_manifest,
        )
        if asset_create_sql:
            logger.info("Using deterministic asset CREATE SQL fast-path.")
            asset_create_sql = self.query_refiner.apply_ironclad_heuristics(asset_create_sql, {}, company_id)
            return {"sql_query": asset_create_sql, "retry_count": state.get("retry_count", 0) + 1}
        
        # Phase 6: Task Aggregation (Counts)
        db_user_id = metadata.get("user_id", "1")
        user_role = metadata.get("user_role", "user")
        task_agg_sql = _maybe_build_task_aggregation_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            db_user_id,
            user_role,
            self.schema_manifest,
        )
        if task_agg_sql:
            logger.info(f"Using deterministic task aggregation SQL fast-path for role: {user_role}.")
            task_agg_sql = self.query_refiner.apply_ironclad_heuristics(task_agg_sql, {}, company_id)
            return {"sql_query": task_agg_sql, "retry_count": state.get("retry_count", 0) + 1}

        task_listing_sql = _maybe_build_task_listing_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            db_user_id,
            user_role,
            self.schema_manifest,
        )
        if task_listing_sql:
            logger.info(f"Using deterministic task listing SQL fast-path for role: {user_role}.")
            task_listing_sql = self.query_refiner.apply_ironclad_heuristics(task_listing_sql, {}, company_id)
            return {"sql_query": task_listing_sql, "retry_count": state.get("retry_count", 0) + 1}

        # Phase 2: Role-based task status fast-path
        task_status_sql = _maybe_build_task_status_sql(
            last_message,
            effective_intent_analysis,
            company_id,
            db_user_id,
            user_role,
            self.schema_manifest,
        )
        if task_status_sql:
            logger.info(f"Using deterministic task status SQL fast-path for role: {user_role}.")
            task_status_sql = self.query_refiner.apply_ironclad_heuristics(task_status_sql, {}, company_id)
            return {"sql_query": task_status_sql, "retry_count": state.get("retry_count", 0) + 1}
        
        # --- DB Context ---
        metadata = state.get("metadata", {})
        user_name = metadata.get("user_name", "user")
        company_name = metadata.get("company_name", "the facility")
        company_id = metadata.get("company_id")
        user_role = metadata.get("user_role", "user")

        # Fallback to settings.DATABASE_URL if not in metadata
        db_url = metadata.get("db_connection_string") or settings.DATABASE_URL
        if not db_url:
            logger.error("No DATABASE_URL available in metadata or settings!")
            return {"error": "Database connection not configured."}
        
        # Step 1: Get all table names and hints
        all_tables = self.schema_service.get_all_tables(db_url=db_url)
        schema_hints = self.schema_service.get_schema_hints(db_url=db_url)
        manifest_tables = set(self.schema_manifest.manifest.get("tables", {}).keys())
        candidate_tables = [t for t in all_tables if not manifest_tables or t in manifest_tables]
        if manifest_tables and not candidate_tables:
            logger.warning("Manifest tables do not overlap with DB tables. Falling back to all DB tables.")
            candidate_tables = all_tables
        
        # Step 2: Extract relevant tables (semantic first, then heuristics)
        semantic_tables = self.schema_manifest.semantic_select_tables(last_message, candidate_tables, top_k=5)
        selected_tables = list(semantic_tables)
        heuristic_tables = self.table_selector.get_relevant_tables(last_message, candidate_tables)
        selected_tables.extend(heuristic_tables)
        
        # If heuristics failed, use LLM to select
        if not selected_tables:
            logger.info("Heuristics found no tables. Falling back to LLM for selection.")
            prompt = TABLE_SELECTION_PROMPT_TEMPLATE.format(
                last_message=last_message,
                all_tables=", ".join(candidate_tables),
                schema_hints=schema_hints
            )
            try:
                selection_response = await ainvoke_with_retry(
                    self.llm,
                    prompt,
                    attempts=2,
                    backoff_seconds=0.3,
                    validator=lambda r: bool(str(getattr(r, "content", "")).strip()),
                    task_name="table_selection_llm",
                )
                content = selection_response.content.strip().replace("```json", "").replace("```", "")
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    selected_tables = [item for item in parsed if isinstance(item, str) and item in candidate_tables]
            except Exception as e:
                logger.warning(f"Selection LLM failed: {e}. Using all tables as fallback.")
                selected_tables = candidate_tables[:10]
        else:
            logger.info(f"Heuristic matched tables: {selected_tables}")
        
        selected_tables = list(dict.fromkeys([s for s in selected_tables if s]))[:5]

        # Step 3: Get schema for selected tables
        if not selected_tables or selected_tables == ['']:
             schema = f"Available Tables: {', '.join(all_tables)}. Please specify which one to query."
        else:
             try:
                 # Verify tables exist
                 valid_tables = [t for t in selected_tables if t in all_tables]
                 if valid_tables:
                     schema = self.schema_service.get_schema(valid_tables, db_url=db_url, concise=True)
                 else:
                     schema = "No valid tables selected."
             except Exception as e:
                 logger.error(f"Failed to fetch schema for {selected_tables}: {e}")
                 schema = "Error fetching schema."
        
        retry_count = state.get("retry_count", 0)
        error_context = ""
        if state.get("error") and retry_count > 0:
            error_context = f"\nThe previous query failed with error: {state['error']}. Please fix the SQL."
        intent_context = json.dumps(effective_intent_analysis, default=str)

        # --- Security Context (RLS) ---
        security_instruction = ""
        if user_role != "super_admin": 
            filters = []
            if company_id:
                filters.append(f"company_id = {company_id}")
            
            if filters:
                security_instruction = f"""
        3. **Row Level Security (RLS)**:
           - You MUST append the following filters to the WHERE clause:
             {' AND '.join(filters)}
                """

        # --- CACHE LOOKUP ---
        input_text = last_message
        db_user_id = metadata.get("user_id", "unknown")
        cache_key_str = f"{company_id}:{db_user_id}:{user_role}:{input_text.strip().lower()}"
        
        if retry_count == 0:
            cached_sql = await self.cache_service.get(cache_key_str)
            if cached_sql and "SKIP" not in cached_sql:
                logger.info(f"Cache HIT for query: {input_text}")
                # Re-refine cached SQL to apply latest heuristics/security
                refined_cached_sql = self.query_refiner.apply_ironclad_heuristics(cached_sql, {}, company_id)
                return {"sql_query": refined_cached_sql, "retry_count": 0, "error": None, "from_cache": True}

        # Step 4: Resolve Persons to IDs
        resolved_persons = await self.person_resolver.resolve_person_to_ids(input_text, company_id)
        person_instruction = ""
        if resolved_persons:
            p_details = []
            for name, ids in resolved_persons.items():
                p_details.append(f"- Person '{name}' matches User IDs: {ids}")
            
            person_instruction = f"\n- **FORCED_IDENTITY**: The following persons were found in the database:\n" + "\n".join(p_details) + "\n- You **MUST** filter by these IDs. Use the appropriate column for the selected table (e.g., `assigned_user_id`, `user_id`, `created_by`).\n"

        manifest_context = self.schema_manifest.render_manifest_context(valid_tables if 'valid_tables' in locals() else selected_tables)
        join_hints = self.schema_manifest.render_join_hints(valid_tables if 'valid_tables' in locals() else selected_tables)
        intent_type = effective_intent_analysis.get("intent_type", "")
        few_shot_examples = self.schema_manifest.render_few_shot_examples(intent_type=intent_type)

        # Generate SQL Prompt
        prompt = SQL_GEN_PROMPT_TEMPLATE.format(
            user_name=str(user_name),
            user_id=str(metadata.get("user_id", "unknown")),
            company_name=str(company_name),
            company_id=str(company_id),
            security_instruction=security_instruction,
            input_text=input_text,
            intent_context=intent_context,
            schema_context=schema,
            manifest_context=manifest_context or "N/A",
            join_hints=join_hints or "N/A",
            few_shot_examples=few_shot_examples or "N/A",
            entity_instruction=person_instruction,
            error_context=error_context
        )
        
        response = await ainvoke_with_retry(
            self.llm,
            prompt,
            max_tokens=250,
            attempts=3,
            backoff_seconds=0.35,
            validator=lambda r: bool(str(getattr(r, "content", "")).strip()),
            task_name="sql_generation_llm",
        )
        raw_content = response.content.strip()
        
        sql_query = ""
        is_text_response = False
        text_response = ""

        try:
            # Parse JSON or SQL
            cleaned_json = raw_content
            if "```json" in cleaned_json:
                cleaned_json = cleaned_json.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned_json:
                 cleaned_json = cleaned_json.split("```")[1].strip()
            
            start = cleaned_json.find("{")
            end = cleaned_json.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(cleaned_json[start:end+1])
                if data.get("type") == "sql":
                    sql_query = data.get("content", "")
                else:
                    is_text_response = True
                    text_response = data.get("content", "I need more information.")
            else:
                 raise json.JSONDecodeError("No JSON", cleaned_json, 0)

        except (json.JSONDecodeError, KeyError, IndexError):
            # Fallback
            recovered_sql = _extract_sql_from_text(raw_content)
            if recovered_sql:
                sql_query = recovered_sql
            else:
                is_text_response = True
                text_response = (
                    "I could not reliably generate a valid SQL query for that request. "
                    "Please rephrase with the exact data you need."
                )
        
        if is_text_response:
             return {"sql_query": "SKIP", "messages": [AIMessage(content=text_response)], "retry_count": 0}
        
        # --- IRONCLAD HEURISTICS ---
        sql_query = self.query_refiner.apply_ironclad_heuristics(sql_query, resolved_persons, company_id)

        # --- CACHE SET ---
        if sql_query and not state.get("error"):
             await self.cache_service.set(cache_key_str, sql_query)

        return {"sql_query": sql_query, "retry_count": state.get("retry_count", 0) + 1}
