# PH4-02-DEMO-01 — AI EMS 콘솔 시연 점검 및 화면 리뷰

## 결론

- 기준 커밋 `ad2c1f800533d17c4851d7ce1986b76886630dbe`에서 별도 `feature/ph4-02-demo-readiness` worktree로 확인했다.
- C-2c opt-in Chrome 시나리오 **13 passed (37.45s)**. 실제 FastAPI, registry, KPG 모델/PyPowSyBl 경로를 사용하고 LLM만 `ScriptedModel`로 대체했다.
- 운영 콘솔은 현재 run capability를 광고하지 않아 AI Run이 노출되지 않는다. AI Run 화면은 테스트 fixture 전용이며, 이 검증을 실제 LLM 시연으로 설명하면 안 된다.
- 실제 Ollama/LLM 호출, 실제 EMS 계통 검증, 몽골 물리해석은 실행하지 않았다.

## 점검 범위와 결과

| 항목 | 확인 결과 | 확인 방식 / 한계 |
|---|---|---|
| KPG-193 지도와 설비 선택 | 확인 | KPG 시험 모델과 `LINE-16-28` 선택 상태를 화면에서 확인했다. 운영망 데이터가 아닌 KPG/local MATPOWER 시험계통이다. |
| 수동 Security와 AI Run Security | 부분 확인 | fixture가 같은 사고에 대해 수동 결과와 run 결과의 반영 일치를 검사한다. 별도 라이브 운영 서버 세션은 실행하지 않았다. |
| Sensitivity | 부분 확인 | AI Run Sensitivity 시나리오에서 화면 N-1 결과를 입력으로 사용하고 결과·대상 일치를 검사했다. 수동 Sensitivity의 독립 화면 시나리오는 이번 캡처에서 따로 수행하지 않았다. |
| 연쇄 분석·결과 반영 | 확인 | Security와 Sensitivity 결과가 함께 반영되는 경로, 지도 overlay·패널의 대상 일치, 결과 상태 처리를 fixture에서 확인했다. |
| Receipt와 기존 결과 설명(Reference) | 기능 경로 확인 | `run_chain` JSON에서 실제 reference 요청이 `referenced`로 완료되고 저장 결과 설명이 반환된 것을 확인했다. Reference는 새 계산 없이 저장된 결과를 설명한다. 아래 연쇄 캡처는 최종 reference 화면이 아니라 시나리오 종료 시점의 분석 화면이다. |
| 결과 종류와 AI 설명 구분 | 확인 | Security/Sensitivity 제목과 결과 상태는 분석 패널에, AI 설명은 채팅 영역에 표시되는 구조를 확인했다. headless 화면은 한글 폰트가 없어 문구의 시각적 가독성은 제한된다. |
| 실패·미실행·평가 불가·잠금 | 기능 경로 확인 | 자동 시나리오에서 결과/receipt/revision 보존, 미지원 상태, 지연·잠금 경계를 검사했다. 모든 상태의 화면을 별도 캡처한 것은 아니다. |
| 몽골 `mn-osm-35k` | 지도·제한 확인 | 합성 OSM 지도와 PF preview 표시를 확인했다. 이번 검증에서 MN 계산·AI Run·Reference는 실행하지 않았다. |
| 한국 `kr-osm-transmission` | 지도·제한 확인 | 지도 전용임을 확인했다. PF, N-1, Sensitivity, AI chat/run은 지원되지 않으며 계산/API를 실행하지 않는다. |
| 실제 LLM 및 운영 경로 | 미확인 | Ollama 호출, Tailscale HTTPS/Caddy 인증 경로, 실제 GPU 모델 적재, 운영 capability 공개를 수행하지 않았다. |

### 검증 방법과 재실행

C-2c fixture는 페이지별 KPG 시험 모델에서 실제 FastAPI·registry·PyPowSyBl 경로를 사용하고 LLM만 B3-2 `ScriptedModel`로 고정한다. fixture가 외부 브라우저 네트워크를 차단하며, 테스트는 미고정 LLM run이 시작되지 않았는지도 확인한다. 테스트 자체가 브라우저 시나리오를 자동화하므로, 사람이 운영 환경에서 자유 질의를 수행한 리허설과는 다르다.

```bash
AI_EMS_BROWSER_TESTS=1 \
AI_EMS_CHROME=/path/to/chrome \
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_console_run_browser.py
```

