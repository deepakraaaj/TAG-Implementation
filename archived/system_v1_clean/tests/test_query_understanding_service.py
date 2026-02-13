import unittest

from langchain_core.messages import AIMessage, HumanMessage

from app.services.query_understanding_service import QueryUnderstandingService


class _DummyLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyLLM:
    def __init__(self, content: str = ""):
        self.content = content

    async def ainvoke(self, prompt, max_tokens=None):  # noqa: ANN001
        return _DummyLLMResponse(self.content)


class _FailingLLM:
    async def ainvoke(self, prompt, max_tokens=None):  # noqa: ANN001
        raise RuntimeError("forced llm failure")


class TestQueryUnderstandingService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = QueryUnderstandingService(
            llm=_DummyLLM(
                '{"intent":"listing","entities":["asset"],"is_self_contained":true,"is_followup":false,"confidence":0.96}'
            )
        )

    async def test_detects_self_contained_operational_query(self):
        result = await self.service.analyze("list assets")
        self.assertEqual(result["intent"], "listing")
        self.assertIn("asset", result["entities"])
        self.assertTrue(result["is_self_contained"])
        self.assertGreaterEqual(result["confidence"], 0.9)

    async def test_detects_entity_only_query(self):
        result = await self.service.analyze("assets")
        self.assertIn("asset", result["entities"])
        self.assertTrue(result["is_self_contained"])
        self.assertGreaterEqual(result["confidence"], 0.7)

    async def test_detects_followup_from_context(self):
        messages = [
            HumanMessage(content="create asset"),
            AIMessage(content="What is the asset name?"),
            HumanMessage(content="Milk boiler"),
        ]
        fallback_service = QueryUnderstandingService(llm=_FailingLLM())
        result = await fallback_service.analyze("Milk boiler", messages)
        self.assertTrue(result["is_followup"])
        self.assertFalse(result["is_self_contained"])

    async def test_fallback_detects_update_as_mutation(self):
        fallback_service = QueryUnderstandingService(llm=_FailingLLM())
        result = await fallback_service.analyze("update asset code for camera 4")
        self.assertEqual(result["intent"], "mutation")


if __name__ == "__main__":
    unittest.main()
