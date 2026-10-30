# 몽골 파이프라인 Stage 2C — 합성 AC PF·무효전력 보상 이관

작업일: 2026-10-28 (Asia/Seoul). 시작 HEAD `86f984d8120c17401871dce5067bb7e6571579be`, 로컬 브랜치 `feature/kr-osm-map-integration`, 추적 대상 `origin/feature/kems-multi-grid`를 확인했다. 시작 작업 트리는 깨끗했다. 브랜치 전환·fetch·Commit·Push·PR·Merge를 수행하지 않았다.

몽골 원본 HEAD `748c0811854b959f9df09377317499056a51601e`, Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`를 확인했다. Earth의 기존 미추적 `config.MN_35k.yaml`도 그대로다. Stage 1 계약·[Stage 1](mongolia-pipeline-stage1.md)·[Stage 2A](mongolia-pipeline-stage2a.md)·[Stage 2B](mongolia-pipeline-stage2b.md) 문서를 확인했다. 시작 시882개 파일 SHA256과 Git 상태를 [보존 스냅샷](../../outputs/mn_stage2c/before-preservation.json)에 기록했다.

## A. 이관 파일·지원 파일·테스트

| 파일 | 역할 |
|---|---|
| `pipelines/mn/run_mn35k_acpf.py` | 초기 합성108대 모델의 원본 trial 순서·진단·PF·검증·보고서 |
| `pipelines/mn/run_mn35k_reactive_support.py` | 원본7개 비교 시나리오·수동 PV→PQ·리액터·45대 기준 모델 |
| `pipelines/mn/synthetic_inputs.py` | 명시적 설정·입력 검증, 원자적 묶음 공개, 실패 진단, 실행 환경·코드 출처 |
| `pipelines/mn/synthetic_contract.json` | 원본70개 파일 SHA256·논리적 경로·보고서 디렉터리 입력 집합 고정 |
| `tests/mn_build/test_stage2c_acpf.py` | 초기 PF 원본 대응16개: 실제 데이터, 비수렴/예외, 독립 수지, 결과 훼손, 부하·양단 부하율 |
| `tests/mn_build/test_stage2c_reactive_support.py` | 원본 대응5개: 2모선 분로 단위/부호 샘플2개와 실제 Q 제한/비수렴 비교3개 |
| `tests/mn_build/test_stage2c_bundles.py` | 전체 원본/이관 비교·재실행·입력·링크·경로·중간 실패·진단 보호40개 |
| `pipelines/mn/README.md`, 이 문서 | 검증 함수 사용법·결과·제한·다음 이관 순서 |

기존 Stage 1~2B Python 코드·계약·보호 helper와 EMS 의존성 선언은 수정하지 않았다. 구축 코드가 EMS 서버의 import 경로에 추가되지 않았다. 새 모델 CLI/API도 활성화하지 않았다.

## B. 초기108대와 기준45대의 실제 입력 관계

두 스크립트 모두 `configs/mn35k_acpf.json`의 수동 입력 `pypsa-earth/networks/MN_35k_poc/base.nc`에서 시작한다. 보상 스크립트는 `configs/mn35k_reactive_support.json`을 추가로 읽고, 원본 `build_scenario()`의 단일 Slack 또는 all_110_220kv 구성을 사용해 **매 시나리오를 새로 만든다**. 초기 합성 NetCDF를 읽어 수정하는 흐름이 아니다.

| 항목 | 초기 합성 AC PF | 보상 기준 시나리오 |
|---|---|---|
| 독립 입력 | 동일 수동173모선 모델 | 동일 수동173모선 모델 |
| 선택 연결 성분 | 166모선, 제외3/2/1/1 | 동일 |
| 최종 모선/선로/변압기 | 166/156/27 | 166/156/27 |
| 합성 발전기/부하 | 108/166 | 45/166 |
| 시나리오 | `pv_110_220kv_same_load` | `reduced_27_q_limited_with_reactor` |
| 계산 후 모선 제어 | PV107/PQ58/Slack1 | PV43/PQ122/Slack1 |
| 리액터 | 없음 | Slack25에20MVAr, `b=-20/220² S` |
| 기존 파일 | `synthetic_pf_base.nc` | `synthetic_reactive_support_base.nc` |

`PV43/PQ122/Slack1`은 PyPSA `buses.control`의 계산 후 분류다. 발전기 제어는 PV43/PQ1/Slack1이다. 44개 PV 중 `SYNTHETIC_PV_10`을 `q_set=-100MVAr`인 PQ로 전환해 재계산하므로 모선 PQ가121→122가 된다. 45대 발전기와 PV43의 차이를 임의로 보정하지 않았다.

부하는35/110/220kV 모선당0.1/0.2/0.1MW, 지상 역률0.95, 총25.6MW/8.4143130925789MVAr다. P 지정값은 초기25.6/108, 보상25.6/45MW이며 Slack이 손실을 부담한다. 실제 부하·발전소 접속 자료가 아니다. 2013-01-01은 원본 snapshot 레이블이다.

보상 스크립트의 과거 `reports/power_flow/pf_attempts.json`과 초기108대 NetCDF는 보존/비교 증거이며 전기적 구축 입력이 아니다. 이번에도 과거 증거는 원본 파일을 읽고, 새 초기108대 실행 결과를 보상 입력으로 대체하지 않았다. `data_informed` 실험은 실행·승격하지 않았다.

## C. 원본 대비 변경과 이유

- 원본 위치 추정 `ROOT`, `REACTIVE_CONFIG`, bare import, 기존 프로젝트 상대 위치를 실행 경로에서 제거했다. 패키지 상대 import와 `Paths`, Stage 2B `logical_input()`을 재사용한다. 사용자 홈·`sys.path.insert`·원본 스크립트 실행 의존성이 없다.
- 초기 계산/진단 함수10개와 보상 계산/보고서 함수8개는 AST가 정확히 같다. `write_report()`만 실제 출력 모델의 hash를 읽을 명시적 `output` 인자를 추가했다. [원본/이관 코드 SHA256·AST 비교](../../outputs/mn_stage2c/code-ownership.json)에 함수별로 기록했다.
- 원본 `main()`/`run()` 흐름을 검증 전용 `_generate()`와 `regenerate_for_verification()`으로 분리했다. 초기 trial3개는 첫 수렴에서 종료하고 보상 비교7개는 매번 독립 구성한다. 선택 기준 시나리오와 PF·Q 제한·검증 수치는 보존했다.
- 원본의 덮어쓰기 exporter와 보고서별 쓰기 대신 기존 Stage 2A exporter·원자적 디렉터리 helper를 사용한다. NetCDF 자체의 인코딩·정밀도·계산은 바꾸지 않았다.
- 원본 보호 모델3개와 과거 보고서를 모두 유지하고, 초기/보상 모델·두 설정·원본 코드·기존 보고서 전체를 포함한70개 해시를 고정했다. 디렉터리 누락, 파일 집합 변화, 해시 차이, 다른 설정 사본·시나리오 ID는 실패한다. Stage 1의87개 역할 계약은 별도로 유지한다.
- 보고서 본문의 원본 실험 안내·논리적 경로는 보존했다. 현재 실행 경로/함수는 README를 따른다. `source_preservation.json`은 보호 집합이 확대되어 원본 실행의 파일과 다르다. 원본과 모든 산출물이 바이트 동일하다고 주장하지 않는다.

원본 코드 SHA256:

```text
run_mn35k_acpf.py             c035e7c59493ae7f4554cde1906e9327c26f59e8536a017fceba1c8021224733
run_mn35k_reactive_support.py 67fb550f439bf3ad478a6902615f9456bda22ea4ab2f8f723450d9b5ed60c89c
```

이관 코드 SHA256은 각각 `92748d6b32146ab8f21c253a2e5d42415340e5715592864a003ea28bbb0f597b`, `09597d4b316a88d3419711b65d108de5a690ad22c9f00d62d9525ae2394e81f7`이다. 전체 실행 코드10개·환경·원본 입력 해시는 각 `build_provenance.json`에 별도로 남긴다.

## D. 실제 재생성과 PF 수치 비교

기존 구축/변환 Python에서 원본 구현과 이관 구현을 같은 프로세스·입력으로 각각 실행했다. 원본은 입력·과거 보고서를 복사한 격리 프로젝트에서 원본 `main()`/`run()`을 실행했다. 원본 export가 실제 Earth 출력으로 향하지 않도록 모든 모델을 복사했으며, 원본 프로젝트에는 아무것도 쓰지 않았다. 테스트에서만 원본 모듈을 이름으로 로드하며 `sys.path` 주입을 하지 않는다.

최종 검증 함수의 별도 결과는 [초기108대](../../outputs/mn_stage2c/generated/initial-02), [보상45대](../../outputs/mn_stage2c/generated/reactive-02), [실행 요약](../../outputs/mn_stage2c/final-regeneration.json)에 있다. 탐색 실행01과 기존 Stage 2B 산출물도 보존했다.

| 항목 | 초기108대 | 보상45대 최종 |
|---|---:|---:|
| PF 상태 | CONVERGED | CONVERGED |
| Q 제한 재계산 | 없음 | PV10→PQ 한 번, PF2회 |
| 마지막 Newton 반복 | 3 | 3 |
| 마지막 error | 2.3017691710980337e-8 | 2.5589072766285526e-7 |
| 전압 최소/최대 pu | 0.998962 부근 /1.065052 부근 | 0.9992014336203505 /1.0940312727934614 |
| 최대 모선 수지 잔차 MVA | 2.672998334790448e-8 | 2.58703832552227e-7 |
| 발전기 Q 한도 위반 | 1 | 0 |
| Slack P MW | 0.391665 부근 | 1.3525505476782933 |
| Slack Q MVAr | -97.317111 부근 | -93.41919681991925 |
| 리액터 실제 Q MVAr | 0 | -20.0 |
| P/Q 수지 잔차 | -1.7204e-8/-2.6534e-8 | -1.3495547768194172e-7/-3.685087803795284e-7 |

원본 `x_tol=1e-6`, `use_seed=false`, `distribute_slack=false`, 기본100회와 수지 한도1e-4MVA를 유지했다. 최초108대 이전 trial 두 개는100회 비수렴이고, 보상 비교의 single_slack·limited_220kv_pv도100회 비수렴으로 남는다. 리액터 없는 Q 제한 모델의 Slack Q는-113.4191968MVAr로 한도 위반을 유지한다. 초기108대의 PV10 Q 한도 위반도 성공한 운전조건으로 재분류하지 않았다.

모든 설비 ID·연결·전압·길이·R/X/B·기준용량·tap/model·발전/부하 P/Q·제어·리액터·snapshot·metadata·전체 시계열을 `check_exact=True`로 비교했다. 실제166×166 Ybus(nnz516)는 모선 순서와 모든 원소가 정확히 같다. 전압·위상·모든 branch P0/Q0/P1/Q1, 발전·부하·분로 결과가 같고 새 허용오차를 도입하지 않았다. 기존 NetCDF와도 같은 검사를 수행했다.

## E. NetCDF·보고서 hash와 의미적 동등성

수동 입력 SHA256: `27dc5d25639fc2ccd7b04c6d255e3add3335e04390303b3783fbda919a0b1484`.

| 모델 | 기존 파일 SHA256 | 원본 재실행 및 이관 재생성 SHA256 |
|---|---|---|
| 초기108대 | `8637b68ddade7a6d4a2a378143be0881d1fc41a8d52315634f8d94a92e406086` | 동일 |
| 보상45대 | `21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4` | `69a368920b6e9deda25d103238d8557dbbdd725c30ae3e10cbd23b740b84744a` |

**보상 기존 파일과 새 파일은 바이트가 다르다.** 둘 다 NetCDF3 64-bit offset, 5,612,108bytes다. `string14`·`string26` 차원 선언 순서가 뒤바뀌어 header의 차원 참조도 달라진다. xarray `assert_identical`은 통과했고, 별도 사본에 차원을 정렬해 직렬화하면 전체 바이트가 일치한다. 이 canonical 사본 SHA256은 `78a17ca3ecef7514e61d1f1727a49a5c516abbb1dbe8a9f4d0a3f3540b481377`이다. 기존 파일을 정규화하거나 덮어쓰지 않았다. [직렬화 조사](../../outputs/mn_stage2c/serialization.json)에 차원 순서와 증거를 기록했다. 이번 두 구현 사이에는 이 차이가 없고 바이트가 같다.

원본 **재실행**과 이관 실행 사이에는 초기14개 보고서 중13개, 보상11개 보고서 중10개가 바이트 동일하다. 다른 한 개는 각각 확대된 입력 보호 manifest `source_preservation.json`이다. 신규 provenance는 원본에 없으며 코드 소유권·실제 환경·70개 입력·결과 hash를 분리한다. 같은 입력으로 두 번째 새 디렉터리에 실행하면 provenance를 포함해 모든 파일이 바이트 동일하다. 기존 디렉터리에는 재실행하지 못한다.

파일별 원본 재실행/이관/기존 보고서 SHA256과 결과는 [초기 비교](../../outputs/mn_stage2c/review-comparisons-01/test_reference_and_migration_a0/equivalence.json), [보상 비교](../../outputs/mn_stage2c/review-comparisons-01/test_reference_and_migration_a1/equivalence.json)에 있다. 원본 재실행 보고서를 과거 보존 보고서와 혼동하지 않는다. 특히 기존 hash를 본문에 담은 보상 Markdown은 새 모델 hash가 들어가므로 과거 파일과 다를 수 있다.

## F. 원자성·입력 hash·원본 보호

초기는 모델1·CSV9·JSON4·Markdown1·provenance1, 보상은 모델1·CSV7·JSON3·Markdown1·provenance1을 한 묶음으로 공개한다. 비공개 임시 디렉터리에서 전체 파일과 모델 재로드를 검증하고, 입력 해시를 재확인한 뒤 기존 `renameat2(RENAME_NOREPLACE)` helper로 새 최종 디렉터리를 공개한다. 일부 파일만 먼저 노출하지 않는다.

회귀 검사는 다음을 실제로 재현한다.

- 모델/설정 누락, 원본 SHA256 불일치, 다른 설정 사본, 잘못된 시나리오 ID
- 원본과 동일 출력, work 영역 출력, work/output 중첩
- 기존 디렉터리·파일·symlink·hardlink 및 입력 symlink의 외부 이탈
- 보고서 디렉터리 누락/추가 입력, 생성 중 입력 변조
- 두 실행 각각3번째·5번째 CSV 쓰기 실패, 모델 export 예외
- PF 비수렴 시 성공 경로 없음, 별도 `.failed/failure.json`에 `accepted=false`, NetCDF 없음
- 기존 실패 기록도 교체하지 않음, 독립 반복의 결정성과 기존 성공 결과 보존

원본87개 기준 파일 해시는 그대로이며, 시작 시882개 중 기존 README만 의도적으로 변경됐다. 원본 몽골·Earth·EMS 런타임·Stage 2B 코드와 산출물은 바뀌지 않았다. [전체 보존 검사](../../outputs/mn_stage2c/preservation.json)에 파일별 hash와 외부 Git 상태가 있다.

## G. 실행 명령과 테스트 결과

EMS 루트에서 실행한다. 외부 환경을 설치·동기화하지 않으며 EMS `.venv`에 PyPSA를 추가하지 않았다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2c/generated
export PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj"
export MPLCONFIGDIR=/tmp/mn-stage2c-mpl
export PYTHONDONTWRITEBYTECODE=1

"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --junitxml=outputs/mn_stage2c/build-complete-review.xml

# 원본 main/run도 실행하는 두 비교 테스트. --basetemp는 항상 새 이름 지정.
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build/test_stage2c_bundles.py -k reference_and_migration_actual_bundles \
  --basetemp=outputs/mn_stage2c/review-comparisons-new

.venv/bin/python -m pytest -q -ra --tb=short
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage2b/baseline.json \
  --numerical-reference outputs/mn_stage2b/baseline.json \
  --test-results outputs/mn_stage2c/build-complete.xml \
  --test-results outputs/mn_stage2c/runtime.xml --test-results outputs/mn_stage2c/chrome.xml \
  --test-results outputs/mn_stage2c/comparisons.xml --test-results outputs/mn_stage2c/samples.xml \
  --report outputs/mn_stage2c/baseline-review-new.json
```

