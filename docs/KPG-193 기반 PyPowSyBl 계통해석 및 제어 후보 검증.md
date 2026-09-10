# KPG-193 기반 PyPowSyBl 계통해석 및 제어 후보 검증

# \[KPG-193 기반 PyPowSyBl 계통해석 및 제어 후보 검증\] — 2026-09-10

## 가설

KPG-193 테스트 계통을 PyPowSyBl에 연계하면 기존에 직접 설비를 탈락시키고 AC Power Flow를 반복하던 N-1 분석을 전용 Security Analysis 기능으로 확장할 수 있다.

또한 사고 후 과부하가 발생한 설비에 대해 Sensitivity Analysis를 수행하면 해당 선로 조류에 영향도가 큰 발전기를 찾을 수 있고, 이를 기반으로 생성한 Redispatch 후보를 AC Power Flow 및 전체 계통 Security Analysis로 다시 검증할 수 있다.

최종적으로 다음 흐름의 구현 가능성을 확인하고자 했다.

> KPG-193  
> ↓  
> AC Power Flow  
> ↓  
> Security Analysis  
> ↓  
> 사고 후 위반 설비 식별  
> ↓  
> Sensitivity Analysis  
> ↓  
> 제어 후보 탐색  
> ↓  
> Redispatch  
> ↓  
> AC / Whole-network Security 재검증

## 설정

- 데이터셋/버전:
  - KPG-193 v2.0
  - 193 Bus
  - 201 실제 발전기
  - 193 Load
  - 385 AC Line
  - 2 HVDC
- 모델/방법:
  - PyPowSyBl 1.16.1
  - AC Load Flow
  - Security Analysis
  - Sensitivity Analysis
  - Generator Redispatch Candidate Generation
  - AC Load Flow Validation
  - Operator Strategy 기반 Whole-network Security Re-validation
- 주요 하이퍼파라미터:
  - AC Load Flow: `distributed_slack=False`
  - Base voltage 보존
  - 대표 N-1 사고: `LINE-16-28` 탈락
  - 대표 감시 선로: `LINE-16-22`
  - Redispatch 검증량: ±10 MW balanced redispatch
  - Redispatch 후보는 Sensitivity 기반으로 생성
  - 최종 효과는 AC 조류계산 및 전체 계통 Security Analysis로 재검증
- 환경:
  - Python 3.11
  - PyPowSyBl 1.16.1
  - SciPy / NumPy
  - AI-EMS Agent repository
  - 최종 regression test: `20 passed`

### 입력 데이터 정비

초기 PyPowSyBl 실습에서는 KPG의 MATPOWER `dcline`을 직접 읽기 어려워 HVDC를 fixed dummy generator로 변환한 파생 데이터를 사용했다.

기존 모델:

```
193 Bus
205 Generator
385 AC Line
0 IIDM HVDC
```

이후 KPG의 병렬 HVDC와 MATPOWER signed PF 표현을 처리하는 Adapter를 작성하여 실제 IIDM HVDC/VSC 모델로 전환하였다.

최종 모델:

```
193 Bus
201 Generator
385 AC Line
2 HVDC
4 VSC Converter Station
```

Adapter는 공통 MAT 파일 자체를 수정하지 않고, PyPowSyBl import 단계에서 `dcline/dclinecost`만 임시로 제외한 뒤 원래 DC line 정보를 이용하여 VSC/HVDC를 재구성하도록 구현하였다.

`gencost`, `genthermal`, `areas`, `dcline`, `dclinecost` 등 주요 운영·최적화 관련 필드는 공통 MAT에 유지하도록 구성하였다.

## 결과

### 1\. Base AC Power Flow

| 지표  | 값   | 비고  |
| --- | --- | --- |
| Bus | 193 | KPG-193 |
| Generator | 201 | 실제 발전기 |
| Load | 193 |     |
| AC Line | 385 |     |
| HVDC | 2   | IIDM HVDC |
| AC PF | CONVERGED | 3 iterations |
| 최저 전압 | 0.96022 pu |     |
| 최고 전압 | 1.03468 pu |     |

기존 dummy-HVDC 모델과 새 actual-HVDC 모델의 정상상태 결과를 비교하였다.

전체 385개 AC 선로 조류 차이:

```
max |ΔP| ≈ 1.3e-7 MW
max |ΔQ| ≈ 1.3e-6 Mvar
```

공통 201개 발전기 출력 차이:

```
max |ΔP| ≈ 3.0e-9 MW
max |ΔQ| ≈ 1.8e-6 Mvar
```

Bus 51/53 전압 magnitude와 angle도 동일하게 재현되었다.

따라서 기존 dummy-generator 기반 HVDC 근사와 실제 HVDC/VSC 모델은 정상상태 AC 해석 관점에서 수치 오차 수준의 동일한 결과를 나타냈다.

---

### 2\. Security Analysis

대표 사고로 `LINE-16-28` 탈락을 적용하였다.

| 지표  | 값   | 비고  |
| --- | --- | --- |
| 사고 선로 | LINE-16-28 | N-1 |
| 사고 전 위반 설비 | 0   |     |
| 사고 후 위반 설비 | 1   | 신규 위반 |
| 위반 선로 | LINE-16-22 |     |
| 선로 한계 | 1906.00 MVA | RateC |
| 사고 후 최대 피상전력 | 1967.28 MVA |     |
| 부하율 | 103.21% | 약 3.21% 초과 |
| Post-contingency | CONVERGED |     |

