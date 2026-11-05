# 몽골 통합 Stage 3B — 독립 배포 후보 번들

2026-10-29. 시작 HEAD `e436ba29f857c537a066c5280c9259d01d4ec2ce`.
로컬 `feature/kr-osm-map-integration`은 `origin/feature/kems-multi-grid`를 추적한다.
시작 시 EMS/몽골 원본은 clean이었다. Earth HEAD는
`23fa6edf021f8b37c1783949891feb0a804c1959`, 기존 untracked `config.MN_35k.yaml`을 보존했다.
원본 Mongolia HEAD는 `748c0811854b959f9df09377317499056a51601e`다.
[시작 상태·파일 hash](../../outputs/mn_stage3b/before-preservation.json)에 기록했다.

이번 결과는 **생성·검증한 후보**다. EMS 활성 모델·환경 변수·보고서 경로·웹/API/Agent/UI를
변경하지 않았고 서버 재시작·설치·승격·복귀 자동화·워크스테이션 전송을 하지 않았다.
`runtime_compatible=true`, `activated=false`, `external_distribution_approved=false`다.

## A. 실제 로딩 조사와 번들 구성

| 실제 코드 | 현재 파일/계약 | 번들에서의 위치·필수성 |
|---|---|---|
| `web_app.py:63`, `grid_catalog.load_mongolia_map()` | `data/grids/mn-osm-35k-map.json` | `assets/map.json`; 지도·PF 구조 검증 필수 |
| `web_app.py:64`, `pf_preview.load_mongolia_pf_preview()` | `data/grids/mn35k-synthetic-pf-preview.json` | `assets/pf_preview.json`; PF 조회·Agent·해석 검증 필수 |
| `web_app.py:66,199`, `reference_layers.load_mongolia_reference_layers()` | `data/grids/mn-osm-35k-reference-layers.json` | `assets/reference.json`; 참조 표시 API에 필요, PF/N-1 입력은 아님 |
| `config.py:53`, `mongolia_security.ModelFiles.xiidm` | 외부 Earth `networks/MN_35k_poc/powsybl/mn35k_synthetic_powsybl.xiidm` | `models/mn35k_synthetic_powsybl.xiidm`; 해석 필수 |
| `config.py:58`, `ModelFiles`의 네 property | 원본 `reports/pypowsybl_conversion/`의 `element_mapping.csv`, `pf_generator_comparison.csv`, `conversion_validation.json`, `input_hashes.json` | `reports/`의 동일 파일명·컬럼; 해석 필수 |
| `MongoliaSecurityService`, `mongolia_sensitivity`, `agent/mongolia_tools.py`, `web_app._mongolia_security()` | 검증된 별도 ModelFiles 객체로 PF/N-1/Sensitivity 실행; Agent가 같은 서비스를 사용 | 검증에서는 번들 상대 경로를 명시한 새 서비스 객체만 사용 |

현재 지도 JSON에는 grid ID 필드가 **없다**. 파일을 편집해 추가하지 않았다. 고정 지도 byte SHA와
PF의 `grid_id`, `map_json_sha256`, reference의 `grid_id`, `base_map_sha256`을 결합해 식별한다.
참조 레이어 33선로/17변전소는 표시 전용이며 모선·조류 모델에 넣지 않는다.

기본 `mn-osm-35k-historical-20261029-review02`는 **23파일**, Stage3A 선택
`mn-osm-35k-stage3a-20261029-review02`는 **25파일**이다.

| 후보 | 파일 수 | 전체 bytes(manifest 포함) | 별도 receipt의 manifest SHA256 |
|---|---:|---:|---|
| historical review02 |23 |1,283,676 |`9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c` |
| stage3a review02 |25 |1,542,848 |`953022c62fb8a4cbb377f8bdb96e248252027616e58b3085ddf0da4eafdf0bd9` |

[historical receipt](../../outputs/mn_stage3b/bundle-historical-final.json),
[Stage3A receipt](../../outputs/mn_stage3b/bundle-stage3a-final.json),
[정적 검사 실제 결과](../../outputs/mn_stage3b/static-final.json).

