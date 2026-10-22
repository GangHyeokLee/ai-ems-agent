"""Browser-only server: real chat handlers, sessions and tools; scripted model choices.

Run through the browser harness, never the production launcher. Followups must resolve
their outage from the actual server history; a new or mixed session fails the test.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import uvicorn

import web_app
from ai_ems.agent.mongolia_graph import create_mongolia_agent_graph
from chat_fixtures import SCENARIOS, ScriptedGraph
from mongolia_chat_fixtures import OUTAGE, SECURITY, ScriptedMongoliaModel, call


def scenario(state, grid):
    humans = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    other = "mn" if grid == "kpg" else "kpg"
    assert not any(f"[session:{other}_" in message for message in humans), "mixed grid context"
    match = re.search(r"\[session:(\w+)\]", humans[-1])
    assert match and match[1].startswith(grid + "_"), "unexpected browser request"
    return match[1]


class KpgContextGraph:
    def invoke(self, state):
        key = scenario(state, "kpg")
        if key == "kpg_followup":
            previous = next(m for m in reversed(state["messages"])
                            if isinstance(m, ToolMessage) and m.name == "line_contingency")
            import json
            outage = json.loads(previous.content)["outage_line_id"]
            name, args = SCENARIOS["sensitivity"]
            args = {**args, "outage_line_id": outage}
        else:
            assert key == "kpg_security"
            name, args = SCENARIOS["security"]
        return ScriptedGraph(web_app.direct_agent_tools[name], args, "KPG 문맥 기반 분석 결과").invoke(state)


class MongoliaContextGraph:
    def invoke(self, state):
        key = scenario(state, "mn")
        if key == "mn_followup":
            steps = ["sensitivity_previous", AIMessage(content="완료")]
        else:
            assert key == "mn_security"
            steps = [call(SECURITY, {"outage_line_id": OUTAGE}), AIMessage(content="완료")]
        model = ScriptedMongoliaModel(steps=steps)
        return create_mongolia_agent_graph(
            web_app._load_validated_mongolia_pf, model=model,
            load_security=web_app._mongolia_security,
        ).invoke(state)


if __name__ == "__main__":
    web_app.agent_graph = KpgContextGraph()
    web_app.mongolia_agent_graph = MongoliaContextGraph()
    uvicorn.run(web_app.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
