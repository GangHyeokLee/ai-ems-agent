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

현재 endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

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

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `outage_line_id` | string | yes | 탈락시킬 선로 ID |
| `monitored_line_id` | string \| null | no | 특정 선로 monitor가 필요한 경우 지정 |

### Response example

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

주요 의미:

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

Violation summary의 `loading_percent=102.79`는 해당 설비가 한계의 약 102.79% 수준이라는 의미이며 계통 전체 안정도를 의미하지 않는다.

---

## 5. Sensitivity Analysis

### `POST /api/v1/sensitivity-analysis`

선로 사고 후 특정 monitored line의 유효전력 조류에 대한 발전기 injection sensitivity를 계산한다.

`monitored_line_id`를 생략하면 Security Analysis 결과에서 가장 심한 위반 선로를 자동 선택한다.

### Request

```json
{
  "outage_line_id": "LINE-183-190",
  "monitored_line_id": null,
  "top_n": 5
}
```

| Field | Type | Required | Default | Meaning |
|---|---|---:|---:|---|
| `outage_line_id` | string | yes | - | 탈락 선로 ID |
| `monitored_line_id` | string \| null | no | `null` | 민감도 계산 대상 선로. `null`이면 가장 심한 위반 선로 자동 선택 |
| `top_n` | int | no | `5` | 반환할 발전기 후보 수. 1 이상 |

### Response example

```json
{
  "outage_line_id": "LINE-183-190",
  "monitored_line_id": "LINE-176-190",
  "target_selection": "most_severe_violation",
  "candidate_count": 5,
  "candidates": [
    {
      "generator_id": "GEN-190#0",
      "sensitivity": -0.589638,
      "abs_sensitivity": 0.589638
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `target_selection` | `user_specified` 또는 `most_severe_violation` |
| `sensitivity` | 발전기 injection 1 MW 변화에 대한 monitored line 유효전력 조류 변화계수. 부호는 branch-flow sign convention을 따른다 |
| `abs_sensitivity` | 후보 정렬에 사용하는 sensitivity 절대값 |

Sensitivity는 local linearization 결과이며 그 자체로 최적 redispatch 방향이나 제어 효과를 보장하지 않는다.

---

## 6. Redispatch Validation

### `POST /api/v1/redispatch-validation`

사용자가 명시한 `+ΔMW / -ΔMW` balanced redispatch 한 건을 AC Load Flow와 whole-network Security Analysis로 검증한다.

이 endpoint는 후보를 생성하지 않는다. Up / Down 발전기와 제어량을 호출자가 명시해야 한다.

### Request

```json
{
  "outage_line_id": "LINE-183-190",
  "monitored_line_id": "LINE-176-190",
  "up_generator_id": "GEN-10#0",
  "down_generator_id": "GEN-190",
  "delta_mw": 10.0
}
```

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `outage_line_id` | string | yes | 탈락 선로 ID |
| `monitored_line_id` | string | yes | 제어효과를 확인할 목표 선로 |
| `up_generator_id` | string | yes | 출력을 증가시킬 발전기 |
| `down_generator_id` | string | yes | 출력을 감소시킬 발전기 |
| `delta_mw` | float | yes | 각 발전기의 제어량 [MW], 0보다 커야 함 |

### Verified response example

```json
{
  "outage_line_id": "LINE-183-190",
  "monitored_line_id": "LINE-176-190",
  "up_generator_id": "GEN-10#0",
  "down_generator_id": "GEN-190",
  "delta_mw": 10.0,
  "post_contingency_converged": true,
  "after_redispatch_converged": true,
  "limit_mva": 2172.0,
  "apparent_power_before_mva": 2476.13,
  "apparent_power_after_mva": 2469.21,
  "apparent_power_change_mva": -6.92,
  "improvement_mva": 6.92,
  "improved": true,
  "loading_before_percent": 114.00,
  "loading_after_percent": 113.68,
  "violation_remaining": true,
  "whole_network_converged": true,
  "new_violation_detected": false,
  "violated_equipment_count_before": 1,
  "violated_equipment_count_after": 1,
  "new_violation_ids": [],
  "resolved_violation_ids": [],
  "remaining_violation_ids": ["LINE-176-190"]
}
```

`apparent_power_change_mva`는 after - before이므로 감소 시 음수일 수 있다. `improvement_mva`는 감소량을 양수로 표현한다.

---

## 7. Contingency Response Analysis

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

| Field | Type | Required | Default | Meaning |
|---|---|---:|---:|---|
| `outage_line_id` | string | yes | - | 탈락 선로 ID |
| `monitored_line_id` | string \| null | no | `null` | 제어 후보 평가 대상 선로. `null`이면 가장 심한 위반 선로 자동 선택 |
| `delta_mw` | float | no | `10.0` | 각 balanced redispatch 후보의 +ΔMW / -ΔMW 제어량 |
| `top_n` | int | no | `3` | 평가할 후보 수 |

주요 Response field:

| Field | Meaning |
|---|---|
| `target_line_id` | 실제 평가 대상 선로 |
| `target_selection` | 자동 선택 또는 사용자 지정 여부 |
| `contingency_new_violation_count` | 사고로 새로 발생한 위반 수 |
| `candidates` | 검증한 redispatch 후보 목록 |
| `best_candidate` | 시험한 후보 중 현재 ranking 기준 1순위 |
| `predicted_active_power_change_mw` | Sensitivity 기반 유효전력 조류 변화 예측 [MW] |
| `apparent_power_before_mva` | 사고 후 제어 전 피상전력 [MVA] |
| `apparent_power_after_mva` | 제어 후 피상전력 [MVA] |
| `improvement_mva` | 피상전력 감소량 [MVA] |
| `violation_remaining` | 목표 선로 위반 잔존 여부 |
| `new_violation_detected` | Redispatch로 인한 whole-network 신규 위반 여부 |

`best_candidate`는 **시험한 후보 중 최선**이며 OPF / SCED에 의한 최적 redispatch를 의미하지 않는다.

---

## 8. Candidate Ranking Semantics

현재 candidate ranking은 다음을 우선한다.

1. AC validation 및 Operator Strategy 수렴
2. Redispatch로 인한 신규 위반 없음
3. 제어 후 위반 설비 수가 적음
4. 목표 선로 개선량이 큼
5. 기존 Sensitivity candidate rank

따라서 `ac_validation_rank=1`은 현재 평가 규칙에서 가장 우선되는 시험 후보라는 의미이다.

---

## 9. Error Handling

Request validation 오류는 FastAPI / Pydantic 기본 validation response를 사용한다.

예:

- `delta_mw <= 0`
- `top_n < 1`
- 필수 ID 누락

Domain layer의 `ValueError`는 HTTP `400`으로 변환한다.

예:

- 존재하지 않는 선로 / 발전기 ID
- 발전기 출력 허용범위를 벗어난 redispatch
- 자동 target 선택이 불가능한 경우

`redispatch-validation` 및 `contingency-response`에서 입력은 유효하지만 물리해석 workflow가 정상 완료되지 않은 `RuntimeError`는 HTTP `422`로 변환한다.

Unexpected internal exception은 별도 Public error schema 없이 FastAPI 기본 500 handling을 따른다.

---

## 10. Integration Guidance

외부 Simulator에서는 목적에 따라 다음 endpoint를 선택한다.

```text
위험 사고 물리검증
  → /security-analysis

