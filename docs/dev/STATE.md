# AI-EMS 작업 인수인계 상태

갱신일: 2026-11-04 (Asia/Seoul). 최근 작업과 다음 작업만 유지한다.

## 현재 단계

- Phase 3 완료. Phase 4의 PH4-01 AI 채팅 통합 구현·로컬 검증 완료.
- DOC-01: 개발 규칙과 출처·해시를 갖춘 개발팀 키트/Artifact 참고 자료를 정리하여 통합 브랜치에 반영했다.
- PH4-02A·B0·B1은 PR #17·#18·#19로 통합됐다. B2 KPG full artifact·요청별 실행 ledger·서버 검증 구현과 로컬 검증 완료(Draft PR 검토 대상).
- B2 진입점은 API에 연결하지 않았다. ChatRequest 확장·receipt registry·분석 문맥 API·콘솔 연동은 미구현이며 v1 capability는 계속 비활성이다.
- 통합 기준 `6a4df5e`에서 이미 `tests/test_mn_pipeline.py` 2개가 실패한다. B1의 `web_app.py` 변경이 PH4-01 해시 고정과 맞지 않기 때문이며
  (B0 트리 사본에서는 통과) B2는 해시 기록·테스트를 바꾸지 않았다. 별도 승인 작업이 필요하다.
