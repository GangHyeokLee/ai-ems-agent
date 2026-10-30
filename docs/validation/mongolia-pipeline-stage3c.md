# 몽골 파이프라인 Stage 3C — 번들 기반 EMS 독립 실행 검증

검증일: 2026-10-29 (Asia/Seoul). 기준 커밋은 `3517c2ba0d7bd12fba7ceab23ad417ecee2c6bc5`이며 로컬 `feature/kr-osm-map-integration`이 `origin/feature/kems-multi-grid`를 추적한다. 작업 시작 Git 상태는 clean이었다. 현재 개발 PC 안에서만 검증했으며 기존 `.env`, 활성 파일, 기존 서버를 전환하지 않았다. Commit·Push·PR·Merge를 수행하지 않았다.

승인된 번들만 사용하는 몽골 Web/Agent 실행 경로를 구현했다. 원본 프로젝트와 개발 EMS 경로가 보이지 않는 별도 mount namespace에서 실제 Web·Physics 서버, HTTP API, Chrome, PF/N-1/Sensitivity와 Agent 도구를 검증한다. Historical → Stage 3A → Historical은 매번 새 프로세스로 실행한다.

**남은 제한:** 승인된 실제 LLM 요청은 공급자502/EMS Agent503으로 실패했다. 실제 외부 모델 응답 성공은 입증하지 못했다. Stage1의87개 원래 SHA 계약에는 이번 수정 대상 코드3개도 포함되어 있어 현재 트리의 엄격 검사는 그3개를 NOT_READY로 판정한다. 원래 계약을 고치거나 실패를 PASS로 바꾸지 않았다. 원본 기준선 검증과 새 런타임 검증을 아래에서 구분한다. 라이선스·사내 이전 승인도 남아 있으므로 워크스테이션 설치/반출 가능 판정은 하지 않는다.

## A. 변경 코드·설정·테스트

| 파일 | 변경 내용 |
|---|---|
| `src/ai_ems/config.py` | 프로세스 시작 시 주입하는 선택자2개 추가. 기존 개별 XIIDM/보고서 설정과 기본값 유지 |
| `src/ai_ems/mongolia_runtime.py` (신규) | 신뢰 manifest·정적 계약·실제186개 수치 검증, 단일 버전 서비스 보유, 변조 시 캐시 폐기, 진단 |
| `src/ai_ems/grid_catalog.py` | 검증 실패한 몽골 지도 경로를 `None`으로 받아 unavailable 표시. KPG/한국 처리 유지 |
| `web_app.py` | 번들 기반 지도/PF/참조/해석/Agent 도구 연결, startup 검증, 요청 전후 무결성 보호, 진단 API |
| `tests/test_mongolia_bundle_runtime.py` (신규) | 고정 선택, 실패·링크·혼합·캐시·HTTP 격리26건 |
| `tests/test_mongolia_bundle_servers.py` (신규) | 실제 독립 서버6건; 세 번의 버전 실행과 정상 종료·복귀, 불량 번들에서 KPG/한국 보호 |
| `tests/mongolia_bundle_server_probe.py` (신규) | 읽기 전용 복사본·bwrap·빈 포트·실제 HTTP·로컬 모의 Ollama 공급자·증거 수집 |
| `tests/stage3c_server_entry.py` (신규) | 원본9경로 읽기 차단과 PyPSA/Pixi 부재를 확인한 뒤 실제 Uvicorn 실행 |
| `tests/browser/mongolia_bundle_probe.js` (신규) | 실제 기존 UI에서 계통 선택·지도·PF·Agent→N-1/Sensitivity 렌더링12개 assertion |
| `tests/test_mn_pipeline.py` | 원래87개 계약의 엄격 실패를 그대로 확인하고, 이번 승인 범위의 코드3개 before/after SHA를 별도 고정 |
| `tests/mn_build/test_stage3b_actual.py` | 주입한 기준선 EMS와 다른 위치에 생성된 원본 번들도 실제 위치를 추가로 가리도록 독립성 시험 강화 |
| `docs/validation/mongolia-pipeline-stage3c-code-hashes.json` (신규) | 코드3개 변경의 검토용 SHA 기록. 기존 전기적 모델/번들의 대체 승인 계약이 아님 |
| `pipelines/mn/README.md`, 이 문서 | 시작·진단·종료·복귀·검증·Stage4 준비와 미완료 사항 |

