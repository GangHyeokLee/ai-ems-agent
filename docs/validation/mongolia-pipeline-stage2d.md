# 몽골 파이프라인 Stage 2D — 발전소 후보·실험·계통 진단 이관

작업일: 2026-10-29 (Asia/Seoul). 시작 HEAD는 `8f17e925fcfd86e28d3dbbc546a8d324be28570a`이며, 로컬 `feature/kr-osm-map-integration`이 `origin/feature/kems-multi-grid`를 추적하고 동기화된 상태였다. 작업 트리는 깨끗했다. 브랜치 전환·fetch·Commit·Push·PR·Merge·amend를 수행하지 않았다.

원본 몽골 HEAD `748c0811854b959f9df09377317499056a51601e`, Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`와 기존 미추적 Earth `config.MN_35k.yaml`을 보존했다. Stage 1 계약·[Stage 1](mongolia-pipeline-stage1.md)·[Stage 2A](mongolia-pipeline-stage2a.md)·[Stage 2B](mongolia-pipeline-stage2b.md)·[Stage 2C](mongolia-pipeline-stage2c.md) 문서를 확인했다. 기존 저장소·모델·이전 단계 산출물4,646개 파일 SHA256과 Git 상태를 [시작 스냅샷](../../outputs/mn_stage2d/before-preservation.json)에 기록했다.

## A. 변경 파일과 이관 기능

| 파일 | 역할 |
|---|---|
| `pipelines/mn/audit_mn35k_plants.py` |27개 공개 발전소 레코드 정체성·중복·다중유닛 검토, 공간 모선 후보 |
| `pipelines/mn/run_mn35k_data_informed_pf.py` |고정 후보1·5의 가정 접속, 합성 P/Q 배분·제한·PF와 부하5x/10x 실험 |
| `pipelines/mn/diagnose_mn35k_reactive.py` |선로 충전·전압·Q 한도·PV→PQ·Jacobian·분로/전압별 민감도 진단 |
| `pipelines/mn/audit_mn35k_220kv_lines.py` |19개220kV 선로 exact-ID 길이 계보·기하·회선·정수·충전 감사 |
| `pipelines/mn/diagnostic_inputs.py` |경로·설정·입력 검증, 분석 묶음 원자적 공개와 provenance |
| `pipelines/mn/diagnostic_contract.json` |151개 입력/보존 파일·8개 보고서 디렉터리 집합·원본 커밋 고정 |
| `tests/mn_build/test_stage2d_samples.py` |원본 대응 고정 샘플14개, 외부 원본 없이 실행 |
| `tests/mn_build/test_stage2d_evidence.py` |공개 레코드·기존 감사 보고서 실제 데이터 검사9개 |
| `tests/mn_build/test_stage2d_comparison.py` |원본/이관 전체 실행4개, 파일별 비교·결정성·재실행 보호 |
| `tests/mn_build/test_stage2d_protection.py` |입력·경로·링크·중간 오류·공개 경쟁·조건부 NetCDF 보호79개 |
| `pipelines/mn/README.md`, 이 문서 |검증 함수·명령·실제 결과·제한·후속 의존성 |

추가 기존 테스트 정리는 다음과 같다.

- 요청된 `test_stage2c_acpf.py`, `test_stage2c_reactive_support.py`: EOF의 불필요한 newline1byte씩만 제거했다. 수정 전후 AST와 나머지 바이트가 같음을 검사했다.
- `test_stage2c_bundles.py`: EMS 전체 수집에서 `xarray`를 PyPSA skip 검사보다 먼저 import하는 기존 오류를 발견했다. **import 한 줄만 기존 PyPSA 검사 아래로 이동**했다. 테스트 함수·assert·계산은 수정하지 않았다. 구축 의존성을 EMS에 추가하지 않았다.

구축용 import 의존성은 발전소 감사 → data-informed PF → 무효전력 진단이다. 220kV 감사는 Stage 2A 길이 helper와 고정된 과거 진단 CSV를 사용한다. 따라서 위 순서로 이관하고 원본 실행과 비교했다. 현재 EMS·운영 CLI와 연결하지 않는다.

## B. 계산·판정 동등성

원본과 이관 구현은 동일한 외부 Python·설정·입력으로 실행했다. 원본 스크립트의 `audit()`/`run()`은 모든 필요한 입력·과거 보고서를 복사한 별도 프로젝트에서 실행했다. 원본의 고정 전역 경로와 길이 함수의 bound default root를 테스트에서 그 복사본으로 주입했다. 원본/Earth에는 쓰지 않았다. 원본 모듈 로드는 비교 테스트에만 있고 `sys.path` 변경은 없다.

원본 대비 정확한 함수 AST 일치는 발전소5개·data-informed12개·진단18개·220kV7개, 총42개다. 변경은 다음 IO 경계에 한정한다.

1. `ROOT`·`EARTH`·`REPORTS` 등 고정 전역 경로를 `Paths`와 기존 `logical_input()`으로 주입한다. 출처의 논리 경로 문자열은 그대로다.
2. `build_audit()`에 명시적 `paths`, 진단 `_scenario_summary()`에 `base_config`를 전달한다. 수식·정수·판정은 보존한다.
3. `audit_line_lengths(length_source, paths)`를 재사용하고 220kV 보고서 출력 경로를 명시한다. helper를 중복 이관하지 않았다.
4. OSM 입력 누락 시 빈 목록으로 숨기던 처리는 `FileNotFoundError`로 바꿨다. 정상 입력의 계산은 동일하며, 전체 분석은 생성 전에151개 해시/파일 집합을 검사한다.
5. 순차 공개·덮어쓰기 대신 기존 Stage 2A 보호 helper로 완성된 새 분석 디렉터리만 공개한다. 원본의 NetCDF 조건부 생성은 새 묶음 내부에서만 허용한다.

[원본/이관 코드 hash·AST 기록](../../outputs/mn_stage2d/code-ownership.json)에 파일별 SHA256과 변경 함수가 있다. 원본 코드 hash와 새 소유 코드17개 hash는 각 provenance에서 별도로 기록한다. 코드 위치·보호 manifest 차이를 전기적 차이로 설명하지 않는다.

### 산출물 비교

| 분석 | 원래 생성하는 파일 | 원본 재실행·기존 보관 결과 대비 바이트 일치 |
|---|---:|---|
| 발전소 감사 |CSV2 |2/2 |
| data-informed PF |19 |18/19; `source_preservation.json`의 보호 목록 확대 |
| 무효전력 진단 |11 |10/11; `diagnostic_assumptions.json`의 보호 목록 확대 |
|220kV 감사 |7 |6/7; Markdown의 보호 파일 수 확대 |

다른 JSON은 확대된 보호 hash 두 필드만 제거한 뒤 정확히 비교했다. 220kV Markdown은 보호 파일 수 문구만 정규화한 뒤 비교했다. CSV·PNG·판정·나머지 보고서는 **바이트 비교**이며 새 허용오차를 사용하지 않는다. 원본 길이 교차검사의 `rtol=1e-9/atol=1e-8`, LineType 검사 `rtol=1e-10/atol=1e-12`, 기존 PF·Q 제한·수지 기준은 유지했다.

모든 파일의 원본 재실행/이관/과거 보관 SHA256은 [비교 결과 디렉터리](../../outputs/mn_stage2d/compare-01)에 있는 분석별 `equivalence.json`에 기록했다. 비교 테스트는 두 번째 새 디렉터리에 완전 재실행하여 **provenance·그림까지 바이트 결정성**을 검사하고, 기존 이름 재실행 거부를 확인했다.

## C. 발전소 후보의 데이터 한계

27개 레코드의 개별 `record_id=0..26`을 유지했다. 공간 모선 후보는265행, 실제 전기적 접속 증거는0건이다.

| 정체성 판정 | 행 수 |
|---|---:|
| `DISTINCT_CONFIRMED` |16 |
| `DUPLICATE_CANDIDATE` |8 |
| `IDENTITY_UNRESOLVED` |2 |
| `MULTI_UNIT_CANDIDATE` |1 |

`DISTINCT_CONFIRMED`는 source record의 구별을 뜻하며, 독립 발전기·현 운전상태·접속 확인을 뜻하지 않는다. 이름 정규화와 좌표 근접성은 검토 자료로만 사용한다. 레코드를 자동 병합·삭제하거나 단위별 발전기로 분할하지 않았다.

- Ulaanbaatar TES6행은 동일 GEO ID/좌표와92.6335MW3회·74.1068MW3회 반복을 중복 후보로 유지한다. 합산하지 않는다.
- 839MW Ulaanbaatar 행의 다중 source ID와 TES 행의 실제 설비 경계·연료/용량 중복은 미해결이다.
- Khushight Kundi/Khushig Khundii15MW 두 행과 Serven/Taishir의 좌표 충돌도 원래 판정을 유지한다.
- 용량·연료·좌표·연도는 `PUBLIC_DATA_ESTIMATE`, 현재 운전상태는 `UNRESOLVED`다. Powerplantmatching0.9.1 계보와 OSM→PPM 중간 CSV0행을 기록한다. 원자료 snapshot/운영자 검증은 확보되지 않았다.

실험 후보는 ID1·5, 모선46·16, 합산 명목 추정71MW다. 선정은 고유 source record·원본 연도 필드·25km 이내 공간 후보 기준일 뿐, `ASSUMED_CONNECTION`과 `SYNTHETIC_ASSUMPTION`을 유지한다. 실제 접속·출력을 확정하지 않는다.

## D. Data-informed PF 상태 보존

최종 분류는 **`DATA_INFORMED_SCENARIO_NOT_CONVERGED`**이며 새 NetCDF가 없다. 기존45대 기준 구성과 발전소 후보 실험·부하 민감도를 구분한다.

| 실험 | 최종 PF | 합성 부하 MW | 의미 |
|---|---|---:|---|
| 기존45대+20MVAr 재실행 |CONVERGED |25.6 |기준 재현, 활성 파일 교체 없음 |
| 후보1·5+Slack, Q 제한 후 |NOT_CONVERGED |25.6 |현재 기준으로 채택하지 않음 |
| 기준 부하5x |CONVERGED |128 |합성 민감도, 실제 수요 추정 아님 |
| 기준 부하10x |CONVERGED_WITH_VIOLATIONS |256 |위반을 유지하며 채택하지 않음 |

후보의 P 배분은 기존대로 합성 부하90%를 두 용량 추정치에 비례 배분하고 Slack10%를 초기 지정한다. Q 한도는 최소 역률0.9 가정이며 실제 능력곡선이 아니다.

후보 제한 전 PF는11회, error `9.404687167924308e-10`에서 수렴했으나 발전 P/Q는 약83.018413MW/-1036.182612MVAr, 전압은0.197119~1.560629pu다. Q 한도·전압·운전제약을 위반한 **중간 진단값**이다. PV 두 대를 PQ로 바꾸면100회, error `1.3718525623804801e45`에서 비수렴한다. 이 잔차를 물리적 최종 전력수지로 표시하지 않으며, 최종 `total_generation_*`는 비워 두고 `last_converged_*`만 진단으로 보존한다.

원본 `x_tol=1e-6`, flat start, distributed_slack=false, 기본100회, PV Q 판정1e-9와 수지 한도1e-4MVA를 변경하지 않았다. 비수렴을 성공으로 바꾸거나 수렴 한도를 넓히지 않았다. 예상 밖의 수렴 시에도 조건부 NetCDF는 새 분석 디렉터리 안에만 남고 `model_usable=false`, `model_activated=false`를 유지하는 별도 모의 회귀도 추가했다. 이 회귀는 실제 후보가 새로 수렴했다는 관측이 아니다.

## E. 무효전력·220kV 진단 결과

무효전력 진단의 전체156개 선로1pu 충전량은 **934.5050027953569MVAr**다. 선로 충전 공식 `B=2πf·C·1e-9·L·n_parallel`, `Q=B·V_nom²·V_pu²`와 실제 branch 양단 Q·변압기/분로 Q를 분리한다. 0.95~1.05pu 충전 범위와 기존 운전 화면0.9~1.1pu 판정도 서로 바꾸지 않았다.

후보 초기 해의 전압 위반은119개다. 초기 PV/PQ/Slack=2/163/1에서0/165/1로 전환된다. 유한차분 Jacobian은 미지수328→330, rank328→330, 조건수 약3.063907948e6→3.144054084e6이다. 평가점에서 full rank라는 관측을 전체 Newton 경로의 비특이성이나 실제 공급 가능성으로 확대하지 않았다. Q 제한 후 비수렴 상태를 그대로 기록했다.

220kV 대상은 최대 연결 성분의19개 선로이고1pu 충전량은 **713.9801783723381MVAr**다. 원본 표준 LineType은 `Al/St 240/40 2-bundle 220.0`, C=12.5nF/km, r=0.06Ω/km, x=0.301Ω/km,50Hz이며 회선수와2-bundle을 구별한다.

| 감사 판정 | 결과 |
|---|---|
| 회선수 |SOURCE_SUPPORTED9 / STANDARD_ASSUMPTION8 / UNRESOLVED2 |
| 모델 길이 |STANDARD_ASSUMPTION18 / UNRESOLVED1 |
| 현장 검증 |모두 false |
| 수정 후보 |1개, `apply_now=false` |
| PF 실행/정수 수정 |없음 |

`221951632-1_0`의 분기·cycle 때문에 길이를 안전한 측지거리로 자동 교체하지 않는다. `237132377-1_0`과 `484276912-1_0` 사이 약0.13189714958km의 build 교차 중첩과 원본 geometry 밖의 계보도 유지한다. raw→clean→build→model은 exact-ID로 검사하며 미해결 길이18개 정책을 변경하지 않았다. OSM 출처 근거를 현장 검증으로 승격하지 않는다.

## F. 입력 hash·경로·원자성

151개 증거/보존 파일의 SHA256을 고정하고8개 보고서 디렉터리의 누락·추가 파일도 실패시킨다. 기존 Stage 1의87개 역할 계약은 별도로 유지한다. `Paths`와 Stage 2B 논리 경로 매핑을 통해 source/Earth를 독립 주입한다. 원본 모델·보고서·설정·발전소/OSM·과거 manifest·원본 코드 모두 읽기 전용이다.

네 원본의 실행 AST에는 과거 `git show`/subprocess 의존성이 없었다. 과거 브랜치를 현재 입력으로 읽는 경로를 새로 만들지 않았다. 고정 파일 hash와 원본/Earth 커밋을 명시하며 `.git`/과거 브랜치가 없는 복사본에서도 검증 목적을 재현했다. 소스코드 자체의 원본 증거는 hash 확인에만 쓰고 실행 import하지 않는다.

최종 생성 파일 수는 발전소3·data-informed20·진단12·220kV8개이며 각각 provenance1개를 포함한다. 첫 비공개 디렉터리에서 전체 파일을 생성하고 필수/선택 파일·일반 파일·크기·JSON·입력 hash를 검사한다. 생성된 링크도 복사 전에 거부한다. 조건부 실험 NetCDF 때문에 파일 집합이 확정된 뒤 기존 `new_artifact_directory()`의 두 번째 비공개 영역으로 옮겨 **비대체 원자적 rename**으로 최종 디렉터리만 공개한다. 두 임시 영역은 모두 지정 출력 영역 내부다.

실제로 검사한 예외는 모델/설정/증거 누락·SHA 불일치·추가 파일·탈출 입력 링크·원본과 동일 출력·work/output 중첩·기존 파일/빈 디렉터리/정상·dangling symlink/hardlink·생성 중 입력 변경·생성물 링크·공개 직전 대상 생성이다. 각 실제 분석의 CSV 쓰기 중간 실패와 전체 실행 오류도 주입했다. 고정 묶음의3번째·5번째 생성 실패 후 같은 이름으로 정상 재시도함을 확인했다. 실패하면 최종 묶음이 없고, 경쟁으로 생긴 기존 디렉터리는 제거하지 않는다.

`analysis_completed=true`는 진단 보고서 생성 완료이며 PF 수렴/운영 가능 여부가 아니다. 모델 사용·활성화는 항상 false다. 실행 예외는 성공으로 기본 처리하지 않고 호출자에게 전달한다.

## G. 실행 명령과 테스트 결과

EMS 루트에서 기존 외부 환경을 직접 사용했다. Pixi run·설치·환경 병합·EMS 필수 의존성 추가는 하지 않았다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage2d/generated
export PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj"
export MPLCONFIGDIR=/tmp/mn-stage2d-mpl
export PYTHONDONTWRITEBYTECODE=1

"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --junitxml=outputs/mn_stage2d/build-review-new.xml

# 고정 샘플은 원본 경로 설정 없이도 실행한다.
env -u KEMS_MN_SOURCE_PROJECT \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build/test_stage2d_samples.py

# 원본/이관 비교4개. --basetemp는 항상 사용하지 않은 새 이름 지정.
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build/test_stage2d_comparison.py \
  --basetemp=outputs/mn_stage2d/independent-review-new

.venv/bin/python -m pytest -q -ra --tb=short
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage2c/baseline.json \
  --numerical-reference outputs/mn_stage2c/baseline.json \
  --test-results outputs/mn_stage2d/build-complete.xml \
  --test-results outputs/mn_stage2d/runtime.xml --test-results outputs/mn_stage2d/chrome.xml \
  --test-results outputs/mn_stage2d/samples.xml \
  --report outputs/mn_stage2d/baseline-review-new.json

git diff --check
```

