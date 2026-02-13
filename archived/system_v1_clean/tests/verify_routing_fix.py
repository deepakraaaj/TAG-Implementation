import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.workflow.router import RouterNode
from app.workflow.nodes.workflow_node import WorkflowNode
from app.services.schema_manifest_service import SchemaManifestService

async def test_routing():
    print("Testing RouterNode...")
    router = RouterNode()
    
    # Test case: "task list"
    state = {
        "messages": [MagicMock(content="task list")],
        "metadata": {"session_id": "test_session"},
        "rewritten_query": "task list"
    }
    
    # We mock the LLM response to simulate the fix
    router.llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"route":"SQL"}'))
    
    result = await router.route_query(state)
    print(f"Route for 'task list': {result['route']}")
    assert result['route'] == "SQL"
    print("Router test PASSED")

async def test_workflow_fallback():
    print("\nTesting WorkflowNode fallback...")
    node = WorkflowNode()
    
    # Simulate a case where router incorrectly sends to WORKFLOW but no workflow_id is set
    state = {
        "messages": [MagicMock(content="task list")],
        "metadata": {"session_id": "new_session_no_workflow"},
        "route": "WORKFLOW",
        "intent_analysis": {}
    }
    
    # Ensure executor returns no active workflow
    node.executor.get_active_workflow = AsyncMock(return_value=None)
    
    result = await node.run(state)
    print(f"WorkflowNode result: {result}")
    assert "error" in result
    assert "No workflow identified" in result["error"]
    print("WorkflowNode fallback test PASSED")

async def test_manifest_aliases():
    print("\nTesting SchemaManifest aliases...")
    manifest = SchemaManifestService()
    
    # Test entity resolution for 'tasks'
    intent = {"entities": ["tasks"]}
    table = manifest.resolve_entity_table("list tasks", intent)
    print(f"Resolved table for 'tasks': {table}")
    assert table == "task_transaction"
    
    # Test query template
    sql = manifest.render_query_template("task_transaction", "list", company_id=123)
    print(f"Rendered SQL: {sql}")
    assert "task_transaction" in sql
    assert "123" in sql
    print("Manifest test PASSED")

async def test_role_based_status():
    print("\nTesting Role-based Task Status SQL...")
    from app.workflow.nodes.sql_node import _maybe_build_task_status_sql
    manifest = SchemaManifestService()
    
    # Test User role
    user_sql = _maybe_build_task_status_sql(
        "my task status today",
        {"entities": ["task"]},
        company_id=56942686,
        user_id=11784848,
        user_role="user",
        schema_manifest=manifest
    )
    print(f"User SQL: {user_sql}")
    assert "assigned_user_id = 11784848" in user_sql
    assert "DATE(tt.scheduled_date) = CURDATE()" in user_sql
    
    # Test Admin role
    admin_sql = _maybe_build_task_status_sql(
        "team task status today",
        {"entities": ["task"]},
        company_id=56942686,
        user_id=11784848,
        user_role="admin",
        schema_manifest=manifest
    )
    print(f"Admin SQL: {admin_sql}")
    assert "LEFT JOIN user u" in admin_sql
    assert "assigned_user_id" not in admin_sql # Admin sees all
    assert "DATE(tt.scheduled_date) = CURDATE()" in admin_sql
    
    print("Role-based Status test PASSED")

async def test_update_signal():
    print("\nTesting 'tasks update' signal...")
    from app.workflow.nodes.sql_node import _maybe_build_task_status_sql
    manifest = SchemaManifestService()
    
    # Test 'tasks update'
    update_sql = _maybe_build_task_status_sql(
        "tasks update",
        {"entities": ["task"]},
        company_id=56942686,
        user_id=11784848,
        user_role="user",
        schema_manifest=manifest
    )
    print(f"Update SQL: {update_sql}")
    assert "DATE(tt.scheduled_date) = CURDATE()" in update_sql
    assert "assigned_user_id = 11784848" in update_sql
    print("'tasks update' signal test PASSED")

if __name__ == "__main__":
    asyncio.run(test_routing())
    asyncio.run(test_workflow_fallback())
    asyncio.run(test_manifest_aliases())
    asyncio.run(test_role_based_status())
    asyncio.run(test_update_signal())
