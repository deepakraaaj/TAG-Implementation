import requests
import base64
import json

# Prepare context
context = {
    "user_id": "test_user_b64",
    "company_id": 99,
    "user_role": "user"
}
# Encode to Base64
json_str = json.dumps(context)
b64_header = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")

print(f"Encoded Header: {b64_header}")

url = "http://localhost:8001/chat"
headers = {
    "x-user-context": b64_header,
    "Content-Type": "application/json"
}
data = {
    "message": "Show me all users",
    "session_id": "b64_test_1"
}

if __name__ == "__main__":
    try:
        response = requests.post(url, headers=headers, json=data, stream=True)
        print(f"Status: {response.status_code}")
        for line in response.iter_lines():
            if line:
                print(line.decode('utf-8'))
    except Exception as e:
        print(f"Error: {e}")
