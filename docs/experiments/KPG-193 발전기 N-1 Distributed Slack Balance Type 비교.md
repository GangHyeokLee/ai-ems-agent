# KPG-193 발전기 N-1 Distributed Slack Balance Type 비교

# [KPG-193 발전기 N-1 Distributed Slack Balance Type 비교] — 2026-09-30

## 가설

앞선 `GEN-124#1` 분석에서는 `PROPORTIONAL_TO_GENERATION_P_MAX`를 사용한 Distributed Slack이 Single slack보다 `LINE-134-193` 과부하와 계통 손실을 낮추는 것을 확인하였다.

다만 Distributed Slack 자체도 하나의 고정된 방식이 아니라, 유효전력 mismatch를 어떤 기준으로 발전기들에 배분할지 결정하는 `BalanceType`에 따라 실제 사고 후 계통 상태가 달라질 수 있다.

따라서 동일한 `GEN-124#1` 발전기 탈락 사고를 대상으로 다음 세 가지 발전기 기반 Balance Type을 비교한다.

- `PROPORTIONAL_TO_GENERATION_P_MAX`
- `PROPORTIONAL_TO_GENERATION_P`
- `PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN`

주요 확인 질문은 다음과 같다.

- `P_MAX`와 `P`는 발전기별 보상 분포와 계통 조류에서 얼마나 다른가
- `REMAINING_MARGIN`은 여유가 작은 발전기의 부담을 실제로 줄이는가
- Balance Type 변화가 `LINE-134-193` 과부하와 계통 손실에 어떤 영향을 주는가
- 특정 Balance Type에서 과부하가 작게 나타날 경우 이를 실제 최적 Redispatch로 해석할 수 있는가
- 후속 Redispatch PoC에서 어떤 Balance Type을 대표 기준으로 사용할 것인가

## Preflight

실험 전 PyPowSyBl 1.16.1 / OpenLoadFlow 환경에서 Balance Type 및 입력 데이터 상태를 확인하였다.

- PyPowSyBl: `1.16.1`
- Load Flow provider: `OpenLoadFlow`
- KPG-193 전체 발전기: 201대
- 접속 발전기: 100대
- 접속 발전기 `target_p`, `min_p`, `max_p`: 100/100 유효
- `P_MAX`, `P`, `REMAINING_MARGIN`, `PARTICIPATION_FACTOR` enum 존재
- OpenLoadFlow provider에서 네 Balance Type 모두 parameter support 확인
- `activePowerControl` extension rows: 0

따라서 `PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR`는 enum/provider 수준에서는 사용할 수 있으나, KPG-193에 실제 participation factor 데이터가 없으므로 이번 실험에서는 제외하였다.

## 설정

- 데이터셋: KPG-193 v2.0
- 파일: `KPG193_ver2_0_powsybl_full.mat`
- 사고: `GEN-124#1` 탈락
- 해석: PyPowSyBl AC Load Flow + AC Security Analysis
- Slack: `distributed_slack=True`
- 관찰 선로: `LINE-134-193`
- 선로 피상전력: 양단 `sqrt(P²+Q²)` 중 큰 값
- `LINE-134-193` 영구 피상전력 한계: 약 2172 MVA
- 비교 코드: `scripts/compare_generator_balance_types.py`
- 비교 코드 커밋: `5c57e9e` — `feat: GEN-124#1 Balance Type 비교 실험 추가`

발전기 분배 지표는 다음과 같이 계산하였다.

- 참여 발전기: `|ΔP| > 0.01 MW`
- near-Pmax 절대 기준: 사고 후 headroom `≤ 5 MW`
- near-Pmax 상대 기준: 사고 후 headroom `≤ Pmax의 1%`

## 결과

### 핵심 비교

