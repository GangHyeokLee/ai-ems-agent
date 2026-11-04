# PH4-02B2 — KPG Agent full artifact와 요청별 실행 ledger

기준: `feature/kems-multi-grid` `6a4df5e` (PR #19 squash, B1 작업 커밋 `a498835`과 트리 동일) / Issue [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
[PH4-02A 설계](CONSOLE_ANALYSIS_CONTEXT_SPEC.md), [B0](PH4-02B0_SERVER_PREFLIGHT.md), [B1](PH4-02B1_MODEL_NETWORK_GUARD.md)을 전제로 한다.

## 범위와 계약

- 구현: KPG Security/Sensitivity의 v1 전용 Tool wrapper(full artifact), 요청별 job·실행 ledger·실행 전 guard,
  AIMessage↔ToolMessage↔ledger↔artifact↔model revision 서버 검증, 검증 결과의 기존 REST canonical 변환.
- 진입점은 API에 연결하지 않았다. 아직 ChatRequest가 없으므로 테스트용 검증 job으로만 동작을 확인한다.
- 바꾸지 않은 것: `ChatRequest`·API 응답 계약, receipt registry, 분석 문맥 API, grid capability(v1 미광고),
  콘솔 JS/HTML/CSS, 전력계통 알고리즘, 기존 테스트·모델·데이터·검증 해시, legacy Tool 반환·schema·오류.
- 몽골(MN)은 B2 job에 연결하지 않았다. MN에는 아직 model revision이 없으므로 `AnalysisJob`은 KPG만 받는다
  (`UNSUPPORTED_GRID`). MN의 기존 artifact 검증·미지원 요청 차단 정책은 그대로다.

| 파일 | 변경 | 이유 |
|---|---|---|
| `src/ai_ems/analysis_context.py` (신규) | `AnalysisJob`, `ExecutionLedger`, `verify_evidence`, 실패/거부 코드 | A 설계 §9가 정한 위치. 요청 모델·registry·세션 잠금은 넣지 않음 |
| `src/ai_ems/agent/tools.py` | legacy 축약을 순수 `summarize_line_contingency`로 추출, `create_line_analysis_tools` 추가 | 같은 계산의 축약 content와 full artifact 동시 생성. legacy Tool 본문은 helper 호출만 바뀜 |
| `src/ai_ems/agent/graph.py` | 기본 모델 생성 `_chat_model` 추출, `line_analysis_instruction`, `create_line_analysis_graph` | 요청별 closure graph·guard·model 주입. legacy graph 동작 동일 |
| `web_app.py` | REST의 미평가 민감도 dict를 `_sensitivity_not_evaluated_for_ui`로, mapper의 순수 부분을 `_sensitivity_payload_for_ui`로 분리, `_kpg_line_analysis_payload` 추가 | 기존 REST mapper 재사용. REST 응답 바이트 동일 |
| `tests/test_kpg_agent_artifact_ledger.py` (신규) | 61개 | 아래 §6 |

REST 엔드포인트의 호출 구조(`web_app.run_line_contingency`, `web_app.select_most_severe_violated_line`)는 그대로 둔다.
B1의 잠금 테스트가 이 이름을 monkeypatch하므로, 자동 감시 판정은 REST와 같은 조건을 v1 wrapper에 두고 parity 테스트로 일치를 강제했다.

## 1. Full artifact

v1 Tool은 legacy와 **같은 이름·설명·모델 노출 schema**를 가진 별도 객체다(`content_and_artifact`).
`InjectedToolCallId`를 keyword-only로 받아 schema에서 제외된다. 따라서 실제 ToolCall로만 호출되며 `.invoke(args)`는 오류다.
legacy `create_agent_tools`의 Tool은 교체하지 않았다.

한 번의 실행 순서:

1. `ledger.begin(tool_call_id, name, args)`: guard가 인가한 정확한 call/인자인지 확인한다. 아니면 계산 없이 `UnauthorizedExecution`.
2. 공유 `network_access(network)` 안에서 revision before → 도메인 계산 **1회** → (민감도) 후보 발전기 `voltage_level_id` 복사 → revision after.
   before 식별이 실패하면 계산하지 않는다(`MODEL_IDENTITY_UNAVAILABLE`).
3. guard 밖에서 artifact를 strict canonical JSON(NaN·암묵 변환 금지)으로 봉인하고 SHA256을 ledger에 기록한다.
4. ToolMessage: `content`=legacy 축약(LLM용), `artifact`=봉인본의 분리 사본. 예외 시 ledger는 `failed`와 사유 코드를 기록한다.

| artifact 필드 | 내용 |
|---|---|
| `schema` | `ai-ems-line-analysis-artifact-v1` |
| `job_id`, `execution_id` | 서버 UUID4 (job 생성, 실행 시작 시) |
| `grid_id`, `operation`, `kind`, `tool_name`, `tool_call_id`, `arguments` | 검증된 job과 인가된 call의 정규화 인자 |
| `model_revision.before/after` | 같은 engine guard 안에서 캡처한 B1 `ModelRevision` 전체 (after 식별 실패 시 null) |
| `outcome` | `assessment`(completed/not_evaluated), `reason_code`, 실제 사고·감시 ID, 감시 선택(auto/explicit), top_n |
| `result` | Security: `run_line_contingency` 원본 전체. Sensitivity: `selection{mode,status,security,selected}`, `ranking` 원본, `generator_metadata` |

- Security content는 `summarize_line_contingency(같은 원본)`이다. 대표 위반 1개 축약은 유지하고 전체 목록은 artifact에만 있다.
- Sensitivity는 top_n=5다. 자동 감시는 REST와 같은 판정(사고 후 비수렴 → `SECURITY_NOT_CONVERGED`, 과부하 선로 없음 → `NO_OVERLOAD`)을
  쓰며, 이때 `ranking=null`·후보 0개다. legacy Tool의 `ValueError` 계약은 유지된다.
- 자동 감시 민감도는 REST처럼 같은 Tool 트랜잭션 안에서 N-1을 한 번 수행해 대상을 고른다. 따라서 chain은 N-1 2회(체인 Security 1 + 자동 선택 1),
  민감도 1회다. projection·검증·canonical 변환에는 추가 계산이 없다(엔진 호출 수로 시험).
- canonical: `_kpg_line_analysis_payload(result)` → Security `_security_result_for_ui(원본)`, Sensitivity `_sensitivity_payload_for_ui(ranking, 봉인 metadata)`
  또는 `_sensitivity_not_evaluated_for_ui`. 엔진 호출·live model 재조회가 없다. B2에서는 어떤 API도 이를 응답하지 않는다.

## 2. 요청별 job·ledger·guard

`AnalysisJob(grid_id, operation, outage_line_id, monitored_line_id=None)`은 frozen이며 `job_id`를 서버가 만든다.
operation은 `security|sensitivity|security_then_sensitivity`, 선로 ID는 1~128자·앞뒤 공백/제어문자 없음이다.
감시 ID는 민감도가 있는 operation에서만 허용하고 사고 ID와 같을 수 없다. null은 도메인 자동 선택이다.

`ExecutionLedger(job)`은 요청마다 새로 만들며 graph 하나에만 연결된다(`claim`, 재사용 시 `LEDGER_ALREADY_CLAIMED`).
기록 상태는 `authorized → started → succeeded|failed`다. 자체 잠금은 기록 갱신에만 짧게 쓰고 엔진·모델 대기 중에는 잡지 않는다.

guard(`authorize`)는 ToolNode **전에** AI turn 하나를 검사한다. 하나라도 위반하면 그 turn의 모든 call을 실행하지 않고 job 전체를 거부한다.

| 코드 | 거부 조건 |
|---|---|
| `MULTIPLE_TOOL_CALLS` | 한 turn에 분석 Tool이 2개 이상 |
| `TOOL_NOT_ALLOWED` | job operation 밖의 Tool, KPG 조회 Tool, MN Tool 등 |
| `INVALID_TOOL_CALL_ID` | call ID 없음/중복 |
| `UNEXPECTED_TOOL_SEQUENCE` | 계획 순서 위반, 완료 후 추가 호출 |
| `UNEXPECTED_ARGUMENT` | 허용 외 인자(예: grid_id, verified, path) |
| `OUTAGE_LINE_MISMATCH` | 고정 사고 ID와 다름(앞뒤 공백 포함) |
| `MONITORED_LINE_NOT_ALLOWED` | Security에 감시 ID |
| `AUTO_SELECTION_REPLACED` / `MONITORED_LINE_MISMATCH` | 자동 감시를 모델이 지정 / 명시 감시와 다름·누락 |
| `TOP_N_NOT_ALLOWED` | top_n이 정수 5가 아님(`"5"`, `True` 포함) |

`create_line_analysis_graph(network, ledger, model=None)`는 요청별 Tool closure로 graph를 새로 compile한다.
전역 `agent_graph`·`direct_agent_tools`·SYSTEM_PROMPT는 바꾸지 않는다. agent 호출 때만 검증된 job 필드로 만든 일시 SystemMessage를
덧붙이며 graph state에는 저장하지 않는다. guard 거부는 실행 없는 `status=error` ToolMessage를 남기고 종료한다.
ToolNode 뒤에는 **서버가** 다음 단계를 정한다. chain은 N-1이 성공·revision 안정·완료(수렴)일 때만 모델에 Sensitivity를 요청하고,
그 외(비수렴, 실패, 마지막 단계 완료)에는 바로 종료한다. 실패 후 재시도·숨겨진 재실행은 없다.

## 3. 서버 검증 (`verify_evidence`)

입력은 해당 graph 실행이 **생성한 메시지만**과 호출자가 지금 캡처한 `current_revision`이다.
메시지 문장, `verified` 값, 과거 이력, 클라이언트 값은 근거가 아니다.

1. AIMessage call ID·name·인자(같은 정규화) ↔ 2. ToolMessage ID·name·status ↔ 3. ledger 실행 기록(인가 인자·성공 여부)
↔ 4. ToolMessage artifact SHA256 = ledger 봉인 SHA256, 봉인 artifact의 job/grid/operation/execution/call 식별
↔ 5. outcome의 실제 사고·감시·top_n = job 조건, 실행 전후 revision 동일, chain 전체 revision 동일, `current_revision` 동일.

| 결과 | 조건 |
|---|---|
| `completed` | 계획 단계 전부 검증·평가 완료 |
| `not_evaluated` | 검증된 실제 미평가(비수렴, `NO_OVERLOAD`). chain은 검증된 비수렴 N-1에서만 조기 종료 가능 |
| `not_run` (`TOOL_NOT_EXECUTED`) | Tool 호출 없이 모델 문장만 있음 |
| `failed` | 거부·불일치·실패·revision 변화·미완료 chain(`CHAIN_INCOMPLETE`). **results는 항상 빈 값**(부분 공개 없음) |

주요 불일치 코드: `UNPAIRED_TOOL_MESSAGE`, `TOOL_NAME_MISMATCH`, `TOOL_ARGUMENT_MISMATCH`, `TOOL_MESSAGE_ERROR`,
`ARTIFACT_MISMATCH`, `UNLEDGERED_TOOL_MESSAGE`, `LEDGER_WITHOUT_TOOL_MESSAGE`, `TOOL_NOT_EXECUTED`, `DUPLICATE_TOOL_MESSAGE`,
`UNEXPECTED_MESSAGE`(현재 실행 외 메시지 혼입). 실행 실패 코드: `ANALYSIS_BUSY`, `MODEL_IDENTITY_UNAVAILABLE`,
`TOOL_INPUT_REJECTED`(도메인 입력 오류), `ARTIFACT_INVALID`, `TOOL_FAILED`.

## 4. 모델 revision 정책

- 실행 전후 revision을 같은 engine guard 안에서 캡처하고, 다르면 `MODEL_REVISION_CHANGED`로 승인하지 않는다.
  판정은 호출 순서가 아니라 실제 상태 쓰기를 따른다. 새로 로드한 full KPG에서 base AC를 수행하는 첫 Security·자동 감시 Sensitivity는
  최초 AC가 상태를 써서 거부된다. 계산은 1회뿐이며 자동 준비·재실행은 없다. 사용자가 명시적으로 새 실행을 요청하면 그 시점의 안정
  revision으로 승인된다. AC를 쓰지 않는 명시 감시 Sensitivity는 첫 실행도 초기 revision(`initial_state=true`)으로 승인된다(모두 시험으로 확인).
- 첫 AC가 chain의 N-1에서 일어나면 Sensitivity를 요청하지 않고 끝낸다.
- chain 단계 사이에 다른 구성요소가 모델을 바꾸거나, 실행 뒤 검증 시점에 모델이 바뀌면 `MODEL_REVISION_CHANGED`다.
- 식별 불가는 fail-closed다. 실행 전 식별 실패는 계산하지 않고, 실행 후 실패는 결과를 귀속하지 않는다.

## 5. 기존 경로 호환성

- legacy 축약은 같은 코드의 함수 추출이다. 통합 기준 트리의 격리 사본과 B2 트리에서 **기존 REST/Tool/legacy 채팅 33개**
  (요약·지도·조회 Tool, Security 6종, Sensitivity 6종(미평가 2종 포함), PF preview, 발전기 API, legacy Tool 출력·오류, 13개 Tool schema,
  chat fixture 4종의 ui_updates)를 같은 순서로 수집했다. key 정렬 JSON SHA256 `7e10e21c26463ec2a5d6aaaa7ac69da967d7de992686c9f2c4937ab146b1af22`로 동일했다.
- 리팩터링한 미평가 민감도 REST 응답 2종과 자동 감시 완료 응답은 key 순서를 포함한 응답 바이트가 같았다.
- legacy Tool의 `response_format=content`·schema, 자동 감시 과부하 없음의 `ValueError`, `/api/chat` 응답 키는 그대로다.

## 6. 검증

실제 LLM·Chrome·운영 서버는 사용하지 않았다. 모델만 scripted이며 Tool은 실제 full KPG(`data/KPG193_ver2_0_powsybl_full.mat`) 계산을 수행한다.
명시적 주입은 세 가지뿐이다: 민감도 엔진 예외 1종(부분 chain), 실제 계산 앞의 지연 대기(잠금 시험), 호출 수를 세는 spy.
비수렴·과부하 없음·다중/전압 위반은 모두 실제 도메인 결과이며 MN 혼입 시험도 실제 MN Tool 결과를 쓴다.
신규 테스트는 각 45초 faulthandler 종료와 40초 SIGALRM, 전체 명령 300초+kill 10초 제한을 둔다.
전체 비브라우저는 테스트별 120초, 명령 900~1500초+kill 15초 제한이다. 로그는 `.local/ph4-02b2/20261104-153953/`에 있다.

실제 KPG 시험 사례(같은 모델 탐색 결과): `LINE-58-59` 위반 설비 12개, `LINE-80-82` 36개(과부하+LOW_VOLTAGE),
`LINE-1-2` 과부하 없음, `LINE-28-80` 사고 후 `MAX_ITERATION_REACHED`, `LINE-16-28` 자동 감시 `LINE-16-22`.

| 검증 | 결과 |
|---|---|
| 신규 `tests/test_kpg_agent_artifact_ledger.py` | 최초 **60 passed (13.64s)**. 상태 쓰기 기준 귀속 시험 1개 추가 후 최종 **61 passed, 1 warning (12.91s)** |
| 신규 + 기존 관련 Agent/API/MN 채팅/콘솔 18개 파일 | **298 passed, 1 warning (21.65s)** (신규 61 + 기존 237) |
| 변경 전 전체 비브라우저(통합 기준) | **2 failed, 707 passed, 103 skipped, 1 warning (51.14s)** |
| 변경 후 전체 비브라우저 | 중간 2 failed/767 passed/103 skipped(61.76s), 최종 **2 failed, 768 passed, 103 skipped, 1 warning (62.28s)**. 실패 2개는 §7의 기존 실패와 같은 assertion |
| legacy parity (격리 기준 사본 vs B2) | 33개 동일, 미평가 REST 원문 바이트 동일 |
| 격리 복사본 변이 시험(신규 60개 시점) | **14/14 검출**. digest·사고 ID·revision 변화·현재 revision·call 수·error status 검사 제거, 이중 계산, chain 계속 판단을 모델에 위임, 인가 생략, ledger 재사용, 이력 혼입 허용, legacy 축약 projection, metadata 누락 |

신규 테스트 범위: 같은 계산의 축약 content+full artifact와 엔진 호출 수(LF 1·SA 1, projection 0), 전체 위반 count=목록(12·36개),
REST와 canonical 동일(7종), chain 순서·LLM 대기 중 엔진 잠금 없음·일시 문맥 미저장, 비수렴 조기 종료, 부분 chain 미공개(3종),
최초 AC·상태 쓰기 기준 귀속·단계 사이/사후 모델 변경 거부, 식별 불가 fail-closed, guard 거부 18종(도메인·엔진 0회), 위조·불일치·재사용 메시지 13종,
Tool 없는 성공 문장, 미인가 직접 실행·재실행 거부, 도메인 오류·`ANALYSIS_BUSY`, ledger 잠금 미보유·REST 409, 요청별 ledger 격리·병렬,
KPG/MN 혼입(실제 MN Tool 결과 포함), legacy schema·반환·오류. 경고 1개는 설치 Starlette의 AnyIO deprecation이다.

## 7. 통합 기준의 기존 실패: PH4-01 `web_app.py` 해시 고정

`tests/test_mn_pipeline.py::test_phase4_code_record_pins_only_console_chat_asset`와
`::test_real_asset_checks_are_deterministic_and_keep_model_roles`는 B2 변경 **전** 통합 기준에서 이미 실패한다.
`docs/validation/kems-console-phase4-code-hashes.json`의 PH4-01 `after`(`6628d110…`)는 B0(`d6ecf79`)까지의 `web_app.py`와 같고,
B1(`6a4df5e`)이 `web_app.py`를 바꿔(`a3d46cd0…`) 불일치가 생겼다. B0 트리의 격리 사본에서는 같은 고정 테스트가 통과했다.
B1의 회귀 범위에 `test_mn_pipeline.py`가 없었다. B2는 `web_app.py`를 다시 바꾸며(`5a7abf5c…`) 같은 assertion으로 실패한다.

B2는 과거 기록·해시·테스트를 덮어쓰거나 완화하지 않았다. 해결에는 PH4-01 `after`를 `before`로 잇는 새 PH4-02 코드 변경 기록과
그 체인을 검사하는 테스트 확장이 필요하며, 검증 해시 영역이므로 별도 승인 작업으로 남긴다.

## 8. 미해결 위험과 B3 선행 조건

- 준비 상태: 새로 로드한 KPG에서 AC를 쓰는 첫 v1 실행은 `MODEL_REVISION_CHANGED`다. B3/C는 사용자 안내 또는 별도로 검증된 준비 정책을 정해야 한다.
  초기 revision의 명시 감시 Sensitivity는 AC 이후의 어떤 Security와도 revision이 같을 수 없으므로, C의 Security↔Sensitivity 대응 규칙에서 거부될 수 있다.
- B3 publish는 network guard 안에서 현재 revision을 다시 캡처해 `current_revision`으로 넘기고, 검증 결과만 registry에 복사해야 한다.
  ledger는 요청 수명 동안의 프로세스 메모리이며 TTL·용량·owner·재시작 정책은 registry의 몫이다.
- 호출자는 graph가 생성한 메시지만 검증에 넘겨야 한다. 세션 이력 저장 시 guard 거부 ToolMessage·일시 문맥 처리 정책이 필요하다.
- 실제 LLM 동작은 미검증이다. 일시 SystemMessage 위치, 한 turn 1개 호출·정확한 인자(`top_n` 문자열 불가)·계획 후 종료 정책이
  실제 모델에서 과도한 거부를 만드는지 별도 smoke가 필요하다. 응답 문장은 B3가 검증 결과로 결정적으로 만든다.
- Security job은 감시 ID를 받지 않는다(receipt의 security monitored=null). 콘솔 문맥의 감시 ID를 Security operation에 전달하지 않도록 B3가 매핑해야 한다.
- MN v1: model revision·요청별 guard·artifact ledger 연결이 없다. 연결 전에는 MN job을 만들 수 없다.
- 세션 잠금·중복 요청 창·LLM provider timeout·graph 예산·단일 worker 명시는 미구현이다(B3).
- §7의 해시 기록 문제는 별도 승인 후 처리해야 한다. v1 capability는 계속 광고하지 않는다.

## 9. 재현 명령

```bash
run_dir=.local/ph4-02b2/20261104-153953
# deadlines.py: 테스트별 40초 SIGALRM, deadlines_wide.py: AI_EMS_TEST_DEADLINE초(stdlib signal 임시 plugin)
timeout --kill-after=10s 300s env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$run_dir:src:." \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines tests/test_kpg_agent_artifact_ledger.py
timeout --kill-after=15s 900s env PYTHONDONTWRITEBYTECODE=1 AI_EMS_TEST_DEADLINE=120 PYTHONPATH="$run_dir:src:." \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines_wide tests
```

main/통합 브랜치 병합, 기존 브랜치 삭제, Issue #14 완료 처리는 하지 않는다. B3는 별도 요청으로 진행한다.
