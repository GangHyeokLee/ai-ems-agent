# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 프로젝트.

이 프로젝트의 목적은 LLM이 전력계통 물리해석을 대체하는 것이 아니라, 사용자의 자연어 요청을 해석하고 적절한 PyPowSyBl 기반 Tool을 선택·호출한 뒤 실제 계산 결과를 운영자 관점에서 설명하는 흐름을 검증하는 것이다.

> **AI는 물리해석 엔진을 대체하지 않는다.**
> LLM은 자연어 의도 해석과 Tool orchestration을 담당하고, 계통 상태와 제어 효과는 Security Analysis, Sensitivity Analysis, AC Load Flow 등 물리해석 결과로 검증한다.

---

## Current Capabilities

### Power-system analysis

- KPG-193 기반 PyPowSyBl network load
- AC Load Flow
- Line N-1 Security Analysis
- Generator N-1 Security Analysis
- Generator N-1 full-batch screening
- Single / Distributed slack 발전기 사고 비교
- pre / post contingency 위반 비교
- Generator Sensitivity Analysis
- Sensitivity 기반 balanced redispatch 후보 생성
- 발전기 출력 범위 guardrail
- Redispatch 후 AC Power Flow 검증
- PyPowSyBl Operator Strategy 기반 whole-network Security 재검증

### AI / Agent

- 자연어 요청 → Tool 선택
- LangGraph ToolNode 기반 Local LLM Tool Calling
- line-outage corrective-action workflow
- generator-outage 개별 분석 / slack 비교 / batch screening
- 고위험 계통 결과의 deterministic formatter
- Tool error의 자연어 설명
- 멀티턴 분석 요청

### Web UI

- FastAPI 기반 Web UI
- KPG 계통 지도
- 선로 / 버스 / 발전기 선택
- Security / Sensitivity 직접 실행
- Generator Contingency 직접 실행
- 분석 패널 접기 / 펼치기
- 사고·위반·민감도·발전기 변화 시각화
- AI 채팅 Markdown 렌더링

### Physics API

Agent와 별도로 외부 Simulator / EMS Tester가 LLM 없이 물리해석 기능을 호출할 수 있는 REST API를 제공한다.

```text
External Simulator
      ↓ HTTP
AI-EMS Physics API
      ↓
Public API Adapter / Response Schema
      ↓
PyPowSyBl Domain Tools
```

---

## Architecture

```text
src/ai_ems/
├─ network.py
├─ config.py
├─ utils/
│  └─ kpg_powsybl_adapter.py
├─ tools/
│  ├─ network_tools.py
│  ├─ security_tools.py
│  ├─ generator_contingency_tools.py
│  ├─ generator_screening_tools.py
│  ├─ sensitivity_tools.py
│  ├─ control_tools.py
│  └─ workflow_tools.py
├─ agent/
│  ├─ graph.py
│  ├─ tools.py
│  ├─ formatters.py
│  ├─ generator_comparison_formatter.py
│  └─ generator_screening_formatter.py
└─ api/
   ├─ app.py
   ├─ routes.py
   ├─ schemas.py
   └─ adapters.py
```

### Domain Layer

LLM과 독립적인 Python / PyPowSyBl 계층이다.

- `network.py`: 계통 load 및 공통 AC Load Flow
- `network_tools.py`: 계통 요약 / 선로 / 발전기 조회
- `security_tools.py`: line / generator contingency Security Analysis 지원 기능
- `generator_contingency_tools.py`: 발전기 탈락 AC Load Flow 및 Security Analysis
- `generator_screening_tools.py`: 연결 발전기 전체 N-1 batch screening
- `sensitivity_tools.py`: 발전기 injection 변화에 대한 선로 유효전력 조류 민감도
- `control_tools.py`: balanced redispatch 후보 / 제약 guardrail / AC·Security 재검증
- `workflow_tools.py`: line outage Security → Sensitivity → Redispatch 후보 → 검증 workflow

### Agent Layer

`src/ai_ems/agent/tools.py`는 Domain function을 LLM-facing Tool schema로 노출한다.

현재 Agent Tool:

```text
network_summary
line_list
line_detail
generator_list
line_contingency
generator_contingency_analysis
generator_contingency_comparison
generator_contingency_screening
generator_sensitivity
balanced_redispatch_validation
contingency_response_analysis
```

고위험 계산 결과는 LLM이 수치를 다시 자유롭게 해석하지 않도록 deterministic formatter를 사용한다.

```text
User
 ↓
LLM: intent / Tool selection
 ↓
Physical-analysis Tool
 ↓
Structured Result
 ↓
Deterministic Formatter
 ↓
Operator-facing Response
```

### Public Physics API Layer

현재 endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

세부 계약은 [`docs/specs/API_SPEC.md`](docs/specs/API_SPEC.md)를 참고한다.

---

## Main Workflows

### Line-outage corrective-action workflow

```text
Line Contingency
      ↓
Security Analysis
      ↓
Most Severe Violation Selection
      ↓
Sensitivity Analysis
      ↓
Balanced Redispatch Candidate Generation
      ↓
Generator Feasibility Guardrail
      ↓
AC Validation
      ↓
Whole-network Security Re-validation
      ↓
Candidate Ranking
```