| 지표 | P_MAX | P | REMAINING_MARGIN |
| --- | ---: | ---: | ---: |
| Post AC LF | CONVERGED | CONVERGED | CONVERGED |
| Security | CONVERGED | CONVERGED | CONVERGED |
| Distributed active power | 969.180 MW | 969.024 MW | 972.244 MW |
| Residual mismatch | 0.433 MW | 0.436 MW | 0.344 MW |
| 참여 발전기 수 | 99 | 99 | 99 |
| 최소 post headroom | 1.795 MW | 1.215 MW | **9.704 MW** |
| Headroom p10 | 3.179 MW | 2.620 MW | **11.728 MW** |
| Headroom median | 18.701 MW | 18.672 MW | 19.187 MW |
| near-Pmax ≤ 5 MW | 25대 | 25대 | **0대** |
| near-Pmax ≤ 1% Pmax | 25대 | 25대 | **0대** |
| `LINE-134-193` 최대 S | 2293.721 MVA | **2287.604 MVA** | 2398.585 MVA |
| `LINE-134-193` 부하율 | 105.604% | **105.322%** | **110.432%** |
| 사고 후 계통 손실 | 817.311 MW | **817.159 MW** | 820.287 MW |
| 사고 후 AC line P loss | 817.316 MW | **817.164 MW** | 820.290 MW |
| Limit violation records | 2 | 2 | 2 |
| 위반 설비 수 | 1 | 1 | 1 |
| Thermal violation records | 2 | 2 | 2 |
| Voltage violation records | 0 | 0 | 0 |

### P_MAX와 P의 차이

`PROPORTIONAL_TO_GENERATION_P`는 `P_MAX` 대비:

- `LINE-134-193`: 2293.721 → 2287.604 MVA, **6.117 MVA 감소**
- 부하율: 105.604 → 105.322%, **0.282%p 감소**
- 사고 후 계통 손실: 817.311 → 817.159 MW, **0.152 MW 감소**
- 최소 headroom: 1.795 → 1.215 MW, **0.580 MW 감소**
- headroom p10: 3.179 → 2.620 MW, **0.559 MW 감소**

즉 대표 사고에서는 두 방식의 계통 상태가 매우 유사했으며, `P`가 관찰 선로 부하율과 손실은 소폭 낮았지만 발전기 headroom 지표는 오히려 소폭 불리했다.

### REMAINING_MARGIN의 차이

`PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN`은 `P_MAX` 대비:

- 최소 headroom: 1.795 → **9.704 MW**, **+7.909 MW**
- headroom p10: 3.179 → **11.728 MW**, **+8.549 MW**
- near-Pmax ≤5 MW: 25 → **0대**
- near-Pmax ≤1% Pmax: 25 → **0대**

반면 계통 조류와 손실은:

- `LINE-134-193`: 2293.721 → **2398.585 MVA**, **+104.864 MVA**
- 부하율: 105.604 → **110.432%**, **+4.828%p**
- 사고 후 계통 손실: 817.311 → **820.287 MW**, **+2.976 MW**

로 악화되었다.

즉 `REMAINING_MARGIN`은 발전기 여유도 관점에서는 가장 균형적인 상태를 만들었지만, 해당 발전기들의 위치와 계통 임피던스 구조 때문에 `LINE-134-193` 혼잡은 오히려 더 커졌다.

### 발전기 출력 분담 예시

#### P_MAX

| 발전기 | ΔP | 사고 후 headroom |
| --- | ---: | ---: |
| `GEN-82#5` | +19.426 MW | 2.314 MW |
| `GEN-82#6` | +19.426 MW | 2.314 MW |
| `GEN-175#4` | +19.426 MW | 3.381 MW |
| `GEN-175#5` | +19.426 MW | 3.381 MW |
| `GEN-75#1` | +14.570 MW | 47.230 MW |
| `GEN-59#8` | +14.570 MW | 14.095 MW |
| `GEN-59#9` | +14.570 MW | 14.095 MW |
| `GEN-71` | +14.431 MW | 40.131 MW |

#### P

