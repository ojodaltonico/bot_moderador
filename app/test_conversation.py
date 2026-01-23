import requests

API = "http://127.0.0.1:8000/moderation/response"

payload = {
    "phone": "69634422268027",
    "response": "2"
}

r = requests.post(API, json=payload)
print("STATUS:", r.status_code)
print("RESPONSE:")
print(r.json())
