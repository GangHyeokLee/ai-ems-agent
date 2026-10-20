"""Chrome headless regression: stale Security/Sensitivity responses are never applied (KPG + Mongolia).

Opt-in because it needs a real Chrome and starts the full web app:

    AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_browser_stale_responses.py

Chrome is taken from ``AI_EMS_CHROME`` or found automatically (Windows Chrome from WSL, or
google-chrome / chromium on PATH). The probe ``tests/browser/stale_responses_probe.js`` is appended
to ``ui/index.html`` by a local proxy. The same proxy delays real API responses on demand: the
probe registers a gate (``/__probe__/gates``), the proxy forwards the matching request to the app,
holds the real response until the probe releases it (or then answers HTTP 500), so no delay is
added to application code. The probe awaits the app's own handlers instead of fixed sleeps.
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
UNIFIED_PROBE = ROOT / "tests" / "browser" / "unified_ui_probe.js"
THEME_PROBE = ROOT / "tests" / "browser" / "theme_help_probe.js"
PROBES = {"stale": PROBE, "unified": UNIFIED_PROBE, "theme": THEME_PROBE}
THEME_SHOT_DIR = ROOT / "outputs" / "browser_theme_help"
THEME_SCREENSHOT_STOPS = ["kpg_dark", "help_model_light", "help_model_kpg_dark", "help_model_mn_dark", "mn_dark",
                          "mn_dark_details", "help_assistant_mn_dark"]
INDEX = ROOT / "ui" / "index.html"
SHOT_DIR = ROOT / "outputs" / "browser_stale_responses"
WINDOWS_CHROME = Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
SCREENSHOT_STOPS = ["mn_security", "mn_sensitivity_monitored", "kpg_sensitivity", "switch_kpg_to_mn"]
UNIFIED_SHOT_DIR = ROOT / "outputs" / "browser_unified_ui"
UNIFIED_SCREENSHOT_STOPS = ["kpg_security", "kpg_sensitivity", "mn_security", "mn_security_details",
                            "mn_sensitivity", "mn_sensitivity_details", "mn_island", "kpg_chat"]


def chat_fixture_responses() -> dict[str, dict]:
    """Real /api/chat responses built from real KPG tool outputs (only the LLM is scripted)."""
    import importlib.util

    sys.path.insert(0, str(ROOT))
    import web_app

    spec = importlib.util.spec_from_file_location("chat_fixtures", ROOT / "tests" / "browser" / "chat_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {key: module.build_chat_response(web_app, key)[0] for key in module.SCENARIOS}

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


class Gates:
    """Response gates shared by proxy threads: hold or fail the next request matching a substring."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[int, dict] = {}

    def reset(self):
        with self.lock:
            for item in self.items.values():
                item["released"].set()       # never leave a thread blocked across page loads
            self.items.clear()

    def add(self, match: str, fail: bool) -> int:
        with self.lock:
            gate_id = len(self.items) + 1
            self.items[gate_id] = {"match": match, "fail": fail, "used": False,
                                   "requested": threading.Event(), "released": threading.Event()}
            return gate_id

    def claim(self, path: str):
        with self.lock:
            for item in self.items.values():
                if not item["used"] and item["match"] in path:
                    item["used"] = True
                    item["requested"].set()
                    return item
        return None


def make_proxy(upstream: str, gates: Gates, chat_fixtures: dict[str, dict] | None = None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status: int, value: dict):
            body = json.dumps(value).encode("utf-8")
            self._send(status, "application/json", body)

        def _send(self, status: int, ctype: str, body: bytes):
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass   # Chrome closed the page (dump/screenshot finished)

        def _control(self, method: str) -> bool:
            if self.path.startswith("/__probe__/tick"):
                self._json(200, {"ok": True})
                return True
            if not self.path.startswith("/__probe__/gates"):
                return False
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            if method == "POST" and len(parts) == 2:
                spec = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self._json(200, {"id": gates.add(str(spec["match"]), bool(spec.get("fail")))})
            elif method == "GET" and len(parts) == 3:
                item = gates.items[int(parts[2])]
                if "wait=1" in self.path:
                    item["requested"].wait(timeout=120)
                self._json(200, {"requested": item["requested"].is_set()})
            elif method == "POST" and len(parts) == 4 and parts[3] == "release":
                gates.items[int(parts[2])]["released"].set()
                self._json(200, {"released": True})
            else:
                self._json(404, {"detail": "unknown probe control"})
            return True

        def do_GET(self):
            if self._control("GET"):
                return
            if self.path == "/" or self.path.startswith("/?"):
                gates.reset()
                name = re.search(r"probe=(\w+)", self.path)
                probe = PROBES.get(name.group(1) if name else "stale", PROBE)
                html = INDEX.read_text(encoding="utf-8").replace(
                    "</body>", f"<script>{probe.read_text(encoding='utf-8')}</script></body>")
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._forward(None)

        def do_POST(self):
            if self._control("POST"):
                return
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.startswith("/api/chat") and chat_fixtures:
                match = re.search(r"\[fixture:(\w+)\]", data.decode("utf-8", "replace"))
                if match and match.group(1) in chat_fixtures:
                    # LLM stand-in: the response was produced by the real /api/chat code from real tool output.
                    self._json(200, chat_fixtures[match.group(1)])
                    return
            self._forward(data)

        def _forward(self, data):
            gate = gates.claim(self.path)
            request = urllib.request.Request(upstream + self.path, data=data,
                                             method="POST" if data is not None else "GET")
            if data is not None:
                request.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
            try:
                with urllib.request.urlopen(request, timeout=300) as resp:
                    body, status, ctype = resp.read(), resp.status, resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as err:
                body, status, ctype = err.read(), err.code, err.headers.get("Content-Type", "")
            if gate is not None:
                # Real API response computed by the app, delivered only when the probe releases it.
                gate["released"].wait(timeout=300)
                if gate["fail"]:
                    self._json(500, {"detail": "forced late failure (test proxy)"})
                    return
            self._send(status, ctype or "application/octet-stream", body)

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
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", proxy_port),
                                                make_proxy(upstream, Gates(), chat_fixture_responses()))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{proxy_port}/", chrome
        proxy.shutdown()
    finally:
        server.terminate()
        server.wait(timeout=30)


