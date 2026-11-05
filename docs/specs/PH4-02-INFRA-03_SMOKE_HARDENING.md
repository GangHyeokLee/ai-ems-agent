# PH4-02-INFRA-03 — 실제 LLM Smoke Test 도구 안정화

기준: 통합 브랜치 `feature/kems-multi-grid` `ad2c1f8`. 작업 브랜치 `feature/ph4-02-infra03-smoke-hardening`.
변경 파일: `scripts/smoke_test_llm.py`, `tests/test_smoke_test_llm.py`(기존 12개 그대로, 신규 추가), 본 문서, `docs/dev/STATE.md`.
서버(`web_app.py`)·`src/`·콘솔·데이터·검증 해시·launcher·Caddy/Tailscale/Ollama 설정은 변경하지 않았다. 실제 LLM은 실행하지 않았다.

## 1. 확인한 결함 (수정 전 `ad2c1f8`에서 재현)

재현 스크립트는 모든 실제 소켓·HTTP 연결을 막은 상태로 실행했다(증거 `pre-fix-defects.log`).

| ID | 결함 | 재현 결과 |
|---|---|---|
| F | `run_smoke_test(mode="mock")`(mock 인자 생략)과 알 수 없는 mode가 검증 없이 실제 경로로 진행 | 연결 점검 실행, 보고서 mode=`execute`, 실제 `/api/chat` POST 4회 시도(차단됨) |
| C | Tool Calling 점검 실패(선로 불일치는 WARN)여도 KPG Run 진행 | 점검 FAIL 뒤 Run POST 2회, 실제 모델 팩토리 2회 호출 |
| E | `/api/tags`에 지정 모델이 없어도 시나리오 1 PASS | 목록에 다른 모델만 있어도 PASS |
| D | 시나리오 8이 항상 PASS | Run 실패·guard 거부에도 "상태 안정 유지(즉시 완료)" PASS |
| G | guard가 거부한 ToolMessage(status=error)를 실제 실행으로 판정, Evidence는 status·source·결과 존재만 확인 | 거부된 호출에 시나리오 4 PASS |
| H | 원래 없던 `_analysis_run_model`이 복구 후 남고, 설정 중 예외 시 network·`AI_EMS_SINGLE_WORKER`가 복구되지 않음 | 둘 다 재현 |
| A | 연결 점검은 smoke가 읽은 `root/.env`+환경, 실제 Agent는 `os.environ`(ai_ems.config 가져올 때의 `.env` 적재)을 사용 | `root`가 패키지 root와 다르면 점검만 Bearer를 보냄 |
| S | Tool Calling 점검 예외 문자열을 그대로 보고서에 기록 | provider 오류에 담긴 토큰이 detail에 기록됨 |

A의 범위: 표준 배치(같은 저장소의 `.env`)에서는 `ai_ems.config`의 `load_dotenv(override=False)`가 같은 값을 `os.environ`에 넣으므로
PM이 예시한 "`.env`에만 토큰" 상황이 그대로 재현되지는 않았다(격리 사본에서 점검·Agent 모두 Bearer 확인).
일치가 가져오기 부작용과 같은 root에 기대고 있었고, root가 다르면 실제로 어긋났다.

추가로 확인한 위험: ollama-python 0.6.2 클라이언트는 명시 Authorization이 없으면 `OLLAMA_API_KEY`를 Bearer로 붙인다.
Bearer 없이 이 변수만 있으면 Agent 요청에만 다른 인증이 실리고 `/api/tags` 점검은 이를 보내지 않는다.

## 2. 수정 내용

- **모드(F)**: `mode`가 유일한 결정 값이다. `mock`은 호환 인자로, 주어지면 `mode == "mock"`과 같아야 하며 bool만 허용한다.
  알 수 없는 mode·불일치·`mock_bad_line`의 mock 외 사용은 설정 읽기 전에 `ValueError`다. Mock은 연결 점검을 하지 않고
  보고서에 `[MOCK]`·`simulated`·`source`와 비고(실제 LLM 결과 아님, 로컬 PyPowSyBl 실제 계산)를 남긴다.
