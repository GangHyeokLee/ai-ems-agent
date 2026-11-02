"""Chrome headless regression for the Artifact console (/console/).

Opt-in because it needs a real Chrome and starts the full web app:

    AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_console_browser.py

A local proxy serves the real ``/console/`` from the app with ``tests/browser/console_hook.js``
(errors, seeded Math.random, fixed Date, optional listener log) and ``tests/browser/console_probe.js``
injected, forwards every other request and records it, and can fail, drop, rewrite or hold the catalog,
map, display-geometry and PF responses (``?scenario=`` / probe gates). The performance test runs
without virtual time: the proxy holds one image so the DOM is dumped only after the probe finished.
The parity test runs the same probe on the original Artifact HTML kept in ``local-reference/``
(skipped when that untracked file is absent) and compares the demo fingerprints value by value.
Screenshots are written to ``.local/browser-tests/screenshots/console_ui/`` and probe reports, Chrome
stderr and the web app log to ``.local/browser-tests/logs/console_ui/``; every Chrome run uses its own
temporary profile (see ``tests/browser/chrome_env.py``).
"""
from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from browser.chrome_env import LOGS_DIR, SCREENSHOTS_DIR, log_path, run_chrome_page
from test_browser_stale_responses import Gates, find_chrome, free_port

pytestmark = pytest.mark.skipif(
    os.environ.get("AI_EMS_BROWSER_TESTS") != "1",
    reason="browser regression is opt-in: set AI_EMS_BROWSER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "tests" / "browser" / "console_hook.js"
PROBE = ROOT / "tests" / "browser" / "console_probe.js"
ORIGINAL = ROOT / "local-reference" / "계통 관제 의사결정지원 테스트베드 (데모).html"
SUITE = "console_ui"
SHOT_DIR = SCREENSHOTS_DIR / SUITE
LOG_DIR = LOGS_DIR / SUITE
BLOCK_FONTS = "--host-resolver-rules=MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND"
SCREENSHOTS = [
    ("demo_kpg193", "probe=shot&shot=demo", ()),
    ("demo_ieee39", "probe=shot&shot=ieee39", ()),
    ("demo_live_simulated", "probe=shot&shot=live", ()),
    ("api_kpg193_map", "probe=shot&shot=api-kpg", ()),
    ("api_kpg193_pf", "probe=shot&shot=api-kpg-pf", ()),
    ("api_mongolia_map", "probe=shot&shot=api-mn", ()),
    ("api_mongolia_pf", "probe=shot&shot=api-mn-pf", ()),
    ("api_korea_osm", "probe=shot&shot=api-kr", ()),
    ("api_demo_screen_gated", "probe=shot&shot=api-gate", ()),
    ("api_pf_error", "probe=shot&shot=api-pf-error&scenario=pf500", ()),
    ("api_map_error", "probe=shot&shot=api-error&scenario=map500", ()),
    ("api_gis_fallback", "probe=shot&shot=api-gis-fallback&scenario=geo404", ()),
    ("api_catalog_unavailable", "probe=shot&shot=demo&scenario=drop", ()),
    ("demo_fonts_blocked", "probe=shot&shot=demo", (BLOCK_FONTS,)),
]
# Response changes the proxy applies per ?scenario= (path -> status / drop / JSON rewrite).
KPG_MAP, KPG_PF = "/api/grids/kr-kpg193/map", "/api/grids/kr-kpg193/pf-preview"
FAULTS = {
    "pf500": lambda path: 500 if path.endswith("/pf-preview") else None,
    "map500": lambda path: 500 if path == KPG_MAP else None,
    "mapdrop": lambda path: "drop" if path == KPG_MAP else None,
    "geo404": lambda path: 404 if path.endswith("/display-geometry") else None,
}
REWRITES = {
    "geobad": lambda path: (lambda d: {**d, "grid_id": "kr-kpg193"}) if path.endswith("/display-geometry") else None,
    "pfgrid": lambda path: (lambda d: {**d, "metadata": {**d["metadata"], "grid_id": "mn-osm-35k"}}) if path == KPG_PF else None,
    "pfnonconv": lambda path: (lambda d: {**d, "metadata": {**d["metadata"], "pf_status": "AC_PF_FAILED"},
                                         "summary": {**d["summary"], "pf_converged": False}}) if path == KPG_PF else None,
}
GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


class PathGates(Gates):
    """Gates whose match may be an exact path ("=/api/grids"); the probe's own fetches never claim one."""

    def claim(self, path: str):
        if "__probe=" in path:
            return None
        bare = urlsplit(path).path
        with self.lock:
            for item in self.items.values():
                match = item["match"]
                if not item["used"] and (bare == match[1:] if match.startswith("=") else match in path):
                    item["used"] = True
                    item["requested"].set()
                    return item
        return None


def run_page(chrome, url: str, label: str, *, extra=(), screenshot: Path | None = None,
             budget: int | None = 600000, window: str = "1600,1000") -> str:
    # Each attempt uses its own temporary profile; retries cover WSL2 localhost forwarding lag.
    return run_chrome_page(chrome, url, suite=SUITE, label=label, screenshot=screenshot, window=window,
                           budget=budget, extra=extra, attempts=10, pause=3, timeout=900)


def wait_reachable_from_chrome(chrome, url: str, timeout: float = 60):
    """Windows Chrome reaches WSL ports through localhost forwarding, which can lag behind a new bind."""
    if not chrome[1]:
        return
    curl = Path("/mnt/c/Windows/System32/curl.exe")
    deadline = time.time() + timeout
    while curl.is_file():
        done = subprocess.run([str(curl), "-s", "-o", "NUL", "-w", "%{http_code}", url], cwd="/mnt/c", capture_output=True, timeout=30)
        if done.stdout == b"200" or time.time() > deadline:
            return
        time.sleep(1)


def report_of(dom: str) -> dict:
    matches = re.findall(r'<pre id="__probe_result"[^>]*>(.*?)</pre>', dom, re.S)
    assert matches, "probe did not finish"
    return json.loads(unescape(matches[-1]))


def make_proxy(upstream: str, gates: Gates, scenario: dict):
    hook = f"<script>{HOOK.read_text(encoding='utf-8')}</script>"
    probe = f"<script>{PROBE.read_text(encoding='utf-8')}</script>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, ctype: str, body: bytes):
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Chrome closed the page (dump/screenshot finished)

        def _json(self, status: int, value: dict):
            self._send(status, "application/json", json.dumps(value).encode("utf-8"))

        def _gates(self, method: str):
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

        def _page(self, original: bool):
            query = parse_qs(urlsplit(self.path).query)
            gates.reset()
            probe_mode = query.get("probe", ["parity"])[0]
            scenario.update(name=query.get("scenario", [probe_mode])[0], hits=[], loaded=threading.Event())
            if original:
                html = ORIGINAL.read_text(encoding="utf-8").replace("<script>", hook + "<script>", 1)
            else:
                with urllib.request.urlopen(upstream + "/console/", timeout=60) as resp:
                    html = resp.read().decode("utf-8")
                html = html.replace('<script src="models.js">', hook + '<script src="models.js">', 1)
            if probe_mode == "perf":
                # Without virtual time Chrome dumps the DOM at the load event: hold one image until the probe is done.
                html = html.replace('<body data-mode="pub">', '<body data-mode="pub"><img src="/__probe__/hold-load" alt="" hidden>', 1)
            self._send(200, "text/html; charset=utf-8", html.replace("</body>", probe + "</body>", 1).encode("utf-8"))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/__probe__/tick":
                return self._json(200, {"ok": True})
            if path == "/__probe__/hits":
                return self._json(200, {"hits": list(scenario.get("hits", []))})
            if path == "/__probe__/hold-load":
                scenario["loaded"].wait(timeout=900)
                return self._send(200, "image/gif", GIF)
            if path.startswith("/__probe__/gates"):
                return self._gates("GET")
            if path in ("/console/", "/__original__/"):
                return self._page(original=path == "/__original__/")
            if path.startswith("/api/"):
                scenario.setdefault("hits", []).append(self.path)
            name = scenario.get("name")
            if path == "/api/grids":
                if name == "fail500":
                    return self._json(500, {"detail": "forced failure (test proxy)"})
                if name == "drop":
                    self.close_connection = True
                    return  # no response at all: the browser sees a network error
                if name == "badjson":
                    return self._json(200, {"grids": "x"})
            fault = FAULTS[name](path) if name in FAULTS else None
            if fault == "drop":
                self.close_connection = True
                return
            if fault:
                return self._json(fault, {"detail": f"forced {fault} (test proxy)"})
            self._forward(rewrite=REWRITES[name](path) if name in REWRITES else None)

        def do_POST(self):
            if self.path.startswith("/__probe__/gates"):
                return self._gates("POST")
            if self.path.startswith("/__probe__/release-load"):
                scenario["loaded"].set()
                return self._json(200, {"released": True})
            self._forward(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

        def _forward(self, data: bytes | None = None, rewrite=None):
            gate = gates.claim(self.path)
            request = urllib.request.Request(upstream + self.path, data=data, method="POST" if data is not None else "GET")
            if data is not None:
                request.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
            try:
                with urllib.request.urlopen(request, timeout=300) as resp:
                    body, status, ctype = resp.read(), resp.status, resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as err:
                body, status, ctype = err.read(), err.code, err.headers.get("Content-Type", "")
            if rewrite is not None and status == 200:
                body = json.dumps(rewrite(json.loads(body))).encode("utf-8")
            if gate is not None:
                gate["released"].wait(timeout=300)  # real response, delivered when the probe releases it
                if gate["fail"]:
                    return self._json(500, {"detail": "forced late failure (test proxy)"})
            self._send(status, ctype or "application/octet-stream", body)

    return Handler


@pytest.fixture(scope="module")
def console():
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome not found (set AI_EMS_CHROME)")
    app_port, proxy_port = free_port(), free_port()
    server_log = log_path(SUITE, "web_app").open("a", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", str(app_port), "--log-level", "warning"],
        cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT,
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
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", proxy_port), make_proxy(upstream, PathGates(), {}))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        wait_reachable_from_chrome(chrome, f"http://127.0.0.1:{proxy_port}/__probe__/tick")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        yield f"http://127.0.0.1:{proxy_port}", chrome
        proxy.shutdown()
    finally:
        server.terminate()
        server.wait(timeout=30)
        server_log.close()


def run_probe(console, query: str, name: str, extra=(), budget: int | None = 600000) -> dict:
    base, chrome = console
    report = report_of(run_page(chrome, f"{base}/console/?{query}", name, extra=extra, budget=budget))
    (LOG_DIR / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def assert_passed(report: dict):
    failed = [item for item in report["results"] if not item["pass"]]
    assert report["results"] and not failed, json.dumps(failed, ensure_ascii=False, indent=2)
    assert report["errors"] == [], report["errors"]


def test_console_matches_original_artifact_demo(console):
    if not ORIGINAL.is_file():
        pytest.skip("original Artifact HTML not present in local-reference/")
    base, chrome = console
    # Fonts are blocked in both runs so layout cannot differ by web-font timing.
    original = report_of(run_page(chrome, f"{base}/__original__/?probe=parity", "parity-original", extra=(BLOCK_FONTS,)))
    ported = report_of(run_page(chrome, f"{base}/console/?probe=parity", "parity-console", extra=(BLOCK_FONTS,)))
    for name, report in [("parity_original", original), ("parity_console", ported)]:
        (LOG_DIR / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert original["errors"] == [] and ported["errors"] == [], (original["errors"], ported["errors"])
    assert ported["diagnostics"]["apiMapHiddenInDemo"] is True
    fp = ported["fingerprint"]
    differences = {key: (original["fingerprint"][key], fp[key]) for key in original["fingerprint"] if original["fingerprint"][key] != fp[key]}
    assert not differences, json.dumps(differences, ensure_ascii=False, indent=2)
    # Known demo values of the Artifact must survive the port.
    assert fp["initial"]["load"] == "55,606 MW" and fp["initial"]["max"] == "98%" and fp["initial"]["n1"] == "20건"
    assert fp["models"]["ieee39"]["load"] == "6,254 MW" and fp["models"]["ieee14"]["load"] == "259 MW" and fp["models"]["six"]["load"] == "750 MW"
    assert fp["bench"]["bCases"] == "365" and fp["bench"]["bMatch"] == "100%" and fp["bench"]["qbPass"] == "14/14"
    assert len(fp["chat"]) == 8 and all(item["added"] >= 2 and item["answer"] for item in fp["chat"])
    assert all(view["on"] and view["length"] > 500 for view in fp["views"].values()) and len(fp["views"]) == 9


def test_console_api_grid_maps_and_pf_preview(console):
    assert_passed(run_probe(console, "probe=api", "api"))


@pytest.mark.parametrize("scenario", ["fail500", "drop", "badjson"])
def test_console_catalog_failure_never_falls_back_to_demo(console, scenario):
    assert_passed(run_probe(console, f"probe={scenario}", f"failure_{scenario}"))


@pytest.mark.parametrize("scenario", ["pf500", "map500", "mapdrop", "geo404", "geobad", "pfgrid", "pfnonconv"])
def test_console_endpoint_failures_are_shown_not_replaced(console, scenario):
    assert_passed(run_probe(console, f"probe={scenario}", f"endpoint_{scenario}"))


def test_console_drops_stale_and_late_responses(console):
    assert_passed(run_probe(console, "probe=stale", "stale"))


def test_console_map_performance_in_real_time(console):
    # No virtual time: the probe measures real rendering, zoom and pan with performance.now().
    report = run_probe(console, "probe=perf", "perf", budget=None)
    assert_passed(report)
    assert report["timings"]["grids"]["kr-osm-transmission"]["nodes"] < 80


def test_console_catalog_timeout(console):
    assert_passed(run_probe(console, "probe=timeout", "timeout"))


def test_console_renders_without_google_fonts(console):
    assert_passed(run_probe(console, "probe=fonts", "fonts", extra=(BLOCK_FONTS,)))


def test_console_screenshots(console):
    base, chrome = console
    for name, query, extra in SCREENSHOTS:
        shot = SHOT_DIR / f"{name}.png"
        if shot.exists():
            shot.unlink()
        run_page(chrome, f"{base}/console/?{query}", f"shot-{name}", extra=extra, screenshot=shot, budget=60000)
        assert shot.is_file() and shot.stat().st_size > 10_000, name
