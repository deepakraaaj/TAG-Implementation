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


def _safe_sql_str(value: str) -> str:
    return (value or "").replace("'", "''").strip()


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

    async def run(self, state: AgentState):
        """
        Generates SQL based on the user query and schema.
        """
        logger.info("Entering generate_sql_node")
        
        # Use rewritten query if available, otherwise fallback
        last_message = state.get("rewritten_query") or state["messages"][-1].content
        logger.info(f"Using Query for SQL Gen: {last_message}")
        intent_analysis = state.get("intent_analysis", {})

        clarification = _manifest_create_clarification(last_message, intent_analysis, self.schema_manifest.manifest)
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
            intent_analysis,
            company_id,
            self.schema_manifest,
        )
        if user_fast_path_sql:
            logger.info("Using deterministic user SQL fast-path.")
            return {"sql_query": user_fast_path_sql, "retry_count": state.get("retry_count", 0) + 1}

        asset_create_sql = _maybe_build_asset_create_sql(
            last_message,
            intent_analysis,
            company_id,
            self.schema_manifest,
        )
        if asset_create_sql:
            logger.info("Using deterministic asset CREATE SQL fast-path.")
            return {"sql_query": asset_create_sql, "retry_count": state.get("retry_count", 0) + 1}
        
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
                selection_response = await self.llm.ainvoke(prompt)
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
        intent_context = json.dumps(intent_analysis, default=str)

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
                return {"sql_query": cached_sql, "retry_count": 0, "error": None, "from_cache": True}

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
        intent_type = state.get("intent_analysis", {}).get("intent_type", "")
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
        
        response = await self.llm.ainvoke(prompt, max_tokens=250)
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
