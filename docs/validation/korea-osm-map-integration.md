# 한국 OSM 송전망 지도 통합 검증

## 구현 결과

작업 기준은 원격 `feature/kems-multi-grid`의 PR #13 병합 커밋 `496f80bb8acfc965fcb365d306219dfc3383bbbf`이다. 작업 전 Git 상태가 깨끗함을 확인하고 `feature/kr-osm-map-integration` 브랜치를 생성했다. 기본 선택 계통은 `kr-kpg193`이다. commit, push, PR, merge는 수행하지 않았다.

새 Grid ID는 `kr-osm-transmission`, 표시명은 `KR · 한국 OSM 송전망 (154/345/765 kV)`이다. 카탈로그는 KPG, 몽골, 한국 OSM 순서로 세 계통을 반환한다. 한국 파일을 검증한 경우에만 `map_available=true`이며 `analysis_available`, `pf_preview_available`, `security_analysis_available`, `sensitivity_analysis_available`, `generator_contingency_available`, `chat_available`은 모두 false, `chat_mode=disabled`이다.

변경 범위는 Grid Catalog·지도 로더, 서버 설정 및 API 경계, 공통 SVG 지도와 테스트·문서이다. 별도 Physics API의 request schema·router에 KPG 전용 계통 ID 검사를 추가했으며 KPG/MN 물리해석 알고리즘, LangGraph, 도메인 Tool, Caddy 실행 코드는 변경하지 않았다. 한국·몽골 데이터 생성 프로젝트도 변경하지 않았다.

변경·추가 파일:

```text
.env.example
README.md
web_app.py
src/ai_ems/config.py
src/ai_ems/grid_catalog.py
src/ai_ems/api/routes.py
src/ai_ems/api/schemas.py
ui/index.html
scripts/import_kr_osm_map.py
tests/test_grid_catalog.py
tests/test_korea_osm_map.py
tests/test_browser_stale_responses.py
tests/browser/chrome_cdp.py
tests/browser/korea_osm_map_probe.js
tests/browser/requirements.txt
docs/validation/korea-osm-map-integration.md
```

## 데이터 경로와 가져오기

실제로 읽은 원본은 형제 프로젝트의 `../korea-grid-poc/outputs/map/kr_grid_preview.json`이다. 이 경로는 문서와 운영자 CLI 입력 예시이며 앱 코드에 개인 계정의 절대 경로를 넣지 않았다. 원본 OSM 기준은 2026-10-20 20:23:47 UTC이다.

```bash
cd ~/Projects/ai-ems-agent
.venv/bin/python scripts/import_kr_osm_map.py \
  --source ../korea-grid-poc/outputs/map/kr_grid_preview.json
```

원본 검증 후 `data/grids/kr-osm-transmission-map.json`에 필요한 지도 데이터만 원자적으로 저장한다. 원본 SHA256을 `metadata.source_map_sha256`에 기록한다. 원본과 목적지가 같으면 거부하며, 검증 실패 시 이전 배포 파일을 보존한다. NetCDF는 복사하지 않는다. 원본 약 26.7MB에서 배포용 약 5.9MB로 줄었으며 표시 대상 모선·선로 경로·발전소 정보는 유지한다. 별도 OSM 발전기·발전소 원본 레이어를 WRI 132개 레코드와 중복 합산하지 않는다.

기존 `.gitignore`의 `data/grids/`, `*.nc`, `outputs/` 정책을 유지해 대용량 데이터와 브라우저 산출물을 Git에 포함하지 않는다. 새 환경에는 위 import 명령을 별도로 실행해야 한다.

복사 없이 승인된 외부 파일을 읽는 구성도 가능하다.

