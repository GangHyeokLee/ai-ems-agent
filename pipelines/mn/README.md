# 몽골 파이프라인 — 기반 점검과 검증 전용 수동 모델 구축

Stage 1 명령은 현재 기능의 기준선과 경로·환경을 **읽기 전용으로 검사**한다. Stage 2A~2D는 자체 구축·합성·진단 코드를, Stage 3A는 변환기를 이관했다. 재생성·분석·변환은 별도 구축 환경의 명시적인 검증 함수로만 실행한다. Stage 3B는 검증된 번들 후보를 만들고 Stage 3C는 프로세스 시작 시 승인된 번들을 선택하는 읽기 전용 런타임을 제공한다. EMS는 이 패키지의 번들 검증 모듈을 사용하지만 PyPSA·구축/변환 코드를 import하지 않는다. 모델 구축·변환 운영 CLI, 번들 설치·실행 중 교체는 제공하지 않는다. 기존 `.env`와 활성 모델은 변경하지 않았다.

## 실행

저장소 루트에서 실행한다. 원본 프로젝트는 반드시 CLI나 환경 변수로 지정한다.

```bash
.venv/bin/python -B -m pipelines.mn preflight \
  --source-project ../mongolia-grid-poc \
  --report outputs/mn_stage1/preflight.json

.venv/bin/python -B -m pipelines.mn baseline \
  --source-project ../mongolia-grid-poc \
  --verify-runtime \
  --report outputs/mn_stage1/baseline-new.json

.venv/bin/python -B -m pipelines.mn migration \
  --source-project ../mongolia-grid-poc \
  --report outputs/mn_stage1/migration.json
```

`preflight`는 파일·해시 관계·Git·설정·환경·도구와 출력 경로의 사전 조건을 검사한다. 성공은 종료 코드 0, 미충족/잘못된 입력은 2다. `build` 사전 조건이 통과해도 모델 구축 명령이 구현되었다는 뜻은 아니다. `stage_capabilities`에서 구축·변환·번들 활성화는 모두 false다.

`baseline`은 87개 입력 역할의 SHA256·출처·실제 설비 수·모델 제어·환경을 기록한다. 기본은 정적 검사이며 `--verify-runtime`을 지정할 때만 기존 EMS 서비스를 사용해 기본 PF, N-1 3건, Sensitivity 3건을 실행한다. 모델 저장은 하지 않는다. 전체 기준선은 `--profile all`만 허용한다.

`migration`은 원본 14개 스크립트를 **실행하거나 import하지 않고 AST로만 분석**한다. 로컬 import 그래프, 경로 결합 위치, 각 파일 SHA256, 위상 정렬에 따른 의존 그룹을 JSON으로 기록한다.

## 경로 설정

CLI가 환경 변수보다 우선한다. 상대 설정과 보고서 경로는 선택한 EMS 루트를 기준으로 해석한다. 사용자 홈 경로를 코드에 고정하지 않는다.

| CLI | 환경 변수 | 기본값 또는 의미 |
|---|---|---|
| `--ems-root` | `KEMS_MN_EMS_ROOT` | 패키지 위치에서 계산한 EMS 소스 루트 |
| `--source-project` | `KEMS_MN_SOURCE_PROJECT` | 필수. 기존 몽골 데이터·보고서·자체 코드, 읽기 전용 |
| `--earth-checkout` | `KEMS_MN_EARTH_CHECKOUT` | `<source-project>/pypsa-earth`, 별도 상류 체크아웃 |
| `--work-dir` | `KEMS_MN_WORK_DIR` | `outputs/mn/work`, 향후 작업 영역 |
| `--output-dir` | `KEMS_MN_OUTPUT_DIR` | `outputs/mn/generated`, 향후 합성·변환 출력 |
| `--bundle-dir` | `KEMS_MN_BUNDLE_DIR` | `outputs/mn/bundles`, 향후 검증된 배포 번들 |
| `--runtime-python` | `KEMS_MN_RUNTIME_PYTHON` | `<ems-root>/.venv/bin/python` |
| `--build-python` | `KEMS_MN_BUILD_PYTHON` | `<earth-checkout>/.pixi/envs/default/bin/python` |
| `--conversion-python` | `KEMS_MN_CONVERSION_PYTHON` | `<source-project>/.venv-powsybl/bin/python` |
| `--pixi` | `KEMS_MN_PIXI` | PATH 검색. `--version`만 실행 |
| `--runtime-xiidm` | `KEMS_MN_RUNTIME_XIIDM` | 검사할 XIIDM의 명시적 선택. 서버 설정은 바꾸지 않음 |
| `--runtime-reports` | `KEMS_MN_RUNTIME_REPORTS` | 검사할 변환 검증 폴더. 서버 설정은 바꾸지 않음 |

런타임 두 파일 선택자를 생략하면 기존 `src/ai_ems/config.py`를 런타임 인터프리터에서 조회한다. 현재 프로세스 환경과 기존 `.env`의 선택 규칙을 따르며, 보고서에는 파일 경로 두 개만 반환한다. 비밀 값이나 전체 환경을 출력하지 않는다. 지도·PF·참조 레이어 위치는 현재 `web_app.py`와 동일한 EMS `data/grids/` 파일이다. 명시적 검사 경로는 실제 실행 중 서버를 재설정하지 않는다.

보고서/작업/출력/번들은 원본 프로젝트·상류 체크아웃·현재 런타임 파일/검증 폴더와 겹칠 수 없다. EMS 안에서는 `outputs/` 아래만 허용한다. 번들은 작업·생성 영역과 분리한다. 심볼릭 링크도 해석하여 검사한다. **검사만으로 작업·생성·번들 디렉터리를 만들지 않는다.** 보고서를 요청한 경우 해당 새 보고서의 부모만 생성한다.

