# KPG-193 PyPowSyBl Adapter 명세

## 1. 목적

이 문서는 `KPG193_ver2_0_powsybl_full.mat`과 `kpg_powsybl_adapter.py`를 함께 전달하여 PyPowSyBl에서 KPG-193 v2.0 계통을 사용할 때 필요한 구조, 사용 방법, 전제조건 및 검증 결과를 정리한다.

본 Adapter의 목적은 KPG MATPOWER 원본 계통을 별도 dummy generator 모델로 치환하지 않고, PyPowSyBl에서 실제 IIDM `HvdcLine` / `VscConverterStation` 객체로 사용할 수 있도록 연결하는 것이다.

핵심 구조는 다음과 같다.

```text
KPG193_ver2_0_powsybl_full.mat
        │
        ├─ AC / 발전기 / 부하 / 비용·운영 메타데이터 유지
        │
        └─ dcline 정보
             ↓
kpg_powsybl_adapter.py
  1. MAT 파일 읽기
  2. 임시 MAT에서 dcline / dclinecost만 비움
  3. AC 계통을 PowSyBl MATPOWER importer로 로드
  4. 원본 dcline 정보를 VSC + HVDC로 재구성
             ↓
        PyPowSyBl Network
```

---

## 2. 전달 파일

권장 전달 파일은 다음 2개이다.

```text
KPG193_ver2_0_powsybl_full.mat
kpg_powsybl_adapter.py
```

AI-EMS Agent 저장소 기준 Adapter 위치:

```text
src/ai_ems/utils/kpg_powsybl_adapter.py
```

권장 폴더 예시는 다음과 같다.

```text
project/
├─ data/
│  └─ KPG193_ver2_0_powsybl_full.mat
└─ src/
   └─ kpg_powsybl_adapter.py
```

---

## 3. full.mat 데이터 보존 범위

`KPG193_ver2_0_powsybl_full.mat`은 PyPowSyBl 전용으로 필요한 최소 필드만 남긴 축약 MAT가 아니라, KPG-193의 물리계통 정보와 운영/최적화용 메타데이터를 함께 유지하는 용도로 사용한다.

현재 사용 중인 KPG MATPOWER 구조에서 확인된 주요 `mpc` 필드는 다음과 같다.

```text
version
baseMVA
bus
gen
branch
areas
gencost
genthermal
dcline
dclinecost
```

따라서 다음과 같이 역할을 구분할 수 있다.

- `bus`, `gen`, `branch`: AC 계통 구성 및 조류계산 입력
- `gencost`: 발전기 비용함수 등 최적화 입력
- `genthermal`: 발전기 기동/정지 및 ramp 등 thermal/UC 관련 제약 정보
- `dcline`: HVDC 운전점 및 제한 정보
- `dclinecost`: DC line 관련 비용 정보
- `areas`: MATPOWER area 정보

### 중요

Adapter가 PyPowSyBl에 계통을 로드할 때 **원본 full.mat 자체를 수정하지 않는다.**

다만 PowSyBl MATPOWER importer의 HVDC 처리 제약을 우회하기 위해 메모리에서 임시 MAT 파일을 만들며, 이 임시 파일에서만 다음 두 필드를 비운다.

```text
dcline
dclinecost
```

그 외 `mpc` 필드는 임시 MAT에 그대로 복사된다.

이후 Adapter는 full.mat에서 읽어 둔 `dcline` 값을 이용해 PyPowSyBl public API로 실제 VSC converter station과 HVDC line을 다시 생성한다.

즉,

```text
full.mat
  ├─ gencost / genthermal / 기타 원본 필드 유지
  └─ dcline 유지

PyPowSyBl import용 temporary MAT
  ├─ 기존 AC/발전기 데이터 유지
  └─ dcline / dclinecost만 일시적으로 제거
```

구조이다.

`gencost`, `genthermal`, `dclinecost`는 MAT 파일에 보존되어 다른 Simulator, UC/SCED/OPF 코드에서 사용할 수 있지만, PyPowSyBl AC Load Flow / Security / Sensitivity가 이 비용·UC 메타데이터를 직접 사용하는 것은 아니다.

또한 현재 검증은 주요 필드 존재와 물리계통 결과 보존을 중심으로 수행하였다. 따라서 문서상 표현은 "원본과 byte-level 완전 동일"보다는 **"원본 주요 물리계통 및 운영/최적화 필드를 유지하며, dcline은 PyPowSyBl 호환을 위해 의미를 보존하는 형태로 처리한다"**가 정확하다.

---

## 4. Adapter가 필요한 이유