```bash
AI_EMS_KOREA_MAP_FILE=../korea-grid-poc/outputs/map/kr_grid_preview.json \
  .venv/bin/python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

환경변수는 서버 운영 설정으로만 읽는다. HTTP 요청에는 파일 선택 인자가 없으며 임의의 `file=/etc/passwd` 인자를 넣어도 설정된 지도만 반환하는 것을 테스트했다. 상대 경로는 AI-EMS 프로젝트 루트를 기준으로 해석한다. 파일 누락 시 지도 API는 404, 손상·스키마 오류는 422이며 카탈로그에서 한국만 사용 불가로 처리한다. 파일의 실제 경로·inode·크기·mtime·ctime이 바뀌면 캐시를 다시 검증한다.

## 공통 로더와 API 계약

기존 `load_mongolia_map()`의 응답 계약은 유지하고 공통 `load_geographic_map()`의 provenance 확장으로 한국 입력을 처리한다. `GET /api/grids/{grid_id}/map`에서 세 계통 모두 같은 기본 구조를 반환한다.

- 모선: `bus_id`, `latitude`, `longitude`, `voltage_kv`, 원본 `osm_id`, `source`, `inferred`, 접속 검토 플래그.
- 선로: `line_id`, `bus1_id`, `bus2_id`, 전압·길이, **원본 `coordinates` 전체**, OSM ID·태그·출처·추정 근거.
- 발전소: 이름·좌표·발전원·과거 설비용량, WRI 및 원출처, 용량 기준연도, 현재 운영 상태 미확인·지도 전용 표시. `mapped_bus_id=null`을 유지한다.
- 경계·메타데이터: OSM 해안선 기반 육지 경계(아래 "지도 형상 수정" 참조), 17개 시도 경계, OSM 기준 시점·라이선스·해시·`NOT_READY` 상태. 원본의 국가 폴리곤(영해 포함 관할 경계)은 표시하지 않는다.

추측한 전기적 정수·발전 출력·부하·접속 모선은 추가하지 않는다. ID 중복, 잘못된 모선 참조, 좌표·전압 범위, 선로 경로와 양단 불일치, 비유한 숫자, 잘못된 추정 플래그를 거부한다. 파일 크기·설비 수·경로 꼭짓점 수에 상한을 둔다. 한국은 154/345/765kV AC 지도 전압과 지도 전용 메타데이터를 확인한다.

## 공통 지도 UI

카탈로그의 `map_kind`로 시험계통 지도와 지리 기반 지도를 구분한다. 몽골과 한국 OSM은 `geographic`이며 동일한 좌표 투영·전압 스타일·레이어·선택·툴팁·휠 확대·pointer 드래그·전체 보기 기능을 쓴다. KPG의 기존 확대 동작과 기본 선택은 유지한다. 국가 이름으로 새 지도 렌더러를 분기하지 않는다.

한국의 `coordinates`가 있는 선로는 모든 꼭짓점을 SVG `path`로 투영한다. 선택용 hit path도 동일한 경로를 사용한다. 경로가 없는 기존 지도는 기존 직선 표현을 유지한다. 지리 범위나 경계 좌표는 변경하지 않으며, 경계의 종횡비에 맞춰 지리 지도 viewport를 선택한다. 765kV에는 기존 팔레트의 적색 계열을 추가하고 Light/Dark/System 테마를 공통 적용한다.

전압별 체크박스는 로드된 데이터에서 생성한다. 필터 변경 시 선로와 선택 영역을 함께 숨기며, 계통 전환 시 필터·툴팁·모선·선로·PF와 분석 결과를 초기화한다. 기존 계통별 채팅 기록과 서버 세션 ID는 유지한다. 늦게 도착한 한국 지도 응답도 기존 `gridSwitchToken` 검사로 차단한다.

실제 데이터 표시 수는 모선 4,197개, 선로 구간 3,984개, WRI 발전소 레코드 132개이다. 같은 좌표의 2개 레코드를 묶어 실제 마커는 131개다. 이는 한국 전체 발전소 수가 아니며, 용량은 실제 발전 출력이 아니다.

| 전압 | 선로 구간 |
|---|---:|
| 154kV | 2,637 |
| 345kV | 1,155 |
| 765kV | 192 |

## 기능 제한과 API 검증

한국 선택 시 PF 표시 모드, Security, Sensitivity, 발전기 사고, AI 입력·전송이 비활성이다. KPG 기본 선로·발전기 입력과 이전 분석 결과도 지운다. 버튼의 disabled 상태뿐 아니라 실행 함수의 capability 검사로 직접 호출도 요청을 만들지 않는다. 모델 설명은 모델 정보에 한 번만 표시한다.

> 공개 OSM 기반 한국 송전망 지도 모델이며, 전기적 모델 검증 전으로 계통해석은 지원하지 않습니다.

한국의 grid-scoped PF·계산·채팅 API는 409로 거부한다. 기존 KPG 전용 웹 API에 한국 `grid_id`를 query/body로 전달해도 409이며, 별도 Physics API의 `/api/v1` 요청도 미지원 계통 ID를 422로 거부한다. 지정된 계통 ID가 URL과 body에서 충돌하면 거부한다. 테스트에서는 양쪽 엔진·Agent를 호출하면 실패하는 spy를 설치해 차단이 계산 전에 실행됨을 확인했다. 계통을 생략한 기존 KPG 요청 계약은 유지한다.

## AC PF를 비활성화한 이유

원본은 공개 OSM 기반 지리적 토폴로지이며 R/X/B, 정격, 변압기 권선별 접속·임피던스·탭, 발전·부하 P/Q, slack 자료가 부족하다. 원본 NetCDF의 전기적 정수 자리표시자를 운영 수치로 가져오지 않는다. 연결 성분 426개와 공유 지지물 분기 검토 대상 1,170개도 실제 접속·개폐 상태를 검증한 결과가 아니다.

한국 모델 접속표·단선도, 선로·변압기 정수, 동시점 발전·부하와 제어 자료를 확보한 뒤 별도 PF 모델에서 수급·전압·수렴을 검증해야 한다. 그 전에는 AC PF, N-1, Sensitivity, OPF, Redispatch, 한국 전용 AI를 활성화하지 않는다.

## 브라우저·성능 검증

실제 전국 JSON으로 Chrome for Testing 156.0.8078.47에서 검증했다. Windows Chrome DevTools는 WSL에서 접속되지 않아 공식 Linux 배포를 `/tmp`에 내려받아 사용했다. 시스템 패키지와 Windows 설정은 변경하지 않았다. Linux 이미지에 한국어 글꼴이 없어 공개 Noto 글꼴을 Git 제외 테스트 캐시에 준비했다.

새 한국 테스트는 Chrome DevTools의 실제 휠·마우스 입력을 사용하고 headless virtual time을 끈 상태에서 `performance.now()`와 animation frame으로 측정한다. 기존 지연 응답 테스트는 원래의 virtual time/gate 방식으로 유지한다. 결과는 `outputs/korea_osm_integration/browser/results.json`, Light/Dark 스크린샷도 같은 디렉터리에 저장한다. 단일 개발 환경의 측정이며 반복 분포나 운영 서비스 SLA를 뜻하지 않는다.

| 항목 | 측정값 |
|---|---:|
| 최초 한국 지도 전환·렌더링 | 502.0 ms |
| 선로 선택·툴팁 | 32.1 ms |
| 전압 필터 변경 | 45.8 ms |
| 실제 휠 입력 후 프레임 | 86.9 ms |
| 드래그 move 후 프레임 최대값 | 70.4 ms |
| 여러 이벤트를 포함한 드래그 전체 | 296.0 ms |
| 한국 → 몽골 | 65.0 ms |
| 몽골 → KPG | 83.3 ms |

최종 결과: 실제 한국 지도 Chrome 검증 **32개 PASS**, 전체 기본 pytest **406 passed, 14 skipped**, Chrome 포함 전체 pytest **420 passed**, `git diff --check` 통과. 경고 1개는 기존 Starlette/AnyIO의 DeprecationWarning이다. KPG·몽골의 채팅, 실제 서버 세션·이력 복원, 늦은 응답 차단, 공통 분석 카드, Light/Dark/System 및 좁은 화면 도움말 회귀를 포함한다. 좁은 화면에서 화면 밖 버튼으로 도움말을 여는 경우 발견된 위치 오류는 viewport 안으로 제한하도록 수정했고 기존 검증을 유지했다.

최초 전환은 API 조회·JSON 파싱·SVG 생성·두 animation frame을 포함한다. 휠 및 move 지연도 입력에서 두 프레임까지의 개발 환경 측정이며 드래그 전체 값에는 여러 CDP 입력의 왕복 시간이 포함된다. 기존 SVG에서 실사용을 막는 성능 문제는 확인되지 않아 Canvas/WebGL이나 신규 렌더링 프레임워크를 도입하지 않았다.

실제 원본과 가져온 파일의 전체 모선·선로·발전소 필드, 선로 49,838개 경로 꼭짓점과 경계를 비교했다. 결과는 `outputs/korea_osm_integration/source_comparison.json`에 있고 모두 일치했다.

## 지도 형상(해안선) 수정 — 2026-10-26

### 발견한 문제

한국 OSM 지도의 국가 외곽선이 실제 육지와 달랐다. 서해·남해안은 직선으로 단순화되어 바다 쪽으로 크게 나가 있었고, 제주도는 본토와 한 면으로 이어졌으며, 울릉도·독도는 원형으로 표시됐다.

### 레이어 분리로 확인한 원인

`tests/browser/capture_korea_map_layers.py`로 실제 Chrome에서 국가 외곽선·행정구역·송전망을 각각 표시하고, 같은 경위도 범위와 크기로 촬영했다. Natural Earth 10m 육지(분홍 선)도 비교용으로 겹쳤다.

| 분류 | 확인 결과 |
|---|---|
| 해안선과 행정·관할 경계 혼용 (**원인**) | 원본 `country_polygons`는 `korea-grid-poc/scripts/build_kr.py`가 OSM `admin_level=2` relation 307756을 그대로 넣은 것이다. OSM 국가 경계에는 직선기선과 12해리 영해가 들어 있어 육지 해안선이 아닌 해상 관할 경계다. 울릉도·독도의 원은 12해리 영해 원이다. 면적은 184,598 km²로, 실제 육지(101,167 km²)보다 45.2% 크다. |
| 원본 경계 데이터 단순화 | 원본 생성 시 `simplify(0.002°)`(약 200 m)도 적용됐지만 형상 오류의 주원인은 아니다. 단순화 전 relation도 영해 경계다. |
| 행정구역 경계 | geoBoundaries gbOpen KOR ADM1(Natural Earth 기반, 2021, Public Domain)이며 육지를 따른다. 화면에서 선만 그리고 채우지 않아 문제를 만들지 않았다. |
| 폴리곤 처리 | 좌표는 `[경도, 위도]`이고 모든 링이 닫혀 있다. 국가 폴리곤 4개는 각각 외곽 링 하나이며 내부 링은 없다. UI의 `evenodd` path 처리에는 오류가 없었다. |
| 투영·화면 비율 | `drawNetwork()`는 등장방형 투영에 경도 `cos(평균 위도)` 보정을 쓰며 외곽선과 설비에 같은 투영을 적용한다. 경계 범위에서 viewport만 고르며 경위도를 따로 늘리지 않는다. 한국 범위(33.1–38.6°N)에서 가로 축척 편차는 약 ±3.5%로, 형상 오류 원인이 아니다. Chrome에서 1° 경도/위도 비율이 `cos(평균 위도)`와 1e-9 이내로 일치함을 확인했다. |
| 송전망 | 모선·선로·발전소 레이어는 정상이다. |

### 적용한 데이터와 생성 절차

- 육지: 송전망과 같은 Geofabrik 추출본(`south-korea-261020.osm.pbf`, SHA256 `74c2033a…`, OSM 기준 2026-10-20T20:23:47Z)의 OSM `natural=coastline`이다. 라이선스는 ODbL-1.0, © OpenStreetMap contributors이다. 새로 내려받은 OSM 자료는 없다.
- 국가 범위: 같은 추출본의 OSM 대한민국 relation 307756으로 육지를 잘랐다. 휴전선 구간의 육상 경계와 섬의 국가 귀속은 OSM 출처를 따른다. 임의로 그린 경계는 없다.
- 독립 비교 기준: Natural Earth 10m land v5.1.1(Public Domain, `ne_10m_land.zip` SHA256 `e547d749…`)이다. 표시에는 쓰지 않으며 `outputs/kr_coastline_fix/reference/`에 원본 zip과 한국 범위 추출본을 보존했다.

`scripts/build_kr_land_boundary.py`가 해안선 way를 방향을 유지하며 잇는다. OSM 규칙은 진행 방향 왼쪽이 육지다. 닫힌 링 4,040개는 모두 반시계 방향(섬)이고 시계 방향 링은 0개다. 열린 해안선 6개(본토와 추출 경계에서 잘린 북측 해안)는 국가 relation 경계와 함께 면으로 만들었다. 각 면은 해안선 좌·우 표본점 투표로 육지와 바다를 나눴다. 투표가 없는 3개 면은 백령도·울릉도·독도 주변 영해 원 안의 바다다. 단순화는 한국 중심 LAEA 평면에서 전체 폴리곤을 한 번에 50 m topology-preserving으로 했다. 0.02 km² 미만 섬 2,669개(합계 11.3 km², 육지의 0.011%)는 표시에서 뺐다. 이 섬들은 전국 화면에서 1픽셀보다 작다.

결과(`data/grids/kr-land-boundary.json`, 약 1.0 MB, Git 제외)는 폴리곤 1,337개, 꼭짓점 45,968개이다. 원해상도 해안선 꼭짓점 474,477개 중 99.86%가 표시 경계에서 51 m 안에 있다. `import_kr_osm_map.py --land-boundary`가 이 파일을 검증해 `country_polygons`로 넣고 `metadata.land_boundary`에 출처·라이선스·해시·단순화 기준을 기록한다. `--land-boundary` 없이 가져오거나, 원본 JSON을 직접 지정해 육지 경계 메타데이터가 없으면 로더가 영해 폴리곤을 버린다. 이때는 국가 외곽선 없이 행정구역과 송전망만 표시한다.

| 비교 항목 | 수정 전(영해) | OSM 해안선(적용) | Natural Earth 10m | 참고(공식 통계) |
|---|---:|---:|---:|---:|
| 육지 면적 km² | 184,598 | 101,167 (표시 101,161) | 99,033 | 약 100,400 |
| 제주도 km² | 본토와 한 면 | 1,841.2 | 1,867.9 | 약 1,833 |
| 울릉도 km² | 12해리 원 | 73.5 | 73.1(영역) | 약 72.9 |
| 독도 동도/서도 km² | 12해리 원 | 0.074 / 0.092 | 없음 | 약 0.073 / 0.089 |
| 서남해안(125.9–126.7°E, 34.3–35.1°N) 섬 수 | — | 748 | 31 | — |

공식 통계 열은 일반 공개 수치로, 정확도 검증 기준이 아니라 규모 확인용이다. 제주 해안선은 OSM과 Natural Earth의 경계가 최대 2.0 km(Hausdorff) 차이 난다. 이는 Natural Earth 1:10m 일반화 수준과 맞는다. OSM 면적이 공식 국토면적보다 약 0.75% 큰 것은 간척지·조간대 표현, 하구 처리 등 해안선 정의 차이로 보인다. 측량 수준의 정확도는 주장하지 않는다.

### 수정 파일

- `scripts/build_kr_land_boundary.py`(신규): 오프라인 육지 경계 생성과 Natural Earth 비교. osmium·shapely·pyproj를 쓰며 앱 의존성은 추가하지 않았다.
- `scripts/import_kr_osm_map.py`: `--land-boundary` 옵션과 검증을 추가하고 영해 폴리곤을 넘기지 않게 했다.
- `src/ai_ems/grid_catalog.py`: 경계 폴리곤 수 상한을 100→5,000, 꼭짓점 상한을 50,000→100,000으로 늘렸다. 링 폐합을 검사하고, 한국은 육지 경계 메타데이터가 있을 때만 국가 폴리곤을 쓴다. 한국 범위(124–132.5°E, 32.5–39°N) 밖의 좌표는 거부한다.
- `ui/index.html`: 경계 범위 계산을 `Math.min(...배열)`에서 반복문으로 바꿨다. 꼭짓점이 많아도 인자 수 한계에 걸리지 않는다. 투영식은 바꾸지 않았다.
- 테스트: `tests/test_korea_osm_map.py`, `tests/browser/korea_osm_map_probe.js`, 개발용 캡처 `tests/browser/capture_korea_map_layers.py`(pytest 수집 대상 아님).

KPG의 `kr-geography.json` 외곽선, 몽골 지도, 물리해석 엔진, LangGraph, Caddy, 한국 전기 모델·NetCDF(`base.nc` SHA256 `46c35535…` 동일)와 `korea-grid-poc` 파일은 변경하지 않았다.

### 해안선과 행정경계의 의미

- 해안선·육지 경계(`country_polygons`, 회색 면): 바다와 맞닿은 육지의 물리적 경계이며 OSM `natural=coastline` 기준이다.
- 국가 relation(OSM `admin_level=2`): 영해·직선기선을 포함하는 관할 경계다. 육지 경계를 국가 범위로 자를 때만 쓰며 화면에는 그리지 않는다.
- 행정구역 경계(`admin_regions`, 선): 시도 관할 구분이다. Natural Earth 기반 1:10m 일반화 자료라 해안 부분이 OSM 해안선과 수백 m~2 km 어긋날 수 있다. 별도 레이어로 유지하며 "행정구역 경계" 체크박스로 따로 끄고 켠다.

### 송전망 데이터 보존

수정 전 배포 파일(`outputs/kr_coastline_fix/before/`)과 새 배포 파일의 필드별 정규화 JSON SHA256을 비교했다(`outputs/kr_coastline_fix/preservation.json`). `buses`, `lines`(경로 꼭짓점 49,838개 포함), `powerplants`, `admin_regions`, `transformers`, `generators`, `schema_version`, `grid_id`, `country`가 모두 동일하다. `metadata`는 `land_boundary` 항목만 추가됐다. 모선 4,197, 선로 3,984, 발전소 132, OSM ID 순서와 `NOT_READY`가 유지된다. 원본 `kr_grid_preview.json` SHA256(`12892e97…`)도 변하지 않았다. pytest `test_actual_transmission_data_identical_to_source_after_land_import`가 원본과 배포본을 필드 단위로 비교한다.

### 수정 전후 화면

같은 경위도 범위와 같은 SVG 크기(Chrome 1600×1200 창의 지도 영역)로 촬영했다. 파일은 `outputs/kr_coastline_fix/{before,after}/<범위>__<레이어>.png`이다. 범위는 `national`, `jeju`, `ulleung_dokdo`, `dokdo`, `southwest_coast`, `west_coast_incheon`이다. 레이어는 `all`, `country_only`, `admin_only`, `network_only`, `country_vs_reference`(Natural Earth 겹침)이다. 수정 전 화면은 커밋 `7f3ac1f`의 코드를 `git archive`로 풀어 같은 원본 데이터로 촬영했다.

- 수정 전: 회색 면이 서해·남해 직선기선까지 바다를 덮었다. 제주도는 본토와 연결되고 울릉도·독도는 원이었다.
- 수정 후: 회색 면이 Natural Earth 해안선과 겹친다(차이는 Natural Earth 일반화 범위 안). 신안·진도 다도해와 인천 간척지가 OSM 해안선대로 보인다. 독도는 동도·서도 두 섬으로, 울릉도는 실제 섬 윤곽으로 보인다.
- 제약: 최대 확대(120배)에서는 50 m 단순화로 독도처럼 작은 섬이 각진 형태로 보인다.

### 테스트 결과(2026-10-26)

| 구분 | 결과 |
|---|---|
| 기본 pytest | **419 passed, 14 skipped** (이전 406/14, 신규 13건) |
| Chrome 포함 전체 pytest | **433 passed** (이전 420) |
| 실제 한국 지도 Chrome probe | **40/40 PASS** (기존 32 + 신규 8) |
| `git diff --check` | 통과 |

Chrome for Testing 156.0.8078.47(Linux)을 썼다. 로그는 `outputs/kr_coastline_fix/full_{basic,browser}_pytest.log`에 있다.

신규 pytest 검증은 다음과 같다.

- 영해 폴리곤을 육지로 그리지 않음.
- 육지 경계 import가 지리 레이어만 바꾸고 MultiPolygon과 내부 링을 보존함.
- 잘못된 육지 경계 8종(열린 링, 4점 미만, 한국 범위 밖, 문자열·3차원·NaN 좌표, 빈 폴리곤, 영해 kind)을 거부하고 기존 배포 파일을 보존함.
- 몽골 외곽선·행정경계가 원본과 동일함.
- 실제 데이터에서 서울·부산·제주·울릉도·독도 동도/서도는 육지 안에, 서해·제주해협·울릉/독도 영해 지점은 육지 밖에 있음. 같은 지점이 이전 영해 폴리곤 안에 있었음도 확인함.
- 원본과 배포본의 송전망 필드가 동일함.

신규 Chrome 검증은 다음과 같다.

- 육지 레이어가 표시되고 최대 경도가 131.9°E 미만임.
- 투영 종횡비가 `cos(평균 위도)`와 같음.
- 모든 모선·발전소가 초기 화면 안에 있음.
- 행정구역 토글은 행정경계만 숨기고 육지·선로 3,984·모선 4,197은 유지함.
- 한국에서 확대하고 345 kV를 끈 상태로 몽골로 전환하면 확대·필터가 넘어가지 않음.
- 몽골은 자체 외곽선만, KPG는 자체 `kr-geography` 외곽선만 남음.

기존 Chrome 테스트도 그대로 통과했다. `KPG → 몽골 → 한국 OSM → 몽골 → KPG` 전환, 늦은 한국 지도 응답 차단, 계통별 채팅 세션 ID·기록 복원, 응답 생성 중 전환, 분석 패널 접힘, KPG·몽골 Security/Sensitivity/발전기 사고 회귀, Light/Dark/System 테마를 포함한다. 채팅 브라우저 테스트는 scripted LLM fixture를 사용한다. 로컬 Ollama(`127.0.0.1:11434`)가 실행 중이 아니어서 실제 Ollama 테스트는 이번에 수행하지 않았다.

| 성능(단일 개발 환경, 육지 path 1,337개 추가 후) | 이전 | 이번 |
|---|---:|---:|
| 최초 한국 지도 전환·렌더링 | 502.0 ms | 599.2 ms |
| 선로 선택·툴팁 | 32.1 ms | 19.2 ms |
| 전압 필터 변경 | 45.8 ms | 52.5 ms |
| 휠 입력 후 프레임 | 86.9 ms | 90.0 ms |
| 드래그 move 후 프레임 최대 | 70.4 ms | 105.0 ms |
| 한국 → 몽골 / 몽골 → KPG | 65.0 / 83.3 ms | 55.4 / 83.2 ms |

최초 렌더링과 드래그 프레임이 약 0.1초, 35 ms 늘었다. 이때는 실사용 제약 수준이 아니라고 판단했다. 그러나 실제 Windows Chrome에서 확대·축소·드래그가 끊기는 것이 확인됐다. 아래 "지도 확대·이동 성능 개선"에서 원인을 측정하고 수정했다.

## 지도 확대·이동 성능 개선 — 2026-10-26

### 측정 방법

`tests/browser/korea_map_performance.py`(pytest 수집 대상 아님)로 측정했다. 이 도구는 CDP의 trusted 휠·마우스 입력을 실제 시간 간격으로 보낸다. 휠은 60 Hz로 40회 확대 후 25회 축소한다. 드래그는 120 Hz로 약 1초 이동한다. 페이지 안에서는 animation frame 간격, 입력→다음 프레임 시간, Long Task(50 ms 초과)를 기록한다. Chrome trace(`devtools.timeline`)로는 렌더러 메인 스레드의 Style/Layout/Paint/Layerize 시간을 합산한다. `--variant`는 CSS로 레이어 하나 또는 규칙 하나만 끈 A/B 비교에 쓴다.

모든 수치는 WSL2 Linux의 Chrome for Testing 156.0.8078.47 headless(소프트웨어 래스터)에서 얻었다. 창 크기 1600×1200, 실제 전국 지도 데이터, 각 조건 3회 반복의 중앙값이다. 이전 표의 측정 방식(단발 입력)과 다르므로 위 표의 수치와 직접 비교하지 않는다.

Windows Chrome은 WSL에서 DevTools 포트에 접속할 수 없었다. 포트를 모든 인터페이스에 여는 방식은 보안상 쓰지 않았다. 그래서 Windows 수치는 이 문서에 없다. Windows Chrome에서는 `tests/browser/map_interaction_console.js`를 DevTools 콘솔에 붙여넣고 10초간 직접 확대·드래그하면 같은 지표(FPS, 프레임 p50/p95/max, 33 ms 초과 프레임, Long Task)를 볼 수 있다.

### 실제 병목

개선 전 A/B 측정 결과(같은 조건, 2회 중 마지막 회의 메인 스레드 합계)이다.

| 조건 | 확대 FPS | 드래그 FPS | 확대 Style ms | 확대 Layout ms | 드래그 Layout ms |
|---|---:|---:|---:|---:|---:|
| 기준 | 33–36 | 28–32 | 2,524 | 1,339 | 약 2,000 |
| 육지 경계 숨김 | 31–36 | 34–44 | 2,099 | 680 | — |
| 행정경계 숨김 | 32–38 | 36–38 | 2,897 | 993 | — |
| 모선 숨김 | 40 | 40–43 | 2,015 | 858 | — |
| 선로 숨김 | 42 | 38–55 | 1,999 | 624 | — |
| 선로 hit path 숨김 | 39–40 | 41–47 | 2,156 | 758 | — |
| 마커 확대 보정 CSS 끔 | 40–44 | 34–45 | 1,752 | 803 | — |
| `non-scaling-stroke` 끔 | 40–43 | 53–54 | 2,091 | **62** | — |

1. **확대: 상속 CSS 변수로 인한 전체 스타일 재계산.** 휠 이벤트마다 `setMapView()`가 `<svg>`의 `--map-zoom`과 `viewBox`를 바꿨다. 사용자 정의 속성은 상속되므로 SVG 하위 약 1만 7천 개 요소의 스타일이 매번 다시 계산됐다. 마커 축소 규칙만 꺼도 1.75초가 남으므로 비용의 원인은 변수 상속 무효화 자체다. 어느 레이어를 지워도 2초 안팎이 남았다.
2. **드래그: `vector-effect: non-scaling-stroke`로 인한 전체 레이아웃.** `viewBox`가 바뀔 때마다 non-scaling stroke 요소 전체의 레이아웃이 다시 계산됐다. 이 속성만 끄면 Layout이 1.3초에서 62 ms로 줄었다. 드래그 move마다 호출한 `getScreenCTM()`이 그 레이아웃을 동기적으로 강제했다.
3. 레이어 하나를 숨기는 효과는 10–30%에 그쳤다. 요소 수는 기여 요인이지만 근본 원인은 입력 이벤트마다 전체 style + layout을 다시 하는 구조였다.

### 수정 내용(`ui/index.html`)

- **제스처 중 compositor 미리보기:** 휠·드래그 중에는 목표 view를 `<svg>`의 CSS `transform`(translate + scale)으로 표시한다. 하위 요소의 스타일·레이아웃·페인트는 다시 하지 않는다. 갱신은 `requestAnimationFrame`으로 프레임당 한 번만 한다. `#network-map`은 `will-change: transform` 레이어로 두고 `#map`이 넘친 부분을 자른다.
- **제스처 종료 시 한 번 확정:** 휠은 마지막 입력 후 160 ms, 드래그는 놓을 때 또는 누른 채 160 ms 멈출 때 기존 `setMapView()`로 `viewBox`·`--map-zoom`을 확정한다. 이때 선 굵기·마커 크기 보정과 hit test가 원래대로 정확해진다.
- **제스처 중 중간 확정:** 미리보기가 렌더링된 view에서 2배 이상 확대·축소되거나 화면의 30% 이상 이동하면 즉시 확정하고 드래그 기준점을 다시 잡는다. 이로써 흐려진 확대 화면과 드래그 중 아직 그려지지 않은 가장자리 띠를 제한한다.
- **좌표 계산:** 휠 중심점과 드래그 이동량은 제스처 시작 시 한 번 읽은 SVG 위치·크기와 view 수학으로 계산한다. 이벤트마다 `getScreenCTM()`을 호출하던 강제 레이아웃이 없어졌다. 미리보기 위치와 확정 후 위치의 차이는 Chrome 측정에서 0.0001 px였다.
- 계통 전환·전체 보기·`setMapView()` 직접 호출은 대기 중인 제스처를 취소하고 transform을 지운다. `setMapView()`는 기존처럼 동기 확정을 유지해 기존 호출·테스트 계약이 같다.