보고서는 같은 내용이면 다시 쓰지 않는다. 기존 파일 내용이 다르면 새 경로를 요구한다. 과거 검증 파일과 기존 기준선은 덮어쓰지 않는다.

## 환경 분리와 검사 범위

```bash
# 구축 사전 조건만 확인: EMS venv가 없어도 외부 구축 환경으로 검사 가능
python3 -B -m pipelines.mn preflight --source-project ../mongolia-grid-poc \
  --profile build --runtime-python /path/to/unavailable-runtime-python

# 실제 런타임 파일·해석 환경을 우선 확인
.venv/bin/python -B -m pipelines.mn preflight --source-project ../mongolia-grid-poc \
  --profile runtime
```

`--profile`은 `all`, `runtime`, `build`, `conversion` 중 선택한다. 각 체크의 적용 범위와 관측 결과를 모두 기록하고 선택한 범위로 종료 코드를 결정한다. 원본 NetCDF의 HDF5/NetCDF4 및 NetCDF3를 별도 인터프리터로 읽고 실제 설비 수를 기록한다. 환경 버전 검사는 배포 메타데이터를 읽으며 PyPSA/Powerplantmatching을 import하거나 환경을 설치·갱신하지 않는다. Pixi 환경과 EMS `.venv`의 `sys.prefix`를 대조한다. venv의 최종 Python 심볼릭 링크는 해제하지 않아 원래 환경 선택을 보존한다.

구축 전용 점검에서 EMS 설정 조회가 불가능하면, 고정된 계약의 원본 산출물 경로로 구축 입력만 선택한다. 보고서에서 이 사실과 런타임 미확인을 명시한다. 이를 활성 런타임이 확인되었다고 간주하지 않는다. 현재 준비된 raw·모델·공개 발전소 파일을 검사하는 것이며, 인터넷에서 새 원본을 내려받아 처음부터 재구축하는 기능은 아직 없다.

## 해시와 기준선 비교

`baseline_contract.json`은 2026-10-27에 존재한 런타임·출처 파일 87개 역할, 상류/원본 Git 커밋, 시나리오·설비 수·제어 설정을 고정한다. 경로는 루트 종류와 상대 경로로 기록하며 모델 데이터 자체를 포함하지 않는다. 이름·설비 수가 같아도 바이트 해시가 다르면 실패한다. XIIDM의 `caseDate` 변경도 다른 바이트로 취급한다. 모델의 의미적 검증은 기존 EMS 서비스가 별도로 수행한다. 기준선 갱신을 자동 승인하는 명령은 없다.

```bash
.venv/bin/python -B -m pipelines.mn baseline --source-project ../mongolia-grid-poc \
  --verify-against outputs/mn_stage1/static-baseline.json \
  --verify-runtime --numerical-reference outputs/mn_stage1/before/numerical.json \
  --test-results outputs/mn_stage1/before/basic.xml \
  --test-results outputs/mn_stage1/before/chrome-linux.xml \
  --report outputs/mn_stage1/baseline-new.json
```

`--verify-against`는 역할 집합과 각 실제 SHA256을 비교한다. 누락된 해시끼리 같다고 처리하지 않는다. `--numerical-reference`는 `--verify-runtime`과 함께 사용하며, 변경 전 관측 또는 이전 baseline 보고서의 실제 수치·제어·ID를 대조한다. 비교는 관측값 검사이며 기존 전기적 계산 코드를 변경하지 않는다.

JUnit은 실행별로 통과/실패/error/skip 및 원인을 분리한다. 기본/Chrome 실행의 중복 테스트 수를 합산하지 않는다. 지정한 실행에 failure/error가 있으면 전체 기준선이 실패한다. skip은 개별 사유를 보존하며 실제 통과로 계산하지 않는다. Agent 테스트의 LLM 모형 응답과 실제 해석 엔진 계산의 차이는 검증 문서를 참고한다.

같은 정적 입력·경로·Git 상태·환경에서 기본 보고서는 결정적이며 생성 시각을 넣지 않는다. 실시간 수치 관측은 환경/엔진별 허용오차로 비교한다.

## Stage 2A: 이관한 라이브러리와 경로 계약

| 모듈 | 경로/보호 변경 | 유지한 계산 |
|---|---|---|
| `audit_mn35k_parameters.py` | `AuditInputs.from_paths(paths)`로 외부 Earth·원본 보고서 입력 분리. `main(paths)`는 라이브러리 함수이며 실행 CLI 없음 | WGS84 측지거리·구면거리·전압 필터·파라미터·발전소 후보·감사 보고서 내용 |
| `mn35k_lengths.py` | 상대 package import. `audit_line_lengths(network, paths)`, `summarize_length_stages(network, paths)`에 필수 경로 주입 | m→km, EPSG:3857, 정확한 ID 대응, 끝점·토폴로지·커넥터·미해결 길이 보존 |
| `mn35k_refinement.py` | `export_deterministic(model, path, *, paths, protected_models=None)`의 보호 루트를 명시적으로 주입 | R/X/C, 변압기 정수·자체 MVA/1MVA 기준, `build_model`·가정 기록·NetCDF3 인코딩 |
| `mn35k_powsybl.py` | 원본과 바이트 동일. 경로 결합 및 로컬 스크립트 import 없음 | SI/pu·부호·ID·IIDM 매핑·제어·결과 비교. PyPowSyBl은 함수 내부 import 유지 |

`Paths`는 기존 CLI/`KEMS_MN_*` 설정으로 만든다. 원본 상대 경로 문자열은 출처 식별자이며 외부 Earth의 물리 위치로 해석한다. 사용자 홈, `Path(__file__)` 기반 원본 추정, `ROOT`/`EARTH`/`REPORTS` 전역 변수와 `sys.path` 수정이 이관 코드에 없다. 원본 설정·입력과 `reports/expansion`의 두 대응표는 계속 읽기 전용 외부 의존성이다. 아직 이관하지 않은 원본 스크립트는 새 패키지에서 import하지 않는다.

