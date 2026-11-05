# PH4-02C-2c — AI Run 콘솔 연결과 원자적 화면 반영

기준: `feature/kems-multi-grid` `aebf3d6`(C-1 #23, C-2a #25, C-2b #26 병합). 작업 브랜치: `feature/ph4-02c2c-console-run`.
상위 [Issue #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14). 보고일: 2026-11-05 (Asia/Seoul).

## 범위

- 콘솔에서 KPG AI 분석 실행(`mode=run`)을 보낸다.
- 서버가 계산·검증한 응답 **전체**를 C-2b `runResponse`로 검증한 뒤, C-2a 공통 commit으로 지도·패널·receipt에 한 번에 반영한다.
- LLM 문장은 해석하지 않으며, 반영은 검증된 `ui_updates`로만 한다.

변경하지 않은 것:
- 서버·`web_app.py`·물리해석·데이터·검증 해시·Artifact 원본
- 운영 capability. 서버는 run 광고 필드를 보내지 않으므로 운영 콘솔에는 run이 보이지 않는다.
- 실제 LLM 호출. 모든 시험은 LLM만 scripted다.

변경 파일:
- `ui/console/api-bridge.js`, `ui/console/api-chat.js`, `ui/console/api-analysis.js` (`api-bridge.css`는 변경 없음)
- 신규 Chrome fixture·probe·시험, 정적 계약 시험
- C-2a/C-2b 범위 가드 시험 2개를 C-2c 계약으로 교체(아래 시험 절)
- 이 문서와 STATE

## 노출 조건 (capability)

- `runSupported(view)` (api-bridge.js 771행) = `referenceSupported(view)` **그리고** 카탈로그 `console_analysis_run_available === true`.
  - reference 지원만으로는 run을 열지 않는다.
  - 서버는 아직 이 필드를 보내지 않으므로 운영 콘솔에는 run 모드가 나타나지 않는다.
  - fixture만 이 필드를 광고한다.
  - MN·한국 OSM은 `referenceSupported`에서 이미 막힌다(광고를 받아도 무시).
- **제안(C-3, 서버 변경 필요):** 서버는 다음 조건이 모두 갖춰졌을 때만 KPG에 `console_analysis_run_available: true`를 넣는다.
  - `single_worker_confirmed()`, 실행 예산 유효
  - 실제 LLM smoke 통과 후 운영자가 명시적으로 켬
  
  기존 `console_analysis_context_versions`(reference)와 분리한다.

## Run 요청 흐름

1. 채팅 모드 `AI 분석 실행`(광고될 때만 표시)과 분석 종류를 고른다.
2. **대상**(`runOption` 780행)
   - Security·연쇄: 지도에서 연 **선로 카드**의 선로.
   - 연쇄의 감시 선로: 그 선로가 화면 N-1 결과의 사고 선로와 같고 감시 선택이 있으면 그 값, 아니면 자동(null).
   - Sensitivity 단독: **화면의 N-1 결과** 사고 선로와 현재 감시 선택. N-1 결과가 없으면 실행할 수 없다.
   - 수동 분석이 진행 중이면 실행할 수 없다.
3. `captureRun` (799행): 다음을 frozen snapshot으로 고정한다.
   - `{version:1, view_id, revision, mode:'run', operation, outage_line_id, monitored_line_id, references:{null,null}}`
   - 세션, 감시 값, `seq`/`sensSeq`
   - chat이 새 `request_id`(UUID v4)와 함께 B3-2 계약 그대로 보낸다.
4. 응답 수신 후 chat의 `current()`로 확인한다: 계통·활성·대기 객체·세션 + `runCurrent`(810행: view 객체·UUID·revision·세션·모드·operation·seq·감시, Sensitivity 단독이면 화면 N-1 사고 일치).
5. `applyRun` (831행)이 끝까지 처리한다.

## 원자적 반영과 세션 보존 (`applyRun`)

```
runResponse(payload, snapshot, request_id, session_id)   // C-2b: 응답 전체, 하나라도 틀리면 Invalid
  ├ failed / not_run → 아무것도 바꾸지 않고 라벨만 반환
runCurrent(snapshot)                                       // 다르면 늦은 응답으로 버림
prepareAnalysisResults(view, run.items)                    // C-2a: 지도 대응까지 포함한 VM, 실패 시 Invalid
── 여기까지 상태 변경 없음 ──
supersedeAnalysisRequests(a)                               // 진행 중 수동 요청 무효화
(security 포함) a.line/monitored/sensitivity=idle, selectedOutageLineId, receipts·notices 초기화
commitAnalysisResults(view, prepared, {epoch: bumpRevision, origin: 'run', storeReceipts})
analysisChanged()                                          // 한 번
```

- **자기 취소 방지:** run 결과는 아직 대기 중인 그 요청이 반영하므로 `bumpRevision`만 쓴다.
  - `advanceAnalysis`를 쓰면 자기 요청을 retire해 세션과 receipt를 잃는다.
  - 정적 시험과 변이 C1이 이를 확인한다.
- **부분 반영 없음:** 검증·현재성·VM 준비를 모두 끝낸 뒤 동기 대입만 한다. 첫 상태 변경 전 구간에 쓰기가 없다는 것을 정적 시험으로 고정했다.
- **receipt:** 각 결과를 `storeReceipt(view, kind, {receipt, sessionId: 요청 세션})`으로 기록한다. commit 뒤에도 세션이 그대로라 바로 reference를 쓸 수 있다.
- **Security를 포함한 run(새 N-1):** 이전 Sensitivity 결과·감시 선택·receipt를 모두 지운다. 따라서 과거 Sensitivity가 새 Security에 연결되지 않는다.
- **Sensitivity 단독 run:** 화면 N-1 결과(사고 선로 일치)를 확인한다. 그 결과와 receipt는 보존하고 Sensitivity만 바꾼다.

## 상태별 화면 정책

| 결과 | 지도·패널 | receipt·revision | 세션 | 채팅 표시 |
|---|---|---|---|---|
| completed | 일괄 반영, 출처 `AI 분석 실행 · 서버 검증` | 새 receipt, revision +1 | 유지 | `AI 분석 실행 · 서버 검증 · 분석 결과 반영 완료` + 서버 답변 |
| not_evaluated | 검증된 결과만 반영(평가 불가 표시, 연쇄 조기 종료는 N-1만) | 새 receipt, +1 | 유지 | `… 평가 불가 항목 있음 (사유) · 검증된 결과만 반영` |
| failed | 그대로 | 그대로 | 유지 | `분석 실행 실패 (사유) · 기존 분석 결과 유지` + 서버 답변 |
| not_run | 그대로 | 그대로 | 유지 | `AI 분석 실행 · Tool 미실행 · 기존 분석 결과 유지` |
| 응답·receipt 계약 위반 | 그대로(전체 거부) | 그대로 | **유지** | `…전체를 거부했습니다. 기존 분석 결과를 유지했습니다.` |
| receipt 만료 | 그대로 | 그대로 | **유지** | `분석 결과가 만료되었습니다. 기존 분석 결과를 유지했습니다.` |
| 60초 timeout | 그대로 | receipt 폐기(기존 정책) | 폐기(기존 정책) | `…서버 계산은 계속될 수 있으며 결과는 반영하지 않았습니다.` |
| HTTP 오류(`AGENT_*`, 409 등) | 그대로 | receipt 폐기(기존 정책) | 폐기(기존 정책) | 코드별 고정 문구(추가: `AGENT_*`, `ANALYSIS_RUN_NOT_READY`, `ANALYSIS_CONTEXT_INVALID`) |
| 늦은 응답(계통 전환·초기화·분석 초기화·재로드 뒤) | 그대로 | 그대로 | — | 버림 |

- **계약 위반·만료에서 세션을 유지하는 이유:** 응답이 도착했다는 것은 서버가 그 요청을 끝냈다는 뜻이고, 콘솔은 아무것도 반영하지 않았다. 그래서 PM 요구(지도·패널·receipt·revision 불변)대로 세션도 유지한다.
- HTTP 오류·timeout·전송 실패의 세션 폐기는 C-1 정책 그대로다. 아래에 별도 제안을 둔다.

## 수동 분석과의 동시성

- run 대기 중 잠그는 항목(`analysisInfo.locked`, 패널 상단 안내)
  - 수동 N-1 버튼(선로 카드)
  - Sensitivity 실행 버튼
  - 감시 선로 select와 대안 감시 선로 버튼
  - 채팅 모드·분석 종류 select
- 비활성화와 별개로 `runSecurity`·`runSensitivity`·`setMonitored`·`referenceChoice`는 run 대기 중 아무것도 하지 않는다. 프로그램으로 강제해도 문맥이 바뀌지 않는다.
- 잠금은 run의 시작·종료(성공·실패·timeout·취소)를 chat이 bridge에 알려(`runPendingChanged`) 다시 그릴 때 풀린다.
- 계통 전환·분석 초기화·대화 초기화는 C-1 원칙대로 대기 중 run을 취소한다(세션 폐기). 늦은 응답은 버린다.
  - HTTP 취소가 서버 계산을 멈추지 않으므로, 안내 문구로 "서버 계산은 계속될 수 있음"을 알린다.
- 같이 고친 C-1 잠재 문제
  - 이전에는 지도가 준비돼 reference 지원이 생겨도 다음 분석 상태 변화 전까지 채팅 모드 선택이 나타나지 않을 수 있었다.
  - 이제 bridge 문맥에 `v1` 지원 상태를 넣어 바뀔 때 다시 그리고, 선로 카드를 열고 닫을 때도 다시 그린다(run 대상 안내 갱신).

## 시험

### 신규 Chrome `tests/test_console_run_browser.py` (opt-in)

- fixture `tests/browser/console_run_app.py`: 실제 FastAPI·registry·PyPowSyBl을 쓰고 LLM만 B3-2 `ScriptedModel`이다.
  - 페이지마다 새 KPG를 적재한다(첫 AC 정책 그대로).
  - 사례별로 광고를 run / reference만 / 없음 중 하나로 정한다.
  - 응답 변조·receipt 시각 변경·지연 전달은 시험 제어로만 쓴다.

| 사례 | 확인 |
|---|---|
| `run_security` | 요청 문맥, 실제 N-1 실행, 출처 표시, receipt(요청·세션 일치), revision +1, run receipt로 reference 성공, **같은 사고 수동 결과와 패널·overlay·결과 데이터 동일** |
| `run_sensitivity` | 화면 N-1 대상, N-1 결과와 receipt 보존, 명시 감시, N-1 없으면 실행 불가 |
| `run_chain` | 두 결과 일괄 반영, 같은 세션·revision, 연쇄 reference, 같은 사고의 명시 감시 연쇄, Security run이 이전 Sensitivity·receipt를 지움 |
| `run_states` | N-1 미평가, 연쇄+NO_OVERLOAD, 연쇄 조기 종료, failed·not_run 화면/receipt/revision/세션 불변, 이후 같은 세션으로 run |
| `run_first_ac` | 새 모델의 첫 AC → failed(MODEL_REVISION_CHANGED), 화면 불변, 숨은 재실행 없음, 새 요청은 성공 |
| `run_reject` | 두 번째 `ui_update` 결과·종류·`result_id`, receipt producer·`tool_call_id`·revision 변조, receipt 만료 → 전체 거부, 화면·receipt·revision·세션 불변. 이후 같은 세션으로 성공 |
| `run_late` | 계통 왕복·대화 초기화·분석 초기화·재로드 × 늦은 성공/실패 → 버림 |
| `run_locks` | 대기 중 수동 N-1·Sensitivity·감시·대안·모드·분석 종류 잠금, 강제 조작 무효, 종료(성공/실패) 후 해제 |
| `run_timeout` | 60초(시험에서는 단축) → 화면 유지, "서버 계산은 계속될 수" 안내, 잠금 해제, 서버는 계산 완료 |
| `run_regression` | 기본 채팅 legacy 본문, 수동 receipt·reference, 몽골 run/reference 미노출·legacy 채팅, 한국 차단 |
| `gating_reference` | reference만 광고: run 숨김, 강제 선택 거부, run 문맥 전송 0 |
| `gating_none` | 광고 없음(현재 운영): v1 컨트롤 없음, legacy 요청만 |

### 정적 시험

- 신규 `tests/test_console_run_contract.py` (7개)
  - 첫 상태 변경 전 쓰기가 없음
  - failed/not_run은 반영하지 않음
  - 새 사고는 이전 Sensitivity·receipt를 지움
  - Sensitivity 단독 조건
  - 수동 동작 guard와 잠금
  - 문맥 고정
  - 거부·만료 시 retire 없음
- **교체한 기존 시험 2개.** C-2a/C-2b가 "아직 run을 연결하지 않음"을 고정하려고 둔 범위 가드다. C-2c 계약을 같은 위치에서 더 엄격하게 검사하도록 바꿨다.
  - `test_console_commit_contract.py::test_chat_tracks_one_pending_v1_snapshot_and_never_offers_run`
    - → `…_offers_run_only_when_advertised`
    - 이전 단언 "chat·bridge에 `'run'` 문자열 없음"을 다음으로 대체했다: run 문맥은 `captureRun`에서만 생성, chat은 문맥을 만들지 않음, 노출은 `runSupported`(별도 광고)뿐.
  - `test_console_run_validation_contract.py::test_validators_are_exported_but_not_wired_into_the_console`
    - → `…_wired_only_through_apply_run`
    - chat은 검증 함수를 쓰지 않는다. bridge는 `applyRun` 한 곳에서만 쓰고, 순서는 runResponse → runCurrent → prepare → supersede → `bumpRevision` commit이며 `advanceAnalysis`가 없다.

### 결과 (증거 worktree `.local/ph4-02c2c/20261105-164754/`)

| 범위 | 결과 |
|---|---|
| 신규 C-2c Chrome | 첫 실행 **6 failed / 6 passed** → 최종 **12 passed (35.03s), 검사 61개** (`run-first.log`, `run-second.log`) |
| 정적(콘솔 19 + C-2a 9 + C-2b 4 + C-2c 7) | **39 passed** |
| C-1 Chrome + C-2a `commit_contract` | **11 passed** |
| C-2b 검증 Chrome | **1 passed** |
| MAINT-03 loading Chrome | **4 passed** |
| PH4-01 + Phase 3 Chrome | **26 passed, 1 deselected** (IEEE39 비교 포함 통과, 309/310 변동 미재현) |
| 전체 비브라우저 | **985 passed, 131 skipped** (C-2b 978/119 + 정적 7 + C-2c Chrome 12 skip) |
| 구현 변이(격리 사본) | **Chrome 10/10 검출**, 정적 8/10 (`implementation-mutations.txt`) |

첫 실행 실패 6건의 원인과 조치:
1. probe가 `startsWith`로 답변을 검사했는데, 메시지 요소의 textContent 앞에는 역할 라벨 "AI"가 붙는다. C-1처럼 `includes`로 바꿨다(probe만 수정).
2. 실제 UI 결함: 지도 준비와 카드 선택이 chat을 다시 그리지 않아 run 모드가 나타나지 않거나 대상 안내가 갱신되지 않았다. 앞 절의 `v1` 문맥과 카드 갱신으로 고쳤다.

worktree에는 Git이 무시하는 실행 자료(KPG `.mat`, `bus_location.csv`, `data/grids`, `local-reference`, `.cache`, `.venv`, 몽골 bundle, `outputs/kr_topology`)를 원본 저장소로 읽기 전용 연결했다. 이 링크는 커밋하지 않았다.

## 통합 후속 (PR #27, 2026-11-05)

### 통합 브랜치 병합

통합 브랜치 `226fd50`(PH4-02-INFRA-02 smoke 도구 `d3b32d5`·`226fd50`)을 일반 merge했다(`b4ee746`). force push·reset·rebase는 하지 않았다.

- 충돌은 `docs/dev/STATE.md` 하나였다. INFRA-02의 현재 단계 항목과 "최근 작업" 절을 **문구 그대로** 보존하고 C-2c 기록과 함께 두었다.
- `scripts/smoke_test_llm.py`·`tests/test_smoke_test_llm.py`는 통합 브랜치와 바이트 단위로 같다(변경 없음).

### run 대기 중 다른 선로 카드 선택

1. **가능한가:** 그렇다. run 대기 중 잠그는 것은 수동 분석·감시 선로·채팅 선택이고, 지도 클릭·검색과 카드는 잠그지 않는다.
2. **응답 시 처리:** `runCurrent`는 선택을 보지 않는다. 결과는 보낼 때 고정한 사고 선로(snapshot)로 반영되고, 새로 연 카드는 그대로 남는다.
   - 카드는 `표시 중인 결과: <사고> 사고`로 결과의 사고 선로를 알려 준다.
   - Sensitivity 단독은 카드가 아니라 화면 N-1 결과의 사고 선로를 대상으로 한다.
3. **혼동:** 실제 문제가 하나 있었다.
   - C-2c에서 카드 선택 때 chat을 다시 그리도록 했는데, run 대기 중에도 상태 줄이 **새 카드**를 `AI 분석 실행 가능 · 사고 <새 카드>`로 보여 줬다.
   - 패널 잠금 안내에는 계산 중인 사고 선로가 없었다.
   - 고치기 전 `b4ee746` 사본에서 신규 사례가 이 두 검사만 실패했다(`pre-fix-b4ee746/`). 결과 반영 자체는 고치기 전에도 정확했다.
4. **판단:** 선택 잠금도, 선택 변경 시 run 무효화도 아니고 **대상 고정을 유지하고 표시를 고친다.**
   - C-1 계약은 "지도·후보 클릭, zoom, 초안은 분석 문맥을 바꾸지 않는다"이다.
   - 잠금은 최대 60초 동안 지도 탐색을 막는다.
   - 무효화는 서버가 계속 계산하는 결과를 버리게 한다.

**최소 수정**
- chat이 대기 중인 v1 snapshot을 읽기 전용으로 알려 준다(`pendingAnalysis`, `isRunPending` 대체).
- bridge는 run이 대기 중이면 상태 줄과 패널 잠금 안내에 **그 run의 고정 대상**을 표시한다: `AI 분석 실행 중 · 사고 X · 감시 … · 지도에서 다른 선로를 골라도 이 요청은 이 사고로 계산·반영합니다`.
- 의미(대상·반영)는 바꾸지 않았다.

**신규 Chrome `run_selection`:** Security와 Sensitivity 단독 각각에 대해 다음을 확인한다.
- 대기 중에 다른 카드를 열 수 있다.
- revision이 그대로이고 run이 계속된다.
- 상태 줄과 잠금 안내가 고정 대상을 표시한다(새 카드 아님).
- 결과는 요청한 사고 선로에만 반영된다(요청 본문, `a.line`, overlay 사고 선로, receipt, 서버 답변).
- 열린 카드는 표시 중인 결과의 사고 선로를 알린다.
- 종료 후 잠금이 풀리고 상태 줄은 다음 대상을 표시한다.

### 통합 후 최종 검증 (증거 worktree `.local/ph4-02c2c/20261105-173955-merge/`)

| 범위 | 결과 |
|---|---|
| C-2c Chrome 13개(기존 12 + `run_selection`) | **13 passed, 검사 69개** (`run-c2c.log`) |
| 첫 실행 | 1 failed. `run_selection` 기대값 오류: Sensitivity 단독의 대상은 앞선 run이 만든 화면 N-1(`LINE-16-28`)인데 probe가 다른 선로를 기대했다. probe만 고쳤고 로그 보존(`run-c2c-first.log`) |
| 고치기 전 `b4ee746` + 신규 사례 | 표시 검사 2개만 실패, 반영 검사 통과 (`pre-fix-b4ee746/`) |
| 정적 계약 | **39 passed** |
| C-1 Chrome + C-2a `commit_contract` | **11 passed** |
| C-2b 검증 Chrome | **1 passed** |
| MAINT-03 Chrome | **4 passed** |
| PH4-01 + Phase 3 Chrome | **26 passed, 1 deselected** (IEEE39 비교 통과, 309/310 변동 미재현) |
| 전체 비브라우저 | **997 passed, 132 skipped** (985/131 + 통합 smoke 시험 12 + `run_selection` skip 1) |
| INFRA-02 smoke 시험 단독 | **12 passed** (실제 LLM 호출 없음) |

## 별도 제안 (이번에 구현하지 않음)

1. **run capability (C-3, 서버):** 위 노출 조건 절. 운영 공개 전 필수.
2. **확정 v1 오류의 세션 유지:** `AGENT_*`(500)·`ANALYSIS_RUN_NOT_READY`·`SESSION_BUSY`(409)처럼 서버가 응답을 끝낸 v1 오류는 서버 쪽 작업이 남아 있지 않다. 그런데 지금은 기존 정책대로 세션과 receipt를 폐기한다. LLM 장애 때마다 수동·run receipt를 잃으므로 유지 정책을 검토할 만하다. timeout·전송 실패·Abort는 계속 폐기한다.
3. **timeout 뒤 결과 회수:** 서버는 계속 계산해 이전 세션에 receipt를 등록한다. 같은 `request_id`로 결과를 수동 재확인하는 동작(자동 재시도 아님)은 PM 결정 사항이다.

## 미해결

- 실제 LLM으로 확인하지 않은 것: Tool·인자 준수율, 지연, 60초와 서버 예산(30초 기한) 적합성, `tool_call_id` 형식.
- receipt에 결과 digest가 없어 결과 수치의 진위는 클라이언트가 인증하지 않는다(C-2b 한계 그대로).
- 운영 노출은 C-3에서 서버 광고와 smoke를 마친 뒤 결정한다.
