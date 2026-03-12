from __future__ import annotations

from typing import Any, Dict


class IntermediateNode:
    def __init__(self, intermediate_service: Any):
        self.intermediate = intermediate_service

    async def run(self, state: Dict) -> Dict:
        return {"intermediate_frame": self.intermediate.build(state)}
