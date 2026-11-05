"""PH4-02C-2c loopback fixture: the console AI run on the real FastAPI/registry/PyPowSyBl paths.

Only the LLM is scripted, with the B3-2 contract-test helpers (ScriptedModel, call, steps_for); every Tool
runs the real full KPG calculation and every receipt is issued by the real registry. The catalog
advertises reference support and, separately, run support (console_analysis_run_available) per case;
the production server advertises neither. Response corruption, receipt ageing and delayed delivery are
explicit test controls, never production behavior. The configured LLM provider is never contacted.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]
os.environ["AI_EMS_SINGLE_WORKER"] = "1"  # this test process only, as in the B3-2 contract tests

from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
import pypowsybl as pp  # noqa: E402
import uvicorn  # noqa: E402

import test_analysis_run_api as b32  # noqa: E402
import web_app  # noqa: E402
from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates  # noqa: E402
from ai_ems.network import load_identified_network  # noqa: E402

COUNTS = Counter()
CONTROL = {"hits": [], "report": None, "advertise": "run", "rule": None, "script": None}
DONE = asyncio.Event()
REAL_CLOCK = time.monotonic
OFFSET = 0
# Per case: what the catalog advertises. "run" also advertises reference; "reference" never advertises run.
ADVERTISE_BY_CASE = {"gating_reference": "reference", "gating_none": "none"}


def spy(module, name):
    original = getattr(module, name)

    def counted(*args, **kwargs):
        COUNTS[name] += 1
        return original(*args, **kwargs)

    setattr(module, name, counted)


for module, name in [(web_app, "run_line_contingency"), (web_app, "rank_generator_sensitivities"),
                     (pp.loadflow, "run_ac"), (pp.security, "create_analysis"), (pp.sensitivity, "create_ac_analysis")]:
    spy(module, name)


class BasicGraph:
    def invoke(self, state):
        COUNTS["legacy_graph"] += 1
        return {"messages": [*state["messages"], AIMessage(content="고정 기본 채팅 응답")]}


def build_steps(spec):
    """B3-2 steps for {operation, outage, monitored}, or explicit tool calls / text-only replies."""
    if "operation" in spec:
        return b32.steps_for(spec["operation"], spec["outage"], spec.get("monitored"))
    return [b32.call(step["tool"], step["args"], step.get("id", "call-1")) if "tool" in step else AIMessage(content=step["text"])
            for step in spec["steps"]]


def scripted_model(budget):
    """Replaces only the provider factory; one armed script per run, never the configured LLM."""
    COUNTS["run_model"] += 1
    spec, CONTROL["script"] = CONTROL["script"], None
    if spec is None:
        COUNTS["unscripted_run"] += 1
        raise AssertionError("no scripted run was armed")
    return b32.ScriptedModel(steps=build_steps(spec))


web_app.agent_graph = BasicGraph()
web_app.mongolia_agent_graph = BasicGraph()
web_app._analysis_run_model = scripted_model
app = web_app.app


@app.middleware("http")
async def boundary(request: Request, call_next):
    global OFFSET
    path = request.url.path
    if path == "/console/":
        case = request.query_params.get("run_probe", "")
        CONTROL.update(hits=[], report=None, advertise=ADVERTISE_BY_CASE.get(case, "run"), rule=None, script=None)
        COUNTS.clear()
        OFFSET = 0
        web_app.network = load_identified_network(b32.FULL)  # fresh: its first AC writes the state (first-AC policy)
        web_app.analysis_results = ResultRegistry(monotonic=lambda: REAL_CLOCK() + OFFSET)
        web_app.analysis_requests = RequestTracker()
        web_app.analysis_sessions = SessionGates()
        web_app.agent_sessions.clear()
        web_app.mongolia_agent_sessions.clear()
        DONE.clear()
        html = (ROOT / "ui/console/index.html").read_text()
        hook = """<script>
window.__runErrors=[];
addEventListener('error',e=>window.__runErrors.push(String(e.message)));
addEventListener('unhandledrejection',e=>window.__runErrors.push(String(e.reason)));
</script>"""
        html = html.replace('<script src="models.js">', hook + '<script src="models.js">', 1)
        html = html.replace('<body data-mode="pub">', '<body data-mode="pub"><img src="/__run_probe__/hold" alt="" hidden>', 1)
        html = html.replace('</body>', '<script src="/__run_probe__/probe.js"></script></body>', 1)
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
    response = await call_next(request)
    if path == "/api/grids" and CONTROL["advertise"] != "none":
        payload = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
        # Deliberately advertise every grid: the console must still keep MN and Korea out of v1.
        for grid in payload["grids"]:
            grid["console_analysis_context_versions"] = [1]
            if CONTROL["advertise"] == "run":
                grid["console_analysis_run_available"] = True
        return JSONResponse(payload)
    if hit is not None:
        payload = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
        hit["response"] = json.loads(json.dumps(payload))  # what the server sent, before any test corruption
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


@app.get("/__run_probe__/probe.js")
def probe_script():
    return Response((ROOT / "tests/browser/console_run_probe.js").read_text(encoding="utf-8"), media_type="text/javascript")


@app.get("/__run_probe__/hold")
async def hold():
    try:
        await asyncio.wait_for(DONE.wait(), timeout=180)
    except asyncio.TimeoutError:
        pass
    return Response(bytes.fromhex("474946383961010001008000000000ffffff21f90401000000002c00000000010001000002024401003b"),
                    media_type="image/gif")


@app.get("/__run_probe__/state")
def snapshot():
    return {**CONTROL, "counts": dict(COUNTS)}


@app.post("/__run_probe__/control")
async def control(request: Request):
    global OFFSET
    body = await request.json()
    for key in ("rule", "script", "report"):
        if key in body:
            CONTROL[key] = body[key]
    if body.get("expire"):
        OFFSET += 601
    if "report" in body:
        DONE.set()
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), workers=1, log_level="warning")
