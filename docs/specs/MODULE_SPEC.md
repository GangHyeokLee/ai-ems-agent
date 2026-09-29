# AI-EMS Physics Module Specification

## 1. Purpose

이 문서는 `ai-ems-agent` 프로젝트의 **PyPowSyBl 기반 물리해석 모듈 경계와 통합 역할**을 정의한다.

핵심 원칙:

> AI / LLM은 물리해석 엔진을 대체하지 않는다. 자연어 해석과 Tool orchestration은 AI가 담당할 수 있지만, 계통 상태와 제어 효과는 PyPowSyBl 기반 물리해석으로 검증한다.

현재 모듈은 두 사용 경로를 지원한다.

1. **AI-EMS Agent**: 자연어 요청을 Domain Tool로 연결
2. **외부 Simulator / EMS Tester**: LLM 없이 Physics REST API 직접 호출

---

## 2. Module Boundary

```text
User ──> LLM Agent ─────┐
                        ↓
                 PyPowSyBl Domain Tools
                        ↑
Simulator ──> REST API ─┘
```

### Domain Layer

```text
src/ai_ems/
├─ network.py
└─ tools/
   ├─ network_tools.py
   ├─ security_tools.py
   ├─ generator_contingency_tools.py
   ├─ generator_screening_tools.py
   ├─ sensitivity_tools.py
   ├─ control_tools.py
   └─ workflow_tools.py
```

역할:

- `network.py`: 계통 load 및 공통 AC Load Flow
- `network_tools.py`: 계통 요약 / 선로 / 발전기 조회
- `security_tools.py`: Security Analysis 공통 기능과 위반 요약
- `generator_contingency_tools.py`: 발전기 탈락 AC Load Flow / Security Analysis
- `generator_screening_tools.py`: 연결 발전기 전체 N-1 batch screening
- `sensitivity_tools.py`: 발전기 injection 변화에 대한 선로 유효전력 조류 민감도
- `control_tools.py`: balanced redispatch 후보 / 출력 제약 / AC 및 whole-network Security 검증
- `workflow_tools.py`: line outage corrective-action workflow

Domain layer는 LLM 없이 직접 호출할 수 있도록 유지한다.

### Agent Layer

```text
src/ai_ems/agent/
├─ graph.py
├─ tools.py
├─ formatters.py
├─ generator_comparison_formatter.py
└─ generator_screening_formatter.py
```

- `tools.py`: Domain function을 LLM-facing Tool schema로 노출
- `graph.py`: LangGraph ToolNode routing과 deterministic response routing
- formatter 파일: 고위험 계산 결과를 LLM 재해석 없이 운영자용 응답으로 변환

### Public API Layer

```text
src/ai_ems/api/
├─ app.py
├─ routes.py
├─ schemas.py
└─ adapters.py
```

- `app.py`: 독립 Physics FastAPI service
- `routes.py`: 외부 호출 endpoint
- `schemas.py`: Public Request / Response 계약
- `adapters.py`: Domain result → Public Response 변환

---

## 3. Supported Analysis

### Line N-1 Security Analysis

- AC Security Analysis
- pre / post contingency 수렴 상태
- 위반 설비 요약
- 신규 / 잔존 / 해소 위반 비교
- 주요 위반 선로 선택

### Generator N-1 Analysis

- 특정 발전기 독립 탈락
- Single / Distributed slack AC Load Flow
- 사고 전 / 후 발전기 출력 변화 비교
- AC Security Analysis
- 주요 과부하 추출
- Single / Distributed 결과 비교

Single / Distributed slack은 **Load Flow balancing 가정**이며 운영자 Redispatch가 아니다.

### Generator N-1 Batch Screening

- 현재 연결된 발전기를 독립 N-1 사고로 자동 구성
- AC Security Analysis batch 실행
- 수렴 / 위반 / islanding / 비수렴 / 실행오류 분류
- 물리해석 결과 기반 deterministic review ranking
- Top-N 사고 후보 반환

Ranking은 AI score나 고장 확률이 아니다.

### Sensitivity Analysis

- 발전기 injection 변화 → 특정 선로 active-power flow 영향
- 절대 민감도 기반 영향도 후보 ranking
- line outage 후 가장 심한 위반 선로 자동 선택 가능

### Explicit Redispatch Validation

- 명시적 `+ΔMW / -ΔMW` balanced redispatch 검증
- 발전기 출력 범위 guardrail
- 사고 후 / 제어 후 AC Load Flow 비교
- 목표 선로 loading / 위반 잔존 여부 확인
- whole-network Operator Strategy Security validation

### Corrective-action Candidate Analysis

- line outage Security Analysis
- Sensitivity 기반 balanced redispatch 후보 생성
- 후보별 AC validation
- whole-network Security validation
- 신규 위반 / 잔존 위반 / 개선량 기반 ranking

`best_tested_candidate`는 시험한 후보 중 최선이며 OPF / SCED 최적해를 의미하지 않는다.

---

## 4. Main Workflows

### Line-outage corrective action

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

### Generator outage

```text
Generator Contingency
      ↓
Single or Distributed Slack AC Load Flow
      ↓
Security Analysis
      ↓
Deterministic Result Explanation
```

### Generator N-1 screening

```text
Connected Generators
      ↓
Independent N-1 Contingencies
      ↓
Batch AC Security Analysis
      ↓
Physics-first Review Ranking
      ↓
Top-N Candidates
```

---

## 5. Public Integration Interface

현재 Public Physics API endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

발전기 contingency / screening은 현재 Agent / Domain Tool 경로에서 사용하며 Public Physics API에는 아직 별도 endpoint로 노출하지 않았다.

세부 Request / Response는 `API_SPEC.md`에 정의한다.

---

## 6. Batch Study Output

`studies/kpg193_full_batch/`는 재현 가능한 batch 실험용 runner다.

주요 출력:

```text
run_manifest.json
contingency_summary.csv
violations.csv
summary.json
report.html
```

Sensitivity 결과가 존재할 때만 `topn_sensitivity.csv`를 생성한다.

`contingency_summary.csv` 자체에 deterministic review ranking이 포함되므로 동일 내용을 복제하는 별도 ranking CSV는 생성하지 않는다.

---

## 7. Configuration

프로젝트 root의 `.env`를 사용한다.

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

`config.py`가 `.env`를 자동 로드하며 shell 환경변수가 이미 존재하면 해당 값이 우선한다.

---

## 8. Runtime Separation

### Agent / Web UI

```bash
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

### Standalone Physics API

```bash
python -m uvicorn ai_ems.api.app:app --host 127.0.0.1 --port 8001
```

외부 Simulator는 Physics API만 사용하면 되며 Ollama / LangGraph / Web UI를 호출할 필요가 없다.

---

## 9. Data Policy

실제 KPG 실습 데이터는 public repository에 포함하지 않는다.

Local-only files:

```text
data/KPG193_ver2_0_powsybl_full.mat
data/bus_location.csv
```

KPG adapter 세부 내용은 `KPG_POWSYBL_ADAPTER_SPEC.md`를 참고한다.

---

## 10. Current Boundary

- 발전기 사고 후 corrective-action workflow는 아직 별도 구현하지 않았다.
- 현재 Sensitivity / Redispatch corrective-action workflow는 line outage 기반이다.
- Batch screening은 위험 후보 선별이며 교정제어가 아니다.
- 정적 AC Load Flow / Security Analysis는 동특성 안정도를 검증하지 않는다.