```text
mn-osm-35k-<historical|stage3a>-<version>/
  manifest.json
  assets/{map.json,pf_preview.json,reference.json}
  models/mn35k_synthetic_powsybl.xiidm
  reports/
    element_mapping.csv, pf_generator_comparison.csv
    conversion_validation.json, input_hashes.json
    conversion_parameters.csv, pf_{bus,line,transformer,shunt}_comparison.csv
    pf_summary.json, n1_feasibility_results.csv, n1_ui_compatible_results.json
    historical: pypowsybl_conversion_report.md
    stage3a: conversion_provenance.json, runtime_crosscheck.json, verification_report.md
  validation/
    runtime_contract.json, bundle_validation.json, runtime_compatibility.json
    source_verification.json, license_review.json
```

위 **전체 23/25파일**을 검증 단위로 유지한다.
기존 웹 기능의 데이터 소비 자체에는 assets3+XIIDM1+필수 보고서4=8파일이 필요하다.
나머지는 원래 변환 비교 증거와 독립 검증·신뢰·출처 계약이다. 원본 NetCDF/PBF,
빌더 Python, Earth/Pixi/변환 venv를 번들에 넣지 않았다. 모든 데이터는 링크가 아닌 실제 복사 bytes다.

## B. 버전·manifest·신뢰 계약

신규 코드와 고정 계약:

- `pipelines/mn/bundle.py`: 표준 라이브러리만 사용하는 파일 집합·byte SHA·출처·XML·ID 정적 검증, EMS subprocess 검증 호출.
- `bundle_build.py`: `Paths` 주입, 생성 시 외부 기준선 확인, 짝 지어진 입력 선정, 비공개 staging·비대체 원자적 공개.
- `bundle_probe.py`: EMS Python에서 기존 로더·ModelFiles·PF/N-1/Sensitivity 실행. 원본 경로 추론/설정 전환 없음.
- `bundle_cli.py`: `create`, `validate`, `runtime`만 제공. 설치/활성화/rollback 명령 없음.
- `bundle_runtime_contract.json`: Stage1 식별자·87개 hash와 Stage3A 수치 기준을 별도 **고정 계약**으로 보존. 수치 원본 baseline SHA도 포함하며 개발 절대 경로를 제거했다.
- `bundle_license_review.json`: 데이터별 확인 조건/미확인 권한, 외부 배포 미승인 상태.
- `tests/mn_build/test_stage3b_samples.py`, `test_stage3b_actual.py`: 고정 안전성 검사와 명시적 외부 입력을 쓰는 실제 검사 분리.

manifest schema는 `mn-deployment-bundle-1`이다. version, source별 bundle ID, MN/grid/scenario,
합성·실제 운영 자료 아님·미검증 정수 상태, 지도/해석/reference 개수, EMS/source/Earth Git identity,
원본 NetCDF 논리 식별자·SHA, XIIDM byte/canonical SHA/caseDate, 변환·포장 코드 SHA,
엔진 버전·EMS 실제 환경·전체 PF 제어, 상대 runtime 경로, 필수 파일 집합과 파일별 크기/SHA,
검증·활성화·배포 승인 상태를 기록한다. 절대 경로는 별도 provenance에만 보존한다.

**manifest 자신의 hash는 manifest 안에 넣지 않는다.** 생성 명령의 결과 JSON이 외부 receipt로
`trusted_manifest_sha256`을 반환한다. 독립 검증 호출자는 검토·별도 보관한 그 hash를 반드시
전달해야 한다. 파일과 manifest를 함께 변조하고 file hash를 고쳐도 기존 신뢰 hash와 불일치한다.
receipt는 전자서명이 아니다. 무신뢰 경로에서 번들과 같이 받은 receipt만으로 인증했다고
주장하지 않는다. Stage3C의 신뢰 채널·승인 기록/서명 정책은 별도로 필요하다.

## C. 기존 모델·자산과의 동등성

historical은 기존 과거 XIIDM과 **해당 원본 보고서 전체13개**만 받는다. XIIDM/report 별도 override를
허용하지 않는다. Stage3A는 완성된 변환 디렉터리를 명시해야 하고 provenance·15개 산출물 hash·
입력130개·canonical XML을 확인한다. 별도 source가 ID에 포함되므로 같은 version이어도 구분된다.
Stage2C 재생성 NetCDF나 초기108발전기/비수렴 data_informed를 자동 선택하지 않는다.

