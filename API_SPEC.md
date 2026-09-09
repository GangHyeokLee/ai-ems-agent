# AI-EMS Physics API Specification

## 1. Overview

AI-EMS Physics API는 외부 Simulator / EMS Tester가 LLM 없이 PyPowSyBl 기반 물리해석 기능을 호출할 수 있도록 제공하는 REST interface이다.

Base URL 예시:

```text
http://127.0.0.1:8001
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```

현재 Public API version은 `/api/v1` prefix를 사용한다.

---

## 2. Runtime

환경변수 예시:

```dotenv
AI_EMS_CASE_FILE=data/KPG193_ver2_0_pypowsybl.mat
AI_EMS_PHYSICS_HOST=127.0.0.1
AI_EMS_PHYSICS_PORT=8001
AI_EMS_LOG_LEVEL=info
```

실행:

```bash
source .venv/bin/activate
set -a; source .env; set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

python -m uvicorn ai_ems.api.app:app \
  --host "$AI_EMS_PHYSICS_HOST" \
  --port "$AI_EMS_PHYSICS_PORT" \
  --log-level "$AI_EMS_LOG_LEVEL"
```

---

## 3. Health Check

### `GET /api/health`

서비스 실행 상태를 확인한다.

Response example:

```json
{
  "status": "ok",
  "service": "ai-ems-physics"
}
```

---

## 4. Security Analysis

### `POST /api/v1/security-analysis`

선로 탈락 사고에 대해 AC Security Analysis를 실행하고 사고 전 / 사고 후 위반 상태를 비교한다.

### Request

```json
{
  "outage_line_id": "LINE-81-84",
  "monitored_line_id": null
}
```

Fields:

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `outage_line_id` | string | yes | 탈락시킬 선로 ID |
| `monitored_line_id` | string \| null | no | 특정 선로 monitor가 필요한 경우 지정 |

### Response

```json
{
  "outage_line_id": "LINE-81-84",
  "base_converged": true,
  "pre_status": "CONVERGED",
  "post_status": "CONVERGED",
  "pre_violated_equipment_count": 0,
  "post_violated_equipment_count": 2,
  "new_violation_count": 2,
  "remaining_violation_count": 0,
  "resolved_violation_count": 0,
  "new_violations": [
    {
      "equipment_id": "LINE-16-28",
      "limit_type": "APPARENT_POWER",
      "limit_name": "RateC",
      "unit": "MVA",
      "limit": 4344.0,
      "value": 4465.24,
      "violation_amount": 121.24,
      "loading_percent": 102.79
    }
  ],
  "remaining_violations": [],
  "resolved_violations": []
}
```

Response fields:

| Field | Meaning |
|---|---|
| `base_converged` | 사고 전 base case 수렴 여부 |
| `pre_status` | 사고 전 Security result 상태 |
| `post_status` | 사고 후 Security result 상태 |
| `pre_violated_equipment_count` | 사고 전 위반 설비 수 |
| `post_violated_equipment_count` | 사고 후 위반 설비 수 |
| `new_violation_count` | 사고로 인해 새로 발생한 위반 수 |
| `remaining_violation_count` | 사고 전부터 존재하고 사고 후에도 남은 위반 수 |
| `resolved_violation_count` | 사고 전에는 있었지만 사고 후 사라진 위반 수 |
| `new_violations` | 사고 후 신규 위반 설비 요약 |
| `remaining_violations` | 사고 전후 모두 존재하는 위반 설비의 post 상태 요약 |
| `resolved_violations` | 사고 후 해소된 위반 설비 요약 |

### Violation Summary

| Field | Unit / Meaning |
|---|---|
| `equipment_id` | 설비 ID |
| `limit_type` | PyPowSyBl limit type, 예: `APPARENT_POWER` |
| `limit_name` | limit 이름, 예: `RateC` |
| `unit` | 물리량 단위, 예: `MVA` |
| `limit` | 설비 limit |
| `value` | 계산된 값 |
| `violation_amount` | limit 초과량 또는 위반량 |
| `loading_percent` | 설비 limit 대비 loading [%], 해당 물리량에서 정의될 때만 사용 |

