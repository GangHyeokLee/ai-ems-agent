"""PH4-01 Chrome regression: real chat routes with scripted graphs, never a real LLM.

Every case has a hard 210-second limit; Chrome, server startup and all HTTP waits are
also bounded. Evidence gets a unique directory so previous screenshots stay intact.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

import pytest

from browser.chrome_env import LOGS_DIR, SCREENSHOTS_DIR, run_chrome_page
from test_browser_stale_responses import find_chrome, free_port

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = uuid.uuid4().hex[:12]
SUITE = f"console_chat_ph401/{RUN_ID}"
CASES = ("sessions", "races", "errors", "gating", "coexistence", "timeout")
pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_BROWSER_TESTS") != "1", reason="Chrome regression is opt-in")


@pytest.fixture(autouse=True)
def bounded_case():
    def expired(signum, frame):
        raise TimeoutError("PH4-01 case exceeded its explicit 210-second limit")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 210)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="module")
def chat_console():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found: set AI_EMS_CHROME")
    log_dir = LOGS_DIR / SUITE
    log_dir.mkdir(parents=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with (log_dir / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "tests/browser/console_chat_app.py"), str(port)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Scripted chat test server unavailable; see {log_dir / 'server.log'}")
                    time.sleep(.1)
            yield base, chrome, log_dir
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("case", CASES)
def test_console_chat_ph401(chat_console, case):
    base, chrome, log_dir = chat_console
    screenshot = SCREENSHOTS_DIR / SUITE / f"{case}.png"
    run_chrome_page(
        chrome, f"{base}/console/?chat_probe={case}", suite=SUITE, label=case,
        screenshot=screenshot, budget=None, attempts=1, timeout=150, window="1600,1100",
        extra=("--disable-background-networking", "--disable-component-update",
               "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"),
    )
    with urllib.request.urlopen(base + "/__chat_probe__/state", timeout=5) as response:
        evidence = json.load(response)
    (log_dir / f"{case}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report = evidence["report"]
    assert report is not None, f"Browser probe did not finish: {log_dir / (case + '.json')}"
    assert report["case"] == case
    assert report["results"], report
    failures = [item for item in report["results"] if not item["pass"]]
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
    assert not report["errors"], report["errors"]
    assert screenshot.is_file() and screenshot.stat().st_size > 10_000
    assert all(hit["path"] != "/api/grids/kr-osm-transmission/chat" for hit in evidence["hits"])
    for hit in evidence["hits"]:
        assert set(hit["body"]) == {"session_id", "message", "grid_id"}
        assert hit["path"] == f"/api/grids/{hit['body']['grid_id']}/chat"
