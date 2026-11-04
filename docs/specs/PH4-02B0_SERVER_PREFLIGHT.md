# PH4-02B0 — 콘솔 분석 문맥 서버 선행 검증

상위 Issue: [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14). 조사·실험: 2026-11-04 (Asia/Seoul).
기준: 통합 브랜치 `df09b3431b2111e22ae8529c8ec93052fd349ffe`.
[PH4-02A 설계](CONSOLE_ANALYSIS_CONTEXT_SPEC.md), [B/C 인수 계약](contracts/console-analysis-context-v1-cases.json)을 전제로 한다.

**B0는 구현 가능성 검증이다. v1 API·receipt registry·운영 잠금·콘솔 기능은 구현하지 않았다.**

## 1. 판정과 진입 조건

| 항목 | 판정 | 실제 근거 | B에서 필요한 조건 |
|---|---|---|---|
| KPG `model_revision` | **CONDITIONAL** | 실제 full KPG 복사본과 IEEE14에서 로드 객체 snapshot 반복 안정성·변경 감지 확인. 최초 AC와 재로드 시 snapshot 차이도 확인 | 로드 시점 provenance, 준비 상태 정책, 유효 설정 고정, 모든 접근 잠금, exporter 범위 검증 |
| 공유 network 잠금 | **CONDITIONAL** | 격리 network에서 RLock 경합 거부·같은 스레드 재진입·결과 동등성 확인. 실제 ToolNode의 호출자/실행 스레드 경계 확인 | REST·legacy Tool·v1 Tool·조회·mapper를 동일 객체의 같은 guard로 연결하고 통합 동시성 검증 |
| 같은 Tool 실행의 full artifact | **GO (방식 검증)** | 실제 Security 계산 1회, 전체 위반 9개 유지, content/artifact 분리, 기존 canonical mapper로 추가 계산 없이 변환 | v1 전용 wrapper·공용 순수 요약 helper·현재 job ledger를 실제 코드에 연결하고 회귀 통과 |

GO는 현재 서비스에 기능이 구현됐다는 뜻이 아니다. 세 조건 중 하나라도 실제 경로에서 충족되지 않으면
PH4-02B는 `console_analysis_context_versions: [1]`을 광고하지 않는다. 한국 OSM은 계속 비활성화한다.
B0 이후 B/C 구현을 자동으로 시작하지 않는다.

## 2. 시작 상태와 변경 범위

