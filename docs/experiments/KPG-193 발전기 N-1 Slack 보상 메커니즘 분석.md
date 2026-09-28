# KPG-193 발전기 N-1 Slack 보상 메커니즘 분석

# \[KPG-193 발전기 N-1 Slack 보상 메커니즘 분석\] — 2026-09-28

## 가설

앞선 발전기 N-1 Full Batch 실험에서 동일한 100개 발전기 사고를 분석했음에도, Single slack과 Distributed slack 설정에 따라 제약 위반 사고 수가 **65건과 24건**, 최대 선로 부하율이 **109.178%와 105.603%**로 다르게 나타났다.

이 차이는 단순한 계산 옵션의 차이가 아니라, 발전기 탈락으로 발생한 유효전력 부족분을 **어디에서, 어떤 방식으로 보상하는지에 따라 계통 조류와 손실이 달라지기 때문일 것**이라고 가정하였다.

따라서 Full Batch에서 최대 부하율을 보인 대표 사고 `GEN-124#1`을 대상으로 Single/Distributed slack의 실제 balancing 결과를 비교하여 다음을 확인한다.

- 탈락 발전량이 어떻게 보상되는가
- Distributed slack에서 어떤 발전기들이 얼마만큼 출력을 증가시키는가
- 발전기 출력 증가량이 `Pmax` 비례 분담 설정과 일치하는가
- 보상 방식 차이가 계통 손실과 `LINE-134-193` 과부하에 어떻게 연결되는가

## 설정

- **데이터셋/버전**
  - KPG-193 v2.0
  - `KPG193_ver2_0_powsybl_full.mat`
- **모델/방법**
  - PyPowSyBl AC Load Flow
  - 대표 사고: `GEN-124#1` 단독 탈락
  - 사고 전 Base와 사고 후 상태를 각각 계산하여 발전기 출력 및 전력수지 비교
  - 발전기 실제 출력은 PyPowSyBl terminal convention을 고려하여 `-p`를 발전량으로 해석
  - 주요 관찰 선로: `LINE-134-193`
  - 선로 피상전력은 양단의 `sqrt(P²+Q²)` 중 큰 값을 사용
  - 한계값: `2172 MVA`
- **Slack 설정**

| 항목  | Single | Distributed |
| --- | --- | --- |
| `distributed_slack` | `False` | `True` |
| `balance_type` | 해당 없음 | `PROPORTIONAL_TO_GENERATION_P_MAX` |
| 분석 목적 | Slack mismatch 확인 | 발전기별 실제 출력 분담 확인 |

- **주요 비교 지표**
  - `GEN-124#1` 사고 전 발전량
  - `distributed_active_power`
  - Slack bus / `active_power_mismatch`
  - 생존 발전기별 ΔP
  - 발전기 `Pmax` 및 사고 후 headroom
  - 총 발전량 / 총 부하
  - 계통 유효전력 손실
  - AC 선로 손실 합계
  - `LINE-134-193` 피상전력 및 부하율
- **환경(하드웨어/코드 커밋 해시)**
  - Python 3.11.16
  - PyPowSyBl 1.16.1
  - 로컬 WSL Python 가상환경
  - 분석 코드: `scripts/analyze_generator_compensation.py`
  - 분석 스크립트 커밋: `f3debe7` **—** `feat: 새로운 발전기 보상 분석 기능 추가`
  - 관련 Generator-only / Distributed slack 기능은 `c6c9587` 이후 PR #1을 통해 `2b4ef34`로 병합됨
  - AC Load Flow에 parameter 전달 기능은 `17dcd2d`에서 추가됨

## 결과

