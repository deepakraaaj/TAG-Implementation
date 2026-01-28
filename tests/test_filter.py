import sys
import os
import asyncio

# Ensure we can import from top level
sys.path.append("/app")

from app.services.vector import vector_service

# Mock filters
filters = {
    "category": "safety"
}

query = "What should I do with chemicals?"

async def test_filter():
    print(f"🔍 Searching for: '{query}' with filter: {filters}")
    results = await vector_service.search_semantic(query, limit=5, filters=filters)
    
    print(f"✅ Found {len(results)} results.")
    for res in results:
        print(f"- {res['metadata'].get('title')} (Category: {res['metadata'].get('category')})")
        if res['metadata'].get('category') != "safety":
             print("❌ ERROR: Result does not match filter!")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_filter())
