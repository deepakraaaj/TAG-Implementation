import json
import logging
import os
import re
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry
from app.workflow.state import AgentState

logger = logging.getLogger(__name__)
settings = get_settings()


class IntentAnalysisNode:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )

    @staticmethod
    def _extract_person_filter(query: str) -> str:
        q = (query or "").strip()
        if not q:
            return ""
        stop_tokens = [" last ", " past ", " next ", " from ", " between ", " status ", " priority "]

        def _clean_candidate(value: str) -> str:
            candidate = f" {value.strip()} "
            for token in stop_tokens:
                idx = candidate.lower().find(token)
                if idx != -1:
                    candidate = candidate[:idx]
                    break
            return candidate.strip(" ,.;")

        # Handles: "for Nirmala", "assigned to Nirmala"
        for_match = re.search(r"\bfor\s+([A-Za-z][A-Za-z\s]{1,40})\b", q, flags=re.IGNORECASE)
        if for_match:
            person = _clean_candidate(for_match.group(1))
            if person and person.lower() not in {"last month", "next week"} and len(person.split()) <= 3:
                return person
        assigned_match = re.search(r"\bassigned\s+to\s+([A-Za-z][A-Za-z\s]{1,40})\b", q, flags=re.IGNORECASE)
        if assigned_match:
            return _clean_candidate(assigned_match.group(1))
        return ""

    @staticmethod
    def _deterministic_intent_analysis(query: str) -> Dict[str, Any]:
        q = (query or "").strip()
        lowered = q.lower()

        entities: List[str] = []
        if re.search(r"\b(task|tasks|work order|work orders|job|jobs)\b", lowered):
            entities.append("task_transaction")
        if re.search(r"\b(user|users|staff)\b", lowered):
            entities.append("user")
        if re.search(r"\b(asset|assets)\b", lowered):
            entities.append("asset")
        if re.search(r"\b(facility|facilities)\b", lowered):
            entities.append("facility")

        filter_dict: Dict[str, Any] = {}
        status_map = {
            "pending": "Pending",
            "completed": "Completed",
            "in progress": "In Progress",
            "open": "Open",
            "closed": "Closed",
        }
        for key, val in status_map.items():
            if re.search(rf"\b{re.escape(key)}\b", lowered):
                filter_dict["status"] = val
                break

        priority_map = {
            "critical": "Critical",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
        }
        for key, val in priority_map.items():
            if re.search(rf"\b{re.escape(key)}\b", lowered):
                filter_dict["priority"] = val
                break

        if re.search(r"\b(last|past)\s+\d+\s+days?\b", lowered):
            filter_dict["scheduled_date"] = re.search(
                r"\b((?:last|past)\s+\d+\s+days?)\b", lowered
            ).group(1)
        elif re.search(r"\blast\s+month\b", lowered):
            filter_dict["scheduled_date"] = "last month"
        elif re.search(r"\bnext\s+week\b", lowered):
            filter_dict["scheduled_date"] = "next week"
        elif re.search(r"\bfrom\s+\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}\b", lowered):
            filter_dict["scheduled_date"] = re.search(
                r"\b(from\s+\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2})\b", lowered
            ).group(1)

        person = IntentAnalysisNode._extract_person_filter(q)
        if person:
            filter_dict["person"] = person

        if re.search(r"\b(count|how many|total number|number of)\b", lowered):
            intent_type = "aggregation"
        elif re.search(r"\b(create|add|new|insert|update|edit|modify|delete|remove)\b", lowered):
            intent_type = "mutation"
        elif re.search(r"\b(list|show|fetch|display)\b", lowered):
            intent_type = "listing"
        elif entities and filter_dict:
            # Short filter asks like "pending tasks" should default to listing.
            intent_type = "listing"
        elif re.search(r"\b(get|find|details|detail|lookup)\b", lowered):
            intent_type = "lookup"
        else:
            intent_type = "unknown"

        metrics = ["count"] if intent_type == "aggregation" else []
        summary = q if q else "Unknown user intent"
        return {
            "intent_type": intent_type,
            "entities": entities,
            "filter_dict": filter_dict,
            "metrics": metrics,
            "original_intent": summary,
        }

    @staticmethod
    def _normalize_intent_payload(parsed: Dict[str, Any], query: str) -> Dict[str, Any]:
        deterministic = IntentAnalysisNode._deterministic_intent_analysis(query)
        normalized: Dict[str, Any] = {
            "intent_type": parsed.get("intent_type", deterministic["intent_type"]),
            "entities": parsed.get("entities", deterministic["entities"]),
            "filter_dict": parsed.get("filter_dict", {}),
            "metrics": parsed.get("metrics", deterministic["metrics"]),
            "original_intent": parsed.get("original_intent", deterministic["original_intent"]),
        }

        if not isinstance(normalized["entities"], list):
            normalized["entities"] = deterministic["entities"]
        if not isinstance(normalized["filter_dict"], dict):
            normalized["filter_dict"] = {}
        if not isinstance(normalized["metrics"], list):
            normalized["metrics"] = deterministic["metrics"]

        # Backfill weak/partial LLM output.
        if normalized["intent_type"] in {"", "unknown"}:
            normalized["intent_type"] = deterministic["intent_type"]
        if not normalized["entities"]:
            normalized["entities"] = deterministic["entities"]
        merged_filters = dict(deterministic["filter_dict"])
        merged_filters.update(normalized["filter_dict"])
        normalized["filter_dict"] = merged_filters

        return normalized

    async def run(self, state: AgentState):
        query = state.get("rewritten_query") or state["messages"][-1].content
        prompt = f"""
Analyze this user query and return ONLY valid JSON with:
{{
  "intent_type": "listing|aggregation|lookup|mutation|unknown",
  "entities": ["..."],
  "filter_dict": {{"column_name": "value"}},
  "metrics": ["..."],
  "original_intent": "Brief summary of what the user wants"
}}

Guidelines for filter_dict:
- If user mentions status (e.g. "pending", "completed"), use {{"status": "Pending"}}.
- If user mentions priority (e.g. "high", "low"), use {{"priority": "High"}}.
- If user mentions a specific date or period, use {{"scheduled_date": "value"}}.
- If user mentions a person, use {{"person": "Name"}}.

User query: {query}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                max_tokens=200,
                attempts=3,
                backoff_seconds=0.35,
                validator=lambda r: "{" in str(getattr(r, "content", "")) and "}" in str(getattr(r, "content", "")),
                task_name="intent_analysis_llm",
            )
            raw = response.content.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(raw[start : end + 1])
                return {"intent_analysis": self._normalize_intent_payload(parsed, str(query))}
        except Exception as e:
            logger.warning("Intent analysis failed: %s", e)
        return {"intent_analysis": self._deterministic_intent_analysis(str(query))}
