"""PH4-02B3-2: v1 run through the request-scoped B2 Agent, publish boundary, sessions, budgets.

Only the LLM is scripted (a BaseChatModel stub); every Tool performs the real full KPG
calculation. The configured provider is never contacted: its factory fails in tests.
"""
import asyncio
import faulthandler
import importlib.util
import json
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import ollama
import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import web_app
from ai_ems import analysis_run
from ai_ems.agent import graph as graph_module
from ai_ems.agent import tools as agent_tools
from ai_ems.agent.formatters.analysis_reference import format_run_answer
from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates
from ai_ems.network import load_identified_network
from ai_ems.network_access import NetworkBusy, access_for, network_access
from ai_ems.tools.security_tools import run_line_contingency as domain_security

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "data/KPG193_ver2_0_powsybl_full.mat"
KPG, MN, KR = "kr-kpg193", "mn-osm-35k", "kr-osm-transmission"
LINE, SENS = "line_contingency", "generator_sensitivity"
OUTAGE, MONITORED = "LINE-16-28", "LINE-16-22"
MULTI, NO_OVERLOAD, NONCONVERGED = "LINE-58-59", "LINE-1-2", "LINE-28-80"
SESSION = "console-00000000000000000000000000000b32"
ENVELOPE_KEYS = ["session_id", "grid_id", "request_id", "analysis_context", "answer", "ui_updates", "analysis"]
_launcher_spec = importlib.util.spec_from_file_location("agent_launcher_b32", ROOT / "scripts/run_agent.py")
launcher = importlib.util.module_from_spec(_launcher_spec)
_launcher_spec.loader.exec_module(launcher)