출력은 `artifacts.py`가 **주입한 `output_dir` 내부의 새 파일에만** 허용한다. 기존 `Paths`의 원본·런타임 보호, work/output/bundle 분리와 심볼릭 링크 정규화를 재사용한다. 기존 파일은 일반 파일·심볼릭 링크·하드링크 모두 덮어쓰지 않는다. 임시 파일에 직렬화한 뒤 보호 원본의 SHA256을 재검사하고 배타적으로 공개한다. 실패하면 임시 파일을 지운다. 대상이 실행 중 생겨도 교체하지 않는다.

MN/MN_35k 원본 모델 두 개의 Stage 1 해시 pin은 항상 적용한다. `protected_models`는 기존 설정의 추가 보호 항목이며, 빈 dict나 다른 해시로 고정 보호를 해제할 수 없다. 감사 보고서 6개는 하나의 묶음이다. 같은 파일시스템의 비공개 임시 디렉터리에서 전부 생성하고 기존 내용 검증·파일 구성·원본 해시를 확인한 뒤, 완성된 디렉터리를 한 번에 공개한다. 중간 실패는 임시 디렉터리만 정리하며 최종 보고서 경로를 만들지 않는다. 기존 디렉터리(빈 디렉터리 포함)·파일·심볼릭 링크는 교체하거나 지우지 않는다. Linux의 `renameat2(RENAME_NOREPLACE)`와 Windows의 비대체 rename을 사용하고, 원자적 비대체 공개를 지원하지 않는 환경에서는 실패한다. 감사 Markdown은 원본 감사 시점의 문구를 보존한 것으로, 현재 EMS의 PF 준비도를 새로 판정한 문서가 아니다.

PyPSA/GIS가 없는 EMS 환경에서는 구축 전용 테스트 모듈이 이유를 명시하고 skip된다. Stage 2A의 구성은 실제 데이터 비교6개·보고서 실패 회귀3개·고정 샘플31개·출력 보호30개다. 아래 실행 명령은 이후 추가한 Stage 2B 검사도 함께 실행한다. Stage 2A 자체 동등성은 3모선 시험 모델과 새 임시 감사 보고서로 확인했고, 실제173모선 재생성은 아래 Stage 2B의 별도 승인된 검증 범위다.

```bash
# 저장소 루트. 기존 환경을 설치·수정하거나 Pixi run으로 동기화하지 않는다.
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py
```

PROJ 경로도 명시적 실행 환경이다. 전용 변환 venv는 PyPSA/GIS가 있는 Pixi 환경과 PyPowSyBl을 사용하고 EMS `.venv`와 합치지 않는다. EMS의 `requirements.txt`는 변경하지 않았다. 실제 결과와 제한은 [Stage 2A 검증 보고서](../../docs/validation/mongolia-pipeline-stage2a.md)를 참고한다.

## Stage 2B: 검증기와 검증 전용 빌더

`validate_mn_grid.validate(paths, report_dir=None)`는 MN 기본 모델과 발전소/OSM 입력을 검사하고 새 보고서 묶음만 원자적으로 공개한다. 누락 입력과 실행 중 입력 해시 변경은 실패한다. 원본의 빈 계통 PF 진단 자동 호출은 사용하지 않고 `NOT_EXECUTED_STAGE2B_NO_PF`로 구분한다. `validate_mn35k_poc.validate_model()`의 기존 수치 검사·허용오차·Ybus 조립은 유지했다. 선택적 `matrices={}` 인자는 같은 조립 과정에서 실제 행렬을 관측하기 위한 것으로 반환 JSON 계약은 바꾸지 않는다.

`build_mn35k_poc.rebuild_for_verification(paths, destination, config_path=None)`는 운영 API/CLI와 연결되지 않은 명시적 검증 함수다. 생성 대상은 `paths.output_dir` 내부의 새 디렉터리여야 한다. 모델·CSV9개·JSON4개·Markdown3개, 총17개 파일을 비공개 영역에서 완성·검증한 뒤 한 번에 공개한다. 기존 파일/디렉터리/링크를 교체하지 않으며, 실패하면 최종 모델도 노출하지 않는다. Stage 2A exporter와 디렉터리 공개 helper를 그대로 재사용한다.

`passive_build_contract.json`은 원본 `reports/model_refinement/input_hashes.json`의 파일57개·해시를 고정한다. `passive_inputs.py`는 논리적 `pypsa-earth/...` 식별자를 주입한 Earth 위치에 대응시키며, 원본 설정·과거 manifest 자체·기존 보완 모델의 hash도 확인한다. 파일/디렉터리 누락, 입력 집합 변화, SHA256 차이, 설정 변경을 자동 대체하거나 승인하지 않는다. 원본 Python 파일2개는 실행하지 않고 과거 입력 증거로만 hash를 검사한다. 새 코드의 hash는 별도 `build_provenance.json`에 기록한다.

