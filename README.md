# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 프로젝트.

이 프로젝트의 목적은 LLM이 전력계통 계산을 직접 수행하도록 하는 것이 아니라, 사용자의 자연어 요청을 해석하고 PyPowSyBl 기반 물리해석 Tool을 선택·호출한 뒤 구조화된 결과를 운영자 관점에서 설명하는 흐름을 검증하는 것이다.

핵심 원칙은 다음과 같다.

> **AI는 물리해석 엔진을 대체하지 않는다.**
> LLM은 자연어 의도 해석과 Tool orchestration을 담당하고, 계통 상태와 제어 효과는 Security Analysis, Sensitivity Analysis, AC Load Flow 등 물리해석 결과로 검증한다.

---

## Current Status

현재 다음 흐름까지 구현하고 동작을 확인하였다.

```text
User Natural Language Request
        ↓
Local LLM / LangGraph
(Intent + Tool Selection)
        ↓
PyPowSyBl Domain Workflow
        ↓
Security Analysis
(pre / post contingency comparison)
        ↓
Sensitivity Analysis
        ↓
Balanced Redispatch Candidate Generation
        ↓
Generator Feasibility Guardrail
        ↓
AC Power Flow Validation
        ↓
Whole-network Security Re-validation
(Operator Strategy)
        ↓
Candidate Ranking
        ↓
Structured Result
        ↓
Deterministic Formatter
        ↓
Operator-facing Response / Web UI
```

구현·검증된 주요 기능:

- KPG-193 기반 PyPowSyBl network load
- 계통 요약 / 선로 / 발전기 조회
- AC Load Flow
- Line N-1 Security Analysis
- pre-contingency / post-contingency 위반 비교
- 신규 / 잔존 / 해소 위반 구분
- 가장 심한 위반 선로 자동 선택
- Generator Sensitivity Analysis
- Sensitivity 기반 balanced redispatch 후보 생성
- 발전기 최소·최대 출력 기반 후보 사전 검증
- Redispatch 후 AC Power Flow 검증
- PyPowSyBl Operator Strategy 기반 whole-network Security 재검증
- 신규 위반 / 잔존 위반 / 해소 위반 비교
- 전체 계통 검증 결과를 포함한 후보 ranking
- LangGraph ToolNode 기반 Local LLM Tool Calling
- 고수준 `contingency_response_analysis` workflow
- 고위험 계통 결과의 deterministic formatter
- CLI 및 FastAPI Web UI
- KPG 계통 지도 / 사고·위반 선로 / 민감도 후보 시각화

현재 regression test 결과:

```text
13 passed
```

---

## Architecture

### 1. Domain Layer

LLM과 독립적인 Python / PyPowSyBl 계층이다.

주요 모듈:

```text
src/ai_ems/
├─ network.py
└─ tools/
   ├─ network_tools.py
   ├─ security_tools.py
   ├─ sensitivity_tools.py
   ├─ control_tools.py
   └─ workflow_tools.py
```

역할:

- `network.py`: 계통 load / 공통 AC Load Flow parameter
- `network_tools.py`: 계통 요약 / 선로 / 발전기 조회
- `security_tools.py`: Line contingency Security Analysis / pre-post 위반 비교 / 주요 위반 선택
- `sensitivity_tools.py`: 발전기 출력 변화에 대한 선로 유효전력 조류 민감도 계산
- `control_tools.py`: Redispatch 후보 생성 / 발전기 출력 제약 guardrail / AC 검증 / whole-network Operator Strategy Security validation
- `workflow_tools.py`: Security → Sensitivity → Candidate → AC / Security Validation 연결

Domain layer는 LLM 없이도 직접 호출할 수 있도록 유지한다.

### 2. Agent Layer

```text
src/ai_ems/agent/
├─ graph.py
├─ tools.py
└─ formatters.py
```

- `tools.py`: Domain function을 LLM-facing Tool schema로 노출
- `graph.py`: LangGraph agent / ToolNode routing / Ollama Local LLM Tool Calling
- `formatters.py`: 고수준 corrective-action workflow 결과를 deterministic하게 출력

현재 Agent Tool은 다음 8개이다.

```text
network_summary
line_list
line_detail
generator_list
line_contingency
generator_sensitivity
balanced_redispatch_validation
contingency_response_analysis
```

### 3. Corrective-action Workflow

사용자가 다음과 같이 요청하면:

```text
LINE-81-84 사고 발생 시 대응방안 찾아줘
```

Agent는 고수준 Tool인 `contingency_response_analysis`를 호출한다.

