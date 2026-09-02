# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 실습 프로젝트.

이 프로젝트의 목표는 LLM이 전력계통 계산을 직접 수행하는 것이 아니라, 사용자의 자연어 요청을 해석하고 필요한 PyPowSyBl 기반 물리해석 Tool을 호출하여 결과를 설명하는 구조를 확인하는 것이다.

```text
자연어 요청
   ↓
Local LLM (Ollama / Qwen2)
   ↓
LangGraph
   ↓
PyPowSyBl Domain Tools
   ├─ Network Query
   ├─ AC Load Flow
   ├─ Security Analysis
   └─ Sensitivity Analysis
   ↓
구조화된 물리해석 결과
   ↓
자연어 설명
```

핵심 관점은 **AI가 물리해석 엔진을 대체하지 않고, 분석 요청·Tool orchestration·결과 설명을 담당한다**는 것이다.

---

## Current Status

현재 다음 기능까지 구현 및 동작을 확인하였다.

- KPG-193 MATPOWER case 로드
- 계통 요약 / 선로 / 발전기 조회
- PyPowSyBl AC Load Flow
- PyPowSyBl Security Analysis
- PyPowSyBl Sensitivity Analysis
- Security 결과의 Agent용 요약 schema
- Sensitivity 기반 발전기 영향도 순위
- LangGraph ToolNode 기반 Tool 실행
- Ollama Local LLM Tool Calling
- Security → Sensitivity multi-turn 대화
- 누락된 monitored line을 Security 결과에서 자동 선택하는 fallback
- 대화형 CLI

현재 CLI에서 다음과 같은 형태의 대화가 가능하다.

```text
You> 선로 리스트 뽑아줘.
Agent> ...

You> LINE-16-28 선로가 탈락하면 어떤 문제가 생겨?
Agent> ... Security Analysis 실행 ...

You> 그럼 거기에 영향이 큰 발전기 5개는?
Agent> ... Sensitivity Analysis 실행 ...
```

다음 개발 단계는 **Web UI + KPG 계통 지도 시각화**이다.

---

## Project Structure

```text
ai-ems-agent/
├─ app.py                         # Interactive CLI entry point
├─ src/
│  └─ ai_ems/
│     ├─ __init__.py
│     ├─ network.py               # Network load / AC Load Flow
│     ├─ agent/
│     │  ├─ __init__.py
│     │  ├─ graph.py              # LangGraph + Ollama agent workflow
│     │  └─ tools.py              # LLM-facing Tool wrappers
│     └─ tools/
│        ├─ __init__.py
│        ├─ network_tools.py      # Network / line / generator queries
│        ├─ security_tools.py     # Security Analysis
│        └─ sensitivity_tools.py  # Sensitivity Analysis
│
├─ data/
│  ├─ README.md
│  └─ KPG193_ver2_0_pypowsybl.mat    # local only, gitignored
│
├─ tests/
│  ├─ smoke_test.py
│  ├─ test_network.py
│  ├─ test_kpg_tools.py
│  ├─ sensitivity_probe.py
│  ├─ kpg_sensitivity_probe.py
│  ├─ kpg_redispatch_probe.py
│  ├─ langgraph_tools_probe.py
│  ├─ ollama_tool_call_probe.py
│  ├─ agent_probe.py
│  └─ agent_multiturn_probe.py
│
├─ Dockerfile
├─ requirements.txt
├─ .dockerignore
├─ .gitignore
└─ README.md
```

---

## Architecture

### 1. Plain Python Domain Tools

PyPowSyBl 기능을 LLM과 독립적인 일반 Python 함수로 구현한다.

대표 기능:

- `get_network_summary()`
- `list_lines()`
- `get_line()`
- `list_generators()`
- `run_line_contingency()`
- `rank_generator_sensitivities()`

물리해석 계층은 상세 결과를 유지하며 LLM 없이도 직접 사용할 수 있다.

### 2. Agent Tool Wrapper

`src/ai_ems/agent/tools.py`에서는 Domain Tool을 LLM이 사용하기 쉬운 schema로 다시 노출한다.

예를 들어 Security Analysis의 상세 raw 결과 전체를 그대로 LLM에 전달하지 않고 다음과 같은 핵심 값으로 정리한다.

- outage line
- convergence status
- violated equipment
- apparent-power limit
- post-contingency apparent-power flow
- loading percent
- overload percent

이렇게 함으로써 LLM이 MW / MVA, 사고 전 조류 / 설비 한계 등을 혼동할 가능성을 줄인다.

### 3. LangGraph Agent

