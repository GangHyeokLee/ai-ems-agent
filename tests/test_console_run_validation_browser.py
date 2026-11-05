"""PH4-02C-2b Chrome execution test: the pure run validators on real B3-2 envelopes; never a real LLM.

The fixture server produces every response through the real web_app run and manual-receipt paths at
start-up (only the LLM is scripted, with the B3-2 contract-test cases). Chrome then runs the real
ui/console/api-analysis.js on those responses and on explicit corruptions of them. Opt-in like the
other Chrome regressions; the case, Chrome, server start-up and HTTP waits are all bounded.
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

from browser.chrome_env import LOGS_DIR, run_chrome_page
from test_browser_stale_responses import find_chrome, free_port

ROOT = Path(__file__).resolve().parents[1]
SUITE = f"console_run_validation_c2b/{uuid.uuid4().hex[:12]}"
pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_BROWSER_TESTS") != "1", reason="Chrome regression is opt-in")


@pytest.fixture(autouse=True)
def bounded_case():
    def expired(signum, frame):
        raise TimeoutError("PH4-02C-2b case exceeded its explicit 300-second limit")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 300)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="module")
def validation_server():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found: set AI_EMS_CHROME")
    log_dir = LOGS_DIR / SUITE
    log_dir.mkdir(parents=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with (log_dir / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "tests/browser/console_run_validation_app.py"), str(port)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 180  # two full KPG loads and every real run happen before the server starts
            while True:
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Run validation fixture unavailable; see {log_dir / 'server.log'}")
                    time.sleep(.2)
            yield base, chrome, log_dir
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_run_validators_accept_real_responses_and_reject_every_corruption(validation_server):
    base, chrome, log_dir = validation_server
    dom = run_chrome_page(chrome, f"{base}/__run_validation__/", suite=SUITE, label="validators", budget=None, attempts=1,
                          timeout=150, extra=("--disable-background-networking", "--disable-component-update",
                                              "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"))
    with urllib.request.urlopen(base + "/__run_validation__/state", timeout=5) as response:
        evidence = json.load(response)
    (log_dir / "validators.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report = evidence["report"]
    assert report is not None and 'id="status">done' in dom, f"Browser probe did not finish: {log_dir / 'validators.json'}"
    assert not report["errors"], report["errors"]
    failures = [item for item in report["results"] if not item["pass"]]
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
    names = [item["name"] for item in report["results"]]
    # Every generated real response was validated, and the corruption matrix ran.
    assert all(f"accept {case['name']}" in names for case in evidence["cases"]) and len(evidence["cases"]) >= 19
    assert sum(name.startswith("reject ") for name in names) >= 60
    assert evidence["provider_calls"] == 0 and evidence["legacy_calls"] == 0
