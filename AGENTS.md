# AI-EMS 공통 개발 규칙

## 프로젝트

자연어 요청을 전력계통 해석 도구에 연결하는 AI-EMS Agent PoC다.
주요 기술은 Python 3.11, FastAPI, LangGraph, 로컬 LLM, PyPowSyBl,
JavaScript 기반 웹 UI다. 상세 구조와 실행 방법은 `README.md`를 참고한다.

## 작업 시작

- 먼저 `docs/dev/STATE.md`에서 현재 작업, 기준 커밋, 검증 범위와 다음 작업을 확인한다.
- 현재 브랜치·HEAD와 staged / unstaged / untracked 변경을 확인한다.
- 기존 변경, 코드, 모델, 데이터와 검증 기록을 보존한다. 다른 작업자의 변경을 덮어쓰지 않는다.
- 요청 범위에 필요한 최소 변경만 수행한다. 후속 Phase를 임의로 시작하지 않는다.

## 계통과 해석 경계

- `kr-kpg193`: KPG-193의 AC PF, N-1, Sensitivity, 발전기 사고, AI capability를 유지한다.
- `mn-osm-35k`: 몽골 합성 AC PF, 선로 N-1, Sensitivity, 별도 AI 도구의 경계를 유지한다.
- `kr-osm-transmission`: 한국 OSM은 지도 전용이다. 계산·AI capability를 임의로 활성화하지 않는다.
- 계통별 API·모델·결과를 혼용하지 않는다. 콘솔 데모 계산을 실제 EMS 해석으로 표시하지 않는다.
- AI는 의도 해석, 도구 선택, 결과 설명을 담당한다. 실제 물리해석은 PyPowSyBl 등
  해석 엔진이 수행하며, 제어 효과는 AC 조류계산·안전도 해석으로 검증한다.
- 합성 모델·추정값·검토 후보를 실제 운전값이나 보장된 최적해로 설명하지 않는다.

## 변경과 검증

- 테스트를 삭제하거나 assertion·검증 조건을 완화하지 않는다. 실패를 성공이나 skip으로 숨기지 않는다.
- 변경 범위에 맞는 검증을 수행하고 실행 명령, 결과, 미검증 범위를 구분해 기록한다.
- Stage 1·Stage 3C·Phase 2·Phase 3 검증 기록과 해시 계약을 보존한다.
  과거 계약과 현재 코드의 차이를 없애려고 기준 해시를 덮어쓰지 않는다.
- 변이 테스트는 가능하면 `/tmp` 등 격리된 복사본에서 수행한다.
  기존 작업 트리·검증 증거를 변이 대상으로 사용하지 않는다.
- Git commit / push / branch 변경은 사용자 승인 후 수행한다.
  `git restore`, `git reset`, `git clean` 등 기존 작업을 지우는 명령을 임의로 실행하지 않는다.
- 비밀키·인증 정보·민감한 환경변수·임의 로그 원문을 문서나 인수인계에 수집하지 않는다.

## 수동 인수인계

- ChatGPT PM은 목적·범위·완료 조건을 정리하고, 사용자가 선택한 Codex / Claude Code /
  Antigravity가 순차적으로 작업한다. 별도 유료 API·추가 구독·자동 에이전트 호출을 도입하지 않는다.
- Claude Code는 `CLAUDE.md`의 가져오기를 사용한다. Antigravity에도 루트 `AGENTS.md`와
  `docs/dev/STATE.md`를 읽도록 전달한다. 도구별 규칙을 중복 작성하지 않는다.
- 시작 시 `python3 scripts/dev_handoff.py`로 현재 인수인계 정보를 확인한다.
- 완료 시 `docs/dev/STATE.md`의 최근 작업·검증·다음 작업·제한사항을 갱신한다.
  과거 검증을 이번 실행 결과로 옮겨 적지 않으며 긴 누적 일지는 만들지 않는다.
- 종료 시 `python3 scripts/dev_handoff.py --write`로 `.local/ai-handoff.md`를 생성한다.
  실제 테스트 로그가 있으면 `--test-log PATH`를 추가한다. 로그 경로는 실행 위치 기준이다.
- 생성된 Markdown을 검토한 뒤 다음 담당자에게 수동 전달한다. 스크립트는 테스트를 실행하지 않는다.