| 발전기 | ΔP | 사고 후 headroom |
| --- | ---: | ---: |
| `GEN-82#5` | +20.241 MW | 1.499 MW |
| `GEN-82#6` | +20.241 MW | 1.499 MW |
| `GEN-175#4` | +20.225 MW | 2.581 MW |
| `GEN-175#5` | +20.225 MW | 2.581 MW |
| `GEN-59#8` | +14.999 MW | 13.665 MW |
| `GEN-59#9` | +14.999 MW | 13.665 MW |
| `GEN-42` | +14.630 MW | 14.607 MW |
| `GEN-53#11` | +14.600 MW | 11.226 MW |

#### REMAINING_MARGIN

| 발전기 | ΔP | 사고 후 headroom |
| --- | ---: | ---: |
| `GEN-192` | +58.278 MW | 173.310 MW |
| `GEN-193#6` | +44.756 MW | 133.097 MW |
| `GEN-193#3` | +35.662 MW | 106.054 MW |
| `GEN-193` | +27.621 MW | 82.141 MW |
| `GEN-193#0` | +27.621 MW | 82.141 MW |
| `GEN-193#1` | +27.621 MW | 82.141 MW |
| `GEN-193#2` | +27.621 MW | 82.141 MW |
| `GEN-193#4` | +27.621 MW | 82.141 MW |

## 분석

### 1. P_MAX와 P는 대표 사고에서 거의 같은 결과를 만들었다

두 방식 모두 99대 생존 발전기가 balancing에 참여하였고, `LINE-134-193` 부하율 차이는 0.282%p, 사고 후 계통 손실 차이는 0.152 MW에 그쳤다.

따라서 이 대표 사고만 보면 `P_MAX`와 `P` 사이의 차이는 Security 판정 자체를 바꿀 정도로 크지 않았다.

다만 발전기 출력 분담은 동일하지 않으며, `P`에서는 이미 높은 출력을 내고 있던 발전기들의 ΔP가 조금 더 커져 최소 headroom이 `P_MAX`보다 작게 나타났다.

### 2. REMAINING_MARGIN은 작은 headroom 발전기의 부담을 실제로 줄였다

가설대로 `REMAINING_MARGIN`에서는 여유가 큰 발전기가 더 많은 mismatch를 부담하였다.

그 결과:

- 최소 headroom이 9.704 MW까지 증가
- 하위 10% headroom이 11.728 MW까지 증가
- Pmax 5 MW 이내 발전기가 25대에서 0대로 감소

하였다.

따라서 발전기 운전 여유 관점에서는 `REMAINING_MARGIN`의 효과가 명확하였다.

### 3. 발전기 여유도 개선이 혼잡 선로 개선을 의미하지는 않는다

`REMAINING_MARGIN`에서 출력 증가량 상위 발전기는 `GEN-192`, `GEN-193*` 계열에 집중되었다.

이 배분은 발전기 자체의 남은 출력 여유를 활용하는 데는 유리했지만, 전력 주입 위치가 달라지면서 `LINE-134-193`을 통과하는 AC 조류는 오히려 증가하였다.

결과적으로 `LINE-134-193` 부하율은:

- P_MAX: 105.604%
- P: 105.322%
- REMAINING_MARGIN: 110.432%

로 나타났다.

즉 **발전기 headroom을 기준으로 좋은 balancing과 특정 송전 혼잡을 완화하는 balancing은 서로 다른 목적**이다.

이 결과는 후속 Sensitivity/Redispatch가 필요한 이유와 직접 연결된다. 혼잡을 완화하려면 단순히 남는 발전 여유만 볼 것이 아니라, 각 발전기의 출력 변화가 혼잡 선로 조류에 미치는 민감도와 실제 AC 재계산 결과를 함께 고려해야 한다.

### 4. 낮은 과부하 결과를 최적 운전이라고 해석할 수 없다

세 방식 중 `PROPORTIONAL_TO_GENERATION_P`가 `LINE-134-193` 부하율 105.322%로 가장 낮았다.

