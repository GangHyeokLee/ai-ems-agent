"""Development benchmark: Korea map wheel/drag smoothness in real Chrome (CDP trusted input + trace).

Not collected by pytest. Sends real-time wheel and pointer input, records animation-frame
intervals, input-to-frame latency and Long Tasks in the page, and aggregates the Chrome trace
(renderer main thread) by phase. ``--variant`` disables one map layer or CSS rule for A/B checks.

    AI_EMS_CHROME=/path/to/chrome .venv/bin/python tests/browser/korea_map_performance.py \\
        --output outputs/kr_map_performance/before.json --variant baseline --variant no_country
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_browser_stale_responses import find_chrome, free_port  # noqa: E402

MARKERS = ".network-bus, .network-transformer-hit, .network-transformer-marker, .powerplant-marker, .analysis-marker"
VARIANTS = {  # CSS-only switches; application code and data are unchanged
    "baseline": "",
    "no_country": ".country-outline { display:none !important; }",
    "no_admin": ".admin-outline { display:none !important; }",
    "no_buses": ".network-bus { display:none !important; }",
    "no_lines": ".network-line { display:none !important; }",
    "no_hit_paths": ".network-line-hit { display:none !important; }",
    "no_marker_zoom_scale": f"#network-map.map-zoomed :is({MARKERS}) {{ transform:none !important; }}",
    "no_non_scaling_stroke": "#network-map.map-zoomed * { vector-effect:none !important; }",
    "network_only": ".country-outline, .admin-outline { display:none !important; }",
    "overflow_visible": "#network-map { overflow:visible !important; }",
}
PAGE_JS = r"""
window.__perf = {
  start() {
    const s = this.s = {frames: [], latencies: [], longTasks: [], pending: [], events: 0, running: true};
    const loop = t => { s.frames.push(t); for (const e of s.pending) s.latencies.push(t - e); s.pending = [];
                        if (s.running) requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
    const svg = document.getElementById("network-map");
    s.onInput = e => { s.events++; s.pending.push(e.timeStamp); };
    svg.addEventListener("wheel", s.onInput, {capture: true, passive: true});
    svg.addEventListener("pointermove", s.onInput, {capture: true, passive: true});
    s.observer = new PerformanceObserver(list => { for (const e of list.getEntries()) s.longTasks.push(e.duration); });
    s.observer.observe({type: "longtask"});
  },
  stop() {
    const s = this.s; s.running = false; s.observer.disconnect();
    const svg = document.getElementById("network-map");
    svg.removeEventListener("wheel", s.onInput, {capture: true});
    svg.removeEventListener("pointermove", s.onInput, {capture: true});
    const intervals = s.frames.slice(1).map((t, i) => t - s.frames[i]);
    return {intervals, latencies: s.latencies, longTasks: s.longTasks, events: s.events, frames: s.frames.length,
            viewBox: svg.getAttribute("viewBox")};
  },
};
"""
PHASES = {"UpdateLayoutTree": "style", "Layout": "layout", "PrePaint": "prepaint", "Paint": "paint",
          "PaintImage": "paint", "Commit": "commit", "EventDispatch": "event", "FunctionCall": "script",
          "TimerFire": "script", "FireAnimationFrame": "script", "HitTest": "hit_test",
          "UpdateLayer": "layerize", "Layerize": "layerize", "RasterTask": "raster"}


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))], 1)


def summarize(raw, trace):
    intervals = raw["intervals"]
    duration = sum(intervals) or 1
    return dict(
        input_events=raw["events"], frames=raw["frames"],
        fps=round(1000 * len(intervals) / duration, 1),
        frame_interval_ms=dict(p50=pct(intervals, .5), p95=pct(intervals, .95), max=pct(intervals, 1)),
        frames_over_33ms=sum(i > 33.4 for i in intervals), frames_over_50ms=sum(i > 50 for i in intervals),
        input_to_frame_ms=dict(p50=pct(raw["latencies"], .5), p95=pct(raw["latencies"], .95)),
        long_tasks=dict(count=len(raw["longTasks"]), total_ms=round(sum(raw["longTasks"]), 1),
                        max_ms=round(max(raw["longTasks"], default=0), 1)),
        main_thread_ms=trace)


def aggregate_trace(events):
    """Self-inclusive totals per phase on the renderer main thread (CrRendererMain)."""
    main = {(e["pid"], e["tid"]) for e in events if e.get("name") == "thread_name"
            and e.get("args", {}).get("name") == "CrRendererMain"}
    raster = {(e["pid"], e["tid"]) for e in events if e.get("name") == "thread_name"
              and "Raster" in e.get("args", {}).get("name", "")}
    totals = defaultdict(float)
    for e in events:
        phase = PHASES.get(e.get("name"))
        if not phase or e.get("ph") != "X":
            continue
        key = (e["pid"], e["tid"])
        if phase == "raster" and key in raster or phase != "raster" and key in main:
            totals[phase] += e.get("dur", 0) / 1000
    return {k: round(v, 1) for k, v in sorted(totals.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", type=Path, default=ROOT / "data/grids/kr-osm-transmission-map.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", action="append", choices=sorted(VARIANTS))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--scale", type=float, default=1.0, help="device scale factor")
    parser.add_argument("--trace", action="store_true", help="also record a Chrome trace per scenario")
    args = parser.parse_args()
    variants = args.variant or ["baseline"]
    chrome = find_chrome()
    if chrome is None or chrome[1]:
        raise SystemExit("Linux Chrome가 필요합니다 (AI_EMS_CHROME).")
    import websocket
    app_port, cdp_port = free_port(), free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1",
                               "--port", str(app_port), "--log-level", "warning"], cwd=ROOT,
                              env={**os.environ, "AI_EMS_KOREA_MAP_FILE": str(args.map.resolve())},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = subprocess.Popen([str(chrome[0]), "--headless=new", f"--user-data-dir=/tmp/ai-ems-kr-perf-{cdp_port}",
                                "--no-first-run", "--window-size=1600,1200", f"--force-device-scale-factor={args.scale}",
                                f"--remote-debugging-port={cdp_port}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    connection = None
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{app_port}/api/grids", timeout=5).read()
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2))
                target = next(t for t in targets if t["type"] == "page")
                connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=600,
                                                         suppress_origin=True)
                break
            except (OSError, StopIteration):
                if time.monotonic() > deadline or server.poll() is not None:
                    raise RuntimeError("app or Chrome did not start")
                time.sleep(0.2)
        counter, trace_events = 0, []

        def call(method, params=None):
            nonlocal counter
            counter += 1
            connection.send(json.dumps(dict(id=counter, method=method, params=params or {})))
            while True:
                message = json.loads(connection.recv())
                if message.get("method") == "Tracing.dataCollected":
                    trace_events.extend(message["params"]["value"])
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

        def paced(events, interval):
            """Dispatch input at a fixed wall-clock rate, like a real mouse, without waiting for frames."""
            start = time.perf_counter()
            for i, event in enumerate(events):
                delay = start + i * interval - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                call("Input.dispatchMouseEvent", event)

        def settle():
            evaluate("new Promise(r => setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(r)), 300))", True)

        def measure(name, action):
            if args.trace:
                trace_events.clear()
                call("Tracing.start", dict(categories="devtools.timeline,disabled-by-default-devtools.timeline",
                                           transferMode="ReportEvents"))
            evaluate("__perf.start()")
            action()
            settle()
            raw = evaluate("__perf.stop()")
            trace = {}
            if args.trace:
                connection.send(json.dumps(dict(id=-1, method="Tracing.end")))
                while True:
                    message = json.loads(connection.recv())
                    if message.get("method") == "Tracing.dataCollected":
                        trace_events.extend(message["params"]["value"])
                    if message.get("method") == "Tracing.tracingComplete":
                        break
                trace = aggregate_trace(trace_events)
            return summarize(raw, trace)

        call("Page.navigate", dict(url=f"http://127.0.0.1:{app_port}/"))
        until("document.readyState === 'complete' && typeof gridEntries !== 'undefined' && !!currentMapData")
        evaluate("setThemePreference('light')")
        results = dict(chrome=call("Browser.getVersion")["product"], viewport="1600x1200 window",
                       device_scale_factor=args.scale, headless=True, platform="Linux (WSL2) headless, software raster",
                       repeat=args.repeat, variants={})
        for variant in variants:
            runs = defaultdict(list)
            for _ in range(args.repeat):
                call("Page.reload")
                until("document.readyState === 'complete' && typeof gridEntries !== 'undefined' && !!currentMapData")
                start = time.perf_counter()
                evaluate("(() => { const s = document.getElementById('grid-select'); s.value = 'kr-osm-transmission';"
                         " s.dispatchEvent(new Event('change')); })()")
                until("selectedGridId === 'kr-osm-transmission' && currentMapData && "
                      "document.querySelectorAll('.network-line').length === currentMapData.lines.length")
                evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", True)
                runs["initial_render_ms"].append(round((time.perf_counter() - start) * 1000, 1))
                evaluate(PAGE_JS)
                style = json.dumps(VARIANTS[variant])
                evaluate(f"(() => {{ const s = document.createElement('style'); s.textContent = {style};"
                         " document.head.appendChild(s); })()")
                settle()
                rect = evaluate("(() => { const r = document.getElementById('network-map').getBoundingClientRect();"
                                " return {x: r.x + r.width / 2, y: r.y + r.height * 0.45}; })()")
                x, y = rect["x"], rect["y"]
                wheel = lambda dy: dict(type="mouseWheel", x=x, y=y, deltaX=0, deltaY=dy)  # noqa: E731
                # Continuous zoom in then out: 40 notches each at 60 Hz (fast trackpad / wheel spin).
                runs["zoom"].append(measure("zoom", lambda: paced([wheel(-60)] * 40 + [wheel(60)] * 25, 1 / 60)))
                # Drag at high zoom: pointer moves at 120 Hz for ~1 s.
                moves = [dict(type="mouseMoved", x=x + 4 * i, y=y + 2 * i, button="left", buttons=1) for i in range(1, 121)]
                def drag():
                    call("Input.dispatchMouseEvent", dict(type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1))
                    paced(moves, 1 / 120)
                    call("Input.dispatchMouseEvent", dict(type="mouseReleased", x=x + 480, y=y + 240, button="left",
                                                          buttons=0, clickCount=1))
                runs["drag"].append(measure("drag", drag))
                filter_ms = evaluate("""(async () => { const input = document.querySelector('#voltage-filters input[data-voltage="154"]');
                    const t = performance.now(); input.checked = false; input.dispatchEvent(new Event('change'));
                    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                    const d = performance.now() - t; input.checked = true; input.dispatchEvent(new Event('change')); return d; })()""", True)
                runs["voltage_filter_ms"].append(round(filter_ms, 1))
            results["variants"][variant] = dict(runs)
            zoom, drag = runs["zoom"], runs["drag"]
            print(json.dumps(dict(variant=variant, initial=statistics.median(runs["initial_render_ms"]),
                                  zoom_fps=[r["fps"] for r in zoom], zoom_p95=[r["frame_interval_ms"]["p95"] for r in zoom],
                                  drag_fps=[r["fps"] for r in drag], drag_p95=[r["frame_interval_ms"]["p95"] for r in drag],
                                  drag_lat_p50=[r["input_to_frame_ms"]["p50"] for r in drag],
                                  main=zoom[-1]["main_thread_ms"])), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if connection:
            connection.close()
        browser.terminate()
        server.terminate()
        browser.wait(timeout=30)
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
