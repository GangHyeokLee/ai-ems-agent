"""Opt-in actual Chrome on a separate local bundle-based server."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

from browser.chrome_cdp import run_probe
from mongolia_bundle_server_probe import BUNDLES, ROOT, free_port
from test_browser_stale_responses import find_chrome

pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_BROWSER_TESTS") != "1",
                                reason="set AI_EMS_BROWSER_TESTS=1 for actual Chrome")


def test_geometry_flow_in_real_chrome(tmp_path, monkeypatch):
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome unavailable")
    asset = ROOT / "data/grids/mn-osm-35k-display-geometry.json"
    if not asset.is_file():
        pytest.skip("optional display geometry unavailable")
    monkeypatch.setenv("FONTCONFIG_FILE", str(ROOT / ".cache/korean-fonts/fonts.conf"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    name, approved_sha = BUNDLES["historical"]
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/src:{ROOT}", "PYTHONDONTWRITEBYTECODE": "1",
           "AI_EMS_MN_BUNDLE_DIR": str(ROOT / "outputs/mn_stage3b/bundles" / name),
           "AI_EMS_MN_BUNDLE_MANIFEST_SHA256": approved_sha}
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+120
            while True:
                try:
                    with urllib.request.urlopen(base+"/api/health", timeout=2):
                        break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        pytest.fail((tmp_path / "server.log").read_text())
                    time.sleep(0.1)
            report = run_probe(chrome, base+"/", ROOT / "tests/browser/mn_display_geometry_probe.js", tmp_path / "chrome", free_port)
            assert all(item["pass"] for item in report["results"]), json.dumps(report, ensure_ascii=False)
            assert len(report["results"]) >= 20
        finally:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
