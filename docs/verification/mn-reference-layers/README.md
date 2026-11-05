# 몽골 참조 레이어 검증 기록 (2026-10-20)

- 브랜치: `feature/mn-reference-layers`, 시작점은 `feature/theme-and-help`의 `b809c9c`
- 환경: WSL2, Python `.venv`, Windows Chrome headless (`--headless=new`), 창 크기 1600×1200
- 사양: [`docs/specs/MONGOLIA_REFERENCE_LAYERS.md`](../../specs/MONGOLIA_REFERENCE_LAYERS.md)

## 1. 파일

| 파일 | 내용 |
|---|---|
| `reference_lines_review.csv` | 축약된 110 kV OSM way 114개 전체의 판정(`reference_line` / `excluded`), 사유, 경로·직선 길이, 축약 모선, 관련 ID, OSM 링크 |
| `reference_substations_review.csv` | OSM 변전소 객체 355개 전체의 판정(`reference_substation` / `hidden_same_site_as_map_bus` / `excluded`), 사유, 전압·유형·이름 태그, 좌표, 가장 가까운 지도 모선 |
| `browser_results.json` | `tests/browser/reference_layers_probe.js` 결과 |
| `screenshots/*.png` | 레이어 OFF/ON 비교 화면 |

두 CSV는 빌더(`--review-dir`)가 생성하며, 정식 지도 반영 여부를 결정할 때 검토 목록으로 쓴다.

## 2. 테스트 결과

| 실행 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q tests` | **299 passed, 9 skipped**. 기준선 280 passed에 신규 19개가 더해졌다. skip 9건은 opt-in 브라우저 테스트다. |
| 신규 `tests/test_mn_reference_layers.py` | **19 passed** |
| 참조 레이어 브라우저 probe `reference_layers_probe.js` | **36/36 통과**, 스크린샷 10장 |
| 테마·도움말 `theme_help_probe.js` | **101/101 통과** |
| 지연·오류 응답 `stale_responses_probe.js` | **39/39 통과** (새 Chrome 프로필로 실행) |
| 공통 UI `unified_ui_probe.js` | **44/44 통과** (새 Chrome 프로필로 실행) |

**환경 이슈(기존 문제)**: `test_stale_responses_are_never_applied`, `test_screenshots_of_end_states`, `test_unified_ui_*` 4개는 고정 Chrome 프로필명(`stale-probe` 등)을 쓴다. 이 환경에서는 Chrome이 출력 없이 종료되어 실패한다. 변경 전 코드(`git stash`)에서도 같은 4개가 똑같이 실패했다. 프로필명에 시각만 붙이는 임시 래퍼로 같은 테스트 함수를 실행한 결과는 위 표와 같이 모두 통과했다. 저장소의 테스트 코드는 수정하지 않았다.

### 2.1 요구 항목별 확인

| 요구 | 확인 방법 | 결과 |
|---|---|---|
| 1. OFF 시 기존 지도와 동일 | 토글 ON→OFF 후 SVG `innerHTML` 비교, 173모선/159선로 수, 계통 재선택 시 기본 OFF | 동일 |
| 2. ON 시 추가 설비 표시 | 선로 33개(`<line>` 점선), 변전소 17개(`<rect>`) | 표시됨 |
| 3. 중복 과다 표시 없음 | 지도 모선 1 km 이내 변전소 0(최소 1.08 km), 변전소 간 최소 0.35 km, 모델 선로와 겹치는 직선 0, 참조선 간 겹침 0 | 통과 |
| 4. N-1·Sensitivity 대상 불가 | 참조선 클릭 시 사고 입력·Security 패널 불변. API에서 사고 404, 감시 선로 422. 분석 실행 후 강조와 감시 후보에 `REF-` 없음 | 통과 |
| 5. KPG 영향 없음 | KPG에서 토글 숨김·요소 0·휠 확대 없음, 전환 후 복귀 확인. KPG 단위·브라우저 회귀 통과 | 통과 |
| 6. Light/Dark | Light `#2563eb`, Dark `#60a5fa`, 변전소 채움색은 테마 표면색. 테마 전환 후 토글 상태 유지 | 통과 |
| 7. AC PF·Security·Sensitivity 회귀 | `test_mongolia_security`, `test_mongolia_sensitivity`, `test_mn35k_*`, KPG 테스트 포함 전체 통과 | 통과 |
| 8. 모델·결과 해시 유지 | 아래 §3 | 유지 |

## 3. 기존 모델·결과 보존

작업 시작 전에 다음 파일의 SHA-256을 기록했고, 종료 시 다시 비교했다.

- `ai-ems-agent`의 추적 파일
- MN_35k / MN_35k_poc NetCDF 4개, XIIDM, `reports/pypowsybl_conversion/*`
- `mongolia-grid-poc`의 추적 파일 182개

- 변경된 추적 파일은 의도한 3개(`ui/index.html`, `web_app.py`, `tests/test_browser_stale_responses.py`)뿐이다. 나머지는 신규 파일이다.
- `mongolia-grid-poc`: 추적 파일 182개 모두 동일하다. 커밋·브랜치 변경은 없다.
- 166모선 모델 `synthetic_reactive_support_base.nc`: `21bd0d90…88c4`로, 감사 기록과 같다.
- XIIDM `mn35k_synthetic_powsybl.xiidm`: `f639b6d4…6650`, 변경 없음
- 지도 `mn-osm-35k-map.json`: `f530d88e…77ba`. PF-preview `metadata.map_json_sha256`과 일치하며, 참조 파일의 `base_map_sha256`에도 고정했다.
- PF-preview `mn35k-synthetic-pf-preview.json`: `44b6f456…ad06`, 변경 없음

## 4. 스크린샷

| 파일 | 상태 |
|---|---|
| `ref_off.png` / `ref_on.png` | 전국, 레이어 OFF / ON (Light) |
| `ub_off.png` / `ub_on.png` | 울란바토르 확대, OFF / ON |
| `ub_tooltip.png` | 참조선 클릭 시 툴팁. 사고 입력은 비어 있다. |
| `ub_analysis_on.png` | 레이어 ON 상태의 N-1(237060334-1_0) 및 Sensitivity 결과. 사고선과 후보 표식이 참조 표현과 구분된다. |
| `darkhan_off.png` / `darkhan_on.png` | 다르항 확대, OFF / ON |
| `national_on_dark.png` / `ub_on_dark.png` | Dark 테마 |
