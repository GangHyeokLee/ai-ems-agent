# 몽골 Stage 3A — PyPowSyBl 변환기 이관과 격리 검증

작업일: 2026-10-29 (Asia/Seoul). 시작 HEAD `fd4a8c665397b09a547ea5ed36811c8f231018eb`, 로컬 `feature/kr-osm-map-integration`이 `origin/feature/kems-multi-grid`와 동기화된 상태였고 작업 트리는 깨끗했다. 브랜치 전환·fetch·Commit·Push·PR·Merge는 수행하지 않았다.

Stage 1~2D 문서·고정 계약, 원본 변환기, `mn35k_powsybl.py`·`paths.py`·`artifacts.py`·`synthetic_inputs.py`, EMS `mongolia_security.py`·`mongolia_sensitivity.py`·`config.py`를 먼저 조사했다. 원본 HEAD `748c0811854b959f9df09377317499056a51601e`, Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`와 기존 미추적 `config.MN_35k.yaml`을 확인했다. 기존 파일4,733개 hash와 저장소 상태를 [시작 스냅샷](../../outputs/mn_stage3a/before-preservation.json)에 기록했다. 이전 단계의 공개 결과·보고서는 모두 보존하며 신규 결과는 Git 제외 `outputs/mn_stage3a/`에만 둔다.

## A. 이관 코드·지원 파일·테스트

| 파일 | 역할 |
|---|---|
| `pipelines/mn/convert_mn35k_to_powsybl.py` |원본 변환기, 명시적 `convert_for_verification(paths, destination, config_path=None)` |
| `pipelines/mn/conversion_inputs.py` |128개 source+2개 EMS 참조 파일 hash, 설정·버전·물리 경로 검증 |
| `pipelines/mn/conversion_contract.json` |위 입력 집합·원본 커밋·출처·기존 변환 버전 고정 |
| `pipelines/mn/conversion_checks.py` |ID/연결/정격·재로드·파일 hash·실패 gate, 기존 EMS probe 재사용 |
| `tests/mn_build/test_stage3a_samples.py` |원본 소형2~3모선 양 엔진 테스트13개 이관 |
| `tests/mn_build/test_stage3a_comparison.py` |원본 전체 실행·XML/보고서·격리된 EMS 호환성·재실행·입력 역할4개 |
| `tests/mn_build/test_stage3a_protection.py` |입력·ID·연결·비수렴·중간 오류·링크·입력 변경·공개 충돌·바이트 훼손32개 |
| `pipelines/mn/README.md`, 이 문서 |검증 함수·명령·결과·제한·Stage3B 요구사항 |

기존 EMS 소스·설정·API·Agent·UI·KPG·한국 OSM·Stage1~2D Python/테스트·`requirements.txt`는 수정하지 않았다. 새 운영 CLI·활성화 경로는 추가하지 않았다. 구축/변환 라이브러리는 기존 외부 변환 Python을 사용하고 EMS `.venv`에 추가하지 않았다.

## B. 원본 대비 차이

`mn35k_powsybl.py`는 기존 Stage2A 파일을 그대로 재사용하며 원본과 SHA256 `95e602dd7f910c48c54fed92cd66b8fc8dfbdd9ec13ec87a431d3194dca7feb3`로 바이트 동일하다. SI/pu·부호·변압기 기준용량·tap·발전/부하·분로·ID 변환을 바꾸지 않았다.

변환기 함수15개는 AST가 정확히 같다. `verify_scenario()`와 `pypsa_rerun()`은 원본 위치 대신 필수 `paths`를 받는다. 원본 `protected_manifest()`와 `run()`/`main()`의 IO 역할은 고정 계약·비공개 `_generate()`·공개 전 검증·원자적 디렉터리 공개로 분리했다. [원본/이관 코드 hash·AST](../../outputs/mn_stage3a/code-ownership.json)에 함수별 증거를 남겼다.

- `ROOT` 추정, `Path.home()`, `AI_EMS_PREVIEW` 홈 결합과 `sys.path.insert`를 새 변환기에서 제거했다. package 상대 import와 기존 `logical_input()`을 사용한다. 계약 파일의 `Path(__file__).with_name(...)`는 패키지 리소스 접근이며 원본 루트 추정이 아니다.
- 설정은 `configs/mn35k_powsybl_conversion.json`의 pinned bytes만 허용한다. 기존 보호 범위인 보고서·설정·MN NetCDF에 현재 XIIDM/검증 보고서·EMS preview/map·원본 코드 증거를 포함한다. 누락·추가·hash 차이·다른 설정을 자동 승인하지 않는다.
- 원본의 동일 경로 쓰기 대신 지정 출력 영역의 새 디렉터리에서만 모델/보고서를 생성한다. 원본 설정의 출력 문자열은 출처 식별자로 남기고 실제 결과 경로는 반환값과 bundle-relative 기록으로 구분한다.
- `input_hashes.json`의 `input_model` 계약은 유지한다. 실행한 새 코드의 `scripts`와 원본 증거 `source_scripts`를 구분하고, 별도 provenance에 실행 코드13개·환경·결과 hash를 기록한다.
- 검증 false를 반환만 하던 원본 흐름에 공개 전 실패 gate를 추가했다. 중복/누락 mapping은 IIDM 생성 전에 거부하고, 연결·전압·양단 정격과 재로드를 확인한다. PF·N-1·EMS 호환성에 실패하면 최종 디렉터리를 만들지 않는다.

원본 전체 실행 비교에서는 원본 Python을 복사 입력 프로젝트에서 실행했다. 테스트 로딩용 top-level `sys.path.insert` 한 문장만 AST에서 제거하고 원본 helper를 이름으로 주입했다. 함수의 계산은 변경하지 않았다. `ROOT`와 preview 참조만 명시적으로 주입했으며 실제 원본/Earth는 쓰지 않았다.

## C. 과거 기준과 Stage2C 재생성 NetCDF

이번 변환은 원본 설정이 지정한 과거45대 모델만 사용한다.

```text
pypsa-earth/networks/MN_35k_poc/scenarios/synthetic_reactive_support_base.nc
SHA256 21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4
```

현재 기준 `reduced_27_q_limited_with_reactor`,166모선/156선로/27변압기·45발전기·166부하·리액터1대20MVAr·Slack25를 확인했다. 계산 후 모선 PV43/PQ122/Slack1, 발전기 PV43/PQ1/Slack1이며 PQ 전환 대상은 `SYNTHETIC_PV_10(q_set=-100)`이다.

실제 로컬 산출물을 읽으니 Stage2C 재생성 파일도 하나의 hash로 묶을 수 없었다.

| 역할 | 실제 SHA256 | 이번 입력 |
|---|---|---|
| 원본 프로젝트의 과거 보관본 |`21bd0d…b88c4` |예 |
| `outputs/mn_stage2c/generated/reactive-01/` |`69a368920b6e9deda25d103238d8557dbbdd725c30ae3e10cbd23b740b84744a` |아니오 |
| `outputs/mn_stage2c/generated/reactive-02/` |`21bd0d…b88c4` |아니오 |

`reactive-02`가 다른 hash일 것이라고 가정한 최초 테스트가 실패해 경로별 실제 파일·해당 provenance·이전 Stage2D 시작 스냅샷을 대조했다. `reactive-02`는 이미 작업 전부터 과거본과 바이트가 같았고, `reactive-01`이69a 파일이다. 기존 결과를 바꾸거나 hash를 갱신하지 않았다. [입력 역할별 경로·전체 SHA](../../outputs/mn_stage3a/input-model-roles.json)에 명시했다.

Stage2C의 차원 선언 순서에 따른 직렬화 차이와 생성 계보를 유지한다. 바이트가 같은 재생성 파일도 원본 경로의 과거 입력으로 자동 치환하지 않는다. 다른 경로/69a hash를 넣은 설정은 pinned config 검사에서 거부했다. 이번에는 재생성 모델을 추가 변환하지 않았다. 새 입력 hash의 변환이 필요하면 별도 검토된 입력 계약·preview·비교 결과를 준비해야 하며 기존 EMS 출처 계약을 조용히 바꾸면 안 된다. 초기108대와 비수렴 data-informed를 자동 선택하는 경로는 없다.

## D. XIIDM·대응표·전기정수

최종 변환 결과는 [historical-02](../../outputs/mn_stage3a/generated/historical-02)에16개 파일로 공개했다. 탐색 실행 historical-01과 원본 비교 결과는 보존했다.

```text
mn35k_synthetic_powsybl.xiidm
 element_mapping.csv                 conversion_parameters.csv
 pf_bus_comparison.csv               pf_line_comparison.csv
 pf_transformer_comparison.csv       pf_generator_comparison.csv
 pf_shunt_comparison.csv             n1_feasibility_results.csv
 n1_ui_compatible_results.json       pf_summary.json
 conversion_validation.json          input_hashes.json
 runtime_crosscheck.json             verification_report.md
 conversion_provenance.json