- **인증 일치(A)**: `resolve_configuration`이 `auth_env`(AUTH_KEYS 3개)를 단일 출처로 만든다. 두 점검은 이것을 명시 사용하고,
  `isolated_app_environment(auth_env=...)`가 실행 범위에서만 `os.environ`의 세 키를 같은 값으로 맞춘 뒤 원래대로(없던 키는 삭제) 되돌린다.
  `ai_ems.config`를 먼저 가져와 이후의 `.env` 적재가 범위 값을 바꾸지 못하게 한다. Run 직전 `agent_auth_matches`가 실제
  `_chat_model` 클라이언트의 인증 헤더와 점검 헤더를 메모리에서 비교하고, 다르거나 비교할 수 없으면 Run을 시작하지 않는다.
  `OLLAMA_API_KEY`는 `AI_EMS_LLM_API_KEY` 없이 설정되면 실행 전에 거부한다. 서버·Agent 공통 코드는 바꾸지 않았다.
- **URL·인증 사전 검증(B)**: 기존 HTTPS origin 검증(기본 URL 없음, `http://`·경로·쿼리·사용자 정보 거부)과
  `get_llm_client_kwargs`의 충돌·불완전 검사를 유지했다. Caddy 경로 요구사항은 README·`deploy/ollama-proxy` 기준이다:
  `GET /api/tags`·`POST /api/chat`만 허용, Bearer(64자리 소문자 16진수) 누락·불일치는 업스트림 전 401, 다른 경로 403.
  모든 HTTPS가 Bearer를 요구한다고 가정하지 않는다. 401/403·502·timeout은 시나리오 1 FAIL이며 추론·계산으로 가지 않는다.
- **모델 존재(E)**: `/api/tags`의 `name`·`model` 값과 정확히 일치해야 한다. 태그 없는 이름만 Ollama 기본 태그 `:latest`로 본다.
  유사 이름·대소문자 차이는 인정하지 않는다. 응답에 `models` 목록이 없으면 FAIL이다.
- **Tool Calling fail-closed(C)**: 호출이 정확히 1개, 이름 `line_contingency`, 인자가 서버 guard의 `normalize_arguments`를 통과할 때만 PASS다.
  미호출·이름 불일치·선로 불일치·추가 인자·다중 호출·모델 오류·timeout은 FAIL이고, 시나리오 3~10은 "차단" SKIP이며 KPG를 적재하지 않는다.
  모델·provider 원문은 기록하지 않는다(예외 클래스·HTTP 상태만).
- **Revision(D)**: Run 1 전후로 실제 모델 revision을 잡아 판정한다.
  - 변경 + `failed`/`MODEL_REVISION_CHANGED` + 공개·등록 없음 → PASS(fail-closed).
  - 변경 없음 + 공개 + receipt revision이 현재와 같고 등록 수 일치 → PASS.
  - 변경인데 결과 수용·등록, 변경인데 다른 실패 사유, 변경 없음인데 `MODEL_REVISION_CHANGED` → FAIL.
  - revision 식별 불가 → FAIL(판정 근거 부족).
  - Tool 미실행 → SKIP. 어느 경우에도 PASS가 아니다.
- **Ledger/Evidence(G)**: 시나리오 4는 이번 요청 이력의 AI 호출과 같은 ID·이름의 `status=success` ToolMessage가 있어야 PASS다.
  - **시나리오 6**: 공개된 Run에서 아래를 대조한다.
    - 응답 status·source, ui_updates↔receipt.
    - 이번 요청 tool_call ID↔성공 ToolMessage↔receipt.
    - Registry evidence의 키·operation·execution_id(UUID)·artifact_sha256(64 hex 형식)·job 일치, Registry payload↔ui_update.
  - **시나리오 7**: producer `agent_tool`, 요청 ID, 사고 선로, 현재 revision, Registry receipt 동일성과 세션 소유를 확인한다.
  - **확인하지 않는 것**(PASS 근거가 아니며 `metrics.limitations`에 기록):
    - ExecutionLedger 원본.
    - artifact 원문 재해시.
    - verify_evidence 내부 issues.
- **재요청**: Run 1이 계약대로(Tool 실행·선로 준수·시나리오 8 PASS) 끝났을 때만 Run 2를 보낸다. Run 2는 `completed`, revision 유지,
  receipt revision 일치여야 PASS다. 공개 Run이 없으면 시나리오 6·7은 SKIP(PASS 아님)이다.
- **종합 판정**: execute·mock은 10개 모두 PASS일 때만 PASS다. FAIL이 하나라도 있으면 FAIL, 그 밖(WARN·SKIP)은 WARN이다(SKIP이 PASS가 되지 않음).
  시나리오 10은 고정 30초 대신 실제 `run_budget()`의 기한·read timeout을 기록한다.
