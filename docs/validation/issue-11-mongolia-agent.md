# Issue #11 — 몽골 Agent 분석 연계 검증

- 검증일: 2026-10-21 (Asia/Seoul)
- Issue: https://github.com/GangHyeokLee/ai-ems-agent/issues/11
- Draft PR: https://github.com/GangHyeokLee/ai-ems-agent/pull/12
- 기준: `feature/kems-multi-grid` / `e8687171d2e6d5d018c5f3d20fd179387dbeef60`
- 작업 브랜치: `feature/mn-agent-analysis`

## 구현

기존 PF 조회 Tool 5개를 유지하고 `run_mongolia_line_contingency`,
`run_mongolia_line_sensitivity`를 추가했다. 두 Tool은 기존
`MongoliaSecurityService.run_line_contingency()`와 `run_line_sensitivity()`만 호출한다.
기존 서비스의 모델 검증, 전용 LF 설정, 독립 network 생성, 유한차분 검증을 그대로 사용한다.

분석 Tool은 원본 사고 선로 ID, 선택 감시 선로 ID, 1~10개의 후보 수(기본 5)만 받는다.
추가 입력 필드는 거부한다. 단일 요청은 Security 결과를 확인한 뒤 Sensitivity를 호출하고,
멀티턴은 몽골 대화에 남아 있는 실제 Tool 결과의 사고 선로 ID를 사용하도록 구성했다.

분석 요약과 `ui_updates`는 호출 ID·Tool 이름·계통 ID·사고 선로 ID가 맞는 실제
ToolMessage artifact에서 생성한다. LLM 자유서술의 수치나 JSON은 결과로 사용하지 않는다.
실패·비수렴·미평가는 서버에서 결과에 맞게 설명한다. 미허용 Tool은 실행 전에 거부한다.
몽골 결과는 기존 공통 카드와 몽골 지도 어댑터로 전달하며 KPG 민감도 변환기를 사용하지 않는다.

계통별 Tool·대화 저장소·계산 모델을 분리했다. UI는 전환 토큰과 계통 ID를 확인하며,
몽골 채팅 갱신 시 오래된 패널 요청을 무효화한다. 다른 사고의 후속 민감도는 이전 Security
카드를 초기화한다. 채팅 완료 후 검증된 Security 결과의 Sensitivity 버튼 상태를 유지한다.
도움말과 placeholder는 Security·Sensitivity 검증 상태를 각각 반영한다.

## 인수 결과

| 시나리오 | 확인 결과 |
| --- | --- |
| `751261406-1_0` N-1 | `CONVERGED`, 합성 스크리닝 위반 0건 |
| 후속 Sensitivity | 감시 선로 자동 선택 `484276912-1_0`, 후보 5개, 서비스 직접 호출 결과와 일치 |
| 단일 요청 Security → Sensitivity | 실제 LangGraph에서 연속 실행, 두 결과 카드와 지도 반영 |
| 멀티턴 “이어서 민감도” | 이전 실제 몽골 Tool 결과에서 사고 ID 사용, 새 민감도 갱신만 반환 |
| `484333456-1_0` | 분리 11모선, 섬 PF 미실행, `NOT_EVALUATED_ISLANDING`, 후보 0개·순위 없음 |
| 오류 처리 | 없는/제외 선로, 잘못된 감시 선로, 범위 밖 후보 수, 추가 필드 거부 |
| 검증·호출 장애 | preview/XIIDM 누락·검증 실패, 민감도 검증 실패, Tool 오류·비수렴에서 성공 주장 차단 |
| LLM 장애·미허용 Tool | 오류 응답, 계산 성공 또는 UI 결과 생성 없음 |
| PF 유지 | 기존 조회 테스트 통과, XIIDM 또는 Sensitivity 불가 상태에서도 PF 조회 가능 |
| KPG 회귀 | 기존 채팅·Tool Calling·Security/Sensitivity·발전기 사고·Redispatch 테스트 유지 |
| 공통 UI | 기존 패널·지도·테마·도움말 검증과 몽골 채팅 검증 통과 |
| 계통 전환 | 한국↔몽골 카드·지도·채팅 초기화, 양방향 늦은 성공/실패 응답 차단 |