```text
Security Analysis
        ↓
사고 전 / 사고 후 위반 비교
        ↓
가장 심한 위반 선로 선택
        ↓
Sensitivity Analysis
        ↓
Balanced Redispatch 후보 생성
        ↓
발전기 출력범위 Guardrail
        ↓
후보별 AC 검증
        ↓
Whole-network Security 재검증
        ↓
후보 Ranking
```

현재 candidate ranking은 다음을 우선한다.

1. AC / Operator Strategy 수렴 여부
2. Redispatch로 인한 신규 위반 발생 여부
3. 제어 후 위반 설비 수
4. 목표 선로 개선량
5. 기존 Sensitivity candidate rank

이 결과는 **시험한 후보 중 최선의 결과**이며 OPF / SCED에 의한 최적 Redispatch를 의미하지 않는다.

### 4. Deterministic Response

작은 Local LLM이 수치 단위, 부호, `%`와 `%p`, MW와 MVA 등을 잘못 해석하는 문제를 줄이기 위해 고수준 corrective-action 결과는 두 번째 LLM inference를 거치지 않는다.

```text
User
 ↓
LLM: intent / Tool selection
 ↓
Domain Workflow
 ↓
Structured Result
 ↓
Python Deterministic Formatter
 ↓
Final Response
```

이를 통해 다음을 고정한다.

- MW / MVA 구분
- loading percent와 percentage point 구분
- 사고로 발생한 신규 위반과 Redispatch로 추가 발생한 신규 위반 구분
- 시험하지 않은 제어를 검증된 조치처럼 표현하지 않음
- tested candidate와 optimization 결과를 구분

---

## Pre / Post / Control Violation Comparison

Security Analysis는 사고 전과 사고 후 위반을 구분한다.

```text
Pre-contingency
        ↓ contingency
Post-contingency
        ↓ corrective action
Post-control / Operator Strategy
```

`security_tools.py`는 사고 전과 사고 후의 위반을 다음으로 분류한다.

```text
new       : 사고 전에는 없고 사고 후 새로 발생
remaining : 사고 전과 사고 후 모두 존재
resolved  : 사고 전에는 있었으나 사고 후 사라짐
```

`remaining` 위반은 `violation_amount` 변화를 기준으로 다음 trend를 추가로 구분한다.

```text
worsened
improved
unchanged
```

### Verified example: LINE-81-84

`LINE-81-84` 탈락 시 정상상태에는 위반이 없었으나 사고 후 다음 두 개의 신규 APPARENT_POWER 위반이 발생하였다.

- `LINE-16-28`: limit `4344 MVA`, post-contingency 약 `4465.24 MVA`, loading 약 `102.79 %`
- `LINE-134-193`: limit `2172 MVA`, post-contingency 약 `2194.38 MVA`, loading 약 `101.03 %`

즉 사고 위치와 지리적으로 떨어진 설비에서도 조류 재분배에 의해 신규 제약 위반이 발생할 수 있으며, corrective action은 목표 선로뿐 아니라 전체 계통에서 다시 검증해야 한다.

---

## Verified Corrective-action Scenarios

### LINE-16-28 outage

사고 후 `LINE-16-22`에 APPARENT_POWER 위반이 발생하는 대표 사례이다.

- outage: `LINE-16-28`
- selected violated line: `LINE-16-22`
- limit: 약 `1906 MVA`
- post-contingency apparent power: 약 `1967.28 MVA`
- loading: 약 `103.21 %`

Sensitivity 기반 대표 Redispatch:

```text
GEN-19#0  +10 MW
GEN-36#0  -10 MW
```

검증 결과:

- Sensitivity predicted branch active-power change: 약 `+7.382 MW`
- AC result apparent power: 약 `1967.28 → 1959.64 MVA`
- loading: 약 `103.21 % → 102.81 %`
- target overload는 완화되지만 여전히 잔존
- whole-network Security 재검증에서 추가 신규 위반 없음

Sensitivity는 **유효전력 조류 변화(MW)**를 예측하며, AC validation의 개선량은 **피상전력(MVA)**일 수 있으므로 서로 같은 물리량으로 비교하지 않는다.

### LINE-183-190 outage

- most severe violation: `LINE-176-190`
- tested best candidate: `GEN-10#0 +10 MW / GEN-190 -10 MW`
- apparent power: 약 `2476.13 → 2469.21 MVA`
- loading: 약 `114.00 % → 113.68 %`
- 신규 whole-network violation 없음
- post-contingency overload는 잔존

이 사례는 영향도가 높은 후보를 찾는 것과 실제 제약 해소는 별개의 문제임을 보여준다.

---

## Web UI