Agent는 다음 흐름으로 동작한다.

```text
START
  ↓
LLM Agent
  ↓
Tool call 필요?
  ├─ No  → 답변
  └─ Yes → ToolNode
             ↓
          LLM Agent
```

LLM은 Tool 선택과 parameter 추출을 담당하고 실제 계통 계산은 PyPowSyBl이 수행한다.

### 4. Domain Fallback

작은 Local LLM이 multi-turn 문맥에서 일부 parameter를 누락할 수 있으므로, 중요한 계통 판단은 가능한 경우 Domain Tool에서 보완한다.

예를 들어 사용자가 Security Analysis 후

```text
그럼 거기에 영향이 큰 발전기 5개는?
```

이라고 질문했을 때 `monitored_line_id`가 누락되면 Tool이 해당 contingency의 Security Analysis 결과를 확인하고 가장 심한 위반 선로를 Sensitivity 대상으로 자동 선택할 수 있다.

즉 LLM이 모든 계통 상태를 기억하고 판단하도록 하지 않고, 실제 계통 상태는 물리해석 Tool이 다시 확인한다.

---

## KPG-193 Test System

이 프로젝트의 테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

KPG-193은 KENTECH AGM Center에서 개발한 synthetic Korean power grid test system이다.

- Upstream repository: https://github.com/agm-center/kpg-testgrid
- KPG Test System documentation: https://agm.kentech.ac.kr/docs/kpg-test-system/
- Paper: Geonho Song and Jip Kim, *KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies*, arXiv:2411.14756, 2024.

```bibtex
@article{song2024kpg,
  title={KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies},
  author={Song, Geonho and Kim, Jip},
  journal={arXiv preprint arXiv:2411.14756},
  year={2024}
}
```

### Local dataset policy

KPG case 데이터는 이 public repository에 포함하지 않는다. `*.mat` 파일은 `.gitignore`로 제외한다.

개발 환경에서는 다음 위치에 별도로 배치한다.

```text
data/KPG193_ver2_0_pypowsybl.mat
```

회사 오프라인 Linux 워크스테이션에 배포할 때는 MAT 파일을 포함한 Docker image를 내부 환경에서 생성·전달할 수 있다. Docker image는 public registry에 배포하지 않는다.

### PyPowSyBl-compatible case

`KPG193_ver2_0_pypowsybl.mat`은 upstream 원본 MAT 파일을 그대로 복사한 것이 아니라 기존 KPG 실습에서 PyPowSyBl import를 위해 변환한 derived MATPOWER case이다.

현재 변환 방식:

- PyPowSyBl import 시 base voltage 정보 사용
- 원본 `dcline` 항목 제거
- fixed HVDC transfer를 양단 dummy generator로 표현
- PyPowSyBl AC Load Flow 검증 완료
- PyPowSyBl Security Analysis 검증 완료
- PyPowSyBl Sensitivity Analysis 검증 완료

세부 provenance는 `data/README.md`에 기록한다.

---

## Verified Scenarios

### Security Analysis

대표 contingency:

- outage: `LINE-16-28`
- monitored / violated line: `LINE-16-22`
- post-contingency status: `CONVERGED`
- limit type: `APPARENT_POWER`
- RateC limit: 약 `1906 MVA`
- post-contingency apparent power: 약 `1967.28 MVA`
- loading: 약 `103.21 %`

이 사례는 기존의 직접 설비 탈락 + AC-PF 반복 방식과 PyPowSyBl 전용 Security Analysis 엔진의 자동화 구조를 비교하는 대표 시나리오로 사용한다.

### Sensitivity Analysis

`LINE-16-28` 탈락 이후 `LINE-16-22` 유효전력 조류에 대한 발전기 민감도 절댓값을 기준으로 제어 후보를 탐색하였다.

대표 상위 후보:

1. `GEN-19#0`
2. `GEN-10#0`
3. `GEN-21`
4. `GEN-34`
5. `GEN-34#2`

Sensitivity는 제어 후보의 영향도를 나타낼 뿐 실제 과부하 해소를 보장하지 않는다.

### Redispatch Physical Validation

별도 probe에서 소규모 balanced redispatch를 적용한 후 AC 물리해석으로 재검증하였다.

- `GEN-19#0`: +10 MW
- `GEN-36#0`: -10 MW
- Sensitivity 예측 LINE-16-22 ΔP: 약 +7.382 MW
- 실제 AC-PF ΔP: 약 +7.382 MW
- apparent power: 약 1967.28 → 1959.64 MVA