검증 재생성 예시(EMS 루트, 매번 사용하지 않은 새 최종 이름 지정):

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2b/generated
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage2b-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -c \
'from types import SimpleNamespace; from pipelines.mn.paths import resolve; from pipelines.mn.build_mn35k_poc import rebuild_for_verification; p=resolve(SimpleNamespace()); r=rebuild_for_verification(p, p.output_dir / "verification-review-new"); print(r["artifact"])'
```

이 함수는 원본173모선 수동 모델만 비교 재생성한다. PF·LPF·Slack/PV 할당·발전기·부하·리액터 합성은 수행하지 않는다. 기존166모선 PF 시나리오와 EMS XIIDM/보고서 경로는 그대로다. [Stage 2B 검증 보고서](../../docs/validation/mongolia-pipeline-stage2b.md)에 원본 대비 차이·실행 명령·NetCDF/Ybus/해시·회귀 결과를 기록했다.

## Stage 2C: 합성 AC PF·무효전력 보상 검증 재생성

`run_mn35k_acpf.regenerate_for_verification(paths, destination)`는 초기108대 시험3개를 첫 수렴까지 실행한다. `run_mn35k_reactive_support.regenerate_for_verification(paths, destination)`는 원본 비교7개와 기준45대 시나리오를 실행한다. **둘 다 같은 수동 `MN_35k_poc/base.nc`에서 독립적으로 구성한다.** 초기108대 NetCDF는 보상 모델의 구축 입력이 아니라 보존·과거 결과 증거다. EMS 활성 모델·XIIDM·응답과 표준 Stage 1 CLI는 그대로다.

`synthetic_inputs.py`와 `synthetic_contract.json`은 설정2개·보호/비교 모델5개·기존 보고서·원본 코드 등70개 SHA256과 보고서 디렉터리 입력 집합을 검사한다. 설정 사본은 원본과 바이트가 같아야 한다. PyPSA1.2.4·시나리오 ID도 고정한다. 경로 매핑은 Stage 2B의 `logical_input()`을 재사용하고, 원본 스크립트는 실행 의존성으로 import하지 않는다.

각 실행은 `output_dir` 내부의 새 디렉터리로만 공개한다. 초기 묶음16개, 보상 묶음13개(각각 provenance 포함)를 비공개 디렉터리에서 완성·재로드·검증한 뒤 기존 `new_artifact_directory()`로 원자적으로 공개한다. 기존 디렉터리/파일/링크는 덮어쓰지 않는다. PF 실패는 성공 경로를 만들지 않고 별도 `<이름>.failed/failure.json`만 `accepted=false`로 남긴 뒤 예외를 발생시킨다. 파일 생성 예외는 부분 보고서 없이 정리된다. 동일 이름으로 성공 결과를 교체하거나 실패 기록을 덮어쓸 수 없다.

```bash
# EMS 루트. 외부 환경을 직접 사용하며 Pixi run/설치/런타임 활성화를 하지 않는다.
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2c/generated
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage2c-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B - <<'PYVERIFY'
from types import SimpleNamespace
from pipelines.mn.paths import resolve
from pipelines.mn.run_mn35k_acpf import regenerate_for_verification as initial
from pipelines.mn.run_mn35k_reactive_support import regenerate_for_verification as reactive
p = resolve(SimpleNamespace())
# 매번 사용하지 않은 이름 지정. 기존 결과는 같은 내용이어도 교체하지 않는다.
print(initial(p, p.output_dir / "review-initial-new")["artifact"])
print(reactive(p, p.output_dir / "review-reactive-new")["artifact"])
PYVERIFY
```

보고서 본문은 원본 실험의 문구·논리적 경로를 보존했다. 현재 실행할 함수·실제 경로·코드/입력 해시·환경은 이 README와 각 `build_provenance.json`이 제공한다. 원본/이관 코드 hash와 계산 결과 hash를 혼동하지 않는다. 기존45대 NetCDF와 이번 출력의 차원 선언 순서 차이, 정확한 수치 비교·명령·회귀 결과는 [Stage 2C 검증 보고서](../../docs/validation/mongolia-pipeline-stage2c.md)에 기록했다. 실제 계통/운전 자료로 승격하지 않는다.

## Stage 2D: 공개 발전소 후보·실험·계통 진단

`audit_mn35k_plants`, `run_mn35k_data_informed_pf`, `diagnose_mn35k_reactive`, `audit_mn35k_220kv_lines`는 각각 `analyze_for_verification(paths, destination)`을 제공한다. 기존 EMS/운영 CLI에 연결하지 않는다. 진단기는 data-informed PF의 구성 helper를 사용하므로 **발전소 감사 → data-informed PF → 무효전력 진단 → 220kV 감사** 순서로 이관·비교했다. 진단 입력은 원본에 고정된 과거 보고서이며, 새 분석 산출물로 조용히 바꾸지 않는다.

`diagnostic_inputs.py`와 `diagnostic_contract.json`은 기존 Stage 1~2C 경로·보호 기능을 재사용하며151개 입력/보존 파일의 SHA256과8개 보고서 디렉터리 집합을 고정한다. 코드 해시·원본 커밋·출처·실행 환경·결과 해시는 별도로 기록한다. 원본 Python은 해시 증거로만 읽고 실행 의존성으로 import하지 않는다. 과거 브랜치나 `git show`를 데이터 입력으로 사용하지 않는다. Git 저장소가 없는 복사 입력에서도 분석 가능함을 검사했다.

각 분석은 `output_dir` 내부의 새 디렉터리로만 원자적으로 공개한다. 모든 결과를 비공개 영역에서 생성·검사한 뒤 기존 디렉터리 공개 helper로 한 번에 공개한다. 기존 파일/디렉터리/심볼릭 링크/하드링크를 교체하지 않는다. I/O·실행 오류와 불완전 묶음은 최종 결과 없이 정리된다. **완성된 진단 보고서는 수렴 모델을 뜻하지 않는다.** 모든 provenance와 반환값의 `model_usable=false`, `model_activated=false`를 유지한다. 예상 밖 수렴으로 실험 NetCDF가 생성돼도 그 새 분석 묶음에만 포함된다.

공개27개 레코드와265개 공간 후보, 실제 접속 증거0건, 후보 ID1·5의 `ASSUMED_CONNECTION`을 보존한다. data-informed 최종 분류는 `DATA_INFORMED_SCENARIO_NOT_CONVERGED`이며 NetCDF를 생성하지 않는다. 기존45대 기준 모델·XIIDM·N-1·Sensitivity 설정은 그대로다.

```bash
# EMS 루트. 기존 외부 환경을 직접 사용한다. 매번 새 최종 이름 지정.
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2d/generated
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage2d-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B - <<'PYVERIFY'
from types import SimpleNamespace
from pipelines.mn.paths import resolve
from pipelines.mn import audit_mn35k_plants as plants
from pipelines.mn import run_mn35k_data_informed_pf as data
from pipelines.mn import diagnose_mn35k_reactive as reactive
from pipelines.mn import audit_mn35k_220kv_lines as lines
p = resolve(SimpleNamespace())
for name, module in [("plants", plants), ("data-informed", data),
                     ("reactive", reactive), ("220kv", lines)]:
    result = module.analyze_for_verification(p, p.output_dir / (name + "-review-new"))
    print(result["artifact"])
