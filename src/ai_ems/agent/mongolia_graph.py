"""Mongolia PF and validated line analysis agent, isolated from all KPG tools.
"""

from __future__ import annotations

import re

from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ai_ems.agent.mongolia_tools import (
    MONGOLIA_TOOL_NAMES, PreviewLoader, SecurityLoader, create_mongolia_tools,
)
from ai_ems.config import LLM_BASE_URL, MODEL_NAME, get_llm_client_kwargs

MONGOLIA_SYSTEM_PROMPT = """
당신은 몽골 송전망(MN_35k)의 합성 PF 조회 및 선로 N-1 Security·Sensitivity AI 어시스턴트입니다.
PF는 사전 검증 결과를 조회하며, 분석은 검증된 몽골 도메인 Tool로만 실행합니다.

사용 가능한 Tool:
- get_mongolia_pf_summary: 계통 전체 요약(수렴 상태, 설비 수, 전압 범위, 최대 부하율, 스크리닝 위반, 합성 설비 정보)
- get_mongolia_bus_pf: 모선 1개의 전압(pu)·위상각·PV/PQ/Slack
- get_mongolia_line_pf: 선로 1개의 P0/Q0, P1/Q1, 정격, 부하율
- get_mongolia_transformer_pf: 변압기 1개의 P/Q, 정격, 부하율
- get_mongolia_pf_ranking: 전압 높은/낮은 순, 선로·변압기 부하율 높은 순 Top N(최대 10)

- run_mongolia_line_contingency: 원본 선로 ID의 N-1 Security Analysis
- run_mongolia_line_sensitivity: 사고 후 합성 발전기 주입 민감도, 감시 선로 생략 시 자동 선택, 기본 후보 5개

연속 실행·후속 요청:
- "N-1 후 민감도 Top 5"는 Security Tool 결과를 확인한 뒤 Sensitivity Tool을 호출한다.
- "이어서 민감도" 등 후속 요청은 대화의 직전 실제 분석 Tool 결과에 있는 outage_line_id를 사용한다.
- 사고 선로 ID가 없고 이전 Tool 결과도 없으면 ID를 요청한다. ID를 추측하지 않는다.
- Tool 오류/검증 실패/비수렴은 성공으로 설명하지 않는다.
- NOT_EVALUATED_ISLANDING은 민감도 미평가이며 후보·순위가 없다. 분리 섬의 PF도 계산하지 않는다.

반드시 지킬 규칙:
1. 계통 숫자(전압, 조류, 부하율, 설비 수 등)가 필요한 질문에는 반드시 Tool을 호출한다.
2. Tool 결과에 없는 전압·조류·발전량·순위를 만들어 내지 않는다. 순위는 get_mongolia_pf_ranking 결과만 사용한다.
3. 합성 PF 결과와 실제 몽골 운전계통을 구분한다. 실제 운영값이라고 표현하지 않는다.
4. PF 수렴은 수치해가 존재한다는 뜻일 뿐, 실제 계통 운영 적합성이나 안정성을 의미하지 않는다.
5. snapshot 2013-01-01 00:00:00은 모델 시점 표기이며 실제 측정 날짜가 아니다.
6. 합성 발전기 45대는 실제 몽골 발전소 45곳이 아니다.
7. 지도에 표시된 발전소 데이터 27건은 실제 계통 접속이 검증되지 않았다.
8. 선로·변압기의 전기정수와 일부 길이·회선수는 실설비로 검증되지 않았다.
9. Redispatch, OPF, 발전기 사고·탈락, 발전기 출력·부하 변경 재계산, 동특성은 지원하지 않는다.
   선로 N-1과 민감도만 분석 Tool로 실행한다. 경로·전기정수·다른 계통을 입력하지 않는다.
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
    ("발전기 사고·탈락", re.compile(r"(?:발전기|generator|GEN[-_]\S+)\s*(?:[\w#-]+\s+)?(?:사고|탈락|고장|정지|outage|trip)|(?:사고|탈락|outage)\s*(?:발전기|generator)", re.IGNORECASE)),
    ("Redispatch(재급전)", re.compile(r"re-?dispatch|재급전|리디스패치", re.IGNORECASE)),
    ("OPF(최적조류)", re.compile(r"(?<![A-Za-z])OPF(?![A-Za-z])|최적\s*조류|optimal\s+power\s+flow", re.IGNORECASE)),
    ("동특성·과도안정도 해석", re.compile(r"동특성|과도\s*안정|dynamic\s+simulation|transient", re.IGNORECASE)),
    (
        "발전기 출력·설비 상태를 바꾼 재계산",
        re.compile(r"(출력|발전량|부하)\S*\s*(을|를)?\s*\S*\s*(늘|줄|증가|감소|올려|내려|변경|조정|바꿔)|재계산|다시\s*계산", re.IGNORECASE),
    ),
]


def detect_unsupported_request(message: str) -> list[str]:
    """Return labels of calculation requests that the Mongolia agent must not attempt."""
    return [label for label, pattern in UNSUPPORTED_PATTERNS if pattern.search(message)]


def unsupported_request_answer(labels: list[str]) -> str:
    requested = ", ".join(labels)
    return (
        f"요청하신 **{requested}**은(는) 몽골 AI 채팅에서 지원하지 않습니다. 실제 계산을 수행하지 않았습니다.\n\n"
        "몽골 Assistant는 합성 PF 조회, 선로 N-1 Security, 사고 후 합성 발전기 주입 Sensitivity를 지원합니다. "
        "Redispatch·OPF·발전기 사고·출력 변경 재계산·동특성은 지원하지 않습니다. "
        "합성 모델이며 실제 몽골 운영 데이터가 아닙니다."

    )


class DisallowedMongoliaTool(ValueError):
    """Reject an unbound tool before ToolNode or another model turn executes."""


def requests_analysis(message: str) -> bool:
    return bool(re.search(r"N\s*[-−]?\s*1|security|sensitivity|민감도|상정|사고|탈락|contingency", message, re.I))


def create_mongolia_agent_graph(
    load_preview: PreviewLoader,
    model_name: str | None = None,
    base_url: str | None = None,
    model=None,
    load_security: SecurityLoader | None = None,
):
    """Compile an agent ↔ tools loop bound only to the Mongolia tools."""
    tools = create_mongolia_tools(load_preview, load_security)

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
        response = model_with_tools.invoke(state["messages"])
        disallowed = {call["name"] for call in response.tool_calls} - MONGOLIA_TOOL_NAMES
        if disallowed:
            raise DisallowedMongoliaTool(", ".join(sorted(disallowed)))
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()