기존 `mongolia_security.py`, `mongolia_sensitivity.py`, `pf_preview.py`, Agent graph/tools, Physics 서버, UI와 KPG/한국 코드·데이터를 수정하지 않았다. 기존 `bundle.py`, `bundle_probe.py`, `bundle_cli.py`, `bundle_runtime_contract.json`, Stage1 `baseline_contract.json`도 그대로 사용한다. PyPSA/구축 라이브러리 설치나 venv 변경은 없다.

## B. 번들 기반 경로 선택

`AI_EMS_MN_BUNDLE_DIR`와 **외부 승인 값**인 `AI_EMS_MN_BUNDLE_MANIFEST_SHA256`를 함께 전달한다. 원시 문자열의 `None`과 빈 값을 구별한다. 하나만 있거나 빈 값이면 명시적 선택 오류이며 과거 원본 프로젝트 경로로 돌아가지 않는다. 둘 다 없을 때만 기존 개별 환경변수 방식이 유지된다. `KEMS_MN_BUNDLE_DIR`은 구축용 출력 위치이며 이 런타임 선택자와 다르다.

manifest의 `runtime_paths` 역할로 아래를 선택한다. Stage3B 검증기가 그 역할·상대 경로·정확한 파일 집합·각 SHA를 고정 계약과 확인한다. 임의 HTTP 경로 선택 API는 없다.

| 역할 | 현재 승인된 번들의 상대 경로 |
|---|---|
| map | `assets/map.json` |
| pf_preview | `assets/pf_preview.json` |
| reference | `assets/reference.json` |
| xiidm | `models/mn35k_synthetic_powsybl.xiidm` |
| reports | `reports/` |

`ModelFiles`의 필수 `element_mapping.csv`, `pf_generator_comparison.csv`, `conversion_validation.json`, `input_hashes.json`도 같은 `reports` 역할 아래에서 선택한다. 고정 계약·출처 검증 자료는 같은 번들의 `validation/`에서 검사한다. 기본 지도/Preview/원본 보고서 경로를 섞지 않는다.

startup은 외부 신뢰 SHA → 정적 검사 → 실제 실행 라이브러리 → `bundle_probe.observe()`의 PF/N-1/Sensitivity → 고정186개 비교 → 기존 `MongoliaSecurityService.status()` → 파일 무결성 재검사의 순서다. 준비된 프로세스만 `VERIFIED`다. 재생성·설치·활성화를 수행하지 않는다.

`/api/grids/mn-osm-35k/runtime-status`는 번들 ID·manifest SHA·선택 경로·검증 상태·실행 패키지 버전·PF 제어·수치 검사 수를 제공한다. 전체 환경변수와 인증값은 반환하지 않는다. `/api/health`는 일반 생존 확인이므로 몽골 검증 성공 판단에 단독 사용하지 않는다.

각 몽골 요청 전후에 무결성을 검사한다. 실패는 `UNAVAILABLE`로 고정하고 해당 프로세스의 서비스 캐시를 폐기한다. 파일을 되돌려도 자동 재활성화하지 않는다. 선택 환경변수의 실행 중 변경도 기존 선택을 바꾸지 않는다. 복귀는 새 프로세스로 수행한다.

## C. Historical 번들 실제 실행

승인된 경로: `outputs/mn_stage3b/bundles/mn-osm-35k-historical-20261029-review02`.

- 승인 manifest: `9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c`
- 23파일, 1,283,676바이트;561행 ID 대응표. 정적/별도 런타임 검증 모두 통과.
- XIIDM: `f639b6d48014ba8d1415b1aecf461bc16026d7172fbe12c7684b08a9b2b26650`.
- 세 계통의 실제 HTTP 지도 조회, 몽골 PF/참조 조회, N-1 세 건, Sensitivity132개, 기존 Agent graph의 몽골 도구 호출을 수행했다.
- Chrome에서 지도 렌더링과173/159/27, 해석166/156/27, 공개 발전소27레코드, 합성/미확정 접속 표시, N-1/Sensitivity 카드 및 후보5개를 확인했다.

