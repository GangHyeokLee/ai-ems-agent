# AI-EMS 민감도 기반 Adaptive Redispatch 개선 설계 및 테스트 계획

> 작성일: 2026-10-15  
> 대상: \`GangHyeokLee/ai-ems-agent\` / \`main\`  
> 기준 환경: KPG-193 v2.0, PyPowSyBl 1.16.1  
> 문서 상태: **설계 제안 (미구현·미검증)**  
> 1차 적용 범위: Generator N-1 Corrective-action Workflow

## 1. 목적과 성공 기준

현재 PoC는 발전기 탈락 이후 **Security Analysis → 사고 후 민감도 → 발전기 Up/Down 조합 → 고정 ±10 MW Redispatch → AC Power Flow → Operator Strategy Security 재검증**을 수행한다. 하지만 기본 제어량이 고정되어 있으며, 목표 선로 개선에 집중해 전체 위반의 심각도를 충분히 비교하지 못한다.

개선 목표는 다음과 같다.

1. 민감도를 사용해 발전기 조합별 **제어량 초기값**을 추정한다.
2. 발전기 **Target P / 사고 후 Actual P와 Min/Max P**로 실행 가능 범위를 제한한다.
3. 여러 제어량을 실제 **AC Load Flow**로 검증한다.
4. 각 후보를 **해당 발전기 사고 + Operator Strategy 기반 Security Analysis**로 검증하고 신규·잔존·악화 위반을 비교한다.
5. **전체 계통의 잔존 위반 심각도 최소화**를 우선하되, 검증 가능한 추천 후보가 없으면 그 이유를 설명한다.
6. 기존 고정 10 MW 제어와 기능·성능을 비교한다.

이는 연구용 **Heuristic Corrective-action Search**이다. OPF/SCED 최적해, 실계통 제어 가능성, 동특성 안정성을 보장하지 않는다.

## 2. 현재 구현과 개선 범위

| 위치 | 현재 확인된 기능 | 이번 개선 방향 |
|---|---|---|
| \`src/ai_ems/tools/generator_control_tools.py\` | 사고 후 단일 선로 민감도, 발전기 조합, 기본 ±10 MW, Min/Max 검사, AC·Security 검증 | 가변 ΔMW 추정·탐색 및 후보별 검증 |
| \`src/ai_ems/tools/sensitivity_tools.py\` | Generator N-1 후 감시 선로 P 조류 민감도 | 기존 계산 재사용. 필요 시 양단/다중 선로 지원 검토 |
| \`src/ai_ems/tools/workflow_tools.py\` | 사고 → 후보 → 검증, 수렴·신규 위반·위반 수·개선량 기반 정렬 | 전체 위반 심각도 평가, 추천 가능성 분리, 부분 실패 허용 |
| \`src/ai_ems/tools/generator_risk_tools.py\` | Generator N-1 전수 Screening → 상위 위험 사고 대응 분석 | 기존 진입 경로 유지 |
| \`src/ai_ems/agent/tools.py\` 및 formatter | 고수준 Tool과 결정적 결과 설명 | Domain 실험 완료 후 옵션·응답 연결 |

### 포함

- Generator N-1에 대한 1대 Up / 1대 Down **Balanced Redispatch**
- 민감도 기반 조합 선정과 제어량 초기 추정
- 발전기 출력 상한/하한과 연결 상태 검사
- 독립적인 AC Power Flow / 해당 사고의 전체 설비 Security 위반 평가
- 후보별 예외 처리, 전체 위반 심각도 정렬, 기존 10 MW 방식과 비교

### 이번 범위에서 제외

- 3대 이상 동시 제어, OPF/SCED, 운전비용, ramp, 예비력, AGC, 운전 권한
- 부하 차단, 토폴로지 제어, 주파수/회전자각 등 동특성
- **모든** N-1 사고에 대한 제어 후 전수 재평가
- LLM 직접 제어량 생성 및 초기 Public Physics API 호환성 변경

Line N-1 기존 구현은 유지한다. Generator N-1 실험에서 유효성 확인 후 공통화 여부를 검토한다.

## 3. 알고리즘 명세

### 3.1 STEP 1: 초기 Security 상태 확정

입력: \`case_path\`, \`outage_generator_id\`, \`slack_mode\`, \`balance_type\`, 선택적 \`monitored_line_id\`.

1. 동일한 KPG 모델과 Load Flow 파라미터를 사용한다.
2. 해당 발전기 N-1 사고의 AC Security Analysis를 수행한다.
3. Pre/Post 수렴, 위반 종류·설비·한계·값, 목표 선로 양단 P/Q/S를 기록한다.
4. 사고 후 비수렴 또는 계통 분리 등으로 유효한 제어 시작 상태를 얻지 못하면 그 원인을 반환하고 일반적인 Redispatch 추천을 중단한다.
5. 목표 선로가 지정되지 않으면 기존 과부하 우선순위 기준으로 선택한다. **목표 선로 한 개의 개선이 최종 추천 기준이 되지는 않는다.**

### 3.2 STEP 2: 민감도 기반 발전기 조합

기존 사고 후 민감도 계산을 재사용한다. 생존·연결 발전기 \`g_up\`, \`g_down\`에 대해

\[
k = s_{\mathrm{up}} - s_{\mathrm{down}}
\]

\[
P_1(\Delta P)\approx P_{1,0} + k\Delta P
\]

- \`s_up\`, \`s_down\`: 발전기 유효전력 주입에 대한 감시 선로 **P1 유효전력 조류 민감도**(MW/MW)
- \`P1,0\`: 발전기 사고 후 P1 조류(MW)
- \`ΔP\`: Up 발전기 +ΔMW, Down 발전기 -ΔMW의 요청 제어량

조합 생성 요건: 탈락 발전기 제외, 두 발전기 서로 다름, 연결 상태, 유효한 민감도, **최소 1개 유효 제어량**을 실행할 수 있는 출력 여유. 기존 동일 Up Bus / Down Bus 조합 중복 제거 유지.

**중요:** 현재 고정 10 MW를 적용할 수 없다는 이유로 조합 전체를 탈락시키면, 2~9 MW의 가능성을 놓칠 수 있다. 조합 유효성과 후보별 제어량 유효성을 분리한다.

### 3.3 STEP 3: 발전기별 제어 가능 범위

후보 발전기별 사고 후 **Target P와 Actual P를 모두** 고려한다.

\[
\Delta P_{\max} =
\min(
P_{\max}^{up}-P_{target}^{up},
P_{\max}^{up}-P_{actual}^{up},
P_{target}^{down}-P_{\min}^{down},
P_{actual}^{down}-P_{\min}^{down}
)
\]

- 음수 또는 0 이하라면 해당 Up/Down 조합은 양의 제어량에 대해 불가능하다.
- \`None\`/NaN/비유한 Min/Max/Target/Actual 값의 정책을 명시적으로 정의하며, **미확인 출력 제약을 무제한 출력 여유로 간주하지 않는다.**
- 계산된 상한은 초기 스크리닝의 보수적인 값이다. 실제 제어 후 발전기 출력은 **반드시 AC 결과로 재확인**한다.
- 현재 범위는 발전기 물리적 Min/Max만 고려하므로, 운전가능 예비력·Ramp·AGC 보장은 하지 않는다.

### 3.4 STEP 4: 민감도 기반 제어량 **초기 추정**

현재 민감도는 **MW** 유효전력 조류 변화에 대한 것이지만, 대표 위반은 **MVA** 피상전력 한계이다. \`MVA 초과량 / MW 민감도\`를 직접 수행하는 식은 사용하지 않는다.

안전한 1차 근사(선로 측면 일치, 무효전력 변동이 충분히 작다는 가정이 확인될 때):

\[
P_{\mathrm{allow}}\approx\sqrt{L_S^2-Q_{1,0}^2}
\]

\[
\Delta P_{\mathrm{est}}\approx
\frac{P_{1,\mathrm{target}}-P_{1,0}}{k}
\]

- \`L_S\`: 해당 측면의 피상전력 한계(MVA)
- \`Q1,0\`: 기준 상태의 무효전력(Mvar)
- \`P1,target\`: 현재 P1 부호와 목표 조류 감소 방향을 반영한 유효전력 목표(MW)
- \`k\`: P1 흐름에 대한 국소 민감도

추정 사용 전 조건:

1. \`L_S > 0\`, \`L_S^2-Q1,0^2 >= 0\`; 미충족이면 근사 불가능
2. \`|k|\`가 지나치게 작거나 비유한 값이면 추정 불가능
3. 요청 ΔMW가 양수이고 허용 범위 내여야 함
4. 추정된 조류 변화가 해당 **P1 방향**의 절댓값을 감소시켜야 함
5. 실제 한계가 P2 측/전류/전압 기준이면 동일한 P1 근사로 해결량을 단정하지 않음

**출력은 최적 제어량이 아니라 AC 검증 시작점**이다. 신뢰할 수 없는 조건이면 제한된 보수적 제어량 샘플링으로 대체한다.

### 3.5 STEP 5: 여러 ΔMW 후보 탐색

제안 초기 실험 설정:

- 상위 Up/Down Bus 조합 **최대 3개**
- 유효한 추정값 기준 \`[0.25, 0.5, 0.75, 1.0, 1.25] × ΔMW_est\` 탐색
- 허용 상한에 맞춰 clipping, 중복 값 제거
- 10 MW fixed 기준 후보도 실행 가능 시 비교용으로 포함
- 추정 불가 시 허용 범위 내 보수적 단계 샘플링
- 조합당 실제 검증 후보 수 및 전체 계산 예산을 설정값으로 제한
- 필요 시 1차 최우수 후보 주변의 좁은 범위를 추가 탐색

초기 목표는 약 **3개 조합 × 최대 5개 내외 제어량**이다. fixed 10 MW 비교 후보 포함 여부에 따라 실제 개수가 달라지므로, **호출 횟수 제한을 구현에서 명확히 정의**한다.

모든 후보는 **동일한 사고 전 Base Network에서 새로 시작해** 사고 + 조치 적용 후 검증한다. 앞 후보의 발전기 상태가 다음 후보에 누적되지 않도록 한다.

### 3.6 STEP 6: AC 재계산

각 후보별:

1. Base Network 재로드/독립 복제
2. 대상 Generator N-1 사고 반영
3. 선택 발전기 각각 +ΔMW, -ΔMW Target 적용
4. AC Power Flow 실행
5. 수렴/비수렴 확인
6. 선로 P1/Q1/P2/Q2 및 실제 MVA 산출
7. **관련 발전기 전체**의 실제 출력·Min/Max 검토
8. 요청량과 실제 발전기 출력 변화량 구분

비수렴, 출력 제약 위반, 계산 예외는 해당 후보의 실패로 처리하고 다른 후보는 계속 검토한다.

### 3.7 STEP 7: 해당 사고의 Security 재검증

동일한 Load Flow balancing 가정 하에, 사고 후 Operator Strategy로 Up/Down 발전기의 유효전력 제어를 적용하여 Security Analysis를 재실행한다.

수집 항목:

- \`post_contingency_status\`, \`operator_strategy_status\`
- 신규 위반, 해소 위반, 잔존 위반
- 기존 위반의 **악화/개선** 정도
- 위반의 종류·단위·한계·값·초과량
- 고유 위반 설비 수와 위반 항목 수

**용어 주의:** 이 단계의 \`whole_network_validation\`은 **해당 발전기 N-1 사고와 제어안에 대해 전체 설비 위반을 평가**하는 것이지, 모든 독립 N-1 사고를 전수 실행한다는 의미가 아니다.

## 4. 계통 위험 평가와 추천 기준

### 4.1 위반 정규화

위반 유형과 단위를 섞어서 직접 합하지 않는다.

- \`APPARENT_POWER\`: \`max(0, (S - L) / L)\`
- \`CURRENT\`: \`max(0, (I - L) / L)\`
- \`ACTIVE_POWER\`: 양의 한계·방향 정의 확인 후 동일 유형으로 별도 계산
- \`HIGH_VOLTAGE\`: \`max(0, (V - V_max) / V_max)\`
- \`LOW_VOLTAGE\`: \`max(0, (V_min - V) / V_min)\`

한계값이 0/누락/비유한 값이면 정규화 불능으로 명시한다. **위반이 없다는 뜻으로 0 처리하지 않는다.** 양단·중복 위반 기록은 기존 그룹화 규칙을 활용하되, 설비 수와 제한별 위반 항목 수를 구별한다.

이 점수는 제한 초과 정도를 비교하는 **대리 지표**이며 실제 사고 확률이나 동특성 위험도는 아니다.

### 4.2 후보별 저장 지표

| 필드 | 의미 |
|---|---|
| \`ac_converged\` | 제어 후 AC 수렴 |
| \`security_converged\` | Operator Strategy 수렴 |
| \`new_violation_count\` | 제어 때문에 생긴 신규 위반 |
| \`worsened_existing_count\` | 기존 위반 중 허용오차를 넘어 악화한 항목 수 |
| \`remaining_violation_count\` | 제어 후 잔존 위반 항목 수 |
| \`remaining_equipment_count\` | 제어 후 고유 위반 설비 수 |
| \`max_normalized_excess\` | 최대 위반 심각도 |
| \`total_normalized_excess\` | 위반 심각도 합계(동일 유형별 값도 보존) |
| \`target_loading_percent\` | 목표 설비의 실제 제어 후 부하율 |
| \`requested_delta_mw\` | 요청한 제어량 |
| \`actual_generation_changes\` | 재계산 후 실제 발전기 출력 변화 |

### 4.3 추천 가능성 판정

**자동 추천 가능** 후보의 1차 제안 조건:

- AC와 Operator Strategy 모두 수렴
- 유효한 발전기 출력 제약 통과
- 신규 위반이 없음
- 기존 위반이 허용 오차를 넘게 악화되지 않음
- 전체 위반 심각도가 기준 사고 후 상태보다 개선됨

수치 허용 오차는 테스트 결과를 보고 명시적으로 확정한다. 불확실/검증 실패 결과를 안전한 결과처럼 반환하지 않는다.

검증된 후보가 하나도 없다면 \`recommended_candidate = null\`과 함께 실패/미해소 이유를 반환한다. 비교에 의미가 있다면 \`best_tested_candidate\`는 **검증 결과를 보유한 시험 후보**로만 제공하되, 운영 추천과 명확히 구분한다.

### 4.4 정렬 정책 (검토안)

추천 가능 후보의 우선순위:

1. 최대 정규화 위반 심각도 작음
2. 정규화 위반 심각도 합계 작음
3. 잔존 위반 항목 수·설비 수 작음
4. 목표 선로 개선량 큼
5. 요청 제어량 작음

모든 위반이 해소된 후보가 있다면 추가 제어량 최소화를 우선 검토한다. 서로 다른 제한 유형의 정규화 점수 합산 방식/가중치는 **실험 전 확정할 설계 결정**이며, 유형별 점수를 별도로 출력해 과도한 의미 부여를 방지한다.

## 5. 반환 데이터 계약 (제안)

기존 반환 데이터의 의미는 우선 보존하고, 새 필드로 확장한다.

상위 레벨:

\`\`\`text
analysis_type
analysis_status
outage_generator_id
monitored_line_id
slack_mode / balance_type
search_mode                 # fixed | adaptive
baseline_security
candidate_count
valid_candidate_count
best_tested_candidate
recommended_candidate
candidates
\`\`\`

각 후보:

\`\`\`text
up_generator_id / down_generator_id
estimated_delta_mw
requested_delta_mw
feasible_delta_max_mw
sensitivity_prediction      # P-flow MW
ac_validation               # actual AC calculation
whole_network_validation    # this outage + operator strategy
risk_metrics
recommendation_eligible
rejection_reasons
evaluation_status
\`\`\`

상태 구분(제안):

- \`COMPLETED\`
- \`NO_OVERLOADED_LINE\`
- \`SECURITY_NOT_CONVERGED\`
- \`NO_FEASIBLE_GENERATOR_PAIR\`
- \`NO_VALID_DELTA\`
- \`NO_RECOMMENDABLE_CANDIDATE\`
- \`PARTIAL_VALIDATION_FAILURE\`

전체 Workflow 상태와 개별 후보 상태는 분리한다.

## 6. 변경 파일 및 단계별 작업 계획

| 우선순위 | 파일 | 계획 |
|---|---|---|
| P0 | \`generator_control_tools.py\` | 제어 가능 Δ 계산·초기 Δ 추정·후보 생성 분리 |
| P0 | \`workflow_tools.py\` | 독립 후보 검증 반복·부분 실패 처리·계통 위험도 기반 후보 정렬 |
| P0 | 신규/기존 \`tests/\` | 순수 함수 단위 테스트·기존 회귀 검증 |
| P1 | \`sensitivity_tools.py\` | P2/다중 선로 민감도 필요 여부 실험 후 결정 |
| P1 | \`generator_risk_tools.py\` | 고수준 연계에서 신규 탐색 모드 선택 지원 검토 |
| P2 | \`agent/tools.py\` / \`agent/formatters/\` | Domain 실험 종료 후 Adaptive Tool 옵션·설명 연결 |
| P2 | \`docs/experiments/\` | 실제 KPG 결과·실행 비용 기록 |
| 후속 | \`api/\` | 기존 Public API 계약 검토 후 필요 시 확장 |

**기존 호환성:** \`delta_mw=10.0\` fixed 동작 유지. 신규 모드는 \`search_mode="adaptive"\` 같은 별도 옵션(명칭 미확정)으로 시작. API/Agent/UI 변경은 Domain 검증 이후에 수행한다.

### 구현 전에 확정할 결정

- [ ] 순수 함수 경계: \`calculate_feasible_delta\`, \`estimate_initial_delta\`, \`generate_delta_samples\`, \`score_validation\` 등
- [ ] 제어량 추정 실패 시 fallback 탐색 정책 및 최소 단계
- [ ] 최대 조합 수·제어량 수·AC/Security 호출 예산
- [ ] 동일 Bus pair 중복 제거 적용 시점
- [ ] P1/P2 감시·Limit 비교 정책
- [ ] 유형별 정규화 및 순위 정책, 수치 허용오차
- [ ] \`best_tested_candidate\`와 \`recommended_candidate\` 의미/계약

## 7. 단위 테스트 계획

| ID | 대상 | 기대 결과 |
|---|---|---|
| UT-01 | 양·음 민감도 조합 | 올바른 조류 감소 방향 판정 |
| UT-02 | \`k≈0\` | 초기 추정 불가, 0 나눗셈 방지 |
| UT-03 | \`Q²>L²\` 또는 Limit 없음 | MVA→P 근사 금지 |
| UT-04 | 추정 Δ가 출력 한계 초과 | 유효 범위 제한 |
| UT-05 | Min/Max/Target/Actual 누락·NaN | 안전한 실패/스킵 처리 |
| UT-06 | 동일 Up/Down 발전기 | 후보 배제 |
| UT-07 | 동일 Bus pair | 대표 조합만 유지 |
| UT-08 | AC 비수렴 | 후보 추천 제외 |
| UT-09 | 신규 위반 | 후보 추천 제외 |
| UT-10 | 기존 위반 악화 | 악화 감지·추천 제외 |
| UT-11 | 위반 수와 심각도 상충 | 정의한 정렬 우선순위 적용 |
| UT-12 | 단일 후보 예외 | 다른 후보 검증 계속 |
| UT-13 | AC 계산 후 실제 발전량 한계 | 제약 재확인 |
| UT-14 | MW/MVA/A/pu 혼합 | 단위별 독립·정규화 검사 |
| UT-15 | 동일 입력 반복 | 결정적 정렬 |
| UT-16 | 기존 fixed 모드 | 동일 계약·회귀 유지 |

수치 추정, 후보 필터, 정렬 등 **실제 KPG 파일 없이 가능한 순수 함수**부터 테스트한다.

## 8. KPG-193 통합 실험 계획

### EXP-01: GEN-124#1 대표 사고 재현과 기존 방식 비교

기존 기록(2026-09-30):

| 지표 | 기록값 |
|---|---|
| 발전기 사고 | \`GEN-124#1\` |
| Load Flow balancing | Distributed / \`PROPORTIONAL_TO_GENERATION_P_MAX\` |
| 주요 과부하 | \`LINE-134-193\` |
| 한계 | RateC = 2172 MVA |
| 사고 후 피상전력 | 2293.71 MVA |
| 사고 후 부하율 | 105.60% |
| 기존 최선 시험 후보 | \`GEN-106 +10 MW / GEN-193 -10 MW\` |
| 제어 후 피상전력 | 2288.11 MVA |
| 제어 후 부하율 | 105.35% |
| 과부하 해소 | 미해소 |
| 새 위반 | 없음 |

**가설 H1:** Adaptive 탐색은 고정 10 MW보다 계통 전체 위반 심각도가 낮은 후보를 발견할 가능성이 있다. 가설일 뿐 성능 개선은 아직 확인되지 않았다.

고정 항목: KPG-193 버전·PyPowSyBl 버전·사고·balancing·설비 한계.  
변수: 발전기 조합, 후보 제어량, 탐색 예산.

측정: Δ 추정/실제 적용, 수렴, 목표 부하율, 잔존/신규/악화 위반, 정규화 위험도, AC/Security 호출 횟수·시간.

### EXP-02: ΔMW와 실제 AC 개선 효과

동일 발전기 조합에 복수 ΔMW를 적용한다. 제어량 증가에 따른 조류 감소·정체·역전, 민감도 선형 근사 유효범위, 제약 및 비수렴 경계를 확인한다.

**MW 예상값과 MVA 검증값은 동일 물리량으로 직접 비교하지 않는다.** 별도 곡선으로 표시한다.

### EXP-03: 단일 목표 개선 vs 전체 계통 위험 최소화

A: 기존 정렬(목표 선로 개선 중심).  
B: 신규 정렬(신규·악화·잔존 위반 및 심각도).

동일 후보 집합에서 최선 후보가 달라지는지, 목표 선로만 좋아지는 후보가 전체 계통 관점에서는 불리한지 검증한다.

### EXP-04: 발전기 N-1 다수 사례

실제 Screening 결과에서 성격이 다른 3~5개 사고를 선정한다.

- 단일 과부하
- 복수 위반
- 출력 여유 부족
- 제어 후보 없음 또는 계산 실패

사례는 실제 결과 확인 후 정한다.

### 통합/회귀

- IT-01: 기존 대표 사고 결과 재현
- IT-02: 출력 제한 내 과부하 완화·해소 후보 존재 여부
- IT-03: 목표 선로 개선 + 다른 설비 신규 위반 처리
- IT-04: 후보 일부 비수렴/오류 시 계속 수행
- IT-05: 다른 Generator N-1 사고 확장
- IT-06: 기존 fixed 방식, Agent formatter, Public Physics API 회귀

## 9. 성공 조건과 해석 원칙

### 기능적 성공

- 유효한 후보·제어량 생성과 출력 제약 검사
- 초기 추정 실패 시 안전한 fallback 또는 중단
- 후보별 독립적인 AC 및 Security 검증
- 신규·기존 악화·잔존 위반 구분
- 추천 후보와 비교용 시험 후보 구분
- 기존 fixed 모드 회귀 유지

### 연구 성과로 인정 가능한 결과

1. 고정 10 MW보다 위반 심각도 감소
2. 제어량 조정으로 과부하 완전 해소
3. 제어량을 늘려도 전체 계통이 악화되거나 위반이 잔존함을 확인
4. 출력 제약 내에서 제어 가능 후보가 없음을 확인
5. 민감도 초기 추정으로 검증 호출 수가 줄어듦

모든 사고에서 과부하 완전 해소를 보장하지 않는다. 실패 사례의 물리적 원인과 탐색 범위를 설명하는 것 역시 유효한 PoC 결과이다.

## 10. 실행 순서

1. **설계 검토:** 알고리즘·정규화·탐색 예산·함수 계약 확정
2. **순수 함수 구현:** Δ 상한/초기값/샘플 생성/위험도 정렬
3. **단위 테스트:** 수학·제약·오류·결정적 순위
4. **KPG 연계:** GEN-124#1 기존 10 MW 결과 재현
5. **Adaptive 시험:** 후보별 AC/Security 비교
6. **확장 실험:** 다른 N-1 사고 및 다중 위반
7. **Agent 연동:** 추천 가능성·잔존 위험 설명
8. **문서 갱신:** 실제 실험값으로 검증 결과 분리 기록

## 11. AI EMS에서의 의미

기존 PoC가 **어떤 발전기 조합이 과부하를 완화할 수 있는가**를 실험했다면, 이번 개선은 **어느 정도 조정해야 전체 계통의 잔존 위반이 줄어드는가**를 검증하는 단계다.

LLM/Agent는 자연어 이해와 도구 선택·결과 설명을 맡고, 실제 계통 상태·제어 효과 판정은 PyPowSyBl 물리해석 결과를 사용한다.

> **현재 상태:** 설계 초안 등록 단계. 구현·AC 검증·성능 개선은 아직 수행하지 않았다.

## 12. 근거 코드 및 기록

- [Generator Redispatch Domain](../../src/ai_ems/tools/generator_control_tools.py)
- [Sensitivity Domain](../../src/ai_ems/tools/sensitivity_tools.py)
- [Corrective-action Workflow](../../src/ai_ems/tools/workflow_tools.py)
- [Generator Risk Workflow](../../src/ai_ems/tools/generator_risk_tools.py)
- [Agent Tool](../../src/ai_ems/agent/tools.py)
- [KPG-193 발전기 사고 후 민감도 기반 Redispatch 후보 생성 및 재검증](../experiments/KPG-193%20발전기%20사고%20후%20민감도%20기반%20Redispatch%20후보%20생성%20및%20재검증.md)
