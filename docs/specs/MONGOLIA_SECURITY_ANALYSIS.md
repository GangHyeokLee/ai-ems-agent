# 몽골 합성 계통 선로 N-1 Security Analysis

몽골 MN_35k 합성 시나리오 `reduced_27_q_limited_with_reactor`(166모선/156선로/27변압기)를 PyPowSyBl XIIDM으로 불러와,
선로 1개 탈락 Security Analysis를 실제로 계산하고 AI-EMS 지도에 표시한다.

> 합성 시험 시나리오이며 실제 몽골 운전계통·측정값이 아니다. 계통 분리와 공급 상실 부하도 합성 부하 기준이다.

## 모델 공급과 경로

모델과 검증 기록은 `mongolia-grid-poc`(읽기 전용)가 생성한다. AI-EMS는 PyPSA를 사용하지 않고 미리 생성된 XIIDM만 읽는다.

| 설정(환경변수) | 기본값 (`PROJECT_ROOT` 기준) | 내용 |
|---|---|---|
| `AI_EMS_MN_XIIDM_FILE` | `../mongolia-grid-poc/pypsa-earth/networks/MN_35k_poc/powsybl/mn35k_synthetic_powsybl.xiidm` | PyPowSyBl 모델 |
| `AI_EMS_MN_POWSYBL_REPORTS_DIR` | `../mongolia-grid-poc/reports/pypowsybl_conversion` | `element_mapping.csv`, `pf_generator_comparison.csv`, `conversion_validation.json`, `input_hashes.json` |

경로는 서버 설정으로만 지정할 수 있고, HTTP 요청에서는 바꿀 수 없다. XIIDM은 Git에서 제외되는 생성물이다. 파일이 없으면 다음 명령으로 다시 만든다(원본 NetCDF는 변경하지 않음).

```bash
cd ~/Projects/mongolia-grid-poc/pypsa-earth
pixi run --frozen ../.venv-powsybl/bin/python ../scripts/convert_mn35k_to_powsybl.py
```

## 모델 검증 (`ai_ems.mongolia_security`)

XIIDM은 `caseDate` 때문에 저장할 때마다 해시가 바뀐다. 따라서 파일 해시는 쓰지 않고 아래 근거로 동일성을 판정한다. 하나라도 실패하면 몽골 Security Analysis만 비활성화하고, 지도·PF·채팅은 그대로 동작한다.

1. **출처**: AI-EMS의 검증된 합성 PF 미리보기(`metadata.source_sha256`)가 변환 입력 NetCDF SHA-256(`input_hashes.json`)과 같고, 시나리오 ID가 `conversion_validation.json`과 같아야 한다.
2. **구조**: 설비 수가 166/156/27/45/166/1이어야 한다. ID 대응표 IIDM ID 집합이 XIIDM과 같고, 선로·변압기의 원본 ID(`name`)와 양단 VL이 대응표와 같으며, 해석 대상 집합이 PF 미리보기와 같아야 한다.
3. **주요 설정**: Slack 발전기가 `VL_25`에 있고, 전압제어 발전기가 44대, `SYNTHETIC_PV_10`이 PQ(−100 MVAr), 리액터 b = −20/220² S, 부하 합계가 25.6 MW / 8.414313 MVAr이어야 한다.
4. **기본 AC PF**: 수렴하고 기준모선·Slack이 `VL_25_0`이어야 한다. PyPSA 기준값과의 오차가 사전 등록 허용오차 이내여야 한다(전압 1e-5 pu, 각도 1e-4°, 선로·변압기·발전기 P/Q 1e-3, 리액터 1e-3 MVAr, Slack P = target_p + mismatch). 기준값은 bus/line/transformer는 PF 미리보기, 발전기는 `pf_generator_comparison.csv`의 PyPSA 열이다.

## Load Flow 설정 (KPG `LOADFLOW_PARAMETERS`와 별개)

`VoltageInitMode.UNIFORM_VALUES`, `distributed_slack=False`, `use_reactive_limits=False`, `ComponentMode.MAIN_CONNECTED`, 변압기·분로 전압제어 off,
`slackBusSelectionMode=NAME`, `slackBusesIds=VL_25`, `referenceBusSelectionMode=FIRST_SLACK`,
`newtonRaphsonConvEpsPerEq=1e-8`, `maxNewtonRaphsonIterations=30`.
mongolia-grid-poc `scripts/mn35k_powsybl.py`의 검증된 설정과 같다.

## API

| Endpoint | 내용 |
|---|---|
| `GET /api/grids/mn-osm-35k/security-status` | 검증 결과(출처, 구조, PF 수렴, 기준값 오차) 또는 비활성 사유 |
| `GET /api/grids/mn-osm-35k/security-analysis?outage_line_id=<원본 선로 ID>` | 실제 PyPowSyBl AC Security Analysis |
| `GET /api/security-analysis` | KPG 기존 API (변경 없음) |

오류 응답: 존재하지 않는 선로 404, 기준 PF 해석 제외 선로 422, 모델 없음·검증 실패 503.

요청마다 XIIDM에서 독립 네트워크를 새로 불러와 기본 상태를 다시 검증한 뒤 계산한다. 이 때문에 사고 상태가 누적되거나 동시 요청 간에 섞이지 않고, KPG 네트워크에도 영향이 없다(요청당 약 0.08초).

응답에는 KPG UI 호환 필드(`outage_line_id`, `base_converged`, `pre_status`, `post_status`, `analysis_status`, `violation_assessment_available`, `violated_equipment_count`, `violations`)와 다음 몽골 필드가 들어간다.