FastAPI + browser UI를 제공한다.

주요 기능:

- KPG-193 계통 지도 표시
- Bus / Line 클릭 정보 표시
- 지도에서 선택한 선로 ID를 Security Analysis 입력에 반영
- Security Analysis 실행
- 사고 선로 / 위반 선로 highlight
- Sensitivity candidate marker 표시
- Agent chat
- Agent Tool result 기반 UI update

실행:

```bash
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

브라우저:

```text
http://localhost:8000
```

---

## Local LLM

Ollama를 사용한다.

예시 `.env`:

```dotenv
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_LLM_BASE_URL=http://172.19.32.1:11434
```

프로젝트는 `.env`를 자동으로 읽지 않으므로 필요한 경우 shell에서 직접 export한다.

```bash
source .venv/bin/activate
set -a
source .env
set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

Agent probe:

```bash
python tests/agent_probe.py
```

Local LLM의 크기와 품질은 자연어 설명과 Tool selection에 영향을 줄 수 있다. 따라서 중요 계통 판단과 결과 해석은 가능한 한 Domain Tool과 deterministic formatter에서 고정한다.

---

## Installation

Python 3.11 기준.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

CLI:

```bash
python app.py
```

Web UI:

```bash
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

Regression test:

```bash
pytest -q
```

현재 확인 결과:

```text
13 passed
```

---

## Project Structure

```text
ai-ems-agent/
├─ app.py
├─ web_app.py
├─ ui/
│  └─ index.html
├─ src/
│  └─ ai_ems/
│     ├─ network.py
│     ├─ agent/
│     │  ├─ graph.py
│     │  ├─ tools.py
│     │  └─ formatters.py
│     └─ tools/
│        ├─ network_tools.py
│        ├─ security_tools.py
│        ├─ sensitivity_tools.py
│        ├─ control_tools.py
│        └─ workflow_tools.py
├─ data/
│  └─ README.md
├─ tests/
├─ requirements.txt
├─ Dockerfile
└─ README.md
```

---

## KPG-193 Test System / Data Policy

테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

- Upstream: https://github.com/agm-center/kpg-testgrid
- Documentation: https://agm.kentech.ac.kr/docs/kpg-test-system/
- Paper: Geonho Song and Jip Kim, *KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies*, arXiv:2411.14756, 2024.

이 repository에는 실제 실습용 KPG MATPOWER case와 위치 CSV를 포함하지 않는다.

Local-only files:

```text
data/KPG193_ver2_0_pypowsybl.mat
data/bus_location.csv
```

두 파일은 public GitHub에 업로드하지 않는다.

`KPG193_ver2_0_pypowsybl.mat`은 upstream 원본을 그대로 복사한 파일이 아니라 PyPowSyBl import를 위해 변환한 derived MATPOWER case이다.

주요 변환 내용:

- base voltage 정보 유지
- PyPowSyBl import 시 base voltage 사용
- 원본 `dcline` 제외
- fixed HVDC transfer를 양단 dummy generator로 표현

세부 provenance는 `data/README.md`에 기록한다.

---

## Limitations

현재 구현은 연구 / PoC 목적이다.

- Redispatch 후보 생성은 OPF / SCED optimization이 아니다.
- 현재 balanced `+ΔMW / -ΔMW` generator pair를 평가한다.
- `best_tested_candidate`는 시험한 후보 중 최선이며 전역 최적해를 의미하지 않는다.
- Sensitivity Analysis는 local linearization이며 실제 제어 효과는 AC 해석으로 재검증한다.
- Whole-network validation은 정적 Security Analysis 기반이며 transient / dynamic stability를 의미하지 않는다.
- 현재 dynamic simulation, frequency response, rotor angle stability는 다루지 않는다.
- Local LLM이 생성한 자유형 문장은 신뢰 가능한 물리계산 결과 자체로 취급하지 않는다.

---

## Next Step

기능 개발은 현재 상태에서 일단 freeze하고, 다른 AI-EMS 작업과의 통합을 위해 다음을 우선한다.

```text
Public module boundary 확정
        ↓
Input / Output schema 명세
        ↓
MODULE_SPEC / API 문서 작성
        ↓
Regression test 정리
        ↓
다른 모듈과 1차 통합
```

통합 시 핵심 entry point 후보는 다음과 같다.

```python
run_line_contingency(...)
rank_generator_sensitivities(...)
validate_balanced_redispatch(...)
analyze_contingency_response(...)
```

특히 `analyze_contingency_response()`를 고수준 통합 interface로 사용할 수 있도록 schema를 정리하는 것이 다음 단계의 우선 과제이다.
