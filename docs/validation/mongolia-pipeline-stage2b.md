# 몽골 파이프라인 Stage 2B 검증·구축 코드 이관

작업일: 2026-10-28 (Asia/Seoul). 시작 HEAD `692412dc10f17a96b180cc386cc53ec8e7fc0f00`, 로컬 브랜치 `feature/kr-osm-map-integration`, 추적 대상 `origin/feature/kems-multi-grid`. 시작 작업 트리는 깨끗했다. 브랜치 전환·fetch·Commit·Push·PR·Merge는 수행하지 않았다.

원본 몽골 HEAD `748c0811854b959f9df09377317499056a51601e`, PyPSA-Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`를 확인했다. 상류의 기존 미추적 `config.MN_35k.yaml`을 포함해 시작 상태를 보존한다. 변경 전 파일872개 SHA256과 저장소 상태는 `outputs/mn_stage2b/before-preservation.json`에 기록했다. Stage 1 계약87개, [Stage 1 문서](mongolia-pipeline-stage1.md), [Stage 2A 문서](mongolia-pipeline-stage2a.md)를 먼저 확인했다.

## A. 이관 코드·지원 파일·테스트

| 파일 | 역할 |
|---|---|
| `pipelines/mn/validate_mn_grid.py` | MN 원본 구조·발전소·OSM 자료 검증. 새 보고서5개만 원자적 공개 |
| `pipelines/mn/validate_mn35k_poc.py` | 수동 보완 모델의 ID·정수·단위·Ybus 검증 |
| `pipelines/mn/build_mn35k_poc.py` | 원본 알고리즘을 이용한 격리된 검증 재생성 함수 |
| `pipelines/mn/passive_inputs.py` | 논리적 입력 식별자와 물리 위치 매핑, 입력 집합·해시 검증 |
| `pipelines/mn/passive_build_contract.json` | 기존 입력 manifest57개·설정·보완 모델 해시 고정 |
| `tests/mn_build/test_stage2b_refinement.py` | 원본 `test_mn35k_refinement.py` 대응 테스트57개 이관 |
| `tests/mn_build/test_stage2b_validators.py` | 원본 검증기·보고서 비교, 누락/중간 변조 입력, 소규모 보조 함수 |
| `tests/mn_build/test_stage2b_samples.py` | 외부 데이터가 필요 없는 2모선 Ybus·정수·정확 비교 샘플7개 |
| `tests/mn_build/test_stage2b_builder.py` | 전체 재생성, 파일/디렉터리 누락·hash·출력·링크·예외·재실행 검증17개 |

기존 Stage 2A helper·exporter·`artifacts.py`·`Paths`와 Stage 1 명령/계약은 수정하지 않았다. 원본 세 파일을 삭제·수정하지 않고, 검증기 두 개를 먼저 이관하여 **60 passed**를 확인한 뒤 빌더를 이관했다. 이 순서의 JUnit은 `outputs/mn_stage2b/validators.xml`이다.

## B. 원본 대비 차이와 이유

1. `ROOT`/`REPORTS`, `Path(__file__)`로 원본 루트를 추정하는 코드, 사용자 홈·bare import·`sys.path` 의존성을 제거했다. 패키지 안의 고정 계약을 읽는 `Path(__file__).with_name(...)`는 원본 루트 추정과 구분한다.
2. 두 검증기의 계산은 보존했다. 일반 검증기의 빈 MN 계통 PF 진단 자동 호출은 Stage 2B 금지 범위에 따라 실행하지 않고 `NOT_EXECUTED_STAGE2B_NO_PF`로 표시한다. 원본 비교 시에도 진단 결과만 이 표식으로 주입하여 PF를 실행하지 않고 CSV·나머지 보고서를 대조했다. 실제 원본에 PF를 실행해 비교하지 않았다.
3. 수동 검증기의 기존 `rtol=1e-12`, `atol=1e-14`, 단위/명판/부호 검사와 Ybus 대칭 기준은 그대로다. 선택적 `matrices` 인자는 같은 조립 결과를 관측한다. 기본 반환 구조를 변경하지 않았다. 추가 동등성 검사는 허용오차를 넓히지 않고 수치·Ybus 원소의 정확 일치를 요구한다.
4. 원본 빌더는 기존 `MN_35k_poc/base.nc`를 `os.replace`하고 보고서를 따로 썼다. 이관 빌더는 모델+증거+보고서17개를 새 비공개 디렉터리에서 완성하고 Stage 2A의 비대체 원자적 디렉터리 공개를 재사용한다. 실패한 생성물을 완료 모델로 노출하지 않는다. 원본 내부 출력 경로는 metadata의 논리적 식별자로만 남겼다.
5. 원본 명령행 `main()`을 자동 활성화하지 않았다. 명시적인 `rebuild_for_verification(paths, destination, config_path=None)`만 제공한다. EMS 서버·API·Agent·UI에는 연결하지 않는다. 보고서의 실제 파일 경로와 재실행 안내는 새 위치에 맞춰 바뀌므로 Markdown/결과 JSON을 원본과 바이트 동일하다고 주장하지 않는다.

### 입력과 출처의 계약

원본 `ensure_sources()`의 PyPSA 버전·보호 모델2개 조건과 `input_manifest()`의 사전/사후 대조를 유지했다. 과거 `reports/model_refinement/input_hashes.json`의 **57개 전체**를 고정 계약으로 옮겼다. 이 파일 자체의 SHA256은 `f35bbb45489cdb89093721a7c201637688909da302d12582d617a8d7747d590c`다.

- 보호 모델2개
- `reports/validation`, `reports/expansion`, MN/MN_35k OSM, MN_35k base_network의 모든 당시 파일
- 발전소 CSV, 설정2개, Pixi lock, 상류 구축 코드2개, 당시 감사/일반 검증 코드2개

설정 `configs/mn35k_poc.json`의 바이트 해시, 과거 manifest 자체, 기존 보완 모델 해시를 추가로 검사한다. 원본 코드2개는 실행 의존성이 아니라 과거 해시 증거로만 읽는다. 실제 실행하는 EMS 소유 코드10개의 해시는 별도 `build_provenance.json`에 기록한다. 코드 위치가 달라진 것을 과거 코드와 동일하다고 간주하지 않는다.

디렉터리 누락·빈/다른 입력 집합·추가 파일·입력 hash 차이·설정 차이·과거 manifest 차이·중간 변조는 모두 실패한다. 해시를 자동 갱신하거나 다른 데이터로 대체하는 경로는 없다. 논리 경로는 `/` 구분자를 유지하고 `pypsa-earth/...`는 독립 주입한 Earth 위치로 해석한다. 실제 테스트에서도 source와 Earth를 서로 다른 디렉터리에 복사하여 재생성했다.

## C. 실제 재생성

최종적으로 `outputs/mn_stage2b/generated/verification-04/`에 재생성했다. 이전 검증01/02/03과 원본·활성 파일은 덮어쓰지 않았다. 마지막 EOF 공백 정리는 AST 동일성을 확인했고, 최종 코드의 바이트 해시까지 다시 기록했다. 공개된 새 디렉터리에는 `base.nc`, 수치 CSV9개, JSON4개, Markdown3개가 있다. 내부 임시 반복 모델은 공개 전에 정리한다.

| 항목 | 결과 |
|---|---|
| 모델 크기 | 5,938,112 bytes |
| 기존/재생성 SHA256 | **`27dc5d25639fc2ccd7b04c6d255e3add3335e04390303b3783fbda919a0b1484`** |
| 독립 반복 재생성 | 바이트 동일 |
| NetCDF 구조·변수·attrs | xarray `assert_identical` 통과 |
| 재로드·dependent values 재계산 | 기존 검사 및 정확 비교 통과 |
| 수치 CSV9개 | 기존 `reports/model_refinement/`와 바이트 동일 |
| 가정 등록부 | 3,845행 |
| 정수 시나리오 | low_impedance/reference/high_impedance 각각102개 검사 통과 |

실제 파일과 행렬 비교 기록은 [모델](../../outputs/mn_stage2b/generated/verification-04/base.nc), [동등성](../../outputs/mn_stage2b/generated/verification-04/equivalence.json), [출처](../../outputs/mn_stage2b/generated/verification-04/build_provenance.json), [실행 결과](../../outputs/mn_stage2b/rebuild-verified.json)에 있다. [원본/이관 코드 SHA256과 AST 차이](../../outputs/mn_stage2b/code-ownership-final.json)도 별도로 기록했다.

## D. 설비·토폴로지·정수·Ybus

모선173/선로159/변압기27, 발전기0/부하0를 유지했다. ID·끝점·전압·길이·회선수·R/X/B/G·정격·tap/phase/model·사용자 LineType·snapshot8760개·metadata가 일치한다. 변압기는 원래 LV→HV 순서와 자체 정격 MVA/1MVA 변환을 그대로 쓴다. 미해결 길이18개는 원래 값을 유지하며 나머지141개만 기존 감사 규칙을 적용했다.

연결 성분은 166/3/2/1/1, 고립 모선은118/124다.

| Ybus 크기 | nnz | 기준 대비 최대 원소 차이 |
|---|---:|---:|
| 166×166 | 516 | 0 |
| 3×3 | 7 | 0 |
| 2×2 | 4 | 0 |
| 1×1 (118) | 0 | 0 |
| 1×1 (124) | 0 | 0 |

입력·파생 정수뿐 아니라 실제 sparse matrix의 모선 순서와 원소를 대조했다. 테스트에서 PF/LPF 및 `find_bus_controls()`를 금지했다. Ybus를 계산할 수 있다는 것은 AC PF 수렴 또는 실제 운영계통 검증을 뜻하지 않는다.

## E. 원본·출력 보호

Stage 2A의 `export_deterministic(..., paths=...)`와 원자적 디렉터리 helper를 그대로 사용한다. 기존 파일·디렉터리(빈 디렉터리 포함)·symlink·hardlink, source/Earth/work/bundle로의 출력은 생성 전에 거부한다. 작업/출력/번들 중첩도 기존 `Paths.validate()`가 차단한다.

구축 도중 모델 생성, 3번째 수치 보고서, 입력 변조에서 실패시키면 최종 디렉터리에 모델/보고서가 남지 않는다. 같은 최종 이름으로 정상 재실행도 확인했다. 보고서 디렉터리의 3번째/5번째 생성 실패 회귀와 개별 파일 보호 테스트도 Stage 2A 전체 검증에 포함했다.

기존 166모선·156선로·27변압기, 발전기45·부하166·리액터1·Slack25 및 `reduced_27_q_limited_with_reactor`는 변경하지 않는다. XIIDM·검증 보고서 경로와 응답 구조, KPG·한국 OSM·Agent·UI는 그대로다. 원본87개 기준 파일과 전체 보존 대상 해시는 최종 보고서에 기록한다.

## F. 실행 명령과 회귀

EMS 루트에서 실행한다. 기존 외부 환경을 사용하고 Pixi run/설치/환경 병합을 수행하지 않는다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2b/generated

PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage2b-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --junitxml=outputs/mn_stage2b/build-complete.xml

# 반드시 아직 사용하지 않은 새 최종 경로를 지정한다.
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage2b-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -c \
'from types import SimpleNamespace; from pipelines.mn.paths import resolve; from pipelines.mn.build_mn35k_poc import rebuild_for_verification; p=resolve(SimpleNamespace()); print(rebuild_for_verification(p,p.output_dir / "verification-review-new")["artifact"])'

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -ra --tb=short \
  --junitxml=outputs/mn_stage2b/runtime.xml
PYTHONDONTWRITEBYTECODE=1 FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short --junitxml=outputs/mn_stage2b/chrome.xml

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage1/baseline.json \
  --numerical-reference outputs/mn_stage1/baseline.json \
  --test-results outputs/mn_stage2b/build-complete.xml \
  --test-results outputs/mn_stage2b/runtime.xml --test-results outputs/mn_stage2b/chrome.xml \
  --report outputs/mn_stage2b/baseline.json
```

