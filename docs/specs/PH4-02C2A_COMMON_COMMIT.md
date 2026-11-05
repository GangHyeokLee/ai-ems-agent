# PH4-02C-2a — 콘솔 분석 상태의 Revision 분리와 공통 Commit

기준: C-1 PR #23 HEAD `6928d89`(MAINT-03 병합 포함). 작업 브랜치: `feature/ph4-02c2a-common-commit`.
PR #23이 열려 있으므로 그 브랜치를 base로 하는 stacked Draft PR로 게시한다. 상위 [Issue #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
보고일: 2026-11-05 (Asia/Seoul).

## 범위

C-2에서 검증된 AI run 결과를 화면에 넣기 전에 필요한 공통 구조만 만든다. **동작은 바꾸지 않는다.**

- 변경: `ui/console/api-bridge.js`, `ui/console/api-chat.js`, 신규 테스트, 이 문서와 STATE.
- 변경하지 않음: 서버·`web_app.py`(해시 체인 기록 불필요), 데이터, 물리해석, 원본 Artifact, `api-analysis.js`, 기존 검증 해시.
- 구현하지 않음: run 모드 표시, `mode=run` 전송, run 응답 검증(C-2b), run 결과 반영(C-2c), capability 광고.

## 변경 전 문제 (`6928d89`)

- `advanceAnalysis()`(api-bridge.js 624–630행)가 두 가지를 한 번에 했다.
  - 대기 중인 reference 요청 무효화: `apiChat.invalidateAnalysisContext()`. reference가 대기 중이면 세션을 폐기하고 `sessionRetired`가 receipt를 지운다.
  - revision 증가.
  
  C-2 run이 자기 결과를 반영하면서 이 함수를 부르면, 아직 대기 중인 자기 요청을 retire해 세션과 방금 받은 receipt를 잃는다.
- `sessionRetired()`(632–643행)는 같은 revision 증가 코드를 중복으로 갖고 있었다.
- 수동 결과 반영이 `runSecurity()`(808–817행)와 `runSensitivity()`(850–859행) 안에 직접 들어 있었다(VM 생성 → `advanceAnalysis` → receipt 수용 → mode). 그래서 run이 재사용할 수 없었다.
- chat의 대기 요청은 `request.reference`라는 reference 전용 필드로만 표현돼, 선택 잠금(F1)과 취소가 reference에만 묶여 있었다.

## 변경 구조

| 함수 (api-bridge.js) | 책임 |
|---|---|
| `bumpRevision(view)` (628행) | revision만 증가. 한도(`MAX_SAFE_INTEGER`)에 닿으면 새 view UUID와 revision 0. 요청 취소·세션·receipt에는 손대지 않는다. **revision을 쓰는 유일한 함수** |
| `advanceAnalysis(view)` (637행) | 사용자 문맥 변경: `invalidateAnalysisContext()` 다음 `bumpRevision(view)`. C-1과 같은 동작 |
| `sessionRetired(gridId)` (642행) | 중복 코드를 `bumpRevision(view)`로 교체(재귀 없음) |
| `storeReceipt(view, kind, entry, notice)` (663행) | receipt 칸을 쓰는 유일한 함수. 받은 receipt면 안내를 지우고, 없으면 사유가 있을 때만 안내를 바꾼다 |
| `acceptManualReceipt(...)` (669행) | 판정 순서와 문구는 그대로이고, 모든 기록을 `storeReceipt`로 한다 |
| `supersedeAnalysisRequests(a)` (784행) | 이 view의 N-1·Sensitivity 응답을 모두 무효화(abort, `seq`, `sensSeq`). 지금은 `clearAnalysis`가 사용한다 |
| `prepareAnalysisResults(view, items)` (795행) | 순수 함수. REST 형식 결과를 기존 `KemsApiAnalysis.security/sensitivity`로 검증해 VM을 만든다. 실패하면 `Invalid`를 던지고 상태는 쓰지 않는다 |
| `commitAnalysisResults(view, prepared, {epoch, storeReceipts})` (817행) | 동기 반영. 순서는 VM 대입 → `epoch(view)` → `storeReceipts()` → `view.mode='analysis'`. await·검증·render가 없다. 그리기는 호출자가 한 번 한다(`analysisChanged`) |