영향도가 큰 발전기 탐색
  → /sensitivity-analysis

외부에서 생성한 특정 Redispatch 검증
  → /redispatch-validation

사고부터 후보 생성·검증까지 한 번에 수행
  → /contingency-response
```

예를 들어 GNN / AI가 위험 사고 후보 또는 제어 후보를 생성하더라도 최종 계통 상태와 제어 효과 판단은 Physics API의 AC 기반 결과를 사용한다.

Simulator는 내부 PyPowSyBl object나 Domain dict 구조에 의존하지 않고 Public API response schema만 사용해야 한다.

---

## 11. Known Limitations

- 현재 Public API는 line contingency 중심이다.
- generator contingency endpoint는 아직 제공하지 않는다.
- `contingency-response`는 fixed-step balanced redispatch candidate evaluation이며 OPF / SCED가 아니다.
- Sensitivity는 local linearization이다.
- transient / dynamic stability 분석은 포함하지 않는다.
- `contingency-response` workflow는 내부적으로 network를 다시 load하는 경로가 있어 대량 반복 호출 성능은 추가 최적화 대상이다.
- 같은 bus / 설비군의 유사 generator ID가 동일하거나 매우 유사한 sensitivity를 가져 중복 성격의 후보가 나타날 수 있다.

---

## 12. Validation Status

최종 로컬 regression test 결과:

```text
19 passed
```

추가로 Swagger UI에서 4개 Physics POST endpoint 노출을 확인했고, `LINE-183-190` 사례에서 Sensitivity API 및 explicit Redispatch Validation API의 실제 HTTP 호출 결과를 확인하였다.

---

## 13. Versioning / Change Policy

현재 Public prefix는 `/api/v1`이다.

외부 Simulator가 의존하는 필드의 의미를 변경해야 할 경우:

1. `schemas.py` 수정
2. `adapters.py` 수정
3. adapter regression test 추가 / 수정
4. `API_SPEC.md` 갱신
5. 호환성을 깨는 변경이면 `/api/v2` 또는 명시적 migration 검토

Domain 내부 구현 변경은 Public schema가 유지되는 한 외부 Simulator 계약을 깨지 않는 방향을 우선한다.