- `grid_id`, `scenario_id`, `synthetic_scenario`, `actual_operating_data`
- `main_component_converged`, `island_detected`, `isolated_bus_count`, `isolated_buses`, `isolated_load_mw`, `isolated_generator_ids`, `isolated_line_ids`, `isolated_transformer_ids`, `island_pf_computed=false`, `island_check`
- `post_voltage_min_pu/max_pu`, `max_voltage_change_pu/_bus_id`, `max_line_p_change_mw/_id`, `max_line_q_change_mvar/_id`, `post_max_line_loading_percent/_id`, `top_voltage_changes`, `top_line_flow_changes`
- `generator_q_screening`, `screening_limits`, `model_caveats`

위반 항목에는 원본 설비 ID, 설비 종류, 제한 유형, 정격·계산값·위반량·단위가 들어간다.

사고 후 PF가 수렴하지 않거나 SA 기준 상태가 수렴하지 않으면 `analysis_status=SECURITY_NOT_CONVERGED`가 되고, 수치 필드는 모두 `null`이다.

### 계통 분리

- `MAIN_CONNECTED`로 Slack이 있는 주 계통만 계산한다. 분리된 섬의 PF는 실행하지 않으며 전압·조류 값도 없다. 0 pu를 넣지 않는다.
- 분리 모선은 토폴로지(선로·변압기 그래프)로 독립 계산하고, 사고 후 PF 결과에서 빠진 모선과 일치하는지 검사한다(`island_check.consistent`). 일치하지 않으면 오류로 처리한다.
- `isolated_load_mw`는 합성 부하의 공급 상실 가능량이다.
- 기준 PF에서 해석 제외된 7모선/3선로는 사고 분리와 별개로 다룬다. 지도에서는 기존 "해석 대상 제외" 표시를 유지한다.

## UI

- 몽골 선택 시 Security Analysis 패널(대표 사고 선택, 선로 입력, 지도 선로 클릭 입력, Analyze)이 활성화된다. Sensitivity·Generator Contingency는 비활성 상태를 유지한다.
- 결과 패널에 상태(분리 시 "주 계통만 수렴"), 위반 0건 정상 표시, 계통 분리 요약(분리 모선 수, 합성 부하 MW, 합성 발전기 수, 섬 PF 미실행), 전압·조류 변화 요약, 합성 데이터 안내를 표시한다.
- 지도 표시: 사고 선로는 빨강, 분리 모선은 보라색 점선 원, 섬 내부 선로는 보라색 점선이다. '분석 결과' 레이어를 끄면 모두 기본 PF 색상으로 돌아간다(`:where(#map:not(.analysis-hidden))` 범위 지정).
- 계통을 전환하면 결과와 강조를 초기화하고, 이전 계통의 늦은 응답은 무시한다.
- 몽골 AI 채팅은 합성 PF 조회 전용이다. N-1은 채팅에서 호출할 수 없고 패널에서 실행한다.

## Capability

| 필드 | KPG-193 | 몽골 |
|---|---|---|
| `analysis_available` (KPG 전체 분석 묶음, 하위 호환) | true | false |
| `security_analysis_available` | true | XIIDM 검증 성공 시 true |
| `security_analysis_note` | – | 비활성 사유 |
| `sensitivity_analysis_available` | true | 민감도 자체 검증(유한차분) 통과 시 true ([사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)) |
| `generator_contingency_available` | true | false |
| `chat_available` / `chat_mode` | true / `analysis` | 지도+PF 파일 존재 시 true / `pf_read_only` |
| `pf_preview_available` | true | 지도+PF 파일 존재 시 true |

## 대표 사고 (시연용, 실제 계산 결과)

| 선로 | 구간 | 결과 |
|---|---|---|
| `222087807-1_0` | 44–3, 110 kV | 주 계통 수렴. 8모선 분리(43 44 65 66 71 103 135 136), 합성 부하 1.1 MW, 발전기 0대. 위반 0. 최대 ΔV 0.0153 pu(84), 사고 후 최대 부하율 8.77 % |
| `751261406-1_0` | 10–25, 220 kV | 수렴, 분리 없음. 위반 0. 최대 ΔV 0.0214 pu(47), 최대 ΔQ 24.68 MVAr(484333456-1_0) |
| `484333456-1_0` | 10–19, 220 kV | 주 계통 수렴. 11모선 분리, 합성 부하 1.8 MW, 합성 발전기 4대(PV_19/34/35/90), 섬 내부 선로 9개. 위반 0. 최대 ΔV 0.0215 pu(47) |

`mongolia-grid-poc`의 `n1_feasibility_results.csv`를 회귀 기준으로 사용하며(`tests/test_mongolia_security.py`), API는 이 CSV를 반환하지 않는다.

## 한계

- 합성 부하가 작아(최대 부하율 약 10 %) 위반이 발생하지 않는다. 정격이나 부하를 조정하지 않았다.
- 병렬 회선은 등가 선로 1개로 표현되어 회선 단위 사고는 계산할 수 없다.
- 분리 섬 독립 PF, Redispatch, 발전기 탈락 분석, AI 채팅 N-1 호출은 지원하지 않는다. Sensitivity는 계통 분리 없는 사고에 한해 지원한다([사양](MONGOLIA_SENSITIVITY_ANALYSIS.md)).
- 사고 후 발전기 Q는 한계(±100 MVAr)를 적용하지 않고 계산한 뒤 위반 여부만 스크리닝한다(기본 PF 검증 조건과 동일).
