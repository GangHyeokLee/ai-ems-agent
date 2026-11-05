"""PH4-02C-2b loopback fixture: real B3-2 run envelopes for the pure console validators.

Every response is produced at start-up by the real web_app paths on the full KPG model: v1 runs through
TestClient -> /api/grids/kr-kpg193/chat (only the LLM is scripted, with the B3-2 contract-test helpers and
cases) and manual opt-in N-1/Sensitivity requests through the real REST routes. The page then loads the
real ui/console/api-analysis.js and validates these responses and explicit corruptions of them. Nothing
is rendered or applied, and the configured LLM provider is never contacted.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]
os.environ["AI_EMS_SINGLE_WORKER"] = "1"  # this test process only, as in the B3-2 contract tests

from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import uvicorn  # noqa: E402

import test_analysis_run_api as b32  # noqa: E402  (B3-2 cases, ScriptedModel and request helpers)
import web_app  # noqa: E402
from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates  # noqa: E402
from ai_ems.network import load_identified_network  # noqa: E402

KPG = "kr-kpg193"
STATE = {"cases": [], "manual": [], "report": None, "provider_calls": 0, "legacy_calls": 0}
DONE = asyncio.Event()


class LegacyForbidden:
    def invoke(self, state):
        STATE["legacy_calls"] += 1
        raise AssertionError("the legacy Agent graph must not run for these fixtures")


def provider_forbidden(budget):
    STATE["provider_calls"] += 1
    raise AssertionError("the configured LLM provider must never be contacted")


def generate() -> None:
    web_app.agent_graph = web_app.mongolia_agent_graph = LegacyForbidden()
    web_app.agent_sessions.clear()
    web_app.analysis_results, web_app.analysis_requests, web_app.analysis_sessions = (
        ResultRegistry(), RequestTracker(), SessionGates())
    web_app._analysis_run_model = provider_forbidden
    client = TestClient(web_app.app)
    prepared = load_identified_network(b32.FULL)
    b32.domain_security(prepared, b32.OUTAGE)  # the B3-2 `kpg` fixture: no first-AC write during the cases

    def run(name, operation, outage, monitored, steps, status, reason, kinds, network=prepared):
        web_app.network = network
        scripted = b32.ScriptedModel(steps=list(steps))
        web_app._analysis_run_model = lambda budget: scripted
        body = b32.run_body(operation, outage, monitored)
        response = client.post(f"/api/grids/{KPG}/chat", json=body)
        web_app._analysis_run_model = provider_forbidden
        assert response.status_code == 200, (name, response.text)
        envelope = response.json()
        assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == (status, reason), (name, envelope["analysis"])
        STATE["cases"].append({
            "name": name,
            "request": {"gridId": KPG, "sessionId": body["session_id"], "requestId": body["request_id"],
                        "context": body["analysis_context"]},
            "response": envelope,
            "expect": {"status": status, "reasonCode": reason, "kinds": kinds},
        })

    for operation, outage, monitored, status, reason, kinds in b32.RUN_CASES:
        run(f"{operation}:{outage}:{monitored or 'auto'}", operation, outage, monitored,
            b32.steps_for(operation, outage, monitored), status, reason, kinds)
    extra = [  # real chain/explicit combinations beyond the seven B3-2 REST-parity cases
        ("security_then_sensitivity", b32.NO_OVERLOAD, None, "not_evaluated", "NO_OVERLOAD", ["security", "sensitivity"]),
        ("security_then_sensitivity", b32.OUTAGE, b32.MONITORED, "completed", None, ["security", "sensitivity"]),
        ("sensitivity", b32.NONCONVERGED, b32.MONITORED, "completed", None, ["sensitivity"]),
    ]
    for operation, outage, monitored, status, reason, kinds in extra:
        run(f"{operation}:{outage}:{monitored or 'auto'}", operation, outage, monitored,
            b32.steps_for(operation, outage, monitored), status, reason, kinds)
    for name, operation, outage, steps, status, reason, _ in b32.FAILURES:
        run(f"failure:{name}", operation, outage, None, steps, status, reason, [])
    # A freshly loaded model: the first AC writes the state, so the run is refused (B3-2 first-AC policy).
    run("failure:first_ac", "security", b32.OUTAGE, None, [b32.call(b32.LINE, {"outage_line_id": b32.OUTAGE})],
        "failed", "MODEL_REVISION_CHANGED", [], network=load_identified_network(b32.FULL))

    # Manual opt-in results of the same model (C-1 receipts), to keep the shared receipt checks honest.
    web_app.network = prepared
    for kind, outage, monitored in [("security", b32.MULTI, None), ("security", b32.NONCONVERGED, None),
                                    ("sensitivity", b32.OUTAGE, None), ("sensitivity", b32.OUTAGE, b32.MONITORED),
                                    ("sensitivity", b32.NO_OVERLOAD, None)]:
        request_id = str(uuid.uuid4())
        params = {"outage_line_id": outage, **({"top_n": 5} if kind == "sensitivity" else {}),
                  **({"monitored_line_id": monitored} if monitored else {})}
        response = client.get(f"/api/{kind}-analysis", params=params, headers={
            "X-EMS-Analysis-Context-Version": "1", "X-EMS-Session-ID": b32.SESSION, "X-EMS-Request-ID": request_id})
        assert response.status_code == 200 and "analysis_receipt" in response.json(), (kind, outage, response.text)
        STATE["manual"].append({"name": f"manual:{kind}:{outage}:{monitored or 'auto'}", "kind": kind, "payload": response.json(),
                                "request": {"gridId": KPG, "line": outage, "monitored": monitored, "requestId": request_id}})


app = web_app.app


@app.middleware("http")
async def fixture_page(request: Request, call_next):
    if request.url.path == "/__run_validation__/":
        # Errors are recorded before the real module loads; the image holds the load event until the report.
        return HTMLResponse("""<!doctype html><meta charset="utf-8"><title>C-2b run validation</title>
