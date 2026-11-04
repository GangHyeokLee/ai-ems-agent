# 가상 개발팀 키트 — 설치 안내

이 키트를 새 프로젝트 폴더 맨 위에 복사하면, Claude Code에서 PM과 팀원 7명(설계, 프론트, 백엔드, 데이터, 테스트, 리뷰, 보안)이 일하는 구조가 갖춰진다.
자세한 사용법은 함께 받은 "가상 개발팀 키트 사용 가이드" 문서를 본다. 설치가 끝나면 이 파일은 지워도 된다.

## 설치
1. 이 폴더의 내용(`AGENTS.md`, `CLAUDE.md`, `.claude/`, `docs/`, `.gitignore`)을 프로젝트 폴더 맨 위에 복사한다. 숨김 파일(`.claude`, `.gitignore`)도 함께 복사되었는지 확인한다.
2. 예전 프로젝트를 참고한다면 `.claude/settings.local.json.example` 을 `.claude/settings.local.json` 으로 이름을 바꾸고 경로를 고친다.
   - macOS/Linux: `"/Users/me/projects/old-shop"`
   - Windows: `"C:/Users/me/projects/old-shop"` (슬래시 `/` 사용)
3. 데모 코드를 `docs/00-inputs/demo/` 에, 캡처를 `docs/00-inputs/demo/screens/` 에 넣는다.
4. VS Code로 폴더를 열고, 폴더를 신뢰한 뒤 **Developer: Reload Window**.
5. Claude Code 채팅창에 `/` 를 쳐서 `kickoff` 등이 보이면 준비 완료. `/kickoff` 로 시작한다.

## 들어 있는 것
- `AGENTS.md` 공통 개발 규칙 (담당 폴더, 과설계 금지, 한국어 주석, 테스트, 참고 프로젝트, 보안)
- `CLAUDE.md` 메인 대화 = PM 운영 규칙
- `.claude/settings.json` 에이전트 팀 켜기, 자주 쓰는 명령 허용, `.env` 읽기·`git push` 차단
- `.claude/agents/` architect, frontend-dev, backend-dev, data-dev, tester, reviewer, security
- `.claude/skills/` kickoff, develop, qa-cycle, wrapup, briefing, worklog
- `docs/` STATUS.md, 입력 폴더, 산출물 폴더, `_templates/` 문서 양식 14종

## 조정하기
- 에이전트 팀 끄기: `.claude/settings.json` 의 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` 를 `"0"` 으로
- 비용 줄이기: 에이전트 파일 머리말에 `model: sonnet` 한 줄 추가
- QA 최대 회차: `.claude/skills/qa-cycle/SKILL.md` 의 "최대 3회"
- 담당 폴더: `AGENTS.md` 2.1 (프로젝트별로는 설계서의 담당 폴더 표가 우선)