PYVERIFY
```

원본과 이관 구현 및 과거 보고서의 비교, 비수렴·입력/출력 실패 회귀와 전체 검증 명령은 [Stage 2D 보고서](../../docs/validation/mongolia-pipeline-stage2d.md)에 있다. 기존 보고서의 원본 실험 문구·논리적 경로는 유지했으며 실제 실행 경로와 코드 소유권은 README와 `analysis_provenance.json`에서 구분한다.

## Stage 3A: PyPowSyBl 변환과 격리된 EMS 호환성 검증

`convert_mn35k_to_powsybl.convert_for_verification(paths, destination, config_path=None)`는 원본 설정이 지정한 **과거45대 NetCDF**만 변환한다. 입력은 `21bd0d…` hash의 `pypsa-earth/networks/MN_35k_poc/scenarios/synthetic_reactive_support_base.nc`다. 초기108대, 비수렴 data-informed, Stage2C의 다른 산출물을 자동 선택하지 않는다. 설정 사본도 pinned bytes와 같아야 한다.

기존 `mn35k_powsybl.py`는 원본과 바이트 동일하게 유지한다. 원본 변환기의 수식·제어·PF/N-1·허용오차를 보존하고, `conversion_inputs.py`/`conversion_contract.json`으로128개 source와2개 EMS 참조 파일을 검사한다. `conversion_checks.py`는 ID/연결/정격·바이트 기록·재로드·N-1과 EMS 제어 호환성의 공개 전 검증을 담당한다.

한 번의 검증은 XIIDM·기존 계약의 대응표/비교 CSV·JSON·새 Markdown·provenance16개를 비공개 디렉터리에서 완성한 뒤 원자적으로 공개한다. 실패하거나 기존 경로/파일/링크가 있으면 공개하지 않는다. XIIDM `caseDate`에 따른 바이트 hash와, 그 한 필드만 제외한 canonical XML hash를 별도로 기록한다. 전기적 검증을 hash 한 개로 대체하지 않는다.

구축/변환은 기존 외부 Python에서 실행한다. EMS 호환성은 기존 Stage1 `preflight.run_probe()`/`probe.numerical()`을 재사용해 **별도 EMS Python**에서 검사한다. `ModelFiles`에 신규 임시 XIIDM·보고서를 명시적으로 전달해 PF·N-1 세 사례·Sensitivity132개 후보를 독립 객체로 실행한다. 기존 `.venv`의 의존성·설정·활성 파일을 바꾸지 않는다. 운영 변환 CLI·배포 번들 설치/롤백은 활성화하지 않았다.

```bash
# EMS 루트, 반드시 사용하지 않은 새 최종 이름 지정.
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage3a/generated
PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage3a-mpl PYTHONDONTWRITEBYTECODE=1 \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B - <<'PYVERIFY'
from types import SimpleNamespace
from pipelines.mn.paths import resolve
from pipelines.mn.convert_mn35k_to_powsybl import convert_for_verification
p = resolve(SimpleNamespace())
print(convert_for_verification(p, p.output_dir / "historical-review-new"))
PYVERIFY
```

이 함수는 실제 모델을 새로 변환하며 PyPSA/PyPowSyBl 환경과 기존 EMS Python·참조 파일이 필요하다. 결과의 `validation_passed=true`/`runtime_compatible=true`는 검증 통과를 뜻하며 `activated=false`를 유지한다. 미검증 실제 계통 데이터로 승격하거나 기존 모델을 교체하지 않는다. [Stage 3A 검증 보고서](../../docs/validation/mongolia-pipeline-stage3a.md)에 원본 비교·hash·수치·예외·전체 테스트와 Stage3B 요구사항을 기록했다.

## Stage 3B: 독립 배포 후보 번들 생성·검증

`bundle_build.create_bundle()`은 `Paths.bundle_dir` 안에 고유한
`mn-osm-35k-{historical|stage3a}-<version>` 디렉터리를 원자적으로 공개한다.
기본 입력은 기존 과거 XIIDM과 해당 보고서 묶음이다. 모델 재변환·설치·활성화는
수행하지 않는다. PyPSA 없이 기존 EMS Python으로 생성/검증할 수 있지만,
**생성 시에는** 원본 데이터와 Stage 1의 87개 기준 파일이 필요하다.

```bash
# EMS 루트. version은 매번 새 이름으로 지정한다. 기존 결과는 교체하지 않는다.
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
.venv/bin/python -B -m pipelines.mn.bundle_cli create \
  --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --bundle-dir outputs/mn/bundles --version review-new

# Stage 3A의 완성된 변환 묶음을 명시적으로 선택할 때만:
.venv/bin/python -B -m pipelines.mn.bundle_cli create \
  --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --bundle-dir outputs/mn/bundles --version review-new \
  --model-source stage3a \
  --conversion-directory outputs/mn_stage3a/generated/historical-02

