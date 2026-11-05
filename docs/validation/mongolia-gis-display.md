# 몽골 기존 선로 GIS 표시 적용 — 2026-10-30

기준 HEAD `41c8689`, 브랜치 `feature/kr-osm-map-integration`. Commit·Push하지 않았다.
기존 모델 선로의 표시 좌표만 분리해 적용했다. 신규 선로·참조층·계통 모델을 추가하지 않았다.

## 결과

| 항목 | 결과 |
|---|---:|
| 기본 지도 | 173모선·159선로·27변압기 유지 |
| 원본 OSM 경로 적용 | 141개 모델 선로, 195개 독립 경로 조각 |
| 직선 유지 | 18개: 단절 11, 분기·루프 7 |
| 표시하는 OSM 구간 길이 합 | 8,428.054 km (회선/실측 길이 아님) |
| 생략한 합성 연결구간 | 152.248 km |
| PF 모델 | 기존 166모선·156선로·27변압기 유지 |
| 기존 참조층 | 33선로·17변전소 데이터/API 보존, UI 미표시 |

전국 화면에서는 기존 선로의 실제 저장 굴곡이 나타나고, 확대 화면에서는 직선과 경로의 차이가 더 분명하다.
설비 수나 새로운 지역의 커버리지는 증가하지 않는다. 합성 연결을 생략했으므로 모델 모선과 원본 경로 사이에 간격이 보일 수 있다.
18개 직선은 실제 지리 경로가 아니라고 툴팁에 표시한다.

## 원본/합성 구간 구분

1. 지도 선로 ID, build GeoJSON, NetCDF geometry, 양단 모선 좌표 및 기존 길이감사 ID 집합을 대조한다.
2. 기존 `pipelines.mn.mn35k_lengths.inspect_chain_geometry()`로 단일 체인·양끝 검사를 재실행한다. 기존 감사 결과와 다르면 export를 중단한다.
3. 각 통과 선로는 **정확한 raw OSM way ID와 clean parent ID**를 사용한다. 가까운 다른 선로를 대신 매칭하지 않는다.
4. build geometry의 각 선분 전체가 raw와 clean 두 원본의 EPSG:3857 **1mm 허용오차** 안에 있을 때만 OSM 구간으로 채택한다.
5. 나머지 build 선분은 합성/원본 미지원 구간으로 측정하고 전부 생략한다. 분리 결과는 기존 감사의 원본 길이·connector 길이와도 대조한다.
6. 원본 조각은 SVG path에서 각각 `M`으로 시작한다. 생략된 구간 사이에 `L`을 생성하거나 직선으로 재연결하지 않는다.

141개 모두 위 대조를 통과했다. 원본 way 전체를 분할 branch마다 반복 복제하지 않았다.
OSM 경로는 실측 도체 경로나 운전 접속을 인증하는 자료가 아니다.

## 변경 파일과 런타임 관계

| 파일 | 내용 |
|---|---|
| `scripts/export_mn_display_geometry.py` | 기존 GIS 환경에서 실행하는 작은 표시 전용 exporter. 원본·NetCDF는 읽기만 하고 EMS `data/grids/`의 새 파일만 생성 |
| `src/ai_ems/mongolia_geometry.py` | 표준 라이브러리만 사용하는 승인 SHA256·현재 지도 hash·ID/양단 검증 |
| `web_app.py` | `GET /api/grids/mn-osm-35k/display-geometry` 추가. 번들 모드에서는 선택된 번들의 지도에 대조 |
| `ui/index.html` | 몽골에서만 별도 자료 조회, multipart 경로 및 선택 hit 영역 적용, 출처/생략 안내, 감시 표식의 경로 위치 대응 |
| `tests/test_mn_display_geometry.py` | 자료 변조·다른 지도·자료 누락·선택된 번들 지도 및 기존 API 격리 |
| `tests/mn_build/test_display_geometry_export.py` | 합성 연결 제거·원본 조각 분리·실제 데이터 재현 |
| `tests/test_mn_display_geometry_browser.py`, `tests/browser/mn_display_geometry_probe.js` | 실제 Chrome 입력/렌더링·fallback·지연 응답·다른 계통 격리 |

생성 자료: `data/grids/mn-osm-35k-display-geometry.json` (약 2.4 MiB, 기존 정책대로 Git 제외).
승인 SHA256: `2a75536d6b26c3996d60113538a32562660aff5330c8bfcd8cd7bd0d7b61f75b`.
입력 파일별 hash와 적용 제외 ID/사유는 이 JSON 및 검증 산출물의 `geometry-summary.json`에 기록했다.

기존 지도/PF-preview JSON과 번들 manifest는 수정하지 않았다. API의 지도 응답에 geometry를 섞지 않는다.
표시 자료가 없거나 해시/지도 검증에 실패하면 기존 직선을 유지하며 PF 및 분석 입력을 변경하지 않는다.
선로 표시/선택/강조는 동일 모델 ID를 사용한다. KPG·한국 OSM에서는 이 자료를 요청하거나 적용하지 않는다.
런타임에는 원본 체크아웃·PyPSA·GIS 환경이 필요 없다.
표시 자료는 번들 밖의 선택적 EMS 자산이므로 코드만 다른 환경에 옮기면 GIS 경로는 자동 생성되지 않는다.