## 전체 테스트

```bash
.venv/bin/python -m pytest -q tests
# 326 passed, 11 skipped, 1 warning (17.54s)

AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
# 337 passed, 1 warning (109.32s)
```

기본 실행의 11개 skip은 opt-in 브라우저 테스트다. 브라우저 포함 실행에서는 skip이 없다.
공통 경고 1건은 기존 Starlette TestClient의 AnyIO BlockingPortal deprecated alias 경고다.
FastAPI TestClient가 제한 sandbox에서 정체되어, 로컬 서버·Chrome·JVM 계산이 가능한
기본 실행 환경에서 두 명령을 실행했다.

몽골 분석 채팅 브라우저 검사 33개가 모두 통과했다. 기존 Security/Sensitivity 패널,
KPG 채팅, 테마·도움말, 지도, 계통 전환 및 늦은 응답 검사도 전체 실행에 포함됐다.

로컬 산출물(gitignored):
- `outputs/mn_agent_analysis/pytest.log`, `pytest-browser.log`
- `outputs/mn_agent_analysis/ollama_access.json` (HTTP 상태만 기록, 인증값 제외)
- `outputs/browser_mongolia_chat/results.json`, `chain.png`
- 기존 `outputs/browser_stale_responses`, `browser_unified_ui`, `browser_theme_help` 결과·화면

## 검증 방법과 한계

API·브라우저 인수 검증은 scripted 모델과 **실제 LangGraph 및 도메인 Tool**을 결합했다.
정상/분리 사고는 실제 PyPowSyBl 계산을 사용했다. 비수렴·provider 오류 일부는 장애 주입으로
검증했다. 브라우저 fixture는 실제 채팅 핸들러가 만든 응답을 테스트 proxy로 전달한다.

실제 Ollama 서버의 `/api/tags` 접근이 HTTP 502로 실패했다. 따라서 실제 LLM의 자연어
Tool 선택, 연속 호출 및 멀티턴 추론은 미검증이다. 접근 복구 후 다음 smoke test가 남는다.

1. “751261406-1_0 선로 N-1 후 민감도 Top 5를 분석해 줘.”
2. 새 세션에서 같은 선로의 N-1을 요청한 뒤 “이어서 민감도 후보 5개 알려줘.”
3. “484333456-1_0 선로 N-1 후 민감도를 분석해 줘.”
4. “Bus 137 전압은?” 및 한국↔몽골 전환 후 KPG 채팅.

합성 모델의 결과이며 실제 몽골 운영 데이터나 실제 발전소 순위가 아니다.
발전소 지도 레코드 27건과 합성 발전기 45대를 구분한다.
Redispatch·OPF·발전기 사고·출력 변경 재계산·동특성 신규 구현은 포함하지 않았다.

## 변경 파일

- `src/ai_ems/agent/mongolia_tools.py`: 분석 Tool 및 제한 입력 스키마.
- `src/ai_ems/agent/mongolia_graph.py`: 몽골 prompt·허용 Tool·요청 라우팅.
- `web_app.py`: 서비스 연결, 분석 결과 근거 확인, 서버 요약·몽골 UI 갱신.
- `src/ai_ems/grid_catalog.py`, `ui/index.html`: capability·안내·공통 카드/지도 연계.
- `tests/test_mongolia_analysis_chat.py`: 실제 도메인 Tool 기반 분석 채팅 인수 테스트.
- `tests/browser/mongolia_chat_fixtures.py`, `mongolia_chat_probe.js`: scripted 모델 및 브라우저 검증.
- 기존 몽골 Tool/API/UI·테마 테스트와 브라우저 harness: 새 지원 범위 반영.

166모선 모델, XIIDM, NetCDF, 원본 지도·OSM 참조 데이터, 몽골/KPG 계산 엔진은 변경하지 않았다.
`main`, 기준 브랜치와 상위 PR #2는 변경하지 않았다. PR은 Draft 상태이며 merge·브랜치 삭제를 수행하지 않는다.

Refs #11
