# 몽골 110kV 송전선·변전소 참조 데이터

몽골 송전망의 OSM 참조 선로와 변전소 데이터 및 감사 기록을 보존한다. 현재 정책은 다음과 같다.

- 참조 데이터는 보존하되 사용자 화면에는 표시하지 않는다.
- 공식 설비임이 확인되기 전에는 기존 지도나 계산 모델에 통합하지 않는다.
- 참조 데이터를 향후 통합할지는 별도 작업에서 공식 계통자료와 대조·검증한 뒤 결정한다.

- 계산 모델은 변경하지 않았다. 대상은 166모선/156선로/27변압기 AC PF, XIIDM, Security·Sensitivity 로직, 기존 지도 JSON, PF-preview이다.
- 근거 자료는 `mongolia-grid-poc/docs/audits/2026-10-20/` 커버리지 감사와 PyPSA-Earth `MN_35k` raw OSM이다. OSM extract는 Geofabrik replication 2026-10-15T20:21Z (seq 4877)이다.
- 후보별 판정표: [`docs/verification/mn-reference-layers/`](../verification/mn-reference-layers/)

## 1. 결과 요약

| 구분 | 원본 후보 | 참조 레이어 표시 | 비고 |
|---|---:|---:|---|
| 110 kV 축약 구간 | 120 segment / **114 OSM way** | **33 선로** (원본 경로 합계 119.4 km) | 분할 segment는 way 단위로 1회만 표시 |
| OSM 변전소 객체 | **355** | **17 변전소** | 기존 지도 모선과 같은 위치인 71개는 데이터에만 보존하고 표시하지 않음 |

### 1.1 제외한 110 kV way (81개)

| 사유 | 개수 | 설명 |
|---|---:|---|
| `short_span_fragment` | 71 | 원본 way 경로가 0.5 km 미만이다. 대부분 수십 m 길이의 인입·구내 스팬 조각이며, 독립 송전 구간으로 볼 근거가 약하다. audit CSV의 수 km 길이는 build geometry의 가상 접속선이 포함된 값이다. |
| `overlaps_existing_map_line` | 6 | 경로의 80% 이상이 기존 지도 선로 build geometry의 150 m 이내에 있다. 동일 통로의 중복 매핑이다. |
| `contained_in_other_reference_line` | 2 | 경로의 80% 이상이 더 긴 참조선의 100 m 이내에 있다. 대표 선로의 `merged_osm_ids`에 ID를 보존했다(259140963→259140967, 484327331→751703429). |
| `demolished_or_disused_tag` | 1 | `1001268843`에 `demolished:power=line` 태그가 있다. |
| `way_partly_in_existing_map` | 1 | `801848651`은 같은 way의 다른 segment가 이미 지도에 있다. |

모든 way의 원본 geometry와 110 kV 전압 태그는 확인되었다. 직선 표현이 부적합한 경우(chord/경로 < 0.5)와 국외 끝점은 이번 데이터에서 0건이었다.

### 1.2 제외한 변전소 (338개)

| 사유 | 개수 | 설명 |
|---|---:|---|
| `same_location_as_map_bus` (비표시, 데이터 보존) | 71 | 가장 가까운 지도 모선이 1 km 이내에 있다. 그 모선 표식이 이미 같은 위치를 나타낸다. |
| `no_voltage_or_transmission_type_evidence` | 257 | 전압 태그가 없고 `substation=transmission`도 아니다. 유형은 태그 없음 227, transition 22, industrial 3, minor_distribution 2, yes 2, distribution 1이다. 배전용 변전소일 수 있어 임의로 보완하지 않았다. |
| `outside_country` | 9 | 비단순화 국가 형상(`MN_35k/shapes/country_shapes.geojson`) 밖에 있다. 9개 모두 남부·동부 국경 너머 중국측이다. |
| `below_35kV` | 1 | 10 kV 태그만 있다. |

### 1.3 지역별 개선

