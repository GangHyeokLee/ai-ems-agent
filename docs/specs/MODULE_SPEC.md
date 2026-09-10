# AI-EMS Physics Module Specification

## 1. Purpose

이 문서는 `ai-ems-agent` 프로젝트의 **PyPowSyBl 기반 물리해석 모듈 경계와 통합 역할**을 정의한다.

프로젝트의 핵심 원칙은 다음과 같다.

> AI / LLM은 물리해석 엔진을 대체하지 않는다. 자연어 해석과 후보 생성·오케스트레이션은 AI가 담당할 수 있지만, 계통 상태와 제어 효과는 PyPowSyBl 기반 물리해석으로 검증한다.

현재 모듈은 두 가지 사용자를 지원한다.

1. **AI-EMS Agent**: 자연어 요청을 Domain Tool로 연결
2. **외부 Simulator / EMS Tester**: LLM 없이 Physics REST API를 직접 호출

---

## 2. Module Boundary

```text
                    ┌──────────────────────┐
User ──> LLM Agent  │                      │
                    │   PyPowSyBl Domain   │
                    │       Tools          │
Simulator ──> API ─>│                      │
                    └──────────────────────┘
```

### Domain Layer

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

- `network.py`: 계통 load 및 공통 AC Load Flow 설정
- `network_tools.py`: 계통 요약, 선로, 발전기 조회
- `security_tools.py`: Line N-1 Security Analysis, pre/post 위반 비교, 주요 위반 선택
- `sensitivity_tools.py`: 발전기 injection 변화에 대한 선로 유효전력 조류 민감도 분석
- `control_tools.py`: balanced redispatch 후보 생성, 발전기 출력 제약 guardrail, AC 검증, whole-network Operator Strategy 검증
- `workflow_tools.py`: Security → Sensitivity → Redispatch Candidate → AC / Security Validation 연결

### Public API Layer

```text
src/ai_ems/api/
├─ app.py
├─ routes.py
├─ schemas.py
└─ adapters.py
```

역할:

- `app.py`: 독립 Physics FastAPI service
- `routes.py`: 외부 호출 endpoint
- `schemas.py`: Public Request / Response 계약
- `adapters.py`: 내부 Domain result를 외부 통합용 응답으로 변환

### Agent Layer

```text
src/ai_ems/agent/
├─ graph.py
├─ tools.py
└─ formatters.py
```

Agent layer는 Domain Tool을 재사용하며 외부 Simulator의 필수 의존성이 아니다.

---

## 3. Supported Analysis

현재 Physics Module은 다음 기능을 지원한다.

### Security Analysis

- Line N-1 contingency
- AC Security Analysis
- pre-contingency / post-contingency 수렴 상태
- 위반 설비 요약
- 신규 / 잔존 / 해소 위반 비교
- APPARENT_POWER 등 정적 계통 제약 결과 처리

### Sensitivity Analysis

- 발전기 injection 변화 → 특정 선로 active-power flow 영향
- 절대 민감도 기반 영향도 후보 ranking
- monitored line 미지정 시 가장 심한 위반 선로 자동 선택

### Explicit Redispatch Validation

- 사용자가 지정한 `+ΔMW / -ΔMW` balanced redispatch 검증
- 발전기 출력 범위 guardrail
- 사고 후 / 제어 후 AC Load Flow 비교
- 목표 선로 loading / 위반 잔존 여부 확인
- whole-network Operator Strategy Security validation
- Redispatch로 인한 신규 / 해소 / 잔존 위반 비교

### Corrective-action Candidate Analysis

- sensitivity 기반 balanced redispatch 후보 생성
- 발전기 출력 범위 guardrail
- 후보별 AC validation
- whole-network Operator Strategy Security validation
- 신규 위반 발생 여부 / 잔존 위반 수 / 목표 선로 개선량 기반 ranking

---

## 4. High-level Workflow

고수준 entry point:

```python
analyze_contingency_response(
    case_path,
    outage_line_id,
    monitored_line_id=None,
    delta_mw=10.0,
    top_n=3,
)
```

실행 흐름:

```text
Line Contingency
      ↓
Security Analysis
      ↓
Pre / Post Violation Comparison
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

---

## 5. Public Integration Interface

외부 Simulator는 내부 Python 함수나 PyPowSyBl 객체를 직접 다룰 필요 없이 REST API를 사용할 수 있다.

현재 Public endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

역할:

```text
/security-analysis
→ 사고 후보 물리검증

/sensitivity-analysis
→ 목표 선로에 영향도가 큰 발전기 탐색

/redispatch-validation
→ 외부에서 생성한 명시적 제어 후보 검증

