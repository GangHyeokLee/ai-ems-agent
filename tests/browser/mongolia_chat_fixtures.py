"""Script only the model; use the real LangGraph, domain tools and chat endpoint."""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ai_ems.agent.mongolia_graph import create_mongolia_agent_graph

OUTAGE = "751261406-1_0"
ISLAND = "484333456-1_0"
SECURITY = "run_mongolia_line_contingency"
SENSITIVITY = "run_mongolia_line_sensitivity"


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class ScriptedMongoliaModel(BaseChatModel):
    steps: list[Any]
    calls: list[list[Any]] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-mongolia"

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == "sensitivity_previous":
            # Resolve from actual history, never a hard-coded fallback or KPG state.
            previous = next(m.artifact for m in reversed(messages)
                            if isinstance(m, ToolMessage) and isinstance(m.artifact, dict)
                            and m.artifact.get("grid_id") == "mn-osm-35k" and not m.artifact.get("error"))
            step = call(SENSITIVITY, {"outage_line_id": previous["outage_line_id"], "top_n": 5}, "sens-followup")
        return ChatResult(generations=[ChatGeneration(message=step)])


def build_mongolia_responses(web_app) -> dict[str, dict]:
    """Browser fixtures: run real tools through actual graph and API handling."""
    scenarios = {
        "mn_security": [call(SECURITY, {"outage_line_id": OUTAGE}), AIMessage(content="model text")],
        "mn_followup": ["sensitivity_previous", AIMessage(content="model text")],
        "mn_chain": [call(SECURITY, {"outage_line_id": OUTAGE}), "sensitivity_previous", AIMessage(content="model text")],
        "mn_island": [call(SECURITY, {"outage_line_id": ISLAND}), "sensitivity_previous", AIMessage(content="model text")],
        "mn_pf": [call("get_mongolia_bus_pf", {"bus_id": "137"}), AIMessage(content="Bus 137 합성 PF 조회")],
    }
    responses = {}
    previous_graph = web_app.mongolia_agent_graph
    previous_sessions = web_app.mongolia_agent_sessions
    web_app.mongolia_agent_sessions = {}
    try:
        for key, steps in scenarios.items():
            model = ScriptedMongoliaModel(steps=steps)
            web_app.mongolia_agent_graph = create_mongolia_agent_graph(
                web_app._load_validated_mongolia_pf, model=model, load_security=web_app._mongolia_security)
            responses[key] = web_app.mongolia_chat(web_app.ChatRequest(
                session_id="mn-browser-fixture", message="이어서 민감도" if key == "mn_followup" else key))
    finally:
        web_app.mongolia_agent_graph = previous_graph
        web_app.mongolia_agent_sessions = previous_sessions
    return json.loads(json.dumps(responses, ensure_ascii=False))
