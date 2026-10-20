# 몽골 합성 계통 N-1 후 선로 조류 Sensitivity

Refs GangHyeokLee/ai-ems-agent#3

몽골 MN_35k 합성 시나리오 `reduced_27_q_limited_with_reactor`의 검증된 PyPowSyBl 모델에서, 선로 1개 사고 후
**감시 선로 유효전력의 합성 발전기 주입 민감도**를 PyPowSyBl AC Sensitivity Analysis로 계산한다. 결과는 모두 독립 AC PF
유한차분으로 검증한다.

> 실적 자료용 합성 시나리오 시연이다. 실제 몽골 운영계통 분석이 아니다. 합성 발전기 45대는 실제 발전소가 아니며,
> 결과는 **주입 민감도**일 뿐 재급전 권고가 아니다.

## 정의

| 항목 | 내용 |
|---|---|
| 엔진 | `pypowsybl.sensitivity.create_ac_analysis()` (OpenLoadFlow), PyPowSyBl 1.16.1 |
| 함수 | `BRANCH_ACTIVE_POWER_1`: 감시 선로 side 1(원본 `bus0`) 단자에서 선로로 들어가는 유효전력 [MW]. N-1 결과의 `p0`과 같은 부호 |
| 변수 | `INJECTION_ACTIVE_POWER`: 연결된 합성 발전기 유효전력 주입 [MW] |
| 값 | 사고 후 운전점에서 ∂P_side1 / ∂P_injection [MW/MW] (사고 전 값도 함께 제공) |
| 유효전력 균형 | `distributed_slack=False`: 주입 변화는 모두 Slack 모선 25(`SYNTHETIC_SLACK_25`)가 흡수한다. Slack 자신의 민감도는 정의상 0이며 순위에서 제외한다 |
| Load Flow 설정 | 몽골 전용 `ai_ems.mongolia_security.loadflow_parameters()`를 `pp.sensitivity.Parameters(load_flow_parameters=...)`로 전달한다(NAME Slack `VL_25`, FIRST_SLACK, flat start, Q 한계 비적용, eps 1e-8, 최대 30회, MAIN_CONNECTED). KPG `LOADFLOW_PARAMETERS`는 쓰지 않는다 |

부호 해석:
- 음수: 그 발전기 주입을 늘리면 bus0 단자에서 선로로 들어가는 P가 줄어든다(bus1 → bus0 방향 조류가 늘어남).
- 양수: 그 반대.

## 검증 (요청마다 수행)

1. 모델: Security Analysis와 같은 검증을 거친다(출처, 구조, ID 대응표, 주요 설정, 기본 PF ↔ PyPSA). 요청마다 XIIDM에서 독립 네트워크를 새로 불러온다.
2. 계통 분리 사고는 토폴로지로 판정한다. 분리되면 `NOT_EVALUATED_ISLANDING`으로 응답하고 순위를 만들지 않는다.
3. 사고 후 AC PF가 비수렴이면 `POST_CONTINGENCY_NOT_CONVERGED`로 응답하고 값을 내지 않는다.
4. 민감도·기준조류에 NaN/Infinity가 있으면 검증 실패(HTTP 422)로 처리한다.
5. 민감도 기준조류(사고 전/후)가 독립 AC PF 조류와 **1e-6 MW 이내**여야 한다.
6. **유한차분**: 사고 후 variant에서 순위 상위 2대 발전기의 `target_p`를 ±0.1 MW 바꿔 AC PF를 다시 계산하고 중심차분을 구한다. 허용오차는 `|AC − FD| ≤ 1e-4 + 0.01·|FD|` [MW/MW]로 구현 전에 정했다(LF 수렴오차 1e-6 MW / 0.2 MW ≈ 5e-6의 20배 이상). 불일치하면 결과를 반환하지 않는다(HTTP 422).
7. 기능 게이트: 모델 검증 시 `751261406-1_0` / `303374638-1_0` 자체 검증이 통과해야 `sensitivity_analysis_available=true`가 된다.

## 검증 결과 (사고 `751261406-1_0`, 계통 분리 없음)

테스트(`tests/test_mongolia_sensitivity.py`)에서 44대 전체(Slack 제외)를 독립 네트워크의 유한차분과 비교했다.

| 감시 선로 | 구간 | 사고 후 형태 | 사고 전 → 후 P [MW] | 0이 아닌 발전기 | 최대 \|AC−FD\| | 상위 민감도 |
|---|---|---|---|---|---|---|
| `303374638-1_0` | 83–13, 220 kV | 방사형(bridge) | 0.1234 → 0.4689 | 19 | 5.6e-7 | PV_7 −1.0133, PV_61 −1.0077, PV_106 −1.0024 |
| `484276912-1_0` (자동 추천) | 13–59, 220 kV | 메시 | 0.6472 → 0.9907 | 17 | 5.6e-7 | PV_7 −1.0074, PV_61 −1.0018, PV_106 −0.9965 |
| `259474322-1_0` | 33–2, 220 kV | 메시 | 0.0375 → 0.3605 | 44 | 5.4e-7 | PV_7 −0.9471, PV_61 −0.9418, PV_106 −0.9369 (최대 +0.0126) |

