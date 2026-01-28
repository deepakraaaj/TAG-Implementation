from typing import TypedDict, Annotated, List, Dict, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
# Dynamic import handled inside class or use generic
from langchain_openai import ChatOpenAI
from ..services.pii_service import PIIService
from ..services.sql_validator import SQLValidatorService
from ..services.schema_service import SchemaService
from ..services.cache_service import SemanticCache
from ..config import get_settings
from .vector_search import VectorSearchNode
from .general_chat import GeneralChatNode
from .contextualize import ContextualizeNode
import logging
import json
import os

logger = logging.getLogger(__name__)
settings = get_settings()

class AgentState(TypedDict):
    messages: List[BaseMessage]
    sql_query: str
    sql_result: str
    row_count: int
    rows_preview: List[Dict]
    error: str
    retry_count: int
    metadata: Dict[str, Any]
    route: str
    rewritten_query: str
    toon_data: Dict[str, Any] # New field for contextualized query

class TAGNodes:
    def __init__(self):
        # Use generic OpenAI compatible client
        # This supports Groq, OpenAI, Ollama, etc. just by changing Base URL
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )
        self.pii_service = PIIService()
        self.sql_validator = SQLValidatorService(allowed_tables=None) # Allow all for now or fetch from schema
        self.schema_service = SchemaService()
        self.cache_service = SemanticCache()
        
        # Instantiate sub-nodes
        self.vector_search = VectorSearchNode()
        self.general_chat = GeneralChatNode()
        self.contextualize = ContextualizeNode()

    async def contextualize_node(self, state: AgentState):
        """
        Rewrites the query to be self-contained.
        """
        return await self.contextualize.run(state)

    async def vector_search_node(self, state: AgentState):
        """
        Executes semantic search (delegates to VectorSearchNode).
        """
        return await self.vector_search.run(state)

    async def general_chat_node(self, state: AgentState):
        """
        Executes general chat (delegates to GeneralChatNode).
        """
        return await self.general_chat.run(state)

    async def pii_node(self, state: AgentState):
        """
        Redacts PII from user messages.
        """
        logger.info("Entering pii_node")
        last_message = state["messages"][-1]
        if isinstance(last_message, HumanMessage):
            original_content = last_message.content
            sanitized_content = self.pii_service.sanitize(original_content)
            # Update the message content in place or append a note
            last_message.content = sanitized_content
            
        return {"messages": state["messages"]}

    async def generate_sql_node(self, state: AgentState):
        """
        Generates SQL based on the user query and schema.
        1. Selects relevant tables.
        2. Fetches schema for selected tables.
        3. Generates SQL.
        """
        logger.info("Entering generate_sql_node")
        
        # Use rewritten query if available, otherwise fallback
        last_message = state.get("rewritten_query") or state["messages"][-1].content
        logger.info(f"Using Query for SQL Gen: {last_message}")
        
        # --- DB Context ---
        metadata = state.get("metadata", {})
        db_url = metadata.get("db_connection_string") # Dynamic DB URL from context
        
        # Step 1: Get all table names and hints
        all_tables = self.schema_service.get_all_tables(db_url=db_url)
        schema_hints = self.schema_service.get_schema_hints(db_url=db_url)
        
        # Step 2: Ask LLM to select relevant tables
        table_selection_prompt = f"""
        Given the user question: "{last_message}"
        
        Available Tables:
        {', '.join(all_tables)}
        
        {schema_hints}
        
        Return a JSON list of the tables that are relevant to answering the question.
        Example: ["table1", "table2"]
        If the question seems unrelated to any table, return an empty list [].
        Return ONLY the JSON list, no markdown or explanation.
        """
        
        selection_response = await self.llm.ainvoke(table_selection_prompt)
        content = selection_response.content.strip().replace("```json", "").replace("```", "")
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                selected_tables = []
                for item in parsed:
                    if isinstance(item, str):
                        selected_tables.append(item)
                    elif isinstance(item, dict) and "name" in item:
                        selected_tables.append(item["name"])
                    elif isinstance(item, list) and item and isinstance(item[0], str):
                        selected_tables.append(item[0]) # Handle [['table']]
            else:
                logger.warning(f"Unexpected JSON format: {parsed}")
                selected_tables = [t.strip() for t in content.split(',') if t.strip()]
        except json.JSONDecodeError:
            logger.error(f"Failed to parse table selection JSON: {content}")
            # Fallback: Split by comma if JSON fails
            selected_tables = [t.strip() for t in content.split(',') if t.strip()]
        
        logger.info(f"Selected tables: {selected_tables}")

        # Step 3: Get schema for selected tables
        # Fallback to all tables if selection is empty or fails (but alert on size?)
        # For now, trust the LLM or fallback efficiently.
        if not selected_tables or selected_tables == ['']:
             # Fallback: maybe just users? or fail?
             # Let's try to get schema for what we have.
             schema = self.schema_service.get_schema(db_url=db_url, concise=True) # Risk of token overflow again if empty
        else:
             try:
                 schema = self.schema_service.get_schema(selected_tables, db_url=db_url, concise=True)
             except Exception as e:
                 logger.error(f"Failed to fetch schema for {selected_tables}: {e}")
                 # Fallback to empty schema or try safe subset?
                 # If table doesn't exist, we might crash.
                 # Let's verify tables exist
                 valid_tables = [t for t in selected_tables if t in all_tables]
                 if valid_tables:
                     schema = self.schema_service.get_schema(valid_tables, db_url=db_url, concise=True)
                 else:
                     schema = "No valid tables selected."
        
        logger.info(f"Generated schema length: {len(schema)}")
        logger.info(f"Schema preview: {schema[:500]}")
        
        error_context = ""
        if state.get("error"):
            error_context = f"\nThe previous query failed with error: {state['error']}. Please fix the SQL."

        # --- Security Context (RLS) ---
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")
        user_role = metadata.get("user_role", "user") # default to user if not set

        security_instruction = ""
        if user_role != "super_admin":  # Apply RLS for everyone except super_admin
            filters = []
            if company_id:
                filters.append(f"company_id = {company_id}")
            
            # Add more context specific filters here if needed (e.g. user_id)
            
            if filters:
                security_instruction = f"""
        3. **Row Level Security (RLS)**:
           - The user is restricted to specific data.
           - You MUST append the following filters to the WHERE clause of ALL queries:
             {' AND '.join(filters)}
           - Example: If the user asks "Show all users", you must generate "SELECT * FROM user WHERE company_id = {company_id}".
           - NEVER return data that violates this filter.
                """
        # -----------------------------

        # Use rewritten query!
        input_text = last_message
        
        # --- CACHE LOOKUP ---
        # Create a composite key to ensure RLS safety (Company + Role + Query)
        cache_key_str = f"{company_id}:{user_role}:{input_text.strip().lower()}"
        cached_sql = await self.cache_service.get(cache_key_str)
        
        if cached_sql and "SKIP" not in cached_sql:
            logger.info(f"Cache HIT for query: {input_text}")
            logger.info(f"Cached SQL: {cached_sql}")
            return {"sql_query": cached_sql, "retry_count": 0}
            
        logger.info(f"Cache MISS for query: {input_text}")
        # --------------------

        # Schema Context
        schema_context = schema
        # User Context
        user_name = metadata.get("user_name", "user")
        company_name = metadata.get("company_name", "the facility")

        prompt = f"""
        You are an AI SQL expert for a MySQL database.
        
        Current User Context:
        - User: {user_name}
        - Company: {company_name}
        
        Capabilities:
        1. You can generate SELECT queries to retrieve data.
        2. You can generate INSERT or UPDATE queries to perform actions (if requested).
        3. **Interactive Mode**: If you need more information or confirmation from the user (e.g. missing parameters for INSERT), ask a clarifying question.
        
        **Strategy for INSERT/UPDATE operations:**
        1. **Foreign Key Resolution (CRITICAL)**:
           - If a table has fields like `schedule_id`, `user_id`, `assignee_id`, etc., you CANNOT insert text names directly. You need the numeric ID.
           - **IF YOU DO NOT HAVE THE ID**:
             - Do NOT ask the user for the ID (they don't know it).
             - **Do NOT ask "Please provide the schedule ID".**
             - **INSTEAD, GENERATE A `SELECT` QUERY** to list the available options from the related table (e.g. `SELECT id, name FROM schedules WHERE company_id = {company_id} LIMIT 10`).
             - **Do NOT ask users to select by ID**. Just show the list.
             - Return this SQL. The user will see the list and pick one in the next turn.
             
        2. **Hallucination Protection**:
           - NEVER list options like "Company 1, 2, 3" if you don't know them. 
           - If you need to show options from the database, GENERATE A SELECT QUERY to fetch them first.
           - User's company is **{company_name}**. Use it if relevant.
        
        Critical Safety & Hallucination Rules:
        1. **Look Before You Leap**: NEVER invent or guess IDs, foreign keys, or values. 
        2. **Schema Compliance**: Use ONLY the tables and columns provided in the schema below.
        3. **Read-Only Defaults**: Use SELECT unless the user explicitly asks to "Add", "Create", "Update", "Delete".
        {security_instruction}
        
        Input:
        - Question: {input_text}
        
        - Schema: 
        {schema_context}
        - Previous Error (if any): {error_context}
        
        **RESPONSE FORMAT**:
        You MUST return a valid JSON object. Do NOT return raw SQL or markdown.
        
        1. If you have a valid SQL query (including LOOKUP queries):
           {{ "type": "sql", "content": "SELECT * FROM ..." }}
           
        2. If you need clarification/confirmation WITHOUT running SQL:
           {{ "type": "text", "content": "For which user would you like to create the schedule?" }}
        """
        
        response = await self.llm.ainvoke(prompt)
        raw_content = response.content.strip()
        # Clean up markdown
        raw_content = raw_content.replace("```json", "").replace("```", "")
        
        sql_query = ""
        is_text_response = False
        text_response = ""

        try:
            # Try finding the JSON object if there is extra text
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1:
                json_str = raw_content[start:end+1]
                parsed = json.loads(json_str)
                
                if parsed.get("type") == "sql":
                    sql_query = parsed["content"]
                else:
                    is_text_response = True
                    text_response = parsed.get("content", "I need more information.")
            else:
                 raise json.JSONDecodeError("No JSON found", raw_content, 0)

        except json.JSONDecodeError:
            # Fallback for legacy/raw strings
            cleaned = raw_content.strip().upper()
            if cleaned.startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "SHOW", "DESCRIBE")):
                 sql_query = raw_content.strip()
            else:
                 # Assume it's a question/text response if it's not SQL
                 is_text_response = True
                 text_response = raw_content
        
        if is_text_response:
             # Do NOT cache interactive questions
             return {"sql_query": "SKIP", "messages": [AIMessage(content=text_response)], "retry_count": 0}
        
        # --- CACHE SET ---
        if sql_query and not state.get("error"): # Only cache if no previous error
             await self.cache_service.set(cache_key_str, sql_query)
        # -----------------

        return {"sql_query": sql_query, "retry_count": state.get("retry_count", 0) + 1}

    async def validate_node(self, state: AgentState):
        """
        Validates the generated SQL.
        """
        logger.info("Entering validate_node")
        sql = state["sql_query"]
        logger.info(f"Generated SQL: {sql}")
        is_valid = self.sql_validator.validate_sql(sql)
        
        if not is_valid:
            return {"error": "SQL Query violated safety policy (destructive command or forbidden table)."}
            
        return {"error": None}

    async def execute_sql_node(self, state: AgentState):
        """
        Executes the SQL query.
        """
        logger.info("Entering execute_sql_node")
        if state.get("error"):
            return {} # Skip execution if validation failed
            
        sql = state["sql_query"]
        try:
             # --- DB Context for Execution ---
             metadata = state.get("metadata", {})
             db_url = metadata.get("db_connection_string")
             
             engine = self.schema_service.get_engine_for_url(db_url)
             
             with engine.connect() as conn:
                from sqlalchemy import text
                result = conn.execute(text(sql))
                
                if result.returns_rows:
                    rows = [dict(row) for row in result.mappings()]
                else:
                    # For INSERT/UPDATE, we must commit explicitly
                    conn.commit()
                    rows = [{"status": "success", "rows_affected": result.rowcount}]
                 
                # Pagination Logic
                metadata = state.get("metadata", {})
                page = int(metadata.get("page", 1))
                limit = int(metadata.get("limit", 5)) # Default 5 if not specified
                
                start_idx = (page - 1) * limit
                end_idx = start_idx + limit
                
                paginated_rows = rows[start_idx:end_idx]
                
                return {
                    "sql_result": json.dumps(rows, default=str), # Keep full result for LLM context if feasible, or maybe truncate?
                    # LLM context uses rows_preview if available now. 
                    # But we probably want LLM to see "top N" regardless of pagination? 
                    "row_count": len(rows),
                    "rows_preview": paginated_rows,
                    "error": None
                } 

        except Exception as e:
            return {"error": str(e)}

    async def response_node(self, state: AgentState):
        """
        Generates the final natural language response.
        """
        logger.info("Entering response_node")
        if state.get("error"):
             return {"messages": [AIMessage(content=f"I encountered an error processing your request: {state['error']}")]}
             
        # Use structured info if available, otherwise fallback to sql_result
        if state.get("rows_preview") is not None:
            # We have structured data
            result_context = f"""
            Total Rows: {state['row_count']}
            Data Preview (Top {len(state['rows_preview'])}):
            {json.dumps(state['rows_preview'], indent=2, default=str)}
            """
        else:
            # Fallback legacy behavior
            result = state["sql_result"]
            if len(result) > 4000:
                result = result[:4000] + "... (truncated)"
            result_context = result
            
        original_question = state["messages"][-1].content
        
        prompt = f"""
        Question: {original_question}
        SQL Result Context:
        {result_context}
        
        **PRESENTATION RULES (CRITICAL):**
        1. **NEVER show unique numeric IDs** to the user (e.g. `id`, `user_id`, `company_id`).
           - BAD: "User John (ID: 5)"
           - GOOD: "User John"
        2. **Required Format for Selection**: If the user needs to select an option, you **MUST LIST** the available options by Name/Title.
           - Do not summarize (e.g. "There are 5 options"). List them!
        3. If the result is a long list (> 10 items) and NOT for selection, you may summarize.
        
        Provide a concise natural language answer based on the result.
        """
        
        response = await self.llm.ainvoke(prompt)
        return {"messages": [response]}