| 항목 | 기준·실제 결과 |
|---|---|
| 지도 | 173모선 /159선로 /27변압기 /공개 발전소 후보27레코드 |
| 해석 | 166모선 /156선로 /27변압기 /발전기45 /부하166 /분로1 |
| 시나리오 | `reduced_27_q_limited_with_reactor`; Slack25, 20MVAr 흡수 리액터 |
| PF 분류 | PQ122/PV43/Slack1; bus10의 수동 PV→PQ/Q=-100MVAr 기록 보존 |
| 원본45 NetCDF SHA | `21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4` |
| historical XIIDM byte SHA | `f639b6d48014ba8d1415b1aecf461bc16026d7172fbe12c7684b08a9b2b26650` |
| Stage3A XIIDM byte SHA | `ab5339d0f3cff20d3c85183397a9fe9689423bfac7b4cb4a7dd84b763c8254e8` |
| 두 XIIDM 공통 canonical SHA | `375e72f2535312c1ac3ef845119be8f4539e26e0a407b6fc9c5b8b3af0f37b85` |

선택한 모든 원본 파일은 bytes 그대로 복사했다. 절대 provenance 경로가 있는 기존 보고서를
편집하지 않는다. 두 XIIDM은 root `caseDate`만 제거한 전체 XML이 정확히 같고, 다른 metadata나
숫자를 제외/반올림하지 않는다. 따라서 R/X/B, 전압, 연결·탭·정격, P/Q·분로 부호·Slack·정수도
같은 XML에 묶인다. canonical 검사는 각 파일의 byte SHA 검사를 대체하지 않는다.

## D. 정적 검증

표준 라이브러리로 다음을 실제 검사한다: 외부 신뢰 manifest hash, schema/version/ID/시나리오,
정확한 파일·디렉터리 집합, 안전한 POSIX 상대 경로, 실제 파일 크기/SHA, 고정 runtime 계약,
지도/PF/reference/source NetCDF 연결, XIIDM-report byte pairing, canonical XML 전체 정수,
561개 대응표의 양방향 유일성·실제 XML ID·선로/변압기 양단·PF 설비 부분집합·발전기 기준 ID.
CSV8개·`pf_summary.json`·N-1 JSON도 Stage1 고정 hash와 대조한다.

완료된 runtime evidence의186개 실제 값을 고정 계약에 다시 대조한다. `passed=true` 문구만
믿지 않는다. 기록된 PF 제어/환경, 미승인 라이선스/합성 caveat도 확인한다.
생성 전 비공개 후보에서는 아직 runtime evidence가 `PENDING`이므로 내부 검사 후 **실제 EMS probe**를
먼저 수행하고, 완료 evidence·최종 manifest를 새로 작성해 공개용 정적 검사를 통과시킨다.
공개 검증 함수는 PENDING 결과를 거부한다.

## E. 런타임 PF·N-1·Sensitivity

EMS `.venv` Python3.11.16, PyPowSyBl1.16.1, pandas3.0.5, numpy2.4.6,
scipy1.17.1, networkx3.6.1에서 실행한다. PyPSA는 설치되어 있지 않다.
기존 runtime 서비스·수식·허용오차를 바꾸지 않고 신규 네트워크 객체를 사용한다.
base loader가 모든 전압/위상각, 선로·변압기 양단P/Q, 발전기P/Q·분로Q를 기존 기준과 비교한다.
추가로 Stage1의186개 수치·제어·후보 비교를 모두 실행한다.

Slack `VL_25`, FIRST_SLACK, distributed_slack=false, MAIN_CONNECTED, UNIFORM_VALUES,
use_reactive_limits=false, transformer/shunt regulation=false, UNIFORM_CRITERIA/1e-8,
최대30회를 유지한다. FD step0.1MW, abs1e-4·rel1%, 후보 비교1e-8을 유지한다.
전압1e-5pu/각도1e-4deg/설비P/Q1e-3의 기존 기본 PF 검증 허용오차를 넓히지 않았다.

base Slack P=1.3525505476769006MW, Q=-93.41919681991475MVAr, 리액터 흡수20MVAr다.

| N-1 차단 선로 | 상태 | 격리 모선 | 전압 최저/최고 pu |
|---|---|---:|---|
|222087807-1_0 |CONVERGED |8 |0.9992014336196917 /1.0651309002190068 |
|751261406-1_0 |CONVERGED |0 |0.9828079706584585 /1.094031269556094 |
|484333456-1_0 |CONVERGED |11 |0.9827788659397438 /1.094031269556093 |