모델 재생성 명령은 [README Stage 2C](../../pipelines/mn/README.md)에 있다. 새 최종 이름이 필요하며 기존 결과 교체는 허용하지 않는다.

| 실행 | 실제 결과 | skip |
|---|---|---|
| Stage 2C 최초 검증 | 55 passed, 82.44초 | 없음; 이후 보호 검사6개 추가 |
| Stage 2A·2B·2C + 산출물 보호 전체 | **218 passed**, 100.41초 | 없음: 기존157+신규61 |
| 정확 NetCDF 구조 검사 추가 후 원본/이관 비교 | **2 passed**, 34.46초 | 없음, 다른38개 deselected |
| 원본 데이터 설정 없이 고정 샘플 | **3 passed** | 없음; 양단 부하율1·2모선 shunt2 |
| EMS 전체 pytest | **567 passed, 28 skipped**, 40.10초 | 브라우저 opt-in19 + 구축 전용 모듈9 |
| Chrome 포함 전체 pytest | **586 passed, 9 skipped**, 141.95초 | 구축 모듈9만 skip, 브라우저19개 실행 |
| Stage 1 preflight/migration | PASS /14개 원본·의존 그룹6 | 운영 CLI의 구축/변환/활성화는 여전히 비활성 |
| Stage 1 baseline | PASS |87개 SHA256·수치 대조186개 통과 |