그러나 이를 "P 방식이 최적"이라고 결론 내리면 안 된다.

Balance Type은 발전기 사고 후 유효전력 mismatch를 계산상 어떻게 분배할지 정하는 **Load Flow balancing assumption**이다. 비용, 운전 우선순위, 예비력, 램프율, 발전기 제약, 혼잡 해소 목적 등을 고려한 실제 운영자 Redispatch가 아니다.

따라서 이번 결과는 다음과 같이 표현하는 것이 적절하다.

> 동일한 발전기 사고에서도 Distributed Slack의 Balance Type에 따라 발전기별 보상 분포와 계통 조류가 달라질 수 있으며, 특정 Balance Type에서 낮은 과부하가 나타났다고 해서 최적 제어를 의미하지는 않는다.

### 5. 후속 Redispatch PoC의 대표 기준은 P_MAX를 유지한다

후속 Sensitivity → Redispatch → AC/Security 재검증 workflow의 기본 Balance Type은 `PROPORTIONAL_TO_GENERATION_P_MAX`를 유지하는 것이 적절하다.

이유는 다음과 같다.

1. 기존 Single/Distributed Full Batch 및 `GEN-124#1` 상세 분석과 연속성이 있다.
2. 기존 코드의 Distributed Slack 기본값과 동일하다.
3. 대표 사고에서 `P`와 결과 차이가 작아, 이번 결과만으로 baseline을 변경할 근거가 충분하지 않다.
4. `REMAINING_MARGIN`은 headroom 보호에는 의미가 있지만, 대표 사고에서는 혼잡 선로 부하율을 크게 악화시켰다.

따라서 역할을 다음처럼 구분한다.

- **P_MAX**: 후속 Redispatch PoC의 대표 baseline
- **P**: baseline sensitivity check용 대안
- **REMAINING_MARGIN**: headroom 중심 balancing 가정의 robustness scenario

## AI EMS 관점의 의미

이번 실험은 AI EMS에서 계통해석 결과를 사용할 때 `distributed_slack=True`만 저장해서는 충분하지 않다는 점을 보여준다.

동일한 사고, 동일한 데이터, 동일한 AC 해석 엔진에서도 `balance_type`에 따라:

- 발전기별 출력 변화
- 발전기 headroom
- 계통 손실
- 특정 선로 조류
- 과부하 크기

가 달라질 수 있다.

따라서 Agent가 Security Analysis 결과를 해석하거나 위험 사고를 순위화할 때 다음 provenance를 함께 관리해야 한다.

- 입력 계통 데이터 버전
- 사고 ID
- 해석 엔진 및 버전
- `distributed_slack`
- `balance_type`
- Load Flow / Security 수렴 상태
- 제어 전/후 여부

또한 AI가 제어 후보를 생성할 때 발전기 여유도만으로 후보를 평가해서는 안 된다. 발전기 위치와 계통 민감도를 고려해 후보를 선택하고, 최종 효과는 실제 AC Load Flow 또는 Security Analysis로 재검증해야 한다.

## 다음 액션

- [x] PyPowSyBl/OpenLoadFlow Balance Type preflight
- [x] KPG-193 participation factor 데이터 여부 확인
- [x] GEN-124#1 P_MAX / P / REMAINING_MARGIN 비교
- [x] 발전기별 ΔP / headroom 비교
- [x] LINE-134-193 조류 및 계통 손실 비교
- [x] 후속 PoC 대표 Balance Type을 P_MAX로 선정
- [ ] 생성된 `results/kpg193_generator_balance_type_gen124_1/` 결과 파일 커밋
- [ ] Generator N-1 Full Batch에서 Balance Type에 따른 사고 순위/위반 여부 민감도 비교
- [ ] Balance Type 차이가 큰 대표 contingency 추출
- [ ] 대표 contingency에 대해 Sensitivity → Redispatch 후보 → 제어량 탐색 → AC/Security 재검증
