"""Test-only Windows loopback relay; request/token arrive through stdin."""
import base64
import http.client
import json
import sys

data = json.load(sys.stdin)
assert data["path"] == "/api/chat" and data["method"] == "POST"
conn = http.client.HTTPConnection("127.0.0.1", 18080, timeout=120)
try:
    conn.request(data["method"], data["path"], body=base64.b64decode(data["body"]),
                 headers=data["headers"])
    response = conn.getresponse()
    print(json.dumps({
        "status": response.status,
        "headers": dict(response.getheaders()),
        "body": base64.b64encode(response.read()).decode(),
    }))
finally:
    conn.close()
