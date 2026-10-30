# PR #7 최종 검증 기록 (Issue #5 공통 UI)

- 대상: `feature/multi-grid-unified-ui`. 앱 코드 기준 `6f5a124`이며, 이 검증 커밋은 테스트와 기록만 추가한다.
- 환경: WSL2, Python `.venv`, PyPowSyBl 1.16.1, Windows Chrome headless (`--headless=new`)
- 결과: **실패 0건**. 앱(`ui/`, `src/`, `web_app.py`) 수정 사항 없음.

## 1. pytest

| 실행 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q tests` | **273 passed, 4 skipped** (skip 4건은 opt-in 브라우저 테스트) |
| `AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests` (2회 연속) | **277 passed, 0 failed** / **277 passed, 0 failed** |

## 2. Chrome headless

| 테스트 | 체크 | 결과 JSON |
|---|---|---|
| 공통 UI 비교 `tests/browser/unified_ui_probe.js` | **44/44 통과** | `unified_ui_results.json` |
| 지연·오류 응답 무효화 `tests/browser/stale_responses_probe.js` | **39/39 통과** | `stale_responses_results.json` |

### KPG·몽골 화면 구조 비교 (실제 API)

| | KPG | 몽골 |
|---|---|---|
| Security | Outage: LINE-16-28 / Status: CONVERGED / Violations: 1 / Violated Equipment: LINE-16-22 / Loading: 103.21 % / Limit: 1906.00 MVA / Post-contingency: 1967.28 MVA | Outage: 751261406-1_0 (10–25, 220 kV) / Status: CONVERGED / Violations: 0 |
| Sensitivity | Target Line: LINE-16-22 / Top Candidates: 5 / 1. GEN-19#0 · Bus 19 · 마포용산 · 민감도: 0.4211 | Target Line: 484276912-1_0 (13–59, 220 kV) / Top Candidates: 5 / 1. SYNTHETIC_PV_7 · Bus 7 · 110 kV · 민감도: -1.0074 |

- 같은 분석 종류는 두 계통에서 행 필드, 행 구성, 후보 항목 DOM 골격이 같다(체크로 검증).
- 몽골
  - 상세 결과가 접힌 상태로 제공되며, 기본 본문에는 진단 수치가 없다.
  - 합성 데이터 고지가 보인다.
  - 계통 분리 사고는 `Status: CONVERGED (주 계통만 수렴)`과 분리 요약 한 줄로 표시하고, Sensitivity는 "미평가"로 비활성화한다.
- 지도
  - 사고 선로는 빨강, 위반 설비는 주황, 감시 선로는 분홍 점선과 링, 민감도 후보는 청록 순위 마커, 분리 모선은 보라로 표시된다.
  - 분석 범례는 현재 표시만 나열한다.
  - 설비 범례는 KPG 발전기 201대, 몽골 발전소 레코드 27건을 표시한다.
  - 레이어를 끄면 모두 숨는다.
  - 계통을 전환하면 결과·오버레이·범례가 초기화된다.

### KPG 기존 기능 회귀

- 발전기 사고: 패널에서 `GEN-124#1` 실행 시 결과가 렌더링된다(지연 응답 probe).
- 채팅 → UI 연계 (`tests/test_chat_ui_updates.py`와 브라우저 체크 11건)
  - LLM 서버가 HTTP 502로 응답하지 않아, LLM의 도구 선택만 스크립트로 대체했다.
  - 실제 KPG Agent Tool(`line_contingency`, `generator_sensitivity`, `generator_contingency_analysis`, `contingency_response_analysis`)의 출력을 실제 `/api/chat` 코드(`_extract_ui_updates`)로 변환했다.
  - `ui_updates`가 REST API 결과와 일치한다.
  - 브라우저에서 채팅 결과가 공통 결과 카드와 지도 오버레이에 반영된다.
  - 발전기 사고 결과는 마커와 범례에 반영된다.
- Redispatch: 대응방안 워크플로우 답변이 deterministic formatter 출력과 같고 채팅에 렌더링된다. 기존 Redispatch 단위 테스트도 통과한다.

## 3. 스크린샷 (로컬 `outputs/`, Git 제외)

- `outputs/browser_unified_ui/`
  - KPG: `kpg_security.png`, `kpg_sensitivity.png`, `kpg_chat.png`
  - 몽골 Security: `mn_security.png`(상세 접힘), `mn_security_details.png`(상세 펼침)
  - 몽골 Sensitivity: `mn_sensitivity.png`(접힘), `mn_sensitivity_details.png`(펼침)
  - 몽골 계통 분리: `mn_island.png`
- `outputs/browser_stale_responses/`: `mn_security.png`, `mn_sensitivity_monitored.png`, `kpg_sensitivity.png`, `switch_kpg_to_mn.png`
- KPG 결과에는 상세 결과가 없다. KPG API 응답에 추가 진단 필드가 없으며, 값을 만들지 않는다.

## 4. 미검증·한계

- 실제 LLM 대화: LLM 서버가 HTTP 502를 반환해 실행하지 못했다. 채팅 연계는 실제 Tool 출력과 실제 endpoint 코드로 검증했고, LLM 응답 품질은 검증하지 않았다.
- Issue #6(다크 모드)은 범위 외다.

## 재현

```bash
cd ~/Projects/ai-ems-agent
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
```
