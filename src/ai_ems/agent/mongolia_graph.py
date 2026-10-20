"""Read-only LangGraph agent for the synthetic Mongolia AC PF preview.

Kept separate from the KPG-193 agent: only the Mongolia query tools are bound,
so KPG PyPowSyBl security/sensitivity/redispatch tools can never be called here.
"""

from __future__ import annotations

import re

from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ai_ems.agent.mongolia_tools import PreviewLoader, create_mongolia_tools
from ai_ems.config import LLM_BASE_URL, MODEL_NAME, get_llm_client_kwargs

MONGOLIA_SYSTEM_PROMPT = """
당신은 몽골 송전망(MN_35k)의 '합성 AC Power Flow 결과 조회' 전용 AI 어시스턴트입니다.
당신은 계산을 수행하지 않습니다. 사전에 계산·검증된 단일 합성 PF 결과를 Tool로 조회해 설명만 합니다.

사용 가능한 Tool:
- get_mongolia_pf_summary: 계통 전체 요약(수렴 상태, 설비 수, 전압 범위, 최대 부하율, 스크리닝 위반, 합성 설비 정보)
- get_mongolia_bus_pf: 모선 1개의 전압(pu)·위상각·PV/PQ/Slack
- get_mongolia_line_pf: 선로 1개의 P0/Q0, P1/Q1, 정격, 부하율
- get_mongolia_transformer_pf: 변압기 1개의 P/Q, 정격, 부하율
- get_mongolia_pf_ranking: 전압 높은/낮은 순, 선로·변압기 부하율 높은 순 Top N(최대 10)

반드시 지킬 규칙:
1. 계통 숫자(전압, 조류, 부하율, 설비 수 등)가 필요한 질문에는 반드시 Tool을 호출한다.
2. Tool 결과에 없는 전압·조류·발전량·순위를 만들어 내지 않는다. 순위는 get_mongolia_pf_ranking 결과만 사용한다.
3. 합성 PF 결과와 실제 몽골 운전계통을 구분한다. 실제 운영값이라고 표현하지 않는다.
4. PF 수렴은 수치해가 존재한다는 뜻일 뿐, 실제 계통 운영 적합성이나 안정성을 의미하지 않는다.
5. snapshot 2013-01-01 00:00:00은 모델 시점 표기이며 실제 측정 날짜가 아니다.
6. 합성 발전기 45대는 실제 몽골 발전소 45곳이 아니다.
7. 지도에 표시된 발전소 데이터 27건은 실제 계통 접속이 검증되지 않았다.
8. 선로·변압기의 전기정수와 일부 길이·회선수는 실설비로 검증되지 않았다.
9. N-1, Redispatch, OPF, 동특성 분석을 실행했다고 주장하지 않는다. AI 채팅은 이런 계산을 호출할 수 없으며,
   조회 가능한 합성 PF 결과만 안내할 수 있다고 답한다. 선로 N-1은 화면의 Security Analysis 패널에서
   사용자가 직접 실행할 수 있다고 안내할 수 있다. 발전기 출력 변경 등 가정 계산도 하지 않는다.
10. 결과만으로 원인을 확정할 수 없으면 추정하지 말고 추가 데이터가 필요하다고 답한다.
11. 모든 응답은 한국어로 작성한다.

Tool 결과 해석:
- status가 EXCLUDED_FROM_ANALYSIS이면 '해석 대상 제외' 설비이며 계산값이 없다. 0 pu나 0 MW로 말하지 않는다.
- status가 NOT_FOUND이면 존재하지 않는 설비 ID라고 답한다.
- status가 DATA_UNAVAILABLE이면 검증된 데이터를 사용할 수 없다고만 답하고 수치를 말하지 않는다.
- P0/Q0는 bus1 단자, P1/Q1은 bus2 단자 값이며, 각 단자에서 모선에서 설비 방향으로 들어가는 조류가 양수다.
  부호를 바꾸거나 반올림 외의 변형을 하지 않는다.
- 위반 여부는 '가정된 스크리닝 기준'(전압 0.9~1.1 pu, 열적 정격) 대비 결과이며 운영 기준 판정이 아니다.
- 답변 끝에 필요하면 "합성 PF 결과이며 실제 운영 데이터가 아님"을 짧게 명시한다.
""".strip()

