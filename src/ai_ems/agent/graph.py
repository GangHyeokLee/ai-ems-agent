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
- for contingency analysis, use equipment-level loading_percent from
  violated_equipment; do not invent per-side loading percentages
- for sensitivity analysis, explain that generator sensitivity means
  how much the monitored branch active-power flow changes when the
  generator injection changes
- do not say that high-sensitivity generators are strongly affected by
  the contingency; describe them as generators with high influence on
  the monitored branch flow
- sensitivity identifies control candidates and does not itself
  determine the required redispatch direction or guarantee overload relief
- use violated_equipment for equipment-level violation summaries
- do not describe thermal or voltage limit violations as dynamic stability problems
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