## 검증

- 런타임/API·몽골 PF/Security/Sensitivity·KPG/한국 지도 회귀: **107 passed**.
- GIS exporter: **3 passed**. 기존 GIS 환경의 cfgrib/ecCodes 및 numpy 바이너리 경고 2건은 있으나 검증은 통과했다.
- 실제 Chrome: **1 passed, 내부 23항목 전부 통과**.
  - 173/159/27, 141개 경로·18개 직선, 독립 조각 수 및 hit geometry 동일성.
  - 실제 마우스 클릭·휠 확대·드래그 이동, 선택·툴팁·분석 강조·전압 필터·PF 색상.
  - 감시 선로 표식이 GIS 경로 위에 위치.
  - 자료 404 시 159개 직선으로 복귀하고 PF 유지, 자료 복구 후 141개 재적용.
  - 늦게 도착한 몽골 geometry가 KPG에 적용되지 않음, 한국 OSM 전환 유지.
- 별도 Historical 번들 서버에서 변경 전후 **11개 API JSON 전체 동일**:
  - 몽골 지도·PF·runtime-status, KPG/한국 OSM 지도.
  - N-1 3사례, Sensitivity 3사례(각 최대45후보).
- 번들 런타임의 실제 수치 기준 **186항목 검증 상태 유지**.
- 작업 전 기록한 원본·기존 데이터/모델·두 승인 번들·`.env` 등 **370파일 SHA256 변경 0**.
- Historical/Stage3A 승인 manifest 및 포함 파일 hash 유지. 기존 서버는 건드리지 않고 직접 시작한 시험 서버만 종료했다.

초기 검증 도구의 KPG 응답 `grid_id` 가정, 한국 generic route 상태 코드 가정, CSS RGB/hex 문자열 비교를 바로잡고 최종 검사를 통과했다.
이 때문에 제품의 한국 API나 색상 계산을 변경하지 않았다.

## 실제 브라우저 전후 화면

별도 loopback 서버, 실제 Chrome 156, 한글 폰트, 동일 창 크기로 캡처했다.
변경 전은 `41c8689`의 UI 파일을 시험 서버에서 제공했다. 작업 트리의 UI를 되돌리지 않았다.
전국은 기본 viewBox, 울란바토르 20배, 서부·동부 5배이며 전후 같은 중심/축척을 사용했다.

| 화면 | 변경 전 | 변경 후 |
|---|---|---|
| 전국 | [전](../../outputs/mn_gis_display_20261030/before/national.png) | [후](../../outputs/mn_gis_display_20261030/after/national.png) |
| 울란바토르 | [전](../../outputs/mn_gis_display_20261030/before/ulaanbaatar.png) | [후](../../outputs/mn_gis_display_20261030/after/ulaanbaatar.png) |
| 서부 | [전](../../outputs/mn_gis_display_20261030/before/west.png) | [후](../../outputs/mn_gis_display_20261030/after/west.png) |
| 동부 | [전](../../outputs/mn_gis_display_20261030/before/east.png) | [후](../../outputs/mn_gis_display_20261030/after/east.png) |

[나란히 비교 HTML](../../outputs/mn_gis_display_20261030/comparison.html).
`outputs/mn_gis_display_20261030/`에 전후 API JSON, 브라우저 검사 JSON, JUnit 및 보존 확인 JSON도 저장했다.

## 재현 명령

GIS 자료 생성은 기존 파일을 덮어쓰지 않는다. 아래 목적지가 이미 있으면 새 이름으로 검토용 export를 하고 바이트를 비교한다.
런타임의 승인 SHA를 자동으로 바꾸지 않는다.

```bash
# EMS 루트; 기존 GIS Python만 사용, 설치/Pixi 동기화/모델 구축 없음.
MN_SOURCE="$(realpath ../mongolia-grid-poc)"
PROJ_DATA="$MN_SOURCE/pypsa-earth/.pixi/envs/default/share/proj" \
PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/mn-display-mpl \
"$MN_SOURCE/.venv-powsybl/bin/python" -B scripts/export_mn_display_geometry.py \
  --earth-checkout "$MN_SOURCE/pypsa-earth" \
  --length-audit "$MN_SOURCE/reports/model_refinement/line_length_changes.csv" \
  --map data/grids/mn-osm-35k-map.json \
  --output data/grids/mn-osm-35k-display-geometry.json

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q \
  tests/test_mn_display_geometry.py tests/test_grid_catalog.py \
  tests/test_mn35k_pf_api.py tests/test_mn35k_pf_preview.py \
  tests/test_mongolia_security.py tests/test_mongolia_sensitivity.py tests/test_korea_osm_map.py

AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME="$PWD/.cache/mn-stage1-chrome/chrome-linux64/chrome" \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q \
  tests/test_mn_display_geometry_browser.py --basetemp=/tmp/mn-display-browser-new
```
