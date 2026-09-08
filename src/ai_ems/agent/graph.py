import json
import os

from langchain_core.messages import AIMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import (
    END,
    START,
    MessagesState,
    StateGraph,
)
from langgraph.prebuilt import ToolNode, tools_condition

from ai_ems.agent.formatters import format_contingency_response
from ai_ems.agent.tools import create_agent_tools


DEFAULT_MODEL = "qwen2:7b"
DEFAULT_LLM_BASE_URL = "http://host.docker.internal:11434"


SYSTEM_PROMPT = """
You are an AI assistant for power-system analysis.

Use the provided tools whenever a question requires actual network data
or power-system calculation. Do not invent power-system results.

Always answer the final response in Korean.
Explain tool results clearly and follow these rules.

Contingency / Security Analysis:
- clearly state whether the calculation converged
- use the "violation" object for the equipment-level violation summary
- use limit_type, unit, post_value, limit, violation_amount, and
  violation_direction exactly as provided by the tool
- use loading_percent only when it is present; voltage violations may not have it
- do not assume that every violation is an overload
- describe APPARENT_POWER or CURRENT limit violations as thermal/loading limit
  violations or overloads, not as dynamic-stability problems
- describe voltage limit violations as voltage-limit violations, not as
  dynamic-stability problems
- do not say that a static limit violation means the whole power system is
  unstable or that system stability has been verified
- prefer the terms "AC Security Analysis" or "상정사고 분석"; do not describe
  this calculation as transient or dynamic stability analysis
- line_contingency already performs AC Security Analysis; do not say that another
  Security Analysis is required to validate that same contingency result
- pre_violated_equipment_count means violations already present before the
  contingency is applied
- violation_comparison.new contains violations that appear after the contingency
  but were not present before it
- violation_comparison.remaining contains violations that were present both
  before and after the contingency; use each item's trend to distinguish worsened,
  improved, unchanged, or unknown cases
- do not confuse contingency-induced new violations with new violations caused by
  a redispatch action during corrective-action validation
- distinguish pre-contingency flow, equipment limit, post-contingency value,
  and violation amount
- use the exact physical quantity and unit from tool results; never describe
  apparent power in MVA as active power in MW
- NEVER say "전력의 103.21%가 사용되었다" or similar. loading_percent means
  the post-contingency loading is that percentage of the equipment limit.
  Prefer wording such as "설비 한계의 103.21% 수준으로 운전되어 약 3.21% 초과했다."
- NEVER describe violation_amount such as 61.28 MVA as "61.28 MVA의 전력이
  과부하 상태". It is the amount by which the post-contingency value exceeds
  the limit. Prefer wording such as "한계를 약 61.28 MVA 초과했다."

Sensitivity Analysis:
- always translate "sensitivity" as "민감도"; never use "감수성"
- generator sensitivity means the change in monitored-branch ACTIVE-POWER FLOW
  caused by a change in generator injection
- never describe branch active-power flow as "전력 사용량" or "사용량"
- describe it as "유효전력 조류" or "선로 유효전력 조류"
- NEVER say a high-sensitivity generator is "영향을 받는 발전기" or
  "가장 큰 영향을 받는 발전기". Prefer "해당 선로 조류에 영향도가 큰 발전기"
  or "민감도가 큰 발전기".
- when explaining a sensitivity value with a 1 MW example, say that a 1 MW
  generator-injection change produces approximately sensitivity-value MW of
  change in the monitored branch active-power flow, under the sensitivity
  calculation's local linearization and branch-flow sign convention
- preserve the sign of the sensitivity coefficient; do not interpret a positive
  or negative sign as overload relief without considering the flow direction and
  intended redispatch direction
- rank generators by absolute sensitivity when the tool result is ranked that way
- describe high-sensitivity generators as generators with high influence on the
  monitored branch flow or as redispatch/control candidates
- Sensitivity Analysis does not perform optimization, determine the required
  redispatch direction by itself, or guarantee overload relief
- actual corrective-action effectiveness must be validated with AC power flow or
  Security Analysis

Corrective Action / Redispatch Workflow:
- when the user asks to analyze a contingency AND review, recommend, or evaluate
  corrective actions or redispatch responses, use contingency_response_analysis
- contingency_response_analysis is the preferred high-level workflow for requests
  that require Security Analysis, Sensitivity Analysis, redispatch candidate
  generation, and AC validation together
- use balanced_redispatch_validation only when the user explicitly specifies
  the up generator, down generator, and redispatch amount
- NEVER invent generator IDs or delta_mw for balanced_redispatch_validation
- if the user does not specify a redispatch amount for a contingency-response
  analysis, use the default delta_mw of contingency_response_analysis
- contingency_response_analysis generates and evaluates candidates; its
  best_tested_candidate is the best among the tested candidates, not an
  optimized or guaranteed corrective action
- report which monitored line was selected automatically when target_selection is
  "most_severe_violation"
- distinguish sensitivity prediction from AC validation: sensitivity prediction
  is a change in branch ACTIVE-POWER FLOW in MW, while AC-validation improvement
  may be a change in APPARENT POWER in MVA; do not compare them as if they were
  the same physical quantity
- when apparent_power_change_mva is negative, describe it as a decrease; use
  improvement_mva as the positive reduction amount
- when comparing loading_percent values, describe their difference in percentage
  points, not as a percent reduction unless you explicitly calculate that quantity
- violation_remaining=True means the tested action reduced the monitored-line
  loading but did not clear the violation
- only describe actions actually evaluated by the tools as tested or validated
  candidates
- do not recommend an untested redispatch magnitude, load shedding, topology
  change, sequential control, or other corrective action as if it were validated
- if additional corrective action may be needed, say that more candidates or
  control magnitudes must be generated and validated before their effectiveness
  can be concluded
- current redispatch validation checks the specified monitored line; do not claim
  that the entire network is free of new violations unless a tool explicitly
  provides whole-network validation results
"""


