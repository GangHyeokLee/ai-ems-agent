# 몽골 파이프라인 Stage 2A 이관 검증

아래는 Stage 2A 최초 이관 기록이다. 2026-10-28 보고서 묶음 공개 보완은 마지막 절에 별도로 기록했다.

Stage 1 기준 커밋 `e82ef506cbbb9ed12d31be30d20e3b0d7c4f1697`에서 시작했다. 실제 브랜치는 `feature/kr-osm-map-integration`, 추적 대상은 `origin/feature/kems-multi-grid`이며 시작 작업 트리는 깨끗했다. 원본 몽골 HEAD는 `748c0811854b959f9df09377317499056a51601e`, 외부 PyPSA-Earth HEAD는 `23fa6edf021f8b37c1783949891feb0a804c1959`다. 상류의 기존 미추적 `config.MN_35k.yaml`을 보존했다. 원격 변경이나 브랜치 전환은 수행하지 않았다.

## 변경 파일과 범위

원본 네 파일을 삭제하거나 수정하지 않고 `pipelines/mn/`에 이관했다.

| 이관 파일 | 변경 범위 |
|---|---|
| `audit_mn35k_parameters.py` | 원본 위치 추정 전역 변수 대신 `AuditInputs.from_paths(paths)`. 감사 입력과 생성 보고서 분리. `build_length_audit`, `plant_and_bus_audit`, `write_reports`, `validate_outputs`, 라이브러리 `main`의 경로 인자 조정 |
| `mn35k_lengths.py` | 상대 package import, `_inputs`의 외부 Earth 선택, 두 감사 함수의 필수 `Paths` 인자 |
| `mn35k_refinement.py` | `export_deterministic`의 필수 `paths` 인자와 보호 writer. 계산 함수·metadata 구성·NetCDF3 변수/형식은 원본 유지 |
| `mn35k_powsybl.py` | 원본과 바이트 동일. 경로·로컬 스크립트 의존성 없음 |

지원 파일은 `artifacts.py`, 안내는 [파이프라인 README](../../pipelines/mn/README.md)다. 신규 테스트는 다음 네 파일이다.

- `tests/test_mn_artifacts.py`: 표준 라이브러리 보호 검사 19개
- `tests/mn_build/conftest.py`: 명시적 외부 입력·환경 fixture, 원본 네 모듈의 hash 확인 및 격리 로딩
- `tests/mn_build/test_stage2a_samples.py`: 실제 몽골 데이터가 필요 없는 고정 샘플 31개
- `tests/mn_build/test_stage2a_real_data.py`: 실제 모델·원본 구현과의 비교 6개

후속 스크립트, 기존 Stage 1 검사 코드·기준 계약, `requirements.txt`, EMS runtime·Agent·KPG·한국 OSM·UI는 변경하지 않았다. 모델 구축 CLI·변환 CLI·런타임 경로 전환은 활성화하지 않았다. `main(paths)`는 감사 라이브러리 함수로만 남기고 실행 블록은 제거했다.

## 원본과 계산 동등성

원본 비교 fixture는 Stage 1에 고정한 SHA256을 확인한 후 정확히 네 모듈만 읽는다. `sys.path`를 수정하거나 후속 스크립트를 import하지 않는다. 원본 길이 모듈의 bare import는 테스트 안에서만 원본 감사 모듈로 연결하고 종료 시 복원한다. 이 과정은 제품 코드에 없다.

실제 데이터 비교는 `check_exact=True`로 수행했다. 허용오차로 다른 값을 같은 값이라고 처리하지 않는다.

| 검사 | 관측 |
|---|---|
| 선로 길이 감사 | 159행·전 필드 원본과 정확히 동일. 미해결18개는 기존 길이 유지 |
| 전압별 단계 합계·35 kV 파라미터·변압기·발전소 후보 | 모든 DataFrame과 요약 dict 동일. 발전소 후보27개는 지리 후보 상태 유지 |
| 선로/변압기 프로필 | 기존 3개 시나리오의 R/X/C·정격·자체 MVA 기준 변환 결과 동일 |
| 기존 passive 모델의 파라미터 표·OSM 증거·가정 기록 | 원본과 정확히 동일 |
| 현재 합성 모델의 중립 변환 데이터 | Bus166/Line156/Transformer27/Generator45/Load166/Shunt1 및 mapping 전 필드 동일, Slack25 |
| 감사 산출물 | 새 임시 폴더에 작성한 CSV4개·Markdown2개가 원본 구현 출력과 바이트 동일 |
| 작은 passive 모델 | 3모선의 `build_model` 결과·metadata 동일. 원본/이관 exporter의 NetCDF 바이트 및 xarray dataset 동일 |
| 결정적 출력 | 같은 작은 모델을 새 경로 두 곳에 내보낸 바이트 동일. 기존 결과의 재실행 덮어쓰기 거부 |
| 소규모 두 엔진 PF | 3모선 PyPSA/IIDM의 전압·각도·선로/변압기 P/Q·발전기·리액터 비교 오차 < 1e-7 |