```

`ModelFiles`가 요구하는 네 보고서 파일명·필드와 `input_model` 출처 계약을 유지했다. mapping은561행이며 Bus166/Line156/Transformer27/Generator45/Load166/Shunt1이다. IIDM은166개 voltage level·139개 substation을 갖는다. 원본 ID는 name에 보존하고 B_/VL_/S_/L_/T_/G_/D_/SH_ 대응을 유지한다.

원본 ID별 누락·중복과 IIDM ID 중복, mapping 재구성의 정확 일치, 소스 설비 집합과 IIDM 집합, 모든 설비 연결·전압을 검사했다. 각 branch 양단 APPARENT_POWER 영구 정격=s_nom, 부하·발전기·분로 접속도 검사한다. 저장·재로드 후에도 반복한다.

변환 규칙은 원본 그대로다.

- 선로 r/x는Ω, b/g는S, 양단 b/2·g/2이며 길이·회선 효과는 PyPSA 유효 정수에 포함된다.
- 변압기는 원래 bus0/bus1 방향과 자체 s_nom 기준에서 side2 Ω로 변환한다. tap=1·phase=0·자화분로0만 지원하고 다른 값은 근사하지 않고 거부한다.
- 발전기 p/q는 주입, IIDM terminal 결과는 부호 반전한다. Slack 물리 P는 terminal 주입+active mismatch이며, 부하는 양의 소비 P/Q다.
- 리액터 b=-20/220²S, section1/1이며 흡수 Q를 양수20MVAr로 비교한다.

`conversion_parameters.csv`의 실제 최대 정수 차이는 **0**이다. 재로드 결과 차이의 최대값은 모선5.2314e-13, 선로2.8920e-12, 변압기5.4712e-13, 발전기2.3093e-12, 분로0이며 원본1e-9 gate를 통과했다. 계통의 실제 설비/운전 자료라는 판정은 하지 않는다.

### 직렬화 hash

| 파일 | SHA256 | caseDate |
|---|---|---|
| 기존 XIIDM |`f639b6d48014ba8d1415b1aecf461bc16026d7172fbe12c7684b08a9b2b26650` |2026-10-20T00:12:49.096Z |
| 최종 새 XIIDM |`ab5339d0f3cff20d3c85183397a9fe9689423bfac7b4cb4a7dd84b763c8254e8` |2026-10-29T05:09:57.775Z |

두 파일 모두267,158bytes다. 원본 재실행·이관·기존 파일의 **caseDate 한 속성만 제거한 전체 canonical XML은 정확히 같다**. canonical SHA256은 `375e72f2535312c1ac3ef845119be8f4539e26e0a407b6fc9c5b8b3af0f37b85`이다. 다른 metadata·정수·결과를 제거하거나 숫자를 반올림한 비교가 아니다. 실제 caseDate를 고정/덮어쓰지 않았다.

파일 byte SHA는 무결성 기록으로, canonical XML은 이 직렬화 차이를 식별하는 증거로 사용한다. 구조/정수·PF·N-1/Sensitivity 검증을 각각 별도로 수행했다. JSON의 기록 hash가 실제 파일과 다르면, XML 전기적 내용이 같아도 무결성 gate에서 거부한다.

## E. PyPSA–OpenLoadFlow PF 비교

원본과 이관 변환기의 같은 입력·환경 실행에서 CSV8개·`pf_summary.json`·`n1_ui_compatible_results.json`은 바이트가 같다. `conversion_validation.json`은 결과 path와 XIIDM hash만 제외하고 정확히 같다. `input_hashes.json`은 보호 집합·코드 소유권·결과 위치/hash 때문에 다르며 과거 파일과 동일하다고 주장하지 않는다. 원본의 정적 Markdown은 스크립트가 생성하지 않으므로 그대로 보존하고 새 `verification_report.md`를 제공한다.

양 엔진 PF는 모두 수렴한다. OLF는3회, reference bus VL_25_0, active mismatch **0.7836616587868183MW**다. 이를 Slack terminal 주입에 더해 물리적 Slack P 약1.3525505476757071MW를 얻는다. Slack Q=-93.41919681991422MVAr, 리액터 흡수 Q=20MVAr다.

| 비교 | 전체 설비 최대 절대 차이 | 기존 허용오차 |
|---|---:|---:|
| 모선 전압 pu |1.9540e-14 |1e-5 |
| 위상각 deg |1.8517e-12 |1e-4 |
| 선로 양단 P/Q MW/MVAr |1.8258e-11 |1e-3 |
| 변압기 양단 P/Q |3.5972e-12 |1e-3 |
| 발전기 P/Q |1.9768e-11 |1e-3 |
| 분로 Q |0 |1e-3 |

총 발전 P 약26.383661658786817MW, 부하25.6MW/8.4143130925789MVAr, 선로/변압기 유효손실0.768377665184325/0.015284128558699MW다. 양 엔진 system balance 차이도 기존1e-3MW/MVAr 이내이며 load 지정값은 정확히 같다. 원본 PyPSA 저장 결과의 약1e-7 수준 잔차를 임의로0으로 바꾸지 않았다.

허용오차·정밀도·PF 제어는 확대/변경하지 않았다. 소형 샘플은 원본대로 PyPSA x_tol1e-10·OLF1e-12와 비교 TOL1e-7을 사용한다. `use_reactive_limits=true`와 OLF 기본값 실행은 원본의 **별도 제어 변화 실험**으로만 보고하며 기준 설정으로 사용하지 않는다.

## F. N-1·Sensitivity와 제어 설정

기존 `preflight.run_probe()`/`probe.numerical()`을 재사용해 EMS Python으로 원본 기준과 새 임시 모델 각각에 `ModelFiles`를 명시한다. 기존 EMS 코드·설정·서비스 전역 객체를 바꾸지 않고 독립 네트워크에서 실제 해석을 수행한다. 임시 결과를 런타임에 설치하지 않는다.

변환 helper와 `mongolia_security.loadflow_parameters()`의 모든 Stage1 제어 필드는 정확히 일치한다. Slack VL_25, FIRST_SLACK, UNIFORM_VALUES, MAIN_CONNECTED, distributed_slack=false, use_reactive_limits=false, shunt/transformer voltage regulation=false, 최대30회·UNIFORM_CRITERIA/1e-8이다. 원본 PyPSA 기준의1e-6MVA와 OLF100MVA base의1e-8pu 관계도 그대로다.

새 모델과 기존 EMS 모델의 수치·ID·제어 **186개 기존 비교+제어 일치2개=188개**가 모두 통과했다.

| N-1 차단 선로 | 상태 | 격리 모선 | 최저/최고 전압 pu |
|---|---|---:|---|
|222087807-1_0 |CONVERGED |8 |0.9992014336196917 /1.0651309002190068 |
|751261406-1_0 |CONVERGED |0 |0.9828079706584585 /1.094031269556094 |
|484333456-1_0 |CONVERGED |11 |0.9827788659397438 /1.094031269556093 |

기존 대비 전압·주요 조류 변화 delta=0이고 위반0건을 유지한다. 원본 변환기의 수동 variant PF와 Security Analysis 비교 최대 차이는 각각 약1.9227e-8·2.2686e-7·2.2686e-7MW이며 원본과 동일하다. variant 정리·선로 재연결·base 재실행도 검증했다. 이것을 새 PyPSA N-1 해석으로 표시하지 않는다.

Sensitivity는 outage751261406-1_0, 감시303374638-1_0·484276912-1_0·259474322-1_0, 각44개=132개 후보를 모두 비교해 최대delta=0이다. 각1위 값은 `-1.0133417708799506`, `-1.0074238413468564`, `-0.9470790553771927`이다. 기존 방식의 유한차분 self-check와 각 감시 선로 검사도 통과하며 FD step0.1MW·abs1e-4·rel1%를 바꾸지 않았다.

[수치 차이·XML 기록](../../outputs/mn_stage3a/numerical-deltas.json), 최종 묶음의 `runtime_crosscheck.json`에 원본·신규 전체 결과와 비교 체크를 남겼다.

## G. 입력 hash·원자성·원본 보호

128개 source 입력·기존 결과와 EMS preview/map2개를 SHA256으로 고정했다. 보고서·설정·MN NetCDF 집합의 누락/추가도 실패한다. 기존 `synthetic_inputs`의70개 pin도 재사용하며 기존 base 모델 hash 보호를 풀지 않았다. 별도 위치의 source/Earth를 주입한 복사 입력 테스트도 수행했다.

각 변환은 `paths.output_dir` 내부 신규 디렉터리만 허용한다. 기존 `new_artifact_directory()`의 비공개 영역에서 전체16개 파일을 생성·검증한다. 성공할 때만 비대체 원자적 rename으로 공개하며 기존 파일/디렉터리/링크를 교체하지 않는다. 실패한 모델은 최종 경로에 없고 정상 배포 가능 모델로 표시하지 않는다. 성공 provenance도 `CONVERSION_VERIFICATION_ONLY`, `activated=false`다.

회귀 테스트는 다음을 실제로 재현한다.

- 모델·설정·mapping·발전기 기준·검증 보고서 누락, 모델/config SHA 불일치·다른 설정·추가 보고서
- 원본과 동일 출력, work/output 중첩, 기존 파일·빈 디렉터리·symlink·dangling symlink·hardlink와 입력 탈출 링크
- 변환 예외, 일부 발전기 누락, 중복 mapping, 잘못된 연결, OLF 비수렴, 검증 false
- 3번째/5번째 CSV 생성 실패, 생성물 hardlink, 생성 중 입력 변경, 최종 대상 생성 경쟁
- 신규 모델 runtime 검증 실패, 제어 불일치
- 전기적 XML 내용에 영향을 주지 않는 newline을 추가하되 기록 hash는 유지한 경우도 바이트 무결성 불일치로 실패

직렬화 재실행은 caseDate 때문에 바이트가 달라도 canonical XML·CSV와 수치는 같음을 확인하고, 기존 결과의 hash가 그대로임을 확인한다. 기존 결과 디렉터리로 같은 내용의 재실행도 거부한다.

## H. 실행 명령과 전체 회귀

EMS 루트에서 실행한다. 기존 외부 환경을 직접 사용하고 설치·버전 변경·Pixi run을 하지 않는다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export KEMS_MN_EARTH_CHECKOUT="$KEMS_MN_SOURCE_PROJECT/pypsa-earth"
export KEMS_MN_OUTPUT_DIR=outputs/mn_stage3a/generated
export PROJ_DATA="$KEMS_MN_EARTH_CHECKOUT/.pixi/envs/default/share/proj"
export MPLCONFIGDIR=/tmp/mn-stage3a-mpl
export PYTHONDONTWRITEBYTECODE=1

"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --junitxml=outputs/mn_stage3a/build-review-new.xml

# 소형 샘플: 원본 프로젝트 설정 없이도 실제 두 엔진으로 실행
env -u KEMS_MN_SOURCE_PROJECT \
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build/test_stage3a_samples.py

# 실제 원본/이관 비교. basetemp는 매번 새 이름이어야 한다.
"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build/test_stage3a_comparison.py \
  --basetemp=outputs/mn_stage3a/independent-review-new

.venv/bin/python -m pytest -q -ra --tb=short
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -m pytest -q -ra --tb=short

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage2d/baseline.json \
  --numerical-reference outputs/mn_stage2d/baseline.json \
  --test-results outputs/mn_stage3a/build-complete.xml \
  --test-results outputs/mn_stage3a/runtime.xml --test-results outputs/mn_stage3a/chrome.xml \
  --test-results outputs/mn_stage3a/samples.xml \
  --report outputs/mn_stage3a/baseline-review-new.json

git diff --check
```