@pytest.fixture(autouse=True)
def bounded_test():
    faulthandler.dump_traceback_later(45, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


class ScriptedModel(BaseChatModel):
    """Replaces only the LLM; steps are messages, exceptions or callables of the input."""

    steps: list
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-b32"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        step = self.steps.pop(0)
        if callable(step):
            step = step(messages)
        if isinstance(step, BaseException):
            raise step
        return ChatResult(generations=[ChatGeneration(message=step)])


class ForbiddenGraph:
    def __init__(self):
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        raise AssertionError("the legacy Agent graph must not run for a v1 request")


class FixedGraph:
    def __init__(self, text="일반 채팅 답변"):
        self.calls, self.text, self.seen = 0, text, None

    def invoke(self, state):
        self.calls += 1
        self.seen = list(state["messages"])
        return {"messages": [*state["messages"], AIMessage(content=self.text)]}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    graphs = {"kpg": ForbiddenGraph(), "mn": ForbiddenGraph()}
    monkeypatch.setattr(web_app, "agent_graph", graphs["kpg"])
    monkeypatch.setattr(web_app, "mongolia_agent_graph", graphs["mn"])
    monkeypatch.setattr(web_app, "agent_sessions", {})
    monkeypatch.setattr(web_app, "mongolia_agent_sessions", {})
    monkeypatch.setattr(web_app, "analysis_results", ResultRegistry())
    monkeypatch.setattr(web_app, "analysis_requests", RequestTracker())
    monkeypatch.setattr(web_app, "analysis_sessions", SessionGates())
    monkeypatch.setenv(analysis_run.SINGLE_WORKER_ENV, "1")
    for name in analysis_run.BUDGET_DEFAULTS:
        monkeypatch.delenv(name, raising=False)

    def provider_forbidden(budget):
        raise AssertionError("tests must never contact the configured LLM provider")

    monkeypatch.setattr(web_app, "_analysis_run_model", provider_forbidden)
    return graphs


@pytest.fixture
def script(monkeypatch):
    def install(*steps):
        model = ScriptedModel(steps=list(steps))
        monkeypatch.setattr(web_app, "_analysis_run_model", lambda budget: model)
        return model
    return install


@pytest.fixture
def kpg(monkeypatch):
    """Fresh identified full KPG model, prepared by one explicit N-1 outside v1."""
    model = load_identified_network(FULL)
    domain_security(model, OUTAGE)
    monkeypatch.setattr(web_app, "network", model)
    return model


@pytest.fixture
def client():
    return TestClient(web_app.app)


@pytest.fixture
def domain_calls(monkeypatch):
    counts = Counter()
    for module, prefix in [(agent_tools, "tool"), (web_app, "rest")]:
        for name in ("run_line_contingency", "rank_generator_sensitivities"):
            real = getattr(module, name)

            def spy(*args, _real=real, _key=f"{prefix}.{name}", **kwargs):
                counts[_key] += 1
                return _real(*args, **kwargs)

            monkeypatch.setattr(module, name, spy)
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


def new_id():
    return str(uuid.uuid4())


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def run_body(operation="security", outage=OUTAGE, monitored=None, session=SESSION, request_id=None,
             message="선택한 선로 분석을 실행해 줘."):
    return {"session_id": session, "message": message, "request_id": request_id or new_id(),
            "analysis_context": {"version": 1, "view_id": "20000000-0000-4000-8000-0000000000b2", "revision": 3,
                                 "mode": "run", "operation": operation, "outage_line_id": outage,
                                 "monitored_line_id": monitored,
                                 "references": {"security": None, "sensitivity": None}}}


def post(client, body, grid=KPG):
    return client.post(f"/api/grids/{grid}/chat", json=body)


def v1_error(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "request_id"} and detail["code"] == code, detail
    return detail


def change_load(model):
    with network_access(model):
        row = model.get_loads().iloc[0]
        model.update_loads(id=row.name, p0=float(row.p0) + 1.0)


def steps_for(operation, outage, monitored):
    args = {"outage_line_id": outage, **({"monitored_line_id": monitored} if monitored else {})}
    security, sensitivity = call(LINE, {"outage_line_id": outage}), call(SENS, args, "call-2")
    return {"security": [security], "sensitivity": [call(SENS, args)],
            "security_then_sensitivity": [security, sensitivity]}[operation]


# --- verified runs -------------------------------------------------------------------

def test_security_run_publishes_one_verified_receipt_and_update(
        kpg, client, script, domain_calls, engine_calls, isolated):
    model = script(call(LINE, {"outage_line_id": OUTAGE}))
    engine_calls.clear()
    body = run_body()
    response = post(client, body)
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert list(envelope) == ENVELOPE_KEYS and envelope["analysis_context"] == body["analysis_context"]
    analysis = envelope["analysis"]
    assert (analysis["status"], analysis["source"], analysis["reason_code"]) == ("completed", "tool_execution", None)
    (receipt,) = analysis["results"]
    assert (receipt["producer"], receipt["producer_request_id"], receipt["tool_call_id"]) == (
        "agent_tool", body["request_id"], "call-1")
    assert (receipt["kind"], receipt["outage_line_id"], receipt["monitored_line_id"], receipt["top_n"]) == (
        "security", OUTAGE, None, None)
    (update,) = envelope["ui_updates"]
    assert (update["type"], update["grid_id"], update["result_id"]) == ("security", KPG, receipt["result_id"])
    assert dict(engine_calls) == {"run_ac": 1, "create_analysis": 1} and dict(domain_calls) == {"tool.run_line_contingency": 1}
    entry = web_app.analysis_results.lookup(receipt["result_id"])
    assert entry.payload() == update["result"] and entry.session_id == SESSION
    evidence = json.loads(entry.evidence_json)
    assert evidence["operation"] == "security" and len(evidence["artifact_sha256"]) == 64
    assert update["result"] == client.get("/api/security-analysis", params={"outage_line_id": OUTAGE}).json()
    assert len(model.calls) == 1 and isolated["kpg"].calls == 0
    history = web_app.agent_sessions[SESSION]
    assert [type(m).__name__ for m in history] == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert history[3].artifact is None and history[-1].content == envelope["answer"]
    assert "검증된 Tool 실행" in envelope["answer"] and "LINE-16-22" in envelope["answer"]
    # The answer is a pure function of the approved receipts and canonical payloads.
    assert envelope["answer"] == format_run_answer([(receipt, update["result"])], "completed", None, "security")


RUN_CASES = [
    ("security", MULTI, None, "completed", None, ["security"]),
    ("security", NONCONVERGED, None, "not_evaluated", "SECURITY_NOT_CONVERGED", ["security"]),
    ("sensitivity", OUTAGE, None, "completed", None, ["sensitivity"]),
    ("sensitivity", OUTAGE, MONITORED, "completed", None, ["sensitivity"]),
    ("sensitivity", NO_OVERLOAD, None, "not_evaluated", "NO_OVERLOAD", ["sensitivity"]),
    ("security_then_sensitivity", OUTAGE, None, "completed", None, ["security", "sensitivity"]),
    ("security_then_sensitivity", NONCONVERGED, None, "not_evaluated", "SECURITY_NOT_CONVERGED", ["security"]),
]


@pytest.mark.parametrize("operation,outage,monitored,status,reason,kinds", RUN_CASES)
def test_run_results_are_the_rest_canonical_payloads_of_the_same_state(
        kpg, client, script, operation, outage, monitored, status, reason, kinds):
    model = script(*steps_for(operation, outage, monitored))
    body = run_body(operation, outage, monitored)
    envelope = post(client, body).json()
    analysis = envelope["analysis"]
    assert (analysis["status"], analysis["reason_code"]) == (status, reason)
    assert envelope["analysis_context"] == body["analysis_context"]  # auto selection never fills the echo
    assert [r["kind"] for r in analysis["results"]] == [u["type"] for u in envelope["ui_updates"]] == kinds
    assert [r["result_id"] for r in analysis["results"]] == [u["result_id"] for u in envelope["ui_updates"]]
    assert len({r["model_revision"] for r in analysis["results"]}) == 1
    assert len(model.calls) == len(kinds)  # the server, not the model, ends an unevaluated chain
    for receipt, update in zip(analysis["results"], envelope["ui_updates"]):
        if receipt["kind"] == "security":
            rest = client.get("/api/security-analysis", params={"outage_line_id": outage}).json()
            assert update["result"]["violated_equipment_count"] == len(update["result"]["violations"])
        else:
            params = {"outage_line_id": outage, "top_n": 5, **({"monitored_line_id": monitored} if monitored else {})}
            rest = client.get("/api/sensitivity-analysis", params=params).json()
            assert receipt["monitored_selection"] == ("explicit" if monitored else "auto")
        assert update["result"] == rest, receipt["kind"]
    if outage == MULTI:
        assert envelope["ui_updates"][0]["result"]["violated_equipment_count"] == 12
    if operation == "security_then_sensitivity" and status == "not_evaluated":
        assert "민감도는 실행하지 않았습니다" in envelope["answer"]


def test_chain_runs_sensitivity_only_after_the_published_security_step(kpg, client, script, domain_calls):
    seen = {}

    def second(messages):
        seen["tool"] = [m for m in messages if isinstance(m, ToolMessage)][-1]
        seen["context"] = messages[-1]
        return call(SENS, {"outage_line_id": OUTAGE, "top_n": 5}, "call-2")

    model = script(call(LINE, {"outage_line_id": OUTAGE}), second)
    envelope = post(client, run_body("security_then_sensitivity")).json()
    assert envelope["analysis"]["status"] == "completed"
    assert seen["tool"].tool_call_id == "call-1" and seen["tool"].status == "success"
    assert isinstance(seen["context"], SystemMessage) and f"{SENS} with arguments" in seen["context"].content
    # Chain N-1 once, plus the REST-equivalent N-1 inside automatic target selection.
    assert dict(domain_calls) == {"tool.run_line_contingency": 2, "tool.rank_generator_sensitivities": 1}
    assert len(model.calls) == 2 and len(web_app.analysis_results) == 2
    stored = web_app.agent_sessions[SESSION]
    assert sum(isinstance(m, SystemMessage) for m in stored) == 1  # transient job context never stored


FAILURES = [
    ("guard_other_outage", "security", OUTAGE, [call(LINE, {"outage_line_id": MULTI})], "failed", "OUTAGE_LINE_MISMATCH", 0),
    ("guard_monitored_on_security", "security", OUTAGE,
     [call(LINE, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED})], "failed", "MONITORED_LINE_NOT_ALLOWED", 0),
    ("guard_multiple_calls", "security_then_sensitivity", OUTAGE,
     [AIMessage(content="", tool_calls=[{"name": LINE, "args": {"outage_line_id": OUTAGE}, "id": "a"},
                                         {"name": SENS, "args": {"outage_line_id": OUTAGE}, "id": "b"}])],
     "failed", "MULTIPLE_TOOL_CALLS", 0),
    ("guard_auto_replaced", "sensitivity", OUTAGE,
     [call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED})], "failed", "AUTO_SELECTION_REPLACED", 0),
    ("guard_top_n", "sensitivity", OUTAGE, [call(SENS, {"outage_line_id": OUTAGE, "top_n": 3})],
     "failed", "TOP_N_NOT_ALLOWED", 0),
    ("text_only_claim", "security", OUTAGE, [AIMessage(content="계산 완료했습니다. 위반 0건, 계통 안전")],
     "not_run", "TOOL_NOT_EXECUTED", 0),
    ("chain_incomplete", "security_then_sensitivity", OUTAGE,
     [call(LINE, {"outage_line_id": OUTAGE}), AIMessage(content="민감도 후보 5개까지 완료")], "failed", "CHAIN_INCOMPLETE", 1),
    ("unknown_line", "security", "NO-SUCH-LINE", [call(LINE, {"outage_line_id": "NO-SUCH-LINE"})],
     "failed", "TOOL_INPUT_REJECTED", 1),
]


