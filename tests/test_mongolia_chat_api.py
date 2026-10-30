import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import web_app
from ai_ems.agent.graph import SYSTEM_PROMPT
from ai_ems.agent.mongolia_graph import MONGOLIA_SYSTEM_PROMPT, create_mongolia_agent_graph
from ai_ems.agent.mongolia_tools import MONGOLIA_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mn_agent_tool_tests", ROOT / "tests" / "test_mongolia_agent_tools.py"
)
tool_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool_tests)

client = TestClient(web_app.app)


class ScriptedChatModel(BaseChatModel):
    """Deterministic stand-in for ChatOllama that records what it was asked."""

    responses: list[Any]
    calls: list[list[Any]] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [item.name for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])


class RecordingKpgGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, state):
        self.calls.append(list(state["messages"]))
        return {"messages": [*state["messages"], AIMessage(content="KPG 응답")]}


def tool_call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def mn_env(tmp_path, monkeypatch):
    map_file, preview_file = tool_tests.write_assets(tmp_path)
    monkeypatch.setattr(web_app, "MONGOLIA_MAP_FILE", map_file)
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", preview_file)
    monkeypatch.setattr(web_app, "agent_sessions", {})
    monkeypatch.setattr(web_app, "mongolia_agent_sessions", {})
    kpg_graph = RecordingKpgGraph()
    monkeypatch.setattr(web_app, "agent_graph", kpg_graph)

    def install(responses):
        model = ScriptedChatModel(responses=list(responses))
        graph = create_mongolia_agent_graph(web_app._load_validated_mongolia_pf, model=model,
                                            load_security=web_app._mongolia_security)
        monkeypatch.setattr(web_app, "mongolia_agent_graph", graph)
        return model

    return {"install": install, "kpg_graph": kpg_graph, "preview_file": preview_file}


def post_mn(message, session_id="same-session"):
    return client.post(
        "/api/grids/mn-osm-35k/chat",
        json={"session_id": session_id, "message": message},
    )


def test_mongolia_question_uses_only_mongolia_tools_and_real_values(mn_env):
    model = mn_env["install"]([
        tool_call("get_mongolia_bus_pf", {"bus_id": "137"}),
        AIMessage(content="Bus 137 전압은 합성 PF 결과 기준입니다."),
    ])

    response = post_mn("Bus 137 전압은?")

    assert response.status_code == 200
    body = response.json()
    assert body["grid_id"] == "mn-osm-35k"
    assert body["tools_used"] == ["get_mongolia_bus_pf"]
    assert body["ui_updates"] == []
    assert set(model.bound_tool_names) == MONGOLIA_TOOL_NAMES
    assert mn_env["kpg_graph"].calls == []
    assert isinstance(model.calls[0][0], SystemMessage)
    assert model.calls[0][0].content == MONGOLIA_SYSTEM_PROMPT
    tool_messages = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    payload = json.loads(tool_messages[0].content)
    preview = json.loads(mn_env["preview_file"].read_text(encoding="utf-8"))
    expected = next(bus for bus in preview["buses"] if bus["bus_id"] == "137")
    assert payload["v_mag_pu"] == expected["v_mag_pu"]
    assert payload["status"] == "ANALYZED"


def test_ranking_question_runs_python_ranking_tool(mn_env):
    model = mn_env["install"]([
        tool_call("get_mongolia_pf_ranking", {"metric": "line_loading_high", "top_n": 5}),
        AIMessage(content="부하율 상위 선로 5개입니다."),
    ])
    body = post_mn("부하율이 가장 높은 선로 5개 알려줘.").json()
    payload = json.loads(next(m for m in model.calls[1] if isinstance(m, ToolMessage)).content)
    assert body["tools_used"] == ["get_mongolia_pf_ranking"]
    assert len(payload["ranking"]) == 5
    values = [row["value"] for row in payload["ranking"]]
    assert values == sorted(values, reverse=True)


def test_hallucinated_kpg_tool_call_is_rejected(mn_env):
    mn_env["install"]([
        tool_call("line_contingency", {"outage_line_id": "LINE-16-28"}),
        AIMessage(content="N-1 결과: 과부하 없음"),
    ])
    response = post_mn("선로 255335646-1_0 조류 알려줘")
    assert response.status_code == 502
    assert "line_contingency" in response.json()["detail"]
    assert web_app.mongolia_agent_sessions == {}
    assert mn_env["kpg_graph"].calls == []


