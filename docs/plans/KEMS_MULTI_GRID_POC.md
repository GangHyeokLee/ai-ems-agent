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
  --country-shapes resources/MN/shapes/country_shapes.geojson
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
