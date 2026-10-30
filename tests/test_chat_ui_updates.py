"""KPG chat -> UI linkage with real tool outputs (only the LLM tool choice is scripted).

Checks that /api/chat turns real KPG Security, Sensitivity and generator-contingency tool
results into the ``ui_updates`` the shared UI renders, consistent with the REST endpoints,
and that the Redispatch workflow answer is produced by the deterministic formatter.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_app

spec = importlib.util.spec_from_file_location(
    "chat_fixtures", Path(__file__).resolve().parent / "browser" / "chat_fixtures.py")
chat_fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat_fixtures)
client = TestClient(web_app.app)


@pytest.fixture(scope="module")
def responses():
    return {key: chat_fixtures.build_chat_response(web_app, key) for key in chat_fixtures.SCENARIOS}


def test_chat_contract_unchanged(responses):
    for body, _ in responses.values():
        assert set(body) == {"session_id", "answer", "ui_updates"}
        assert body["answer"].strip()


def test_chat_security_update_matches_rest_security(responses):
    body, _ = responses["security"]
    assert [u["type"] for u in body["ui_updates"]] == ["security"]
    chat = body["ui_updates"][0]["result"]
    rest = client.get("/api/security-analysis", params={"outage_line_id": "LINE-16-28"}).json()
    assert chat["outage_line_id"] == rest["outage_line_id"] == "LINE-16-28"
    assert chat["post_status"] == rest["post_status"] == "CONVERGED"
    assert chat["violation_assessment_available"] is True
    assert chat["violations"][0]["equipment_id"] == rest["violations"][0]["equipment_id"] == "LINE-16-22"
    assert chat["violations"][0]["loading_percent"] == pytest.approx(rest["violations"][0]["loading_percent"], abs=1e-6)


def test_chat_sensitivity_update_matches_rest_sensitivity(responses):
    body, _ = responses["sensitivity"]
    assert [u["type"] for u in body["ui_updates"]] == ["sensitivity"]
    chat = body["ui_updates"][0]["result"]
    rest = client.get("/api/sensitivity-analysis",
                      params={"outage_line_id": "LINE-16-28", "monitored_line_id": "LINE-16-22", "top_n": 5}).json()
    assert chat["monitored_line_id"] == rest["monitored_line_id"] == "LINE-16-22"
    assert [c["generator_id"] for c in chat["candidates"]] == [c["generator_id"] for c in rest["candidates"]]
    assert chat["candidates"][0]["generator_id"] == "GEN-19#0"
    assert chat["candidates"][0]["sensitivity"] == pytest.approx(rest["candidates"][0]["sensitivity"], abs=1e-9)
    assert all(c.get("bus_id") is not None for c in chat["candidates"])


def test_chat_generator_contingency_update(responses):
    body, output = responses["generator"]
    assert [u["type"] for u in body["ui_updates"]] == ["generator_contingency"]
    chat = body["ui_updates"][0]["result"]
    rest = client.post("/api/generator-contingency-analysis",
                       json={"generator_id": "GEN-124#1", "slack_mode": "single"}).json()
    assert chat["generator_id"] == rest["generator_id"] == "GEN-124#1"
    assert chat["slack_mode"] == "single"
    assert chat["post_contingency_converged"] == rest["post_contingency_converged"]
    assert chat["outage_generator"]["bus_id"] == rest["outage_generator"]["bus_id"]
    from ai_ems.agent.graph import DETERMINISTIC_FORMATTERS

    assert body["answer"] == DETERMINISTIC_FORMATTERS["generator_contingency_analysis"][1](output)
    assert "GEN-124#1" in body["answer"]


def test_chat_redispatch_workflow_answer_from_deterministic_formatter(responses):
    from ai_ems.agent.graph import DETERMINISTIC_FORMATTERS

    body, output = responses["redispatch"]
    assert output["analysis_type"] == "Contingency Response Analysis"
    assert body["answer"] == DETERMINISTIC_FORMATTERS["contingency_response_analysis"][1](output)
    assert body["ui_updates"] == []                       # no map overlay for the workflow answer
    assert "LINE-16-28" in body["answer"]
