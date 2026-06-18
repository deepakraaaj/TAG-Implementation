from typing import Dict

from app.services.core.token_usage_service import TokenUsageService


class RouterNode:
    def __init__(self, router_service):
        self.router = router_service

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content if messages else "")
        metadata = state.get("metadata") or {}
        combined = getattr(self.router, "route_and_intent_with_usage", None)
        if callable(combined):
            route, intent, usage = await combined(query, metadata=metadata)
        else:
            route, usage = await self.router.route_with_usage(query, metadata=metadata)
            intent = None
        merged_usage = TokenUsageService.merge(state.get("token_usage"), usage)
        out: Dict = {"route": route, "token_usage": merged_usage}
        # When routing extracted the intent in the same LLM call, hand it to the
        # intent node so it can skip a second round-trip.
        if isinstance(intent, dict):
            out["prefetched_intent"] = {"query": query, "intent": intent}
        return out