| 지역 (GADM) | 축약 110 kV way | 참조선 | 참조 변전소 | 비고 |
|---|---:|---:|---:|---|
| **Ulaanbaatar** | 64 | **19** (78.1 km) | **10** | 개선 효과가 가장 크다. 기존 지도는 19모선·110 kV branch 22개(감사 집계)가 Bus 1/2/32/33 부근에 겹쳐 있다. 참조 레이어는 병합 모선 주변의 실제 OSM 노선 방향과 변전소 위치(병합 모선에서 1.1–6.4 km)를 드러낸다. 이름 태그가 있는 변전소는 `Арцат дэд станц`, `ИХБ - 3` 두 곳이다. |
| Darhan-Uul | 7 | 3 | 1 | Bus 4 주변 way 3개(회선 태그 2·1·2) |
| Dornogovi | 6 | 5 | 1 | Bus 51(Sainshand 부근)과 Bus 106(Zamyn-Üüd 국경 부근) |
| Govisümber / Uvs / Hovd / Bulgan / Hentiy / Govi-Altay | 각 1–4 | 각 1 | Govisümber 1 | |
| Dornod | 0 | 0 | 2 | `1528332032`(110 kV)는 가장 가까운 지도 모선에서 104.8 km 떨어진 고립 변전소이다. |
| Dundgovi / Hövsgöl | 3 / 1 | 0 | 1 / 1 | |
| Ömnögovi 등 기타 6개 지역 | 14 | 0 | 0 | 모두 단거리 조각 |

전국 축척에서는 변화가 거의 보이지 않는다. 추가된 선로의 원본 경로가 0.7–14.6 km여서 1200 단위 viewBox에서 수 px에 불과하기 때문이다. 그래서 몽골 지도 전용 확대 기능을 함께 넣었다(§3.3).

## 2. 데이터 구조

- 생성: `scripts/build_mn_reference_layers.py` (표준 라이브러리만 사용, 읽기 전용 입력)
  ```bash
  .venv/bin/python scripts/build_mn_reference_layers.py --review-dir docs/verification/mn-reference-layers
  ```
- 출력: `data/grids/mn-osm-35k-reference-layers.json`. `data/grids/`는 기존 지도와 마찬가지로 git 추적 대상이 아니며, 재생성 결과는 결정적이다.
- 기존 `mn-osm-35k-map.json`은 수정하지 않는다. 참조 파일에 `base_map_sha256`(현재 지도의 SHA-256)을 기록한다. 지도가 바뀌면 로더가 참조 레이어만 거부(HTTP 422)하고, 기존 지도·PF·분석 기능은 그대로 동작한다. PF-preview의 `map_json_sha256` 검증은 그대로이다.
- 각 항목에는 다음을 보존한다.
  - 공통: `ref_id`(`REF-L-<osm>`, `REF-S-<osm>`), OSM ID·URL, 전압 태그, 출처, 중복 판정(`duplicate_check`), 가장 가까운 지도 모선과 거리, 지역
  - 선로: 축약 segment ID, 축약된 지도 모선(`collapsed_bus_ids`), 원본 경로 길이와 직선 길이, 회선 태그
  - 변전소: 유형 태그, 이름(OSM 태그가 있을 때만), 병합에 쓰인 계산 모선(`model_bus_ids`)
- 제외 항목도 `excluded.lines` / `excluded.substations`에 사유와 함께 모두 남긴다. 판정 규칙과 임계값은 파일의 `rules`에 기록한다.

### 2.1 선택 규칙

**110 kV 선로** (way 단위, 순서대로 적용)

1. 원본 geometry 존재
2. 전압 태그에 110000 포함
3. `demolished/disused/abandoned` 태그 없음
4. 같은 way가 이미 지도에 일부 표시되어 있지 않음
5. 원본 경로 ≥ 0.5 km
6. 직선 길이/경로 ≥ 0.5
7. 양 끝점이 국가 형상 안에 있음
8. 기존 지도 선로 통로(150 m)와 80% 미만으로 겹침
9. 더 긴 참조선 통로(100 m)와 80% 미만으로 겹침. 80% 이상 겹치면 그 참조선에 병합한다.

