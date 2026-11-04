# AI-EMS 작업 인수인계 상태

갱신일: 2026-11-04 (Asia/Seoul). 최근 작업과 다음 작업만 유지한다.

## 현재 단계

- Phase 3 완료. Phase 4의 PH4-01 AI 채팅 통합 구현·로컬 검증 완료.
- PH4-02A: 콘솔 분석 문맥·검증 결과 API 계약 설계 제안 작성. 설계 리뷰 대상이며 기능 구현은 아직 하지 않았다.
- 실시간 브랜치·HEAD·커밋 여부·staged/unstaged/untracked 상태는 `dev_handoff.py` 출력으로 확인한다.
- 다음 기능 작업은 [PH4-02 #14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14)의 B(서버)·C(콘솔) 단계다.

## 최근 완료 작업

- `docs/specs/CONSOLE_ANALYSIS_CONTEXT_SPEC.md`에 현재 흐름, optional API, 서버 receipt/artifact 검증,
  snapshot/revision·원자적 UI commit, 동시성·실패·단일 worker 경계, 파일별 책임과 B/C 완료 조건을 설계했다.
- `docs/specs/contracts/console-analysis-context-v1-cases.json`에 요청 예시 4개와 B/C 인수 조건 28개를 작성했다.
  이는 미래 테스트 계약이며 현재 API에 새 문맥 필드나 기능이 구현됐다는 뜻이 아니다.
- 기본 채팅은 유지하고 reference는 서버 저장 결과만 새 계산 없이 설명, run은 실제 Tool 재실행으로 구분한다.
  클라이언트 수치·LLM 자유서술은 검증 근거로 사용하지 않으며 KPG/MN 결과 변환을 분리한다.
- 이전 MAINT-01에서 #11을 Completed 처리하고 Draft PR #2와 PH4-02 #14를 정리했다.

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

## PH4-02A 실제 확인 범위

- 최신 통합 브랜치와 깨끗한 작업 트리를 확인하고 허용된 `feature/ph4-02-console-analysis-context`에서 설계했다.
- 현재 legacy 응답 shape 및 MN의 잘못된 artifact 거부 기준선: **2 passed, 1 warning (1.99s)**.
  로그: `.local/ph4-02a/20261104-scwvjjy0/legacy-contract-baseline.log`. 실제 LLM 호출 없음, 각 테스트 90초 제한.
- JSON 구문·예시·UUID·인수 조건 ID·요구사항 1~8 대응 확인. 미래 v1 동작/Chrome 검증을 통과했다고 기록하지 않는다.
- 설계 문서·최소 테스트 계약·STATE만 변경한다. 기존 코드·테스트·데이터·검증 기록과 로컬 Artifact는 보존한다.
- `git diff --check` 및 범위/해시 보존 점검을 수행한다. 아래 Phase 3·PH4-01 수치는 과거 실행 결과다.

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

- `app.js`, `models.js`, `app.css`, 기존 모델·데이터·Agent 코드와 Stage 1·3C·Phase 2·3 기록을 보존했다.
- 새 `docs/validation/kems-console-phase4-code-hashes.json`이 Phase 3 기록을 이어 현재 allowlist 변경을 고정한다.
  원래 Stage 1 계약의 실패를 성공으로 바꾸지 않으며 변조 검사는 임시 복사본에서만 수행했다.
- PH4-01 실제 LLM 연결·응답 품질은 미검증이다. Stage 3C 외부 LLM 502/503 실패 기록도 유지한다.
  Issue #11의 2026-10-22 사용자 Ollama 성공 기록과는 검증 환경·시점·범위를 구분한다.
- 대화는 페이지 메모리에만 보존된다. 초기화는 새 세션을 시작하며 기존 서버 세션 정리 정책은 바꾸지 않았다.
  HTTP 취소가 서버 Agent 실행 중단을 보장하지 않으므로 중단된 세션을 재사용하지 않는다.

## 다음 작업과 다음 담당자에게

- 설계 리뷰 후 PH4-02B: active model revision·공유 KPG network 잠금·full artifact를 우선 확인하고 서버 계약을 구현한다.
- B의 검증 후 PH4-02C: snapshot/receipt·검증 후 UI commit과 결정론적 Chrome 회귀를 연결한다.
- 실제 LLM smoke는 별도 허용 범위에서 수행한다. timeout 예산·단일 worker 운영 전제도 실제 환경에서 확인해야 한다.
- 작업은 base=`feature/kems-multi-grid`인 Draft PR에서 진행하며 A 단계에서 B/C 기능을 구현하지 않는다.
- 두 완료 브랜치는 이력 보존상 삭제 후보이며 실제 삭제와 PR/main 병합은 별도 승인 후 판단한다.
- 다음 담당자는 `AGENTS.md`와 이 문서를 읽고 실시간 Git 상태와 기존 변경을 확인·보존한 뒤 지정된 범위만 수행한다.
- 시작: `python3 scripts/dev_handoff.py`
- 종료: 이 문서를 갱신한 후 `python3 scripts/dev_handoff.py --write`
- 실제 테스트 로그가 있을 때만 `--test-log PATH`를 추가한다. 로그 요약은 현재 HEAD의 성공을 자동 증명하지 않는다.