위반0건이며 기존 모델 대비 수치 비교를 통과한다. Sensitivity는 outage751261406-1_0에서
303374638-1_0·484276912-1_0·259474322-1_0을 감시하고 각44개, 총132개 후보를 검사한다.
각1위 값은 -1.0133417708799506, -1.0074238413468564, -0.9470790553771927이며
기존 후보 ID·전체 수치와 같고 FD 검사도 통과한다. Stage3A 모델은 byte hash 차이를 유지한 채
같은186개 비교를 통과했다. 실제 계산 결과는 각 번들의 `validation/runtime_compatibility.json`에 있다.
최종 EMS 재검증에서는 historical과 Stage3A 모두 기준선 대비 base·N-1 각 측정값·132개
Sensitivity 값의 최대 delta=0, 제어 설정 정확히 일치를 확인했다.
[전체 delta 기록](../../outputs/mn_stage3b/numerical-deltas.json)에 숫자를 남겼다.

## F. 외부 원본 없이 복사본 검증

실제 테스트는 번들 전체를 `/tmp/mn-bundle-independent-*/relocated`로 복사한다.
Linux bwrap mount namespace에서 root를 읽기 전용으로 두고 원본 Mongolia/Earth, 변환 venv,
원본 보고서, EMS 기존 `data/`, `outputs/`를 빈 mount로 가린다. 이들 실제 파일의 read가
불가능함을 먼저 assert한 뒤, **복사본+EMS 코드+EMS Python**으로 동일한 정적/모델/수치 검증을 실행한다.
원본 디렉터리를 실제 rename/delete하지 않는다. 복사본도 read-only mount하고 전후 file SHA 집합이 같다.
native 엔진에 필요한 `/dev`, `/proc`를 제공하고 `/tmp`만 scratch로 연다.
Python open monkeypatch만 사용하는 검사가 아니므로 native XIIDM 로더도 가려진 원본에 접근할 수 없다.

전체 독립 검사에서 PyPSA 미설치, 원본 데이터 read 실패,186개 검사 통과,132개 후보를 확인했다.
실행 명령·차단 파일·실제 runtime 결과는
[independence-proof.json](../../outputs/mn_stage3b/build-review-02/stage3b-bundles0/independence-proof.json)에 보존한다.
bwrap은 **테스트 도구**이며 워크스테이션 런타임 라이브러리가 아니다.

## G. 입력·출력 보호 및 실패 검사

생성 시 기존 conversion source manifest130개와 Stage1의87개 기준 파일·Git commit을 실제 읽어 검사한다.
완료 직전 모두 다시 확인한다. 독립 검증은 외부 원본 존재·Git을 재조회하지 않고 번들 내 고정된
출처 hash·자산/모델 관계·runtime contract를 재검증한다. 이 둘을 같은 검사라고 주장하지 않는다.

출력은 `Paths.bundle_dir` 안의 신규 디렉터리만 허용한다. work/output/bundle 양방향 중첩,
source/Earth/runtime 경로 침범, 기존 파일/빈 디렉터리/심볼릭·하드링크, 상대 경로 탈출을 거부한다.
비공개 sibling 임시 디렉터리에서 전체 복사·해석·hash를 끝내고 기존 `_publish_directory()`의
Linux `renameat2(RENAME_NOREPLACE)`로 공개한다. 공개 직전 parent inode/경로를 재검사하고,
열어 고정한 parent fd로 rename해 마지막 parent 경로 교체 우회를 막는다.
미지원 환경은 대체/덮어쓰기 fallback 없이 실패한다.

회귀 테스트는 입력 누락/불일치, 잘못된 시나리오·출처·계약·제어·mapping·전기정수,
원본과 같은 출력/기존 대상/링크,3번째·5번째 보고서 생성 오류, 일부 파일만 생성된 실패,
PF 비수렴·probe timeout/실패, runtime 검증 중 파일 변경, 입력 변경, 최종 공개 충돌을 재현한다.
입력 변경은 원본을 실제 변조하지 않는 주입으로 검증한다. 대상이 이미 있으면 동일 입력의
재실행도 거부하고, 실패한 staging만 정리하며 기존/경쟁자가 만든 결과는 제거하지 않는다.
파일 집합·hash를 다시 맞춘 공격도 외부 신뢰 hash 및 고정 내부 계약 검사에서 실패한다.