정적 및 runtime CLI 증거: [historical-static.json](../../outputs/mn_stage3c/historical-static.json), [historical-runtime.json](../../outputs/mn_stage3c/historical-runtime.json).

## D. Stage 3A 번들 실제 실행

승인된 경로: `outputs/mn_stage3b/bundles/mn-osm-35k-stage3a-20261029-review02`.

- 승인 manifest: `953022c62fb8a4cbb377f8bdb96e248252027616e58b3085ddf0da4eafdf0bd9`
- 25파일, 1,542,848바이트;561행 대응표. 정적/별도 런타임 검증 모두 통과.
- XIIDM: `ab5339d0f3cff20d3c85183397a9fe9689423bfac7b4cb4a7dd84b763c8254e8`.
- Historical과 별도 서버·세션·서비스 객체를 사용해 같은 HTTP/Chrome/Agent/수치 검증을 통과했다.

XIIDM의 byte SHA는 Historical과 다르며 동일 파일이라고 주장하지 않는다. Stage3B에 기록된 `caseDate`를 제외한 구조 계약과 실제186개 수치를 모두 다시 검증했다. 두 번들은 과거 기준45대 NetCDF `21bd0d90426970b2f7f22114cb63ac3e6b194e9c3d5af365e6299185735b88c4` 계보를 유지한다. Stage2C의 다른 직렬화 NetCDF를 입력으로 교체하지 않았다.

증거: [stage3a-static.json](../../outputs/mn_stage3c/stage3a-static.json), [stage3a-runtime.json](../../outputs/mn_stage3c/stage3a-runtime.json).

## E. 지도·PF·N-1·Sensitivity·Agent

지도173모선/159선로/27변압기와 해석166모선/156선로/27변압기는 기존의 서로 다른 역할이다. 발전기45, 부하166, 리액터1·20MVAr, Slack25/VL_25를 유지했다. 공개 발전소27개 레코드의 중복/다중유닛/미확정 접속 의미를 보존하며 실제 운영 데이터로 표시하지 않는다. data-informed 비수렴 실험을 선택하거나 승격하지 않는다.

기본 PF는 `CONVERGED`, 반복3회다. 실제 OpenLoadFlow와 기존 PyPSA 참조 비교의 최대 절대 오차는 다음과 같다. 기존 허용오차를 그대로 사용했다.

| 항목 | 최대 절대 오차 | 기존 허용오차 |
|---|---:|---:|
| 전압 pu | 4.945599485495222e-12 | 1e-5 |
| 위상각 deg | 5.628608690244619e-12 | 1e-4 |
| 선로 MW/MVAr | 4.9972470606007846e-11 | 1e-3 |
| 변압기 MW/MVAr | 3.964828465541359e-11 | 1e-3 |
| 발전기 MW/MVAr | 5.303846251081268e-11 | 1e-3 |
| 분로 MVAr | 0 | 1e-3 |

Slack P/Q는 `1.3525505476769006 MW / -93.41919681991475 MVAr`, 리액터 흡수20MVAr다. 엔진의 별도 Slack active-power mismatch 진단 `0.7836616587880118 MW`도 기존 값 그대로 기록한다. 이를 Slack 출력이나 수지 오차와 임의로 동일시하지 않는다.

| N-1 선로 | 사고 후 주성분 | 격리 모선 | 최소/최대 전압 pu | 기존 screening 위반 설비 |
|---|---|---:|---|---:|
| `222087807-1_0` | CONVERGED | 8 | 0.9992014336196917 / 1.0651309002190068 | 0 |
| `751261406-1_0` | CONVERGED | 0 | 0.9828079706584585 / 1.094031269556094 | 0 |
| `484333456-1_0` | CONVERGED | 11 | 0.9827788659397438 / 1.094031269556093 | 0 |

고립 부분계통은 기존 정책대로 별도 PF를 계산하지 않는다. 주성분 수렴/격리와 위반 판정 가능 여부를 분리해 반환한다.

차단선로 `751261406-1_0`의 Sensitivity는 각44개·총132개다. 세 감시선로에서1위는 모두 `SYNTHETIC_PV_7`이다.

| 감시 선로 | 1위 MW/MW |
|---|---:|
| `303374638-1_0` | -1.0133417708799506 |
| `484276912-1_0` | -1.0074238413468564 |
| `259474322-1_0` | -0.9470790553771927 |

