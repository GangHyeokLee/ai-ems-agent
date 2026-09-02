from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from ai_ems import load_network
from ai_ems.agent.graph import (
    SYSTEM_PROMPT,
    create_agent_graph,
)


network = load_network(
    "data/KPG193_ver2_0_pypowsybl.mat"
)

graph = create_agent_graph(network)


first_result = graph.invoke(
    {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "LINE-16-28 선로가 탈락하면 "
                    "어떤 문제가 생기는지 분석해줘. "
                    "LINE-16-22를 모니터링해."
                )
            ),
        ]
    }
)

print("=== First Turn ===")

for message in first_result["messages"]:
    print(
        f"\n[{message.__class__.__name__}]"
    )
    print(message.content)

    if getattr(message, "tool_calls", None):
        print("Tool Calls:", message.tool_calls)

second_result = graph.invoke(
    {
        "messages": first_result["messages"]
        + [
            HumanMessage(
                content=(
                    "그럼 거기에 영향이 큰 "
                    "발전기 5개는?"
                )
            )
        ]
    }
)

print("\n\n=== Second Turn ===")

new_messages = second_result["messages"][
    len(first_result["messages"]):
]

for message in new_messages:
    print(
        f"\n[{message.__class__.__name__}]"
    )
    print(message.content)

    if getattr(message, "tool_calls", None):
        print("Tool Calls:", message.tool_calls)