SVG 요소, 해안선, 선로 원본 경로, hit path, 마커는 하나도 제거하지 않았다. Canvas나 LOD는 도입하지 않았다. 측정 결과 원인이 요소 수가 아니라 이벤트마다 반복되는 확정 비용이었기 때문이다.

### 개선 전후(같은 Chrome·창 크기·데이터·입력, 3회 중앙값)

| 항목 | 이전 DSF 1 | 개선 DSF 1 | 이전 DSF 1.5 | 개선 DSF 1.5 |
|---|---:|---:|---:|---:|
| 초기 렌더링 ms | 558.3 | 545.4 | 527.1 | 522.9 |
| 확대 FPS | 39.4 | 51.4 | 40.7 | 51.7 |
| 확대 프레임 간격 p50 / p95 / max ms | 16.7 / 50 / 150 | 16.7 / 50 / 83.4 | 16.7 / 50.1 / 150 | 16.7 / 50 / 83.4 |
| 확대 33 ms 초과 프레임 | 43 | 10 | 49 | 10 |
| 확대 입력→다음 프레임 p50 / p95 ms | 81.9 / 98.9 | 32.0 / 82.2 | 81.9 / 115 | 32 / 98.7 |
| 확대 Long Task 수 / 합계 / 최대 ms | 58 / 3,380 / 168 | 10 / 689 / 91 | 54 / 3,141 / 162 | 10 / 683 / 91 |
| 드래그 FPS | 34.0 | 58.3 | 32.5 | 57.6 |
| 드래그 프레임 간격 p50 / p95 / max ms | 33.3 / 33.4 / 100 | 16.7 / 16.8 / 33.3 | 33.3 / 49.9 / 83.4 | 16.7 / 16.8 / 50 |
| 드래그 33 ms 초과 프레임 | 14 | 0 | 16 | 2 |
| 드래그 Long Task 수 / 최대 ms | 3 / 90 | 0 / 0 | 3 / 91 | 0 / 0 |
| 154 kV 필터 변경 ms | 45.7 | 48.5 | 46.8 | 48.0 |