132개 개별 ID/값, 순위 및 유한차분 결과를 기존186개 비교에 포함했다. 기존 FD는 ±0.1MW 중심차분이며 `|AC-FD| <= 0.0001 + 0.01*|FD|`다. 각 감시선로 상위2개 FD 검증의 최대 차이는 약5.63e-7이며 모두 통과한다.132개 전체를 FD 계산했다는 뜻은 아니다.

PF 제어는 `VL_25`, `FIRST_SLACK`, `UNIFORM_VALUES`, `MAIN_CONNECTED`, `distributed_slack=false`, `use_reactive_limits=false`, 변압기/분로 전압 제어false, `UNIFORM_CRITERIA`, `1e-8`, 최대30회다. 원래 합성 모델의 Q/PV/PQ 처리와 기존 EMS 엔진 제어의 의미를 바꾸지 않는다.

Agent 검증은 다음을 구분한다.

- **실제 graph/도구/API/UI + 모의 LLM:** 로컬 Ollama HTTP 형식 공급자가 모델 응답만 제어한다. 기존 ChatOllama/LangGraph와 실제 몽골 도구가 `get_mongolia_pf_summary`, `run_mongolia_line_contingency`, `run_mongolia_line_sensitivity`를 실행한다. 몽골 도구만 bind됐는지, 응답 grid ID·JSON·ui_updates·합성 설명 및 실제 카드 렌더링을 확인한다. KPG 모델로 대체하지 않는다.
- **실제 외부 LLM:** 목적지와 전송 문맥에 대한 자동 승인 검토가 처음 요청을 거절하여 전송하지 않았다. [요청 미리보기](../../outputs/mn_stage3c/real-llm-request-preview.json)를 제시한 뒤 사용자의 명시적 승인을 받았다. 승인한 PF 요약 요청만 기존 인증을 자식 프로세스에 주입해 전송했다. 목적지는 `https://desktop-q0sdm6g.tail3d6cbe.ts.net:8443/api/chat`, 모델은 `qwen3.8:27b-q4_K_M`다. 실제 결과는 공급자 `status code:502`가 Agent HTTP503으로 반환된 실패다. 성공한 답변/도구 호출을 확인하지 못했으며 다른 목적지나 N-1/Sensitivity 질문을 추가 전송하지 않았다. `.env`·XIIDM·NetCDF를 전송하지 않았다. [최종 실제 호출 기록](../../outputs/mn_stage3c/real-llm-approved-05.json)에 인증값을 제거한 오류만 남겼다. 외부 접속/프록시/모델 서비스를 별도 점검해야 한다.

## F. 원본 프로젝트 없는 실행의 증거

시험 도구가 개발 저장소 밖의 새 `standalone-ems`에 현재 코드·KPG/한국 필수 데이터·번들 복사본만 배치한다. `.env`는 복사하지 않는다. EMS venv만 읽기 전용 mount로 연결한다. 기존 환경을 설치/갱신하지 않는다.

`bwrap`은 `/`를 읽기 전용으로 두고, 원본 `mongolia-grid-poc` 전체와 원래 `ai-ems-agent` 전체를 빈 mount로 가린다. 실행 작업 디렉터리는 복사본이다. 원본 Earth·reports·NetCDF·변환 venv 및 개발 PC의 원래 EMS 몽골 데이터에 실제 접근할 수 없다. 기존 원본 디렉터리를 rename/delete하지 않는다.

각 Web·Physics 자식은 서버 시작 전에 다음9개 실제 파일을 읽어 모두 `FileNotFoundError`인 것을 기록한다.

1. 원본 변환 Python 스크립트
2. Earth의 `MN_35k_poc/base.nc`
3. 원본 `.venv-powsybl/bin/python`
4. 원본 변환 `element_mapping.csv`
5. 원래 EMS `.env`
6. 원래 EMS `web_app.py`
7. 원래 EMS 몽골 지도
8. 원래 EMS 몽골 PF Preview
9. 원래 Historical 번들 manifest