수동 경로(`runSecurity` 841행, `runSensitivity` 884행):
1. 기존 `current()` 확인
2. `prepareAnalysisResults`. 실패하면 기존처럼 `invalid` 상태로 둔다.
3. `commitAnalysisResults(view, prepared, {epoch: advanceAnalysis, storeReceipts: () => acceptManualReceipt(...)})`
4. `analysisChanged()` 한 번

`clearAnalysis`는 `supersedeAnalysisRequests(a)`를 쓴다.

api-chat.js: `request.reference`를 `request.analysis`로 바꿨다(frozen bridge snapshot). `context.mode`로 종류를 구분하며 지금은 `reference`뿐이다. 선택 잠금(123행)과 취소(262행)는 이 필드 하나를 본다. 요청 본문(52행), 현재성(190행), 검증(199행), 표시(207행)도 같은 필드를 쓴다. 문구·전송 본문·hook 이름은 그대로다.

## C-1 동작 보존 근거

1. **순서 불변:** 수동 반영 순서(VM → `advanceAnalysis` → receipt → mode)와 `Invalid` 처리 범위가 이전과 같다. `acceptManualReceipt`의 분기·문구·안내 유지 규칙(수동 opt-in이 없으면 안내를 건드리지 않음)도 그대로다.
2. **동작 서술 테스트:** 신규 Chrome `commit_contract`는 리팩터링 전 `6928d89`의 원래 UI에서도 14/14 통과한다. 단계별 상세값도 두 빌드에서 14/14 동일하다(VM 요약, mode, overlay, receipt 형태, 안내, 시간만 가린 패널 텍스트, revision 증가량, render 횟수).
3. **기존 회귀:** C-1 Chrome 10개, MAINT-03 4개, PH4-01 6개, Phase 3가 모두 통과한다(원본 Artifact 비교의 기존 변동 1건은 아래 미해결 참고). 비브라우저 전체는 C-1 기록에 신규 테스트 수만 더한 값과 같다.
4. **변이:** 구조 계약 변이 9/9 검출. 동작 변이는 3/4 검출했고, 나머지 1개(초기화의 `sensSeq` 증가 제거)는 관찰 가능한 차이가 없는 동등 변이다.

관찰 가능한 동작 차이는 없다. 내부 차이는 VM의 `at` 시각이 VM 생성 직후가 아니라 반영 시점(같은 동기 구간, 마이크로초 단위)에 계산된다는 점뿐이다.

## C-2b/C-2c에 제공하는 계약

- `prepareAnalysisResults(view, [{kind, payload, line, monitored}])`
  - 지원하는 `kind`는 `security`와 `sensitivity`다. `payload`는 REST와 같은 형식이며, run 응답의 `ui_updates[].result`도 같은 형식이다(B3-2).
  - 반환값은 frozen `{security?: {vm}, sensitivity?: {vm, requested}}`이다. 실패하면 `KemsApiAnalysis.Invalid`를 던진다.
  - run envelope·agent receipt·`ui_updates` 검증은 C-2b에서 이 함수 **앞에** 둔다.
- `commitAnalysisResults(view, prepared, {epoch, storeReceipts})`
  - 수동 결과에는 `epoch: advanceAnalysis`를 쓴다.
  - **대기 중인 chat 요청이 자기 결과를 반영할 때(run)는 `epoch: bumpRevision`을 써야 한다.** `advanceAnalysis`를 쓰면 자기 요청을 retire한다.
  - `storeReceipts`는 epoch 다음에 실행되므로 epoch가 남긴 세션을 본다.
- run 반영 전에 진행 중인 수동 요청을 무효화할 때는 `supersedeAnalysisRequests(a)`를 쓴다.
- receipt 기록은 `storeReceipt(view, kind, Object.freeze({receipt, sessionId}))`로 한다.
- chat의 v1 요청은 `request.analysis`(frozen snapshot, `context.mode`) 하나로 표현한다.
  - run을 추가해도 선택 잠금·취소·본문은 그대로 동작한다.
  - 응답 검증·표시는 mode별로 분기해야 한다(199·207행).

