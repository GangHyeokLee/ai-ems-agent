"""Chrome headless regression: stale Security/Sensitivity responses are never applied (KPG + Mongolia).

Opt-in because it needs a real Chrome and starts the full web app:

    AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_browser_stale_responses.py

Chrome is taken from ``AI_EMS_CHROME`` or found automatically (Windows Chrome from WSL, or
google-chrome / chromium on PATH). The probe ``tests/browser/stale_responses_probe.js`` is appended
to ``ui/index.html`` by a local proxy; it controls response order by intercepting ``fetch`` and
awaits the app's own handlers, so no delay is added to application code and no fixed sleeps are used.
Screenshots of selected end states are written to ``outputs/browser_stale_responses/``.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "browser" / "stale_responses_probe.js"
INDEX = ROOT / "ui" / "index.html"
SHOT_DIR = ROOT / "outputs" / "browser_stale_responses"
WINDOWS_CHROME = Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
SCREENSHOT_STOPS = ["mn_security", "mn_sensitivity_monitored", "kpg_sensitivity", "switch_kpg_to_mn"]

pytestmark = pytest.mark.skipif(
    os.environ.get("AI_EMS_BROWSER_TESTS") != "1",
    reason="browser regression is opt-in: set AI_EMS_BROWSER_TESTS=1",
)


def find_chrome() -> tuple[Path, bool] | None:
    configured = os.environ.get("AI_EMS_CHROME")
    if configured:
        path = Path(configured)
        return path, path.suffix.lower() == ".exe"
    if WINDOWS_CHROME.is_file():
        return WINDOWS_CHROME, True
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return Path(found), False
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_proxy(upstream: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                html = INDEX.read_text(encoding="utf-8").replace(
                    "</body>", f"<script>{PROBE.read_text(encoding='utf-8')}</script></body>")
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._forward(None)

        def do_POST(self):
            self._forward(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

        def _forward(self, data):
            request = urllib.request.Request(upstream + self.path, data=data,
                                             method="POST" if data is not None else "GET")
            if data is not None:
                request.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
            try:
                with urllib.request.urlopen(request, timeout=300) as resp:
                    body, status, ctype = resp.read(), resp.status, resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as err:
                body, status, ctype = err.read(), err.code, err.headers.get("Content-Type", "")
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass   # Chrome closed the page (dump/screenshot finished)

    return Handler


@pytest.fixture(scope="module")
def app_url():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found (set AI_EMS_CHROME)")
    app_port, proxy_port = free_port(), free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", str(app_port),
         "--log-level", "warning"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    upstream = f"http://127.0.0.1:{app_port}"
    try:
        deadline = time.time() + 180
        while True:
            try:
                urllib.request.urlopen(upstream + "/api/grids", timeout=5).read()
                break
            except OSError:
                if time.time() > deadline or server.poll() is not None:
                    raise RuntimeError("web app did not start")
                time.sleep(0.5)
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", proxy_port), make_proxy(upstream))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{proxy_port}/", chrome
        proxy.shutdown()
    finally:
        server.terminate()
        server.wait(timeout=30)


def run_chrome(chrome: tuple[Path, bool], url: str, profile: str, screenshot: Path | None = None) -> str:
    path, windows = chrome
    if windows:
        profile_dir = f"C:\\Windows\\Temp\\ai-ems-{profile}"
        cwd = "/mnt/c"
    else:
        profile_dir = f"/tmp/ai-ems-{profile}"
        cwd = None
    args = [str(path), "--headless=new", f"--user-data-dir={profile_dir}", "--no-first-run",
            "--window-size=1600,1200", "--virtual-time-budget=600000"]
    target = None
    if screenshot is not None:
        target = Path("/mnt/c/Users/Public") / screenshot.name if windows else screenshot
        win_target = "C:\\Users\\Public\\" + screenshot.name if windows else str(target)
        args.append(f"--screenshot={win_target}")
    else:
        args.append("--dump-dom")
    args.append(url)
    # Retries cover harness start-up only: a fresh Chrome profile can fail its first launch, and
    # WSL2 localhost forwarding needs a moment before Windows Chrome can reach a newly bound port.
    for attempt in range(5):
        if attempt:
            time.sleep(2)
        done = subprocess.run(args, cwd=cwd, capture_output=True, timeout=600)
        if b"ERR_CONNECTION_REFUSED" in done.stderr:
            continue
        if screenshot is not None:
            if target.is_file():
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), screenshot)
                return ""
        elif done.stdout:
            return done.stdout.decode("utf-8", "replace")
    raise RuntimeError(f"Chrome produced no output: {done.stderr[-500:]!r}")


def probe_results(dom: str) -> dict:
    match = re.search(r'<pre id="__probe_result">(.*?)</pre>', dom, re.S)
    assert match, "probe did not finish"
    return json.loads(unescape(match.group(1)))


def test_stale_responses_are_never_applied(app_url):
    url, chrome = app_url
    report = probe_results(run_chrome(chrome, url, "stale-probe"))
    results = report["results"]
    (SHOT_DIR).mkdir(parents=True, exist_ok=True)
    (SHOT_DIR / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [item for item in results if not item["pass"]]
    # Every static check in the probe must have run (no scenario silently skipped).
    expected = len(re.findall(r'\bcheck\("(?!probe exception)', PROBE.read_text(encoding="utf-8")))
    assert len(results) == expected, [item["name"] for item in results]
    assert not failed, json.dumps(failed, ensure_ascii=False, indent=2)


def test_screenshots_of_end_states(app_url):
    url, chrome = app_url
    for stop in SCREENSHOT_STOPS:
        shot = SHOT_DIR / f"{stop}.png"
        if shot.exists():
            shot.unlink()
        run_chrome(chrome, f"{url}?stop={stop}", f"stale-shot-{stop}", screenshot=shot)
        assert shot.is_file() and shot.stat().st_size > 10_000, stop
