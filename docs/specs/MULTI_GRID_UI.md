# 멀티 계통 공통 UI (Issue #5)

계통 선택만 바뀌고 화면 구성, 조작 흐름, 결과 형식은 같다. 물리계산과 API 계약은 변경하지 않았다.

## 흐름 (모든 계통 공통)

계통 선택 → 사고 선로 지정(입력·지도 클릭·몽골 대표 사고) → **Analyze** → 감시 선로(입력·후보 목록, 비우면 자동) → **Sensitivity Analysis**.

- 감시 선로 입력은 두 계통이 공유하며, 기존 API의 `monitored_line_id`로 전달한다.
- 비워 두면 각 API의 기존 자동 선택을 따른다. KPG는 최대 위반 선로, 몽골은 사고 후 |ΔP|가 가장 큰 메시 선로다.
- 계통에서 지원하지 않는 기능은 capability(`security_/sensitivity_analysis_available`, `generator_contingency_available`)로 비활성화하고 사유를 표시한다.

## 코드 구조 (`ui/index.html`)

| 단계 | 구성 |
|---|---|
| 요청 | `runSecurityAnalysis()`, `runSensitivityAnalysis()`: 계통별로 엔드포인트만 다르다. 요청 순번(`securityRequestSeq`, `sensitivityRequestSeq`), 계통 전환 토큰, 입력 변경 무효화는 그대로 유지한다 |
| 어댑터 | `kpgSecurityViewModel` / `mongoliaSecurityViewModel`, `kpgSensitivityViewModel` / `mongoliaSensitivityViewModel`: API 응답을 공통 View Model로 정규화한다. 응답에 없는 값은 행 자체를 만들지 않는다(`hasValue`, `detailSection`) |
| 진입점 | `applySecurityResult`·`applyMongoliaSecurityResult`·`applySensitivityResult`·`applyMongoliaSensitivityResult`: 어댑터를 호출하는 얇은 함수다(채팅 `ui_updates` 경로 호환) |
| 렌더러 | `renderResultCard(element, vm)` 하나로 그린다. Security는 Outage / Status / Violations, 위반 설비(Violated Equipment / Loading / Limit / Post-contingency)를, Sensitivity는 Target Line / Top Candidates, 후보(순위·발전기·Bus·민감도)를 표시한다. 상태 알림(계통 분리·미평가)과 짧은 데이터 고지가 이어지고, 진단 정보는 `<details class="result-details">`에 접어 둔다 |
| 지도 | `applySecurityOverlay` / `applySensitivityOverlay`: 사고 선로는 빨강, 위반 설비는 주황, 감시 선로는 분홍 점선과 중간점 링, 분리 모선·선로는 보라 점선, 민감도 후보는 청록 순위 사각형으로 그린다 |
| 범례 | `renderEquipmentLegend(data)`(좌하단): KPG는 모델 발전기, 몽골은 발전소 레코드를 보여 준다. `updateAnalysisLegend()`(좌상단)는 현재 지도에 있는 분석 표시만 나열한다. '분석 결과' 레이어를 끄면 함께 숨는다 |

## 결과 형식 예시

```text
KPG Security                      몽골 Security
Outage: LINE-16-28                Outage: 751261406-1_0 (10–25, 220 kV)
Status: CONVERGED                 Status: CONVERGED
Violations: 1                     Violations: 0
Violated Equipment: LINE-16-22    합성 시나리오 · 실제 몽골 운영 데이터가 아닙니다.
Loading: 103.21 %                 ▸ 상세 결과
Limit: 1906.00 MVA
Post-contingency: 1967.28 MVA
```

- 몽골 계통 분리 사고는 `Status: CONVERGED (주 계통만 수렴)` 아래에 `계통 분리: 모선 11개 · 합성 부하 1.80 MW · 합성 발전기 4대 · 분리 섬 PF 미실행` 한 줄을 표시하고, Sensitivity는 "미평가"로 비활성화한다.
- 비수렴은 `Violations: 판정 불가`로 표시하며 수치 행을 만들지 않는다.
- 몽골 상세 결과에 들어가는 항목은 다음과 같다.
  - 사고 후 주 계통 전압·조류 변화와 상위 목록
  - 계통 분리 목록
  - 스크리닝 기준과 모델 주의사항
  - Sensitivity의 감시 선로 선택·방사형 여부, 사고 전후 조류, 양단 MVA 부하율, 정의(함수·변수·단위·Slack), 평가 범위, 유한차분 검증
- KPG 응답에는 추가 진단 필드가 없으므로 상세 결과가 없다.

## 검증

```bash
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_browser_stale_responses.py
```

- `tests/test_ui_mongolia_chat.py`: 계통별 렌더러가 남아 있지 않은지, 어댑터가 공통 View Model과 렌더러를 거치는지, 감시 선로를 공유하는지, 범례 컴포넌트를 검사한다(정적).
- `tests/browser/unified_ui_probe.js`(Chrome, 실제 API): 다음을 확인한다.
  - KPG `LINE-16-28` 위반 1건과 몽골 `751261406-1_0` 위반 0건의 개조식 텍스트
  - 같은 분석 종류의 DOM 골격(행 필드·행 구성·후보 항목 구조)이 두 계통에서 동일한지
  - Sensitivity Top 5와 첫 후보
  - 지도 강조 색, 링, 순위 마커, 분석·설비 범례
  - 레이어 끄기, 계통 분리, 계통 전환
  - 스크린샷은 `outputs/browser_unified_ui/`에 저장한다.
- `tests/browser/stale_responses_probe.js`: 비동기 지연·오류 응답 무효화를 회귀 검사한다.