# 생성 명령이 출력한 trusted_manifest_sha256을 별도 검토/보관한 후 전달한다.
# 번들 안의 manifest를 즉석에서 hash해 신뢰 기준으로 삼지 않는다.
.venv/bin/python -B -m pipelines.mn.bundle_cli validate \
  --bundle outputs/mn/bundles/mn-osm-35k-historical-review-new \
  --manifest-sha256 '<외부에 보관하고 승인한 SHA256>'
.venv/bin/python -B -m pipelines.mn.bundle_cli runtime \
  --bundle outputs/mn/bundles/mn-osm-35k-historical-review-new \
  --manifest-sha256 '<외부에 보관하고 승인한 SHA256>' \
  --runtime-python "$PWD/.venv/bin/python" --ems-root "$PWD"

# 신규 고정 샘플과 실제 원본/복사본 검사; 원본 프로젝트는 수정하지 않는다.
.venv/bin/python -B -m pytest -q tests/mn_build/test_stage3b_samples.py
KEMS_MN_SOURCE_PROJECT="$KEMS_MN_SOURCE_PROJECT" \
.venv/bin/python -B -m pytest -q tests/mn_build/test_stage3b_actual.py \
  --basetemp=outputs/mn_stage3b/independent-review-new
```

생성 결과 JSON을 저장하려면 **기존 파일을 덮어쓰지 않는 별도 경로**에 보관한다.
이 receipt는 서명이 아니며, 무신뢰 경로에서 번들과 함께 받은 receipt만으로는
출처를 인증할 수 없다. 검토한 hash를 별도 신뢰 채널/배포 승인 기록에 고정해야 한다.
정적 검증에는 표준 라이브러리만 필요하며 `Paths`나 원본 경로를 읽지 않는다.
런타임 검증에는 EMS 코드·고정 계약·EMS Python 및 실행 라이브러리가 필요하다.
PyPSA/Earth/Pixi/원본 NetCDF/변환 venv는 필요 없다.

독립성 테스트는 Linux `bwrap`의 읽기 전용 mount와 빈 mount로 원본 프로젝트,
Earth, 기존 EMS `data/`, `outputs/`를 가리고 `/tmp`의 번들 복사본에서 검증한다.
테스트용 bwrap은 EMS 실행 의존성이 아니다. 생성의 비대체 공개는 기존 Linux
`renameat2(RENAME_NOREPLACE)`와 고정한 부모 디렉터리 fd를 사용하며 지원되지 않는
플랫폼에서는 실패한다. 정적 검증/읽기 전용 실행은 공개 기능과 분리되어 있다.

`runtime_compatible=true`여도 `activated=false`다. GADM 경계가 포함되어
재배포/상업 이용 허가가 확인되지 않았으므로 `external_distribution_approved=false`를
유지한다. 사내 상업 PoC의 이용 권한도 확인됐다고 주장하지 않는다.
Stage 3C에서는 새 개발용 프로세스의 선택·종료·복귀만 검증했다.
기존 서버의 활성 경로 전환·워크스테이션 전송·설치는 별도 승인 대상이다.
[Stage 3B 검증 보고서](../../docs/validation/mongolia-pipeline-stage3b.md)에
런타임 파일 계약, 수치 검증, 독립성 증거, 출처·재배포 검토와 회귀 결과를 기록한다.

## Stage 3C: 번들만 사용하는 EMS 실행과 복귀

번들 선택은 **프로세스 시작 시 고정**한다. 아래 두 환경변수를 함께 전달하면 지도·PF Preview·참조 레이어·XIIDM·대응표·검증 보고서를 manifest의 동일 버전에서 읽는다. 둘 중 하나만 설정하거나 빈 값을 전달해도 명시적 선택 실패다. 잘못된 번들에서 외부 원본으로 fallback하지 않는다. 두 변수를 모두 생략할 때만 기존 개별 `AI_EMS_MN_XIIDM_FILE`/`AI_EMS_MN_POWSYBL_REPORTS_DIR` 방식이 유지된다. 번들 선택 시 이 개별 경로들은 사용하지 않는다. `KEMS_MN_BUNDLE_DIR`은 구축 출력 부모를 지정하는 Stage 1 설정이므로 런타임 선택자와 다르다.

| 런타임 설정 | 의미 |
|---|---|
| `AI_EMS_MN_BUNDLE_DIR` | 읽기 전용으로 선택할 완성된 번들 디렉터리 |
| `AI_EMS_MN_BUNDLE_MANIFEST_SHA256` | 운영자가 번들 외부의 승인 기록에서 전달하는 신뢰 SHA256 |

이번에 승인된 로컬 시험 대상은 다음과 같다. manifest를 즉석에서 hash한 값을 승인 값처럼 사용하지 않는다.

| 버전 | `outputs/mn_stage3b/bundles/` 아래 디렉터리 | 승인 manifest SHA256 |
|---|---|---|
| Historical | `mn-osm-35k-historical-20261029-review02` | `9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c` |
| Stage 3A | `mn-osm-35k-stage3a-20261029-review02` | `953022c62fb8a4cbb377f8bdb96e248252027616e58b3085ddf0da4eafdf0bd9` |

새 개발 서버 예시다. 기존 프로세스와 겹치지 않는 포트를 선택하고 기존 `.env`는 편집하지 않는다. 포트 18000/18001은 예시이며 이번 자동 검증은 매번 OS가 배정한 빈 포트를 사용했다.

```bash
# 터미널 1: 별도 Physics 서버. 현재 Physics API는 KPG용이다.
PYTHONPATH="$PWD/src:$PWD" \
.venv/bin/python -B -m uvicorn ai_ems.api.app:app --host 127.0.0.1 --port 18001

