import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.workflow.nodes.intent_analysis_node import IntentAnalysisNode
from app.workflow.nodes.sql_node import GenerateSQLNode

async def test_intent_analysis_structured():
    print("Testing IntentAnalysisNode with structured filter_dict...")
    node = IntentAnalysisNode()
    
    # Mock LLM
    node.llm = MagicMock()
    node.llm.ainvoke = AsyncMock(return_value=MagicMock(content="""
    {
      "intent_type": "aggregation",
      "entities": ["task"],
      "filter_dict": {"status": "Pending"},
      "metrics": ["count"],
      "original_intent": "count pending tasks"
    }
    """))
    
    state = {"messages": [MagicMock(content="how many tasks are pending")]}
    result = await node.run(state)
    
    intent = result["intent_analysis"]
    assert intent["intent_type"] == "aggregation"
    assert intent["filter_dict"]["status"] == "Pending"
    print("IntentAnalysisNode test PASSED")

async def test_task_aggregation_fast_path():
    print("\nTesting task aggregation fast-path in GenerateSQLNode...")
    node = GenerateSQLNode()
    
    # Mock services
    node.query_understanding.analyze = AsyncMock(return_value={
        "intent": "aggregation",
        "entities": ["task"],
        "filters": ["pending"] # Legacy
    })
    
    intent_analysis = {
        "intent_type": "aggregation",
        "entities": ["task"],
        "filter_dict": {"status": "Pending"}
    }
    
    state = {
        "messages": [MagicMock(content="how many tasks are pending")],
        "intent_analysis": intent_analysis,
        "metadata": {
            "company_id": 12345,
            "user_id": 678,
            "user_role": "user"
        }
    }
    
    # We need to mock table selector to avoid DB calls in some paths, 
    # but the fast-path should hit BEFORE table selection.
    
    result = await node.run(state)
    sql = result["sql_query"]
    
    assert "SELECT COUNT(*)" in sql
    assert "tt.status) = 'pending'" in sql.lower()
    # As a 'user', it should also filter by assigned_user_id
    assert "tt.assigned_user_id = 678" in sql
    assert "f.company_id = 12345" in sql
    
    print("Task aggregation fast-path test PASSED")

async def test_task_aggregation_admin():
    print("\nTesting task aggregation (Admin) fast-path...")
    node = GenerateSQLNode()
    
    intent_analysis = {
        "intent_type": "aggregation",
        "entities": ["task"],
        "filter_dict": {"status": "Completed"}
    }
    
    state = {
        "messages": [MagicMock(content="how many completed tasks")],
        "intent_analysis": intent_analysis,
        "metadata": {
            "company_id": 12345,
            "user_id": 1,
            "user_role": "admin"
        }
    }
    
    result = await node.run(state)
    sql = result["sql_query"]
    
    assert "SELECT COUNT(*)" in sql
    assert "tt.status) = 'completed'" in sql.lower()
    # As 'admin', it should NOT filter by assigned_user_id by default
    assert "tt.assigned_user_id =" not in sql
    
    print("Task aggregation (Admin) fast-path test PASSED")

if __name__ == "__main__":
    asyncio.run(test_intent_analysis_structured())
    asyncio.run(test_task_aggregation_fast_path())
    asyncio.run(test_task_aggregation_admin())
