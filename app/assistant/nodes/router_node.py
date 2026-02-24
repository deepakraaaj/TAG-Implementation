from typing import Dict

from app.assistant.services.router_service import RouterService
from app.services.token_usage_service import TokenUsageService


class RouterNode:
    def __init__(self):
        self.router = RouterService()

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        route, usage = await self.router.route_with_usage(str(query))
        merged_usage = TokenUsageService.merge(state.get("token_usage"), usage)
        return {"route": route, "token_usage": merged_usage}