동시에 `find_spec('pypsa') is None`, `which('pixi') is None`을 확인한다. 과거 개별 모델 환경변수는 존재하지 않는 `/blocked-legacy-mongolia/...`로 주입했는데도 번들 모델로 동작한다. 환경변수만 비웠거나 monkeypatch로 파일 조회를 흉내 낸 검증이 아니다. namespace에서 원본 전체가 가려져 있어 원본의 다른 파일명으로 읽는 우회도 차단된다. mount 차단·읽기 실패·실제 API 수치가 같은 자식 실행의 증거다. 시스템 전체 open syscall 추적을 수행했다는 주장은 하지 않는다.

증거는 [runtime-evidence-verified/sequence.json](../../outputs/mn_stage3c/runtime-evidence-verified/sequence.json), [chrome-evidence-verified/sequence.json](../../outputs/mn_stage3c/chrome-evidence-verified/sequence.json) 및 같은 폴더의 Web/Physics 로그에 보존한다. 진단의 실제 경로는 전부 해당 `standalone-ems/bundles/{historical|stage3a}/` 내부다.

이 검증은 같은 PC의 기존 EMS 실행 라이브러리를 읽기 전용으로 공유한다. 다른 OS의 새 Python 환경 설치나 워크스테이션 이전을 검증한 것은 아니다.

## G. 전환·복귀와 정상 종료

각 전체 실행은 Historical → 정상 종료 → Stage3A → 정상 종료 → Historical 재시작의 순서다. 새 Web·Physics 프로세스, 빈 포트, 새 Agent 세션을 사용한다. 최초/복귀 Historical의 전체 수치 JSON과 Agent 응답이 정확히 일치하고 두 번들 파일 집합·SHA가 불변인지 확인한다.

격리 래퍼인 bwrap 종료만으로 서버 종료를 판정하지 않는다. 자신의 시작 로그에서 실제 Uvicorn 자식 PID를 얻어 Linux pidfd로 고정한 뒤 그 자식에만 SIGTERM을 보낸다. 래퍼 wait 완료, Uvicorn `Application shutdown complete.`와 해당 자식의 `Finished server process` 로그, 실제 포트 닫힘을 확인해야 정상 종료다. Uvicorn은 lifespan 종료 후 SIGTERM을 다시 전달하며 bwrap은 자식의 신호 종료를 `128+15=143`으로 반환한다. 따라서0 또는143과 두 종료 로그를 함께 확인한다. timeout 때만 자신이 생성한 전용 process group을 종료하며, 최종 검증에서는 강제 종료가 없어야 한다. 다른 프로세스나 기존8000/8001 서버에 종료 신호를 보내지 않는다.

각 실행의 번들 ID, manifest SHA, 모델/보고서 경로, 실제 PID·포트, 정적/수치 검증 결과와 종료 결과는 위 sequence JSON에 기록한다. 실행 중 전환 기능이나 디스크의 활성 포인터는 만들지 않았다. 복귀는 승인된 Historical 설정으로 새 프로세스를 실행하는 방식이다.

## H. 변조·경로·캐시·오류 처리

신규26개 런타임 테스트와 실제 불량 번들 서버, 기존 Stage3B 보호 테스트가 다음을 다룬다.

| 상황 | 확인한 동작 |
|---|---|
| 빈/부분 설정, 잘못된 승인 SHA/미승인 ID | 실제 모델 실행 전 거부; 외부 모델 fallback 없음 |
| 필수 지도/대응표 누락, 모델·보고서 변조 | 정확한 파일 집합/SHA/내용 계약 실패 |
| 심볼릭 링크 root/파일, 하드링크, `../` 탈출 | 거부; 링크를 resolve해 검사를 건너뛰지 않음 |
| 다른 번들 보고서 혼합 | 신뢰 SHA 및 출처/모델 hash 관계 실패 |
| 지도–PF 출처, PF–XIIDM 시나리오,561행 대응 ID 불일치 | 거부. Stage3B는 파일 hash를 다시 계산한 변조도 내용 계약으로 검사 |
| 모델 로딩 실패, 기본 PF 비수렴, Sensitivity self-check/라이브러리 실패 | 준비 상태가 되지 않음 |
| N-1 예외, Sensitivity 계산/FD 오류 | 기존 HTTP500/422 오류로 구분; 성공 결과로 재분류하지 않음 |
| 검증 후 파일 변조·복구, 환경변수 버전 변경 | 기존 캐시 폐기·UNAVAILABLE 고정; 새 프로세스만 재검증 가능 |
| HTTP에 임의 bundle/path/hash 제공 | 선택이 바뀌지 않음; 변경용 POST 미제공 |
| 불량 번들의 실제 Web 실행 | 몽골 unavailable/503, KPG·한국 지도/카탈로그200, 기존 파일/서버 변경 없음 |