| 지표  | Single slack | Distributed slack | 비고  |
| --- | --- | --- | --- |
| 탈락 발전기 | `GEN-124#1` | `GEN-124#1` | 동일 사고 |
| 사고 전 발전량 | 984.329 MW | 984.329 MW | 동일 Base |
| Base 계통 손실 | 832.011 MW | 832.011 MW | 동일 Base |
| Distributed active power | 0.000 MW | **969.180 MW** | Distributed에서 발전기 분담 |
| Post slack mismatch | **993.514 MW** | **0.433 MW** | Reference bus `VL-190_0` |
| 사고 후 계통 손실 | **841.213 MW** | **817.311 MW** |     |
| Base 대비 손실 변화 | **+9.202 MW** | **\-14.700 MW** |     |
| AC line loss | 841.232 MW | 817.316 MW | 전력수지 결과와 거의 일치 |
| `LINE-134-193` 최대 S | **2371.341 MVA** | **2293.721 MVA** |     |
| `LINE-134-193` 부하율 | **109.178%** | **105.604%** | 차이 3.574%p |

### Distributed slack 발전기 출력 분담 예시

| 발전기 | Pmax | 사고 전 출력 | 출력 증가 ΔP | 사고 후 출력 | 사고 후 여유 |
| --- | --- | --- | --- | --- | --- |
| `GEN-175#4` | 1400 MW | 1377.193 MW | **+19.426 MW** | 1396.619 MW | 3.381 MW |
| `GEN-175#5` | 1400 MW | 1377.193 MW | **+19.426 MW** | 1396.619 MW | 3.381 MW |
| `GEN-82#5` | 1400 MW | 1378.260 MW | **+19.426 MW** | 1397.686 MW | 2.314 MW |
| `GEN-82#6` | 1400 MW | 1378.260 MW | **+19.426 MW** | 1397.686 MW | 2.314 MW |
| `GEN-75#1` | 1050 MW | 988.200 MW | **+14.570 MW** | 1002.770 MW | 47.230 MW |
| `GEN-71` | 1040 MW | 985.438 MW | **+14.431 MW** | 999.869 MW | 40.131 MW |

대표 발전기의 `ΔP / Pmax`를 비교하면 약 **1.3876%**로 동일하게 나타나, 설정한 `PROPORTIONAL_TO_GENERATION_P_MAX` 방식이 실제 발전기 출력 변화에 반영되었음을 확인하였다.

## 분석

결과는 가설을 지지하였다.

### 1\. Single slack의 993.514 MW는 실제 발전기 Redispatch 결과가 아니다

Single slack에서는 발전기별 계산 출력이 사고 전과 동일하게 유지되었고,

- `distributed_active_power = 0 MW`
- Slack bus = `VL-190_0`
- `active_power_mismatch = 993.514 MW`

로 나타났다.

따라서 Single 결과를 "`VL-190_0`의 발전기들이 993.514 MW를 실제 증발하였다"고 해석해서는 안 된다.

이번 계산에서는 발전기 탈락에 따른 유효전력 부족이 **Slack bus의 mismatch 형태로 처리된 계산 상태**로 해석한다.

### 2\. Single의 balancing 규모는 탈락량과 손실 증가로 설명된다

Base 대비 사고 후 계통 손실은

- `832.011 → 841.213 MW`
- **+9.202 MW**

증가하였다.

따라서 필요한 전력수지 보상량은

`984.329 MW + 9.202 MW ≈ 993.531 MW`

이다.

Base의 residual mismatch까지 고려하면 실제 Slack mismatch 변화와 거의 일치하였다.

즉 Single 결과의 약 993.5 MW는 **탈락 발전량과 사고 후 증가한 계통손실을 함께 보상하기 위해 필요한 유효전력 불일치 규모**로 설명할 수 있다.

### 3\. Distributed slack에서는 실제 발전기 출력이 Pmax 비례로 증가하였다

Distributed에서는

- `distributed_active_power = 969.180 MW`
- Post residual mismatch = `0.433 MW`

로 나타났다.

생존 발전기별 실제 출력도 증가하였으며, 대표 발전기들의 출력 증가 비율은

`ΔP / Pmax ≈ 1.3876%`