JUnit과 로그는 모두 `outputs/mn_stage2c/`에 있다. 위 실행 수는 중복 합산하지 않는다. 실제 모델 부재로 skip된 해석 테스트는 없고, EMS 환경에서 빠진 구축 테스트는 외부 구축 환경에서 전부 실행했다. KPG·한국 OSM·Agent·지도·검토 도구는 기존 전체 테스트와 Chrome 회귀에 포함된다. LLM 외부 호출을 하는 신규 검사는 추가하지 않았으며 기존 Agent 테스트의 응답 모형을 실제 LLM 관측으로 표시하지 않는다.

외부 환경의 기존 경고2개(ecCodes/cfgrib 미설치, NumPy ndarray C-header 크기 경고)와 런타임의 Starlette/anyio 경고는 그대로 관측했다. 사전 점검의 networkx 직접 의존성 미선언 안내도 그대로다. 설치/환경 변경으로 숨기지 않았으며 실제 수치·재로드·비교는 통과했다.

## H. 기준선·해석 기능 보존

기존 EMS는 기존 XIIDM·보고서·시나리오를 계속 사용한다. Stage 2C NetCDF는 EMS에 설치하지 않았고 변환기도 실행하지 않았다. 따라서 새 PyPSA 결과와 기존 OpenLoadFlow 결과의 미세한 차이를 EMS 설정 변경으로 해소하지 않는다. 원본 PyPSA 재실행은 원본 PyPSA와 정확히 비교하고, 기존 EMS 결과는 기존 EMS 기준선과 비교했다.

