# 몽골 통합 1단계: 기존 기능 기준선과 점검 기반

작업일: 2026-10-27 (Asia/Seoul). 시작 EMS HEAD `846ff55`, 원본 프로젝트 HEAD `748c0811854b959f9df09377317499056a51601e`, PyPSA-Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`.

실제 작업 브랜치는 `feature/kr-osm-map-integration`이며 원격 추적 대상은 `origin/feature/kems-multi-grid`다. 브랜치 전환이나 원격 갱신 없이 시작 상태를 보존했다.

이번 단계는 기준선 확보·검사 도구·후속 이관 분석이다. 기존 모델의 전기적 알고리즘·시나리오·EMS 경로를 바꾸지 않았다. 14개 스크립트와 8개 원본 테스트는 이동하지 않았고, PyPSA-Earth·Pixi 환경을 복사하거나 병합하지 않았다. 한국/KPG 코드와 공유 exporter도 그대로다. 원본 내부의 기존 미추적 `config.MN_35k.yaml`을 보존했다.

## 변경 전 확인과 보존

Git 상태가 깨끗한 상태에서 기존 테스트를 실행했다. 코드 변경 전 소스/설정/보고서/모델/OSM 입력 등 851개 파일의 SHA256과 세 저장소의 HEAD·상태를 `outputs/mn_stage1/before/preservation.json`에 기록했다. 원본 소스·설정·모델과 EMS 파일의 실제 바이트를 나중에 대조한다. 가상환경·캐시·생성 출력은 보존 대상 코드로 취급하지 않는다.

| 변경 전 실행 | 결과 | skip/실패 의미 |
|---|---|---|
| 기본 전체 pytest | **491 passed, 19 skipped** | 모두 브라우저 opt-in, 모델 누락 skip 없음 |
| 이전 임시 Chrome 경로 실행 | 491 passed, 19 failed | 삭제된 실행 파일 경로. 환경 실패 기록을 별도 보존 |
| 설치된 Windows Chrome 전체 실행 | 504 passed, 5 skipped, 1 failed | Linux 전용 검토 프로브 5개 skip; 한국 지도 DevTools 연결 실패 1건 |
| 고정 Linux Chrome 전체 재실행 | **510 passed, skip 없음** | 기존 기능 기준선 확보 |

Linux Chrome for Testing 156.0.8078.47은 Google 공식 저장소에서 EMS의 Git 제외 `.cache/mn-stage1-chrome/`에 설치했다. 원본 프로젝트·PyPSA-Earth·EMS `.venv`를 변경하지 않았다. 브라우저 프로필과 테스트 생성 파일은 캐시/출력 영역에만 남는다. 테스트에는 기존 Starlette/anyio deprecation warning 1개가 있다.

JUnit 파일은 `before/basic.xml`, `before/chrome.xml`, `before/chrome-windows.xml`, `before/chrome-linux.xml`이다. 최초 환경 실패를 성공으로 숨기거나 합산하지 않는다. 기준선 보고서에는 성공한 기본/Linux 실행을 명시적으로 입력하고 각 실행을 별도 집계한다. KPG·몽골 지도/PF/N-1/Sensitivity/Agent·한국 지도/검토 도구를 모두 포함한다. Agent 회귀 테스트는 LLM 응답/도구 선택을 fixture로 제어하며, 해석 도구는 실제 모델과 엔진을 사용한다. 외부 LLM의 자유 대화 품질을 새로 검증한 것은 아니다.

## 실제 파일과 역할

| 역할 | 현재 파일 | 실제 수 |
|---|---|---|
| 활성 지도 | EMS `data/grids/mn-osm-35k-map.json` | 173모선/159선로/27변압기/공개 발전소 27레코드 |
| 합성 PF JSON | EMS `data/grids/mn35k-synthetic-pf-preview.json` | 166모선/156선로/27변압기 |
| 참조 레이어 | EMS `data/grids/mn-osm-35k-reference-layers.json` | 선로 33/변전소 88, 표시 전용 |
| XIIDM | 외부 `networks/MN_35k_poc/powsybl/mn35k_synthetic_powsybl.xiidm` | 166모선/156선로/27변압기/45발전기/166부하/리액터1 |
| ID 대응표 | 원본 `reports/pypowsybl_conversion/element_mapping.csv` | 동일 집합, original ID↔IIDM ID |
| 발전기 기준값 | 같은 폴더 `pf_generator_comparison.csv` | 합성 발전기 45개 |
| 변환 증거 | 같은 폴더 `conversion_validation.json`, `input_hashes.json`, `pf_summary.json` | 시나리오·해시·구조·교차검증 |
| N-1 기대값 | 같은 폴더 `n1_feasibility_results.csv`, `n1_ui_compatible_results.json` | 3개 사고. 현재 EMS는 실제로 다시 계산 |
| 제어·자체 코드 | 기존 EMS security/sensitivity 모듈, 원본 설정 6개·스크립트14개 | SHA256·소스 위치 기록 |
| Sensitivity 기준 | 현재 `SELF_CHECK`, FD step/tolerance와 변경 전 실측 JSON | 감시 선로 3개, 각 44개 합성 발전기 후보 |

87개 입력 역할의 전체 SHA256은 `pipelines/mn/baseline_contract.json`에 고정했다. 생성 보고서에는 실제 해시·기준 해시·바이트 수·물리 경로·논리 출처·관계 검사 결과를 기록한다. 파일 데이터 자체는 Git에 추가하지 않는다.

주요 원본 SHA256:

| 모델 | SHA256 |
|---|---|
| `MN/base.nc` | `bc62f45b392959050c51fb36cba6e49275f60361ac4692d28a7d8024116e96c0` |
| `MN_35k/base.nc` | `0c94f9b998af724c4b4417eff973349405233d5edb9f7a95655a2c59c7cd6644` |
| `MN_35k_poc/base.nc` | `27dc5d25639fc2ccd7b04c6d255e3add3335e04390303b3783fbda919a0b1484` |
| 현재 합성 PF NetCDF | `21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4` |
| 활성 지도 JSON | `f530d88e789250ffff8d6a1cd9e27345bbf02cc53368ae601f77e74c6bb177ba` |

검사는 지도↔PF↔참조 레이어 바이트 해시, PF↔실제 NetCDF↔변환 입력 해시, XIIDM↔기록된 export 해시, ID 집합·끝점·발전기 기준값, exporter의 출처 해시를 대조한다. 현재 OSM PBF·raw·공개 발전소 CSV·상류 설정/코드·환경 잠금도 고정한다. XIIDM의 시각 필드만 달라도 기존 파일과 같은 바이트로 처리하지 않는다.

지도와 해석 모델을 임의로 일치시키지 않는다. PF 제외 모선은 `50, 107, 118, 124, 128, 138, 161`, 제외 선로는 `236319607-1_0`, `863961136-1_0`, `863961136-1_1`이다. 참조 레이어는 계산 대상에 넣지 않는다.

## 제어와 수치 기준선

시나리오는 `reduced_27_q_limited_with_reactor`, 합성 부하 25.6MW, 발전기45·부하166·리액터1대(20MVAr), PV43/PQ122/Slack1이다. Slack은 모선25, `SYNTHETIC_SLACK_25`다. 모델 snapshot `2013-01-01 00:00:00`은 실제 측정 시점이 아니다.

기존 EMS의 PF 제어 설정을 실제 객체에서 읽어 기록했다. `distributed_slack=false`, `use_reactive_limits=false`, `MAIN_CONNECTED`, `UNIFORM_VALUES`, Slack 선택 `VL_25`, 기준각 `FIRST_SLACK`, OLF 수렴 기준 `1e-08`, 최대 반복30을 유지한다. 기존 시나리오의 PV→PQ 전환 결과를 유지하며 새 Q 제한 정책을 도입하지 않는다.

코드 변경 전에 기존 서비스를 호출한 결과:

| 항목 | 관측 |
|---|---:|
| Slack P | 1.352550547677 MW |
| Slack Q | −93.419196819915 MVAr |
| 리액터 Q 흡수 | 20 MVAr |
| PyPSA 기준 대비 최대 모선 전압 오차 | 약 4.95e-12 pu |
| PyPSA 기준 대비 최대 선로 P/Q 오차 | 약 5.00e-11 MW/MVAr |

| N-1 사고 선로 | 결과 | 분리 모선 | 주 계통 전압 min / max (pu) |
|---|---|---:|---:|
| `222087807-1_0` | CONVERGED | 8 | 0.999201433620 / 1.065130900219 |
| `751261406-1_0` | CONVERGED | 0 | 0.982807970658 / 1.094031269556 |
| `484333456-1_0` | CONVERGED | 11 | 0.982778865940 / 1.094031269556 |

세 사고의 스크리닝 위반은 0이다. 분리된 섬의 PF를 계산했다는 뜻이 아니며 실제 정전 규모나 실제 운영 적합성으로 해석하지 않는다.

사고 `751261406-1_0` 후 Sensitivity:

| 감시 선로 | 후보 수 | 첫 후보의 주입 민감도 (MW/MW) |
|---|---:|---:|
| `303374638-1_0` | 44 | −1.013341770880 |
| `484276912-1_0` | 44 | −1.007423841347 |
| `259474322-1_0` | 44 | −0.947079055377 |

전체 후보별 수치는 `before/numerical.json` 및 최종 baseline JSON에 있다. 각 관측의 유한차분 검사도 통과했다. Slack은 순위에서 제외하며 합성 주입 민감도를 실제 재급전 권고로 표시하지 않는다.

## 구현·환경·재현

신규 코드는 `pipelines/mn/{paths,inventory,preflight,probe,migration}.py`, 진입점 `__main__.py`, 기준 계약 JSON 및 `tests/test_mn_pipeline.py`다. 경로·환경 변수·명령 및 이관 순서는 [파이프라인 README](../../pipelines/mn/README.md)를 따른다.

환경은 runtime Python3.11/PyPowSyBl1.16.1/pandas3.0.5와 외부 build Python3.13.16/PyPSA1.2.4/pandas2.3.3로 구분한다. 변환 venv는 별도 prefix에서 기존 Pixi 라이브러리를 상속한다. 기존 환경을 재생성하거나 패키지를 설치하지 않았다. 모델 메타데이터는 HDF5/NetCDF4와 NetCDF3 모두 읽는다. 구축 사전 점검은 EMS venv가 없어도 외부 구축 인터프리터로 가능하며, 이 경우 런타임 미확인을 명시한다.

재현 명령:

```bash
.venv/bin/python -B -m pipelines.mn preflight --source-project ../mongolia-grid-poc \
  --report outputs/mn_stage1/preflight.json