- Slack 유한차분은 0이다(Slack 흡수 확인).
- `303374638-1_0`의 사고 후 조류 변화 +0.3455 MW는 Security Analysis N-1 결과와 같다.
- 사고 후 이 계통은 155개 선로 중 33개만 메시이고 나머지는 방사형이다. 방사형 감시 선로에서는 하류 발전기 민감도가 약 −1(손실 효과로 ±1% 차이), 상류 발전기는 0이 된다. 이는 계산 오류가 아니라 합성 모델 토폴로지의 결과이며, 응답의 `monitored_line_radial_after_contingency`와 `radial_note`로 표시한다.

계통 분리 사고 `222087807-1_0`, `484333456-1_0`은 `NOT_EVALUATED_ISLANDING`으로 표시한다(순위 없음). 분리 섬 처리 정책은 별도 Issue로 정한다.

## 감시 선로 선택

- 사용자가 `monitored_line_id`를 지정할 수 있다. 존재하지 않는 선로, 기준 PF 제외 선로, 사고 선로 자신은 422로 처리한다.
- 지정하지 않으면 자동 추천한다. 사고 후 주 계통에서 |ΔP|가 가장 큰 **메시(비방사형)** 선로를 고르고, 없으면 |ΔP| 최대 선로를 고른다. KPG의 "위반 선로 자동 선택"에 의존하지 않는다(몽골은 사고 후 위반 0건). 응답의 `monitored_line_selection.alternatives`에 |ΔP| 상위 후보와 방사형 여부를 제공한다.

## API

`GET /api/grids/mn-osm-35k/sensitivity-analysis?outage_line_id=<원본 ID>&monitored_line_id=<선택>&top_n=10`

주요 응답 필드:
- 상태·선로: `analysis_status`, `sensitivity_available`, `outage_line`, `monitored_line`(구간·kV·정격), `monitored_line_selection`, `monitored_line_radial_after_contingency`, `radial_note`
- 정의: `function`, `variable`, `unit`, `slack_balance`
- 조류: `base_flow_mw`, `post_flow_mw`, `post_loading_percent`, `reference_check`
- 순위: `candidates[]`(`rank`, `generator_id`, `bus_id`, `voltage_kv`, `control`, `target_p_mw`, `sensitivity`, `pre_contingency_sensitivity`, `synthetic_generator=true`), `excluded_from_ranking`, `zero_sensitivity_count`
- 검증·주의: `finite_difference_check`, `model_caveats`

오류 응답: 모델 없음·검증 실패 503, 없는 사고 선로 404, 제외 선로·잘못된 감시 선로·민감도 검증 실패 422.

KPG `/api/sensitivity-analysis`와 `LOADFLOW_PARAMETERS`는 변경하지 않았다.

## UI

- 몽골 Security Analysis 결과가 수렴하고 계통 분리가 없을 때만 Sensitivity 버튼이 켜진다. 분리 사고에서는 "미평가" 문구를 표시한다.
- 감시 선로 입력(비우면 자동 추천)에는 사고 후 |ΔP| 상위 선로를 후보로 제공한다.
- 결과 패널: 감시 선로(자동/사용자), 사고 전→후 조류, 정의(단자·단위·Slack 흡수), 방사형 안내, 합성 발전기 순위(사고 후·사고 전 민감도, "합성 · 실제 발전소 아님"), 유한차분 검증 결과, 주의 문구.
- 지도: 사고 선로는 빨강, 감시 선로는 분홍 점선과 중간점 링 '감시 선로'(짧은 선로도 보이도록), 합성 발전기 순위는 청록 사각형 번호(실제 발전소 레이어와 구분)로 표시한다. '분석 결과' 레이어를 끄면 모두 사라지고 기본 PF 색상으로 돌아간다.
- 요청 식별자(`securityRequestSeq`, `sensitivityRequestSeq`): 같은 몽골 계통에서도 이전 요청의 늦은 응답이 최신 결과를 덮어쓰지 않는다. 새 Security 실행은 진행 중인 Sensitivity 응답을 무효화한다. 계통 전환 시 둘 다 무효화한다.
- 몽골 AI 채팅은 합성 PF 조회 전용으로 유지한다. 민감도는 채팅에서 호출할 수 없다.

## 검증 명령

```bash
cd ~/Projects/ai-ems-agent
.venv/bin/python -m pytest -q tests/test_mongolia_sensitivity.py tests/test_mongolia_security.py tests/test_ui_mongolia_chat.py
.venv/bin/python -m pytest -q tests
```

## 한계

- 사고 후 운전점의 선형화 값이므로 큰 출력 변화에는 그대로 적용되지 않는다.
- 분리 섬 민감도는 정책 미정으로 미평가한다.
- 합성 부하가 작아(감시 선로 부하율 < 1 %) 실제 혼잡 완화 효과를 시연할 수 없다. 정격·부하는 조정하지 않았다.
- 주입 변화는 Slack 단일 흡수를 가정한다. 분산 Slack이나 다른 보상 방식에서는 값이 달라진다.
- 발전기 Q 한계는 기본 PF 검증 조건과 같이 적용하지 않는다.
