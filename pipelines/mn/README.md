# 몽골 파이프라인 — 1단계 기반

현재 기능의 기준선과 경로·환경을 **읽기 전용으로 검사**한다. 원본 스크립트 이관, 모델 구축·변환, 번들 생성·설치, EMS 런타임 경로 변경은 이번 단계에서 수행하지 않는다. 이 패키지를 EMS가 import하지 않으며, Python 표준 라이브러리로 검사 명령을 구동한다. 필요한 외부 라이브러리는 선택한 인터프리터의 별도 subprocess에서만 조회한다.

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

## 다음 이관 순서

먼저 모든 `ROOT`, `EARTH`, `REPORTS`, `Path.home()`, `sys.path.insert`와 보호 출력 경로를 명시적 경로 객체로 전환한다. 해시 검사를 제거하지 않고 논리적 원본 식별자와 물리 위치를 구분한다. 이후 다음 순서로 작게 이관한다.

| 순서 | 파일 | 확인할 검증 |
|---|---|---|
| 1 | `audit_mn35k_parameters.py`, `mn35k_lengths.py`, `mn35k_refinement.py`, `mn35k_powsybl.py` | 거리/단위/정수 변환·원본 보호·순수 helper 테스트 |
| 2 | `validate_mn_grid.py`, `validate_mn35k_poc.py`, `build_mn35k_poc.py` | 173/159/27, 원본 보존, 길이 미해결 18개, NetCDF/Ybus·재현성 |
| 3 | `run_mn35k_acpf.py`, `run_mn35k_reactive_support.py` | 초기 108대와 현재 45대 시나리오 분리, 166/156/27·PV43/PQ122/Slack1·20MVAr |
| 4 | `audit_mn35k_plants.py`, `run_mn35k_data_informed_pf.py`, `diagnose_mn35k_reactive.py`, `audit_mn35k_220kv_lines.py` | 공개 레코드·접속 후보·비수렴 상태·부하/길이/Q 진단 보존 |
| 5 | `convert_mn35k_to_powsybl.py` | 이미 이관한 `mn35k_powsybl.py` 사용, 양 엔진 단위/부호/기준용량·PF/N-1 비교 |
| 6 | 기존 EMS exporter·번들 경계 정리 (후속 단계) | 지도–PF–모델 해시 계약, 실제 배포/롤백·런타임 독립성 |

AST 위상 정렬은 import 의존 그룹 6개를 산출한다. 위 수동 순서는 데이터 소비 관계까지 반영하므로 위상 그룹과 동일하지 않다. 원본 8개 테스트 파일은 대응 모듈과 함께 별도 구축 테스트 영역으로 이관한다. bare import와 `tests`의 `sys.path` 주입도 같이 정리한다.

특히 `mn35k_refinement.export_deterministic()`은 모듈 위치에서 보호 원본 경로를 다시 계산하고, 빌더는 전용 출력 디렉터리와 hardlink/symlink를 검사한다. 단순 폴더 이동으로 보호 기능을 무력화하면 안 된다. 변환기는 기존 EMS PF 파일과 보고서를 보호 manifest에 넣는다. 감사 코드는 절대 경로·과거 브랜치·`git show`에 의존하는 경우가 있어 별도 정리가 필요하다.

현재 `scripts/export_pypsa_map.py`는 `prepare_kpg_geography.py`도 사용하므로 위치와 함수 계약을 유지한다. PyPSA-Earth 소스, 기존 환경과 대용량 원본·산출물은 이관하지 않는다. 버전 manifest와 작은 설정·코드·테스트·문서만 Git 관리한다.

실제 기준선 결과: [1단계 검증 보고서](../../docs/validation/mongolia-pipeline-stage1.md).
