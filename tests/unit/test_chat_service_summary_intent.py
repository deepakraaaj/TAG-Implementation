from app.services.chat_service import ChatService


def test_summary_intent_detection_keywords():
    assert ChatService._is_summary_request("give me summary for the above list")
    assert ChatService._is_summary_request("how many tasks are complete for now")
    assert not ChatService._is_summary_request("show task list for today")
