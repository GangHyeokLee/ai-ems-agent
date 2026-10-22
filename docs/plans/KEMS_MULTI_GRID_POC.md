# K-EMS 국가별 계통 선택 PoC (1차)

## 목적

기존 AI-EMS Agent에 국가/계통을 선택하는 화면을 추가하고, 실제 운전정보가 없는 공개 OSM 계통은 **지도 미리보기 전용**으로 표시한다. 국가 선택이 물리해석 엔진의 자동 전환을 의미하지 않는다.

## 구성

- `kr-kpg193`: 기존 KPG-193 테스트 계통. 기존 PyPowSyBl 분석/Agent 유지.
- `mn-osm`: PyPSA-Earth로 만든 몽골 기본 송전망 지도. **AC Power Flow, N-1, LLM 분석 불가** (운전조건 및 계통해석 검증 필요).
- `GET /api/grids`: 계통 목록과 지도/분석 지원 상태.
- `GET /api/grids/{grid_id}/map`: KPG 지도 또는 몽골 JSON 지도. 몽골 파일이 없으면 404.
- 이전 `/api/network-map` 및 KPG 분석 API는 호환성을 위해 유지. 현재 직접 분석 API는 KPG 전용이므로 국가 공통 API가 아님.

## 몽골 지도 데이터 준비

몽골 PyPSA-Earth 생성이 성공한 다음 두 개발환경을 혼동하지 말 것.

```bash
cd ~/Projects/mongolia-grid-poc/pypsa-earth
# PyPSA-Earth Pixi 환경을 이용해 NetCDF에서 지도 좌표/연결정보만 추출
pixi run python ~/Projects/ai-ems-agent/scripts/export_pypsa_map.py \
  networks/MN/base.nc \
  ~/Projects/ai-ems-agent/data/grids/mn-osm-map.json \
  --powerplants resources/MN/powerplants.csv \
  --country-shapes resources/MN/shapes/country_shapes.geojson \
  --admin-shapes resources/MN/shapes/gadm_shapes.geojson
```

이 명령은 `base.nc`가 실제 생성된 뒤에만 실행할 수 있다. `mn-osm-map.json`은 몽골 국경 GeoJSON 윤곽선, 모선 좌표·선로 연결과 공개 발전소 **데이터 행**(이름, 발전원, 용량, 좌표, 추정 매핑 모선)을 담는다. 27개 행은 물리적인 발전소 27곳을 의미하지 않는다. 동일 좌표/이름이 반복되면 지도 마커 하나에서 레코드를 모두 보여준다. 전기적 파라미터, 실제 발전기·부하 운전 상태, 지리적 선로 곡선은 포함하지 않는다. 공개 데이터 총 설비용량 합계를 실제 국가 총용량이라고 주장하면 안 된다.

`ai-ems-agent` 서버 재시작 후 국가 선택에서 **몽골 OSM 기반 송전망**을 선택하면 지도 조회 가능 상태로 바뀐다.

대용량 계통 원본과 생성물은 Git에 올리지 않는다. 실사용 PSS/E RAW 또는 PyPSA NetCDF를 Agent 분석엔진으로 넘기는 작업은 별도 후속 개발이다.

## 검증

```bash
cd ~/Projects/ai-ems-agent
source .venv/bin/activate
python -m pytest -q tests/test_grid_catalog.py tests/test_export_pypsa_map.py
python -m pytest -q
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

점검할 항목:
- KPG 선택 시 기존 지도 및 분석 기능 동작
- 몽골 지도 파일이 없을 때 `준비 중` 상태, 빈 지도, 분석 버튼 비활성화
- 몽골 지도 파일 생성 후 모선/선로/발전소 데이터 마커와 발전원 범례 렌더링
- 동일 좌표 레코드 묶음 팝업에서 각 데이터 행과 용량 및 추정 매핑 모선 표시
- 몽골 선택 시 KPG 상정사고·Agent 분석이 실행되지 않는지 확인
- KPG로 재선택하면 지도/분석 기능 복원
- 향후 전환 API는 모델 선택값과 Tool 실행 컨텍스트를 서버에서도 강제 검증해야 함

## 다음 구현 단계

1. 몽골 계통의 전기적 데이터와 발전기/부하 보완
2. PyPSA 또는 PyPowSyBl에서 AC Power Flow 수렴 확인
3. 모델 ID로 격리된 서버측 실행 컨텍스트와 Tool Binding
4. 분석 가능 여부 판정 후 몽골 Power Flow 및 결과 화면 활성화

## 지도 표시 (몽골)

- 국경 및 GADM 행정구역 폴리곤을 같은 좌표계로 시각화하고, 경계 레이어를 끌 수 있다.
- 몽골은 가로형 지도(1200×650)를 사용하고, 기존 KPG 화면은 변경하지 않는다.
- 공개 발전소 점을 선택하면 마커 테두리가 강조되고 레코드 세부정보가 툴팁으로 표시된다. 이것은 운전용 발전기 선택/제어가 아니다.
- GADM 파일은 해당 배포본의 실제 속성/행정단위를 확인해야 한다. 개발 코드는 하위 행정구역 FeatureCollection을 기대하고, 1개 국가 전체 폴리곤이면 오류를 낸다.
- `base.nc` 및 `powerplants.csv`는 다시 다운로드하거나 Snakemake 실행할 필요 없이 JSON 변환만 재실행한다.

## KPG-193 한국 지도 배경 및 공통 마커

KPG-193은 **합성 한국 시험계통**이며 실제 계통 설비의 지리적 좌표를 보증하지 않는다.
한국 배경은 geoBoundaries gbOpen KOR ADM0(국경), ADM1(시도 경계)를 기반으로 생성하고,
출처 및 CC BY 4.0 라이선스 표기를 유지한다.

```bash
cd ~/Projects/ai-ems-agent
source .venv/bin/activate
python scripts/prepare_kpg_geography.py
```

생성 결과: `data/grids/kr-geography.json` (Git에 커밋하지 않음).
외부 geoBoundaries 서버의 연결 또는 데이터 변경에 따라 오류가 나면
경계를 표시하지 않고 기존 KPG 계통 지도를 그대로 유지한다.

공통 레이어 체크박스 **발전기 / 발전소**는 KPG에서는 실제 PyPowSyBl
Generator 레코드의 녹색 원형 마커, 몽골에서는 공개 발전소 자료의
발전원별 색상 원형 마커를 제어한다. 몽골 마커는 계통 운전용 발전기와 다르며
분석 기능을 활성화하지 않는다.

개발 및 배포에서는 geoBoundaries의 자료 출처 및 라이선스 조건을 확인하고 표기해야 한다.
