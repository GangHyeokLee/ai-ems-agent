"""Development capture: Korea map layers separated in real Chrome, at fixed geographic frames.

Starts the web app with the given Korea map file, switches to ``kr-osm-transmission`` and saves
one screenshot per (frame, layer) so boundary, administrative and network layers can be checked
independently and compared before/after with the same lon/lat window and image size.

    AI_EMS_CHROME=/path/to/chrome .venv/bin/python tests/browser/capture_korea_map_layers.py \\
        --map data/grids/kr-osm-transmission-map.json --output outputs/kr_coastline_fix/after \\
        --reference outputs/kr_coastline_fix/reference/natural_earth_10m_land_kr.json
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_browser_stale_responses import find_chrome, free_port  # noqa: E402

# lon/lat windows shared by every capture (west, south, east, north)
FRAMES = {
    "national": (124.0, 32.7, 132.3, 38.9),
    "jeju": (126.05, 33.10, 127.05, 33.60),
    "ulleung_dokdo": (130.70, 37.10, 132.00, 37.65),
    "dokdo": (131.855, 37.234, 131.878, 37.248),
    "southwest_coast": (125.90, 34.30, 126.80, 35.10),
    "west_coast_incheon": (126.10, 36.80, 127.00, 37.80),
}
LAYERS = {  # CSS applied to the map only; the app's own data and DOM are unchanged
    "all": ".probe-reference { display:none !important; }",
    "country_only": "#network-map > :not(.country-outline) { display:none !important; }",
    "admin_only": "#network-map > :not(.admin-outline) { display:none !important; }",
    "network_only": "#network-map > :is(.country-outline, .admin-outline, .probe-reference) { display:none !important; }",
    "country_vs_reference": "#network-map > :not(.country-outline, .probe-reference) { display:none !important; }",
}
SETUP_JS = r"""
window.__probe = {
  frame(w, s, e, n) {
    const svg = document.getElementById("network-map"), base = svg.dataset.baseViewBox.split(" ").map(Number);
    const a = mapProjection({longitude: w, latitude: n}), b = mapProjection({longitude: e, latitude: s});
    let width = b.x - a.x, height = b.y - a.y;
    const aspect = base[2] / base[3];
    if (width / height < aspect) width = height * aspect; else height = width / aspect;
    const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2, zoom = base[2] / width;
    svg.setAttribute("viewBox", `${cx - width / 2} ${cy - height / 2} ${width} ${height}`);
    svg.classList.add("map-zoomed"); svg.style.setProperty("--map-zoom", String(zoom));
    return zoom;
  },
  layer(css) {
    let style = document.getElementById("probe-style");
    if (!style) { style = document.createElement("style"); style.id = "probe-style"; document.head.appendChild(style); }
    style.textContent = css;
  },
  reference(polygons) {
    const svg = document.getElementById("network-map");
    const d = polygons.map(poly => poly.map(ring => ring.map(([lon, lat], i) => {
      const p = mapProjection({longitude: lon, latitude: lat});
      return (i ? "L" : "M") + p.x.toFixed(2) + " " + p.y.toFixed(2); }).join(" ") + " Z").join(" ")).join(" ");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d); path.setAttribute("class", "probe-reference");
    path.setAttribute("style", "fill:none;stroke:#db2777;stroke-width:1;vector-effect:non-scaling-stroke;pointer-events:none");
    svg.appendChild(path);
  },
};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="JSON {polygons: [...]} drawn as a magenta outline")
    args = parser.parse_args()
    chrome = find_chrome()
    if chrome is None or chrome[1]:
        raise SystemExit("Linux Chrome가 필요합니다 (AI_EMS_CHROME).")
    import websocket
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app_port, cdp_port = free_port(), free_port()
    env = {**os.environ, "AI_EMS_KOREA_MAP_FILE": str(args.map.resolve())}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1",
                               "--port", str(app_port), "--log-level", "warning"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    font_conf = ROOT / ".cache/korean-fonts/fonts.conf"
    chrome_env = {**os.environ, **({"FONTCONFIG_FILE": str(font_conf)} if font_conf.is_file() else {})}
    browser = subprocess.Popen([str(chrome[0]), "--headless=new", f"--user-data-dir=/tmp/ai-ems-kr-layers-{cdp_port}",
                                "--no-first-run", "--window-size=1600,1200", "--hide-scrollbars",
                                f"--remote-debugging-port={cdp_port}", "about:blank"],
                               env=chrome_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    connection = None
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{app_port}/api/grids", timeout=5).read()
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2))
                target = next(t for t in targets if t["type"] == "page")
                connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=300,
                                                         suppress_origin=True)
                break
            except (OSError, StopIteration):
                if time.monotonic() > deadline or server.poll() is not None:
                    raise RuntimeError("app or Chrome did not start")
                time.sleep(0.2)
        counter = 0

        def call(method, params=None):
            nonlocal counter
            counter += 1
            connection.send(json.dumps(dict(id=counter, method=method, params=params or {})))
            while True:
                message = json.loads(connection.recv())
                if message.get("id") == counter:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})

        def evaluate(expression, wait=False):
            reply = call("Runtime.evaluate", dict(expression=expression, returnByValue=True, awaitPromise=wait))
            if "exceptionDetails" in reply:
                raise RuntimeError(reply["exceptionDetails"])
            return reply.get("result", {}).get("value")

        def until(expression, seconds=120):
            end = time.monotonic() + seconds
            while not evaluate(expression):
                if time.monotonic() > end:
                    raise TimeoutError(expression)
                time.sleep(0.05)

        paint = "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        call("Page.navigate", dict(url=f"http://127.0.0.1:{app_port}/"))
        until("document.readyState === 'complete' && typeof gridEntries !== 'undefined' && !!currentMapData")
        evaluate("setThemePreference('light')")
        start = time.perf_counter()
        evaluate("(() => { const s = document.getElementById('grid-select'); s.value = 'kr-osm-transmission';"
                 " s.dispatchEvent(new Event('change')); })()")
        until("selectedGridId === 'kr-osm-transmission' && currentMapData && "
              "document.querySelectorAll('.network-line').length === currentMapData.lines.length")
        evaluate(paint, wait=True)
        summary = dict(map_file=str(args.map), switch_and_render_ms=round((time.perf_counter() - start) * 1000, 1),
                       counts=evaluate("({buses: document.querySelectorAll('.network-bus').length,"
                                       " lines: document.querySelectorAll('.network-line').length,"
                                       " plants: currentMapData.powerplants.length,"
                                       " country_paths: document.querySelectorAll('.country-outline').length,"
                                       " admin_paths: document.querySelectorAll('.admin-outline').length,"
                                       " base_view_box: document.getElementById('network-map').dataset.baseViewBox})"),
                       frames=FRAMES, shots=[])
        evaluate(SETUP_JS)
        if args.reference:
            reference = json.loads(args.reference.read_text(encoding="utf-8"))
            evaluate(f"__probe.reference({json.dumps(reference['polygons'])})")
        for frame, bounds in FRAMES.items():
            zoom = evaluate("__probe.frame(%s)" % ",".join(map(str, bounds)))
            for layer, css in LAYERS.items():
                if layer == "country_vs_reference" and not args.reference:
                    continue
                evaluate(f"__probe.layer({json.dumps(css)})")
                evaluate(paint, wait=True)
                rect = evaluate("(() => { const r = document.getElementById('network-map').getBoundingClientRect();"
                                " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()")
                data = call("Page.captureScreenshot", dict(format="png", clip={**rect, "scale": 1}))["data"]
                name = f"{frame}__{layer}.png"
                (output / name).write_bytes(base64.b64decode(data))
                summary["shots"].append(dict(file=name, frame=frame, layer=layer, zoom=round(zoom, 3)))
        (output / "capture.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k != "shots"}, ensure_ascii=False))
    finally:
        if connection:
            connection.close()
        browser.terminate()
        server.terminate()
        browser.wait(timeout=30)
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
