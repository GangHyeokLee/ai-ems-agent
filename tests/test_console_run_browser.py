"""PH4-02C-2c Chrome regression: the console AI run on the real FastAPI/registry/PyPowSyBl paths.

Only the LLM is scripted (B3-2 ScriptedModel); never a real provider. Every case has a hard limit, and
Chrome, server start-up and HTTP waits are bounded. Evidence goes to a unique run directory.
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
SUITE = f"console_run_c2c/{RUN_ID}"
CASES = ("run_security", "run_sensitivity", "run_chain", "run_states", "run_first_ac", "run_reject", "run_late",
         "run_locks", "run_timeout", "run_regression", "gating_reference", "gating_none")
pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_BROWSER_TESTS") != "1", reason="Chrome regression is opt-in")


@pytest.fixture(autouse=True)
def bounded_case():
    def expired(signum, frame):
        raise TimeoutError("PH4-02C-2c case exceeded its explicit 240-second limit")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 240)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="module")
def run_console():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found: set AI_EMS_CHROME")
    log_dir = LOGS_DIR / SUITE
    log_dir.mkdir(parents=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with (log_dir / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "tests/browser/console_run_app.py"), str(port)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Run console fixture unavailable; see {log_dir / 'server.log'}")
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
def test_console_run_c2c(run_console, case):
    base, chrome, log_dir = run_console
    screenshot = SCREENSHOTS_DIR / SUITE / f"{case}.png"
    run_chrome_page(
        chrome, f"{base}/console/?run_probe={case}", suite=SUITE, label=case,
        screenshot=screenshot, budget=None, attempts=1, timeout=180, window="1600,1100",
        extra=("--disable-background-networking", "--disable-component-update",
               "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"),
    )
    with urllib.request.urlopen(base + "/__run_probe__/state", timeout=5) as response:
        evidence = json.load(response)
    (log_dir / f"{case}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report = evidence["report"]
    assert report is not None, f"Browser probe did not finish: {log_dir / (case + '.json')}"
    assert report["case"] == case and report["results"], report
    failures = [item for item in report["results"] if not item["pass"]]
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
    assert not report["errors"], report["errors"]
    assert screenshot.is_file() and screenshot.stat().st_size > 10_000
    # No run was ever started without an armed script, and only the scripted factory was used.
    assert not evidence["counts"].get("unscripted_run")
    for hit in evidence["hits"]:
        assert hit["path"] != "/api/grids/kr-osm-transmission/chat"
        if hit["body"] is not None:
            assert hit["path"] == f"/api/grids/{hit['body']['grid_id']}/chat"
            context = hit["body"].get("analysis_context") or {}
            assert context.get("mode") in (None, "reference", "run")
            if context.get("mode") == "run":
                assert hit["path"] == "/api/grids/kr-kpg193/chat" and context["references"] == {"security": None, "sensitivity": None}
        if "mn-osm-35k" in hit["path"]:
            assert not hit["headers"] and not (hit["body"] or {}).get("analysis_context")
