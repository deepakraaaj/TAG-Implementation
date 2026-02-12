import unittest

from app.workflow.router import RouterNode


class TestRouterIntent(unittest.TestCase):
    def test_extract_route_from_json_sql(self):
        route = RouterNode._extract_route('{"route":"SQL"}')
        self.assertEqual(route, "SQL")

    def test_extract_route_from_json_chat(self):
        route = RouterNode._extract_route('{"route":"CHAT"}')
        self.assertEqual(route, "CHAT")

    def test_extract_route_from_markdown_fallback(self):
        route = RouterNode._extract_route("```json\n{\"route\":\"VECTOR\"}\n```")
        self.assertEqual(route, "VECTOR")

    def test_extract_route_unparsable(self):
        route = RouterNode._extract_route("not valid")
        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()