KPG-193 v2.0에는 동일한 두 버스 사이에 병렬 MATPOWER `dcline` 2개가 존재한다.

기본 PowSyBl MATPOWER importer를 그대로 사용할 경우 다음 문제가 있었다.

### 4.1 음수 PF 방향 표현

KPG MATPOWER `dcline`은 signed `PF` 값으로 전력 흐름 방향을 표현할 수 있다.

반면 IIDM `HvdcLine`은 `target_p`를 음수로 두기보다,

```text
target_p = 양의 크기
converters_mode = 방향
```

으로 표현한다.

Adapter는 `PF`의 부호를 읽어 다음과 같이 변환한다.

```python
if PF >= 0:
    converters_mode = "SIDE_1_RECTIFIER_SIDE_2_INVERTER"
else:
    converters_mode = "SIDE_1_INVERTER_SIDE_2_RECTIFIER"

target_p = abs(PF)
```

### 4.2 병렬 HVDC ID 충돌

PowSyBl MATPOWER importer는 converter/HVDC ID를 endpoint bus 조합으로 생성한다.

동일한 bus pair를 사용하는 병렬 `dcline`이 있으면 ID가 중복될 수 있다.

Adapter는 각 `dcline` row에 순번을 붙여 명시적으로 고유 ID를 생성한다.

예:

```text
KPG-HVDC-53-51-1
KPG-HVDC-53-51-2
```

각 HVDC에는 두 개의 VSC가 생성된다.

```text
KPG-HVDC-53-51-1-CS1
KPG-HVDC-53-51-1-CS2
KPG-HVDC-53-51-2-CS1
KPG-HVDC-53-51-2-CS2
```

---

## 5. 지원 환경

현재 검증 환경:

```text
Python       3.11
PyPowSyBl    1.16.1
NumPy        2.x
SciPy        1.16.x
```

최소 설치 예:

```bash
python -m pip install pypowsybl==1.16.1 scipy numpy
```

AI-EMS Agent 전체 환경을 사용하는 경우:

```bash
python -m pip install -r requirements.txt
```

---

## 6. 가장 간단한 사용 방법

### 6.1 Adapter 직접 사용

```python
from kpg_powsybl_adapter import load_kpg_network

network = load_kpg_network(
    "data/KPG193_ver2_0_powsybl_full.mat"
)
```

AI-EMS Agent 패키지 내부에서는:

```python
from ai_ems.utils.kpg_powsybl_adapter import load_kpg_network

network = load_kpg_network(
    "data/KPG193_ver2_0_powsybl_full.mat"
)
```

`ignore_base_voltage=False`가 기본이며 KPG의 154/345/765 kV base voltage 정보를 유지한다.

```python
network = load_kpg_network(
    "data/KPG193_ver2_0_powsybl_full.mat",
    ignore_base_voltage=False,
)
```

---

## 7. AI-EMS Agent 공통 loader 사용

AI-EMS Agent에서는 직접 Adapter를 호출하기보다 공통 `load_network()` 사용을 권장한다.

```python
from ai_ems import load_network
from ai_ems.config import CASE_FILE

network = load_network(CASE_FILE)
```

`.env` 또는 환경변수:

```text
AI_EMS_CASE_FILE=data/KPG193_ver2_0_powsybl_full.mat
```

현재 `network.py`는 해당 full.mat 파일일 때 KPG Adapter를 호출하고, 그 외 일반 MATPOWER 파일에는 기본 `pp.network.load()`를 사용한다.

---

## 8. 기본 동작 확인

```python
import pypowsybl as pp
from ai_ems import load_network
from ai_ems.config import CASE_FILE

network = load_network(CASE_FILE)

print("Bus  :", len(network.get_buses()))
print("Gen  :", len(network.get_generators()))
print("Line :", len(network.get_lines()))
print("HVDC :", len(network.get_hvdc_lines()))

result = pp.loadflow.run_ac(network)
print(result)
```

현재 KPG-193 full.mat 기준 기대값:

```text
Bus  : 193
Gen  : 201
Line : 385
HVDC : 2
AC Load Flow : CONVERGED
```

VSC 확인:

```python
print(network.get_vsc_converter_stations(all_attributes=True))
```

기대 VSC 수:

```text
4
```

HVDC 확인:

```python
print(network.get_hvdc_lines(all_attributes=True))
```

현재 검증된 두 병렬 HVDC는 각각 약 `1496.223 MW`의 setpoint를 사용한다.

---

## 9. AC Load Flow 사용

```python
import pypowsybl as pp

result = pp.loadflow.run_ac(network)

for component in result:
    print(component.status)
```

현재 검증 결과:

```text
status         : CONVERGED
iteration      : 3
Vmin           : 0.9602167463 pu
Vmax           : 1.0346833465 pu
```

---

## 10. Security Analysis 사용

예: `LINE-16-28` 탈락 후 `LINE-16-22` 확인

```python
from ai_ems.tools.security_tools import run_line_contingency

result = run_line_contingency(
    network,
    outage_line_id="LINE-16-28",
    monitored_line_ids=["LINE-16-22"],
)

print(result["post_status"])
print(result["violated_equipment"])
```

현재 회귀검증 결과:

```text
Post-contingency status : CONVERGED
Violated equipment      : LINE-16-22
RateC                    : 1906 MVA
Post-contingency value   : 1967.276 MVA
Loading                  : 103.2149 %
```

---

## 11. Sensitivity Analysis 사용

```python
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities

result = rank_generator_sensitivities(
    network,
    outage_line_id="LINE-16-28",
    monitored_line_id="LINE-16-22",
    top_n=10,
)

for candidate in result["candidates"]:
    print(candidate)
```

대표 결과:

```text
GEN-19#0  +0.4210858
GEN-10#0  +0.3637581
```

Sensitivity는 제어 후보의 영향도를 판단하기 위한 선형 민감도이며, 최적 Redispatch 자체를 의미하지 않는다.

---

## 12. Redispatch / 전체 Workflow 사용

```python
from ai_ems.config import CASE_FILE
from ai_ems.tools.workflow_tools import analyze_contingency_response

result = analyze_contingency_response(
    CASE_FILE,
    "LINE-16-28",
    delta_mw=10.0,
    top_n=3,
)

print(result["best_tested_candidate"])
```

대표 검증 결과:

```text
GEN-19#0 +10 MW
GEN-36#0 -10 MW

LINE-16-22
1967.276 MVA -> 1959.637 MVA
103.2149 %   -> 102.8141 %

Whole-network Security re-validation : CONVERGED
New violation                         : False
Violation remaining                   : True
```

이는 시험한 후보의 물리검증 결과이며 OPF/SCED 최적해를 의미하지 않는다.

---

## 13. Adapter 내부 처리 절차

`load_kpg_network()`의 처리 흐름은 다음과 같다.

### Step 1. full.mat 읽기

```python
source = scipy.io.loadmat(...)
```

### Step 2. `mpc.dcline` 확보

Adapter는 실제 HVDC 재생성을 위해 원본 dcline matrix를 별도로 읽어 둔다.

### Step 3. 임시 AC-only MAT 생성

원본 full.mat를 복사한 in-memory 구조에서:

```text
dcline     -> empty matrix
dclinecost -> empty matrix
```

로 바꾼 임시 MAT를 생성한다.

원본 파일은 수정하지 않는다.

### Step 4. PowSyBl MATPOWER importer 실행

```python
network = pp.network.load(
    temp_case,
    parameters={
        "matpower.import.ignore-base-voltage": "false"
    },
)
```

### Step 5. VSC / HVDC 재생성

각 dcline row에 대해:

```text
VSC 2개 생성
Reactive limits 적용
HVDC line 생성
Converters mode 설정
Active-power setpoint 설정
```

을 수행한다.

---

## 14. 현재 Adapter의 KPG 전제조건

본 Adapter는 범용 MATPOWER dcline converter가 아니라 현재 KPG-193 v2.0 데이터에 맞춘 Adapter이다.

현재 코드가 검사하는 주요 전제는 다음과 같다.

### 14.1 dcline 운전 상태

```text
BR_STATUS = 1
```

인 in-service line을 대상으로 한다.

### 14.2 DC loss

현재 KPG dcline은:

```text
LOSS0 = 0
LOSS1 = 0
```

을 전제로 한다.

다른 손실 모델을 가진 dcline은 현재 Adapter에서 오류 처리한다.

### 14.3 PF / PT

현재 zero-loss KPG 데이터에 대해 Adapter는:

```text
PF == PT
```

인지 확인한다.

### 14.4 Bus ID

PowSyBl MATPOWER importer가 생성하는 configured bus ID가 다음 형식이라는 전제를 사용한다.

```text
BUS-{bus_number}
```

예:

```text
BUS-51
BUS-53
```

### 14.5 Base voltage

KPG 계통의 base voltage를 보존하기 위해:

```text
matpower.import.ignore-base-voltage=false
```

를 사용한다.

---

## 15. 검증 결과

기존 PyPowSyBl 실습에서는 HVDC를 4개의 fixed dummy generator로 표현한 derived case를 사용하였다.

기존 모델:

```text
193 buses
205 generators
385 AC lines
0 IIDM HVDC
```