기존 개별 파일 보호·보고서 디렉터리 원자적 공개·생성 중 예외·최종 충돌·입력 변경도449개 구축 테스트에 포함된다. 이번 런타임은 번들을 쓰지 않으며 Stage3B의 불변 파일을 직접 수정하는 실패 테스트는 하지 않는다. 모든 변조는 테스트 복사본에서만 수행한다.

## I. 전체 검사 결과와 재현 명령

| 실행 | 실제 통과 | failure/error | skip |
|---|---:|---:|---:|
| 별도 구축/변환 환경 Stage2A~3B 및 artifacts | 449 | 0 | 0 |
| EMS 전체, 실제 Stage3C 서버 포함 | 642 | 0 | 68 |
| EMS 전체 + Chrome + Stage3C 실제 UI | 661 | 0 | 49 |
| 신규 Stage3C 테스트 (위 전체 실행에 포함) | 32 | 0 | 0 |
| 양 번들 정적/별도 runtime CLI | 각각 PASS | 0 | 0 |
| Stage1 migration | 14스크립트/6의존 그룹 PASS | 0 | 0 |
| Stage1 원래 코드 복사본 preflight/baseline | 87 SHA/186 수치 PASS | 0 | 0 |
| Stage1 현재 수정 트리 preflight/baseline | 아래 설명 참조 | 코드3개 SHA 불일치 | 해당 없음 |
| 실제 외부 LLM | 성공0 | 공급자502/Agent503 | skip하지 않고 실제 실패로 기록 |

JUnit: [build-final.xml](../../outputs/mn_stage3c/build-final.xml), [runtime-verified.xml](../../outputs/mn_stage3c/runtime-verified.xml), [chrome-verified.xml](../../outputs/mn_stage3c/chrome-verified.xml). 세 실행은 중복이 있으므로 통과 수를 합산하지 않는다. 신규 Chrome 검사는 세 단계 각각12개 assertion, 기존 브라우저 회귀19개도 실행한다. KPG·한국 OSM·접속 검토 도구·기존 Agent 회귀를 포함한다.

EMS의68 skip은 구축 전용16개 모듈의 collection skip, 명시적 원본 입력을 지정하지 않은 실제 데이터33개, 브라우저 opt-in19개다. Chrome 실행의49 skip은 앞의 구축16+원본33이다. 이 skip을 실제 통과로 간주하지 않고 별도 환경에서449개를 모두 실행했다. EMS 테스트의 선택 입력 skip을 없애기 위해 EMS venv에 PyPSA를 설치하지 않았다. 현재 엔진의 선택적 OPF 경고와 원본/이관 pandas 코드의 FutureWarning은 보존했다.

실행 명령(저장소 루트, 같은 결과를 덮어쓰지 않도록 basetemp/보고서 이름을 새로 선택):

```bash
# 기준선 EMS 코드 복사본: 현재 트리의 원래 SHA 계약을 완화하지 않는다.
git clone --shared --no-hardlinks --no-checkout "$PWD" /path/to/new-baseline-ems
git -C /path/to/new-baseline-ems checkout --detach 3517c2b
# 로컬 검증 복사본에만 기존 data/와 검증 참조 출력
# outputs/mn_stage2c/generated/reactive-{01,02},
# outputs/mn_stage3a/generated/historical-02를 배치한다. 원본은 읽기 전용.

KEMS_MN_EMS_ROOT=/path/to/new-baseline-ems \
KEMS_MN_SOURCE_PROJECT="$(realpath ../mongolia-grid-poc)" \
KEMS_MN_RUNTIME_PYTHON="$PWD/.venv/bin/python" \
PROJ_DATA="$(realpath ../mongolia-grid-poc)/pypsa-earth/.pixi/envs/default/share/proj" \
MPLCONFIGDIR=/tmp/mn-stage3c-mpl PYTHONDONTWRITEBYTECODE=1 \
../mongolia-grid-poc/.venv-powsybl/bin/python -B -m pytest -q \
  tests/mn_build tests/test_mn_artifacts.py \
  --basetemp=/tmp/mn-stage3c-build-review-new

AI_EMS_STAGE3C_TESTS=1 PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -B -m pytest -q -ra --basetemp=/tmp/mn-stage3c-runtime-review-new

AI_EMS_STAGE3C_TESTS=1 AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
FONTCONFIG_FILE="$PWD/.cache/korean-fonts/fonts.conf" PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -B -m pytest -q -ra --basetemp=/tmp/mn-stage3c-chrome-review-new

.venv/bin/python -B -m pipelines.mn preflight --source-project ../mongolia-grid-poc
.venv/bin/python -B -m pipelines.mn migration --source-project ../mongolia-grid-poc
git diff --check
```