## H. 출처와 재배포 검토

2026-10-29 공식 출처를 조회했다. 이는 번들 전체의 재배포 승인이 아니다.

- OSM 계통·참조 설비는 ODbL이며 표시 출처는 `© OpenStreetMap contributors`다.
  공개 배포의 attribution과 파생 데이터베이스/share-alike 조건을 검토해야 한다.
  [OSM copyright](https://www.openstreetmap.org/copyright),
  [ODbL 원문](https://opendatacommons.org/licenses/odbl/1-0/),
  [OSMF attribution 지침](https://osmfoundation.org/wiki/Attribution_Guidelines).
- 원본 `resources/MN/powerplants.csv`의27행은 GEM19/GPD7/GEO7행 출처를 참조한다.
  겹치는 출처가 있으므로 합계33은 고유 발전소 수가 아니다. 중복·다중유닛·추정 접속을 유지한다.
  현재 PPM 저장소는 소프트웨어 MIT/default output CC BY4.0을 표시하지만, 이것만으로 이 사용자
  정의 Mongolia 추출물의 권한을 확정할 수 없다. [PPM 공식 저장소](https://github.com/PyPSA/powerplantmatching).
- WRI v1.3.0은 CC BY4.0, GEM 공식 페이지는 CC BY4.0, GEO AboutUs는 CC BY3.0 US를 표시한다.
  실제 원본 release·SQLite 추출본·개별 record attribution/예외의 대응은 아직 미확인이다.
  [WRI 데이터 저장소](https://github.com/wri/global-power-plant-database),
  [GEM terms](https://globalenergymonitor.org/creative-commons-license),
  [GEO AboutUs](https://globalenergyobservatory.org/geo/docs/AboutUs.php).
- **GADM** 경계/행정구역이 지도에 포함된다. 공식 조건은 학술·비상업 사용을 허용하고 재배포나
  상업 이용에는 사전 허가를 요구한다. 허가 증거는 확인하지 못했다. 따라서 사내 상업 PoC도
  허용됐다고 가정하지 않으며 워크스테이션 전송·외부 수출·판매 전에 허가 또는 검증된 대체 경계가 필요하다.
  이번에 지도를 편집·대체하지 않았다. [GADM license](https://gadm.org/license.html).
- 합성/추정 정수·XIIDM·생성 지도/보고서도 생성 사실만으로 upstream 조건에서 벗어나지 않는다.
  회사 코드/산출물 소유권·소프트웨어별 notice와 데이터별 의무를 분리 검토해야 한다.
  Earth 소스/환경은 포함하지 않았다. 번들은 로컬 검증 목적으로만 생성했고 외부에 전송하지 않았다.

## I. 회귀 검증 결과와 재실행

최종 실행 결과는 다음과 같다. 각 실행의 중복 검사를 합산하지 않는다.

| 실행 환경/범위 | 최종 결과 | skip 의미 |
|---|---|---|
| 신규 고정 안전성 샘플 |43 passed |외부 모델/구축 import 없음 |
| 실제 Stage3B 검사 |33 passed |모두 외부 환경의 전체 실행에서 실제 수행 |
| Stage2A~3A+Stage3B 구축·변환 전체 |**449 passed,0 skipped**,243.40초 |기존373+신규76; 실패0 |
| EMS 전체 pytest 최종 |**610 passed,68 skipped**,56.89초 |브라우저 opt-in19 + 기존 PyPSA 필요 구축 모듈16 + 명시적 원본 필요 신규 검사33 |
| Chrome 포함 전체 최종 |**629 passed,49 skipped**,167.24초 |브라우저19개 실제 수행; 구축 모듈16+원본 opt-in33만 skip |
| Stage1 preflight/migration |PASS/PASS |14개 원본 스크립트·의존 그룹6 유지 |
| Stage1 baseline |PASS |87개 byte SHA 동일;186개 수치 대조 통과;566개 check true/정보 warning1 |
| 두 최종 번들 별도 정적/EMS 검사 |PASS/PASS |각561개 ID/186개 수치; activated=false |

[구축 JUnit](../../outputs/mn_stage3b/build-02.xml),
[EMS JUnit](../../outputs/mn_stage3b/runtime-final.xml),
[Chrome JUnit](../../outputs/mn_stage3b/chrome-final.xml),
[baseline](../../outputs/mn_stage3b/baseline.json)에 실행별 결과를 보존했다.
모델 부재로 해석 검사를 skip하지 않았다. KPG193·한국 OSM 지도/접속 후보 검토·기존 Agent/API/UI
회귀도 기존 전체 suite에서 실행했다. Agent 모형 검사를 실제 외부 LLM 관측이라고 표시하지 않는다.

초기43개 고정 샘플은 통과했고, 초기 통합 검사에서 namespace의 native 엔진 초기화 실패를 관측했다.
`/dev`·`/proc` 제공과 scratch 구성을 명시한 최종 독립 검사로 해결했다.
첫 구축 전체는448 passed/1 failed였다. 새 비수렴 주입 테스트가 구축 Python에서 EMS 모듈을
import하려 한 테스트 환경 오류였으며, 실제 EMS Python subprocess를 사용하도록 수정했다.
환경을 설치/병합하거나 계산 설정을 바꾸지 않고 전체449개를 재실행했다.
초기 JSON/XML/로그와 초기 후보 번들은 지우지 않았다. **최종 리뷰 대상은 review02 두 후보**다.

기존 ecCodes/cfgrib·NumPy C-header 경고, 원본/이관 data-informed의 pandas concat 경고,
EMS Starlette/anyio 경고를 관측했다. 경고를 숨기거나 외부 Pixi/venv를 갱신하지 않았다.
시작 시 기록한4,809개 파일 중 README만 의도적으로 변경했고, 나머지4,808개는 byte 그대로다.
원본 Git 상태와 기존 Earth untracked 설정도 유지했다.
[보존 검사](../../outputs/mn_stage3b/preservation.json),
[최종 코드·산출물 검사](../../outputs/mn_stage3b/final-review.json)에 결과를 기록한다.
구축 환경은 기존 외부 `.venv-powsybl` Python3.13.16/PyPSA1.2.4/PyPowSyBl1.16.1이고,
EMS 환경과 설치/병합/갱신하지 않았다. Skip을 통과로 합산하지 않는다.

```bash
export KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)"
export PROJ_DATA="$KEMS_MN_SOURCE_PROJECT/pypsa-earth/.pixi/envs/default/share/proj"
export MPLCONFIGDIR=/tmp/mn-stage3b-mpl
export PYTHONDONTWRITEBYTECODE=1

"$KEMS_MN_SOURCE_PROJECT/.venv-powsybl/bin/python" -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --basetemp=outputs/mn_stage3b/build-independent-review-new \
  --junitxml=outputs/mn_stage3b/build-independent-review-new.xml

.venv/bin/python -B -m pytest -q -ra --tb=short
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" \
AI_EMS_BROWSER_TESTS=1 AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
.venv/bin/python -B -m pytest -q -ra --tb=short

.venv/bin/python -B -m pipelines.mn preflight --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn migration --source-project "$KEMS_MN_SOURCE_PROJECT"
.venv/bin/python -B -m pipelines.mn baseline --source-project "$KEMS_MN_SOURCE_PROJECT" \
  --verify-runtime --verify-against outputs/mn_stage3a/baseline.json \
  --numerical-reference outputs/mn_stage3a/baseline.json \
  --test-results outputs/mn_stage3b/build-02.xml \
  --test-results outputs/mn_stage3b/runtime-final.xml --test-results outputs/mn_stage3b/chrome-final.xml \
  --report outputs/mn_stage3b/baseline-review-new.json

git diff --check
```

번들 생성/검증 명령은 [README](../../pipelines/mn/README.md#stage-3b-독립-배포-후보-번들-생성검증)에 있다.
`--basetemp`, version과 report는 매번 새 이름이어야 한다. 이번 산출물은 `outputs/`에만 두며 Git 제외다.
수치·전기 모델·EMS API/UI/Agent 응답 계약·KPG193·한국 OSM/검토 도구 코드는 수정하지 않았다.

이미 생성한 최종 historical 후보의 실제 리뷰 명령은 다음과 같다. `runtime`은 결과를 stdout으로만
반환하며 번들 내부나 기존 모델을 쓰지 않는다. 정적 검사는 PyPSA/PyPowSyBl/pandas/numpy/xarray/
ai_ems import를 명시적으로 차단한 별도 실행에서도 두 후보 모두 통과했다.

```bash
.venv/bin/python -B -m pipelines.mn.bundle_cli validate \
  --bundle outputs/mn_stage3b/bundles/mn-osm-35k-historical-20261029-review02 \
  --manifest-sha256 9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c
.venv/bin/python -B -m pipelines.mn.bundle_cli runtime \
  --bundle outputs/mn_stage3b/bundles/mn-osm-35k-historical-20261029-review02 \
  --manifest-sha256 9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c \
  --runtime-python "$PWD/.venv/bin/python" --ems-root "$PWD"
```

## J. 워크스테이션 의존성과 Stage3C 요구사항

| 구분 | 필요한 항목 |
|---|---|
| 정적 독립 검증 | 번들 전체23/25파일 + 외부 승인 manifest SHA + EMS의 pipelines/mn 검증 코드·고정 계약 + Python 표준 라이브러리 |
| PF/N-1/Sensitivity 검증 | 위 항목 + EMS `src/ai_ems`, `studies` 코드 + runtime Python/PyPowSyBl1.16.1/numpy/pandas/networkx/scipy/python-dotenv 및 native 엔진의 OS 호환성 |
| 전체 EMS 서버/Agent | 현재 `requirements.txt`의 FastAPI/Uvicorn/LangGraph/LangChain/Ollama 등 기존 실행 의존성, KPG 등 다른 계통의 기존 데이터/설정; 실제 LLM 연결은 별도 |
| 구축·재생성 전용 | PyPSA1.2.4/xarray/NetCDF/GIS/PPM/OSM/Snakemake/Pixi, 외부 Earth, 원본 NetCDF/설정/보고서, 별도 변환 환경; 워크스테이션 실행에는 불필요 |
| 테스트 전용 | pytest, Chrome, bwrap; standalone runtime에 포함하지 않음 |

현재 networkx는 EMS 환경에 설치되어 있으나 `requirements.txt`에 직접 선언되지 않은 기존 문제가 있다.
이번 단계에서 런타임 의존성 파일을 수정하지 않았다. Stage3C의 재현 가능한 workstation 설치 계약에서
명시 의존성 및 엔진/OS/native 지원을 확인해야 한다. PyPowSyBl wheel을 쓰는 현재 실행에서
독립 Java/Pixi/PyPSA 환경을 가져오지 않았다.

Stage3C에서 검토할 전환 대상은 `AI_EMS_MN_XIIDM_FILE`→번들 `models/...xiidm`,
`AI_EMS_MN_POWSYBL_REPORTS_DIR`→번들 `reports/`다. 지도/PF/reference는 현재 `web_app.py`의
`PROJECT_ROOT/data/grids` 상수이므로 **아직 번들로 자동 전환되지 않는다**. 이 세 자산도 동일한
번들 manifest에 결합된 경로로 선택하는 별도의 런타임 설정 설계가 필요하다. 이번에 상수나 서버를 바꾸지 않았다.

전환 전에 권한 검토, 별도 신뢰 hash/서명, exact file set, readonly 복사 설치, OS 검증,
전체 PF/N-1/Sensitivity gate, UI/API/Agent smoke 확인과 전환 중 요청·캐시의 일관성 정책이 필요하다.
자동 활성화 기능은 구현하지 않았다.

복귀 준비에는 기존 XIIDM byte SHA `f639…6650`, 원본 보고서13개/지도/PF/reference SHA,
기존 두 환경 변수의 원래 값 또는 unset 상태, 기존 세 자산의 위치, EMS commit·Python/engine 버전,
87개 baseline/186개 수치 기준이 필요하다. 원본 파일은 보존되어 있다. 전환 후 실패 시 이전
전체 세트를 선택하고 서비스/캐시 재검증하는 실제 절차는 Stage3C에서 승인·구현한다.

독립 리뷰 대상은 신규 `bundle*.py`4개·`bundle*.json`2개·Stage3B 테스트2개,
`pipelines/mn/README.md`, 이 문서다. Commit·Push·PR·Merge를 수행하지 않는다.