새 모델 변환 함수의 실행 예시는 [README Stage3A](../../pipelines/mn/README.md)에 있다. 반드시 사용하지 않은 새 최종 이름을 지정한다.

| 실행 | 결과 | skip 의미 |
|---|---|---|
| 소형 양 엔진 샘플 |13 passed,2.88초 |없음 |
| 실제 비교·보호 선행 검사 |35 passed,47.56초 |없음, 이후 바이트 훼손1개 추가 |
| 공개 직전 gate 별도 검사 |6 passed |정상 입력 변경/충돌/링크/runtime/제어/바이트 검증 |
| Stage2A~2D+Stage3A 전체 |**373 passed** |기존324+신규49, skip 없음 |
| EMS 전체 pytest |**567 passed,35 skipped**,50.66초 |브라우저 opt-in19+구축 모듈16 |
| Chrome 포함 전체 |**586 passed,16 skipped**,154.84초 |구축 모듈16만 skip, 브라우저19개 실행 |
| Stage1 preflight/migration |PASS /14개 원본·의존 그룹6 |운영 CLI/활성화는 계속 비활성 |
| Stage1 baseline |PASS |87개 SHA256·기존 수치186개 대조 통과 |

원본 모델 부재로 skip된 해석 검사는 없고, EMS에서 빠진 구축 검사는 별도 환경에서 모두 실행했다. KPG·한국 OSM·Agent·UI와 검토 도구의 기존 전체 테스트도 포함했다. 실행별 중복 테스트 수를 합산하지 않는다. 기존 Agent 응답 모형을 실제 외부 LLM 관측으로 표시하지 않는다.