@pytest.mark.parametrize("case,operation,outage,steps,status,reason,security_calls", FAILURES,
                         ids=[case[0] for case in FAILURES])
def test_failed_or_unexecuted_runs_publish_nothing(
        kpg, client, script, domain_calls, case, operation, outage, steps, status, reason, security_calls):
    script(*steps)
    envelope = post(client, run_body(operation, outage)).json()
    analysis = envelope["analysis"]
    assert (analysis["status"], analysis["reason_code"], analysis["source"]) == (status, reason, "none")
    assert analysis["results"] == [] and envelope["ui_updates"] == [] and len(web_app.analysis_results) == 0
    assert domain_calls["tool.run_line_contingency"] == security_calls
    stored = web_app.agent_sessions[SESSION]
    assert stored[-1].content == envelope["answer"] and "결과를 공개하지 않습니다" in envelope["answer"]
    assert not any("위반 0건" in str(m.content) or "후보 5개까지" in str(m.content) for m in stored)


def test_tool_failure_in_the_second_step_publishes_no_partial_chain(kpg, client, script, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("injected sensitivity engine failure")

    monkeypatch.setattr(agent_tools, "rank_generator_sensitivities", broken)
    script(call(LINE, {"outage_line_id": OUTAGE}), call(SENS, {"outage_line_id": OUTAGE}, "call-2"))
    envelope = post(client, run_body("security_then_sensitivity")).json()
    assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == ("failed", "TOOL_FAILED")
    assert envelope["ui_updates"] == [] and len(web_app.analysis_results) == 0
    assert "injected" not in envelope["answer"]


# --- model state and the publish boundary ------------------------------------------

def test_first_ac_run_is_refused_and_only_a_new_request_recomputes(client, script, monkeypatch, domain_calls):
    monkeypatch.setattr(web_app, "network", load_identified_network(FULL))  # fresh: no preparation
    model = script(call(LINE, {"outage_line_id": OUTAGE}), call(LINE, {"outage_line_id": OUTAGE}))
    first_body = run_body()
    first = post(client, first_body)
    analysis = first.json()["analysis"]
    assert (analysis["status"], analysis["reason_code"], analysis["results"]) == ("failed", "MODEL_REVISION_CHANGED", [])
    assert first.json()["ui_updates"] == [] and len(web_app.analysis_results) == 0
    assert "새 요청으로 다시 실행" in first.json()["answer"] and "자동으로 다시 계산하지 않았습니다" in first.json()["answer"]
    assert domain_calls["tool.run_line_contingency"] == 1 and len(model.calls) == 1
    assert post(client, first_body).content == first.content  # same request: replayed, not recomputed
    assert domain_calls["tool.run_line_contingency"] == 1 and len(model.calls) == 1
    second = post(client, run_body())  # an explicit new request computes in the current state
    assert second.json()["analysis"]["status"] == "completed" and domain_calls["tool.run_line_contingency"] == 2


def test_first_ac_policy_follows_actual_state_writes_not_run_order(client, script, monkeypatch):
    fresh = load_identified_network(FULL)
    monkeypatch.setattr(web_app, "network", fresh)
    initial = access_for(fresh).capture_revision().revision
    script(call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED}))
    envelope = post(client, run_body("sensitivity", monitored=MONITORED)).json()
    # An explicit-target sensitivity writes no state, so even a first run can be approved.
    assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == ("completed", None)
    assert envelope["analysis"]["results"][0]["model_revision"] == initial