"이전"은 커밋 `7f3ac1f`에 해안선 수정만 적용한 상태(이번 작업 직전)이다. 결과 파일은 `outputs/kr_map_performance/{before,after}_dsf{1,1.5}.json`, `comparison.md`, A/B 결과는 `before_ab.json`·`after_ab.json`에 있다.

- 이 확대 시나리오는 약 1초에 36배까지 확대·축소하는 스트레스 조건이다. 남은 Long Task 10개는 2배마다 하는 중간 확정이다. 한 번에 55–90 ms가 걸리고 스타일·페인트·레이아웃에 나뉘어 있다. 중간 확정 없이 끝에서만 확정하면 59 fps·Long Task 1개까지 줄지만, 미리보기가 8배 이상 흐리게 확대되어 채택하지 않았다.
- 드래그 "입력→다음 프레임" 중앙값은 9.1 → 13.7 ms로 커 보인다. 이 지표는 다음 프레임의 시작 시각만 잰다. 이전에는 그 프레임이 33–50 ms 걸려 끝났으므로 실제 화면 반영은 개선 후가 더 빠르다.
- 제스처 중 화면은 이미 그린 비트맵을 확대·이동한 것이다. 최대 2배까지 흐릿하거나, 렌더링 범위 밖 가장자리(최대 화면의 30%)가 잠시 비어 보이다가 확정 시 선명해진다.

