# 한국 OSM 송전망 데이터 파이프라인

AI-EMS Agent에서 한국 계통 데이터를 만들고 검증하는 단계를 정리한다. 데이터 생성 파이프라인은 EMS 실행 코드(`web_app.py`, `src/ai_ems`)와 분리되어 있다. EMS는 이 패키지를 import하지 않는다. 한국 AC PF 준비 상태는 계속 `NOT_READY`이다.

## 단계

| 단계 | 위치 | 의존성 | 입력 → 출력 |
|---|---|---|---|
| 1. OSM 추출·지리 토폴로지 구축 | 외부 `korea-grid-poc` (`scripts/extract_osm.py`, `scripts/build_kr.py`, Snakefile) | pyosmium, shapely, PyPSA 1.2, Snakemake (독립 `.venv`) | Geofabrik PBF, WRI GPPD, geoBoundaries → `networks/KR/base.nc`, `outputs/map/kr_grid_preview.json`, `reports/*` |
| 2. 육지 경계 | `scripts/build_kr_land_boundary.py` | osmium, shapely, pyproj (오프라인 실행 시만 필요) | PBF → `data/grids/kr-land-boundary.json` |
| 3. 지도 가져오기 | `scripts/import_kr_osm_map.py` | AI-EMS 기본 환경 | 원본 지도 + 육지 경계 → `data/grids/kr-osm-transmission-map.json` |
| 4. 토폴로지 분석·검토 후보 | `pipelines/kr/` (이 패키지) | AI-EMS 기본 환경(numpy, h5py) | 원본 지도·`base.nc`·배포 지도·OSM 객체 → `outputs/kr_topology/` |

1단계는 무거운 의존성(PyPSA, Snakemake, pyosmium, 1.7 GB 가상환경)과 PyPSA-Earth 소스 스냅샷(508 MB)이 필요하다. 그래서 외부 작업 디렉터리에 두고 산출물만 읽기 전용 입력으로 쓴다. 1단계를 다시 실행하는 방법은 `korea-grid-poc/README.md`를 따른다.

## 토폴로지 분석 실행

```bash
cd ~/Projects/ai-ems-agent
.venv/bin/python -m pipelines.kr.analyze_topology --korea-project ../korea-grid-poc \
  --output-dir outputs/kr_topology
```

`--korea-project`는 아래 기본 경로를 채운다. 각 경로는 인자로 따로 지정할 수 있으며 `--map`만 필수다.

| 인자 | `korea-grid-poc` 기본 경로 | 용도 |
|---|---|---|
| `--map` | `outputs/map/kr_grid_preview.json` | 분석 기준 그래프와 OSM 변전소·변압기·HVDC 레이어 |
| `--network-nc` | `networks/KR/base.nc` | PyPSA 모델과 그래프 동일성 비교(h5py로 읽음) |
| `--osm-objects` | `data/processed/osm_power_objects.json` | 제외 선로의 OSM 노드 참조 |
| `--excluded` | `reports/excluded_objects.json` | 전처리 제외 사유 |
| `--topology-suspects` | `reports/topology_suspects.json` | 교차 쌍(연결로 보지 않음) |
| `--baseline-report` | `reports/kr_network_validation.json` | 기존 보고 연결 성분 수 |
| `--deployed-map` | AI-EMS `data/grids/kr-osm-transmission-map.json` | 배포 지도와 비교 |
| `--land-boundary` | AI-EMS `data/grids/kr-land-boundary.json` | 성분의 본토·섬 위치 |

검토 거리는 `--bus-distance-m`(모선 간, 기본 200 m)과 `--line-distance-m`(선로 끝점에서 다른 성분 경로까지, 기본 50 m)로 지정한다. 0보다 큰 유한한 숫자만 받는다(`NaN`, `Infinity`, 0, 음수는 거부). 이 값은 검토 범위일 뿐이며 접속 판단 기준이 아니다.

## 출력(`outputs/`, Git 제외)

- `summary.json`: 입력 SHA256, 그래프 정의, 통계, 입력 간 비교, 원인별 근거, 후보 집계, 가설적 민감도.
- `components.csv`: 성분별 전압, 크기, 길이, 끝점, 변전소, 섬 위치, 후보 유형, 검토 플래그.
- `connection_candidates.csv`: 양측 모선·OSM ID·성분·전압·좌표·태그, 거리, 근거, 의심 사유, 필요한 확인 정보. 모두 `status=unverified`이다.
  - `distance_target`이 `distance_m`의 측정 대상을 나타낸다.
    - `bus_to_bus`: a 모선과 b 모선 사이 거리.
    - `a_bus_to_line_route`: a 모선에서 `line_id` 경로 위 최근접점(`route_nearest_latitude/longitude`)까지 거리.
    - `not_applicable`: 거리 없음.
  - 끝점-경로 후보의 b 모선은 접근한 선로의 끝 모선(`b_bus_role=end_bus_of_approached_line`)이며 측정 지점이 아니다. a–b 모선 거리는 `a_to_b_bus_distance_m`에 있다.
  - 제외 선로가 여러 성분에 닿으면 모든 성분 쌍을 후보로 만들고, 닿는 성분 전체를 `excluded_way_components`에 적는다. 요약에는 제외 선로 수와 성분 쌍 후보 수를 따로 집계한다.
- `report.md`: 위 내용을 정리한 한국어 보고서.

같은 입력이면 출력이 바이트 단위로 같다. 시각 정보는 넣지 않는다.

## 원칙

- 입력 파일은 읽기만 하고 해시를 기록한다. 출력 경로가 입력과 같으면 거부한다.
- 그래프는 입력 그대로 쓴다. 거리 병합, 교차 연결, 전압 간 연결, 변압기 추정을 하지 않는다.
- 후보는 검토 목록이다. 모델에 반영하지 않는다. 가설적 성분 수는 "후보가 모두 실제 접속이라면"이라는 상한 민감도일 뿐이다.
- 중복 ID, 없는 모선 참조, 비유한 좌표, `base.nc`의 모선·선로별 배열 길이 불일치가 있으면 실패한다(종료 코드 2).
