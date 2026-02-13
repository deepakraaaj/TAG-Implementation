import unittest

from langchain_core.messages import AIMessage, HumanMessage

from app.services.contextualization_service import ContextualizationService


class TestContextualizationService(unittest.TestCase):
    def test_self_contained_operational_query(self):
        self.assertTrue(ContextualizationService.is_self_contained_operational_query("list assets"))
        self.assertTrue(ContextualizationService.is_self_contained_operational_query("assets"))
        self.assertTrue(ContextualizationService.is_self_contained_operational_query("show users"))
        self.assertFalse(
            ContextualizationService.is_self_contained_operational_query(
                "for that result show only active ones"
            )
        )

    def test_rewrites_structured_asset_followup(self):
        messages = [
            HumanMessage(content="create a asset"),
            AIMessage(
                content=(
                    "Sure, I can help you create an asset! What kind of asset are you looking to create? "
                    "Please provide more details."
                )
            ),
            HumanMessage(content="asset name : Water bottle category : Furnitures & seatings"),
        ]
        rewritten = ContextualizationService.infer_deterministic_rewrite(messages)
        self.assertIn("Create a new asset with details:", rewritten)
        self.assertIn("asset name : Water bottle", rewritten)
        self.assertIn("category : Furnitures & seatings", rewritten)

    def test_keeps_short_slot_fill_behavior(self):
        messages = [
            HumanMessage(content="create a asset"),
            AIMessage(content="What kind of asset are you looking to create?"),
            HumanMessage(content="Coffee mug"),
        ]
        rewritten = ContextualizationService.infer_deterministic_rewrite(messages)
        self.assertEqual(rewritten, "Create a new asset named Coffee mug")

    def test_does_not_rewrite_long_unstructured_followup(self):
        messages = [
            HumanMessage(content="create a asset"),
            AIMessage(content="What kind of asset are you looking to create?"),
            HumanMessage(content="I am checking options and will share details later once approved"),
        ]
        rewritten = ContextualizationService.infer_deterministic_rewrite(messages)
        self.assertEqual(rewritten, "")

    def test_detects_refinement_only_query(self):
        self.assertTrue(ContextualizationService.is_refinement_only_query("for last 30 days"))
        self.assertTrue(ContextualizationService.is_refinement_only_query("only high priority"))
        self.assertFalse(ContextualizationService.is_refinement_only_query("how many tasks for Nirmala"))

    def test_rewrites_time_refinement_followup_with_previous_user_query(self):
        messages = [
            HumanMessage(content="how many tasks are there for nirmala"),
            AIMessage(content="I found 1173 tasks for Nirmala. Please filter by date range."),
            HumanMessage(content="for last 30 days"),
        ]
        rewritten = ContextualizationService.infer_deterministic_rewrite(messages)
        self.assertEqual(
            rewritten,
            "how many tasks are there for nirmala for last 30 days",
        )


if __name__ == "__main__":
    unittest.main()
