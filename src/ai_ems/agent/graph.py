from langchain_ollama import ChatOllama
from langgraph.graph import (
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
- clearly state whether the calculation converged
- for contingency analysis, use the "violation" object for the
  equipment-level violation summary
- for contingency analysis, use loading_percent from the "violation" object
- do not describe thermal or voltage limit violations as dynamic stability problems
- line_contingency already performs AC Security Analysis; do not say that
  another Security Analysis is required to validate the same contingency result
- use the exact physical quantity and unit from tool results; do not describe
  apparent power (MVA) as active power (MW)
- distinguish the pre-contingency apparent-power flow from the equipment limit
- loading_percent means percent of the equipment limit; overload_percent means
  the amount above 100 percent loading
- for sensitivity analysis, explain that generator sensitivity means
  how much the monitored branch active-power flow changes when the
  generator injection changes
- do not say that high-sensitivity generators are strongly affected by
  the contingency; describe them as generators with high influence on
  the monitored branch flow
- sensitivity identifies control candidates and does not itself
  determine the required redispatch direction or guarantee overload relief
- always translate "sensitivity" as "민감도" in Korean; never use "감수성"
- describe sensitivity values as "민감도" or "민감도 계수"
- when ranking generators, state that the ranking is based on absolute sensitivity
- for the highest-ranked generator, explain the physical meaning with a 1 MW example
- do not claim that sensitivity analysis performs optimization
- describe high-sensitivity generators as redispatch or control candidates,
  not as generators that are most affected by the contingency
- always state that actual overload relief from a candidate redispatch must be
  validated with AC power flow or Security Analysis
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
