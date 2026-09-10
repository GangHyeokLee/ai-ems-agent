# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 프로젝트.

이 프로젝트의 목적은 LLM이 전력계통 계산을 직접 수행하도록 하는 것이 아니라, 사용자의 자연어 요청을 해석하고 PyPowSyBl 기반 물리해석 Tool을 선택·호출한 뒤 구조화된 결과를 운영자 관점에서 설명하는 흐름을 검증하는 것이다.

핵심 원칙은 다음과 같다.

> **AI는 물리해석 엔진을 대체하지 않는다.**
> LLM은 자연어 의도 해석과 Tool orchestration을 담당하고, 계통 상태와 제어 효과는 Security Analysis, Sensitivity Analysis, AC Load Flow 등 물리해석 결과로 검증한다.

---

## Current Status

현재 다음 흐름까지 구현하였다.

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

또한 Domain Tool을 다른 Simulator에서 LLM 없이 직접 호출할 수 있도록 별도의 **Physics API**를 제공한다.

```text
External Simulator
      ↓ HTTP
AI-EMS Physics API
      ↓
Public API Adapter / Response Schema
      ↓
PyPowSyBl Domain Tools
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
- FastAPI Web UI
- KPG 계통 지도 / 사고·위반 선로 / 민감도 후보 시각화
- 독립 실행 가능한 Physics REST API
- Pydantic 기반 Public Request / Response schema
- Domain result → Public API response adapter
- Physics API 4종 endpoint 실제 HTTP 호출 검증

최종 로컬 regression test:

```text
19 passed
```

---

## Architecture

### 1. Domain Layer

LLM과 독립적인 Python / PyPowSyBl 계층이다.

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

### 2. Public Physics API Layer

Agent / Web UI와 별개로 실행할 수 있는 물리해석 API이다.

```text
src/ai_ems/api/
├─ app.py
├─ routes.py
├─ schemas.py
└─ adapters.py
```

역할:

- `app.py`: 독립 FastAPI Physics service 생성
- `routes.py`: 외부 Simulator가 호출할 REST endpoint
- `schemas.py`: Public Request / Response 계약
- `adapters.py`: 내부 Domain result를 안정된 Public Response로 변환

현재 endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

외부 Simulator는 PyPowSyBl 객체 구조를 직접 알 필요 없이 위 API의 JSON 계약만 사용하면 된다.

### 3. Agent Layer

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

### 4. Web UI Layer

`web_app.py`는 Agent와 KPG 계통 시각화를 위한 별도 FastAPI app이다.

```text
Web UI / Chat
      ↓
web_app.py
      ↓
Agent + Domain Tools
```

Physics API와 Web UI는 독립된 port에서 동시에 실행할 수 있다.

---

## Corrective-action Workflow

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

### Deterministic Response

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

## Verified Scenarios

### LINE-81-84 outage

사고 전에는 위반이 없었고, 사고 후 다음 두 신규 APPARENT_POWER 위반이 발생하였다.

- `LINE-16-28`: 약 `4465.24 MVA`, loading 약 `102.79%`
- `LINE-134-193`: 약 `2194.38 MVA`, loading 약 `101.03%`

사고 위치와 떨어진 설비에서도 조류 재분배로 신규 제약 위반이 발생할 수 있어 corrective action은 whole-network에서 다시 검증해야 한다.

### LINE-183-190 outage

- most severe violation: `LINE-176-190`
- tested best candidate: `GEN-10#0 +10 MW / GEN-190 -10 MW`
- apparent power: 약 `2476.13 → 2469.21 MVA`
- loading: 약 `114.00% → 113.68%`
- 신규 whole-network violation 없음
- 기존 `LINE-176-190` 위반은 잔존

이 사례는 영향도가 높은 후보를 찾는 것과 실제 제약 해소는 별개의 문제임을 보여준다.

---

## Environment Configuration

프로젝트 설정은 repository root의 `.env`를 사용한다.

실제 `.env`는 Git에 commit하지 않으며, 공유 가능한 예시는 `.env.example`에 둔다.

초기 설정:

```bash
cp .env.example .env
```

예시:

```dotenv
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_LLM_BASE_URL=http://127.0.0.1:11434
AI_EMS_CASE_FILE=data/KPG193_ver2_0_pypowsybl.mat
AI_EMS_BUS_LOCATION_FILE=data/bus_location.csv
AI_EMS_WEB_HOST=127.0.0.1
AI_EMS_WEB_PORT=8000
AI_EMS_PHYSICS_HOST=127.0.0.1
AI_EMS_PHYSICS_PORT=8001
AI_EMS_LOG_LEVEL=info
```