class AfterInvoke:
    """Wraps the real request graph and acts after the last Tool, just before publish."""

    def __init__(self, graph, action):
        self.graph, self.action = graph, action

    def invoke(self, state, config=None):
        output = self.graph.invoke(state, config=config)
        self.action()
        return output


def test_model_change_just_before_registration_is_refused(kpg, client, script, monkeypatch):
    real = web_app.create_line_analysis_graph
    monkeypatch.setattr(web_app, "create_line_analysis_graph",
                        lambda *args, **kwargs: AfterInvoke(real(*args, **kwargs), lambda: change_load(kpg)))
    script(call(LINE, {"outage_line_id": OUTAGE}))
    envelope = post(client, run_body()).json()
    assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == ("failed", "MODEL_REVISION_CHANGED")
    assert envelope["ui_updates"] == [] and len(web_app.analysis_results) == 0


def test_registration_happens_inside_the_network_guard(kpg, client, script, monkeypatch):
    real, seen = web_app._register_published, []

    def probe(*args, **kwargs):
        def other_model_user():
            try:
                with network_access(kpg):
                    return "entered"
            except NetworkBusy:
                return "busy"

        with ThreadPoolExecutor(max_workers=1) as pool:
            seen.append(pool.submit(other_model_user).result(timeout=5))
        return real(*args, **kwargs)

    monkeypatch.setattr(web_app, "_register_published", probe)
    script(call(LINE, {"outage_line_id": OUTAGE}))
    assert post(client, run_body()).json()["analysis"]["status"] == "completed"
    assert seen == ["busy"]  # no model access can interleave between the revision check and registration