| 최종 실행 | 결과 | skip 의미 |
|---|---|---|
| 검증기 선행 검사 | **60 passed**, 5.69초 | 빌더 이관 전 실행 |
| Stage 2A 전체 + Stage 2B 구축 검증 | **157 passed**, 21.40초 | 없음 |
| 원본 프로젝트 설정 없이 고정 샘플 | **11 passed**, 1.68초 | 없음 |
| EMS 전체 pytest | **567 passed, 25 skipped**, 40.74초 | 브라우저 opt-in19 + 구축 모듈6 |
| Chrome 전체 pytest | **586 passed, 6 skipped**, 144.69초 | 별도 구축 모듈6만 skip, 브라우저 전부 실행 |
| Stage 1 preflight/migration | PASS / 원본14개·의존 그룹6개 | 운영용 구축/변환/활성화 명령은 계속 비활성 |
| 기존 baseline | **567개 검사 실패0**, 파일87개 SHA256 동일 | 수치 대조186개 통과 |
| 원본 보존 | **872개 중871개 SHA256 동일** | 기존 파일 변경1개는 의도한 파이프라인 README뿐 |

실제 모델 부재로 skip된 검사는0개다. EMS에서 skip된 구축 모듈은 전용 환경에서 전부 실행했다. 실행별 중복을 합산하지 않는다. Stage 2B는 구축 전용 모듈을 추가했으므로 EMS의 기존 통과 수는 유지되고 환경 분리에 따른 collection skip만 늘었다.