현재 모델:

```text
193 buses
201 physical generators
385 AC lines
2 IIDM HVDC
4 VSC converter stations
```

두 모델에 대해 AC Load Flow 후 전체 정상상태를 비교하였다.

### 15.1 전압

```text
NEW Vmin : 0.960216746314
OLD Vmin : 0.960216746313

NEW Vmax : 1.034683346527
OLD Vmax : 1.034683346530
```

HVDC 접속 bus 51 / 53의 전압 magnitude와 angle도 동일하게 재현되었다.

### 15.2 전체 385개 AC line flow

```text
max |delta P| ≈ 1.3e-7 MW
max |delta Q| ≈ 1.3e-6 Mvar
```

### 15.3 공통 발전기 201개

```text
max |delta P| ≈ 3.0e-9 MW
max |delta Q| ≈ 1.8e-6 Mvar
```

수치적으로 동일한 정상상태 해로 판단할 수 있는 수준이다.

### 15.4 대표 N-1 회귀검증

```text
Outage       : LINE-16-28
Violation    : LINE-16-22
Limit        : 1906 MVA
Post value   : 1967.276 MVA
Loading      : 103.2149 %
Status       : CONVERGED
```

기존 dummy-HVDC 모델에서 확인했던 대표 Security Analysis 결과가 실제 HVDC 모델에서도 재현되었다.

### 15.5 테스트

AI-EMS Agent 기준:

```text
pytest -q
20 passed
```

Smoke test에서도:

```text
193 Bus / 201 Gen / 385 Line / 2 HVDC
AC Load Flow CONVERGED
Representative N-1 CONVERGED
```

를 확인하였다.

---

## 16. 주의사항

1. 본 Adapter는 현재 KPG-193 v2.0의 dcline 구조를 대상으로 한다.
2. `gencost`, `genthermal`, `dclinecost` 등이 full.mat에 포함되어 있어도 PyPowSyBl Load Flow / Security / Sensitivity가 이 값을 직접 사용하는 것은 아니다.
3. UC, SCED, OPF 등에서는 full.mat의 비용/운영 제약 데이터를 별도로 읽어 사용할 수 있다.
4. Adapter는 원본 full.mat를 수정하지 않는다.
5. PyPowSyBl Network 내부에서는 `dclinecost`와 같은 MATPOWER 최적화 메타데이터가 IIDM core object로 그대로 노출되지 않을 수 있다.
6. 현재 AI-EMS Agent의 공통 `load_network()`는 파일명이 `KPG193_ver2_0_powsybl_full.mat`일 때 KPG Adapter를 사용한다. 파일명을 변경하여 사용할 경우 직접 `load_kpg_network()`를 호출하거나 loader 조건을 함께 변경해야 한다.
7. 본 검증은 full.mat의 물리계통 동등성과 주요 운영/최적화 필드 유지 구조를 확인한 것이다. 원본 MAT와 full.mat의 모든 byte/모든 nested value에 대한 완전 동일성 검증을 의미하지는 않는다.

---

## 17. 권장 사용 구분

```text
KPG193_ver2_0_powsybl_full.mat
        │
        ├─ Simulator / UC / SCED / OPF
        │    └─ gencost, genthermal 등 포함 원본형 데이터 활용
        │
        └─ PyPowSyBl
             └─ kpg_powsybl_adapter.py
                  ├─ AC Load Flow
                  ├─ Security Analysis
                  ├─ Sensitivity Analysis
                  └─ Redispatch / Operator Strategy validation
```

즉 하나의 full.mat를 공통 데이터 소스로 유지하고, PyPowSyBl 호환성 문제만 Adapter 계층에서 처리하는 방식을 권장한다.

---

## 18. 전달 시 최소 설명

다른 개발자에게 파일을 전달할 때는 아래 내용만 전달해도 된다.

> `KPG193_ver2_0_powsybl_full.mat`은 KPG-193의 AC 계통 및 비용/운영 관련 필드를 함께 유지하는 버전입니다. PyPowSyBl의 MATPOWER importer가 KPG의 병렬 dcline과 signed PF를 직접 처리하기 어려워 `kpg_powsybl_adapter.py`를 함께 사용합니다. Adapter는 원본 MAT를 수정하지 않고, import 단계에서 dcline만 제외한 뒤 실제 VSC/HVDC 객체를 재구성합니다. `load_kpg_network(path)`로 로드하면 193 buses, 201 generators, 385 AC lines, 2 HVDC 구조가 생성되며 AC Load Flow / Security / Sensitivity / Redispatch 검증까지 회귀테스트를 완료했습니다.