<script>window.__runValidationErrors=[];addEventListener('error',e=>window.__runValidationErrors.push(String(e.message)));
addEventListener('unhandledrejection',e=>window.__runValidationErrors.push(String(e.reason)));</script>
<body><img src="/__run_validation__/hold" alt="" hidden><p id="status">running</p>
<script src="/console/api-analysis.js"></script><script src="/__run_validation__/probe.js"></script></body>""")
    return await call_next(request)


@app.get("/__run_validation__/probe.js")
def probe_script():
    return Response((ROOT / "tests/browser/console_run_validation_probe.js").read_text(encoding="utf-8"), media_type="text/javascript")


@app.get("/__run_validation__/cases")
def cases():
    return {"cases": STATE["cases"], "manual": STATE["manual"]}


@app.post("/__run_validation__/report")
async def report(request: Request):
    STATE["report"] = await request.json()
    DONE.set()
    return {"ok": True}


@app.get("/__run_validation__/hold")
async def hold():
    try:
        await asyncio.wait_for(DONE.wait(), timeout=120)
    except asyncio.TimeoutError:
        pass
    return Response(bytes.fromhex("474946383961010001008000000000ffffff21f90401000000002c00000000010001000002024401003b"),
                    media_type="image/gif")


@app.get("/__run_validation__/state")
def snapshot():
    return {"report": STATE["report"], "provider_calls": STATE["provider_calls"], "legacy_calls": STATE["legacy_calls"],
            "cases": [{"name": case["name"], "expect": case["expect"]} for case in STATE["cases"]],
            "manual": [case["name"] for case in STATE["manual"]]}


if __name__ == "__main__":
    generate()  # after the fixture routes and middleware exist; requests pass through them unchanged
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), workers=1, log_level="warning")
