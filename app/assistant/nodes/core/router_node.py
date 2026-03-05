from typing import Dict

from app.services.core.token_usage_service import TokenUsageService


class RouterNode:
    def __init__(self, router_service):
        self.router = router_service

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        route, usage = await self.router.route_with_usage(str(query), metadata=state.get("metadata") or {})
        merged_usage = TokenUsageService.merge(state.get("token_usage"), usage)
        return {"route": route, "token_usage": merged_usage}