def test_chain_registration_is_all_or_nothing(kpg, client, script, monkeypatch):
    real = web_app._kpg_line_analysis_payload

    def unserializable_second(result):
        payload = real(result)
        return {**payload, "bad": float("nan")} if result.kind == "sensitivity" else payload

    monkeypatch.setattr(web_app, "_kpg_line_analysis_payload", unserializable_second)
    script(call(LINE, {"outage_line_id": OUTAGE}), call(SENS, {"outage_line_id": OUTAGE}, "call-2"))
    envelope = post(client, run_body("security_then_sensitivity")).json()
    assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == ("failed", "RESULT_NOT_REGISTERED")
    assert envelope["ui_updates"] == [] and envelope["analysis"]["results"] == []
    assert len(web_app.analysis_results) == 0  # the valid security result was not stored alone


@pytest.mark.parametrize("where", ["during_tool", "at_publish"])
def test_engine_contention_is_an_explicit_busy_failure(kpg, client, script, monkeypatch, domain_calls, where):
    ready, release = threading.Event(), threading.Event()

    def hold():
        with network_access(kpg):
            ready.set()
            assert release.wait(10)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        if where == "during_tool":
            holder = pool.submit(hold)
            assert ready.wait(5)
        else:
            real = web_app.create_line_analysis_graph
            holders = []

            def start_holder():
                holders.append(pool.submit(hold))
                assert ready.wait(5)

            monkeypatch.setattr(web_app, "create_line_analysis_graph",
                                lambda *args, **kwargs: AfterInvoke(real(*args, **kwargs), start_holder))
        script(call(LINE, {"outage_line_id": OUTAGE}))
        envelope = post(client, run_body()).json()
    finally:
        release.set()
        pool.shutdown(wait=True)
    assert (envelope["analysis"]["status"], envelope["analysis"]["reason_code"]) == ("failed", "ANALYSIS_BUSY")
    assert envelope["ui_updates"] == [] and len(web_app.analysis_results) == 0
    assert domain_calls["tool.run_line_contingency"] == (0 if where == "during_tool" else 1)


# --- sessions, duplicates, cancellation ----------------------------------------------