`outputs/kr_map_performance/gesture_frames/`에 휠 미리보기, 확정, 드래그 중, 드래그 멈춤 후 확정 화면을 저장했다.

### 회귀 검증

한국 지도 Chrome probe에 다음을 추가했다.

- 휠 미리보기가 `viewBox`를 바꾸지 않는 transform인지 확인한다.
- 확정 후 모선 300개의 화면 위치가 미리보기와 1 px 안에서 같은지 확인한다(측정 0.0001 px).
- 확대 상태에서 모선 중심에 trusted 클릭을 하면 그 모선만 선택되는지 확인한다(`chrome_cdp.py`에 click 단계 추가).
- 미리보기가 남은 상태에서 몽골로 전환해도 transform·확대가 넘어가지 않는지 확인한다.

- 미리보기 확인은 1.57배 휠로 한다. 2배를 넘는 단일 휠은 즉시 확정되는지 별도로 확인한다.

| 구분 | 결과 |
|---|---|
| 기본 pytest | **419 passed, 14 skipped** |
| Chrome 포함 전체 pytest | **433 passed** |
| 실제 한국 지도 Chrome probe | **45/45 PASS**(3회 연속), 해안선 작업 40 + 성능 5 |
| `git diff --check` | 통과 |

pytest 개수가 이전과 같은 이유는 신규 확인이 기존 한국 지도 Chrome 테스트 안의 probe 항목으로 들어갔기 때문이다. 기존 브라우저 회귀는 모두 통과했다. 여기에는 KPG·몽골 분석·채팅, 세션 복원, 늦은 응답 차단, 응답 중 전환, 패널 상태, 테마·도움말, 몽골 `setMapView()` 확대·전체 보기가 포함된다. 로그는 `outputs/kr_map_performance/full_{basic,browser}_pytest.log`에 있다.

