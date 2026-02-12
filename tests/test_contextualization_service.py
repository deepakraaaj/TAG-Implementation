import unittest

from langchain_core.messages import AIMessage, HumanMessage

from app.services.contextualization_service import ContextualizationService


class TestContextualizationService(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
