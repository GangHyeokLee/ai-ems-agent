"""PH4-02B2: KPG full artifacts, per-request execution ledgers and evidence checks.

Only the model is scripted. Every v1 Tool performs the actual KPG domain
calculation on a freshly loaded, identified full KPG model; no LLM or Chrome.
"""
import dataclasses
import faulthandler
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import web_app
from ai_ems import analysis_context as ac
from ai_ems.agent import tools as agent_tools
from ai_ems.agent.graph import SYSTEM_PROMPT, create_line_analysis_graph
from ai_ems.agent.mongolia_tools import create_mongolia_tools
from ai_ems.network import load_identified_network, load_network
from ai_ems.network_access import ModelIdentityUnavailable, NetworkBusy, access_for, network_access
from ai_ems.tools.security_tools import run_line_contingency as domain_security

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "data/KPG193_ver2_0_powsybl_full.mat"
KPG = "kr-kpg193"
LINE, SENS = "line_contingency", "generator_sensitivity"
OUTAGE, MONITORED = "LINE-16-28", "LINE-16-22"
MULTI = "LINE-58-59"         # actual N-1: 12 overloaded lines
VOLTAGE = "LINE-80-82"       # actual N-1: overloads and LOW_VOLTAGE violations
NO_OVERLOAD = "LINE-1-2"     # actual N-1: converged without violations
NONCONVERGED = "LINE-28-80"  # actual N-1: post-contingency MAX_ITERATION_REACHED
MN_SECURITY, MN_OUTAGE = "run_mongolia_line_contingency", "751261406-1_0"


@pytest.fixture(autouse=True)
def bounded_test():
    # A native call or deadlocked worker cannot extend a test indefinitely.
    faulthandler.dump_traceback_later(45, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


class ScriptedModel(BaseChatModel):
    """Replaces only the LLM; steps are messages or callables of the input."""

    steps: list
    calls: list = Field(default_factory=list)
    bound: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-kpg-v1"

    def bind_tools(self, tools, **kwargs):
        self.bound = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        step = self.steps.pop(0)
        if callable(step):
            step = step(messages)
        return ChatResult(generations=[ChatGeneration(message=step)])


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def job(operation, outage=OUTAGE, monitored=None):
    return ac.AnalysisJob(KPG, operation, outage, monitored)


def current_revision(model):
    try:
        return access_for(model).capture_revision().revision
    except (ModelIdentityUnavailable, NetworkBusy):
        return None


def run_job(model, analysis_job, steps):
    ledger = ac.ExecutionLedger(analysis_job)
    scripted = ScriptedModel(steps=list(steps))
    graph = create_line_analysis_graph(model, ledger, model=scripted)
    inputs = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="선택한 선로 분석을 실행해 줘.")]
    generated = graph.invoke({"messages": inputs})["messages"][len(inputs):]
    verification = ac.verify_evidence(ledger, generated, current_revision=current_revision(model))
    return SimpleNamespace(ledger=ledger, model=scripted, generated=generated, verification=verification)


def jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def prepared():
    # Explicit preparation OUTSIDE the v1 path, as a prior manual N-1 would do.
    model = load_identified_network(FULL)
    domain_security(model, OUTAGE)
    return model


@pytest.fixture
def kpg():
    return prepared()


@pytest.fixture
def domain_calls(monkeypatch):
    counts = Counter()
    for name, kind in [("run_line_contingency", "security"), ("rank_generator_sensitivities", "sensitivity")]:
        real = getattr(agent_tools, name)

        def spy(*args, _real=real, _kind=kind, **kwargs):
            counts[_kind] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(agent_tools, name, spy)
    return counts


@pytest.fixture
def engine_calls(monkeypatch):
    counts = Counter()
    for module, name, kind in [(pp.loadflow, "run_ac", "loadflow"), (pp.security, "create_analysis", "security"),
                               (pp.sensitivity, "create_ac_analysis", "sensitivity")]:
        real = getattr(module, name)

        def spy(*args, _real=real, _kind=kind, **kwargs):
            counts[_kind] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, spy)
    return counts


