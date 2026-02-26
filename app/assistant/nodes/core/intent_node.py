from typing import Dict

from app.services.interfaces import IntentAnalyzer
from app.services.core.token_usage_service import TokenUsageService


class IntentNode:
    def __init__(self, intent_service: IntentAnalyzer):
        self.intent = intent_service

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        intent, usage = await self.intent.analyze_with_usage(str(query), metadata=state.get("metadata") or {})
        merged_usage = TokenUsageService.merge(state.get("token_usage"), usage)
        return {"intent": intent, "token_usage": merged_usage}