기존에 직접 선로를 탈락시킨 뒤 AC Power Flow를 반복하던 방식과 달리, PyPowSyBl Security Analysis에서는 contingency 정의, 사고 전·후 결과, limit violation을 하나의 분석 구조에서 관리할 수 있음을 확인하였다.

---

### 3\. Sensitivity Analysis

`LINE-16-28` 탈락 후 과부하가 발생한 `LINE-16-22`를 대상으로 발전기 출력 변화에 대한 선로 유효전력 조류 민감도를 계산하였다.

대표 결과:

| 발전기 | 민감도 | 해석  |
| --- | --- | --- |
| GEN-19#0 | +0.4211 | 절대 민감도 상위 |
| GEN-10#0 | +0.3638 | 절대 민감도 상위 |
| GEN-21 | +0.3513 | 절대 민감도 상위 |

민감도 값은 해당 운전점의 선형화 조건에서 발전기 주입량 1 MW 변화가 감시 선로 유효전력 조류에 어느 정도 영향을 주는지를 나타낸다.

Sensitivity Analysis 자체는 최적 Redispatch를 결정하지 않으며, 영향도가 큰 발전기를 제어 후보로 좁히는 용도로 사용하였다.

---

### 4\. Redispatch 후보 검증

Sensitivity 결과를 기반으로 balanced redispatch 후보를 생성하고 AC Power Flow로 검증하였다.

대표 후보:

```
GEN-19#0 +10 MW
GEN-36#0 -10 MW
```

| 지표  | 사고 후 | Redispatch 후 | 변화  |
| --- | --- | --- | --- |
| LINE-16-22 피상전력 | 1967.28 MVA | 1959.64 MVA | \-7.64 MVA |
| 부하율 | 103.21% | 102.81% | \-0.40%p |
| 위반 상태 | 위반  | 위반  | 잔존  |

Redispatch 후 선로 부하는 감소했으나 위반이 완전히 해소되지는 않았다.

즉 Sensitivity 기반 후보가 실제로 개선 방향을 보일 수는 있지만, Sensitivity만으로 제어 효과나 위반 해소를 보장할 수 없음을 확인하였다.

---

### 5\. Whole-network Security Re-validation

Redispatch 후보 적용 후 감시 선로만 확인하지 않고 PyPowSyBl Operator Strategy를 이용해 전체 계통 Security Analysis를 다시 수행하였다.

대표 결과:

```
Post-contingency status       : CONVERGED
Operator Strategy status      : CONVERGED
Redispatch 신규 위반 발생     : False
기존 LINE-16-22 위반           : Remaining
```

따라서 특정 선로의 부하가 감소했다는 사실만으로 제어 후보를 평가하지 않고, 다른 설비에서 신규 위반이 발생하는지까지 다시 확인하는 흐름을 구현할 수 있었다.

## 분석

가설은 전반적으로 확인되었다.

기존 KPG 실습에서는 설비를 직접 탈락시키고 AC Power Flow를 반복하여 N-1 결과를 확인하였다. 이번 실습에서는 동일한 물리계통을 PyPowSyBl Security Analysis에 연결함으로써 사고 정의, 사고 전·후 비교, 위반 설비 관리가 전용 분석 기능 안에서 구조화되는 차이를 확인하였다.

또한 사고 후 과부하 여부 확인에서 끝나지 않고 Sensitivity Analysis를 이용해 문제 선로에 영향도가 큰 발전기를 제어 후보로 좁힐 수 있었다.

다만 Sensitivity는 선형화된 영향도이므로 높은 민감도가 곧바로 최적 제어 또는 위반 해소를 의미하지 않았다. 실제 대표 Redispatch 후보도 `103.21% → 102.81%`로 과부하는 완화되었지만 위반은 남았다.

따라서 다음과 같은 역할 구분이 적절하다고 판단하였다.

```
Security Analysis
→ 어디에서 문제가 발생했는가?

Sensitivity Analysis
→ 어떤 제어변수가 그 문제에 영향이 큰가?

Candidate Generation
→ 어떤 조치를 시험해볼 것인가?

AC / Security Re-validation
→ 실제 적용 시 계통이 어떻게 변하는가?
```

이 구조는 향후 AI-EMS에서 AI가 위험 후보 또는 제어 후보를 생성하더라도, 최종 계통 상태와 조치 효과는 기존 물리해석 엔진으로 재검증하는 구조로 연결할 수 있다.

입력 데이터 측면에서도 기존 dummy HVDC 표현을 실제 HVDC/VSC 모델로 교체한 뒤 정상상태 전압, 전체 AC 선로 조류, 발전기 출력 및 대표 N-1 결과가 기존 모델과 사실상 동일하게 재현됨을 확인하였다.

**이를 통해 KPG-193을 공통 테스트 계통으로 두고, 목적에 따라 PyPowSyBl과 같은 외부 해석도구를 결합하는 방식이 가능함을 확인하였다.**

## 다음 액션

- [x] KPG-193 AC Load Flow 검증
- [x] PyPowSyBl Security Analysis 적용
- [x] 대표 N-1 사고 결과 기존 실습과 비교
- [x] Sensitivity Analysis를 통한 발전기 영향도 분석
- [x] Sensitivity 기반 Redispatch 후보 생성
- [x] Redispatch 후 AC Power Flow 검증
- [x] Whole-network Security Re-validation
- [x] Dummy HVDC → Actual HVDC/VSC 모델 전환
- [x] 기존 모델과 정상상태 및 대표 N-1 회귀검증
- [ ] 발표자료에 Security → Sensitivity → Control Validation 흐름 반영
-> 향후 실제 Simulator/AI 모듈에서 Physics API를 통한 호출 구조 검토