분석 재실행 함수4개와 새 이름 지정 예시는 [README](../../pipelines/mn/README.md)에 있다. 신규 결과는 모두 Git 제외 `outputs/mn_stage2d/`에 있다. [분석 결과/파일 해시 요약](../../outputs/mn_stage2d/analysis-summary.json)과 네 `generated/*-01/analysis_provenance.json`에서 실제 실행과 코드 소유권을 확인할 수 있다.

| 실행 | 결과 | 의미 |
|---|---|---|
| 원본 대응 샘플·증거·전체 비교 |27 passed |이 중 고정 샘플14·증거9·실제 비교4 |
| 최초 보호 회귀 |78 passed |이후 조건부 모델 회귀1개 추가 |
| 기존2A~2C + Stage2D 전체 |**324 passed** |기존218+신규106, skip 없음 |
| 원본 경로 없는 고정 샘플 |**14 passed** |외부 데이터 없이 실제 계산 |
| EMS 전체 최종 |**567 passed,32 skipped**,47.02초 |브라우저 opt-in19+구축 모듈13 |
| Chrome 전체 최종 |**586 passed,13 skipped**,151.57초 |구축 모듈13만 skip, 브라우저19개 실행 |
| Stage 1 preflight/migration |PASS /14개 원본·의존 그룹6 |운영 build/convert/activate CLI는 계속 비활성 |
| 기준선 |PASS |87개 SHA256·수치186개 대조 통과 |

