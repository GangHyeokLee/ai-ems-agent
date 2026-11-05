"""MAINT-03 initial map/PF ordering and cancellation through the real FastAPI routes.

The existing proxy only holds or faults explicitly selected responses. It never
serializes requests for the application. No Agent or real LLM is called.
"""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

import pytest
import test_console_browser as existing
from browser.chrome_env import LOGS_DIR, SCREENSHOTS_DIR, run_chrome_page
from test_browser_stale_responses import find_chrome, free_port

ROOT = Path(__file__).resolve().parents[1]
SUITE = f"console_loading_maint03/{uuid.uuid4().hex[:12]}"
pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_BROWSER_TESTS") != "1", reason="Chrome regression is opt-in")


@pytest.fixture(autouse=True)
def bounded_case():
    def expired(*args):
        raise TimeoutError("MAINT-03 loading case exceeded 210 seconds")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 210)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="module")
def loading_console():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found: set AI_EMS_CHROME")
    logs = LOGS_DIR / SUITE
    logs.mkdir(parents=True)
    port, proxy_port = free_port(), free_port()
    upstream = f"http://127.0.0.1:{port}"
    with (logs / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        proxy = None
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(upstream + "/api/grids", timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"MAINT-03 server unavailable: {logs / 'server.log'}")
                    time.sleep(.1)
            # make_proxy captures the probe source on construction; existing tests stay intact.
            class HeldResponse(threading.Event):
                response_ready = False

                def wait(self, timeout=None):
                    # Existing proxy calls wait only after reading the upstream body.
                    self.response_ready = True
                    return super().wait(timeout)

            class ResponseGates(existing.PathGates):
                def add(self, match, fail):
                    gate_id = super().add(match, fail)
                    self.items[gate_id]["released"] = HeldResponse()
                    return gate_id

            gates = ResponseGates()
            original = existing.PROBE
            try:
                existing.PROBE = ROOT / "tests/browser/console_loading_probe.js"
                base_handler = existing.make_proxy(upstream, gates, {})
            finally:
                existing.PROBE = original
            class Handler(base_handler):
                def _gates(self, method):
                    if method == "GET" and self.path.endswith("/response-ready"):
                        gate_id = int(self.path.split("/")[-2])
                        return self._json(200, {"ready": gates.items[gate_id]["released"].response_ready})
                    return super()._gates(method)

            proxy = http.server.ThreadingHTTPServer(("127.0.0.1", proxy_port), Handler)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            yield f"http://127.0.0.1:{proxy_port}", chrome, logs
        finally:
            if proxy:
                proxy.shutdown()
                proxy.server_close()
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


@pytest.mark.parametrize("case", ["order", "failures", "cancel", "timeout"])
def test_console_initial_map_pf_loading(loading_console, case):
    base, chrome, logs = loading_console
    dom = run_chrome_page(chrome, f"{base}/console/?probe=perf&loading_case={case}", suite=SUITE, label=case, budget=None, attempts=1, timeout=150,
                          extra=("--disable-background-networking", "--disable-component-update", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"))
    report = existing.report_of(dom)
    (logs / f"{case}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    existing.assert_passed(report)