로 거의 동일하였다.

따라서 이번 조건에서는 `PROPORTIONAL_TO_GENERATION_P_MAX` 설정에 따라 **다수 발전기가 Pmax에 비례하여 탈락분을 분담한 것을 실제 계산 출력으로 확인하였다.**

### 4\. Distributed에서 필요한 balancing 총량이 작은 이유도 손실로 설명된다

Distributed 사고 후 손실은

- `832.011 → 817.311 MW`
- **\-14.700 MW**

로 감소하였다.

따라서 필요한 추가 발전량은

`984.329 MW - 14.700 MW ≈ 969.629 MW`

이다.

이는 distributed active power와 residual mismatch 변화를 합친 값과 거의 일치한다.

즉 Single/Distributed에서 **보상 위치가 달라짐에 따라 조류 분포와 계통손실도 달라졌고, 필요한 balancing 총량 자체도 달라졌다.**

### 5\. 보상 방식의 차이는 `LINE-134-193` 조류 차이로 이어졌다

동일한 `GEN-124#1` 탈락에 대해:

- Single: **2371.341 MVA / 109.178%**
- Distributed: **2293.721 MVA / 105.604%**

로 나타났다.

Distributed에서는 Single보다

- 피상전력 약 **77.620 MVA 감소**
- 부하율 약 **3.574%p 감소**

하였다.

따라서 앞선 Full Batch에서 관측한 Single/Distributed 최대 부하율 차이는 단순한 출력 형식상의 차이가 아니라, **유효전력 balancing 방식에 따른 실제 AC 조류 재분배 차이**와 연결되는 것으로 확인하였다.

다만 Distributed에서도 `LINE-134-193`은 여전히 100%를 초과하므로, 이를 과부하를 해소하는 실제 corrective redispatch로 해석할 수는 없다.

---

## AI EMS 관점의 의미

이번 실험은 발전기 N-1 결과를 AI EMS에서 사용할 때 **Contingency ID와 위반 결과만 전달해서는 충분하지 않음**을 보여준다.

동일한 발전기 사고라도 balancing assumption에 따라:

- 위반 여부
- 최대 부하율
- 계통손실
- 발전기별 출력
- 전력수지 처리 방식

이 달라질 수 있다.

따라서 Agent가 N-1 결과를 해석하거나 위험 사고를 순위화할 경우 최소한 다음 분석 조건을 함께 관리해야 한다.

- `distributed_slack`
- `balance_type`
- Slack/reference bus
- 계산 엔진 및 버전
- 입력 데이터 버전
- 사고 ID
- 계산 수렴 상태

즉 AI EMS에서는 결과 숫자뿐 아니라 **어떤 물리해석 조건에서 생성된 결과인지 provenance를 함께 전달하는 구조**가 필요하다.

또한 Distributed slack은 계통해석을 위한 balancing assumption이며, 운영자의 실제 corrective redispatch를 의미하지 않는다. 실제 제어 후보는 발전기 출력한계, 운전조건 등을 고려해 별도로 생성하고 AC Power Flow 또는 Security Analysis로 재검증해야 한다.

## 다음 액션

- [x] GEN-124#1 사고 전후 발전기 출력 비교
- [x] Single/Distributed 실제 balancing 방식 확인
- [x] Distributed 발전기별 분담량 확인
- [x] 발전기 Pmax 및 출력 여유 확인
- [x] 사고 전후 계통손실 비교
- [x] LINE-134-193 조류 차이와 balancing 방식 연결
- [ ] 기존 KPG-193 발전기 N-1 Single/Distributed Slack 비교 문서에 본 상세 분석 링크 추가
- [ ] Full Batch N-1 결과를 Agent Tool로 연결
- [ ] Batch에서 선택된 위험 사고를 개별 상세 분석으로 연결하는 workflow 구현
- [ ] 필요 시 Generator contingency용 Sensitivity / Redispatch 연계 범위 검토