## 검증

증거: worktree `.local/ph4-02c2a/20261105-152220/`. 실제 LLM은 호출하지 않았다.

| 범위 | 결과 |
|---|---|
| 신규 구조 계약 `test_console_commit_contract.py` + 정적 `test_console_ui.py` | **28 passed** (9 + 19) |
| C-1 Chrome 10개 + 신규 `commit_contract` | **11 passed (49.93s), 검사 235개** (`reference-c2a.log`) |
| `commit_contract`를 리팩터링 전 `6928d89` 사본에서 실행 | **1 passed, 14/14**. 단계별 상세값 14/14 동일 (`baseline-6928d89/`, `commit-contract-diff.txt`) |
| MAINT-03 loading Chrome | **4 passed (8.32s)** (`maint03-loading.log`) |
| PH4-01 + Phase 3 Chrome | **25 passed, 1 failed, 1 deselected (82.42s)**. 실패는 원본 Artifact 비교의 IEEE39 `#sld *` 310/309. 같은 테스트 단독 재실행 **2회 통과** (`ph401-phase3.log`, `parity-rerun-*.log`) |
| 전체 비브라우저 | **974 passed, 118 skipped, 1 warning (86.63s)**. C-1 기록 965/117에 신규 계약 9개(passed)와 Chrome 1개(skip)를 더한 값 (`nonbrowser-final.log`) |
| 변이 | 구조 9/9, 동작 3/4(나머지 1개는 동등 변이) (`mutations.txt`) |

**환경 기록**
- worktree에는 Git이 추적하지 않는 실행 자료가 없다. 그래서 원본 저장소의 무시 대상 경로를 읽기 전용 심볼릭 링크로 연결했다: KPG `.mat`, `bus_location.csv`, `data/grids`, `local-reference`, `.cache`, `.venv`, `outputs/mn_stage3b/bundles/*`, `outputs/kr_topology`. 이 링크들은 커밋하지 않았다.
- 첫 비브라우저 실행은 몽골 bundle 링크가 없어 **950 passed, 120 skipped, 22 errors**였다(`nonbrowser.log`).
- 이어진 두 실행은 bundle 1개 누락으로 1 failed, 한국 분석 산출물 누락으로 972 passed/120 skipped였다. 이 두 로그는 같은 파일명으로 덮어써 수치만 기록한다. skip 사유를 남긴 972/120 실행은 `nonbrowser-final-rs.log`에 있다.
- 실행 자료를 모두 연결한 최종 실행이 위 수치다.
- `git archive` 사본은 Git 체크아웃이 없어 해시 체인 테스트 2개가 실패한다(`nonbrowser-baseline-6928d89.log`). 그래서 비브라우저 비교 기준은 C-1 기록 수치로 했다.

실행 명령(worktree 루트, `RUN=.local/ph4-02c2a/20261105-152220`, `deadlines.py`·`browser_paths.py`는 제한 시간과 출력 경로만 설정):

```bash
CH="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome"
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_console_commit_contract.py tests/test_console_ui.py
env PYTHONDONTWRITEBYTECODE=1 AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$CH" .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_console_reference_browser.py
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUN:src:." AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$CH" .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines tests/test_console_loading_browser.py
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUN:src:." AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$CH" .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines -p browser_paths \
  tests/test_console_chat_browser.py tests/test_console_browser.py -k 'not test_console_screenshots'
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUN:src:." .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines tests
```

## 미해결

- 원본 Artifact 비교의 IEEE39 SLD 노드 수 309/310 변동은 그대로다(MAINT-04에서 별도 조사 중). 이번 변경은 데모 모드 코드를 건드리지 않는다.
- run의 envelope·agent receipt·`ui_updates` 검증(C-2b), run 반영과 잠금·상태별 UI(C-2c), 확정 오류 시 세션 유지 정책, run capability 분리는 PM 결정과 다음 단계의 범위다.
- C-1 리뷰의 F4(브라우저 시계 기반 만료)와 F5(안내 없는 폐기 가능성)는 이번 범위가 아니다.