실행 환경에는 프로젝트 Python 의존성, 지원되는 Java/PyPowSyBl 런타임, KPG 모델 데이터, Chrome/Chromium이 필요하다. Chrome 실행 파일은 `AI_EMS_CHROME`으로 지정한다. fixture 서버는 테스트가 loopback에 시작·종료하므로 별도 서버를 띄우지 않는다.

- 이번 실행: **13 passed (37.45s)**, 실제 Ollama 미호출.
- pytest 로그: [console-run-c2c-pytest.log](../../.local/demo-review/console-run-c2c-pytest.log)
- 브라우저 JSON/PNG 증거: [logs](../../.local/browser-tests/logs/console_run_c2c/7ae88b7ea050/), [screenshots](../../.local/browser-tests/screenshots/console_run_c2c/7ae88b7ea050/)
- 캡처는 로컬 전용 [screenshots](../../.local/demo-review/screenshots/)에 보존했다. `.local/`은 Git에 추가하지 않는다.

## 주요 화면 캡처

| 화면 | 캡처 | 주의 |
|---|---|---|
| KPG AI Run Security | [kpg-security-run.png](../../.local/demo-review/screenshots/kpg-security-run.png) | 시험계통 결과이며 운영 결과가 아니다. |
| KPG AI Run Sensitivity | [kpg-sensitivity-run.png](../../.local/demo-review/screenshots/kpg-sensitivity-run.png) | fixture가 광고한 run 모드에서 촬영했다. |
| 연쇄 시나리오 종료 화면 | [kpg-chain-result.png](../../.local/demo-review/screenshots/kpg-chain-result.png) | Reference 응답의 별도 스크린샷이 아니다. reference 완료는 `run_chain.json`으로 확인한다. |
| 결과 상태 시나리오 | [kpg-run-outcomes.png](../../.local/demo-review/screenshots/kpg-run-outcomes.png) | 자동 테스트 시나리오 종료 화면이다. |
| run 미광고 상태 | [kpg-no-run-advertisement.png](../../.local/demo-review/screenshots/kpg-no-run-advertisement.png) | 현재 운영 capability 미광고 상태를 재현하는 fixture 화면이다. |
| 몽골 OSM | [mongolia-grid.png](../../.local/demo-review/screenshots/mongolia-grid.png) | 지도·표시 범위만 확인했다. |
| 한국 OSM 제한 | [korea-osm-limit.png](../../.local/demo-review/screenshots/korea-osm-limit.png) | 지도 전용 및 분석 비활성 표시. |

캡처 환경에는 한국어 폰트가 없어 일부 한글이 네모/깨진 글리프로 보인다. 이는 캡처 환경의 제한으로, 제품의 한글 렌더링 결함이라고 단정하지 않는다. 한글 UI 문구는 DOM/테스트 검증으로 확인했다.

## UI/UX 리뷰와 개선 우선순위

이번 범위에서 확인된 시연 위험과 개선 후보는 다음과 같다. 기능 결함으로 확인되지 않은 항목은 제안으로 구분한다.

| 우선순위 | 관찰 | 재현 방법 / 영향 | 제안 |
|---|---|---|---|
| 높음 — 시연 차단 | 운영 콘솔은 `console_analysis_run_available`을 받지 않아 AI Run이 노출되지 않는다. | 실제 운영 카탈로그/콘솔을 열면 run 선택지가 없다. fixture에서만 광고를 강제해 run 화면을 확인할 수 있다. | C-3의 capability 승인·서버 광고와 별도 실제 LLM 검증 전에는 라이브 AI Run 시연을 약속하지 않는다. |
| 중간 | 분석 결과 패널과 채팅 결과가 각각 스크롤되어 긴 연쇄 결과에서 정보가 분절된다. | 1600×1100 화면에서 연쇄 결과와 긴 AI 답변을 열고 두 패널을 각각 스크롤한다. 지도·결과·설명 사이의 맥락 전환이 필요하다. | 결과 요약/상세 접기와 출처·대상 고정 표시를 검토한다. 이번에는 UI를 변경하지 않았다. |
| 중간 — 시연 주의 | KPG-193은 시험계통이며 운영계통이 아니다. 화면상 모델 제한 표기가 있어도 시연 중 실운영 결과로 오인될 수 있다. | KPG-193을 선택하고 PF/N-1 결과를 보여준다. | 시연 시작과 결과 설명에서 “KPG-193 합성 시험계통”임을 구두로 고지한다. |
| 낮음 | 결과가 길거나 작은 표시 영역에서는 상세 정보가 접혀 보인다. | 작은 창 또는 다수의 위반/후보가 있는 결과에서 분석 패널을 스크롤한다. | 첫 화면에 상태·사고 선로·주요 위반·출처를 요약하고 세부 항목은 펼치도록 검토한다. |
| 낮음 — 환경 한계 | 현재 headless 캡처에서 한글 글리프가 깨진다. | 이 worktree의 Chrome 캡처 파일을 연다. | 화면 시연/최종 캡처는 한국어 폰트가 설치된 브라우저 환경에서 다시 확인한다. 제품 수정으로 간주하지 않는다. |