**변전소**

1. 전압 태그 ≥ 35 kV이거나 `substation=transmission`
2. 국가 형상 안에 있음
3. 지도 모선과 1 km 초과 떨어짐. 1 km 이내면 데이터에만 남기고 표시하지 않는다.
4. 다른 참조 변전소와 0.2 km 초과 떨어짐. 그 이내면 병합한다.

위치는 원본 OSM node 좌표 또는 polygon 중심이며, audit 계보 좌표와의 차이는 최대 0.0002°이다. 전압·이름이 없으면 비워 두고 채우지 않는다.

**접속 관계를 만들지 않는다.** 참조선은 원본 OSM way의 첫·끝 vertex를 잇는 직선이며, 모선이나 다른 설비에 연결하지 않는다.

## 3. 화면

참조 레이어 토글과 설비 표식은 사용자 화면에서 제거했다. 지도 로딩 시 참조 레이어 API도 자동 요청하지 않는다. 기존 지도에는 공식 지도 설비와 분석 결과만 표시한다. 이 UI 정책은 참조 데이터, 생성기, API, 검증 결과를 삭제하거나 변경하지 않는다.

### 3.1 몽골 전용 확대 (유지)

도심 비교를 위한 몽골 지도 전용 확대 기능은 유지한다.

- 마우스 휠로 커서 위치를 기준으로 최대 120배까지 확대하고, 드래그로 이동한다. `전체 보기` 버튼으로 원래 화면에 돌아간다.
- 확대한 상태에서도 선 굵기와 모선·변압기·발전소·분석 표식은 화면 크기를 유지한다(`vector-effect`, CSS `transform`).
- 초기 화면과 KPG는 기존과 동일하다. KPG에서는 휠 입력을 무시한다.

## 4. 분석 대상과의 격리

- 서버 측에서 Security API는 참조 ID와 OSM way 번호를 404로 거부하고, Sensitivity 감시 선로로는 422로 거부한다. 기존 검증 로직은 변경하지 않았다.
- 로더는 참조 ID가 지도 선로·모선·변압기 ID와 겹치거나 중복되면 거부한다.

## 5. API

`GET /api/grids/mn-osm-35k/reference-layers` → `{grid_id, reference_only: true, source, lines[], substations[]}`(표시 항목만). 파일이 없으면 404, 기준 지도가 다르거나 형식이 잘못되면 422를 반환한다.

## 6. 후속 권장

1. **별도 통합 검토**: 담당자가 `reference_*_review.csv`의 33개 선로와 17개 변전소를 공식 계통자료와 대조해 설비를 확인하고, 별도 작업에서 정식 통합 여부를 결정한다. 우선 대상은 울란바토르 19선·10변전소이다.
2. **국경 필터로 제외된 110 kV way 15개**(예: 서부 `216216504`, 국내 약 101 km)를 같은 구조의 별도 참조 레이어로 검토한다(감사 P1-4).
3. **실제 GIS 경로 표시**(감사 P1-1): 직선 대신 원본 경로를 표시하는 방안이다. 참조층에서 먼저 시험할 수 있다.
4. **전압 미상 변전소 257개**의 태그를 검증한다. 우선 transition 22개와 울란바토르 무태그 객체를 확인한다. 검증 전에는 표시하지 않는다.
5. 정식 지도에 반영하는 경우 지도 JSON 해시와 PF-preview `map_json_sha256` 관계를 새 버전으로 기록한다. 해석 모델 편입은 별도 과제(P3)이며 AC PF·N-1·Sensitivity 전 과정을 재검증해야 한다.
6. 기존 브라우저 테스트 중 고정 Chrome 프로필명을 쓰는 4개(`stale-probe`, `unified-probe`, `stale-shot-*`, `unified-shot-*`)는 이 환경에서 기존 코드 기준으로도 Chrome 실행에 실패한다. 테마 테스트처럼 프로필명에 시각을 붙이도록 정리할 것을 권장한다(§검증 기록 참고).
