# PH4-02C-2b — AI Run 응답과 Receipt의 순수 검증 계층

기준: `feature/kems-multi-grid` `950de21`(C-1 PR #23, C-2a PR #25 병합). 작업 브랜치: `feature/ph4-02c2b-run-validation`.
상위 [Issue #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14). 보고일: 2026-11-05 (Asia/Seoul).

## 범위

서버가 반환한 v1 run 응답(HTTP 200)을 프런트엔드에서 검증하는 **순수 함수**만 추가한다.

- 변경: `ui/console/api-analysis.js`, 신규 테스트, 이 문서와 STATE.
- 변경하지 않음: `api-bridge.js`, `api-chat.js`, 서버·`web_app.py`(해시 기록 불필요), 데이터, 물리해석, capability.
- 구현하지 않음: run 모드 UI, `mode=run` 전송, run 결과 반영(C-2c).
- 검증 함수는 bridge·chat 어디에서도 호출하지 않는다(정적 테스트로 고정).

## 함수 계약 (`ui/console/api-analysis.js`, `KemsApiAnalysis`로 공개)

### `runResponse(payload, snapshot, requestId, sessionId)` (437행)

- 입력
  - `payload`: run 응답 본문.
  - `snapshot`: 보낸 요청의 `{gridId, context}`. `context.mode`는 `run`이고 `references`는 둘 다 null이어야 한다.
  - `requestId`, `sessionId`: 보낸 요청의 값.
- 순수성
  - 인자만 읽는다(만료 판정에만 시계를 쓴다).
  - 상태 저장·revision·세션·DOM·네트워크·계산에 손대지 않는다.
  - 입력을 바꾸거나 동결하지 않는다.
- 실패
  - 계약에 하나라도 맞지 않으면 `KemsApiAnalysis.Invalid`를 던진다. receipt나 `ui_updates` 하나만 틀려도 응답 전체를 거부한다.
  - 계약이 모두 맞은 **뒤에만** 만료를 판정한다. 만료면 `code: 'RECEIPT_EXPIRED'`를 붙인 `Invalid`를 던진다. 그래서 만료가 계약 위반을 가리지 않는다.
- 성공하면 frozen 요약을 반환한다.
  - `{status, reasonCode, message, answer, modelRevision, expiresAt, receipts, items}`
  - `items`: `[{kind, payload, line, monitored, receipt}]`. C-2a `prepareAnalysisResults(view, items)`에 그대로 넘길 수 있는 형태다. `payload`는 복사 후 deep-freeze한 값이다.
  - `failed`/`not_run`이면 `receipts`와 `items`가 비어 있고 `modelRevision`과 `expiresAt`은 null이다.

### `agentReceipt(receipt, expected)` (377행)

- `expected = {kind, gridId, line, monitored: 요청한 감시 선로(null = 자동), requestId, payload: 같은 위치의 결과 데이터}`.
- 검증한 receipt의 frozen 복사본을 반환한다. 만료 판정은 호출자 몫이다.

### 공용 검사 (337–352행)

`receiptShape`(키·식별자·revision 형식), `assessmentMatches`(receipt 평가 ↔ 결과 데이터 `analysis_status`), `receiptTimes`(시각 형식·순서).
C-1 `manualReceipt`(354행)와 `agentReceipt`가 함께 쓴다. `manualReceipt`의 수용·거부 판정은 이전과 같다(아래 C-1 회귀와 수동 receipt 검사로 확인).

## 실제 서버 계약과의 대응 (서버 코드는 `15f634c` 이후 변경 없음)

| 검증 | 서버 근거 |
|---|---|
| 응답 키 7개, `analysis` 키 5개, `ui_updates` 항목 키 4개, `analysis_context`는 요청 그대로 | `web_app.py` `_execute_run` 1273–1313행, `AnalysisContextV1.echo` |
| 문맥: `mode=run`, `references`는 둘 다 null | `analysis_context.py` 629행 |
| `failed`/`not_run`: 결과와 `ui_updates`가 비어 있고 `source=none`, 사유 있음. `not_run` 사유는 `TOOL_NOT_EXECUTED`뿐 | `_publish_run` 1316행, `verify_evidence` 442행 |
| `completed`/`not_evaluated`: `source=tool_execution`. completed면 사유 null. not_evaluated면 첫 미평가 결과의 사유 | `verify_evidence` 451행 |
| 결과 종류와 순서는 operation 계획과 같다. chain의 조기 종료는 **미평가 N-1 하나**만 가능. 결과가 둘이면 N-1은 completed | `_condition_problems` 474–507행 |
| receipt: `producer=agent_tool`, `producer_request_id`=요청 ID, `tool_call_id`는 비어 있지 않은 문자열이고 요청 안에서 중복 없음 | `_register_published` 1342행, ledger의 `INVALID_TOOL_CALL_ID` 검사 |
| receipt: 키 15개, UUID, 64자리 hex revision, 결과 간 같은 revision, 시각은 `Z` 형식이며 만료가 생성보다 뒤 | registry `_prepare` 114행, `_rfc3339` 35행, `_condition_problems`의 revision 집합 검사 |
| Security receipt: 감시·선택 방식·top_n이 모두 null | `_receipt_problem` 75행, `_outcome_problem` 155행 |
| Sensitivity receipt: top_n 5. 명시면 `explicit` + 요청 ID. 자동이면 `auto`, 평가된 경우에만 대상(사고 선로와 다른 유효 ID) | `_outcome_problem` 155행, `agent/tools.py` 279–310행 |
| 평가 ↔ 결과 데이터: completed는 `COMPLETED`, 미평가는 `analysis_status`=사유(`SECURITY_NOT_CONVERGED`, `NO_OVERLOAD`) | `agent/tools.py` 266–268·288행, `_kpg_line_analysis_payload` 1011행 |
| 미평가 Sensitivity 결과 데이터에는 감시 선로가 없다(null) | `_sensitivity_not_evaluated_for_ui` 942행 |
| `ui_updates[i]`: `type`=receipt 종류, `result_id`=receipt ID, `grid_id`=요청 계통, `result`의 사고 선로=요청 사고 선로 | `_execute_run` 1304행 |
| 결과 데이터 구조: 수동 결과와 같은 `securityCommon`/`sensitivityCommon` 검사(수렴·판정 가능 여부·건수·후보·감시) | REST와 같은 canonical payload(B3-2 REST 동등성 시험) |

**검증하지 않는 것(한계)**
- 결과 수치의 진위: receipt에 결과 digest가 없어 클라이언트가 값 자체를 인증할 수 없다. 같은 출처 서버 응답이라는 전제다.
- 지도 ID 대응: C-2c의 `prepareAnalysisResults`(KPG adapter)가 맡는다.
- 소유권과 현재 모델: 서버 registry와 reference 재검증이 판정한다.
- `answer`·`message` 문구: 문자열인지만 확인한다.
- TTL 길이: 서버 설정이라 순서만 확인한다.
- `tool_call_id` 형식: 서버와 같이 비어 있지 않은 문자열만 요구한다.

## 시험

### 실행 시험: `tests/test_console_run_validation_browser.py` (Chrome, opt-in)

- fixture `tests/browser/console_run_validation_app.py`가 시작할 때 **실제 web_app 경로**로 응답을 만든다.
  - B3-2 계약 시험의 `RUN_CASES`·`FAILURES`·`ScriptedModel`·`steps_for`·`run_body`를 재사용한다.
  - 실제 full KPG와 실제 Tool 계산을 쓰고, LLM만 scripted다.
- run 응답 19건
  - 정상·미평가 10: B3-2 7종, chain + NO_OVERLOAD, 명시 chain, 비수렴 사고에 명시 감시
  - 실패·미실행 9: guard 5종, Tool 미실행, chain 불완전, 입력 거부, 최초 AC
- 실제 수동 opt-in 응답 5건
- 실제 LLM provider 호출 0, legacy graph 호출 0.
- `probe.js`는 실제 `ui/console/api-analysis.js`를 불러 다음을 확인한다.
  - 수용: 19건 모두 서버 상태·사유·종류와 같고, item·receipt·payload 대응, 반환값 frozen, payload는 복사본.
  - 변조 66종(총 781개 응답): 식별자·문맥·응답 구조·상태 규칙·receipt·`ui_updates` 누락/중복/순서/종류/ID·결과 데이터를 바꾼다. 모두 `RECEIPT_EXPIRED`가 아닌 계약 위반으로 거부해야 한다.
  - 만료: 만료만 있으면 `RECEIPT_EXPIRED`이고, chain에서 하나만 만료돼도 같다. 만료 + 계약 위반(세션·결과·`result_id`·receipt)은 계약 위반으로 판정한다.
  - 수동 receipt: 실제 5건 수용, 변조 7종 거부. agent receipt와 manual receipt는 서로 대체되지 않는다.
  - 순수성: 검증 중 fetch 0회, DOM·저장소 불변, 입력 불변·비동결.

### 정적 시험: `tests/test_console_run_validation_contract.py`

검증 함수가 인자 외 상태를 건드리지 않고 인자에 쓰지 않는지 확인한다. 또 공용 검사 재사용, 만료가 마지막 판정인지, 공개됐지만 bridge·chat에서 쓰지 않는지(C-2c 미착수)를 확인한다.

### 결과 (증거 worktree `.local/ph4-02c2b/20261105-160144/`)

| 범위 | 결과 |
|---|---|
| 신규 Chrome 실행 시험 | **1 passed (5.60s), 검사 108개**(수용 19, 변조 66종/781건, 만료 6, 수동 14, 기타 3) (`validation-second.log`) |
| 첫 실행 | 1 failed. probe가 `window.KemsApiAnalysis`를 참조했으나 최상위 `const`는 window 속성이 아니다. probe만 고쳤고 로그를 보존했다 (`validation-first.log`) |
| 정적: 신규 4 + C-2a 계약 9 + 콘솔 19 | **32 passed** |
| 검증 함수 변이(격리 사본) | **10/10 검출**. tool_call_id 중복, revision 불일치, 만료 순서, `ui_update` 키, `result_id`, 결과 데이터 구조, 상태·사유, 문맥, producer, chain 조기 종료 (`validator-mutations.txt`) |
| C-1 Chrome + C-2a `commit_contract` | **11 passed** (`reference-c1.log`) |
| MAINT-03 loading Chrome | **4 passed** |
| PH4-01 + Phase 3 Chrome | **26 passed, 1 deselected** (`ph401-phase3.log`) |
| 전체 비브라우저 | **978 passed, 119 skipped** (C-2a 974/118 + 신규 정적 4 + Chrome 1 skip) (`nonbrowser.log`) |

worktree에는 Git이 무시하는 실행 자료(KPG `.mat`, `bus_location.csv`, `data/grids`, `local-reference`, `.cache`, `.venv`, 몽골 bundle, `outputs/kr_topology`)를 원본 저장소로 읽기 전용 연결했고, 커밋하지 않았다.

## C-2c에서 호출할 인터페이스

1. 응답을 받으면 chat의 `current()`와 bridge의 run 현재성 검사를 먼저 통과시킨다.
2. `const run = KemsApiAnalysis.runResponse(payload, {gridId, context}, requestId, sessionId)`
   - 실패하면 화면·receipt를 바꾸지 않는다.
   - `RECEIPT_EXPIRED`는 만료로 따로 안내한다.
3. `run.status`가 `failed`/`not_run`이면 사유만 표시하고 아무것도 반영하지 않는다.
4. 그 밖이면 `prepareAnalysisResults(view, run.items)`로 지도 대응까지 포함한 VM을 만든다. 실패하면 전체를 거부한다.
5. 반영은 `commitAnalysisResults(view, prepared, {epoch: bumpRevision, storeReceipts: () => run.items.forEach((item) => storeReceipt(view, item.kind, Object.freeze({receipt: item.receipt, sessionId})))})`로 한다(C-2a 계약).
   - 그 전에 `supersedeAnalysisRequests`로 진행 중인 수동 요청을 무효화한다.
   - security만 실행한 run에서 sensitivity 칸을 어떻게 다룰지는 C-2c 설계에서 정한다.
6. 표시 문장은 `run.answer`(서버의 결정적 문장)를 쓴다.

## 미해결

- 결과 수치를 인증할 수단(서버 결과 digest)이 없다. 필요하면 서버 계약 변경(별도 승인과 해시 기록)이 필요하다.
- 실제 LLM이 만드는 `tool_call_id` 형식은 scripted 시험의 `call-1` 형태와 다를 수 있다. 서버와 같이 비어 있지 않은 문자열만 요구하므로 영향은 없을 것으로 보지만 실제 smoke에서 확인해야 한다.
- C-2c(반영·잠금·상태별 UI), 확정 오류 시 세션 유지 정책, run capability 분리는 별도 요청과 PM 결정이 필요하다.
