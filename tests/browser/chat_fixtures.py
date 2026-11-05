"""Real KPG /api/chat responses with only the LLM replaced by a script.

Each scenario invokes the real KPG agent tool, wraps its output in the same LangChain
message sequence the agent graph produces (AIMessage tool call -> ToolMessage -> final
AIMessage; workflow tools use the graph's deterministic formatter), and returns what the
real ``web_app.chat`` endpoint builds from it, including ``ui_updates``.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

SCENARIOS: dict[str, tuple[str, dict[str, Any]]] = {
    "security": ("line_contingency", {"outage_line_id": "LINE-16-28"}),
    "sensitivity": ("generator_sensitivity", {"outage_line_id": "LINE-16-28", "monitored_line_id": "LINE-16-22", "top_n": 5}),
    "generator": ("generator_contingency_analysis", {"generator_id": "GEN-124#1", "slack_mode": "single", "top_n_overloads": 5}),
    "redispatch": ("contingency_response_analysis", {"outage_line_id": "LINE-16-28", "delta_mw": 10.0, "top_n": 3}),
}


class ScriptedGraph:
    """Stands in for the compiled agent graph: one scripted tool call, real tool output."""

    def __init__(self, tool, args: dict[str, Any], final_text: str | None):
        self.tool, self.args, self.final_text = tool, args, final_text
        self.output = None

    def invoke(self, state):
        from ai_ems.agent.graph import DETERMINISTIC_FORMATTERS

        self.output = self.tool.invoke(self.args)
        call = AIMessage(content="", tool_calls=[{"name": self.tool.name, "args": self.args, "id": "call-1"}])
        tool_message = ToolMessage(content=json.dumps(self.output, ensure_ascii=False, default=str),
                                   name=self.tool.name, tool_call_id="call-1")
        formatter = DETERMINISTIC_FORMATTERS.get(self.tool.name)
        answer = formatter[1](self.output) if formatter else (self.final_text or f"{self.tool.name} 결과입니다.")
        return {"messages": [*state["messages"], call, tool_message, AIMessage(content=answer)]}


def build_chat_response(web_app, key: str, session_id: str = "fixture") -> tuple[dict, Any]:
    """Return (``/api/chat`` JSON, raw tool output) for one scenario via the real endpoint code."""
    tool_name, args = SCENARIOS[key]
    graph = ScriptedGraph(web_app.direct_agent_tools[tool_name], args, None)
    previous = web_app.agent_graph
    web_app.agent_graph = graph
    try:
        response = web_app.chat(web_app.ChatRequest(session_id=f"{session_id}-{key}", message=f"[fixture:{key}]"))
    finally:
        web_app.agent_graph = previous
        web_app.agent_sessions.pop(f"{session_id}-{key}", None)
    return json.loads(json.dumps(response, ensure_ascii=False, default=str)), graph.output
