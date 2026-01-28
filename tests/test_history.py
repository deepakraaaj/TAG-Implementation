import requests
import json
import time

url = "http://localhost:8001/chat"
session_id = f"history_test_{int(time.time())}"

def chat(msg):
    print(f"\nUser: {msg}")
    data = {"message": msg, "session_id": session_id}
    # Using streaming endpoint but just grabbing lines
    resp = requests.post(url, json=data, stream=True)
    full_response = ""
    for line in resp.iter_lines():
        if line:
            try:
                obj = json.loads(line.decode('utf-8'))
                if obj['type'] == 'token':
                    full_response = obj['content']
                elif obj['type'] == 'result':
                     if obj.get('sql'):
                         print(f"[SQL]: {obj['sql']['query']}")
                     if obj.get('toon'):
                         print(f"[TOON]: Compression Savings: {obj['toon']['meta']['savings']}")
                         print(f"[TOON Payload Size]: {obj['toon']['meta']['toon_len']} bytes")
            except:
                pass
    print(f"AI: {full_response}")
    return full_response

if __name__ == "__main__":
    # Turn 1: List
    chat("Show me all schedulers")

    # Turn 2: Contextual Filter
    # Turn 2: Contextual Filter
    chat("Only those with 'Morning' in the name")

    # Turn 3: Repeat (Should hit cache)
    # Turn 4: Vector Search (RAG)
    print("\n--- TESTING VECTOR SEARCH (TOON) ---")
    chat("How to add a new user?")
