from app.assistant.services.intent_service import IntentService
from app.assistant.services.router_service import RouterService


def test_router_fallback_sql():
    assert RouterService.fallback("list users") == "SQL"


def test_router_fallback_chat():
    assert RouterService.fallback("hello there") == "CHAT"


def test_intent_fallback_insert():
    payload = IntentService.fallback("create asset named Pump")
    assert payload["operation"] == "insert"


def test_intent_fallback_update():
    payload = IntentService.fallback("update task status to done")
    assert payload["operation"] == "update"
