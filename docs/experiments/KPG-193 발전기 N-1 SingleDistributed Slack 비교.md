# KPG-193 발전기 N-1: Single/Distributed Slack 비교

# \[KPG-193 발전기 N-1: Single/Distributed Slack 비교\] — 2026-09-28

## 가설

발전기 탈락으로 발생한 유효전력 부족분을 보상하는 방식에 따라 선로 과부하 발생 양상이 달라질 것이다.

기존 Single slack 결과에서 발전기 사고의 과부하가 `LINE-134-193`에 집중되었다. 동일한 사고를 Distributed slack 설정으로 계산하여, 이러한 결과가 보상 방식에 얼마나 영향을 받는지 확인한다.

## 설정

- 데이터셋: KPG-193 v2.0 (`KPG193_ver2_0_powsybl_full.mat`)
- 분석 대상: 접속 중인 발전기 100대. 매 사고마다 발전기 한 대만 독립적으로 탈락시키는 N-1 분석
- 분석 방법: PyPowSyBl AC Security Analysis
- 환경: Python 3.11.16 / PyPowSyBl 1.16.1
- 공통 설정: Generator-only, chunk size 100, Sensitivity 후처리 비활성화
- 계산 일관성: 각 시나리오의 기준상태와 사고 후 계산에 동일한 Slack 설정 적용

| 비교 조건 | Single slack | Distributed slack |
| --- | --- | --- |
| `distributed_slack` | `False` | `True` |
| 유효전력 불일치 보상 | 단일 Slack 쪽에서 보상 | 여러 참여 발전기에 분담 |
| 분담 기준 | 해당 없음 | `PROPORTIONAL_TO_GENERATION_P_MAX` |

## 결과

| 지표  | Single slack | Distributed slack |
| --- | --- | --- |
| 분석 사고 수 | 100 | 100 |
| 수렴·위반 없음 | 35  | 76  |
| 수렴·제약 위반 | 65  | 24  |
| 미수렴 | 0   | 0   |
| 최대 선로 부하율 | 109.178% | 105.603% |

### 사고 ID별로 대응하여 비교한 결과는 다음과 같다.

| 상태 변화: Single → Distributed | 사고 수 |
| --- | --- |
| 위반 → 위반 없음 | 41  |
| 위반 → 위반 유지 | 24  |
| 위반 없음 → 위반 없음 | 35  |
| 위반 없음 → 위반 | 0   |

- 두 설정 모두 발전기 사고의 제약 위반은 `LINE-134-193`의 피상전력 한계 **2,172 MVA** 초과로 나타났다.
- 두 설정 모두 전압 위반은 기록되지 않았다.
- 최대 부하율을 보인 사고는 `GEN-124#1`~`GEN-124#4`의 개별 탈락이었다. 네 사고는 동일한 최대 부하율을 보였다.

## 분석

결과는 **발전기 탈락분의 보상 방식이 과부하 평가에 영향을 준다**는 가설을 지지한다.

Distributed slack 설정에서는 위반 사고가 65건에서 24건으로 감소했고, 최대 선로 부하율도 낮아졌다. 따라서 이번 계통의 Generator N-1 결과를 해석할 때는 사고 대상뿐 아니라 유효전력 불일치를 어떻게 보상하도록 설정했는지도 함께 명시해야 한다.

다만 다음과 같이 해석 범위를 제한한다.

- 이번 결과는 **보상 가정을 변경한 두 계산 시나리오의 비교**이다. 실제 제어를 적용해 과부하를 해소했다고 판단할 수는 없다.
- Distributed slack에서도 24건의 위반이 남았다.
- 발전기별 실제 보상 출력과 출력 여유, 보상에 참여한 발전기는 별도로 확인하지 않았다.
- 정상상태 AC 해석이므로 주파수 응답, 과도안정도, 시간에 따른 출력 추종 능력은 평가하지 않았다.
- 과부하 지표의 개선만으로 모든 계통 지표가 개선됐다고 판단하지 않는다.

### AI-EMS 사업 관점의 의의

이번 실험은 **KPG-193과 PyPowSyBl을 AI-EMS의 계통해석 기능을 개발·시험하는 환경으로 활용할 수 있음을 보여주는 사례**이다.