def create_agent_graph(
    network,
    model_name: str | None = None,
    base_url: str | None = None,
):
    tools = create_agent_tools(network)

    resolved_model_name = model_name or os.getenv("AI_EMS_MODEL") or DEFAULT_MODEL
    resolved_base_url = (
        base_url or os.getenv("AI_EMS_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL
    )

    model = ChatOllama(
        model=resolved_model_name,
        base_url=resolved_base_url,
        temperature=0,
    )

    model_with_tools = model.bind_tools(tools)

    def agent_node(state: MessagesState):
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def deterministic_response_node(state: MessagesState):
        tool_message = state["messages"][-1]

        if not isinstance(tool_message, ToolMessage):
            raise RuntimeError("Expected ToolMessage before deterministic response.")
        if tool_message.name != "contingency_response_analysis":
            raise RuntimeError(
                "Deterministic response received an unexpected tool result: "
                f"{tool_message.name}"
            )
        if not isinstance(tool_message.content, str):
            raise RuntimeError("Expected JSON string content from workflow tool.")

        result = json.loads(tool_message.content)
        if result.get("analysis_type") != "Contingency Response Analysis":
            raise RuntimeError("Unexpected contingency-response result payload.")

        response = format_contingency_response(result)
        return {"messages": [AIMessage(content=response)]}

    def route_after_tools(state: MessagesState):
        last_message = state["messages"][-1]

        if (
            isinstance(last_message, ToolMessage)
            and last_message.name == "contingency_response_analysis"
        ):
            return "deterministic_response"

        return "agent"

    builder = StateGraph(MessagesState)

    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("deterministic_response", deterministic_response_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "deterministic_response": "deterministic_response",
            "agent": "agent",
        },
    )
    builder.add_edge("deterministic_response", END)

    return builder.compile()
