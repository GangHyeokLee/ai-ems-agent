from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from ai_ems import load_network
from ai_ems.agent.graph import (
    SYSTEM_PROMPT,
    create_agent_graph,
)
from ai_ems.config import CASE_FILE

network = load_network(CASE_FILE)

graph = create_agent_graph(network)

result = graph.invoke(
    {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=("LINE-16-28 사고를 분석하고 " "대응방안까지 검토해줘.")
            ),
        ]
    }
)

print("=== Conversation ===")

for message in result["messages"]:
    print(f"\n[{message.__class__.__name__}]")
    print(message.content)

    if getattr(message, "tool_calls", None):
        print(
            "Tool Calls:",
            message.tool_calls,
        )