초기 EMS/Chrome 수집은 기존 `xarray` import 오류로 실패했다. [초기 수집 오류 로그](../../outputs/mn_stage2d/runtime-collection-error.log)와 Chrome 대응 로그/XML을 별도로 보존했다. 원인을 수정한 뒤 전체를 재실행했으며 초기 실패를 통과에 포함하지 않는다. 조건부 모델 회귀 추가 전323개 전체 실행도 별도 로그/XML로 보존했다. 실행별 중복 테스트 수는 합산하지 않는다.

원본 모델 부재로 skip된 몽골 해석 검사는 없다. EMS 환경의 구축 모듈 skip은 외부 구축 환경에서 모두 실행했다. KPG·한국 OSM·Agent·UI·검토 도구의 기존 기능은 전체 pytest와 실제 Chrome 회귀로 확인했다. Agent의 기존 LLM 모형 응답 테스트를 실제 외부 LLM 관측으로 표시하지 않는다.

외부 환경의 기존 ecCodes/cfgrib·NumPy C-header 경고, 원본/이관 양쪽 pandas concat FutureWarning을 관측했다. 계산을 바꾸거나 환경을 갱신해서 숨기지 않았다. EMS의 기존 Starlette/anyio 경고와 networkx 직접 의존성 미선언 안내도 유지한다. 새 파일도 `git diff --no-index --check /dev/null <파일>`로 검사해 아직 미추적인 파일의 whitespace/EOF까지 확인했다.

