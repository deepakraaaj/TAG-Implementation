import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService


class _CaptureWorkflow:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, *_args, **_kwargs):
        self.calls += 1
        return {
            "messages": [type("M", (), {"content": "workflow-response"})()],
            "sql_query": "",
            "error": None,
            "workflow_payload": None,
            "token_usage": None,
        }


async def _collect_events(service: ChatService, request: ChatRequest):
    events = []
    async for chunk in service.generate_chat_stream(request):
        events.append(json.loads(chunk))
    return events


def test_navigation_request_emits_redirect_payload_without_workflow():
    service = ChatService()
    workflow = _CaptureWorkflow()
    request = ChatRequest(
        session_id="nav-s1",
        message="take me to the tasks page",
        metadata={
            "page_routes": {
                "tasks": {
                    "path": "/work-orders",
                    "label": "Tasks",
                    "aliases": ["task", "tasks", "task page"],
                },
                "assets": "/equipment",
            }
        },
    )
    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 0
    assert events[0]["type"] == "token"
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "ok"
    assert events[-1]["message"] == "Opening the Tasks page."
    assert events[-1]["navigation"]["action"] == "redirect"
    assert events[-1]["navigation"]["target"] == "tasks"
    assert events[-1]["navigation"]["label"] == "Tasks"
    assert events[-1]["navigation"]["path"] == "/work-orders"


def test_navigation_request_with_unknown_target_suggests_known_pages():
    service = ChatService()
    workflow = _CaptureWorkflow()
    request = ChatRequest(
        session_id="nav-s2",
        message="where can i find the operations page",
        metadata={
            "page_routes": {
                "tasks": "/tasks",
                "assets": "/assets",
                "facilities": "/facilities",
            }
        },
    )
    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 0
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "ok"
    assert events[-1]["navigation"]["action"] == "suggest"
    assert "Tasks" in events[-1]["navigation"]["available_pages"]
    assert "Assets" in events[-1]["navigation"]["available_pages"]
    assert "I can help you navigate to pages like" in events[-1]["message"]