def test_legacy_chat_and_run_never_overlap_on_one_session(kpg, client, script, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class BlockingLegacy(FixedGraph):
        def invoke(self, state):
            entered.set()
            assert release.wait(10)
            return super().invoke(state)

    monkeypatch.setattr(web_app, "agent_graph", BlockingLegacy())
    monkeypatch.setattr(web_app, "mongolia_agent_graph", FixedGraph("몽골 일반 답변"))
    script(call(LINE, {"outage_line_id": OUTAGE}))
    with ThreadPoolExecutor(max_workers=1) as pool:
        legacy = pool.submit(TestClient(web_app.app).post, "/api/chat", json={"session_id": SESSION, "message": "안녕"})
        try:
            assert entered.wait(5)
            v1_error(post(client, run_body()), 409, "SESSION_BUSY")
            other = post(client, run_body(session="console-other-session"))
            assert other.status_code == 200 and other.json()["analysis"]["status"] == "completed"
            mn = client.post(f"/api/grids/{MN}/chat", json={"session_id": SESSION, "message": "계통 요약"})
            assert mn.status_code == 200 and mn.json()["grid_id"] == MN  # another grid's gate
        finally:
            release.set()
        assert legacy.result(timeout=20).status_code == 200

    entered.clear(), release.clear()

    def slow(messages):
        entered.set()
        assert release.wait(10)
        return call(LINE, {"outage_line_id": OUTAGE})

    script(slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        run = pool.submit(TestClient(web_app.app).post, f"/api/grids/{KPG}/chat", json=run_body())
        try:
            assert entered.wait(5)
            busy = client.post("/api/chat", json={"session_id": SESSION, "message": "안녕"})
            assert busy.status_code == 409 and busy.json() == {"detail": "SESSION_BUSY"}
            spaced = client.post("/api/chat", json={"session_id": f"  {SESSION} ", "message": "안녕"})
            assert spaced.status_code == 409  # legacy IDs keep their stripped legacy form
        finally:
            release.set()
        assert run.result(timeout=20).json()["analysis"]["status"] == "completed"


def test_runs_on_different_sessions_proceed_independently(kpg, client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(messages):
        entered.set()
        assert release.wait(10)
        return call(LINE, {"outage_line_id": OUTAGE})

    models = [ScriptedModel(steps=[slow]), ScriptedModel(steps=[call(LINE, {"outage_line_id": MULTI})])]
    monkeypatch.setattr(web_app, "_analysis_run_model", lambda budget: models.pop(0))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(TestClient(web_app.app).post, f"/api/grids/{KPG}/chat",
                            json=run_body(session="console-session-a"))
        try:
            assert entered.wait(5)  # session A waits on its model; it holds no engine lock
            second = post(client, run_body(outage=MULTI, session="console-session-b"))
        finally:
            release.set()
        first = first.result(timeout=20)
    assert first.status_code == second.status_code == 200
    a, b = first.json()["analysis"]["results"][0], second.json()["analysis"]["results"][0]
    assert (a["outage_line_id"], b["outage_line_id"]) == (OUTAGE, MULTI)
    assert [web_app.analysis_results.lookup(r["result_id"]).session_id for r in (a, b)] == [
        "console-session-a", "console-session-b"]
    assert set(web_app.agent_sessions) == {"console-session-a", "console-session-b"}


def test_mongolia_legacy_chat_is_gated_per_grid_only_under_concurrency(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class BlockingMongolia(FixedGraph):
        def invoke(self, state):
            entered.set()
            assert release.wait(10)
            return super().invoke(state)

    mongolia = BlockingMongolia("몽골 일반 답변")
    monkeypatch.setattr(web_app, "mongolia_agent_graph", mongolia)
    monkeypatch.setattr(web_app, "agent_graph", FixedGraph())
    body = {"session_id": SESSION, "message": "계통 요약"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(TestClient(web_app.app).post, f"/api/grids/{MN}/chat", json=body)
        try:
            assert entered.wait(5)
            busy = client.post(f"/api/grids/{MN}/chat", json=body)
            assert busy.status_code == 409 and busy.json() == {"detail": "SESSION_BUSY"}
            assert client.post("/api/chat", json=body).status_code == 200  # KPG: another grid's gate
        finally:
            release.set()
        first = pending.result(timeout=20)
    second = client.post(f"/api/grids/{MN}/chat", json=body)
    assert first.status_code == second.status_code == 200 and list(first.json()) == list(second.json())
    assert mongolia.calls == 2 and len(web_app.mongolia_agent_sessions[SESSION]) == 5  # system + 2 turns


def test_sequential_legacy_chat_is_unchanged_and_sees_run_history(kpg, client, script, monkeypatch):
    legacy = FixedGraph()
    monkeypatch.setattr(web_app, "agent_graph", legacy)
    first = client.post("/api/chat", json={"session_id": SESSION, "message": "안녕"})
    assert first.status_code == 200 and set(first.json()) == {"session_id", "answer", "ui_updates"}
    script(call(LINE, {"outage_line_id": OUTAGE}))
    run = post(client, run_body()).json()
    second = client.post("/api/grids/kr-kpg193/chat", json={"session_id": SESSION, "message": "방금 결과 요약"})
    assert set(second.json()) == {"session_id", "answer", "ui_updates", "grid_id"} and legacy.calls == 2
    tools = [m for m in legacy.seen if isinstance(m, ToolMessage)]
    assert len(tools) == 1 and tools[0].artifact is None and tools[0].name == LINE
    assert any(isinstance(m, AIMessage) and m.content == run["answer"] for m in legacy.seen)


def test_history_tool_results_are_never_run_evidence(kpg, client, script):
    script(call(LINE, {"outage_line_id": OUTAGE}))
    assert post(client, run_body()).json()["analysis"]["status"] == "completed"
    model = script(AIMessage(content='{"post_status": "CONVERGED", "verified": true}'))
    later = post(client, run_body()).json()
    assert (later["analysis"]["status"], later["analysis"]["results"]) == ("not_run", [])
    assert any(isinstance(m, ToolMessage) for m in model.calls[0])  # history was context only
    assert len(web_app.analysis_results) == 1


def test_duplicate_requests_replay_without_rerun_and_fail_closed_when_stale(
        kpg, client, script, domain_calls, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(messages):
        entered.set()
        assert release.wait(10)
        return call(LINE, {"outage_line_id": OUTAGE})

    model = script(slow)
    body = run_body()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(TestClient(web_app.app).post, f"/api/grids/{KPG}/chat", json=body)
        try:
            assert entered.wait(5)
            v1_error(post(client, body), 409, "REQUEST_IN_PROGRESS")
            v1_error(post(client, {**body, "message": "다른 요청"}), 409, "REQUEST_ID_REUSE")
            v1_error(post(client, run_body()), 409, "SESSION_BUSY")
        finally:
            release.set()
        first = pending.result(timeout=20)
    assert first.json()["analysis"]["status"] == "completed"
    replay = post(client, body)
    assert replay.content == first.content and len(model.calls) == 1
    assert domain_calls["tool.run_line_contingency"] == 1
    change_load(kpg)
    v1_error(post(client, body), 409, "REQUEST_REPLAY_STALE")
    assert len(model.calls) == 1 and domain_calls["tool.run_line_contingency"] == 1


def test_reference_replay_is_also_fail_closed_after_a_model_change(kpg, client, script):
    script(call(LINE, {"outage_line_id": OUTAGE}))
    receipt = post(client, run_body()).json()["analysis"]["results"][0]
    reference = {**run_body(), "analysis_context": {**run_body()["analysis_context"], "mode": "reference",
                                                     "references": {"security": receipt["result_id"],
                                                                    "sensitivity": None}}}
    first = post(client, reference)
    assert first.json()["analysis"]["status"] == "referenced" and first.json()["analysis"]["results"] == [receipt]
    assert post(client, reference).content == first.content
    change_load(kpg)
    v1_error(post(client, reference), 409, "REQUEST_REPLAY_STALE")


def test_http_cancel_keeps_the_gate_until_the_run_itself_finishes(kpg, script, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = web_app._execute_run

    def tracked(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        finally:
            finished.set()

    def slow(messages):
        entered.set()
        assert release.wait(10)
        return call(LINE, {"outage_line_id": OUTAGE})

    monkeypatch.setattr(web_app, "_execute_run", tracked)
    script(slow, call(LINE, {"outage_line_id": OUTAGE}))

    async def scenario():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(client.post(f"/api/grids/{KPG}/chat", json=run_body()))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert task.cancelling() > 0 and not finished.is_set()
                busy = await client.post(f"/api/grids/{KPG}/chat", json=run_body())
                assert busy.status_code == 409 and busy.json()["detail"]["code"] == "SESSION_BUSY"
                legacy = await client.post("/api/chat", json={"session_id": SESSION, "message": "안녕"})
                assert legacy.status_code == 409 and legacy.json() == {"detail": "SESSION_BUSY"}
            finally:
                release.set()
                if not task.done():
                    task.cancel()
            assert await asyncio.to_thread(finished.wait, 10)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
            # The cancelled HTTP request's run still finished and published on the server.
            assert len(web_app.analysis_results) == 1
            deadline = time.monotonic() + 5
            while True:
                after = await client.post(f"/api/grids/{KPG}/chat", json=run_body())
                if after.status_code != 409 or time.monotonic() > deadline:
                    break
                await asyncio.sleep(0.02)
            assert after.status_code == 200 and after.json()["analysis"]["status"] == "completed"

    asyncio.run(asyncio.wait_for(scenario(), 30))


# --- budgets, provider timeouts, single worker, isolation --------------------------------

@pytest.mark.parametrize("case,code,security_calls", [
    ("provider_timeout", "AGENT_TIMEOUT", 0), ("provider_refused", "AGENT_UNAVAILABLE", 0),
    ("provider_bad_gateway", "AGENT_UNAVAILABLE", 0), ("internal_error", "AGENT_FAILED", 0),
    ("deadline_between_steps", "AGENT_BUDGET_EXCEEDED", 1), ("step_budget", "AGENT_BUDGET_EXCEEDED", 0),
])
def test_agent_failures_publish_nothing_and_release_the_session(
        kpg, client, script, monkeypatch, domain_calls, case, code, security_calls):
    operation = "security"
    if case == "provider_timeout":
        script(httpx.ReadTimeout("read timed out at upstream"))
    elif case == "provider_refused":
        script(httpx.ConnectError("connection refused by upstream"))
    elif case == "provider_bad_gateway":
        script(ollama.ResponseError("bad gateway from upstream", 502))
    elif case == "internal_error":
        script(RuntimeError("unexpected upstream state"))
    elif case == "deadline_between_steps":
        operation = "security_then_sensitivity"
        script(call(LINE, {"outage_line_id": OUTAGE}), call(SENS, {"outage_line_id": OUTAGE}, "call-2"))
        ticks = iter([0.0, 0.0, 10_000.0])
        monkeypatch.setattr(web_app, "_run_clock", lambda: next(ticks))
    else:
        script(call(LINE, {"outage_line_id": OUTAGE}))
        monkeypatch.setattr(web_app, "run_budget", lambda: analysis_run.RunBudget(httpx.Timeout(1.0), 30.0, 2))
    body = run_body(operation)
    detail = v1_error(post(client, body), 500, code)
    assert "upstream" not in detail["message"] and detail["request_id"] == body["request_id"]
    assert domain_calls["tool.run_line_contingency"] == security_calls
    assert len(web_app.analysis_results) == 0 and web_app.agent_sessions == {}
    monkeypatch.setattr(web_app, "_run_clock", time.monotonic)
    monkeypatch.setattr(web_app, "run_budget", analysis_run.run_budget)
    script(*steps_for(operation, OUTAGE, None))
    retry = post(client, body)  # nothing was stored: the gate and the request ID are free
    assert retry.status_code == 200 and retry.json()["analysis"]["status"] == "completed"


def test_run_budget_and_provider_timeouts_are_explicit(kpg, client, script, monkeypatch):
    budget = analysis_run.run_budget({})
    assert budget.llm_timeout == httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
    assert (budget.deadline_seconds, budget.recursion_limit) == (30.0, 12)
    custom = analysis_run.run_budget({"AI_EMS_RUN_LLM_READ_TIMEOUT": "20", "AI_EMS_RUN_DEADLINE_SECONDS": "25"})
    assert custom.llm_timeout.read == 20.0 and custom.deadline_seconds == 25.0
    for bad in ["0", "-1", "nan", "inf", "abc", "601"]:
        with pytest.raises(ValueError):
            analysis_run.run_budget({"AI_EMS_RUN_LLM_READ_TIMEOUT": bad})
    provider = analysis_run.run_chat_model(budget)  # builds clients only; sends no request
    assert provider._client._client.timeout == budget.llm_timeout
    assert graph_module._chat_model(None, None)._client._client.timeout == httpx.Timeout(None)  # legacy unchanged
    model = script(call(LINE, {"outage_line_id": OUTAGE}))
    monkeypatch.setenv("AI_EMS_RUN_DEADLINE_SECONDS", "0")
    v1_error(post(client, run_body()), 409, "ANALYSIS_RUN_NOT_READY")
    assert len(model.calls) == 0


@pytest.mark.parametrize("marker", [None, "2", "true"])
def test_run_requires_the_single_worker_assertion(kpg, client, script, monkeypatch, isolated, marker):
    model = script(call(LINE, {"outage_line_id": OUTAGE}))
    if marker is None:
        monkeypatch.delenv(analysis_run.SINGLE_WORKER_ENV)
    else:
        monkeypatch.setenv(analysis_run.SINGLE_WORKER_ENV, marker)
    v1_error(post(client, run_body()), 409, "ANALYSIS_RUN_NOT_READY")
    assert len(model.calls) == 0 and isolated["kpg"].calls == 0 and web_app.agent_sessions == {}


def test_launcher_pins_one_worker_and_rejects_other_concurrency(tmp_path, monkeypatch, capsys):
    launched = []

    class Child:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return Child()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    for name in ("WEB_CONCURRENCY", "AI_EMS_CADDY_AUTOSTART", "AI_EMS_WEB_PORT", analysis_run.SINGLE_WORKER_ENV):
        monkeypatch.delenv(name, raising=False)
    assert launcher.run_app(tmp_path) == 0
    command, kwargs = launched[-1]
    assert command[:4] == [sys.executable, "-m", "uvicorn", "web_app:app"]
    assert command[command.index("--workers") + 1] == "1"
    assert kwargs["env"][analysis_run.SINGLE_WORKER_ENV] == "1"
    for value in ["4", "0", "two", ""]:
        monkeypatch.setenv("WEB_CONCURRENCY", value)
        assert launcher.run_app(tmp_path) == 2
    assert len(launched) == 1 and "단일 Uvicorn worker" in capsys.readouterr().err
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert launcher.run_app(tmp_path) == 0 and len(launched) == 2
    monkeypatch.delenv("WEB_CONCURRENCY")
    (tmp_path / ".env").write_text("WEB_CONCURRENCY=3\n")
    assert launcher.run_app(tmp_path) == 2 and len(launched) == 2
    assert launcher.run_app(tmp_path, [sys.executable, "-c", "pass"]) == 0
    assert launched[-1][1]["env"] is None  # custom commands keep the inherited environment


def test_runs_are_kpg_only_and_never_reach_other_agents(client, script, isolated):
    model = script(call(LINE, {"outage_line_id": OUTAGE}))
    v1_error(post(client, run_body(), grid=MN), 409, "ANALYSIS_CONTEXT_UNSUPPORTED")
    v1_error(post(client, run_body(), grid=KR), 409, "ANALYSIS_CONTEXT_UNSUPPORTED")
    assert len(model.calls) == 0 and isolated["mn"].calls == isolated["kpg"].calls == 0
    assert web_app.mongolia_agent_sessions == {} and web_app.agent_sessions == {}
    grids = client.get("/api/grids").json()["grids"]
    assert all("console_analysis_context_versions" not in grid for grid in grids)


def test_run_receipts_are_referenced_like_manual_ones(kpg, client, script):
    script(call(LINE, {"outage_line_id": OUTAGE}), call(SENS, {"outage_line_id": OUTAGE}, "call-2"))
    run = post(client, run_body("security_then_sensitivity")).json()
    security, sensitivity = run["analysis"]["results"]
    reference = run_body("security_then_sensitivity")
    reference["analysis_context"].update(mode="reference", references={
        "security": security["result_id"], "sensitivity": sensitivity["result_id"]})
    referenced = post(client, reference).json()
    assert referenced["analysis"]["status"] == "referenced"
    assert referenced["analysis"]["results"] == [security, sensitivity]
    assert all(r["producer"] == "agent_tool" for r in referenced["analysis"]["results"])
