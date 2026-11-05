# PH4-02C-1 — 콘솔 수동 분석 Receipt 및 Reference

기준: `feature/kems-multi-grid` `15f634c` (PR #22 병합 확인). 상위 [Issue #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
작업 브랜치: `feature/ph4-02c1-console-reference`. 보고일: 2026-11-05 (Asia/Seoul).

## 범위

KPG 수동 Security/Sensitivity의 실제 결과를 receipt로 등록하고, 서버에 저장된 결과만 설명하는 `mode=reference`를 콘솔에 연결한다.
운영 서버의 v1 capability는 여전히 미광고다. 새 UI와 receipt 헤더는 광고가 있는 테스트 환경에서만 사용한다.
`mode=run`, 새 계산 결과의 지도 반영, MN v1, 한국 OSM AI, 서버·물리 알고리즘 변경은 포함하지 않는다.

## 변경 파일과 책임

| 파일 | 함수·변경 |
|---|---|
| `ui/console/api-bridge.js` | `openView`, `advanceAnalysis`, `referenceSupported/State/Option/Choice`, `captureReference`, `referenceCurrent`, `sessionRetired`: 문맥·receipt의 유일한 소유자 |
| 같은 파일 | `fetchJson`, `runSecurity`, `runSensitivity`, `manualOptIn`, `acceptManualReceipt`, `clearAnalysis`, `setMonitored`: 기존 요청에 opt-in 헤더·provenance 연결 |
| `ui/console/api-chat.js` | `create`, `post`, `submit`, `render`, `retire`, `setContext`: 기본/참조 모드, snapshot 전송, 취소·세션 통지, 텍스트 표시. `uuid`, `errorText` 공통 사용 |
| `ui/console/api-analysis.js` | 기존 `security/sensitivity` VM 검증 유지. `manualReceipt`, `referenceResponse`, `sameJson`: provenance·응답 순수 검증 추가 |
| `ui/console/api-bridge.css` | 기존 composer를 유지하는 작은 모드/operation 선택 영역 |
| `tests/test_console_reference_browser.py` | 신규 Chrome 10개 시나리오(최초 8 + F1/F3 2), 제한 시간·실행별 증거 디렉터리 |
| `tests/browser/console_reference_app.py`, `console_reference_probe.js` | 실제 FastAPI·registry·수동 계산, capability 주입, 의도적인 오류·지연 시험과 호출 수 기록 |
| 이 문서, `docs/dev/STATE.md` | 결정·검증·미해결 사항 인수인계 |

`web_app.py`, 서버 capability/모델 설정, 기존 테스트, 기존 검증 기록은 변경하지 않는다. 서버 코드 해시 체인을 추가할 필요가 없다.
`index.html`, `api-map.js`, `app.js`, `models.js`, `app.css`, 기존 `/` UI와 Artifact 원본도 그대로다.

## 문맥 소유권

- bridge의 view: `viewId`(소문자 UUID v4), `analysisRevision`(안전 정수), `selectedOutageLineId`, `reference.mode/operation/receipts/notices`.
- 감시 선택은 기존 `analysis.monitored`를 유일한 값으로 쓰며 빈 문자열은 전송 시 null이다. 중복 상태를 만들지 않는다.
- `analysis.line`은 표시 중인 수동 결과/요청의 사고다. `selectedOutageLineId`는 기존 N-1 실행 동작으로만 갱신한다.
  지도·후보 클릭, zoom, 입력 초안은 사고 선택을 바꾸지 않는다.
- 기존 VM과 지도 overlay는 기존 경로를 사용한다. receipt는 `{receipt, sessionId}`라는 별도 provenance로 저장한다.
- revision은 수동 요청 시작·성공 commit, 감시 변경, 분석 초기화, 대화 초기화/세션 폐기, 참조 모드/operation 변경에서 증가한다.
  같은 계통을 다시 열어도 view UUID가 바뀐다. 안전 정수 한도에서는 새 UUID와 revision 0으로 새 epoch를 만든다.
- `analysisRevision`은 화면 문맥이며 서버의 `model_revision`과 비교하거나 대체하지 않는다.

## 세션과 receipt 수명

기존 채팅의 grid별 세션을 그대로 쓴다. **수동 계산마다 세션을 교체하지 않는다.** pending reference가 있을 때만 이를 취소·폐기하면서 세션을 교체한다.
정상 Security → Sensitivity는 같은 세션을 유지하므로 두 receipt를 chain reference로 묶을 수 있다.

채팅 초기화·취소·timeout·HTTP/응답 검증 실패는 기존의 보수적인 세션 폐기 정책을 유지한다. bridge에는 세션 교체를 통지해 두 receipt를 무효화한다.
기존 수동 VM/지도는 유지한다. 진행 중 수동 응답은 기존 seq 검사에 맞으면 표시하지만, 요청에 사용한 세션과 현재 세션이 다르면 receipt는 저장하지 않는다.
예를 들어 Security 이후 pending reference를 취소하고 Sensitivity를 시작하면 새 세션의 Sensitivity만 남으므로 chain은 비활성이다.
사용자가 같은 세션에서 두 수동 분석을 다시 수행해야 한다. 과거 receipt를 새 소유자로 재등록하거나 자동 계산하지 않는다.

만료 시각은 UI의 조기 비활성화에만 사용한다. 서버의 monotonic TTL·owner·현재 모델 검증이 최종 판정이다.
브라우저 시계 오차·서버 재시작·registry 용량 제거는 서버의 unavailable/stale 응답으로 처리한다. 페이지 재로드에 따른 영구 복원은 없다.

## 기능 협상·수동 등록

- KPG, `chat_available=true`, 지도 준비, `console_analysis_context_versions` 배열에 정수 1 포함이 기본 조건이다.
  각 operation은 해당 security/sensitivity capability도 요구한다. 미광고 시 controls를 숨기고 기존 본문/헤더를 유지한다.
- MN은 광고를 잘못 받더라도 v1 헤더/문맥을 보내지 않는다. 기존 채팅·수동 해석은 유지한다. 한국 OSM은 기존 채팅·해석 차단을 유지한다.
- 수동 API URL·query는 기존 어댑터를 사용한다. Sensitivity `top_n=5`를 유지한다.
- opt-in이면 `X-EMS-Analysis-Context-Version: 1`, `X-EMS-Session-ID`, 매 요청의 새 `X-EMS-Request-ID`를 모두 전송한다.
- 먼저 기존 canonical VM 검증을 통과해야 한다. receipt는 필수 필드, grid/kind/사고/감시/auto·explicit/top_n,
  원 요청 ID, `producer=manual`, `tool_call_id=null`, model revision 형식, 평가 상태/사유, UTC 시각을 대조한다.
- receipt 자체에는 session 필드가 없다. 요청 closure의 owner와 현재 채팅 세션을 대조해 저장하며, 최종 소유권은 서버 registry가 확인한다.
  클라이언트 검사는 물리계산이나 소유권의 독립 증명이 아니다.
- receipt 누락/오류/불일치에도 canonical 검증에 성공한 수동 결과는 표시한다. 참조는 막고 이유를 안내한다.
  `MODEL_REVISION_CHANGED`는 최초 AC 등을 예로 설명하며 두 번째 실행의 성공을 보장하지 않는다.

## Reference 요청·응답

기본 채팅의 본문은 `{session_id, message, grid_id}` 그대로다. 추가 문맥을 보내거나 기본 응답의 update를 적용하지 않는다.
reference만 `request_id`와 다음 문맥을 추가한다.

```text
version: 1, view_id: 현재 view UUID, revision: 현재 화면 revision
mode: reference
operation: security | sensitivity | security_then_sensitivity
outage_line_id: 선택 사고
monitored_line_id: Security는 null / Sensitivity는 명시 ID 또는 자동 null
references: {security: 해당 receipt ID 또는 null, sensitivity: 해당 receipt ID 또는 null}
```

operation에 필요한 receipt만 지정한다. receipt가 없거나 만료됐거나 grid/session/사고/감시가 다르면 전송하지 않는다.
chain은 동일한 `model_revision`까지 요구한다. chat 문맥에 `top_n`, 계산 수치, 결과 본문, 후보, `verified`를 추가하지 않는다.
입력 snapshot의 문맥·참조 ID·기대 receipt는 복사/freeze하며 전송 본문에는 scalar ID만 사용한다.

응답은 active grid, 세션, pending 객체, request ID, view 객체/UUID, revision, 현재 사고·감시, mode/operation과 일치해야 한다.
문맥 전체 echo, `analysis.status=referenced`, `source=server_reference`, receipt 전체와 순서, 사유 코드, `ui_updates=[]`를 확인한다.
이 계약이 모두 맞은 뒤에만 receipt 만료를 따로 확인한다. 만료된 receipt는 수용하지 않으며 계약 불일치가 아니라 만료로 안내한다(아래 리뷰 후속 F3).
reference receipt의 `producer_request_id`는 현재 설명 요청이 아니라 원래 수동 계산 요청 ID다.
정상 응답은 `기존 결과 설명 · 새 계산 없음`과 서버 문장만 텍스트로 표시한다. 분석 패널·지도·revision을 변경하지 않는다.
이력 복원으로 update를 재생하지 않으며 run을 생성하는 코드 경로도 없다.

## 오류·늦은 응답

v1 detail 객체와 legacy 문자열의 알려진 code를 고정 한국어 안내로 변환한다. provider 원문을 채팅에 노출하지 않는다.
unavailable/stale/replay-stale, request 중복, session/engine busy, 모델 식별 불가, 미지원, 등록 실패를 구분한다.
실패 후 문맥 없는 채팅으로 자동 재전송하지 않으며, 추가 Security/Sensitivity 요청도 하지 않는다.
이 변환은 capability 광고와 무관하게 적용된다. 미광고 운영 경로에서 달라지는 표시 문구는 아래 리뷰 후속 F2에 기록한다.

수동 분석 중 reference 전송은 비활성이다. 수동 분석 시작·commit, 감시 변경, 초기화는 진행 중 reference를 무효화한다.
reference 대기 중에는 모드·분석 종류 선택을 잠그고 성공·실패·timeout·취소 후 다시 연다(리뷰 후속 F1). 대기가 없을 때의 선택 변경은
화면 revision만 올리며 receipt와 세션을 유지한다. 기본 채팅 대기는 선택을 잠그지 않는다.
HTTP Abort와 별도로 `Promise.race`의 60초 deadline과 현재 요청 판정을 유지한다. Abort를 무시하는 늦은 성공·실패도 폐기한다.
정상 reference 완료는 세션을 교체하지 않아 동일한 receipt로 다른 설명을 요청할 수 있다.

## 검증과 증거

증거 루트: `.local/ph4-02c1/20261104-222056/`. 실제 LLM 호출은 없다.

| 검증 | 결과 |
|---|---|
| 전체 비브라우저(B1/B2/B3 포함) | **965 passed, 111 skipped, 1 warning (93.64s)**. 111은 기존 opt-in 등 103 + 신규 Chrome 8 |
| 최종 정적·Artifact·라우트 계약 | **19 passed, 1 warning (0.89s)** |
| 최초 실행 가능한 신규 C-1 Chrome | **8 passed (32.92s)**, 181개 브라우저 검사 |
| 명시 감시·NO_OVERLOAD 보강 후 | **1 failed, 7 passed (75.57s)**. 계통 복귀 시 초기 지도 조회 경합으로 races setup 대기 실패 |
| 초기 읽기 순서 제어 후 최종 신규 Chrome | **8 passed (35.14s)**, 187개 브라우저 검사. `reference-ordered-reads.log` |
| 기존 PH4-01 + Phase 3 Chrome | **5 failed, 21 passed, 1 deselected (81.16s)**. PH4-01 6개 통과. 중복 스크린샷 일괄 생성 1개 제외 |
| 기준 `15f634c` 격리 사본의 실패 5개 재현 | **5 failed, 16 deselected (18.27s)**. 아래와 같은 기존 실패 |

초기 fixture import 오류(8 setup errors)는 수정했고 `reference-first.log`에 보존한다. sandbox 정적 검증은 180초 timeout(exit 124)이었으며
제한 밖의 동일 정적 테스트는 통과했다(`static-final.log`). timeout을 성공/skip으로 바꾸지 않는다. 경고는 설치 Starlette/AnyIO deprecation이다.

신규 Chrome은 실제 FastAPI·registry·수동 KPG/MN 계산을 사용한다. 기본 채팅만 scripted graph이며 reference 전후
graph·도메인·PyPowSyBl 호출 카운터가 같음을 확인한다. 최초 AC, 정상 연속 receipt, 세 operation, 자동/명시 감시,
실제 NO_OVERLOAD, XSS, receipt/응답 조작, 만료/모델 변경, 초기화, 같은 계통 reload, 계통 왕복, Abort 무시, timeout을 검사한다.

위 과거 실행에서는 fixture의 초기 KPG map/PF **읽기 요청만** 순서대로 실제 경로에 전달했다. FINAL 통합에서는 해당 우회를 제거했다. 이는 아래 기준 커밋 경합을 C-1 시험의 준비 단계에서 격리하기 위한 것이다.
응답 수치·잠금·수동 계산·reference 경로는 대체하지 않는다. 실제 서비스의 병렬 읽기 호환성이 통과했다는 뜻이 아니다.
오류/변조/지연 주입은 시험 사례로 명시한다. 운영 capability는 변경하지 않았고, fixture가 MN/Korea에도 잘못 광고했을 때 차단되는지도 확인한다.

신규 케이스는 각각 210초, Chrome 150초, 서버 기동 60초, probe HTTP 5초, fixture 대기 180초, 각 명령 180~1200초+kill 제한이다.
기존 회귀에는 테스트별 240초 plugin을 적용했다. 과거 증거를 덮어쓰지 않도록 기존 Phase 3의 출력 상수만 임시 plugin에서 새 디렉터리로 지정했다.
기존 테스트 파일·단언은 수정하지 않았다. 기준 비교는 `/tmp/ai-ems-c1-baseline-a6mi12x5`의 Git archive와 같은 로컬 계통 데이터로 수행했다.

실행 명령(저장소 루트, 위 증거 폴더의 `deadlines.py`·`browser_paths.py`는 시간 제한/출력 경로만 설정):

```bash
run_dir=.local/ph4-02c1/20261104-222056
timeout --kill-after=15s 1200s env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$run_dir:src:." \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines tests
timeout --kill-after=10s 600s env PYTHONDONTWRITEBYTECODE=1 AI_EMS_BROWSER_TESTS=1 \
  AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_console_reference_browser.py
timeout --kill-after=15s 1200s env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$run_dir:src:." AI_EMS_BROWSER_TESTS=1 \
  AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines -p browser_paths \
  tests/test_console_chat_browser.py tests/test_console_browser.py -k 'not test_console_screenshots'
```

## PR #23 리뷰 후속 보완 (2026-11-05)

독립 리뷰에서 나온 F1·F3을 고치고 F2는 현재 동작으로 기록했다. MAINT-03이 별도 worktree에서 `api-bridge.js`를 수정 중이라 이 파일은 바꾸지 않았다.
서버, `web_app.py`, 기존 테스트의 단언, 계통 데이터, 검증 해시도 그대로다.

| 항목 | 변경 |
|---|---|
| F1 | `api-chat.js` `render()`: reference 대기 중에는 `#kemsChatMode`·`#kemsChatOperation`을 비활성화한다. 대기가 끝나면 다음 render에서 다시 연다(성공·실패·timeout·초기화·계통 전환 모두 해당). 선택 change 처리는 그대로 두었다. 그래서 선택이 프로그램으로 바뀌면 늦은 응답을 폐기하는 기존 `races` 검사가 방어 경로로 계속 유효하다 |
| F3 | `api-analysis.js` `referenceResponse`: 응답 계약 전체(문맥·세션·요청·receipt 동일성·순서·사유·`ui_updates=[]`)를 먼저 검사한다. 그 뒤에만 만료를 확인하고, 만료면 `code=RECEIPT_EXPIRED`로 거부한다. `api-chat.js`는 이를 `분석 결과가 만료되었습니다. 다시 분석해야 참조 가능합니다.`로 안내한다. 답변 미표시·세션 폐기는 그대로다. 변조와 만료가 겹치면 계약 불일치로 안내한다 |
| 시험 | 신규 Chrome 사례 2개를 추가했다: `selection_lock`(검사 23개), `expiry_in_flight`(검사 11개). 시험 fixture에는 `ttl` 제어를 추가했다. registry TTL과 브라우저·서버 시계는 모두 실제 값을 쓴다 |

### F2: 미광고 상태의 오류 문구 (현재 동작 기록, 코드 변경 없음)

C-1의 `KemsApiChat.errorText`는 capability와 무관하게 두 곳에서 쓰인다. 기본 채팅의 `post`, 그리고 `api-bridge.js`의 `fetchJson`(101–102행)이다.
바뀌는 것은 표시 문구뿐이다. 요청 URL·본문·헤더, HTTP 상태, 실패 판정, 세션 정책, VM 상태는 그대로다.
v1 detail 객체는 opt-in 요청에서만 생긴다. 따라서 미광고 운영 경로에서 실제로 마주치는 알려진 code는 아래 두 개다.

| 경로 | 서버 응답 | 기준 `15f634c` 표시 | C-1 표시 |
|---|---|---|---|
| 기본 채팅(KPG·MN) | 409 `{"detail":"SESSION_BUSY"}` (`_legacy_session_gate`) | `AI 요청이 거부되었습니다. 계통과 입력 내용을 확인하세요. (HTTP 409)` | `현재 대화의 다른 요청을 처리 중입니다. 잠시 후 다시 시도하세요. (HTTP 409)` |
| 지도·PF·N-1·Sensitivity 읽기 | 409 `{"detail":"ANALYSIS_BUSY"}` (`NetworkBusy` 처리기) | `HTTP 409 · ANALYSIS_BUSY` | `HTTP 409 · 모델에서 다른 계산이 진행 중입니다. 잠시 후 다시 시도하세요.` |

- 의도 판정
  - 기본 채팅: 이 문서 오류 절의 "legacy 문자열의 알려진 code 변환"에 해당하는 의도된 변경이다.
  - REST 읽기: 공용 `fetchJson`을 바꾼 결과다. C-1 문서에는 적용 범위가 적혀 있지 않았으며, 이번에 현재 동작으로 기록한다.
- 별도 제안(이번에 구현하지 않음): 미광고 REST 읽기의 기존 문구를 그대로 지켜야 한다면, MAINT-03 병합 뒤 `fetchJson`의 변환을 opt-in 헤더가 있는 요청으로 한정한다. 그 경우 MAINT-03의 지도/PF 409 표시 검사와 문구를 맞춘다.

### 남은 리뷰 항목

- F4: 만료 판정이 브라우저 시계에 의존한다(`api-bridge.js` `referenceOption`). 같은 PC 배포에서는 영향이 없다. `api-bridge.js` 소관이라 MAINT-03 이후 별도로 다룬다.
- F5: 대기 중 capability나 지도 상태가 바뀌면 응답이 안내 없이 버려질 수 있다. 운영 경로는 찾지 못했다. 미수정이다.

### 검증

증거: `.local/ph4-02c1-review/20261105-135739/`. 실제 LLM은 호출하지 않았다.

| 범위 | 결과 |
|---|---|
| C-1 Chrome 전체(기존 8 + 신규 2) | **10 passed (44.81s)**, 브라우저 검사 221개 (`reference-fixed.log`) |
| 수정 전 `dc415f1` 격리 사본 + 신규 시험 | **2 failed**. F1 잠금 검사 3개와 F3 만료 안내 검사 1개만 실패하고 나머지 30개는 통과 (`prefix-dc415f1/`) |
| 정적·Artifact·라우트 | **19 passed**, 1 warning |
| 전체 비브라우저 | **965 passed, 113 skipped**(기존 103 + C-1 Chrome 10), 1 warning (82.12s) |
| 기존 PH4-01 + Phase 3 Chrome | 수정 후 **6 failed / 20 passed** 2회, 같은 명령으로 수정 전 사본 **6 failed / 20 passed** |

- PH4-01 채팅 Chrome 6개는 모든 실행에서 통과했다.
- 다음 4개는 매번 실패했다. C-1 기록과 같은 기존 경합 실패다.
  - `api_grid_maps_and_pf_preview`, `pfgrid`, `pfnonconv`, `n1_and_sensitivity_on_the_real_api`
- 나머지 실패는 실행마다 달랐다.
  - 수정 전 사본: `analysis_drops_stale_results`, `map_performance_in_real_time`
  - 수정 후: `monitored_line_change_drops_sensitivity`, `matches_original_artifact_demo`
- `monitored_line_change_drops_sensitivity`는 선로 선택 단계에서 같은 null 오류로 멈췄다. 수정 전 사본에서 단독으로 2회 실행해도 같은 오류로 실패했다.
- `matches_original_artifact_demo`의 차이는 ieee39 `#sld *` 개수(309/310) 하나뿐이다.
  - 수정 후 두 실행에서 310이 나온 쪽이 원본 Artifact와 콘솔로 번갈아 바뀌었다.
  - 원본 HTML은 콘솔 스크립트를 불러오지 않는다. 따라서 이번 변경과 무관한 렌더링 타이밍 차이로 판단한다.
  - 수정 후 트리에서 단독으로 2회 다시 실행하면 통과했다. 시험은 수정하지 않았다.
- MAINT-03 대상 실패는 수정하거나 우회하지 않았다. 전체 회귀 통과로 보고하지 않는다.

## 통합 전 차단 사항 (과거 기록; FINAL 결과는 아래 참조)

**기존 Phase 3 Chrome은 전체 통과가 아니다.** 다음 5개는 변경 전 `15f634c`에서도 모두 실패한다.

- `test_console_api_grid_maps_and_pf_preview`
- `test_console_endpoint_failures_are_shown_not_replaced[pfgrid]`
- `test_console_endpoint_failures_are_shown_not_replaced[pfnonconv]`
- `test_console_n1_and_sensitivity_on_the_real_api`
- `test_console_analysis_drops_stale_results`

기존 `loadGrid`는 KPG map/PF-preview를 병렬 요청하며, B1 이후 같은 network의 non-blocking guard에서 읽기끼리 경합할 수 있다.
실제 증거에 `409 ANALYSIS_BUSY`와 지도/PF 준비 실패가 있고, 후속 KPI·선로 선택 검사가 실패한다.
C-1은 해당 로딩 순서·서버 guard·기존 테스트를 바꾸지 않았다. 이 실패와 재현 로그를 남기며 전체 회귀 통과나 공개 준비 완료로 보고하지 않는다.

C-2 이전에 이 기존 읽기 경합을 별도 범위로 해결하고 Phase 3 회귀를 재확인해야 한다. 이후 run의 prepare→atomic commit,
서버 검증 receipt/update 대응, 실제 LLM smoke와 예산, 지원 launcher·단일 worker 조건, capability 공개 절차를 검증한다.
MN v1에는 별도 model identity가 필요하다. 본 작업은 C-2/C-3에 착수하거나 Issue #14를 닫지 않는다.


## FINAL — MAINT-03 통합 및 PR #23 병합 준비 (2026-11-05)

### 구현·충돌 해결

`76d9fe8`(C-1/F1/F3)에 `origin/feature/kems-multi-grid` `c7251c3`(PR #24)을 일반 merge했다.
기존 두 C-1 커밋을 보존한다. 충돌은 `docs/dev/STATE.md` 하나였으며 C-1·Claude 후속·MAINT-03의 구현/검증 기록과 미완료 사항을 함께 유지했다.
`api-bridge.js`는 자동 병합됐고 `loadGrid()` 본문은 MAINT-03과 정확히 같다. KPG는 지도 검증·표시 → 현재 view/Abort 확인 → PF,
몽골은 map/GIS/PF 병렬 조회다. 기존 view/session/revision/receipt/reference와 수동 Security/Sensitivity 경로를 보존했다.

이번 별도 편집은 `tests/browser/console_reference_app.py`, 이 문서, STATE뿐이다. fixture의 `READ_GATE`와 초기 map/PF용 30초 잠금 대기 래퍼를 제거했다.
각 요청은 다른 요청과 동일하게 `call_next`로 실제 서버에 전달하고 상태만 기록한다. 명시적 오류/지연, TTL/모델 변경, 실제 계산/registry,
기본 scripted graph, LLM 금지, 210초 case·150초 Chrome·60초 기동·5초 HTTP 제한은 유지한다. 테스트 단언이나 사례를 삭제·완화하지 않았다.

`api-chat.js`, `api-analysis.js`, C-1 probe/테스트 파일은 `76d9fe8` 그대로다. F1 잠금·F3 만료/변조 구분과 F2 문구 기록을 보존했다.
서버·Network Guard·계산 설정·모델·원본 UI·검증 해시를 변경하지 않는다. run UI/v1 광고/MN v1/C-2/C-3는 포함하지 않는다.

### 실제 통합 검증

증거 루트: `.local/ph4-02c1-final/20261105-integration/`. 아래는 **이번 실행**이며 앞선 과거 결과와 합산하지 않는다.

| 범위 | 결과 | 로그 |
|---|---|---|
| 기존 문제 5건 + MAINT-03 4건, 1회 | **9 passed (31.51s)**, 235개 검사 | `a1.log` |
| 같은 9건, 2회 | **9 passed (29.63s)**, 235개 검사 | `a2.log` |
| 우회 없는 C-1 Chrome | **10 passed (45.47s)**, 221개 검사 | `b-reference.log` |
| Phase 3 전체 + PH4-01 Chrome | **27 passed (119.40s)**, Phase 3 21 + 채팅 6 | `c-chrome.log` |
| 첫 전체 비브라우저 | **555 passed, 117 skipped, 410 errors (65.89s)**, 실행용 임시 경로 준비 오류 | `c-nonbrowser.log/xml` |
| 경로 보완 후 전체 비브라우저 | **965 passed, 117 skipped, 1 warning (94.39s)** | `c-nonbrowser-final.log/xml` |
| 정적·원본 Artifact·라우트 | 위 최종 전체 실행의 **19 passed** 부분집합(JUnit 확인) | `c-nonbrowser-final.xml` |
| IEEE39 추가 SVG 관찰 | **1 passed (22.16s)**, 기존 전체 fingerprint 단언 유지 | `d-parity.log` |

첫 비브라우저 410개 오류는 모두 `--basetemp` 상위 `pytest/` 폴더가 없는 동일 FileNotFoundError였다. 제품 코드·fixture 단언 변경 없이
실행 경로의 상위 폴더를 만들고 새로운 경로에서 전체 재실행했다. 이전 실패를 지우거나 통과/skip으로 표시하지 않았다.
최종 skip 117은 기존 opt-in/환경 103 + C-1 Chrome 10 + MAINT-03 Chrome 4다. 경고 1개는 기존 Starlette/AnyIO deprecation이다.

A의 기존 5개는 API map/PF, PF 다른 계통, PF 비수렴, 실제 N-1/Sensitivity, 분석 stale이다. 실행마다 기존 213 + 신규 로딩 22개 검사와 JS 오류 0.
신규 로딩 4개는 KPG 순차/MN 병렬, map/PF 실패·409 노출/재시도 없음, Abort/계통 전환/동일 계통 reload/늦은 성공·실패, timeout 복구를 포함한다.
명시적으로 주입한 PF 409는 유지하며 초기 읽기의 비의도적 409는 재현되지 않았다.

C-1은 순차 수동 분석의 동일 owner·chain receipt, reference 전후 graph/Tool/PyPowSyBl 호출 수 불변, stale, F1 선택 잠금·복구,
F3 만료·변조 분리, 미광고 기본 채팅/수동 분석을 확인했다. 초기 map/PF 읽기 **28개 전부 HTTP 200**. 실제 LLM 호출과 JS 오류는 0이다.

### IEEE39와 남은 리뷰 사항

변경하지 않은 전체 Chrome 비교와 추가 관찰 모두 원본/콘솔 IEEE39 SVG 309개, 전체 fingerprint가 일치했다.
추가 관찰은 `.local/`의 probe 사본에 관찰값만 넣었다. viewBox `0 0 640 780`, SVG 크기 `817.4375 × 364.1875`, 노드의 tag/id/class/text/x/y 배열도 같다.
원본 테스트/단언·정규화는 그대로이며, 원본 HTML과 제품 코드를 수정하지 않았다.

과거 C-1 리뷰·MAINT-03의 309↔310 실패를 이번 통과로 삭제하지 않는다. 원본과 콘솔에서 더 큰 수가 번갈아 나타났고, 이번과 과거 단독 실행은
일치한다. `app.js`의 overlay 배치는 viewBox/크기와 rAF 시점에 의존하며 이 코드는 이번 merge로 바뀌지 않았다. **기존 렌더링 시점 차이로 보는
과거 분석과 일치하지만, 이번에는 차이가 재현되지 않아 정확한 트리거·누락 노드를 확정하지 못했다.** 실제 UI 내용 차이나 이번 통합 회귀의 근거는 찾지 못했다.

- F4: 브라우저 시계가 앞서면 유효 receipt가 조기에 비활성화되고 뒤처지면 서버가 만료를 거부할 수 있다. 서버 monotonic TTL/owner/revision이 최종 경계다.
  이번에는 시계 보정 기능을 추가하거나 시간 오차를 새로 주입하지 않았다. 이 UX 제약을 해결했다고 보고하지 않는다.
- F5: `referenceCurrent`가 false이면 응답 적용을 막고 `finally`에서 pending을 해제한다. capability/지도 상태가 통지 없이 바뀌면 안내 없는 폐기 가능성이 있다.
  정상 계통 전환·초기화·수동 분석·선택 잠금/프로그램 변경의 회귀는 통과했다. 새로운 운영 재현 경로나 잘못된 결과 수용은 확인되지 않아 범위를 넓히지 않았다.
- 다른 탭/클라이언트/진행 중 분석이 같은 network를 점유하면 정상적인 409는 여전히 가능하다. Abort는 서버 계산 중단을 보장하지 않는다.
- **판정: 요청된 최종 통합 회귀 PASS, 병합 권고 CONDITIONAL.** 알려진 IEEE39 불안정성의 별도 처리를 수용하는 검토가 필요하다.
  PR #23은 Draft로 유지하며 자동 병합하지 않는다. 실제 LLM smoke·공개 capability·C-2는 별도 작업이다.

### 재현·리뷰 근거

`run_tests.py`는 위 루트의 로컬 실행 보조다. 각 실행의 정확한 pytest argv는 `*-command.json`에 보존했다.
`c1final_checks.py`는 테스트별 240초 제한(신규 자체 제한 210초 유지), 기존 증거와 다른 출력 경로, Chrome 프로필 보존만 설정한다.
기존 source/단언/요청 순서에는 관여하지 않는다. 전체 명령은 1200초 + kill 15초이며, 모든 로그·임시 산출물·기존 worktree를 보존한다.

```bash
run_dir=.local/ph4-02c1-final/20261105-integration
# 새 실행 이름 사용: 기존 basetemp/증거를 덮어쓰지 않음
python3 "$run_dir/run_tests.py" repeat-reference browser tests/test_console_reference_browser.py
python3 "$run_dir/run_tests.py" repeat-chrome browser tests/test_console_browser.py tests/test_console_chat_browser.py
python3 "$run_dir/run_tests.py" repeat-nonbrowser nonbrowser tests
```

- C-1 JSON/PNG: `.local/browser-tests/{logs,screenshots}/console_reference_c1/b62cb4ca788c/`.
- Phase 3 JSON/PNG: `.local/browser-tests/{logs,screenshots}/console_c1final/c-chrome/`(스크린샷 22장).
- MAINT-03 JSON: `.local/browser-tests/logs/console_loading_maint03/8836363683d9/`, `098b2568479e/`.
- IEEE39 추가 관찰 JSON: `.local/browser-tests/logs/console_c1final/d-parity/`.
- 스크린샷에서 지도/분석/채팅 공존을 확인했다. 기존 Linux 한국어 폰트 부재는 그대로이며 내용은 DOM 문자열로 검사했다.
- 리뷰: `loadGrid()`의 MAINT-03 동일성, F1/F3 및 서버/모델/기존 테스트·해시 보존, `git diff --check`, Git 제외 증거와 인수인계를 확인한다.
