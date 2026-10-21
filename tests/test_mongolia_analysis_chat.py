"""Issue #11: scripted model + actual Mongolia domain calculations and API."""
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

import web_app
from ai_ems.agent.mongolia_graph import create_mongolia_agent_graph
from ai_ems.agent.mongolia_tools import create_mongolia_tools
from ai_ems.mongolia_security import MongoliaModelUnavailable
from ai_ems.mongolia_sensitivity import SensitivityVerificationFailed
from browser.mongolia_chat_fixtures import (
    ScriptedMongoliaModel, OUTAGE, ISLAND, SECURITY, SENSITIVITY, call,
)

client = TestClient(web_app.app)


@pytest.fixture
def install(monkeypatch):
    monkeypatch.setattr(web_app, "mongolia_agent_sessions", {})

    def apply(steps):
        model = ScriptedMongoliaModel(steps=list(steps))
        monkeypatch.setattr(web_app, "mongolia_agent_graph", create_mongolia_agent_graph(
            web_app._load_validated_mongolia_pf, model=model, load_security=web_app._mongolia_security))
        return model
    return apply


def post(message="분석해 줘", session="mn-analysis"):
    return client.post("/api/grids/mn-osm-35k/chat", json={"session_id": session, "message": message})


def test_chain_matches_actual_service_results_and_ignores_model_numbers(install):
    model = install([call(SECURITY, {"outage_line_id": OUTAGE}), "sensitivity_previous",
                     AIMessage(content="위반 999건, 가짜 발전기 우선순위")])
    response = post(f"{OUTAGE} N-1 후 민감도 Top 5")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tools_used"] == [SECURITY, SENSITIVITY]
    assert [u["type"] for u in body["ui_updates"]] == ["security", "sensitivity"]
    security, sensitivity = [u["result"] for u in body["ui_updates"]]
    assert security == web_app._mongolia_security().run_line_contingency(OUTAGE)
    assert sensitivity == web_app._mongolia_security().run_line_sensitivity(OUTAGE, top_n=5)
    assert security["post_status"] == "CONVERGED" and security["violated_equipment_count"] == 0
    assert sensitivity["monitored_line_id"] == "484276912-1_0"
    assert sensitivity["candidate_count"] == len(sensitivity["candidates"]) == 5
    assert "999" not in body["answer"] and "가짜" not in body["answer"]
    assert "자동 선택" in body["answer"]
    assert len(model.calls) == 3


def test_multiturn_uses_previous_actual_outage_and_emits_only_new_updates(install):
    model = install([call(SECURITY, {"outage_line_id": OUTAGE}), AIMessage(content="완료"),
                     "sensitivity_previous", AIMessage(content="완료")])
    assert post(f"{OUTAGE} N-1").status_code == 200
    body = post("이어서 민감도 후보 5개").json()
    assert body["tools_used"] == [SENSITIVITY]
    assert len(body["ui_updates"]) == 1
    assert body["ui_updates"][0]["result"]["outage_line_id"] == OUTAGE
    assert any(isinstance(m, ToolMessage) and m.name == SECURITY for m in model.calls[2])


def test_islanding_keeps_11_buses_and_no_fabricated_sensitivity(install):
    install([call(SECURITY, {"outage_line_id": ISLAND}), "sensitivity_previous", AIMessage(content="후보 5개 성공")])
    body = post(f"{ISLAND} N-1 후 민감도").json()
    sec, sens = [u["result"] for u in body["ui_updates"]]
    assert sec["isolated_bus_count"] == 11
    assert sens["analysis_status"] == "NOT_EVALUATED_ISLANDING"
    assert sens["candidate_count"] == 0 and sens["candidates"] == []
    assert "NOT_EVALUATED_ISLANDING" in body["answer"] and "후보 5개 성공" not in body["answer"]


@pytest.mark.parametrize("name,args", [
    (SECURITY, {"outage_line_id": "does-not-exist"}),
    (SECURITY, {"outage_line_id": "LINE-16-28"}),
    (SENSITIVITY, {"outage_line_id": OUTAGE, "monitored_line_id": "does-not-exist"}),
    (SENSITIVITY, {"outage_line_id": OUTAGE, "monitored_line_id": OUTAGE}),
    (SENSITIVITY, {"outage_line_id": OUTAGE, "top_n": 999}),
    (SENSITIVITY, {"outage_line_id": OUTAGE, "top_n": 0}),
    (SECURITY, {"outage_line_id": OUTAGE, "path": "/tmp/model.xiidm"}),
    (SECURITY, {"outage_line_id": OUTAGE, "grid_id": "kr-kpg193"}),
])
def test_bad_ids_or_arguments_never_claim_success(install, name, args):
    install([call(name, args), AIMessage(content="분석 성공, 위반 0건")])
    body = post().json()
    assert body["ui_updates"] == []
    assert "실패" in body["answer"] and "분석 성공" not in body["answer"]


def test_excluded_line_is_not_analyzed(install):
    excluded = web_app._load_validated_mongolia_pf()["metadata"]["excluded_line_ids"][0]
    install([call(SECURITY, {"outage_line_id": excluded}), AIMessage(content="분석 성공")])
    body = post().json()
    assert body["ui_updates"] == [] and "제외" in body["answer"]