@pytest.mark.parametrize("kwargs,code", [
    (dict(grid_id=KPG, operation="redispatch", outage_line_id=OUTAGE), "UNSUPPORTED_OPERATION"),
    (dict(grid_id=KPG, operation="security", outage_line_id=""), "INVALID_OUTAGE_LINE_ID"),
    (dict(grid_id=KPG, operation="security", outage_line_id=" LINE-16-28"), "INVALID_OUTAGE_LINE_ID"),
    (dict(grid_id=KPG, operation="security", outage_line_id="L" * 129), "INVALID_OUTAGE_LINE_ID"),
    (dict(grid_id=KPG, operation="security", outage_line_id="LINE\n16-28"), "INVALID_OUTAGE_LINE_ID"),
    (dict(grid_id=KPG, operation="security", outage_line_id=None), "INVALID_OUTAGE_LINE_ID"),
    (dict(grid_id=KPG, operation="security", outage_line_id=OUTAGE, monitored_line_id=MONITORED),
     "MONITORED_LINE_NOT_ALLOWED"),
    (dict(grid_id=KPG, operation="sensitivity", outage_line_id=OUTAGE, monitored_line_id=OUTAGE),
     "INVALID_MONITORED_LINE_ID"),
    (dict(grid_id=KPG, operation="sensitivity", outage_line_id=OUTAGE, monitored_line_id=""),
     "INVALID_MONITORED_LINE_ID"),
    (dict(grid_id="mn-osm-35k", operation="security", outage_line_id=MN_OUTAGE), "UNSUPPORTED_GRID"),
    (dict(grid_id="kr-osm-transmission", operation="security", outage_line_id=OUTAGE), "UNSUPPORTED_GRID"),
])
def test_job_conditions_are_validated_and_frozen(kwargs, code):
    with pytest.raises(ValueError, match=code):
        ac.AnalysisJob(**kwargs)
    frozen = job("security_then_sensitivity")
    assert frozen.plan == ("security", "sensitivity") and frozen.tools == {LINE: "security", SENS: "sensitivity"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.outage_line_id = MULTI


@pytest.mark.parametrize("outage,expected_count", [(MULTI, 12), (VOLTAGE, 36)])
def test_one_security_calculation_yields_legacy_content_and_full_artifact(
        kpg, domain_calls, engine_calls, outage, expected_count):
    engine_calls.clear()
    run = run_job(kpg, job("security", outage), [call(LINE, {"outage_line_id": outage})])
    assert run.verification.status == "completed" and run.verification.reason_code is None
    assert dict(domain_calls) == {"security": 1}
    assert dict(engine_calls) == {"loadflow": 1, "security": 1}
    (result,) = run.verification.results
    ai, tool = run.generated
    raw = result.artifact()["result"]
    assert tool.artifact == result.artifact() and tool.status == "success"
    assert json.loads(tool.content) == jsonable(agent_tools.summarize_line_contingency(raw))
    canonical = web_app._kpg_line_analysis_payload(result)
    assert canonical == web_app._security_result_for_ui(raw)
    assert canonical["violated_equipment_count"] == len(canonical["violations"]) == expected_count
    assert raw["violated_equipment_count"] == len(raw["violated_equipment"]) == expected_count
    assert [v["equipment_id"] for v in canonical["violations"]] == [v["equipment_id"] for v in raw["violated_equipment"]]
    if outage == VOLTAGE:
        voltage = [v for v in canonical["violations"] if v["limit_type"] == "LOW_VOLTAGE"]
        assert voltage and all(v["unit"] == "kV" and v["violation_direction"] == "below_minimum" for v in voltage)
    # The legacy chat projection keeps one representative item: why B2 seals the full result.
    legacy_view = web_app._agent_security_result_for_ui(json.loads(tool.content))
    assert legacy_view["violated_equipment_count"] == expected_count and len(legacy_view["violations"]) == 1
    assert dict(engine_calls) == {"loadflow": 1, "security": 1}  # projections computed nothing
    assert (result.outage_line_id, result.monitored_line_id, result.top_n) == (outage, None, None)


REST_CASES = [
    ("security", OUTAGE, None, "/api/security-analysis", "completed", None),
    ("security", MULTI, None, "/api/security-analysis", "completed", None),
    ("security", NONCONVERGED, None, "/api/security-analysis", "not_evaluated", "SECURITY_NOT_CONVERGED"),
    ("sensitivity", OUTAGE, None, "/api/sensitivity-analysis", "completed", None),
    ("sensitivity", OUTAGE, MONITORED, "/api/sensitivity-analysis", "completed", None),
    ("sensitivity", NO_OVERLOAD, None, "/api/sensitivity-analysis", "not_evaluated", "NO_OVERLOAD"),
    ("sensitivity", NONCONVERGED, None, "/api/sensitivity-analysis", "not_evaluated", "SECURITY_NOT_CONVERGED"),
]


def test_v1_canonical_payloads_equal_rest_on_the_same_model(monkeypatch):
    model = load_identified_network(FULL)
    monkeypatch.setattr(web_app, "network", model)
    client = TestClient(web_app.app)
    assert client.get("/api/security-analysis", params={"outage_line_id": OUTAGE}).status_code == 200
    for operation, outage, monitored, path, status, reason in REST_CASES:
        name = LINE if operation == "security" else SENS
        args = {"outage_line_id": outage, **({"monitored_line_id": monitored} if monitored else {})}
        run = run_job(model, job(operation, outage, monitored), [call(name, args)])
        assert (run.verification.status, run.verification.reason_code) == (status, reason), operation
        (result,) = run.verification.results
        rest = client.get(path, params=args)
        assert rest.status_code == 200, rest.text
        assert jsonable(web_app._kpg_line_analysis_payload(result)) == rest.json(), (operation, outage)
        if operation == "sensitivity":
            raw = result.artifact()["result"]
            assert result.top_n == 5 and result.monitored_selection == ("explicit" if monitored else "auto")
            if raw["ranking"] is None:
                assert result.monitored_line_id is None and raw["generator_metadata"] == {}
                assert json.loads(run.generated[-1].content)["candidates"] == []
            else:
                assert result.monitored_line_id == MONITORED
                assert set(raw["generator_metadata"]) == {c["generator_id"] for c in raw["ranking"]["candidates"]}
                assert (raw["selection"]["security"] is None) == bool(monitored)
                if not monitored:
                    assert raw["selection"]["selected"]["equipment_id"] == MONITORED


def test_chain_runs_sensitivity_only_after_the_actual_security_result(kpg, domain_calls):
    seen = {}

    def engine_lock_free():
        with network_access(kpg):
            return True

    def second_turn(messages):
        seen["security"] = [m for m in messages if isinstance(m, ToolMessage)][-1]
        seen["system_messages"] = sum(isinstance(m, SystemMessage) for m in messages)
        # While the model "waits", another thread can use the engine: no lock is held.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(engine_lock_free).result(timeout=5) is True
        return call(SENS, {"outage_line_id": OUTAGE, "top_n": 5}, "call-2")

    run = run_job(kpg, job("security_then_sensitivity"), [call(LINE, {"outage_line_id": OUTAGE}), second_turn])
    assert [type(m).__name__ for m in run.generated] == ["AIMessage", "ToolMessage", "AIMessage", "ToolMessage"]
    assert seen["security"].tool_call_id == "call-1" and seen["security"].status == "success"
    assert run.verification.status == "completed"
    security, sensitivity = run.verification.results
    assert (security.kind, sensitivity.kind) == ("security", "sensitivity")
    assert security.model_revision == sensitivity.model_revision == current_revision(kpg)
    assert (sensitivity.monitored_line_id, sensitivity.monitored_selection) == (MONITORED, "auto")
    # Automatic target selection reuses the REST-equivalent N-1 inside the Sensitivity transaction.
    assert dict(domain_calls) == {"security": 2, "sensitivity": 1}
    assert [tool.name for tool in run.model.bound] == [LINE, SENS]
    first_context, second_context = run.model.calls[0][-1], run.model.calls[1][-1]
    assert isinstance(first_context, SystemMessage) and f"{LINE} with arguments" in first_context.content
    assert f"{SENS} with arguments" in second_context.content and "Omit monitored_line_id" in second_context.content
    assert seen["system_messages"] == 2  # leading prompt + this turn's transient context only
    assert not any(isinstance(m, SystemMessage) for m in run.generated)


def test_chain_ends_after_an_actual_nonconverged_security_result(kpg, domain_calls):
    run = run_job(kpg, job("security_then_sensitivity", NONCONVERGED), [call(LINE, {"outage_line_id": NONCONVERGED})])
    assert len(run.model.calls) == 1  # the server ended the chain; Sensitivity was never requested
    assert dict(domain_calls) == {"security": 1}
    assert (run.verification.status, run.verification.reason_code) == ("not_evaluated", "SECURITY_NOT_CONVERGED")
    (security,) = run.verification.results
    canonical = web_app._kpg_line_analysis_payload(security)
    assert canonical["analysis_status"] == "SECURITY_NOT_CONVERGED"
    assert canonical["violation_assessment_available"] is False


@pytest.mark.parametrize("variant", ["text_only_second_turn", "sensitivity_tool_failure", "invalid_second_call"])
def test_partial_chain_never_publishes_the_completed_security(kpg, domain_calls, monkeypatch, variant):
    second = {
        "text_only_second_turn": AIMessage(content="민감도 후보 5개까지 분석 완료"),
        "sensitivity_tool_failure": call(SENS, {"outage_line_id": OUTAGE}, "call-2"),
        "invalid_second_call": call(SENS, {"outage_line_id": MULTI}, "call-2"),
    }[variant]
    if variant == "sensitivity_tool_failure":
        def broken(*args, **kwargs):
            raise RuntimeError("injected sensitivity engine failure")
        monkeypatch.setattr(agent_tools, "rank_generator_sensitivities", broken)
    run = run_job(kpg, job("security_then_sensitivity"), [call(LINE, {"outage_line_id": OUTAGE}), second])
    expected = {"text_only_second_turn": "CHAIN_INCOMPLETE", "sensitivity_tool_failure": "TOOL_FAILED",
                "invalid_second_call": "OUTAGE_LINE_MISMATCH"}[variant]
    assert (run.verification.status, run.verification.reason_code, run.verification.results) == ("failed", expected, ())
    records = run.ledger.snapshot().records
    assert records[0].kind == "security" and records[0].status == "succeeded"
    assert domain_calls["sensitivity"] == 0 or variant == "sensitivity_tool_failure"


def test_first_ac_revision_change_is_never_attributed_or_rerun(domain_calls):
    fresh = load_identified_network(FULL)
    initial = access_for(fresh).capture_revision()
    first = run_job(fresh, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    (record,) = first.ledger.snapshot().records
    assert record.status == "succeeded" and record.revision_before == initial.revision != record.revision_after
    revisions = record.artifact()["model_revision"]
    assert revisions["before"]["initial_state"] is True and revisions["after"]["initial_state"] is False
    assert (first.verification.status, first.verification.reason_code) == ("failed", "MODEL_REVISION_CHANGED")
    assert first.verification.results == () and dict(domain_calls) == {"security": 1}  # no hidden rerun
    second = run_job(fresh, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert second.verification.status == "completed"
    assert second.verification.results[0].model_revision == record.revision_after
    assert dict(domain_calls) == {"security": 2}
    chain_model = load_identified_network(FULL)
    chain = run_job(chain_model, job("security_then_sensitivity"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert len(chain.model.calls) == 1 and domain_calls["sensitivity"] == 0
    assert chain.verification.reason_code == "MODEL_REVISION_CHANGED"


def test_attribution_follows_the_actual_state_write_not_call_order():
    # Explicit-monitored Sensitivity performs no base AC: the loaded state stays the same.
    fresh = load_identified_network(FULL)
    run = run_job(fresh, job("sensitivity", OUTAGE, MONITORED),
                  [call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED})])
    assert run.verification.status == "completed"
    (record,) = run.ledger.snapshot().records
    assert record.revision_stable and record.artifact()["model_revision"]["before"]["initial_state"] is True
    # Automatic monitoring runs the N-1 base AC first, so the same first call is not attributable.
    auto = run_job(load_identified_network(FULL), job("sensitivity"), [call(SENS, {"outage_line_id": OUTAGE})])
    assert (auto.verification.status, auto.verification.reason_code) == ("failed", "MODEL_REVISION_CHANGED")


def test_model_changes_between_steps_or_after_execution_are_rejected(kpg):
    done = run_job(kpg, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert done.verification.status == "completed"

    def change_load():
        with network_access(kpg):
            row = kpg.get_loads().iloc[0]
            kpg.update_loads(id=row.name, p0=float(row.p0) + 1.0)

    change_load()
    later = ac.verify_evidence(done.ledger, done.generated, current_revision=current_revision(kpg))
    assert (later.status, later.reason_code, later.results) == ("failed", "MODEL_REVISION_CHANGED", ())
    domain_security(kpg, OUTAGE)  # explicit re-preparation of the changed model, outside v1

    def mutate_then_call(messages):
        change_load()  # e.g. another component writes the model during the LLM wait
        return call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED, "top_n": 5}, "call-2")

    chain = run_job(kpg, job("security_then_sensitivity", OUTAGE, MONITORED),
                    [call(LINE, {"outage_line_id": OUTAGE}), mutate_then_call])
    security, sensitivity = chain.ledger.snapshot().records
    assert security.revision_stable and sensitivity.revision_stable
    assert security.revision_after != sensitivity.revision_before
    assert (chain.verification.status, chain.verification.reason_code) == ("failed", "MODEL_REVISION_CHANGED")


def test_unidentified_model_fails_closed_before_any_calculation(domain_calls):
    legacy_model = load_network(FULL)  # legacy loader: no load provenance or revision
    run = run_job(legacy_model, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert dict(domain_calls) == {}
    tool = run.generated[-1]
    assert tool.status == "error" and tool.artifact is None
    (record,) = run.ledger.snapshot().records
    assert (record.status, record.reason_code) == ("failed", "MODEL_IDENTITY_UNAVAILABLE")
    assert (run.verification.status, run.verification.reason_code) == ("failed", "MODEL_IDENTITY_UNAVAILABLE")


def two_calls(first, second):
    return AIMessage(content="", tool_calls=[{"name": first, "args": {"outage_line_id": OUTAGE}, "id": "a"},
                                             {"name": second, "args": {"outage_line_id": OUTAGE}, "id": "b"}])


@pytest.mark.parametrize("operation,monitored,turn,code", [
    ("security", None, call(LINE, {"outage_line_id": MONITORED}), "OUTAGE_LINE_MISMATCH"),
    ("security", None, call(LINE, {"outage_line_id": " " + OUTAGE}), "OUTAGE_LINE_MISMATCH"),
    ("security", None, call(LINE, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED}),
     "MONITORED_LINE_NOT_ALLOWED"),
    ("security", None, call(LINE, {"outage_line_id": OUTAGE, "grid_id": KPG}), "UNEXPECTED_ARGUMENT"),
    ("security", None, call(LINE, {"outage_line_id": OUTAGE, "verified": True}), "UNEXPECTED_ARGUMENT"),
    ("sensitivity", MONITORED, call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": "LINE-16-17"}),
     "MONITORED_LINE_MISMATCH"),
    ("sensitivity", MONITORED, call(SENS, {"outage_line_id": OUTAGE}), "MONITORED_LINE_MISMATCH"),
    ("sensitivity", None, call(SENS, {"outage_line_id": OUTAGE, "monitored_line_id": MONITORED}),
     "AUTO_SELECTION_REPLACED"),
    ("sensitivity", None, call(SENS, {"outage_line_id": OUTAGE, "top_n": 3}), "TOP_N_NOT_ALLOWED"),
    ("sensitivity", None, call(SENS, {"outage_line_id": OUTAGE, "top_n": "5"}), "TOP_N_NOT_ALLOWED"),
    ("sensitivity", None, call(SENS, {"outage_line_id": OUTAGE, "top_n": True}), "TOP_N_NOT_ALLOWED"),
    ("security", None, call("network_summary", {}), "TOOL_NOT_ALLOWED"),
    ("security", None, call(SENS, {"outage_line_id": OUTAGE}), "TOOL_NOT_ALLOWED"),
    ("sensitivity", None, call(LINE, {"outage_line_id": OUTAGE}), "TOOL_NOT_ALLOWED"),
    ("security", None, call(MN_SECURITY, {"outage_line_id": MN_OUTAGE}), "TOOL_NOT_ALLOWED"),
    ("security_then_sensitivity", None, call(SENS, {"outage_line_id": OUTAGE}), "UNEXPECTED_TOOL_SEQUENCE"),
    ("security_then_sensitivity", None, two_calls(LINE, SENS), "MULTIPLE_TOOL_CALLS"),
    ("security", None, two_calls(LINE, LINE), "MULTIPLE_TOOL_CALLS"),
])
def test_guard_rejects_conflicting_calls_before_any_calculation(
        kpg, domain_calls, engine_calls, operation, monitored, turn, code):
    engine_calls.clear()
    run = run_job(kpg, job(operation, OUTAGE, monitored), [turn])
    assert dict(domain_calls) == {} and dict(engine_calls) == {}
    assert len(run.model.calls) == 1
    rejected = run.generated[1:]
    assert len(rejected) == len(turn.tool_calls)
    assert all(isinstance(m, ToolMessage) and m.status == "error" and m.artifact is None for m in rejected)
    snapshot = run.ledger.snapshot()
    assert snapshot.records == () and snapshot.rejection == code
    assert (run.verification.status, run.verification.reason_code, run.verification.results) == ("failed", code, ())


@pytest.fixture(scope="module")
def evidence():
    model = prepared()
    legit = run_job(model, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    other = run_job(model, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert legit.verification.status == other.verification.status == "completed"
    return SimpleNamespace(model=model, legit=legit, other=other)


def tampered(evidence, case):
    ai, tool = evidence.legit.generated
    fake = {"grid_id": KPG, "outage_line_id": OUTAGE, "post_status": "CONVERGED", "verified": True}
    changed = {**tool.artifact, "result": {**tool.artifact["result"], "violated_equipment_count": 0}}
    return {
        "unpaired_tool_message": [tool],
        "other_call_id": [ai, tool.model_copy(update={"tool_call_id": "other"})],
        "other_tool_name": [ai, tool.model_copy(update={"name": SENS})],
        "error_status": [ai, tool.model_copy(update={"status": "error"})],
        "artifact_changed": [ai, tool.model_copy(update={"artifact": changed})],
        "content_only": [ai, tool.model_copy(update={"artifact": None})],
        "verified_flag_payload": [ai, tool.model_copy(update={"content": json.dumps(fake), "artifact": fake})],
        "ai_arguments_changed": [call(LINE, {"outage_line_id": MULTI}), tool],
        "ai_name_changed": [call(SENS, {"outage_line_id": OUTAGE}), tool],
        "missing_tool_message": [ai],
        "duplicate_tool_message": [ai, tool, tool],
        "other_job_evidence": evidence.other.generated,
        "history_replay": [HumanMessage(content="이전 대화"), ai, tool],
    }[case]


@pytest.mark.parametrize("case,code", [
    ("unpaired_tool_message", "UNPAIRED_TOOL_MESSAGE"), ("other_call_id", "UNPAIRED_TOOL_MESSAGE"),
    ("other_tool_name", "TOOL_NAME_MISMATCH"), ("error_status", "TOOL_MESSAGE_ERROR"),
    ("artifact_changed", "ARTIFACT_MISMATCH"), ("content_only", "ARTIFACT_MISMATCH"),
    ("verified_flag_payload", "ARTIFACT_MISMATCH"), ("ai_arguments_changed", "TOOL_ARGUMENT_MISMATCH"),
    ("ai_name_changed", "TOOL_NAME_MISMATCH"), ("missing_tool_message", "TOOL_NOT_EXECUTED"),
    ("duplicate_tool_message", "DUPLICATE_TOOL_MESSAGE"), ("other_job_evidence", "ARTIFACT_MISMATCH"),
    ("history_replay", "UNEXPECTED_MESSAGE"),
])
def test_forged_mismatched_or_replayed_evidence_is_never_approved(evidence, case, code):
    revision = current_revision(evidence.model)
    assert ac.verify_evidence(evidence.legit.ledger, evidence.legit.generated,
                              current_revision=revision).status == "completed"
    result = ac.verify_evidence(evidence.legit.ledger, tampered(evidence, case), current_revision=revision)
    assert result.status == "failed" and result.results == () and code in result.issues, result
    # A message from this very run is still not evidence for a ledger that never executed it.
    empty = ac.ExecutionLedger(evidence.legit.ledger.job)
    replay = ac.verify_evidence(empty, evidence.legit.generated, current_revision=revision)
    assert replay.status == "failed" and "UNLEDGERED_TOOL_MESSAGE" in replay.issues


def test_model_text_without_a_tool_is_not_a_calculation(kpg, domain_calls):
    run = run_job(kpg, job("security"), [AIMessage(content='분석 성공 {"post_status": "CONVERGED", "verified": true}')])
    assert dict(domain_calls) == {}
    assert (run.verification.status, run.verification.reason_code, run.verification.results) == (
        "not_run", "TOOL_NOT_EXECUTED", ())


def test_v1_tool_bodies_execute_only_authorized_calls_exactly_once(kpg, domain_calls):
    ledger = ac.ExecutionLedger(job("security"))
    tools = {tool.name: tool for tool in agent_tools.create_line_analysis_tools(kpg, ledger)}

    def tool_call(call_id, args, name=LINE):
        return {"type": "tool_call", "id": call_id, "name": name, "args": args}

    with pytest.raises(ValueError, match="InjectedToolCallId"):
        tools[LINE].invoke({"outage_line_id": OUTAGE})
    with pytest.raises(ac.UnauthorizedExecution):
        tools[LINE].invoke(tool_call("forged", {"outage_line_id": OUTAGE}))
    ledger.authorize([{"name": LINE, "args": {"outage_line_id": OUTAGE}, "id": "ok"}])
    with pytest.raises(ac.UnauthorizedExecution):
        tools[LINE].invoke(tool_call("ok", {"outage_line_id": MULTI}))
    with pytest.raises(ac.UnauthorizedExecution):
        tools[SENS].invoke(tool_call("ok", {"outage_line_id": OUTAGE}, SENS))
    assert dict(domain_calls) == {}
    message = tools[LINE].invoke(tool_call("ok", {"outage_line_id": OUTAGE}))
    assert message.status == "success" and message.artifact["tool_call_id"] == "ok"
    with pytest.raises(ac.UnauthorizedExecution):
        tools[LINE].invoke(tool_call("ok", {"outage_line_id": OUTAGE}))
    assert dict(domain_calls) == {"security": 1}
    (record,) = ledger.snapshot().records
    assert record.status == "succeeded" and record.execution_id == message.artifact["execution_id"]


def test_tool_errors_and_engine_contention_are_failures(kpg, domain_calls):
    unknown = run_job(kpg, job("security", "NO-SUCH-LINE"), [call(LINE, {"outage_line_id": "NO-SUCH-LINE"})])
    assert unknown.generated[-1].status == "error"
    assert (unknown.verification.status, unknown.verification.reason_code) == ("failed", "TOOL_INPUT_REJECTED")
    ready, release = threading.Event(), threading.Event()

    def hold():
        with network_access(kpg):
            ready.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold)
        try:
            assert ready.wait(5)
            busy = run_job(kpg, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
        finally:
            release.set()
        holder.result(timeout=5)
    assert dict(domain_calls) == {"security": 1}  # only the unknown-line attempt reached the domain
    assert busy.ledger.snapshot().records[0].reason_code == "ANALYSIS_BUSY"
    assert (busy.verification.status, busy.verification.reason_code) == ("failed", "ANALYSIS_BUSY")


def test_engine_lock_not_ledger_lock_is_held_while_a_tool_runs(kpg, monkeypatch):
    monkeypatch.setattr(web_app, "network", kpg)
    entered, release = threading.Event(), threading.Event()
    real = agent_tools.run_line_contingency

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_tools, "run_line_contingency", slow)
    ledger = ac.ExecutionLedger(job("security"))
    graph = create_line_analysis_graph(kpg, ledger, model=ScriptedModel(steps=[call(LINE, {"outage_line_id": OUTAGE})]))
    inputs = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="N-1")]
    client = TestClient(web_app.app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(graph.invoke, {"messages": inputs})
        try:
            assert entered.wait(5)
            snapshot = pool.submit(ledger.snapshot).result(timeout=2)  # short ledger lock is free
            assert [record.status for record in snapshot.records] == ["started"]
            in_progress = ac.verify_evidence(ledger, [], current_revision=None)
            assert in_progress.status == "failed" and "LEDGER_WITHOUT_TOOL_MESSAGE" in in_progress.issues
            assert client.get("/api/network-summary").status_code == 409  # shared engine guard
        finally:
            release.set()
        generated = pending.result(timeout=20)["messages"][len(inputs):]
    verification = ac.verify_evidence(ledger, generated, current_revision=current_revision(kpg))
    assert verification.status == "completed"
    assert client.get("/api/network-summary").status_code == 200


def test_ledgers_are_request_scoped_and_global_agent_is_unchanged(kpg):
    graph_before, tools_before = web_app.agent_graph, dict(web_app.direct_agent_tools)
    first = run_job(kpg, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    second = run_job(kpg, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    a, b = first.verification.results[0], second.verification.results[0]
    assert a.job_id != b.job_id and a.execution_id != b.execution_id and a.artifact_sha256 != b.artifact_sha256
    assert a.artifact()["result"] == b.artifact()["result"] and a.model_revision == b.model_revision
    revision = current_revision(kpg)
    for ledger, foreign in [(first.ledger, second.generated), (second.ledger, first.generated)]:
        crossed = ac.verify_evidence(ledger, foreign, current_revision=revision)
        assert crossed.status == "failed" and crossed.results == ()
    with pytest.raises(RuntimeError, match="LEDGER_ALREADY_CLAIMED"):
        create_line_analysis_graph(kpg, first.ledger, model=ScriptedModel(steps=[]))
    assert web_app.agent_graph is graph_before and web_app.direct_agent_tools == tools_before
    assert all(tool.response_format == "content" for tool in web_app.direct_agent_tools.values())
    models = [prepared(), prepared()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda model: run_job(model, job("security", MULTI),
                                                   [call(LINE, {"outage_line_id": MULTI})]), models))
    assert [run.verification.status for run in runs] == ["completed", "completed"]
    assert runs[0].verification.results[0].model_revision != runs[1].verification.results[0].model_revision


def test_kpg_and_mongolia_evidence_do_not_mix(kpg):
    run = run_job(kpg, job("security"), [call(LINE, {"outage_line_id": OUTAGE})])
    assert run.verification.status == "completed"
    ai, tool = run.generated
    # Mongolia's existing evidence path ignores or rejects KPG v1 results.
    assert web_app._mongolia_analysis_evidence([ai, tool]) == ([], [])
    relabeled = [call(MN_SECURITY, {"outage_line_id": OUTAGE}), tool.model_copy(update={"name": MN_SECURITY})]
    updates, notes = web_app._mongolia_analysis_evidence(relabeled)
    assert updates == [] and "실패" in notes[0]
    # An actual Mongolia Tool result is never KPG ledger evidence.
    mn_tools = {t.name: t for t in create_mongolia_tools(web_app._load_validated_mongolia_pf,
                                                         web_app._mongolia_security)}
    mn_message = mn_tools[MN_SECURITY].invoke({"type": "tool_call", "id": "mn-1", "name": MN_SECURITY,
                                               "args": {"outage_line_id": MN_OUTAGE}})
    assert mn_message.status == "success" and mn_message.artifact["grid_id"] == "mn-osm-35k"
    assert mn_message.artifact["post_status"] == "CONVERGED" and not mn_message.artifact.get("error")
    revision = current_revision(kpg)
    for messages in ([call(MN_SECURITY, {"outage_line_id": MN_OUTAGE}, "mn-1"), mn_message],
                     [ai, mn_message.model_copy(update={"tool_call_id": tool.tool_call_id, "name": LINE})]):
        mixed = ac.verify_evidence(run.ledger, messages, current_revision=revision)
        assert mixed.status == "failed" and mixed.results == ()


def test_legacy_tools_keep_schema_return_and_error_contracts(kpg):
    legacy = {tool.name: tool for tool in agent_tools.create_agent_tools(kpg)}
    v1 = {tool.name: tool for tool in agent_tools.create_line_analysis_tools(
        kpg, ac.ExecutionLedger(job("security_then_sensitivity")))}
    assert set(v1) == {LINE, SENS}
    for name, tool in v1.items():
        assert legacy[name].response_format == "content" and tool.response_format == "content_and_artifact"
        assert tool.tool_call_schema.model_json_schema() == legacy[name].tool_call_schema.model_json_schema()
        assert tool.description == legacy[name].description
    raw = domain_security(kpg, MULTI)
    assert legacy[LINE].invoke({"outage_line_id": MULTI}) == agent_tools.summarize_line_contingency(raw)
    with pytest.raises(ValueError, match="No overloaded transmission line"):
        legacy[SENS].invoke({"outage_line_id": NO_OVERLOAD})
    automatic = legacy[SENS].invoke({"outage_line_id": OUTAGE})
    assert automatic["target_selection"] == "most_severe_violation" and automatic["monitored_line_id"] == MONITORED