probe 단발 측정 결과는 다음과 같다. 휠→미리보기 프레임 16.1–16.4 ms(이전 단발 측정 86.9–90.0 ms), 휠→확정 250–266 ms(160 ms 대기 포함), 드래그 move→프레임 최대 41–51 ms(이전 70–105 ms, CDP 왕복 포함)이다.

남은 한계는 다음과 같다.

- Windows Chrome 실측이 없다. 콘솔 스크립트로 확인이 필요하다.
- 확정 1회에 55–90 ms가 걸린다(Linux 소프트웨어 래스터 기준). 연속 확대 중 2배마다 짧게 멈출 수 있다.
- 제스처 중 최대 2배 흐림과 가장자리 빈 띠가 잠시 보일 수 있다.
- 확정 비용 자체를 더 줄이려면 마커를 별도 그룹으로 분리해 `--map-zoom` 변경 범위를 줄이는 방법, 또는 정적 배경(육지·행정경계)을 Canvas로 분리하는 방법을 측정 후 검토할 수 있다.


## 재현 명령

```bash
# 육지 경계 생성(+ Natural Earth 비교)과 가져오기
../korea-grid-poc/.venv/bin/python scripts/build_kr_land_boundary.py \
  --pbf ../korea-grid-poc/data/raw/south-korea-261020.osm.pbf --output data/grids/kr-land-boundary.json \
  --natural-earth /path/to/ne_10m_land.shp --comparison-output outputs/kr_coastline_fix/reference/comparison.json
.venv/bin/python scripts/import_kr_osm_map.py --source ../korea-grid-poc/outputs/map/kr_grid_preview.json \
  --land-boundary data/grids/kr-land-boundary.json
# 레이어 분리·범위 고정 스크린샷
AI_EMS_CHROME=/path/to/chrome .venv/bin/python tests/browser/capture_korea_map_layers.py \
  --map data/grids/kr-osm-transmission-map.json --output outputs/kr_coastline_fix/after \
  --reference outputs/kr_coastline_fix/reference/natural_earth_10m_land_kr.json
# 확대·드래그 성능(개발용, Linux Chrome). --variant로 A/B, --trace로 메인 스레드 분해
AI_EMS_CHROME=/path/to/chrome .venv/bin/python tests/browser/korea_map_performance.py \
  --output outputs/kr_map_performance/after_dsf1.json --repeat 3 --scale 1
.venv/bin/python -m pytest -q tests/test_korea_osm_map.py tests/test_grid_catalog.py
.venv/bin/python -m pytest -q tests
# 실제 Chrome 경로를 지정한다. 테스트용 websocket-client가 필요하다.
uv --cache-dir .cache/uv pip install --python .venv/bin/python -r tests/browser/requirements.txt
AI_EMS_CHROME=/path/to/chrome AI_EMS_BROWSER_TESTS=1 \
  .venv/bin/python -m pytest -q tests
git diff --check
```

