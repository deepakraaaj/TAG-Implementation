import asyncio
import json
import httpx
from pprint import pprint

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # POST to /chat endpoint directly assuming TAG backend is on port 8000
        # If it's a different port, we need to know. Assuming FastAPI default 8000.
        payload = {
            "session_id": "test-debug-123",
            "message": "List all network operators.",
            "metadata": {
                "company_id": 1,
                "app_id": "ims"
            }
        }
        print("Sending chat payload...")
        try:
            req = await client.post("http://localhost:8012/chat", json=payload)
            print(req.text)
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
