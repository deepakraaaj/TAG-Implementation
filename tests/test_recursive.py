import requests
import json
import time

url = "http://localhost:8001/chat"
session_id = f"slot_fill_test_{int(time.time())}"

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
    chat("Insert a new record into task_transaction.")

    # Turn 2: Providing Schedule (system should have asked/listed)
    # Mocking that the user selected "Schedule 123"
    chat("Use schedule ID 1.")

    # Turn 3: Providing Assignee
    chat("Assign to user 5.")

    # Turn 4: Final Insert? if other fields are optional
    # chat("Go ahead.")
