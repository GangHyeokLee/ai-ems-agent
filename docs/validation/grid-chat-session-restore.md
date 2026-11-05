# 계통별 AI 채팅 기록 및 세션 복원

- 작업일: 2026-10-22 (Asia/Seoul)
- 기준: `feature/kems-multi-grid` / `a85bea2d85b874a4e79df402cad2cb0c7e74d0a5`
- 작업 브랜치: `feature/grid-chat-session-restore`

## 상태와 세션

`ui/index.html`의 `gridChats: Map<grid_id, {sessionId, messages, pending}>`에 페이지가
열려 있는 동안의 대화를 저장한다. 국가 코드 대신 전체 계통 ID를 키로 사용한다.
계통을 처음 방문하면 UUID를 만들고, 이후 전환·후속 요청에서도 같은 ID를 사용한다.
종전의 단일 `sessionStorage` 세션 ID는 읽거나 재사용하지 않는다. 새 페이지에서는
메시지와 세션 ID를 함께 새로 시작한다.

계통 전환은 채팅 DOM을 비우고 선택한 계통의 메시지를 순서대로 다시 렌더링한다.
이때 기록에 메시지를 다시 추가하지 않는다. 기존 Markdown 렌더링과 오류 표시를 재사용하며,
도움말과 placeholder를 현재 계통에 맞춘다. 미전송 입력은 전환 시 비운다.
주황색 Tool 근거 박스는 생성하지 않는다.

서버의 `agent_sessions`와 `mongolia_agent_sessions`, 기존 `/api/chat` 및
`/api/grids/{grid_id}/chat` 계약은 변경하지 않았다. 계통별 기존 세션 ID를 다시 보내므로
서버가 저장한 실제 ToolMessage와 대화 이력을 후속 질문에서 사용한다.
LangGraph·PyPowSyBl·도메인 Tool 및 Caddy 코드·설정은 변경하지 않았다.

## 진행 중 요청과 늦은 응답

- 요청 시작 시 해당 계통의 `pending`을 설정하고 같은 세션의 추가 전송을 차단한다.
  다른 계통에서는 독립적으로 채팅할 수 있다.
- 성공·오류 응답은 요청한 계통의 기록에 한 번만 추가한다. 해당 계통이 선택돼 있을 때만
  채팅 DOM에도 추가한다. 계통 전환을 이유로 요청을 취소하거나 세션을 교체하지 않는다.
- 카드·지도·분석 입력값 갱신은 기존 `gridSwitchToken`과 요청 계통 검사를 통과해야 한다.
  요청 도중 다른 계통으로 갔다가 돌아와도 이전 화면의 토큰은 무효다. 답변은 복원하지만
  오래된 분석 결과는 적용하지 않는다.
- 완료 시 해당 계통의 `pending`을 해제한다. 원래 계통으로 먼저 돌아와 기다리던 경우에도
  입력과 전송 버튼을 정상 활성화한다.

계통 변경 시 분석 카드·지도·입력값은 기존 정책대로 초기화한다. 채팅 기록 복원으로
과거 `ui_updates`를 재실행하지 않는다. 패널 접힘·펼침과 수동 분석 동작도 유지한다.

## 검증

새 브라우저 서버 `tests/browser/chat_session_app.py`는 실제 FastAPI 채팅 핸들러와
세션 저장소, 몽골 LangGraph·도메인 Tool 및 KPG 도메인 Tool을 사용한다. 모델의 도구
선택만 스크립트로 대체한다. 미리 계산한 채팅 응답으로 세션 복원을 검증하지 않는다.
후속 요청은 서버 이력의 이전 ToolMessage에서 사고 선로를 찾아야 성공하며, 다른 계통의
대화가 섞이면 테스트 서버가 거부한다. 실제 Ollama 자연어 추론 검증은 이번 범위에 없다.

새 브라우저 probe의 35개 검사가 다음을 확인했다.

- KPG → 몽골 → KPG 및 역방향 기록·Markdown·세션 복원, 반복 전환 시 순서와 개수 유지.
- 같은 국가의 다른 `grid_id`도 별도 세션 사용.
- 두 계통의 전환 후 후속 민감도 요청이 실제 서버의 이전 사고 문맥 사용.
- 몽골 요청 중 KPG 채팅 가능, 늦은 성공·오류가 KPG 채팅·카드·지도·입력값을 변경하지 않음.
- 백그라운드에서 완료된 답변 복원과 후속 질문, 대기 중 원래 계통 복귀 시 중복 전송 차단,
  답변 1회 반영과 입력 잠금 해제.
- 이전 화면의 분석 결과 재적용 차단, 패널 접힘 유지, 도움말·placeholder 갱신.
- 새로고침 시 기록과 세션을 함께 초기화하고 기존 단일 저장소 ID는 재사용하지 않음.

기존 몽골 채팅 probe는 기록 삭제 검사 대신 독립 세션·순서 복원 검사를 수행하며,
기존 분석 카드·지도·계통 전환·지연 응답 검사를 유지한다. 패널 상태 probe의 접힘·펼침
12개 조합과 수동 분석 동작 83개 검사도 유지한다.

실행 명령:

```bash
.venv/bin/python -m pytest -q tests/test_ui_mongolia_chat.py
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_browser_stale_responses.py::test_grid_chat_restores_history_and_real_server_context tests/test_browser_stale_responses.py::test_mongolia_analysis_chat_cards_maps_and_stale_responses tests/test_browser_stale_responses.py::test_chat_preserves_analysis_panel_state_on_both_grids
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
```

정적 UI 검사 `23 passed`, 관련 브라우저 검사 `3 passed`, 전체 기본 회귀
`381 passed, 13 skipped, 1 warning`, 브라우저 포함 전체 회귀
`394 passed, 1 warning`. 기본 실행의 13개 skip은 선택 실행 방식의
브라우저 테스트이며, 경고 1건은 기존 Starlette/AnyIO deprecated alias 경고다.
브라우저 산출물은 `outputs/browser_chat_session_restore/results.json`,
`outputs/browser_mongolia_chat/results.json`, `outputs/browser_chat_panel_state/results.json`에
저장한다(gitignored).

## 보존 범위

현재 페이지 생명주기만 지원한다. 새로고침·새 탭·새 페이지에서는 새 대화이며,
서버 재시작 후 문맥 복원은 보장하지 않는다. DB·Redis·계정별 저장·계통별 새 대화 버튼은
추가하지 않았다. 서버의 기존 메모리 세션 수명 정책도 변경하지 않았다.
