"""PH4-02B3-1: optional analysis context, manual receipts and server-result reference.

Real FastAPI handlers, registry and full KPG domain calculations; any LLM graph is
replaced by a stub that fails if invoked. No Chrome and no real LLM requests.
"""
import faulthandler
import json
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import web_app
from ai_ems.analysis_context import is_uuid4
from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates
from ai_ems.grid_catalog import KOREA_OSM_NOTE
from ai_ems.network import load_identified_network, load_network
from ai_ems.network_access import network_access
from ai_ems.tools.security_tools import run_line_contingency as domain_security

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "data/KPG193_ver2_0_powsybl_full.mat"
KPG, MN, KR = "kr-kpg193", "mn-osm-35k", "kr-osm-transmission"
OUTAGE, MONITORED = "LINE-16-28", "LINE-16-22"
MULTI, NO_OVERLOAD, NONCONVERGED = "LINE-58-59", "LINE-1-2", "LINE-28-80"
MN_OUTAGE = "751261406-1_0"
SESSION = "console-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def bounded_test():
    faulthandler.dump_traceback_later(45, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


class ForbiddenGraph:
    def __init__(self):
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        raise AssertionError("an LLM graph must not run for this request")


class FixedGraph:
    def __init__(self, text="기존 일반 채팅 답변"):
        self.calls, self.text = 0, text

    def invoke(self, state):
        self.calls += 1
        return {"messages": [*state["messages"], AIMessage(content=self.text)]}


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Fresh v1 state and forbidden LLM graphs for every test; no global leakage."""
    graphs = {"kpg": ForbiddenGraph(), "mn": ForbiddenGraph()}
    monkeypatch.setattr(web_app, "agent_graph", graphs["kpg"])
    monkeypatch.setattr(web_app, "mongolia_agent_graph", graphs["mn"])
    monkeypatch.setattr(web_app, "agent_sessions", {})
    monkeypatch.setattr(web_app, "mongolia_agent_sessions", {})
    monkeypatch.setattr(web_app, "analysis_results", ResultRegistry())
    monkeypatch.setattr(web_app, "analysis_requests", RequestTracker())
    monkeypatch.setattr(web_app, "analysis_sessions", SessionGates())
    return graphs


@pytest.fixture
def domain_calls(monkeypatch):
    counts = Counter()
    for name in ("run_line_contingency", "rank_generator_sensitivities", "select_most_severe_violated_line"):
        real = getattr(web_app, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            counts[_name] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(web_app, name, spy)
    return counts


@pytest.fixture
def engine_calls(monkeypatch):
    counts = Counter()
    for module, name in [(pp.loadflow, "run_ac"), (pp.security, "create_analysis"),
                         (pp.sensitivity, "create_ac_analysis")]:
        real = getattr(module, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            counts[_name] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, spy)
    return counts


@pytest.fixture
def kpg(monkeypatch):
    """A fresh identified full KPG model, prepared by one explicit N-1 outside v1."""
    model = load_identified_network(FULL)
    domain_security(model, OUTAGE)
    monkeypatch.setattr(web_app, "network", model)
    return model


@pytest.fixture
def client():
    return TestClient(web_app.app)


def new_id():
    return str(uuid.uuid4())


def headers(request_id=None, session=SESSION, version="1"):
    return {"X-EMS-Analysis-Context-Version": version, "X-EMS-Session-ID": session,
            "X-EMS-Request-ID": request_id or new_id()}


def manual(client, kind, outage=OUTAGE, monitored=None, session=SESSION, top_n=5, request_headers=None):
    path = "/api/security-analysis" if kind == "security" else "/api/sensitivity-analysis"
    params = {"outage_line_id": outage, **({"monitored_line_id": monitored} if monitored else {}),
              **({"top_n": top_n} if kind == "sensitivity" else {})}
    return client.get(path, params=params, headers=request_headers if request_headers is not None
                      else headers(session=session))


def receipt(client, kind, outage=OUTAGE, monitored=None, session=SESSION):
    response = manual(client, kind, outage, monitored, session)
    assert response.status_code == 200, response.text
    assert "analysis_receipt" in response.json(), response.json()
    return response.json()["analysis_receipt"]


def context(operation="security", mode="reference", security=None, sensitivity=None, outage=OUTAGE,
            monitored=None, **extra):
    return {"version": 1, "view_id": "20000000-0000-4000-8000-000000000001", "revision": 7,
            "mode": mode, "operation": operation, "outage_line_id": outage, "monitored_line_id": monitored,
            "references": {"security": security, "sensitivity": sensitivity}, **extra}


def chat_body(analysis_context, request_id=None, session=SESSION, message="기존 결과를 설명해 줘.", **extra):
    return {"session_id": session, "message": message, "request_id": request_id or new_id(),
            "analysis_context": analysis_context, **extra}


def reference(client, analysis_context, grid=KPG, **kwargs):
    return client.post(f"/api/grids/{grid}/chat", json=chat_body(analysis_context, **kwargs))


def v1_error(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "request_id"} and detail["code"] == code, detail
    return detail


# --- legacy compatibility -------------------------------------------------------------

def test_legacy_chat_contracts_and_history_are_unchanged(client, monkeypatch, isolated_state):
    graph = FixedGraph()
    monkeypatch.setattr(web_app, "agent_graph", graph)
    plain = client.post("/api/chat", json={"session_id": "legacy-1", "message": "안녕하세요"})
    assert plain.status_code == 200 and set(plain.json()) == {"session_id", "answer", "ui_updates"}
    nulls = client.post("/api/chat", json={"session_id": "legacy-2", "message": "안녕하세요",
                                          "request_id": None, "analysis_context": None, "unknown": 1})
    assert nulls.status_code == 200 and set(nulls.json()) == {"session_id", "answer", "ui_updates"}
    grid = client.post(f"/api/grids/{KPG}/chat", json={"session_id": "legacy-3", "message": "안녕"})
    assert set(grid.json()) == {"session_id", "answer", "ui_updates", "grid_id"}
    assert graph.calls == 3 and set(web_app.agent_sessions) == {"legacy-1", "legacy-2", "legacy-3"}
    korea = client.post(f"/api/grids/{KR}/chat", json={"session_id": "legacy-4", "message": "안녕"})
    assert korea.status_code == 409 and korea.json() == {"detail": KOREA_OSM_NOTE}
    mismatch = client.post(f"/api/grids/{MN}/chat", json={"session_id": "legacy-5", "message": "안녕", "grid_id": KPG})
    assert mismatch.status_code == 409 and isinstance(mismatch.json()["detail"], str)
    assert isolated_state["mn"].calls == 0 and web_app.mongolia_agent_sessions == {}


def test_legacy_rest_payload_matches_opt_in_payload_without_new_fields(kpg, client):
    for kind, outage, monitored in [("security", MULTI, None), ("sensitivity", OUTAGE, None),
                                    ("sensitivity", OUTAGE, MONITORED), ("sensitivity", NO_OVERLOAD, None)]:
        legacy = manual(client, kind, outage, monitored, request_headers={})
        assert legacy.status_code == 200 and "cache-control" not in legacy.headers
        assert not any(key.startswith("analysis_receipt") for key in legacy.json())
        opted = manual(client, kind, outage, monitored)
        assert opted.status_code == 200 and opted.headers["cache-control"] == "no-store"
        body = opted.json()
        assert {key: value for key, value in body.items() if key != "analysis_receipt"} == legacy.json()
    assert len(web_app.analysis_results) == 4


# --- v1 input validation ----------------------------------------------------------------

INVALID_REQUESTS = [
    ("request_id_only", lambda b: {**b, "analysis_context": None}, "ANALYSIS_OPT_IN_INCOMPLETE"),
    ("context_only", lambda b: {**b, "request_id": None}, "ANALYSIS_OPT_IN_INCOMPLETE"),
    ("request_id_uppercase", lambda b: {**b, "request_id": b["request_id"].upper()}, "ANALYSIS_CONTEXT_INVALID"),
    ("request_id_uuid1", lambda b: {**b, "request_id": "10000000-0000-1000-8000-000000000001"}, "ANALYSIS_CONTEXT_INVALID"),
    ("request_id_number", lambda b: {**b, "request_id": 12345}, "ANALYSIS_CONTEXT_INVALID"),
    ("session_space", lambda b: {**b, "session_id": "console 1"}, "ANALYSIS_CONTEXT_INVALID"),
    ("session_long", lambda b: {**b, "session_id": "s" * 129}, "ANALYSIS_CONTEXT_INVALID"),
    ("message_blank", lambda b: {**b, "message": "   "}, "ANALYSIS_CONTEXT_INVALID"),
    ("message_long", lambda b: {**b, "message": "가" * 8001}, "ANALYSIS_CONTEXT_INVALID"),
    ("context_list", lambda b: {**b, "analysis_context": []}, "ANALYSIS_CONTEXT_INVALID"),
    ("version_2", lambda b: ctx(b, version=2), "ANALYSIS_CONTEXT_INVALID"),
    ("version_string", lambda b: ctx(b, version="1"), "ANALYSIS_CONTEXT_INVALID"),
    ("version_bool", lambda b: ctx(b, version=True), "ANALYSIS_CONTEXT_INVALID"),
    ("revision_bool", lambda b: ctx(b, revision=True), "ANALYSIS_CONTEXT_INVALID"),
    ("revision_string", lambda b: ctx(b, revision="7"), "ANALYSIS_CONTEXT_INVALID"),
    ("revision_fraction", lambda b: ctx(b, revision=7.5), "ANALYSIS_CONTEXT_INVALID"),
    ("revision_negative", lambda b: ctx(b, revision=-1), "ANALYSIS_CONTEXT_INVALID"),
    ("revision_too_large", lambda b: ctx(b, revision=2 ** 53), "ANALYSIS_CONTEXT_INVALID"),
    ("view_id_invalid", lambda b: ctx(b, view_id="view-1"), "ANALYSIS_CONTEXT_INVALID"),
    ("mode_unknown", lambda b: ctx(b, mode="explain"), "ANALYSIS_CONTEXT_INVALID"),
    ("operation_unknown", lambda b: ctx(b, operation="redispatch"), "ANALYSIS_CONTEXT_INVALID"),
    ("outage_blank", lambda b: ctx(b, outage_line_id=""), "ANALYSIS_CONTEXT_INVALID"),
    ("outage_padded", lambda b: ctx(b, outage_line_id=" " + OUTAGE), "ANALYSIS_CONTEXT_INVALID"),
    ("outage_long", lambda b: ctx(b, outage_line_id="L" * 129), "ANALYSIS_CONTEXT_INVALID"),
    ("security_with_monitored", lambda b: ctx(b, monitored_line_id=MONITORED), "ANALYSIS_CONTEXT_INVALID"),
    ("monitored_equals_outage", lambda b: ctx(b, operation="sensitivity", monitored_line_id=OUTAGE,
                                              references={"security": None, "sensitivity": new_id()}),
     "ANALYSIS_CONTEXT_INVALID"),
    ("references_extra_key", lambda b: ctx(b, references={"security": new_id(), "sensitivity": None,
                                                          "generator": None}), "ANALYSIS_CONTEXT_INVALID"),
    ("references_missing_key", lambda b: ctx(b, references={"security": new_id()}), "ANALYSIS_CONTEXT_INVALID"),
    ("reference_not_uuid", lambda b: ctx(b, references={"security": "result-1", "sensitivity": None}),
     "ANALYSIS_CONTEXT_INVALID"),
    ("reference_missing_required", lambda b: ctx(b, references={"security": None, "sensitivity": None}),
     "ANALYSIS_CONTEXT_INVALID"),
    ("reference_unneeded_kind", lambda b: ctx(b, references={"security": new_id(), "sensitivity": new_id()}),
     "ANALYSIS_CONTEXT_INVALID"),
    ("chain_needs_both", lambda b: ctx(b, operation="security_then_sensitivity"), "ANALYSIS_CONTEXT_INVALID"),
    ("run_with_reference", lambda b: ctx(b, mode="run"), "ANALYSIS_CONTEXT_INVALID"),
    ("client_verified_flag", lambda b: ctx(b, verified=True), "ANALYSIS_CONTEXT_INVALID"),
    ("client_result_values", lambda b: ctx(b, result={"violated_equipment_count": 0}), "ANALYSIS_CONTEXT_INVALID"),
    ("client_candidates", lambda b: ctx(b, candidates=[]), "ANALYSIS_CONTEXT_INVALID"),
]


def ctx(body, **changes):
    return {**body, "analysis_context": {**body["analysis_context"], **changes}}


@pytest.mark.parametrize("case,mutate,code", INVALID_REQUESTS, ids=[case[0] for case in INVALID_REQUESTS])
def test_invalid_opt_in_is_rejected_before_any_llm_or_domain_call(
        kpg, client, domain_calls, engine_calls, isolated_state, case, mutate, code):
    body = mutate(chat_body(context(security=new_id())))
    for url in (f"/api/grids/{KPG}/chat", "/api/chat"):
        detail = v1_error(client.post(url, json=body), 422, code)
        assert detail["request_id"] == (body["request_id"] if is_uuid4(body["request_id"]) else None)
    assert dict(domain_calls) == {} and dict(engine_calls) == {}
    assert isolated_state["kpg"].calls == isolated_state["mn"].calls == 0
    assert web_app.agent_sessions == {} and len(web_app.analysis_results) == 0


@pytest.mark.parametrize("url,body_grid,status,code", [
    (f"/api/grids/{KPG}/chat", MN, 409, "GRID_MISMATCH"),
    ("/api/chat?grid_id=mn-osm-35k", None, 409, "GRID_MISMATCH"),
    (f"/api/grids/{MN}/chat", None, 409, "ANALYSIS_CONTEXT_UNSUPPORTED"),
    (f"/api/grids/{KR}/chat", None, 409, "ANALYSIS_CONTEXT_UNSUPPORTED"),
    ("/api/grids/unknown-grid/chat", None, 404, "UNKNOWN_GRID"),
])
def test_grid_routing_never_falls_back_to_another_grid_or_agent(
        kpg, client, domain_calls, isolated_state, url, body_grid, status, code):
    body = chat_body(context(security=new_id()), **({"grid_id": body_grid} if body_grid else {}))
    detail = v1_error(client.post(url, json=body), status, code)
    if url.endswith(f"{KR}/chat"):
        assert detail["message"] == KOREA_OSM_NOTE
    assert isolated_state["kpg"].calls == isolated_state["mn"].calls == 0 and dict(domain_calls) == {}
    assert web_app.agent_sessions == {} and web_app.mongolia_agent_sessions == {}


@pytest.mark.parametrize("operation", ["security", "sensitivity", "security_then_sensitivity"])
def test_run_mode_is_refused_without_legacy_fallback(kpg, client, domain_calls, isolated_state, operation):
    for url in (f"/api/grids/{KPG}/chat", "/api/chat"):
        body = chat_body(context(operation=operation, mode="run"), message="선택한 선로의 N-1을 실행해 줘.")
        v1_error(client.post(url, json=body), 409, "ANALYSIS_RUN_NOT_READY")
    assert isolated_state["kpg"].calls == 0 and dict(domain_calls) == {}
    assert web_app.agent_sessions == {} and len(web_app.analysis_results) == 0


def test_no_grid_advertises_the_v1_capability(client):
    grids = client.get("/api/grids").json()["grids"]
    assert grids and all("console_analysis_context_versions" not in grid for grid in grids)


# --- manual receipts ----------------------------------------------------------------

@pytest.mark.parametrize("kind,outage,monitored,assessment,reason,actual_monitored,selection", [
    ("security", OUTAGE, None, "completed", None, None, None),
    ("security", MULTI, None, "completed", None, None, None),
    ("security", NONCONVERGED, None, "not_evaluated", "SECURITY_NOT_CONVERGED", None, None),
    ("sensitivity", OUTAGE, None, "completed", None, MONITORED, "auto"),
    ("sensitivity", OUTAGE, MONITORED, "completed", None, MONITORED, "explicit"),
    ("sensitivity", NO_OVERLOAD, None, "not_evaluated", "NO_OVERLOAD", None, "auto"),
    ("sensitivity", NONCONVERGED, None, "not_evaluated", "SECURITY_NOT_CONVERGED", None, "auto"),
])
def test_manual_opt_in_registers_the_same_calculation_canonical_payload(
        kpg, client, domain_calls, kind, outage, monitored, assessment, reason, actual_monitored, selection):
    request_id = new_id()
    response = manual(client, kind, outage, monitored, request_headers=headers(request_id))
    assert response.status_code == 200, response.text
    body = response.json()
    issued = body.pop("analysis_receipt")
    assert issued["kind"] == kind and issued["grid_id"] == KPG and issued["outage_line_id"] == outage
    assert (issued["assessment"], issued["reason_code"]) == (assessment, reason)
    assert (issued["monitored_line_id"], issued["monitored_selection"]) == (actual_monitored, selection)
    assert issued["top_n"] == (5 if kind == "sensitivity" else None)
    assert (issued["producer"], issued["producer_request_id"], issued["tool_call_id"]) == ("manual", request_id, None)
    created = datetime.fromisoformat(issued["created_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00"))
    assert created.tzinfo == timezone.utc and (expires - created).total_seconds() == 600
    entry = web_app.analysis_results.lookup(issued["result_id"])
    assert entry.payload() == body and entry.session_id == SESSION
    evidence = json.loads(entry.evidence_json)
    assert evidence["model_revision_before"] == evidence["model_revision_after"]
    assert evidence["model_revision_after"]["revision"] == issued["model_revision"]
    assert domain_calls["run_line_contingency"] == (1 if kind == "security" or monitored is None else 0)
    if kind == "security" and assessment == "completed":
        assert body["violated_equipment_count"] == len(body["violations"])


@pytest.mark.parametrize("request_headers,status,code", [
    ({"X-EMS-Analysis-Context-Version": "1"}, 422, "ANALYSIS_OPT_IN_INCOMPLETE"),
    ({"X-EMS-Session-ID": SESSION, "X-EMS-Request-ID": new_id()}, 422, "ANALYSIS_OPT_IN_INCOMPLETE"),
    ({"X-EMS-Analysis-Context-Version": "1", "X-EMS-Session-ID": SESSION}, 422, "ANALYSIS_OPT_IN_INCOMPLETE"),
    (headers(version="2"), 422, "ANALYSIS_CONTEXT_INVALID"),
    (headers(session="console 1"), 422, "ANALYSIS_CONTEXT_INVALID"),
    (headers(request_id="not-a-uuid"), 422, "ANALYSIS_CONTEXT_INVALID"),
])
def test_partial_or_invalid_receipt_headers_compute_nothing(
        kpg, client, domain_calls, engine_calls, request_headers, status, code):
    for kind in ("security", "sensitivity"):
        v1_error(manual(client, kind, request_headers=request_headers), status, code)
    v1_error(manual(client, "sensitivity", top_n=3), 422, "ANALYSIS_CONTEXT_INVALID")
    assert dict(domain_calls) == {} and dict(engine_calls) == {} and len(web_app.analysis_results) == 0


def test_first_ac_is_never_registered_and_never_rerun(client, monkeypatch, domain_calls):
    fresh = load_identified_network(FULL)
    monkeypatch.setattr(web_app, "network", fresh)
    first = manual(client, "security")
    assert first.status_code == 200 and "analysis_receipt" not in first.json()
    assert first.json()["analysis_receipt_error"]["code"] == "MODEL_REVISION_CHANGED"
    assert first.json()["violated_equipment_count"] == 1 and first.headers["cache-control"] == "no-store"
    assert len(web_app.analysis_results) == 0 and domain_calls["run_line_contingency"] == 1
    second = manual(client, "security")
    assert "analysis_receipt" in second.json() and domain_calls["run_line_contingency"] == 2
    assert {k: v for k, v in second.json().items() if k != "analysis_receipt"} == \
           {k: v for k, v in first.json().items() if k != "analysis_receipt_error"}


def test_unidentified_model_keeps_result_but_issues_no_receipt(client, monkeypatch):
    legacy_model = load_network(FULL)  # legacy loader: no provenance, no revision
    monkeypatch.setattr(web_app, "network", legacy_model)
    plain = manual(client, "security", request_headers={})
    opted = manual(client, "security")
    assert opted.status_code == 200 and opted.json()["analysis_receipt_error"]["code"] == "MODEL_IDENTITY_UNAVAILABLE"
    assert {k: v for k, v in opted.json().items() if k != "analysis_receipt_error"} == plain.json()
    assert len(web_app.analysis_results) == 0


def test_failed_calculation_keeps_the_legacy_error_and_registers_nothing(kpg):
    raw = TestClient(web_app.app, raise_server_exceptions=False)
    legacy = raw.get("/api/security-analysis", params={"outage_line_id": "NO-SUCH-LINE"})
    opted = raw.get("/api/security-analysis", params={"outage_line_id": "NO-SUCH-LINE"}, headers=headers())
    assert legacy.status_code == opted.status_code == 500 and len(web_app.analysis_results) == 0


def test_mongolia_rest_refuses_opt_in_but_keeps_legacy_calls(client, monkeypatch):
    calls = Counter()
    service = web_app._mongolia_security()
    for method in ("run_line_contingency", "run_line_sensitivity"):
        real = getattr(service, method)

        def spy(*args, _real=real, _method=method, **kwargs):
            calls[_method] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(service, method, spy)
    for path in ("security-analysis", "sensitivity-analysis"):
        url = f"/api/grids/{MN}/{path}"
        v1_error(client.get(url, params={"outage_line_id": MN_OUTAGE}, headers=headers()), 409,
                 "ANALYSIS_CONTEXT_UNSUPPORTED")
        v1_error(client.get(url, params={"outage_line_id": MN_OUTAGE}, headers={"X-EMS-Session-ID": SESSION}),
                 422, "ANALYSIS_OPT_IN_INCOMPLETE")
    assert dict(calls) == {}
    legacy = client.get(f"/api/grids/{MN}/security-analysis", params={"outage_line_id": MN_OUTAGE})
    assert legacy.status_code == 200 and legacy.json()["grid_id"] == MN and "analysis_receipt" not in legacy.json()
    assert dict(calls) == {"run_line_contingency": 1} and len(web_app.analysis_results) == 0


# --- reference mode ---------------------------------------------------------------

def test_reference_explains_registered_results_with_no_calculation_or_llm(
        kpg, client, domain_calls, engine_calls, isolated_state):
    security = receipt(client, "security", MULTI)
    sensitivity = receipt(client, "sensitivity", MULTI)
    domain_calls.clear(); engine_calls.clear()
    request_id = new_id()
    analysis_context = context("security_then_sensitivity", security=security["result_id"],
                               sensitivity=sensitivity["result_id"], outage=MULTI)
    response = reference(client, analysis_context, request_id=request_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["session_id", "grid_id", "request_id", "analysis_context", "answer", "ui_updates", "analysis"]
    assert (body["session_id"], body["grid_id"], body["request_id"]) == (SESSION, KPG, request_id)
    assert body["analysis_context"] == analysis_context and body["ui_updates"] == []
    assert body["analysis"]["status"] == "referenced" and body["analysis"]["source"] == "server_reference"
    assert body["analysis"]["results"] == [security, sensitivity] and body["analysis"]["reason_code"] is None
    stored = [web_app.analysis_results.lookup(r["result_id"]).payload() for r in (security, sensitivity)]
    for violation in stored[0]["violations"][:10]:
        assert violation["equipment_id"] in body["answer"]
    for candidate in stored[1]["candidates"]:
        assert candidate["generator_id"] in body["answer"] and f"{candidate['sensitivity']:.6f}" in body["answer"]
    assert "새 계산은 수행하지 않았습니다" in body["answer"] and "외 2건" in body["answer"]
    assert dict(domain_calls) == {} and dict(engine_calls) == {}
    assert isolated_state["kpg"].calls == 0 and web_app.agent_sessions == {}


def test_reference_of_an_unevaluated_result_reports_no_candidates(kpg, client):
    unevaluated = receipt(client, "sensitivity", NO_OVERLOAD)
    response = reference(client, context("sensitivity", sensitivity=unevaluated["result_id"], outage=NO_OVERLOAD))
    body = response.json()
    assert body["analysis"]["reason_code"] == "NO_OVERLOAD" and body["analysis"]["results"] == [unevaluated]
    assert "후보 0개, 순위 없음(미평가)" in body["answer"] and "\n  1. " not in body["answer"]


def test_reference_identity_and_condition_mismatches_are_unavailable(kpg, client, domain_calls):
    security = receipt(client, "security")
    auto = receipt(client, "sensitivity")
    explicit = receipt(client, "sensitivity", monitored=MONITORED)
    other_session = receipt(client, "security", session="console-other")
    other_grid = web_app.analysis_results.register(
        grid_id=MN, session_id=SESSION, kind="security", outage_line_id=OUTAGE, monitored_line_id=None,
        monitored_selection=None, top_n=None, model_revision=security["model_revision"], producer="manual",
        producer_request_id=new_id(), tool_call_id=None, assessment="completed", reason_code=None,
        payload={"grid_id": MN}, evidence={})
    domain_calls.clear()
    cases = {
        "unknown": context(security=new_id()),
        "other_session": context(security=other_session["result_id"]),
        "other_grid": context(security=other_grid.result_id),
        "wrong_kind": context(security=auto["result_id"]),
        "other_outage": context(security=security["result_id"], outage=MULTI),
        "explicit_mismatch": context("sensitivity", sensitivity=explicit["result_id"], monitored="LINE-16-17"),
        "auto_vs_explicit": context("sensitivity", sensitivity=explicit["result_id"]),
        "explicit_vs_auto": context("sensitivity", sensitivity=auto["result_id"], monitored=MONITORED),
    }
    for name, analysis_context in cases.items():
        v1_error(reference(client, analysis_context), 409, "ANALYSIS_REFERENCE_UNAVAILABLE")
    with pytest.raises(ValueError):  # a non-v1 top_n can never be registered
        web_app.analysis_results.register(
            grid_id=KPG, session_id=SESSION, kind="sensitivity", outage_line_id=OUTAGE,
            monitored_line_id=MONITORED, monitored_selection="explicit", top_n=3,
            model_revision=security["model_revision"], producer="manual", producer_request_id=new_id(),
            tool_call_id=None, assessment="completed", reason_code=None, payload={}, evidence={})
    assert reference(client, context("sensitivity", sensitivity=explicit["result_id"], monitored=MONITORED)).status_code == 200
    assert dict(domain_calls) == {}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_expiry_capacity_and_restart_make_references_unavailable(kpg, client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(web_app, "analysis_results", ResultRegistry(capacity=2, monotonic=clock))
    first, second, third = (receipt(client, "security", outage) for outage in (OUTAGE, MULTI, NO_OVERLOAD))
    v1_error(reference(client, context(security=first["result_id"])), 409, "ANALYSIS_REFERENCE_UNAVAILABLE")
    assert reference(client, context(security=second["result_id"], outage=MULTI)).status_code == 200
    clock.now += 600
    v1_error(reference(client, context(security=third["result_id"], outage=NO_OVERLOAD)), 409,
             "ANALYSIS_REFERENCE_UNAVAILABLE")
    fourth = receipt(client, "security")
    monkeypatch.setattr(web_app, "analysis_results", ResultRegistry())  # process restart
    v1_error(reference(client, context(security=fourth["result_id"])), 409, "ANALYSIS_REFERENCE_UNAVAILABLE")


def test_model_change_makes_references_stale_without_recalculation(client, monkeypatch, domain_calls):
    fresh = load_identified_network(FULL)
    monkeypatch.setattr(web_app, "network", fresh)
    initial = receipt(client, "sensitivity", monitored=MONITORED)  # explicit: no AC state write
    domain_calls.clear()
    assert reference(client, context("sensitivity", sensitivity=initial["result_id"], monitored=MONITORED)).status_code == 200
    manual(client, "security", request_headers={})  # a legacy N-1 performs the first AC
    v1_error(reference(client, context("sensitivity", sensitivity=initial["result_id"], monitored=MONITORED)),
             409, "ANALYSIS_REFERENCE_STALE")
    current = receipt(client, "security")
    with network_access(fresh):
        row = fresh.get_loads().iloc[0]
        fresh.update_loads(id=row.name, p0=float(row.p0) + 1.0)
    domain_calls.clear()
    v1_error(reference(client, context(security=current["result_id"])), 409, "ANALYSIS_REFERENCE_STALE")
    assert dict(domain_calls) == {}


def test_engine_contention_and_unidentified_model_are_explicit(kpg, client, monkeypatch):
    issued = receipt(client, "security")
    ready, release = threading.Event(), threading.Event()

    def hold():
        with network_access(kpg):
            ready.set()
            assert release.wait(10)

    request_id = new_id()
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold)
        try:
            assert ready.wait(5)
            v1_error(reference(client, context(security=issued["result_id"]), request_id=request_id), 409, "ANALYSIS_BUSY")
        finally:
            release.set()
        holder.result(timeout=5)
    assert reference(client, context(security=issued["result_id"]), request_id=request_id).status_code == 200
    monkeypatch.setattr(web_app, "network", load_network(FULL))
    v1_error(reference(client, context(security=issued["result_id"])), 503, "MODEL_IDENTITY_UNAVAILABLE")


# --- request identity and sessions -------------------------------------------------

def test_completed_request_is_replayed_byte_for_byte_and_never_reused(kpg, client, monkeypatch):
    issued = receipt(client, "security")
    calls = Counter()
    real = web_app.format_reference_answer

    def counted(results):
        calls["format"] += 1
        return real(results)

    monkeypatch.setattr(web_app, "format_reference_answer", counted)
    body = chat_body(context(security=issued["result_id"]))
    first = client.post(f"/api/grids/{KPG}/chat", json=body)
    replay = client.post(f"/api/grids/{KPG}/chat", json=body)
    assert first.status_code == replay.status_code == 200 and first.content == replay.content
    assert calls["format"] == 1
    v1_error(client.post(f"/api/grids/{KPG}/chat", json={**body, "message": "다른 질문"}), 409, "REQUEST_ID_REUSE")
    v1_error(client.post(f"/api/grids/{KPG}/chat", json=ctx(body, revision=8)), 409, "REQUEST_ID_REUSE")
    # Mongolia keeps its own identity space: the same IDs are not a KPG replay.
    v1_error(client.post(f"/api/grids/{MN}/chat", json=body), 409, "ANALYSIS_CONTEXT_UNSUPPORTED")


def test_failed_request_releases_its_id_for_a_retry(kpg, client):
    body = chat_body(context(security=new_id()))
    v1_error(client.post(f"/api/grids/{KPG}/chat", json=body), 409, "ANALYSIS_REFERENCE_UNAVAILABLE")
    v1_error(client.post(f"/api/grids/{KPG}/chat", json=body), 409, "ANALYSIS_REFERENCE_UNAVAILABLE")
    issued = receipt(client, "security")
    retry = ctx(body, references={"security": issued["result_id"], "sensitivity": None})
    assert client.post(f"/api/grids/{KPG}/chat", json=retry).status_code == 200


def test_overlapping_requests_get_in_progress_session_busy_or_proceed(kpg, client, monkeypatch):
    issued = receipt(client, "security")
    other = receipt(client, "security", session="console-second")
    entered, release = threading.Event(), threading.Event()
    real = web_app.format_reference_answer

    def slow(results):
        entered.set()
        assert release.wait(10)
        return real(results)

    monkeypatch.setattr(web_app, "format_reference_answer", slow)
    body = chat_body(context(security=issued["result_id"]))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(TestClient(web_app.app).post, f"/api/grids/{KPG}/chat", json=body)
        try:
            assert entered.wait(5)
            v1_error(client.post(f"/api/grids/{KPG}/chat", json=body), 409, "REQUEST_IN_PROGRESS")
            v1_error(client.post(f"/api/grids/{KPG}/chat", json={**body, "message": "변경"}), 409, "REQUEST_ID_REUSE")
            v1_error(client.post(f"/api/grids/{KPG}/chat", json=chat_body(context(security=issued["result_id"]))),
                     409, "SESSION_BUSY")
            monkeypatch.setattr(web_app, "format_reference_answer", real)
            separate = client.post(f"/api/grids/{KPG}/chat",
                                   json=chat_body(context(security=other["result_id"]), session="console-second"))
            assert separate.status_code == 200
        finally:
            release.set()
        first = pending.result(timeout=20)
    assert first.status_code == 200
    assert client.post(f"/api/grids/{KPG}/chat", json=body).content == first.content


def test_request_window_expires_and_registry_lock_is_never_held_by_calculation(kpg, monkeypatch):
    clock = Clock()
    tracker = RequestTracker(monotonic=clock)
    key = (KPG, SESSION, new_id())
    assert tracker.begin(key, "a") is None
    tracker.complete(key, "a", {"answer": "x"})
    with pytest.raises(Exception, match="REQUEST_ID_REUSE"):
        tracker.begin(key, "b")
    clock.now += 600
    assert tracker.begin(key, "b") is None
    entered, release = threading.Event(), threading.Event()
    real = web_app.run_line_contingency

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(web_app, "run_line_contingency", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(manual, TestClient(web_app.app), "security")
        try:
            assert entered.wait(5)
            assert pool.submit(len, web_app.analysis_results).result(timeout=2) == 0  # registry lock is free
            assert pool.submit(web_app.analysis_results.lookup, new_id()).result(timeout=2) is None
        finally:
            release.set()
        assert pending.result(timeout=20).status_code == 200
    assert len(web_app.analysis_results) == 1


def test_registry_is_bounded_and_thread_safe():
    registry = ResultRegistry(capacity=50)
    revision = "a" * 64

    def register(index):
        return registry.register(
            grid_id=KPG, session_id=SESSION, kind="security", outage_line_id=f"LINE-{index}",
            monitored_line_id=None, monitored_selection=None, top_n=None, model_revision=revision,
            producer="manual", producer_request_id=new_id(), tool_call_id=None, assessment="completed",
            reason_code=None, payload={"index": index}, evidence={}).result_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(register, range(400)))
    assert len(set(ids)) == 400 and len(registry) == 50
    live = [result_id for result_id in ids if registry.lookup(result_id) is not None]
    assert len(live) == 50
    for bad in [{"payload": {"value": float("nan")}}, {"producer": "client"}, {"model_revision": "x"},
                {"assessment": "completed", "reason_code": "NO_OVERLOAD"}]:
        with pytest.raises(ValueError):
            registry.register(**{**dict(
                grid_id=KPG, session_id=SESSION, kind="security", outage_line_id=OUTAGE, monitored_line_id=None,
                monitored_selection=None, top_n=None, model_revision=revision, producer="manual",
                producer_request_id=new_id(), tool_call_id=None, assessment="completed", reason_code=None,
                payload={}, evidence={}), **bad})


def test_session_gates_are_per_grid_and_session():
    gates = SessionGates()
    with gates.hold(KPG, SESSION):
        with pytest.raises(Exception, match="SESSION_BUSY"):
            with gates.hold(KPG, SESSION):
                pass
        with gates.hold(MN, SESSION), gates.hold(KPG, "console-other"):
            pass
    with gates.hold(KPG, SESSION):
        pass
