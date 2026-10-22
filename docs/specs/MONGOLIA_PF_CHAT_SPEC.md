# 몽골 AI Assistant — PF 읽기 전용 조회 및 분석 Tool 연계

몽골 MN_35k 계통의 AI Assistant는 **사전 계산·검증된 합성 AC PF 결과 조회**와
**선로 N-1 Security 및 사고 후 Sensitivity Tool Calling**을 지원한다.
기존 PF 조회 Tool 5개는 읽기 전용이며, PF를 재계산하거나 설비 상태·출력을 변경하지 않는다.
선로 분석은 별도 Tool 2개가 검증된 몽골 도메인 서비스를 호출해 수행한다.

이 문서는 PF 조회 계약과 Agent의 지원 범위·채팅 연계를 다룬다. 계산 모델·설정·검증·분석 API는
[Security 사양](MONGOLIA_SECURITY_ANALYSIS.md)과 [Sensitivity 사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)을 따른다.
채팅 분석 지원은 PR #12에서 추가됐으며, 기존 PF 조회 Tool 자체의 읽기 전용 범위와 구분한다.

## 구성

| 구분 | KPG-193 | 몽골 |
|---|---|---|
| Agent | `ai_ems.agent.graph.create_agent_graph` (변경 없음) | `ai_ems.agent.mongolia_graph.create_mongolia_agent_graph` |
| Tool | PyPowSyBl 분석 Tool (`ai_ems.agent.tools`) | 읽기 전용 PF 조회 5개 + 선로 분석 2개 (`ai_ems.agent.mongolia_tools`) |
| 시스템 프롬프트 | `SYSTEM_PROMPT` | `MONGOLIA_SYSTEM_PROMPT` |
| Chat API | `POST /api/chat` (계약 유지) | `POST /api/grids/mn-osm-35k/chat` |
| 세션 저장소 | `agent_sessions` | `mongolia_agent_sessions` |

`POST /api/grids/kr-kpg193/chat`은 기존 `/api/chat`과 같은 KPG Agent·세션으로 전달된다.

## PF 조회 Tool (읽기 전용)

| Tool | 입력 | 내용 |
|---|---|---|
| `get_mongolia_pf_summary` | 없음 | 시나리오, 수렴 상태, 해석 대상/제외 설비 수, 전압 범위, 최대 부하율, 스크리닝 위반, 합성 발전기·부하·리액터, 모델 신뢰성 |
| `get_mongolia_bus_pf` | `bus_id` | 정격전압, 전압 pu, 위상각, PV/PQ/Slack, 전압 위반 |
| `get_mongolia_line_pf` | `line_id` | 양단 모선, P0/Q0·P1/Q1(원본 부호), 정격, 부하율, 열적 위반 |
| `get_mongolia_transformer_pf` | `transformer_id` | 양단 모선·정격전압, P/Q, 정격, 부하율, 미검증 안내 |
| `get_mongolia_pf_ranking` | `metric`, `top_n`(기본 5, 최대 10) | `bus_voltage_high`, `bus_voltage_low`, `line_loading_high`, `transformer_loading_high` Python 정렬 결과 |

위 5개 Tool의 조회 결과 `status`:

- `ANALYZED`: 검증된 계산값
- `EXCLUDED_FROM_ANALYSIS`: 지도에는 있으나 PF 해석 대상(166모선/156선로) 제외. 값 없음
- `NOT_FOUND`: 존재하지 않는 ID
- `DATA_UNAVAILABLE`: PF JSON 누락·검증 실패. 값 없음

P0/Q0는 bus1 단자, P1/Q1은 bus2 단자 값이며 각 단자에서 모선 → 설비 방향 조류가 양수다.

## 분석 Tool 연계

| Tool | 입력 | 내용 |
|---|---|---|
| `run_mongolia_line_contingency` | `outage_line_id` | 원본 선로 ID의 N-1 Security Analysis ([상세 사양](MONGOLIA_SECURITY_ANALYSIS.md)) |
| `run_mongolia_line_sensitivity` | `outage_line_id`, 선택 `monitored_line_id`, `top_n`(기본 5, 1~10) | 사고 후 합성 발전기 주입에 대한 감시 선로 조류 민감도 ([상세 사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)) |

단일 요청의 “N-1 후 민감도 Top 5”는 Security 결과를 확인한 뒤 Sensitivity를 호출한다.
“이어서 민감도”는 같은 몽골 세션의 이전 실제 분석 Tool 결과에서 사고 선로 ID를 사용한다.
사고 ID와 이전 결과가 모두 없으면 ID를 요청하며 추측하지 않는다.
분석 Tool은 기존 `MongoliaSecurityService`의 검증·계산을 재사용한다. 계통 분리 사고의
민감도는 `NOT_EVALUATED_ISLANDING`으로 미평가하고 후보·순위를 만들지 않는다.
민감도는 합성 발전기의 주입 민감도이며 실제 발전소 순위나 Redispatch 권고가 아니다.

