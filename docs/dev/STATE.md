# AI-EMS 작업 인수인계 상태

갱신일: 2026-11-05 (Asia/Seoul). 최근 작업과 다음 작업만 유지한다.

## 현재 단계

- Phase 3 완료. Phase 4의 PH4-01 AI 채팅 통합 구현·로컬 검증 완료.
- DOC-01: 개발 규칙과 출처·해시를 갖춘 개발팀 키트/Artifact 참고 자료를 정리하여 통합 브랜치에 반영했다.
- PH4-02A·B0·B1·B2(+MAINT-02)·B3-1은 PR #17·#18·#19·#20·#21로 통합됐다(B3-1 squash `00f6edc`).
- PH4-02B3-2는 [PR #22](https://github.com/GangHyeokLee/ai-ems-agent/pull/22)로 통합됐다(squash `15f634c`). 서버 reference/run과 검증 receipt 경로가 있다.
- PH4-02C-1: KPG 수동 receipt와 기존 결과 설명(reference) UI를 구현했다. 실제 FastAPI/registry/계산을 사용하는 신규 Chrome 검증을 마쳤다.
  [PR #23](https://github.com/GangHyeokLee/ai-ems-agent/pull/23)으로 통합됐다(merge `a77ef8c`). 리뷰 후속 F1·F3 보완을 포함한다.
  MAINT-03은 [PR #24](https://github.com/GangHyeokLee/ai-ems-agent/pull/24)로 통합됐으며, C-1에 일반 merge로 반영했다. 실제 통합 경로의 최종 회귀는 모두 통과했다. 과거 IEEE39 렌더링 불안정성을 해결한 것은 아니므로 병합 권고는 CONDITIONAL이다.
- v1 capability는 어떤 계통에도 광고하지 않는다. C-1 UI는 광고가 있는 테스트 fixture에서만 활성화된다. MN v1·한국 OSM AI는 차단을 유지한다.
- 실시간 브랜치·HEAD·커밋 여부·staged/unstaged/untracked 상태는 `dev_handoff.py` 출력으로 확인한다.
- PH4-02C-2a: run 반영을 위한 공통 상태·commit 리팩터링. [PR #25](https://github.com/GangHyeokLee/ai-ems-agent/pull/25)로 통합됐다(merge `950de21`).
  동작 변경은 없고, run 모드·`mode=run` 전송·run 결과 반영은 구현하지 않았다.
- PH4-02C-2b: run 응답·agent receipt·`ui_updates`의 순수 검증 함수를 `feature/ph4-02c2b-run-validation`([PR #26](https://github.com/GangHyeokLee/ai-ems-agent/pull/26), Draft)에 구현했다.
  화면 연결은 없다(C-2c 미착수).
- PH4-02-INFRA-02: `feature/ph4-02-infra-real-llm-smoke`에서 Gemini LLM smoke 도구를 인수·보완했다. 실제 LLM `--execute`는 실행하지 않았다.
  Draft PR 생성과 최신 통합 브랜치 기준 diff/충돌 확인이 남아 있다.
- 다음 기능 작업은 [PH4-02 #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14)의 C-2c(run 결과 반영·잠금·상태별 UI)이며 자동 착수하지 않는다.

## 최근 작업: PH4-02-INFRA-02 — LLM Smoke Test 인수

- 기존 Gemini 구현·두 파일을 유지했다. Smoke 전용 기본 URL을 제거하고 실제 실행에는 명시된 HTTPS origin과 기존 인증 설정 계약을 요구한다.
  접속 확인 실패 시 후속 LLM 호출과 계통 계산을 건너뛰며, Tool 호출·선로 인자는 in-memory ToolMessage에서, receipt는 API 응답에서 검증한다.
- `.env`, Ollama/Caddy/Tailscale, 서버·모델·계통 데이터는 변경하지 않았다. Tailscale 접근 정책은 실행 환경 설정이므로 실제 연결 전까지 확인되지 않았다.
- 최종 검증: `tests/test_smoke_test_llm.py` **12 passed, 1 기존 Starlette deprecation warning**.
  CLI Dry Run은 URL 미설정을 표시하고 실행을 건너뛰었다. CLI Mock은 **10/10 PASS**.
  첫 검증 보강 중 ToolMessage import 누락으로 3개 실패했으며 수정 후 최종 실행에서 모두 통과했다. 실제 LLM `--execute` 미실행.
- 기준 통합 HEAD는 `aebf3d6`까지 fast-forward했다. Draft PR은 `feature/kems-multi-grid` 대상으로 생성 후 이 항목을 갱신한다.

## 최근 작업: PH4-02C-2b — Run 응답·Receipt 순수 검증

- 기준은 `950de21`이며 별도 worktree에서 작업했다. 변경 파일은 `ui/console/api-analysis.js`와 신규 테스트, 문서다.
  bridge·chat·서버·`web_app.py`·데이터·검증 해시는 그대로다.
- `KemsApiAnalysis.runResponse(payload, {gridId, context}, requestId, sessionId)`: B3-2 `_execute_run` 응답 전체를 검증한다.
  하나라도 맞지 않으면 `Invalid`를 던지고, 계약이 모두 맞은 뒤의 만료만 `RECEIPT_EXPIRED`다.
  반환값은 frozen `{status, reasonCode, message, answer, modelRevision, expiresAt, receipts, items}`이며, `items`는 C-2a `prepareAnalysisResults` 입력 형식이다.
- `agentReceipt`는 C-1 `manualReceipt`와 공용 검사(`receiptShape`·`assessmentMatches`·`receiptTimes`)를 함께 쓴다. 수동 판정은 그대로다.
  결과 데이터는 기존 `securityCommon`/`sensitivityCommon`으로 지도 없이 검사한다(지도 대응은 C-2c의 prepare).
- 검증(worktree `.local/ph4-02c2b/20261105-160144/`)
  - 신규 Chrome 실행 시험: **1 passed, 검사 108개**. 실제 B3-2 run 응답 19건(정상·미평가 10, 실패·미실행 9)과 수동 응답 5건을 썼다.
    변조 66종을 781개 응답에 적용해 모두 거부했다. 만료/계약 구분 6건, 순수성(fetch 0·DOM·저장소·입력 불변)도 확인했다.
  - 첫 실행 1 failed는 probe의 `window.KemsApiAnalysis` 참조 오류였고 로그를 보존했다.
  - 정적 **32 passed**. 검증 함수 변이 **10/10** 검출.
  - C-1 + `commit_contract` **11 passed**, MAINT-03 **4 passed**, PH4-01 + Phase 3 **26 passed**.
  - 비브라우저 **978 passed, 119 skipped**.
- 한계: receipt에 결과 digest가 없어 결과 수치의 진위는 클라이언트가 인증하지 않는다. 상세는 `docs/specs/PH4-02C2B_RUN_VALIDATION.md`.

## 보존된 작업: PH4-02C-2a — Revision 분리와 공통 Commit

- 기준은 C-1 HEAD `6928d89`이며 별도 worktree에서 작업했다. 변경 파일은 `api-bridge.js`·`api-chat.js`와 신규 테스트, 문서다.
  서버·`web_app.py`·`api-analysis.js`·데이터·검증 해시는 그대로다.
- `bumpRevision`이 revision을 쓰는 유일한 함수가 됐고, `advanceAnalysis`는 `invalidateAnalysisContext` + `bumpRevision`이다.
  `sessionRetired`는 `bumpRevision`을 쓴다. receipt 칸은 `storeReceipt` 하나로만 쓰고, `clearAnalysis`는 `supersedeAnalysisRequests`를 쓴다.
- 수동 결과는 `prepareAnalysisResults`(순수 검증·VM) → 기존 `current()` → `commitAnalysisResults(view, prepared, {epoch: advanceAnalysis, storeReceipts})`로 반영한다.
  반영 순서(VM → epoch → receipt → mode)는 이전과 같다. chat의 대기 요청은 `request.analysis`(frozen snapshot) 하나로 표현한다.
- C-2c 계약: 대기 중인 chat 요청이 자기 결과를 반영할 때는 `epoch: bumpRevision`을 써야 한다(`advanceAnalysis`면 자기 세션을 retire).
  상세: `docs/specs/PH4-02C2A_COMMON_COMMIT.md`.
- 검증(worktree `.local/ph4-02c2a/20261105-152220/`)
  - 구조 계약 + 정적: **28 passed**.
  - C-1 Chrome + 신규 `commit_contract`: **11 passed (검사 235개)**. `commit_contract`는 리팩터링 전 `6928d89` 사본에서도 14/14 통과했고, 단계별 상세값도 14/14 동일했다.
  - MAINT-03 loading: **4 passed**. PH4-01 + Phase 3: **25 passed, 1 failed**. 실패는 기존 IEEE39 `#sld *` 310/309 변동이며 단독 재실행 2회 통과했다.
  - 비브라우저: **974 passed, 118 skipped** (C-1 965/117 + 신규 9/1).
  - 변이: 구조 9/9, 동작 3/4(나머지 1개는 동등 변이).
  - worktree의 무시 대상 실행 자료는 원본 저장소로 읽기 전용 연결했다. 연결 전 실행의 22 errors·1 failed도 문서에 기록했다.

## 보존된 작업: PH4-02C-1 FINAL — MAINT-03 통합

- `76d9fe8`의 C-1/F1/F3을 보존하고 통합 브랜치 `c7251c3`(PR #24)을 일반 merge했다. 충돌은 STATE 하나였다.
  `api-bridge.js`는 자동 병합됐고 `loadGrid()`는 MAINT-03과 동일하다. KPG 지도 검증·표시 후 PF, 몽골 병렬 조회, view/Abort 검사를 유지한다.
- C-1 fixture의 `READ_GATE`와 초기 map/PF 순서 제어를 제거했다. 오류·지연 주입과 실제 FastAPI/registry/물리 계산은 유지한다.
  F1/F3 파일·기존 테스트 단언·서버·모델·데이터·검증 해시는 변경하지 않았다. F2 문구 기록도 보존했다.
- 실제 통합 검증(증거 `.local/ph4-02c1-final/20261105-integration/`):
  - 기존 문제 5건 + MAINT-03 신규 4건: **9 passed** 두 번(31.51s / 29.63s), 실행마다 235개 검사.
  - C-1 Chrome: **10 passed (45.47s), 221개 검사**, 초기 읽기 28개 전부 HTTP 200, JS 오류·실제 LLM 호출 0.
    동일 세션 Security→Sensitivity/chain, reference 추가 계산 없음, 늦은 응답, F1/F3, 미광고 기본 경로를 확인했다.
  - Phase 3 전체 + PH4-01 Chrome: **27 passed (119.40s)**. Phase 3 21개(스크린샷 22장 생성 포함)와 채팅 6개다.
  - 전체 비브라우저: **965 passed, 117 skipped, 1 warning (94.39s)**. 기존 opt-in/환경 103 + C-1 10 + MAINT-03 4 skip.
    정적·Artifact·라우트 19개 통과는 이 전체 실행의 JUnit에서 확인한 부분집합이다.
  - IEEE39 추가 관찰: **1 passed (22.16s)**. 원본 테스트·단언은 그대로이며 격리 probe 사본에 SVG 관찰값만 추가했다.
    전체 실행과 추가 관찰 모두 원본/콘솔 309개였다. 추가 관찰에서는 viewBox·크기·노드 배열·fingerprint도 같았다.
  - 첫 비브라우저는 실행용 basetemp 상위 폴더 누락으로 **555 passed, 117 skipped, 410 setup errors (65.89s)**였다.
    410개 모두 같은 FileNotFoundError임을 JUnit으로 확인했다. 임시 경로 준비만 고쳐 전체 재실행했으며 초기 로그도 보존한다.
- 판정: 통합 코드의 요청된 최종 회귀는 모두 통과. **전체 병합 권고 CONDITIONAL**: 과거 IEEE39 309/310 변동은 이번에 재현되지 않았으며 원인 해소를 주장하지 않는다.
  F4 브라우저 시계·F5 미확정 응답 폐기 가능성은 분석 사항으로 유지한다. 새 안전 결함은 확인되지 않았다.
- PR #23은 Draft로 유지한다. C-2/C-3, run UI, v1 광고, MN v1 활성화, 병합은 하지 않는다. 상세 근거·명령·리뷰는 C-1 spec의 FINAL 절을 확인한다.

## 보존된 작업: PH4-02C-1 리뷰 후속 보완 (PR #23)

- F1: `api-chat.js` `render()`에서 reference 대기 중에는 모드·분석 종류 선택을 비활성화한다. 성공·실패·timeout·초기화가 끝나면 다시 연다.
  선택 change 처리는 그대로이며, 대기가 없을 때 선택을 바꿔도 receipt와 세션은 유지된다.
- F3: `api-analysis.js` `referenceResponse`는 응답 계약 전체를 검사한 뒤에만 만료를 확인한다(`RECEIPT_EXPIRED`).
  `api-chat.js`는 이를 `분석 결과가 만료되었습니다`로 안내한다. 만료된 결과는 계속 거부하고 세션을 폐기한다. 변조와 만료가 겹치면 계약 불일치로 안내한다.
- F2: 미광고 상태에서 바뀌는 표시 문구 2건을 현재 동작으로 기록하고, 범위를 한정하는 안은 별도 제안으로 남겼다(spec 리뷰 후속 절).
  기본 채팅 `SESSION_BUSY`와 REST `ANALYSIS_BUSY`다. `api-bridge.js`는 MAINT-03과의 충돌을 피하려고 바꾸지 않았다.
- 시험: 신규 Chrome `selection_lock`·`expiry_in_flight`를 추가하고, fixture에 `ttl` 제어를 넣었다. 기존 C-1 사례 8개의 단언은 바꾸지 않았다.
- 검증(`.local/ph4-02c1-review/20261105-135739/`)
  - C-1 Chrome **10 passed (44.81s, 검사 221개)**. 수정 전 `dc415f1` 사본에서는 신규 2개가 F1·F3 검사 4개에서만 실패했다.
  - 정적 **19 passed**. 비브라우저 **965 passed, 113 skipped**.
  - 기존 PH4-01 + Phase 3 Chrome: 수정 후·수정 전 모두 **6 failed / 20 passed**다. 매번 실패하는 기존 경합 4개 외에 실행마다 다른 2개가 실패했다.
  - 원본 Artifact 비교의 ieee39 `#sld *` 309/310 차이는 원본과 콘솔 쪽을 번갈아 나타나는 타이밍 차이였다.
- 남은 리뷰 항목: F4(브라우저 시계 기반 만료, `api-bridge.js`)와 F5(안내 없는 폐기 가능성, 운영 경로 미발견)는 미수정이다.

## 보존된 작업: PH4-02C-1

- 기능 수정은 `api-chat.js`, `api-bridge.js`, `api-analysis.js`, `api-bridge.css` 4개뿐이다. 서버·기존 테스트·원본 UI·계통 데이터·검증 해시는 그대로다.
- bridge가 view UUID/revision/선택 사고·감시/receipt owner를 소유한다. 정상 수동 Security→Sensitivity는 같은 세션을 유지하고, pending reference 취소·대화 초기화·실패 시 세션과 이전 receipt를 폐기한다.
- 기본 채팅은 기존 본문을 유지한다. reference는 receipt ID만 보내며 전체 echo/세션/요청/현재 view·revision/receipt/빈 update를 확인하고 텍스트만 표시한다. 지도·패널·revision을 바꾸지 않는다.
- 등록 실패·최초 AC·만료·모델 변경은 안내하며 자동 재계산하지 않는다. run 모드·결과 지도 적용·capability 공개는 구현하지 않았다.
- 상세 결정·함수·테스트·남은 문제: `docs/specs/PH4-02C1_CONSOLE_REFERENCE.md`.

## 보존된 검증: PH4-02C-1 (이번 리뷰 후속 실행 결과 아님)

- 최종 신규 Chrome **8 passed (35.14s), 187개 브라우저 검사**, 정적/원본/라우트 **19 passed, 1 warning (0.89s)**.
- 전체 비브라우저(B1/B2/B3 포함): **965 passed, 111 skipped, 1 warning (93.64s)**. 기존 opt-in 등 103 + 신규 Chrome 8 skip이며 실패를 skip으로 바꾼 것이 아니다.
- 기존 PH4-01 + Phase 3 Chrome: **5 failed, 21 passed, 1 deselected (81.16s)**. PH4-01 6개 통과. 중복 스크린샷 일괄 생성만 제외했다.
- 동일 실패 5개를 기준 `15f634c` 격리 사본에서도 **5 failed, 16 deselected (18.27s)**로 재현했다. KPG map/PF-preview의 공유 guard 경합(409 ANALYSIS_BUSY)과 후속 KPI/선로 선택 실패다.
- 당시 신규 C-1 fixture만 초기 KPG 지도/PF 읽기 순서를 제어해 위 기존 경합을 준비 단계에서 격리했다. 이번 통합에서 이 우회를 제거했다. 실제 응답·계산·registry·잠금은 그대로다. 별도 기존 회귀의 실패를 숨기거나 통과로 취급하지 않는다.
- 초기 fixture import 오류 8 setup errors, 정적 sandbox 180초 timeout, 보강 중 Chrome 1 failed/7 passed(기준 읽기 경합)는 로그를 보존했다.
- 증거: `.local/ph4-02c1/20261104-222056/`. 최종 Chrome JSON/PNG: `.local/browser-tests/{logs,screenshots}/console_reference_c1/23e909585c6b/`.
- 실제 LLM 호출 없음. reference 전후 graph/도메인/PyPowSyBl 호출 수 불변을 확인했다. 각 신규 테스트 210초·Chrome 150초·서버 기동 60초·HTTP 5초, 기존 테스트 240초·명령 180~1200초+kill 제한.
- 스크린샷은 생성됐으나 Linux Chrome의 한국어 폰트 부재로 일부 글리프가 네모로 표시된다. 한국어 내용은 DOM 문자열 검사로 확인했다.

## 보존된 작업·검증: PH4-02-MAINT-03 (이번 통합 실행 결과 아님)

- 운영 코드 수정은 `ui/console/api-bridge.js:loadGrid()` 8줄 추가·2줄 제거다. KPG 지도 검증·표시 후 현재 view/Abort를 확인하고 PF를 시작한다.
- 몽골 map/GIS/PF 병렬 조회와 한국 OSM 지도 전용 정책은 유지한다. map 실패 시 PF blocked, PF 오류·비수렴·timeout·취소·stale 처리는 기존 경로를 쓴다.
- 서버 guard·모델/데이터·기존 테스트·검증 해시·원본 UI·C-1 receipt/reference를 변경하지 않았다. 별도 신규 Chrome 2파일과 검증 문서를 추가했다.
- 수정 전 목표 5개: **4 failed, 1 passed (21.80s)**. 수정 후 동일 5개가 모두 통과했고, 신규 지연/오류/취소/timeout **4 passed (8.37s), 22개 검사, JS 오류 0**.
- 기존 Phase 3(스크린샷 포함)+PH4-01 Chrome: 두 실행 각각 **1 failed, 26 passed (120.01s / 114.64s)**. 목표 5개·Security/Sensitivity·채팅 6개는 통과했다.
  실패는 `test_console_matches_original_artifact_demo`의 IEEE39 SVG 노드 수 309↔310 차이다. 단언·데모 코드를 수정하거나 실패를 skip 처리하지 않았다.
- Artifact 비교 단독은 정확한 기준 사본 **1 passed (23.37s)**, 수정본 **1 passed (21.02s)**. 전체 실행의 실패를 이 단독 통과로 대체하지 않는다.
- 최종 전체 비브라우저(B1/B2/B3 포함): **965 passed, 107 skipped, 1 warning (83.14s)**. 기존 opt-in/환경 103개 + 신규 Chrome 4개이며 실패를 skip으로 바꾼 것이 아니다.
- 기존 추적 파일 354개와 공급 데이터 62개의 SHA256을 보존했고 `git diff --check`를 통과했다. 전체 검증 판정은 Artifact 비교 불안정성 때문에 CONDITIONAL이다.
- 초기 Chrome 경로 오류 5 failed, 새 취소 시나리오의 계산 완료 관찰 오류 1 failed/8 passed, 첫 비브라우저의 자료 부족 963 passed/109 skipped도 로그를 보존한다.
- 환경: `.env`/비밀값 복사 없이 의존성은 읽기 전용 연결, 필요한 데이터·Artifact·번들·검토 입력만 worktree 내부 Git 제외 경로로 복사해 SHA256을 확인했다.
  캐시·임시 파일·서버/Chrome 증거는 worktree 내부에 둔다. 각 테스트 210/240초와 전체 명령/프로세스 제한을 적용했다. 실제 LLM 호출 없음.
- 증거(MAINT-03 worktree): `.local/maint03/20261105-map-pf/`. 상세 계약·명령·PR #23 충돌 및 남은 위험: `docs/specs/PH4-02-MAINT-03_MAP_PF_READ_RACE.md`.

## 보존된 구현: PH4-02B3-2

- `web_app.py`: run = 단일 worker 단언·예산 확인 → 중복 확인 → 세션 gate → 요청별 `AnalysisJob`/`ExecutionLedger`/graph(기한·recursion 12)
  → `verify_evidence` → network guard 안에서 현재 revision 재검증·REST canonical 변환·`register_all` → 결정적 답변·`ui_updates`·artifact 없는 이력.
  성공·실패 200 응답은 중복 창에 보관하고, receipt가 있는 응답의 재생은 registry·현재 revision을 확인해 아니면 409 `REQUEST_REPLAY_STALE`(reference 재생 포함).
  KPG·MN legacy 채팅 본문은 그대로 옮겨 같은 `(grid, session)` gate 안에서 실행하며 겹칠 때만 `409 {"detail": "SESSION_BUSY"}`.
- `analysis_run.py`(신규): `AI_EMS_SINGLE_WORKER`, 예산(connect 5·read 30·write 10·pool 5초, 실행 기한 30초, 0<값≤600), `AGENT_*` 실패 코드.
  `agent/graph.py`: run 전용 provider timeout, `RunDeadlineExceeded`. `analysis_registry.py`: `register_all`(all-or-nothing).
  `formatters/analysis_reference.py`: `format_run_answer`. `scripts/run_agent.py`: `--workers 1`, `WEB_CONCURRENCY`≠1 거부(exit 2), 자식에만 단일 worker 표시.
- 최초 AC: 실제 계산 후 `failed`/`MODEL_REVISION_CHANGED`와 새 요청 안내. warm-up·숨은 재실행 없음. 명시 감시 Sensitivity는 상태를 쓰지 않아 승인될 수 있다.
- 해시 체인: `kems-console-ph402b32-code-hashes.json`(B3-1 기록 `104ecf38…`을 잇고 `web_app.py` `13c0e2cb…`→`f7bcaa61…`, edits 9개)과
  `tests/test_mn_pipeline.py` 확장(B3-1 테스트는 HEAD 대신 복원한 B3-1 바이트 사용, 단언 유지). 상세: `docs/specs/PH4-02B3-2_AGENT_RUN_API.md`.

## 보존된 검증: PH4-02B3-2 (이번 실행 결과 아님)

- 신규 run API: 최초 39개 통과 → 보강 후 **46 passed (12.00s)**. 신규 + 기존 관련 20개 파일: **443 passed (40.87s)**. 해시 체인 관련: **101 passed** (기존 75 + 신규 26).
- 전체 비브라우저: 변경 전 `00f6edc` **893 passed, 103 skipped**, 최종 **965 passed, 103 skipped, 1 warning (80.23s)**, 실패 0.
- 통합 기준 격리 사본 대비 기존 REST/Tool/legacy 채팅 33개 JSON 동일(`7e10e21c…`), 응답 원문 21개 동일, HTTP 순차 legacy 채팅 원문·이력 10개 동일(최종 바이트로 재확인).
- Chrome(고정 그래프, 실제 채팅 경로): PH4-01 콘솔 채팅 **6 passed (14.04s)**, 기존 채팅 관련 `test_browser_stale_responses.py -k chat` **4 passed (15.02s)**(고정 증거 경로 보존 위해 격리 사본).
- 격리 사본 변이: 구현 안전장치 24/24 검출, 체인 검사 약화 9개 중 8개 검출(HEAD 핀 단언 1개는 전체 체인 검사와 중복). 확장 전 체인 테스트는 새 `web_app.py`에서 45개 실패.
- 실제 LLM smoke: **미실행**(`/api/tags` HTTP 502, 18:50·21:15 KST). 모델만 scripted이고 Tool·계산은 실제 full KPG·MN. 제한: 테스트별 45/40초(전체 120초), 명령 300~1500초(+kill), Chrome 케이스 210초.
- 증거: `.local/ph4-02b32/20261104-182655/`. `git diff --check`와 범위 외 기존 파일 SHA256 보존을 확인했다.

## 보존된 검증: PH4-02B3-1 (이번 실행 결과 아님)

- 신규 76 passed, 신규+관련 397 passed, 해시 체인 75 passed, 전체 비브라우저 893 passed/103 skipped, legacy 33개 동일(`7e10e21c…`)·원문 21개 동일,
  변이 16/16·체인 약화 5/6(중복 1). 실제 LLM·Chrome 미사용. 증거 `.local/ph4-02b31/20261104-174059/`.

## 보존된 검증: PH4-02-MAINT-02·PH4-02B2 (이번 실행 결과 아님)

- MAINT-02: 해시 체인 관련 수정 전 2 failed/26 passed → 53 passed, 전체 비브라우저 795 passed/103 skipped. 증거 `.local/ph4-02-maint-02/20261104-165048/`.
- B2: 신규 61 passed, 신규+관련 298 passed, 전체 비브라우저 최종 2 failed(당시 기존 해시 문제)/768 passed/103 skipped,
  legacy 33개 동일(`7e10e21c…`), 변이 14/14. 증거 `.local/ph4-02b2/20261104-153953/`. 실제 LLM·Chrome 미사용.

## 보존된 검증: PH4-02B1 (이번 실행 결과 아님)

- 신규 22개 + 기존 관련 95개: **117 passed, 1 warning (7.03s)**. 통합 기준 사본과 기존 REST/Tool 15개 출력·schema 동일.
- 최초 3 failed/14 passed와 export 옵션 보완 중 12 failed/105 passed는 원인 수정 후 로그를 보존했다. 실제 LLM·Chrome 미검증.
- 증거: `.local/ph4-02b1/20261104-143915/`. 당시 회귀 범위에 `tests/test_mn_pipeline.py`는 없었다.

## 보존된 검증: PH4-02B0 (이번 실행 결과 아님)

- 격리 feasibility **8 passed (1.55s)**, 기존 관련 계약 **20 passed, 1 warning (1.72s)**. 초기 4 failed/4 passed, 중간 3 failed/5 passed 보존.
- 기존 테스트 sandbox 실행은 180초 제한(exit 124)으로 종료했고 제한 밖에서 통과했다. 원인을 특정 syscall로 확정하지 않는다.
- 증거: `.local/ph4-02b0/20261104-140557/`. 각 테스트 40초, 전체 180초+kill 10초 제한. 실제 LLM·Chrome 미검증.

### PH4-01 기능 요약

- 목적: `/console/` 의사결정지원 패널에서 기존 FastAPI / LangGraph 채팅 API 사용.
- `ui/console/api-chat.js`를 분리하고 bridge·CSS·HTML에 연결했다.
  `web_app.py`는 `api-chat.js` 정적 파일 허용 목록만 변경했다. 기존 Agent/API 구현은 그대로다.
- KPG·몽골은 각각 `/api/grids/{grid_id}/chat`을 사용하며 한국 OSM은 채팅을 차단한다.
  카탈로그의 `chat_available`도 확인한다. Artifact 데모 채팅 원형은 유지한다.
- 사용자·AI 메시지를 텍스트로 표시하고 계통별 대화·초안·세션을 메모리에 분리했다.
- 대화 초기화, 중복 전송 방지, 응답의 계통·세션 검증, 60초 제한, 서버·연결 오류 표시를 구현했다.
  진행 중 전환·초기화·실패는 세션 ID도 교체해 취소 후 서버 작업과 새 대화의 경합을 방지한다.
- 분석 영역과 채팅을 별도 DOM에 유지해 N-1·Sensitivity 갱신 시 입력 초안이 사라지지 않는다.
- 마지막 완료 작업: PH4-01 구현, 신규 채팅 Chrome 6개 및 기존 Chrome 20개 회귀 통과.

## 보존된 검증: PH4-02A (이번 실행 결과 아님)

- 최신 통합 브랜치와 깨끗한 작업 트리를 확인하고 허용된 `feature/ph4-02-console-analysis-context`에서 설계했다.
- 현재 legacy 응답 shape 및 MN의 잘못된 artifact 거부 기준선: **2 passed, 1 warning (1.99s)**.
  로그: `.local/ph4-02a/20261104-scwvjjy0/legacy-contract-baseline.log`. 실제 LLM 호출 없음, 각 테스트 90초 제한.
- JSON 구문·예시·UUID·인수 조건 ID·요구사항 1~8 대응 확인. 미래 v1 동작/Chrome 검증을 통과했다고 기록하지 않는다.
- 설계 문서·최소 테스트 계약·STATE만 변경한다. 기존 코드·테스트·데이터·검증 기록과 로컬 Artifact는 보존한다.
- `git diff --check` 및 범위/해시 보존 점검을 수행한다. 아래 Phase 3·PH4-01 수치는 과거 실행 결과다.

## DOC-01 보존 검증 (PH4-02A에서 재실행한 결과 아님)

- 로컬 최신/회귀 Artifact는 700,339바이트·SHA256 `47b7eed9878b75f5583c4ebd5e7fd8db0ba6a266832d751fb513f13d93838b49`로 동일하다. 게시 사본만 빈 줄 공백 2바이트를 제거했다.
- Artifact CSS·모델·스크립트·마크업/원본 슬라이스: **5 passed, 1 warning**.
- Artifact 원본/콘솔 Chrome parity: **1 passed**. 실제 LLM 호출 없음.
- 로그: `.local/doc-01/20261104-5cxny96h/artifact-static.log`, `artifact-parity.log`.
- 키트 원문 4개는 출처 Git blob과 바이트·SHA256이 일치한다. 원래 로컬 HTML 두 파일은 수정하지 않았다.
- `.gitignore`, 기능 코드·테스트·데이터·검증 해시는 보존했으며 참고 사본 해시는 각 README에 기록했다.
- 병합으로 인해 이번 검증이 재실행됐다고 주장하지 않는다.

## 과거 검증: Phase 3 (이번 실행 결과 아님)

| 검증 범위 | 과거 결과 |
|---|---|
| 비브라우저 | 632 passed, 47 skipped |
| Chrome | 21 passed |

기준 기능 커밋은 `266475f`다. 수치는 사용자 제공 과거 수행 결과이며 다른 실행 결과와 합산하지 않는다.

## 보존된 검증: PH4-01

| 검증 범위 | 당시 결과 | 로그 |
|---|---|---|
| 콘솔 정적·라우트 + 몽골 파이프라인/해시 계약 | 92 passed, 1 warning | `.local/ph401-validation/e97b26a54e71/nonbrowser.log` |
| 신규 Chrome 채팅 6개 시나리오, 51개 브라우저 검사 | 6 passed | `.local/ph401-validation/e97b26a54e71/chat-browser-final.log` |
| Artifact 원본 비교 시계 수정 후 단독 확인 | 1 passed | `.local/ph401-validation/865ef9323a30/parity-first.log` |
| 기존 Phase 3 Chrome (별도 스크린샷 일괄 생성 제외) | 20 passed, 1 deselected | `.local/ph401-validation/865ef9323a30/phase3-browser-final.log` |

- 초기 기존 Chrome 실행의 1 failed / 19 passed는 숨기지 않고 `e97b26a54e71/phase3-browser.log`에 보존했다.
  원인은 테스트의 실제 경과 시간에 따른 스냅샷 ID·age 배지 차이였다. parity에만 수동 시계를 적용하고
  기존 배지 렌더러를 호출했다. 원본 코드·정규화·전체 fingerprint 동등성 검사는 완화하지 않았다.
  수정 후 독립된 두 실행의 Artifact fingerprint도 동일했다. 미해결 테스트 실패는 없다.
- 신규 테스트는 실제 FastAPI 채팅 경로와 서버 세션을 사용하되 두 Agent 그래프만 고정 응답으로 대체한다.
  오류·지연·취소 무시·XSS 문자열·초기화·IME·한국 차단·초안 보존·실제 해석과 공존을 확인했다.
- 각 테스트 제한: 신규 Chrome 210초, 기존 Chrome 240초, 비브라우저 90초.
  Chrome 프로세스·서버 기동·HTTP에도 별도 제한을 두고 외부 호스트는 차단했다.
- 신규 실행: `AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_console_chat_browser.py`
- 스크린샷: `.local/browser-tests/screenshots/console_chat_ph401/ecb42971d969/`의 6개 PNG.
  JSON 증거는 같은 run ID의 `logs/` 경로다. 응답은 테스트용 고정 응답이며 실제 LLM 결과가 아니다.
- PH4-01 당시 `git diff --check` 통과. 인수인계 생성 및 Git 제외 확인 완료.

## 주요 주의 사항과 보존 기록

- PH4-01 당시 원본 UI·모델/데이터·Agent와 Stage 1·3C·Phase 2·3 기록을 보존했다. B1은 서버/Tool 경계만,
  B2는 v1 Tool wrapper·검증 모듈·`web_app.py` 순수 mapper 분리와 canonical 변환만, B3-1은 v1 요청·receipt·reference 서버 경로만,
  B3-2는 v1 run·legacy gate 공유·예산/launcher 경계만 변경한다(콘솔·모델·데이터·물리 알고리즘 불변).
- PH4-01의 `docs/validation/kems-console-phase4-code-hashes.json`은 당시 allowlist 변경을 고정한다.
  원래 Stage 1 계약의 실패를 성공으로 바꾸지 않으며 변조 검사는 임시 복사본에서만 수행했다.
  PH4-01(`6628d110…`)·PH4-02(`5a7abf5c…`)·B3-1(`13c0e2cb…`) 고정값은 과거 기록으로 그대로 두고, 최신 기록
  `kems-console-ph402b32-code-hashes.json`이 현재 바이트(`f7bcaa61…`)를 고정한다. 이후 `web_app.py`를 바꾸면 기존 기록을 수정하지 말고
  최신 기록의 SHA256과 edits를 잇는 새 기록을 추가하고, `ph402b31_after_bytes` 같은 복원 경로를 한 단계 연장한다.
- run 운영 전제: 지원 launcher만 `AI_EMS_SINGLE_WORKER=1`을 자식 프로세스에 넣는다. 셸·`.env`에 직접 넣지 않는다(worker 자동 감지 아님).
  HTTP 취소·콘솔 60초 timeout은 서버 run·JVM 계산을 멈추지 않으며 gate·ledger·network guard는 실행이 끝날 때 해제된다.
- PH4-01 실제 LLM 연결·응답 품질은 미검증이다. Stage 3C 외부 LLM 502/503 실패 기록도 유지한다.
  Issue #11의 2026-10-22 사용자 Ollama 성공 기록과는 검증 환경·시점·범위를 구분한다.
- 대화는 페이지 메모리에만 보존된다. 초기화는 새 세션을 시작하며 기존 서버 세션 정리 정책은 바꾸지 않았다.
  HTTP 취소가 서버 Agent 실행 중단을 보장하지 않으므로 중단된 세션을 재사용하지 않는다.

## 다음 작업과 다음 담당자에게

- PR #23은 MAINT-03을 포함한 C-1 통합 검토 대상이다. Draft를 유지하고 Issue #14를 닫지 않는다. 최종 실행은 모두 통과했지만 과거 IEEE39 불안정성을 별도 항목으로 수용할지 판단한 뒤 병합한다.
- 이번 IEEE39 비교는 일치했다. 과거 309/310 변동의 원인 확인·결정적 렌더링 검증은 별도 범위로 남는다. 기존 테스트를 skip/xfail하거나 단언을 완화하지 않는다.
- F2 REST 오류 문구 범위 한정은 선택 사항이다. F4 브라우저 시계 의존과 F5 미확정 응답의 안내 없는 폐기 가능성도 후속 검토한다(이번 범위에서 새 결함으로 확정하지 않음).
- C-2a(PR #25)는 통합됐다. C-2b(run 응답·agent receipt·`ui_updates` 순수 검증)는 Draft PR #26으로 검토한다. 자동 병합하지 않는다.
- C-2c(run 반영·잠금·상태별 UI)는 별도 요청 후 진행한다. `runResponse` → `prepareAnalysisResults(view, run.items)` →
  `commitAnalysisResults(..., {epoch: bumpRevision, storeReceipts})` 순서를 쓴다(C-2b spec의 인터페이스 절).
  확정 오류 시 세션 유지 여부와 run capability 분리는 PM 결정 사항이다.
- C-2는 별도 요청 후 검증된 run receipt/update의 prepare→atomic commit, 실패/미실행/미평가 표시, 수동 분석 중재를 구현한다.
  `completed/not_evaluated/not_run/failed`와 사유 코드, 취소/timeout 결과 미확정, 같은 요청 자동 재시도 금지 조건을 보존한다. C-1 reference는 새 계산·지도 적용 없이 유지한다.
- 실제 LLM smoke(고정 Tool/인자 준수·지연), provider 예산, 지원 launcher·단일 worker와 Chrome 검증 후에만 capability 공개를 별도로 결정한다. MN v1에는 별도 model identity가 필요하다.
- 서버 변경이 없다면 `web_app.py` 해시 체인을 늘리지 않는다. 이후 서버 변경 시 기존 기록을 잇는 새 기록이 필요하다.
- 과거 통합 작업 브랜치의 삭제 후보 검토는 이력 보존 조건과 별도 승인에 따른다. 이번에 기존 worktree·브랜치·임시 증거를 삭제하지 않는다.
- 시작: `python3 scripts/dev_handoff.py`. 종료: STATE 갱신 후 `python3 scripts/dev_handoff.py --write`. 실제 테스트 로그가 있을 때만 `--test-log PATH`를 사용한다.
- 로그 요약 하나로 전체 성공을 판단하지 않는다. FINAL의 두 A 실행·C-1·전체 Chrome·비브라우저 최종 및 초기 경로 오류·IEEE39 관찰 로그를 함께 전달한다.