def run_chrome(chrome: tuple[Path, bool], url: str, profile: str, screenshot: Path | None = None,
               window: str = "1600,1200") -> str:
    path, windows = chrome
    if windows:
        profile_dir = f"C:\\Windows\\Temp\\ai-ems-{profile}"
        cwd = "/mnt/c"
    else:
        profile_dir = f"/tmp/ai-ems-{profile}"
        cwd = None
    args = [str(path), "--headless=new", f"--user-data-dir={profile_dir}", "--no-first-run",
            f"--window-size={window}", "--virtual-time-budget=600000"]
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


def expected_checks(probe: Path) -> int:
    source = probe.read_text(encoding="utf-8")
    # check("...") calls plus checkText("...") calls (each records exactly one check).
    return len(re.findall(r'\bcheck\("(?!probe exception)', source)) + len(re.findall(r'\bcheckText\("', source))


def missing_checks(probe: Path, results: list[dict]) -> list[str]:
    """Names of checks declared in the probe source that did not run (covers helper-generated checks)."""
    source = probe.read_text(encoding="utf-8")
    ran = {item["name"] for item in results}
    missing = [name for name in set(re.findall(r'\bcheck\("([^"]+)"', source)) - {"probe exception"} if name not in ran]
    missing += [f"contrast {label}" for label in re.findall(r'\bcheckText\("([^"]+)"', source)
                if not any(name.startswith(f"contrast {label} >=") for name in ran)]
    missing += [f"{label}: layout" for label in re.findall(r'\bcheckHelpLayout\("([^"]+)"', source)
                if f"{label}: no line break inside a Korean word" not in ran]
    return missing


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
    expected = expected_checks(PROBE)
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


def test_unified_ui_same_components_for_kpg_and_mongolia(app_url):
    url, chrome = app_url
    report = probe_results(run_chrome(chrome, f"{url}?probe=unified", "unified-probe"))
    results = report["results"]
    UNIFIED_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIFIED_SHOT_DIR / "results.json").write_text(
        json.dumps({"results": results, "visible_text": report["shots"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [item for item in results if not item["pass"]]
    assert len(results) == expected_checks(UNIFIED_PROBE), [item["name"] for item in results]
    assert not failed, json.dumps(failed, ensure_ascii=False, indent=2)


def test_unified_ui_screenshots(app_url):
    url, chrome = app_url
    for stop in UNIFIED_SCREENSHOT_STOPS:
        shot = UNIFIED_SHOT_DIR / f"{stop}.png"
        if shot.exists():
            shot.unlink()
        run_chrome(chrome, f"{url}?probe=unified&stop={stop}", f"unified-shot-{stop}", screenshot=shot)
        assert shot.is_file() and shot.stat().st_size > 10_000, stop


def run_theme_probe(app_url, profile: str, window: str = "1600,1200", stop: str | None = None) -> dict:
    url, chrome = app_url
    target = f"{url}?probe=theme" + (f"&stop={stop}" if stop else "")
    return probe_results(run_chrome(chrome, target, profile, window=window))


def test_theme_and_help_on_both_grids(app_url):
    report = run_theme_probe(app_url, f"theme-probe-{int(time.time())}")
    results = report["results"]
    THEME_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    (THEME_SHOT_DIR / "results.json").write_text(
        json.dumps({"results": results, "contrast": report["contrast"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [item for item in results if not item["pass"]]
    assert not missing_checks(THEME_PROBE, results), missing_checks(THEME_PROBE, results)
    assert not failed, json.dumps(failed, ensure_ascii=False, indent=2)


def test_help_popover_stays_inside_a_small_viewport(app_url):
    # Runs the light-theme help checks (hover/click/keyboard/viewport) and stops with the help open.
    report = run_theme_probe(app_url, f"theme-narrow-{int(time.time())}", window="820,640", stop="help_model_kpg_dark")
    failed = [item for item in report["results"] if not item["pass"]]
    assert report["results"] and not failed, json.dumps(failed, ensure_ascii=False, indent=2)
    (THEME_SHOT_DIR / "results_narrow.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def test_theme_screenshots(app_url):
    url, chrome = app_url
    for stop in THEME_SCREENSHOT_STOPS:
        shot = THEME_SHOT_DIR / f"{stop}.png"
        if shot.exists():
            shot.unlink()
        run_chrome(chrome, f"{url}?probe=theme&stop={stop}", f"theme-shot-{stop}-{int(time.time())}", screenshot=shot)
        assert shot.is_file() and shot.stat().st_size > 10_000, stop
    narrow = THEME_SHOT_DIR / "help_model_narrow.png"
    run_chrome(chrome, f"{url}?probe=theme&stop=help_model_kpg_dark", f"theme-shot-narrow-{int(time.time())}",
               screenshot=narrow, window="820,640")
    assert narrow.is_file()
