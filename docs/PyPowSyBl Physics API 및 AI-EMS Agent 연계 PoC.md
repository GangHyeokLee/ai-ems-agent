# PyPowSyBl Physics API 및 AI-EMS Agent 연계 PoC

# \[PyPowSyBl Physics API 및 AI-EMS Agent 연계 PoC\] — 2026-09-10

## 가설

PyPowSyBl 기반 계통해석 기능을 Domain Tool 및 REST API 형태로 분리하면, LLM Agent나 외부 Simulator/AI 모듈이 동일한 물리해석 기능을 재사용할 수 있다.

또한 외부 모듈이 위험 후보 또는 제어 후보를 생성하고, Physics API가 Security Analysis, Sensitivity Analysis 및 제어 후보 검증을 수행하도록 역할을 분리하면 다음과 같은 AI-EMS 연계 구조를 구현할 수 있다.

```
External Simulator / AI / GNN
        ↓
위험 후보 / 제어 후보
        ↓
Physics API
        ↓
Security Analysis
        ↓
Sensitivity Analysis
        ↓
Control Candidate Validation
        ↓
PyPowSyBl
```

LLM은 동일한 Domain Tool을 자연어 인터페이스로 호출하고, 외부 Simulator는 REST API를 통해 직접 호출하는 구조가 가능한지 확인하고자 했다.

## 설정

- **데이터셋/버전**
  - KPG-193 v2.0
  - `KPG193_ver2_0_powsybl_full.mat`
  - 193 Bus
  - 201 Generator
  - 385 AC Line
  - 2 HVDC
- **모델/방법**
  - PyPowSyBl 1.16.1
  - FastAPI Physics API
  - LangGraph
  - Local LLM / Ollama
  - Domain Tool Calling
  - External Simulator Mock Integration
- **Physics API**
  - `POST /api/v1/security-analysis`
  - `POST /api/v1/sensitivity-analysis`
  - `POST /api/v1/redispatch-validation`
  - `POST /api/v1/contingency-response`
- **Domain Tool**
  - Network 조회
  - Security Analysis
  - Sensitivity Analysis
  - Redispatch Candidate Generation
  - Balanced Redispatch AC Validation
  - Whole-network Security Validation
- **주요 테스트 입력**
  - 위험 후보: `LINE-183-190`
  - Mock risk score: `0.91`
  - 외부 제어 후보:
    - `GEN-10#0 +10 MW`
    - `GEN-190 -10 MW`
- **환경**
  - Python 3.11
  - PyPowSyBl 1.16.1
  - FastAPI
  - LangGraph
  - Ollama
  - AI-EMS Agent repository
  - Unit regression: `20 passed`

## 결과

### 1\. Domain Tool / Physics API 분리

계통해석 로직을 LLM 내부에 직접 구현하지 않고 별도의 Domain Tool로 구성하였다.

```
Agent
  └─ Domain Tools
       └─ PyPowSyBl

External Simulator
  └─ HTTP
       └─ Physics API
            └─ Domain Tools
                 └─ PyPowSyBl
```

이를 통해 LLM Agent와 외부 시스템이 동일한 계통해석 코드를 재사용할 수 있도록 구성하였다.

---

### 2\. LLM Agent Tool Calling

LLM Agent가 자연어 요청을 해석한 뒤 필요한 Domain Tool을 호출하도록 구성하였다.

예를 들어 다음과 같은 요청 흐름을 구현하였다.

```
"LINE-16-28 사고를 분석해줘"
→ Security Analysis

"영향이 큰 발전기는?"
→ Sensitivity Analysis

"대응방안까지 검토해줘"
→ Security
→ Sensitivity
→ Redispatch Candidate
→ AC / Security Validation
```

물리해석 결과를 LLM이 임의 생성하지 않도록 실제 계통 데이터 또는 계산이 필요한 질문에는 Tool을 사용하도록 System Prompt를 구성하였다.

또한 Security/Sensitivity/Redispatch 결과의 주요 출력은 deterministic formatter를 적용하여 물리량과 단위가 잘못 설명되는 것을 줄였다.

---

### 3\. External Simulator Mock Integration

실제 Simulator/GNN을 직접 연결하기 전에 외부 모듈이 위험 후보를 전달하는 상황을 Mock으로 구성하였다.

입력:

```
Risk candidate : LINE-183-190
Risk score     : 0.91
```

여기서 `risk_score=0.91`은 Physics API가 계산한 값이 아니라 **외부 AI/Simulator가 생성했다고 가정한 값**이며, Physics API는 이를 재계산하지 않는다.

---

### 4\. Security Validation

Mock Simulator가 전달한 `LINE-183-190`을 사고 후보로 Physics API에 전달하였다.

| 지표  | 결과  |
| --- | --- |
| Outage | LINE-183-190 |
| 사고 후 위반 설비 | 1   |
| 신규 위반 | LINE-176-190 |
| Security Analysis | CONVERGED |