초기 비교 실패1개는 reactive-02의 hash를 실제와 다르게 가정한 테스트였으며 reactive-01을 명시적으로 선택해 수정했다. 초기 중복 mapping 주입도 실제 변환을 중단했지만 예상보다 먼저 native PyPowSyBl 예외로 실패해 테스트가 실패했다. 중복/누락 mapping을 IIDM 생성 전에 명확히 검증하도록 보강했다. 기존 입력·계산을 바꿔서 통과시키지 않았으며 초기 XML/로그와372개 선행 전체 실행을 별도로 보존했다.

기존 ecCodes/cfgrib·NumPy C-header 경고와 pandas concat FutureWarning, EMS Starlette/anyio 경고를 관측했다. 선택적 OPF extra 미설치 안내는 사용하지 않는 기능의 기존 안내다. 환경을 설치/갱신하거나 경고를 숨기지 않았다. preflight의 networkx 직접 의존성 미선언 안내도 유지한다.

[최종 baseline](../../outputs/mn_stage3a/baseline.json), [보존 검사](../../outputs/mn_stage3a/preservation.json), [최종 산출물 검토](../../outputs/mn_stage3a/final-review.json)에 실제 결과를 남긴다. 기존4,733개 파일 중 의도한 README만 변경하고 원본·EMS 런타임·기존 모델·보고서·이전 단계 산출물은 보존했다. 미추적 새 파일도 별도 `git diff --no-index --check /dev/null <파일>`로 검사한다. Commit·Push·PR·Merge는 수행하지 않았다.