@pytest.mark.parametrize("error", [MongoliaModelUnavailable("XIIDM validation failed"),
                                    SensitivityVerificationFailed("FD validation failed"),
                                    RuntimeError("provider failed")])
def test_service_failures_override_llm_claims(install, monkeypatch, error):
    service = web_app._mongolia_security()
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(service, "run_line_sensitivity", fail)
    install([call(SENSITIVITY, {"outage_line_id": OUTAGE}), AIMessage(content="성공한 후보 5개")])
    body = post().json()
    assert body["ui_updates"] == [] and "실패" in body["answer"]
    assert "성공한 후보" not in body["answer"]


def test_missing_xiidm_blocks_analysis_but_preserves_pf(install, monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "MONGOLIA_XIIDM_FILE", tmp_path / "missing.xiidm")
    install([call(SECURITY, {"outage_line_id": OUTAGE}), AIMessage(content="계산 성공"),
             call("get_mongolia_bus_pf", {"bus_id": "137"}), AIMessage(content="PF 조회 완료")])
    body = post().json()
    assert not body["ui_updates"] and "실패" in body["answer"]
    pf = post("Bus 137 전압은?").json()
    assert pf["tools_used"] == ["get_mongolia_bus_pf"] and "PF 조회 완료" == pf["answer"]


def test_sensitivity_verification_failure_keeps_pf_and_security_available(install, monkeypatch):
    service = web_app._mongolia_security()
    assert service.status()["available"]
    monkeypatch.setattr(service, "_sensitivity", {"passed": False, "reason": "self check failed"})
    mn = next(g for g in web_app.grid_catalog()["grids"] if g["id"] == "mn-osm-35k")
    assert mn["security_analysis_available"] and mn["chat_available"]
    assert not mn["sensitivity_analysis_available"]
    install([call(SENSITIVITY, {"outage_line_id": OUTAGE}), AIMessage(content="성공")])
    assert post().json()["ui_updates"] == []


def test_llm_failure_after_tool_produces_no_success_payload(install):
    install([call(SECURITY, {"outage_line_id": OUTAGE}), ConnectionError("ollama unreachable")])
    response = post()
    assert response.status_code == 503 and "answer" not in response.json()
    assert not web_app.mongolia_agent_sessions


@pytest.mark.parametrize("question", [f"{OUTAGE} N-1", "분석해 줘"])
def test_analysis_text_without_tool_is_never_a_calculation(install, question):
    install([AIMessage(content='분석 성공 {"post_status": "CONVERGED", "violated_equipment_count": 0}')])
    body = post(question).json()
    assert body["ui_updates"] == [] and body["tools_used"] == []
    assert "분석 성공" not in body["answer"] and "결과가 없습니다" in body["answer"]


def test_ui_evidence_rejects_kpg_and_unpaired_or_text_only_tool_results():
    fake = {"grid_id": "kr-kpg193", "outage_line_id": OUTAGE, "post_status": "CONVERGED"}
    tool = ToolMessage(content=json.dumps(fake), artifact=fake, name=SECURITY, tool_call_id="call-1")
    for messages in ([tool], [call(SECURITY, {"outage_line_id": OUTAGE}), tool],
                     [call(SECURITY, {"outage_line_id": OUTAGE}),
                      ToolMessage(content=json.dumps({**fake, "grid_id": "mn-osm-35k"}), name=SECURITY, tool_call_id="call-1")]):
        assert web_app._mongolia_analysis_evidence(messages)[0] == []


def test_tool_schema_exposes_only_original_ids_and_bounded_top_n():
    tools = {t.name: t for t in create_mongolia_tools(web_app._load_validated_mongolia_pf, web_app._mongolia_security)}
    assert len(tools) == 7
    assert set(tools[SECURITY].args) == {"outage_line_id"}
    assert set(tools[SENSITIVITY].args) == {"outage_line_id", "monitored_line_id", "top_n"}


def test_pf_query_worded_as_analysis_is_preserved(install):
    install([call("get_mongolia_pf_summary", {}), AIMessage(content="합성 PF 분석 요약입니다. N-1 결과는 포함하지 않습니다.")])
    body = post("전체 PF 분석 요약해줘").json()
    assert body["answer"] == "합성 PF 분석 요약입니다. N-1 결과는 포함하지 않습니다."
    assert body["tools_used"] == ["get_mongolia_pf_summary"]
    assert not body["ui_updates"]


def test_security_nonconvergence_is_not_a_success_claim(install, monkeypatch):
    service = web_app._mongolia_security()
    def fail_to_converge(outage_line_id):
        return {"grid_id": "mn-osm-35k", "outage_line_id": outage_line_id,
                "post_status": "MAX_ITERATION_REACHED", "violation_assessment_available": False}
    monkeypatch.setattr(service, "run_line_contingency", fail_to_converge)
    install([call(SECURITY, {"outage_line_id": OUTAGE}), AIMessage(content="수렴 성공, 위반 0건")])
    body = post().json()
    assert "비수렴" in body["answer"] and "위반 0건" not in body["answer"]
    assert body["ui_updates"][0]["result"]["violation_assessment_available"] is False