## 5–10분 시연 동선 (약 9분)

현재 capability 제약을 반영해 AI Run/Reference 단계는 **Scripted LLM fixture 검증 화면**으로 제시한다. 이 단계가 실제 운영 콘솔에서 실행되는 것처럼 설명하지 않는다.

1. **KPG-193 선택 및 지도 확인 (40초)** — 시험계통·모델 제한을 먼저 고지한다.
2. **사고 선로 선택 (40초)** — `LINE-16-28`을 선택하고 선택 선로/카드가 일치하는지 보여준다.
3. **수동 N-1 Security (1분 15초)** — PF 수렴 상태, 위반 설비와 사고 선로를 확인한다.
4. **Sensitivity (1분 15초)** — 감시 선로와 결과가 N-1 문맥을 어떻게 사용하는지 설명한다. 민감도는 국부 선형 지표이지 재급전 권고/최적해가 아님을 밝힌다.
5. **Scripted AI Run 연쇄 분석 (1분 30초)** — 테스트 fixture에서만 가능한 run 화면을 보여주고 LLM 답변과 서버 계산 결과가 별도임을 설명한다.
6. **지도·패널·receipt 대조 (50초)** — 서버 검증된 결과의 사고 선로·상태·위반과 지도 표시가 같은지 확인한다.
7. **Reference로 기존 결과 설명 (1분)** — fixture가 저장된 receipt를 참조하며 새 계산은 하지 않는 경로를 설명한다. 실제 LLM 응답으로 소개하지 않는다.
8. **몽골/한국 OSM 범위 비교 (1분 15초)** — 몽골은 합성 지도/PF preview, 한국 OSM은 지도 전용임을 보여준다.
9. **정리와 질의 (35초)** — 실제 운영 LLM·운전계통 검증이 아직 시연 범위 밖임을 다시 고지한다.

## 향후 실제 LLM 시연 전 조건

- 별도 승인된 작업에서 C-3 운영 capability 조건과 서버의 명시적 광고를 구현·검증한다. 현재 필드 미광고는 의도된 gate다.
- 인증된 Tailscale HTTPS 8443 → Caddy → Ollama 경로, TLS/인증 요구와 모델 준비 상태를 운영자가 확인한다. 이 문서에서 URL·인증 값을 추정하거나 기록하지 않는다.
- 제한된 KPG 시험 환경에서 실제 LLM smoke 및 실행 예산/단일 worker 조건을 확인한다. 실제 LLM 출력과 scripted 출력은 자료·화면·발표에서 분리한다.
- 실제 모델 응답이 선택한 Tool/인자와 server receipt를 준수하는지, 첫 AC·timeout·실패·모델 변경을 포함한 run/브라우저 회귀를 확인한다.
- 스크린샷/로그에 인증 정보나 민감한 운전 데이터를 포함하지 않도록 검토한다.
- 합성 KPG/MN 모델의 계산 결과를 실제 계통 결과나 보장된 최적해로 표현하지 않는다.

## 변경 및 제외 범위

이번 작업은 시연 보고서와 [개발 상태](../dev/STATE.md)만 갱신했다. UI/JS/CSS, 서버, 계통 데이터, 테스트 코드/단언은 변경하지 않았다. 실제 LLM 실행 및 C-3 착수는 하지 않았다. 캡처 파일과 실행 로그는 로컬 `.local/`에만 보존한다.