주의: `loading_percent=102.79`는 설비 한계의 약 102.79% 수준이라는 의미이며, 계통 전체 안정도를 의미하지 않는다.

---

## 5. Contingency Response Analysis

### `POST /api/v1/contingency-response`

선로 사고 후 Security → Sensitivity → balanced redispatch candidate generation → AC validation → whole-network Security re-validation을 연결해 시험 후보를 평가한다.

### Request

```json
{
  "outage_line_id": "LINE-81-84",
  "monitored_line_id": null,
  "delta_mw": 10.0,
  "top_n": 3
}
```

Fields:

| Field | Type | Required | Default | Meaning |
|---|---|---:|---:|---|
| `outage_line_id` | string | yes | - | 탈락 선로 ID |
| `monitored_line_id` | string \| null | no | `null` | 제어 후보 평가 대상 선로. `null`이면 가장 심한 위반 선로 자동 선택 |
| `delta_mw` | float | no | `10.0` | 각 balanced redispatch 후보의 +ΔMW / -ΔMW 제어량. 0보다 커야 함 |
| `top_n` | int | no | `3` | 평가할 후보 수. 1 이상 |

### Response

```json
{
  "outage_line_id": "LINE-81-84",
  "target_line_id": "LINE-16-28",
  "target_selection": "most_severe_violation",
  "delta_mw": 10.0,
  "candidate_count": 3,
  "pre_violated_equipment_count": 0,
  "post_violated_equipment_count": 2,
  "contingency_new_violation_count": 2,
  "contingency_new_violation_ids": [
    "LINE-134-193",
    "LINE-16-28"
  ],
  "candidates": [
    {
      "rank": 1,
      "ac_validation_rank": 1,
      "up_generator_id": "GEN-19#0",
      "down_generator_id": "GEN-82",
      "delta_mw": 10.0,
      "predicted_active_power_change_mw": 3.98,
      "predicted_abs_p1_reduction_mw": 3.98,
      "apparent_power_before_mva": 4465.24,
      "apparent_power_after_mva": 4460.85,
      "improvement_mva": 4.39,
      "loading_before_percent": 102.79,
      "loading_after_percent": 102.69,
      "violation_remaining": true,
      "whole_network_converged": true,
      "new_violation_detected": false,
      "violated_equipment_count_after": 2,
      "remaining_violation_ids": [
        "LINE-134-193",
        "LINE-16-28"
      ]
    }
  ],
  "best_candidate": {
    "rank": 1,
    "ac_validation_rank": 1,
    "up_generator_id": "GEN-19#0",
    "down_generator_id": "GEN-82",
    "delta_mw": 10.0,
    "predicted_active_power_change_mw": 3.98,
    "predicted_abs_p1_reduction_mw": 3.98,
    "apparent_power_before_mva": 4465.24,
    "apparent_power_after_mva": 4460.85,
    "improvement_mva": 4.39,
    "loading_before_percent": 102.79,
    "loading_after_percent": 102.69,
    "violation_remaining": true,
    "whole_network_converged": true,
    "new_violation_detected": false,
    "violated_equipment_count_after": 2,
    "remaining_violation_ids": [
      "LINE-134-193",
      "LINE-16-28"
    ]
  }
}
```

### Candidate Fields

