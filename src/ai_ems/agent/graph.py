from langchain_ollama import ChatOllama
from langgraph.graph import (
    END,
    START,
    MessagesState,
    StateGraph,
)
from langgraph.prebuilt import ToolNode, tools_condition

from ai_ems.agent.tools import create_agent_tools


SYSTEM_PROMPT = """
You are an AI assistant for power-system analysis.

Use the provided tools whenever a question requires actual network data
or power-system calculation.

Do not invent power-system results.

After receiving a tool result:
- explain the result in Korean
- distinguish raw violation records from violated equipment
- clearly state whether the calculation converged
- for contingency analysis, explain loading percentage and violations
- for sensitivity analysis, explain that sensitivity identifies control
  candidates and does not itself guarantee corrective action
"""


def create_agent_graph(network):

    tools = create_agent_tools(network)

    model = ChatOllama(
        model="qwen2:7b",
        base_url="http://host.docker.internal:11434",
        temperature=0,
    )

    model_with_tools = model.bind_tools(tools)

    def agent_node(state: MessagesState):
        response = model_with_tools.invoke(
            state["messages"]
        )

        return {
            "messages": [response]
        }

    builder = StateGraph(MessagesState)

    builder.add_node(
        "agent",
        agent_node,
    )

    builder.add_node(
        "tools",
        ToolNode(tools),
    )

    builder.add_edge(
        START,
        "agent",
    )

    builder.add_conditional_edges(
        "agent",
        tools_condition,
    )

    builder.add_edge(
        "tools",
        "agent",
    )

    return builder.compile()