# 공통 개발 규칙 (모든 에이전트 공통)

> 이 파일은 PM과 모든 팀원이 따르는 규칙이다. 프로젝트별 정보(1장)는 `/kickoff` 에서 PM이 채운다.
> 규칙끼리 충돌하면 번호가 작은 장이 우선한다.

## 1. 프로젝트 정보 (PM이 /kickoff 에서 채움)

- 프로젝트 이름: AI Dispatcher (계통 관제 의사결정지원 테스트베드)
- 한 줄 설명: KPG193 계통에서 DC/AC 계산 엔진과 로컬 Qwen LLM(도구 호출 + 규정 RAG)이 관제 대안과 근거를 제시하는 연구용 테스트베드, 도메인 튜닝 포함
- 프론트엔드: React + TypeScript + Vite
- 백엔드: Python 3.12 + FastAPI. 계산: numpy/scipy(DC), gridfm-datakit + Julia PowerModels(AC). LLM: Qwen3.8-27B FP8 + vLLM(Docker, 최신 이미지). 튜닝: QLoRA
- DB: SQLite (업무 데이터) + Qdrant(Docker, 벡터 검색, bge-m3 + bge-reranker-v2-m3)
- 계통 데이터: kpg-testgrid `v2.0.0` / `kpg193_v2_0_0_2024` (ODbL, 출처 표기 필수)
- 참고 프로젝트: `/workspace1/ai_ems/working/lmh/opengridfm_korea`, `/workspace1/ai_ems/working/lmh/gridfm-datakit`, `/workspace1/ai_ems/working/lmh/gridfm-graphkit` (읽기 전용, 5장 참고. `lmh` 는 이 프로젝트의 상위 폴더 `u242060` 링크이므로 `../` 경로 작업 시 주의)
- 실행 명령: `cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` / `cd frontend && npm install && npm run dev` / 인프라 `docker compose up -d qdrant vllm` / 관리 작업 `cd backend && uv run python -m app.cli <명령>` (상세: `docs/02-design/api-spec.md` 1장)
- 테스트 명령: `cd backend && OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 uv run pytest && cd ../frontend && npm test` (무거운 환경 테스트는 따로: `cd backend && uv run --env-file ../.env pytest -m julia` (Julia·PowerModels), `-m models`, `-m llm`)

## 2. 협업 규칙

### 2.1 담당 폴더 (자기 폴더만 수정한다)

| 담당 | 수정 가능한 경로 |
| --- | --- |
| frontend-dev | `frontend/` |
| backend-dev | `backend/app/api/`, `backend/app/services/`, `backend/app/core/`, `backend/app/main.py` |
| data-dev | `backend/app/models/`, `backend/app/repositories/`, `backend/migrations/`, `data/` |
| 각 개발자 | 자기 영역의 단위 테스트 (`frontend/tests/`, `backend/tests/unit/<영역>/`) |
| tester | `backend/tests/integration/`, `tests/e2e/`, `docs/03-test/` |
| reviewer, security | `docs/04-reports/` 만 (코드 수정 금지) |
| architect | `docs/02-design/` |
| PM(메인 대화) | `docs/` 전체, `AGENTS.md` 1장, 공용 설정 파일 |

- 위 표는 기본값이다. 실제 경로는 `docs/02-design/api-spec.md` 의 "담당 폴더" 표가 우선한다.
- 남의 폴더를 고쳐야 하면 직접 고치지 말고 담당자(또는 PM)에게 요청한다.
- 공용 파일(`pyproject.toml`, `package.json`, `.env.example`, 공통 타입)은 PM의 승인을 받고 한 명만 수정한다.

### 2.2 계약 우선

- 개발은 `docs/02-design/api-spec.md`, `data-design.md` 를 기준으로 한다. 문서에 없는 엔드포인트, 필드, 테이블을 만들지 않는다.
- 계약을 바꿔야 하면 관련 팀원과 합의하고 architect 문서를 먼저 고친 뒤 코드를 바꾼다. 변경 사실을 PM에게 알린다.

### 2.3 문서로 전달한다

- 팀 간 전달, 결정, 보고는 모두 `docs/` 에 남긴다. 대화로만 전달하지 않는다.
- 문서 양식은 `docs/_templates/` 를 쓴다.

## 3. 코드 작성 원칙 (시니어 기준, 과설계 금지)

