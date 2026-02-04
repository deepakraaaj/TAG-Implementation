import logging
import json
import os
from typing import Dict, Any, List
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
from app.workflow.prompts import (
    SQL_GEN_PROMPT_TEMPLATE,
    TABLE_SELECTION_PROMPT_TEMPLATE
)

logger = logging.getLogger(__name__)
settings = get_settings()

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

    async def run(self, state: AgentState):
        """
        Generates SQL based on the user query and schema.
        """
        logger.info("Entering generate_sql_node")
        
        # Use rewritten query if available, otherwise fallback
        last_message = state.get("rewritten_query") or state["messages"][-1].content
        logger.info(f"Using Query for SQL Gen: {last_message}")
        
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
        
        # Step 2: Extract relevant tables
        selected_tables = self.table_selector.get_relevant_tables(last_message, all_tables)
        
        # If heuristics failed, use LLM to select
        if not selected_tables:
            logger.info("Heuristics found no tables. Falling back to LLM for selection.")
            prompt = TABLE_SELECTION_PROMPT_TEMPLATE.format(
                last_message=last_message,
                all_tables=", ".join(all_tables),
                schema_hints=schema_hints
            )
            try:
                selection_response = await self.llm.ainvoke(prompt)
                content = selection_response.content.strip().replace("```json", "").replace("```", "")
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    selected_tables = [item for item in parsed if isinstance(item, str) and item in all_tables]
            except Exception as e:
                logger.warning(f"Selection LLM failed: {e}. Using all tables as fallback.")
                selected_tables = all_tables[:10] 
        else:
            logger.info(f"Heuristic matched tables: {selected_tables}")
        
        selected_tables = list(dict.fromkeys([s for s in selected_tables if s]))

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

        # Generate SQL Prompt
        prompt = SQL_GEN_PROMPT_TEMPLATE.format(
            user_name=str(user_name),
            user_id=str(metadata.get("user_id", "unknown")),
            company_name=str(company_name),
            company_id=str(company_id),
            security_instruction=security_instruction,
            input_text=input_text,
            schema_context=schema,
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
            cleaned = raw_content.strip().split("\n")[0].upper()
            if cleaned.startswith(("SELECT", "INSERT", "UPDATE")):
                 sql_query = raw_content.strip()
            else:
                 is_text_response = True
                 text_response = raw_content
        
        if is_text_response:
             return {"sql_query": "SKIP", "messages": [AIMessage(content=text_response)], "retry_count": 0}
        
        # --- IRONCLAD HEURISTICS ---
        sql_query = self.query_refiner.apply_ironclad_heuristics(sql_query, resolved_persons, company_id)

        # --- CACHE SET ---
        if sql_query and not state.get("error"):
             await self.cache_service.set(cache_key_str, sql_query)

        return {"sql_query": sql_query, "retry_count": state.get("retry_count", 0) + 1}
