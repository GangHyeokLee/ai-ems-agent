# KPG-193 발전기 N-1: Single/Distributed Slack 비교

- 실험일: 2026-09-28
- 저장소: [GangHyeokLee/ai-ems-agent](https://github.com/GangHyeokLee/ai-ems-agent)
- 관련 구현: [PR #1](https://github.com/GangHyeokLee/ai-ems-agent/pull/1)
- 구현 병합: [2b4ef34](https://github.com/GangHyeokLee/ai-ems-agent/commit/2b4ef344ff9e36631d080f0c7a0455fa4d253225)

이 폴더는 동일한 발전기 사고 100건에 대해 Single slack과 Distributed slack을 비교한 실행 결과를 보관한다. 발전기 탈락분의 보상 가정에 따라 위반 사고가 **65건에서 24건**으로 달라졌다. 이는 계산 조건의 영향을 보여주는 결과이며, 실제 제어를 적용한 과부하 해소 실험은 아니다.

## 목적과 가설

발전기 탈락으로 발생한 유효전력 부족분의 보상 방식에 따라 선로 과부하 발생 양상이 달라질 것이다.

기존 Single slack 결과에서 과부하가 `LINE-134-193`에 집중되었다. 동일한 계통과 사고 목록을 Distributed slack 설정으로 계산하여 보상 가정의 영향을 확인한다.

## 실험 설정

| 항목 | 설정 |
|---|---|
| 입력 데이터 | KPG-193 v2.0, `KPG193_ver2_0_powsybl_full.mat` |
| 분석 방법 | PyPowSyBl AC Security Analysis |
| 분석 대상 | 접속 발전기 100대의 개별 N-1 사고 |
| 계통 규모 | 193 buses, 385 AC lines, 전체 발전기 201대 중 접속 100대 |
| 환경 | Python 3.11.16 / PyPowSyBl 1.16.1 |
| 실행 옵션 | Generator-only, chunk size 100, Sensitivity 후처리 비활성화 |
| 기준상태 | 두 시나리오 모두 수렴. 각 시나리오의 기준상태와 사고 후 계산에 동일한 Slack 설정 적용 |

각 사고는 발전기 한 대만 독립적으로 탈락시키며, 탈락을 누적하지 않는다.

| 비교 조건 | Single slack | Distributed slack |
|---|---|---|
| `distributed_slack` | `False` | `True` |
| 유효전력 불일치 보상 | 단일 Slack 쪽에서 보상하는 설정 | 여러 참여 발전기에 분담하는 설정 |
| 분담 기준 | 해당 없음 | `PROPORTIONAL_TO_GENERATION_P_MAX` |

두 실행의 입력 파일 SHA-256은 동일하다.

```text
13593043ad8ea766a3860513dca1aa9cc592289774dcc24e38f7d13654865b81
```

## 결과

| 지표 | Single slack | Distributed slack |
|---|---:|---:|
| 분석 사고 수 | 100 | 100 |
| 수렴·위반 없음 | 35 | 76 |
| 수렴·제약 위반 | 65 | 24 |
| 미수렴 | 0 | 0 |
| 최대 선로 부하율 | 109.178% | 105.603% |

사고 ID별 대응 비교:

| 상태 변화: Single → Distributed | 사고 수 |
|---|---:|
| 위반 → 위반 없음 | 41 |
| 위반 → 위반 유지 | 24 |
| 위반 없음 → 위반 없음 | 35 |
| 위반 없음 → 위반 | 0 |

- 두 실행의 사고 ID 100개가 일치하며 중복은 없다.
- 두 설정 모두 발전기 사고의 제약 위반은 `LINE-134-193`의 피상전력 한계 **2,172 MVA** 초과로 나타났다.
- 두 설정 모두 전압 위반은 기록되지 않았다.
- 최대 부하율을 보인 사고는 `GEN-124#1`, `GEN-124#2`, `GEN-124#3`, `GEN-124#4`의 개별 탈락이었다. 네 사고는 각 설정 내에서 동일한 최대 부하율을 보였다.

## 분석과 해석 범위

결과는 발전기 탈락분의 보상 방식이 과부하 평가에 영향을 준다는 가설을 지지한다. 따라서 위험 순위를 설명할 때는 사고 대상과 함께 보상 가정도 명시해야 한다.

- Distributed slack에서도 24건의 위반이 남았다.
- 위반 건수 감소는 두 계산 시나리오의 차이이며, 재급전이나 실제 운전 제어의 효과를 검증한 결과가 아니다.
- 발전기별 실제 보상 출력, 참여 발전기, 출력 여유는 별도로 확인하지 않았다.
- 정상상태 AC 해석이므로 주파수 응답, 과도안정도, 시간에 따른 출력 추종 능력은 평가하지 않았다.
- 과부하 지표의 개선만으로 모든 계통 지표가 개선됐다고 판단하지 않는다.

## AI-EMS 사업 관점의 의의

### 계통해석 자동화 기반

발전기 사고 100건을 일괄 계산하고 수렴 여부, 제약 위반, 위험 순위를 구조화된 파일로 저장했다. 이는 AI-EMS가 사용자의 분석 요청에 따라 해석 도구를 호출하고 결과를 설명하는 기능을 개발할 때 활용할 수 있는 배치 분석 사례이다. 이번 실험에서 발전기 사고 분석의 AI 대화 연계까지 검증한 것은 아니다.

### 분석 조건과 근거를 함께 제공해야 할 필요성

동일한 계통과 사고 목록에서도 Slack 설정에 따라 위반 건수가 달라졌다. AI-EMS는 결과 숫자뿐 아니라 입력 데이터, 계산 설정, 수렴 상태, 실행 이력을 함께 제공해야 한다. 이 폴더는 그러한 설명 기능과 향후 변경 결과를 비교할 때 참고할 수 있는 기준 자료이다.

### 테스트 계통과 해석 엔진의 활용 가능성

이번 조건에서 KPG-193은 발전기 N-1 및 보상 방식 비교를 위한 테스트 계통으로, PyPowSyBl은 분석 자동화와 결과 수집을 위한 해석 엔진으로 활용 가능했다.

다만 **수렴 성공은 KPG-193의 실계통 대표성이나 PyPowSyBl 계산의 정확도를 독립적으로 입증하지 않는다.** 정확도 검증에는 동일한 입력·제어 조건을 맞춘 다른 해석 도구 또는 신뢰할 수 있는 기준 결과와의 비교가 필요하다.

## 재현 방법

저장소 루트에서 프로젝트 의존성을 설치한 Python 환경을 활성화하고 실행한다. 원본 MAT 파일은 저장소에 포함되지 않으므로 [데이터 안내](../../data/README.md)에 따라 별도로 준비한다. 원본과 비교할 때는 위 SHA-256도 확인한다.

```bash
python scripts/run_kpg193_full_batch.py \
  --generator-only \
  --generator-slack single \
  --chunk-size 100 \
  --no-sensitivity
```

```bash
python scripts/run_kpg193_full_batch.py \
  --generator-only \
  --generator-slack distributed \
  --chunk-size 100 \
  --no-sensitivity
```

새 결과는 `outputs/kpg193_full_batch/<실행시각>/`에 생성된다. 이 폴더의 보관 결과를 덮어쓰지 않고 비교한다.

관련 코드:

- [실행 스크립트](../../scripts/run_kpg193_full_batch.py): 분석 대상과 Slack 방식 선택
- [Full Batch runner](../../studies/kpg193_full_batch/runner.py): 사고 구성, 계산 실행, 결과 수집
- [조류계산 래퍼](../../src/ai_ems/network.py): 전달받은 설정으로 기준상태 계산

## 보관 파일

| 파일 | 역할 | Single | Distributed |
|---|---|---|---|
| `run_manifest.json` | 입력 해시, 환경, 설정, 실행 당시 코드 상태 | [열기](single/run_manifest.json) | [열기](distributed/run_manifest.json) |
| `contingency_summary.csv` | 사고별 수렴·위반 분류와 지표 | [열기](single/contingency_summary.csv) | [열기](distributed/contingency_summary.csv) |
| `violations.csv` | 설비별 제약 위반 상세 | [열기](single/violations.csv) | [열기](distributed/violations.csv) |
| `risk_ranking.csv` | 사고 위험 순위 | [열기](single/risk_ranking.csv) | [열기](distributed/risk_ranking.csv) |
| `summary.json` | 집계 및 상세 결과 | [열기](single/summary.json) | [열기](distributed/summary.json) |
| `report.html` | 조회용 보고서 | [열기](single/report.html) | [열기](distributed/report.html) |
| `topn_sensitivity.csv` | 이번 실행에서는 분석 미수행으로 빈 파일 | [열기](single/topn_sensitivity.csv) | [열기](distributed/topn_sensitivity.csv) |

HTML 보고서는 파일을 내려받아 브라우저로 열어 확인할 수 있다.

## 실행 이력과 검토

| 보관 폴더 | 원본 실행 폴더 |
|---|---|
| `single/` | `outputs/kpg193_full_batch/20260928_105102/` |
| `distributed/` | `outputs/kpg193_full_batch/20260928_105115/` |

- 실행 당시 HEAD는 `c6c95879ee252ff578b9955d42bb0b35d5b4920e`이며, `network.py`, `runner.py`, `test_network.py`에 미커밋 수정이 있었다. 정확한 경로는 각 manifest의 `git_worktree_status`에 남아 있다.
- 관련 수정은 이후 커밋되어 `2b4ef34`로 병합되었다. 보관 결과를 머지 커밋에서 재실행한 결과로 표기하지 않는다. `c6c9587`만 체크아웃하면 실행 당시 수정 사항은 포함되지 않는다.
- manifest의 절대 경로와 코드 상태는 원본 실행 기록으로 보존했다. 보관 폴더로 복사했다고 경로를 바꾸지 않았다.
- 실행자가 공유한 검증 로그: `pytest` 36개 통과, `git diff --check` 지적 없음. README 작성 과정에서는 시뮬레이션이나 테스트를 재실행하지 않았다.
- 저장소에 복사된 14개 파일은 앞서 제공된 결과 ZIP과 Git blob 해시가 모두 일치한다. CSV를 다시 집계하여 사고 수, 분류, 최대 부하율, 사고별 상태 전이를 확인했다.

## 다음 실험

- [ ] 대표 사고 `GEN-124#1`의 사고 전후 발전기별 유효전력 출력 비교
- [ ] Single/Distributed 설정에서 실제 보상 발전기와 분담량 확인
- [ ] 발전기 출력 한계 및 출력 여유와 비교
- [ ] 확인 결과를 바탕으로 발전기 사고의 민감도 분석·재급전 연계 범위 결정
