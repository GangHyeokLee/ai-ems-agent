from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode

from ai_ems import load_network
from ai_ems.agent.tools import create_agent_tools

network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

tools = create_agent_tools(network)

print("=== Registered Tools ===")
for item in tools:
    print(item.name)

tool_node = ToolNode(tools)

request = AIMessage(
    content="",
    tool_calls=[
        {
            "name": "line_contingency",
            "args": {
                "outage_line_id": "LINE_16-28",
                "monitored_line_ids": ["LINE_16-22"],
            },
            "id": "test-call-1",
            "type": "tool_call",
        }
    ],
)

result = tool_node.invoke(
    {
        "messages": [request],
    }
)

print("\n=== Tool Result ===")
print(result["messages"][0].content)