# KPG-193 GEN-124#1 Distributed Slack Balance Type 비교

## 목적

동일한 발전기 N-1 사고에서 Distributed Slack의 Balance Type에 따라 발전기 보상 분포, headroom, 선로 과부하, 손실 및 Security Analysis 결과가 어떻게 달라지는지 비교한다.

## 비교 대상

- `PROPORTIONAL_TO_GENERATION_P_MAX`
- `PROPORTIONAL_TO_GENERATION_P`
- `PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN`

`PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR`는 KPG-193의 `activePowerControl` extension 데이터가 없어 이번 실험에서 제외했다.

## 해석 주의

- Distributed Slack Balance Type은 Load Flow의 유효전력 mismatch 배분 가정이다.
- 특정 Balance Type에서 과부하나 손실이 더 작다고 해서 그 설정을 최적 Redispatch라고 해석하지 않는다.
- 실제 운영 제어 후보는 Sensitivity/Redispatch를 별도로 생성하고 AC Load Flow 및 Security Analysis로 재검증해야 한다.

## 출력 파일

- `balance_type_summary.csv`: Balance Type별 핵심 비교 지표
- `generator_distribution.csv`: 생존 발전기별 ΔP/headroom
- `violations.csv`: Security Analysis limit violation 원시 레코드
- `summary.json`: 핵심 비교 지표 JSON
- `run_manifest.json`: 실행 조건

## 실행 결과 요약

### PROPORTIONAL_TO_GENERATION_P_MAX

- Post AC LF converged: `True`
- Security status: `CONVERGED`
- Distributed active power: `969.180 MW`
- Residual mismatch: `0.433 MW`
- Participating generators: `99`
- Minimum post headroom: `1.795 MW`
- Near Pmax (≤5 MW): `25`
- Near Pmax (≤1% Pmax): `25`
- LINE-134-193: `2293.721 MVA / 105.604%`
- Post system loss: `817.311 MW`
- Post AC line P loss: `817.316 MW`
- Limit violations: `2` records / `1` equipment
- Thermal / voltage violation records: `2 / 0`

### PROPORTIONAL_TO_GENERATION_P

- Post AC LF converged: `True`
- Security status: `CONVERGED`
- Distributed active power: `969.024 MW`
- Residual mismatch: `0.436 MW`
- Participating generators: `99`
- Minimum post headroom: `1.215 MW`
- Near Pmax (≤5 MW): `25`
- Near Pmax (≤1% Pmax): `25`
- LINE-134-193: `2287.604 MVA / 105.322%`
- Post system loss: `817.159 MW`
- Post AC line P loss: `817.164 MW`
- Limit violations: `2` records / `1` equipment
- Thermal / voltage violation records: `2 / 0`

### PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN

- Post AC LF converged: `True`
- Security status: `CONVERGED`
- Distributed active power: `972.244 MW`
- Residual mismatch: `0.344 MW`
- Participating generators: `99`
- Minimum post headroom: `9.704 MW`
- Near Pmax (≤5 MW): `0`
- Near Pmax (≤1% Pmax): `0`
- LINE-134-193: `2398.585 MVA / 110.432%`
- Post system loss: `820.287 MW`
- Post AC line P loss: `820.290 MW`
- Limit violations: `2` records / `1` equipment
- Thermal / voltage violation records: `2 / 0`
