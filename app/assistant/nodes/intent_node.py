from typing import Dict

from app.assistant.services.intent_service import IntentService


class IntentNode:
    def __init__(self):
        self.intent = IntentService()

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        return {"intent": await self.intent.analyze(str(query))}