# 터미널 2: 새 Web 서버. 아래 환경변수는 이 자식 프로세스에만 전달한다.
AI_EMS_MN_BUNDLE_DIR="$PWD/outputs/mn_stage3b/bundles/mn-osm-35k-historical-20261029-review02" \
AI_EMS_MN_BUNDLE_MANIFEST_SHA256=9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c \
PYTHONPATH="$PWD/src:$PWD" \
.venv/bin/python -B -m uvicorn web_app:app --host 127.0.0.1 --port 18000

# 터미널 3: 실제 선택·검증 상태 및 HTTP 해석 확인
curl --fail http://127.0.0.1:18001/api/health
curl --fail http://127.0.0.1:18000/api/health
curl --fail http://127.0.0.1:18000/api/grids
curl --fail http://127.0.0.1:18000/api/grids/mn-osm-35k/runtime-status
curl --fail http://127.0.0.1:18000/api/grids/mn-osm-35k/pf-preview
curl --fail 'http://127.0.0.1:18000/api/grids/mn-osm-35k/security-analysis?outage_line_id=751261406-1_0'
curl --fail 'http://127.0.0.1:18000/api/grids/mn-osm-35k/sensitivity-analysis?outage_line_id=751261406-1_0&monitored_line_id=303374638-1_0&top_n=45'
```

Web의 몽골 API/Agent는 같은 프로세스 안의 기존 Mongolia 서비스와 PyPowSyBl을 호출한다. Physics 서버를 먼저 확인하는 절차는 두 서버의 상태를 구분하기 위한 것이며 몽골 계산이 Physics HTTP를 거친다는 의미는 아니다. `/api/health`는 일반 서버 생존 확인이다. 몽골의 준비 상태는 `/api/grids/mn-osm-35k/runtime-status`의 `VERIFIED`, `available=true`, `numerical_checks=186` 및 `security-status`로 확인한다. 검증 실패는 몽골 기능만 unavailable/503으로 처리하며 KPG·한국 OSM 조회는 유지한다.

런타임은 신뢰 manifest, 정확한 파일 집합·바이트·링크·출처 계약을 검사하고 실제 PF/N-1/Sensitivity186개 값을 대조한 뒤 준비 상태가 된다. 진단은 번들 ID·승인 SHA·선택된 경로·버전·제어를 제공하며 비밀 환경변수는 출력하지 않는다. 요청 전후에도 무결성을 재검사한다. 실행 중 변조가 발견되면 캐시를 폐기하고 같은 프로세스에서는 재활성화하지 않는다. 파일을 복구한 뒤에도 새 프로세스로 재검증한다. HTTP 요청으로 번들/파일 경로를 바꿀 수 없다.

안전한 전환·복귀 절차:

1. 시험 서버의 진단 JSON과 API 결과를 번들 밖의 새 증거 경로에 저장한다.
2. **직접 시작한 시험 프로세스만** Ctrl+C로 정상 종료한다. 기존 EMS 서버는 그대로 둔다.
3. Stage 3A 디렉터리와 해당 승인 SHA를 함께 전달하여 새 프로세스를 시작하고 동일 검사를 실행한다.
4. 시험 프로세스를 정상 종료하고 위 Historical 설정으로 새 프로세스를 시작한다.
5. 최초 Historical 번들 ID·manifest SHA·모델 경로·186개 값·Agent 도구 결과로 복귀했는지 확인한다. 서로 다른 버전의 파일을 합치지 않는다.

실제 독립 서버·Chrome 재검증 명령은 다음과 같다. `/tmp` 또는 개발 저장소 밖의 새 `--basetemp`를 사용한다. `bwrap`은 시험에서 원본 프로젝트와 개발 EMS 전체를 빈 mount로 가리는 도구이며 일반 EMS 실행 의존성은 아니다.

```bash
AI_EMS_STAGE3C_TESTS=1 PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -B -m pytest -q tests/test_mongolia_bundle_runtime.py \
  tests/test_mongolia_bundle_servers.py --basetemp=/tmp/mn-stage3c-review-new

