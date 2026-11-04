# AI-EMS 작업 인수인계 상태

갱신일: 2026-11-04 (Asia/Seoul). 최근 작업과 다음 작업만 유지한다.

## 현재 단계

- Phase 3 완료. Phase 4의 PH4-01 AI 채팅 통합 구현·로컬 검증 완료.
- DOC-01: 개발 규칙과 출처·해시를 갖춘 개발팀 키트/Artifact 참고 자료를 정리하여 통합 브랜치에 반영했다.
- PH4-02A·B0·B1·B2(+MAINT-02)·B3-1은 PR #17·#18·#19·#20·#21로 통합됐다(B3-1 squash `00f6edc`).
- PH4-02B3-2: KPG `mode=run`을 요청별 B2 Agent·ledger·`verify_evidence`에 연결하고, 검증된 결과만 `producer=agent_tool` receipt로
  한 network guard 안에서 원자 등록한다. legacy 채팅의 세션 gate 공유, provider timeout·실행 예산·단일 worker 단언까지 구현·로컬 검증 완료
  (`feature/ph4-02b32-agent-run-api`, Draft PR 검토 대상). 실제 LLM smoke는 endpoint HTTP 502로 **미실행**이다.
- v1 capability(`console_analysis_context_versions`)는 여전히 어떤 계통에도 광고하지 않는다. 몽골·한국 OSM v1은 409로 거부하고 콘솔 UI는 바꾸지 않았다.
- 실시간 브랜치·HEAD·커밋 여부·staged/unstaged/untracked 상태는 `dev_handoff.py` 출력으로 확인한다.
- 다음 기능 작업은 [PH4-02 #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14)의 C(콘솔) 단계이며 자동 착수하지 않는다.

## 최근 완료 작업: PH4-02B3-2

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

## PH4-02B3-2 실제 검증

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

- PH4-02C(콘솔)는 별도 요청으로 진행한다. run 응답의 `completed/not_evaluated/not_run/failed`·사유 코드 표시, 성공 run의 `ui_updates`만 반영,
  v1 오류(`AGENT_*`, `ANALYSIS_RUN_NOT_READY`, `REQUEST_REPLAY_STALE`, `SESSION_BUSY`)·legacy 문자열 오류·`analysis_receipt_error` 처리,
  Security 감시 ID null·Sensitivity top_n=5, 취소/timeout은 결과 미확정으로 다루고 같은 `request_id`를 자동 재시도하지 않는다.
- C 이전/병행 확인: 실제 LLM smoke(고정 Tool·인자 준수, 지연)와 예산 재조정, 배포 환경의 지원 launcher·단일 worker. MN v1은 model identity가 별도로 필요하다.
- 위 연결과 Chrome 회귀를 마친 뒤에만 `console_analysis_context_versions: [1]`을 광고한다.
- B3-2는 base=`feature/kems-multi-grid`인 `feature/ph4-02b32-agent-run-api`·Draft PR로 검토한다. 상위 #14는 완료 처리하지 않으며 C는 자동 착수하지 않는다.
- 통합된 작업 브랜치(DOC-01, PH4-02A·B0·B1·B2·B3-1)는 이력 보존상 삭제 후보이며 실제 삭제와 PR/main 병합은 별도 승인 후 판단한다.
- 다음 담당자는 `AGENTS.md`와 이 문서를 읽고 실시간 Git 상태와 기존 변경을 확인·보존한 뒤 지정된 범위만 수행한다.
- 시작: `python3 scripts/dev_handoff.py`
- 종료: 이 문서를 갱신한 후 `python3 scripts/dev_handoff.py --write`
- 실제 테스트 로그가 있을 때만 `--test-log PATH`를 추가한다. 로그 요약은 현재 HEAD의 성공을 자동 증명하지 않는다.