| N-1 차단 선로 | 상태 | 격리 모선 | 최소/최대 전압 pu |
|---|---|---:|---|
| `222087807-1_0` | CONVERGED | 8 | 0.9992014336196917 /1.0651309002190068 |
| `751261406-1_0` | CONVERGED | 0 | 0.9828079706584585 /1.094031269556094 |
| `484333456-1_0` | CONVERGED | 11 | 0.9827788659397438 /1.094031269556093 |

위반 설비0개를 유지한다. Sensitivity는 기존 outage `751261406-1_0`, 감시 선로3개×44개=132개 후보를 유지하고 유한차분 검사도 통과한다. 각1위 값은 `-1.0133417708799506`, `-1.0074238413468564`, `-0.9470790553771927`로 기준과 같다. 제어·ID·PF·N-1·Sensitivity 수치186개 대조는 모두 통과했다. 기존 OLF는 Slack `VL_25`, MAIN_CONNECTED, FIRST_SLACK, distributed_slack=false, 최대30회·1e-8·UNIFORM_CRITERIA 설정을 유지한다. [최종 baseline](../../outputs/mn_stage2c/baseline.json)에 전체 수치·제어·87개 SHA256을 기록했다.

## I. 후속 이관과 외부 의존성

다음은 기존 README 순서대로 발전소 감사·data_informed PF·무효전력/220kV 진단 코드다. `audit_mn35k_plants.py`, `run_mn35k_data_informed_pf.py`, `diagnose_mn35k_reactive.py`, `audit_mn35k_220kv_lines.py`는 이번에 이관/실행 의존성으로 추가하지 않았다. 비수렴 실험과 공개 발전소 후보를 기준 모델/실제 접속 설비로 승격하지 않는 것이 선행 조건이다.

최종 변환 실행 `convert_mn35k_to_powsybl.py`는 후속 단계다. 기존 `mn35k_powsybl.py` helper 재사용, 두 엔진의 단위·부호·제어·PF/N-1 계약, 새 검증 번들·원자적 공개·기존 runtime 보호를 먼저 확인해야 한다. 활성화/롤백 설계 없이 이번 출력을 런타임에 연결하지 않는다.

남은 외부 의존성은 명시적으로 주입한 원본 설정/모델/과거 보고서70개, 외부 Earth 체크아웃과 Pixi 라이브러리, 별도 변환 venv다. 원본 소스2개는 해시 provenance 증거로만 읽는다. PyPSA-Earth·Pixi·대용량 원본·NetCDF·캐시·XIIDM·재생성 결과를 Git에 넣지 않았다. 원본을 수정하거나 해시를 자동 갱신하는 기능은 없다. 신규 결과는 Git 제외 `outputs/mn_stage2c/` 안에만 있다.
