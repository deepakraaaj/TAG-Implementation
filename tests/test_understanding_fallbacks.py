import unittest

from app.workflow.nodes.intent_analysis_node import IntentAnalysisNode
from app.workflow.router import RouterNode


class TestUnderstandingFallbacks(unittest.TestCase):
    def test_router_deterministic_pending_tasks(self):
        route = RouterNode._deterministic_route("pending tasks")
        self.assertEqual(route, "SQL")

    def test_router_deterministic_how_to(self):
        route = RouterNode._deterministic_route("how to create a facility")
        self.assertEqual(route, "VECTOR")

    def test_intent_deterministic_pending_tasks(self):
        intent = IntentAnalysisNode._deterministic_intent_analysis("pending tasks")
        self.assertEqual(intent["intent_type"], "listing")
        self.assertIn("task_transaction", intent["entities"])
        self.assertEqual(intent["filter_dict"].get("status"), "Pending")

    def test_intent_deterministic_count_last_30_days(self):
        intent = IntentAnalysisNode._deterministic_intent_analysis(
            "how many tasks for nirmala last 30 days"
        )
        self.assertEqual(intent["intent_type"], "aggregation")
        self.assertIn("task_transaction", intent["entities"])
        self.assertEqual(intent["filter_dict"].get("person", "").lower(), "nirmala")
        self.assertEqual(intent["filter_dict"].get("scheduled_date"), "last 30 days")


if __name__ == "__main__":
    unittest.main()
