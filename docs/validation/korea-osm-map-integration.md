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
- 경계·메타데이터: 원본의 국가 외곽선, 17개 시도 경계, OSM 기준 시점·라이선스·해시·`NOT_READY` 상태.

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


## 재현 명령

```bash
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

OSM 기반 데이터는 © OpenStreetMap contributors, ODbL-1.0이며 Geofabrik 추출물에서 유래한다. WRI GPPD v1.3.0은 CC BY 4.0의 과거 자료다. 원본 JSON의 라이선스·출처를 메타데이터와 발전소별 레코드에 보존한다. 시도 경계는 원본에서 제공한 Natural Earth 기반 geoBoundaries 자료다. 파생 데이터베이스·지도를 외부 배포할 때 OSM의 저작자 표시·데이터베이스 공유 조건과 WRI 출처 표시를 유지해야 한다. 데이터 파일을 소스 코드와 함께 무조건 Git에 넣지 않는다.

## 후속 작업

이번 공통 로더는 기존 몽골 계약을 유지하면서 경로·provenance를 보존하는 확장이다. 여러 국가로 늘릴 때는 국가별 입력 스키마 검증과 공통 응답 정규화를 분리하고, 데이터 버전·체크섬 manifest와 배포 절차를 공통화할 수 있다. 각 국가의 지도 가용성과 PF·Security·Sensitivity·AI capability는 계속 독립적으로 검증해야 한다.
