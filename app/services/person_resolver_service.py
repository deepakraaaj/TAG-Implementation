import logging
import json
from typing import List, Dict, Optional
from sqlalchemy import text
from .schema_service import SchemaService

logger = logging.getLogger(__name__)

class PersonResolverService:
    def __init__(self, llm, schema_service: SchemaService):
        self.llm = llm
        self.schema_service = schema_service

    async def resolve_person_to_ids(self, query: str, company_id: int, db_url: str = None) -> Dict[str, List[int]]:
        """
        Detects persons in query and resolves them to database IDs.
        Returns mapping: { "name": [id1, id2] }
        """
        # Step 1: Extract potential names using LLM
        extract_prompt = f"""
        Analyze the user query: "{query}"
        Identify specific people mentioned by name (e.g. 'Soban', 'John').
        
        RULES:
        1. Return a JSON list of names found.
        2. If NO specific names are found, return [].
        3. IGNORE generic words: "me", "my", "tasks", "list", "show", "details".
        
        Examples:
        "List tasks for Soban" -> ["Soban"]
        "List my tasks" -> []
        "Show cleaning tasks" -> []
        
        Return ONLY the JSON list.
        """
        
        try:
            response = await self.llm.ainvoke(extract_prompt)
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            
            names = json.loads(content)
            if not isinstance(names, list):
                return {}
                
            resolved = {}
            engine = self.schema_service.get_engine_for_url(db_url)
            
            with engine.connect() as conn:
                for name in names:
                    # Guardrail: Verify name is actually in the query (case-insensitive)
                    if name.lower() not in query.lower():
                        logger.warning(f"Hallucinated name detected: '{name}' not in query '{query}'. Ignoring.")
                        continue
                        
                    # Clean name for SQL
                    safe_name = name.strip("',\"")
                    sql = text(f"SELECT id FROM user WHERE first_name LIKE :name AND company_id = :cid LIMIT 5")
                    result = conn.execute(sql, {"name": f"%{safe_name}%", "cid": company_id})
                    ids = [row[0] for row in result.fetchall()]
                    if ids:
                        resolved[name] = ids
            
            return resolved
            
        except Exception as e:
            logger.error(f"Person resolution failed: {e}")
            return {}
