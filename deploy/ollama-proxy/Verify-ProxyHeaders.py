"""Isolated network proof of header removal using in-memory mock upstream.

Production config stays fixed at 127.0.0.1:11434. This check stops before the
real Ollama checks and uses the same Caddy loopback listener, never a bypass.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import threading
import time

settings = json.load(sys.stdin)
config = settings["config"]
token = settings["token"]
received = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        received.append(dict(self.headers))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')


server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
for route in config["apps"]["http"]["servers"]["ollama"]["routes"][0]["handle"][0]["routes"]:
    for handler in route["handle"]:
        if handler["handler"] == "reverse_proxy":
            handler["upstreams"] = [{"dial": f"127.0.0.1:{server.server_port}"}]
proxy = subprocess.Popen(
    [settings["caddy"], "run", "--config", "-"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, creationflags=subprocess.CREATE_NO_WINDOW,
)
try:
    proxy.stdin.write(json.dumps(config))
    proxy.stdin.close()
    for _ in range(50):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 18080, timeout=3)
            conn.request("GET", "/api/tags",
                         headers={"Authorization": "Bearer " + token})
            response = conn.getresponse()
            assert response.status == 200
            response.read()
            conn.close()
            break
        except ConnectionRefusedError:
            time.sleep(0.1)
    else:
        raise AssertionError("Caddy not ready")
    assert received
    assert all("authorization" not in {k.lower() for k in headers} for headers in received)
    print("PASS network proof: Authorization absent at mock upstream behind Caddy", flush=True)
finally:
    proxy.kill()
    proxy.wait()
    output = proxy.stdout.read() + proxy.stderr.read()
    assert not output, "Caddy produced output (suppressed)"
    server.shutdown()
    server.server_close()
