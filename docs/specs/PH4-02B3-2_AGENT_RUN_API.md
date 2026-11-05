# PH4-02B3-2 — K-EMS 분석 문맥 Agent Run과 서버 실행 계약

기준: `feature/kems-multi-grid` `00f6edc` (PR #21 squash) / Issue [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
[PH4-02A 설계](CONSOLE_ANALYSIS_CONTEXT_SPEC.md)와 [B0](PH4-02B0_SERVER_PREFLIGHT.md)·[B1](PH4-02B1_MODEL_NETWORK_GUARD.md)·
[B2](PH4-02B2_AGENT_ARTIFACT_LEDGER.md)·[B3-1](PH4-02B3-1_CONTEXT_REFERENCE_API.md)을 전제로 한다.

## 범위

- 구현: KPG `mode=run`을 요청별 B2 Agent(`AnalysisJob`·`ExecutionLedger`·`create_line_analysis_graph`·`verify_evidence`·
  `_kpg_line_analysis_payload`)에 연결하고, 검증된 결과만 `producer=agent_tool` receipt로 한 번에 등록한다.
  legacy 채팅은 v1과 같은 `(grid, session)` gate를 쓴다. provider timeout·실행 예산·단일 worker 단언을 추가했다.
- 미구현(의도): 몽골·한국 OSM v1은 계속 409. `console_analysis_context_versions`는 광고하지 않는다.
  콘솔 JS·HTML·CSS, 전력계통 알고리즘, 모델·데이터, 기존 Tool/Agent 계약, 기존 검증 기록·해시는 바꾸지 않았다. PH4-02C는 시작하지 않았다.

| 파일 | 변경 |
|---|---|
| `web_app.py` | run 분기와 실행·publish·이력 처리, cached v1 응답의 fail-closed 재생, KPG·MN legacy 채팅 본문을 그대로 옮겨 gate 안에서 실행 |
| `src/ai_ems/analysis_run.py` (신규) | 단일 worker 단언 확인, 실행 예산 검증, provider timeout을 준 모델, Agent 실패 코드 |
| `src/ai_ems/agent/graph.py` | `_chat_model(timeout=)`(run 전용, legacy는 인자 없음), `RunDeadlineExceeded`, `create_line_analysis_graph(deadline, clock)` |
| `src/ai_ems/analysis_registry.py` | `register_all`: 모두 검증·직렬화한 뒤 한 lock에서 삽입(all-or-nothing). `register`는 1개짜리 호출 |
| `src/ai_ems/agent/formatters/analysis_reference.py` | `format_run_answer`와 고정 실패 안내(B3-1 reference 설명은 그대로) |
| `scripts/run_agent.py` | 기본 실행에 `--workers 1`, `WEB_CONCURRENCY`≠1 거부, 자식 프로세스에만 `AI_EMS_SINGLE_WORKER=1` |
| `tests/test_analysis_run_api.py` (신규) | run API·세션·예산·launcher 46개 |
| `docs/validation/kems-console-ph402b32-code-hashes.json` (신규), `tests/test_mn_pipeline.py` | 해시 체인 연장(§8) |

## 1. Agent run 연결 구조

`POST /api/chat`·`POST /api/grids/kr-kpg193/chat`에서 B3-1 검증을 통과한 `mode=run` 요청만 아래 순서로 처리한다.

1. B3-1 검증(형식·grid·operation·사고/감시 선로). KPG가 아니면 409이며 Agent를 만들지 않는다.
2. 단일 worker 단언과 실행 예산을 확인한다(없거나 잘못되면 409 `ANALYSIS_RUN_NOT_READY`).
3. 요청 중복 확인(`RequestTracker.begin`) → 세션 gate(`SessionGates.hold`) 획득. gate는 이력 읽기부터 이력 쓰기까지 유지한다.
4. 요청마다 새 `AnalysisJob`·`ExecutionLedger`를 만들고 그 ledger에 묶인 graph를 만든다. 전역 graph·Tool·prompt는 바꾸지 않는다.
   검증된 job 문맥(사고/감시 ID, 다음 Tool과 고정 인자)은 매 모델 호출에만 붙는 임시 SystemMessage이며 이력에 저장하지 않는다.
5. guard가 ToolNode 전에 호출 1개·허용 Tool·고정 인자·계획 순서를 승인하고, Tool이 실제 계산을 수행한다.
   chain의 다음 단계는 모델이 아니라 ledger가 정한다. N-1이 미평가면 서버가 그래프를 끝내며 민감도를 실행하지 않는다.
6. 이번 실행에서 생성된 메시지만으로 `verify_evidence`를 수행한다. 이전 대화 이력은 증거가 아니다.
7. 승인 후보가 있으면 network guard를 잡고 현재 model revision을 캡처해 `verify_evidence`를 다시 수행한다.
8. 같은 guard 안에서 결과를 기존 REST canonical payload로 변환하고 9. `register_all`로 전부 등록한다(하나라도 실패하면 아무것도 등록하지 않음).
10. 등록된 receipt와 payload로 결정적 답변과 `ui_updates`를 만들고, 이력을 쓴 뒤 gate를 해제한다. 성공·실패 응답(HTTP 200)은 중복 창에 보관한다.

| operation | 실제 Tool 실행(정상 경로) |
|---|---|
| `security` | `line_contingency` 1회(N-1 1회). 감시 ID 인자는 guard가 거부(`MONITORED_LINE_NOT_ALLOWED`) |
| `sensitivity` 자동 | `generator_sensitivity` 1회(대상 선정용 N-1 1회 + 민감도 1회). 다른 감시 ID 지정은 `AUTO_SELECTION_REPLACED` |
| `sensitivity` 명시 | `generator_sensitivity` 1회(민감도 1회). top_n은 5만 허용 |
| `security_then_sensitivity` | security 1회 → 완료 시 sensitivity 1회. N-1 미평가면 security 결과만 `not_evaluated`로 공개 |

## 2. Tool·ledger·artifact 검증

B2의 `verify_evidence`를 그대로 쓴다. 실제 ToolCall ID·이름·정규화 인자, ToolMessage ID·이름·status, 이번 ledger의 실행 기록,
full artifact SHA256, 사고·감시 선로와 operation, 각 실행의 전후 revision, 등록 직전 현재 revision을 대조한다.
새 계산으로 검증을 보충하지 않는다. 1차 검증은 실행 시 revision으로 실패 여부만 가리고(guard 불필요),
승인 후보는 guard 안에서 현재 revision으로 다시 검증한다. guard 획득 실패는 기다리지 않고 `ANALYSIS_BUSY`다.

| 결과 | HTTP | `analysis.status` / `source` | `results`·`ui_updates` | `reason_code` |
|---|---|---|---|---|
| 계획 단계 모두 검증·등록 | 200 | `completed` / `tool_execution` | receipt·update | null |
| 미평가 포함(N-1 비수렴, 과부하 없음, chain 조기 종료) | 200 | `not_evaluated` / `tool_execution` | 실제 실행분만 | `SECURITY_NOT_CONVERGED`, `NO_OVERLOAD` |
| Tool 호출 없이 문장만 | 200 | `not_run` / `none` | [] | `TOOL_NOT_EXECUTED` |
| guard 거부, Tool 오류·입력 거부, chain 불완전, revision 변경, 모델 식별 불가, 엔진 사용 중, 등록 실패 | 200 | `failed` / `none` | [] | 예: `OUTAGE_LINE_MISMATCH`, `MULTIPLE_TOOL_CALLS`, `TOOL_INPUT_REJECTED`, `TOOL_FAILED`, `CHAIN_INCOMPLETE`, `MODEL_REVISION_CHANGED`, `ANALYSIS_BUSY`, `RESULT_NOT_REGISTERED` |
| provider 응답 시간 초과 / 연결·응답 오류 / 예산 초과 / 그 밖의 Agent 오류 | 500 | v1 detail | 없음 | `AGENT_TIMEOUT` / `AGENT_UNAVAILABLE` / `AGENT_BUDGET_EXCEEDED` / `AGENT_FAILED` |

500 detail은 고정 문장이며 provider 원문·주소를 담지 않는다. 500이면 등록·이력 쓰기 없이 요청 ID와 gate를 해제한다(같은 ID 재시도 가능).
PH4-02A §7의 "서버/연결 장애는 기존 KPG 500" 경계를 따른다. 위반·불일치를 성공으로 설명하는 경로는 없다.

## 3. receipt와 ui_updates

- receipt: B3-1 `AnalysisReceipt`와 같고 `producer=agent_tool`, `producer_request_id`=이번 `request_id`, `tool_call_id`=실제 호출 ID다.
  서버 근거로 `job_id`·`execution_id`·`artifact_sha256`·`operation`을 저장한다(응답에는 넣지 않음). run receipt도 reference로 설명할 수 있다.
- `ui_updates`: `{type, grid_id, result_id, result}`만 만든다. `result`는 같은 상태의 `GET /api/security-analysis`·
  `/api/sensitivity-analysis?top_n=5` 응답과 같은 canonical payload다(7개 경우를 REST와 직접 비교). 전체 검증·등록이 끝난 뒤
  한 번에 반환하므로 chain은 둘 다 있거나 둘 다 없다(두 번째 payload 등록 실패 시 첫 결과도 등록하지 않음을 시험).
- `answer`: `format_run_answer`가 receipt와 canonical payload만으로 만든다(같은 입력이면 같은 문장). 모델 문장은 쓰지 않는다.
  실패는 고정 안내와 사유 코드를 쓰고 "기존 지도와 분석 패널은 바뀌지 않습니다"를 덧붙인다. `analysis_context`는 요청을 그대로 echo한다.
- 이력: `[..., Human, (AI tool_calls, artifact 없는 ToolMessage)*, AI(서버 답변)]`. 모델의 텍스트 주장과 artifact는 저장하지 않는다.
  다음 요청의 모델 입력에는 보이지만 증거로 쓰이지 않음을 시험했다.

## 4. 최초 AC와 모델 변경

- 새로 로드한 KPG에서 최초 AC가 상태를 바꾸면 그 실행은 `failed`/`MODEL_REVISION_CHANGED`다. 계산은 실제로 1회 수행되며 숨기거나
  재실행하지 않는다. 이전 revision의 receipt를 만들지 않는다. warm-up·자동 초기화는 없다.
- 안내: "자동으로 다시 계산하지 않았습니다. 같은 분석을 새 요청으로 다시 실행하면 현재 상태에서 계산합니다."
  같은 `request_id` 재전송은 보관된 실패 응답을 그대로 돌려주고(재계산 없음), 새 `request_id`만 새 계산이다.
- 정책은 실행 순서가 아니라 실제 상태 변경 기준이다. 명시 감시 Sensitivity는 상태를 쓰지 않아 새 모델에서도 승인될 수 있다(시험).
- Tool 실행 뒤 등록 전에 모델이 바뀌면 `MODEL_REVISION_CHANGED`. 현재 revision 확인부터 등록까지 network guard를 계속 잡으므로
  그 사이 다른 모델 접근이 끼어들 수 없다(등록 중 다른 스레드가 `NetworkBusy`를 받는 시험).
- HTTP 취소는 동기 graph·JVM 계산을 멈추지 않는다. 엔진 강제 중단 기능은 없으며 lock·ledger·gate는 실행 자체가 끝날 때만 해제된다.

## 5. 세션·중복·legacy 호환

- gate 공유: v1(run·reference)과 legacy 채팅이 같은 `(grid, session)` gate를 쓴다. KPG는 `/api/chat`과 `/api/grids/kr-kpg193/chat`,
  몽골은 `/api/grids/mn-osm-35k/chat`이며 계통끼리는 공유하지 않는다. 몽골 v1은 없지만 PH4-02A §7(이력 read→graph→write의
  grid/session non-blocking lock, legacy·v1 동일 보호)에 따라 몽골 legacy에도 적용했다.
- 겹칠 때만 BUSY: legacy는 `409 {"detail": "SESSION_BUSY"}`(기존 문자열 오류 형식), v1은 `{code: SESSION_BUSY, ...}`.
  순차 legacy 요청·응답·이력은 그대로다. legacy 세션 ID는 기존처럼 앞뒤 공백 제거 후 쓰며 v1의 ID 규칙을 적용하지 않는다.
  예전에는 같은 세션의 동시 legacy 요청이 이력을 경합했고, 이제 두 번째 요청이 409를 받는다(동시 요청에서만 바뀌는 동작).
- 다른 세션은 독립 실행된다(한 세션이 모델 응답을 기다리는 동안 다른 세션 run 완료 시험). 같은 KPG network를 공유하므로
  엔진 호출이 겹치면 대기 대신 `ANALYSIS_BUSY`다(작업 큐 없음).
- 중복: 진행 중 같은 입력 409 `REQUEST_IN_PROGRESS`, 다른 입력 409 `REQUEST_ID_REUSE`. 완료된 200 응답(실패·미실행 포함)은 10분 동안 재사용한다.
- fail-closed 재생: 보관된 응답에 receipt가 있으면 재생 전에 registry에 같은 receipt가 살아 있고 현재 revision과 같은지 확인한다.
  아니면 409 `REQUEST_REPLAY_STALE`("새 request_id로 요청하세요")이며 다시 계산하지 않는다. B3-1 reference 재생에도 같은 확인을 적용했다
  (모델 변경·만료 뒤의 재생만 바뀜). 결과가 없는 실패·미실행 응답은 낡을 결과가 없어 그대로 재생한다.
- 취소: HTTP 요청을 취소해도 gate는 실행이 끝날 때까지 유지되고(그동안 같은 세션 v1·legacy 모두 409), 실행이 끝나면 결과가 등록된다.
  PH4-01 콘솔은 취소·timeout 뒤 새 세션을 쓰므로 늦게 끝난 결과는 새 대화에 반영되지 않고 10분 뒤 만료된다.

## 6. timeout·실행 예산·단일 worker

| 항목 | 값·동작 | 한계 |
|---|---|---|
| provider 전송 | `httpx.Timeout(connect=5, read=30, write=10, pool=5)`를 ChatOllama client에 전달 | read는 응답 조각 사이 간격 한도이며 전체 응답 시간이 아니다. legacy graph는 기존 `Timeout(None)` 그대로 |
| 실행 기한 | run 시작(세션 gate 획득 직후)부터 30초가 지나면 새 모델 호출을 시작하지 않고 500 `AGENT_BUDGET_EXCEEDED` | 진행 중인 모델 호출·Tool·JVM 계산은 중단하지 않는다 |
| graph 단계 | `recursion_limit=12`(chain 정상 경로 6단계). 초과 시 `AGENT_BUDGET_EXCEEDED` | 단계 수 제한이며 시간 제한이 아니다 |
| 설정 | `AI_EMS_RUN_LLM_CONNECT_TIMEOUT`·`AI_EMS_RUN_LLM_READ_TIMEOUT`·`AI_EMS_RUN_DEADLINE_SECONDS`(0 초과 600 이하 유한 수) | 잘못된 값은 기본값으로 대체하지 않고 run을 409 `ANALYSIS_RUN_NOT_READY` |

- 설치 확인: ollama 0.6.2, langchain-ollama 1.1.0, httpx 0.28.1, langgraph 1.2.11. 기본값은 콘솔의 60초 대기 안에 모델 호출 두 번이
  들어가도록 정한 출발점이며 실측 지연이나 서비스 수준이 아니다. 실제 provider는 이번 작업 중 응답하지 않아(§10) 지연을 측정하지 못했다.
- 엔진 실측(이 PC, scripted 모델, 참고값, `run-timing.json`): full KPG 적재 0.07초, N-1 사고 4종 0.016~0.036초(새 모델의 최초 N-1 0.032초),
  명시 민감도 0.012초, revision 캡처 중앙값 0.05초(최대 0.094초), API run 중앙값 security 0.18초·자동 sensitivity 0.19초·chain 0.29초.
  실행 시간의 대부분은 모델 응답이 차지한다.
- 최악의 전체 시간은 상한이 없다: 기한 직전에 시작한 모델 호출의 시간(조각 간격만 제한) + Tool 계산 시간이 더해진다.
  콘솔의 60초 취소는 클라이언트 대기만 끝내며 서버는 실행을 마칠 때까지 gate·ledger·network guard를 유지한다.
- 단일 worker: registry·중복 창·gate·대화 이력이 프로세스 메모리이므로 지원 launcher(`run_agent.sh` → `scripts/run_agent.py`)가
  `--workers 1`을 명시하고, `WEB_CONCURRENCY`(환경변수 또는 `.env`)가 1이 아니면 실행하지 않고 종료 코드 2로 안내한다.
  launcher는 자식 프로세스 환경에만 `AI_EMS_SINGLE_WORKER=1`을 넣는다. 이 값이 없으면 run은 409 `ANALYSIS_RUN_NOT_READY`다.
  worker 수를 자동 감지하지 않는다. 다른 방식으로 띄우며 이 값을 직접 넣으면 운영자가 단일 worker를 보증하는 것이며,
  셸이나 `.env`에 넣지 않는다(`config.py`가 `.env`를 환경변수로 읽기 때문).

## 7. 계통 격리와 capability

KPG만 run을 지원한다. 몽골·한국 OSM의 v1 요청은 409 `ANALYSIS_CONTEXT_UNSUPPORTED`이며 Agent·모델을 호출하지 않고 이력을 만들지 않는다.
`/api/grids`는 `console_analysis_context_versions`를 어떤 계통에도 넣지 않는다(시험). 몽골 model revision·검증형 Agent는 만들지 않았다.

## 8. 해시 체인

새 기록 `docs/validation/kems-console-ph402b32-code-hashes.json`(`kems-ph402b32-code-change-audit-1`, SHA256 `c171b3dc…`)을 만들었다.
- 연결: B3-1 기록 SHA256 `104ecf38…`, Stage 1 계약 `bde2dbd9…`. 기존 기록은 바이트 그대로다.
- `web_app.py`: before `13c0e2cb…`(blob `5354d227`, `base_commit` `00f6edc`) → after `f7bcaa61…`(blob `889ba282`).
- 정확한 edits 9개(−8/+200, `git diff --stat`과 일치). 기능 코드를 확정한 뒤 실제 Git blob과 최종 바이트로 생성하고 정·역방향 적용을 확인했다.

`tests/test_mn_pipeline.py` 변경:
- 신규 `ph402b32_code_changes`, `ph402b31_after_bytes`, `full_web_app_chain_b32`. `ph402_after_bytes`는 복원 경로를 한 단계 늘렸다.
- 기존 B3-1 테스트 4곳은 HEAD 대신 새 기록으로 복원한 B3-1 바이트를 쓰고 단언은 같다. 삭제 11줄은 주석 5, HEAD 읽기 교체 5,
  같은 단언을 복원 바이트에 적용 1이다. skip·완화는 없다. real-asset 체인은 기록 하나를 더 잇는다.
- 신규 26개: HEAD→PH4-01 전체 복원, 단독 before 고정값, B3-1 핀 연결 단독 검사 2, 미기록 바이트 거부 4, 격리 기록 변이 18.

## 9. 검증

모델만 scripted(`BaseChatModel` stub)이고 Tool·registry·도메인 계산은 실제 full KPG(몽골은 실제 데이터)다. 시험에서 설정된 provider를
호출하면 실패하도록 막았다. 실제 LLM 검증은 §10이다. 증거는 `.local/ph4-02b32/20261104-182655/`에 있다.

| 검증 | 결과 |
|---|---|
| 신규 run API 테스트 | 최초 39개 모두 통과(`-qq`라 요약 줄 없는 로그), 보강 후 최종 **46 passed (12.00s)** |
| 신규 + 기존 관련 20개 파일(B1/B2/B3-1·legacy 채팅·launcher 등) | 중간 441 passed → 최종 **443 passed (40.87s)** (B3-1 기준 397 + 신규 46) |
| 해시 체인 관련(`-k "code_record or real_asset or ph402"`) | **101 passed** (B3-1 기준 75 + 신규 26), `test_mn_pipeline.py` 전체 146개 |
| 전체 비브라우저 | 변경 전 `00f6edc` **893 passed, 103 skipped** → 최종 **965 passed, 103 skipped, 1 warning (80.23s)**, 실패 0 |
| legacy parity(통합 기준 격리 사본 vs B3-2, 최종 바이트로 재확인) | 기존 REST/Tool/legacy 채팅 33개 JSON 동일(`7e10e21c…`), 응답 원문 21개 동일, HTTP 순차 legacy 채팅 원문·이력 10개 동일(공백·기호가 있는 legacy 세션 ID 포함) |
| Chrome(실제 FastAPI 채팅 경로, 고정 그래프) | PH4-01 콘솔 채팅 **6 passed (14.04s)**(run `f2755bf05d6a`), 기존 채팅 관련 `test_browser_stale_responses.py -k chat` **4 passed (15.02s)**. 후자는 고정 경로의 기존 스크린샷·로그를 덮어쓰지 않도록 격리 사본에서 실행 |
| 격리 사본 변이 | 구현 안전장치 24/24 검출. 체인 검사 약화 9개 중 최초 6개 → B3-1 핀 연결 단독 시험 추가 후 8개 검출. 나머지 1개(체인 테스트의 HEAD 핀 단언)는 같은 테스트의 전체 체인 검사가 같은 값을 확인하는 중복 |
| 미기록 변경 탐지 | 확장 전(`00f6edc`) 체인 테스트를 새 `web_app.py`에 실행하면 45개 실패 |

구현 변이 예: 등록 직전 revision 재확인 생략, guard 밖 등록, 비원자 등록, 숨은 warm-up, 재생 신선도 확인 생략(run·reference),
단일 worker 확인 생략, gate 밖 실행, legacy gate 제거(KPG·MN), legacy BUSY 형식 변경, 실패 시 요청 ID 미해제, 이력의 artifact·모델 주장 저장,
기한·단계 예산 제거, provider timeout 누락, 예산 범위 검사 약화, provider 원문 노출, 내부 오류의 연결 오류 표시, launcher 3종, 실패 안내 누락.
제한: 테스트별 45초 faulthandler·40초 SIGALRM(전체 회귀 120초), 명령 300~1500초(+kill), Chrome 케이스 210초.
경고 1개는 설치 Starlette/AnyIO deprecation이다.

## 10. 실제 LLM smoke

미실행. 설정된 endpoint의 `/api/tags`가 작업 중 두 번 모두 HTTP 502(`upstream_502`)였다(2026-11-04 18:50, 21:15 KST,
launcher의 `probe_llm`). 주소·인증 값은 기록하지 않았고 Caddy 등 외부 프로세스를 시작하지 않았다.
scripted 시험은 실제 LLM 검증이 아니다. 실제 모델이 고정된 Tool·인자를 지키는지(guard 거부율), 응답 지연과 예산의 적합성은 미확인이다.

## 11. 남은 PH4-02C 선행 조건

- 콘솔: run 응답의 `completed/not_evaluated/not_run/failed`와 사유 코드 표시, 성공 run의 `ui_updates`만 지도·패널에 반영,
  v1 오류(`AGENT_*`, `ANALYSIS_RUN_NOT_READY`, `REQUEST_REPLAY_STALE`, `SESSION_BUSY` 등)와 legacy 문자열 `SESSION_BUSY` 처리.
  Security operation은 감시 ID null, Sensitivity는 top_n=5, 세션 ID는 `[A-Za-z0-9_-]{1,128}`(현재 `console-` + 32 hex는 적합).
- 60초 클라이언트 대기와 서버 실행의 차이: 취소·timeout은 결과 미확정으로 다루고 같은 `request_id`를 자동 재시도하지 않는다.
- 실제 LLM smoke(guard 엄격성, 지연)와 예산 재조정. 배포 환경에서 지원 launcher·단일 worker 확인.
- 위 연결과 Chrome 회귀를 마친 뒤에만 `console_analysis_context_versions: [1]`을 광고한다. 몽골 v1은 별도 model identity가 필요하다.
- 이후 `web_app.py` 변경은 이 기록을 수정하지 않고 그 SHA256과 edits를 잇는 새 기록이 필요하다.