- 실시간 브랜치·HEAD·커밋 여부·staged/unstaged/untracked 상태는 `dev_handoff.py` 출력으로 확인한다.
- 다음 기능 작업은 [PH4-02 #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14)의 B3(서버 계약)·C(콘솔) 단계다.

## 최근 완료 작업: PH4-02B2

- `src/ai_ems/analysis_context.py`(신규): frozen `AnalysisJob`(KPG만, operation·고정 사고/감시), 요청별 `ExecutionLedger`
  (authorized→started→succeeded/failed, 봉인 artifact SHA256·실행 전후 revision), ToolNode 전 guard 거부 코드, `verify_evidence`.
- `agent/tools.py`: legacy 축약을 순수 `summarize_line_contingency`로 추출(반환 동일). v1 전용 `create_line_analysis_tools`는 legacy와 같은
  이름·schema로 인가된 ToolCall만 1회 계산하고 축약 content와 같은 계산의 full artifact를 함께 반환한다.
- `agent/graph.py`: `create_line_analysis_graph`(요청별 closure·model 주입·저장하지 않는 일시 job 문맥·guard·서버가 chain 계속 판단).
  legacy graph는 기본 모델 생성 `_chat_model` 추출만 바뀌었다.
- `web_app.py`: 미평가 민감도 REST dict와 민감도 mapper의 순수 부분을 분리(응답 바이트 동일), 검증 결과→REST canonical `_kpg_line_analysis_payload`.
- 실행 전후 revision을 같은 engine guard에서 캡처한다. 변화·식별 불가·chain 단계 간 불일치·검증 시점 변화는 승인하지 않으며 자동 준비·재실행은 없다.
- 상세: `docs/specs/PH4-02B2_AGENT_ARTIFACT_LEDGER.md`. UI·데이터·기존 테스트·검증 해시는 바꾸지 않았다. B3 구현은 시작하지 않았다.

## PH4-02B2 실제 검증

- 신규 61개: **61 passed, 1 warning (12.91s)**. 최초 60개 버전은 60 passed(13.64s), 이후 상태 쓰기 기준 귀속 시험 1개를 추가했다.
- 신규 + 기존 관련 18개 파일: **298 passed, 1 warning (21.65s)**.
- 전체 비브라우저: 변경 전 통합 기준 **2 failed, 707 passed, 103 skipped (51.14s)**, 최종 **2 failed, 768 passed, 103 skipped (62.28s)**.
  실패 2개는 같은 기존 PH4-01 해시 assertion이다. 경고 1개는 설치 Starlette/AnyIO deprecation이다.
- 통합 기준 트리의 격리 사본과 B2에서 기존 REST/Tool/legacy 채팅 33개 출력 동일(SHA256 `7e10e21c…`), 리팩터링한 REST 원문 바이트 동일.
- 격리 복사본 변이 14/14 검출. 모델만 scripted, Tool은 실제 full KPG·MN 계산. 실제 LLM·Chrome·운영 서버는 사용하지 않았다.
- 제한: 신규 테스트별 45초 faulthandler+40초 SIGALRM, 명령 300초; 전체 비브라우저 테스트별 120초, 명령 900~1500초(+kill).
- 로그·probe·변이 결과: `.local/ph4-02b2/20261104-153953/`. `git diff --check`와 범위 외 기존 파일 SHA256 보존을 확인했다.

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
  B2는 v1 Tool wrapper·검증 모듈·`web_app.py` 순수 mapper 분리와 canonical 변환만 변경한다.
- PH4-01의 `docs/validation/kems-console-phase4-code-hashes.json`은 당시 allowlist 변경을 고정한다.
  원래 Stage 1 계약의 실패를 성공으로 바꾸지 않으며 변조 검사는 임시 복사본에서만 수행했다.
  현재 `web_app.py`는 B1(`a3d46cd0…`)·B2(`5a7abf5c…`) 변경으로 그 고정(`6628d110…`)과 달라 기존 실패 2개가 남아 있다. 고정값을 덮어쓰지 않는다.
- PH4-01 실제 LLM 연결·응답 품질은 미검증이다. Stage 3C 외부 LLM 502/503 실패 기록도 유지한다.
  Issue #11의 2026-10-22 사용자 Ollama 성공 기록과는 검증 환경·시점·범위를 구분한다.
- 대화는 페이지 메모리에만 보존된다. 초기화는 새 세션을 시작하며 기존 서버 세션 정리 정책은 바꾸지 않았다.
  HTTP 취소가 서버 Agent 실행 중단을 보장하지 않으므로 중단된 세션을 재사용하지 않는다.

## 다음 작업과 다음 담당자에게

- B3는 별도 요청으로 진행한다. ChatRequest optional 필드·receipt registry·세션/중복 잠금·v1 envelope을 구현하고,
  B2의 job/ledger/`verify_evidence`/`_kpg_line_analysis_payload`를 쓴다. publish 직전 network guard 안에서 현재 revision을 다시 캡처하고
  검증 결과만 registry에 복사한다. graph가 생성한 메시지만 검증에 넘긴다.
- B3 선행 결정: 새로 로드한 KPG에서 AC를 쓰는 첫 실행이 `MODEL_REVISION_CHANGED`인 문제의 안내/준비 정책, Security operation의 감시 ID 매핑,
  provider timeout·graph 예산·단일 worker 명시, MN model identity와 MN job 연결. 실제 LLM smoke로 guard 엄격성(1회 호출·정확한 인자)을 확인한다.
- PH4-01 해시 고정 실패는 새 PH4-02 코드 변경 기록(PH4-01 `after`→현재 bytes)과 체인 테스트 확장으로 해결해야 하며 별도 승인 후 수행한다.
- 서버 계약·분석 문맥/receipt·세션 동시성과 단일 worker/timeout 전제를 연결·검증한 뒤에만 v1을 광고한다. 그 후 PH4-02C에서 UI commit과 Chrome 회귀를 연결한다.
- B2는 base=`feature/kems-multi-grid`인 별도 작업 브랜치·Draft PR로 검토한다. 상위 #14는 완료 처리하지 않으며 B3/C는 자동 착수하지 않는다.
- 통합된 작업 브랜치(DOC-01, PH4-02A·B0·B1)는 이력 보존상 삭제 후보이며 실제 삭제와 PR/main 병합은 별도 승인 후 판단한다.
- 다음 담당자는 `AGENTS.md`와 이 문서를 읽고 실시간 Git 상태와 기존 변경을 확인·보존한 뒤 지정된 범위만 수행한다.
- 시작: `python3 scripts/dev_handoff.py`
- 종료: 이 문서를 갱신한 후 `python3 scripts/dev_handoff.py --write`
- 실제 테스트 로그가 있을 때만 `--test-log PATH`를 추가한다. 로그 요약은 현재 HEAD의 성공을 자동 증명하지 않는다.