@pytest.mark.parametrize(
    "message",
    [
        "이 발전기의 출력을 20MW 늘려 줘.",
        "전압 문제 해결을 위한 Redispatch를 계산해 줘.",
        "GEN-1 발전기 탈락 분석해 줘.",
        "발전기 N-1 분석해 줘",
        "generator contingency 분석해 줘",
        "GEN-19#0 N-1 분석해 줘",
        "발전기 탈락 사고 분석해 줘",
    ],
)
def test_unsupported_calculation_is_answered_without_llm_or_tools(mn_env, message, monkeypatch):
    service_loader = Mock(side_effect=AssertionError("unsupported request reached domain service"))
    monkeypatch.setattr(web_app, "_mongolia_security", service_loader)
    model = mn_env["install"]([])
    response = post_mn(message)
    assert response.status_code == 200
    body = response.json()
    assert body["ui_updates"] == []
    service_loader.assert_not_called()
    assert body["tools_used"] == []
    assert body["unsupported_requests"]
    assert "지원하지 않습니다" in body["answer"]
    assert model.calls == []
    assert mn_env["kpg_graph"].calls == []


def test_llm_failure_returns_error_without_fabricated_answer(mn_env):
    mn_env["install"]([ConnectionError("ollama unreachable")])
    response = post_mn("Bus 137 전압은?")
    assert response.status_code == 503
    assert "answer" not in response.json()
    assert "LLM" in response.json()["detail"]
    assert web_app.mongolia_agent_sessions == {}


def test_missing_or_invalid_preview_blocks_chat_before_llm(mn_env, tmp_path, monkeypatch):
    model = mn_env["install"]([AIMessage(content="임의 답변")])
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", tmp_path / "missing.json")
    assert post_mn("Bus 137 전압은?").status_code == 404

    invalid = tmp_path / "invalid.json"
    payload = json.loads(mn_env["preview_file"].read_text(encoding="utf-8"))
    payload["metadata"]["scenario_id"] = "other"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", invalid)
    assert post_mn("Bus 137 전압은?").status_code == 422
    assert model.calls == []


def test_same_session_id_keeps_grid_histories_separate(mn_env):
    mn_env["install"]([AIMessage(content="몽골 요약 답변"), AIMessage(content="몽골 두 번째 답변")])

    assert post_mn("몽골 계통 요약해줘").status_code == 200
    kpg = client.post("/api/chat", json={"session_id": "same-session", "message": "KPG 질문"})
    assert kpg.status_code == 200
    assert post_mn("다시 요약").status_code == 200

    kpg_history = web_app.agent_sessions["same-session"]
    mn_history = web_app.mongolia_agent_sessions["same-session"]
    assert kpg_history[0].content == SYSTEM_PROMPT
    assert mn_history[0].content == MONGOLIA_SYSTEM_PROMPT
    kpg_humans = [m.content for m in kpg_history if isinstance(m, HumanMessage)]
    mn_humans = [m.content for m in mn_history if isinstance(m, HumanMessage)]
    assert kpg_humans == ["KPG 질문"]
    assert mn_humans == ["몽골 계통 요약해줘", "다시 요약"]


def test_kpg_chat_contract_unchanged_and_never_uses_mongolia_agent(mn_env):
    model = mn_env["install"]([])
    response = client.post("/api/chat", json={"session_id": "k1", "message": "LINE-16-28 사고 분석"})
    routed = client.post("/api/grids/kr-kpg193/chat", json={"session_id": "k2", "message": "요약"})

    assert response.status_code == 200
    assert set(response.json()) == {"session_id", "answer", "ui_updates"}
    assert routed.status_code == 200
    assert routed.json()["grid_id"] == "kr-kpg193"
    assert len(mn_env["kpg_graph"].calls) == 2
    assert model.calls == []
    assert web_app.mongolia_agent_sessions == {}


def test_kpg_agent_tools_do_not_include_mongolia_tools():
    names = set(web_app.direct_agent_tools)
    assert names
    assert not names & MONGOLIA_TOOL_NAMES


def test_unknown_grid_chat_is_404(mn_env):
    assert client.post("/api/grids/xx/chat", json={"session_id": "s", "message": "hi"}).status_code == 404


def test_empty_message_is_rejected(mn_env):
    assert post_mn("   ").status_code == 400