- **격리 복구(H)**: 모든 변경을 `try` 안에서 하고, 원래 없던 속성·환경변수는 삭제, `None`은 `None`으로 되돌린다. TestClient는 닫는다(lifespan 미실행).
- **비밀정보(S)**: 보고서 문자열·metrics에서 설정된 인증 값을 `[보호됨]`으로 가린다. Base URL은 origin만 표시한다(사용자 정보·경로·쿼리 생략).
  `main()`은 설정을 읽은 root를 Run에도 그대로 넘긴다.

코드 구조: 기존 `run_smoke_test` 한 함수에 결함이 고르게 퍼져 있어, 시나리오 번호·이름·보고서 필드·CLI를 유지한 채
판정 부분을 `judge_tool_execution`·`judge_initial_revision`·`verify_published_evidence`·`verify_receipts`로 분리했다(단위 시험 대상).
보고서 JSON에는 `notes`가 추가됐다.

## 3. 검증 (증거: worktree `.local/ph4-02-infra03/20261105-180858/`)

- 기준선: 수정 전 기존 smoke **12 passed**(`baseline-smoke.log`). 결함 재현 `pre-fix-defects.log`, 수정 후 `post-fix-defects.log`.
- 신규 시험: 시험 함수 26개(매개변수 포함 58개). 기존 12개를 포함한 smoke 파일 **70 passed**. 신규 시험은 기존 시험의 assertion을 바꾸지 않는다.
  - **가짜 Caddy**: 메모리 안에서 README 계약(허용 경로 2개, Bearer 401)을 흉내 낸다.
    실제 ChatOllama·`_chat_model`·FastAPI·Registry·KPG 계산을 네트워크 없이 실행한다.
    `.env`에만 있는 Bearer가 Tool 점검과 Run 1·2의 모든 `/api/chat`에 실린 것을 확인했다.
  - Dry Run·Mock·잘못된 인자·URL·인증 거부 시험은 실제 소켓·HTTP 시도가 0건이어야 통과한다.
- 변이(격리 사본): 안전장치 21개를 각각 제거했을 때 **21/21 검출**(`mutation.log`, `mutation-followup.log`).
  - 첫 실행의 미검출 2건은 시험을 보강했다.
    - provider 원문 복사: 가림 처리가 비밀값은 막았다. 원문 자체가 출력되지 않는지 단언을 추가했다.
    - SKIP이 PASS로 이어지는 종합 판정: 직접 시험을 추가했다.
- CLI(연결 차단 래퍼, 시도 0건): Dry Run URL 미설정 WARN, HTTPS 설정 DRY_RUN, Mock **10/10 PASS**, URL 없는 `--execute` exit 2.
- 전체 비브라우저: **1055 passed, 132 skipped, 1 warning (87.42s)** = 직전 997/132 + 신규 58.
- `git diff --check` 통과. 변경·증거 파일에서 실제 `.env` 인증 값 일치 0건(값은 출력하지 않음).

## 4. 남은 위험과 실제 실행 준비

- 실제 LLM은 실행하지 않았다. 가짜 Caddy는 README 계약의 재현이며 실제 Tailscale·Caddy·Ollama 응답 형식·지연을 대신하지 않는다.
- 실제 실행 전 확인:
  - **연결**: 사용자 승인, `AI_EMS_LLM_BASE_URL`=Tailscale HTTPS 8443 origin, `AI_EMS_LLM_API_KEY`=Caddy 토큰(`.env` 또는 프로세스).
  - **사전 상태**: `OLLAMA_API_KEY` 미설정, 대상 모델이 `/api/tags`에 있음, Caddy·Ollama 기동.
  - **Dry Run**: `python scripts/smoke_test_llm.py`가 DRY_RUN인지 확인한다.
  - **실행 범위**: 실행은 Tool 점검 1회 + Run 최대 2회의 `/api/chat`과 격리 KPG 계산만 한다.
- 운영 서버 참고(이번 범위에서 변경하지 않음):
  - 운영 Agent도 AI_EMS 인증이 없으면 `OLLAMA_API_KEY`를 Bearer로 보낸다.
  - `ai_ems.config`의 `.env` 적재는 기본 interpolation을 쓰고, launcher·smoke는 `interpolate=False`를 쓴다.
  - Caddy 토큰(16진수)에는 영향이 없다.
  - ollama 클라이언트는 redirect를 따른다. 점검은 따르지 않는다.
- 기존 INFRA-02 시험 일부(`test_configuration_resolution` 등)는 저장소 root의 `.env`를 읽으므로 `.env`가 있는 checkout에서는 결과가 달라질 수 있다(이번에 수정하지 않음).
- 보고서는 Base URL origin(호스트 이름)을 표시한다. 공유 시 내부 호스트 이름 노출 여부를 확인한다.