- `git fetch origin` 후 PR [#16](https://github.com/GangHyeokLee/ai-ems-agent/pull/16)과
  [#17](https://github.com/GangHyeokLee/ai-ems-agent/pull/17)의 merged 상태를 확인했다.
  통합 커밋은 각각 `281f8c9`와 `df09b34`이며 설계·JSON 계약이 통합 트리에 실제로 있다.
  squash 반영이므로 원 작업 커밋의 ancestry만으로 미반영이라고 판단하지 않는다.
- 깨끗한 최신 통합 브랜치에서 허용된 `feature/ph4-02b0-server-preflight`를 생성했다.
- 변경 대상: 이 보고서, A 설계의 B0 근거 링크/주의 사항, `docs/dev/STATE.md`.
- 검증 코드는 `/tmp/ai-ems-ph402b0-29fugkqm/`에만 작성했고 사본·로그는 Git 제외된
  `.local/ph4-02b0/20261104-140557/`에 보관한다. 영구 실행 코드를 추가하지 않는다.
- KPG 원본은 읽어 임시 사본을 만들었다. 이미 실행 중인 서버·공유 network에는 접속하지 않았다.
  tiny 모델의 정격/운전점/연결 상태 변경은 새로 만든 메모리 객체에서만 수행했다.

## 3. B0-1: 실제 로드 모델의 식별

### 3.1 로딩과 설정의 실제 경로

| 위치 | 확인한 사실 |
|---|---|
| `src/ai_ems/config.py:38` | `CASE_FILE`은 신뢰된 서버 설정 `AI_EMS_CASE_FILE` 또는 저장소 full MAT 파일. HTTP 입력으로 선택하지 않음 |
| `web_app.py:79` | 모듈 import 시 한 번 load한 객체를 graph와 direct Tool factory에 함께 전달. 현재 provenance 저장은 없음 |
| `src/ai_ems/network.py:19` | basename이 `KPG193_ver2_0_powsybl_full.mat`일 때 전용 어댑터, 나머지는 일반 importer. 이름만 같다고 동일 모델은 아님 |
| `src/ai_ems/utils/kpg_powsybl_adapter.py:227` | MAT을 읽고 임시 AC-only MAT 생성→base voltage 보존 import→원본 dcline 기반 VSC/HVDC 추가. 원본 파일은 수정하지 않음 |
| 같은 파일 `:67`, `:159` | dcline/dclinecost를 임시 입력에서 비우고 나머지 MPC 보존. VSC·무효전력 한계·HVDC의 실제 변환 코드도 모델 정체성에 영향 |
| `src/ai_ems/network.py:14` | 공용 LF는 `distributed_slack=False`; 나머지 일반/provider 파라미터는 설치 환경 기본값을 포함 |
| `src/ai_ems/tools/security_tools.py:27` | 같은 공용 LF로 base AC 후 Security. AC가 전압·조류·slack 정보 등을 network에 기록 |
| `src/ai_ems/tools/sensitivity_tools.py:68` | 선로 Sensitivity는 `analysis.run(network)`의 자체 기본 설정 사용. 현재 환경의 LF distributed_slack은 **True** |

실제 configured 입력은 저장소 full MAT이며 존재한다. 임시 사본의 SHA256은
`13593043ad8ea766a3860513dca1aa9cc592289774dcc24e38f7d13654865b81`이다.
이 파일을 전용 어댑터로 로드한 실험 객체는 193 buses / 385 lines / **201 generators / 2 HVDC**였다.
다른 `*_pypowsybl.mat` 및 과거 205-generator 테스트 수치를 이 full 모델의 현재 실험으로 혼용하지 않는다.

Security와 Sensitivity의 현재 설정 차이는 의도/타당성을 이번 작업에서 바꾸지 않았다.
B에서 하나의 `distributed_slack=False` 또는 짧은 설정 이름만 hash하면 서로 다른 계산을 같은 조건으로 오인할 수 있다.
**기존 수치는 유지하고 연산별 유효 파라미터 전체를 식별**해야 한다.

### 3.2 후보: 로드 provenance + 전체 working-state snapshot + 유효 계산 설정

최소 후보는 다음과 같다. 아래는 식별 방식이며 API 구현이 아니다.

```text
model_revision = SHA256(canonical JSON {
  revision_schema,
  load_provenance: {exact_source_bytes_sha256, importer_parameters, adapter_revision},
  working_variant_id,
  canonical_full_XIIDM_of_actual_loaded_object,
  effective_settings: {LF, Security, Sensitivity, provider names/versions, conversion/mapping revision}
})
```

- 출처는 **로드할 바로 그 bytes**에서 얻는다. B에서 provenance를 포함하는 로더를 추가하고
  안정된 입력 사본의 digest와 실제 변환 후 객체 snapshot을 함께 캡처해야 한다.
  현재 디스크 파일의 hash만 나중에 읽어 기존 global 객체의 출처라고 소급 인정하지 않는다.
  파일 교체/재로드는 새로운 로드 세대로 취급하고 기존 receipt를 무효화한다.
- 실제 snapshot은 `Network.save_to_string('XIIDM', parameters=...)`와 표준 XML canonicalization을 사용했다.
  sorted=true, branch-state=true, only-main-cc=false, only-selected-operational-limits-groups=false,
  throw-exception-if-extension-not-found=true다. state 값·전기정수·제어값·정격·연결·extension을 임의로 제거하지 않는다.
  deprecated `dump_to_string` 대신 `save_to_string`을 사용한다.
- enum은 이름/종류, mapping은 key 정렬, LF의 provider_parameters 및 Security increased/modified thresholds,
  Sensitivity thresholds·각 LF 설정까지 재귀적으로 직렬화한다. provider 기본값을 호출마다 조용히 바꾸지 않는다.
  B는 이 설정 객체를 계산에도 전달해 hash와 실제 실행이 같은 설정을 쓰도록 해야 한다.
- package `pypowsybl=1.16.1`, bundled `powsybl-core=7.3.0`, `OpenLoadFlow=2.3.0`과 importer/mapper 버전을 포함한다.
  설치본/source가 달라지면 구 revision을 이어 쓰지 않는다. 비밀 설정·인증 헤더는 fingerprint 입력/로그에 넣지 않는다.
- snapshot은 동일 network guard 안에서 생성한다. hash/순수 JSON 직렬화는 이미 복사된 값으로 guard 밖에서 할 수 있다.
  hash schema나 exporter가 바뀌면 새 revision schema로 분리한다.

실험 digest는 settings+실제 XML의 후보 증명이다. 아직 로더에서 provenance를 원자적으로 결합한 최종
`model_revision` 구현은 아니다. `observations.json`의 digest를 배포용 기준 해시로 사용하지 않는다.

### 3.3 안정성·변경 감지·비용과 한계

- IEEE14 동일 factory 입력과 동일 객체 반복에서 같은 digest였다.
  load P0, line X, generator target P, 연결 상태, 정격, LF distributed_slack, working variant 변경을 각각 감지했다.
  full KPG 복사본에서는 HVDC target P 변경도 감지했다.
- full KPG의 **동일 로드 객체**에서는 반복 snapshot이 같았다. 최초 base AC 후에는 달라졌다.
  명시적으로 base AC를 수행한 준비 상태에서는 재차 AC, N-1, 명시 감시 Sensitivity 후 snapshot이 같았다.
  이것은 해당 모델/설정/사고에서의 실험이며 모든 사고·제어 모드의 불변성을 증명하지 않는다.
- 독립적으로 같은 MAT을 재로드하면 root `caseDate`가 import 시각으로 달랐으며 digest도 달랐다.
  그 외 XML 차이가 없는 것을 비교했다. 이 보수적 후보는 **재시작/재로드 간 동일 revision을 보장하지 않는다**.
  process-local receipt는 재시작 시 폐기되므로 안전한 추가 무효화가 가능하지만,
  `caseDate`를 실제 운전 시각이라고 표현하거나 임의로 지워서 안정성을 가장하지 않는다.
- full KPG snapshot+설정 직렬화+SHA256 7회: min **43.81 ms**, median **45.73 ms**, max **59.90 ms**.
  앞선 예비 XML 크기는 load 직후 약 563 KB, AC 후 약 613 KB다. 이 환경에서 측정한 비용이며 SLA가 아니다.
  매 polling/LLM turn에 만들지 말고 모델 준비/실제 실행 전후/참조 검사에 적용한다. 256개 결과마다 XML을 중복 저장할 필요는 없다.
- **준비 상태는 아직 미해결 B 진입 조건**이다. 첫 AC가 바꾼 실제 상태를 무조건 같은 revision으로 통과시키면 안 된다.
  명시적인 모델 readiness 준비 단계를 선택한다면 legacy 조회/수치 호환성을 먼저 검증하고 준비 계산을 별도 기록한다.
  준비가 없으면 첫 실행의 전후 차이를 fail-closed로 거부하며 숨겨진 재계산·결과 누락 보충은 하지 않는다.
- 전체 working XML도 비활성 variant·비직렬화 extension·native 내부 캐시의 완전한 증명이 아니다.
  B에서 KPG 사용 element/extension 목록·export 실패 정책·round-trip/설정 coverage를 검증해야 한다.
  지원되지 않는 상태를 만났으면 receipt를 발급하지 않는다. 엔진 상태를 fingerprint에서 광범위하게 제외하는 대안은 별도 검증 전 사용하지 않는다.

## 4. B0-2: 공유 객체 접근 목록과 잠금

### 4.1 접근 경로

| 경로/위치 | web_app 공유 KPG 객체인가? | 접근/격리와 필요한 경계 |
|---|---|---|
| `/api/security-analysis`, `web_app.py:462` | 예 | validation get_lines→base AC 쓰기→SA→전체 raw 복사/mapper까지 한 경계 |
| `/api/sensitivity-analysis`, `:480` | 예 | 자동 N-1→target 선택→Sensitivity→generator metadata 변환을 함께 보호. 명시 감시도 보호 |
| `/api/network-summary`, `:352` | 예 | 여러 get_*를 하나의 일관된 snapshot으로 보호 |
| `/api/network-map`, `:362` 및 grid map(KPG) | 예 | voltage/line/transformer/generator 조회를 한 경계로 보호 |
| `create_agent_tools`의 network_summary/line_list/line_detail/generator_list, `agent/tools.py:51` | 예 | 각 Tool의 전체 읽기 결과 복사까지 보호. list_lines/generators에는 계산 결과 p/q도 노출됨 |
| 같은 factory의 line_contingency `:71` | 예 | REST와 동일 객체. Tool 실행 스레드에서 전체 연산을 보호 |
| 같은 factory의 generator_sensitivity `:142` | 예 | 자동 선택 N-1 포함 전체 연산 및 metadata snapshot 보호 |
| `_sensitivity_result_for_ui`, `web_app.py:861` | 예 | 계산이 끝난 뒤라도 global generator metadata를 읽음. guard 또는 실행 때 복사한 metadata 필요 |
| `_security_result_for_ui`, `_agent_security_result_for_ui` | 아니오 | raw dict의 순수 변환. raw가 안전하게 복사됐다면 native lock 밖에서 가능 |
| `/api/generator-contingency-analysis` 및 generator comparison/screening/risk/response Tool | 아니오 | case_path 기반 새 base/post/security network를 로드. 공유 KPG 객체 lock 대상이 아님. source bytes/provenance는 별도 문제 |
| balanced_redispatch/contingency_response Tool | 아니오 | 파일 기반 private network. 기존 전기 계산을 global로 바꾸지 않음 |
| standalone `run_ac_load_flow`, `network.py:39` | 호출자가 결정 | web_app에 독립 AC PF endpoint/Tool은 없음. shared 객체로 호출하는 새 경로는 반드시 owner guard 사용 |
| KPG PF preview `kpg_pf_preview.py:459` | 아니오(계산), 예(map 검증) | 자체 cache lock+별도 모델의 AC. endpoint가 부르는 network_map만 공유 경계 필요 |
| Physics API `api/app.py:8`, `api/routes.py:42` | 다른 객체 | `create_app`마다 새 객체, 서버 모듈과 공유하지 않음. 그 객체의 Security/Sensitivity 간에는 동일 문제가 있으므로 per-object guard 필요 |
| MN Security/Sensitivity `mongolia_security.py:404` | 아니오 | 검증 cache Lock+매 분석별 독립 network. KPG lock과 합치지 않음 |
| CLI `app.py:main` 및 probe scripts | 다른 프로세스/객체 | 웹 global lock으로 보호되는 대상이 아님. 같은 입력 파일이 같은 native 객체를 뜻하지 않음 |

현재 web_app/shared Tool 경계에는 공통 Lock/RLock이 없다. 단순 get_*도 쓰기 중이면 일관성을 보장할 수 없다.
Security/Sensitivity의 native 내부 격리를 일반 thread safety로 가정하지 않는다.
`control_tools.py:generate_redispatch_candidates`의 비변경 설명만 믿어서도 안 된다.
그 함수가 받은 객체에 `run_line_contingency`를 호출하면 base AC 결과 쓰기가 발생한다.

FastAPI 설치본 `routing.py:344`는 동기 endpoint를 `run_in_threadpool`로 실행한다.
LangGraph `ToolNode._func`는 별도 executor의 `map`으로 Tool 호출을 실행한다.
단일 Uvicorn worker라도 REST/Tool이 서로 다른 스레드에서 겹칠 수 있다.

### 4.2 권장 경계와 교착 방지

1. B에서 공유 객체 owner가 RLock 한 개를 소유하고, web_app REST와 **두 Tool factory(graph/direct)**에 같은 guard를 전달한다.
   `network.py` 또는 작은 전용 owner 모듈에 guard/helper를 두되 임의 network 전체에 전역 lock 하나를 적용하지 않는다.
   Physics router의 다른 객체에는 그 객체의 별도 guard가 필요하다.
2. 실제 읽기/계산/결과 복사/모델 검사에만 non-blocking으로 획득한다. 실패는 `ANALYSIS_BUSY`로 구분해 REST 409로 연결한다.
   v1에서 ToolNode가 busy 예외를 일반 content로 삼켜 성공 처리하지 않도록 typed 실패·ledger 검증을 연결한다.
   legacy 순차 성공의 dict/수치/오류 계약은 유지하고 경합 응답만 명시적으로 테스트한다.
3. 같은 스레드의 N-1→선택→Sensitivity→mapper 중첩은 RLock으로 허용한다.
   **chat handler가 lock을 쥔 채 graph.invoke/ToolNode executor를 기다리면 안 된다.** RLock은 다른 스레드에서 재진입할 수 없다.
   실제 ToolNode 그래프로 확인했으며 호출자 소유 상태에서는 child Tool이 BUSY, 해제 후에는 ACQUIRED였다.
4. LLM HTTP 대기·프롬프트/이력 처리·다른 private network 계산·응답 전송 중 engine lock을 잡지 않는다.
   job별 metadata/artifact는 guard 안에서 복사하고 LLM이 도는 동안 global network를 뒤늦게 읽어 이어 붙이지 않는다.
5. registry의 짧은 lock을 보유한 채 engine에 진입하지 않는다. engine 결과를 immutable 값으로 복사하고 해제한 뒤 registry/ledger에 publish한다.
   세션 lock은 graph 실행 owner가 유지하되 Tool 스레드가 그 세션 lock을 다시 획득하지 않는다.
   B에서 이 순서와 snapshot→publish 사이 모델 세대 변경 검사를 명시적으로 검증한다.
6. HTTP 취소/timeout으로 engine lock을 조기 해제하지 않는다. 실제 실행 worker의 `finally`만 해제한다.
   client를 기다리는 coroutine만 취소되더라도 native 작업이 계속될 수 있다.

격리 실험은 한 worker가 작은 network에 실제 N-1을 수행하는 동안 다른 스레드가 non-blocking 획득을 거부하는지 확인했다.
중첩 진입/해제 후 읽기와 별도 순차 계산의 전체 결과 동등성도 확인했다. 잠금 없이 같은 native 객체에
동시 쓰기를 시도하지 않았으며, live REST/legacy/v1 통합 경합은 아직 미구현·미검증이다.

## 5. B0-3: 같은 실행의 전체 artifact

### 5.1 현재 raw와 누락

`security_tools.py:27`의 raw에는 contingency/outage, base_converged, pre/post status,
raw limit_violations 및 수, equipment별 요약 전체와 수, pre 전체 목록,
violation_comparison(new/resolved/remaining), monitored_branches(base/post 흐름)가 있다.

`agent/tools.py:71`은 여기에서 대표 equipment 하나와 monitored branch 하나를 선택한다.
주요 누락은 전체 post/pre raw 목록, 전체 equipment 목록, raw record 수·세부 속성/limit_name/side 등이다.
**violation_comparison은 compact content에도 남아 있으므로 모든 추가 위반 정보가 content에서 사라진다고 말하지 않는다.**
하지만 이 비교용 부분 구조로 전체 post 상태를 재구성하는 것은 허용하지 않는다.
`web_app.py:821`의 legacy mapper는 대표 위반만 변환하므로 count와 목록 길이가 달라질 수 있다.
v1은 raw 전체를 받는 기존 순수 `_security_result_for_ui`(`:783`)를 사용해야 한다.

### 5.2 설치본에서 확인한 구조와 최소 변경

- 설치본: langchain-core 1.6.3 / langgraph 1.2.11 / langgraph-prebuilt 1.1.0 / langchain-ollama 1.1.0.
- `@tool(response_format='content_and_artifact')`는 `(compact_content, full_artifact)`를 반환한다.
  ToolCall 경로에서 ToolMessage의 `content`, `artifact`, `tool_call_id`, `name`, `status`를 실제 확인했다.
- `InjectedToolCallId`는 모델용 `tool_call_schema`에서 제외된다. 이를 쓰는 v1 wrapper는 반드시
  실제 ToolCall로 호출해야 하며 `.invoke(args_dict)`는 오류다. legacy 직접 호출을 이 wrapper로 교체하지 않는다.
- 권장 B 변경: 기존 compact dict 생성 부분을 **raw→compact 순수 helper**로 추출한다.
  legacy는 기존 content-only Tool/직접 반환을 유지한다. v1 전용 factory/wrapper만 raw 계산 1회→helper→artifact를 반환한다.
  격리 실험은 원본 함수를 바꾸지 않기 위해 process-local monkeypatch로 raw를 관찰했다.
  이 관찰용 monkeypatch를 프로덕션 구현 방식으로 사용하지 않는다.
- 일괄적으로 모든 KPG Tool을 artifact 모드로 바꾸는 대안은 비권장이다.
  직접 호출 결과, InjectedToolCallId, workflow의 content JSON을 읽는 결정적 formatter와 기존 `/` 변환에 영향이 있다.
  v1용 line/security/sensitivity 경계만 먼저 추가하고 발전기·Redispatch Tool은 legacy에 둔다.

검증용 artifact 모양(물리값을 생략한 구조):

```text
ToolMessage {
  name: line_contingency, tool_call_id: tool-1, status: success,
  content: JSON(compact legacy summary),
  artifact: {execution_id: server-exec-1, raw: complete result of that call}
}
server-only ledger[tool-1] = {name, validated args, artifact digest}
```

실제 IEEE14에 시험용 1 MVA 정격을 설정하고 `L2-4-1`을 사고로 지정했다.
**domain 호출 1회 / post CONVERGED / raw 9 records·9 equipment / canonical 9 violations**였다.
대표 violation은 하나이며 raw에 전체가 남았다. mapper 호출 뒤에도 domain 호출 수는 1이었다.
legacy `.invoke(args)`와 같은 compact dict인 것을 동일 raw 재생으로 확인했다(추가 물리계산 없음).
오류 ToolMessage의 status=error·artifact=None도 확인했다.

### 5.3 artifact 자체를 증명으로 신뢰하지 않기

서버 guard는 ToolNode 실행 전에 job의 허용 call ID/name/인자를 등록하고, wrapper는
실제 함수 실행 시도/완료와 server execution ID·정확한 raw digest·모델 revision을 기록해야 한다.
실제 결과가 없는 call은 ledger에 완료로 남기지 않는다. caller가 넣은 수치·verified flag·과거 messages는 근거가 아니다.

최종 변환은 현재 job의 AI 호출 ID/name/검증 인자, ToolMessage ID/name/status,
ledger의 실행/결과 digest·model revision을 모두 대조한다. artifact는 deep copy하여 이후 변조를 거부한다.
격리 verifier는 다른 call ID, 다른 name, error status, 변조 raw, 현재 ledger에 없는 message를 거부했다.
이는 proof의 verifier이며 프로덕션 ledger 구현/인증 체계를 의미하지 않는다.
B에서 MN의 `_mongolia_analysis_evidence`와 같은 현재-job 검증·예외 경계를 실제 연결해야 한다.
Sensitivity canonical mapper는 global generator metadata를 읽으므로 같은 실행의 metadata snapshot까지 연결해야 한다.

## 6. B0-4: Timeout과 worker

| 실행 경계 | 현재 사실 | B에서 필요한 처리/한계 |
|---|---|---|
| 콘솔 HTTP | `api-chat.js:13` 60,000 ms, AbortController와 세션 교체 | client 대기를 끝낼 뿐 서버 graph/JVM 종료를 뜻하지 않음 |
| FastAPI sync endpoint | worker thread에서 graph.invoke를 동기 대기 | disconnect가 Python/native worker를 강제 종료한다고 가정하지 않음 |
| KPG/MN ChatOllama | graph factory는 temperature/model/base_url 및 인증 kwargs만 전달 | 명시적 provider timeout 없음. 임의 인증 설정/원문을 보고서에 수집하지 않음 |
| 설치 Ollama/httpx | 실제 생성한 미호출 client의 timeout은 `Timeout(timeout=None)` | B에서 명시 connect/read/write/pool budget, 실패 전달 검증. read timeout은 전체 wall time이 아님 |
| LangGraph | 현재 invoke에 step/recursion/총 시간 예산 없음 | 설치 기본 recursion_limit **10007**, step_timeout=None. 명시 recursion_limit=3의 GraphRecursionError는 실험 확인. 단계 제한은 wall time/JVM kill이 아님 |
| PyPowSyBl | 현재 LF/SA/Sensitivity는 synchronous native call | 사용 경로에 timeout/cancel 인자 없음. 완료 전 lock/ledger 해제 금지. 엔진 반복 한계는 시간 한계가 아님 |
| 별도 async LF API | 설치본에 run_ac_async는 존재 | 반환 asyncio Future wrapper에 native 중단 연결이 확인되지 않음. Future.cancel을 JVM kill로 간주하지 않음. 현재 코드 미사용 |
| run_agent.sh → scripts/run_agent.py:217 | uvicorn web_app:app, workers flag 없음 | 기본은 1이나 WEB_CONCURRENCY로 바뀔 수 있어 현재 단일 worker 보장 아님 |
| run_api.sh | 별도 physics app, port 8001, workers flag 없음 | 별도 프로세스 객체지만 그 앱 내부 병렬 접근은 여전히 보호 필요 |
| Dockerfile | CMD는 smoke_test.py | 운영 web worker 수를 보장하는 배포 설정이 아님 |

`uvicorn.Config`의 미기동 실험에서 환경 없음→1, WEB_CONCURRENCY=3→3, 명시 workers=1→1을 확인했다.
실제 실행 중인 배포 서버/프록시 worker 수는 조사하지 않았으며 보장됐다고 기록하지 않는다.
B는 지원 launcher에 명시적 `--workers 1` 또는 그와 동등한 검증된 운영 전제를 두고,
단일 worker가 확인되지 않으면 capability를 광고하지 않아야 한다. 자동 worker 감지 기능이 있다고 가정하지 않는다.

B에서 provider/graph budget을 명시하더라도 이미 시작한 JVM 작업의 강제 종료는 보장하지 않는다.
실행 종료 전까지 busy 상태를 유지하고 재시도·새 세션을 계산 중단으로 오인하지 않게 한다.
실제 LLM/JVM 최악 시간은 이번 작업에서 측정하지 않았으므로 숫자 예산은 구현 시 환경 검증이 필요하다.
새 작업 큐·Redis·DB·강제 종료용 구조는 도입하지 않는다.

## 7. 실제 검증과 보존 증거

실행 환경은 저장소 `.venv`, Python 3.11이며 실제 LLM·외부 서비스 호출은 **0회**다.
서버는 시작하지 않았다. 기존 테스트의 TestClient는 실제 endpoint를 in-process로 호출하며 LLM 선택은 scripted다.

| 검증 | 결과/범위 |
|---|---|
| 격리 feasibility probe | **8 passed in 1.55s**. 안정성/변경 감지, full KPG 사본, 설정 차이, 잠금, 실제 ToolNode, full artifact/변조 거부 |
| runtime boundary introspection | client timeout·명시 graph recursion 제한·worker 환경 override 확인. 실제 LLM 요청/서버 기동 없음 |
| 기존 관련 계약 | **20 passed, 1 warning in 1.72s**. chat UI↔REST·legacy response·API mapper·LF 설정 전달·발전기 비교·MN artifact 거부 |
| 보존/공백 점검 | `git diff --check`, 변경 파일 allowlist, 기존 추적 파일·로컬 Artifact SHA256 비교 |

최초 probe는 **4 failed, 4 passed, 12 warnings**였다. 같은 KPG 파일의 cross-load hash가 같다는 가정이 틀렸고,
설치 ToolNode의 단독 invoke에는 graph runtime이 필요했다. 이후 compiled MessagesState가 입력 AIMessage를
보존하는데 첫 message를 ToolMessage로 읽은 harness 오류로 **3 failed, 5 passed**였다.
이를 실제 마지막 ToolMessage를 검사하도록 수정했다. 기존 테스트/assertion은 변경하지 않았다.
caseDate 차이는 성공으로 숨기지 않고 후보 방식의 보수적 재로드 무효화 한계로 남겼다.
`dump_to_string` deprecation은 새 save_to_string으로 바꿨다. 초기/중간 로그도 보존한다.

기존 계약 테스트의 sandbox 실행은 첫 테스트 표시 후 TestClient 호출에서 진행하지 않아
전체 **180초 제한, exit 124**로 종료했다. 같은 테스트를 sandbox 제한 밖에서 실행해 20개가 통과했다.
특정 sandbox syscall이 원인이라고 확정하지 않는다. 기능 실패가 해결됐다고 꾸미거나 timeout을 skip으로 바꾸지 않았다.
최종 경고 1개는 설치 Starlette의 AnyIO BlockingPortal deprecation이다.

로그/재현 코드:

- `.local/ph4-02b0/20261104-140557/isolated-probes-initial.log`, `isolated-probes-harness.log`, `isolated-probes-final.log`
- 같은 경로의 `test_preflight.py`, `deadlines.py`, `observations.json`, `runtime-boundaries.json`, `existing-contracts-final.log`, `existing-contracts-sandbox-timeout.log`
- probe 코드는 원본을 보존한 채 임시 workspace에서만 실행했다. artifact 검증용 monkeypatch는 그 process 안의 함수 바인딩만 변경했다.
- 각 pytest 테스트 40초 SIGALRM, 명령 전체 180초 + kill-after 10초, 개별 introspection 30~60초 제한.
  native/worker가 signal을 즉시 처리하지 못할 가능성을 전체 프로세스 deadline으로 제한한다.

재현 명령(임시 경로는 위 검증 코드 사본을 복사해 다시 구성 가능):

```bash
timeout --kill-after=10s 180s env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/tmp/ai-ems-ph402b0-29fugkqm:src:. \
  .venv/bin/python -B -m pytest -q -s -p no:cacheprovider -p deadlines \
  /tmp/ai-ems-ph402b0-29fugkqm/test_preflight.py

timeout --kill-after=10s 180s env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/tmp/ai-ems-ph402b0-29fugkqm:src:. \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines \
  tests/test_chat_ui_updates.py tests/test_api_adapters.py \
  tests/test_network.py::test_load_flow_uses_default_parameters \
  tests/test_network.py::test_load_flow_accepts_custom_parameters \
  tests/test_generator_contingency_comparison.py \
  tests/test_mongolia_analysis_chat.py::test_ui_evidence_rejects_kpg_and_unpaired_or_text_only_tool_results
```

미검증: production v1 receipt/lock/ledger, MN revision, 전체 REST 병렬, Chrome, 실제 LLM, 장시간 JVM 강제 중단.
위 8개는 향후 B/C 28개 계약의 통과를 뜻하지 않는다. 과거 Phase 3·PH4-01 결과와 합산하지 않는다.

## 8. B 구현 파일·순서·중단 조건

1. `network.py`/작은 owner 모듈·KPG 로더: load provenance와 실제 snapshot, per-object guard, 준비 상태 정책.
   `web_app.py`와 `api/routes.py`/`api/app.py`의 소유자를 구분하고 공유 조회까지 빠짐없이 연결한다.
2. KPG `agent/tools.py`/`graph.py`: raw→compact helper와 v1 전용 wrapper, 현재 job guard/ledger, model 주입.
   legacy 직접 Tool·workflow formatter·자동 감시의 기존 오류 계약을 유지한다. Sensitivity 설정은 임의로 통일하지 않는다.
3. `web_app.py`의 순수 mapper·`analysis_context.py`(B에서 신규): canonical metadata snapshot과 모델 대조,
   optional schema/receipt/ref/run/중복·세션 경계. MN의 기존 artifact와 독립 network를 재사용한다.
4. launcher/graph factory: 단일 worker 명시, provider timeout·graph budget과 실패 경계를 기록한다.
5. B의 API/Tool/병렬 테스트와 legacy 회귀를 통과한 뒤에만 v1 capability를 공개한다. C의 UI 변경은 이후 별도 작업이다.

실제 로드 모델과의 대응 불명, 잠금 경로 누락, 실행 근거가 없는 artifact, 미해결 테스트 실패가 있으면
활성화를 중단하고 이유를 보고한다. 상위 Issue #14는 완료 처리하지 않는다.