로컬 소켓/Chrome/bwrap를 허용하는 검증 환경에서 실행해야 한다. 초기 시험의 Chrome 경로 오지정·종료 증거 보완 전 실행·중단된 실행은 최종 JUnit으로 사용하지 않는다. 결과는 삭제/덮어쓰기하지 않고 `outputs/mn_stage3c/`의 별도 이름으로 남겼다. 최종 실행 목록/증거 위치는 [verification-index.json](../../outputs/mn_stage3c/verification-index.json)에 정리한다.

### Stage1 원래 계약과 이번 코드 변경의 구분

원래87개 중 `ems_code.src/ai_ems/config.py`, `ems_code.src/ai_ems/grid_catalog.py`, `ems_code.web_app.py`의 SHA만 달라졌다. 나머지84개는 원래 SHA와 같다.84개는 데이터만의 수가 아니라 나머지 코드·설정·출처 역할도 포함한다. [before/after SHA 기록](mongolia-pipeline-stage3c-code-hashes.json)과 [현재 preflight](../../outputs/mn_stage3c/preflight-current.json), [현재 baseline](../../outputs/mn_stage3c/baseline-current.json)에 실제 불일치를 기록했다. baseline의 hash 비교에서도 같은3개가 추가로 실패하므로 실패 check6개/고유 파일3개다.

원래 계약과 과거 검증 결과는 바꾸지 않았다. 기존 구축 회귀에는 Git3517c2b의 별도 EMS 코드/데이터 증거 복사본을 `KEMS_MN_EMS_ROOT`로 주입했다. **실행 중인 파이프라인/테스트 코드는 현재 트리**이고, 모델 및 과거 코드 hash 검사의 참조 EMS만 그 복사본이다. 실제 새 런타임/HTTP/Chrome는 현재 코드 복사본을 사용하며 과거 코드를 대신 테스트하지 않는다.

과거 코드 복사본의 preflight/baseline은87개 SHA·186개 수치·최종 JUnit을 대조한다. [preflight](../../outputs/mn_stage3c/preflight-historical-code.json), [baseline](../../outputs/mn_stage3c/baseline-historical-code-final.json)에 별도로 보존한다. 현재 트리의87개가 모두 unchanged라거나 현재 트리의 Stage1 preflight가 PASS라고 주장하지 않는다. 후속 구축/번들 **생성**을 현재 코드 기준으로 승인하려면 독립 리뷰 후 새로운 코드 기준선을 별도로 정해야 한다. 기존 검증 번들의 읽기 전용 실행은 원래 전기적 계약과 실제186개 대조로 검증한다.

## J. 기존 서버·모델·원본 보존

시작 시 기존4,978개 파일의 SHA와 세 저장소 Git 상태를 [before-preservation.json](../../outputs/mn_stage3c/before-preservation.json)에 기록했다. 최종 [preservation-final-02.json](../../outputs/mn_stage3c/preservation-final-02.json)에서 그중4,972개가 바이트 동일하며 나머지6개는 A의 기존 코드/테스트/README 수정 파일뿐인지 확인한다. 원본·활성 모델·보고서·두 Stage3B 번들·`.env`의 예상 외 변경/누락은 없어야 한다.

원본 Mongolia 저장소 HEAD `748c0811854b959f9df09377317499056a51601e`와 clean 상태, Earth HEAD `23fa6edf021f8b37c1783949891feb0a804c1959`와 기존 untracked `config.MN_35k.yaml` 상태를 보존한다. EMS HEAD와 원격 추적 ref도3517c2b 그대로다. 기존 main과 Draft PR#2를 변경하는 작업을 수행하지 않았다.