`best_tested_candidate`는 **시험한 후보 중 최선의 결과**이며 OPF / SCED 기반 최적해를 의미하지 않는다.

### Generator-outage workflow

```text
Specific Generator Outage
      ↓
Single or Distributed Slack AC Load Flow
      ↓
Security Analysis
      ↓
Deterministic Result Explanation
```

Single / Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아니다.

### Generator N-1 batch screening

```text
All Connected Generators
      ↓
Independent Generator N-1 Contingencies
      ↓
AC Security Analysis
      ↓
Physics-first Deterministic Ranking
      ↓
Top-N Review Candidates
```

Screening ranking은 AI score나 고장 확률이 아니라 물리해석 결과 기반 **review priority**다.

---

## Environment

Python 3.11 기준.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

프로젝트 설정은 repository root의 `.env`를 사용하며 `src/ai_ems/config.py`가 자동 로드한다.

주요 설정:

```dotenv
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_LLM_BASE_URL=http://127.0.0.1:11434
AI_EMS_CASE_FILE=data/KPG193_ver2_0_powsybl_full.mat
AI_EMS_BUS_LOCATION_FILE=data/bus_location.csv
AI_EMS_WEB_HOST=127.0.0.1
AI_EMS_WEB_PORT=8000
AI_EMS_PHYSICS_HOST=127.0.0.1
AI_EMS_PHYSICS_PORT=8001
```

---

## Run

### CLI Agent

```bash
python app.py
```

### Agent Web UI

```bash
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000 --log-level info
```

브라우저:

```text
http://127.0.0.1:8000/
```

### Standalone Physics API

```bash
python -m uvicorn ai_ems.api.app:app --host 127.0.0.1 --port 8001 --log-level info
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```

### Full-batch study

```bash
python scripts/run_kpg193_full_batch.py \
  --generator-only \
  --generator-slack distributed \
  --chunk-size 100 \
  --no-sensitivity
```

`contingency_summary.csv`에는 이미 deterministic review ranking이 포함되어 있으므로 별도 중복 ranking CSV는 생성하지 않는다. Sensitivity 결과가 없으면 빈 `topn_sensitivity.csv`도 생성하지 않는다.

---

## Test

Regression test:

```bash
python -m pytest -q
```

실제 KPG case smoke test:

```bash
python tests/smoke_test.py
```

실험용 probe 스크립트는 `tests/`와 repository root에 남아 있으며, 자동 regression test와 달리 특정 통합 흐름을 수동 확인하는 용도다.

---

## Documentation

### Specifications

- [Physics API Specification](docs/specs/API_SPEC.md)
- [Physics Module Specification](docs/specs/MODULE_SPEC.md)
- [KPG-193 PyPowSyBl Adapter Specification](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)

### Experiment Records

- [KPG-193 기반 PyPowSyBl 계통해석 및 제어 후보 검증](docs/experiments/KPG-193%20기반%20PyPowSyBl%20계통해석%20및%20제어%20후보%20검증.md)
- [PyPowSyBl Physics API 및 AI-EMS Agent 연계 PoC](docs/experiments/PyPowSyBl%20Physics%20API%20%EB%B0%8F%20AI-EMS%20Agent%20%EC%97%B0%EA%B3%84%20PoC.md)
- [KPG-193 발전기 N-1 Slack 보상 메커니즘 분석](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20Slack%20%EB%B3%B4%EC%83%81%20%EB%A9%94%EC%BB%A4%EB%8B%88%EC%A6%98%20%EB%B6%84%EC%84%9D.md)
- [KPG-193 발전기 N-1 Single/Distributed Slack 비교](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20SingleDistributed%20Slack%20%EB%B9%84%EA%B5%90.md)

보관된 실행 결과는 `results/` 아래에 둔다.

---

## KPG-193 Data Policy

테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

실제 실습용 MATPOWER case와 위치 CSV는 public repository에 포함하지 않는다.

Local-only files:

```text
data/KPG193_ver2_0_powsybl_full.mat
data/bus_location.csv
```

PyPowSyBl에서는 `src/ai_ems/utils/kpg_powsybl_adapter.py`를 통해 KPG 병렬 dcline을 IIDM HVDC / VSC로 구성한다.

세부 사용법과 제약조건은 [`docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md`](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)를 참고한다.

---

## Limitations

현재 구현은 연구 / PoC 목적이다.

- 발전기 사고 후 corrective-action workflow는 아직 구현하지 않았다.
- 현재 Sensitivity / Redispatch corrective-action workflow는 line outage 중심이다.
- Redispatch 후보 생성은 OPF / SCED optimization이 아니다.
- `best_tested_candidate`는 시험한 후보 중 최선이며 전역 최적해를 의미하지 않는다.
- Sensitivity Analysis는 local linearization이며 실제 제어 효과는 AC 해석으로 재검증한다.
- Single / Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아니다.
- 정적 AC Load Flow / Security Analysis는 transient, frequency, rotor-angle 등 동특성 안정도를 검증하지 않는다.
- 반복 batch / contingency 호출의 성능 최적화는 향후 과제다.