새 invalid-data·API 차단 테스트는 작은 fixture를 사용한다. 별도의 실제 전국 데이터 테스트와 Chrome 테스트는 가져온 4,197/3,984/132 데이터를 사용한다. 기존 채팅 브라우저 테스트는 LLM 응답 선택을 fixture로 고정하되 실제 API·서버 세션·해석 Tool 경로를 검증한다. 실제 외부 LLM 서비스의 가용성을 검증했다는 뜻은 아니다.

## 데이터 출처와 재배포

OSM 기반 데이터는 © OpenStreetMap contributors, ODbL-1.0이며 Geofabrik 추출물에서 유래한다. WRI GPPD v1.3.0은 CC BY 4.0의 과거 자료다. 원본 JSON의 라이선스·출처를 메타데이터와 발전소별 레코드에 보존한다. 시도 경계는 원본에서 제공한 Natural Earth 기반 geoBoundaries 자료다. 육지 경계는 OSM 해안선에서 파생한 ODbL 데이터로, 같은 저작자 표시와 공유 조건을 따른다. 비교 기준인 Natural Earth 10m land는 Public Domain이며 표시에는 쓰지 않는다. 파생 데이터베이스·지도를 외부 배포할 때 OSM의 저작자 표시·데이터베이스 공유 조건과 WRI 출처 표시를 유지해야 한다. 데이터 파일을 소스 코드와 함께 무조건 Git에 넣지 않는다.