즉 외부 AI가 위험하다고 판단한 후보를 실제 AC Security Analysis로 다시 검증할 수 있음을 확인하였다.

---

### 5\. Sensitivity Analysis

Security Analysis에서 확인된 `LINE-176-190`을 대상으로 발전기 민감도를 계산하였다.

대표 결과:

| 발전기 | 민감도 |
| --- | --- |
| GEN-190#0 | \-0.589638 |
| GEN-190 | \-0.589638 |
| GEN-190#3 | \-0.589638 |
| GEN-190#4 | \-0.589638 |
| GEN-190#2 | \-0.589638 |

GEN-190 계열 발전기들이 해당 감시 선로 유효전력 조류에 높은 영향도를 보였다.

동일 계통 위치 또는 전기적으로 동일한 위치에 연결된 발전기들의 경우 동일한 민감도 값이 나타날 수 있다.

Sensitivity 결과는 제어 후보 탐색에 사용할 수 있지만, 그 자체로 최적 제어나 과부하 해소를 의미하지 않는다.

---

### 6\. 외부 제어 후보 검증

외부 Simulator 또는 제어 모듈이 다음 Redispatch 후보를 생성했다고 가정하였다.

```
GEN-10#0 +10 MW
GEN-190   -10 MW
```

Physics API의 `redispatch-validation`을 이용하여 후보를 실제 AC 계통에 적용하고 전체 계통 Security Analysis까지 수행하였다.

| 지표  | 적용 전 | 적용 후 |
| --- | --- | --- |
| LINE-176-190 피상전력 | 2476.13 MVA | 2469.21 MVA |
| 부하율 | 114.00% | 113.68% |
| 개선 여부 | \-  | True |
| 위반 잔존 | \-  | True |
| 신규 위반 발생 | \-  | False |
| 전체 재검증 | \-  | CONVERGED |

Redispatch로 선로 부하는 감소했지만 기존 과부하는 완전히 해소되지 않았다.

따라서 외부 모듈이 제어 후보를 생성하더라도 실제 효과는 물리해석 엔진에서 다시 검증해야 함을 확인하였다.

---

### 7\. Integration 결과

최종 연계 테스트 흐름은 다음과 같이 동작하였다.

```
Mock Simulator
  │
  ├─ LINE-183-190
  └─ risk_score = 0.91
          ↓
      Physics API
          ↓
Security Analysis
→ LINE-176-190 신규 위반
          ↓
Sensitivity Analysis
→ GEN-190 계열 영향도 확인
          ↓
External Control Candidate
→ GEN-10#0 +10 MW
→ GEN-190   -10 MW
          ↓
AC / Whole-network Security Validation
→ 2476.13 → 2469.21 MVA
→ 114.00 → 113.68 %
→ CONVERGED
→ 신규 위반 없음
```

이를 통해 **외부 위험 후보 → 물리검증 → 영향도 분석 → 외부 제어 후보 → 물리 재검증**까지의 모듈 간 연계 흐름이 정상 동작함을 확인하였다.

## 분석

가설은 전반적으로 확인되었다.

PyPowSyBl 기능을 Agent 내부 코드에 직접 종속시키지 않고 Domain Tool로 분리한 뒤, 동일한 기능을 LLM Agent와 REST Physics API에서 재사용할 수 있었다.

이를 통해 다음과 같은 역할 분리가 가능하였다.

```
AI / GNN
→ 미래 상태 예측
→ 위험 후보 선별
→ 제어 후보 생성

Physics API / PyPowSyBl
→ 후보에 대한 실제 계통 영향 계산
→ Security Analysis
→ Sensitivity Analysis
→ 제어 효과 재검증

LLM Agent
→ 자연어 분석 요청
→ Tool 선택 및 실행
→ 결과 설명
```

특히 `integration_probe.py`를 통해 실제 Simulator와 연결되기 전에 HTTP API 경계를 포함한 모듈 간 연계 구조를 사전 검증하였다.

다만 이번 실험에서 위험 후보와 `risk_score`는 실제 GNN에서 전달받은 것이 아니라 Mock 데이터이며, 실제 GraphKit/GNN 모듈과 직접 통합한 것은 아니다.

또한 제어 후보 역시 최적화 결과가 아니라 외부 모듈이 제안했다고 가정한 후보를 검증한 것이다.

따라서 이번 PoC의 의미는 AI 모델 자체의 성능을 검증한 것이 아니라,

> **AI 또는 외부 모듈이 생성한 후보를 기존 물리해석 엔진으로 검증할 수 있는 인터페이스와 모듈 구조를 확인한 것**

에 있다.

이는 AI가 기존 EMS 물리해석을 대체하기보다, 예측·후보 생성·운영자 지원을 담당하고 최종 계통 상태 및 제어 효과는 물리해석 엔진으로 검증하는 AI-EMS 구조와 연결된다.

## 다음 액션

- \[ \]