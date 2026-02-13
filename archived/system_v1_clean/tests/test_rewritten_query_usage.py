import unittest
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import app.workflow.general_chat as general_chat_module
import app.workflow.vector_search as vector_search_module


class _DummyLLMResponse:
    def __init__(self, content: str):
        self.content = content
        self.response_metadata = {"token_usage": {}}


class _DummyLLM:
    def __init__(self):
        self.prompts = []

    async def ainvoke(self, prompt, max_tokens=None):  # noqa: ANN001
        self.prompts.append(str(prompt))
        return _DummyLLMResponse("ok")


class _DummyVectorService:
    def __init__(self):
        self.queries = []

    async def search_semantic(self, query: str):
        self.queries.append(query)
        return []


class TestRewrittenQueryUsage(unittest.IsolatedAsyncioTestCase):
    async def test_general_chat_prefers_rewritten_query(self):
        node = general_chat_module.GeneralChatNode.__new__(general_chat_module.GeneralChatNode)
        node.llm = _DummyLLM()

        state = {
            "messages": [HumanMessage(content="raw follow-up")],
            "rewritten_query": "fully contextualized question",
            "metadata": {"user_name": "Deepak", "company_name": "ACME"},
        }

        await node.run(state)

        self.assertTrue(node.llm.prompts)
        self.assertIn("fully contextualized question", node.llm.prompts[0])
        self.assertNotIn("raw follow-up", node.llm.prompts[0])

    async def test_vector_search_prefers_rewritten_query(self):
        node = vector_search_module.VectorSearchNode.__new__(vector_search_module.VectorSearchNode)
        node.llm = _DummyLLM()

        original_vector_service = vector_search_module.vector_service
        try:
            dummy_vector = _DummyVectorService()
            vector_search_module.vector_service = dummy_vector

            state = {
                "messages": [HumanMessage(content="raw follow-up")],
                "rewritten_query": "contextualized retrieval query",
            }

            await node.run(state)
            self.assertEqual(dummy_vector.queries, ["contextualized retrieval query"])
        finally:
            vector_search_module.vector_service = original_vector_service


if __name__ == "__main__":
    unittest.main()
