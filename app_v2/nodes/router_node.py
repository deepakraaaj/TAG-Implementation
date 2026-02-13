from typing import Dict

from app_v2.services.router_service import RouterService


class RouterNode:
    def __init__(self):
        self.router = RouterService()

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        return {"route": await self.router.route(str(query))}