## H. Stage 1 기준선과 기존 해석 기능

기존 지도173/159/27, 해석166/156/27, 발전기45·부하166·리액터1·20MVAr·Slack25와 `reduced_27_q_limited_with_reactor`를 변경하지 않았다. 기존 XIIDM·PF/검증 보고서·API·Agent·UI 응답 구조와 전기적 모델은 그대로다.

| N-1 차단 선로 | 상태 | 격리 모선 | 최저/최고 전압 pu |
|---|---|---:|---|
|222087807-1_0 |CONVERGED |8 |0.9992014336196917 /1.0651309002190068 |
|751261406-1_0 |CONVERGED |0 |0.9828079706584585 /1.094031269556094 |
|484333456-1_0 |CONVERGED |11 |0.9827788659397438 /1.094031269556093 |

위반0건을 유지한다. Sensitivity는 기존 outage751261406-1_0, 감시 선로3개×44개=132개 후보와 유한차분 검사를 유지한다. 각1위 값 `-1.0133417708799506`, `-1.0074238413468564`, `-0.9470790553771927`을 포함해 수치·제어·ID186개 대조가 통과했다.

OLF 제어도 Slack VL_25, MAIN_CONNECTED, FIRST_SLACK, distributed_slack=false, uniform 시작·최대30회·UNIFORM_CRITERIA/1e-8 그대로다. 새 실험의100회/1e-6 설정과 혼동하지 않는다. [최종 baseline](../../outputs/mn_stage2d/baseline.json)에87개 파일과 실제 수치·제어를 기록했다.

