# AI Agent 기반 발전기 N-1 Screening 및 대응방안 Tool Orchestration

# \[AI Agent 기반 발전기 N-1 Screening 및 대응방안 Tool Orchestration\] — 2026-09-30

## 가설

자연어로 계통 분석을 요청했을 때 AI Agent가 요청 의도를 해석하여 적절한 전력계통 해석 Tool을 선택하고, 이전 분석 결과를 후속 요청의 문맥으로 활용할 수 있을 것이다. 또한 선택된 고수준 Tool 내부의 분석 workflow를 통해 Security Analysis, Sensitivity Analysis, Redispatch 후보 생성 및 물리해석 재검증을 연계할 수 있는지 확인한다.

특히 발전기 N-1 전체 Screening 결과에서 위험 사고를 선별한 뒤, 사용자가 “1위 사고 대응방안 분석해줘”와 같이 후속 요청을 했을 때 해당 발전기 사고를 다시 식별하여 **Security Analysis → Sensitivity Analysis → Redispatch 후보 생성 → AC Load Flow / Security Analysis 재검증**으로 이어지는 분석 절차를 수행할 수 있는지 확인한다.

## 설정

- 데이터셋/버전: KPG-193 v2.0 (`KPG193_ver2_0_powsybl_full.mat`)
- 계통해석 도구: PyPowSyBl 1.16.1
- Agent 구성: LangGraph 기반 Tool Calling
- LLM 인터페이스: ChatOllama
- LLM 모델: qwen3.5:9b
- 사용자 인터페이스: AI-EMS Agent Web UI
- Load Flow balancing: Distributed slack
- Balance type: `PROPORTIONAL_TO_GENERATION_P_MAX`
- 주요 Agent Tool
  - `generator_contingency_analysis`
  - `generator_contingency_comparison`
  - `generator_contingency_screening`
  - `generator_outage_response_analysis`
- 대응방안 분석 내부 흐름
  - Generator Security Analysis
  - 사고 후 Sensitivity Analysis
  - Balanced Redispatch 후보 생성
  - AC Load Flow 재검증
  - 전체 계통 Security Analysis 재검증
- 결과 출력 방식: 계산 결과를 기반으로 한 결정론적 Markdown Formatter
- 회귀 테스트: 전체 pytest 59개 통과

## 결과

### 1\. 과부하가 발생하지 않는 발전기 사고 처리

자연어 요청:

> `GEN-166 발전기 사고에 대한 대응방안 분석해줘`

Agent는 `GEN-166` 발전기 사고에 대한 계통해석을 수행했고, 사고 후 AC Load Flow / Security Analysis는 수렴했으나 교정제어 대상으로 선택할 과부하 선로가 확인되지 않았다.

따라서 불필요한 Sensitivity Analysis나 Redispatch 후보를 생성하지 않고 분석을 종료했다.

| 지표  | 결과  | 비고  |
| --- | --- | --- |
| 사고 발전기 | `GEN-166` | 사용자 자연어 지정 |
| 사고 후 계산 | 수렴  | AC Load Flow / Security Analysis |
| 과부하 선로 | 없음  | 교정제어 대상 없음 |
| Redispatch 후보 생성 | 수행하지 않음 | 불필요한 제어 탐색 방지 |

**첨부 화면:** `GEN-166 발전기 사고 대응방안 분석` 결과 화면

![](/api/files/01a0f002-467e-728b-b08b-fff83260b00c/스크린샷_2026-09-30_100044.png)

---

### 2\. 자연어 기반 발전기 N-1 전체 Screening

자연어 요청:

> `발전기 N-1 전체 분석해서 위험한 사고 5개 알려줘. 분산 슬랙으로 분석해줘.`

Agent는 개별 발전기 사고를 반복 호출하는 대신 발전기 N-1 Screening Tool을 선택하여 접속 중인 발전기 100대에 대한 Batch Security Analysis를 수행했다.

결과는 다음과 같았다.

| 지표  | 결과  |
| --- | --- |
| 분석 발전기 수 | 100개 |
| 정상 수렴 / 위반 없음 | 76건 |
| 수렴 / 위반 발생 | 24건 |
| Load Flow balancing | Distributed slack |

상위 위험 사고는 다음과 같이 선정되었다.

| 순위  | 발전기 | 분류  | 사고 후 위반 설비 | 최대 부하율 |
| --- | --- | --- | --- | --- |
| 1   | `GEN-124#1` | CONVERGED_VIOLATION | `LINE-134-193` | 105.60% |
| 2   | `GEN-124#2` | CONVERGED_VIOLATION | `LINE-134-193` | 105.60% |
| 3   | `GEN-124#3` | CONVERGED_VIOLATION | `LINE-134-193` | 105.60% |
| 4   | `GEN-124#4` | CONVERGED_VIOLATION | `LINE-134-193` | 105.60% |
| 5   | `GEN-124` | CONVERGED_VIOLATION | `LINE-134-193` | 105.24% |