/contingency-response
→ Security부터 후보 생성·검증까지 고수준 workflow 수행
```

세부 Request / Response는 `API_SPEC.md`에 정의한다.

---

## 6. Configuration

프로젝트 root의 `.env`를 사용한다.

공유 가능한 예시는 `.env.example`에 둔다.

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

권장 WSL shell 초기화:

```bash
source .venv/bin/activate
set -a; source .env; set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

`config.py`는 `.env`를 자동으로 읽으며 이미 shell에 설정된 환경변수는 `.env`보다 우선한다.

---

## 7. Runtime Separation

### Agent / Web UI

```bash
python -m uvicorn web_app:app \
  --host "$AI_EMS_WEB_HOST" \
  --port "$AI_EMS_WEB_PORT" \
  --log-level "$AI_EMS_LOG_LEVEL"
```

### Standalone Physics API

```bash
python -m uvicorn ai_ems.api.app:app \
  --host "$AI_EMS_PHYSICS_HOST" \
  --port "$AI_EMS_PHYSICS_PORT" \
  --log-level "$AI_EMS_LOG_LEVEL"
```

외부 Simulator는 Physics API만 사용하면 되며 Ollama / LangGraph / Web UI를 호출할 필요가 없다.

---

## 8. Data Policy

실제 KPG 실습 데이터는 public repository에 포함하지 않는다.

Local-only files:

```text
data/KPG193_ver2_0_pypowsybl.mat
data/bus_location.csv
```

`KPG193_ver2_0_pypowsybl.mat`은 PyPowSyBl import를 위해 변환한 derived MATPOWER case이다.

---

## 9. Validation Status

최종 로컬 regression test:

```text
19 passed
```

테스트 범위:

- KPG network load 및 AC load flow
- known contingency violation
- sensitivity 기반 redispatch candidate generation
- balanced redispatch AC validation
- contingency-response workflow
- Security / Sensitivity / Redispatch / Contingency Public API adapter contract

실제 HTTP 확인:

- Swagger UI에서 4개 Physics POST endpoint 노출 확인
- `LINE-183-190` outage에 대해 `/sensitivity-analysis` 정상 응답 확인
- 같은 사고에 `GEN-10#0 +10 MW / GEN-190 -10 MW`를 `/redispatch-validation`으로 검증
- `2476.13 → 2469.21 MVA`, `114.00% → 113.68%`, 신규 whole-network 위반 없음, 기존 `LINE-176-190` 위반 잔존 확인

대표 검증 사례:

- `LINE-16-28` outage → `LINE-16-22` overload
- `LINE-183-190` outage → `LINE-176-190` overload
- `LINE-81-84` outage → `LINE-16-28`, `LINE-134-193` 신규 위반

---

## 10. Current Limitations

현재 구현은 연구 / PoC 범위이다.

- line contingency 중심
- Redispatch는 balanced fixed-step candidate evaluation이며 OPF / SCED가 아님
- Sensitivity는 local linearization
- whole-network validation은 정적 Security Analysis 기반
- transient stability, frequency response, rotor-angle stability는 범위 밖
- 반복 `contingency-response` 실행 시 workflow 내부에서 network를 다시 load하는 경로가 있어 대량 반복 호출 성능 최적화는 아직 하지 않음
- 같은 bus / 설비군의 유사 generator ID가 동일하거나 매우 유사한 sensitivity를 가져 중복 성격의 후보가 나타날 수 있음

위 성능 / candidate 정리 항목은 **현재 기능 검증이나 API 통합을 막는 blocker가 아니며**, 실제 Simulator 통합에서 필요성이 확인될 때 개선한다.

---

## 11. Freeze Criteria

현재 프로젝트는 다음 조건을 충족하므로 기능 개발을 일시 중지할 수 있다.

- Security / Sensitivity 핵심 실습 완료
- explicit Redispatch validation 구현
- corrective-action candidate workflow 구현
- AC 및 whole-network 재검증 구현
- Agent Tool Calling PoC 동작 확인
- 외부 Simulator용 Physics API 분리
- Public Request / Response schema 분리
- 핵심 Physics 기능 4종 API 제공
- 19 regression tests 통과
- 실제 HTTP 호출 검증
- `.env` 기반 실행 설정 및 README / API / Module 문서 정리

이후 기능 추가는 다음 경우에만 재개한다.

1. 외부 Simulator 실제 통합에서 명확한 API 요구가 발생한 경우
2. 발표 사실 검증 과정에서 오류가 발견된 경우
3. 현재 시나리오를 재현하지 못하는 regression이 발생한 경우
