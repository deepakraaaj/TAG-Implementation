from typing import Dict

from app.services.interfaces import IntentAnalyzer
from app.services.core.token_usage_service import TokenUsageService


class IntentNode:
    def __init__(self, intent_service: IntentAnalyzer):
        self.intent = intent_service

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content if messages else "")

        # Reuse intent extracted during routing (same LLM call) when it belongs
        # to this exact query, avoiding a second round-trip.
        prefetched = state.get("prefetched_intent")
        if (
            isinstance(prefetched, dict)
            and isinstance(prefetched.get("intent"), dict)
            and str(prefetched.get("query", "")) == query
        ):
            return {"intent": prefetched["intent"]}

        intent, usage = await self.intent.analyze_with_usage(query, metadata=state.get("metadata") or {})
        merged_usage = TokenUsageService.merge(state.get("token_usage"), usage)
        return {"intent": intent, "token_usage": merged_usage}
