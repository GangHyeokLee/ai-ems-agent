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

result = graph.invoke(
    {
        "messages": [
            SystemMessage(
                content=SYSTEM_PROMPT
            ),
            # HumanMessage(
            #     content=(
            #         "LINE-16-28 선로가 탈락하면 "
            #         "계통에 어떤 문제가 생기는지 분석해줘. "
            #         "LINE-16-22를 모니터링해."
            #     )
            # ),
            HumanMessage(
                content=(
                    "LINE-16-28 선로 탈락 시 "
                    "LINE-16-22 조류에 영향이 큰 발전기 "
                    "5개를 알려줘."
                )
            ),
        ]
    }
)

print("=== Conversation ===")

for message in result["messages"]:
    print(
        f"\n[{message.__class__.__name__}]"
    )

    print(message.content)

    if getattr(message, "tool_calls", None):
        print(
            "Tool Calls:",
            message.tool_calls,
        )