N-1 세 사고는 기존과 같이 CONVERGED, 분리 모선8/0/11 및 위반0이다. 주 계통 전압 min/max는 각각 0.999201433620/1.065130900219, 0.982807970658/1.094031269556, 0.982778865940/1.094031269556 pu다. Sensitivity는 사례3개·후보44개씩이며 기존 기준선과의 수치·제어 대조186개가 모두 통과했다. 기존 OLF/PF 제어 설정과 합성 시나리오를 바꾸지 않았다.

전체 근거는 [최종 기준선](../../outputs/mn_stage2b/baseline.json), [구축 JUnit](../../outputs/mn_stage2b/build-complete.xml), [EMS JUnit](../../outputs/mn_stage2b/runtime.xml), [Chrome JUnit](../../outputs/mn_stage2b/chrome.xml), [보존 대조](../../outputs/mn_stage2b/preservation.json)에 있다. 기존 cfgrib/ecCodes·NumPy ABI 경고와 Starlette/anyio 경고는 환경을 변경하지 않고 기록했다. Linux에서 검증했으며 다른 플랫폼의 재생성은 별도 검증 대상이다.

첫 추가 테스트에서 서로 다른 파일시스템 사이의 hardlink 생성과 작은 샘플의 필수 tap 설정 누락으로 실패했다. 원본 데이터를 변경하지 않고 같은 파일시스템의 읽기 전용 입력 복사와 완전한 샘플 설정으로 해결했다. 검사 조건이나 계산식을 완화하지 않았다. 실패 JUnit도 별도 이름으로 보관하며 성공 횟수에 합산하지 않는다.

## G. Stage 2C

다음은 `run_mn35k_acpf.py`, 그다음 `run_mn35k_reactive_support.py`를 이관한다. 먼저166모선 연결 성분 선택, 초기 합성108발전기와 현재45발전기의 시나리오 분리, PV/PQ 전환·Q 제약·리액터20MVAr·Slack25의 제어와 부호를 계약으로 정리해야 한다. 기존 제어·입력 해시·시나리오 기록을 유지하고, 새 생성 결과는 격리 영역에서 기존 PF JSON/XIIDM과 대조한다.

PyPowSyBl 최종 변환 실행 스크립트는 더 뒤의 단계이며 이번 대상이 아니다. EMS 적용은 별도 단계로 두고, 검증된 번들의 승인·롤백 방법을 정하기 전까지 현재 런타임 경로를 유지한다. Commit·Push·PR·Merge는 수행하지 않는다.