#### 1\. 계통해석 기능의 자동화 가능성 확인

접속 발전기 100대에 대한 개별 탈락 사고를 일괄 계산하고, 수렴 여부·제약 위반·위험 순위를 구조화된 결과로 저장했다.

이는 AI-EMS에서 사용자의 분석 요청을 받아 계통해석 도구를 실행하고 결과를 설명하는 기능을 구현할 때, 반복 실행 가능한 분석 기반으로 활용할 수 있다. 다만 이번 실험은 배치 분석까지 수행했으며, 발전기 사고 분석을 AI 대화 기능에 연결하는 검증은 별도 과제이다.

#### 2\. 분석 조건을 함께 설명해야 할 필요성 확인

동일한 계통과 발전기 사고를 사용했지만, Slack 보상 방식에 따라 위반 사고가 65건에서 24건으로 달라졌다.

따라서 AI-EMS는 위반 건수나 위험 순위만 제시해서는 충분하지 않다. 어떤 계통 상태와 보상 가정으로 계산했는지를 함께 제시해야 한다. 이번 결과는 분석 설정·실행 이력·결과를 연결해서 관리해야 하는 이유를 보여준다.

#### 3\. KPG-193과 PyPowSyBl의 활용 범위 구체화

이번 조건에서 KPG-193은 발전기 N-1 및 보상 방식 비교를 위한 테스트 계통으로 활용 가능했고, PyPowSyBl은 해당 분석을 자동 실행하고 결과를 수집하는 해석 엔진으로 활용 가능했다.

다만 이는 KPG-193의 실계통 대표성이나 PyPowSyBl의 계산 정확도를 독립적으로 입증한 것은 아니다. 정확도 검증을 위해서는 동일한 입력·제어 조건을 맞춘 다른 해석 도구 또는 신뢰할 수 있는 기준 결과와의 비교가 필요하다.

**현재 확보한 성과는 AI-EMS의 발전기 사고 분석 기능을 개발하고, 계산 조건에 따른 결과 차이를 검토할 수 있는 재현 가능한 실험 사례이다.**

## 다음 액션

- [ ] 대표 사고 GEN-124#1을 대상으로 사고 전후 발전기별 유효전력 출력 비교
- [ ] Single/Distributed 설정에서 실제 보상 발전기와 분담량 확인
- [ ] 발전기 출력 한계 및 출력 여유와 비교
- [ ] 확인 결과를 바탕으로 발전기 사고의 민감도 분석·재급전 연계 범위 결정

## 관련 코드 및 결과 자료

### 코드

- [AI-EMS Agent 저장소](https://github.com/GangHyeokLee/ai-ems-agent)
- Clone 주소: `https://github.com/GangHyeokLee/ai-ems-agent.git`
- [관련 PR #1](https://github.com/GangHyeokLee/ai-ems-agent/pull/1)
- [머지 커밋 2b4ef34](https://github.com/GangHyeokLee/ai-ems-agent/commit/2b4ef344ff9e36631d080f0c7a0455fa4d253225)
- 코드 검증: `pytest` 36개 통과, `git diff --check` 지적 없음

### 결과 자료

원본 실행 결과:

- Single: `outputs/kpg193_full_batch/20260928_105102/`
- Distributed: `outputs/kpg193_full_batch/20260928_105115/`

저장소 보관 위치:

- [README](https://github.com/GangHyeokLee/ai-ems-agent/tree/main/results/kpg193_generator_slack_20260928)
- `results/kpg193_generator_slack_20260928/single/`
- `results/kpg193_generator_slack_20260928/distributed/`

보관 대상:

- `run_manifest.json`: 입력 데이터·환경·실행 설정·코드 상태
- `contingency_summary.csv`: 사고별 수렴 및 위반 분류
- `violations.csv`: 설비별 제약 위반 상세
- `risk_ranking.csv`: 사고 위험 순위
- `summary.json`: 집계 결과
- `report.html`: 조회용 보고서

**실행 이력:** 위 결과는 `c6c9587`에 미커밋 수정 사항이 있는 상태에서 생성되었다. 관련 수정은 이후 커밋되어 `2b4ef34`로 병합되었다. 결과 파일과 manifest는 실행 당시 기록을 그대로 보존한다.