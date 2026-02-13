import requests
import json
import time

url = "http://localhost:8001/chat"
session_id = f"workflow_test_{int(time.time())}"

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
            except:
                pass
    print(f"AI: {full_response}")
    return full_response

if __name__ == "__main__":
    # Turn 1: Intent
    chat("I want to create a schedule.")

    # Turn 2: Providing partial info
    chat("For user John.")

    # Turn 3: Providing date (Mocking John's ID since we don't have DB populated, expecting SELECT first)
    # Actually, the AI should first SELECT to find John. 