## 데이터 검증

- PF 조회 Tool 호출은 `load_mongolia_pf_preview()`를 매번 거친다(고정 시나리오, 지도 fingerprint, 수렴, 스크리닝, NaN/Infinity 차단).
- 분석 Tool의 XIIDM 모델·사고 결과·민감도 검증은 위 Security/Sensitivity 사양을 따른다.
- 조회·분석 Tool 모두 파일 경로를 입력받지 않는다.
- Chat API는 LLM 호출 전에 데이터를 검증하며, 누락 시 404, 검증 실패 시 422를 반환하고 LLM을 호출하지 않는다.

## 안전 장치

- 몽골 Agent에는 위 몽골 전용 Tool 7개만 bind된다. 허용 목록 밖 Tool 호출은 실행 전에 차단하고 Chat API는 502로 거부한다. KPG Tool을 사용하지 않는다.
- Redispatch, OPF, 발전기 N-1·탈락·상정사고, 동특성, 발전기 출력·부하·설비 상태를 바꾼 재계산 요청은 LLM 호출 없이 "지원하지 않음" 고정 안내로 답한다. 선로 N-1과 사고 후 합성 발전기 주입 Sensitivity는 지원한다.
- 분석 답변과 `ui_updates`는 호출 ID·Tool 이름·계통·사고 ID가 확인된 실제 ToolMessage artifact에서 생성한다. 오류·비수렴·미평가를 계산 성공이나 후보 순위로 설명하지 않는다.
- LLM 연결·실행 실패 시 503을 반환하며 답변을 생성하지 않고 세션 이력도 저장하지 않는다.
- 응답의 `tools_used`에 실제 호출된 Tool 이름이 담긴다.

## Grid Capability

| 필드 | 의미 | KPG | 몽골 |
|---|---|---|---|
| `analysis_available` | KPG 전체 분석 묶음(하위 호환) | true | false |
| `security_analysis_available` | 선로 N-1 Security Analysis (몽골은 [별도 사양](MONGOLIA_SECURITY_ANALYSIS.md)) | true | XIIDM 검증 성공 시 true |
| `sensitivity_analysis_available` | 사고 후 선로 조류 Sensitivity ([별도 사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)) | true | 민감도 자체 검증(유한차분) 통과 시 true |
| `generator_contingency_available` | 발전기 상정사고 분석 | true | false |
| `pf_preview_available` | PF 미리보기 파일 존재(요청 시 검증) | true | 지도+PF 파일 존재 시 true |
| `chat_available` | AI Assistant 사용 가능 | true | 지도+PF 파일 존재 시 true |
| `chat_mode` | Agent 종류 | `analysis` | `mongolia_analysis` |

Security/Sensitivity 검증 상태는 각각의 capability로 판단한다. 분석을 사용할 수 없어도
검증된 PF 조회는 유지하며, `analysis_available=false`를 몽골의 모든 분석 미지원으로 해석하지 않는다.

## UI

- 몽골 선택 시 채팅에서 PF 조회와 선로 N-1·후속 Sensitivity를 요청할 수 있다. 도움말·placeholder는 Security/Sensitivity의 검증 상태를 반영한다. 기존 패널의 수동 분석도 유지하며 Generator Contingency 버튼은 비활성 상태다.
- 채팅 로그와 세션 ID는 현재 페이지에서 `grid_id`별로 유지·복원한다. 늦은 응답은 요청한 계통의 기록에만 저장하고, 이전 화면의 분석 결과는 재적용하지 않는다. 새로고침 시에는 새 대화를 시작한다([세션 복원 검증](../validation/grid-chat-session-restore.md)).
- PF 조회 답변은 분석 `ui_updates`를 만들지 않는다. 검증된 분석 Tool 결과는 몽골의 공통 Security/Sensitivity 카드와 지도 강조에 반영한다. 계통·화면 토큰 검사를 통과한 응답만 분석 UI에 적용하며, 채팅으로 패널을 강제로 펼치거나 Tool 근거 박스를 만들지 않는다.

## 예시 질문

- "Bus 137 전압은?"
- "부하율이 가장 높은 선로 5개 알려줘."
- "몽골 합성 PF 결과에서 전압 위반 모선이 있나?"
- "선로 255335646-1_0의 조류 알려줘."
- "751261406-1_0 선로 N-1 후 민감도 Top 5를 분석해 줘."
- 같은 몽골 대화에서 N-1 분석 후 "이어서 민감도 후보 5개 알려줘."

PF 조회와 선로 분석 모두 합성 모델 결과이며 실제 몽골 운영 데이터가 아니다.
