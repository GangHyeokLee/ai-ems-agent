# PH4-02B3-1 — K-EMS 분석 문맥 API와 서버 결과 참조

기준: `feature/kems-multi-grid` `b0fbeb9` (PR #20 squash, PR head `6ba8de7`과 트리 동일) / Issue [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
[PH4-02A 설계](CONSOLE_ANALYSIS_CONTEXT_SPEC.md)와 [B1](PH4-02B1_MODEL_NETWORK_GUARD.md)·[B2](PH4-02B2_AGENT_ARTIFACT_LEDGER.md)·[MAINT-02](PH4-02-MAINT-02_CODE_HASH_CHAIN.md)를 전제로 한다.

## 범위

- 구현: 선택적 `request_id`·`analysis_context`(v1), KPG 수동 Security/Sensitivity receipt, 제한된 프로세스 메모리 registry,
  요청 중복 추적과 v1 세션 gate, 서버 저장 결과만 설명하는 `mode=reference`.
- 미구현(의도): `mode=run`은 409 `ANALYSIS_RUN_NOT_READY`로 거부한다. 몽골·한국 OSM v1은 409로 거부한다.
  `console_analysis_context_versions`는 광고하지 않는다. 콘솔 UI·Agent run·LLM timeout·worker 설정은 바꾸지 않았다.
- B2 job/ledger/검증 코드, 전력계통 알고리즘, 데이터, 기존 검증 기록·해시는 바꾸지 않았다.

| 파일 | 변경 |
|---|---|
| `src/ai_ems/analysis_context.py` | v1 요청·헤더의 순수 검증(`parse_analysis_request`, `parse_receipt_opt_in`)과 `AnalysisApiError`를 끝에 추가. B2 코드는 그대로 |
| `src/ai_ems/analysis_registry.py` (신규) | `ResultRegistry`(256·10분), `RequestTracker`(10분 창), `SessionGates`, `AnalysisReceipt` |
| `src/ai_ems/agent/formatters/analysis_reference.py` (신규) | 저장된 canonical 결과의 결정적 한국어 설명 |
| `web_app.py` | `ChatRequest` 선택 필드, v1 분기, KPG 수동 receipt, MN REST opt-in 거부, reference 처리. legacy 경로 코드는 그대로 |
| `tests/test_analysis_context_api.py` (신규) | API 76개 |
| `docs/validation/kems-console-ph402b31-code-hashes.json` (신규), `tests/test_mn_pipeline.py` | 해시 체인 연장(§6) |

## 1. ChatRequest 호환성

`POST /api/chat`(KPG)과 `POST /api/grids/{grid_id}/chat`을 유지한다. 두 필드가 모두 없거나 null이면 기존 함수 본문이 그대로 실행된다.
알 수 없는 다른 필드를 무시하는 기존 동작도 같다. legacy 응답에는 새 필드를 넣지 않는다(exact-key 테스트).
하나라도 null이 아니면 v1 경로로만 가며 legacy Agent로 되돌아가지 않는다.

| 검사(LLM·Tool·엔진·registry 호출 전) | 결과 |
|---|---|
| 두 필드 중 하나만 있음 | 422 `ANALYSIS_OPT_IN_INCOMPLETE` |
| `request_id`·`view_id`가 소문자 UUID v4가 아님 / `session_id`가 `[A-Za-z0-9_-]{1,128}`이 아님 / `message`가 공백뿐이거나 8000자 초과 | 422 `ANALYSIS_CONTEXT_INVALID` |
| `version`이 정수 1이 아님, `revision`이 bool·문자열·실수이거나 0~2^53−1 밖 | 422 `ANALYSIS_CONTEXT_INVALID` |
| `mode`·`operation` 미지원, 선로 ID 1~128자·앞뒤 공백·제어문자 규칙 위반, 감시=사고, Security operation의 감시 ID | 422 `ANALYSIS_CONTEXT_INVALID` |
| `references`가 두 key가 아님, UUID 아님, run에 ID 있음, reference에 operation이 요구하는 ID 조합이 아님 | 422 `ANALYSIS_CONTEXT_INVALID` |
| 문맥의 추가 필드(`verified`, `result`, `candidates` 등 클라이언트 값) | 422 `ANALYSIS_CONTEXT_INVALID` |
| URL/본문 grid 불일치, `/api/chat?grid_id=`가 KPG 아님 | 409 `GRID_MISMATCH` |
| 알 수 없는 grid / 한국 OSM / 몽골 | 404 `UNKNOWN_GRID` / 409 `ANALYSIS_CONTEXT_UNSUPPORTED`(한국은 기존 안내문) |
| `mode=run` | 409 `ANALYSIS_RUN_NOT_READY` |

v1 오류 detail은 `{code, message, request_id}`이며 입력 원문을 되돌려주지 않는다. `request_id`는 형식이 올바를 때만 echo한다.
legacy 오류의 문자열 detail은 그대로다. 존재하지 않는 선로 ID는 B3-1 검증에서 모델을 읽어 확인하지 않는다.
reference는 registry 조건 불일치로 거부되고, 실제 계산 경로(B3-2)는 도메인 검증을 쓴다.
콘솔(C)은 Security operation에 감시 ID null을, Sensitivity에 top_n=5를 보내야 한다.

## 2. 수동 분석 receipt

기존 `GET /api/security-analysis`·`/api/sensitivity-analysis`에 세 헤더 `X-EMS-Analysis-Context-Version: 1`,
`X-EMS-Session-ID`, `X-EMS-Request-ID`가 **모두** 있을 때만 등록을 시도한다. 헤더 없는 요청은 기존 코드 그대로이며 응답 바이트가 같다(§7).
일부만 있으면 422 `ANALYSIS_OPT_IN_INCOMPLETE`, 형식 오류나 top_n≠5면 422 `ANALYSIS_CONTEXT_INVALID`이며 계산하지 않는다.
몽골 REST는 opt-in을 무시하지 않고 409 `ANALYSIS_CONTEXT_UNSUPPORTED`(부분 헤더 422)로 거부한다. 헤더 없는 몽골 요청은 그대로다.

처리 순서(공유 network guard 안): 실제 revision before → 기존 도메인 계산 1회 → 기존 REST mapper의 canonical payload
→ revision after → 같으면 registry 등록. 응답은 legacy payload에 `analysis_receipt`를 더하고 `Cache-Control: no-store`를 붙인다.
등록하지 못하면 계산 결과는 그대로 두고 `analysis_receipt_error {code, message}`를 붙인다.

| code | 의미 |
|---|---|
| `MODEL_REVISION_CHANGED` | 계산 중 상태가 바뀜(새로 로드한 모델의 최초 AC 등). 이전 revision으로 발급하지 않고 숨겨진 재실행도 없다 |
| `MODEL_IDENTITY_UNAVAILABLE` | 실제 로드 모델을 식별할 수 없음(legacy loader 등) |
| `RESULT_NOT_REGISTERED` | strict canonical JSON 등 등록 형식 검증 실패 |

도메인 예외는 기존 오류 계약(예: 미지 선로 500)을 그대로 따르며 등록하지 않는다. 미평가(`SECURITY_NOT_CONVERGED`, `NO_OVERLOAD`)는
`assessment=not_evaluated`와 `reason_code`로 등록되며 후보·성공 판정이 없다.

`AnalysisReceipt`: `result_id`(서버 UUID v4), `grid_id`, `kind`, `outage_line_id`, 실제 `monitored_line_id`,
`monitored_selection`(`auto|explicit|null`), `top_n`(Security null, Sensitivity 5), `model_revision`, `producer=manual`,
`producer_request_id`, `tool_call_id=null`, `assessment`, `reason_code`, `created_at`·`expires_at`(UTC RFC3339).
`monitored_selection`과 `reason_code`는 A 설계 필드의 최소 확장이다. 자동 감시와 명시 감시를 구분하고 미평가 사유를 남긴다.

## 3. Registry

- 프로세스 메모리 dict + 짧은 lock. 최대 256개(초과 시 가장 오래된 항목부터 제거), TTL 600초(monotonic clock), 재시작 시 비어 있다.
- 저장: receipt + owner `(grid_id, session_id)` + 같은 계산의 canonical payload(strict JSON) + 근거(before/after revision 전체, 요청 조건).
- 등록 검증: owner·사고 ID·revision 64 hex·producer·UUID·assessment/reason 조합, Security monitored/top_n null,
  Sensitivity top_n=5, 자동 미평가만 monitored null. 클라이언트 값·`verified`·임의 해시는 입력 경로가 없다.
- lock은 기록 갱신에만 쓰고, 계산·LLM 중에는 잡지 않는다(계산 중 lookup이 즉시 반환되는 시험).

## 4. reference 모드

순서: 요청 중복 확인 → v1 세션 gate → registry lookup(존재·TTL, owner grid/session, kind, 사고, 감시 조건, top_n=5)
→ 현재 revision 캡처(network guard, non-blocking) → 모든 receipt의 revision과 비교 → 결정적 설명 생성.
operation이 요구하는 reference 조합은 검증 단계에서 확인한다. 자동 감시 요청은 `auto`로 등록된 결과만, 명시 감시 요청은 같은 ID의 `explicit` 결과만 맞는다.

응답: `session_id, grid_id, request_id, analysis_context`(검증한 요청 그대로, 자동 null 유지), `answer`, `ui_updates: []`,
`analysis {status: referenced, source: server_reference, results: [receipt...], reason_code, message}`.
`results`는 원래 receipt(원래 `producer_request_id`)다. `answer`의 수치는 저장된 canonical payload에서만 온다.
미평가는 후보·순위 없이 사유를 적는다. 새 LLM·Tool·물리계산이 없음을 호출 수(도메인·엔진·LLM 0)로 시험했다.
KPG/MN의 Agent 대화 이력은 읽거나 쓰지 않는다.

## 5. 모델 변경·만료·중복·동시성

| 상황 | 결과 |
|---|---|
| 없음·만료·용량 제거·재시작·owner/kind/조건 불일치 | 409 `ANALYSIS_REFERENCE_UNAVAILABLE`, 자동 재계산 없음 |
| 등록 뒤 모델 상태 변경(최초 AC, 운전점 변경) | 409 `ANALYSIS_REFERENCE_STALE` |
| 현재 revision 캡처 중 엔진 사용 중 / 모델 식별 불가 | 409 `ANALYSIS_BUSY` / 503 `MODEL_IDENTITY_UNAVAILABLE` |
| 같은 `(grid, session, request_id)` 진행 중·같은 입력 / 다른 입력 | 409 `REQUEST_IN_PROGRESS` / 409 `REQUEST_ID_REUSE` |
| 완료된 같은 입력 | 같은 응답을 바이트 그대로 재사용(10분 창, 최대 1024개, 성공 응답만 보관) |
| 같은 세션의 다른 v1 요청이 진행 중 | 409 `SESSION_BUSY` |

오류로 끝난 요청은 ID를 보관하지 않아 같은 ID로 다시 시도할 수 있다. 입력 fingerprint는 grid·session·message·문맥의 정규화 JSON이다.
KPG와 MN은 key·gate·이력이 분리된다.

lock 순서는 v1 세션 gate → 짧은 registry lock(해제) → network guard다. 수동 receipt는 network guard 안에서 짧은 registry lock을 잡는다.
어떤 경로도 registry lock이나 network guard를 잡은 채 세션 gate를 기다리지 않고, 모든 획득은 non-blocking 또는 짧은 구간이다.
HTTP 취소는 동기 worker를 멈추지 않으며 gate·진행 중 표시는 그 요청의 `finally`에서만 해제된다. 그동안 재전송은 위 409를 받는다.
단일 worker·프로세스 메모리 전제이며 재시작 뒤 영구 exactly-once를 약속하지 않는다.

Legacy 세션과의 관계: B3-1의 v1 경로는 Agent 이력을 읽거나 쓰지 않으므로 legacy 이력과 경합하지 않는다.
legacy 채팅에는 v1 gate를 적용하지 않았다(동작 불변). B3-2 run은 이력 read→graph→write를 하므로 A §7에 따라 legacy와 같은 gate를 공유해야 한다.
이는 동시 요청에서만 legacy 동작을 바꾸므로(두 번째 요청 409) B3-2에서 브라우저 회귀와 함께 결정·검증해야 한다.

## 6. 해시 체인

새 기록 `docs/validation/kems-console-ph402b31-code-hashes.json`(`kems-ph402b31-code-change-audit-1`)을 만들었다.
- 연결: PH4-02 기록 SHA256 `6b8c8482…`, Stage 1 계약 `bde2dbd9…`.
- `web_app.py`: before `5a7abf5c…`(blob `546b19a3`, `base_commit` `b0fbeb9`) → after `13c0e2cb…`.
- 정확한 edits 13개(−5/+241, `git diff --stat`과 일치)를 담는다.

기능 코드를 확정한 뒤 실제 Git blob과 최종 바이트로 생성했고(`build_ph402b31_record.py`), 정·역방향 적용을 검증했다.

`tests/test_mn_pipeline.py` 변경:
- 신규 `ph402b31_code_changes`, `ph402b31_versions`, `undo_edits`, `ph402_after_bytes`, `full_web_app_chain`.
- 기존 PH4-02 테스트 4곳이 HEAD 대신 새 기록으로 복원한 PH4-02 바이트를 쓴다. PH4-01 `api-chat.js` 과거 검증도 그 바이트에서 같은 단언으로 유지된다.
- real-asset 체인은 기록 하나를 더 잇는다. 기존 단언 삭제·skip·완화는 없다(삭제 10줄 = 주석 5 + HEAD 읽기 교체 5).
- 신규 22개: HEAD→PH4-01 전체 복원, 단독 before 고정값, 미기록 바이트 거부 3, 격리 기록 변이 17.

## 7. 검증

모델만 대체(호출 시 실패하는 stub)했고 registry·도메인 계산은 실제 full KPG·MN이다. 실제 LLM·Chrome은 사용하지 않았다.
증거는 `.local/ph4-02b31/20261104-174059/`에 있다.

| 검증 | 결과 |
|---|---|
| 신규 API 테스트 | 최초·최종 **76 passed** (최종 12.42s) |
| 신규 + 기존 관련 19개 파일 | **397 passed (최종 32.92s)** |
| 해시 체인 관련(`-k "code_record or real_asset or ph402"`) | **75 passed** (기존 53 + 신규 22) |
| 전체 비브라우저 | 변경 전 `b0fbeb9` **795 passed, 103 skipped** → 최종 **893 passed, 103 skipped, 1 warning (74.11s)**, 실패 0 |
| legacy parity(통합 기준 격리 사본 vs B3-1) | 기존 REST/Tool/legacy 채팅 33개 JSON 동일(`7e10e21c…`, B2 때와 같은 값), 응답 원문 21개(catalog·REST·MN·KR·오류) 바이트·상태·`Cache-Control` 동일 |
| 격리 사본 변이 | 구현 안전장치 16/16 검출. 체인 검사 약화 6개 중 5개 검출, 나머지 1개(before_blob 연결)는 다른 검사가 같은 값을 확인하는 중복 검사 |
| 미기록 변경 탐지 | 확장 전 체인 테스트를 새 `web_app.py`에 실행하면 23개 실패(HEAD≠PH4-02 after) |

체인 약화 최초 실행에서 B3-1 before 고정값 제거가 검출되지 않아 단독 사용 시험 1개를 추가했다. 이후 검출됐다.
제한: 테스트별 45초 faulthandler·40초 SIGALRM(전체 120초), 명령 180~900초(+kill). 경고 1개는 설치 Starlette/AnyIO deprecation이다.

## 8. 미해결 사항과 B3-2 선행 조건

- run: B2 job/graph/ledger/`verify_evidence`를 검증된 v1 문맥에 연결한다. publish 직전 현재 revision을 재확인하고 `producer=agent_tool` receipt만 등록하며,
  응답 문장은 검증 결과로 결정적으로 만든다. 실제 LLM smoke·provider timeout·graph 예산을 함께 검증한다.
- legacy 채팅과 v1 세션 gate 공유 여부(§5)를 결정하고 Chrome 회귀로 확인해야 한다.
- 새로 로드한 KPG에서 첫 수동 opt-in은 `MODEL_REVISION_CHANGED`다. 콘솔 안내 또는 별도로 검증된 준비 정책이 필요하다.
- 몽골 model identity가 없어 MN v1·receipt는 거부 상태다. 단일 worker 전제를 실행 설정에서 보장하기 전에는 v1을 광고하지 않는다.
- 이후 `web_app.py` 변경은 이 기록을 수정하지 않고 그 SHA256과 edits를 잇는 새 기록이 필요하다.
