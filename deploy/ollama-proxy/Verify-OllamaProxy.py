"""Windows stdlib live checks. Read the token from stdin, never print it."""
import http.client
import json
import sys
import time

token = json.load(sys.stdin)["token"]


def request(method, path, auth=token, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", 18080, timeout=120)
    headers = {"Content-Type": "application/json"}
    if auth is not None:
        headers["Authorization"] = "Bearer " + auth
    try:
        conn.request(method, path, body=json.dumps(payload) if payload is not None else None,
                     headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


status, body = request("GET", "/api/tags")
assert status == 200
assert "qwen3.5:9b" in [m["name"] for m in json.loads(body)["models"]]
print("PASS authenticated tags", flush=True)
assert request("GET", "/api/tags", auth=None)[0] == 401
assert request("GET", "/api/tags", auth="invalid")[0] == 401
print("PASS missing/invalid token rejected", flush=True)
for method, path in [
    ("POST", "/api/pull"), ("POST", "/api/push"), ("POST", "/api/create"),
    ("POST", "/api/copy"), ("DELETE", "/api/delete"), ("POST", "/api/delete"),
    ("POST", "/api/generate"), ("GET", "/api/chat"), ("POST", "/api/tags"),
    ("GET", "/"), ("POST", "/api/chat/"), ("POST", "/api/%70ull"),
]:
    assert request(method, path, payload={})[0] == 403, (method, path)
print("PASS management/unknown paths and wrong methods blocked", flush=True)
payload = {
    "model": "qwen3.5:9b",
    "messages": [{"role": "user", "content": "Reply with only OK."}],
    "stream": False, "think": False,
    "options": {"temperature": 0, "num_predict": 32},
}
status, body = request("POST", "/api/chat", payload=payload)
assert status == 200
assert json.loads(body)["message"]["content"].strip() == "OK"
print("PASS short inference", flush=True)

conn = http.client.HTTPConnection("127.0.0.1", 18080, timeout=120)
payload["stream"] = True
payload["messages"] = [{"role": "user", "content": "Write the integers 1 through 20 separated by spaces."}]
payload["options"]["num_predict"] = 128
start = time.monotonic()
conn.request("POST", "/api/chat", body=json.dumps(payload),
             headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
response = conn.getresponse()
assert response.status == 200
chunks = []
arrival = []
try:
    while line := response.readline():
        if line.strip():
            chunks.append(json.loads(line))
            arrival.append(time.monotonic() - start)
finally:
    conn.close()
assert len(chunks) > 2
assert chunks[-1]["done"]
assert any(item.get("message", {}).get("content") for item in chunks[:-1])
assert arrival[0] < arrival[-1]
print(f"PASS streaming: {len(chunks)} NDJSON chunks, first={arrival[0]:.2f}s last={arrival[-1]:.2f}s", flush=True)