AI_EMS_STAGE3C_TESTS=1 AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -B -m pytest -q --basetemp=/tmp/mn-stage3c-chrome-review-new
```

Stage 4 준비 항목:

- **코드/데이터:** `src/ai_ems`, `web_app.py`, `ui/`, `studies/`, `pipelines/mn/`의 번들 검증 코드와 고정 계약, 승인된 완성 번들 한 버전. Web이 시작 시 KPG도 읽으므로 `data/KPG193_ver2_0_powsybl_full.mat`, `data/bus_location.csv`, `data/grids/kr-geography.json`, `data/grids/kr-osm-transmission-map.json`도 필요하다. 몽골의 별도 개발용 지도/PF/NetCDF/원본 보고서는 필요 없다. 한국 검토 도구를 별도 서비스하려면 그 정적 데이터와 기존 서비스 절차도 별도로 준비한다.
- **실행 환경:** 검증한 Python3.11.16, PyPowSyBl1.16.1 및 native 엔진, numpy/pandas/scipy/networkx/python-dotenv, 기존 FastAPI/Uvicorn·LangChain/LangGraph·Ollama 클라이언트와 KPG 의존성. `requirements.txt`는 이번에 바꾸지 않았다. 이미 설치된 networkx의 명시적 런타임 의존성 선언·대상 OS의 native 엔진 호환성은 Stage4 전에 정리해야 한다. 전체 검증 버전은 보고서에 있다.
- **구축 전용:** PyPSA, GIS, Powerplantmatching, Pixi, Earth 소스, 원본 PBF/NetCDF, `.venv-powsybl`은 번들 기반 EMS 실행에 필요 없다. Chrome·pytest·bwrap은 검증 도구다. 이번 시험은 기존 EMS venv를 읽기 전용으로 연결했으며 환경 설치/갱신은 하지 않았다.
- **설정/네트워크:** 번들 두 선택자와 필요 시 `AI_EMS_CASE_FILE`, `AI_EMS_BUS_LOCATION_FILE`, `AI_EMS_KOREA_MAP_FILE`. Web/Physics를 별도 loopback 포트로 실행한다. 몽골 지도/PF/N-1/Sensitivity에는 외부 원본 서버가 필요 없다. LLM을 사용할 때만 승인한 목적지의 DNS·TLS·네트워크 접근과 `AI_EMS_MODEL`, `AI_EMS_LLM_BASE_URL`, 기존 인증 설정이 필요하다. 인증 값은 승인된 비밀 관리 경로로 전달하고 보고서에 출력하지 않는다.
- **Agent:** 몽골 선택 시 기존 `/api/grids/mn-osm-35k/chat`를 사용한다. 도구 이름은 몽골 전용이며 실제 계통/발전소 접속이 검증됐다는 뜻이 아니다. 로컬 모의 LLM 검증과 실제 외부 LLM 연결 결과를 구분한다. 이번 승인된 외부 요청은503으로 실패했으므로 실제 모델 응답 성공으로 보고하지 않는다.
- **장애/승인:** `runtime-status`의 원인을 확인하고 실패 번들을 교체 승인하지 않는다. 해당 시험 프로세스만 종료한 뒤 승인된 Historical로 재시작한다. 원본87개 계약 중 이번에 수정한 코드3개는 별도 SHA 변경 기록을 남겼다. 현재 트리의 Stage1 엄격 검사는 이3개를 NOT_READY로 판정하며 원래 계약을 완화하지 않았다. 새 구축 기준선 승인은 독립 리뷰 후 별도 작업이다. 기존 데이터/모델84개 비교와 과거 코드 복사본의87개 검증은 보고서에서 구분한다.
- **이전 제한:** GADM 등 번들 출처별 이용·재배포 조건 및 사내 이전 승인이 해결되어야 실제 워크스테이션 설치를 진행할 수 있다. 이번은 현재 개발 PC 내부 검증이며 파일 반출·상업/수출 패키지 승인을 수행하지 않았다.

실제 명령, API·브라우저·원본 접근 차단 증거와 잔여 제한은 [Stage 3C 검증 보고서](../../docs/validation/mongolia-pipeline-stage3c.md)에 기록한다.

## 다음 이관 순서

먼저 모든 `ROOT`, `EARTH`, `REPORTS`, `Path.home()`, `sys.path.insert`와 보호 출력 경로를 명시적 경로 객체로 전환한다. 해시 검사를 제거하지 않고 논리적 원본 식별자와 물리 위치를 구분한다. 이후 다음 순서로 작게 이관한다.

| 순서 | 파일 | 확인할 검증 |
|---|---|---|
| 1 (Stage 2A 완료) | `audit_mn35k_parameters.py`, `mn35k_lengths.py`, `mn35k_refinement.py`, `mn35k_powsybl.py` | 거리/단위/정수 변환·원본 보호·순수 helper 테스트 |
| 2 (Stage 2B 완료) | `validate_mn_grid.py`, `validate_mn35k_poc.py`, `build_mn35k_poc.py` | 173/159/27, 원본 보존, 길이 미해결 18개, NetCDF/Ybus·재현성 |
| 3 (Stage 2C 완료) | `run_mn35k_acpf.py`, `run_mn35k_reactive_support.py` | 초기 108대와 현재 45대 시나리오 분리, 166/156/27·PV43/PQ122/Slack1·20MVAr |
| 4 (Stage 2D 완료) | `audit_mn35k_plants.py`, `run_mn35k_data_informed_pf.py`, `diagnose_mn35k_reactive.py`, `audit_mn35k_220kv_lines.py` | 공개 레코드·접속 후보·비수렴 상태·부하/길이/Q 진단 보존 |
| 5 (Stage 3A 완료) | `convert_mn35k_to_powsybl.py` | 이미 이관한 `mn35k_powsybl.py` 사용, 양 엔진 단위/부호/기준용량·PF/N-1 비교 |
| 6 | 기존 EMS exporter·번들 경계 정리 (후속 단계) | 지도–PF–모델 해시 계약, 실제 배포/롤백·런타임 독립성 |

AST 위상 정렬은 import 의존 그룹 6개를 산출한다. 위 수동 순서는 데이터 소비 관계까지 반영하므로 위상 그룹과 동일하지 않다. 원본 8개 테스트 파일은 대응 모듈과 함께 별도 구축 테스트 영역으로 이관한다. bare import와 `tests`의 `sys.path` 주입도 같이 정리한다.

특히 `mn35k_refinement.export_deterministic()`은 모듈 위치에서 보호 원본 경로를 다시 계산하고, 빌더는 전용 출력 디렉터리와 hardlink/symlink를 검사한다. 단순 폴더 이동으로 보호 기능을 무력화하면 안 된다. 변환기는 기존 EMS PF 파일과 보고서를 보호 manifest에 넣는다. 감사 코드는 절대 경로·과거 브랜치·`git show`에 의존하는 경우가 있어 별도 정리가 필요하다.

현재 `scripts/export_pypsa_map.py`는 `prepare_kpg_geography.py`도 사용하므로 위치와 함수 계약을 유지한다. PyPSA-Earth 소스, 기존 환경과 대용량 원본·산출물은 이관하지 않는다. 버전 manifest와 작은 설정·코드·테스트·문서만 Git 관리한다.

실제 기준선 결과: [1단계 검증 보고서](../../docs/validation/mongolia-pipeline-stage1.md).