**첨부 화면:** `발전기 N-1 Screening 결과` 화면

![](/api/files/01a0f002-a3d2-76e7-b246-dcc711d78ad7/스크린샷_2026-09-30_100112.png)

---

### 3\. 이전 Screening 결과를 이용한 멀티턴 대응방안 분석

Screening 직후 동일한 대화에서 다음과 같이 요청했다.

> `1위 사고 대응방안 분석해줘`

사용자는 발전기 ID를 다시 입력하지 않았지만, Agent는 이전 Screening 결과의 1위 사고인 `GEN-124#1`을 대상으로 해석을 이어갔다.

이후 다음 분석 절차가 수행되었다.

```
발전기 N-1 Screening
→ 1위 사고 GEN-124#1 선택
→ Generator Security Analysis
→ LINE-134-193 과부하 확인
→ 사고 후 Sensitivity Analysis
→ Redispatch 후보 생성
→ 후보별 AC Load Flow 재검증
→ 전체 계통 Security Analysis 재검증
→ 결과 비교 및 설명
```

`GEN-124#1` 사고 후 `LINE-134-193`의 부하율은 105.60%였고, 총 3개의 Redispatch 후보가 실제 물리해석으로 검증되었다.

| 민감도 순위 | AC 검증 순위 | Redispatch | 민감도 예상 감소 | 실제 개선 | 제어 후 부하율 |
| --- | --- | --- | --- | --- | --- |
| 1   | 1   | `GEN-106 +10 / GEN-193 -10 MW` | 5.56 MW | 5.60 MVA | 105.35% |
| 2   | 2   | `GEN-100#2 +10 / GEN-193 -10 MW` | 5.24 MW | 5.28 MVA | 105.36% |
| 3   | 3   | `GEN-53#10 +10 / GEN-193 -10 MW` | 5.17 MW | 5.19 MVA | 105.36% |

최선의 시험 후보는 다음과 같았다.

- Redispatch: `GEN-106 +10 MW / GEN-193 -10 MW`
- 사고 후 피상전력: 2293.71 MVA
- 제어 후 피상전력: 2288.11 MVA
- 부하율: 105.60% → 105.35%
- 과부하: 완화되었으나 미해소
- Redispatch로 인한 신규 위반: 없음
- 제어 후 잔존 위반: `LINE-134-193`

**첨부 화면:** `1위 사고 대응방안 분석` 결과 화면

![](/api/files/01a0f002-d235-75f0-9240-d0511ed3a760/스크린샷_2026-09-30_100125.png)

## 분석

이번 실험에서는 자연어 요청에 따라 Agent가 서로 다른 분석 Tool을 선택하고, 각 Tool의 결과를 다음 분석 단계로 연결할 수 있음을 확인했다.

첫 번째 `GEN-166` 사례에서는 발전기 사고 이후 과부하가 없었기 때문에 Redispatch 후보를 생성하지 않았다. 이는 모든 발전기 사고에 동일한 분석 절차를 기계적으로 수행하는 것이 아니라, **실제 계통해석 결과에 따라 후속 분석 필요 여부를 구분하는 흐름**이 동작했음을 보여준다.

두 번째 N-1 Screening 요청에서는 개별 발전기 사고 Tool을 여러 번 호출하지 않고 전체 발전기를 대상으로 하는 Batch Screening Tool이 선택되었다. 100개 발전기 사고 중 24건의 위반 사례를 분류하고 상위 5건을 제시함으로써, 자연어 요청을 계통해석 기능의 실행 단위로 변환할 수 있었다.

특히 Screening 이후의 `1위 사고 대응방안 분석해줘` 요청에서는 발전기 ID를 다시 입력하지 않았음에도 이전 결과의 `GEN-124#1`을 대상으로 분석이 이어졌다. 이를 통해 **이전 Tool 실행 결과를 후속 자연어 요청의 문맥으로 활용하는 멀티턴 분석 흐름**을 확인했다.

대응방안 분석에서는 LLM이 계통해석 결과를 직접 생성하는 대신, 사전에 구현한 물리해석 함수를 이용하여 실제 계산을 수행했다.

```
자연어 요청
→ Agent가 분석 목적 해석
→ 적절한 고수준 Tool 선택
→ Tool 내부 workflow에서 PyPowSyBl 기반 실제 계산
→ 계산 결과에 따라 후속 분석 수행
→ 결정론적 Formatter로 결과 정리
```

