import os
import sys
import asyncio
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.workflow.router import RouterNode
from app.workflow.nodes.workflow_node import WorkflowNode
from app.workflow.engine.executor import MenuStep

async def test_router_cancellation_regex():
    print("Testing Router cancellation regex...")
    router = RouterNode()
    
    # Mock LLM to avoid real calls
    router.llm = MagicMock()
    
    state = {"messages": [], "metadata": {"session_id": "test_session"}}
    
    # Test 'cancel'
    state["rewritten_query"] = "cancel this"
    result = await router.route_query(state)
    assert result["route"] == "WORKFLOW"
    
    # Test 'stop'
    state["rewritten_query"] = "stop"
    result = await router.route_query(state)
    assert result["route"] == "WORKFLOW"
    
    print("Router cancellation regex test PASSED")

async def test_router_context_switch():
    print("\nTesting Router context switching...")
    router = RouterNode()
    
    # Mock Executor to simulate active session
    router.executor.get_active_workflow = AsyncMock(return_value="CREATE_SCHEDULE")
    
    # Mock LLM response for 'Hello' -> CHAT
    router.llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"route":"CHAT"}'))
    
    from langchain_core.messages import HumanMessage
    state = {
        "messages": [HumanMessage(content="Hello")],
        "metadata": {"session_id": "test_session"}
    }
    
    result = await router.route_query(state)
    assert result["route"] == "CHAT"
    print("Router context switch test PASSED")

async def test_workflow_explicit_cancel():
    print("\nTesting WorkflowNode explicit cancellation...")
    wf_node = WorkflowNode()
    
    # Mock Executor
    wf_node.executor.get_active_workflow = AsyncMock(return_value="CREATE_SCHEDULE")
    wf_node.executor.cancel = AsyncMock()
    
    from langchain_core.messages import HumanMessage
    state = {
        "messages": [HumanMessage(content="cancel")],
        "metadata": {"session_id": "test_session"},
        "route": "WORKFLOW"
    }
    
    result = await wf_node.run(state)
    assert "canceled" in result["messages"][0].content
    assert result["route"] == "CHAT"
    wf_node.executor.cancel.assert_called_once()
    print("WorkflowNode explicit cancel test PASSED")

def test_menu_search_exclusion():
    print("\nTesting MenuStep search exclusion...")
    step = MenuStep()
    
    # 'Hello' should NOT be a search term
    assert step._extract_search_term("Hello") is None
    # 'cancel' should NOT be a search term
    assert step._extract_search_term("cancel") is None
    # 'room' SHOULD be a search term
    assert step._extract_search_term("room") == "room"
    
    print("MenuStep search exclusion test PASSED")

if __name__ == "__main__":
    asyncio.run(test_router_cancellation_regex())
    asyncio.run(test_router_context_switch())
    asyncio.run(test_workflow_explicit_cancel())
    test_menu_search_exclusion()