## 후속 작업

이번 공통 로더는 기존 몽골 계약을 유지하면서 경로·provenance를 보존하는 확장이다. 여러 국가로 늘릴 때는 국가별 입력 스키마 검증과 공통 응답 정규화를 분리하고, 데이터 버전·체크섬 manifest와 배포 절차를 공통화할 수 있다. 각 국가의 지도 가용성과 PF·Security·Sensitivity·AI capability는 계속 독립적으로 검증해야 한다.

지리 데이터 후속 확인 사항:

- 행정구역 경계는 Natural Earth 1:10m 일반화 자료다. 해안 부분이 OSM 육지와 최대 약 2 km 어긋날 수 있다. 정밀 표시가 필요하면 OSM `admin_level=4`를 육지 경계로 잘라 쓰는 방안을 검토한다.
- OSM 해안선은 간척지·하구 처리가 시점에 따라 바뀐다. 추출본을 갱신하면 육지 경계를 다시 생성하고 비교 지표를 다시 확인해야 한다.
- 0.02 km² 미만 섬 2,669개는 표시에서 제외했다.
- 실제 사용자 브라우저(Windows Chrome·고배율 디스플레이)에서 서해안 다도해 확대·드래그 체감 성능을 확인해야 한다.
- KPG 외곽선(`kr-geography.json`, geoBoundaries)은 이번 범위 밖이라 변경하지 않았다.