작은 변화 범위에서 선형 Sensitivity 예측이 실제 AC 결과와 매우 유사함을 확인했지만, 이 조치만으로 과부하가 완전히 해소되지는 않았다.

따라서 기본 흐름은 다음과 같이 해석한다.

```text
Security Analysis
문제 설비 탐지
      ↓
Sensitivity Analysis
영향이 큰 제어 후보 탐색
      ↓
Redispatch / Corrective Action
      ↓
AC Power Flow / Security Analysis
물리 검증
```

---

## Local LLM

현재 개발 환경에서는 Ollama의 `qwen2:7b`를 사용한다.

Docker container에서는 Windows host의 Ollama API에 다음 주소로 접근한다.

```text
http://host.docker.internal:11434
```

Ollama host는 container에서 접근할 수 있도록 별도로 설정되어 있어야 한다.

Agent의 자연어 설명은 Local LLM의 크기와 품질에 영향을 받을 수 있다. 따라서 전력계통 수치와 상태 판단은 가능한 한 Domain Tool의 구조화 결과로 고정하고, LLM에는 Tool 선택과 설명 역할을 중심으로 맡긴다.

---

## Installation

### Python

Python 3.11 환경을 기준으로 한다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Docker

```bash
docker build -t ai-ems-agent:dev .
```

개발 중 source code를 bind mount하여 실행할 수 있다.

```bash
docker run --rm \
  -v "$(pwd):/app" \
  ai-ems-agent:dev \
  python tests/smoke_test.py
```

---

## Tests

### Regression Test

```bash
docker run --rm \
  -v "$(pwd):/app" \
  ai-ems-agent:dev \
  pytest -q
```

### Agent Probe

```bash
docker run --rm \
  -v "$(pwd):/app" \
  ai-ems-agent:dev \
  python tests/agent_probe.py
```

### Multi-turn Agent Probe

```bash
docker run --rm \
  -v "$(pwd):/app" \
  ai-ems-agent:dev \
  python tests/agent_multiturn_probe.py
```

---

## Interactive CLI

```bash
docker run --rm -it \
  -v "$(pwd):/app" \
  ai-ems-agent:dev \
  python app.py
```

예시:

```text
AI-EMS Agent
Type 'exit' to quit.

You> 선로 리스트 뽑아줘.
Agent> ...

You> LINE-2-4 선로가 탈락하면 전체 계통은 어떻게 될까?
Agent> ...

You> 발전기 리스트 뽑아줘.
Agent> ...
```

---

## Offline Deployment

회사 오프라인 Linux 워크스테이션 이전을 고려한다.

Docker image 생성:

```bash
docker build -t ai-ems-agent:dev .
```

이미지 저장:

```bash
docker save -o ai-ems-agent.tar ai-ems-agent:dev
```

오프라인 환경에서:

```bash
docker load -i ai-ems-agent.tar
```

실제 배포 전에는 회사 워크스테이션에서 Docker 또는 Podman 사용 가능 여부와 Local LLM 실행 방식을 별도로 확인한다.

---

## UI Roadmap

다음 단계에서는 기존 KPG 계통 위치 데이터를 활용해 Web UI를 추가한다.

목표 기능:

- 자연어 Chat UI
- KPG-193 계통 지도
- Bus / Line / Generator 조회
- 사고 선로 강조
- Security Analysis 위반 선로 강조
- 사고 전 / 사고 후 loading 비교
- Sensitivity 상위 발전기 표시
- 분석 결과 상세 panel

UI에서도 동일한 원칙을 유지한다.

```text
UI
 ↓
Agent
 ↓
PyPowSyBl Domain Tools
 ↓
Physics Result
 ↓
UI Visualization + LLM Explanation
```

지도와 시각화는 물리해석 엔진의 결과를 표시하는 역할이며, 계산 자체는 PyPowSyBl Tool에서 수행한다.

---

## Roadmap

- [x] Plain Python Tool layer
- [x] Network / Line / Generator query
- [x] AC Load Flow
- [x] Security Analysis Tool
- [x] Sensitivity Analysis Tool
- [x] Redispatch validation probe
- [x] LangGraph workflow
- [x] Local LLM Tool Calling
- [x] Multi-turn interaction
- [x] Interactive CLI
- [ ] Web UI
- [ ] KPG map visualization
- [ ] Security result highlighting
- [ ] Sensitivity candidate visualization
- [ ] Offline workstation deployment test

발표에서는 Agent 자체를 AI-EMS의 전체 구조로 설명하지 않고, **계통해석 기능을 자연어로 호출하고 물리해석 결과를 연결하는 작은 orchestration PoC**로 사용한다.