기존8000/8001에 대한 읽기 전용 관측은 검증 도구의 namespace에서 접근 불가였다. 이를 호스트 전체에서 서버가 없었다는 증거로 해석하지 않는다. 시험은 동적 빈 포트와 소유한 Popen/pidfd만 사용하며 기존 포트/서버의 종료·재시작이나 설정 저장을 수행하지 않았다. 새 서버는 mount namespace에서 기존 소스를 읽지 못하고 파일시스템도 읽기 전용이다.

## K. Stage4 이전 요구사항과 잔여 위험

실행·복귀 명령과 필수 파일 목록은 [README Stage3C](../../pipelines/mn/README.md#stage-3c-번들만-사용하는-ems-실행과-복귀)에 있다.

| 항목 | 현재 확인/필요한 후속 조치 |
|---|---|
| 코드/데이터 | `src/ai_ems`, `web_app.py`, `studies`, `ui`, 번들 검증용 `pipelines/mn`, 고정 계약, 승인 번들. KPG case/MAT·bus 좌표·한국 지리/OSM JSON은 기존 Web 초기화 때문에 필요 |
| Python/엔진 | 실제 runtime Python3.11.16, PyPowSyBl1.16.1, numpy2.4.6, pandas3.0.5, scipy1.17.1, networkx3.6.1, python-dotenv1.2.3. 새 OS/native 엔진 설치·호환성은 별도 검증 필요 |
| 기존 Web/Agent/KPG 의존성 | FastAPI/Uvicorn·LangGraph1.2.11·langchain-ollama1.1.0·h5py 등 기존 requirements 유지. networkx가 설치되어 있으나 직접 선언되지 않은 기존 경고를 해결해야 새 환경 재현성이 확보됨 |
| 구축 환경 | Python3.13.16/PyPSA1.2.4/PyPowSyBl1.16.1/pandas2.3.3 별도 구축 환경으로449개 실행. EMS와 병합하지 않음. 원본/Earth/Pixi/PBF/NetCDF/변환 venv는 번들 런타임 필수 아님 |
| 시작/포트 | 예시 Physics18001→Web18000, 모두127.0.0.1. 실제 MN 해석은 Web 프로세스의 서비스가 수행. 외부 접속 허용·인증·방화벽 변경은 이번 범위 아님 |
| LLM | 승인 목적지502의 운영 상태 확인과 실제 모델 응답 재검증 필요. 인증정보/도구 문맥의 승인된 전달 경로 유지. 모의 응답의 성공으로 실제 연결 성공을 대체하지 않음 |
| 장애/복귀 | 몽골 runtime-status와 로그로 원인 확인, 해당 시험 프로세스만 정상 종료, 승인된 Historical 두 선택자로 새 프로세스 재실행. 실패한 번들을 자동 승인/수정하지 않음 |
| 출처·라이선스 | Stage3B의 `validation/license_review.json`과 `pipelines/mn/bundle_license_review.json` 유지. `assets/map.json`의 GADM country/admin 경계, `assets/reference.json`의 지역 필터/명칭, 공개 발전소27레코드의 원천 release/이용조건, OSM 고지·파생 데이터 의무, 합성 모델의 사내 소유/이용 조건 확인 필요 |
| 이전 승인 | GADM 등 재배포/상업 이용 권한 및 사내 워크스테이션 이전 허가가 확보되지 않음. `external_distribution_approved=false`, `internal_commercial_permission_verified=false` 유지. 파일 반출·실제 워크스테이션 복사/설치·수출 패키지 승인 없음 |
| 새 구축 기준선 | 원래87개 계약의 코드3개 변경을 독립 리뷰한 뒤 후속 기준선을 승인할 것. 이번 검토용 before/after JSON으로 기존 모델/번들을 새로 승인하지 않음 |

번들 기반 지도·PF·N-1·Sensitivity·Agent 도구와 버전 전환/복귀의 독립 실행을 검증했다. 외부 LLM의 실제 응답, 신규 구축 코드 기준선 승인, 다른 PC 설치와 라이선스/이전 승인은 별도 미완료 항목으로 남긴다.