## I. 남은 외부 의존성

자체14개 스크립트는 모두 패키지로 이관됐지만 원본 프로젝트를 즉시 제거할 수 있는 단계는 아니다. 원본 NetCDF·설정·과거 보고서·source hash 증거128개, EMS preview/map2개, 외부 Earth·Pixi PyPSA와 별도 PyPowSyBl 환경, EMS 검증 Python이 필요하다. 새 변환 결과를 복사하기 위해 대용량 원본·환경·캐시·PyPSA-Earth 소스를 Git에 넣지 않았다.

설정과 데이터의 물리 위치는 `Paths`로 주입하고 원본 Python은 hash 증거로만 읽는다. 원본 converter 실행은 비교 테스트에서만 한다. Stage1의 기존 subprocess probe가 EMS src/studies 모듈을 읽는 환경 경계는 재사용했으며 새 변환기에 `sys.path` 조작을 추가하지 않았다.

Stage2C 재생성 모델을 사용하려면 해당 **실제 파일 hash와 출처 역할**을 가진 별도 계약·검증 preview가 필요하다. 현재 함수는 기존 과거 모델만 받는다. 기존 EMS source_sha를 재생성 hash로 자동 교체하는 경로는 없다. EMS exporter `scripts/export_pypsa_map.py`는 KPG 지리 생성에도 쓰므로 위치·계약을 유지했다.