| Field | Meaning |
|---|---|
| `rank` | Sensitivity candidate generation 단계의 원래 순위 |
| `ac_validation_rank` | AC / whole-network validation 후 최종 평가 순위 |
| `up_generator_id` | 출력을 증가시킨 발전기 |
| `down_generator_id` | 출력을 감소시킨 발전기 |
| `delta_mw` | 각 발전기의 제어량 [MW] |
| `predicted_active_power_change_mw` | Sensitivity 기반 목표 선로 active-power flow 변화 예측 [MW] |
| `predicted_abs_p1_reduction_mw` | 절대 조류 감소량 예측 [MW] |
| `apparent_power_before_mva` | 사고 후, 제어 전 목표 선로 피상전력 [MVA] |
| `apparent_power_after_mva` | Redispatch 후 목표 선로 피상전력 [MVA] |
| `improvement_mva` | 목표 선로 피상전력 감소량 [MVA] |
| `loading_before_percent` | 제어 전 loading [%] |
| `loading_after_percent` | 제어 후 loading [%] |
| `violation_remaining` | 목표 선로 위반이 제어 후에도 남아 있는지 여부 |
| `whole_network_converged` | Operator Strategy whole-network validation 수렴 여부 |
| `new_violation_detected` | Redispatch로 인해 추가 신규 위반이 발생했는지 여부 |
| `violated_equipment_count_after` | 제어 후 whole-network 위반 설비 수 |
| `remaining_violation_ids` | 제어 후에도 남아 있는 위반 설비 ID |

`best_candidate`는 **시험한 후보 중 최선**이며 최적 redispatch를 의미하지 않는다.

---

## 6. Candidate Ranking Semantics

현재 candidate ranking은 다음을 우선한다.

1. AC validation 및 Operator Strategy 수렴
2. Redispatch로 인한 신규 위반 없음
3. 제어 후 위반 설비 수가 적음
4. 목표 선로 개선량이 큼
5. 기존 Sensitivity candidate rank

따라서 `ac_validation_rank=1`은 현재 평가 규칙에서 가장 우선되는 시험 후보라는 의미이다.

---

## 7. Error Handling

Request validation 오류는 FastAPI / Pydantic 기본 validation response를 사용한다.

예:

- `delta_mw <= 0`
- `top_n < 1`
- 필수 `outage_line_id` 누락

Domain layer에서 `ValueError`가 발생하면 API는 HTTP `400`으로 변환한다.

예:

```json
{
  "detail": "No overloaded transmission line found after the contingency."
}
```

현재 구현에서는 unexpected internal exception에 대한 별도 Public error schema를 정의하지 않았으며 FastAPI 기본 500 handling을 따른다.

---

## 8. Integration Guidance

외부 Simulator에서는 다음 방식으로 사용하는 것을 권장한다.

### 단순 물리검증

AI / GNN이 위험 사고 후보를 생성한 경우:

```text
Risk Candidate
      ↓
POST /api/v1/security-analysis
      ↓
AC Security Validation
```

### 제어 후보 평가까지 필요한 경우

```text
Contingency Candidate
      ↓
POST /api/v1/contingency-response
      ↓
Security + Sensitivity + Redispatch Candidate + AC Validation
```

Simulator는 내부 PyPowSyBl object나 Domain dict 구조에 의존하지 않고 Public API response schema만 사용해야 한다.

---

## 9. Known Limitations

- 현재 Public API는 line contingency 중심이다.
- generator contingency endpoint는 아직 제공하지 않는다.
- `contingency-response`는 fixed-step balanced redispatch candidate evaluation이며 OPF / SCED가 아니다.
- transient / dynamic stability 분석은 포함하지 않는다.
- `contingency-response` workflow는 현재 내부적으로 network를 다시 load하는 경로가 있어 대량 반복 호출 성능은 추가 최적화 대상이다.
- candidate generator ID가 유사한 경우 중복 성격의 후보가 나타날 수 있다.

---

## 10. Versioning / Change Policy

현재 Public prefix는 `/api/v1`이다.

외부 Simulator가 의존하는 필드의 의미를 변경해야 할 경우:

1. `schemas.py` 수정
2. `adapters.py` 수정
3. adapter regression test 추가 / 수정
4. `API_SPEC.md` 갱신
5. 호환성을 깨는 변경이면 `/api/v2` 또는 명시적 migration 검토

Domain 내부 구현 변경은 Public schema가 유지되는 한 외부 Simulator 계약을 깨지 않는 방향을 우선한다.
