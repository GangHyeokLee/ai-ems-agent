"""C-1 loopback fixture: real HTTP/registry/manual physics; only catalog opt-in is advertised.

LLM/Tool entry points are forbidden for references. Explicit transport corruption,
clock expiry and model changes are test controls, never production behavior.
Initial KPG map/PF reads are delivered to the real routes sequentially to isolate
the pre-existing 15f634c non-blocking read contention. The separate, unchanged
Phase 3 regression runs without this scheduling and records that failure.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from langchain_core.messages import AIMessage
import pypowsybl as pp
import uvicorn

import web_app
from ai_ems.analysis_registry import ResultRegistry, RequestTracker, SessionGates
from ai_ems.network import load_identified_network
from ai_ems.network_access import network_access

COUNTS = Counter()
CONTROL = {"hits": [], "reads": [], "report": None, "advertise": True, "rule": None}
DONE = asyncio.Event()
READ_GATE = asyncio.Lock()
REAL_CLOCK = time.monotonic
OFFSET = 0
FULL = ROOT / "data/KPG193_ver2_0_powsybl_full.mat"


def spy(module, name):
    original = getattr(module, name)

    def counted(*args, **kwargs):
        COUNTS[name] += 1
        return original(*args, **kwargs)

    setattr(module, name, counted)


for module, name in [(web_app, "run_line_contingency"), (web_app, "rank_generator_sensitivities"),
                     (web_app, "select_most_severe_violated_line"), (pp.loadflow, "run_ac"),
                     (pp.security, "create_analysis"), (pp.sensitivity, "create_ac_analysis")]:
    spy(module, name)


class BasicGraph:
    def invoke(self, state):
        COUNTS["legacy_graph"] += 1
        return {"messages": [*state["messages"], AIMessage(content="고정 기본 채팅 응답")]}


def forbidden_model(*args, **kwargs):
    COUNTS["forbidden_llm"] += 1
    raise AssertionError("C-1 must not create or call a run model")


web_app.agent_graph = BasicGraph()
web_app.mongolia_agent_graph = BasicGraph()
web_app._analysis_run_model = forbidden_model
app = web_app.app


@app.middleware("http")
async def boundary(request: Request, call_next):
    global OFFSET
    path = request.url.path
    if path == "/console/":
        CONTROL.update(hits=[], reads=[], report=None, advertise=request.query_params.get("reference_probe") != "gating", rule=None)
        COUNTS.clear()
        OFFSET = 0
        web_app.network = load_identified_network(FULL)
        web_app.analysis_results = ResultRegistry(monotonic=lambda: REAL_CLOCK() + OFFSET)
        web_app.analysis_requests = RequestTracker()
        web_app.analysis_sessions = SessionGates()
        web_app.agent_sessions.clear()
        web_app.mongolia_agent_sessions.clear()
        DONE.clear()
        html = (ROOT / "ui/console/index.html").read_text()
        hook = """<script>
window.__referenceErrors=[];
addEventListener('error',e=>window.__referenceErrors.push(String(e.message)));
addEventListener('unhandledrejection',e=>window.__referenceErrors.push(String(e.reason)));
</script>"""
        html = html.replace('<script src="models.js">', hook + '<script src="models.js">', 1)
        html = html.replace('<body data-mode="pub">', '<body data-mode="pub"><img src="/__reference_probe__/hold" alt="" hidden>', 1)
        html = html.replace('</body>', '<script src="/__reference_probe__/probe.js"></script></body>', 1)
        return HTMLResponse(html)
    recorded = path.startswith("/api/") and (path.endswith("/chat") or "security-analysis" in path or "sensitivity-analysis" in path)
    hit = None
    rule = None
    if recorded:
        hit = {"path": path, "query": str(request.url.query), "headers": {k: v for k, v in request.headers.items() if k.startswith("x-ems-")},
               "body": await request.json() if request.method == "POST" else None}
        CONTROL["hits"].append(hit)
        rule = CONTROL["rule"]
        CONTROL["rule"] = None
    if path in ("/api/grids/kr-kpg193/map", "/api/grids/kr-kpg193/pf-preview"):
        # Only order the prerequisite reads. No response, engine lock, manual
        # analysis, receipt/reference request or cancellation behavior is mocked.
        async with asyncio.timeout(30):
            async with READ_GATE:
                response = await call_next(request)
        CONTROL["reads"].append({"path": path, "status": response.status_code})
    else:
        response = await call_next(request)
    if path == "/api/grids" and CONTROL["advertise"]:
        payload = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
        # Deliberately advertise all grids: the console must still gate MN/Korea.
        for grid in payload["grids"]:
            grid["console_analysis_context_versions"] = [1]
        return JSONResponse(payload)
    if hit is not None:
        payload = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
        hit["response"] = payload
        hit["status"] = response.status_code
        if rule:
            if "replace" in rule:
                payload = rule["replace"]
            for keys, value in rule.get("patches", []):
                target = payload
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
        return JSONResponse(payload, status_code=rule.get("status", response.status_code) if rule else response.status_code,
                            headers={"Cache-Control": "no-store"})
    return response


@app.get("/__reference_probe__/probe.js")
def probe_script():
    return Response((ROOT / "tests/browser/console_reference_probe.js").read_text(), media_type="text/javascript")


@app.get("/__reference_probe__/hold")
async def hold():
    try:
        await asyncio.wait_for(DONE.wait(), timeout=180)
    except asyncio.TimeoutError:
        pass
    return Response(bytes.fromhex("474946383961010001008000000000ffffff21f90401000000002c00000000010001000002024401003b"), media_type="image/gif")


@app.get("/__reference_probe__/state")
def snapshot():
    return {**CONTROL, "counts": dict(COUNTS)}


@app.post("/__reference_probe__/control")
async def control(request: Request):
    global OFFSET
    body = await request.json()
    for key in ("rule", "advertise", "report"):
        if key in body:
            CONTROL[key] = body[key]
    if body.get("expire"):
        OFFSET += 601
    if body.get("short_ttl"):
        web_app.analysis_results.ttl_seconds = 1
    if "ttl" in body:  # receipts registered afterwards expire on the real clocks after this many seconds
        web_app.analysis_results.ttl_seconds = float(body["ttl"])
    if body.get("change_model"):
        with network_access(web_app.network):
            loads = web_app.network.get_loads()
            identifier = str(loads.index[0])
            web_app.network.update_loads(id=identifier, p0=float(loads.iloc[0].p0) + 1)
    if "report" in body:
        DONE.set()
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), workers=1, log_level="warning")