## J. Stage3B 배포 번들 구축 전 요구사항

1. 검증 산출물과 배포 번들을 분리하고, 파일별 byte SHA·원본 source SHA·원본/이관 코드·환경·검증 결과를 명시하는 버전 manifest를 설계한다. caseDate 제외 XML hash를 byte 무결성 hash로 대체하지 않는다.
2. XIIDM·mapping·발전기 기준·conversion validation·input hashes·preview·map의 상호 출처를 한 번에 검사한다.173모선 지도와166모선 해석을 임의로 일치시키지 않는다.
3. 기존 `ModelFiles` 계약과 제어·PF·N-1·Sensitivity 검증을 새 번들에서도 수행한다. 미검증 설비/합성/비수렴 표시와 원본 provenance를 보존한다.
4. `bundle_dir`를 work/output/원본/runtime과 분리하고 새 디렉터리에 비대체 원자적으로 공개한다. 운영 설치·경로 전환·재시작·rollback은 별도 설계/검토를 거친다. 이번 단계의 `runtime_compatible=true`는 자동 활성화 승인이 아니다.
5. 고정된 증거 파일의 배포 범위·출처/라이선스와 외부 source 없이 재검증하는 방식을 확정한다. 대용량 파일·원본 코드 증거를 제거하기 전에 필요한 계약을 대체해야 한다.
6. KPG·한국 OSM·기존 Agent/UI 보호와 기존 모델 유지/복귀 검증을 별도로 정의한다. 국가별 공통 API·배포 설치/rollback 구현은 이번에 하지 않았다.