.venv/bin/python -B -m pipelines.mn baseline --source-project ../mongolia-grid-poc \
  --report outputs/mn_stage1/static-baseline.json
.venv/bin/python -B -m pipelines.mn baseline --source-project ../mongolia-grid-poc \
  --verify-runtime --numerical-reference outputs/mn_stage1/before/numerical.json \
  --verify-against outputs/mn_stage1/static-baseline.json \
  --test-results outputs/mn_stage1/before/basic.xml \
  --test-results outputs/mn_stage1/before/chrome-linux.xml \
  --test-results outputs/mn_stage1/after/basic.xml \
  --test-results outputs/mn_stage1/after/chrome-linux.xml \
  --report outputs/mn_stage1/baseline.json
.venv/bin/python -B -m pipelines.mn migration --source-project ../mongolia-grid-poc \
  --report outputs/mn_stage1/migration.json
.venv/bin/python -B -m pytest -q tests/test_mn_pipeline.py
```

내용이 달라진 보고서는 기존 파일을 덮어쓰지 않는다. 재실행할 때 새 출력 이름을 지정한다. 동일한 정적 입력으로 생성한 보고서는 바이트까지 같으며 생성 시각을 넣지 않는다. 수치 관측은 명시한 허용오차로 기준값과 비교한다.

## 남은 의존성과 이번 단계의 범위

- 기존 EMS는 계속 형제 프로젝트의 XIIDM과 변환 검증 폴더를 읽는다. 배포 번들로의 전환은 후속 단계다.
- `networkx`는 현재 런타임에 설치되어 있지만 `requirements.txt`에 직접 선언되어 있지 않다. 이번에는 런타임 의존성을 변경하지 않고 preflight 참고 사항으로 표시한다.
- 원본의 `ROOT`/절대 경로/import 결합과 구축 테스트 8개는 다음 이관 단계에서 정리한다. 상류 소스와 대용량 데이터는 외부에 유지한다.
- 원본 PyPSA 구축과 PyPowSyBl 변환은 실행하지 않았다. 이번에 실행한 계산은 기존 EMS의 PF/N-1/Sensitivity와 회귀 테스트다.
- 국가 공통 API, 운영 UI, 모델 승인 또는 자동 접속 기능은 추가하지 않았다. KPG·한국 OSM 기능과 상태를 유지한다.

## 최종 검증 결과

| 검증 | 실제 결과 |
|---|---|
| 신규 경로·계약·환경·해시 관계 테스트 | 39 passed |
| 변경 후 전체 pytest | **530 passed, 19 skipped**, 38.27초 |
| 변경 후 Linux Chrome 전체 pytest | **549 passed, skip 없음**, 139.06초 |
| 모델 파일 부재에 따른 skip | **0개** |
| 정적 preflight | runtime/build/conversion 모두 PASS, 87개 파일·197개 검사 |
| 정적 보고서 반복 생성 | 바이트 동일, SHA256 `5b8d12e431c7c7805ae2502484e9e10b1d8df59f90c07ac47cadb7ac76422495` |
| EMS Python 없이 구축 전용 점검 | PASS, 활성 런타임 미확인을 명시 |
| 존재하지 않는 구축 Python 지정 | 의도한 `NOT_READY`, 종료 코드2와 `FileNotFoundError` 출력 |
| 보존 파일 | **851/851 SHA256 동일**, 변경·삭제0 |
| 원본/상류 저장소 | HEAD·추적 파일·기존 미추적 설정 상태 유지 |
| 최종 기준선 | 568개 검사 중 실패0, 수치 대조186개 모두 통과 |

최종 실행의 Slack P/Q와 리액터 Q는 변경 전 관측과 차이가0이다. 세 Sensitivity 사례의 전체 후보 132개도 최대 수치 차이가0이며, N-1 세 사례의 수치·상태·분리 모선 대조가 모두 통과했다. 합성 시나리오와 지도173/해석166의 역할은 그대로다.

신규 테스트를 제외한 기존 510개 테스트는 변경 전후 모두 Chrome에서 통과했다. 기본 실행의 skip 19개는 브라우저 opt-in뿐이다. 기본/Chrome 테스트 수를 합산하지 않는다. 기존 경고는 Starlette/anyio deprecation 1개다.

전체 회귀 실행 명령은 다음과 같다. `$PWD`는 EMS 저장소 루트다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -ra --tb=short \
  --junitxml=outputs/mn_stage1/after/basic.xml

PYTHONDONTWRITEBYTECODE=1 \
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short \
  --junitxml=outputs/mn_stage1/after/chrome-linux.xml
```

Git에서 제외한 실제 실행 증거:

- [최종 기준선·해시·수치·회귀 실행별 집계](../../outputs/mn_stage1/baseline.json)
- [정적 사전 점검](../../outputs/mn_stage1/preflight.json), [정적 기준선](../../outputs/mn_stage1/static-baseline.json), [반복 보고서](../../outputs/mn_stage1/static-repeat.json)
- [원본 보존 대조](../../outputs/mn_stage1/after/preservation.json)
- [이관 import·경로 결합 분석](../../outputs/mn_stage1/migration.json)
- [구축 전용 독립 점검](../../outputs/mn_stage1/build-only.json), [의도한 누락 진단](../../outputs/mn_stage1/missing-build.json)
- [변경 후 기본 JUnit](../../outputs/mn_stage1/after/basic.xml), [변경 후 Chrome JUnit](../../outputs/mn_stage1/after/chrome-linux.xml)

새 코드·테스트·문서·작은 기준 계약만 추가했다. 기존 추적 파일 수정, 원본/모델 덮어쓰기, 런타임 경로 전환과 Commit·Push·PR·Merge는 없다.
