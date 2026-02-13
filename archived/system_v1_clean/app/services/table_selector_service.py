from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class TableSelectorService:
    def __init__(self):
        pass

    def get_relevant_tables(self, query: str, all_tables: List[str]) -> List[str]:
        """
        Uses keyword-based heuristics to identify relevant tables.
        """
        selected_tables = []
        lower_msg = query.lower()
        
        # Robust Heuristics
        for t in all_tables:
            lt = t.lower()
            if "task" in lower_msg:
                if lt == "task_transaction" or lt =="task_description":
                    selected_tables.append(t)
                elif "task" in lt and ("list" not in lower_msg):
                    selected_tables.append(t)
                    
            if ("user" in lower_msg or "assigned" in lower_msg or "who" in lower_msg or "person" in lower_msg or "for " in lower_msg) and "user" in lt:
                selected_tables.append(t)
                
            if ("asset" in lower_msg) and "asset" in lt:
                selected_tables.append(t)
                
            if ("company" in lower_msg or "companies" in lower_msg or "business" in lower_msg or "compan" in lower_msg) and "compan" in lt:
                selected_tables.append(t)
                
            if ("facility" in lower_msg or "facilities" in lower_msg) and ("facil" in lt):
                selected_tables.append(t)
                
        # Deduplicate
        return list(dict.fromkeys([s for s in selected_tables if s]))
