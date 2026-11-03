"""PH4-01 browser-only app: real routes/sessions, deterministic graph responses.

Never launch this with the production runner. Only the two Agent graphs are replaced;
map/PF/security/sensitivity routes keep their real implementations. Test control routes
are bound to loopback by the launcher in test_console_chat_browser.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from langchain_core.messages import AIMessage, HumanMessage
import uvicorn

import web_app


class ScriptedGraph:
    def __init__(self, grid_id):
        self.grid_id = grid_id

    def invoke(self, state):
        humans = [message.content for message in state["messages"] if isinstance(message, HumanMessage)]
        if humans[-1] == "fixture-error":
            raise ConnectionError("deterministic fixture LLM unavailable")
        answer = f"{self.grid_id} · turn={len(humans)} · history=" + " | ".join(humans)
        return {"messages": [*state["messages"], AIMessage(content=answer)]}


web_app.agent_graph = ScriptedGraph("kr-kpg193")
web_app.mongolia_agent_graph = ScriptedGraph("mn-osm-35k")
app = web_app.app
CONTROL = {"hits": [], "api_hits": [], "report": None, "disabled": None, "rule": None}
LOAD_DONE = asyncio.Event()
RELEASE = asyncio.Event()
CHAT_PATHS = {f"/api/grids/{grid}/chat" for grid in ("kr-kpg193", "mn-osm-35k", "kr-osm-transmission")}
GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


@app.middleware("http")
async def fixture_boundary(request: Request, call_next):
    path = request.url.path
    if path == "/console/":
        CONTROL.update(hits=[], api_hits=[], report=None, disabled=None, rule=None)
        LOAD_DONE.clear()
        RELEASE.clear()
        web_app.agent_sessions.clear()
        web_app.mongolia_agent_sessions.clear()
        html = (ROOT / "ui/console/index.html").read_text(encoding="utf-8")
        hook = """<script>
window.__chatErrors=[];
addEventListener('error', e => window.__chatErrors.push(String(e.message)));
addEventListener('unhandledrejection', e => window.__chatErrors.push(String(e.reason)));
if (new URLSearchParams(location.search).get('chat_probe') === 'timeout') {
  const timer=window.setTimeout;
  window.setTimeout=(fn, ms, ...args) => timer(fn, ms === 60000 ? 100 : ms, ...args);
  window.__chatRestoreTimeout=() => { window.setTimeout=timer; };
}
</script>"""
        html = html.replace('<script src="models.js">', hook + '<script src="models.js">', 1)
        html = html.replace('<body data-mode="pub">', '<body data-mode="pub"><img src="/__chat_probe__/hold-load" alt="" hidden>', 1)
        html = html.replace("</body>", '<script src="/__chat_probe__/probe.js"></script></body>', 1)
        return HTMLResponse(html)
    if path.startswith("/api/"):
        CONTROL["api_hits"].append(path)
    if path in CHAT_PATHS and request.method == "POST":
        # A rule belongs to the next arriving request, not whichever response finishes first.
        rule = CONTROL["rule"]
        CONTROL["rule"] = None
        body = await request.json()
        CONTROL["hits"].append({"path": path, "body": body})
        # The model is always the scripted graph, even for responses held below.
        response = await call_next(request)
        if rule and rule.get("hold"):
            await asyncio.wait_for(RELEASE.wait(), timeout=30)
        if rule and rule.get("mode"):
            mode = rule["mode"]
            data = b"".join([chunk async for chunk in response.body_iterator])
            result = json.loads(data)
            if mode == "wrong-grid":
                result["grid_id"] = "kr-osm-transmission"
            elif mode == "wrong-session":
                result["session_id"] = "another-session"
            elif mode == "empty":
                result["answer"] = "  "
            elif mode == "ui-updates":
                # The probe supplies a real, different N-1 result in the production update schema.
                result["ui_updates"] = [{"type": "security", "result": rule["result"]}]
            elif mode == "bad-json":
                return Response("<not-json>", media_type="text/plain")
            return JSONResponse(result)
        return response
    response = await call_next(request)
    if path == "/api/grids" and CONTROL["disabled"]:
        data = b"".join([chunk async for chunk in response.body_iterator])
        catalog = json.loads(data)
        for grid in catalog["grids"]:
            if grid["id"] == CONTROL["disabled"]:
                grid["chat_available"] = False
        return JSONResponse(catalog)
    return response


@app.get("/__chat_probe__/probe.js")
def probe_script():
    return Response((ROOT / "tests/browser/console_chat_probe.js").read_text(encoding="utf-8"), media_type="text/javascript")


@app.get("/__chat_probe__/hold-load")
async def hold_load():
    try:
        await asyncio.wait_for(LOAD_DONE.wait(), timeout=140)
    except asyncio.TimeoutError:
        pass
    return Response(GIF, media_type="image/gif")


@app.get("/__chat_probe__/state")
def fixture_state():
    return {"hits": CONTROL["hits"], "api_hits": CONTROL["api_hits"], "report": CONTROL["report"]}


@app.post("/__chat_probe__/control")
async def control(request: Request):
    body = await request.json()
    if "disabled" in body:
        CONTROL["disabled"] = body["disabled"]
    if "rule" in body:
        CONTROL["rule"] = body["rule"]
        RELEASE.clear()
    if body.get("release"):
        RELEASE.set()
    if "report" in body:
        CONTROL["report"] = body["report"]
        LOAD_DONE.set()
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