이 구조에서는 Agent의 역할과 물리해석 엔진의 역할이 구분된다. Agent는 사용자의 요청을 해석하고 필요한 분석 기능을 선택하거나 연결하는 역할을 담당하며, 조류계산·상정사고·민감도·Redispatch 효과와 같은 계통 상태는 PyPowSyBl 기반 함수에서 계산한다.

또한 대응방안 결과를 LLM의 자유 생성 문장에만 맡기지 않고, 주요 물리량과 해석 주의사항을 결정론적 Formatter로 구성했다. 이를 통해 MW와 MVA의 구분, Distributed slack과 실제 Redispatch의 구분, 과부하 완화와 위반 해소의 구분 등 계통해석 결과를 설명할 때 필요한 기준을 일정하게 유지하도록 했다.

이번 실험에서 민감도 기반 1순위 후보가 실제 AC 검증에서도 가장 큰 개선을 보였지만, 과부하를 완전히 해소하지는 못했다. Agent는 이를 “최적 제어”나 “문제 해결 완료”로 표현하지 않고, 시험 후보 중 가장 좋은 결과와 잔존 위반을 함께 출력했다. 따라서 Agent 기반 분석에서도 **후보 생성 결과와 실제 물리해석 검증 결과를 구분하여 전달하는 방식**이 중요함을 확인했다.

---

### AI EMS 활용 가능성

이번 실험에서 확인한 Agent 구조는 운영자가 여러 계통해석 기능의 세부 API나 실행 순서를 직접 지정하지 않고도 자연어로 분석을 요청할 수 있는 인터페이스로 활용할 수 있다.

예를 들어 다음과 같은 요청 흐름을 구성할 수 있다.

```
"발전기 N-1 전체 분석해줘"
→ Generator N-1 Screening

"1위 사고 자세히 분석해줘"
→ 해당 발전기 사고 AC/Security Analysis

"대응방안 분석해줘"
→ Sensitivity
→ Redispatch 후보 생성
→ AC/Security 재검증

→ 결과 요약 및 잔존 위험 설명
```

이러한 방식은 여러 해석 기능을 하나의 AI 모델로 대체하는 것보다, **기존 계통해석 기능을 Tool로 구성하고 Agent가 상황에 따라 필요한 Tool을 선택·연결하는 형태**로 활용할 수 있다.

특히 Screening과 상세분석을 분리하면 전체 사고를 모두 동일한 수준으로 깊게 계산하기보다, 먼저 위험 후보를 좁힌 뒤 필요한 사고에 대해 추가 분석을 수행하는 흐름을 구성할 수 있다. 이후 Sensitivity와 Redispatch 검증까지 연결하면 사고 확인에서 제어 후보 검토까지 하나의 분석 절차로 확장할 수 있다.

현재 Agent는 사전에 정의된 Tool과 단순한 분석 흐름을 연결하는 PoC 수준이며, 실제 EMS 운영 절차, 계통 운영 기준, 경제성, 제어 가능 여부 등의 조건을 모두 반영한 시스템은 아니다. 다만 이번 실험을 통해 **자연어 요청 → Tool 선택 → 물리해석 → 후속 분석 → 결과 설명**으로 이어지는 기본적인 Agent orchestration 구조를 확인했다.

향후에는 기존 EMS 계산엔진, 운영 기준, RAG 기반 운영 절차 검색 등을 추가 Tool로 연결하여 분석 결과와 운영 규칙을 함께 참고하는 형태로 확장할 수 있다.

## 다음 액션

- [x] 발전기 사고 단건 분석 Tool 연결
- [x] 발전기 N-1 전체 Screening Tool 연결
- [x] 발전기 사고 대응방안 분석 Tool 연결
- [x] Screening 결과의 후속 멀티턴 요청 확인
- [x] 과부하가 없는 사고에서 불필요한 Redispatch 생성을 하지 않는 경로 확인
- [x] Sensitivity → Redispatch → AC/Security 재검증 흐름 연결
- [x] 결정론적 Markdown Formatter 적용
- [x] Agent/Workflow 관련 회귀 테스트 포함 전체 pytest 59개 통과
- [ ] 자연어 표현이 달라져도 동일한 Tool을 안정적으로 선택하는지 추가 검토
- [ ] 여러 종류의 사고와 사용자 후속 요청에 대한 Tool routing 평가
- [ ] 실제 EMS 기능 및 운영 절차/RAG 연계 가능성 검토
- [ ] 장기적으로 Tool 실행 이력, 입력값, 결과를 운영자 확인용 로그로 구조화