`src/ai_ems/config.py`는 `python-dotenv`를 사용해 `.env`를 자동으로 읽는다. 이미 shell에 설정된 환경변수는 `.env`보다 우선한다.

권장 shell 초기화:

```bash
source .venv/bin/activate
set -a; source .env; set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

---

## Installation

Python 3.11 기준.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

---

## Run

### CLI Agent

```bash
python app.py
```

### Agent Web UI

```bash
python -m uvicorn web_app:app --host "$AI_EMS_WEB_HOST" --port "$AI_EMS_WEB_PORT" --log-level "$AI_EMS_LOG_LEVEL"
```

브라우저:

```text
http://127.0.0.1:8000/
```

### Standalone Physics API

```bash
python -m uvicorn ai_ems.api.app:app --host "$AI_EMS_PHYSICS_HOST" --port "$AI_EMS_PHYSICS_PORT" --log-level "$AI_EMS_LOG_LEVEL"
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```

`AI_EMS_PHYSICS_HOST=127.0.0.1`은 local-only access이다. 다른 PC 또는 Simulator에서 접속해야 하는 환경에서는 필요한 경우 `0.0.0.0`으로 bind하고 네트워크 / 방화벽 정책에 맞게 접근을 제한한다.

---

## Physics API Examples

### Security Analysis

```bash
curl -s -X POST "http://127.0.0.1:8001/api/v1/security-analysis" -H "Content-Type: application/json" -d '{"outage_line_id":"LINE-81-84"}' | python -m json.tool
```

### Sensitivity Analysis

```bash
curl -s -X POST "http://127.0.0.1:8001/api/v1/sensitivity-analysis" -H "Content-Type: application/json" -d '{"outage_line_id":"LINE-183-190","top_n":5}' | python -m json.tool
```

### Explicit Redispatch Validation

```bash
curl -s -X POST "http://127.0.0.1:8001/api/v1/redispatch-validation" -H "Content-Type: application/json" -d '{"outage_line_id":"LINE-183-190","monitored_line_id":"LINE-176-190","up_generator_id":"GEN-10#0","down_generator_id":"GEN-190","delta_mw":10.0}' | python -m json.tool
```

### Contingency Response Analysis

```bash
curl -s -X POST "http://127.0.0.1:8001/api/v1/contingency-response" -H "Content-Type: application/json" -d '{"outage_line_id":"LINE-81-84","delta_mw":10.0,"top_n":3}' | python -m json.tool
```

세부 Request / Response 계약은 `API_SPEC.md`, 모듈 역할과 통합 경계는 `MODULE_SPEC.md`를 참고한다.

---

## Regression Test

```bash
python -m pytest -q
```

현재 확인 결과:

```text
19 passed
```

테스트 범위:

- 실제 KPG / PyPowSyBl 계산 결과 regression
- Security / Sensitivity / Redispatch / Contingency Public API adapter contract

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

---

## Limitations

현재 구현은 연구 / PoC 목적이다.

- line contingency 중심이다.
- Redispatch 후보 생성은 OPF / SCED optimization이 아니다.
- 현재 balanced `+ΔMW / -ΔMW` generator pair를 평가한다.
- `best_tested_candidate`는 시험한 후보 중 최선이며 전역 최적해를 의미하지 않는다.
- Sensitivity Analysis는 local linearization이며 실제 제어 효과는 AC 해석으로 재검증한다.
- Whole-network validation은 정적 Security Analysis 기반이며 transient / dynamic stability를 의미하지 않는다.
- 같은 bus / 설비군의 유사 generator ID가 동일하거나 매우 유사한 sensitivity를 가져 중복 성격의 후보가 나타날 수 있다.
- 반복 `contingency-response` 호출은 network reload 경로가 있어 대량 처리 성능 최적화 대상이다.

---

## Project Status / Freeze

현재 PoC 기능 개발은 **freeze**한다.

완료 기준:

- Security / Sensitivity 실습 완료
- explicit Redispatch validation 완료
- corrective-action candidate workflow 완료
- Agent Tool Calling PoC 완료
- 외부 Simulator용 Physics API 분리
- Public Request / Response schema 및 adapter 분리
- 핵심 Physics 기능 4종 API 제공
- Swagger / 실제 HTTP 호출 검증
- `19 passed` regression 확인
- README / `MODULE_SPEC.md` / `API_SPEC.md` 정리

이후 코드 변경은 외부 Simulator 실제 통합 요구, regression 오류, 발표 사실 검증에서 필요한 경우에 한해 재개한다.
