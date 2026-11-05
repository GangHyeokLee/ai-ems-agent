# PH4-02-MAINT-03 — KPG 지도/PF 초기 조회 경합

기준: `feature/kems-multi-grid` `15f634c995894f5e49cddef627c5fba9b8cbcc8a`.
작업: `feature/ph4-02-maint03-map-pf-sequencing`, `~/Projects/ai-ems-agent-maint03`.
상위 [Issue #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14), 관련 [Draft PR #23](https://github.com/GangHyeokLee/ai-ems-agent/pull/23). 보고일: 2026-11-05 (Asia/Seoul).

## 원인과 재현

기존 `loadGrid()`는 KPG map과 PF-preview를 함께 시작했다. 두 서버 경로는 같은 KPG network의
B1 non-blocking guard를 사용하므로 읽기 요청도 겹치면 HTTP 409 `ANALYSIS_BUSY`가 된다.
이는 단순 로딩 지연이 아니라 PF/지도 준비 실패로 남아 KPI·선로 선택·N-1 표시의 후속 검사도 실패시킨다.

C-1의 과거 로그에서 실패 5개와 기준 커밋 재현을 읽기 전용으로 확인했다. 이번 worktree의 수정 전 실제 실행은
**4 failed, 1 passed (21.80s)**였다. `analysis_drops_stale_results`는 이번에는 통과했다. 경합의 발생 순서는 비결정적이며
C-1의 과거 5개 실패를 이번 실행 결과로 옮겨 쓰지 않는다. `baseline-five.log`와 `baseline-real/api.json`에 409 증거가 있다.

## 구현 범위와 계약

운영 코드 변경은 **`ui/console/api-bridge.js`의 `loadGrid()` 8줄 추가·2줄 제거**다.

- KPG만 지도 응답 수신 → 기존 지도 검증 → 기존 지도 표시 → 현재 view/Abort 재확인 → PF 요청 순서로 바꾼다.
- 지도 표시는 PF 응답을 기다리지 않는다. PF의 20초 제한은 실제 PF 요청을 시작할 때 적용한다.
- 지도 실패·잘못된 계통·형식·timeout에는 기존 지도 오류와 PF `blocked` 상태를 유지하며 KPG PF는 시작하지 않는다.
- PF 실패/409/잘못된 응답/비수렴은 기존 처리와 안내를 유지한다. 409 무시·재시도·숨은 재계산은 없다.
- 기존 `current()`의 view 객체 동일성·선택 계통·Abort 검사로 이전 지도 응답에서 PF를 새로 시작하지 않으며,
  PF 응답도 기존 검사를 통과한 현재 view에만 적용한다. 동일 계통 재연결도 새 view다.
- 몽골의 map·display geometry·PF 병렬 요청은 유지한다. 한국 OSM은 기존 지도 전용 capability를 유지한다.
- guard·서버·모델·계산 설정·원본 Artifact·기존 테스트·검증 해시를 바꾸지 않는다. C-1 receipt/reference 코드는 가져오지 않는다.

전체 완료 시각은 KPG 지도 시간 + PF 시간이지만 지도 가시화는 지도 완료 직후다. 기존 요청별 catalog 10초·map 30초·PF 20초 제한을
유지하며 인위적인 대기·backoff를 추가하지 않는다. 다른 탭·클라이언트·기존 Agent/분석이 같은 network를 점유하면 여전히 409가 가능하다.
HTTP Abort가 서버 계산 중단을 보장하지 않으므로 계산 중 계통 재선택에서도 같은 제한이 있다. 오류를 표시하고 명시적 재연결을 허용한다.

## 검증 구조

신규 파일은 `tests/test_console_loading_browser.py`, `tests/browser/console_loading_probe.js`다.
기존 실제 FastAPI 서버·Chrome·프록시를 재사용하며 프록시는 명시적으로 지정한 응답만 지연·오류 처리한다.
애플리케이션 요청을 대신 순차화하거나 guard를 우회하지 않는다. 브라우저 fetch 관찰은 요청 시작·본문 수신·Abort·당시 지도 상태를 기록한다.

4개 Chrome 시나리오, **22개 검사, JavaScript 오류 0**:

1. KPG map 응답을 보류하는 동안 PF 요청 0, 지도 본문 수신 후 PF 시작, PF 대기 중 지도 표시, 요청 각각 1회.
   몽골은 지도 응답을 보류해도 PF 요청이 시작되는 기존 병렬 계약 확인.
2. 지도 500/다른 계통 응답에 PF 차단, PF 409 표시·재시도 0, 명시적 재연결 복구.
3. 정상 Abort 및 취소를 무시한 지도/PF의 늦은 성공·실패. 계통 전환·동일 계통 새 view에서 현재 결과 불변과 퇴역 지도에서 PF 시작 0.
4. map/PF timeout 각각 Abort·오류·blocked/지도 유지·명시적 재연결 복구. 해당 요청의 timeout만 80ms로 단축한 장애 주입이다.

응답 지연 gate는 실제 upstream 계산·본문 수신 완료를 별도로 관찰한 뒤 전달만 보류한다. 초기 테스트는 gate의 **요청 도착**을
계산 완료로 오인하여 이전 PF 실행 중 새 KPG 지도에서 정상적인 409를 받은 **1 failed, 8 passed**였다.
새 관찰 지점은 이 두 상태를 구분한다. 실제 계산이 진행 중인 경우까지 성공을 보장하는 fixture/서버 변경은 하지 않았다.
기존 대상 5개 테스트는 이 신규 fixture 없이 원래 서버/프록시 경로로 실행한다.

## 실제 검증 결과

| 범위 | 실제 결과 | 로그 |
|---|---|---|
| 수정 전 대상 5개 | **4 failed, 1 passed (21.80s)** | `baseline-five.log` |
| 수정 후 대상 5개 | **5/5 통과**. 아래 신규 최초 실행과 합쳐 8 passed/1 failed였으며 실패는 신규 취소 관찰 시나리오 | `fixed-five-loading.log` |
| 최종 신규 로딩 Chrome | **4 passed (8.37s)**, 22개 검사, JS 오류 0 | `loading-final.log` |
| 기존 Phase 3 전체 + PH4-01 Chrome, 첫 실행 | **1 failed, 26 passed (120.01s)** | `chrome-regression.log` |
| 동일 전체 Chrome 재실행 | **1 failed, 26 passed (114.64s)** | `chrome-regression-final.log` |
| 기준 커밋 Artifact 비교 단독 | **1 passed (23.37s)** | `base-parity.log` |
| 수정본 Artifact 비교 단독 | **1 passed (21.02s)** | `parity-isolated.log` |
| 초기 비브라우저 | **963 passed, 109 skipped, 1 warning (97.25s)**. 한국 검토 입력 누락 2건 포함 | `nonbrowser.log` |
| 자료 보충 후 최종 비브라우저 | **965 passed, 107 skipped, 1 warning (83.14s)** | `nonbrowser-final.log` |

전체 Chrome의 유일한 실패는 `test_console_matches_original_artifact_demo`다. 첫 실행에서 IEEE39의 SVG 전체 자식 수는
원본 310/콘솔 309, 다음 실행에서는 원본 309/콘솔 310이었다. 나머지 모델 수치와 브라우저 오류 검사는 일치했다.
기준과 수정본의 동일 테스트는 단독 실행에서 각각 통과했다. 데모 전용 비교에서는 수정한 `loadGrid()`를 호출하지 않지만,
SVG 노드 수의 시간 의존성을 이번 작업에서 해결했다고 주장하지 않는다. **전체 Chrome 통과 상태는 아니며 이 별도 불안정성이 남는다.**
원본 UI·비교 테스트·정규화·단언을 바꾸거나 최종 결과에서 실패를 제외하지 않았다.

두 전체 실행에서 기존 Phase 3의 나머지 **20개**(스크린샷 22장 생성 포함)와 PH4-01 채팅 **6개**가 통과했다.
목표 5개는 원래 fixture에서 모두 통과하며, 두 번째 실행의 JSON에서 API 121·PF grid 6·PF 비수렴 5·분석 65·분석 stale 16개 검사와
JS 오류 0을 확인했다. 최종 신규 증거는 `.local/browser-tests/logs/console_loading_maint03/4d9024762bdc/`,
기존 전체 증거·스크린샷은 `.local/browser-tests/{logs,screenshots}/console_maint03/full-regression-final/`에 있다.

비브라우저 skip 107개는 기존 opt-in/환경 103개와 신규 Chrome 4개다. 부족했던 한국 검토 입력 2건은 사본 보충 후 실제로 통과했다.
기존 추적 파일 354개와 공급한 데이터 62개의 SHA256을 보존했으며 `git diff --check`를 통과했다.

구현 결과는 KPG 초기 읽기 경합에 대해 완료이며, 전체 검증 상태는 Artifact 비교 불안정성 때문에 **CONDITIONAL**이다.
C-1/MAINT-03 실제 통합 후 원래 경로의 회귀를 확인하고 Artifact 비교를 별도 범위로 안정화해야 한다.

## 환경과 증거

증거 루트는 worktree 안 `.local/maint03/20261105-map-pf/`다. 로그·프로필·캐시·pytest 임시 경로도 worktree 안에 둔다.
기존 저장소는 지침·C-1 보고서·실패 증거·의존성/데이터를 읽는 데만 사용했다. `.env`나 인증 정보를 복사하지 않았다.
가상환경 패키지는 읽기 전용 연결하며 Python bytecode 쓰기를 끈다. 계통 데이터·Artifact·승인된 몽골 번들·한국 검토 입력만
Git 제외 경로로 별도 복사하고 SHA256을 대조했다(`supplied-data-sha256.json`). 외부 sibling 모델/보고서는 읽기만 한다.

처음 Chrome은 긴 TMPDIR의 Unix socket 경로 제한으로 시작 실패했다(`baseline-chrome-path-error.log`).
worktree 내부의 짧은 `.local/t`로 바꾸었다. 테스트 실패를 skip/xfail로 바꾸거나 기존 단언을 수정하지 않았다.
각 테스트는 210/240초, 신규 Chrome은 150초, 신규 서버 기동 60초, 응답 관찰 15초, 전체 명령은 600/900/1200초+kill 제한이다.
기존 Phase 3 Chrome도 테스트별 240초 제한을 적용하며 기존 harness 자체의 서버/HTTP/Chrome 제한을 보존한다.
기존 증거를 보존하려고 로컬 pytest plugin은 출력 폴더와 실행 제한만 설정한다.

재실행 예시(저장소 루트):

```bash
run_dir="$PWD/.local/maint03/20261105-map-pf"
export PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.local/t"
export XDG_CACHE_HOME="$run_dir/cache" XDG_CONFIG_HOME="$run_dir/config"
export PYTHONPATH="$run_dir:$PWD/src:$PWD"
export AI_EMS_CHROME=/home/rkdgu/Projects/ai-ems-agent/.cache/mn-stage1-chrome/chrome-linux64/chrome
timeout --kill-after=15s 1200s env AI_EMS_BROWSER_TESTS=1 MAINT03_EVIDENCE=another-run \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p maint03_checks \
  tests/test_console_browser.py tests/test_console_chat_browser.py tests/test_console_loading_browser.py
timeout --kill-after=15s 1200s .venv/bin/python -B -m pytest -q -p no:cacheprovider -p maint03_checks tests
```

실제 LLM은 호출하지 않는다. PH4-01만 기존 scripted graph를 사용하며 map/PF/N-1/Sensitivity는 실제 서버 경로다.
Chrome 스크린샷은 생성하지만 한국어 폰트 부재로 일부 글리프가 네모로 보일 수 있다. 내용은 DOM 문자열로 검사한다.

## PR #23과 통합 시 주의

C-1 커밋 `dc415f1`의 `loadGrid()` 본문은 이 작업 기준의 본문과 바이트가 같다. 따라서 해당 작은 JS 변경은
현재 PR #23과 겹치는 로직 수정이 없지만 `api-bridge.js` 자체는 공통 파일이므로 통합 후 실제 diff와 Chrome 회귀를 확인해야 한다.
`docs/dev/STATE.md`의 최근 작업·현재 단계는 양쪽에서 바뀌어 수동 조정이 예상된다. 과거 검증 수치를 보존한다.

PR #23의 테스트 전용 초기 읽기 순서 제어는 MAINT-03 적용 후 제거 여부를 C-1 담당자가 판단하고, 우회 없는 통합 검증을 해야 한다.
이 브랜치는 PR #23을 수정·병합하지 않으며 C-1 결과를 재검증했다고 주장하지 않는다. C-2·capability 공개는 별도 작업이다.