UNSUPPORTED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("N-1 상정사고 분석", re.compile(r"(?<![A-Za-z0-9])N\s*-\s*1(?![0-9])|상정\s*사고|사고\s*분석|contingency|탈락", re.IGNORECASE)),
    ("Redispatch(재급전)", re.compile(r"re-?dispatch|재급전|리디스패치", re.IGNORECASE)),
    ("OPF(최적조류)", re.compile(r"(?<![A-Za-z])OPF(?![A-Za-z])|최적\s*조류|optimal\s+power\s+flow", re.IGNORECASE)),
    ("동특성·과도안정도 해석", re.compile(r"동특성|과도\s*안정|dynamic\s+simulation|transient", re.IGNORECASE)),
    ("민감도 분석", re.compile(r"민감도|sensitivity", re.IGNORECASE)),
    (
        "발전기 출력·설비 상태를 바꾼 재계산",
        re.compile(r"(출력|발전량|부하)\S*\s*(을|를)?\s*\S*\s*(늘|줄|증가|감소|올려|내려|변경|조정|바꿔)|재계산|다시\s*계산", re.IGNORECASE),
    ),
]


def detect_unsupported_request(message: str) -> list[str]:
    """Return labels of calculation requests that the Mongolia read-only agent must not attempt."""
    return [label for label, pattern in UNSUPPORTED_PATTERNS if pattern.search(message)]


def unsupported_request_answer(labels: list[str]) -> str:
    requested = ", ".join(labels)
    return (
        f"요청하신 **{requested}**은(는) 몽골 AI 채팅에서 지원하지 않습니다. 실제 계산을 수행하지 않았습니다.\n\n"
        "**이유**\n"
        "- 몽골 AI 채팅은 사전에 계산·검증된 단일 합성 AC PF 결과만 조회하는 읽기 전용 기능입니다.\n"
        "- 선로 N-1 Security Analysis는 화면의 Security Analysis 패널에서 선로를 선택해 직접 실행할 수 있습니다"
        "(PyPowSyBl 합성 모델, AI 채팅에서는 호출 불가).\n"
        "- 선로·변압기 전기정수, 발전소 접속, 발전기 제약이 실설비로 검증되지 않아 "
        "Sensitivity·Redispatch·OPF·동특성 해석은 지원하지 않습니다.\n"
        "- 웹 요청 시 PF를 재계산하지 않으므로 설비 출력·상태를 바꾼 결과를 만들 수 없습니다.\n\n"
        "**현재 조회 가능한 내용** (합성 PF 결과, 실제 운영 데이터 아님)\n"
        "- 계통 전체 요약: 수렴 상태, 전압 범위, 최대 부하율, 스크리닝 위반 여부\n"
        "- 모선 전압(pu)·위상각, 선로·변압기 P/Q와 부하율\n"
        "- 전압 높은/낮은 순, 선로·변압기 부하율 높은 순 Top N\n\n"
        "예: \"Bus 137 전압은?\", \"부하율이 가장 높은 선로 5개 알려줘.\""
    )


def create_mongolia_agent_graph(
    load_preview: PreviewLoader,
    model_name: str | None = None,
    base_url: str | None = None,
    model=None,
):
    """Compile an agent ↔ tools loop bound only to the Mongolia read-only tools."""
    tools = create_mongolia_tools(load_preview)

    if model is None:
        resolved_base_url = base_url or LLM_BASE_URL
        client_kwargs = get_llm_client_kwargs(resolved_base_url)
        model = ChatOllama(
            model=model_name or MODEL_NAME,
            base_url=resolved_base_url,
            temperature=0,
            **({"client_kwargs": client_kwargs} if client_kwargs else {}),
        )

    model_with_tools = model.bind_tools(tools)

    def agent_node(state: MessagesState):
        return {"messages": [model_with_tools.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()