[전체 보존 검사](../../outputs/mn_stage2d/preservation.json)는 기존4,646개 hash를 대조한다. 기존 파일 변경은 의도한 README·Stage2C EOF2개·import 순서1개뿐이다. 원본 몽골/Earth·기존 모델·보고서·Stage1~2C 산출물은 모두 같다. 신규 코드·문서·테스트·작은 해시 계약만 변경 목록에 있고 원본·NetCDF·캐시·가상환경은 Git에 포함하지 않았다.

## I. Stage 3 전 남은 코드와 외부 의존성

원본14개 자체 스크립트 중13개가 패키지에 이관됐으며 실행 스크립트 `convert_mn35k_to_powsybl.py`1개가 남는다. 기존 `mn35k_powsybl.py` helper는 이미 이관돼 있다. 다음 단계에서는 최종 변환기의 `Path.home()/Projects/ai-ems-agent/...` 결합·원본/EMS 출력 경계·설정·보고서 원자성·모델/ID/단위/부호/제어 계약을 먼저 정리해야 한다.

Stage 2C 기존 보상 NetCDF와 재생성 파일은 차원 선언 순서 때문에 바이트 hash가 다르지만 의미가 같았다. Stage 3에서도 원본 hash를 새 hash로 조용히 교체하지 말고 원본·새 변환 번들·검증·롤백을 구분해야 한다. 두 엔진 PF/N-1/Sensitivity 비교와 사용자 검토 없이 런타임을 전환하지 않는다.

외부 의존성은 명시적으로 주입한 source/Earth의 모델·OSM·발전소 CSV·설정·과거 보고서151개와 별도 Pixi/PyPowSyBl 환경이다. 원본 코드 일부는 과거 manifest의 출처 증거로 hash만 확인하므로 원본 프로젝트를 즉시 제거할 수 있는 단계는 아니다. 향후 고정 증거 패키지화가 필요하지만 이번에 파일을 복사·삭제하거나 보호 pin을 풀지 않았다. PyPSA-Earth 자체, 대용량 원본, 환경은 계속 외부에 둔다.

EMS `scripts/export_pypsa_map.py`는 KPG 지리 생성에서도 쓰므로 이동하지 않았다. 배포 번들·활성화/롤백·외부 원본 의존성 해소·향후 국가별 재사용 경계는 후속 검토 대상이다. 이번에 공통 국가 API나 새로운 계통 알고리즘을 도입하지 않았다.