**코딩 전에 생각한다**
- 구현 전에 이해한 요구사항과 가정을 짧게 밝힌다.
- 명세가 애매하면 추측하지 말고 PM에게 묻는다.
- 더 단순한 방법이 있으면 제안하고 트레이드오프를 함께 말한다.

**요청받은 만큼만 만든다**
- 명세에 필요한 최소한의 코드만 쓴다.
- 요청하지 않은 기능, 옵션, 설정값, "나중을 위한" 확장 포인트를 만들지 않는다.
- 일어날 수 없는 상황에 대한 방어 코드를 넣지 않는다. 외부 입력 검증은 반드시 한다.
- 표준 라이브러리와 이미 있는 코드로 해결되면 새로 만들지 않는다.
- 새 추상화(베이스 클래스, 팩토리, 제네릭 유틸)는 같은 패턴이 3번 반복될 때만 도입한다.
- 새 외부 의존성은 추가 전에 이유를 PM에게 설명하고 승인을 받는다.

**결합도를 낮춘다**
- 백엔드 레이어는 `api → services → repositories` 방향으로만 의존한다. 역방향 import 금지.
  - api: 입력 검증, 변환, 응답 형식만
  - services: 비즈니스 로직
  - repositories: DB 접근. SQL과 ORM 쿼리는 이 레이어에만 둔다.
- 프론트는 화면 컴포넌트와 API 호출 코드를 분리한다. API 호출은 한 곳(`frontend/src/api/`)에 모은다.
- 설정, 비밀값, DB 경로는 환경변수로만 주입하고 `.env.example` 에 키 목록을 유지한다.

**필요한 곳만 고친다**
- 작업과 무관한 코드는 수정, 리팩터링, 포맷 변경하지 않는다.
- 기존 코드 스타일을 따른다.
- 내 변경 때문에 안 쓰이게 된 코드는 정리한다. 원래 있던 죽은 코드는 지우지 말고 보고만 한다.

**한국어 주석**
- 코드가 "무엇을" 하는지는 이름으로 드러낸다. 주석은 "왜"를 쓴다.
- 주석을 다는 곳: 비즈니스 규칙의 근거, 명세서 항목 연결(`# 요구사항 FR-03: 배송 전까지만 취소 가능`), 의도적인 선택이나 우회, 외부 시스템과의 약속.
- 주석을 달지 않는 곳: 코드를 그대로 읽어주는 설명, 변경 이력(그건 git과 worklog에), 주석 처리된 옛 코드.
- 의도적으로 미룬 확장은 `# shortcut: <미룬 내용과 이유>` 로 남기고 지우지 않는다.

**검증으로 끝낸다**
- 작업 시작 시 완료 기준(단위 테스트 통과, 실행 결과 등)을 정한다.
- 완료 기준을 직접 실행해 확인한 뒤에 끝낸다. 확인하지 못했으면 못했다고 보고한다.

## 4. 테스트 규칙

- 개발자는 자기 코드의 단위 테스트를 함께 작성한다. 테스트 이름에 요구사항 ID를 넣는다 (예: `test_fr03_cancel_before_shipping`).
- tester는 시나리오·통합 테스트를 쓰고 전체를 실행해 결과서를 쓴다. 실패를 고치지 않고 보고한다.
- 테스트를 통과시키려고 테스트를 약하게 고치지 않는다.

## 5. 참고 프로젝트 규칙

- 참고 프로젝트는 **읽기 전용**이다. 절대 수정, 이동, 삭제하지 않는다.
- 코드를 통째로 복사하지 않는다. `docs/00-inputs/reference-analysis.md` 에서 승인된 패턴만 이 규칙에 맞게 다시 작성한다.
- 참고 코드와 이 규칙이 충돌하면 이 규칙이 우선한다.
- 참고 프로젝트의 `.env`, 키, 비밀번호, 인증서는 읽지도 옮기지도 않는다.

## 6. 보안 기본

- 비밀값을 코드, 문서, 로그, 테스트 데이터에 넣지 않는다.
- 모든 외부 입력(요청 본문, 쿼리, 파일 업로드)은 api 레이어에서 검증한다.
- SQL은 ORM 또는 바인딩 파라미터로만 만든다. 문자열 이어붙이기 금지.
- 인증·권한 검사는 서비스 진입점에서 빠짐없이 한다.

## 7. 작업 마무리

- 작업을 마치면 변경 파일, 결정, 남은 일을 PM에게 짧게 보고한다 (5~8줄, 코드 붙여넣기 금지).
- 실제로 한 일만 보고한다. 계획을 완료된 것처럼 쓰지 않는다.