실제 173/166모선 모델은 읽기·감사·중립 데이터 추출만 수행했다. 이 모델들을 재구축하거나 새 파일로 내보내지 않았다. 실제 데이터 비교에서는 PF/LPF 호출을 금지했다. 기존 EMS PF/N-1/Sensitivity 회귀는 별도로 기존 런타임 서비스를 사용했다.

## 경로와 출력 보호

이관 모듈에 원본 `Path(__file__)`/사용자 홈/`ROOT`·`EARTH`·`REPORTS` 전역 추정·`sys.path` 수정이 없다. 길이 모듈은 `.audit_mn35k_parameters`의 거리/ID 함수만 import한다. PyPSA-Earth는 독립 체크아웃으로 선택할 수 있고, 원본 `reports/expansion` 대응표는 source_project에서 읽는다.

`export_deterministic(model, path, *, paths, protected_models=None)`은 `paths.output_dir`의 새 파일만 허용한다. 보호 원본의 위치는 Stage 1 계약의 논리적 경로와 주입한 Earth에서 계산한다. MN/MN_35k의 고정 SHA256 검사는 호출자가 생략하거나 다른 값으로 덮어쓸 수 없다. 원본 설정의 추가 `protected_models`도 명시적으로 확인한다.

기존 `Paths`의 원본·런타임 경로 보호와 작업/출력/번들 분리를 유지한다. 심볼릭 링크를 해석한 출력이 생성 영역 밖이면 거부한다. 기존 일반 파일·심볼릭 링크·하드링크는 모두 덮어쓰지 않는다. 새 임시 파일에 기록한 뒤 원본 SHA256을 다시 확인하고 `os.link`로 배타적으로 공개한다. 대상이 중간에 생기거나 생성이 실패하면 기존 결과를 보존하고 임시 파일을 정리한다. 감사 보고서도 동일 writer를 쓴다.

남은 의존성은 원본 입력/설정/대응표, 외부 PyPSA-Earth 및 기존 구축·변환 환경이다. `build_model` metadata의 논리적 출처 문자열과 원본 감사 Markdown의 과거 판정은 그대로 보존했다. 이는 현재 EMS의 상태를 새로 판정하거나 실제 운영 데이터로 인증한 결과가 아니다.

## 해시와 런타임 기준선

Stage 1 계약 파일과 87개 런타임·출처 역할의 SHA256은 바꾸지 않았다. 지도173/159/27, 해석166/156/27, `reduced_27_q_limited_with_reactor`, 발전기45·부하166·리액터1(20MVAr)·Slack25를 유지한다.

새 코드의 위치/경로/보호가 달라진 세 모듈은 원본과 바이트가 다르다. 같은 코드라고 주장하지 않고 [코드 소유권·AST 변경 함수·원본/이관 SHA256](../../outputs/mn_stage2a/code-ownership-final.json)에 따로 기록했다. `mn35k_powsybl.py`는 양쪽 모두 SHA256 `95e602dd7f910c48c54fed92cd66b8fc8dfbdd9ec13ec87a431d3194dca7feb3`이다. 계산 동등성과 산출물 동등성은 위 비교로 별도 검증했다.

주요 기존 모델 해시:

- `MN/base.nc`: `bc62f45b392959050c51fb36cba6e49275f60361ac4692d28a7d8024116e96c0`
- `MN_35k/base.nc`: `0c94f9b998af724c4b4417eff973349405233d5edb9f7a95655a2c59c7cd6644`
- 기준 합성 PF NetCDF: `21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4`

기존 N-1 기준은 세 사고 모두 CONVERGED, 분리 모선8/0/11, 스크리닝 위반0이다. 사고 후 주 계통 전압(min/max)은 각각 0.999201433620/1.065130900219, 0.982807970658/1.094031269556, 0.982778865940/1.094031269556 pu다. Sensitivity는 3개 감시 선로마다 Slack을 제외한 후보44개다. 제어 설정은 `VL_25`, `FIRST_SLACK`, `distributed_slack=false`, `use_reactive_limits=false`, `MAIN_CONNECTED`, `UNIFORM_VALUES`, OLF 1e-8/최대30을 유지한다. 전체 값은 [변경 전 기준선](../../outputs/mn_stage2a/before-baseline.json)과 최종 기준선에 기록한다.

## 실행 명령과 결과

EMS 루트에서 실행했다. 아래 변수는 실행 경로 주입이며 코드에 고정하지 않는다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"

PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --junitxml=outputs/mn_stage2a/build-verified.xml

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -ra --tb=short \
  --junitxml=outputs/mn_stage2a/runtime.xml

PYTHONDONTWRITEBYTECODE=1 \
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short \
  --junitxml=outputs/mn_stage2a/chrome.xml

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage1/baseline.json \
  --numerical-reference outputs/mn_stage1/baseline.json \
  --test-results outputs/mn_stage2a/build-verified.xml \
  --test-results outputs/mn_stage2a/runtime.xml \
  --test-results outputs/mn_stage2a/chrome.xml \
  --report outputs/mn_stage2a/baseline.json
```

전용 환경의 최초 실행은 PROJ 데이터 경로 누락으로 collection error였다. 외부 Pixi 환경을 수정하지 않고 `PROJ_DATA`를 명시해 해결했다. 고정 샘플의 첫 루프 형상은 자기 겹침이어서 원본도 `self_intersecting_geometry`로 분류했다. 계산 코드를 바꾸지 않고 정상 삼각 루프와 자기 겹침 사례로 나눠 기대값을 수정했다. 실패 실행은 별도 JUnit에 남겨 최종 성공 집계에 합산하지 않았다.

확인된 기존 환경 경고는 xarray의 선택적 cfgrib/ecCodes 부재와 NumPy ndarray C-header 크기 경고다. 해당 라이브러리를 갱신하거나 환경을 병합하지 않았다. EMS 실행에는 기존 Starlette/anyio deprecation 경고1개가 있다. Stage 1의 `networkx` 직접 의존성 선언 누락 참고도 그대로다.

| 최종 실행 | 결과 | skip 의미 |
|---|---|---|
| 별도 변환 환경: 실제 데이터6 + 고정 샘플31 + 보호19 | **56 passed**, 7.19초 | 없음 |
| 원본 프로젝트 설정 없이 고정 샘플만 실행 | **31 passed**, 1.84초 | 없음 |
| EMS 전체 pytest | **556 passed, 21 skipped**, 37.28초 | 브라우저 opt-in19 + 별도 구축 환경 모듈2 |
| EMS Chrome 전체 pytest | **575 passed, 2 skipped**, 139.87초 | 별도 구축 환경 모듈2. 브라우저 skip 없음 |
| Stage 1 preflight | runtime/build/conversion PASS | metadata·정적 검사 |
| Stage 1 migration | 원본14개, 의존 그룹6개 | 원본 스크립트 실행 없음 |
| 최종 baseline | **567개 검사, 실패0**, 파일87개 SHA256 일치 | 수치 대조186개 모두 통과 |
| 보존 대조 | 시작 파일862개 중861개 SHA256 동일 | 변경1개는 의도한 `pipelines/mn/README.md`뿐 |

N-1 세 사고의 전압 min/max는 기존 기준선과 차이가0이다. Sensitivity 3×44개 후보의 최대 절대 차이도0이며 유한차분 검사가 통과했다. PF 제어 설정 전체가 기존 기준선과 같다. 원본 몽골·PyPSA-Earth의 HEAD·Git 상태는 시작 시점과 같고, 기존 runtime·모델·보고서·한국/KPG·Agent·UI 파일은 그대로다.

skip된 구축 모듈은 위 별도 변환 환경에서 실제 실행했다. 실제 모델 파일 부재에 따른 skip은0개다. 기본/Chrome/별도 환경의 중복 테스트 수를 합산하지 않는다. 최종 코드의 EOF 공백 정리는 AST 동일성을 확인했고, 코드 소유권 해시는 별도 최종 파일로 기록하여 이전 결과를 덮어쓰지 않았다.

실행 증거는 [구축 JUnit](../../outputs/mn_stage2a/build-verified.xml), [EMS JUnit](../../outputs/mn_stage2a/runtime.xml), [Chrome JUnit](../../outputs/mn_stage2a/chrome.xml), [최종 기준선](../../outputs/mn_stage2a/baseline.json), [원본 보존 검사](../../outputs/mn_stage2a/preservation.json)에 있다. 이 산출물과 대용량 입력·환경·캐시는 Git에 추가하지 않는다.

## Stage 2B 순서와 선행 조건

1. `validate_mn_grid.py`: 기본 루트 추정/보고서 경로를 `Paths`로 주입하고 구조·ID·단위·분리망 검사를 독립적으로 이관한다.
2. `validate_mn35k_poc.py`: 상대 import로 이관된 `component_map`, `line_type_parameters`를 사용한다. 원본/이관 모델 metadata·모든 전기 필드·Ybus·미해결18개를 검증한다. PF/Slack 할당 금지를 유지한다.
3. `build_mn35k_poc.py`: 위 validator 이후에 이관한다. `ensure_sources`, 입력 manifest, 전용 출력 검사, 임시 산출물 처리와 새 `export_deterministic(..., paths=...)` 호출을 연결한다. 기존 Earth 내부 출력 경로를 그대로 쓰지 않고 생성 영역으로 분리한다.

선행 조건은 본 Stage 2A 리뷰, 기존 설정/환경 pin, 논리적 출처와 이동 후 코드 해시의 분리 기록, 생성 영역의 재실행·승인·보관 정책 확정이다. 원본 테스트 이관 시 새로운 API 인자를 명시하고 symlink/hardlink·원본 해시 실패 검사를 유지해야 한다. 실제 모델 구축/활성화는 별도 단계의 범위·검증을 거쳐야 한다.

AC PF·reactive support·변환 실행 스크립트는 Stage 2B보다 뒤다. `scripts/export_pypsa_map.py`는 KPG에서도 사용하므로 현재 위치와 계약을 유지한다. Commit·Push·PR·Merge는 수행하지 않는다.

## 2026-10-28: 감사 보고서 묶음의 원자적 공개

`write_reports()`가 CSV4개·Markdown2개를 순차 공개하던 문제를 수정했다. 파일 내용의 계산·서식은 `_render_reports()`에 그대로 유지하고, 같은 파일시스템의 비공개 임시 디렉터리에 전부 작성한다. 기존 `validate_outputs()`와 정확한 파일 구성·비어 있지 않은 일반 파일 여부·원본 NetCDF SHA256 검사를 통과한 뒤 디렉터리를 한 번에 공개한다.

`new_artifact_directory()`는 기존 `Paths`의 원본/런타임·출력 영역·심볼릭 링크 검사를 재사용한다. 기존 디렉터리(빈 디렉터리 포함)·파일·링크는 거부한다. Linux에서는 `renameat2(RENAME_NOREPLACE)`, Windows에서는 비대체 rename으로 공개한다. 지원되지 않는 플랫폼/파일시스템에서는 기존 경로를 교체하는 방식으로 후퇴하지 않고 실패한다. 생성/검증/공개 실패 시 이 호출이 만든 임시 디렉터리만 정리하고 기존 경로는 지우지 않는다. 개별 파일용 `new_artifact()`와 결정적 NetCDF exporter는 변경하지 않았다.

실제 데이터 회귀에서 3번째 CSV와 5번째 Markdown에 일부 바이트를 쓴 뒤 예외를 발생시켰다. 두 경우 모두 최종 경로와 임시 파일이 남지 않았으며, 같은 최종 경로로 재실행하면 정상 생성됐다. 6개 파일 완성 후 검증 실패도 최종 경로를 만들지 않는다. 정상 보고서6개는 기존 원본 구현과 바이트 동일하다.

변경 파일은 `artifacts.py`, `audit_mn35k_parameters.py`, `tests/test_mn_artifacts.py`, `tests/mn_build/test_stage2a_real_data.py`, 이 문서와 파이프라인 README다. 기존 원본·계산 모델·후속 스크립트·런타임·KPG·한국 OSM은 그대로다.

실행 결과는 별도 `outputs/mn_stage2a/report-bundle-fix/`에 보관하여 최초 검증 기록을 덮어쓰지 않는다. 구축 실행 명령은 앞의 명령과 같으며, JUnit 경로만 `report-bundle-fix/build.xml`이다.

| 보완 후 검증 | 결과 |
|---|---|
| 기존 구축56개 + 신규 회귀14개 | **70 passed**, 7.71초 |
| EMS 전체 pytest | **567 passed, 21 skipped**, 41.16초 |
| Chrome 포함 전체 pytest | **586 passed, 2 skipped**, 149.18초 |
| 기존 기준선 | 파일87개 SHA256 및 수치 비교186개 모두 통과 |
| 보존 대조 | 확인872개 중 요청한 코드/테스트/문서6개만 변경, 나머지866개 SHA256 동일 |

기본 실행의 skip은 브라우저 opt-in19개와 별도 구축 모듈2개다. Chrome 실행에서는 구축 모듈2개만 skip됐으며, 해당 모듈은 위 전용 환경에서 모두 실행했다. 기존 경고는 그대로이며 새 실패는 없다. 원본 두 저장소의 HEAD·Git 상태도 유지했다. 개별 실행 기록은 [구축](../../outputs/mn_stage2a/report-bundle-fix/build.xml), [EMS](../../outputs/mn_stage2a/report-bundle-fix/runtime.xml), [Chrome](../../outputs/mn_stage2a/report-bundle-fix/chrome.xml), [기준선](../../outputs/mn_stage2a/report-bundle-fix/baseline.json)에 있다. Commit·Push·Merge는 수행하지 않았다.
