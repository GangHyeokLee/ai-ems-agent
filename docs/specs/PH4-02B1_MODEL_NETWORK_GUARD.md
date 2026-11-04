# PH4-02B1 — KPG 모델 식별과 공유 network guard

기준: `d6ecf79b0c1a4073c1c0292b06bbc0f2b58ba2af` / Issue [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
PR #18의 B0 보고서·A 설계 보완이 통합된 것을 확인하고 B1 브랜치에서 작업했다.

## 범위와 계약

- B1은 Python 서버의 로드 provenance·working-state revision·per-object guard만 구현한다.
- ChatRequest, 응답 필드, Tool schema/content/반환 값, grid capability, 콘솔 JS는 바꾸지 않는다.
- 신규 분석 문맥 v1 API·receipt registry·full artifact·실행 ledger는 구현하지 않는다. v1 capability도 광고하지 않는다.
- 일반 순차 REST 응답은 유지한다. 경합한 shared-native 접근은 새 `NetworkBusy`를 발생시키며,
  web/physics FastAPI 앱은 HTTP 409 `{"detail":"ANALYSIS_BUSY"}`로 변환한다.
  direct Tool은 같은 예외, 기존 ToolNode는 기존 오류 정책에 따라 status=error ToolMessage를 만든다.
- 원본 UI·계통 데이터·기존 테스트·과거 검증 해시는 수정하지 않는다. 도메인 알고리즘은 바꾸지 않고 경계와 설정 전달만 연결한다.

## 1. 실제 로드와 revision

`network.load_identified_network`를 web_app과 별도 physics app의 공유 모델 로더로 사용한다.
원본에서 **한 번 읽은 bytes**를 basename을 유지한 private 임시 파일에 쓰고 기존 `load_network`로 실제 로드한다.
따라서 KPG 전용 어댑터의 AC-only 임시 MAT·VSC/HVDC 변환을 그대로 거친다. 원본을 나중에 다시 읽어 출처라고 주장하지 않는다.
파일이 로드 후 교체돼도 그 객체의 출처는 실제 로드 bytes이며, 새 로드는 새 generation이다.
private 파일 기반 발전기/재급전 workflow의 기존 로더는 유지한다.

`network_access.NetworkAccess`는 native 객체마다 하나이며, 해당 객체의 속성에 붙이고 객체는 weak reference로 참조한다.
조회만 필요한 일반 모델도 같은 객체의 lock은 공유하지만, provenance가 없는 객체에는 revision을 발급하지 않는다.

내부 `capture_revision()`의 입력은 다음과 같다.

| 구성 | 내용 |
|---|---|
| 로드 provenance | source bytes SHA256, 실제 loader/import 설정, 로드한 process의 network/변환 어댑터 코드 hash, SDK·native engine build |
| generation | 로드별 UUID. 같은 입력을 다시 로드해도 과거 revision과 구분하며 재시작 간 동일 ID를 약속하지 않음 |
| working state | 실제 객체의 전체 canonical XIIDM 1.17, working variant ID, per-unit view/nominal apparent power |
| 실행 설정 | LF·Security·Sensitivity의 전체 일반/provider 설정·threshold와 실제 선택한 provider |
| 규약 | revision schema 및 명시적인 exporter 설정 |

XML을 sorted/full branch state/all CC/all operational-limit groups로 내보낸다.
알 수 없는 extension exporter·beta 직렬화 문제는 허용하지 않고 실패로 처리한다.
생성 시각·전압·조류·slack·제어값을 임의로 제거하지 않는다. 지원 범위는 현재 KPG MAT 모델과 working variant다.
설치본이 지원하지 않는 모델/extension을 추가할 때는 coverage 검증이 필요하다. native 내부 캐시 전체를 식별한다고 주장하지 않는다.

실제 계산에는 revision이 식별한 **같은 설정의 detached copy와 provider**를 전달한다.
LF/Security의 distributed_slack=False와 선로 Sensitivity의 자체 기본 True를 각각 유지한다.
기본 설정은 로드 시 고정된다. 환경/provider 전역 값을 나중에 바꿔도 로드 객체의 계산 조건이 조용히 바뀌지 않는다.
신뢰된 설정 변경은 owner lock 하에서 처리하거나 모델을 다시 로드해야 한다. B1은 외부 설정 변경 API를 만들지 않는다.

`capture_revision`은 매번 실제 snapshot을 새로 검증한다. 실패 시 이전 정상 revision을 반환하지 않는다.
초기 snapshot/설정/engine 식별이 실패하면 해당 로드의 revision은 사용 불가다. legacy 계산은 계속 사용할 수 있다.
`require_revision(expected)`는 현재 상태와 다르면 `ModelRevisionChanged`, 식별 불가면 `ModelIdentityUnavailable`이다.
소비자는 native 작업과 검사 전체를 같은 `network_access(n)` 경계 안에 두어야 한다. 메서드 반환 후 무잠금 접근의 원자성을 약속하지 않는다.

Python 라이브러리의 `run_ac_load_flow(n, parameters=...)` 명시 override는 계산·기존 반환을 유지하지만,
공유 모델의 고정 설정 identity와 혼용하지 않도록 해당 객체의 revision 사용을 닫는다(재로드 필요).
임의 native 외부 호출을 계산 근거로 인정하는 API는 없다. 지원 밖 코드가 raw 객체를 쓴다면 같은 guard를 직접 사용해야 한다.

## 2. 최초 AC와 준비 상태 정책

**자동 warm-up·추가 AC·상태 초기화·LF 설정 통일을 하지 않는다.**
로드 직후 snapshot도 실제 상태지만 성공한 물리 계산을 뜻하지 않는다. `initial_state`는 초기 snapshot과의 동등성 표기다.
기존 요청이 처음 AC를 수행해 전압·조류·slack 등을 쓰면 그 결과 그대로 state digest와 revision이 바뀐다.
이전 revision은 즉시 부적합하며, 새 상태의 반복 snapshot은 같은 revision이다. 단순 조회/snapshot은 계산을 실행하지 않는다.

이 정책은 B0의 최초 AC 문제를 정상 계산 변경으로 가장하지 않고 보수적으로 무효화한다.
B2에서 전후 revision이 다른 실행을 기존 상태의 결과로 등록해서는 안 된다.
현재 A 설계의 fail-closed 원칙에 따라 해당 요청의 receipt를 발급하지 않거나 별도로 검증된 준비 정책을 먼저 설계해야 한다.
자동 재실행이나 숨겨진 누락 보충은 금지한다. B1에는 receipt·활성화 경로 자체가 없다.

## 3. 잠금 적용 경계

| 파일 | 적용 함수/경계 |
|---|---|
| `network_access.py` | per-object RLock, non-blocking 획득, 동일 스레드 재진입, finally 해제, native 객체별 owner 연결 |
| `network.py` | standalone `run_ac_load_flow`, 식별 로드 및 일관된 snapshot |
| `tools/network_tools.py` | summary, line list/detail, generator list의 여러 조회/복사 전체 |
| `tools/security_tools.py` | line/generator contingency, most-severe-line 선택 |
| `tools/sensitivity_tools.py` | line 및 generator-outage sensitivity 진입 경계 |
| `tools/control_tools.py` | shared 객체를 받을 수 있는 candidate generation 전체. 내부 AC 쓰기도 보호 |
| `agent/tools.py` | shared 조회 4개 + line contingency + generator_sensitivity 전체. 자동 N-1→선택→민감도도 하나의 경계 |
| `web_app.py` | summary/map, Security/Sensitivity endpoint 전체, `_sensitivity_result_for_ui` metadata 조회 |
| `api/app.py`, `api/routes.py` | 별도 physics 객체의 식별/오류 처리와 Security/Sensitivity 전체 경계 |

map route의 KPG 분기와 PF-preview의 map 검증도 보호된 `network_map`을 거친다.
KPG preview 자체 계산은 기존 private 모델/cache lock을 유지한다. 순수 raw dict mapper에는 native lock이 필요 없다.
두 Agent factory(graph/direct)는 같은 raw 객체 속성의 같은 owner를 사용한다. graph.py/LLM 대기/채팅 handler 전체를 감싸지 않는다.
ToolNode의 실행 스레드가 실제로 획득하며, 호출자 thread의 RLock을 다른 thread가 재진입한다고 가정하지 않는다.

owner 생성 확인용 짧은 Python lock은 계산을 수행하거나 owner lock을 기다리지 않는다.
다른 객체의 엔진 작업·MN 독립 서비스·KPG 파일 기반 private 작업은 공유 KPG lock을 기다리지 않는다.
예외는 context의 finally로 해제하며, HTTP 취소는 다른 thread의 lock을 해제하지 않는다.
실제 worker가 native 작업·필요한 데이터 복사를 끝낸 뒤 자기 thread에서 해제한다.

## 4. 검증 근거와 범위

로그: `.local/ph4-02b1/20261104-143915/`. 테스트는 실제 LLM·Chrome 없이 수행했다.
신규 테스트별 45초 faulthandler 종료, 기존 테스트별 40초 SIGALRM, 전체 명령 300초+kill 10초를 적용했다.
thread/event/future/HTTP 취소 재현에는 별도 2~15초 제한도 있다.

- `tests/test_kpg_network_access.py`: 정확한 로드 bytes와 원본 교체, 반복/재로드/variant/최초 AC revision,
  운전점·전기정수·연결·정격·실행 설정·HVDC 변경 감지, snapshot 실패, XML round-trip, 실제 설정 전달·engine build 누락 거부,
  full KPG의 old-loader 수치·Tool schema/content 비교, RLock/예외/복구, 양방향 REST↔ToolNode 경합,
  LLM 대기 중 조회, 자동 민감도의 단계 사이 잠금, HTTP 취소 후 busy 유지, MN 실제 분석·private Tool 분리를 확인한다.
- 최초 신규 테스트는 **3 failed, 14 passed, 1 warning**이었다. weak reference만 남긴 테스트의 객체 수명,
  sorted XML round-trip의 행 순서, Starlette가 sync worker 동안 취소 확인을 지연하는 harness 가정을 수정했다.
  전 필드의 exact 값 비교는 ID 정렬 뒤 유지했다. 기존 assertion은 수정하지 않았다. 초기 로그도 보존한다.
- exporter 옵션 보완 중 inclusion/exclusion을 둘 다 빈 값으로 전달해 SDK가 거부하는 **12 failed, 105 passed**가 있었다.
  빈 exclusion 하나로 모든 지원 extension을 유지하도록 코드에서 수정했다. identity가 fail-closed였고 기존 계산은 유지됐다.
  `b1-export-options-failure.log`에 보존했다. 최종 full KPG round-trip은 변압기 tap·VSC/HVDC·정격 등 14종 테이블의 모든 반환 열을 ID 정렬 뒤 exact 비교했다.
- 기존 관련 95개는 1차 **95 passed, 1 warning (5.86s)**이었다. 최종 결합 결과는 **117 passed, 1 warning (7.03s)**이며 신규 22개와 기존 95개다.
- 통합 기준 코드의 `/tmp` 복사본과 B1에서 **기존 REST/Tool 15개**를 같은 순서로 수집해 전체 값·Tool schema를 비교했다.
  최초 계산 전 summary/map/조회 Tool, Security(명시/자동 감시), Sensitivity(명시/자동), PF preview, 발전기 API를 포함한다.
  두 결과 JSON은 SHA256 `870667b3e2af9d1fcadeeb2ceee6c55592bd3fadb6a733f7ef58b70850e3e1da`로 동일했다.
  baseline workspace의 studies/pipelines import 누락도 수정·로그 보존했다. 코드 차이로 인한 실패로 오인하지 않는다.
- 경고는 설치 Starlette/AnyIO deprecation이다. 별도 process import의 OPF optional dependency 안내는 AC 기능 실패가 아니다.
- 변경 범위 외 기존 파일 331개(기존 테스트·데이터·해시 기록·로컬 Artifact 포함)를 SHA256 비교로 보존 확인했다. `git diff --check`도 통과했다.

실행 명령:

```bash
run_dir=.local/ph4-02b1/20261104-143915
# deadlines.py는 기존 검증 때 사용한 stdlib signal 기반 임시 pytest plugin의 사본이다.
timeout --kill-after=10s 300s env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$run_dir:src:." .venv/bin/python -B -m pytest -q -p no:cacheprovider -p deadlines \
  tests/test_kpg_network_access.py tests/test_network.py tests/test_kpg_tools.py \
  tests/test_chat_ui_updates.py tests/test_api_adapters.py tests/test_kpg_pf_preview.py \
  tests/test_mongolia_analysis_chat.py tests/test_generator_contingency_tools.py \
  tests/test_generator_contingency_comparison.py tests/test_agent_adaptive_redispatch.py \
  tests/test_workflow_tools.py tests/test_console_ui.py
```

## 5. 판정과 후속 단계

B1 기반 구현은 **GO**이며 B2 착수에 필요한 객체 소유권·식별·잠금 진입점을 제공한다.
전후 state가 다른 최초 실행의 receipt 처리에는 위 보수적 정책을 적용해야 하며, 이를 준비 완료라고 광고해서는 안 된다.
B2는 별도 요청으로 진행한다. full artifact·현재-job ledger·분석 문맥/receipt·세션 동시성·서버 검증이 아직 없다.
단일 worker 명시·LLM/graph 시간 예산도 B0에서 확인한 후속 전제다. 기존 provider timeout과 launcher는 이번 범위에서 바꾸지 않았다.
v1 공개는 이 조건들과 B/C 인수 계약이 모두 충족된 후의 별도 판단이다. 28개 인수 조건 전체 통과를 주장하지 않는다.

이번 단계의 미검증: 실제 LLM·Chrome·운영 서버/프록시·JVM 강제 중단·다중 worker의 공유 identity.
main/통합 병합·기존 브랜치 삭제·상위 Issue #14 완료 처리는 수행하지 않는다.
