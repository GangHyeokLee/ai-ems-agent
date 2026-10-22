# 몽골 합성 AC PF 읽기 전용 AI 질의응답

몽골 MN_35k 계통의 **사전 계산·검증된 합성 AC PF 결과**를 AI Assistant가 Tool Calling으로 조회해 한국어로 설명한다.
PF 재계산, N-1, Redispatch, OPF, 동특성 분석은 수행하지 않는다.

## 구성

| 구분 | KPG-193 | 몽골 |
|---|---|---|
| Agent | `ai_ems.agent.graph.create_agent_graph` (변경 없음) | `ai_ems.agent.mongolia_graph.create_mongolia_agent_graph` |
| Tool | PyPowSyBl 분석 Tool (`ai_ems.agent.tools`) | 읽기 전용 조회 Tool 5개 (`ai_ems.agent.mongolia_tools`) |
| 시스템 프롬프트 | `SYSTEM_PROMPT` | `MONGOLIA_SYSTEM_PROMPT` |
| Chat API | `POST /api/chat` (계약 유지) | `POST /api/grids/mn-osm-35k/chat` |
| 세션 저장소 | `agent_sessions` | `mongolia_agent_sessions` |

`POST /api/grids/kr-kpg193/chat`은 기존 `/api/chat`과 같은 KPG Agent·세션으로 전달된다.

## Tool

| Tool | 입력 | 내용 |
|---|---|---|
| `get_mongolia_pf_summary` | 없음 | 시나리오, 수렴 상태, 해석 대상/제외 설비 수, 전압 범위, 최대 부하율, 스크리닝 위반, 합성 발전기·부하·리액터, 모델 신뢰성 |
| `get_mongolia_bus_pf` | `bus_id` | 정격전압, 전압 pu, 위상각, PV/PQ/Slack, 전압 위반 |
| `get_mongolia_line_pf` | `line_id` | 양단 모선, P0/Q0·P1/Q1(원본 부호), 정격, 부하율, 열적 위반 |
| `get_mongolia_transformer_pf` | `transformer_id` | 양단 모선·정격전압, P/Q, 정격, 부하율, 미검증 안내 |
| `get_mongolia_pf_ranking` | `metric`, `top_n`(기본 5, 최대 10) | `bus_voltage_high`, `bus_voltage_low`, `line_loading_high`, `transformer_loading_high` Python 정렬 결과 |

조회 결과 `status`:

- `ANALYZED`: 검증된 계산값
- `EXCLUDED_FROM_ANALYSIS`: 지도에는 있으나 PF 해석 대상(166모선/156선로) 제외. 값 없음
- `NOT_FOUND`: 존재하지 않는 ID
- `DATA_UNAVAILABLE`: PF JSON 누락·검증 실패. 값 없음

P0/Q0는 bus1 단자, P1/Q1은 bus2 단자 값이며 각 단자에서 모선 → 설비 방향 조류가 양수다.

## 데이터 검증

- 모든 Tool 호출은 `load_mongolia_pf_preview()`를 매번 거친다(고정 시나리오, 지도 fingerprint, 수렴, 스크리닝, NaN/Infinity 차단).
- Tool은 파일 경로를 입력받지 않는다.
- Chat API는 LLM 호출 전에 데이터를 검증하며, 누락 시 404, 검증 실패 시 422를 반환하고 LLM을 호출하지 않는다.

## 안전 장치

- 몽골 Agent에는 몽골 조회 Tool만 bind된다. 응답에 몽골 외 Tool 호출이 있으면 502로 거부한다.
- N-1·상정사고·Redispatch·OPF·민감도·동특성·출력 변경 재계산 요청은 LLM 호출 없이 "지원하지 않음" 고정 안내로 답한다.
- LLM 연결·실행 실패 시 503을 반환하며 답변을 생성하지 않고 세션 이력도 저장하지 않는다.
- 응답의 `tools_used`에 실제 호출된 Tool 이름이 담긴다.

## Grid Capability

| 필드 | 의미 | KPG | 몽골 |
|---|---|---|---|
| `analysis_available` | KPG 전체 분석 묶음(하위 호환) | true | false |
| `security_analysis_available` | 선로 N-1 Security Analysis (몽골은 [별도 사양](MONGOLIA_SECURITY_ANALYSIS.md)) | true | XIIDM 검증 성공 시 true |
| `pf_preview_available` | PF 미리보기 파일 존재(요청 시 검증) | true | 지도+PF 파일 존재 시 true |
| `chat_available` | AI Assistant 사용 가능 | true | 지도+PF 파일 존재 시 true |
| `chat_mode` | Agent 종류 | `analysis` | `pf_read_only` |

## UI

- 몽골 선택 시 채팅은 합성 PF 조회 전용으로 활성화된다. 채팅에서는 N-1·민감도를 호출할 수 없으며, 선로 N-1과 Sensitivity는 Security Analysis 패널에서 실행한다([Sensitivity 사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)). Generator Contingency 버튼은 비활성 상태를 유지한다.
- 채팅 로그와 세션 ID는 현재 페이지에서 `grid_id`별로 유지·복원한다. 늦은 응답은 요청한 계통의 기록에만 저장하고, 이전 화면의 분석 결과는 재적용하지 않는다. 새로고침 시에는 새 대화를 시작한다([세션 복원 검증](../validation/grid-chat-session-restore.md)).
- 몽골 답변은 지도 분석 강조(`ui_updates`)를 적용하지 않는다.

## 예시 질문

- "Bus 137 전압은?"
- "부하율이 가장 높은 선로 5개 알려줘."
- "몽골 합성 PF 결과에서 전압 위반 모선이 있나?"
- "선로 255335646-1_0의 조류 알려줘."
