# Workstation Agent Kit 참고 자료

선배님의 별도 Claude 개발팀 프로젝트 **AI Dispatcher** 지침을 원문으로 보존한다.
AI-EMS에 키트를 설치하거나 이 폴더의 `AGENTS.md`·`CLAUDE.md`를 실행 지침으로 가져오지 않는다.

## 출처와 보존 범위

- 저장소: [GangHyeokLee/ai-ems-agent](https://github.com/GangHyeokLee/ai-ems-agent).
- 출처: `origin/main`의 커밋 [`eabdd878f26be221c99b08f94f9fba961cbdbd08`](https://github.com/GangHyeokLee/ai-ems-agent/tree/eabdd878f26be221c99b08f94f9fba961cbdbd08/docs/references/workstation-agent-kit).
- 가져온 날짜: 2026-11-04 (Asia/Seoul), DOC-01.
- 아래 4개 파일만 `git show <출처 커밋>:<경로>`의 원본 바이트 그대로 복사했다.
  main 전체 병합, `.claude/` 설치, 루트 지침·`.gitignore` 교체는 수행하지 않았다.

| 파일 | 바이트 | SHA256 |
|---|---:|---|
| `AGENTS.md` | 7575 | `24e8ea6f30316bb15369d95336ebddd5fbbce9fbea6e6e417156a3f07bec1837` |
| `CLAUDE.md` | 2252 | `753f2645115064f73f23b9fd1defd20b99e19396be7e147b03f1d99ddc6efd2b` |
| `KIT_GUIDE.md` | 2161 | `1e06f66671e3bd48081745eddb92aabf864c3176fa8e72eb75be4f857401a474` |
| `gitignore.sample` | 475 | `03356f395bc350ccae2bc5996dc81c4bee7fb2d1c63ac9b23855db50ae1d5a91` |

## AI-EMS에서의 용도

루트 [AGENTS.md](../../../AGENTS.md)에 다음 원칙만 최소 반영했다.

1. 코딩 전 요구사항·가정·완료 기준 확인.
2. 필요 이상의 추상화·옵션·외부 의존성 금지.
3. API·데이터 계약 우선, 계약 변경 시 설계 기록.
4. 작업별 수정 파일 범위 명시 및 기존 코드 보존.
5. 구현·검증·리뷰 역할과 결과 구분.

이 자료의 React/TypeScript/Vite, Julia/PowerModels, Qdrant, vLLM과 7개 Claude Agent Teams는
별도 프로젝트의 구성이다. AI-EMS는 기존 Python/FastAPI/LangGraph/PyPowSyBl 및 JavaScript UI를 유지한다.
키트의 `/kickoff`·`/develop` 명령, 담당 폴더 표, 자동 팀 구성·승인 흐름을 AI-EMS에 적용하지 않는다.
원문에 포함된 경로는 해당 프로젝트의 예시·맥락이며 AI-EMS에서 실행하거나 접근할 대상이 아니다.

공통 규칙은 루트 `AGENTS.md`, Claude 연결은 루트 `CLAUDE.md`, 현재 상태는 `docs/dev/STATE.md`에서 관리한다.
기존 계통별 물리해석 경계와 테스트·검증 기록 보존 규칙이 계속 적용된다.
